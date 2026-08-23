#!/usr/bin/env bash
# Queue runner: one fresh Claude Code agent per PDF.
#
#   ./run_queue.sh                     process every PDF in the inbox, once
#   ./run_queue.sh --loop [SECONDS]    keep watching the inbox (default 300 s)
#   ./run_queue.sh --pdf URL|PATH      one-off on a single paper, outside the queue
#   ./run_queue.sh --resume [SLUG]     force a parked run even with no data found
#   ./run_queue.sh --dry-run           resolve everything, dispatch nothing
#   ./run_queue.sh --summary           print the table and exit
#   ./run_queue.sh --preflight         check the environment and exit
#
# Runs from anywhere: every path is derived from this script's own location
# (config.sh). The agent's cwd is BENCH_ROOT.
#
# NOT unattended: extraction goes through the AI digitizer in SKILLs/
# EXTRACT-WEB.md, which drives a visible Chrome via the Claude in Chrome
# extension. Start the queue with Chrome open and connected, and leave it be.
#
# Human handoff: when the digitizer cannot read a figure the agent parks the run
# (state needs_human) and leaves CSV templates under
# <case>/reference/_needs_human/. Put your numbers there in whatever shape you
# have them and just run the queue again — it notices and hands them to the
# agent, which does the reshaping.
#
# Resume: a PDF whose STATUS.json holds a terminal state is never re-dispatched;
# a crashed one is retried until MAX_ATTEMPTS, with the workdir left in place so
# the next agent resumes from what is already on disk (WORKER.md Phase 0).
set -uo pipefail
shopt -s extglob

source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

TERMINAL='["done","infeasible","extract_failed","run_failed","stuck"]'
# Parked, not finished: waiting on a human to fill in the extraction handoff
# (tools/handoff.py). Skipped on normal passes, dispatched by --resume.
PARKED='["needs_human"]'
STATUS_PY="$WORKER_DIR/tools/status.py"
HANDOFF_PY="$WORKER_DIR/tools/handoff.py"

# --- status helpers --------------------------------------------------------

state_of()    { jq -r '.state // "unknown"'   "$1" 2>/dev/null || echo unknown; }
phase_of()    { jq -r '.phase_reached // "?"' "$1" 2>/dev/null || echo "?"; }
attempts_of() { jq -r '.attempts // 0'        "$1" 2>/dev/null || echo 0; }
is_terminal() { jq -e --argjson t "$TERMINAL" '.state as $s | $t | index($s)' "$1" >/dev/null 2>&1; }
is_parked()   { jq -e --argjson t "$PARKED"   '.state as $s | $t | index($s)' "$1" >/dev/null 2>&1; }

summary() {
  echo "=== queue summary ==="
  { printf 'STATE\tSLUG\tPHASE\tTRY\tRESULT\n'
    for s in "$WORK_ROOT"/*/STATUS.json "$AUTOBENCH"/dryrun/*/STATUS.json; do
      [[ -f "$s" ]] || continue
      jq -r '[(.state // "?" | ascii_upcase), .slug,
              ((.phase_reached // "?")|tostring), ((.attempts // 0)|tostring),
              (.key_result // .reason // "")] | @tsv' "$s" 2>/dev/null
    done; } | column -t -s $'\t'
  local parked
  parked=$(grep -l '"needs_human"' "$WORK_ROOT"/*/STATUS.json 2>/dev/null | wc -l)
  (( parked )) && {
    echo
    echo "$parked run(s) parked on a human. For each:"
    echo "  $PY $HANDOFF_PY list $WORK_ROOT/<slug>      what is outstanding, and why"
    echo "  $PY $HANDOFF_PY scan $WORK_ROOT/<slug>      what has been dropped in so far"
    echo "  add your data to the figure folder, then just re-run the queue"
  }
  echo "phase log of one benchmark:  $PY $STATUS_PY show $WORK_ROOT/<slug>"
}

preflight() {
  local bad=0
  for f in "$PY" "$THCM" "$PROMPT_FILE" "$RENDER" "$STATUS_PY" "$HANDOFF_PY"; do
    [[ -x "$f" || -f "$f" ]] || { echo "missing: $f"; bad=1; }
  done
  # Symlinked from FEM_ROOT — a dangling link means the solver repo moved.
  for f in "$SKILL_CREATE" "$SKILL_ANALYSIS" "$SCHEMA"; do
    [[ -e "$f" ]] || { echo "missing (dangling symlink? set THCM_FEM_ROOT): $f"; bad=1; }
  done
  [[ -f "$SKILL_WEB" ]] || { echo "missing: $SKILL_WEB"; bad=1; }
  # A Windows-side CLI reached from WSL cannot execute the Linux venv at all:
  # it sees the repo over a UNC path and every {{PY}}/{{THCM}} call is an ELF
  # binary it cannot run. Costs a full agent run to discover.
  local claude_path
  claude_path="$(command -v claude 2>/dev/null || true)"
  if (( ! DRY )) && [[ -n "$claude_path" && ( "$claude_path" == /mnt/* || "$claude_path" == *.exe || "$claude_path" == *.cmd ) ]]; then
    echo "claude on PATH is the Windows CLI ($claude_path) — it cannot run $PY."
    echo "  install the Linux CLI inside WSL, or put it ahead of the Windows one on PATH."
    bad=1
  fi
  local tools=(jq column timeout curl)
  # A dry run dispatches no agent, so it must not require the CLI to be there.
  (( DRY )) || tools+=(claude)
  for c in "${tools[@]}"; do
    command -v "$c" >/dev/null || { echo "not on PATH: $c"; bad=1; }
  done
  "$PY" -c "import pymupdf, PIL" 2>/dev/null || { echo "venv lacks pymupdf/pillow: $PY"; bad=1; }
  # The bindings are named 'oofem', not 'oofempy'. Non-fatal: Phases 1-3
  # (feasibility, authoring, extraction) work without them; Phase 4 does not.
  "$PY" -c "import oofem" 2>/dev/null || \
    echo "warn: 'import oofem' fails in $PY — workers will die at Phase 4 (run)."
  [[ -d "$PDF_DIR" ]] || { echo "no inbox: $PDF_DIR"; bad=1; }
  (( bad )) && return 1
  echo "preflight ok  (FEM_ROOT=$FEM_ROOT)"
}

slug_of() {
  basename "${1%.*}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//'
}

# --- the unit of work: process_pdf <source-pdf> <work-root> ----------------

process_pdf() {
  local pdf="$1" root="$2"
  local slug work status attempt work_pdf log rc why prompt pair

  slug="$(slug_of "$pdf")"
  work="$root/$slug"
  status="$work/STATUS.json"

  # Did a human put extraction data anywhere in this workdir? That question comes
  # FIRST, before any skip rule. Someone who has just done the digitizing by hand
  # must never be answered with "needs a human" — whatever state the run is in,
  # and wherever they put the file, their work changes the inputs and earns a
  # fresh attempt.
  local has_human=0
  [[ -d "$work" ]] && "$PY" "$HANDOFF_PY" scan "$work" > /dev/null 2>&1 && has_human=1

  if (( has_human )) && [[ ! -f "$status" || "$(state_of "$status")" != "done" ]]; then
    "$PY" "$STATUS_PY" set "$work" --set attempts=0 > /dev/null 2>&1
    echo "[resum] $slug — human data found, dispatching"
    "$PY" "$HANDOFF_PY" scan "$work" 2>/dev/null | sed 's/^/         /' | head -8
  elif [[ -f "$status" ]] && is_terminal "$status"; then
    echo "[skip ] $slug — terminal: $(state_of "$status")"
    return 0
  elif [[ -f "$status" ]] && is_parked "$status" && (( ! RESUME )); then
    echo "[park ] $slug — waiting on a human:"
    "$PY" "$HANDOFF_PY" scan "$work" 2>&1 | sed 's/^/         /'
    return 0
  fi

  attempt=$(( $(attempts_of "$status") + 1 ))
  if (( attempt > MAX_ATTEMPTS )); then
    echo "[skip ] $slug — $MAX_ATTEMPTS attempts spent, last stop in phase $(phase_of "$status"); needs a human"
    echo "         nothing from you found under $work — put your data anywhere in the"
    echo "         case directory and re-run, or reset: $PY $STATUS_PY set $work --set attempts=0"
    return 0
  fi

  # The workdir is the whole handover: the PDF lives next to the work, so a
  # finished benchmark is one `git mv` away from the curated corpus.
  mkdir -p "$work" "$LOG_DIR"
  work_pdf="$work/$slug.pdf"
  if [[ ! -f "$work_pdf" ]]; then
    if ! "$PY" "$RENDER" prepare "$pdf" "$work_pdf"; then
      # A password-protected paper is a permanent property of the file, not a
      # failed attempt: record it and never dispatch an agent for it.
      "$PY" "$STATUS_PY" finish "$work" --state infeasible \
        --key-result "PDF is password-protected and cannot be opened" > /dev/null
      echo "[skip ] $slug — unreadable PDF, no agent dispatched"
      return 0
    fi
  fi

  # Bash substitution, not sed: paths may contain & or | and must survive verbatim.
  prompt="$(<"$PROMPT_FILE")"
  for pair in \
    "PDF|$work_pdf" "WORKDIR|$work" "SLUG|$slug" \
    "PY|$PY" "THCM|$THCM" "RENDER|$RENDER" "STATUS_PY|$STATUS_PY" \
    "HANDOFF_PY|$HANDOFF_PY" "SCHEMA|$SCHEMA" \
    "SKILL_CREATE|$SKILL_CREATE" "SKILL_ANALYSIS|$SKILL_ANALYSIS" "SKILL_WEB|$SKILL_WEB" \
    "SOLVER_TIMEOUT|$SOLVER_TIMEOUT" "ATTEMPT|$attempt" "MAX_ATTEMPTS|$MAX_ATTEMPTS"
  do
    prompt="${prompt//\{\{${pair%%|*}\}\}/${pair#*|}}"
  done

  if (( DRY )); then
    printf '%s\n' "$prompt" > "$LOG_DIR/$slug.prompt.txt"
    echo "[dry  ] $slug  attempt $attempt/$MAX_ATTEMPTS"
    echo "         pdf      $work_pdf"
    echo "         workdir  $work"
    echo "         run      timeout $SOLVER_TIMEOUT $THCM oofem $work/<case> --remesh"
    echo "         prompt   $LOG_DIR/$slug.prompt.txt"
    echo "         unresolved placeholders: $(grep -c '{{' "$LOG_DIR/$slug.prompt.txt")"
    echo "         (no agent dispatched)"
    return 0
  fi

  echo "[run  ] $slug  attempt $attempt/$MAX_ATTEMPTS  $(date +%F' '%T)"

  # Fresh context per PDF: each `claude -p` is an isolated session with no
  # conversation history. --max-turns and timeout are the anti-loop backstops.
  # --output-format json so the log carries cost, turns and stop reason, not
  # just the final message.
  log="$LOG_DIR/$slug.$attempt.json"
  timeout -k 30s --foreground "$TIMEOUT" \
    claude -p "$prompt" \
      --chrome \
      --output-format json \
      --max-turns "$MAX_TURNS" \
      "${CLAUDE_PERMS[@]}" \
      --add-dir "$FEM_ROOT" \
      > "$log" 2>"$LOG_DIR/$slug.$attempt.err"
  rc=$?
  # CLAUDE_PERMS comes from config.sh. It must grant Bash outright: this session
  # is non-interactive, so any command needing approval is DENIED, not queued —
  # and every tool in this contract is a shell command.

  # Parked counts as a real outcome, not a missing one: the agent stopped on
  # purpose and said why. Overwriting it with "crashed" destroys the handoff.
  if [[ -f "$status" ]] && { is_terminal "$status" || is_parked "$status"; }; then
    jq --argjson a "$attempt" '.attempts = $a' "$status" > "$status.tmp" && mv "$status.tmp" "$status"
    if is_parked "$status"; then
      echo "[park ] $slug — $(jq -r '.key_result // ""' "$status")"
      echo "         add your data under $work/<case>/reference/_needs_human/,"
      echo "         then just run the queue again"
    else
      echo "[ ok  ] $slug — $(state_of "$status"): $(jq -r '.key_result // ""' "$status")"
    fi
  else
    # Preserve whatever the agent managed to write — its phase log is the only
    # record of where it stopped — and carry that record into the crash entry.
    [[ -f "$status" ]] && cp "$status" "$work/STATUS.attempt$attempt.json"
    case $rc in
      124|137) why="timeout after $TIMEOUT" ;;
      0)       why="agent exited cleanly without a terminal STATUS.json (max-turns?)" ;;
      *)       why="agent exited $rc" ;;
    esac
    jq -n --arg pdf "$work_pdf" --arg slug "$slug" --arg why "$why" \
          --argjson a "$attempt" --argjson rc "$rc" --arg t "$(date -Is)" \
          --argjson prev "$(jq -c '{phase_reached, phase_log, case, fix_iterations}' "$status" 2>/dev/null || echo '{}')" \
          '$prev + {pdf:$pdf, slug:$slug, state:"crashed", reason:$why,
                    attempts:$a, runner_exit:$rc, updated:$t}' > "$status"
    echo "[crash] $slug — $why, stopped in phase $(phase_of "$status"); see $log"
  fi

  jq -r '.result // empty' "$log" 2>/dev/null | grep '^STATUS:' | tail -1
  jq -r 'select(.total_cost_usd) | "        cost $\(.total_cost_usd*1000|round/1000)  turns \(.num_turns)"' \
     "$log" 2>/dev/null
}

pass_over_inbox() {
  local n=0 pdf
  shopt -s nullglob
  for pdf in "$PDF_DIR"/*.pdf; do
    [[ -n "$ONLY_SLUG" && "$(slug_of "$pdf")" != "$ONLY_SLUG" ]] && continue
    process_pdf "$pdf" "$WORK_ROOT"
    n=$((n + 1))
  done
  if (( ! n )); then
    [[ -n "$ONLY_SLUG" ]] && echo "[idle ] no PDF in $PDF_DIR matches slug '$ONLY_SLUG'" \
                          || echo "[idle ] no PDFs in $PDF_DIR"
  fi
}

# Fetch a one-off paper by URL or path into the scratch area.
fetch_pdf() {
  local src="$1" dst_dir="$AUTOBENCH/dryrun" dst
  mkdir -p "$dst_dir"
  if [[ "$src" == http://* || "$src" == https://* ]]; then
    dst="$dst_dir/$(slug_of "$(basename "${src%%\?*}")").pdf"
    echo "fetching $src" >&2
    curl -fsSL --max-time 120 -o "$dst" "$src" || { echo "download failed: $src" >&2; return 1; }
  else
    [[ -f "$src" ]] || { echo "no such file: $src" >&2; return 1; }
    dst="$dst_dir/$(slug_of "$src").pdf"
    cp "$src" "$dst"
  fi
  # A 404 HTML page saved as .pdf would waste a whole agent run.
  [[ "$(head -c 5 "$dst")" == "%PDF-" ]] || {
    echo "not a PDF (no %PDF- header): $dst" >&2; return 1; }
  echo "$dst"
}

# --- argument parsing ------------------------------------------------------

DRY=0; RESUME=0; LOOP_INTERVAL=""; ONE_PDF=""; ONLY_SLUG=""
while (( $# )); do
  case "$1" in
    --summary)   summary; exit 0 ;;
    --preflight) preflight; exit $? ;;
    --dry-run)   DRY=1; shift ;;
    # --resume [slug]: pick up runs parked on a human. Without a slug, every
    # parked run whose handoff is filled in.
    --resume)    RESUME=1
                 if [[ -n "${2:-}" && "$2" != -* ]]; then ONLY_SLUG="$2"; shift 2
                 else shift; fi ;;
    # Resolved here, before the cd to BENCH_ROOT, so a relative path means what
    # it meant in the shell you typed it in.
    --pdf)       ONE_PDF="${2:?--pdf needs a URL or a path}"
                 [[ "$ONE_PDF" == http?(s)://* ]] || ONE_PDF="$(realpath -m "$ONE_PDF")"
                 shift 2 ;;
    --loop)      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then LOOP_INTERVAL="$2"; shift 2
                 else LOOP_INTERVAL=300; shift; fi ;;
    -h|--help)   sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *)           echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$ONE_PDF" && -n "$LOOP_INTERVAL" ]] && {
  echo "--pdf and --loop are mutually exclusive" >&2; exit 2; }

preflight || { echo "preflight failed — fix the above before dispatching agents" >&2; exit 1; }

# One runner at a time: two would fight over the same workdirs.
mkdir -p "$LOG_DIR"
exec 9>"$LOG_DIR/.lock"
flock -n 9 || { echo "another run_queue.sh is running (lock: $LOG_DIR/.lock)" >&2; exit 1; }

cd "$BENCH_ROOT"

# One-off: a single paper by URL or path, kept out of the queue's work root so
# it can never be promoted by accident.
if [[ -n "$ONE_PDF" ]]; then
  pdf="$(fetch_pdf "$ONE_PDF")" || exit 1
  echo "[one  ] scratch run in $AUTOBENCH/dryrun/ — not queued, not promotable"
  process_pdf "$pdf" "$AUTOBENCH/dryrun"
  exit 0
fi

# Loop: keep watching the inbox. New PDFs are picked up on the next pass;
# finished ones are skipped, and MAX_ATTEMPTS stops a broken one from cycling.
if [[ -n "$LOOP_INTERVAL" ]]; then
  echo "[loop ] watching $PDF_DIR every ${LOOP_INTERVAL}s — Ctrl-C to stop"
  trap 'echo; echo "[loop ] stopped"; summary; exit 0' INT TERM
  while :; do
    pass_over_inbox
    echo "[loop ] pass done $(date +%F' '%T) — sleeping ${LOOP_INTERVAL}s"
    sleep "$LOOP_INTERVAL"
  done
fi

pass_over_inbox
echo
summary
