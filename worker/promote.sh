#!/usr/bin/env bash
# Move a finished AutoBench benchmark into the curated corpus.
#
#   ./promote.sh <slug>
#
# The queue's output is unreviewed machine work: it lives in AutoBench/, outside
# the corpus tree and untracked by git. Promotion is the human gate — read
# REPORT.md first, then run this. It is the moment the work enters both the
# corpus and version control.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

slug="${1:?usage: promote.sh <slug>}"
src="$WORK_ROOT/$slug"
dst="$BENCH_ROOT/benchmarks/$slug"

[[ -d "$src" ]] || { echo "no such workdir: $src" >&2; exit 1; }
[[ -e "$dst" ]] && { echo "already in the corpus: $dst" >&2; exit 1; }

status="$src/.run/status.json"
[[ -f "$status" ]] || status="$src/STATUS.json"          # workdir from before the move
state="$(jq -r '.state // "none"' "$status" 2>/dev/null)"
verdict="$(jq -r '.verdict // ""' "$status" 2>/dev/null)"

# `done` is not enough on its own any more: it now also covers "investigated and
# it does not reproduce" and "this paper cannot be benchmarked". Those are real
# answers worth keeping, but they are not what you silently add to the corpus.
[[ "$state" == "done" ]] || {
  echo "state is '$state', not 'done' — read $src/RESULT.md first" >&2; exit 1; }
if [[ "$verdict" != "reproduced" && "${FORCE:-0}" != "1" ]]; then
  echo "verdict is '${verdict:-unset}', not 'reproduced'." >&2
  echo "  $src/RESULT.md says why. Promote anyway with:  FORCE=1 $0 $slug" >&2
  exit 1
fi

cd "$BENCH_ROOT"
# Plain mv, not git mv: the source is untracked (AutoBench/ is gitignored), so
# git mv would refuse. `git add` is what puts the promoted work under version
# control, in one commit, as new files.
mv "$src" "$dst"
# .run/ is run scaffolding -- session log, figure crops, the phase log. Useful
# while the run is being reviewed, noise in the corpus. RESULT.md is what stays.
rm -rf "$dst/.run"
git add "benchmarks/$slug"
echo "promoted -> benchmarks/$slug (staged; .run/ dropped, RESULT.md kept)"
echo "next: rm $PDF_DIR/$slug.pdf   (the paper now lives with the benchmark)"
echo "      $PY viewer/build_index.py"
