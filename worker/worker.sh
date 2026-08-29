#!/usr/bin/env bash
# The one entry point. Pick a job, pass it its arguments.
#
#   worker/worker.sh                    list the jobs
#   worker/worker.sh <job> --help       what that job takes
#   worker/worker.sh <job> [args ...]   run it
#
# A job is any directory under worker/jobs/ containing an executable job.sh.
# Adding one needs no change here: create the directory, answer --describe with
# a one-liner, and it appears in the list.
#
# Everything shared lives outside jobs/: config.sh names every path, schemas/
# holds benchmark.schema.json, tools/ holds the scripts both jobs use.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"

jobs() { find "$JOBS_DIR" -mindepth 2 -maxdepth 2 -name job.sh -printf '%h\n' 2>/dev/null | xargs -rn1 basename | sort; }

usage() {
  echo "usage: worker.sh <job> [args ...]"
  echo
  echo "jobs:"
  local j desc
  while read -r j; do
    [[ -n "$j" ]] || continue
    desc="$("$JOBS_DIR/$j/job.sh" --describe 2>/dev/null)"
    printf '  %-12s %s\n' "$j" "${desc:-—}"
  done < <(jobs)
  echo
  echo "  worker.sh <job> --help    the job's own arguments"
  echo
  echo "shared:  config.sh (paths) · schemas/ · tools/"
  echo "output:  $WORKSPACE"
}

job="${1:-}"
case "$job" in
  ""|-h|--help|help) usage; exit 0 ;;
  --list)            jobs; exit 0 ;;
esac
shift

script="$JOBS_DIR/$job/job.sh"
if [[ ! -x "$script" ]]; then
  echo "no such job: '$job'" >&2
  echo >&2
  usage >&2
  exit 2
fi

# `--help` on a job prints that job's own header block, so the usage a person
# reads is the one that sits next to the code, never a copy here that drifts.
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,/^set -/p' "$script" | sed '$d' | sed 's/^# \?//'
  exit 0
fi

where="$("$script" --where 2>/dev/null)"
started=$(date +%s)

# The banner is the "where am I" line: which job, on which model route, writing
# where. Everything after it is the job's own output, unfiltered.
echo "── worker: $job ─────────────────────────────────────────────"
echo "   args     ${*:-<none>}"
[[ -n "$where" ]] && echo "   output   $where"
[[ "$job" == "eval" || -n "${WORKER_MODEL:-}" ]] && \
  echo "   model    ${WORKER_MODEL:-<per models.yaml>}  @ ${WORKER_BASE_URL:-<sdk default>}"
echo "   started  $(date +%F' '%T)"
echo "─────────────────────────────────────────────────────────────"

"$script" "$@"
rc=$?

elapsed=$(( $(date +%s) - started ))
printf '─────────────────────────────────────────────────────────────\n'
printf '   %s %s after %dm%02ds' "$job" \
  "$( ((rc==0)) && echo 'finished' || echo "exited $rc" )" $((elapsed/60)) $((elapsed%60))
[[ -n "$where" ]] && printf '   ->  %s' "$where"
printf '\n'
exit $rc
