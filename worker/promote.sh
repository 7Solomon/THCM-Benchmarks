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

state="$(jq -r '.state // "none"' "$src/STATUS.json" 2>/dev/null)"
[[ "$state" == "done" ]] || {
  echo "state is '$state', not 'done' — promote by hand if you mean it" >&2; exit 1; }

cd "$BENCH_ROOT"
# Plain mv, not git mv: the source is untracked (AutoBench/ is gitignored), so
# git mv would refuse. `git add` is what puts the promoted work under version
# control, in one commit, as new files.
mv "$src" "$dst"
git add "benchmarks/$slug"
echo "promoted -> benchmarks/$slug (staged; STATUS.json carries the phase log)"
echo "next: rm $PDF_DIR/$slug.pdf   (the paper now lives with the benchmark)"
echo "      $PY viewer/build_index.py"
