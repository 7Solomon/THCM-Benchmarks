#!/usr/bin/env python3
"""
build_dataset.py -- freeze benchmarks/ into an evaluation dataset.

    python worker/tools/build_dataset.py [--out dataset]

The corpus under `benchmarks/` is a working tree: a case is a directory holding a
spec, a paper, reference CSVs, and megabytes of solver output. An evaluation wants
the opposite shape -- every spec in one flat folder, every paper in another, and a
manifest saying which goes with which. That is what this writes.

`dataset/` is DERIVED. Never hand-edit it; re-run this instead. The only thing that
is not a byte copy is `metadata.paper_pdf`, injected into each spec so a case
carries its paper's filename (the schema puts no constraints on `metadata`, so this
validates exactly as the original does).

Layout:

    dataset/
      manifest.json      the index -- start here
      json/              one spec per case, 'ATENA/TPBB' -> 'ATENA__TPBB.json'
      pdf/               the papers, deduplicated by content hash
      text/              pdf/ extracted to text, for models with no PDF input

A paper is found in the case's own directory, else in the nearest ancestor under
benchmarks/ -- three papers serve two cases each that way (HITEC, faria-2006-slab,
jedrzejewska-2018-cost-tu1404). Cases with no paper anywhere are still exported,
with `paper_pdf: null`: they are a visible to-do list, and dropping a PDF into the
case directory and re-running enrolls it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/tools
WORKER = HERE.parent                            # worker
REPO = WORKER.parent                            # repo root

BENCHMARKS = REPO / "benchmarks"
STATUS_JSON = REPO / "viewer" / "status.json"
RENDER = HERE / "pdf_render.py"

# The spec key this script owns. Written into the dataset copy only; the corpus
# file is never touched.
PDF_KEY = "paper_pdf"


def flat_name(case: str) -> str:
    """'faria-2006-slab/thermal' -> 'faria-2006-slab__thermal'. Reversible, and
    keeps json/ flat, sortable, and safe on every filesystem."""
    return case.replace("/", "__")


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_paper(case_dir: Path) -> Path | None:
    """The paper for a case: a PDF in its own directory, else the nearest one in an
    ancestor up to benchmarks/. Stops at the first level that has any, so a case
    with its own paper never picks up the collection-level one.

    More than one PDF at the same level is ambiguous and we do not guess -- the
    caller reports it and the case exports with no paper.
    """
    d = case_dir
    while True:
        pdfs = sorted(d.glob("*.pdf"))
        if pdfs:
            return pdfs[0] if len(pdfs) == 1 else None
        if d == BENCHMARKS:
            return None
        d = d.parent


def paper_filename(pdf: Path) -> str:
    """A readable name for the dataset copy.

    Publisher filenames are opaque ('s11440-024-02502-w.pdf',
    '1-s2.0-S036013232500349X-main.pdf'). Name the copy after the directory path
    that holds the paper -- already the human-chosen identifier -- and keep the
    original in the manifest so nothing is lost.

    The whole path, not just the last segment: a paper under ATENA/GFM/ should be
    'atena-gfm.pdf', because 'gfm.pdf' says nothing about which collection it
    belongs to once it sits in a flat folder beside twenty others.
    """
    try:
        rel = pdf.parent.relative_to(BENCHMARKS)
    except ValueError:
        rel = Path(pdf.parent.name)
    return f"{slugify(str(rel))}.pdf"


def load_status() -> dict:
    """Our own verdicts, so an eval can exclude cases we know are wrong."""
    try:
        return json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status_of(raw) -> str:
    """status.json holds true / false / null / {"correct": bool, ...}."""
    if isinstance(raw, dict):
        raw = raw.get("correct")
    return {True: "correct", False: "incorrect"}.get(raw, "unreviewed")


def extract_text(pdf: Path, out: Path) -> str | None:
    """Reuse pdf_render.py rather than opening a second pymupdf path."""
    try:
        subprocess.run([sys.executable, str(RENDER), "text", str(pdf), "-o", str(out)],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"  warn: text extraction failed for {pdf.name}: "
              f"{(exc.stderr or '').strip()}", file=sys.stderr)
        return None
    return out.name


def build(out_root: Path, force: bool) -> int:
    specs = sorted(BENCHMARKS.glob("**/benchmark.json"))
    if not specs:
        sys.exit(f"no benchmark.json under {BENCHMARKS}")

    # Every one of these is wholly derived, so they are rebuilt from empty. That is
    # what makes the build deterministic: a name chosen to avoid colliding with a
    # LEFTOVER from the previous run would depend on run order, and the output would
    # drift on every rebuild.
    json_dir, pdf_dir, text_dir = out_root / "json", out_root / "pdf", out_root / "text"
    for d in (json_dir, pdf_dir, text_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    status = load_status()
    by_hash: dict[str, str] = {}                 # sha256 -> filename in pdf/
    entries, no_paper, ambiguous = [], [], []

    for spec in specs:
        case = str(spec.parent.relative_to(BENCHMARKS))
        body = json.loads(spec.read_text(encoding="utf-8"))
        md = body.setdefault("metadata", {})

        pdf = find_paper(spec.parent)
        if pdf is None and sorted(spec.parent.glob("*.pdf")):
            ambiguous.append(case)

        pdf_name = text_name = None
        if pdf is not None:
            digest = sha256(pdf)
            pdf_name = by_hash.get(digest)
            if pdf_name is None:
                pdf_name = paper_filename(pdf)
                if (pdf_dir / pdf_name).exists():
                    # Two DIFFERENT papers whose directories slugify the same. Break
                    # the tie on content, not on arrival order, so the name a paper
                    # gets does not depend on which case was walked first.
                    pdf_name = f"{Path(pdf_name).stem}-{digest[:8]}.pdf"
                shutil.copy2(pdf, pdf_dir / pdf_name)
                by_hash[digest] = pdf_name
                extract_text(pdf_dir / pdf_name, text_dir / f"{Path(pdf_name).stem}.txt")
            candidate = text_dir / f"{Path(pdf_name).stem}.txt"
            text_name = candidate.name if candidate.exists() else None
        else:
            no_paper.append(case)

        md[PDF_KEY] = pdf_name
        json_name = f"{flat_name(case)}.json"
        (json_dir / json_name).write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        src = body.get("metadata", {}).get("source", {}) or {}
        entries.append({
            "case": case,
            "json": f"json/{json_name}",
            "paper_pdf": f"pdf/{pdf_name}" if pdf_name else None,
            "paper_text": f"text/{text_name}" if text_name else None,
            "paper_source": str(pdf.relative_to(REPO)) if pdf else None,
            "id": md.get("id"),
            "title": md.get("title"),
            "doi": src.get("doi"),
            "year": src.get("year"),
            "physics_class": md.get("physics_class"),
            "dimension": md.get("dimension"),
            "validation_method": md.get("validation_method"),
            "spec_bytes": len(json.dumps(body)),
            "our_verdict": status_of(status.get(case)),
        })

    manifest = {
        "generated_by": "worker/tools/build_dataset.py",
        "source": "benchmarks/",
        "note": "DERIVED — do not hand-edit; re-run the builder instead.",
        "counts": {
            "cases": len(entries),
            "with_paper": sum(e["paper_pdf"] is not None for e in entries),
            "without_paper": len(no_paper),
            "unique_papers": len(by_hash),
        },
        "cases": entries,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_readme(out_root, manifest, no_paper)

    print(f"{out_root}: {len(entries)} cases, {len(by_hash)} unique papers, "
          f"{len(no_paper)} without a paper")
    if ambiguous:
        print(f"  ambiguous (several PDFs in the case dir, none chosen): "
              f"{', '.join(ambiguous)}", file=sys.stderr)
    if no_paper:
        print("  no paper found for: " + ", ".join(no_paper))
    return 0


def write_readme(out_root: Path, manifest: dict, no_paper: list[str]) -> None:
    c = manifest["counts"]
    rows = "\n".join(
        f"| `{e['case']}` | {e['physics_class'] or '—'} | "
        f"{'`' + Path(e['paper_pdf']).name + '`' if e['paper_pdf'] else '—'} | "
        f"{e['our_verdict']} |"
        for e in manifest["cases"])
    (out_root / "README.md").write_text(f"""# Benchmark authoring dataset

**Derived from `benchmarks/`. Do not hand-edit — re-run the builder.**

```bash
python worker/tools/build_dataset.py
```

{c['cases']} cases, {c['unique_papers']} unique papers,
{c['without_paper']} cases with no paper in the repo.

| what | where |
|---|---|
| the index | `manifest.json` |
| reference specs | `json/<case with / as __>.json` |
| papers | `pdf/` (deduplicated by content hash) |
| papers as text | `text/` (`pdf_render.py text`, for models with no PDF input) |

Each spec carries `metadata.{PDF_KEY}` naming its paper, or `null` when there is
none. `metadata` is unconstrained in `benchmark.schema.json`, so a dataset spec
validates exactly as its corpus original does.

`our_verdict` comes from `viewer/status.json` and is our own judgement of whether
that benchmark reproduces — useful for excluding cases we already know are wrong.

## Cases

| case | physics | paper | our verdict |
|---|---|---|---|
{rows}

{"## No paper in the repo\n\n" + "\n".join(f"- `{c}`" for c in no_paper) if no_paper else ""}
""", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "dataset"), type=Path)
    a = ap.parse_args(argv)
    return build(Path(a.out).resolve(), force=True)


if __name__ == "__main__":
    sys.exit(main())
