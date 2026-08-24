# Sourced by run_queue.sh (and any other worker script). The ONE place that
# names anything outside this repo.
#
# Everything is absolute: the queue must behave identically no matter which
# directory it was launched from.

WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_ROOT="$(cd "$WORKER_DIR/.." && pwd)"

# The solver lives in a separate repo. Override with THCM_FEM_ROOT if it moves.
FEM_ROOT="${THCM_FEM_ROOT:-$HOME/programme/THCM-Fem}"

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
RENDER="$WORKER_DIR/tools/pdf_render.py"

# Work areas. AutoBench sits OUTSIDE benchmarks/: it holds unreviewed machine
# output, and keeping it out of the corpus tree means nothing that reads
# benchmarks/ (the viewer, a glob, a reviewer) can mistake it for curated work.
AUTOBENCH="$BENCH_ROOT/AutoBench"
PDF_DIR="$AUTOBENCH/pdf"          # inbox: drop papers here
WORK_ROOT="$AUTOBENCH"            # one workdir per PDF: $WORK_ROOT/<slug>/
LOG_DIR="$AUTOBENCH/logs"
PROMPT_FILE="$WORKER_DIR/WORKER.md"

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
#   WORKER_PERMS=skip worker/run_queue.sh
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
