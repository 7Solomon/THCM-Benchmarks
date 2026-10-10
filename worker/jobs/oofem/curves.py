#!/usr/bin/env python3
"""
curves.py -- which curves does a case have to reproduce, exactly?

    python worker/jobs/oofem/curves.py --case ATENA/TPBB

ONE source of truth, used twice: the prompt tells the model to produce exactly
these curve ids, and the scorer looks for exactly these curve ids. They cannot
drift apart, because there is only one list.

## Why a curve is not a file

89 of the corpus's 102 reference CSVs are a clean two columns, x then y. The
other 13 are not -- one file often carries several curves sharing an x:

    deflection_mm, load_rotating_kN, load_fixed_kN        ATENA/TPBB
    t_s, p_Pa_xD0p1, p_Pa_xD0p2, p_Pa_xD0p5, ...          OpenGeoSys

So the unit is a (file, y-column) pair, identified as `<file stem>::<y column>`,
and the default rule is: the first numeric column is x, every other numeric
column is its own curve. Non-numeric label columns (`Series`) are skipped.

## Overrides

Two files in the corpus defeat that rule, so a case may ship
`reference/curves.json` naming the columns outright:

    [{"file": "uac_stress_strain.csv", "x": "strain_1e-3_50mm",
      "y": "stress_exp_50mm",
      "x_label": "strain x1e-3 [-]", "y_label": "sigma/sigma_peak [-]"}, ...]

`x_label` / `y_label` are optional and exist for files whose headers do not say
what the column IS -- `Series,x,y` tells a model nothing about which quantity
or which unit it is being asked to reproduce.

`ATENA/UAC` needs one because its 9 columns are three INDEPENDENT x/y/y triples,
not one shared x. `cervera-1999-thermo-chemo-partI` needs one because its
`t_s, time_h, T_degC` carries the same time twice, and the default rule would
invent a `time_h` curve -- a model asked to reproduce time against time scores
1.000 for free, which would quietly inflate that case.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
PROBE_ROWS = 8


def _numeric_columns(header: list, rows: list) -> list:
    out = []
    for i in range(len(header)):
        vals = [r[i] for r in rows[:PROBE_ROWS] if len(r) > i and r[i].strip() != ""]
        if not vals:
            continue
        try:
            [float(v) for v in vals]
        except ValueError:
            continue
        out.append(i)
    return out


def _read(path: Path) -> tuple:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return [], []
    return rows[0], rows[1:]


def curves_for(ref_dir: Path) -> list:
    """Every curve a case must reproduce, as ordered descriptors."""
    if not ref_dir.is_dir():
        return []

    override = {}
    ov_path = ref_dir / "curves.json"
    if ov_path.exists():
        for e in json.loads(ov_path.read_text(encoding="utf-8")):
            override.setdefault(e["file"], []).append(e)

    out = []
    # Top level only, and no `depr_`. Nested folders (BenVaSim, HITEC) hold the
    # OTHER participating codes' curves -- alternative references to plot against,
    # not targets to reproduce; asking a model to match seven teams at once is not
    # a benchmark. `depr_` is the corpus's own mark for a superseded digitisation.
    for path in sorted(ref_dir.glob("*.csv")):
        if path.name.startswith("depr_"):
            continue
        header, rows = _read(path)
        if not header:
            continue
        if path.name in override:
            for e in override[path.name]:
                if e["x"] not in header or e["y"] not in header:
                    continue
                out.append({"id": f"{path.stem}::{e['y']}", "file": path.name,
                            "x_name": e["x"], "y_name": e["y"],
                            "x_col": header.index(e["x"]),
                            "y_col": header.index(e["y"]),
                            "x_label": e.get("x_label"),
                            "y_label": e.get("y_label")})
            continue
        num = _numeric_columns(header, rows)
        if len(num) < 2:
            continue
        x_col, x_name = num[0], header[num[0]]
        for y_col in num[1:]:
            out.append({"id": f"{path.stem}::{header[y_col]}", "file": path.name,
                        "x_name": x_name, "y_name": header[y_col],
                        "x_col": x_col, "y_col": y_col,
                        "x_label": None, "y_label": None})
    return out


def series(ref_dir: Path, c: dict) -> tuple:
    """The (x, y) samples of one curve descriptor."""
    header, rows = _read(ref_dir / c["file"])
    xs, ys = [], []
    for r in rows:
        if len(r) <= max(c["x_col"], c["y_col"]):
            continue
        # Parse both before appending either: a blank y cell (a column that
        # only has data at some x) must drop the whole row, not just y.
        try:
            x, y = float(r[c["x_col"]]), float(r[c["y_col"]])
        except ValueError:
            continue
        xs.append(x)
        ys.append(y)
    return xs, ys


# Most cases call it reference/; EarlyAgeThermalCracking calls it reference_data/.
# Probing both beats renaming a corpus directory out from under whatever else
# points at it.
REF_DIR_NAMES = ("reference", "reference_data")


def ref_dir_for(case: str) -> Path:
    base = REPO / "benchmarks" / case
    for name in REF_DIR_NAMES:
        if (base / name).is_dir():
            return base / name
    return base / REF_DIR_NAMES[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--case", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rd = ref_dir_for(a.case)
    cs = curves_for(rd)
    if a.json:
        print(json.dumps(cs, indent=2))
    else:
        print(f"{len(cs)} curve(s) for {a.case}")
        for c in cs:
            xs, ys = series(rd, c)
            print(f"  {c['id']:<52s} x={c['x_name']:<26s} y={c['y_name']:<24s} "
                  f"n={len(xs):>4d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
