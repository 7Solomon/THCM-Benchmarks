#!/usr/bin/env python3
"""Read/write a worker's STATUS.json — the one file that says where an agent is.

Written through this tool rather than by hand so that: the state string is
checked against the runner's enum (a typo reads as a crash and burns a retry),
the phase log is append-only, writes are atomic (a queue killed mid-write must
not leave truncated JSON), and every phase transition is timestamped — which is
what tells you where an agent stopped when it stops in the middle.

    status.py phase  WORKDIR 3 --note "digitizing fig 7a" [--case thermal]
    status.py set    WORKDIR --set fix_iterations=1 --set 'fixes_applied=["units"]'
    status.py finish WORKDIR --state done --key-result "peak 70.0 vs 59.0 C"
    status.py show   WORKDIR
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# THREE stops, and each answers ONE question: what does the human do now?
#
#   done      the investigation finished -- read RESULT.md
#   needs_you blocked on data or a decision only a person has -- RESULT.md says
#             exactly what to drop where
#   failed    the pipeline broke -- RESULT.md names the phase and the error
#
# The older model had five terminal states that described the AGENT's situation
# (infeasible / extract_failed / run_failed / stuck) and left the reader to work
# out the consequence. "This paper needs axisymmetric elements, which are not
# wired" is a complete, correct answer, not a failure; so is "it does not
# reproduce and here is why". Both are `done` with a VERDICT.
TERMINAL = ["done", "failed"]
PARKED = ["needs_you"]
STOPPED = TERMINAL + PARKED
STATES = ["in_progress"] + STOPPED

# What happened, separate from what you do about it. Only meaningful when done.
VERDICTS = ["reproduced", "not_reproduced", "infeasible"]

# Legacy -> new, so an old workdir still reads. `stuck` is `done`: the agent had
# a complete analysis, it just was not a match.
LEGACY_STATE = {
    "infeasible": ("done", "infeasible"),
    "stuck": ("done", "not_reproduced"),
    "extract_failed": ("failed", None),
    "run_failed": ("failed", None),
    "needs_human": ("needs_you", None),
}

# A key_result is a table cell, not an essay. Today's longest is 700 characters.
KEY_RESULT_MAX = 160


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


RUNDIR = ".run"


def path_of(workdir):
    """`.run/status.json`. Dot-prefixed so `ls` and file explorers hide the
    machine's bookkeeping -- the human should see paper.pdf, RESULT.md and the
    case directory, and nothing else."""
    return Path(workdir) / RUNDIR / "status.json"


def legacy_path_of(workdir):
    return Path(workdir) / "STATUS.json"


def load(workdir):
    p = path_of(workdir)
    if not p.exists():
        p = legacy_path_of(workdir)          # a workdir written before the move
    if not p.exists():
        return {"slug": Path(workdir).name, "state": "in_progress",
                "phase_reached": None, "phase_log": [], "fix_iterations": 0}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"{p.name} is not valid JSON ({e}) — fix or delete it: {p}")
    old = d.get("state")
    if old in LEGACY_STATE:                  # read an old workdir without rewriting it
        d["state"], verdict = LEGACY_STATE[old]
        d.setdefault("verdict", verdict)
        d["migrated_from"] = old
    return d


def save(workdir, d):
    d["updated"] = now()
    p = path_of(workdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)                      # atomic: readers see old or new, never half

    # A pointer next to benchmark.json, so a case directory always answers
    # "what happened here?" without knowing the workdir convention.
    case = d.get("case")
    if case:
        link = Path(workdir) / case / "status.json"
        if link.parent.is_dir() and not link.exists():
            try:
                link.symlink_to(Path("..") / RUNDIR / "status.json")
            except OSError:
                pass                        # filesystem without symlinks: not fatal
    return p


def apply_sets(d, sets):
    for item in sets or []:
        if "=" not in item:
            sys.exit(f"--set expects key=value, got: {item}")
        k, v = item.split("=", 1)
        try:
            d[k] = json.loads(v)            # numbers, lists, objects, true/false
        except json.JSONDecodeError:
            d[k] = v                        # plain string
    return d


def cmd_phase(a):
    d = load(a.workdir)
    d["state"] = "in_progress"
    d["phase_reached"] = a.phase
    for k in ("case", "pdf", "slug"):
        if getattr(a, k):
            d[k] = getattr(a, k)
    d.setdefault("phase_log", []).append(
        {"phase": a.phase, "at": now(), "note": a.note or ""})
    apply_sets(d, a.set)
    print(f"phase {a.phase}: {a.note or ''}  -> {save(a.workdir, d)}")


def cmd_set(a):
    d = apply_sets(load(a.workdir), a.set)
    print(f"updated -> {save(a.workdir, d)}")


def cmd_finish(a):
    if a.state not in STOPPED:
        sys.exit(f"state must be one of {STOPPED}, got '{a.state}' — "
                 "the runner keys on this string and anything else reads as a crash")
    d = load(a.workdir)
    d["state"] = a.state
    if a.state == "done" and not (a.verdict or d.get("verdict")):
        sys.exit("--state done requires --verdict "
                 f"{VERDICTS}: what happened is separate from what to do next")
    if a.verdict:
        d["verdict"] = a.verdict
    if a.key_result:
        kr = " ".join(a.key_result.split())
        if len(kr) > KEY_RESULT_MAX:
            kr = kr[:KEY_RESULT_MAX - 1].rstrip() + "\u2026"
        d["key_result"] = kr                 # the long version belongs in RESULT.md
    if a.case:
        d["case"] = a.case
    d.setdefault("report", str(Path(a.workdir) / "RESULT.md"))
    d.setdefault("phase_log", []).append(
        {"phase": d.get("phase_reached"), "at": now(), "note": f"stopped: {a.state}"})
    apply_sets(d, a.set)
    v = d.get("verdict")
    print(f"STATUS: {a.state}{('/' + v) if v else ''} | {d.get('key_result') or ''}")
    save(a.workdir, d)


def cmd_show(a):
    d = load(a.workdir)
    v = d.get("verdict")
    print(f"{d.get('state','?'):<10}{('/' + v) if v else '':<16} {d.get('slug','?')}  "
          f"phase {d.get('phase_reached','?')}  {d.get('key_result') or ''}")
    for e in d.get("phase_log", []):
        print(f"   {e.get('at','?')}  phase {e.get('phase','?')}  {e.get('note','')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("workdir")
        p.add_argument("--set", action="append", metavar="KEY=JSON",
                       help="set any other field (repeatable)")

    p = sub.add_parser("phase", help="entering a phase (call this on EVERY transition)")
    common(p)
    p.add_argument("phase", type=int)
    p.add_argument("--note", default="")
    p.add_argument("--case", default=None)
    p.add_argument("--pdf", default=None)
    p.add_argument("--slug", default=None)
    p.set_defaults(fn=cmd_phase)

    p = sub.add_parser("set", help="update fields without a phase transition")
    common(p)
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("finish", help="write the terminal state")
    common(p)
    p.add_argument("--state", required=True, choices=STOPPED)
    p.add_argument("--verdict", choices=VERDICTS,
                   help="required with --state done: what actually happened")
    p.add_argument("--key-result", default="")
    p.add_argument("--case", default=None)
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("show", help="print state and phase log")
    common(p)
    p.set_defaults(fn=cmd_show)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
