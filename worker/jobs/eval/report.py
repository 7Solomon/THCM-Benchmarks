#!/usr/bin/env python3
"""
report.py -- turn an eval run into a leaderboard and a misinterpretation census.

    worker/worker.sh eval --report RUN_ID [--md OUT.md]
    worker/worker.sh eval --report            # the latest run

Reads workspace/runs/<RUN_ID>/run.json. Makes no API calls, so it is safe to
re-run after `run_eval.py --rescore` changed the scoring.

Section scores stay separate in every table. "Model X scores 0.7" is not
actionable; "model X reproduces the physics declaration but gets a third of the
material constants wrong" is, and the two collapse to the same number.

The census at the end is the part worth reading on a wide sweep: the same
misreading committed by every model is a property of the PAPER or of the PROMPT,
not of any one model.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/jobs/eval
REPO = HERE.parent.parent.parent                # repo root
RUNS = Path(os.environ.get("EVAL_RUNS", REPO / "workspace" / "runs"))

sys.path.insert(0, str(REPO / "worker" / "tools"))
import cluster_findings as CLU                  # noqa: E402

# Column order for the section tables: strict first, because those are the ones
# that decide whether the spec is the same experiment.
STRICT_COLS = ["physics", "materials", "boundary_conditions", "initial_conditions"]
OTHER_COLS = ["geometry", "time_stepping", "metadata", "results.queries"]
SHORT = {"physics": "physics", "materials": "materials",
         "boundary_conditions": "BCs", "initial_conditions": "ICs",
         "geometry": "geom", "time_stepping": "time", "metadata": "meta",
         "results.queries": "queries"}


def latest() -> str:
    runs = sorted(p.name for p in RUNS.glob("*") if (p / "run.json").exists())
    if not runs:
        sys.exit(f"no runs under {RUNS}")
    return runs[-1]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(v, w=6):
    return f"{'—':>{w}}" if v is None else f"{v:{w}.3f}"


def section_mean(recs, section):
    return mean(r.get("sections", {}).get(section) for r in recs
                if isinstance(r.get("sections"), dict))


def build(run: dict, runs_root: Path) -> str:
    recs = run["records"]
    by_model = defaultdict(list)
    for r in recs:
        by_model[r["model"]].append(r)
    cols = STRICT_COLS + OTHER_COLS

    L = [f"# Authoring eval — run `{run['run_id']}`", "",
         f"Endpoint `{run['endpoint']}` · numeric tolerance "
         f"{run.get('tolerance', 0.01):.1%} · {len(recs)} calls "
         f"({len(by_model)} models × {len(run['cases'])} cases)", "",
         "Every model got the same initial authoring prompt: the authoring guide, "
         "physics modules, schema, case scope, and paper text. "
         + ("THCM-FEM execution was enabled; any repair calls used only solver "
            "diagnostics, not the reference JSON."
            if any(r.get("execution") for r in recs)
            else "No tools or solver were used."), "",
         "**Accuracy is section-weighted.** `physics`, `materials` and the two "
         "condition blocks are compared strictly — every key, every value. "
         "`results.queries` is matched loosely on what is measured. "
         "`results.plots` and `results.reference_curves` are not scored.", ""]

    # The leaderboard says WHAT scored how. explain.py answers WHY, and a reader
    # who has one should be told before they start reading tables.
    if (runs_root / run["run_id"] / "explain.md").exists():
        L += ["> An analyst's diagnosis of this run exists: **`explain.md`** in this "
              "directory. It groups the findings below into the decisions behind "
              "them and marks which are the comparator's fault rather than a "
              "model's.", ""]

    L += ["## Models", "",
          "| model | parsed | accuracy | validates | " + " | ".join(SHORT[c] for c in cols)
          + " | findings |",
          "|---|---:|---:|---:|" + "---:|" * len(cols) + "---:|"]
    rows = sorted(by_model.items(),
                  key=lambda kv: -(mean(r.get("accuracy") for r in kv[1]) or -1))
    for model, rs in rows:
        cells = " | ".join(fmt(section_mean(rs, c)) for c in cols)
        n_find = mean(r.get("n_findings") for r in rs)
        parsed = sum(r["outcome"] == "ok" for r in rs)
        L.append(f"| `{model}` | {parsed}/{len(rs)} | "
                 f"{fmt(mean(r.get('accuracy') for r in rs))} | "
                 f"{fmt(mean(r.get('validates') for r in rs))} | {cells} | "
                 f"{'—' if n_find is None else f'{n_find:.0f}'} |")

    L += ["", "`parsed` counts first replies that were usable JSON. A reply fixed "
              "by a JSON repair turn is not counted there, but its content is "
              "scored in `accuracy`.", "",
          "`validates` is the fraction of cases whose spec parses, matches the "
              "schema and passes the authoring rules. It is reported beside accuracy, "
              "never folded into it — a spec can be perfectly valid and describe the "
              "wrong experiment.", ""]

    if any(r.get("execution") for r in recs):
        L += ["## THCM-FEM execution", "",
              "Execution is separate from JSON accuracy. `ok` means the solver "
              "finished and wrote at least one query row.", "",
              "| model | ok | attempted | repaired | median solver s |",
              "|---|---:|---:|---:|---:|"]
        for model, rs in rows:
            attempted = [r for r in rs if r.get("execution")]
            ok = sum(r["execution"].get("status") == "ok" for r in attempted)
            repaired = sum(bool(r.get("repairs")) for r in attempted)
            durations = [r.get("solver_seconds", r["execution"].get("seconds"))
                         for r in attempted]
            durations = [v for v in durations if isinstance(v, (int, float))]
            median = statistics.median(durations) if durations else None
            L.append(f"| `{model}` | {ok} | {len(attempted)} | {repaired} | "
                     f"{'—' if median is None else f'{median:.1f}'} |")

        L += ["", "### Execution detail", "",
              "| model | case | status | stage | rows | attempts | JSON Δ | solver s |",
              "|---|---|---|---|---:|---:|---:|---:|"]
        for r in sorted(recs, key=lambda item: (item["case"], item["model"])):
            execution = r.get("execution")
            if not execution:
                continue
            output = execution.get("output") or {}
            delta = ((r.get("accuracy") - r.get("initial_accuracy"))
                     if isinstance(r.get("accuracy"), (int, float)) and
                     isinstance(r.get("initial_accuracy"), (int, float)) else None)
            L.append(f"| `{r['model']}` | `{r['case']}` | "
                     f"{execution.get('status', '—')} | {execution.get('stage', '—')} | "
                     f"{output.get('rows', 0)} | {len(r.get('solver_attempts') or [])} | "
                     f"{'—' if delta is None else f'{delta:+.3f}'} | "
                     f"{r.get('solver_seconds', execution.get('seconds', 0)):.1f} |")
        L += [""]

        baseline_failures = {
            case: value for case, value in (run.get("solver_baselines") or {}).items()
            if value.get("status") != "ok"
        }
        if baseline_failures:
            L += ["> **Runner baseline failed:** " + ", ".join(
                f"`{case}` ({value.get('status')})"
                for case, value in sorted(baseline_failures.items()))
                + ". Candidate execution for these cases is diagnostic but should "
                  "not be used to rank models.", ""]

    L += ["## Where the errors are", "",
          "| model | " + " | ".join(k for k in
                                    ("missing", "extra", "wrong_value", "wrong_unit",
                                     "wrong_model", "type_differs")) + " |",
          "|---|" + "---:|" * 6]
    for model, rs in rows:
        agg = Counter()
        for r in rs:
            agg.update(r.get("finding_counts") or {})
        L.append(f"| `{model}` | " + " | ".join(
            str(agg.get(k, 0)) for k in ("missing", "extra", "wrong_value",
                                         "wrong_unit", "wrong_model",
                                         "type_differs")) + " |")
    L += ["", "`wrong_unit` is a value off by a clean 1e3/1e6/86400-type ratio — a "
              "transcription slip. `wrong_value` is a number the model read or "
              "invented differently. They need different fixes.", ""]

    L += ["## Cases", "",
          "| case | best model | best | mean | hardest section |", "|---|---|---:|---:|---|"]
    by_case = defaultdict(list)
    for r in recs:
        by_case[r["case"]].append(r)
    for case, rs in sorted(by_case.items(),
                           key=lambda kv: (mean(r.get("accuracy") for r in kv[1]) or -1)):
        scored = [r for r in rs if r.get("accuracy") is not None]
        best = max(scored, key=lambda r: r["accuracy"], default=None)
        worst_section = min(
            ((c, section_mean(rs, c)) for c in cols if section_mean(rs, c) is not None),
            key=lambda t: t[1], default=(None, None))
        L.append(f"| `{case}` | {'`' + best['model'] + '`' if best else '—'} | "
                 f"{fmt(best['accuracy']) if best else '—'} | "
                 f"{fmt(mean(r.get('accuracy') for r in rs))} | "
                 f"{SHORT.get(worst_section[0], '—')} "
                 f"{fmt(worst_section[1]).strip() if worst_section[1] is not None else ''} |")

    L += census(run, runs_root)

    repaired = [r for r in recs if r.get("json_repaired")]
    if repaired:
        L += ["", "## Replies fixed by a JSON repair turn", "",
              "| model | case | first outcome | first error | repair calls |",
              "|---|---|---|---|---:|"]
        for r in repaired:
            L.append(f"| `{r['model']}` | `{r['case']}` | {r['outcome']} | "
                     f"{(r.get('message') or '')[:120]} | "
                     f"{len(r.get('json_repairs') or [])} |")

    failures = [r for r in recs if r["outcome"] != "ok" and r.get("accuracy") is None]
    if failures:
        L += ["", "## Calls that produced no scorable spec", "",
              "| model | case | outcome | why |", "|---|---|---|---|"]
        for r in failures:
            L.append(f"| `{r['model']}` | `{r['case']}` | {r['outcome']} | "
                     f"{(r.get('message') or '')[:120]} |")

    truncated = sorted({r["case"] for r in recs if r.get("paper_truncated")})
    if truncated:
        L += ["", "> **Paper truncated** for " + ", ".join(f"`{c}`" for c in truncated)
              + " — those scores are a lower bound; the model did not see the whole "
                "paper.", ""]

    L += ["", "## Per-case detail", "",
          "| model | case | accuracy | validates | " + " | ".join(SHORT[c] for c in cols)
          + " | findings | s |",
          "|---|---|---:|---:|" + "---:|" * len(cols) + "---:|---:|"]
    for r in sorted(recs, key=lambda r: (r["case"], -(r.get("accuracy") or -1))):
        cells = " | ".join(fmt((r.get("sections") or {}).get(c)) for c in cols)
        L.append(f"| `{r['model']}` | `{r['case']}` | {fmt(r.get('accuracy'))} | "
                 f"{fmt(r.get('validates'))} | {cells} | "
                 f"{r.get('n_findings', '—')} | {r['seconds']:.0f} |")
    return "\n".join(L) + "\n"


def causes(run_dir: Path, n_models: int) -> list:
    """The census one level up: ranked by CAUSE, not by differing key.

    A model that never writes `time_function` produces one finding per boundary
    condition, so the key-by-key census below ranks the same mistake four times
    and buries a rarer, worse one. cluster_findings.py groups findings by the
    decision behind them first; this counts those.
    """
    per: dict[tuple, set] = defaultdict(set)
    weight: dict[tuple, float] = {}
    for score_file in sorted(run_dir.glob("*/*/score.json")):
        model, case = score_file.parent.parent.name, score_file.parent.name
        try:
            data = json.loads(score_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in CLU.cluster(data.get("findings", [])):
            # Singletons are the key-by-key census by another name; the causes
            # table earns its place only above that resolution.
            if c.kind == "single":
                continue
            key = (case, c.section, c.title)
            per[key].add(model)
            weight[key] = max(weight.get(key, 0.0), c.weight)
    if not per:
        return []
    L = ["", "## What gets misinterpreted", "", "### By cause", "",
         "One row per *decision*, not per differing key — a model that omits one "
         "required key from every boundary condition made one mistake, not six. "
         f"Ranked by how many of the {n_models} model(s) made the same decision on "
         "the same case; a row every model hits is a property of the paper or of "
         "the authoring guide, not of any one model.", "",
         "| models | case | section | the decision |", "|---:|---|---|---|"]
    ranked = sorted(per.items(), key=lambda kv: (-len(kv[1]), -weight[kv[0]], kv[0]))
    for (case, section, title), models in ranked[:30]:
        L.append(f"| {len(models)}/{n_models} | `{case}` | {section} | {title} |")
    if len(ranked) > 30:
        L.append(f"\n{len(ranked) - 30} further cause(s) not shown.")
    return L


def census(run: dict, runs_root: Path) -> list:
    """What gets misread, aggregated over every candidate in the run.

    A finding every model makes is not a model weakness -- it is the paper being
    ambiguous, or the prompt failing to ask for something. That distinction is
    the whole reason to sweep more than one model.
    """
    run_dir = runs_root / run["run_id"]
    per_key: dict[tuple, set] = defaultdict(set)
    detail: dict[tuple, list] = defaultdict(list)
    for score_file in run_dir.glob("*/*/score.json"):
        model = score_file.parent.parent.name
        case = score_file.parent.name
        try:
            data = json.loads(score_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for f in data.get("findings", []):
            # Strip the aligned-name prefix so `concrete~mature.temperature.k` and
            # `sv40~mature.temperature.k` are recognised as the same mistake.
            what = f["what"].split(".", 1)[-1] if "~" in f["what"].split(".")[0] else f["what"]
            # Keyed BY CASE. Collapsing across cases would put one paper's candidate
            # value beside another paper's reference in the same row -- the counts
            # would be right and the example would be a fiction.
            key = (case, f["section"], f["kind"], what)
            per_key[key].add(model)
            detail[key].append(f)

    n_models = len({r["model"] for r in run["records"]})
    if not per_key:
        return []
    L = causes(run_dir, n_models)
    L += ["", "### Key by key", "",
          f"The same census at the comparator's own resolution — one row per "
          f"differing key, ranked by how many of the {n_models} model(s) hit it on "
          "the same case. Use it to check a cause above, not to read first.", "",
          "| models | case | section | kind | what | candidate | reference |",
          "|---:|---|---|---|---|---|---|"]
    ranked = sorted(per_key.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = [kv for kv in ranked if len(kv[1]) > 1] or ranked
    for (case, section, kind, what), models in shown[:25]:
        ex = detail[(case, section, kind, what)][0]
        cand = "—" if ex["candidate"] is None else str(ex["candidate"])[:32]
        refv = "—" if ex["reference"] is None else str(ex["reference"])[:32]
        L.append(f"| {len(models)}/{n_models} | `{case}` | {section} | {kind} | "
                 f"`{what}` | {cand} | {refv} |")
    hidden = len(ranked) - min(len(shown), 25)
    if hidden > 0:
        L.append(f"\n{hidden} further finding(s) not shown "
                 f"(one-model-only, or beyond the first 25).")
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--md", type=Path, help="write here (default: the run directory)")
    a = ap.parse_args(argv)

    run_id = a.run_id or (latest() if a.latest else None)
    if not run_id:
        ap.error("give a RUN_ID or --latest")
    path = RUNS / run_id / "run.json"
    if not path.exists():
        sys.exit(f"no such run: {path}")
    text = build(json.loads(path.read_text(encoding="utf-8")), RUNS)
    out = a.md or (RUNS / run_id / "leaderboard.md")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
