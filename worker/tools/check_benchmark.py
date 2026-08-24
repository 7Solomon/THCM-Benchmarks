#!/usr/bin/env python3
"""
check_benchmark.py -- the mechanical half of the authoring rules.

The schema (SKILLs/benchmark.schema.json) enforces SHAPE: which keys exist, arity,
enums. It cannot express cross-key constraints, unit conventions, or backend
restrictions that span two sections. Those used to live only as prose in CREATE.md,
where every one of them was a coin flip weighted by how well the author attended to
a line 1300 lines in.

This script carries them instead. Every rule here was a prose trap; the prose now
keeps only what needs judgement (is this boundary a Robin or a Dirichlet?).

    python worker/tools/check_benchmark.py <benchmark.json> [--schema SKILLs/benchmark.schema.json]

Exit 0 = clean (warnings allowed), 1 = at least one ERROR, 2 = could not read input.
Each finding prints as

    ERROR  <rule-id>  <json path>: <what is wrong>  -> <what to do>

Adding a rule: write a function taking (B, ctx) and yielding Finding, and decorate
it with @rule. Nothing else.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── framework ────────────────────────────────────────────────────────────────

ERROR, WARN = "ERROR", "WARN"


@dataclass
class Finding:
    level: str
    rule: str
    where: str
    what: str
    fix: str = ""


_RULES: list = []


def rule(fn):
    _RULES.append(fn)
    return fn


def err(r, where, what, fix=""):
    return Finding(ERROR, r, where, what, fix)


def warn(r, where, what, fix=""):
    return Finding(WARN, r, where, what, fix)


@dataclass
class Ctx:
    """Everything a rule might need, resolved once."""
    path: Path
    root: Path
    dim: int = 2
    groups: set = field(default_factory=set)
    points: set = field(default_factory=set)
    locations: set = field(default_factory=set)
    materials: dict = field(default_factory=dict)
    couplings: set = field(default_factory=set)
    active: dict = field(default_factory=dict)       # domain -> set(fields)
    all_active: set = field(default_factory=set)
    query_ids: set = field(default_factory=set)
    ref_ids: set = field(default_factory=set)
    hygrothermal: bool = False
    poromech: bool = False


# Canonical coupling keys. A `physics` block whose type is "coupling" must use one
# of these -- an off-name makes (temperature, displacement) ambiguous between
# thermal_strain and thermal_aging, which the backend rejects.
COUPLINGS = {
    "thermal_strain", "autogenous_shrinkage", "b3_creep", "power_law_creep",
    "drying_shrinkage", "thermal_aging", "heat_moisture_transport",
    "hydro_mechanical",
}
MONOLITHIC = {"heat_moisture_transport", "hydro_mechanical"}
NO_SOURCE = {"b3_creep", "power_law_creep", "autogenous_shrinkage"}
# mps-hosted sections: power_law_creep is a DIFFERENT chain and excludes all of them
MPS_SECTIONS = {"b3_creep", "autogenous_shrinkage", "drying_shrinkage", "thermal_aging"}
REDUCERS = {"avg", "mean", "max", "min", "sum", "norm"}
STRESS_COMPONENTS = {f"{p}{c}" for p in "se"
                     for c in ("xx", "yy", "zz", "xy", "xz", "yz")}


def build_ctx(B: dict, path: Path) -> Ctx:
    c = Ctx(path=path, root=path.parent)
    c.dim = int(B.get("metadata", {}).get("dimension", 2) or 2)
    geo = B.get("geometry", {}) or {}
    c.groups = {g.get("name") for g in geo.get("physical_groups", []) or []}
    c.points = {p.get("name") for p in geo.get("points", []) or []}
    c.locations = c.groups | c.points
    c.materials = B.get("materials", {}) or {}
    c.couplings = {k for k, v in (B.get("physics", {}) or {}).items()
                   if isinstance(v, dict) and v.get("type") == "coupling"}
    for name, d in ((B.get("assignment", {}) or {}).get("domains", {}) or {}).items():
        c.active[name] = set(d.get("active_fields", []) or [])
        c.all_active |= c.active[name]
    res = B.get("results", {}) or {}
    c.query_ids = {q.get("id") for q in res.get("queries", []) or []}
    c.ref_ids = {r.get("id") for r in res.get("reference_curves", []) or []}
    c.hygrothermal = "heat_moisture_transport" in c.couplings
    c.poromech = "hydro_mechanical" in c.couplings
    return c


# ── reference integrity ──────────────────────────────────────────────────────

@rule
def r_locations_exist(B, c):
    for kind in ("boundary_conditions", "initial_conditions"):
        for i, e in enumerate(B.get(kind, []) or []):
            loc = e.get("location")
            # "all" is the legacy every-node default and is special-cased for ICs
            # only (BCs/factory.py:156); it is NOT a group name.
            if kind == "initial_conditions" and loc in (None, "all"):
                continue
            if loc is not None and loc not in c.locations:
                yield err("location-unknown", f"{kind}[{i}].location",
                          f"'{loc}' is not a physical_group or a geometry point",
                          "names are case-sensitive and matched byte-for-byte")


@rule
def r_active_field_has_material(B, c):
    for dom, fields in c.active.items():
        matname = ((B.get("assignment", {}) or {}).get("domains", {})
                   .get(dom, {}).get("material"))
        mat = c.materials.get(matname)
        if mat is None:
            yield err("material-missing", f"assignment.domains.{dom}.material",
                      f"'{matname}' is not in materials")
            continue
        for f in fields:
            if f not in mat:
                yield err("field-no-material", f"materials.{matname}",
                          f"domain '{dom}' activates '{f}' but the material has "
                          f"no '{f}' section")


@rule
def r_coupling_material_pairing(B, c):
    """A coupling without its section (or the reverse) is a hard backend error --
    it refuses to run 'elastically by accident'."""
    for cp in sorted(c.couplings):
        if cp in MONOLITHIC:
            continue
        if not any(cp in m for m in c.materials.values()):
            yield err("coupling-no-section", f"physics.{cp}",
                      f"declared but no material carries a '{cp}' section")
    for mname, m in c.materials.items():
        for sec in sorted(set(m) & (COUPLINGS - MONOLITHIC)):
            if sec not in c.couplings:
                yield err("section-no-coupling", f"materials.{mname}.{sec}",
                          f"section present but '{sec}' is not declared in physics")


@rule
def r_coupling_names_and_direction(B, c):
    for k, v in (B.get("physics", {}) or {}).items():
        if not (isinstance(v, dict) and v.get("type") == "coupling"):
            continue
        if k not in COUPLINGS:
            yield err("coupling-noncanonical", f"physics.{k}",
                      f"'{k}' is not a canonical coupling key",
                      f"use one of: {', '.join(sorted(COUPLINGS))}")
            continue
        if k in MONOLITHIC and ("target_field" in v or "source_field" in v):
            yield err("monolithic-has-direction", f"physics.{k}",
                      "monolithic couplings solve their fields together and take "
                      "neither source_field nor target_field")
        if k in NO_SOURCE and "source_field" in v:
            yield err("coupling-spurious-source", f"physics.{k}.source_field",
                      f"'{k}' reads its own material state and takes no source_field")


@rule
def r_query_and_plot_refs(B, c):
    res = B.get("results", {}) or {}
    for i, q in enumerate(res.get("queries", []) or []):
        for dep in q.get("depends_on", []) or []:
            if dep not in c.query_ids:
                yield err("depends-unknown", f"results.queries[{i}].depends_on",
                          f"'{dep}' is not a query id")
        if q.get("type") == "formula":
            for tok in set(re.findall(r"[A-Za-z_]\w*", q.get("formula", ""))):
                if tok in c.query_ids or tok in {"abs", "min", "max", "exp", "log",
                                                 "sqrt", "pow", "e", "pi"}:
                    continue
                yield warn("formula-unknown-name", f"results.queries[{i}].formula",
                           f"'{tok}' is neither a query id nor a known function")
    for i, p in enumerate(res.get("plots", []) or []):
        for j, cu in enumerate(p.get("curves", []) or []):
            at = f"results.plots[{i}].curves[{j}]"
            if cu.get("source") == "reference":
                if cu.get("ref_id") not in c.ref_ids:
                    yield err("refid-unknown", at,
                              f"ref_id '{cu.get('ref_id')}' is not a reference_curve")
            for axis in ("x", "y"):
                k = (cu.get(axis) or {}).get("key")
                if k is not None and k not in c.query_ids:
                    yield err("plotkey-unknown", f"{at}.{axis}.key",
                              f"'{k}' is not a query id")
            if cu.get("source") == "query" and "axis" not in cu:
                yield warn("query-curve-no-axis", at,
                           "a source:'query' curve without 'axis' defaults to 0",
                           "the query's own axis does NOT propagate; set it here too")


@rule
def r_files_exist(B, c):
    res = B.get("results", {}) or {}
    for i, r in enumerate(res.get("reference_curves", []) or []):
        f = r.get("file")
        if f and not (c.root / f).exists():
            yield err("reference-missing", f"results.reference_curves[{i}].file",
                      f"'{f}' does not exist",
                      "a missing reference is dropped silently from the plot")
    for i, e in enumerate(B.get("boundary_conditions", []) or []):
        for key in ("value", "u_ref", "alpha"):
            v = e.get(key)
            if isinstance(v, dict) and "file" in v:
                if not (c.root / v["file"]).exists():
                    yield err("bc-table-missing", f"boundary_conditions[{i}].{key}.file",
                              f"'{v['file']}' does not exist")


# ── units and conventions ────────────────────────────────────────────────────

def _temperature_entries(B):
    for i, e in enumerate(B.get("initial_conditions", []) or []):
        if e.get("field") == "temperature" and isinstance(e.get("value"), (int, float)):
            yield f"initial_conditions[{i}].value", e["value"]
    for i, e in enumerate(B.get("boundary_conditions", []) or []):
        if e.get("field") != "temperature":
            continue
        for key in ("u_ref", "value"):
            v = e.get(key)
            if isinstance(v, (int, float)):
                yield f"boundary_conditions[{i}].{key}", v


@rule
def r_temperature_convention(B, c):
    """degC everywhere EXCEPT a heat_moisture_transport domain, which is Kelvin.
    Neither mistake produces a clean error on its own."""
    for where, v in _temperature_entries(B):
        if c.hygrothermal and v < 200:
            yield err("hygro-temp-not-kelvin", where,
                      f"{v} looks like degC, but a heat_moisture_transport domain's "
                      "temperature field is KELVIN", "add 273.15")
        elif not c.hygrothermal and v >= 200:
            yield err("temp-not-celsius", where,
                      f"{v} looks like Kelvin; the temperature field is degC",
                      "subtract 273.15")


@rule
def r_tref_is_kelvin(B, c):
    for mname, m in c.materials.items():
        doh = m.get("degree_of_hydration")
        if not isinstance(doh, dict) or "T_ref" not in doh:
            continue
        t = doh["T_ref"]
        if isinstance(t, (int, float)) and t < 200:
            yield err("tref-not-kelvin",
                      f"materials.{mname}.degree_of_hydration.T_ref",
                      f"{t} looks like degC. T_ref is the ONE Kelvin temperature "
                      "in the file",
                      f"use {t + 273.15:g}; a degC value makes the Arrhenius factor "
                      "~1e94 and the solve never converges")


@rule
def r_no_kelvin_formula(B, c):
    if c.hygrothermal:
        return
    for i, q in enumerate((B.get("results", {}) or {}).get("queries", []) or []):
        if q.get("type") != "formula":
            continue
        if re.search(r"-\s*273\.15|273\.15\s*-", q.get("formula", "")):
            yield err("kelvin-formula", f"results.queries[{i}].formula",
                      "the temperature field is already degC",
                      "plot the field query directly; this shifts the plot ~253 degC")


# ── boundary-condition restrictions ──────────────────────────────────────────

@rule
def r_bc_restrictions(B, c):
    for i, e in enumerate(B.get("boundary_conditions", []) or []):
        at = f"boundary_conditions[{i}]"
        typ, fld = e.get("type"), e.get("field")

        for key in ("value", "u_ref", "alpha"):
            v = e.get(key)
            if isinstance(v, dict) and ("time_table" in v or "file" in v) \
                    and "time_function" in e:
                yield err("table-and-timefunction", f"{at}.{key}",
                          "both a table and a time_function on one entry",
                          "they are two spellings of one mechanism; keep the table")

        if typ == "dirichlet" and "start_time" in e:
            yield err("dirichlet-start-time", f"{at}.start_time",
                      "start_time on a Dirichlet is rejected",
                      "use a Robin film with a leading zero-alpha segment")

        if typ == "robin":
            a = e.get("alpha")
            if isinstance(a, dict) and ("time_table" in a or "file" in a):
                if a.get("interp", "step") != "step":
                    yield err("robin-alpha-interp", f"{at}.alpha.interp",
                              "a time-varying Robin alpha must use interp 'step'")
                if "start_time" in e:
                    yield err("robin-alpha-start-time", at,
                              "a table alpha cannot be combined with start_time",
                              "model the delay as a leading zero-alpha segment")
            if fld == "displacement":
                if not isinstance(a, (int, float)):
                    yield err("spring-alpha-not-const", f"{at}.alpha",
                              "a mechanical Robin becomes discrete springs and needs "
                              "a constant numeric alpha")
                if float(e.get("u_ref", 0.0) or 0.0) != 0.0:
                    yield err("spring-uref-nonzero", f"{at}.u_ref",
                              "a mechanical Robin requires u_ref exactly 0")
                if "start_time" in e:
                    yield err("spring-start-time", at,
                              "nodalspring elements are permanent; start_time is rejected")

        if typ == "neumann" and c.hygrothermal:
            yield err("neumann-on-hygrothermal", at,
                      "a neumann on a heat_moisture_transport domain is rejected",
                      "the transport element picks the DOF by index, so a humidity "
                      "flux would land on temperature")


# ── query restrictions ───────────────────────────────────────────────────────

@rule
def r_query_restrictions(B, c):
    for i, q in enumerate((B.get("results", {}) or {}).get("queries", []) or []):
        at = f"results.queries[{i}]"
        typ = q.get("type")

        if typ in ("stress", "strain"):
            yield err("comsol-only-query", f"{at}.type",
                      f"type '{typ}' is COMSOL-only and raises on every step here",
                      'use {"type":"field","field":"stress","component":"syy"}')

        if typ == "field":
            fld, comp = q.get("field"), q.get("component")
            if fld in ("stress", "strain", "mechanical_stress", "total_strain"):
                if comp is None:
                    yield err("tensor-no-component", at,
                              f"a '{fld}' query needs a component")
                elif comp not in STRESS_COMPONENTS:
                    yield err("tensor-component-unprefixed", f"{at}.component",
                              f"'{comp}' is not a valid component",
                              "components carry a prefix: sxx syy szz sxy sxz syz / "
                              "exx eyy ...")
            if fld == "degree_of_hydration" and comp is not None:
                yield err("doh-has-component", f"{at}.component",
                          "degree_of_hydration is a bare element scalar and is "
                          "rejected with a component")
            if fld == "degree_of_hydration" and not any(
                    "degree_of_hydration" in m for m in c.materials.values()):
                yield err("doh-query-no-section", at,
                          "no material has a degree_of_hydration section; the query "
                          "raises 'Unknown field' every step")
            if q.get("at") and q["at"] not in c.locations:
                yield err("query-at-unknown", f"{at}.at",
                          f"'{q['at']}' is not a physical_group or a geometry point")

        red = q.get("reduce")
        if red is not None and red not in REDUCERS:
            yield warn("reduce-unknown", f"{at}.reduce",
                       f"'{red}' silently falls back to mean",
                       f"valid: {'|'.join(sorted(REDUCERS))}")

        if typ == "line_profile":
            for k in ("start", "end"):
                v = q.get(k)
                if isinstance(v, (list, tuple)) and len(v) != c.dim:
                    yield err("lineprofile-arity", f"{at}.{k}",
                              f"{len(v)} coordinates on a {c.dim}D mesh",
                              "line_profile INVERTS the geometry rule: give exactly "
                              "the mesh dimension")


# ── geometry ─────────────────────────────────────────────────────────────────

@rule
def r_geometry_arity(B, c):
    geo = B.get("geometry", {}) or {}
    for i, e in enumerate(geo.get("entities", []) or []):
        if "transforms" in e:
            yield err("transforms-typo", f"geometry.entities[{i}].transforms",
                      "the key is 'transform' (singular); 'transforms' is silently "
                      "ignored and the geometry is meshed untransformed")
        p = e.get("parameters", {}) or {}
        for k in ("origin", "base_center", "axis", "center", "start", "end"):
            v = p.get(k)
            if isinstance(v, (list, tuple)) and len(v) != 3:
                yield err("coord-arity", f"geometry.entities[{i}].parameters.{k}",
                          f"{len(v)} values; every geometry coordinate is 3 "
                          "[x, y, z], z = 0.0 in 2D")
    for i, g in enumerate(geo.get("physical_groups", []) or []):
        sel = g.get("selection", {}) or {}
        boxes = ([sel["box"]] if "box" in sel else []) + list(sel.get("boxes", []))
        for b in boxes:
            if isinstance(b, (list, tuple)) and len(b) != 6:
                yield err("box-arity", f"geometry.physical_groups[{i}].selection",
                          f"a box has {len(b)} values; every box is 6 "
                          "[xmin,ymin,zmin,xmax,ymax,zmax], also in 2D")
        if g.get("dimension") == c.dim - 1 and not boxes and not g.get("entity_ids"):
            yield warn("group-no-selection", f"geometry.physical_groups[{i}]",
                       f"'{g.get('name')}' has neither a selection nor entity_ids")


# ── per-physics gates ────────────────────────────────────────────────────────

@rule
def r_hydration_needs_temperature(B, c):
    for dom, fields in c.active.items():
        if "degree_of_hydration" in fields and "temperature" not in fields:
            yield err("hydration-no-temperature", f"assignment.domains.{dom}",
                      "degree_of_hydration requires temperature active on the "
                      "same domain")


@rule
def r_thermal_aging(B, c):
    for mname, m in c.materials.items():
        ta = m.get("thermal_aging")
        if ta is None:
            continue
        if "b3_creep" not in m:
            yield err("aging-no-chain", f"materials.{mname}.thermal_aging",
                      "thermal_aging ages the mps creep chain; without a b3_creep "
                      "section there is nothing to age")
        for k in ("kTm", "mus"):
            if k not in ta:
                yield err("aging-missing-key", f"materials.{mname}.thermal_aging.{k}",
                          f"'{k}' is required for coupledanalysistype 3")


@rule
def r_power_law_creep(B, c):
    for mname, m in c.materials.items():
        plc = m.get("power_law_creep")
        if plc is None:
            continue
        disp = m.get("displacement", {}) or {}
        nu, n = disp.get("nu"), plc.get("n")
        if nu is not None and n is not None and float(nu) != float(n):
            yield err("plc-nu-ne-n", f"materials.{mname}",
                      f"displacement.nu={nu} but power_law_creep.n={n}",
                      "oofem reads ONE key 'n' as both Poisson's ratio and the "
                      "power-law exponent, so they cannot differ")
        clash = sorted(set(m) & MPS_SECTIONS)
        if clash:
            yield err("plc-with-mps", f"materials.{mname}",
                      f"power_law_creep excludes {', '.join(clash)}",
                      "power_law_creep is an ALTERNATIVE chain to b3_creep, not an "
                      "addition")
        if (m.get("displacement", {}) or {}).get("model") == "damage_rankine":
            yield err("plc-with-damage", f"materials.{mname}.displacement.model",
                      "oofem has no damage variant of doublepowerlaw")


@rule
def r_creep_damage_pairing(B, c):
    """mpsDamMat (damage_rankine + b3_creep) is the ONLY class that is both a
    rheologic chain and a damage model."""
    for mname, m in c.materials.items():
        model = (m.get("displacement", {}) or {}).get("model")
        if model in ("cdpm2", "cdpm1", "damage_mazars") and (set(m) & MPS_SECTIONS):
            yield err("damage-rejects-creep", f"materials.{mname}",
                      f"'{model}' rejects a creep section",
                      "creep + cracking is damage_rankine + b3_creep, full stop")


@rule
def r_hydro_mechanical(B, c):
    if not c.poromech:
        return
    if (B.get("geometry", {}) or {}).get("mesh", {}).get("recombine") is not True:
        yield err("hm-needs-quads", "geometry.mesh.recombine",
                  "upquad11 is the only u-p element reachable here",
                  "set mesh.recombine: true")
    for dom, fields in c.active.items():
        if "pore_pressure" in fields and "displacement" not in fields:
            yield err("pp-no-displacement", f"assignment.domains.{dom}",
                      "pore_pressure requires displacement active on the same domain")
    for mname, m in c.materials.items():
        bad = sorted(set(m) & (MPS_SECTIONS | {"thermal_strain", "degree_of_hydration"}))
        if bad:
            yield err("hm-with-eigenstrain", f"materials.{mname}",
                      f"hydro_mechanical rejects {', '.join(bad)}",
                      "the Biot material is not a StructuralMaterial and has no "
                      "eigenstrain path")
        disp = m.get("displacement", {}) or {}
        for k in ("plane_stress", "thickness"):
            if k in disp:
                yield err("hm-plane-knob", f"materials.{mname}.displacement.{k}",
                          "hydro_mechanical is plane strain, unit depth, no knob")
        pp = m.get("pore_pressure")
        if not isinstance(pp, dict):
            continue
        perm = [k for k in ("mobility", "intrinsic_permeability",
                            "hydraulic_conductivity") if k in pp]
        if len(perm) != 1:
            yield err("pp-permeability-route", f"materials.{mname}.pore_pressure",
                      f"exactly one permeability route required, found {perm or 'none'}",
                      "mobility | intrinsic_permeability+fluid_viscosity | "
                      "hydraulic_conductivity+fluid_unit_weight")
        stor = [k for k in ("storage", "porosity") if k in pp]
        if len(stor) != 1:
            yield err("pp-storage-route", f"materials.{mname}.pore_pressure",
                      f"exactly one storage route required, found {stor or 'none'}")
        if "biot_alpha" not in pp:
            yield err("pp-no-biot-alpha", f"materials.{mname}.pore_pressure",
                      "biot_alpha is required")


@rule
def r_drying_shrinkage_href(B, c):
    ic = {e.get("location"): e.get("value")
          for e in (B.get("initial_conditions", []) or [])
          if e.get("field") == "humidity"}
    for mname, m in c.materials.items():
        ds = m.get("drying_shrinkage")
        if not isinstance(ds, dict) or "h_ref" not in ds:
            continue
        if ic and all(abs(float(ds["h_ref"]) - float(v)) > 1e-9 for v in ic.values()):
            yield err("href-ne-ic", f"materials.{mname}.drying_shrinkage.h_ref",
                      f"h_ref={ds['h_ref']} does not match any humidity IC "
                      f"({sorted(ic.values())})")
        if "b3_creep" not in m:
            yield err("drying-no-chain", f"materials.{mname}.drying_shrinkage",
                      "drying_shrinkage rides on the mps chain and needs b3_creep")


@rule
def r_domains_agree(B, c):
    sets = {frozenset(v) for v in c.active.values()}
    if len(sets) > 1:
        yield err("domains-differ", "assignment.domains",
                  "all domains must declare the same active_fields",
                  f"found {[sorted(s) for s in sets]}")


@rule
def r_plane_state(B, c):
    for mname, m in c.materials.items():
        disp = m.get("displacement")
        if not isinstance(disp, dict):
            continue
        if disp.get("plane_stress") is True and "thickness" not in disp:
            yield err("plane-stress-no-thickness", f"materials.{mname}.displacement",
                      "plane_stress without thickness silently uses 1.0 m",
                      "every reported force scales by 1/b -- state the real thickness")
        if disp.get("plane_stress") is not True and "thickness" in disp:
            yield warn("thickness-without-plane-stress",
                       f"materials.{mname}.displacement.thickness",
                       "thickness is only meaningful with plane_stress: true")


@rule
def r_dimension(B, c):
    d = (B.get("metadata", {}) or {}).get("dimension")
    if d not in (2, 3):
        yield err("dimension-missing", "metadata.dimension",
                  f"must be 2 or 3, got {d!r}",
                  "it is compared against the mesh at load time")


# ── driver ───────────────────────────────────────────────────────────────────

def schema_errors(B, schema_path):
    try:
        from jsonschema import Draft202012Validator as V
    except ImportError:
        return [warn("schema-skipped", "-", "jsonschema is not installed")]
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    out = []
    for e in sorted(V(schema).iter_errors(B), key=lambda e: list(e.absolute_path)):
        out.append(err("schema", ".".join(str(p) for p in e.absolute_path) or "(root)",
                       e.message))
    return out


def run(path: Path, schema_path=None):
    B = json.loads(path.read_text(encoding="utf-8"))
    findings = list(schema_errors(B, schema_path)) if schema_path else []
    c = build_ctx(B, path)
    for fn in _RULES:
        try:
            findings.extend(fn(B, c) or [])
        except Exception as exc:                       # a rule must never mask a file
            findings.append(warn("rule-crashed", fn.__name__, f"{type(exc).__name__}: {exc}"))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("benchmark", nargs="+")
    ap.add_argument("--schema", default=None,
                    help="also run JSON-Schema validation")
    ap.add_argument("--quiet", action="store_true", help="errors only")
    a = ap.parse_args(argv)

    worst = 0
    for p in a.benchmark:
        p = Path(p)
        if p.is_dir():
            p = p / "benchmark.json"
        if not p.exists():
            print(f"[--] {p}: no such file", file=sys.stderr)
            worst = max(worst, 2)
            continue
        try:
            fs = run(p, a.schema)
        except json.JSONDecodeError as e:
            print(f"[--] {p}: invalid JSON: {e}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        if a.quiet:
            fs = [f for f in fs if f.level == ERROR]
        n_err = sum(f.level == ERROR for f in fs)
        head = "OK" if n_err == 0 else f"{n_err} ERROR"
        print(f"\n=== {p}  [{head}, {len(fs) - n_err} warn] ===")
        for f in fs:
            print(f"{f.level:5s} {f.rule:28s} {f.where}")
            print(f"      {f.what}")
            if f.fix:
                print(f"      -> {f.fix}")
        if n_err:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
