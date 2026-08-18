# Benchmark viewer

Static browser view of `benchmarks/`. No dependencies, no build step.

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
