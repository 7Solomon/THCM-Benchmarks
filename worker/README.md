# worker

Two jobs, one entry point.

```bash
worker/worker.sh                      # list the jobs
worker/worker.sh <job> --help         # what that job takes
worker/worker.sh <job> [args ...]     # run it
```

| job | what it does | writes to |
|---|---|---|
| `authoring` | paper → `benchmark.json` → solver run → analysis, one fresh agent per PDF | `workspace/AutoBench/` |
| `eval` | scores how well models author a `benchmark.json` from a paper — no solver | `workspace/runs/` |

They answer different questions. `authoring` is trying to produce a *validated
benchmark*, and the solver run is the point. `eval` is trying to find out *which
model can read a paper*, so it stops at the spec and scores it against
`dataset/`.

## Commands

Everything goes through `worker.sh`. The shortest useful form of each is on the
left; the flags below it are optional.

### The two you will type most

```bash
worker/worker.sh                     # which jobs exist
worker/worker.sh <job> --help        # that job's own flags
```

### eval — which model can author a spec

```bash
worker/worker.sh eval --probe        # which models answer, and your budget
worker/worker.sh eval                # the whole free sweep
worker/worker.sh eval --report       # leaderboard for the latest run
```

`eval` with no flags runs every default model in `models.yaml` over every case
that has a paper, and costs nothing. Narrow it with:

| flag | |
|---|---|
| `--models ID [ID ...]` | pick models (`--probe` lists them) |
| `--cases NAME [NAME ...]` | pick cases (names are the `benchmarks/` paths) |
| `--limit N` | stop after N cases |
| `--tol 0.05` | numeric tolerance, default 1 % |
| `--allow-metered` | permit models that cost money |
| `--allow-no-pdf` | include the 2 cases with no paper |
| `--max-paper-chars N` | truncate very long papers (default 400 000) |
| `--rescore RUN_ID` | re-score a finished run, no API calls |
| `--report [RUN_ID]` | leaderboard (default: latest run) |
| `--dataset` | rebuild `dataset/` from `benchmarks/` |

### authoring — paper to validated benchmark

```bash
worker/worker.sh authoring --summary     # what every run is doing
worker/worker.sh authoring               # process the inbox once
```

Drop PDFs in `workspace/AutoBench/pdf/` first. Long-running, and it can park a
run waiting for you to digitize a figure by hand.

| flag | |
|---|---|
| `--loop [SECONDS]` | keep watching the inbox (default 300) |
| `--pdf URL\|PATH` | one-off on a single paper, outside the queue |
| `--resume [SLUG]` | pick up a run parked on a human |
| `--summary` | the status table, then exit |
| `--preflight` | check the environment, then exit |
| `--dry-run` | resolve everything, dispatch nothing |
| `--harness claude\|dsh` | which agent runtime (default `claude`) |
| `--model ID` | route dsh at one model |
| `--allow-metered` | permit a model that costs money |
| `--list-models` | what the endpoint serves |

### Tools you can run on their own

```bash
worker/tools/compare_benchmark.py CAND.json --case <case>   # what got misread
worker/tools/check_benchmark.py SPEC.json --schema worker/schemas/benchmark.schema.json
worker/tools/build_dataset.py                               # benchmarks/ -> dataset/
worker/tools/pdf_render.py {prepare|pages|text|render|crop} # the PDF side
worker/tools/plot_digitize.py {axes|calibrate|series|verify}
worker/jobs/authoring/promote.sh <slug>                     # AutoBench -> benchmarks/
worker/dispatch/dsh-harness/preflight.py --route mock       # is dsh composed right
```

`compare_benchmark.py` also takes `--tol`, `--limit N` (findings shown per
section), `--json`, and `--strict-files`.

## Layout

```
worker.sh          the dispatcher
config.sh          every path in one place; sourced by worker.sh and both jobs
.env               the API key — one file, because both jobs use one endpoint
models.yaml        which models exist, and which cost money
schemas/           benchmark.schema.json — the structural source of truth
SKILLs/            CREATE.md, modules/, the extraction and debugging playbooks
dispatch/          how one agent turn happens: claude.sh, dsh.py, dsh-harness/
tools/             shared scripts, usable on their own:
  check_benchmark.py    the mechanical authoring rules
  compare_benchmark.py  candidate spec vs reference — what got misread
  build_dataset.py      benchmarks/ -> dataset/
  pdf_render.py         page render, panel crop, text extraction
  plot_digitize.py      figure -> CSV
  status.py handoff.py  run state and the human-handoff files
jobs/
  authoring/       job.sh, WORKER.md (the agent contract), run_queue.sh, promote.sh
  eval/            job.sh, run_eval.py, report.py, prompts/
```

### Where does a thing go?

`jobs/<name>/` holds only what is **about** that job — its entry point, its
contract, its own scripts. Anything a second job could reasonably want sits
above it.

The test is one question: *does another job reach across for this?* If it does,
the thing is in the wrong place. `SKILLs/` is at worker level because the
authoring agent reads it and the eval prompt embeds it. `dispatch/` is there
because "how does one agent turn happen" is a capability, not a job. `.env` and
`models.yaml` are there because both jobs talk to the same endpoint — and
because a shared model registry means the metered-model gate protects **both**
jobs, not just the one that happened to be written with it.

## Adding a job

Create `jobs/<name>/job.sh`, make it executable, and have it answer two flags:

```bash
--describe   one line, shown in the job list
--where      the directory it writes to, shown in the banner
```

`worker.sh` discovers it with no change here. `--help` prints the job's own
header comment, so the usage a person reads always sits beside the code.
