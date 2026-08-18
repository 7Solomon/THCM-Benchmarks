#!/usr/bin/env python3
"""Scan benchmarks/ and emit the data for the static viewer.

Served viewer (default):
    python3 viewer/build_index.py            -> viewer/index.json
    python3 -m http.server                   -> localhost:8000/viewer/

Single-file export, images inlined, opens by double-click over file://:
    python3 viewer/build_index.py --standalone [-o benchmarks.html]
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BENCH = ROOT / "benchmarks"
OUT = HERE / "index.json"
STATUS = HERE / "status.json"      # hand-maintained verdicts; never overwritten

# physics_class is free text across the corpus; fold it onto a few stable tags
# so the overview can filter on something consistent. Order matters: the coupled
# classes must be tested before their substrings, since "hydro-thermo-mechanical"
# contains "thermo-mechanical".
PHYSICS_TAGS = [
    ("thermo-hydro-mechanical", ("hydro-thermo-mechanical", "thermo-hydro-mechanical",
                                 "thermo_hydro_mechanical", "thm")),
    ("hydro-mechanical", ("hydro-mechanical", "hydromechanical", "hydro_mechanical",
                          "hm", "poroelastic")),
    ("thermo-mechanical", ("thermo_mechanical", "thermo-mechanical")),
    ("thermal", ("thermal", "thermo-chemo", "heat")),
    ("hygric", ("hygric", "hygral", "moisture")),
    ("mechanical", ("mechanical", "fracture")),
]


def physics_tag(raw):
    """Fold a free-text physics_class onto one tag.

    Only the head of the string is classified — the part before any
    parenthetical gloss. The glosses are prose and produce false hits:
    'hygric (isothermal moisture transport)' contains 'thermal', and
    'thermal (conduction-only leg of the THM benchmark)' contains 'THM'.
    """
    head = (raw or "").lower().split("(")[0].strip()
    for tag, needles in PHYSICS_TAGS:
        if any(re.search(rf"\b{re.escape(n)}\b", head) for n in needles):
            return tag
    return "other"


def source_line(src):
    """'Okada, Horii & Cervenka (1998), FRAMCOS-3' from the source block."""
    if not isinstance(src, dict):
        return None
    authors = src.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    names = [a.split(",")[0].strip() for a in authors]
    if len(names) > 3:
        who = names[0] + " et al."
    elif len(names) > 1:
        who = ", ".join(names[:-1]) + " & " + names[-1]
    else:
        who = names[0] if names else ""
    year = src.get("year")
    venue = src.get("journal") or src.get("conference") or src.get("publisher")
    parts = [p for p in (who, f"({year})" if year else None, venue) if p]
    return " ".join(parts) or None


def find_analysis(case_dir):
    for p in sorted(case_dir.glob("*.md")):
        if p.name.lower().startswith(("analysis", "readme")):
            return p
    return None


def find_paper(case_dir):
    """Paper PDF sits next to the case or one level up at family level."""
    for d in (case_dir, case_dir.parent, case_dir.parent.parent):
        if not str(d).startswith(str(BENCH)):
            break
        pdfs = sorted(d.glob("*.pdf"))
        if pdfs:
            return pdfs[0]
    return None


def collect_plots(case_dir, declared):
    """Group PNGs by solver dir, joining each to its declared plot spec."""
    solvers = []
    for solver_dir in sorted((case_dir / "output").glob("*")):
        pngs = sorted((solver_dir / "plots").glob("*.png"))
        if not pngs:
            continue
        figures = []
        for png in pngs:
            # Curve labels are deliberately not carried over: the rendered plot
            # already draws its own legend.
            spec = declared.get(png.stem, {})
            figures.append({
                "name": png.stem,
                "title": spec.get("title") or png.stem.replace("_", " "),
                "x_label": (spec.get("x_axis") or {}).get("label"),
                "y_label": (spec.get("y_axis") or {}).get("label"),
                "declared": png.stem in declared,
                "src": png.relative_to(ROOT).as_posix(),
            })
        solvers.append({
            "name": solver_dir.name,
            "figures": figures,
            "has_vtu": (solver_dir / "vtu").is_dir(),
            "has_logs": (solver_dir / "logs").is_dir(),
        })
    return solvers


def load_status():
    """Read the hand-maintained verdict file.

    Values may be a bare true/false/null, or an object
    {"correct": true, "comment": "..."} when you want to record why.
    """
    if not STATUS.exists():
        return {}
    try:
        raw = json.loads(STATUS.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"{STATUS.name} is not valid JSON: {e}")
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def apply_status(cases, status):
    """Attach verdicts to cases and report keys that no longer match a folder."""
    for case in cases:
        entry = status.get(case["path"])
        if isinstance(entry, dict):
            case["correct"] = entry.get("correct")
            case["verdict_comment"] = entry.get("comment")
        else:
            case["correct"] = entry if isinstance(entry, bool) else None
            case["verdict_comment"] = None

    known = {c["path"] for c in cases}
    return sorted(k for k in status if k not in known)


def sync_status(cases):
    """Add newly found benchmarks as null. Existing values are left untouched."""
    raw = {}
    if STATUS.exists():
        raw = json.loads(STATUS.read_text())
    elif not raw:
        raw["_comment"] = ("Manual verdicts, keyed by benchmark folder. "
                           "Set true once the result is checked against the paper; "
                           "false if it is wrong. null = not yet reviewed. "
                           "A value may also be {\"correct\": true, \"comment\": \"...\"}. "
                           "build_index.py only ever adds new keys here.")

    added = [c["path"] for c in cases if c["path"] not in raw]
    for path in added:
        raw[path] = None

    if added or not STATUS.exists():
        ordered = {k: raw[k] for k in raw if k.startswith("_")}
        ordered.update({k: raw[k] for k in sorted(k for k in raw if not k.startswith("_"))})
        STATUS.write_text(json.dumps(ordered, indent=2) + "\n")
    return added


def build_case(spec_path):
    case_dir = spec_path.parent
    try:
        spec = json.loads(spec_path.read_text())
    except json.JSONDecodeError as e:
        print(f"  ! skipping {spec_path}: {e}", file=sys.stderr)
        return None

    meta = spec.get("metadata", {})
    results = spec.get("results", {})
    declared = {p["name"]: p for p in results.get("plots", []) if p.get("name")}

    rel = case_dir.relative_to(BENCH).as_posix()
    solvers = collect_plots(case_dir, declared)

    analysis = find_analysis(case_dir)
    paper = find_paper(case_dir)
    refs = sorted((case_dir / "reference").glob("*.csv")) + \
        sorted((case_dir / "reference_data").glob("*.csv"))

    ts = spec.get("time_stepping", {})

    return {
        "path": rel,                       # the key: id is not unique (TPBB)
        "id": meta.get("id"),
        "title": meta.get("title") or rel,
        "family": rel.split("/")[0],
        "source": source_line(meta.get("source")),
        "year": (meta.get("source") or {}).get("year"),
        "physics_raw": meta.get("physics_class"),
        "physics": physics_tag(meta.get("physics_class")),
        "dimension": meta.get("dimension"),
        "validation_method": meta.get("validation_method"),
        "validation_target": meta.get("validation_target"),
        "notes": meta.get("notes"),
        "fields": sorted(spec.get("physics", {}).keys()),
        "materials": sorted(spec.get("materials", {}).keys()),
        "n_bcs": len(spec.get("boundary_conditions", []) or []),
        "analysis_type": ts.get("analysis_type"),
        "t_end": ts.get("t_end"),
        "dt": ts.get("dt"),
        "n_declared_plots": len(declared),
        "solvers": solvers,
        "n_figures": sum(len(s["figures"]) for s in solvers),
        "reference_files": [p.relative_to(ROOT).as_posix() for p in refs],
        "analysis_md": analysis.read_text() if analysis else None,
        "analysis_name": analysis.name if analysis else None,
        "paper_pdf": paper.relative_to(ROOT).as_posix() if paper else None,
        "spec_path": spec_path.relative_to(ROOT).as_posix(),
    }


def inline_images(cases):
    """Replace every figure's relative path with a base64 data URI (in place)."""
    n = 0
    for case in cases:
        for solver in case["solvers"]:
            for fig in solver["figures"]:
                raw = (ROOT / fig["src"]).read_bytes()
                fig["src"] = "data:image/png;base64," + base64.b64encode(raw).decode()
                n += 1
    return n


def write_standalone(cases, dest):
    """Fold index.html + style.css + app.js + data into one self-contained file."""
    html = (HERE / "index.html").read_text()
    css = (HERE / "style.css").read_text()
    js = (HERE / "app.js").read_text()

    n = inline_images(cases)
    # </script> inside the JSON would close the tag early; escape the sequence.
    data = json.dumps({"cases": cases}, separators=(",", ":")).replace("</", "<\\/")

    html = html.replace(
        '<link rel="stylesheet" href="style.css">',
        f"<style>\n{css}\n</style>")
    html = html.replace(
        '<script src="app.js"></script>',
        f"<script>window.__BENCHMARK_DATA__ = {data};</script>\n<script>\n{js}\n</script>")
    html = html.replace(
        "Generated by <code>viewer/build_index.py</code>. Re-run it after a "
        "simulation batch to refresh.",
        "Standalone export &mdash; regenerate with "
        "<code>python3 viewer/build_index.py --standalone</code>.")

    dest.write_text(html)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--standalone", action="store_true",
                    help="write one self-contained HTML with images inlined")
    ap.add_argument("-o", "--output", type=Path,
                    help="destination for --standalone (default: benchmarks.html at repo root)")
    args = ap.parse_args()

    if not BENCH.is_dir():
        sys.exit(f"no benchmarks/ directory at {BENCH}")

    cases = []
    for spec_path in sorted(BENCH.rglob("benchmark.json")):
        case = build_case(spec_path)
        if case:
            cases.append(case)

    added = sync_status(cases)
    stale = apply_status(cases, load_status())

    mark = {True: "correct", False: "INCORRECT", None: ""}
    for case in cases:
        flag = "" if case["n_figures"] else "   (no plots)"
        print(f"  {case['path']:<48} {case['n_figures']:>2} figures"
              f"{flag:<14} {mark[case['correct']]}")

    n_fig = sum(c["n_figures"] for c in cases)
    n_ok = sum(1 for c in cases if c["correct"] is True)
    n_run = sum(1 for c in cases if c["n_figures"])

    if added:
        print(f"\n{STATUS.name}: added {len(added)} new benchmark(s) as null "
              f"— {', '.join(added)}")
    if stale:
        print(f"\n! {STATUS.name} has {len(stale)} key(s) matching no benchmark "
              f"folder (renamed or removed): {', '.join(stale)}", file=sys.stderr)

    if args.standalone:
        dest = args.output or (ROOT / "benchmarks.html")
        n = write_standalone(cases, dest)
        size = dest.stat().st_size / 1024 / 1024
        print(f"\n{len(cases)} benchmarks ({n_run} with plots, {n_ok} marked correct), "
              f"{n} figures inlined -> {dest} ({size:.1f} MB)")
        print("Open it directly in a browser — no server needed.")
    else:
        OUT.write_text(json.dumps({"cases": cases}, indent=1))
        print(f"\n{len(cases)} benchmarks ({n_run} with plots, {n_ok} marked correct), "
              f"{n_fig} figures -> "
              f"{OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
