# Authoring eval

**Which model can write a correct `benchmark.json` from a paper?** No solver runs —
this scores the spec, not the physics.

```bash
python worker/tools/build_dataset.py         # once: benchmarks/ -> dataset/
worker/worker.sh eval --probe       # what is reachable, and your budget
worker/worker.sh eval                       # free models, every case with a paper
worker/worker.sh eval --report        # leaderboard
```

## Cost

Models marked `metered: true` in `models.yaml` are refused unless you pass
`--allow-metered`. **A run with no flags costs nothing** — it touches only the
KIT-hosted (`kit.*`) models, which are unmetered.

The metered ones are billed against a per-user KIT budget and return
`HTTP 400 ExceededBudget` once it is spent. `--probe` tells you which is the case
before a sweep discovers it the expensive way.

## What it does

One identical single-shot call per (model, case): `SKILLs/CREATE.md`, all six
physics modules, a compacted `benchmark.schema.json`, and the paper's text in —
one JSON object out. No tools, no phases, no browser.

That is deliberate. It isolates *"can it author the spec"* from *"does the physics
reproduce"*, and it keeps a 31-billion-parameter local model and a frontier model
comparable on the same input. The agentic path — where the model gets tools, the
PDF, and the seven phases — is `worker/dispatch/dsh.py`, and it answers a
different question.

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

### The census

`report.py` ends with **What gets misinterpreted** — every finding ranked by how
many models made it *on the same case*. A row every model hits is a property of
the paper or of the prompt, not of any one model, and those are the rows worth
acting on.

## Layout

```
worker/jobs/eval/
  models.yaml                  which models; `metered: true` gates spending
  prompts/author_benchmark.md  the single-shot prompt
  run_eval.py                  (model x case) -> candidate.json + score.json
  report.py                    -> leaderboard.md

workspace/runs/<run-id>/       machine output, gitignored ($EVAL_RUNS overrides)
  run.json                     what was asked, and what every call did
  <model>/<case>/raw.json      the API response, untouched
  <model>/<case>/candidate.json
  <model>/<case>/score.json
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
| `unparseable` | a reply arrived with no JSON object in it | a real model failure |
| `refused` | the endpoint rejected the call (budget) | not a model result |
| `error` | transport or HTTP failure | retry |

Reasoning models on this endpoint put the chain in `reasoning_content` and the
answer in `content`. The runner reads `content` only — scoring the other one
would be scoring the thinking.
