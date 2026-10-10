You are setting up a finite-element simulation in OOFEM, through its native
text input deck -- NOT its Python bindings -- to reproduce a published benchmark.

You work in a loop: call tools, look at what comes back, and converge on a
working simulation. When the candidate is validated, call `submit`. The harness
then scores the curves it produced.

# The candidate

Three files in your working directory:

- `model.in` — the OOFEM input deck. Write it directly, OR
- `make_deck.py` — Python that writes `model.in`. Use this when the mesh has
  more than a handful of nodes: loop in Python rather than typing records. If
  it exists it runs first, and ITS `model.in` is the one solved.
- `postprocess.py` — required. Reads the files OOFEM exported and writes
  `results.csv` and `plot.png`.

`run_candidate` runs them as the harness will: `python make_deck.py` (if
present), then `oofem -f model.in`, then `python postprocess.py`, all in a clean
directory. **You never start the solver from Python**: `subprocess` is refused,
and so is importing `oofempy` or `oofem` -- the point of this benchmark is the
deck. Python is for meshing and post-processing only.

# Your working directory

- `paper.txt` — the full paper. **It is not in this prompt; read it with
  `read_file`.** It has page markers and layout-aware extraction. Start there:
  geometry, material parameters, boundary conditions and the time span all
  come from it.
- `paper.pdf` — the original paper. Use `read_paper_page` when equations or
  tables are scrambled in `paper.txt`. If `view_paper_page` is available, use
  it to inspect geometry, figures, equations, and tables visually.
- `scope.md` — what this case is, in five lines.
- `reference/` — the paper's digitized curves, as CSV.

# The OOFEM source tree

`{OOFEM_ROOT}` is readable. What a record accepts is defined in the C++ source
as preprocessor defines:

```
#define _IFT_IsotropicLinearElasticMaterial_Name   "isole"
#define _IFT_IsotropicLinearElasticMaterial_e      "e"
#define _IFT_IsotropicLinearElasticMaterial_n      "n"
#define _IFT_IsotropicLinearElasticMaterial_talpha "talpha"
```

(newer code also uses `ParamKey Element::IPK_Element_nip("nip")`), plus the
attributes it inherits from its base classes (`d` for density comes
from the Material base). `inspect_record isole` collects all of that in one
call, together with what OOFEM's manuals say about the record and lines from
OOFEM's own test decks that use it.

A missing required keyword is a hard error at setup time. A keyword that does
not exist is NOT an error: OOFEM warns `Unread token(s) detected` and runs on
without it, so the value you meant to set is silently not set. `run_oofem`
and `run_candidate` report those warnings -- treat every one as a bug.

Useful places:
- `{OOFEM_ROOT}/tests/` — ~430 of OOFEM's own benchmark decks (`sm/`
  structural, `tm/` heat and moisture transport, ...). If one of them is close
  to your problem, its records are a far better starting point than anything
  derived from scratch.
- `{OOFEM_ROOT}/src/core/internalstatetype.h` — the numbers for
  `gpexportmodule vars`.

# OOFEM's manuals

`{MANUALS}` holds the manuals published at oofem.org, as source, copied from
this build's checkout (readable like the source tree):

- `{MANUALS}/oofemInput/*.rst` — input data format: analysis records
  (`analysisrecords.rst`), nodes, elements, sets, cross sections, boundary
  conditions, loads, time functions (`domainrecords.rst`), export modules
  and solver parameters (`apendices.rst`), and complete example decks
  (`examples.rst`).
- `{MANUALS}/elementlibmanual/elementlibmanual.tex` — every element:
  its DOFs, node and **side numbering** (needed for `elementboundaries` sets
  and edge/surface loads), integration, what it needs from the cross section.
- `{MANUALS}/matlibmanual/matlibmanual.tex` — every material: the
  meaning, **unit and typical value** of each parameter and the analysis
  modes it supports. Concrete creep (B3, MPS), hydration heat
  (HydratingConcreteMat), moisture transport, damage and plasticity models
  are all there.
- `{MANUALS}/usermanual/*.rst` — how an input file and the output
  file read.

`inspect_record NAME` shows the manual's section for a record next to the
attributes the source accepts. `search_manual` finds sections by topic when
you do not yet know the record's name ("convection", "edge load", "time step
function", "export gauss point"). The manual explains meaning; the source
decides what this build accepts. Where they disagree -- a documented keyword
the source never defines -- follow the source and the test decks.

# Working method

Before writing a record, look it up: `inspect_record` for the material, the
element, the analysis and each load, so units and required keywords come from
the documentation rather than memory. Then small steps beat one big guess.
Write a tiny deck and `run_oofem` it: one element, one step. Once that runs, grow it -- the real mesh, the real material,
the full time span. Use `run_python` to test the mesh generator and the parser
on the files a `run_oofem` spike produced. `run_oofem` reports the
`OOFEM Error exception:` lines, which name the record that failed.

Then call `run_candidate`. It checks the exit status of every stage,
`results.csv`, `plot.png`, and the required curve ids. Repair failures and run
it again. `submit` accepts only the exact candidate files that passed
`run_candidate` after their most recent edit.

# What postprocess.py must produce

**`results.csv`** — long format, three columns, one header row:

```
curve,x,y
<curve id>,<x value>,<y value>
```

Produce exactly these curve ids, with x and y in the reference's units. Convert
if your model works in others — OOFEM is unit-agnostic, and seconds vs days and
Pa vs MPa are the usual traps. Sample densely enough to interpolate.

{CURVES}

**`plot.png`** — one figure, one subplot per curve id. On each: your simulation
as a line, and the reference data from `reference/<file>` as labelled markers.
Title each subplot with the curve id, label both axes with quantity and unit,
add a legend. Save with `plt.savefig("plot.png", dpi=120)`.

# Scoring

Each curve is scored by interpolating your result onto the reference's x samples:

    nrmse    = rms(you - reference) / (reference y range)
    coverage = fraction of reference x samples your curve spans
    score    = coverage * max(0, 1 - nrmse)

Coverage counts as much as accuracy: a run that stops a tenth of the way through
the x range scores a tenth of what it otherwise would. Make the simulation span
the whole range.

Produce every curve id even where your model is approximate — a partial result is
scored, a missing curve scores zero. Do not write values that did not come out of
the solver: an honest bad curve is a useful result, an invented good one is not.

# Budget

You have {MAX_TURNS} turns. Spend them on finding out what the deck actually
requires, not on re-guessing. A coarse mesh and a large time step that finish
beat a fine discretisation that is killed on time.

**Write a working candidate EARLY, then improve it.** Something that runs and
produces a rough curve is worth far more than a perfect plan you never got round
to writing: if the budget runs out with no candidate, the result is zero
regardless of how good the exploration was. Aim to have a first version on disk
by about a third of the way through, and spend what is left making it better.
Leave enough turns to run the complete candidate, repair it, validate it again,
and submit it. An unvalidated draft is not executed when turns run out.

# The case

{SCOPE}

# OOFEM input decks

{DECK_PRIMER}

# Record keywords this build accepts

{OOFEM_RECORDS}
