#!/usr/bin/env python3
"""
llm.py -- the KIT Toolbox chat endpoint, and models.yaml.

Shared by every job that calls a model: `eval` (author a benchmark.json) and
`oofem` (author an oofempy script). It owns four things and nothing else:

    load_env()      worker/.env -> os.environ, without clobbering a real env var
    api_key()       the credential, or a message naming where to put one
    read_yaml()     models.yaml only -- a flat shape we control, so a 40-line
                    reader beats a dependency the venv does not have
    call()          one chat completion, returning (response, error) rather
                    than raising: a sweep must survive one model being down

Stdlib only, on purpose. worker/.venv is a bare interpreter until the solver is
installed, and a sweep that cannot start because numpy is missing is a sweep
that does not happen.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import urllib.error
import urllib.request

WORKER = Path(__file__).resolve().parent.parent
ENV_FILES = (WORKER / ".env",)
API_KEY_VARS = ("WORKER_API_KEY", "KI_TOOLBOX_API_KEY", "DEEPSEEK_API_KEY")
MODELS_YAML = WORKER / "models.yaml"


def load_env() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


def api_key() -> str:
    for var in API_KEY_VARS:
        if os.environ.get(var):
            return os.environ[var]
    sys.exit(f"no API key: set one of {', '.join(API_KEY_VARS)} "
             f"(or put it in {ENV_FILES[0]})")


def strip_comment(v: str) -> str:
    """Drop a trailing ` # ...`. Quote-aware, so a value containing a # survives."""
    out, in_str, quote = [], False, ""
    for i, ch in enumerate(v):
        if in_str:
            if ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str, quote = True, ch
        elif ch == "#" and (i == 0 or v[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out).strip()


def coerce(v: str):
    v = strip_comment(v)
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v.strip('"').strip("'")


def read_yaml(path: Path = MODELS_YAML) -> dict:
    root: dict = {}
    stack = [(-1, root)]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            line = line[2:].strip()
            if not isinstance(parent, list):
                continue
            item: dict = {}
            parent.append(item)
            stack.append((indent, item))
            parent, line = item, line
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            nxt: list | dict = [] if key == "models" else {}
            parent[key] = nxt
            stack.append((indent, nxt))
        else:
            parent[key] = coerce(val)
    return root


def select_models(cfg: dict, requested: list | None, allow_metered: bool) -> list:
    """Named models, or every `default: true` one. Metered models are a HARD gate:
    a sweep is tens of calls of ~60k input tokens, so spending has to be asked
    for, never defaulted into by naming a model."""
    known = {m["id"]: m for m in cfg.get("models", []) if "id" in m}
    models = ([known.get(m, {"id": m}) for m in requested] if requested
              else [m for m in cfg.get("models", []) if m.get("default")])
    if not models:
        sys.exit("no models selected")
    metered = [m["id"] for m in models if known.get(m["id"], m).get("metered")]
    if metered and not allow_metered:
        sys.exit(f"these models are metered: {', '.join(metered)}\n"
                 f"  re-run with --allow-metered to spend, or pick KIT-hosted "
                 f"models (they are unmetered).\n"
                 f"  `worker.sh eval --list-models` shows what is served.")
    return models


def chat(endpoint: str, key: str, model: str, messages: list,
         max_tokens: int, temperature: float, timeout: float,
         tools: list | None = None) -> dict:
    """One chat completion over a message list, optionally with tools.

    Returns the raw response dict, with transport failures encoded INTO it
    (`error` / `http_error`) rather than raised: the agent loop has to be able
    to record a failed turn and stop cleanly, not unwind."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        return {"http_error": e.code, "detail": detail}
    except Exception as e:
        return {"error": str(e)}


def call(endpoint: str, key: str, model: str, prompt: str,
         max_tokens: int, temperature: float, timeout: float) -> tuple:
    """One single-shot completion, as (response, error). Kept as its own entry
    point because the one-shot arm wants the error split out for its outcome
    table, while the agent loop wants the raw dict."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}], "stream": False}
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        return {"http_error": e.code, "detail": detail}, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return {"error": str(e)}, f"{type(e).__name__}: {e}"
