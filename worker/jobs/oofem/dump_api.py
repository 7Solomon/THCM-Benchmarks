#!/usr/bin/env python3
"""
dump_api.py -- regenerate prompts/oofem_api.md from the installed bindings.

    OOFEMPY_DIR=... python worker/jobs/oofem/dump_api.py

The first real run of this job failed because the model invented
`oofempy.concretedamage`, which does not exist, and then fell back to `isoLE`
without the keyword that one requires. Nothing in the prompt told it what the
bindings actually contain -- so it guessed, twice.

This writes that list out as a file the prompt includes. Two halves, on purpose:

    the NAMES come from dir(oofempy)   -- generated, always true of the binary
    the GROUPING comes from CATEGORIES -- hand-maintained, editable below

Regenerating keeps the curation and drops anything the bindings no longer
export. A symbol that is new, or that nobody has classified yet, lands in
"unclassified" rather than vanishing, so the list cannot silently go stale.

Annotate sparingly. A name the model can see is worth more than a description
that might be wrong: a confident false gloss on a material model is worse for
the benchmark than no gloss at all.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "prompts" / "oofem_api.md"
MAX_ENUM_MEMBERS = 40

# name -> optional one-line note. None means "listed, not described".
CATEGORIES: dict = {
    "Analysis types (engineering models)": {
        "linearStatic": "one linear solve",
        "nonlinearstatic": "incremental, path-following",
        "staticStructural": "quasi-static structural, Newton-Raphson",
        "transientTransport": "transient heat / mass transport",
        "dummyProblem": None,
    },
    "Problem setup": {
        "domain": None, "domainType": "enum: _2dPlaneStressMode, _1dTrussMode, ...",
        "problemMode": None, "createSet": "group elements/nodes for assignment",
        "node": None, "hangingnode": None,
        "init": None, "getClassFactory": None, "hasModule": None, "test": None,
    },
    "Cross sections": {
        "simpleCS": "structural; takes thick=, material=, set=",
        "simpleTransportCS": "transport counterpart",
        "rebarCS": None,
    },
    "Materials - solid mechanics": {
        "isoLE": "isotropic linear elastic. REQUIRES d, E, n AND tAlpha",
        "concreteDPM": "damage-plastic concrete",
        "concreteFcm": "fixed crack model",
        "concreteFcmViscoelastic": "fixed crack + viscoelasticity",
        "idm1": "isotropic damage",
        "mazarsmodel": "Mazars damage",
        "misesmat": "von Mises plasticity",
        "j2mat": "J2 plasticity",
        "steel1": None, "steelrelaxmat": "steel relaxation",
        "mps": "MPS creep (B3 family)",
        "ec2creepmat": "Eurocode 2 creep",
        "bondceb": None, "rebar3d": None,
    },
    "Materials - transport": {
        "isoHeat": "isotropic heat conduction; k, c, d",
    },
    "Boundary and initial conditions, loads": {
        "boundaryCondition": "prescribed value (Dirichlet)",
        "initialCondition": None,
        "nodalLoad": None,
        "constantEdgeLoad": None,
        "constantSurfaceLoad": None,
        "structEigenstrainLoad": None,
        "structTemperatureLoad": "imposed temperature as structural load",
    },
    "Time functions": {
        "constantFunction": None,
        "peakFunction": None,
        "piecewiseLinFunction": "t=(...), f_t=(...)",
    },
    "Export modules": {
        "vtkxml": "VTU files on disk",
        "vtkmemory": "in-memory export; pythonExport=1 to read values back",
        "homExport": "homogenised averages",
    },
    "Elements - 2D structural": {
        "planestress2d": None, "trPlaneStress2d": "linear triangle, plane stress",
        "trplanestress2d": None, "qplanestress2d": None, "qtrplstr": None,
        "quad1planestrain": None, "trplanestrain": None,
        "trplanestressrotallman": None, "trplanestressrotallman3d": None,
        "trplanestrrot": None, "linquad3dplanestress": None,
        "l4axisymm": "axisymmetric quad", "q4axisymm": None, "axisymm3d": None,
    },
    "Elements - 3D structural": {
        "lspace": "linear brick", "lspacebb": None, "ltrspace": "linear tetrahedron",
        "qspace": None, "qtrspace": None, "lwedge": None, "qwedge": None,
    },
    "Elements - beams, trusses, lattice": {
        "beam2d": None, "beam3d": None, "libeam3d": None,
        "truss1d": None, "truss2d": None, "truss3d": None, "bondlink3d": None,
        "lattice2d": None, "lattice3d": None,
        "lattice2dboundary": None, "lattice3dboundary": None,
        "latticelink3d": None, "latticelink3dboundary": None,
    },
    "Elements - transport": {
        "tr1ht": None, "quad1ht": None, "qquad1ht": None, "brick1ht": None,
        "qbrick1ht": None, "tetrah1ht": None, "line1ht": None,
        "traxisym1ht": None, "quadAxiSym1ht": None, "quadaxisym1ht": None,
        "tr1mt": None, "quad1mt": None, "qquad1mt": None, "line1mt": None,
        "qbrick1mt": None,
        "tr1hmt": None, "quad1hmt": None, "qquad1hmt": None, "brick1hmt": None,
        "qbrick1hmt": None, "line1hmt": None,
    },
}

SUFFIX_NOTE = """
Element name suffixes are the transport convention, and they are load-bearing:

    ...ht    heat transport
    ...mt    mass (moisture) transport
    ...hmt   coupled heat AND mass transport

so `quad1ht` is the heat-transport quadrilateral and `quad1hmt` its coupled
counterpart. Pick the element family that matches the fields you are solving.
"""


def main() -> int:
    sys.path.insert(0, os.environ.get("OOFEMPY_DIR", ""))
    try:
        import oofempy
    except ImportError as e:
        sys.exit(f"cannot import oofempy ({e}); set OOFEMPY_DIR")

    live = {n for n in dir(oofempy) if not n.startswith("_") and n[0].islower()}
    classified = {n for group in CATEGORIES.values() for n in group}

    lines = [
        "These are the names the installed `oofempy` actually exports. "
        "**Use only names from this list.** A name that is not here does not "
        "exist in this build, and calling it is an immediate AttributeError.",
        "",
        "Keyword arguments are not listed -- read the worked example below for "
        "the calling convention, and note that some materials require keywords "
        "the example does not show (`isoLE` needs `tAlpha`).",
        "",
    ]
    for title, group in CATEGORIES.items():
        present = [(n, note) for n, note in group.items() if n in live]
        if not present:
            continue
        lines.append(f"### {title}")
        lines.append("")
        for n, note in present:
            lines.append(f"- `{n}`" + (f" -- {note}" if note else ""))
        lines.append("")

    # The Uppercase half: classes, exception types and enums. The first run that
    # got past imports died on `oofempy.SetType`, which does not exist -- the
    # list above is only the lowercase factory functions, so nothing told the
    # model what the enums are actually called. Enum MEMBERS are spelled out
    # because that is what gets written at a call site (`DofIDItem.D_u`).
    ups = sorted(n for n in dir(oofempy)
                 if not n.startswith("_") and n[0].isupper())
    if ups:
        lines += ["### Classes, exceptions and enums", "",
                  "Enum members are listed where the binding exposes them. "
                  "Names not in this list do not exist.", ""]
        for n in ups:
            obj = getattr(oofempy, n)
            try:
                mem = [k for k in obj.__dict__.keys()
                       if not k.startswith("_") and k not in ("name", "value")]
            except AttributeError:
                mem = []
            if mem:
                shown = ", ".join(f"`{n}.{k}`" for k in mem[:MAX_ENUM_MEMBERS])
                more = (f" ... and {len(mem) - MAX_ENUM_MEMBERS} more"
                        if len(mem) > MAX_ENUM_MEMBERS else "")
                lines.append(f"- `{n}`: {shown}{more}")
            else:
                lines.append(f"- `{n}`")
        lines.append("")

    extra = sorted(live - classified)
    if extra:
        lines += ["### Unclassified", "",
                  "Exported by the bindings, not yet grouped:", ""]
        lines += [f"- `{n}`" for n in extra]
        lines.append("")

    lines.append(SUFFIX_NOTE.strip())
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    stale = sorted(classified - live)
    print(f"wrote {OUT}: {len(live & classified)} classified, "
          f"{len(extra)} unclassified")
    if stale:
        print(f"  in CATEGORIES but NOT in this build (dropped): "
              f"{', '.join(stale)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
