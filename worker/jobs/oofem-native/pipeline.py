#!/usr/bin/env python3
"""
pipeline.py -- run one oofem-native candidate: generate -> solve -> postprocess.

The `oofem` job's candidate is one Python script that drives the solver through
oofempy. This job's candidate is a set of files around the solver EXECUTABLE:

    make_deck.py     optional   writes model.in (meshing, gmsh, loops over nodes)
    model.in         required   the OOFEM input deck -- written by hand, or by
                                make_deck.py, which then takes precedence
    postprocess.py   required   reads what the solver exported, writes
                                results.csv and plot.png

and the harness, not the model, runs `oofem -f model.in` in between. Both arms
(one shot, agent) run exactly this function, so a candidate that validated in
the agent's sandbox is the same thing the one-shot arm executes.

Python is allowed AROUND the solver, never IN it: a script that imports oofempy
(or the `oofem` wheel .venv also carries) is rejected, because then this job
would be measuring the bindings again. The Python stages also run without
OOFEM_LD_PRELOAD, which the wheel needs to load -- a second, quieter fence.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import resource
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

GENERATOR = "make_deck.py"
DECK = "model.in"
POST = "postprocess.py"

# Outcomes. Distinct because each points at a different skill: deck_error is
# OOFEM input knowledge, post_crashed is reading OOFEM's output formats.
OK, REJECTED, GEN_CRASHED, NO_DECK = "ok", "rejected", "gen_crashed", "no_deck"
DECK_ERROR, POST_CRASHED = "deck_error", "post_crashed"
TIMEOUT, NO_OUTPUT, ERROR = "timeout", "no_output", "error"

# Not a sandbox -- a guard rail, same list as the oofem job. `subprocess` stays
# banned: the harness launches the solver, the candidate never does.
BANNED_IMPORTS = {
    "subprocess", "socket", "requests", "urllib", "http", "ftplib",
    "telnetlib", "smtplib", "xmlrpc", "ctypes", "pty",
}
BINDINGS = {"oofempy", "oofem"}

INCLUDE = re.compile(r'^\s*@include\s+"([^"]+)"', re.M)


@dataclass
class Toolchain:
    """Where the solver is, and how each kind of process is started."""
    oofem_bin: str
    python: str
    ld_preload: str = ""

    def solver_env(self) -> dict:
        env = dict(os.environ)
        env.pop("LD_PRELOAD", None)
        if self.ld_preload:
            env["LD_PRELOAD"] = self.ld_preload
        return env

    def python_env(self) -> dict:
        env = dict(os.environ)
        env.pop("LD_PRELOAD", None)
        env["PYTHONPATH"] = ""
        env["MPLBACKEND"] = "Agg"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONFAULTHANDLER"] = "1"
        return env


@dataclass
class Result:
    outcome: str
    message: str | None = None
    stage: str | None = None
    seconds: dict = field(default_factory=dict)
    solver_ok: bool = False
    oofem_outputs: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # oofem's unread-token warnings
    logs: dict = field(default_factory=dict)       # stage -> full text

    @property
    def total_seconds(self) -> float:
        return round(sum(self.seconds.values()), 1)


def imports_of(src: str) -> set:
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def import_gate(src: str, name: str) -> str | None:
    """Reasons to refuse a Python stage. None to proceed."""
    try:
        imported = imports_of(src)
    except SyntaxError as e:
        return f"{name} is not valid Python: {e.msg} at line {e.lineno}"
    bound = imported & BINDINGS
    if bound:
        return (f"{name} imports {', '.join(sorted(bound))}: this job runs the "
                f"oofem executable on model.in, not the Python bindings")
    banned = imported & BANNED_IMPORTS
    if banned:
        return f"{name} imports {', '.join(sorted(banned))}"
    return None


def includes_of(deck: Path) -> list:
    try:
        return INCLUDE.findall(deck.read_text(errors="replace"))
    except OSError:
        return []


def candidate_files(root: Path) -> list:
    """The files that ARE the candidate, in a stable order.

    With a generator, the deck is its product, not an input: model.in in the
    workdir is then ignored (and not hashed), so a scratch deck left there by a
    spike cannot invalidate a validated submission. Without one, the deck and
    every file it @includes are the candidate.
    """
    out = [root / n for n in (GENERATOR, POST) if (root / n).is_file()]
    if not (root / GENERATOR).is_file() and (root / DECK).is_file():
        out.append(root / DECK)
        for inc in includes_of(root / DECK):
            p = (root / inc)
            if p.is_file() and p.resolve().parent == root.resolve():
                out.append(p)
    return out


def candidate_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in candidate_files(root):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def has_candidate(root: Path) -> bool:
    return (root / POST).is_file() and (
        (root / GENERATOR).is_file() or (root / DECK).is_file())


def static_problems(root: Path) -> list:
    problems = []
    if not (root / POST).is_file():
        problems.append(f"{POST} does not exist")
    if not ((root / GENERATOR).is_file() or (root / DECK).is_file()):
        problems.append(f"neither {DECK} nor {GENERATOR} exists")
    for name in (GENERATOR, POST):
        p = root / name
        if p.is_file():
            why = import_gate(p.read_text(encoding="utf-8", errors="replace"), name)
            if why:
                problems.append(why)
    return problems


def oofem_errors(text: str, limit: int = 4) -> list:
    """The lines that say WHY oofem stopped. On a bad deck it aborts with a
    stack trace, and the last line of the log is a libc frame -- useless. The
    message is `OOFEM Error exception: ...`, often followed by the `Record:`
    it choked on."""
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if "Error exception" in s or s.startswith(("Error", "OOFEM Error")):
            msg = s
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("Record:"):
                msg += "  " + lines[i + 1].strip()
            if msg not in out:
                out.append(msg[:300])
            if len(out) >= limit:
                break
    return out


def oofem_warnings(text: str, limit: int = 6) -> list:
    """Unknown attributes are NOT errors: oofem warns and runs on without them,
    so a misspelled `tAlpha` quietly leaves the value unset. Three lines each:
    the warning, the record, the unread tokens."""
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if "Unread token" in ln:
            out.append(" ".join(s.strip() for s in lines[i:i + 3])[:300])
            if len(out) >= limit:
                break
    return out


def status(rc: int) -> str:
    if rc >= 0:
        return f"exit {rc}"
    try:
        name = signal.Signals(-rc).name
    except ValueError:
        name = f"signal {-rc}"
    return f"killed by {name}"


def _no_core():
    # A deck error ends in abort(); without this every one leaves a core file.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_process(cmd: list, cwd: Path, env: dict, timeout: float) -> tuple:
    """(returncode or None on timeout, output text, seconds).

    Output goes to a FILE rather than a pipe: oofem is C++ and prints through
    its own buffers, and when it dies natively a piped buffer dies with it --
    exactly the run whose log matters most.
    """
    log = cwd / f".proc-{os.getpid()}-{time.monotonic_ns()}.log"
    t0 = time.time()
    rc = None
    try:
        with log.open("wb") as fh:
            rc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=fh,
                                stderr=subprocess.STDOUT,
                                timeout=max(1.0, timeout),
                                start_new_session=True,
                                preexec_fn=_no_core).returncode
    except subprocess.TimeoutExpired:
        rc = None
    finally:
        text = log.read_text(errors="replace") if log.exists() else ""
        log.unlink(missing_ok=True)
    return rc, text, round(time.time() - t0, 1)


def _snapshot(root: Path) -> dict:
    return {p.name: p.stat().st_mtime_ns for p in root.iterdir()
            if p.is_file() and not p.name.startswith(".proc-")}


def new_files(root: Path, before: dict) -> list:
    """What a solver run wrote: files that are new or changed since `before`."""
    return sorted(n for n, m in _snapshot(root).items() if before.get(n) != m)


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "no output"


def run_pipeline(stage: Path, tc: Toolchain, timeout: float) -> Result:
    """Run the candidate that sits in `stage`, in `stage`. The caller prepares
    the directory (candidate files, reference/) and keeps the logs."""
    deadline = time.time() + timeout
    r = Result(outcome=OK)

    def left() -> float:
        return deadline - time.time()

    problems = static_problems(stage)
    if problems:
        r.outcome, r.message = REJECTED, "; ".join(problems)
        return r

    # 1. generate
    if (stage / GENERATOR).is_file():
        rc, text, s = run_process([tc.python, "-X", "faulthandler", GENERATOR],
                                  stage, tc.python_env(), left())
        r.logs["generate"], r.seconds["generate"] = text, s
        if rc is None:
            r.outcome, r.stage = TIMEOUT, "generate"
            r.message = f"{GENERATOR} killed after {timeout:.0f}s budget"
            return r
        if rc != 0:
            r.outcome, r.stage = GEN_CRASHED, "generate"
            r.message = f"{GENERATOR} {status(rc)}: {_last_line(text)}"
            return r
    if not (stage / DECK).is_file():
        r.outcome, r.stage = NO_DECK, "generate"
        r.message = f"{GENERATOR} exited 0 but wrote no {DECK}"
        return r

    # 2. solve
    if left() <= 0:
        r.outcome, r.stage, r.message = TIMEOUT, "solve", "no time left to solve"
        return r
    before = _snapshot(stage)
    try:
        rc, text, s = run_process([tc.oofem_bin, "-f", DECK], stage,
                                  tc.solver_env(), left())
    except FileNotFoundError:
        r.outcome, r.stage = ERROR, "solve"
        r.message = f"oofem executable not found: {tc.oofem_bin}"
        return r
    r.logs["solve"], r.seconds["solve"] = text, s
    r.oofem_outputs = [n for n in new_files(stage, before) if n != DECK]
    r.warnings = oofem_warnings(text)
    if rc is None:
        r.outcome, r.stage = TIMEOUT, "solve"
        r.message = f"oofem killed after {timeout:.0f}s budget"
        return r
    errs = oofem_errors(text)
    if rc != 0:
        r.outcome, r.stage = DECK_ERROR, "solve"
        r.message = f"oofem {status(rc)}: " + (" | ".join(errs) or _last_line(text))
        return r
    r.solver_ok = True

    # 3. postprocess
    if left() <= 0:
        r.outcome, r.stage, r.message = TIMEOUT, "postprocess", "no time left"
        return r
    rc, text, s = run_process([tc.python, "-X", "faulthandler", POST],
                              stage, tc.python_env(), left())
    r.logs["postprocess"], r.seconds["postprocess"] = text, s
    if rc is None:
        r.outcome, r.stage = TIMEOUT, "postprocess"
        r.message = f"{POST} killed after {timeout:.0f}s budget"
        return r
    if rc != 0:
        r.outcome, r.stage = POST_CRASHED, "postprocess"
        r.message = f"{POST} {status(rc)}: {_last_line(text)}"
        return r
    if not (stage / "results.csv").is_file():
        r.outcome, r.stage = NO_OUTPUT, "postprocess"
        r.message = f"{POST} exited 0 but wrote no results.csv"
        return r
    return r


def render_log(r: Result) -> str:
    """One run.log, stage by stage, for a person reading it afterwards."""
    parts = []
    for name in ("generate", "solve", "postprocess"):
        if name in r.logs:
            parts.append(f"===== {name} ({r.seconds.get(name, 0):.1f}s) =====\n"
                         + r.logs[name].rstrip() + "\n")
    parts.append(f"===== outcome: {r.outcome}"
                 + (f" [{r.stage}] {r.message}" if r.message else "") + "\n")
    return "\n".join(parts)
