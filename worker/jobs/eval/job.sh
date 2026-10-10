#!/usr/bin/env bash
# JOB: eval -- which model can author a correct benchmark.json from a paper?
#
# One identical single-shot call per (model, case): the authoring guide, the
# physics modules, the schema, the case scope and the paper text in, one JSON
# object out. Scored against dataset/ by worker/tools/compare_benchmark.py;
# THCM-FEM execution and diagnostic repair turns are opt-in.
#
#   worker.sh eval --probe                  which free API chat models answer
#   worker.sh eval                          free models, every case with a paper
#   worker.sh eval --models M [M ...]       pick models  (--list-models to see them)
#   worker.sh eval --cases C [C ...]        pick cases
#   worker.sh eval --limit N                stop after N cases
#   worker.sh eval --allow-metered          permit models that cost money
#   worker.sh eval --rescore RUN_ID         re-score a finished run, no API calls
#   worker.sh eval --run-solver              run each candidate through THCM-FEM
#   worker.sh eval --solver-feedback-turns 2 let the model repair solver failures
#   worker.sh eval --execute-run RUN_ID      solve candidates from an old run
#   worker.sh eval --solver-baseline         also solve curated JSON as a preflight
#   worker.sh eval --report [RUN_ID]        leaderboard for a run (default: latest)
#   worker.sh eval --explain [RUN_ID]       ask an analyst model WHY each spec is wrong
#                                           (metered: needs --allow-metered)
#   worker.sh eval --dataset                rebuild dataset/ from benchmarks/
#
# A run with no flags touches only unmetered KIT-hosted models and costs nothing.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../config.sh"

case "${1:-}" in
  --describe) echo "score benchmark.json authoring, optionally execute it with THCM-FEM"; exit 0 ;;
  --where)    echo "$WORKSPACE/runs"; exit 0 ;;
  --dataset)  shift; exec "$PY" "$WORKER_DIR/tools/build_dataset.py" "$@" ;;
  --report)   shift; exec "$PY" "$HERE/report.py" "${@:---latest}" ;;
  --explain)  shift; exec "$PY" "$HERE/explain.py" "$@" ;;
  --list-models|--probe) exec "$PY" "$HERE/run_eval.py" "$@" ;;
esac

# The dataset is the eval's input; building it is seconds and skipping the check
# would fail much later with a confusing message about a missing case.
if [[ ! -f "$BENCH_ROOT/dataset/manifest.json" ]]; then
  echo "no dataset yet — building it first"
  "$PY" "$WORKER_DIR/tools/build_dataset.py" || exit 1
  echo
fi

exec "$PY" "$HERE/run_eval.py" "$@"
