#!/usr/bin/env python3
"""
run_eval.py -- can a model author a benchmark.json from a paper?

    worker/worker.sh eval --probe                     # what is reachable
    worker/worker.sh eval --cases cervera-1999-thermo-chemo-partI
    worker/worker.sh eval --models kit.gpt-oss-120b --limit 3
    worker/worker.sh eval --rescore RUN_ID            # re-score, no API calls

(worker.sh is the entry point; this script also runs directly.)

One identical single-shot call per (model, case): the guide, the physics modules,
the schema and the paper text in, one JSON object out. No tools, no phases, no
solver. That is the whole point -- it isolates "can it author the spec" from
"does the physics reproduce", and it keeps very different models comparable.

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
"""
from __future__ import annotations

import argparse
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


def build_prompt(paper_text: str) -> str:
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
            .replace("{PAPER}", paper_text))


def call(endpoint: str, key: str, model: str, prompt: str,
         max_tokens: int, temperature: float, timeout: float) -> tuple[dict, str | None]:
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}], "stream": False}
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


def extract_json(text: str) -> dict | None:
    """The reply should be one bare object. Models fence it, prefix it, or narrate
    around it anyway, so fall back to the largest balanced {...} span."""
    text = (text or "").strip()
    if not text:
        return None
    for candidate in (text, *re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)):
        try:
            obj = json.loads(candidate.strip())
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    start, depth, in_str, esc = text.find("{"), 0, False, False
    if start < 0:
        return None
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str, esc = True, False
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def interpret(resp: dict) -> tuple[str, str | None, dict | None, str]:
    """(outcome, message, candidate, finish_reason)."""
    if "http_error" in resp:
        blocked = "ExceededBudget" in resp.get("detail", "")
        return (REFUSED if blocked else ERROR,
                resp.get("detail", "")[:200], None, "http_error")
    if "error" in resp:
        return ERROR, resp["error"][:200], None, "error"
    choices = resp.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    finish = choices[0].get("finish_reason") or "?"
    # Reasoning models on this endpoint put the chain in `reasoning_content` and
    # the answer in `content`; reading the wrong one scores thinking, not output.
    content = msg.get("content")
    if not content:
        if msg.get("reasoning_content"):
            return (TRUNCATED, "content empty; the whole budget went to reasoning_content",
                    None, finish)
        return UNPARSEABLE, "empty content", None, finish
    obj = extract_json(content)
    if obj is None:
        if finish == "length":
            return TRUNCATED, "cut off mid-object (raise max_tokens)", None, finish
        return UNPARSEABLE, "no JSON object in the reply", None, finish
    if finish == "length":
        return TRUNCATED, "finish_reason=length but an object was recovered", obj, finish
    return OK, None, obj, finish


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
                    help="check which models answer, then exit without a sweep")
    ap.add_argument("--rescore", metavar="RUN_ID",
                    help="re-score an existing run; makes no API calls")
    a = ap.parse_args(argv)

    load_env()
    manifest_path = DATASET / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no dataset at {DATASET} — run: python worker/tools/build_dataset.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if a.rescore:
        return rescore(RUNS / a.rescore, manifest, a.tol)

    key = api_key()
    known = {m["id"]: m for m in cfg.get("models", [])}
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

    if a.probe:
        return probe(a.endpoint, key, list(known.values()) or models)

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

    print(f"run {run_id}: {len(models)} model(s) x {len(cases)} case(s) "
          f"= {len(models) * len(cases)} calls -> {run_dir}", flush=True)

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
            truncated_paper = len(paper) > a.max_paper_chars
            if truncated_paper:
                paper = paper[:a.max_paper_chars] + "\n\n[paper truncated]\n"
            prompt = build_prompt(paper)

            t0 = time.time()
            resp, err = call(a.endpoint, key, mid, prompt, mt, temp, tmo)
            elapsed = round(time.time() - t0, 1)
            (out / "raw.json").write_text(
                json.dumps(resp, indent=2, ensure_ascii=False)[:4_000_000], encoding="utf-8")

            outcome, message, obj, finish = interpret(resp)
            rec = {"model": mid, "case": case["case"], "outcome": outcome,
                   "finish_reason": finish, "message": message,
                   "seconds": elapsed, "prompt_chars": len(prompt),
                   "paper_truncated": truncated_paper,
                   "usage": resp.get("usage"), "overall": None}
            if obj is not None:
                cand = out / "candidate.json"
                cand.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
                ref = DATASET / case["json"]
                score = CMP.compare(cand, ref, a.tol, strict_files=False)
                (out / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
                rec["overall"] = score["overall"]
                rec["accuracy"] = score["accuracy"]
                rec["validates"] = score["validation"]["score"]
                rec["n_findings"] = len(score["findings"])
                rec["finding_counts"] = score["finding_counts"]
                rec["sections"] = {k: v["score"]
                                   for k, v in score["scores"]["sections"].items()
                                   if v["applicable"]}
            records.append(rec)

            flag = "" if outcome == OK else f"  [{outcome}: {message}]"
            score_txt = ("—" if rec["overall"] is None
                         else f"{rec['accuracy']:.3f}/{rec['n_findings']:>3d}f")
            # flush: a sweep is minutes per call, and stdout block-buffers when it
            # is redirected to a log -- without this the run looks hung.
            print(f"  {mid:32s} {case['case']:46s} {score_txt:>10s}  "
                  f"{elapsed:6.1f}s{flag}", flush=True)

    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "endpoint": a.endpoint, "tolerance": a.tol,
        "models": [m["id"] for m in models], "cases": [c["case"] for c in cases],
        "records": records,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {run_dir / 'run.json'}\n"
          f"report:  worker/worker.sh eval --report {run_id}")
    return 0


def probe(endpoint: str, key: str, models: list) -> int:
    """Which models answer at all. Cheap, and it separates "the model is bad" from
    "you are over budget", which otherwise both arrive as a failed sweep."""
    print(f"{endpoint}\n")
    for m in models:
        resp, _ = call(endpoint, key, m["id"],
                       "Reply with the single word: ok", 64, 0.0, 60)
        if "choices" in resp:
            verdict = "reachable"
        elif "ExceededBudget" in str(resp.get("detail", "")):
            verdict = "REFUSED — over budget"
        else:
            verdict = f"error — {str(resp.get('detail') or resp.get('error'))[:70]}"
        print(f"  {m['id']:34s} {verdict}")
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
        (out / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
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
