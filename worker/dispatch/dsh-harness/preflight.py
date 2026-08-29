#!/usr/bin/env python3
"""
preflight.py -- assert the dsh composition is what we think it is, before a run.

`read_image` is not registered without an attachment store, and nothing says so:
the tool is simply absent and the agent works around it. The same is true of every
other row in bench.cordis.yml. So we do not trust the config -- we start the runtime,
read the assembled tool list out of the `request/header` event, and compare.

This needs NO API key. The model call fails (`finish_reason: "error"`), but the whole
turn pipeline up to `agent/request` fires first, and `request/header` carries both the
system prompt and the full tool schema list.

    python worker/dispatch/dsh-harness/preflight.py              # default route, from ./.env
    python worker/dispatch/dsh-harness/preflight.py --route mock # no credentials at all
    python worker/dispatch/dsh-harness/preflight.py --json       # machine-readable, for CI

Exit 0 = composition matches. 1 = the header was read and a required tool is missing.
2 = the runtime would not start, OR no request/header event ever fired.

The 1/2 split matters. If the runtime or the event schema changes so that
`request/header` never arrives, `header` stays empty and EVERY tool reads as missing
-- which sends you debugging bench.cordis.yml when what actually broke is the probe.
Exit 2 says "I learned nothing", exit 1 says "I looked, and the composition is wrong".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/dispatch/dsh-harness
WORKER = HERE.parent.parent                     # worker
ENV_FILE = WORKER / ".env"                      # shared by every job

# What bench.cordis.yml is supposed to produce. A tool going missing here is a
# silent capability loss, which is exactly what this file exists to catch.
REQUIRED = {
    "bash":        "run commands; the 3 h solver needs run_in_background",
    "job_output":  "collect a background job -- wait:true instead of polling",
    "job_list":    "enumerate running jobs",
    "job_kill":    "stop a job that stopped mattering",
    "skill":       "load an authoring skill by name from the catalog",
    "read":        "read a file",
    "write":       "write a file",
    "edit":        "edit a file",
    "read_image":  "LOOK at a figure crop -- absent unless dsh-attachment-local is mounted",
}
OPTIONAL = {"todo", "ask_user", "web_search", "web_fetch", "fs_search",
            "str_replace_editor"}  # redundant once tool-fs supplies read/write/edit


def load_env(path: Path) -> list[str]:
    """Minimal .env loader. Existing environment always wins, so an exported key
    is not silently overridden by a stale file."""
    loaded = []
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


def probe(route: str, timeout: float) -> dict:
    """Start the runtime, run one prompt, capture request/header. Never raises on a
    model failure -- that is expected without a key and is not what we are testing."""
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError:
        print("deepseek-harness-sdk is not installed in this interpreter.\n"
              "  pip install deepseek-harness-sdk   (NOT 'deepseek-harness' -- that is\n"
              "  an unrelated third-party client that shadows the same module name)",
              file=sys.stderr)
        raise SystemExit(2)

    ws = Path(tempfile.mkdtemp(prefix="dsh-preflight-"))
    (ws / "skills").mkdir(exist_ok=True)
    env = {
        "DSH_CWD": str(ws),
        "DSH_SESSION_ROOT": str(ws / "sessions"),
        "DSH_SKILL_ROOT": os.environ.get("DSH_SKILL_ROOT", str(ws / "skills")),
    }
    if route == "mock":
        env["DEEPSEEK_API_KEY"] = "preflight-no-key-needed"

    header: dict = {}
    seen = {"header": False}

    def tap(n):
        ev = (n.payload or {}).get("event")
        if isinstance(ev, dict) and ev.get("type") == "request/header":
            seen["header"] = True
            header.update(ev.get("data", {}).get("header", {}))

    h = DeepSeekHarness(
        cordis=str(HERE / "bench.cordis.yml"),
        cwd=str(ws),
        session_root=str(ws / "sessions"),
        env=env,
        request_timeout_seconds=timeout,
    )
    try:
        h.start()
    except Exception as exc:                       # the runtime itself is broken
        print(f"runtime failed to start: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    try:
        h.run("preflight", session_id="preflight", on_notification=tap)
    except Exception:
        pass                                        # model failure is fine
    finally:
        h.close()
    return header, seen["header"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--route", default="default", choices=["default", "mock"])
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    loaded = load_env(ENV_FILE)
    header, saw_header = probe(a.route, a.timeout)
    tools = {t["name"] for t in header.get("tools", [])}
    missing = {k: v for k, v in REQUIRED.items() if k not in tools}
    extra = sorted(tools - set(REQUIRED) - OPTIONAL)

    if a.json:
        print(json.dumps({
            "ok": saw_header and not missing,
            "header_observed": saw_header,
            "tools": sorted(tools),
            "missing": sorted(missing) if saw_header else [],
            "unexpected": extra,
            "model": header.get("config", {}).get("model"),
            "system_prompt_chars": len(header.get("system", "")),
        }, indent=2))
        return 0 if (saw_header and not missing) else (1 if saw_header else 2)

    if not saw_header:
        print("no request/header event was observed — the PROBE is broken, not the "
              "composition.\n"
              "  Nothing can be said about the tool list from this run. Check that the\n"
              "  runtime starts, that the event is still called 'request/header', and\n"
              "  that a turn gets as far as agent/request.", file=sys.stderr)
        return 2

    print(f"env      : {ENV_FILE} -> "
          f"{', '.join(loaded) if loaded else 'nothing loaded'}")
    print(f"route    : {a.route}")
    print(f"model    : {header.get('config', {}).get('model', '?')}  "
          f"maxTokens={header.get('config', {}).get('maxTokens', '?')}")
    print(f"prompt   : {len(header.get('system', ''))} chars")
    print(f"tools    : {len(tools)} registered")
    for name in sorted(tools):
        print(f"           + {name}")
    if extra:
        print(f"           (not in the expected set: {', '.join(extra)})")
    if missing:
        print("\nMISSING — the composition is not what bench.cordis.yml intends:")
        for name, why in sorted(missing.items()):
            print(f"  - {name:20s} {why}")
        if "read_image" in missing:
            print("\n  read_image specifically: check the `attachment-local` row is present\n"
                  "  AND that the routed model's inputModalities include 'image'. Missing\n"
                  "  either one removes the tool with no error anywhere.")
        return 1
    print("\nOK — every required tool is registered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
