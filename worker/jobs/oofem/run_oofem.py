#!/usr/bin/env python3
"""
run_oofem.py -- can a model write an OOFEM simulation that reproduces a paper?

    worker/worker.sh oofem --list-curves               what each case must produce
    worker/worker.sh oofem --dry-run --cases ATENA/TPBB    build the prompt, call nothing
    worker/worker.sh oofem --models kit.deepseek-v4-flash --limit 1
    worker/worker.sh oofem --rescore RUN_ID            re-score, no API calls, no solving

The sibling `eval` job asks whether a model can author a benchmark.json -- a
SPEC, scored key by key against a reference spec. This job removes that
abstraction: the model writes Python against the oofempy bindings directly,
meshes the geometry itself, runs the solver, and is scored on the CURVE that
comes out.

That means there is no reference artifact to diff against. OOFEM builds its
problem in memory and never writes a deck, so nothing here compares text. The
only question asked is whether the thing runs and lands on the paper's data.

    runs/<run-id>/
      run.json                          what was asked, and what each call did
      <model>/<case>/raw.json           the API response, untouched
      <model>/<case>/candidate.py       the extracted script
      <model>/<case>/work/              cwd for the run: reference/ in,
                                        results.csv + plot.png out
      <model>/<case>/run.log            the script's stdout and stderr
      <model>/<case>/score.json|.txt    curve scores

Every response is written BEFORE anything is executed, so --rescore can re-run
the comparator after a scoring change without paying for the calls again.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent              # worker/jobs/oofem
REPO = HERE.parent.parent.parent                    # repo root
WORKER = REPO / "worker"
DATASET = REPO / "dataset"
RUNS = Path(os.environ.get("EVAL_RUNS", REPO / "workspace" / "runs"))

sys.path.insert(0, str(WORKER / "tools"))
sys.path.insert(0, str(HERE))
import llm                                          # noqa: E402
import curves as CURVES                             # noqa: E402
import agent as AGENT                               # noqa: E402
import score as SCORE                               # noqa: E402

# Where oofempy lives, and which interpreter can import it. The bindings are
# compiled against one Python minor version -- a mismatch is an ImportError, not
# a warning -- so this is a separate interpreter from the one running this file.
OOFEM_ROOT = Path(os.environ.get(
    "OOFEM_ROOT", Path.home() / "Documents" / "johannes" / "oofem-gw"))
OOFEMPY_DIR = Path(os.environ.get("OOFEMPY_DIR", OOFEM_ROOT / "build"))
OOFEM_PY = os.environ.get("OOFEM_PY", "python3.12")
# The bindings ship a `util` helper next to the examples, and every example
# imports it -- so a model that follows them will too.
OOFEM_BINDINGS = OOFEM_ROOT / "bindings" / "python"
SHIM = Path(os.environ.get("EVAL_RUNS", REPO / "workspace")).parent / "workspace" \
    / ".oofem-shim"


def ensure_shim() -> Path:
    """One directory where `import oofempy` AND `import util` both work.

    Putting bindings/python on the path directly does not work, and both models
    that got far enough to run a script died on it. That directory contains an
    `oofem/` PACKAGE whose __init__ is `from .oofempy import *` -- it expects the
    compiled module to sit inside it, which it does not: oofempy.so is over in
    build/. So `import oofem` half-succeeds and then raises
    `No module named 'oofem.oofempy'`, and the examples' own

        try:    import oofem as oofempy      # installed
        except: import oofempy               # in-tree

    idiom picks the branch that cannot work.

    Making `import oofem` merely FAIL is not enough either: both models that got
    this far wrote `from oofem import util` flat, with no try/except, so a clean
    failure is still a dead run. Which import incantation a model guesses is not
    what this benchmark measures.

    So the shim supports BOTH idioms. It holds oofempy.so and util.py as
    top-level modules, plus a tiny `oofem/` package that re-exports the compiled
    module and registers it as `oofem.oofempy`, which is the name its own util.py
    reaches for. All four of these then work:

        import oofempy                 from oofem import util
        import util                    import oofem as oofempy
    """
    SHIM.mkdir(parents=True, exist_ok=True)
    so = OOFEMPY_DIR / "oofempy.so"
    link = SHIM / "oofempy.so"
    if so.exists() and not link.exists():
        link.symlink_to(so)
    util = OOFEM_BINDINGS / "util.py"
    if util.exists():
        shutil.copy2(util, SHIM / "util.py")

    pkg = SHIM / "oofem"
    pkg.mkdir(exist_ok=True)
    if util.exists():
        shutil.copy2(util, pkg / "util.py")
    (pkg / "__init__.py").write_text(
        "# Generated by run_oofem.py -- see ensure_shim().\n"
        "# The real bindings package expects oofempy.so to sit inside it; in a\n"
        "# build tree it does not. Re-export the compiled module and register it\n"
        "# under this package's name so `from . import oofempy` resolves.\n"
        "import sys\n"
        "import oofempy as _oofempy\n"
        "sys.modules[__name__ + '.oofempy'] = _oofempy\n"
        "from oofempy import *  # noqa: F401,F403\n", encoding="utf-8")
    return SHIM


def _pythonpath(env: dict) -> str:
    parts = [str(OOFEMPY_DIR), str(ensure_shim()), env.get("PYTHONPATH", "")]
    return os.pathsep.join(p for p in parts if p)

# Outcomes, kept distinct because each needs a different response from a person.
# `rejected` is a static failure and costs no solver time; `crashed` means the
# script ran and oofem or the script itself raised; `no_output` means it exited
# clean and wrote nothing, which is usually a wrong working directory.
OK, TRUNCATED, UNPARSEABLE, REJECTED = "ok", "truncated", "unparseable", "rejected"
CRASHED, TIMEOUT, NO_OUTPUT, INCOMPLETE = "crashed", "timeout", "no_output", "incomplete"
REFUSED, ERROR = "refused", "error"

# Not a sandbox -- a guard rail. The scripts are model output run on a
# workstation, and these imports have no business in a finite-element script.
# Anything stronger (containers, seccomp) belongs in the runner, not here.
BANNED_IMPORTS = {
    "subprocess", "socket", "requests", "urllib", "http", "ftplib",
    "telnetlib", "smtplib", "xmlrpc", "ctypes", "pty",
}


def extract_python(text: str) -> tuple:
    """Pull the script out of the reply, preferring a fenced block."""
    text = (text or "").strip()
    if not text:
        return None, "empty reply"

    blocks, rest = [], text
    while "```" in rest:
        _, _, after = rest.partition("```")
        if after.startswith("python"):
            after = after[len("python"):]
        body, sep, rest = after.partition("```")
        if sep:
            blocks.append(body.strip())
        else:
            break

    for candidate in blocks + [text]:
        if not candidate:
            continue
        try:
            ast.parse(candidate)
            return candidate, None
        except SyntaxError as e:
            last = f"{e.msg} at line {e.lineno}"
    return None, (f"no syntactically valid Python found ({last})"
                  if blocks or text else "no code block")


def static_gate(src: str) -> str | None:
    """Reasons to refuse to execute. Returns a message, or None to proceed."""
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = imported & BANNED_IMPORTS
    if banned:
        return f"imports {', '.join(sorted(banned))}"
    if "oofempy" not in imported:
        return "never imports oofempy"
    return None


def describe_curves(ref_dir: Path, wanted: list) -> str:
    """The curve contract, as the prompt states it. Same list the scorer uses."""
    lines = []
    for c in wanted:
        xs, _ = CURVES.series(ref_dir, c)
        span = f"{min(xs):g} to {max(xs):g}" if xs else "?"
        x_desc = c.get("x_label") or c["x_name"]
        y_desc = c.get("y_label") or c["y_name"]
        lines.append(
            f"- `{c['id']}`\n"
            f"    x = {x_desc}   (reference spans {span}, {len(xs)} samples)\n"
            f"    y = {y_desc}\n"
            f"    reference file: reference/{c['file']}  "
            f"(columns `{c['x_name']}` and `{c['y_name']}`)")
    return "\n".join(lines)


def oofem_examples() -> str:
    """The bindings' own example and helper, read from the checkout the script
    will run against -- so the prompt cannot drift from the installed API."""
    parts = []
    for rel in ("bindings/python/tests/test_5.py", "bindings/python/util.py"):
        path = OOFEM_ROOT / rel
        if path.exists():
            parts.append(f"### {rel}\n\n```python\n"
                         f"{path.read_text(encoding='utf-8')}\n```")
    if not parts:
        parts.append(f"(no examples found under {OOFEM_ROOT})")
    return "\n\n".join(parts)


def oofem_api() -> str:
    """The symbol list, kept in its own file so it can be curated without
    editing the prompt. Regenerate with dump_api.py when the bindings change."""
    path = HERE / "prompts" / "oofem_api.md"
    if not path.exists():
        return ("(no oofem_api.md -- regenerate it with "
                "`python worker/jobs/oofem/dump_api.py`)")
    return path.read_text(encoding="utf-8")


def build_prompt(case: str, paper: str, ref_dir: Path, wanted: list,
                 timeout: int) -> str:
    tpl = (HERE / "prompts" / "author_oofem.md").read_text(encoding="utf-8")
    scope = REPO / "benchmarks" / case / "scope.md"
    return (tpl
            .replace("{OOFEM_API}", oofem_api())
            .replace("{CURVES}", describe_curves(ref_dir, wanted))
            .replace("{SCOPE}", scope.read_text(encoding="utf-8")
                     if scope.exists() else f"(no scope.md for {case})")
            .replace("{OOFEM_EXAMPLES}", oofem_examples())
            .replace("{TIMEOUT}", str(timeout))
            .replace("{PAPER}", paper))


def build_agent_system(case: str, ref_dir: Path, wanted: list,
                       max_turns: int) -> str:
    tpl = (HERE / "prompts" / "agent_oofem.md").read_text(encoding="utf-8")
    scope = REPO / "benchmarks" / case / "scope.md"
    return (tpl
            .replace("{OOFEM_API}", oofem_api())
            .replace("{OOFEM_ROOT}", str(OOFEM_ROOT))
            .replace("{CURVES}", describe_curves(ref_dir, wanted))
            .replace("{MAX_TURNS}", str(max_turns))
            .replace("{SCOPE}", scope.read_text(encoding="utf-8")
                     if scope.exists() else f"(no scope.md for {case})"))


def interpret(resp: dict) -> tuple:
    """(outcome, message, script, finish_reason) from one API response."""
    if "http_error" in resp:
        blocked = "ExceededBudget" in resp.get("detail", "")
        return (REFUSED if blocked else ERROR,
                resp.get("detail", "")[:200], None, "http_error")
    if "error" in resp:
        return ERROR, resp["error"][:200], None, "error"

    choices = resp.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    finish = choices[0].get("finish_reason") or "?"
    content = msg.get("content")
    if not content:
        if msg.get("reasoning_content"):
            return (TRUNCATED,
                    "content empty; the whole budget went to reasoning_content",
                    None, finish)
        return UNPARSEABLE, "empty content", None, finish

    src, err = extract_python(content)
    if src is None:
        if finish == "length":
            return TRUNCATED, f"cut off: {err}", None, finish
        return UNPARSEABLE, err, None, finish
    if finish == "length":
        return TRUNCATED, "finish_reason=length but a script was recovered", src, finish
    return OK, None, src, finish


def execute(script: Path, work: Path, log: Path, timeout: float) -> tuple:
    """Run the candidate. Returns (outcome, message, seconds).

    Output goes STRAIGHT to the log file rather than through a pipe. oofem is
    C++ and prints through its own buffered streams: when it segfaults, a piped
    buffer dies with the process and the log lands empty, which is exactly the
    case where the log is the only evidence there is.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath(env)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    t0 = time.time()
    try:
        with log.open("wb") as fh:
            proc = subprocess.run(
                [OOFEM_PY, "-X", "faulthandler", str(script)], cwd=str(work), env=env,
                stdout=fh, stderr=subprocess.STDOUT,
                timeout=timeout, start_new_session=True)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        with log.open("ab") as fh:
            fh.write(b"\n[killed: timeout]\n")
        return TIMEOUT, f"killed after {timeout:.0f}s", round(time.time() - t0, 1)
    except FileNotFoundError:
        return ERROR, f"interpreter not found: {OOFEM_PY}", 0.0

    elapsed = round(time.time() - t0, 1)
    if rc != 0:
        return CRASHED, crash_reason(rc, log), elapsed
    if not (work / "results.csv").exists():
        return NO_OUTPUT, "exited 0 but wrote no results.csv", elapsed
    return OK, None, elapsed


def crash_reason(rc: int, log: Path) -> str:
    """A negative return code is a SIGNAL, not an exit status. `exit -11` means
    nothing to a reader; `killed by SIGSEGV` says the solver died natively and
    the Python traceback nobody is looking for does not exist."""
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = f"signal {-rc}"
        how = f"killed by {name}"
        if name == "SIGSEGV":
            how += " (native crash inside oofem)"
    else:
        how = f"exit {rc}"
    tail = ""
    try:
        lines = [ln for ln in log.read_text(errors="replace").strip().splitlines()
                 if ln.strip()]
        if lines:
            tail = f": {lines[-1][:160]}"
    except OSError:
        pass
    return how + (tail or ": no output")


def prepare_work(work: Path, ref_dir: Path, paper: str = "",
                 case: str = "", paper_pdf: Path | None = None) -> None:
    """Fresh cwd with the reference data in it, so the script can overlay it.

    In agent mode the paper and the scope go in too. They are FILES rather than
    prompt text on purpose: a 400k-character paper repeated across forty turns
    of context is the fastest way to make an agent loop unaffordable.
    """
    if work.exists():
        shutil.rmtree(work)
    (work / "reference").mkdir(parents=True)
    for csv_file in ref_dir.glob("*.csv"):
        shutil.copy2(csv_file, work / "reference" / csv_file.name)
    if paper:
        (work / "paper.txt").write_text(paper, encoding="utf-8")
    if paper_pdf and paper_pdf.is_file():
        shutil.copy2(paper_pdf, work / "paper.pdf")
    if case:
        scope = REPO / "benchmarks" / case / "scope.md"
        if scope.exists():
            shutil.copy2(scope, work / "scope.md")


def price_of(model: dict) -> dict | None:
    """Per-model token price from models.yaml, when it carries one. KIT-hosted
    models are free and can say so with a price of 0; a model with no price at
    all reports cost_usd = null rather than a made-up number."""
    keys = ("price_in_per_mtok", "price_out_per_mtok")
    if not any(k in model for k in keys):
        return None
    return {"in_per_mtok": float(model.get(keys[0], 0.0)),
            "out_per_mtok": float(model.get(keys[1], 0.0))}


def report(mid: str, name: str, rec: dict) -> None:
    flag = "" if rec["outcome"] == OK else f"  [{rec['outcome']}: {rec['message']}]"
    shown = "-" if rec.get("overall") is None else f"{rec['overall']:.3f}"
    extra = ""
    if rec.get("turns") is not None:
        cost = rec.get("cost_usd")
        extra = (f"  {rec['turns']:>2d}t/{rec.get('tool_calls', 0):>3d}c"
                 f"  {rec.get('prompt_tokens', 0) // 1000:>5d}k in"
                 f"/{rec.get('completion_tokens', 0) // 1000:>4d}k out"
                 + (f"  {rec.get('validation_attempts')}v"
                    if rec.get("validation_attempts") else "")
                 + (f"  ${cost:.3f}" if cost else ""))
    print(f"  {mid:28s} {name:40s} {shown:>7s}  "
          f"api {rec.get('api_seconds') or 0:6.1f}s  "
          f"run {rec.get('run_seconds') or 0:6.1f}s{extra}{flag}", flush=True)


def write_score(out: Path, s: dict) -> None:
    (out / "score.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (out / "score.txt").write_text(SCORE.render(s) + "\n", encoding="utf-8")


def rescore(run_dir: Path) -> int:
    """Re-run the comparator over a finished run. No API calls, no solving."""
    run_json = run_dir / "run.json"
    if not run_json.exists():
        sys.exit(f"no run.json in {run_dir}")
    run = json.loads(run_json.read_text(encoding="utf-8"))
    for rec in run["records"]:
        out = run_dir / rec["model"].replace("/", "_") / rec["case"].replace("/", "__")
        work = out / "work"
        if not work.exists():
            continue
        s = SCORE.score(work, CURVES.ref_dir_for(rec["case"]))
        write_score(out, s)
        rec["overall"] = s["overall"]
        rec["n_missing"] = len(s.get("missing", []))
        print(f"  {rec['model']:32s} {rec['case']:46s} {s['overall']:.3f}")
    run_json.write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"\nrescored {run_dir}")
    return 0


def list_curves(manifest: dict, only: list | None) -> int:
    total = scorable = 0
    for case in manifest["cases"]:
        if only and case["case"] not in only:
            continue
        total += 1
        rd = CURVES.ref_dir_for(case["case"])
        cs = CURVES.curves_for(rd)
        scorable += bool(cs)
        print(f"\n{case['case']}  --  {len(cs)} curve(s)"
              + ("   [UNSCORABLE: no reference curves]" if not cs else ""))
        for c in cs:
            xs, _ = CURVES.series(rd, c)
            print(f"    {c['id']:<56s} x={c['x_name']:<24s} "
                  f"y={c['y_name']:<22s} n={len(xs)}")
    print(f"\n{scorable}/{total} case(s) scorable")
    return 0


def check_oofem() -> str | None:
    """A sweep that cannot import oofempy produces 21 identical crashes. Say so
    once, up front, instead."""
    probe = "import oofempy; print(getattr(oofempy, '__version__', 'ok'))"
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath(env)
    try:
        r = subprocess.run([OOFEM_PY, "-c", probe], env=env, timeout=120,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"cannot run {OOFEM_PY}: {e}"
    if r.returncode != 0:
        return (r.stdout or b"").decode(errors="replace").strip().splitlines()[-1]
    return None


def main(argv=None) -> int:
    llm.load_env()
    cfg = llm.read_yaml()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--models", nargs="+")
    ap.add_argument("--cases", nargs="+")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--endpoint", default=cfg.get("endpoint"))
    ap.add_argument("--allow-metered", action="store_true")
    ap.add_argument("--max-paper-chars", type=int, default=400_000)
    ap.add_argument("--run-timeout", type=float, default=900.0,
                    help="wall-clock cap on one simulation (default 900s)")
    ap.add_argument("--agent", action="store_true",
                    help="agentic arm: let the model read the OOFEM source and "
                         "run spikes until it submits (default: one shot)")
    ap.add_argument("--api-timeout", type=float,
                    help="override models.yaml timeout_seconds per call. A "
                         "one-shot arm makes ONE call, so a 2400s entry is "
                         "fine there; the agent arm makes one per turn, where "
                         "the same number is effectively unbounded")
    ap.add_argument("--max-turns", type=int, default=40,
                    help="agent turn budget (default 40)")
    ap.add_argument("--finalization-turns", type=int, default=4,
                    help="extra agent turns restricted to validating, repairing, "
                         "and submitting an existing candidate (default 4)")
    ap.add_argument("--api-retries", type=int, default=2,
                    help="retries for transient per-turn API failures (default 2)")
    ap.add_argument("--spike-timeout", type=float, default=120.0,
                    help="wall-clock cap on one run_python spike (default 120s)")
    ap.add_argument("--rescore", metavar="RUN_ID")
    ap.add_argument("--list-curves", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the prompt and write it out; call nothing")
    a = ap.parse_args(argv)

    manifest_path = DATASET / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no dataset at {DATASET} — run: python worker/tools/build_dataset.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if a.list_curves:
        return list_curves(manifest, a.cases)
    if a.rescore:
        return rescore(RUNS / a.rescore)

    cases = [c for c in manifest["cases"]
             if c["paper_text"] and (not a.cases or c["case"] in a.cases)]
    if a.cases:
        missing = set(a.cases) - {c["case"] for c in cases}
        if missing:
            sys.exit(f"unknown or paper-less case(s): {', '.join(sorted(missing))}")

    # Drop what cannot be scored rather than burning a call to find out.
    skipped = [c["case"] for c in cases
               if not CURVES.curves_for(CURVES.ref_dir_for(c["case"]))]
    cases = [c for c in cases if c["case"] not in skipped]
    if skipped:
        print(f"skipping {len(skipped)} case(s) with no reference curves: "
              f"{', '.join(skipped)}\n")
    cases = cases[:a.limit] if a.limit else cases
    if not cases:
        sys.exit("no cases selected")

    models = llm.select_models(cfg, a.models, a.allow_metered)
    defaults = cfg.get("defaults", {})

    if not a.dry_run:
        problem = check_oofem()
        if problem:
            sys.exit(f"oofempy is not importable by {OOFEM_PY}:\n  {problem}\n"
                     f"  looked in OOFEMPY_DIR={OOFEMPY_DIR}\n"
                     f"  build it, or point OOFEMPY_DIR / OOFEM_PY at a build "
                     f"that matches the interpreter.\n"
                     f"  `--dry-run` works without it.")
        key = llm.api_key()

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    records = []

    print(f"run {run_id}: {len(models)} model(s) x {len(cases)} case(s) "
          f"= {len(models) * len(cases)} calls -> {run_dir}", flush=True)

    for model in models:
        mid = model["id"]
        mt = int(model.get("max_tokens", defaults.get("max_tokens", 32000)))
        temp = float(model.get("temperature", defaults.get("temperature", 0.0)))
        tmo = float(a.api_timeout or
                    model.get("timeout_seconds", defaults.get("timeout_seconds", 900)))
        for case in cases:
            name = case["case"]
            out = run_dir / mid.replace("/", "_") / name.replace("/", "__")
            out.mkdir(parents=True, exist_ok=True)
            ref_dir = CURVES.ref_dir_for(name)
            wanted = CURVES.curves_for(ref_dir)

            paper = (DATASET / case["paper_text"]).read_text(encoding="utf-8")
            # Agent mode pages through a file on demand, so retaining the full
            # paper costs no repeated prompt tokens. The one-shot arm embeds it
            # in one large prompt and still needs the explicit size limit.
            clipped = not a.agent and len(paper) > a.max_paper_chars
            if clipped:
                paper = paper[:a.max_paper_chars] + "\n\n[paper truncated]\n"

            rec = {"model": mid, "case": name, "mode": "agent" if a.agent else "oneshot",
                   "outcome": None, "message": None, "api_seconds": None,
                   "run_seconds": None, "paper_truncated": clipped,
                   "n_curves": len(wanted), "overall": None}
            work = out / "work"
            t0 = time.time()

            if a.agent:
                system = build_agent_system(name, ref_dir, wanted, a.max_turns)
                if a.dry_run:
                    (out / "prompt.md").write_text(system, encoding="utf-8")
                    print(f"  {mid:32s} {name:46s} system {len(system):>7d} chars, "
                          f"{len(wanted)} curve(s) -> {out / 'prompt.md'}", flush=True)
                    continue
                paper_pdf = (DATASET / case["paper_pdf"]
                             if case.get("paper_pdf") else None)
                prepare_work(work, ref_dir, paper, name, paper_pdf)
                sb = AGENT.Sandbox(
                    work, OOFEM_ROOT, OOFEM_PY,
                    _pythonpath(dict(os.environ)), a.spike_timeout,
                    candidate_timeout=a.run_timeout,
                    curve_ids=[c["id"] for c in wanted])

                def one_turn(messages, tools, _mid=mid):
                    return llm.chat(a.endpoint, key, _mid, messages,
                                    mt, temp, tmo, tools)

                totals = AGENT.run_agent(one_turn, system, sb,
                                         out / "transcript.jsonl",
                                         a.max_turns, price_of(model),
                                         finalization_turns=a.finalization_turns,
                                         api_retries=a.api_retries,
                                         vision=bool(model.get("vision", False)))
                rec.update(totals)
                elapsed_total = time.time() - t0
                rec["api_seconds"] = round(
                    max(0.0, elapsed_total - totals.get("tool_seconds", 0.0)), 1)
                rec["run_seconds"] = totals.get("validation_total_seconds") or 0.0
                script = work / "candidate.py"
                if not script.exists():
                    rec["outcome"] = NO_OUTPUT
                    rec["message"] = f"never wrote candidate.py ({totals['stop']})"
                    records.append(rec)
                    report(mid, name, rec)
                    continue
                src = script.read_text(encoding="utf-8")
                try:
                    ast.parse(src)
                except SyntaxError as e:
                    rec["outcome"] = UNPARSEABLE
                    rec["message"] = f"candidate.py: {e.msg} at line {e.lineno}"
                    records.append(rec)
                    report(mid, name, rec)
                    continue
                # Not gated: this arm has already had a Python sandbox for many
                # turns, so refusing the final script for an import it was free
                # to use all along would be theatre. Recorded instead.
                rec["solver_evidence"] = "oofempy" in src
                if not totals.get("submitted") or not totals.get("validated"):
                    rec["outcome"] = INCOMPLETE
                    detail = totals.get("last_validation")
                    rec["message"] = totals.get("stop") or "not submitted"
                    if totals.get("candidate_dirty"):
                        rec["message"] += "; candidate changed since last validation"
                        if detail:
                            rec["message"] += f" (previous result: {detail})"
                    elif detail:
                        rec["message"] += f"; last validation: {detail}"
                    records.append(rec)
                    report(mid, name, rec)
                    continue

                # run_candidate already executed this exact source from a clean
                # directory and copied only its contract outputs back to work.
                sc = SCORE.score(work, ref_dir)
                write_score(out, sc)
                rec["outcome"] = OK
                rec["overall"] = sc["overall"]
                rec["n_missing"] = len(sc.get("missing", []))
                rec["plot"] = sc.get("plot", False)
                records.append(rec)
                report(mid, name, rec)
                continue
            else:
                prompt = build_prompt(name, paper, ref_dir, wanted, int(a.run_timeout))
                if a.dry_run:
                    (out / "prompt.md").write_text(prompt, encoding="utf-8")
                    print(f"  {mid:32s} {name:46s} prompt {len(prompt):>7d} chars, "
                          f"{len(wanted)} curve(s) -> {out / 'prompt.md'}", flush=True)
                    continue
                resp, _ = llm.call(a.endpoint, key, mid, prompt, mt, temp, tmo)
                rec["api_seconds"] = round(time.time() - t0, 1)
                (out / "raw.json").write_text(
                    json.dumps(resp, indent=2, ensure_ascii=False)[:4_000_000],
                    encoding="utf-8")
                outcome, message, src, finish = interpret(resp)
                rec.update({"outcome": outcome, "message": message,
                            "finish_reason": finish, "prompt_chars": len(prompt),
                            "usage": resp.get("usage")})
                if src is None:
                    records.append(rec)
                    report(mid, name, rec)
                    continue
                script = out / "candidate.py"
                script.write_text(src + "\n", encoding="utf-8")
                refusal = static_gate(src)
                if refusal:
                    rec["outcome"], rec["message"] = REJECTED, refusal
                    records.append(rec)
                    report(mid, name, rec)
                    continue
                prepare_work(work, ref_dir)

            r_outcome, r_msg, r_s = execute(
                script, work, out / "run.log", a.run_timeout)
            rec["run_seconds"] = r_s
            if r_outcome != OK:
                rec["outcome"], rec["message"] = r_outcome, r_msg
            else:
                sc = SCORE.score(work, ref_dir)
                write_score(out, sc)
                rec["outcome"] = OK
                rec["overall"] = sc["overall"]
                rec["n_missing"] = len(sc.get("missing", []))
                rec["plot"] = sc.get("plot", False)
            records.append(rec)
            report(mid, name, rec)

    if a.dry_run:
        print("\ndry run: no calls made")
        return 0

    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "endpoint": a.endpoint, "run_timeout": a.run_timeout,
        "mode": "agent" if a.agent else "oneshot",
        "max_turns": a.max_turns if a.agent else None,
        "finalization_turns": a.finalization_turns if a.agent else None,
        "api_retries": a.api_retries if a.agent else None,
        "oofem_root": str(OOFEM_ROOT), "oofem_py": OOFEM_PY,
        "models": [m["id"] for m in models], "cases": [c["case"] for c in cases],
        "skipped_cases": skipped, "records": records,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {run_dir / 'run.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
