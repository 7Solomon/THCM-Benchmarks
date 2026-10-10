# Authoring and execution eval

**Which model can write a correct `benchmark.json` from a paper, and does that
JSON execute?** Solver execution is opt-in, so the original single-shot
authoring comparison remains available unchanged.

```bash
python worker/tools/build_dataset.py         # once: benchmarks/ -> dataset/
worker/worker.sh eval --probe       # which live free chat routes actually answer
worker/worker.sh eval                       # free models, every case with a paper
worker/worker.sh eval --run-solver          # score JSON, then execute THCM-FEM
worker/worker.sh eval --report        # leaderboard
worker/worker.sh eval --explain --allow-metered   # and WHY each spec is wrong
```

## Cost

Models marked `metered: true` in `models.yaml` are refused unless you pass
`--allow-metered`. **A run with no flags costs nothing** — it touches only
KIT-hosted models, which are unmetered.

Solver feedback adds up to `--solver-feedback-turns` model calls for each
candidate that has not produced query output yet. A reply that is not parseable
JSON adds up to `--json-repair-turns` calls (default 1). The same metered-model
gate applies to those calls.

The endpoint answers the budget question itself, per model, as
`connection_type`: `local` is hosted at KIT and free, `external` is routed out
and billed. `--list-models` applies exactly that rule:

```bash
worker/worker.sh eval --list-models   # what is served now, free vs metered
worker/worker.sh eval --probe         # which of them actually answer
```

**The roster drifts.** Between the 2026-08-28 run and 2026-09-11, four of the
eight models in `models.yaml` stopped resolving — a sweep would have spent one
failed call per case on each. `--list-models` names any configured model the
endpoint no longer knows, so run it *before* a sweep.

The metered ones are billed against a per-user KIT budget and return
`HTTP 400 ExceededBudget` once it is spent. The default probe never calls them;
probe one explicitly with `--models MODEL --allow-metered` only when needed.

`--probe` gets its default targets from the live API rather than from the YAML.
That makes it include free moving aliases and report actual reachability. The
benchmark itself still uses the fixed, `default: true` IDs in `models.yaml`, so
an alias cannot silently change what a time-series row means.

## What it does

One identical single-shot call per (model, case): `SKILLs/CREATE.md`, all six
physics modules, a compacted `benchmark.schema.json`, the case's
`dataset/scope/*.md`, and the paper's text in — one JSON object out. No tools,
no phases, no browser.

That is deliberate. It isolates *"can it author the spec"* from *"does the physics
reproduce"*, and it keeps a 31-billion-parameter local model and a frontier model
comparable on the same input. The agentic path — where the model gets tools, the
PDF, and the seven phases — is `worker/dispatch/dsh.py`, and it answers a
different question.

## THCM-FEM execution and repair

Execution is a second axis and is never folded into JSON accuracy. Start with a
small case set:

```bash
worker/worker.sh eval \
  --models kit.mistral-small-4-119b-a8b \
  --cases HAMSTAD/B2 cervera-1999-thermo-chemo-partI \
  --run-solver --solver-timeout 900
```

`--solver-feedback-turns N` permits up to N additional calls to the same model.
Each repair call sees the original paper prompt, its current JSON, and only the
THCM-FEM status/log/output summary. It never sees the curated JSON or comparator
findings. Every revision remains in `candidate.attempt-N.json`; `candidate.json`
is the final revision.

```bash
worker/worker.sh eval --models kit.mistral-small-4-119b-a8b \
  --cases HAMSTAD/B2 --run-solver --solver-feedback-turns 2
```

To add solver output to an existing run without making API calls:

```bash
worker/worker.sh eval --execute-run RUN_ID --cases HAMSTAD/B2
```

Use `--solver-baseline` on pilot runs. It executes the curated JSON through the
same installed solver, separating a broken/unsupported backend from a generated
candidate failure. OOFEM is the default backend; `--solver-backend fem` selects
the legacy FEM backend. VTU is disabled unless `--keep-vtu` is passed.

Each execution is isolated under the candidate directory:

```text
solver/attempt-0/
  benchmark.json             execution projection, not the scored candidate
  execution_transform.json   presentation changes and copied input hashes
  execution.json             classified status and output inventory
  mesh.stdout.log / *.stderr.log
  solver.stdout.log / *.stderr.log
  results.csv                 exported query_results.db, when available
  output/OOFEM/query_results.db
  output/OOFEM/plots/         the candidate's own plots, reference curves removed
```

The execution projection removes reference curves (the model's
`results.reference_curves` and every plot curve with `source: reference`; a plot
left with no curves is dropped), disables live reference deviation, forces
`save_db`, and disables checkpoint/VTU output. The candidate's remaining plots are
drawn, so a run shows what the solver produced. A plotting crash after the solve
has written its query rows still counts as `ok`, with `stage: postprocess` and the
traceback in `plot_error`; a single bad plot is listed in `output.plot_failures`.
The `--solver-baseline` run keeps the curated plots and reference curves and
copies `benchmarks/<case>/reference/` in, so `_baseline/<case>/output/OOFEM/plots/`
is the reference picture to compare against. Geometry is
always remeshed from the candidate. A non-mesh `input/...` file is copied only
when the candidate names that exact path and it exists in the curated case;
missing or invented files are reported as `asset_missing`.

Figures do not survive text extraction, so a value that only exists in a plot is
not recoverable here. The prompt tells the model to tag those `MISSING` rather
than guess.

## Scoring — a diagnostic, not a grade

`worker/tools/compare_benchmark.py`, usable on its own against any candidate:

```bash
python worker/tools/compare_benchmark.py CAND.json --case faria-2006-slab/thermal
```

The output is a list of concrete misinterpretations — *"u_ref 15 °C where the
paper says 10"*, *"cube where the reference is a rectangle"*, *"declared
thermal_strain but never wrote the material section"*. The numbers exist to rank
runs; the findings are the point.

### Sections are not equally important

A `results.plots` block is presentation. A `materials` block **is** the physics.
Scoring them alike gives a number that moves for the wrong reasons, so each
section carries a strictness and a weight:

| strictness | sections | how it is compared |
|---|---|---|
| **STRICT** | `physics`, `materials`, `boundary_conditions`, `initial_conditions` | every key, every value, every difference reported |
| **NORMAL** | `geometry`, `time_stepping`, `metadata` | shape and the numbers that matter — mesh size is a choice, *dimension* is not |
| **LOOSE** | `results.queries` | matched on what is measured, not on ids or count |
| **IGNORED** | `results.plots`, `results.reference_curves` | presentation, and digitized data no model can produce |

### Names are free, roles are not

A model may call a material `concrete` or `sv40`, and a boundary `top` or
`surface_top`. It may not get the *value* wrong. So:

- **materials** are aligned by section shape (Jaccard, greedy), then compared
  key-by-key. The alignment is printed, so a finding can be checked.
- **boundary conditions** are aligned by `(field, type, component)` — `location`
  is excluded on purpose, because the group name is the model's own — then their
  payloads (`value`, `alpha`, `u_ref`, …) are compared strictly.
- **`physics_class`** is normalised for case and punctuation before comparison;
  `validation_method` is not compared at all, because across the corpus it ranges
  from the token `analytical_comparison` to a full sentence naming equations.

### Finding kinds

`missing` · `extra` · `wrong_value` · `wrong_unit` · `wrong_model` ·
`type_differs`

`wrong_unit` is split out from `wrong_value` when the ratio is a clean
1e3 / 1e6 / 86400-type factor. A transcription slip and a misreading of the paper
need different fixes, and lumping them together hides which one you have.

### Validity is reported beside accuracy, never folded in

`validates` is binary per case — parses, matches the schema, passes
`check_benchmark.py`. A spec can be perfectly valid and describe the wrong
experiment, so the two numbers stay apart.

Two rules are excluded: `reference-missing` and `bc-table-missing` check that a
*sibling file* exists on disk. A model authoring from a paper cannot produce a
digitized CSV, so they would fire on every candidate and carry no signal.
`--strict-files` puts them back.

### Findings are not the unit a person reads in

The comparator reports one finding per differing key, which is right for scoring
and wrong for reading. A model that simply never writes `time_function` on a
boundary condition produces one finding per condition; a model that authored a
full THM problem where the reference is the conduction-only leg produced **15
findings across five sections from one decision**.

`worker/tools/cluster_findings.py` groups findings by the decision behind them.
It is purely mechanical — no model, no network — and it never touches a score,
because clustering is a view over findings and findings are what accuracy is
computed from. It runs automatically, and standalone:

```bash
python worker/tools/cluster_findings.py RUN/<model>/<case>/score.json
```

```
28 finding(s) -> 14 cause(s)

  14x  [physics] the spec models physics the reference does not have: displacement,
                 hydro_mechanical, pore_pressure
        a scope decision, not 14 mistakes — every material, condition and query
        attached to those follows from it
   2x  [boundary_conditions] 2 `temperature/dirichlet/-` conditions in the
                 reference, absent from the spec
```

### The census

`report.py` ends with **What gets misinterpreted**, at two resolutions. **By
cause** first — one row per decision, ranked by how many models made the same
one *on the same case*. **Key by key** below it, the comparator's own resolution,
for checking a cause rather than for reading first.

A row every model hits is a property of the paper or of the authoring guide, not
of any one model, and those are the rows worth acting on.

## Why, not just what

Scoring answers *what differs*. It cannot answer *why*: `score.json` holds a
candidate value and a reference value and no source, so "u_ref 15 vs 10" is
unexplainable from it. `explain.py` supplies the paper, the reference and the
candidate to an analyst model and asks for the diagnosis.

```bash
worker/worker.sh eval --explain --allow-metered          # worst 5 cases per model
worker/worker.sh eval --explain RUN_ID --worst 0         # every scored candidate
worker/worker.sh eval --explain RUN_ID --no-paper        # cheap; loses "the paper says"
worker/worker.sh eval --explain RUN_ID --rollup-only     # rewrite the summary only
```

### Two things it deliberately is not

**Not self-critique.** The analyst is ONE fixed model — `analyst: true` in
`models.yaml` — for every (model, case), never the model under audit. If each
model diagnosed its own output, summary quality would track that model's
ability, and this eval exists to keep a 31-billion-parameter model and a
frontier model comparable on one identical input.

**Not part of the score.** Accuracy stays exactly what `compare_benchmark.py`
computed, offline and reproducible. The diagnosis sits beside it, the way
`validates` does.

### Grounded, so a claim can be checked

The analyst must assign every finding index to exactly one group, and coverage is
then verified mechanically — uncovered, duplicated and invented indices are all
recorded in `explain.json` and flagged in `explain.md`. A diagnosis that does not
account for the findings is still written, but the reader is told.

Each group is tagged `model_error`, `comparator_artifact` or `paper_ambiguous`.
That last distinction is the point: the **artifact rate** tells you which
comparator rule to fix, and every rule fixed makes every future run's accuracy
mean more. On the first trial run it independently found the three false
positives already known by hand — `physical_groups (count)`, `dt_strategy.type`
`uniform` vs `fixed dt`, and `physics_class` `mechanics` vs `mechanical`.

### Cost

`--worst 5` is the default because a full pass is one call per scored candidate
with a whole paper in it. Diagnoses are cached on
`(candidate, reference, prompt version, analyst)`, so a `--rescore` does not make
you pay again; `--force` ignores the cache. The run-level rollup is a single call
that sees only the grouped diagnoses — it is the cheapest part and the one that
feeds back into `CREATE.md`.

## Layout

```
worker/jobs/eval/
  prompts/author_benchmark.md  the single-shot authoring prompt
  prompts/explain_case.md      the per-case diagnosis prompt
  prompts/explain_run.md       the run-level rollup prompt
  run_eval.py                  (model x case) -> candidate.json + score.json/.txt
  report.py                    -> leaderboard.md
  explain.py                   -> explain.json per case, explain.md per run

worker/
  models.yaml                  which models; `metered: true` gates spending,
                               `analyst: true` names who writes the diagnoses
  tools/compare_benchmark.py   candidate vs reference -> findings + score
  tools/cluster_findings.py    findings -> the decisions behind them

workspace/runs/<run-id>/       machine output, gitignored ($EVAL_RUNS overrides)
  run.json                     what was asked, and what every call did
  leaderboard.md               report.py: the tables and the census
  explain.md                   explain.py: the diagnosis and the artifact rate
  <model>/<case>/raw.json      the API response, untouched
  <model>/<case>/candidate.json
  <model>/<case>/score.json    the comparator's output
  <model>/<case>/score.txt     the same, for a person: causes, then findings
  <model>/<case>/explain.json  the analyst's grouped diagnosis + coverage check
```

Raw responses are written **before** scoring, so `run_eval.py --rescore RUN_ID`
re-scores a finished run after a comparator change without paying for the calls
again.

## Outcomes

`ok`, and four failures kept distinct because a person responds to each
differently:

| outcome | means | what to do |
|---|---|---|
| `truncated` | `finish_reason: length`, or the whole budget went to `reasoning_content` | raise `max_tokens` for that model in `models.yaml` |
| `unparseable` | a reply arrived with no parseable JSON object in it | a real model failure |
| `refused` | the endpoint rejected the call (budget) | not a model result |
| `error` | transport or HTTP failure | retry |

`unparseable` and `truncated` replies with content get the parser error back,
with the text around the error position marked, up to `--json-repair-turns`
times (default 1; `0` disables). The record keeps the first reply's `outcome`, so
the parse rate still measures the model; a repaired object is scored like any
other and flagged `json_repaired`. Replies land in `json-repairs/attempt-N/`.

Reasoning models on this endpoint put the chain in `reasoning_content` and the
answer in `content`. The runner reads `content` only — scoring the other one
would be scoring the thinking.
