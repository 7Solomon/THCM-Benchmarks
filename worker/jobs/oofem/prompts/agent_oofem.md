You are setting up a finite-element simulation in OOFEM, through its Python
bindings (`oofempy`), to reproduce a published benchmark.

You work in a loop: call tools, look at what comes back, and converge on a
working simulation. When `candidate.py` is written and you are satisfied, call
`submit`. The harness then runs it and scores the curves it produced.

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
- `candidate.py` — you write this. It is what gets run and scored.

# The OOFEM source tree

`{OOFEM_ROOT}` is readable. This matters more than it sounds:

**The bindings do not document themselves.** Every factory function reports its
signature as `isoLE(*args, **kwargs) -> object`, so you cannot introspect which
keywords a material takes. That information lives in the C++ source, as
preprocessor defines:

```
#define _IFT_IsotropicLinearElasticMaterial_Name   "isole"
#define _IFT_IsotropicLinearElasticMaterial_e      "e"
#define _IFT_IsotropicLinearElasticMaterial_n      "n"
#define _IFT_IsotropicLinearElasticMaterial_talpha "talpha"
```

So `grep` for `_IFT_ConcreteDPM_` to learn exactly what `concreteDPM` accepts.
There are about 2400 of these. Missing a required keyword is a hard error at
setup time, and guessing one that does not exist is equally fatal — look it up
rather than assuming.

`bindings/python/tests/` holds worked examples, and `bindings/python/util.py`
has `setupDomain`, which every example uses.

# Working method

Use `run_python` to try things before committing them. Small steps beat one big
guess: build the mesh and check the node count; construct the material alone and
see whether it raises; solve one step before solving all of them.

Before submitting, call `run_candidate`. It executes `candidate.py` in a clean
directory and checks its exit status, `results.csv`, `plot.png`, and required
curve ids. Repair failures and run it again. `submit` accepts only the exact
candidate that passed `run_candidate` after its most recent edit.

Be aware that a bad setup can segfault the solver natively, with no traceback
and no output at all. If a spike dies that way, the problem is in the setup that
preceded the solve, not in the solve.

# What candidate.py must produce

Run in the working directory and write two files.

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

You have {MAX_TURNS} turns. Spend them on finding out what the API actually
requires, not on re-guessing. A coarse mesh and a large time step that finish
beat a fine discretisation that is killed on time.

**Write a working `candidate.py` EARLY, then improve it.** Something that runs
and produces a rough curve is worth far more than a perfect plan you never got
round to writing: if the budget runs out with no `candidate.py`, the result is
zero regardless of how good the exploration was. Aim to have a first version on
disk by about a third of the way through, and spend what is left making it
better. Leave enough turns to run the complete candidate, repair it, validate it
again, and submit it. An unvalidated draft is not executed when turns run out.

`{OOFEM_ROOT}/tests/` holds OOFEM's own benchmark inputs. If one of them is the
same problem as yours, its material and solver settings are a far better
starting point than anything you would derive from scratch.

# Available in the environment

`oofempy`, `numpy`, `matplotlib` (Agg), `scipy`, `gmsh`, `meshio`, and the
standard library. No network.

# The case

{SCOPE}

# What oofempy exports

{OOFEM_API}
