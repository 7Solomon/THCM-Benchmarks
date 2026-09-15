#!/usr/bin/env python3
"""
cluster_findings.py -- collapse a finding list into the mistakes that CAUSED it.

`compare_benchmark.py` reports one finding per differing key, which is the right
unit for scoring and the wrong unit for reading. A model that simply never writes
`time_function` on a boundary condition produces one finding per condition; a
model that read the whole time axis in hours produces one per time value. At
25-30 findings per case the list stops being a diagnosis and becomes a diff.

This groups findings by the mistake behind them, so the same case reads:

    3x  `time_function` omitted from every displacement/dirichlet condition
    2x  the uy boundary written as a time_table where the reference uses a scalar
    1x  physics_class "mechanics" vs "mechanical"

Purely mechanical -- no model, no network. It sits between the comparator and
both readers (report.py, explain.py), and it never changes a score: clustering
is a view over findings, and the findings are what accuracy is computed from.

    from cluster_findings import cluster, render
    for c in cluster(score["findings"]):
        print(c.title, c.findings)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare_benchmark import SECTIONS, fmt          # noqa: E402

# `displacement/dirichlet/uy[1]` -- a whole condition, as opposed to
# `displacement/dirichlet/uy[1].value`, one key inside one.
ITEM_RE = re.compile(r"^(.*)\[(\d+)\]$")

# `mechanics (field:displacement)` -- cmp_physics prints the resolved field
# beside the block's own label when the two differ.
FIELD_RE = re.compile(r"\(field:([^)]+)\)")

# How a section is named in a sentence. The schema keys are snake_case nouns and
# reading "3 boundary_conditions entries" out loud is what a report should never
# make a person do.
SECTION_NOUN = {
    "physics": "physics blocks", "materials": "materials",
    "boundary_conditions": "boundary conditions",
    "initial_conditions": "initial conditions",
    "geometry": "geometry keys", "time_stepping": "time-stepping keys",
    "metadata": "metadata keys", "results.queries": "queries",
}

# A cluster is worth naming as a cause only when it has this many findings.
# Below it, the one-line rendering of the finding itself is already the clearest
# statement of the problem and a title would just be a longer way to say it.
MIN_GROUP = 2


class Cluster:
    """One mistake, and every finding it produced."""

    __slots__ = ("kind", "section", "subject", "findings", "title", "note")

    def __init__(self, kind, section, subject, findings, title, note=""):
        self.kind, self.section, self.subject = kind, section, subject
        self.findings, self.title, self.note = findings, title, note

    @property
    def weight(self) -> float:
        """Ranking only. The section weight from the comparator times how many
        findings the cause produced, so a materials habit outranks a metadata
        one-off even when the metadata row is alphabetically first."""
        w = SECTIONS.get(self.section, ("", 1.0))[1]
        return w * len(self.findings)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "section": self.section, "subject": self.subject,
                "title": self.title, "note": self.note,
                "n": len(self.findings),
                "findings": [f["_i"] for f in self.findings]}


def leaf(what: str) -> str:
    return what.rsplit(".", 1)[-1]


def parent(what: str) -> str:
    return what.rsplit(".", 1)[0] if "." in what else ""


def cause_key(f: dict) -> tuple:
    """The mistake a finding is evidence OF.

    Two findings share a key when fixing one thing would fix both. That is the
    whole rule -- everything below is it applied to the shapes `what` can take.
    """
    section, kind, what = f["section"], f["kind"], f["what"]

    # A clean 1e3/86400-type ratio is a conversion, and every value that shares
    # the ratio shares the one mistake -- regardless of which section it is in.
    if kind == "wrong_unit":
        return ("unit-slip", "", f.get("note") or "?")

    # `concrete.temperature` with reference "section": a whole constitutive block
    # the spec never wrote. Same for a physics block that is absent entirely.
    if kind in ("missing", "extra") and (f.get("reference") == "section"
                                         or f.get("candidate") == "section"):
        return ("absent-block", section, kind)
    if section == "physics" and kind in ("missing", "extra"):
        return ("absent-block", section, kind)

    # `displacement/dirichlet/uy[2]` -- a whole condition present on one side
    # only. Grouped by role, because "the spec has no uy conditions" is one fact.
    m = ITEM_RE.match(what)
    if m and kind in ("missing", "extra"):
        return ("absent-item", section, f"{kind}:{m.group(1)}")

    # Everything else: the same leaf key handled the same wrong way in more than
    # one place is one habit. `time_function` missing from four conditions is not
    # four mistakes.
    return ("key", section, f"{kind}:{leaf(what)}")


def title_for(key: tuple, fs: list) -> tuple[str, str]:
    """(title, note) for a group. Written as a sentence about the AUTHOR, because
    the reader's next question is always 'what do I change'."""
    tag, section, subject = key
    n = len(fs)
    whats = [f["what"] for f in fs]

    if tag == "unit-slip":
        return (f"{n} value{'' if n == 1 else 's'} off by a factor of {subject}",
                "a conversion, not a misreading — " + ", ".join(whats[:4]))

    if tag == "absent-block":
        kind = subject
        if section == "physics":
            verb = ("the spec does not model" if kind == "missing"
                    else "the spec models physics the reference does not have")
            return (f"{verb}: {', '.join(whats[:4])}",
                    "a whole field or coupling block, not a value")
        verb = "never written" if kind == "missing" else "written but not in the reference"
        return (f"{n} {SECTION_NOUN.get(section, section)} {verb}: "
                f"{', '.join(whats[:4])}",
                "the physics has no constitutive data here" if kind == "missing" else "")

    if tag == "absent-item":
        kind, role = subject.split(":", 1)
        noun = "condition" if n == 1 else "conditions"
        if kind == "missing":
            return (f"{n} `{role}` {noun} in the reference, absent from the spec", "")
        return (f"{n} `{role}` {noun} the reference does not have", "")

    kind, key_name = subject.split(":", 1)
    where = SECTION_NOUN.get(section, section.replace("_", " "))
    if kind == "missing":
        return (f"`{key_name}` omitted from {n} of the {where}",
                ", ".join(parent(w) or w for w in whats[:4]))
    if kind == "extra":
        return (f"`{key_name}` added to {n} of the {where}, which do not have it",
                ", ".join(parent(w) or w for w in whats[:4]))
    if kind in ("wrong_value", "wrong_model", "type_differs"):
        ex = fs[0]
        return (f"`{key_name}` differs in {n} of the {where}",
                f"e.g. {fmt(ex['candidate'])} vs {fmt(ex['reference'])}")
    return (f"{n} {kind} finding(s) in {where}", "")


def finding_field(f: dict) -> str | None:
    """Which physical field a finding is about, or None.

    The schema keys materials, conditions and queries by field name, so this is
    read straight off `what` -- the shape differs per section, nothing else does.
    """
    section, what = f["section"], f["what"]
    if section == "physics":
        m = FIELD_RE.search(what)
        return m.group(1) if m else what.split(".")[0].strip()
    if section == "materials":
        parts = what.split(".")
        return parts[1] if len(parts) > 1 else None
    if section in ("boundary_conditions", "initial_conditions"):
        return re.split(r"[/\[]", what, 1)[0] or None
    if section == "results.queries":
        return what.split(":", 1)[-1] if ":" in what else None
    return None


def scope_causes(fs: list) -> tuple[list, list]:
    """The one mistake that fans out furthest: modelling the wrong PROBLEM.

    A spec that declares physics the reference does not have does not make one
    error -- it then writes materials, boundary conditions, initial conditions
    and queries for those fields, and every one of them is reported separately.
    On HITEC/cox the conduction-only leg authored as full THM produced 15
    findings across five sections from a single decision.

    So: when a physics block is `extra` (or `missing`), every same-kind finding
    about the same field is absorbed into it. Same-kind only -- a field the spec
    should not have at all cannot also be the reason a value inside it is wrong.

    Returns (clusters, remaining findings).
    """
    out, absorbed = [], set()
    for kind in ("extra", "missing"):
        blocks = [f for f in fs if f["section"] == "physics" and f["kind"] == kind]
        fields = {finding_field(f) for f in blocks} - {None}
        if not fields:
            continue
        members = [f for f in fs if f["kind"] == kind
                   and (f["section"] == "physics" or finding_field(f) in fields)]
        # Nothing fanned out: leave it to the ordinary physics grouping rather
        # than dressing a lone block up as a scope decision.
        if len(members) <= len(blocks):
            continue
        absorbed |= {f["_i"] for f in members}
        named = ", ".join(sorted(fields))
        if kind == "extra":
            title = f"the spec models physics the reference does not have: {named}"
            note = (f"a scope decision, not {len(members)} mistakes — every material, "
                    "condition and query attached to those follows from it")
        else:
            title = f"the spec never models: {named}"
            note = ("a scope decision — the reference's materials, conditions and "
                    "queries for those fields have nowhere to attach")
        out.append(Cluster("scope", "physics", f"{kind}:{named}", members, title, note))
    return out, [f for f in fs if f["_i"] not in absorbed]


def singleton_title(f: dict) -> str:
    """A lone finding is its own clearest description."""
    what, kind = f["what"], f["kind"]
    if kind == "missing":
        return f"`{what}` missing — reference has {fmt(f['reference'])}"
    if kind == "extra":
        return f"`{what}` is not in the reference (spec has {fmt(f['candidate'])})"
    return f"`{what}` {fmt(f['candidate'])} vs {fmt(f['reference'])}"


def cluster(findings: list) -> list:
    """Findings -> causes, ranked by how much they cost.

    `_i` is stamped onto each finding: it is the finding's index in the original
    list, and it is what explain.py makes the analyst cite so a narrative claim
    can be traced back to a mechanical difference.
    """
    fs = []
    for i, f in enumerate(findings):
        f = dict(f)
        f["_i"] = i
        fs.append(f)

    # Scope first: it crosses sections, so it has to claim its findings before
    # the per-section rules split them up.
    out, fs = scope_causes(fs)

    groups: dict[tuple, list] = {}
    for f in fs:
        groups.setdefault(cause_key(f), []).append(f)

    loose = []
    for key, members in groups.items():
        if len(members) >= MIN_GROUP:
            title, note = title_for(key, members)
            out.append(Cluster(key[0], key[1] or members[0]["section"],
                               key[2], members, title, note))
        else:
            loose.extend(members)

    for f in loose:
        out.append(Cluster("single", f["section"], f["what"], [f],
                           singleton_title(f), f.get("note") or ""))

    # Heaviest cause first: that is the order a person should read them in, and
    # the order report.py and the explain prompt both inherit.
    out.sort(key=lambda c: (-c.weight, c.section, c.title))
    return out


def render(clusters: list, limit: int = 0) -> str:
    """Plain text, one line per cause. `limit` caps the lines, not the counts."""
    shown = clusters[:limit] if limit else clusters
    L = []
    for c in shown:
        L.append(f"  {len(c.findings):>2d}x  [{c.section}] {c.title}")
        if c.note:
            L.append(f"        {c.note}")
    if limit and len(clusters) > limit:
        L.append(f"  ... {len(clusters) - limit} further cause(s)")
    return "\n".join(L)


def main(argv=None) -> int:
    """cluster_findings.py SCORE.json -- the causes behind one scored candidate."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(main.__doc__.strip(), file=sys.stderr)
        return 2
    score = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    cs = cluster(score.get("findings", []))
    print(f"{len(score.get('findings', []))} finding(s) -> {len(cs)} cause(s)\n")
    print(render(cs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
