# JOB: oofem

**Can a model write an OOFEM simulation that reproduces a paper?**

The sibling `eval` job asks whether a model can author a `benchmark.json` — a
*spec*, scored key by key against a reference spec. This job removes the spec.
The model writes Python against the `oofempy` bindings, meshes the geometry
itself, runs the solver, and is scored on the **curve that comes out**.

```bash
worker/worker.sh oofem --list-curves                    # what each case must produce
worker/worker.sh oofem --dry-run --cases ATENA/TPBB     # build the prompt, call nothing
worker/worker.sh oofem --models kit.deepseek-v4-flash --limit 1
worker/worker.sh oofem --rescore RUN_ID                 # re-score, no calls, no solving
```

## Two arms

```bash
worker/worker.sh oofem                      # one shot: write the script blind
worker/worker.sh oofem --agent              # agentic: explore, spike, converge
```

Same cases, same curve contract, same workdir, same scorer — the only
difference is how `candidate.py` gets produced. That is what makes them
comparable.

**Why the agentic arm exists.** The one-shot arm's first three real failures
were all OOFEM API knowledge, not physics: a material that does not exist, a
required keyword it could not have known about, a setup that segfaults. The
bindings cannot answer those questions — every factory function reports itself
as `isoLE(*args, **kwargs) -> object`. The answers live only in the C++ source,
as ~2400 `#define _IFT_...` macros. So the agentic arm hands the model the
source tree and a Python sandbox and lets it find out.

Tools: `list_dir`, `read_file`, `read_paper_page`, `grep`, `inspect_oofem`,
`run_python`, `write_file`, `run_candidate`, `submit`. Vision-capable models
also receive `view_paper_page`, which renders one requested PDF page and adds it
to the following model turn.
Read access covers the OOFEM tree and the workdir; writes are workdir-only.

`inspect_oofem` is focused source retrieval rather than embedded API knowledge:
given one class/factory name, it returns matching official Python examples,
registration lines and `_IFT_` keyword definitions from the installed checkout.

`run_candidate` executes the current candidate from a clean directory and checks
the output contract. Writing `candidate.py` invalidates that validation, and
`submit` accepts only the exact source that most recently passed. A failed
submission returns feedback to the agent instead of ending its turn loop.

The first draft is requested after one third of `--max-turns` has elapsed, so
two thirds remain for integrated testing and repair. After that normal budget,
`--finalization-turns` (default 4) grants a small submit-only tail for a final
edit/run/submit sequence. Transient API failures are retried according to
`--api-retries` (default 2) without consuming a model turn.

**The paper is a file, not prompt text.** The one-shot prompt carries up to
400k characters of paper; across forty turns that is unaffordable. In agent
mode the complete, layout-sorted text is written to `work/paper.txt`, the
original is copied to `work/paper.pdf`, and both are accessed page by page
through tools. The `--max-paper-chars` limit applies only to the one-shot arm,
so a long paper is not silently cut off for the agent.

**Accounting.** Turn count alone is not a fair axis when models take different
numbers of turns, so every turn's `usage` is summed into the record:
`turns`, `tool_calls`, `prompt_tokens`, `completion_tokens`, and `cost_usd`
when `models.yaml` carries `price_in_per_mtok` / `price_out_per_mtok` for that
model. Without a price the cost is `null`, never a guess.

**Cheating is possible and is not prevented.** The model can read `reference/`
and fit to it without solving anything — but the paper contains the same
values, so withholding the CSV would buy little. Instead the run is auditable:
`transcript.jsonl` records every turn and every tool result, and
`solver_evidence` records whether the submitted script mentions `oofempy` at
all. Signals, not gates.

## Why the scoring is different

There is no reference artifact to diff against. OOFEM builds its problem in
memory and never writes a deck, so nothing here compares text. The only
question asked is whether the thing runs and lands on the paper's data.

Each curve is scored by interpolating the simulation onto the reference's own
x samples:

```
nrmse    = rms(sim - ref) / (ref y range)
coverage = fraction of reference x samples the simulation spans
score    = coverage * max(0, 1 - nrmse)
```

Coverage is a separate factor because the common early failure is a run that
dies a tenth of the way through the time span. Without it, a simulation that
produced the first 3 % of the curve and got that bit right would outscore one
that ran to the end and was slightly off everywhere.

## What the model is given, and what it must produce

In: the paper text, the case's `scope.md`, the exact curve contract, and the
oofem checkout's own `test_5.py` and `util.py` as API reference. **Not** the
mesh — it derives the geometry from the paper and meshes it itself.

Out: one Python script that writes, into its working directory,

- `results.csv`, long format `curve,x,y`, using the exact curve ids it was given
- `plot.png`, one subplot per curve, simulation over the reference data

Long format because a case mixes x-axes — some curves are against time, others
against position — so one wide table cannot hold them without lying about one.

`reference/` is copied into the working directory before the run, so the script
can read the digitized data it has to plot against.

## Curves are (file, column) pairs, not files

89 of the corpus's 102 reference CSVs are a clean two columns. The other 13
carry several curves sharing an x, so the unit is `<file stem>::<y column>`.
`curves.py` owns that resolution and is the **single source of truth**: the
prompt states exactly the ids the scorer will look for, so the two cannot drift.

A case may override the rule with `reference/curves.json` — see `curves.py`.
Two cases need one today (`ATENA/UAC`, `cervera-1999-thermo-chemo-partI`).

## Outcomes

| outcome | meaning |
|---|---|
| `unparseable` | no syntactically valid Python in the reply |
| `truncated` | hit the token cap |
| `rejected` | static gate: banned import, or never imports `oofempy` |
| `crashed` | ran and raised |
| `timeout` | exceeded `--run-timeout` |
| `no_output` | exited 0 but wrote no `results.csv` |
| `incomplete` | agent exhausted/stopped without submitting a validated candidate |
| `ok` | produced results; `overall` carries the score |
| `refused` / `error` | budget or transport problem, not a model failure |

## Coverage

19 of 21 cases are scorable. Two are not, and are skipped with a message
rather than burning a call:

- `ATENA/GFM` — no reference data in the repo at all
- `HITEC/hm-excavation` — only nested per-team folders, no top-level curves

Nested reference folders (BenVaSim, HITEC) hold the *other* participating
codes' results. Those are alternative references to plot against, not targets:
asking a model to match seven teams at once is not a benchmark.

## Requirements

`oofempy`, plus `numpy` and `matplotlib` for the generated scripts.
`OOFEM_ROOT`, `OOFEMPY_DIR` and `OOFEM_PY` in `worker/config.sh` say where the
bindings are and which interpreter can import them — `oofempy` is a compiled
extension, so a Python minor-version mismatch is a hard `ImportError`, not a
warning. The job checks once at startup and says so, instead of producing 21
identical crashes.

`--dry-run` and `--list-curves` work without any of it.

## Not a sandbox

`static_gate()` blocks a short list of imports that have no business in a
finite-element script. That is a guard rail, not isolation — these are model-
written scripts executed on a workstation. Real isolation (container, seccomp)
belongs in the runner if this job ever runs unattended.
