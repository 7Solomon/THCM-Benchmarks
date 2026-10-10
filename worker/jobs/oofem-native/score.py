#!/usr/bin/env python3
"""
score.py -- how close is a generated simulation's curve to the paper's?

    score.py WORKDIR --case faria-2006-slab/thermal
    score.py WORKDIR --refs benchmarks/<case>/reference [--json]

The eval's other job scores a SPEC against a reference spec, key by key. There
is no reference oofempy script to diff against -- the solver builds its problem
in memory and never writes one -- so this job is scored on its OUTPUT instead:
run the thing, pull the curve, measure the distance to the digitized paper data.

## The comparison

`results.csv` is long-format -- `curve,x,y` -- because a case mixes x-axes. Two
of faria-2006-slab's target curves are against time in days and a third is a
profile against depth in metres; one wide table cannot hold both without lying
about one of them.

Each curve is matched to `reference/<curve>.csv` by name, the simulation is
linearly interpolated onto the reference's own x samples, and the error is
normalised by the reference's y range:

    nrmse    = rms(sim(x_ref) - y_ref) / (max(y_ref) - min(y_ref))
    coverage = fraction of reference x samples the simulation actually spans
    score    = coverage * max(0, 1 - nrmse)

Coverage is a separate factor because the common early failure is a run that
dies a tenth of the way through the time span. Without it, a simulation that
produced the first 3 % of the curve and got that bit right would outscore one
that ran to the end and was slightly off everywhere -- which is backwards.

A curve the reference expects and the run never produced scores 0. Extra curves
are reported but do not count: a model probing more points than asked is not an
error.

Stdlib only -- no numpy. Interpolating a few thousand points does not need it,
and worker/.venv does not have it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curves as CURVES                              # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent.parent


def load_results(path: Path) -> tuple:
    """Long-format `curve,x,y` -> {curve: ([x], [y])}, plus a parse complaint."""
    curves: dict = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        if not rdr.fieldnames:
            return {}, "results.csv is empty"
        cols = {c.strip().lower(): c for c in rdr.fieldnames}
        missing = {"curve", "x", "y"} - set(cols)
        if missing:
            return {}, (f"results.csv header is {rdr.fieldnames}, "
                        f"missing column(s): {', '.join(sorted(missing))}")
        for row in rdr:
            name = (row.get(cols["curve"]) or "").strip()
            if not name:
                continue
            try:
                x = float(row[cols["x"]])
                y = float(row[cols["y"]])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            curves.setdefault(name, ([], []))
            curves[name][0].append(x)
            curves[name][1].append(y)
    return curves, None


def interpolate(xs: list, ys: list, at: float):
    """Linear interpolation, None outside the sampled span.

    Returning None rather than clamping is deliberate: clamping would turn a
    run that stopped early into a flat line at its last value, which scores as
    a modelling error when it is really a truncated run. Coverage counts these.
    """
    if not xs:
        return None
    pts = sorted(zip(xs, ys))
    if at < pts[0][0] or at > pts[-1][0]:
        return None
    lo, hi = 0, len(pts) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if pts[mid][0] <= at:
            lo = mid
        else:
            hi = mid
    x0, y0 = pts[lo]
    x1, y1 = pts[hi]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (at - x0) / (x1 - x0)


def compare_curve(ref_x: list, ref_y: list, sim_x: list, sim_y: list) -> dict:
    if not ref_x:
        return {"score": 0.0, "note": "reference curve is empty"}
    paired = []
    for x, y in zip(ref_x, ref_y):
        got = interpolate(sim_x, sim_y, x)
        if got is not None:
            paired.append((y, got))
    coverage = len(paired) / len(ref_x)
    if not paired:
        return {"score": 0.0, "coverage": 0.0, "n_points": 0,
                "note": "simulation x-range does not overlap the reference"}
    spread = max(ref_y) - min(ref_y)
    # A flat reference has no range to normalise by; fall back to its magnitude
    # so a constant-temperature benchmark is still scorable.
    if spread == 0:
        spread = abs(ref_y[0]) or 1.0
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in paired) / len(paired))
    nrmse = rmse / spread
    return {"score": round(coverage * max(0.0, 1.0 - nrmse), 4),
            "nrmse": round(nrmse, 4), "rmse": round(rmse, 6),
            "coverage": round(coverage, 4), "n_points": len(paired),
            "ref_range": [min(ref_y), max(ref_y)],
            "sim_range": [min(sim_y), max(sim_y)]}


def score(workdir: Path, ref_dir: Path) -> dict:
    """Score one run directory against one case's reference curves."""
    wanted = CURVES.curves_for(ref_dir)
    ids = [c["id"] for c in wanted]
    out = {"reference_dir": str(ref_dir), "expected": ids,
           "curves": {}, "missing": [], "extra": [], "overall": 0.0}

    if not wanted:
        out["error"] = (f"no scorable reference curves under {ref_dir} -- this "
                        f"case cannot be scored")
        return out

    results = workdir / "results.csv"
    if not results.exists():
        out["error"] = "no results.csv"
        out["missing"] = ids
        return out

    produced, err = load_results(results)
    if err:
        out["error"] = err
        out["missing"] = ids
        return out

    out["plot"] = (workdir / "plot.png").exists()
    for c in wanted:
        name = c["id"]
        if name not in produced:
            out["missing"].append(name)
            out["curves"][name] = {"score": 0.0, "note": "not produced"}
            continue
        ref_x, ref_y = CURVES.series(ref_dir, c)
        sim_x, sim_y = produced[name]
        out["curves"][name] = compare_curve(ref_x, ref_y, sim_x, sim_y)
    out["extra"] = sorted(set(produced) - set(ids))
    out["overall"] = round(
        sum(out["curves"][n]["score"] for n in ids) / len(ids), 4)
    return out


def render(s: dict, width: int = 46) -> str:
    lines = []
    if s.get("error"):
        lines.append(f"ERROR: {s['error']}")
    lines.append(f"overall {s.get('overall', 0.0):.3f}"
                 f"   plot {'yes' if s.get('plot') else 'NO'}"
                 f"   {len(s.get('expected', []))} reference curve(s)")
    lines.append("")
    for name in s.get("expected", []):
        c = s.get("curves", {}).get(name, {})
        if "nrmse" in c:
            lines.append(f"  {name:<{width}} {c['score']:.3f}  "
                         f"nrmse {c['nrmse']:.3f}  cover {c['coverage']:.2f}  "
                         f"n={c['n_points']}")
        else:
            lines.append(f"  {name:<{width}} {c.get('score', 0.0):.3f}  "
                         f"-- {c.get('note', 'no data')}")
    if s.get("extra"):
        lines.append("")
        lines.append(f"  extra curves (not scored): {', '.join(s['extra'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("workdir", type=Path, help="directory holding results.csv")
    ap.add_argument("--case", help="dataset case name, e.g. ATENA/TPBB")
    ap.add_argument("--refs", type=Path, help="reference/ directory (overrides --case)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ref_dir = a.refs
    if ref_dir is None:
        if not a.case:
            ap.error("one of --case or --refs is required")
        ref_dir = CURVES.ref_dir_for(a.case)
    s = score(a.workdir, ref_dir)
    print(json.dumps(s, indent=2) if a.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
