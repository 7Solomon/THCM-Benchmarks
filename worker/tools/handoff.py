#!/usr/bin/env python3
"""The human-extraction handoff: what an agent leaves behind when the digitizer
cannot read a figure, and what it reads back when a human has filled it in.

The folder is a contract in both directions, so it is a spec and not a note:
the agent must be able to ingest the result mechanically, which means the curve
ids, the axis units and the column order are fixed up front by the side that
knows them. The human's only job is numbers.

    handoff.py request WORKDIR --spec request.json   # agent: park a figure
    handoff.py scan    WORKDIR                       # has a human put anything there?
    handoff.py list    WORKDIR                       # human: what is outstanding

Layout it creates, per failed figure:

    <case>/reference/_needs_human/
        README.md                     what to do, in one screen
        fig7a/
            fig7a.png                 the exact crop the digitizer was given
            request.json              the spec (axes, units, curves, cross-check)
            fig7a_sensor_t1.csv       header only — fill rows below it
            fig7a_sensor_t2.csv
"""
import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

DIRNAME = "_needs_human"
LEDGER = ".ledger.json"


def folder(workdir, case):
    return Path(workdir) / case / "reference" / DIRNAME


def find_requests(workdir):
    """Every request.json under the workdir, whatever the case dir is called."""
    return sorted(Path(workdir).glob(f"*/reference/{DIRNAME}/*/request.json"))


def col(axis):
    """Column header carrying the unit the human reads off the axis."""
    label = str(axis.get("label", "x")).strip().lower().replace(" ", "_")
    unit = str(axis.get("unit", "")).strip().replace(" ", "")
    return f"{label}_{unit}" if unit else label


# --- agent side ------------------------------------------------------------

REQUIRED = ("figure", "case", "crop", "x", "y", "curves")


def cmd_request(a):
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8")
                      if Path(a.spec).exists() else a.spec)
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        sys.exit(f"spec is missing required keys: {missing}")
    if not spec["curves"]:
        sys.exit("spec lists no curves — nothing for a human to fill in")

    fig = spec["figure"]
    dest = folder(a.workdir, spec["case"]) / fig
    dest.mkdir(parents=True, exist_ok=True)

    crop = Path(spec["crop"])
    if not crop.is_absolute():
        crop = Path(a.workdir) / spec["case"] / crop
    if not crop.exists():
        sys.exit(f"crop image not found: {crop} — render and crop it before parking "
                 "the figure; the image IS the request")
    shutil.copy2(crop, dest / f"{fig}{crop.suffix}")
    spec["crop_image"] = f"{fig}{crop.suffix}"

    header = [col(spec["x"]), col(spec["y"])]
    for c in spec["curves"]:
        p = dest / f"{c['id']}.csv"
        if p.exists() and sum(1 for _ in p.open()) > 1:
            continue                      # never clobber data a human already typed
        with p.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(header)
    spec["columns"] = header

    # Fingerprint what WE created. Anything added or changed afterwards is the
    # human's contribution — whatever they choose to name it or however they
    # shape it. Detection must not depend on the human following a convention.
    spec["_created"] = {f.name: digest(f) for f in sorted(dest.iterdir())
                        if f.name != "request.json"}
    (dest / "request.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_readme(a.workdir, spec["case"])
    print(f"parked {fig} for human extraction -> {dest}")
    for c in spec["curves"]:
        print(f"   {c['id']}.csv  [{', '.join(header)}]  {c.get('legend','')}")


README = """# Figures that need a human

The automatic digitizer could not read these. Each folder holds the exact image
it was given, a `request.json` saying what the figure contains, and one CSV per
curve with the header already written.

## What to do

1. Open the folder's PNG, and `request.json` next to it — it names each curve,
   its legend entry, the axis ranges, and why the automatic attempt failed.
2. Digitize the curves however you like (plot2data.com by hand, WebPlotDigitizer,
   or reading points off the axes).
3. Put the numbers in this folder. The prepared CSV files (one per curve, header
   already written) are the easy path — paste your rows below the header. But
   anything works: drop the site's raw export in here under whatever name it
   downloaded as, a `.txt`, one file holding all three curves. The agent reads
   whatever is here and reshapes it. You do not have to match a format.

## Units: use the figure's own

Enter exactly what the axes show — the template headers name those units, e.g.
`time_hours,temperature_degC`. Do NOT convert to seconds or SI: the agent does
that on the way in and keeps your raw reading as the audit trail. Converting
twice is the most common way this goes wrong.

If you use the templates, x then y, one row per point. Beyond that, do not worry
about tidiness — reversed columns, an extra index column, semicolons, a decimal
comma, several curves in one file: the agent sorts it out. What it cannot sort
out is which curve is which when nothing identifies them, so if you rename files
or merge curves, leave a hint — a header, or a line in `request.json` under
`"human_note"`.

If a curve is genuinely unreadable (occluded, merged with another), leave it out
and say so in `"human_note"`. Do not invent points to fill a gap.

## When you are done

Nothing to flip, no flag to remember. Just run the queue again:

    {worker}/run_queue.sh

It looks in this folder, sees that something new is here, and dispatches an agent
to pick the work up. To see what it can find first:

    {py} {handoff} scan {workdir}

The agent re-checks your data the same way it checks its own: it overlays your
curves on the PNG and cross-checks a value against the paper's text or tables.
Hand-read numbers are not trusted more than machine-read ones — they are tagged
`HUMAN_DIGITIZED` and carry their own reading error into the analysis.
"""


def write_readme(workdir, case):
    workdir = Path(workdir).resolve()
    here = Path(__file__).resolve()
    (folder(workdir, case) / "README.md").write_text(
        README.format(py=here.parents[2] / ".venv/bin/python", handoff=here,
                      workdir=workdir, worker=here.parents[1], slug=workdir.name),
        encoding="utf-8")


# --- runner / human side ---------------------------------------------------

def n_lines(path):
    """Non-empty lines after the header. Shape-agnostic on purpose: an export may
    be long-form (series,x,y) or wide, and either is data."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return max(sum(1 for ln in fh if ln.strip()) - 1, 0)
    except OSError:
        return 0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def rows_of(path):
    """Data rows that parse as two floats. Anything else is a filling error."""
    good, bad = 0, []
    with path.open(encoding="utf-8") as fh:
        for i, row in enumerate(csv.reader(fh), start=1):
            if i == 1 or not row or not any(f.strip() for f in row):
                continue
            try:
                float(row[0]); float(row[1])
                good += 1
            except (ValueError, IndexError):
                bad.append((i, ",".join(row)[:60]))
    return good, bad


def survey(workdir):
    """(figure, curve_id, path, n_rows, bad_rows) for every parked curve."""
    out = []
    for req_path in find_requests(workdir):
        spec = json.loads(req_path.read_text(encoding="utf-8"))
        for c in spec.get("curves", []):
            p = req_path.parent / f"{c['id']}.csv"
            n, bad = rows_of(p) if p.exists() else (0, [])
            out.append((spec.get("figure", req_path.parent.name), c["id"], p, n, bad))
    return out


def contributions(workdir):
    """What a human has put into each parked figure, in whatever form.

    Deliberately format-blind: a filled-in template, a raw export from the
    digitizer site under its own name, a .txt, a spreadsheet — all count. The
    agent is the one that reshapes it; the human should never have to match a
    schema to get their work noticed.
    """
    found = {}
    for req_path in find_requests(workdir):
        spec = json.loads(req_path.read_text(encoding="utf-8"))
        created = spec.get("_created")
        # Requests written before fingerprinting existed: fall back to the names
        # the spec implies, and call a template "filled" when it holds rows.
        expected = {spec.get("crop_image", "")} | {f"{c['id']}.csv" for c in spec.get("curves", [])}
        added, filled, untouched = [], [], []
        for f in sorted(req_path.parent.iterdir()):
            if f.name in ("request.json", LEDGER, "CONCERNS.md") or f.is_dir():
                continue
            if created is not None:
                if f.name not in created:
                    added.append(f)
                elif digest(f) != created[f.name]:
                    filled.append(f)
                else:
                    untouched.append(f)
            elif f.name in expected:
                has_rows = f.suffix.lower() in (".csv", ".txt", ".tsv") and rows_of(f)[0] > 0
                (filled if has_rows else untouched).append(f)
            else:
                added.append(f)
        found[spec.get("figure", req_path.parent.name)] = {
            "dir": req_path.parent, "added": added,
            "filled": filled, "untouched": untouched}
    return found


def check_ledger(dirpath, files):
    """Tripwire for human data that disappeared between scans.

    Nothing in the pipeline is allowed to remove or empty a human's file, so if
    one shrinks to nothing it is a bug or a misjudgement, never a normal event.
    The ledger is what makes that visible after the fact instead of silently.
    """
    led_path = dirpath / LEDGER
    old = {}
    if led_path.exists():
        try:
            old = json.loads(led_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = {}
    now, lost = {}, []
    for f in files:
        n = rows_of(f)[0] if f.suffix.lower() in (".csv", ".txt", ".tsv") else -1
        now[f.name] = {"sha": digest(f), "rows": n}
        was = old.get(f.name)
        if was and was.get("rows", 0) > 0 and n == 0:
            lost.append((f.name, was["rows"]))
    for name, rows in old.items():
        if name not in now and rows.get("rows", 0) > 0:
            lost.append((name, rows["rows"]))
    led_path.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    return lost


DATA_SUFFIXES = (".csv", ".tsv", ".txt", ".dat", ".xlsx", ".ods")


def strays(workdir):
    """Data files a human dropped somewhere other than a handoff folder.

    They will: a run can fail before it ever parks a figure, leaving no folder to
    fill in, and the obvious place to put an export is next to the crop it came
    from. The pipeline has to notice that, or the person's work sits there
    invisibly while the queue reports 'needs a human'.

    Anything under web/ (we only ever write PNGs there), or loose in the case
    directory, counts. reference/*.csv does not — those are the agent's own
    normalized curves. input/ and output/ are the solver's.
    """
    out = []
    for case in sorted(Path(workdir).iterdir()):
        if not case.is_dir() or case.name.startswith((".", "_")):
            continue
        for f in case.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in DATA_SUFFIXES:
                continue
            rel = f.relative_to(case)
            top = rel.parts[0] if len(rel.parts) > 1 else ""
            if top in ("input", "output"):
                continue
            if top == "reference" and len(rel.parts) == 2:
                continue                      # the agent's own normalized curves
            if DIRNAME in rel.parts:
                continue                      # handled as a proper handoff
            out.append(f)
    return out


def cmd_scan(a):
    found = contributions(a.workdir)
    loose = strays(a.workdir)
    if loose:
        print("data files a human left outside the handoff folders:")
        for f in loose:
            print(f"   {f}  ({f.stat().st_size} B, ~{n_lines(f)} data lines)")
        print("   ^ treat these as human-supplied extraction data: read them, work out "
              "which curve each column is, normalize, and use them.")
    if not found:
        if loose:
            sys.exit(0)
        print("nothing parked for human extraction")
        sys.exit(1)
    any_data = bool(loose)
    for fig, c in found.items():
        # A concern already raised, on data the human then left alone, is answered:
        # they saw it and re-ran anyway. Saying so here stops the agent asking twice.
        concerns = c["dir"] / "CONCERNS.md"
        if concerns.exists():
            newest = max((f.stat().st_mtime for f in c["added"] + c["filled"]),
                         default=0)
            if newest <= concerns.stat().st_mtime:
                print(f"{fig}: CONCERNS.md was raised and the data is UNCHANGED since. "
                      "The human has seen it and re-ran anyway — treat the concern as "
                      "acknowledged, use the data, keep the reservation in REPORT.md, "
                      "and do NOT park again on that point.")
            else:
                print(f"{fig}: data was edited after CONCERNS.md was written — "
                      "the human has responded to it; re-check before reusing your "
                      "earlier measurements.")
        lost = check_ledger(c["dir"], c["added"] + c["filled"] + c["untouched"])
        for name, rows in lost:
            print(f"!! {fig}/{name}: {rows} rows of human data are GONE since the last "
                  f"scan. Nothing may empty or remove a human's file — recover it from "
                  f"git or from a quarantine copy before continuing.")
        n = len(c["added"]) + len(c["filled"])
        any_data = any_data or bool(n)
        print(f"{fig}: {n} file(s) with human data" if n
              else f"{fig}: nothing added yet")
        for f in c["filled"]:
            rows, bad = rows_of(f) if f.suffix.lower() in (".csv", ".txt", ".tsv") else (0, [])
            extra = f"  ~{rows} parseable rows" if rows else ""
            print(f"   {f.name:<28} filled template{extra}")
        for f in c["added"]:
            print(f"   {f.name:<28} added by human ({f.stat().st_size} B)")
    if not any_data:
        print("\nnothing to ingest yet — fill in the templates or drop your export "
              "in the figure folder, then just re-run the queue")
        sys.exit(1)
    print("\nhuman data present — the queue will pick this up on its next run; "
          "the agent reshapes it, whatever format it is in")


def cmd_list(a):
    for req_path in find_requests(a.workdir):
        spec = json.loads(req_path.read_text(encoding="utf-8"))
        crit = "critical" if spec.get("critical", True) else "optional"
        print(f"{spec.get('figure')}  p.{spec.get('page','?')}  ({crit})")
        print(f"   why: {spec.get('why_failed','')}")
        print(f"   dir: {req_path.parent}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("request", help="park one figure for a human (agent)")
    p.add_argument("workdir")
    p.add_argument("--spec", required=True, help="path to a JSON file, or inline JSON")
    p.set_defaults(fn=cmd_request)

    p = sub.add_parser("scan", help="has a human added anything? (exit 1 if not)")
    p.add_argument("workdir")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("list", help="what is outstanding, and why")
    p.add_argument("workdir")
    p.set_defaults(fn=cmd_list)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
