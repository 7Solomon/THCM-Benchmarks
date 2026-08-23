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

# needs_human parks a run: it stops, but it is not a verdict. The queue skips it
# on normal passes (nothing has changed, a fresh agent would fail identically)
# and picks it up only on `run_queue.sh --resume`, after a human filled in the
# extraction handoff.
TERMINAL = ["done", "infeasible", "extract_failed", "run_failed", "stuck"]
PARKED = ["needs_human"]
STOPPED = TERMINAL + PARKED
STATES = ["in_progress"] + STOPPED


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def path_of(workdir):
    return Path(workdir) / "STATUS.json"


def load(workdir):
    p = path_of(workdir)
    if not p.exists():
        return {"slug": Path(workdir).name, "state": "in_progress",
                "phase_reached": None, "phase_log": [], "fix_iterations": 0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"STATUS.json is not valid JSON ({e}) — fix or delete it: {p}")


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
        link = Path(workdir) / case / "STATUS.json"
        if link.parent.is_dir() and not link.exists():
            try:
                link.symlink_to(Path("..") / "STATUS.json")
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
    if a.key_result:
        d["key_result"] = a.key_result
    if a.case:
        d["case"] = a.case
    d.setdefault("report", str(Path(a.workdir) / "REPORT.md"))
    d.setdefault("phase_log", []).append(
        {"phase": d.get("phase_reached"), "at": now(), "note": f"stopped: {a.state}"})
    apply_sets(d, a.set)
    print(f"STATUS: {a.state} | {a.key_result or ''}")
    save(a.workdir, d)


def cmd_show(a):
    d = load(a.workdir)
    print(f"{d.get('state','?'):<14} {d.get('slug','?')}  "
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
