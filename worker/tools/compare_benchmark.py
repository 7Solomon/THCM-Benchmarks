#!/usr/bin/env python3
"""
compare_benchmark.py -- what did a model get WRONG when it authored this spec?

    compare_benchmark.py CAND --case faria-2006-slab/thermal
    compare_benchmark.py CAND --ref dataset/json/<case>.json [--json] [--tol 0.01]

This is a diagnostic, not a grade. The output is a list of concrete
misinterpretations -- "u_ref 15 degC where the paper says 10", "cube where the
reference is a rectangle", "declared thermal_strain but never wrote the material
section" -- because that is what tells you whether a model can read a paper.
The numbers exist to rank runs; the findings are the point.

## Sections are not equally important

A spec's `results.plots` block is presentation. Its `materials` block IS the
physics. Scoring them alike produces a number that moves for the wrong reasons,
so each section carries a weight and a strictness:

    STRICT   physics, materials, boundary_conditions, initial_conditions
             Every key compared, every value compared, every difference reported.
             These decide whether the spec models the same problem at all.

    NORMAL   geometry, time_stepping, metadata
             Shape and the numbers that matter. A different mesh size is a
             modelling choice; a different DIMENSION is not.

    LOOSE    results.queries
             Matched on what is measured (field/type), not on ids or count. A
             model may reasonably probe more or fewer points.

    IGNORED  results.plots, results.reference_curves
             Presentation, and digitized data no model can produce.

## Names are free, roles are not

A model may call a material `concrete` or `sv40`, and a boundary `top` or
`surface_top`. It may not get the VALUE wrong. So materials are aligned by
section shape, boundary conditions by (field, type, component), and only then are
values compared -- strictly. Every alignment is printed, so a finding can be
checked rather than trusted.

Exit 0 when the comparison ran. Exit 2 if the candidate could not be read.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # worker/tools
WORKER = HERE.parent
REPO = WORKER.parent
SCHEMA = WORKER / "schemas" / "benchmark.schema.json"
DATASET = REPO / "dataset"

sys.path.insert(0, str(HERE))
import check_benchmark as CB                    # noqa: E402  (path set above)

# Rules about a file NEXT TO the spec, not about the spec. A model authoring from
# a paper cannot produce a digitized CSV, so these fire on every candidate and
# carry no signal. --strict-files puts them back.
SIBLING_FILE_RULES = {"reference-missing", "bc-table-missing"}

STRICT, NORMAL, LOOSE, IGNORED = "strict", "normal", "loose", "ignored"

# weight is what the section contributes to the headline score.
SECTIONS = {
    "physics":              (STRICT, 3.0),
    "materials":            (STRICT, 3.0),
    "boundary_conditions":  (STRICT, 3.0),
    "initial_conditions":   (STRICT, 2.0),
    "geometry":             (NORMAL, 1.5),
    "time_stepping":        (NORMAL, 1.5),
    "metadata":             (NORMAL, 1.0),
    "results.queries":      (LOOSE,  0.5),
    "results.plots":        (IGNORED, 0.0),
    "results.reference_curves": (IGNORED, 0.0),
}

# Ratios that mean "wrong unit", not "wrong physics". A transcription slip and a
# misreading of the paper need different responses, so they are reported apart.
UNIT_RATIOS = {1e-9: "1e-9", 1e-6: "1e-6 (Pa<->MPa?)", 1e-3: "1e-3 (J<->kJ? m<->mm?)",
               1e3: "1e3 (kJ<->J? mm<->m?)", 1e6: "1e6 (MPa<->Pa?)",
               1e9: "1e9 (GPa<->Pa?)", 1 / 86400: "1/86400 (d<->s?)",
               86400: "86400 (s<->d?)", 1 / 3600: "1/3600 (h<->s?)",
               3600: "3600 (s<->h?)", 1 / 24: "1/24 (d<->h?)", 24: "24 (h<->d?)"}

# Kinds of finding, worst first. The order is the report order.
KINDS = ("wrong_model", "missing", "extra", "wrong_value", "wrong_unit", "type_differs")


class Finding:
    __slots__ = ("section", "kind", "what", "candidate", "reference", "note")

    def __init__(self, section, kind, what, candidate=None, reference=None, note=""):
        self.section, self.kind, self.what = section, kind, what
        self.candidate, self.reference, self.note = candidate, reference, note

    def as_dict(self):
        return {"section": self.section, "kind": self.kind, "what": self.what,
                "candidate": self.candidate, "reference": self.reference,
                "note": self.note}


def is_comment(k) -> bool:
    return isinstance(k, str) and k.startswith("_")


def clean(d: dict) -> dict:
    return {k: v for k, v in (d or {}).items() if not is_comment(k)}


def num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def rel_error(a, b) -> float:
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(b), abs(a), 1e-12)


def unit_hint(cand, ref):
    if not cand or not ref:
        return None
    ratio = cand / ref
    for target, label in UNIT_RATIOS.items():
        if abs(ratio / target - 1.0) < 0.02:
            return label
    return None


def fmt(v) -> str:
    if v is None:
        return "—"
    if num(v):
        return f"{v:.6g}"
    return str(v)


def compare_values(section, prefix, cand: dict, ref: dict, tol: float,
                   out: list, model_keys=("model",)) -> None:
    """Key-by-key over two aligned dicts. Strict: every key in either side is
    accounted for, and every shared numeric value is checked against tol."""
    cand, ref = clean(cand), clean(ref)
    for key in sorted(set(cand) | set(ref)):
        cv, rv = cand.get(key), ref.get(key)
        where = f"{prefix}.{key}" if prefix else key
        if key in ref and key not in cand:
            out.append(Finding(section, "missing", where, None, rv))
        elif key in cand and key not in ref:
            out.append(Finding(section, "extra", where, cv, None))
        elif isinstance(cv, dict) and isinstance(rv, dict):
            compare_values(section, where, cv, rv, tol, out, model_keys)
        elif num(cv) and num(rv):
            if rel_error(cv, rv) > tol:
                hint = unit_hint(cv, rv)
                out.append(Finding(
                    section, "wrong_unit" if hint else "wrong_value", where, cv, rv,
                    hint or f"{(cv - rv) / rv:+.1%}" if rv else "vs 0"))
        elif cv != rv:
            kind = "wrong_model" if key in model_keys else "type_differs"
            out.append(Finding(section, kind, where, cv, rv))


# ── physics ──────────────────────────────────────────────────────────────────

# The eight coupling keys the backend matches BY EXACT NAME. Everything else in
# `physics` is a field block whose top-level key is arbitrary.
COUPLING_KEYS = {
    "thermal_strain", "autogenous_shrinkage", "b3_creep", "power_law_creep",
    "drying_shrinkage", "thermal_aging", "heat_moisture_transport",
    "hydro_mechanical",
}


def physics_key(name: str, blk: dict):
    """What the BACKEND resolves this block to.

    A field block's top-level key is documentation -- CREATE.md: "Top-level keys
    are arbitrary block names, EXCEPT coupling keys, which are matched by exact
    name". The backend keys a field on `dependent_variable.name`. Comparing field
    blocks by their label therefore scores a naming convention, not the physics:
    `mechanics` vs `solid_mechanics` vs `displacement_field` all declare the same
    field and all run identically, but produced one `missing` AND one `extra`
    each. That cost 20 of 41 candidates in the 2026-08-27 run real score on the
    heaviest-weighted section.
    """
    blk = blk or {}
    if blk.get("type") == "coupling" or name in COUPLING_KEYS:
        return ("coupling", name)
    dv = (blk.get("dependent_variable") or {}).get("name")
    # No dependent_variable is itself the error; fall back to the label so the
    # finding still names something the author can find.
    return ("field", dv or name)


def cmp_physics(cand, ref, tol, out):
    c, r = clean(cand.get("physics", {})), clean(ref.get("physics", {}))
    cmap = {physics_key(n, b): (n, b) for n, b in c.items()}
    rmap = {physics_key(n, b): (n, b) for n, b in r.items()}

    def label(key, name):
        # Report the reference's own wording, but keep the resolved key visible
        # when the two differ, so "missing mechanics" is not mystifying when the
        # candidate called it something else.
        return name if key[1] == name else f"{name} ({key[0]}:{key[1]})"

    for key in sorted(set(cmap) | set(rmap), key=str):
        if key in rmap and key not in cmap:
            rname, rblk = rmap[key]
            out.append(Finding("physics", "missing", label(key, rname), None,
                               (rblk or {}).get("type", "field"),
                               "the spec does not model this at all"))
        elif key in cmap and key not in rmap:
            cname, cblk = cmap[key]
            out.append(Finding("physics", "extra", label(key, cname),
                               (cblk or {}).get("type", "field"), None,
                               "physics the paper's case does not have"))
        else:
            cname, cblk = cmap[key]
            rname, rblk = rmap[key]
            ct, rt = (cblk or {}).get("type"), (rblk or {}).get("type")
            if ct != rt:
                out.append(Finding("physics", "type_differs", f"{rname}.type", ct, rt))
            cs = (cblk or {}).get("source_field")
            rs = (rblk or {}).get("source_field")
            if rt == "coupling" and cs != rs:
                out.append(Finding("physics", "type_differs",
                                   f"{rname}.source_field", cs, rs))


# ── materials ────────────────────────────────────────────────────────────────

def mat_shape(mat: dict) -> frozenset:
    facets = set()
    for section, body in clean(mat).items():
        facets.add(f"section:{section}")
        if isinstance(body, dict) and isinstance(body.get("model"), str):
            facets.add(f"{section}.model={body['model']}")
    return frozenset(facets)


def align_materials(cand: dict, ref: dict):
    """By shape overlap, never by name -- a model is free to call a material
    anything. Greedy on Jaccard, and the pairing is reported."""
    cs = {n: mat_shape(m) for n, m in clean(cand).items() if isinstance(m, dict)}
    rs = {n: mat_shape(m) for n, m in clean(ref).items() if isinstance(m, dict)}
    pairs, used = [], set()
    for rname in sorted(rs):
        best, best_score = None, -1.0
        for cname in sorted(cs):
            if cname in used:
                continue
            score = len(cs[cname] & rs[rname]) / (len(cs[cname] | rs[rname]) or 1)
            if score > best_score:
                best, best_score = cname, score
        if best is not None:
            used.add(best)
        pairs.append((best, rname))
    pairs += [(c, None) for c in sorted(cs) if c not in used]
    return pairs


def cmp_materials(cand, ref, tol, out):
    pairs = align_materials(cand.get("materials") or {}, ref.get("materials") or {})
    for cname, rname in pairs:
        if rname is None:
            out.append(Finding("materials", "extra", cname, cname, None,
                               "a material the reference does not have"))
            continue
        if cname is None:
            out.append(Finding("materials", "missing", rname, None, rname,
                               "a material the spec never defines"))
            continue
        cm, rm = clean(cand["materials"][cname]), clean(ref["materials"][rname])
        label = cname if cname == rname else f"{cname}~{rname}"
        for sec in sorted(set(cm) | set(rm)):
            where = f"{label}.{sec}"
            if sec in rm and sec not in cm:
                out.append(Finding("materials", "missing", where, None,
                                   "section", "the physics has no constitutive data"))
            elif sec in cm and sec not in rm:
                out.append(Finding("materials", "extra", where, "section", None))
            elif isinstance(cm[sec], dict) and isinstance(rm[sec], dict):
                compare_values("materials", where, cm[sec], rm[sec], tol, out)
    return pairs


# ── conditions ───────────────────────────────────────────────────────────────

def bc_role(b: dict) -> tuple:
    """The identity of a boundary condition that a model is NOT free to change.
    `location` is excluded on purpose: the group name is the model's own."""
    return (str(b.get("field")), str(b.get("type")), str(b.get("component") or "-"))


def bc_payload(b: dict) -> dict:
    return {k: v for k, v in clean(b).items()
            if k not in ("field", "type", "component", "location")}


def cmp_conditions(section, cand_list, ref_list, tol, out, role=bc_role):
    """Align by role, then compare payloads strictly. Several conditions can share
    a role (four Robin faces), so within a role they are paired in order after
    sorting by payload -- deterministic, and the pairing is what gets reported."""
    def bucket(items):
        d = {}
        for b in items or []:
            if isinstance(b, dict):
                d.setdefault(role(b), []).append(b)
        for v in d.values():
            v.sort(key=lambda b: json.dumps(bc_payload(b), sort_keys=True, default=str))
        return d

    cb, rb = bucket(cand_list), bucket(ref_list)
    for key in sorted(set(cb) | set(rb)):
        label = "/".join(key)
        cs, rs = cb.get(key, []), rb.get(key, [])
        for i in range(max(len(cs), len(rs))):
            if i >= len(cs):
                out.append(Finding(section, "missing", f"{label}[{i}]", None,
                                   json_short(bc_payload(rs[i])),
                                   f"the paper's {label} condition is not in the spec"))
            elif i >= len(rs):
                out.append(Finding(section, "extra", f"{label}[{i}]",
                                   json_short(bc_payload(cs[i])), None,
                                   "a condition the reference does not have"))
            else:
                compare_values(section, f"{label}[{i}]",
                               bc_payload(cs[i]), bc_payload(rs[i]), tol, out)


def json_short(d, n=90) -> str:
    s = json.dumps(d, sort_keys=True, default=str)
    return s if len(s) <= n else s[:n - 1] + "…"


def ic_role(b: dict) -> tuple:
    return (str(b.get("field")),)


# ── geometry / time / metadata ───────────────────────────────────────────────

def cmp_geometry(cand, ref, tol, out):
    cg, rg = clean(cand.get("geometry", {})), clean(ref.get("geometry", {}))
    ce = [e.get("type") for e in cg.get("entities", []) or [] if isinstance(e, dict)]
    re_ = [e.get("type") for e in rg.get("entities", []) or [] if isinstance(e, dict)]
    if sorted(ce) != sorted(re_):
        out.append(Finding("geometry", "type_differs", "entities",
                           ", ".join(sorted(ce)) or "—", ", ".join(sorted(re_)) or "—",
                           "a different solid shape is a different problem"))
    cn = {g.get("name") for g in cg.get("physical_groups", []) or [] if isinstance(g, dict)}
    rn = {g.get("name") for g in rg.get("physical_groups", []) or [] if isinstance(g, dict)}
    if len(cn) != len(rn):
        out.append(Finding("geometry", "type_differs", "physical_groups (count)",
                           len(cn), len(rn), "names are free; the count is a shape"))
    cm, rm = clean(cg.get("mesh", {})), clean(rg.get("mesh", {}))
    for key in ("order", "recombine"):
        # Only when BOTH state it. These carry schema defaults, and a candidate
        # that omits one has not made a wrong choice -- it has made no choice.
        if key in cm and key in rm and cm[key] != rm[key]:
            out.append(Finding("geometry", "type_differs", f"mesh.{key}",
                               cm[key], rm[key]))


def cmp_time(cand, ref, tol, out):
    ct, rt = clean(cand.get("time_stepping", {})), clean(ref.get("time_stepping", {}))
    for key in ("analysis_type", "t_start", "t_end", "linear_solver"):
        cv, rv = ct.get(key), rt.get(key)
        if num(cv) and num(rv):
            if rel_error(cv, rv) > tol:
                hint = unit_hint(cv, rv)
                out.append(Finding("time_stepping",
                                   "wrong_unit" if hint else "wrong_value", key, cv, rv,
                                   hint or (f"{(cv - rv) / rv:+.1%}" if rv else "")))
        elif cv != rv and not (cv is None and rv is None):
            out.append(Finding("time_stepping", "type_differs", key, cv, rv))
    cs = (ct.get("dt_strategy") or {}).get("type") if isinstance(ct.get("dt_strategy"), dict) else None
    rs = (rt.get("dt_strategy") or {}).get("type") if isinstance(rt.get("dt_strategy"), dict) else None
    if cs != rs:
        out.append(Finding("time_stepping", "type_differs", "dt_strategy.type",
                           cs or "fixed dt", rs or "fixed dt"))


def norm_class(v):
    """`physics_class` is free text: 'thermo_mechanical', 'thermo-mechanical' and
    'Thermo Mechanical' are the same answer. Punctuation and case are not."""
    if not isinstance(v, str):
        return v
    return "-".join(sorted(t for t in v.lower().replace("_", "-").replace(" ", "-")
                           .replace("(", "").replace(")", "").split("-") if t))


def cmp_metadata(cand, ref, tol, out):
    """Only the two fields that say what the model IS.

    `validation_method` is deliberately not compared: across the corpus it ranges
    from the token 'analytical_comparison' to a full sentence naming figures and
    equations. Diffing prose produces a finding on almost every candidate and
    tells you nothing about whether the paper was read correctly.
    """
    cm, rm = clean(cand.get("metadata", {})), clean(ref.get("metadata", {}))
    if cm.get("dimension") != rm.get("dimension"):
        out.append(Finding("metadata", "type_differs", "dimension",
                           cm.get("dimension"), rm.get("dimension"),
                           "a 3D model of a 2D case costs far more and answers "
                           "differently"))
    if norm_class(cm.get("physics_class")) != norm_class(rm.get("physics_class")):
        out.append(Finding("metadata", "type_differs", "physics_class",
                           cm.get("physics_class"), rm.get("physics_class")))


def cmp_queries(cand, ref, tol, out):
    """LOOSE: what is measured, not how many or by what id."""
    def kinds(doc):
        s = set()
        for q in (doc.get("results") or {}).get("queries", []) or []:
            if isinstance(q, dict):
                s.add(f"{q.get('type')}:{q.get('field') or q.get('component') or '-'}")
        return s
    c, r = kinds(cand), kinds(ref)
    for k in sorted(r - c):
        out.append(Finding("results.queries", "missing", k, None, k,
                           "the spec never measures this"))
    for k in sorted(c - r):
        out.append(Finding("results.queries", "extra", k, k, None))


# ── scoring ──────────────────────────────────────────────────────────────────

def ref_facts(ref: dict) -> dict:
    """How many comparable facts each section of the REFERENCE holds. The
    denominator of every section score, so a section is scored against how much
    it actually says, not against how much the candidate wrote."""
    def count(node):
        if isinstance(node, dict):
            return sum(count(v) for k, v in node.items() if not is_comment(k)) or 1
        if isinstance(node, list):
            return sum(count(v) for v in node) or 1
        return 1
    n = {}
    for section in SECTIONS:
        node = ref
        for part in section.split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        n[section] = count(node) if node else 0
    n["physics"] = max(len(clean(ref.get("physics", {}))), 1)
    n["metadata"] = 2      # dimension, physics_class
    n["geometry"] = 5
    n["time_stepping"] = 5
    return n


def score_sections(findings: list, facts: dict) -> dict:
    per = {}
    for section, (strictness, weight) in SECTIONS.items():
        if strictness == IGNORED:
            continue
        fs = [f for f in findings if f.section == section]
        denom = max(facts.get(section, 0), 1)
        # A section can be arbitrarily wrong; clamp so one catastrophic section
        # cannot drag the headline below zero and hide the others.
        per[section] = {
            "strictness": strictness, "weight": weight,
            "findings": len(fs), "facts": denom,
            "score": round(max(0.0, 1.0 - len(fs) / denom), 4),
            "applicable": facts.get(section, 0) > 0 or len(fs) > 0,
        }
    live = {k: v for k, v in per.items() if v["applicable"]}
    total_w = sum(v["weight"] for v in live.values()) or 1
    overall = sum(v["score"] * v["weight"] for v in live.values()) / total_w
    return {"sections": per, "weighted": round(overall, 4)}


# ── tier 1: does it validate at all? ─────────────────────────────────────────

def validation(cand_path: Path, strict_files: bool) -> dict:
    try:
        findings = CB.run(cand_path, str(SCHEMA))
    except json.JSONDecodeError as exc:
        return {"parses": False, "error": f"invalid JSON: {exc}", "score": 0.0}
    if not strict_files:
        findings = [f for f in findings if f.rule not in SIBLING_FILE_RULES]
    errs = [f for f in findings if f.level == CB.ERROR]
    schema_errs = [f for f in errs if f.rule == "schema"]
    return {
        "parses": True,
        "schema_errors": len(schema_errs),
        "rule_errors": len(errs) - len(schema_errs),
        "warnings": len(findings) - len(errs),
        "errors": [{"rule": f.rule, "where": f.where, "what": f.what} for f in errs][:30],
        # Binary: WORKER.md Phase 2 treats every ERROR as a hard stop, so
        # "nearly valid" is not a state the pipeline recognises.
        "score": 1.0 if not errs else 0.0,
    }


# ── driver ───────────────────────────────────────────────────────────────────

def compare(cand_path: Path, ref_path: Path, tol: float, strict_files: bool) -> dict:
    val = validation(cand_path, strict_files)
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    base = {"candidate": str(cand_path), "reference": str(ref_path), "tolerance": tol,
            "validation": val}
    if not val["parses"]:
        return {**base, "findings": [], "scores": None, "alignment": [],
                "accuracy": 0.0, "overall": 0.0}

    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    out: list = []
    cmp_physics(cand, ref, tol, out)
    pairs = cmp_materials(cand, ref, tol, out)
    cmp_conditions("boundary_conditions", cand.get("boundary_conditions"),
                   ref.get("boundary_conditions"), tol, out)
    cmp_conditions("initial_conditions", cand.get("initial_conditions"),
                   ref.get("initial_conditions"), tol, out, role=ic_role)
    cmp_geometry(cand, ref, tol, out)
    cmp_time(cand, ref, tol, out)
    cmp_metadata(cand, ref, tol, out)
    cmp_queries(cand, ref, tol, out)

    scores = score_sections(out, ref_facts(ref))
    accuracy = scores["weighted"]
    return {
        **base,
        "alignment": [{"candidate": c, "reference": r} for c, r in pairs],
        "findings": [f.as_dict() for f in out],
        "finding_counts": {k: sum(f.kind == k for f in out) for k in KINDS},
        "scores": scores,
        "accuracy": accuracy,
        # Validity gates nothing by itself -- a spec can be perfectly valid and
        # describe the wrong experiment -- so it is reported beside accuracy,
        # not folded into it.
        "overall": round((val["score"] + accuracy) / 2, 4),
    }


def resolve_ref(case: str) -> Path:
    p = DATASET / "json" / f"{case.replace('/', '__')}.json"
    if not p.exists():
        sys.exit(f"no dataset spec for case '{case}' (looked for {p});\n"
                 f"run: python worker/tools/build_dataset.py")
    return p


SYMBOL = {"missing": "-", "extra": "+", "wrong_value": "~", "wrong_unit": "!",
          "wrong_model": "M", "type_differs": "~"}


def render(res: dict, limit: int) -> str:
    v = res["validation"]
    L = [f"=== {Path(res['candidate']).name}  vs  {Path(res['reference']).name}", ""]
    if not v["parses"]:
        return "\n".join(L + [f"DOES NOT PARSE — {v['error']}", ""])

    L.append(f"VALIDATES  {'yes' if v['score'] else 'NO'}   "
             f"schema errors {v['schema_errors']}, rule errors {v['rule_errors']}, "
             f"warnings {v['warnings']}")
    for e in v["errors"][:6]:
        L.append(f"           {e['rule']:24s} {e['where']}")
    if len(v["errors"]) > 6:
        L.append(f"           ... {len(v['errors']) - 6} more")

    by_section: dict = {}
    for f in res["findings"]:
        by_section.setdefault(f["section"], []).append(f)

    for group, title in ((STRICT, "STRICT — does it model the same problem?"),
                         (NORMAL, "NORMAL — discretisation and framing"),
                         (LOOSE, "LOOSE — what it measures")):
        rows = [(s, cfg) for s, cfg in SECTIONS.items() if cfg[0] == group]
        L += ["", title]
        for section, _ in rows:
            sc = res["scores"]["sections"].get(section, {})
            if not sc.get("applicable"):
                continue
            fs = by_section.get(section, [])
            head = f"  {section:22s} {sc['score']:.3f}"
            L.append(head + ("   clean" if not fs else f"   {len(fs)} finding(s)"))
            if section == "materials" and res["alignment"]:
                for a in res["alignment"]:
                    if a["candidate"] != a["reference"]:
                        L.append(f"      aligned  {a['candidate'] or '—'} "
                                 f"-> {a['reference'] or '—'}")
            shown = sorted(fs, key=lambda f: (KINDS.index(f["kind"]), f["what"]))
            for f in shown[:limit]:
                sym = SYMBOL.get(f["kind"], "?")
                line = f"      {sym} {f['kind']:12s} {f['what']:38s}"
                if f["kind"] in ("wrong_value", "wrong_unit", "wrong_model",
                                 "type_differs"):
                    line += f" {fmt(f['candidate']):>14s} vs {fmt(f['reference']):>14s}"
                elif f["kind"] == "missing":
                    line += f" {'—':>14s} vs {fmt(f['reference']):>14s}"
                else:
                    line += f" {fmt(f['candidate']):>14s} vs {'—':>14s}"
                if f["note"]:
                    line += f"   {f['note']}"
                L.append(line)
            if len(shown) > limit:
                L.append(f"      ... {len(shown) - limit} more (--limit to see them)")

    ignored = [s for s, c in SECTIONS.items() if c[0] == IGNORED]
    L += ["", f"IGNORED    {', '.join(ignored)}"]

    counts = ", ".join(f"{k} {n}" for k, n in res["finding_counts"].items() if n)
    L += ["", f"{len(res['findings'])} finding(s): {counts or 'none'}",
          f"ACCURACY {res['accuracy']:.3f} (section-weighted)   "
          f"VALIDATES {v['score']:.0f}   OVERALL {res['overall']:.3f}", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("--ref", type=Path, help="reference benchmark.json")
    ap.add_argument("--case", help="dataset case name; resolves --ref for you")
    ap.add_argument("--tol", type=float, default=0.01,
                    help="relative tolerance for a numeric match (default 1%%)")
    ap.add_argument("--limit", type=int, default=12,
                    help="findings shown per section (default 12)")
    ap.add_argument("--strict-files", action="store_true",
                    help="also apply the sibling-file rules")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args(argv)

    if not a.ref and not a.case:
        ap.error("need --ref or --case")
    ref = a.ref or resolve_ref(a.case)
    if not a.candidate.exists():
        print(f"no such candidate: {a.candidate}", file=sys.stderr)
        return 2
    res = compare(a.candidate, ref, a.tol, a.strict_files)
    print(json.dumps(res, indent=2) if a.json else render(res, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
