#!/usr/bin/env python3
"""
run_native.py -- can a model write an OOFEM INPUT DECK that reproduces a paper?

    worker/worker.sh oofem-native --list-curves             what each case must produce
    worker/worker.sh oofem-native --dry-run --cases ATENA/TPBB    build the prompt, call nothing
    worker/worker.sh oofem-native --models kit.deepseek-v4-flash --limit 1
    worker/worker.sh oofem-native --rescore RUN_ID          re-score, no API calls, no solving

The sibling `oofem` job asks the same question through the Python bindings: the
model writes one oofempy script. This job takes the bindings away. The model
writes the native text deck (`model.in`, or `make_deck.py` that generates it)
and a `postprocess.py` that turns OOFEM's exported files into curves; the
harness runs `oofem -f model.in` in between. See pipeline.py.

Same cases, same curve contract, same scorer as the oofem job -- curves.py and
score.py are copies -- so the two jobs' numbers compare directly: bindings vs
deck, one model, one case.

    runs/<run-id>/
      run.json                          what was asked, and what each call did
      <model>/<case>/raw.json           the API response, untouched  (one shot)
      <model>/<case>/transcript.jsonl   every turn and tool result   (agent)
      <model>/<case>/candidate/         the extracted candidate files
      <model>/<case>/work/              cwd for the run: reference/ in,
                                        oofem output + results.csv + plot.png out
      <model>/<case>/run.log            generate / solve / postprocess logs
      <model>/<case>/solved.in          the deck oofem actually ran
      <model>/<case>/score.json|.txt    curve scores

Every response is written BEFORE anything is executed, so --rescore can re-run
the comparator after a scoring change without paying for the calls again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent              # worker/jobs/oofem-native
REPO = HERE.parent.parent.parent                    # repo root
WORKER = REPO / "worker"
DATASET = REPO / "dataset"
RUNS = Path(os.environ.get("EVAL_RUNS", REPO / "workspace" / "runs"))

sys.path.insert(0, str(WORKER / "tools"))
sys.path.insert(0, str(HERE))
import llm                                          # noqa: E402
import curves as CURVES                             # noqa: E402
import agent_native as AGENT                        # noqa: E402
import pipeline as PIPE                             # noqa: E402
import score as SCORE                               # noqa: E402

# The executable, and the checkout it was built from -- the prompts quote that
# checkout's test decks, so pointing OOFEM_ROOT elsewhere would document input
# syntax the binary does not accept. config.sh sets all three.
OOFEM_ROOT = Path(os.environ.get(
    "OOFEM_ROOT", Path.home() / "Documents" / "Gereon" / "oofem" / "oofem-gw"))
OOFEM_BIN = os.environ.get(
    "OOFEM_BIN", str(Path(os.environ.get(
        "OOFEMPY_DIR", Path.home() / "Documents" / "Gereon" / "oofem" / "build"
        / "debug")) / "oofem"))
OOFEM_LD_PRELOAD = os.environ.get("OOFEM_LD_PRELOAD", "")
# The Python stages (generator, postprocessor) need numpy/matplotlib/gmsh, not
# oofempy, so they run on the interpreter running this file -- .venv.
NATIVE_PY = os.environ.get("OOFEM_NATIVE_PY", sys.executable)

# Outcomes. The pipeline's own (gen_crashed, no_deck, deck_error, post_crashed,
# timeout, no_output, rejected) plus the ones that happen before anything runs.
OK, TRUNCATED, UNPARSEABLE = "ok", "truncated", "unparseable"
INCOMPLETE, NO_OUTPUT = "incomplete", "no_output"
REFUSED, ERROR = "refused", "error"

# Two worked decks, chosen to cover both halves of the corpus: transient heat
# transport (hydration) and nonlinear structural, each with an export module.
EXAMPLE_DECKS = ("tests/tm/hydratingConcreteMat06.in", "tests/sm/con2dpm5.in")

# A deck small enough to prove the executable starts, reads input and solves.
SMOKE_DECK = """smoke.out
smoke test of the oofem executable
LinearStatic nsteps 1 nmodules 0
domain 1dTruss
OutputManager tstep_all dofman_all element_all
ndofman 2 nelem 1 ncrosssect 1 nmat 1 nbc 2 nic 0 nltf 1 nset 2
node 1 coords 3 0. 0. 0.
node 2 coords 3 1. 0. 0.
truss1d 1 nodes 2 1 2 mat 1 crossSect 1
Set 1 nodes 1 1
Set 2 nodes 1 2
SimpleCS 1 area 1.0
isole 1 d 1. E 1. n 0.2 tAlpha 0.
BoundaryCondition 1 loadTimeFunction 1 dofs 1 1 values 1 0. set 1
NodalLoad 2 loadTimeFunction 1 dofs 1 1 components 1 1. set 2
ConstantFunction 1 f(t) 1.0
"""


def toolchain() -> PIPE.Toolchain:
    return PIPE.Toolchain(oofem_bin=OOFEM_BIN, python=NATIVE_PY,
                          ld_preload=OOFEM_LD_PRELOAD)


# --- the one-shot reply ------------------------------------------------------

FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.S)
FILE_NAMES = (PIPE.GENERATOR, PIPE.DECK, PIPE.POST)


def extract_files(text: str) -> tuple:
    """{file name: content} from the reply, or (None, why).

    A block belongs to a file when its info string names it (```model.in,
    ```python postprocess.py) or, failing that, when the last non-empty line
    before the fence does (a `### model.in` heading). Untagged blocks are
    ignored rather than guessed at: assigning a deck to postprocess.py by
    position would turn a formatting slip into a nonsense crash.
    """
    text = (text or "").strip()
    if not text:
        return None, "empty reply"
    files = {}
    for m in FENCE.finditer(text):
        info, body = m.group(1).strip(), m.group(2)
        name = next((t for t in info.replace(":", " ").split()
                     if t in FILE_NAMES), None)
        if name is None:
            before = [ln for ln in text[:m.start()].splitlines() if ln.strip()]
            if before:
                name = next((n for n in FILE_NAMES if n in before[-1]), None)
        if name and name not in files:
            files[name] = body.rstrip() + "\n"
    if not files:
        return None, "no fenced block named model.in / make_deck.py / postprocess.py"
    if PIPE.POST not in files:
        return None, f"no {PIPE.POST} block"
    if PIPE.DECK not in files and PIPE.GENERATOR not in files:
        return None, f"neither a {PIPE.DECK} nor a {PIPE.GENERATOR} block"
    for name in (PIPE.GENERATOR, PIPE.POST):
        if name in files:
            try:
                compile(files[name], name, "exec")
            except SyntaxError as e:
                return None, f"{name}: {e.msg} at line {e.lineno}"
    return files, None


def interpret(resp: dict) -> tuple:
    """(outcome, message, files, finish_reason) from one API response."""
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

    files, err = extract_files(content)
    if files is None:
        if finish == "length":
            return TRUNCATED, f"cut off: {err}", None, finish
        return UNPARSEABLE, err, None, finish
    if finish == "length":
        return TRUNCATED, "finish_reason=length but a candidate was recovered", \
            files, finish
    return OK, None, files, finish


# --- prompts -----------------------------------------------------------------

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


def _prompt_file(name: str, regenerate: str) -> str:
    path = HERE / "prompts" / name
    if not path.exists():
        return f"(no {name} -- regenerate it with `{regenerate}`)"
    return path.read_text(encoding="utf-8")


def oofem_records() -> str:
    return _prompt_file("oofem_records.md",
                        "python worker/jobs/oofem-native/dump_keywords.py")


def deck_primer() -> str:
    return _prompt_file("deck_primer.md", "(hand-written; restore it from git)")


def example_decks() -> str:
    """OOFEM's own test decks, read from the checkout the executable was built
    from -- so the prompt cannot drift from the installed input syntax."""
    parts = []
    for rel in EXAMPLE_DECKS:
        path = OOFEM_ROOT / rel
        if path.exists():
            parts.append(f"### {rel}\n\n```\n"
                         f"{path.read_text(encoding='utf-8', errors='replace')}\n```")
    return "\n\n".join(parts) or f"(no example decks found under {OOFEM_ROOT})"


def _scope(case: str) -> str:
    scope = REPO / "benchmarks" / case / "scope.md"
    return (scope.read_text(encoding="utf-8") if scope.exists()
            else f"(no scope.md for {case})")


def build_prompt(case: str, paper: str, ref_dir: Path, wanted: list,
                 timeout: int) -> str:
    tpl = (HERE / "prompts" / "author_native.md").read_text(encoding="utf-8")
    return (tpl
            .replace("{DECK_PRIMER}", deck_primer())
            .replace("{OOFEM_RECORDS}", oofem_records())
            .replace("{CURVES}", describe_curves(ref_dir, wanted))
            .replace("{SCOPE}", _scope(case))
            .replace("{OOFEM_EXAMPLES}", example_decks())
            .replace("{TIMEOUT}", str(timeout))
            .replace("{PAPER}", paper))


def build_agent_system(case: str, ref_dir: Path, wanted: list,
                       max_turns: int) -> str:
    tpl = (HERE / "prompts" / "agent_native.md").read_text(encoding="utf-8")
    return (tpl
            .replace("{DECK_PRIMER}", deck_primer())
            .replace("{OOFEM_RECORDS}", oofem_records())
            .replace("{OOFEM_ROOT}", str(OOFEM_ROOT))
            .replace("{MANUALS}", str(AGENT.MANUAL.MANUAL_DIR))
            .replace("{CURVES}", describe_curves(ref_dir, wanted))
            .replace("{MAX_TURNS}", str(max_turns))
            .replace("{SCOPE}", _scope(case)))


# --- running -----------------------------------------------------------------

def prepare_work(work: Path, ref_dir: Path, paper: str = "",
                 case: str = "", paper_pdf: Path | None = None) -> None:
    """Fresh cwd with the reference data in it, so postprocess.py can overlay
    it. In agent mode the paper and the scope go in too, as FILES."""
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


def execute(files: dict, out: Path, ref_dir: Path, timeout: float) -> PIPE.Result:
    """One-shot: write the candidate into a fresh work/ and run the pipeline
    there. The oofem output files stay in work/ for inspection."""
    work = out / "work"
    prepare_work(work, ref_dir)
    for name, body in files.items():
        (work / name).write_text(body, encoding="utf-8")
    r = PIPE.run_pipeline(work, toolchain(), timeout)
    (out / "run.log").write_text(PIPE.render_log(r), encoding="utf-8")
    if (work / PIPE.DECK).is_file():
        shutil.copy2(work / PIPE.DECK, out / "solved.in")
    return r


def price_of(model: dict) -> dict | None:
    """Per-model token price from models.yaml, when it carries one; a model
    with no price reports cost_usd = null rather than a made-up number."""
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
                 + (f"  ${cost:.3f}" if cost else "")
                 + ("  auto-submit" if rec.get("auto_submitted") else ""))
    print(f"  {mid:28s} {name:40s} {shown:>7s}  "
          f"api {rec.get('api_seconds') or 0:6.1f}s  "
          f"run {rec.get('run_seconds') or 0:6.1f}s{extra}{flag}", flush=True)


def write_score(out: Path, s: dict) -> None:
    (out / "score.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (out / "score.txt").write_text(SCORE.render(s) + "\n", encoding="utf-8")


def record_pipeline(rec: dict, r: PIPE.Result) -> None:
    """What the run says about the solver, whatever the outcome."""
    rec["failed_stage"] = r.stage
    rec["stage_seconds"] = r.seconds
    # Evidence, not a gate: oofem exited 0 on the deck and wrote files.
    rec["solver_evidence"] = bool(r.solver_ok and r.oofem_outputs)
    rec["n_oofem_outputs"] = len(r.oofem_outputs)
    rec["oofem_warnings"] = r.warnings


def rescore(run_dir: Path) -> int:
    """Re-run the comparator over a finished run. No API calls, no solving."""
    run_json = run_dir / "run.json"
    if not run_json.exists():
        sys.exit(f"no run.json in {run_dir}")
    run = json.loads(run_json.read_text(encoding="utf-8"))
    for rec in run["records"]:
        out = run_dir / rec["model"].replace("/", "_") / rec["case"].replace("/", "__")
        work = out / "work"
        if not (work / "results.csv").exists():
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
    """A sweep whose solver cannot start produces 21 identical deck_errors.
    Run a two-node deck once, up front, and say so instead."""
    import tempfile
    if not Path(OOFEM_BIN).is_file():
        return f"no executable at {OOFEM_BIN}"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "smoke.in").write_text(SMOKE_DECK, encoding="utf-8")
        tc = toolchain()
        try:
            rc, out, _ = PIPE.run_process([OOFEM_BIN, "-f", "smoke.in"],
                                          Path(td), tc.solver_env(), 120)
        except OSError as e:
            return f"cannot run {OOFEM_BIN}: {e}"
        if rc != 0:
            why = PIPE.oofem_errors(out) or [PIPE._last_line(out)]
            return (f"{'timed out' if rc is None else PIPE.status(rc)}: "
                    + " | ".join(why))
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
                    help="wall-clock cap on one generate+solve+postprocess "
                         "run (default 900s)")
    ap.add_argument("--agent", action="store_true",
                    help="agentic arm: let the model read the OOFEM source and "
                         "test decks until it submits (default: one shot)")
    ap.add_argument("--api-timeout", type=float,
                    help="override models.yaml timeout_seconds per call")
    ap.add_argument("--max-turns", type=int, default=40,
                    help="agent turn budget (default 40)")
    ap.add_argument("--finalization-turns", type=int, default=4,
                    help="extra agent turns restricted to validating, repairing, "
                         "and submitting an existing candidate (default 4)")
    ap.add_argument("--api-retries", type=int, default=2,
                    help="retries for transient per-turn API failures (default 2)")
    ap.add_argument("--spike-timeout", type=float, default=120.0,
                    help="wall-clock cap on one run_oofem / run_python spike "
                         "(default 120s)")
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
            sys.exit(f"the oofem executable does not run a smoke deck:\n"
                     f"  {problem}\n"
                     f"  OOFEM_BIN={OOFEM_BIN}\n"
                     f"  OOFEM_LD_PRELOAD={OOFEM_LD_PRELOAD or '<empty>'}\n"
                     f"  build it, or point OOFEM_BIN at a working build.\n"
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
                sb = AGENT.Sandbox(work, OOFEM_ROOT, toolchain(), a.spike_timeout,
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
                # Keep the candidate as submitted, next to the scores.
                cand = out / "candidate"
                cand.mkdir(exist_ok=True)
                for f in PIPE.candidate_files(work):
                    shutil.copy2(f, cand / f.name)
                if sb.last_result is not None:
                    record_pipeline(rec, sb.last_result)
                if not PIPE.has_candidate(work):
                    rec["outcome"] = NO_OUTPUT
                    rec["message"] = (f"never wrote a candidate "
                                      f"({AGENT.CANDIDATE_DESC}; {totals['stop']})")
                    records.append(rec)
                    report(mid, name, rec)
                    continue
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

                # run_candidate already executed these exact files from a clean
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
            outcome, message, files, finish = interpret(resp)
            rec.update({"outcome": outcome, "message": message,
                        "finish_reason": finish, "prompt_chars": len(prompt),
                        "usage": resp.get("usage")})
            if files is None:
                records.append(rec)
                report(mid, name, rec)
                continue
            cand = out / "candidate"
            cand.mkdir(exist_ok=True)
            for fname, body in files.items():
                (cand / fname).write_text(body, encoding="utf-8")
            rec["files"] = sorted(files)

            r = execute(files, out, ref_dir, a.run_timeout)
            rec["run_seconds"] = r.total_seconds
            record_pipeline(rec, r)
            if r.outcome != PIPE.OK:
                rec["outcome"], rec["message"] = r.outcome, r.message
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
        "run_id": run_id, "job": "oofem-native", "endpoint": a.endpoint,
        "run_timeout": a.run_timeout,
        "mode": "agent" if a.agent else "oneshot",
        "max_turns": a.max_turns if a.agent else None,
        "finalization_turns": a.finalization_turns if a.agent else None,
        "api_retries": a.api_retries if a.agent else None,
        "oofem_root": str(OOFEM_ROOT), "oofem_bin": OOFEM_BIN,
        "python": NATIVE_PY,
        "models": [m["id"] for m in models], "cases": [c["case"] for c in cases],
        "skipped_cases": skipped, "records": records,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {run_dir / 'run.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
