# Sourced by worker.sh and every job. The ONE place that
# names anything outside this repo.
#
# Everything is absolute: the queue must behave identically no matter which
# directory it was launched from.

WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_ROOT="$(cd "$WORKER_DIR/.." && pwd)"

# One directory per job. worker.sh dispatches into these.
#
# The rule for what lives WHERE: jobs/<name>/ holds only what is ABOUT that job --
# its entry point, its contract, its own scripts. Anything a second job could
# reasonably want sits above it, at worker level: the skills (domain knowledge),
# dispatch/ (how one agent turn happens), schemas/, tools/, models.yaml and the
# credential. Two jobs reaching into a third directory is fine; one job reaching
# into another job's directory means the thing is in the wrong place.
JOBS_DIR="$WORKER_DIR/jobs"
AUTHORING_DIR="$JOBS_DIR/authoring"
EVAL_DIR="$JOBS_DIR/eval"

# The solver lives in a separate repo. Override with THCM_FEM_ROOT if it moves.
# This checkout is installed editable into BENCH_ROOT/.venv, so edits to the
# solver are picked up without reinstalling it.
FEM_ROOT="${THCM_FEM_ROOT:-$HOME/Documents/johannes/THCM-FEM}"

# OOFEM itself, for the `oofem` job. That job drives the bindings directly --
# no THCM-Fem in between -- so this is a separate knob from FEM_ROOT above.
#
# These three must agree with each other, and the reason is that oofempy is a
# COMPILED extension: it is built against one Python minor version, and any
# other interpreter refuses it outright ("module was compiled for Python 3.10").
#
#   OOFEMPY_DIR   holds oofempy.so
#   OOFEM_PY      the interpreter that .so was compiled against
#   OOFEM_ROOT    the checkout that .so was BUILT FROM. The job feeds
#                 bindings/python/{tests/test_5.py,util.py} to the model as API
#                 reference, so pointing this at a different checkout would
#                 document an API the binary does not have.
#
# BENCH_ROOT/.venv is deliberately built on Python 3.10 to match that ABI, so
# OOFEM_PY and $PY below are the SAME interpreter: the worker, the dataset
# builder and the model-written simulation scripts all run on one environment.
# If the bindings are ever rebuilt against another version, rebuild .venv to
# match or split these two apart again.
#
# The eval job's THCM-FEM backend imports the `oofem` wheel installed in .venv;
# these legacy direct-binding paths remain the knobs for the separate oofem job.
OOFEM_ROOT="${OOFEM_ROOT:-$HOME/Documents/Gereon/oofem/oofem-gw}"
OOFEMPY_DIR="${OOFEMPY_DIR:-$HOME/Documents/Gereon/oofem/build/debug}"
OOFEM_PY="${OOFEM_PY:-$BENCH_ROOT/.venv/bin/python}"
# The Python interpreter behind .venv comes from the older Gereon conda env.
# The wheel built by the system compiler needs the newer system libstdc++.
# Keep this opt-in variable separate from LD_PRELOAD: only solver children get
# it, not every worker/API process. Empty it after rebuilding with one toolchain.
OOFEM_LD_PRELOAD="${OOFEM_LD_PRELOAD:-/usr/lib/x86_64-linux-gnu/libstdc++.so.6}"
export OOFEM_ROOT OOFEMPY_DIR OOFEM_PY OOFEM_LD_PRELOAD

# The `oofem-native` job skips the bindings and runs the solver executable on a
# text input deck. The same build tree produces both, so the binary sits next to
# oofempy.so and shares its OOFEM_ROOT (the source the prompts quote) and its
# OOFEM_LD_PRELOAD (the same libstdc++ mismatch applies to liboofem.so).
OOFEM_BIN="${OOFEM_BIN:-$OOFEMPY_DIR/oofem}"
export OOFEM_BIN

# Interpreters / entry points (BENCH_ROOT/.venv is an editable install of FEM_ROOT)
PY="$BENCH_ROOT/.venv/bin/python"
THCM="$BENCH_ROOT/.venv/bin/thcm"

# Skills. CREATE.md and the schema are symlinks into FEM_ROOT (they describe the
# solver). DEBUGGING-PLAYBOOK and EXTRACT-WEB are worker-owned: they are method,
# not solver surface. (The old ANALYSIS.md was retired 2026-08-23.)
SKILL_CREATE="$WORKER_DIR/SKILLs/CREATE.md"
SKILL_ANALYSIS="$WORKER_DIR/SKILLs/DEBUGGING-PLAYBOOK.md"
SKILL_EXTRACT="$WORKER_DIR/SKILLs/EXTRACT.md"        # the ROUTER: pick a route
SKILL_WEB="$WORKER_DIR/SKILLs/EXTRACT-WEB.md"        # browser sub-route, optional
SCHEMA="$WORKER_DIR/schemas/benchmark.schema.json"
# CREATE.md is the CORE, loaded every run. The physics detail lives in modules/,
# loaded only when the START-HERE table routes to it, and the mechanical authoring
# rules live in tools/check_benchmark.py rather than in prose.
SKILL_MODULES="$WORKER_DIR/SKILLs/modules"
CHECK_PY="$WORKER_DIR/tools/check_benchmark.py"
DIGITIZE="$WORKER_DIR/tools/plot_digitize.py"

# Which agent harness runs a turn. The CONTRACT (WORKER.md, skills, tools, the
# workdir layout, the three stops) is harness-independent; a dispatcher only
# answers "how does one turn happen?". Override per run:
#   WORKER_HARNESS=dsh worker/worker.sh authoring   or  ... authoring --harness dsh
WORKER_HARNESS="${WORKER_HARNESS:-claude}"
DISPATCH_DIR="$WORKER_DIR/dispatch"

# Model routing for the dsh harness. Empty means "the SDK default", which is the
# stock DeepSeek route -- so the queue behaves exactly as before unless you ask
# for something else. The dsh adapter speaks the OpenAI dialect, so any
# OpenAI-compatible endpoint works without a new adapter:
#
#   worker/worker.sh authoring --harness dsh --model azure.gpt-5-mini
#
# The key is read from worker/.env (KI_TOOLBOX_API_KEY or
# DEEPSEEK_API_KEY) by the dispatcher; it is never passed on the command line,
# where it would land in the process table and in this script's own logs.
# The shared model registry: which models exist and which cost money. Both jobs
# read it, so the metered gate protects both.
MODELS_YAML="$WORKER_DIR/models.yaml"
ENV_FILE="$WORKER_DIR/.env"

WORKER_MODEL="${WORKER_MODEL:-}"
WORKER_BASE_URL="${WORKER_BASE_URL:-https://ki-toolbox.scc.kit.edu/api/v1}"
export WORKER_MODEL WORKER_BASE_URL
RENDER="$WORKER_DIR/tools/pdf_render.py"

# Work areas. AutoBench sits under workspace/, OUTSIDE benchmarks/: it holds
# unreviewed machine output, and keeping it out of the corpus tree means nothing
# that reads benchmarks/ (the viewer, a glob, a reviewer) can mistake it for
# curated work. workspace/ is the machine's side of the repo -- the queue's
# workdirs and the authoring eval both live there.
WORKSPACE="${WORKSPACE:-$BENCH_ROOT/workspace}"
AUTOBENCH="$WORKSPACE/AutoBench"
PDF_DIR="$AUTOBENCH/pdf"          # inbox: drop papers here
WORK_ROOT="$AUTOBENCH"            # one workdir per PDF: $WORK_ROOT/<slug>/
LOG_DIR="$AUTOBENCH/logs"
PROMPT_FILE="$AUTHORING_DIR/WORKER.md"

# Permissions. `claude -p` is non-interactive: a tool call that needs approval is
# DENIED outright, never queued. Every step of this contract is a shell command,
# so dropping the Bash allow-rule silently costs a whole run — which is exactly
# what the first real dispatch did (14 denials, $1.80, zero progress).
#
# Default: an explicit allowlist. "Bash" with no specifier is an allow-rule for
# all Bash calls, which is what the queue needs and nothing more. The Chrome
# extension's tools (mcp__claude-in-chrome__*) are what EXTRACT-WEB.md drives —
# the second dispatch died on exactly these, denied at Phase 3.
CLAUDE_PERMS=(--permission-mode acceptEdits
              --allowedTools "Read,Write,Edit,Glob,Grep,Bash,mcp__claude-in-chrome")

# NOTE: allowlisting the tool is not the same as the extension trusting the
# session. Claude in Chrome asks for its own approval on first use, and that
# prompt cannot be answered in a non-interactive run. Approve it ONCE in an
# interactive `claude --chrome` session before dispatching the queue.
#
# If the browser tools are still denied, opt into the permissive mode for a
# disposable/trusted run:
#
#   WORKER_PERMS=skip worker/worker.sh authoring
#
# That grants the agent unrestricted tool use on this machine for the whole run.
# It is off by default deliberately: it is your decision, not the script's.
if [[ "${WORKER_PERMS:-}" == "skip" ]]; then
  CLAUDE_PERMS=(--dangerously-skip-permissions)
fi

# Budgets
MAX_TURNS=400                     # per attempt
MAX_ATTEMPTS=2                    # per PDF, across queue re-runs
TIMEOUT="6h"                      # wall clock per attempt
SOLVER_TIMEOUT="3h"               # wall clock for one thcm run (inside the attempt)
