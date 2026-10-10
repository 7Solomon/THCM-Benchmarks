# Benchmark viewer

Static browser view of the project. No dependencies, no build step. The home page
(`#/`) leads to two sections:

- **Benchmarks** (`#/benchmarks`): every `benchmarks/**/benchmark.json`, with its
  plots and review verdict.
- **Workflows** (`#/workflows`): every worker job drawn as a diagram from its
  `worker/jobs/<job>/dataflow.json`, meaning the steps, the loops that send results
  back to a model, and a flags panel that shows what each option changes and
  builds the matching command line.

```bash
python3 viewer/build_index.py     # scan benchmarks/ -> viewer/index.json
python3 -m http.server            # from the repo root
```

Then open <http://localhost:8000/viewer/>.

Re-run `build_index.py` after a simulation batch; reload the page to see the new plots.
It must be served from the repo root — the page references plots at `../benchmarks/...`,
and `index.json` is fetched, which `file://` blocks.

## Standalone export

```bash
python3 viewer/build_index.py --standalone          # -> benchmarks.html (~13 MB)
python3 viewer/build_index.py --standalone -o /tmp/for-anna.html
```

One self-contained file: index, stylesheet, script and every plot PNG base64-inlined.
Double-click it, or mail it — no server, no repo. It is a snapshot, so regenerate it
after a run. The spec and paper links become plain text there, since the surrounding
files are not carried along.

## Marking a benchmark correct

Verdicts live in `viewer/status.json`, which is hand-edited and **git-tracked** —
it is the one file here that is not generated. `build_index.py` only ever *adds*
keys to it (new benchmarks appear as `null`); it never changes or removes a value
you set, so rescanning is always safe.

```jsonc
{
  "HITEC/hm-excavation": true,                 // checked against the paper
  "ATENA/TPBB": false,                         // known wrong
  "HAMSTAD/B2": { "correct": true, "comment": "within 2% of the analytical solution" },
  "BenVaSim/1.1": null                         // not yet reviewed
}
```

Keys are benchmark folder paths. If you rename a folder, the old key is kept but
reported as stale on the next build, so you can move the verdict across by hand.

The overview's **Status** row then filters on it: *Has results* · *Correct* ·
*Incorrect* · *Unreviewed*.

## What it reads

For every `benchmarks/**/benchmark.json`:

| from the spec | used for |
|---|---|
| `metadata` | title, source, physics tag, dimension, validation target, description |
| `physics`, `materials`, `boundary_conditions`, `time_stepping` | the setup facts table |
| `results.plots[].name` | joined to `output/<solver>/plots/<name>.png` for captions and axis labels |

The folder path is the identifier, not `metadata.id` — `ATENA/TPBB/GT` and
`ATENA/TPBB/OOFEM` share one id.

`physics_class` is free text in the specs (`thermo-chemo`, `thermo_mechanical`,
`hydro-mechanical (Biot poroelasticity)`), so `build_index.py` folds it onto a
short tag list for filtering. Extend `PHYSICS_TAGS` there when a new class appears.

A PNG with no matching entry in `results.plots` is still shown, captioned with its
filename. A benchmark with no `output/*/plots/` is listed as "no output".

## Workflows

`build_index.py` copies each `worker/jobs/<job>/dataflow.json` (format:
`worker/schemas/dataflow.schema.json`) into the index and runs
`worker/tools/check_dataflow.py` on it. A file that no longer matches the job's
real flags is still drawn, under a "may be out of date" banner listing the
problems: run the checker, fix the JSON, rebuild.

The diagram is laid out top to bottom by `layoutFlow()` in `workflows.js`. Rows
follow the longest path, feedback edges become numbered loops beside the boxes,
and an edge that skips rows gets a lane of its own, so it never runs behind a box.

## Code layout

| file | holds |
|---|---|
| `core.js` | data, router, top navigation, lightbox, markdown, helpers |
| `benchmarks.js` | the Benchmarks section |
| `workflows.js` | the Workflows section: layout, flags panel, step drawer |
| `home.js` | the landing page |
| `main.js` | loads the index and starts the router (loaded last) |

A section is one `registerSection({id, title, icon, blurb, stats, render, leave})`
call in its own file; its home-page card, its top-navigation link and its route
`#/<id>/...` all come from that. Add the file to `index.html` and to `SCRIPTS` in
`build_index.py`, which inlines them in that order for the standalone export.

Old links of the form `#HAMSTAD%2FB2` still work: they redirect to
`#/benchmarks/HAMSTAD%2FB2`.
