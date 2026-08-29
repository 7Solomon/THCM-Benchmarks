#!/usr/bin/env bash
# Dispatcher: Claude Code (`claude -p`).
#
# One of two interchangeable backends. The CONTRACT -- the 7 phases, the skills,
# the tools, the workdir layout, the three stops -- lives in WORKER.md and is
# harness-independent. A dispatcher only answers "how does one agent turn happen?"
#
#   dispatch/claude.sh <prompt-file> <workdir> <log-json> <log-err>
#
# Exit code is what run_queue.sh classifies: 124/137 timeout, 0 clean, other crash.
# stdout must carry the agent's final message so the runner can grep `STATUS:`.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

prompt_file="${1:?prompt file}"; work="${2:?workdir}"
log="${3:?log json}"; err="${4:?log stderr}"

timeout -k 30s --foreground "$TIMEOUT" \
  claude -p "$(<"$prompt_file")" \
    --chrome \
    --output-format json \
    --max-turns "$MAX_TURNS" \
    "${CLAUDE_PERMS[@]}" \
    --add-dir "$FEM_ROOT" \
    > "$log" 2>"$err"
rc=$?

# The runner reads cost/turns out of the JSON envelope and the final message for
# the STATUS: line. Surface the latter on stdout so both dispatchers look alike.
jq -r '.result // empty' "$log" 2>/dev/null || true
exit $rc
