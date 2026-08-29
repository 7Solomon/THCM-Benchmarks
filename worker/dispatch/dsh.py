#!/usr/bin/env python3
"""
Dispatcher: DeepSeek Harness (dsh).

The other half of the pair with dispatch/claude.sh. The CONTRACT -- the 7 phases,
the skills, the tools, the workdir layout, the three stops -- lives in WORKER.md
and is harness-independent. A dispatcher only answers "how does one agent turn
happen?", and must present the same interface to run_queue.sh:

    dispatch/dsh.py <prompt-file> <workdir> <log-json> <log-err>

  * stdout carries the agent's final message, so the runner can grep `STATUS:`
  * exit code is classified by the runner: 124 timeout, 0 clean, other crash
  * <log-json> gets an envelope with the same shape claude -p produces
    (`result`, `num_turns`, `total_cost_usd`), so the runner's reporting is
    identical either way

WHY ONE run() AND NOT SEVEN: dsh supports per-phase runs on a shared Session,
which is the better shape -- scoped tools per phase, smaller context, a real
resume point. But WORKER.md is currently ONE prompt because `claude -p` takes one
string, and two dispatchers reading the same contract differently is how they
drift. So this starts at parity: one Session, one run, same prompt. Phase slicing
is a later change to BOTH sides at once.

Every notification is appended to <workdir>/.run/session.jsonl. That is the dsh
session event stream -- the thing that eventually replaces status.py as the
source of truth ("model-visible means logged").

MODEL ROUTING. The SDK takes provider/model/base_url/api_key directly, and the
stock DeepSeek adapter speaks the OpenAI dialect, so any OpenAI-compatible
endpoint is reachable without writing an adapter:

    WORKER_BASE_URL=https://ki-toolbox.scc.kit.edu/api/v1 \
    WORKER_MODEL=kit.gpt-oss-120b worker/worker.sh authoring --harness dsh

Whether a given model drives the TOOL loop well enough to finish the contract is
a separate question from whether it answers -- read the envelope's finish_reason.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/dispatch
WORKER = HERE.parent                            # worker
HARNESS = HERE / "dsh-harness"                  # the cordis composition
# One credential file for the whole worker: both jobs talk to the same endpoint,
# and a key that lives inside one job's directory is a key the other job has to
# reach across for.
ENV_FILE = WORKER / ".env"

TIMEOUT_EXIT = 124                              # match `timeout(1)`, which the runner reads

# `finish_reason` is the kind of the LAST turn/end: completed | max-tokens | error,
# or None when no turn ended at all. Only "completed" is a clean run; the rest are
# distinct failures and the runner must be able to tell them apart. None is the
# worst of them -- the agent produced no turn, so there is nothing to resume from.
FINISH_EXIT = {"completed": 0, "max-tokens": 3, "error": 1, None: 4}

# The key names we accept, in order. KI_TOOLBOX_API_KEY is what worker/.env
# carries; DEEPSEEK_API_KEY is the adapter's own name.
API_KEY_VARS = ("WORKER_API_KEY", "KI_TOOLBOX_API_KEY", "DEEPSEEK_API_KEY")


def load_env(path: Path) -> None:
    """Existing environment wins, so an exported key beats a stale file."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def routing() -> dict:
    """What to hand the SDK. Every field is optional -- omitting one leaves the
    SDK default, which is the stock DeepSeek route."""
    opts = {}
    if os.environ.get("WORKER_PROVIDER"):
        opts["provider"] = os.environ["WORKER_PROVIDER"]
    if os.environ.get("WORKER_MODEL"):
        opts["model"] = os.environ["WORKER_MODEL"]
    base = os.environ.get("WORKER_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL")
    if base:
        opts["base_url"] = base
    key_var = next((v for v in API_KEY_VARS if os.environ.get(v)), None)
    if key_var:
        opts["api_key"] = os.environ[key_var]
    if os.environ.get("WORKER_MAX_TOKENS"):
        opts["max_tokens"] = int(os.environ["WORKER_MAX_TOKENS"])

    # A key for one provider with no base_url set goes to a DIFFERENT provider's
    # endpoint, because that is the SDK default. Silently shipping someone's KIT
    # credential to api.deepseek.com is not a failure mode worth being subtle
    # about, so refuse instead.
    if key_var == "KI_TOOLBOX_API_KEY" and "base_url" not in opts:
        raise SystemExit(
            "KI_TOOLBOX_API_KEY is set but no endpoint is: the SDK default would send "
            "it to DeepSeek.\n"
            "  Set WORKER_BASE_URL (worker/config.sh exports it for the queue), or add\n"
            "  WORKER_BASE_URL=https://ki-toolbox.scc.kit.edu/api/v1 to "
            "worker/.env")
    return opts


class Timeout(SystemExit):
    """Raised from the SIGTERM handler.

    `timeout(1)` sends SIGTERM, whose default disposition kills the process
    outright -- no exception, no `finally`, no envelope. Turning it into an
    exception is what lets the envelope be written on the one path the runner
    most needs to classify. (The old code caught KeyboardInterrupt, which SIGTERM
    never raises, and so mislabelled every real timeout as a clean crash.)
    """


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    prompt_file, work, log_json, log_err = (Path(a) for a in argv)

    run_dir = work / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "session.jsonl"

    # A stale envelope from the previous attempt is worse than none: the runner
    # would read it as this attempt's result.
    log_json.unlink(missing_ok=True)
    errf = open(log_err, "w", encoding="utf-8")

    load_env(ENV_FILE)
    route = routing()
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError:
        print("deepseek-harness-sdk is not installed.\n"
              "  pip install deepseek-harness-sdk   (NOT 'deepseek-harness' -- a\n"
              "  different third-party package shadows that module name)", file=errf)
        errf.close()
        return 2

    slug = work.name
    n_events = n_turns = 0
    started = time.time()
    final, reason, rc, note = "", None, 0, None

    def tap(notification):
        nonlocal n_events, n_turns
        n_events += 1
        payload = notification.payload or {}
        event = payload.get("event")
        # num_turns must mean what it means in `claude -p`'s envelope: AGENT turns.
        # Counting notifications instead inflates it by one to two orders of
        # magnitude, so the two dispatchers' logs stop being comparable.
        if isinstance(event, dict) and event.get("type") == "turn/end":
            n_turns += 1
        try:
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"method": notification.method,
                                     "payload": payload}, default=str) + "\n")
        except Exception:
            pass                                # logging must never kill a run

    def on_sigterm(_signum, _frame):
        raise Timeout()

    previous = signal.signal(signal.SIGTERM, on_sigterm)

    harness = DeepSeekHarness(
        cordis=str(HARNESS / "bench.cordis.yml"),
        cwd=str(work),
        session_root=str(run_dir / "sessions"),
        env={
            "DSH_CWD": str(work),
            "DSH_SESSION_ROOT": str(run_dir / "sessions"),
            # The agent must reach the solver repo, which is outside the workdir.
            "DSH_SANDBOX_MODE": os.environ.get("DSH_SANDBOX_MODE", "danger-full-access"),
        },
        request_timeout_seconds=float(os.environ.get("DSH_REQUEST_TIMEOUT", "1800")),
        **route,
    )

    try:
        harness.start()
        # One durable session per benchmark, named for it: re-running the same
        # slug continues rather than starting over.
        result = harness.start_session(f"bench-{slug}").run(
            prompt_file.read_text(encoding="utf-8"), on_notification=tap)
        final = result.final_response or ""
        reason = result.finish_reason
        rc = FINISH_EXIT.get(reason, 1)
        if rc:
            note = (f"dsh finished with finish_reason={reason!r} after {n_turns} turns "
                    f"/ {n_events} events; see {events_path}")
            print(note, file=errf)
    except Timeout:
        rc, reason, note = TIMEOUT_EXIT, "timeout", f"SIGTERM after {n_turns} turns"
        print(note, file=errf)
    except KeyboardInterrupt:
        # A person pressing Ctrl-C is not a timeout, and recording it as one sends
        # the next attempt looking for a budget problem that never happened.
        rc, reason, note = 130, "interrupted", "SIGINT (Ctrl-C)"
        print(note, file=errf)
    except Exception as exc:
        rc, reason, note = 1, "exception", f"{type(exc).__name__}: {exc}"
        print(note, file=errf)
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            harness.close()
        except Exception:
            pass
        # In `finally` so the timeout path -- the one the runner most needs to
        # classify -- gets an envelope too.
        log_json.write_text(json.dumps({
            "result": final,
            "num_turns": n_turns,
            "num_events": n_events,
            "total_cost_usd": None,          # dsh does not report cost here
            "harness": "dsh",
            "finish_reason": reason,
            "exit_code": rc,
            "note": note,
            "model": route.get("model", "<sdk default>"),
            "provider": route.get("provider", "<sdk default>"),
            "base_url": route.get("base_url", "<sdk default>"),
            "session_events": str(events_path),
            "wall_seconds": round(time.time() - started, 1),
        }, indent=2) + "\n", encoding="utf-8")
        errf.close()

    if final:
        print(final)                        # the runner greps STATUS: from stdout
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
