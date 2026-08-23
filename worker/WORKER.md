# Worker contract: one PDF → one benchmark

You are a single-use worker agent with a fresh context window. You process exactly
ONE benchmark paper end to end and then stop. All state is written to files —
nothing is carried over to any other agent or run.

## Inputs (substituted by the runner — these are absolute paths, use them verbatim)

| placeholder | meaning |
|---|---|
| `{{PDF}}` | the paper, already copied into the workdir |
| `{{WORKDIR}}` | your workdir, `AutoBench/{{SLUG}}/`. Everything you write goes here |
| `{{PY}}` | the Python interpreter (all deps installed) |
| `{{THCM}}` | the solver entry point |
| `{{RENDER}}` | `pdf_render.py` — page render / panel crop |
| `{{STATUS_PY}}` | `status.py` — the ONLY way you write `STATUS.json` |
| `{{HANDOFF_PY}}` | `handoff.py` — park a figure a human must digitize, and read it back |
| `{{SCHEMA}}` | `benchmark.schema.json`, the structural source of truth |
| `{{SKILL_CREATE}}` | CREATE.md — authoring `benchmark.json`. Read it FIRST and completely |
| `{{SKILL_WEB}}` | EXTRACT-WEB.md — the ONLY extraction route |
| `{{SKILL_ANALYSIS}}` | ANALYSIS.md — output analysis |

Budgets: at most **2 fix iterations** (Phase 6) — a hard cap, not a target. This is
attempt **{{ATTEMPT}} of {{MAX_ATTEMPTS}}**; if it is not the first, a previous agent
died mid-run and left its work behind (see Phase 0).

The skills are written against the solver repo and use paths relative to it
(`tools/...`, `SKILLs/...`, `debug/<bm>/OOFEM/`). **Ignore those paths.** The
placeholders above are authoritative, and your output layout is the one below.

## Workdir layout (create exactly this)

```
{{WORKDIR}}/
  {{SLUG}}.pdf              the paper (already there)
  <case>/                   ONE case dir, named for the physics: thermal,
    benchmark.json          thermo_mechanical, hygric, ...
    reference/              digitized reference curves (CSV)
      _web_raw/             the digitizer's unmodified exports
    web/                    rendered pages and panel crops
    input/  output/         created by the solver
  STATUS.json   REPORT.md   at the top level, one per workdir
```

There is no `OOFEM/` level — CREATE.md's `debug/<bm>/OOFEM/benchmark.json` becomes
`{{WORKDIR}}/<case>/benchmark.json` here. The layout matches the curated corpus so a
finished benchmark can be promoted with a single `git mv`.

## Rules above all

1. One PDF only. Never create, edit, or read another benchmark's directory — not
   under `AutoBench/`, not under `benchmarks/`. In particular do not read
   another benchmark's `REPORT.md` or `STATUS.json`: your analysis must be
   independent. Your OWN `{{WORKDIR}}` is the one exception (Phase 0).
2. **`_needs_human/` is the human's, not yours.** You may READ anything in it. You
   may never write, move, rename, empty or delete anything in it — not to tidy it,
   not to re-park a figure, not because you believe the contents are wrong. Copy out
   if you must; the original stays where the human put it. A pipeline that deletes
   the work it asked a person to do is worse than one that never asks.

   And provenance there is settled by `{{HANDOFF_PY}} scan`, which fingerprints what
   the agent created, so anything else came from a human. Do NOT reason about it
   yourself from checksums or from what a previous attempt believed. "These bytes
   match a file an earlier attempt distrusted" proves only that nobody changed the
   file since; it is not evidence about who wrote it, and treating it as evidence
   makes one attempt's mistake permanent.
3. Every number carries a provenance tag per CREATE.md (`PAPER_VALUE`, `INFERRED`,
   `ASSUMED`, `MISSING`, …). Never invent a value. Never calibrate physics parameters
   to force a fit.
4. Work through the phases in order. Each phase has explicit exit verdicts — when one
   fires, stop immediately.
5. Whatever happens, your LAST two actions are writing `{{WORKDIR}}/REPORT.md` and
   the terminal `STATUS.json` (`finish`, below). A run without a terminal
   `STATUS.json` is treated as crashed and retried.
6. **Log every phase transition.** On ENTERING each phase, before doing its work:

   ```bash
   {{PY}} {{STATUS_PY}} phase "{{WORKDIR}}" <n> --note "<what you are about to do>"
   ```

   Add `--case <name>` the first time you know it, and `--set fix_iterations=<n>`
   whenever it changes. This is not bookkeeping for its own sake: if you die
   mid-phase — timeout, max turns, a crashed browser — this file is the ONLY record
   of where you got to, and it is what the next attempt reads in Phase 0. An agent
   that runs for three hours and logs nothing has produced nothing.

   Write `STATUS.json` ONLY through `{{STATUS_PY}}` — never by hand. It validates the
   state string (the runner keys on it; anything else reads as a crash and burns a
   retry), keeps the phase log append-only, and writes atomically. It also drops a
   `STATUS.json` symlink next to your `benchmark.json`, so the case directory answers
   "what happened here?" on its own.

## Phase 0 — Resume check

```bash
{{PY}} {{STATUS_PY}} show "{{WORKDIR}}"     # state, phase reached, full phase log
{{PY}} {{HANDOFF_PY}} scan "{{WORKDIR}}"    # has a human supplied extraction data?
```

Run BOTH, always. If the scan reports human data, go to Phase 3b — **whatever the
recorded state says, and wherever the files are**. A person who could not find a
handoff folder (because an earlier attempt died before making one) will have put
their export somewhere sensible instead: next to the crop in `<case>/web/`, or loose
in the case directory. The scan looks there too. Data outside `_needs_human/` is
human data like any other — use it, and afterwards say in REPORT.md where you found
it. A run can be parked for a human and then be recorded as
crashed by a later mishap; the files on disk are the truth, the state string is only
a summary of them.

Read `{{WORKDIR}}` (and only it). If a `STATUS.json` or `STATUS.attempt*.json` is
there, a previous attempt stopped: read them and `REPORT.md`. The phase log tells you
where it stopped and when — start from there, not from scratch. Then verify on disk what
actually got finished — `<case>/benchmark.json` exists and validates, which
`reference/*.csv` exist and are wired into `results.reference_curves`, whether
`output/` holds a completed run. Keep verified work, redo nothing that passes its own
check, and carry `fix_iterations` forward — the 2-iteration budget is per benchmark,
not per attempt. Say in REPORT.md what you inherited. On a fresh workdir this phase
is a no-op.

## Phase 1 — Feasibility → verdict FEASIBLE / NOT_FEASIBLE

- Read the PDF **in page ranges**: the Read tool takes a `pages` argument, needs it
  for any PDF over 10 pages, and accepts at most 20 pages per call. A paper is
  typically 15-30 pages, so this is two or three calls (`pages: "1-15"`, then
  `"16-30"`) — not one. An error on a whole-file read is that limit, not a broken or
  protected file; check `{{PY}} {{RENDER}} pages "{{PDF}}"` for the page count and
  read in ranges. Only treat the paper as unreadable if `pdf_render.py` itself cannot
  open it.
- Build the value table first (parameter → value → unit → where in the
  paper), per CREATE.md Authoring Workflow step 1. No JSON before this table exists.
- Answer CREATE.md's four START-HERE questions (physics, dimension, hydration
  kinetics given?, creep/shrinkage data given?). The physics answer names your
  `<case>` directory.
- Capability check: cross-check the required physics against CREATE.md "What this
  backend CAN and CANNOT do" and the schema's `x-model-status`. Use the four-state
  vocabulary (wired / unverified / not wired / legacy only / impossible).
- Inventory the figure pages: `{{PY}} {{RENDER}} pages "{{PDF}}"`. Note which pages
  carry the validation figures. Raster vs vector does NOT matter here — the
  extraction route is the same either way.
- Verdict NOT_FEASIBLE if either: required physics is `impossible`; or a
  validation-critical parameter is `MISSING` and cannot be soundly inferred. Write
  STATUS.json (`state: "infeasible"`, with the concrete reason — which physics, which
  parameter) and a short REPORT.md. STOP. `not wired` is reported as effort, not as a
  wall — name the OOFEM class and continue only if the benchmark is meaningful
  without it.

## Phase 2 — Author benchmark.json

Follow CREATE.md exactly: unit conventions box, hand sanity checks against the
paper's own numbers, then write `{{WORKDIR}}/<case>/benchmark.json` with a `_comment`
provenance tag on every value. Validate:

```bash
{{PY}} -c "import json,sys; from jsonschema import Draft202012Validator as V; \
s=json.load(open('{{SCHEMA}}',encoding='utf-8')); \
d=json.load(open('{{WORKDIR}}/<case>/benchmark.json',encoding='utf-8')); \
[print(list(e.path), e.message) for e in V(s).iter_errors(d)] or print('OK')"
```

Fix every error. Finish the Pre-Flight Checklist and the (mandatory) Assumptions
Report — put it in REPORT.md.

## Phase 3 — Extract reference data — `{{SKILL_WEB}}` ONLY

Read `{{SKILL_WEB}}` and follow it. It is the only extraction route: every figure is
digitized by the AI reader at plot2data.com through the browser, whether the figure
is vector or raster. Do NOT look for the vector extraction tool
(`pdfplot2csv.py`) — it is not available here, and any skill text pointing at it does
not apply.

The two PDF-side commands you need:

```bash
{{PY}} {{RENDER}} render "{{PDF}}" <page> "{{WORKDIR}}/<case>/web/" [--dpi 300]
{{PY}} {{RENDER}} crop "{{WORKDIR}}/<case>/web/page<N>.png" <x0> <y0> <x1> <y1> \
       "{{WORKDIR}}/<case>/web/fig<N>_<panel>.png"
```

Render the page, LOOK at it, read off the panel's bounding box as fractions of the
page, crop tight (axes + tick labels + legend, no caption, no neighbouring panel),
upload that crop. Everything downstream is per EXTRACT-WEB.md: raw export to
`reference/_web_raw/`, normalized CSVs to `reference/`, units converted to the
benchmark's conventions by you, the overlay check, the cross-check against a number
from the paper's text or tables, the `AI-DIGITIZED` note in `metadata.notes`, and the
wiring into `results.reference_curves`.

Decide, per figure, whether it is **validation-critical**: does the benchmark's claim
rest on it? Say so in REPORT.md before you start uploading — deciding afterwards is
how a failed figure gets quietly demoted to "optional".

Budget: **2 upload attempts per figure** (the second with a tighter crop). Then:

- **A non-critical figure fails** → record it in `extraction.figures_failed` with the
  reason and continue. Do not park the run for it.
- **A critical figure fails** → hand it to a human (Phase 3a). Do not continue to
  Phase 4 without it: a scorecard against the figures that happened to work is not a
  validation of this paper.
- **The browser or the site is unavailable** (extension not connected, site down or
  refusing) → this is not a figure that cannot be read, it is a run that cannot
  start. Do not spend upload attempts on a broken browser, and never record
  `extract_failed` — that verdict is permanent and this condition is not. What you do
  next depends on how much rope is left:

  - **Attempt {{ATTEMPT}} < {{MAX_ATTEMPTS}}** → stop immediately WITHOUT a terminal
    state, saying so on stdout. The runner records a crash and retries, which is the
    right answer for an outage that may clear on its own.
  - **Attempt {{ATTEMPT}} == {{MAX_ATTEMPTS}} (this is the last one)** → do NOT just
    stop. Park the critical figures for a human first (Phase 3a): render the page,
    crop the panel, write the request, then `--state needs_human`. Otherwise the run
    dead-ends — attempts exhausted, no folder for anyone to fill in, and the queue
    can only report "needs a human" without saying where to put anything. A broken
    extension is not always transient; after two failures, assume it is not.

Never weaken the overlay or cross-check to make a curve pass, and never fall back to
reading points off the figure yourself — hand it over instead. Your reading of a plot
is not more reliable than the digitizer's, and it arrives without the audit trail.

## Phase 3a — Hand a figure to a human

For each critical figure that failed, park it:

```bash
{{PY}} {{HANDOFF_PY}} request "{{WORKDIR}}" --spec '<json>'
```

The spec — write it from what you already know, it is what makes the result ingestible:

```json
{
  "figure": "fig7a", "case": "<case>", "page": 7, "critical": true,
  "crop": "web/fig7a.png",
  "why_failed": "both sensor curves are solid black; the site merged them (2 uploads)",
  "x": {"label": "time", "unit": "hours", "range": [0, 168], "log": false},
  "y": {"label": "temperature", "unit": "degC", "range": [10, 80], "log": false},
  "curves": [{"id": "fig7a_sensor_t1", "legend": "T1 (core)", "note": "upper curve"}],
  "cross_check": {"what": "peak core temperature, Table 3", "value": 68.5, "unit": "degC"}
}
```

The `id`s must be the reference-curve ids you already wired into
`results.reference_curves`, and the units must be **the figure's own printed units** —
the human enters what the axes show, you convert on the way back in. `range` is what
you read off the axes; it is how the human catches a mis-scaled digitization.

Then stop and wait for the human:

```bash
{{PY}} {{STATUS_PY}} finish "{{WORKDIR}}" --state needs_human --case <case> \
  --key-result "parked: fig7a, fig9b need manual digitization (curves merged)" \
  --set 'extraction={"figures_digitized":["fig5"],"figures_parked":["fig7a","fig9b"]}'
```

`needs_human` is NOT a verdict — the queue will not retry it, and will not treat the
benchmark as finished. A human fills in the CSV templates and resumes you.

## Phase 3b — Ingest a human's extraction

You reach this whenever Phase 0 finds `state: "needs_human"`. Do it before anything
else, and treat it as the normal way this data arrives — not an exception.

```bash
{{PY}} {{HANDOFF_PY}} scan "{{WORKDIR}}"    # what a human has dropped in, per figure
```

**Scan the whole `_needs_human/` tree yourself as well.** The human is not required to
follow a format, and you must not demand one. What lands there might be the prepared
templates filled in, or a raw export straight off the digitizer site under whatever
name the browser gave it, or a `.txt`, or a spreadsheet, or three curves in one file
instead of three files. Any of it counts. Your job is to recognize that data arrived
and reshape it; making the human match a schema defeats the point of asking them.

For each figure folder, work out what you were given, using `request.json` as the key:

- **Which curve is which.** Match on filename first (the templates are named for the
  curve ids), then on column headers, then on the legend text in `request.json`, then
  on the `note` field (`"upper curve"`). If a single file holds several curves —
  common in site exports: `x,y1,y2,y3` or repeated `x,y` column pairs — split it.
  If you genuinely cannot tell two curves apart, say so rather than guessing; a
  swapped pair is worse than a missing one.
- **Which columns.** x then y is the convention, but an export may be reversed, may
  carry an index column, or may use `;` with a decimal comma. Read the numbers, not
  the header.
- **Which units.** The templates' headers name the figure's own units. A raw export
  carries the axis values as printed, which is the same thing. Convert to the
  benchmark's conventions yourself (CREATE.md units box) and record the factor in the
  curve's `_comment`. If the reference CSV keeps the figure's units, set `x_scale` /
  `y_scale` on the reference curve so the loader converts — a curve in days compared
  against a model time base in seconds is wrong by 86400, and it will read as a
  physics deviation in Phase 5.
- **Sanity-check against `request.json`** before trusting anything: the x/y `range`
  fields say what the axes spanned. Data outside them, or clustered at round numbers
  when the axis is log, means it was misread — say so, do not silently clip.

Then:

1. Write the normalized result to `<case>/reference/<curve_id>.csv` and wire it into
   `results.reference_curves`. Leave everything in `_needs_human/` exactly as it is:
   it is the raw reading, and the audit trail for your conversion.
2. Verify as you would your own extraction — overlay the normalized curves on the
   folder's PNG, and cross-check the value named in `cross_check`. Human numbers get
   the same scrutiny as machine numbers; a hand-typed decimal point is at least as
   easy to get wrong as an AI misread.
3. Tag provenance `HUMAN_DIGITIZED` (not `AI-DIGITIZED`) in `metadata.notes`.
4. If a figure genuinely arrived with NO usable data, park just that one again with a
   `"human_note"` saying precisely what was missing — file name included. Do not park
   figures that arrived fine, and do not park a figure you can run with.
5. **If you doubt the numbers: say so, then keep going.** You may well be right that a
   curve disagrees with the figure — points inside a data gap, an inverted curve
   ordering, round-number x values where digitized markers were expected. Those are
   real findings. But a doubt about some points is not a reason to stop: the run is
   what turns it into evidence.

   Default: **proceed to Phase 4 with the data as given**, and carry the doubt
   forward —

   - `CONCERNS.md` in that figure's folder: what you checked, how you checked it,
     which specific points disagree and by how much, what the figure shows instead.
     Numbers, not adjectives. Say plainly which curves you are *accepting*, so a
     human is not invited to redo work that was fine.
   - `--set 'extraction={"concerns":["fig7 TP18 tail low by up to 3.5 K after 0.5 d"]}'`
     on the status file, so it is visible without opening the folder.
   - Phase 5 scores against those curves *with the reservation stated*: quantify the
     disagreement you measured, and say which part of any deviation it could account
     for. A benchmark whose reference data carries a known 3 K uncertainty on one
     curve is still worth running — it just cannot claim agreement tighter than that.

   Park ONLY when a critical figure has **no usable data at all** — nothing arrived,
   or what arrived cannot be matched to any curve. "Some of it looks wrong to me" is
   not that.

   You must never move the data out, empty the templates, mark it fabricated, or fail
   the benchmark on your own reading. You are one reader of a crop; the person who
   typed those numbers had the paper open.

6. **Do not raise the same concern twice.** If `CONCERNS.md` already exists for a
   figure from an earlier attempt and the human has re-run the queue without changing
   those files, that IS their answer: they have seen it and want you to proceed. Use
   the data, keep the reservation in REPORT.md, and do not park again on that point.
   `{{HANDOFF_PY}} scan` tells you whether the files changed since the concern was
   written.

7. Continue at Phase 4.

## Phase 4 — Run

```bash
timeout {{SOLVER_TIMEOUT}} {{THCM}} oofem "{{WORKDIR}}/<case>" --remesh
```

First run must remesh; later runs only after a geometry change. If it errors and the
cause is a mistake in your JSON, fix it (this consumes one fix iteration). If it hits
the timeout, say so in REPORT.md with the step count reached and treat it as
`run_failed` unless a whitelist fix (Phase 6 — dt strategy, `linear_solver: "dss"`)
plausibly brings it inside budget. If the evidence points to a pipeline bug, do not
attempt code fixes — STATUS.json `state: "run_failed"`, document the evidence. STOP.

## Phase 5 — Analyze — `{{SKILL_ANALYSIS}}`

Follow ANALYSIS.md in its mandated order: Phase 0 (read the JSON), 0b (capability),
0c (time base / dropped kwargs / probe snap / mesh integrity) BEFORE any physics,
then the Phase 1 scorecard, VTU fields, and the thought pipelines. Obey the honesty
rules: every claim carries a number; quantify the unexplained remainder.

One addition here: your reference curves are AI-digitized, not exact. Before blaming
the model for a deviation, ask whether the digitizer could account for it — state the
digitization error character (from the Phase 3 overlay and cross-check) alongside the
model deviation, and do not attribute to physics what is inside the reading error.

Write the Phase-5 report into REPORT.md. Verdicts:

- ACCEPTABLE — deviations explained or within a tolerance you state and justify.
- FIXABLE — a whitelist fix exists (Phase 6).
- STUCK — neither. Go to Phase 7.

## Phase 6 — Bounded fix loop (the anti-loop contract)

Whitelist — mechanical, paper-grounded corrections ONLY:

- unit conversion errors (CREATE.md units box)
- axis calibration / tick misreads from Phase 3 (re-crop and re-digitize the panel)
- probe coordinates vs the sensor positions given in the paper
- schema violations and missing required keys
- time grid / dt strategy; `linear_solver: "dss"` for slow runs
- BC values or event times that are stated in the paper and were transcribed wrong

Forbidden:

- tuning material parameters to improve the fit (that is calibration, not correction)
- inventing values the paper does not give
- weakening the overlay/cross-check, or loosening thresholds
- editing pipeline or tool code (ANALYSIS.md Phase 4: report pipeline findings, do
  not fix code)

Loop: at most 2 iterations total across Phases 4–6, carried across attempts. ONE
whitelist fix per iteration, then a full re-run and re-analysis. If no whitelist fix
applies, or the budget is spent: go to Phase 7.

## Phase 7 — Terminate

Write REPORT.md (what you inherited, scorecard, what matched, findings ranked by
impact with numbers, digitization error character, unexplained remainder, fixes
applied, remaining deviations + the discriminating test that would resolve them),
then close the status file:

```bash
{{PY}} {{STATUS_PY}} finish "{{WORKDIR}}" --state <state> --case <case> \
  --key-result "peak 70.0 vs 59.0 °C (+11.0 K), cause: ..." \
  --set 'fixes_applied=["units: MPa->Pa"]' \
  --set 'feasibility={"physics":"thermal","missing":[]}' \
  --set 'extraction={"figures_digitized":["fig7a"],"figures_failed":[]}'
```

The resulting file:

```json
{
  "pdf": "{{PDF}}",
  "slug": "{{SLUG}}",
  "case": "<case dir name>",
  "state": "done | infeasible | extract_failed | run_failed | stuck | needs_human",
  "phase_reached": 7,
  "phase_log": [{"phase": 1, "at": "<ISO-8601>", "note": "feasibility"}, "..."],
  "fix_iterations": 0,
  "fixes_applied": ["..."],
  "feasibility": {"physics": "...", "missing": []},
  "extraction": {"figures_digitized": [], "figures_failed": []},
  "key_result": "peak 70.0 vs 59.0 °C (+11.0 K), cause: ...",
  "report": "{{WORKDIR}}/REPORT.md",
  "updated": "<ISO-8601>"
}
```

`--state` accepts only the listed strings, so a typo fails loudly at the command
instead of silently reading as a crash. `needs_human` is the one non-verdict among
them — the run is parked, not judged. `done` includes "runs and matches
with stated, explained deviations". `stuck` means: a human should look — say exactly
what you checked and what would discriminate the remaining causes.

## Final stdout line (the runner greps for it)

`STATUS: <state> | <one-line summary with the key numbers>`
