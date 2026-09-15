#!/usr/bin/env python3
"""
explain.py -- hand the reference back to an analyst and ask what went wrong.

    worker/worker.sh eval --explain                     # latest run, worst 5 per model
    worker/worker.sh eval --explain RUN_ID --worst 0    # every scored candidate
    worker/worker.sh eval --explain RUN_ID --rollup-only # re-write the summary only
    worker/worker.sh eval --explain RUN_ID --no-paper   # cheaper; loses "the paper says"

Scoring answers *what differs*. It cannot answer *why*, because `score.json`
holds a candidate value and a reference value and no source -- "u_ref 15 vs 10"
is unexplainable without the paper. This pass supplies the paper, the reference
and the candidate to one analyst model and asks for the diagnosis.

## Two things this is deliberately NOT

**Not self-critique.** The analyst is ONE fixed model for every (model, case),
never the model under audit. If each model diagnosed its own output the summary
quality would track the model's ability -- the weak model gets the weak
diagnosis -- and this eval's entire premise is one identical prompt so that a
31-billion-parameter model and a frontier model stay comparable. A per-model
analyst would put that back.

**Not part of the score.** Accuracy stays exactly what `compare_benchmark.py`
computed, offline and reproducible. This writes beside it, the way `validates`
does. A narrative that moved a leaderboard number would make the leaderboard
unreproducible.

## Grounded, so a claim can be checked

The analyst must assign every finding index to exactly one group. Coverage is
then verified mechanically -- uncovered, duplicated and invented indices are all
recorded in `explain.json`. A summary that does not account for the findings is
still written, but it is marked, and `report.py` says so.

That check also buys a signal the pipeline had no way to produce: each group is
tagged `model_error`, `comparator_artifact` or `paper_ambiguous`, so the run
ends with an artifact rate per comparator rule -- where to fix
`compare_benchmark.py` rather than guess.

    runs/<run-id>/
      <model>/<case>/explain.json   the analyst's grouped diagnosis + coverage
      explain.md                    the run-level summary (one extra call)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/jobs/eval
sys.path.insert(0, str(HERE))
import run_eval as RE                           # noqa: E402  (path set above)

sys.path.insert(0, str(RE.WORKER / "tools"))
import cluster_findings as CLU                  # noqa: E402

RUNS = RE.RUNS
DATASET = RE.DATASET

# Bumped when a prompt template changes in a way that invalidates cached output.
# The cache key is (candidate bytes, reference bytes, this) -- so a re-run after
# a rescore is free, and a re-run after a prompt edit is not silently stale.
PROMPT_VERSION = 1

CAUSES = ("model_error", "comparator_artifact", "paper_ambiguous")


def sha(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()[:16]


def pick_analyst(cfg: dict, requested: str | None) -> dict:
    """One model does every diagnosis in the run. `analyst: true` in models.yaml
    names the default; --analyst overrides it for a single run."""
    models = cfg.get("models", [])
    if requested:
        for m in models:
            if m["id"] == requested:
                return m
        return {"id": requested}
    for m in models:
        if m.get("analyst"):
            return m
    sys.exit("no analyst model: mark one `analyst: true` in worker/models.yaml, "
             "or pass --analyst MODEL")


def number_findings(findings: list) -> str:
    """The analyst cites these indices, so they are the same indices
    `cluster_findings` stamps and the same order `score.json` stores."""
    L = []
    for i, f in enumerate(findings):
        cand = "—" if f["candidate"] is None else json.dumps(f["candidate"], default=str)
        ref = "—" if f["reference"] is None else json.dumps(f["reference"], default=str)
        line = (f"[{i}] {f['section']}/{f['kind']}  {f['what']}\n"
                f"     candidate: {cand[:200]}\n"
                f"     reference: {ref[:200]}")
        if f.get("note"):
            line += f"\n     note: {f['note']}"
        L.append(line)
    return "\n".join(L)


def build_case_prompt(model: str, case: str, score: dict, candidate: dict,
                      reference: dict, paper: str) -> str:
    tpl = (HERE / "prompts" / "explain_case.md").read_text(encoding="utf-8")
    clusters = CLU.cluster(score["findings"])
    hint = "\n".join(
        f"- ({', '.join(str(f['_i']) for f in c.findings)}) {c.title}"
        for c in clusters)
    return (tpl
            .replace("{MODEL}", model)
            .replace("{CASE}", case)
            .replace("{FINDINGS}", number_findings(score["findings"]))
            .replace("{CLUSTERS}", hint or "(none)")
            .replace("{REFERENCE}", json.dumps(reference, indent=1, ensure_ascii=False))
            .replace("{CANDIDATE}", json.dumps(candidate, indent=1, ensure_ascii=False))
            .replace("{PAPER}", paper or "(the paper text was not supplied for this pass)"))


def check_coverage(groups: list, n_findings: int) -> dict:
    """Did the diagnosis actually account for the findings?

    A narrative is only worth trusting if every mechanical difference it claims
    to explain is one that exists, and every difference that exists is claimed.
    Both directions are recorded rather than enforced: a good summary with one
    stray index is still worth having, as long as the reader is told.
    """
    seen = Counter()
    invalid = []
    for g in groups:
        for i in g.get("finding_indices") or []:
            if isinstance(i, int) and 0 <= i < n_findings:
                seen[i] += 1
            else:
                invalid.append(i)
    return {
        "findings": n_findings,
        "covered": len(seen),
        "uncovered": sorted(set(range(n_findings)) - set(seen)),
        "duplicated": sorted(i for i, n in seen.items() if n > 1),
        "invalid": invalid,
        "complete": len(seen) == n_findings and not invalid
                    and not any(n > 1 for n in seen.values()),
    }


def normalise(obj: dict, n_findings: int) -> dict:
    """Keep what the schema promised and drop the rest, so a downstream reader
    never has to guard against a field the analyst improvised."""
    groups = []
    for g in obj.get("groups") or []:
        if not isinstance(g, dict):
            continue
        cause = str(g.get("cause") or "").strip()
        groups.append({
            "finding_indices": [i for i in (g.get("finding_indices") or [])
                                if isinstance(i, int)],
            "title": str(g.get("title") or "").strip(),
            # An unrecognised cause is recorded as-is rather than coerced: a
            # silent remap to model_error would corrupt the artifact rate, which
            # is the number this pass exists to produce.
            "cause": cause if cause in CAUSES else f"?{cause}",
            "why": str(g.get("why") or "").strip(),
            "fix": str(g.get("fix") or "").strip(),
        })
    return {"verdict": str(obj.get("verdict") or "").strip(),
            "groups": groups,
            "coverage": check_coverage(groups, n_findings)}


def explain_case(out: Path, rec: dict, paper: str, analyst: dict, endpoint: str,
                 key: str, defaults: dict, force: bool) -> dict | None:
    """One (model, case). Returns the diagnosis, or None if there was nothing to
    diagnose or the call failed."""
    cand_path, score_path = out / "candidate.json", out / "score.json"
    if not (cand_path.exists() and score_path.exists()):
        return None
    score = json.loads(score_path.read_text(encoding="utf-8"))
    if not score.get("findings"):
        return None

    cand_bytes = cand_path.read_bytes()
    ref_path = DATASET / "json" / f"{rec['case'].replace('/', '__')}.json"
    stamp = sha(cand_bytes, ref_path.read_bytes(), str(PROMPT_VERSION).encode(),
                analyst["id"].encode(), paper[:1000].encode())
    dest = out / "explain.json"
    if dest.exists() and not force:
        try:
            prev = json.loads(dest.read_text(encoding="utf-8"))
            if prev.get("stamp") == stamp:
                return prev
        except Exception:
            pass

    prompt = build_case_prompt(
        rec["model"], rec["case"], score,
        json.loads(cand_bytes.decode("utf-8")),
        json.loads(ref_path.read_text(encoding="utf-8")), paper)

    t0 = time.time()
    resp, _ = RE.call(endpoint, key, analyst["id"], prompt,
                      int(analyst.get("max_tokens", defaults.get("max_tokens", 16000))),
                      float(analyst.get("temperature", 0.0)),
                      float(analyst.get("timeout_seconds",
                                        defaults.get("timeout_seconds", 900))))
    outcome, message, obj, _ = RE.interpret(resp)
    if obj is None:
        print(f"      [{outcome}: {message}]", flush=True)
        return None

    diag = {"stamp": stamp, "analyst": analyst["id"], "model": rec["model"],
            "case": rec["case"], "seconds": round(time.time() - t0, 1),
            "usage": resp.get("usage"), **normalise(obj, len(score["findings"]))}
    dest.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
    return diag


def case_md(diag: dict) -> list:
    """One case's diagnosis, as the run document renders it."""
    SYM = {"model_error": "model", "comparator_artifact": "ARTIFACT",
           "paper_ambiguous": "paper"}
    L = [f"#### `{diag['model']}` — {diag['verdict']}", ""]
    cov = diag.get("coverage") or {}
    if not cov.get("complete"):
        L += [f"> Coverage incomplete: {cov.get('covered')}/{cov.get('findings')} "
              f"findings accounted for"
              + (f", {len(cov['invalid'])} invented index(es)" if cov.get("invalid") else "")
              + ". Read the groups below as partial.", ""]
    L += ["| n | cause | what | why | fix |", "|---:|---|---|---|---|"]
    for g in diag["groups"]:
        L.append(f"| {len(g['finding_indices'])} | {SYM.get(g['cause'], g['cause'])} | "
                 f"{g['title']} | {g['why']} | {g['fix']} |")
    return L + [""]


def build_rollup_prompt(run: dict, diags: list) -> str:
    """The one call that pays for itself.

    It sees only the grouped diagnoses, never a paper or a spec, so it is cheap
    and it runs even when --worst diagnosed a handful of cases. The coverage line
    matters: on a partial pass the scores table describes the whole run and the
    diagnoses describe a slice of it, and without being told so an analyst will
    write "every model" about a sample of one.
    """
    tpl = (HERE / "prompts" / "explain_run.md").read_text(encoding="utf-8")
    by_model = defaultdict(list)
    for r in run["records"]:
        by_model[r["model"]].append(r)

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    rows = ["| model | accuracy | validates | findings/case | cases |",
            "|---|---:|---:|---:|---:|"]
    for m, rs in sorted(by_model.items(),
                        key=lambda kv: -(mean(r.get("accuracy") for r in kv[1]) or -1)):
        acc, val = mean(r.get("accuracy") for r in rs), mean(r.get("validates") for r in rs)
        nf = mean(r.get("n_findings") for r in rs)
        rows.append(f"| {m} | {acc:.3f} | {val:.3f} | {nf:.0f} | {len(rs)} |"
                    if acc is not None else f"| {m} | — | — | — | {len(rs)} |")

    blocks = []
    for d in diags:
        gs = "\n".join(
            f"  - [{g['cause']}] ({len(g['finding_indices'])} findings) {g['title']} "
            f"— {g['why']} — fix: {g['fix']}" for g in d["groups"])
        blocks.append(f"### {d['case']} / {d['model']}\n{d['verdict']}\n{gs}")
    scored = sum(1 for r in run["records"] if r.get("accuracy") is not None)
    pairs = sorted({(d["model"], d["case"]) for d in diags})
    coverage = (f"**{len(pairs)} of this run's {scored} scored (model, case) pairs "
                f"were diagnosed**, and only those are evidence for anything you "
                f"write: " + ", ".join(f"`{m}`/`{c}`" for m, c in pairs[:40])
                + ("" if len(pairs) <= 40 else f", and {len(pairs) - 40} more") + ".")
    return (tpl.replace("{SCORES}", "\n".join(rows))
               .replace("{COVERAGE}", coverage)
               .replace("{GROUPS}", "\n\n".join(blocks)))


def rollup(run: dict, diags: list, analyst: dict, endpoint: str,
           key: str, defaults: dict) -> str | None:
    """One call for the whole run. The cheapest part of this pass and the part
    that actually feeds back into CREATE.md, so it runs even when only a handful
    of cases were diagnosed."""
    prompt = build_rollup_prompt(run, diags)
    resp, _ = RE.call(endpoint, key, analyst["id"], prompt,
                      int(defaults.get("max_tokens", 16000)), 0.3,
                      float(defaults.get("timeout_seconds", 900)))
    outcome, message, _, _ = RE.interpret(resp)
    content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not content:
        print(f"  rollup failed [{outcome}: {message}]", flush=True)
        return None
    return content.strip()


def main(argv=None) -> int:
    cfg = RE.read_yaml(RE.MODELS_YAML)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?", help="default: the latest run")
    ap.add_argument("--analyst", help="model that writes the diagnoses "
                                      "(default: `analyst: true` in models.yaml)")
    ap.add_argument("--worst", type=int, default=5,
                    help="diagnose the N lowest-scoring cases per model; 0 = all "
                         "(default 5)")
    ap.add_argument("--models", nargs="+", help="only these models under audit")
    ap.add_argument("--cases", nargs="+", help="only these cases")
    ap.add_argument("--no-paper", action="store_true",
                    help="omit the paper text — much cheaper, but the analyst can "
                         "no longer say what the paper actually states")
    ap.add_argument("--max-paper-chars", type=int, default=80_000)
    ap.add_argument("--rollup-only", action="store_true",
                    help="re-write the run summary from existing explain.json files")
    ap.add_argument("--force", action="store_true", help="ignore cached diagnoses")
    ap.add_argument("--endpoint", default=cfg.get("endpoint"))
    ap.add_argument("--allow-metered", action="store_true",
                    help="permit an analyst marked `metered: true`")
    a = ap.parse_args(argv)

    RE.load_env()
    run_id = a.run_id or latest()
    run_dir = RUNS / run_id
    run_path = run_dir / "run.json"
    if not run_path.exists():
        sys.exit(f"no such run: {run_path}")
    run = json.loads(run_path.read_text(encoding="utf-8"))

    analyst = pick_analyst(cfg, a.analyst)
    defaults = cfg.get("defaults", {})
    # The gate that protects a sweep protects this too: the analyst is normally
    # the most capable model available, which is normally the one that costs.
    if analyst.get("metered") and not a.allow_metered and not a.rollup_only:
        sys.exit(f"analyst `{analyst['id']}` is metered — re-run with --allow-metered, "
                 f"or pass --analyst with a KIT-hosted model (they are unmetered).")

    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    paper_of = {c["case"]: c["paper_text"] for c in manifest["cases"]}

    scored = [r for r in run["records"] if r.get("accuracy") is not None]
    if a.models:
        scored = [r for r in scored if r["model"] in a.models]
    if a.cases:
        scored = [r for r in scored if r["case"] in a.cases]
    # Worst-first: the cases with the most to explain are the ones worth paying
    # an analyst for, and a default pass should not cost a full sweep.
    if a.worst:
        per = defaultdict(list)
        for r in sorted(scored, key=lambda r: r["accuracy"]):
            per[r["model"]].append(r)
        scored = [r for rs in per.values() for r in rs[:a.worst]]

    diags = []
    if a.rollup_only:
        for p in sorted(run_dir.glob("*/*/explain.json")):
            try:
                diags.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        if not diags:
            sys.exit(f"no explain.json under {run_dir} — run without --rollup-only first")
        key = RE.api_key()
    else:
        key = RE.api_key()
        print(f"analyst `{analyst['id']}` · {len(scored)} case(s) "
              f"{'without' if a.no_paper else 'with'} paper text -> {run_dir}",
              flush=True)
        for rec in sorted(scored, key=lambda r: (r["case"], r["model"])):
            out = run_dir / rec["model"].replace("/", "_") / rec["case"].replace("/", "__")
            paper = ""
            if not a.no_paper and paper_of.get(rec["case"]):
                paper = (DATASET / paper_of[rec["case"]]).read_text(encoding="utf-8")
                if len(paper) > a.max_paper_chars:
                    paper = paper[:a.max_paper_chars] + "\n\n[paper truncated]\n"
            print(f"  {rec['model']:32s} {rec['case']:46s} ", end="", flush=True)
            d = explain_case(out, rec, paper, analyst, a.endpoint, key, defaults, a.force)
            if d:
                diags.append(d)
                cov = d["coverage"]
                print(f"{len(d['groups']):>2d} group(s)  "
                      f"{cov['covered']}/{cov['findings']} covered"
                      f"{'' if cov['complete'] else '  INCOMPLETE'}", flush=True)
            else:
                print("skipped", flush=True)

    if not diags:
        print("nothing diagnosed")
        return 1

    print("\nwriting the run summary ...", flush=True)
    text = rollup(run, diags, analyst, a.endpoint, key, defaults)

    L = [f"# What went wrong — run `{run_id}`", "",
         f"Diagnoses by `{analyst['id']}`, which is **not** one of the models under "
         f"audit. Nothing here changes a score: accuracy is what "
         f"`compare_benchmark.py` computed, and this sits beside it.", ""]
    if text:
        L += [text, ""]
    L += artifact_table(diags)
    L += ["", "## Case by case", ""]
    for case in sorted({d["case"] for d in diags}):
        L += [f"### `{case}`", ""]
        for d in sorted((d for d in diags if d["case"] == case),
                        key=lambda d: d["model"]):
            L += case_md(d)

    dest = run_dir / "explain.md"
    dest.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {dest}")
    return 0


def artifact_table(diags: list) -> list:
    """Where the benchmark scores a difference that is not one.

    This is the number the pass exists to produce: an artifact rate tells you
    which comparator rule to fix, and every one fixed makes every future run's
    accuracy mean more.
    """
    by_cause = Counter()
    art_by_section = Counter()
    for d in diags:
        for g in d["groups"]:
            n = len(g["finding_indices"])
            by_cause[g["cause"]] += n
            if g["cause"] == "comparator_artifact":
                art_by_section[g["title"][:60]] += n
    total = sum(by_cause.values()) or 1
    L = ["", "## Findings by cause", "",
         "| cause | findings | share |", "|---|---:|---:|"]
    for cause, n in by_cause.most_common():
        L.append(f"| {cause} | {n} | {n / total:.0%} |")
    if art_by_section:
        L += ["", "The `comparator_artifact` share is the fraction of this run's "
                  "accuracy loss that is the benchmark's fault rather than a "
                  "model's. Most common:", ""]
        for title, n in art_by_section.most_common(10):
            L.append(f"- {n}x {title}")
    return L


def latest() -> str:
    runs = sorted(p.name for p in RUNS.glob("*") if (p / "run.json").exists())
    if not runs:
        sys.exit(f"no runs under {RUNS}")
    return runs[-1]


if __name__ == "__main__":
    sys.exit(main())
