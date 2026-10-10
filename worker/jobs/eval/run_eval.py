#!/usr/bin/env python3
"""
run_eval.py -- can a model author a benchmark.json from a paper?

    worker/worker.sh eval --probe                     # what is reachable
    worker/worker.sh eval --cases cervera-1999-thermo-chemo-partI
    worker/worker.sh eval --models kit.gpt-oss-120b --limit 3
    worker/worker.sh eval --rescore RUN_ID            # re-score, no API calls
    worker/worker.sh eval --run-solver                # execute candidates
    worker/worker.sh eval --execute-run RUN_ID        # execute an old run

(worker.sh is the entry point; this script also runs directly.)

One identical single-shot call per (model, case): the guide, the physics modules,
the schema, the case scope and the paper text in, one JSON object out. No tools,
no phases, no solver. That is the whole point -- it isolates "can it author the
spec" from "does it execute". Solver execution and diagnostic repair turns are
opt-in and reported separately from authoring accuracy.

A reply that is not parseable JSON gets its parser error back once
(--json-repair-turns). The record keeps the first reply's `outcome`, so the
parse rate still measures the model; the repaired object is scored normally.

The agentic path is worker/dispatch/dsh.py, which is a different question.

Models marked `metered: true` in worker/models.yaml need --allow-metered. A run with no
flags only touches KIT-hosted models and costs nothing.

Every raw response is written to disk BEFORE scoring, so `--rescore` can re-run
the comparator after a scoring change without paying for the calls again.

    runs/<run-id>/
      run.json                      what was asked, and what each call did
      <model>/<case>/raw.json       the API response, untouched
      <model>/<case>/candidate.json the extracted object (when one was extractable)
      <model>/<case>/score.json     compare_benchmark.py output
      <model>/<case>/score.txt      the same thing for a person: the causes behind
                                    the findings, then the findings themselves
      <model>/<case>/json-repairs/attempt-N/raw.json
                                    replies to "your JSON did not parse"
      <model>/<case>/solver/attempt-N/output/*/plots/
                                    the candidate's own plots (reference curves
                                    removed); _baseline/<case>/ keeps them
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent          # worker/jobs/eval
REPO = HERE.parent.parent.parent                # repo root
WORKER = REPO / "worker"
DATASET = REPO / "dataset"
# Shared with the authoring job: which models exist, and which cost money.
MODELS_YAML = WORKER / "models.yaml"
# Run artifacts are machine output, not source: they live in workspace/ beside
# AutoBench, so `worker/` stays code and `workspace/` stays disposable.
RUNS = Path(os.environ.get("EVAL_RUNS", REPO / "workspace" / "runs"))

sys.path.insert(0, str(REPO / "worker" / "tools"))
import compare_benchmark as CMP                 # noqa: E402
import cluster_findings as CLU                  # noqa: E402
import execute_candidate as EXEC                # noqa: E402

ENV_FILES = (WORKER / ".env",)
API_KEY_VARS = ("WORKER_API_KEY", "KI_TOOLBOX_API_KEY", "DEEPSEEK_API_KEY")

# Outcomes. Kept distinct because they need different responses from a person:
# `truncated` is a cap to raise, `refused` is a budget or permission problem,
# `unparseable` is a real model failure.
OK, TRUNCATED, UNPARSEABLE, REFUSED, ERROR = (
    "ok", "truncated", "unparseable", "refused", "error")


def load_env() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


def api_key() -> str:
    for var in API_KEY_VARS:
        if os.environ.get(var):
            return os.environ[var]
    sys.exit(f"no API key: set one of {', '.join(API_KEY_VARS)} "
             f"(or put it in {ENV_FILES[0]})")


def read_yaml(path: Path) -> dict:
    """models.yaml is a fixed, flat shape we control, so a tiny reader beats a
    dependency the venv does not have."""
    root: dict = {}
    stack = [(-1, root)]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            line = line[2:].strip()
            if not isinstance(parent, list):
                continue
            item: dict = {}
            parent.append(item)
            stack.append((indent, item))
            parent, line = item, line
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            nxt: list | dict = [] if key == "models" else {}
            parent[key] = nxt
            stack.append((indent, nxt))
        else:
            parent[key] = coerce(val)
    return root


def strip_comment(v: str) -> str:
    """Drop a trailing ` # ...`. Quote-aware, so a value containing a # survives."""
    out, in_str, quote = [], False, ""
    for i, ch in enumerate(v):
        if in_str:
            if ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str, quote = True, ch
        elif ch == "#" and (i == 0 or v[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out).strip()


def coerce(v: str):
    v = strip_comment(v)
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v.strip('"').strip("'")


def compact_schema(node):
    """Strip prose from the schema. `description` and the `_comment` documentation
    are ~60 % of its 95 kB and none of it constrains the output -- the model has
    the authoring guide for the prose."""
    if isinstance(node, dict):
        return {k: compact_schema(v) for k, v in node.items()
                if k not in ("description", "$comment", "examples")}
    if isinstance(node, list):
        return [compact_schema(v) for v in node]
    return node


def build_prompt(paper_text: str, scope_text: str) -> str:
    tpl = (HERE / "prompts" / "author_benchmark.md").read_text(encoding="utf-8")
    skills = WORKER / "SKILLs"
    modules = "\n\n".join(
        f"### {p.name}\n\n{p.read_text(encoding='utf-8')}"
        for p in sorted((skills / "modules").glob("*.md")))
    schema = json.dumps(
        compact_schema(json.loads(
            (WORKER / "schemas" / "benchmark.schema.json").read_text(encoding="utf-8"))),
        indent=1)
    return (tpl
            .replace("{CREATE}", (skills / "CREATE.md").read_text(encoding="utf-8"))
            .replace("{MODULES}", modules)
            .replace("{SCHEMA}", schema)
            .replace("{SCOPE}", scope_text)
            .replace("{PAPER}", paper_text))


def call_messages(endpoint: str, key: str, model: str, messages: list[dict],
                  max_tokens: int, temperature: float,
                  timeout: float) -> tuple[dict, str | None]:
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages, "stream": False}
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        return {"http_error": e.code, "detail": detail}, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return {"error": str(e)}, f"{type(e).__name__}: {e}"


def call(endpoint: str, key: str, model: str, prompt: str,
         max_tokens: int, temperature: float, timeout: float) -> tuple[dict, str | None]:
    return call_messages(endpoint, key, model,
                         [{"role": "user", "content": prompt}],
                         max_tokens, temperature, timeout)


def extract_json(text: str) -> tuple[dict | None, str | None]:
    """Extract a JSON object and preserve parse diagnostics."""
    text = (text or "").strip()
    if not text:
        return None, "empty reply"

    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", text, re.S))

    errors = []

    for candidate in candidates:
        candidate = candidate.strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, None
            errors.append(
                f"top-level JSON value is {type(obj).__name__}, expected object"
            )
        except json.JSONDecodeError as e:
            errors.append(
                f"{e.msg} at line {e.lineno}, column {e.colno} "
                f"(character {e.pos})"
            )

    start = text.find("{")
    if start < 0:
        return None, "no opening '{' found"

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    return (obj, None) if isinstance(obj, dict) else (
                        None,
                        f"balanced span is {type(obj).__name__}, expected object",
                    )
                except json.JSONDecodeError as e:
                    errors.append(
                        f"balanced object: {e.msg} at line {e.lineno}, "
                        f"column {e.colno} (character {e.pos})"
                    )
                break

    return None, "; ".join(errors[-3:]) or "no parseable JSON object found"


def interpret(resp: dict) -> tuple[str, str | None, dict | None, str]:
    """Return (outcome, message, candidate, finish_reason)."""
    if "http_error" in resp:
        blocked = "ExceededBudget" in resp.get("detail", "")
        return (
            REFUSED if blocked else ERROR,
            resp.get("detail", "")[:200],
            None,
            "http_error",
        )

    if "error" in resp:
        return ERROR, resp["error"][:200], None, "error"

    choices = resp.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    finish = choices[0].get("finish_reason") or "?"

    # Bei Reasoning-Modellen ist reasoning_content nicht die eigentliche Antwort.
    content = msg.get("content")

    if not content:
        if msg.get("reasoning_content"):
            return (
                TRUNCATED,
                "content empty; the whole budget went to reasoning_content",
                None,
                finish,
            )
        return UNPARSEABLE, "empty content", None, finish

    obj, parse_error = extract_json(content)

    if obj is None:
        if finish == "length":
            return (
                TRUNCATED,
                f"cut off or invalid JSON: {parse_error}",
                None,
                finish,
            )
        return UNPARSEABLE, f"invalid JSON: {parse_error}", None, finish

    if finish == "length":
        return (
            TRUNCATED,
            "finish_reason=length but an object was recovered",
            obj,
            finish,
        )

    return OK, None, obj, finish


def reply_content(resp: dict) -> str:
    choices = resp.get("choices") or [{}]
    return ((choices[0].get("message") or {}).get("content") or "").strip()


def json_error_context(text: str, radius: int = 200) -> str | None:
    """The reply around the first syntax error, with the error position marked.

    raw_decode, not loads: it stops at the end of the object, so prose after
    the JSON does not move the reported position to the wrong place.
    """
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        json.JSONDecoder().raw_decode(text, start)
        return None
    except json.JSONDecodeError as e:
        pos = e.pos
    lo, hi = max(0, pos - radius), min(len(text), pos + radius)
    return (("…" if lo else "") + text[lo:pos] + "⟪HERE⟫" + text[pos:hi]
            + ("…" if hi < len(text) else ""))


def format_repair_instruction(outcome: str, message: str | None,
                              content: str, attempt: int) -> str:
    if outcome == TRUNCATED:
        why = ("Your reply hit the output token limit before the JSON object was "
               "complete, so it cannot be parsed.")
        ask = ("Return the complete object again, more compactly: drop optional "
               "`_comment` prose rather than any data.")
    else:
        why = "Your reply is not valid JSON, so it could not be parsed."
        ask = ("Fix the JSON syntax and return the complete corrected object. "
               "Do not change any content that was not part of the error.")
    context = json_error_context(content)
    where = (f"\nThe reply around the error (⟪HERE⟫ marks the parser's "
             f"position):\n\n{context}\n" if context else "")
    return f"""{why}

Parser error: {message or "(none)"}
{where}
{ask}
Return one JSON object and nothing else. This is JSON repair attempt {attempt}.
"""


def recover_json(endpoint: str, key: str, model: str, history: list[dict],
                 resp: dict, parsed: tuple, turns: int, mt: int, temp: float,
                 tmo: float, repair_root: Path, log_prefix: str = "    "
                 ) -> tuple[tuple, list[dict]]:
    """Give the model its own parse error back, up to `turns` times.

    `history` is the conversation that produced `resp`; `parsed` is
    interpret(resp). Returns the final interpret() tuple and one record per
    repair call. An empty reply, a refusal or an HTTP error is not a syntax
    problem and is not retried.
    """
    repairs: list[dict] = []
    for n in range(1, turns + 1):
        outcome, message, obj, _ = parsed
        content = reply_content(resp)
        if obj is not None or outcome not in (UNPARSEABLE, TRUNCATED) or not content:
            break
        history = history + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": format_repair_instruction(
                outcome, message, content, n)},
        ]
        started = time.time()
        resp, _ = call_messages(endpoint, key, model, history, mt, temp, tmo)
        seconds = round(time.time() - started, 1)
        out = repair_root / f"attempt-{n}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "raw.json").write_text(
            json.dumps(resp, indent=2, ensure_ascii=False)[:4_000_000],
            encoding="utf-8")
        parsed = interpret(resp)
        repairs.append({"attempt": n, "outcome": parsed[0], "message": parsed[1],
                        "finish_reason": parsed[3], "seconds": seconds,
                        "usage": resp.get("usage")})
        print(f"{log_prefix}json repair {n}: {parsed[0]} ({seconds:.1f}s)"
              + (f"  [{parsed[1]}]" if parsed[1] else ""), flush=True)
    return parsed, repairs


def write_score(out: Path, score: dict) -> None:
    """score.json for machines, score.txt for a person.

    The comparator has always been able to render itself -- `compare_benchmark.py
    CAND --case X` prints exactly this -- but the eval never called it, so a run
    directory held nothing a human could read and every question about one case
    meant grepping a 7 kB JSON. The causes go above the findings because they are
    the answer; the finding list below them is the evidence.
    """
    (out / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    clusters = CLU.cluster(score.get("findings", []))
    text = (f"{len(score.get('findings', []))} finding(s) from "
            f"{len(clusters)} cause(s)\n\n" + CLU.render(clusters) + "\n\n"
            + CMP.render(score, 12))
    (out / "score.txt").write_text(text, encoding="utf-8")


def score_candidate(out: Path, candidate: Path, reference: Path,
                    tol: float, record: dict) -> dict:
    score = CMP.compare(candidate, reference, tol, strict_files=False)
    write_score(out, score)
    record.update(
        overall=score["overall"], accuracy=score["accuracy"],
        validates=score["validation"]["score"],
        n_findings=len(score["findings"]),
        finding_counts=score["finding_counts"],
        sections={k: v["score"]
                  for k, v in score["scores"]["sections"].items()
                  if v["applicable"]},
    )
    return score


def execution_for_record(execution: dict) -> dict:
    """Keep run.json concise; full logs and transforms live in solver/attempt-N."""
    keys = ("status", "stage", "message", "seconds", "backend",
            "candidate_sha256", "output")
    return {key: execution.get(key) for key in keys if key in execution}


def repair_instruction(execution: dict, attempt: int) -> str:
    return f"""Your benchmark JSON was executed with THCM-FEM and did not produce a
usable query result. Repair the JSON using the diagnostic below.

Rules:
- Return one complete corrected JSON object and nothing else.
- Preserve the experiment and values supported by the paper; do not remove
  physics merely to make execution pass.
- Correct schema/API inconsistencies, geometry/group mismatches, unsupported
  declarations, time stepping, and result queries indicated by the log.
- Do not add reference files or other sibling files.
- This is solver-feedback repair attempt {attempt}.

{EXEC.feedback_text(execution)}
"""


def execute_existing_run(run_dir: Path, manifest: dict, args) -> int:
    if not (run_dir / "run.json").exists():
        sys.exit(f"no such run: {run_dir}")
    info = EXEC.preflight(args.solver_backend)
    print(f"solver preflight: ok ({info['backend']}, {info.get('oofem_version') or 'n/a'})")
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    by_case = {c["case"]: c for c in manifest["cases"]}
    selected = 0
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for rec in run["records"]:
        if args.models and rec["model"] not in args.models:
            continue
        if args.cases and rec["case"] not in args.cases:
            continue
        out = run_dir / rec["model"].replace("/", "_") / rec["case"].replace("/", "__")
        candidate = out / "candidate.json"
        if not candidate.exists() or rec["case"] not in by_case:
            continue
        attempt_dir = out / "solver" / f"manual-{stamp}"
        result = EXEC.execute(candidate, rec["case"], attempt_dir,
                              backend=args.solver_backend,
                              timeout=args.solver_timeout,
                              keep_vtu=args.keep_vtu)
        rec["execution"] = execution_for_record(result)
        rec.setdefault("solver_attempts", []).append(execution_for_record(result))
        selected += 1
        print(f"  {rec['model']:32s} {rec['case']:46s} "
              f"{result['status']:24s} {result['seconds']:7.1f}s", flush=True)
        # Persist after every expensive solve so an interrupted sweep is resumable.
        run["solver_environment"] = info
        (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"executed {selected} candidate(s) in {run_dir}")
    return 0


def main(argv=None) -> int:
    cfg = read_yaml(MODELS_YAML)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", help="model ids (default: models.yaml defaults)")
    ap.add_argument("--cases", nargs="+", help="dataset case names (default: all with a paper)")
    ap.add_argument("--limit", type=int, help="stop after N cases")
    ap.add_argument("--endpoint", default=cfg.get("endpoint"))
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--allow-no-pdf", action="store_true",
                    help="include cases with no paper (they get an empty paper text)")
    ap.add_argument("--max-paper-chars", type=int, default=400_000,
                    help="truncate very long papers (a thesis can be 500k chars)")
    ap.add_argument("--allow-metered", action="store_true",
                    help="permit models marked `metered: true` in worker/models.yaml; "
                         "without it they are refused, so a default run costs nothing")
    ap.add_argument("--probe", action="store_true",
                    help="probe all live free chat models, or the models selected "
                         "with --models, then exit without a sweep")
    ap.add_argument("--list-models", action="store_true",
                    help="what the endpoint serves right now, split into free "
                         "(KIT-hosted) and metered, and which models.yaml entries "
                         "it no longer knows")
    ap.add_argument("--rescore", metavar="RUN_ID",
                    help="re-score an existing run; makes no API calls")
    ap.add_argument("--run-solver", action="store_true",
                    help="execute each candidate with THCM-FEM after JSON scoring")
    ap.add_argument("--execute-run", metavar="RUN_ID",
                    help="execute candidates from an existing run; makes no API calls")
    ap.add_argument("--solver-backend", choices=("oofem", "fem"), default="oofem",
                    help="THCM-FEM backend (default: oofem)")
    ap.add_argument("--solver-timeout", type=float, default=900,
                    help="seconds allowed for each mesh and solve stage (default: 900)")
    ap.add_argument("--solver-feedback-turns", type=int, default=0, metavar="N",
                    help="up to N extra model calls using failed solver diagnostics")
    ap.add_argument("--json-repair-turns", type=int, default=1, metavar="N",
                    help="when a reply is not parseable JSON, send the parser error "
                         "back up to N times (default: 1; 0 disables). The record "
                         "keeps the first reply's outcome")
    ap.add_argument("--keep-vtu", action="store_true",
                    help="keep large VTU field output (off by default)")
    ap.add_argument("--solver-baseline", action="store_true",
                    help="also execute each curated reference JSON as a runner baseline")
    a = ap.parse_args(argv)

    if a.solver_feedback_turns < 0:
        ap.error("--solver-feedback-turns must be non-negative")
    if a.json_repair_turns < 0:
        ap.error("--json-repair-turns must be non-negative")
    if a.solver_timeout <= 0:
        ap.error("--solver-timeout must be positive")
    if a.solver_feedback_turns:
        a.run_solver = True
    if a.solver_baseline:
        a.run_solver = True

    load_env()
    # Before the dataset check: asking what is reachable must work whether or not
    # the corpus has been built.
    if a.list_models:
        return list_models(a.endpoint, api_key(), cfg)

    known = {m["id"]: m for m in cfg.get("models", [])}
    if a.probe:
        return probe_models(a.endpoint, api_key(), cfg, known, a.models,
                            a.allow_metered)

    manifest_path = DATASET / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no dataset at {DATASET} — run: python worker/tools/build_dataset.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if a.rescore:
        return rescore(RUNS / a.rescore, manifest, a.tol)
    if a.execute_run:
        return execute_existing_run(RUNS / a.execute_run, manifest, a)

    solver_info = None
    if a.run_solver:
        try:
            solver_info = EXEC.preflight(a.solver_backend)
        except RuntimeError as exc:
            sys.exit(f"solver preflight failed before API calls: {exc}")
        print(f"solver preflight: ok ({solver_info['backend']}, "
              f"{solver_info.get('oofem_version') or 'n/a'})", flush=True)

    key = api_key()
    if a.models:
        models = [known.get(m, {"id": m}) for m in a.models]
    else:
        models = [m for m in cfg.get("models", []) if m.get("default")]
    if not models:
        sys.exit("no models selected")

    # A sweep is 21 calls per model of ~60k input tokens each. Spending money on
    # that has to be asked for, not defaulted into by naming a model.
    metered = [m["id"] for m in models if known.get(m["id"], m).get("metered")]
    if metered and not a.allow_metered:
        sys.exit(f"these models are metered: {', '.join(metered)}\n"
                 f"  re-run with --allow-metered to spend, or pick KIT-hosted models "
                 f"(they are unmetered).\n"
                 f"  `run_eval.py --probe` shows what is reachable.")

    cases = [c for c in manifest["cases"]
             if (a.allow_no_pdf or c["paper_text"])
             and (not a.cases or c["case"] in a.cases)]
    if a.cases:
        missing = set(a.cases) - {c["case"] for c in cases}
        if missing:
            sys.exit(f"unknown or paper-less case(s): {', '.join(sorted(missing))}")
    cases = cases[:a.limit] if a.limit else cases
    if not cases:
        sys.exit("no cases selected")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    defaults = cfg.get("defaults", {})
    records = []
    baselines = {}

    print(f"run {run_id}: {len(models)} model(s) x {len(cases)} case(s) "
          f"= {len(models) * len(cases)} calls -> {run_dir}", flush=True)

    if a.solver_baseline:
        print("running curated solver baselines:", flush=True)
        for case in cases:
            reference = DATASET / case["json"]
            baseline_dir = run_dir / "_baseline" / case["case"].replace("/", "__")
            result = EXEC.execute(reference, case["case"], baseline_dir,
                                  backend=a.solver_backend,
                                  timeout=a.solver_timeout,
                                  keep_vtu=a.keep_vtu, keep_references=True)
            baselines[case["case"]] = execution_for_record(result)
            plots = len((result.get("output") or {}).get("plots") or [])
            print(f"  {case['case']:46s} {result['status']:24s} "
                  f"{result['seconds']:7.1f}s  {plots} plot(s)", flush=True)
            (run_dir / "run.json").write_text(json.dumps({
                "run_id": run_id, "endpoint": a.endpoint, "tolerance": a.tol,
                "models": [m["id"] for m in models],
                "cases": [c["case"] for c in cases],
                "solver_environment": solver_info,
                "solver_baselines": baselines,
                "solver_feedback_turns": a.solver_feedback_turns,
                "records": records,
            }, indent=2), encoding="utf-8")

    for model in models:
        mid = model["id"]
        mt = int(model.get("max_tokens", defaults.get("max_tokens", 32000)))
        temp = float(model.get("temperature", defaults.get("temperature", 0.0)))
        tmo = float(model.get("timeout_seconds", defaults.get("timeout_seconds", 900)))
        for case in cases:
            out = run_dir / mid.replace("/", "_") / case["case"].replace("/", "__")
            out.mkdir(parents=True, exist_ok=True)
            paper = ""
            if case["paper_text"]:
                paper = (DATASET / case["paper_text"]).read_text(encoding="utf-8")
            scope = "(no case scope provided)"
            if case.get("scope"):
                scope_path = DATASET / case["scope"]
                if scope_path.exists():
                    scope = scope_path.read_text(encoding="utf-8")
            truncated_paper = len(paper) > a.max_paper_chars
            if truncated_paper:
                paper = paper[:a.max_paper_chars] + "\n\n[paper truncated]\n"
            prompt = build_prompt(paper, scope)

            t0 = time.time()
            resp, err = call(a.endpoint, key, mid, prompt, mt, temp, tmo)
            elapsed = round(time.time() - t0, 1)
            (out / "raw.json").write_text(
                json.dumps(resp, indent=2, ensure_ascii=False)[:4_000_000], encoding="utf-8")

            outcome, message, obj, finish = interpret(resp)
            rec = {"model": mid, "case": case["case"], "outcome": outcome,
                   "finish_reason": finish, "message": message,
                   "seconds": elapsed, "prompt_chars": len(prompt),
                   "scope_chars": len(scope),
                   "paper_truncated": truncated_paper,
                   "usage": resp.get("usage"), "overall": None}
            cand = out / "candidate.json"
            ref = DATASET / case["json"]

            def save_and_score(value: dict) -> None:
                cand.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
                score_candidate(out, cand, ref, a.tol, rec)

            def score_text() -> str:
                return ("—" if rec["overall"] is None
                        else f"{rec['accuracy']:.3f}/{rec['n_findings']:>3d}f")

            if obj is not None:
                save_and_score(obj)
            # The case line comes first, so the indented json-repair and solver
            # lines below it read as belonging to this case.
            # flush: a sweep is minutes per call, and stdout block-buffers when it
            # is redirected to a log -- without this the run looks hung.
            flag = "" if outcome == OK else f"  [{outcome}: {message}]"
            print(f"  {mid:32s} {case['case']:46s} {score_text():>10s}  "
                  f"{elapsed:6.1f}s{flag}", flush=True)

            # `outcome` stays the first reply's, so the parse rate still measures
            # the model; the repaired object is scored for its content.
            if obj is None and a.json_repair_turns:
                parsed, json_repairs = recover_json(
                    a.endpoint, key, mid, [{"role": "user", "content": prompt}],
                    resp, (outcome, message, obj, finish), a.json_repair_turns,
                    mt, temp, tmo, out / "json-repairs")
                if json_repairs:
                    rec["json_repairs"] = json_repairs
                    rec["json_repaired"] = parsed[2] is not None
                obj = parsed[2]
                if obj is not None:
                    save_and_score(obj)
                    print(f"    scored after json repair: {score_text()}", flush=True)

            if obj is not None:
                rec["initial_accuracy"] = rec["accuracy"]

                if a.run_solver:
                    (out / "candidate.attempt-0.json").write_text(
                        json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
                    solver_attempts = []
                    repairs = []
                    current_obj = obj
                    for solver_attempt in range(a.solver_feedback_turns + 1):
                        result = EXEC.execute(
                            cand, case["case"], out / "solver" / f"attempt-{solver_attempt}",
                            backend=a.solver_backend, timeout=a.solver_timeout,
                            keep_vtu=a.keep_vtu)
                        solver_attempts.append(execution_for_record(result))
                        plots = len((result.get("output") or {}).get("plots") or [])
                        print(f"    solver attempt {solver_attempt}: {result['status']} "
                              f"({result['seconds']:.1f}s, {plots} plot(s))", flush=True)
                        if result["status"] == "ok" or \
                                solver_attempt >= a.solver_feedback_turns:
                            break

                        repair_no = solver_attempt + 1
                        repair_dir = out / "repairs" / f"attempt-{repair_no}"
                        repair_dir.mkdir(parents=True, exist_ok=True)
                        messages = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": json.dumps(
                                current_obj, ensure_ascii=False)},
                            {"role": "user", "content": repair_instruction(
                                result, repair_no)},
                        ]
                        repair_started = time.time()
                        repair_resp, _ = call_messages(
                            a.endpoint, key, mid, messages, mt, temp, tmo)
                        repair_seconds = round(time.time() - repair_started, 1)
                        (repair_dir / "raw.json").write_text(
                            json.dumps(repair_resp, indent=2, ensure_ascii=False)[:4_000_000],
                            encoding="utf-8")
                        repair_parsed = interpret(repair_resp)
                        repair_record = {
                            "attempt": repair_no, "outcome": repair_parsed[0],
                            "message": repair_parsed[1], "finish_reason": repair_parsed[3],
                            "seconds": repair_seconds, "usage": repair_resp.get("usage"),
                        }
                        repairs.append(repair_record)
                        print(f"    solver repair {repair_no}: {repair_parsed[0]} "
                              f"({repair_seconds:.1f}s)"
                              + (f"  [{repair_parsed[1]}]" if repair_parsed[1] else ""),
                              flush=True)
                        repair_parsed, json_repairs = recover_json(
                            a.endpoint, key, mid, messages, repair_resp, repair_parsed,
                            a.json_repair_turns, mt, temp, tmo,
                            repair_dir / "json-repairs", log_prefix="      ")
                        if json_repairs:
                            repair_record["json_repairs"] = json_repairs
                        repaired_obj = repair_parsed[2]
                        if repaired_obj is None:
                            break
                        current_obj = repaired_obj
                        (out / f"candidate.attempt-{repair_no}.json").write_text(
                            json.dumps(current_obj, indent=2,
                                       ensure_ascii=False) + "\n", encoding="utf-8")
                        save_and_score(current_obj)
                    rec["solver_attempts"] = solver_attempts
                    rec["execution"] = solver_attempts[-1]
                    rec["repairs"] = repairs
                    rec["solver_seconds"] = round(sum(
                        x.get("seconds", 0) for x in solver_attempts), 3)
                    rec["repair_seconds"] = round(sum(
                        x.get("seconds", 0) for x in repairs), 1)
                    if case["case"] in baselines:
                        rec["solver_baseline"] = baselines[case["case"]]
                        rec["execution_comparable"] = (
                            baselines[case["case"]].get("status") == "ok")
                    if rec["accuracy"] != rec["initial_accuracy"]:
                        print(f"    scored after solver repair: {score_text()}",
                              flush=True)
            records.append(rec)

            # A solver-enabled run can last hours. Preserve progress after each
            # candidate rather than waiting for the complete sweep.
            (run_dir / "run.json").write_text(json.dumps({
                "run_id": run_id, "endpoint": a.endpoint, "tolerance": a.tol,
                "models": [m["id"] for m in models],
                "cases": [c["case"] for c in cases],
                "solver_environment": solver_info,
                "solver_baselines": baselines,
                "solver_feedback_turns": (
                    a.solver_feedback_turns if a.run_solver else 0),
                "records": records,
            }, indent=2), encoding="utf-8")

    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "endpoint": a.endpoint, "tolerance": a.tol,
        "models": [m["id"] for m in models], "cases": [c["case"] for c in cases],
        "solver_environment": solver_info,
        "solver_baselines": baselines,
        "solver_feedback_turns": a.solver_feedback_turns if a.run_solver else 0,
        "records": records,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {run_dir / 'run.json'}\n"
          f"report:  worker/worker.sh eval --report {run_id}")
    return 0


def fetch_models(endpoint: str, key: str) -> list:
    req = urllib.request.Request(f"{endpoint.rstrip('/')}/models",
                                 headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()).get("data", [])


def is_chat_model(model: dict) -> bool:
    """The endpoint marks image/audio/embedding models as hidden."""
    meta = ((model.get("info") or {}).get("meta") or {})
    return not meta.get("hidden")


def probe_models(endpoint: str, key: str, cfg: dict, known: dict,
                 requested: list[str] | None, allow_metered: bool) -> int:
    """Resolve probe targets against the live roster before making chat calls.

    With no explicit selection, probe every free chat route advertised right
    now. This includes moving aliases, which belong in a reachability check but
    deliberately do not belong in a reproducible benchmark roster. Explicit
    selections may include metered models, but only behind --allow-metered.
    """
    try:
        served = fetch_models(endpoint, key)
    except Exception as e:
        sys.exit(f"could not list models before probing: {type(e).__name__}: {e}")

    live = {m["id"]: m for m in served if is_chat_model(m)}
    if requested:
        target_ids = list(dict.fromkeys(requested))
        missing = [mid for mid in target_ids if mid not in live]
        if missing:
            print("configured/requested but not served by the endpoint:")
            for mid in missing:
                print(f"  {mid}")
        target_ids = [mid for mid in target_ids if mid in live]
    else:
        target_ids = sorted(mid for mid, model in live.items()
                            if model.get("connection_type") == "local")

    metered = [mid for mid in target_ids
               if live[mid].get("connection_type") != "local"]
    if metered and not allow_metered:
        sys.exit("these probe targets are metered: " + ", ".join(metered) + "\n"
                 "  pass --allow-metered only if you intend to call them")
    if not target_ids:
        return 1

    defaults = cfg.get("defaults", {})
    timeout = float(defaults.get("probe_timeout_seconds", 180))
    targets = [known.get(mid, {"id": mid}) for mid in target_ids]
    return probe(endpoint, key, targets, timeout, known)


def list_models(endpoint: str, key: str, cfg: dict) -> int:
    """What the endpoint serves RIGHT NOW, and which half of it is free.

    `connection_type` is the endpoint's own answer to the budget question and it
    is exact: `local` is hosted at KIT and unmetered, `external` is routed out
    and billed. That beats matching on the `kit.` prefix, which is a convention
    the roster is under no obligation to keep.

    The roster drifts -- between the 2026-08-28 run and 2026-09-11 four of the
    eight models in models.yaml stopped resolving -- so this also reports which
    configured entries the endpoint no longer knows. A dead entry is not a
    warning at sweep time; it is one wasted call per case.
    """
    try:
        served = fetch_models(endpoint, key)
    except Exception as e:
        sys.exit(f"could not list models: {type(e).__name__}: {e}")

    known = {m["id"]: m for m in cfg.get("models", [])}
    free, metered, other = [], [], []
    for m in served:
        info = m.get("info") or {}
        meta = info.get("meta") or {}
        row = (m["id"], str(meta.get("description") or "").split("\n")[0][:78])
        # `hidden` marks the embedding, rerank, transcription and image models.
        # They share the endpoint and cannot author anything.
        if not is_chat_model(m):
            other.append(m["id"])
        elif m.get("connection_type") == "local":
            free.append(row)
        else:
            metered.append(row)

    def show(rows):
        for mid, desc in sorted(rows):
            mark = ""
            if mid in known:
                mark = " [default]" if known[mid].get("default") else " [in models.yaml]"
            print(f"  {mid:34s}{mark}")
            if desc:
                print(f"      {desc}")

    print(f"{endpoint}  —  {len(served)} model(s)\n")
    print(f"FREE — KIT-hosted (connection_type=local), unmetered, a sweep costs nothing")
    show(free)
    print(f"\nMETERED — routed externally, billed against your KIT budget "
          f"(need --allow-metered)")
    show(metered)
    if other:
        print(f"\nnot chat models: {', '.join(sorted(other))}")

    dead = sorted(set(known) - {m["id"] for m in served})
    if dead:
        print(f"\nIN models.yaml BUT NO LONGER SERVED — every call to these fails:")
        for mid in dead:
            print(f"  {mid}")
    return 0


def probe(endpoint: str, key: str, models: list, timeout: float = 180,
          known: dict | None = None) -> int:
    """Which models answer at all. Cheap, and it separates "the model is bad" from
    "you are over budget", which otherwise both arrive as a failed sweep."""
    print(f"{endpoint}\n")
    known = known or {m["id"]: m for m in models}

    def check(m: dict) -> tuple[dict, str]:
        resp, _ = call(endpoint, key, m["id"],
                       "Reply with the single word: ok", 64, 0.0, timeout)
        if "choices" in resp:
            verdict = "reachable"
        elif "ExceededBudget" in str(resp.get("detail", "")):
            verdict = "REFUSED — over budget"
        else:
            verdict = f"error — {str(resp.get('detail') or resp.get('error'))[:70]}"
        return m, verdict

    # A slow hardware-limited route must not hold up every other independent
    # reachability check. Print each result as soon as it completes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(models))) as pool:
        futures = [pool.submit(check, m) for m in models]
        for future in concurrent.futures.as_completed(futures):
            m, verdict = future.result()
            configured = known.get(m["id"])
            if configured:
                mark = (" [default]" if configured.get("default")
                        else " [in models.yaml]")
            else:
                mark = " [live only]"
            print(f"  {m['id']:34s} {verdict}{mark}", flush=True)
    return 0


def rescore(run_dir: Path, manifest: dict, tol: float) -> int:
    if not run_dir.exists():
        sys.exit(f"no such run: {run_dir}")
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    by_case = {c["case"]: c for c in manifest["cases"]}
    n = 0
    for rec in run["records"]:
        out = run_dir / rec["model"].replace("/", "_") / rec["case"].replace("/", "__")
        cand = out / "candidate.json"
        if not cand.exists():
            continue
        score = CMP.compare(cand, DATASET / by_case[rec["case"]]["json"], tol, False)
        write_score(out, score)
        rec.update(overall=score["overall"], accuracy=score["accuracy"],
                   validates=score["validation"]["score"],
                   n_findings=len(score["findings"]),
                   finding_counts=score["finding_counts"],
                   sections={k: v["score"]
                             for k, v in score["scores"]["sections"].items()
                             if v["applicable"]})
        n += 1
    run["tolerance"] = tol
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"rescored {n} candidate(s) in {run_dir} at tol={tol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
