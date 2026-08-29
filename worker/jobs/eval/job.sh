#!/usr/bin/env bash
# JOB: eval -- which model can author a correct benchmark.json from a paper?
#
# One identical single-shot call per (model, case): the authoring guide, the
# physics modules, the schema and the paper text in, one JSON object out. No
# tools, no solver. Scored against dataset/ by worker/tools/compare_benchmark.py.
#
#   worker.sh eval --probe                  which models answer, and your budget
#   worker.sh eval                          free models, every case with a paper
#   worker.sh eval --models M [M ...]       pick models  (--list-models to see them)
#   worker.sh eval --cases C [C ...]        pick cases
#   worker.sh eval --limit N                stop after N cases
#   worker.sh eval --allow-metered          permit models that cost money
#   worker.sh eval --rescore RUN_ID         re-score a finished run, no API calls
#   worker.sh eval --report [RUN_ID]        leaderboard for a run (default: latest)
#   worker.sh eval --dataset                rebuild dataset/ from benchmarks/
#
# A run with no flags touches only unmetered KIT-hosted models and costs nothing.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../config.sh"

case "${1:-}" in
  --describe) echo "score how well models author benchmark.json from a paper (no solver)"; exit 0 ;;
  --where)    echo "$WORKSPACE/runs"; exit 0 ;;
  --dataset)  shift; exec "$PY" "$WORKER_DIR/tools/build_dataset.py" "$@" ;;
  --report)   shift; exec "$PY" "$HERE/report.py" "${@:---latest}" ;;
esac

# The dataset is the eval's input; building it is seconds and skipping the check
# would fail much later with a confusing message about a missing case.
if [[ ! -f "$BENCH_ROOT/dataset/manifest.json" ]]; then
  echo "no dataset yet — building it first"
  "$PY" "$WORKER_DIR/tools/build_dataset.py" || exit 1
  echo
fi

exec "$PY" "$HERE/run_eval.py" "$@"
