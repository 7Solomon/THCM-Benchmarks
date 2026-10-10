# JOB: oofem-native

**Can a model write an OOFEM input deck that reproduces a paper?**

The `oofem` job asks this through the Python bindings: the model writes one
oofempy script. This job takes the bindings away. The model writes the native
text deck that the `oofem` executable reads, plus the Python on either side of
it. The **harness** runs the solver.

```bash
worker/worker.sh oofem-native --list-curves                    # what each case must produce
worker/worker.sh oofem-native --dry-run --cases ATENA/TPBB     # build the prompt, call nothing
worker/worker.sh oofem-native --models kit.deepseek-v4-flash --limit 1
worker/worker.sh oofem-native --agent --models kit.deepseek-v4-flash --cases ATENA/TPBB
worker/worker.sh oofem-native --rescore RUN_ID                 # re-score, no calls, no solving
```

Same cases, same curve contract, same scorer as `oofem`. `curves.py` and
`score.py` are **copies**, so a score here and a score there measure the same
thing: bindings vs deck, for one model on one case. The duplication is
deliberate. A change to one job's loop or scorer must not silently move the
other job's numbers. If you change one copy, decide whether the other copy
should change too.

## The candidate

| file | | |
|---|---|---|
| `make_deck.py` | optional | writes `model.in` (meshing, loops over nodes, gmsh) |
| `model.in` | required unless generated | the OOFEM input deck; `@include`d files count too |
| `postprocess.py` | required | reads what the deck's export modules wrote, writes `results.csv` + `plot.png` |

Both arms run exactly one function, `pipeline.run_pipeline`, in one directory,
under one `--run-timeout`:

```
python make_deck.py  →  oofem -f model.in  →  python postprocess.py
   (if present)          (by the harness)
```

**Python is allowed around the solver, never in it.** A stage that imports
`oofempy` or `oofem` is `rejected`, and so is one that imports `subprocess`,
`socket`, etc. The model never starts the solver itself. The Python stages
also run without `OOFEM_LD_PRELOAD`, which the `oofem` wheel in `.venv` needs
in order to load. That makes a second, quieter fence.

When `make_deck.py` exists, its `model.in` is the one that gets solved. A
`model.in` left in the workdir is then not part of the candidate, so a scratch
deck from a spike cannot invalidate a validated submission.

## What the model is given

- `prompts/deck_primer.md`: hand-written. It covers record order, sets, BCs,
  and the export modules that make curves extractable. File names and formats
  were checked against this build (see below).
- `prompts/oofem_records.md`: **generated** by `dump_keywords.py`. It lists
  every record keyword registered in the source *and* compiled into
  `liboofem.so`: 451 today; 86 registered classes live in modules that were
  not built. Regenerate it when the build changes:
  ```bash
  source worker/config.sh && .venv/bin/python worker/jobs/oofem-native/dump_keywords.py
  ```
- One-shot arm only: two worked decks from the checkout
  (`tests/tm/hydratingConcreteMat06.in`, `tests/sm/con2dpm5.in`).
- Agent arm only: OOFEM's own manuals, the sources of the ones published at
  oofem.org, copied into `manuals/` from the checkout this build comes from
  (commit and licence in `manuals/SOURCE.md`; LGPL 2.1 like OOFEM): the input
  data format (`oofemInput/*.rst`), the element library
  (`elementlibmanual`: DOFs, side numbering), the material library
  (`matlibmanual`: meaning, unit and typical value of every parameter) and
  the user manual. Refresh the copy when the build changes:
  ```bash
  source worker/config.sh && .venv/bin/python worker/jobs/oofem-native/manual.py sync "$OOFEM_ROOT"
  ```
  `manual.py` splits them into sections (307 today, 207
  documented records) and finds the ones that document a record;
  `inspect_record` shows them and `search_manual` searches them. The manuals
  lag the source in places, so `inspect_record` also lists documented
  keywords that no `_IFT_`/`ParamKey`/literal in `src/` defines.

### Facts the primer relies on (verified 2026-09-25)

- A bad deck makes oofem **abort** (exit 134) after a stack trace. The useful
  line is `OOFEM Error exception: ...`, and the pipeline surfaces it. Core
  dumps are disabled for the solver process.
- An **unknown attribute is not an error.** OOFEM prints `Warning:Unread
  token(s) detected ... [bogus][7]` and runs on without it. The pipeline
  records these warnings as `oofem_warnings`.
- `dm` / `gpexportmodule` write `<outfile>.m<N>.<step>.{dm,gp}`. OOFEM
  assigns `<N>` itself and it is *not* reliably the module's position, so the
  primer says to glob.
- `gnuplot tstep_all reactionforces 1` writes `ReactionForceGnuplotBC<id><X|Y>.dat`
  with columns `u F`. That is a load-displacement curve with no parsing of `.out`.

## Agent arm

`agent_native.py` is a copy of the oofem job's `agent.py`: the same turn
pressure, finalization turns, retries, image compaction and accounting. The
tools differ:

| tool | |
|---|---|
| `inspect_record NAME` | a record's class, its own and **inherited** attribute keywords (`_IFT_` defines, `ParamKey`s, literals read in its methods), the record's section from OOFEM's manuals, and lines from OOFEM's test decks that use it |
| `search_manual QUERY` | manual sections matching a topic, with `file:line`, the records they document and the best matching line (`manual.py`) |
| `run_oofem [deck]` | the executable on a spike deck: exit status, error lines, unread-keyword warnings, files written |
| `run_python` | for the mesh generator and the output parser; refuses oofempy/oofem imports |
| `run_candidate` / `submit` | the full pipeline from a clean stage dir; `submit` only accepts the exact files that passed |

If the agent stops without calling `submit` but its files are still exactly the
ones that last passed `run_candidate`, the harness submits them and the record
carries `auto_submitted: true`.

plus `list_dir`, `read_file`, `grep`, `read_paper_page` and `view_paper_page`,
all unchanged.

## Outcomes

| outcome | meaning |
|---|---|
| `unparseable` | one shot: no `postprocess.py` block, no deck/generator block, or Python that does not parse |
| `truncated` | hit the token cap |
| `rejected` | static gate: imports oofempy/oofem, or a banned module |
| `gen_crashed` / `no_deck` | `make_deck.py` failed, or wrote no `model.in` |
| `deck_error` | oofem exited nonzero; the message carries its `Error exception` line |
| `post_crashed` / `no_output` | `postprocess.py` failed, or wrote no `results.csv` |
| `timeout` | the pipeline exceeded `--run-timeout` (`failed_stage` says where) |
| `incomplete` | agent stopped with no validated candidate on disk |
| `ok` | produced results; `overall` carries the score |
| `refused` / `error` | budget or transport problem, not a model failure |

Every record also carries `failed_stage`, `stage_seconds`, `oofem_warnings`,
and `solver_evidence`: true when oofem exited 0 on the deck and wrote files.
Like the oofem job, this records evidence of cheating; it does not block it.

## Run layout

```
runs/<run-id>/<model>/<case>/
  raw.json | transcript.jsonl   the reply (one shot) or every turn (agent)
  candidate/                    the candidate files as extracted / submitted
  work/                         the run directory: reference/, oofem output, results.csv, plot.png
  run.log                       generate / solve / postprocess, stage by stage
  solved.in                     the deck oofem actually ran
  score.json | score.txt
```

## Requirements

The `oofem` executable. `OOFEM_BIN` (default: next to `oofempy.so`),
`OOFEM_ROOT` and `OOFEM_LD_PRELOAD` in `worker/config.sh`. The job solves a
two-node smoke deck once at startup instead of producing 21 identical
`deck_error`s. `--dry-run` and `--list-curves` work without a build.

```bash
.venv/bin/python -m unittest discover -s worker/jobs/oofem-native -p 'test_*.py'
```

Two of the tests run the real executable and are skipped when it is absent.
