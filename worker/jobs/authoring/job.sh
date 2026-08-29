#!/usr/bin/env bash
# JOB: authoring -- one paper in, one validated benchmark out.
#
# Dispatches a fresh agent per PDF in the inbox. The agent reads the paper,
# authors benchmark.json, digitizes the reference curves, runs the solver, and
# analyses the result. Long-running and NOT unattended: figure extraction may
# park a run and wait for a person.
#
#   worker.sh authoring                     process every PDF in the inbox, once
#   worker.sh authoring --loop [SECONDS]    keep watching the inbox
#   worker.sh authoring --pdf URL|PATH      one-off on a single paper
#   worker.sh authoring --summary           the status table, then exit
#   worker.sh authoring --preflight         check the environment, then exit
#   worker.sh authoring --dry-run           resolve everything, dispatch nothing
#   worker.sh authoring --resume [SLUG]     pick up a run parked on a human
#   worker.sh authoring --harness dsh       run turns on DeepSeek Harness
#   worker.sh authoring --model NAME        route dsh at one model
#   worker.sh authoring --allow-metered     permit a model that costs money
#   worker.sh authoring --list-models       what the endpoint serves
#
# Everything is forwarded verbatim to run_queue.sh, which owns the queue.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../config.sh"

case "${1:-}" in
  --describe) echo "paper -> benchmark.json -> solver run -> analysis (agent queue)"; exit 0 ;;
  --where)    echo "$AUTOBENCH"; exit 0 ;;
esac

exec "$HERE/run_queue.sh" "$@"
