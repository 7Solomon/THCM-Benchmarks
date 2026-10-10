#!/usr/bin/env python3
"""
check_dataflow.py -- does each job's dataflow.json still describe the real job?

    python worker/tools/check_dataflow.py              # every worker/jobs/*/dataflow.json
    python worker/tools/check_dataflow.py eval oofem   # just these jobs

A dataflow.json is hand-written: nothing can extract a repair loop from Python
control flow. What CAN be extracted is the CLI, so this checks the part that
drifts silently:

  - every flag the job's code accepts is listed in params, and every listed
    flag still exists (argparse `add_argument("--x")` via ast; `--x)` case
    branches in shell)
  - a param's `default` matches a literal argparse default, or the
    `NAME=value` / `${NAME:-value}` assignment for an env param
  - every id an edge, group, `runs` or `affects` names exists; toggles and loop
    limits are referenced by something; `code` paths exist

The structure itself is checked against worker/schemas/dataflow.schema.json
when jsonschema is installed. Exits 1 on any problem, so it can gate a commit.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
JOBS = REPO / "worker" / "jobs"
SCHEMA = REPO / "worker" / "schemas" / "dataflow.schema.json"

IGNORED_FLAGS = {"--help"}
# A shell `case` branch: `  --a|--b)` at the start of a line. Comment and usage
# lines never match, because they do not start with the flag.
SHELL_BRANCH = re.compile(r"^\s*((?:-{1,2}[A-Za-z0-9][\w-]*\|?)+)\)")
_NO_DEFAULT = object()


def python_flags(path: Path) -> dict[str, object]:
    """{flag: literal default or _NO_DEFAULT} for every add_argument("--x")."""
    flags: dict[str, object] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        names = [a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)
                 and a.value.startswith("--")]
        if not names:
            continue
        kw = {k.arg: k.value for k in node.keywords}
        default: object = _NO_DEFAULT
        if "default" in kw:
            try:
                default = ast.literal_eval(kw["default"])
            except ValueError:
                pass                    # computed, e.g. cfg.get("endpoint")
        elif isinstance(kw.get("action"), ast.Constant) and \
                kw["action"].value == "store_true":
            default = False
        for name in names:
            flags[name] = default
    return flags


def shell_flags(path: Path) -> dict[str, object]:
    flags: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = SHELL_BRANCH.match(line)
        if m:
            for name in m.group(1).split("|"):
                if name.startswith("--"):
                    flags[name] = _NO_DEFAULT
    return flags


def env_default(path: Path, name: str) -> tuple[bool, str | None]:
    """(is the name mentioned at all, the value it is assigned if literal)."""
    text = path.read_text(encoding="utf-8")
    if not re.search(rf"\b{re.escape(name)}\b", text):
        return False, None
    m = re.search(rf"^\s*{re.escape(name)}=(\S+)", text, re.M)
    if not m:
        m = re.search(rf"\$\{{{re.escape(name)}:-([^}}]*)\}}", text)
        return True, m.group(1) if m else None
    value = m.group(1).strip("\"'")
    inner = re.fullmatch(rf"\$\{{{re.escape(name)}:-([^}}]*)\}}", value)
    return True, inner.group(1) if inner else value


def schema_problems(doc: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"schema: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def check(path: Path) -> list[str]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"not valid JSON: {e}"]
    problems = schema_problems(doc)

    if doc.get("job") != path.parent.name:
        problems.append(f"job is {doc.get('job')!r} but the file is in {path.parent.name}/")

    groups = {g["id"]: g for g in doc.get("groups", [])}
    nodes = {n["id"]: n for n in doc.get("nodes", [])}
    for kind, items in (("group", doc.get("groups", [])), ("node", doc.get("nodes", []))):
        seen: set[str] = set()
        for item in items:
            if item["id"] in seen:
                problems.append(f"duplicate {kind} id {item['id']!r}")
            seen.add(item["id"])
    clash = set(groups) & set(nodes)
    if clash:
        problems.append(f"ids used for both a group and a node: {', '.join(sorted(clash))}")

    for g in groups.values():
        if g.get("parent") and g["parent"] not in groups:
            problems.append(f"group {g['id']}: unknown parent {g['parent']!r}")
    for n in nodes.values():
        if n.get("group") and n["group"] not in groups:
            problems.append(f"node {n['id']}: unknown group {n['group']!r}")
        for ref in (n.get("code") or "").split(","):
            file = ref.strip().split(":")[0]
            if file and not (REPO / file).exists():
                problems.append(f"node {n['id']}: code path does not exist: {file}")
    for i, e in enumerate(doc.get("edges", [])):
        for end in ("from", "to"):
            if e.get(end) not in nodes:
                problems.append(f"edge {i} ({e.get('from')} -> {e.get('to')}): "
                                f"unknown {end} node {e.get(end)!r}")

    # --- params against the code ----------------------------------------
    found: dict[str, object] = {}
    for rel in doc.get("flag_sources", []):
        src = REPO / rel
        if not src.exists():
            problems.append(f"flag source does not exist: {rel}")
            continue
        for flag, default in (python_flags(src) if src.suffix == ".py"
                              else shell_flags(src)).items():
            # The first source that declares a literal default wins.
            if found.get(flag, _NO_DEFAULT) is _NO_DEFAULT:
                found[flag] = default
    found = {f: d for f, d in found.items() if f not in IGNORED_FLAGS}

    params = doc.get("params", [])
    by_flag = {p["flag"]: p for p in params if "flag" in p}
    by_env = {p["env"]: p for p in params if "env" in p}
    for flag in sorted(set(found) - set(by_flag)):
        problems.append(f"{flag} is accepted by the job but missing from params")
    for flag in sorted(set(by_flag) - set(found)):
        problems.append(f"{flag} is in params but no flag source accepts it")
    for flag, p in by_flag.items():
        code_default = found.get(flag, _NO_DEFAULT)
        if "default" in p and code_default is not _NO_DEFAULT \
                and p["default"] != code_default:
            problems.append(f"{flag}: default is {p['default']!r} here but "
                            f"{code_default!r} in the code")
    for name, p in by_env.items():
        src = REPO / p["source"] if p.get("source") else None
        if not src or not src.exists():
            problems.append(f"env {name}: source missing or does not exist")
            continue
        mentioned, value = env_default(src, name)
        if not mentioned:
            problems.append(f"env {name}: not mentioned in {p['source']}")
        elif "default" in p and value is not None and str(p["default"]) != value:
            problems.append(f"env {name}: default is {p['default']!r} here but "
                            f"{value!r} in {p['source']}")

    # --- params against the graph ---------------------------------------
    # "!--agent" is on while --agent is off; it still names the --agent toggle.
    switches = [(x.get("enabled_by") or "").lstrip("!") or None for x in
                list(nodes.values()) + list(groups.values()) + doc.get("edges", [])]
    limits = [e.get("limit") or {} for e in doc.get("edges", [])]
    for switch in filter(None, switches):
        if switch.startswith("--"):
            if by_flag.get(switch, {}).get("kind") != "toggle":
                problems.append(f"enabled_by {switch}: not a toggle in params")
        elif switch.split("=")[0] not in by_env:
            problems.append(f"enabled_by {switch}: env not in params")
    for lim in limits:
        if lim.get("flag") and lim["flag"] not in by_flag:
            problems.append(f"limit {lim['flag']}: not in params")
        if lim.get("env") and lim["env"] not in by_env:
            problems.append(f"limit {lim['env']}: env not in params")
    for p in params:
        name = p.get("flag") or p.get("env")
        for key in ("runs", "affects"):
            for ref in p.get(key, []):
                if ref not in nodes:
                    problems.append(f"{name}: {key} names unknown node {ref!r}")
        for implied in p.get("implies", []):
            if implied not in by_flag:
                problems.append(f"{name}: implies unknown flag {implied}")
        if p["kind"] == "mode" and not p.get("runs"):
            problems.append(f"{name}: a mode must list the nodes it runs")
        if p["kind"] == "toggle" and name not in switches and not p.get("affects"):
            problems.append(f"{name}: toggle that nothing is enabled_by")
        if p["kind"] == "loop-limit" and \
                not any(name in (lim.get("flag"), lim.get("env")) for lim in limits) \
                and not p.get("affects"):
            problems.append(f"{name}: loop limit that caps no edge")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("jobs", nargs="*", help="job names (default: every job with a dataflow.json)")
    a = ap.parse_args(argv)
    paths = ([JOBS / j / "dataflow.json" for j in a.jobs] if a.jobs
             else sorted(JOBS.glob("*/dataflow.json")))
    bad = 0
    for path in paths:
        if not path.exists():
            print(f"{path.parent.name}: no dataflow.json")
            bad += 1
            continue
        problems = check(path)
        bad += bool(problems)
        print(f"{path.parent.name}: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
        for p in problems:
            print(f"  - {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
