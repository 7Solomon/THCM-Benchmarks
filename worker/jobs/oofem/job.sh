#!/usr/bin/env bash
# JOB: oofem -- can a model write an OOFEM simulation that reproduces a paper?
#
# The `eval` job asks whether a model can author a benchmark.json, and scores
# that spec against a reference spec. This job removes the spec: the model
# writes Python against the oofempy bindings, meshes the geometry itself, runs
# the solver, and is scored on the CURVE that comes out, against the paper's
# digitized reference data. It also has to plot its result over that reference.
#
#   worker.sh oofem --list-curves            what each case must produce
#   worker.sh oofem --dry-run --cases C      build the prompt, call nothing
#   worker.sh oofem                          free models, every scorable case
#   worker.sh oofem --models M [M ...]       pick models
#   worker.sh oofem --cases C [C ...]        pick cases
#   worker.sh oofem --limit N                stop after N cases
#   worker.sh oofem --run-timeout S          wall-clock cap per simulation
#   worker.sh oofem --max-turns N            normal agent exploration budget
#   worker.sh oofem --finalization-turns N   extra validate/repair/submit turns
#   worker.sh oofem --api-retries N          retry transient turn API failures
#   worker.sh oofem --allow-metered          permit models that cost money
#   worker.sh oofem --rescore RUN_ID         re-score a finished run, no calls
#
# Needs oofempy. OOFEM_ROOT / OOFEMPY_DIR / OOFEM_PY in config.sh say where it
# is and which interpreter can import it; --dry-run works without it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../config.sh"

case "${1:-}" in
  --describe) echo "score models on writing an oofempy simulation that reproduces a paper"; exit 0 ;;
  --where)    echo "$WORKSPACE/runs"; exit 0 ;;
esac

# The dataset carries the paper text and the scope.md each prompt is built from.
if [[ ! -f "$BENCH_ROOT/dataset/manifest.json" ]]; then
  echo "no dataset yet — building it first"
  "$PY" "$WORKER_DIR/tools/build_dataset.py" || exit 1
  echo
fi

exec "$PY" "$HERE/run_oofem.py" "$@"
