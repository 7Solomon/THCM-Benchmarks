#!/usr/bin/env bash
# JOB: oofem-native -- can a model write an OOFEM INPUT DECK that reproduces a paper?
#
# The `oofem` job asks the model for a script against the oofempy bindings. This
# job takes the bindings away: the model writes the native text deck (model.in,
# or make_deck.py that generates it) and a postprocess.py that turns OOFEM's
# exported files into curves. The harness runs `oofem -f model.in` in between,
# and the curves are scored against the paper's digitized reference data -- same
# cases, same curve contract, same scorer as `oofem`, so the two compare.
#
#   worker.sh oofem-native --list-curves            what each case must produce
#   worker.sh oofem-native --dry-run --cases C      build the prompt, call nothing
#   worker.sh oofem-native                          free models, every scorable case
#   worker.sh oofem-native --agent                  agentic arm: explore, spike, converge
#   worker.sh oofem-native --models M [M ...]       pick models
#   worker.sh oofem-native --cases C [C ...]        pick cases
#   worker.sh oofem-native --limit N                stop after N cases
#   worker.sh oofem-native --run-timeout S          wall-clock cap per pipeline run
#   worker.sh oofem-native --max-turns N            normal agent exploration budget
#   worker.sh oofem-native --finalization-turns N   extra validate/repair/submit turns
#   worker.sh oofem-native --spike-timeout S        cap per run_oofem / run_python spike
#   worker.sh oofem-native --api-retries N          retry transient turn API failures
#   worker.sh oofem-native --allow-metered          permit models that cost money
#   worker.sh oofem-native --rescore RUN_ID         re-score a finished run, no calls
#
# Needs the oofem executable. OOFEM_BIN / OOFEM_ROOT / OOFEM_LD_PRELOAD in
# config.sh say where it is and what it was built from; --dry-run works without it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../config.sh"

case "${1:-}" in
  --describe) echo "score models on writing a native OOFEM input deck that reproduces a paper"; exit 0 ;;
  --where)    echo "$WORKSPACE/runs"; exit 0 ;;
esac

# The dataset carries the paper text and the scope.md each prompt is built from.
if [[ ! -f "$BENCH_ROOT/dataset/manifest.json" ]]; then
  echo "no dataset yet — building it first"
  "$PY" "$WORKER_DIR/tools/build_dataset.py" || exit 1
  echo
fi

exec "$PY" "$HERE/run_native.py" "$@"
