You are given a published benchmark paper. Write ONE self-contained Python
script that sets up and runs the corresponding simulation in OOFEM through its
Python bindings (`oofempy`), extracts the curves the benchmark is judged on, and
plots them against the paper's digitized reference data.

Answer with exactly one ```python code block and nothing else. No explanation
before or after it. The block is written to a file and executed as-is.

# What the script must do

1. Build the mesh. Nothing is given to you -- derive the geometry from the
   paper and mesh it yourself, in the script.
2. Set up the OOFEM problem: materials, cross sections, boundary and initial
   conditions, time stepping.
3. Solve it.
4. Write `results.csv` and `plot.png` into the current working directory.

# The run

- Working directory is already set; write with relative paths.
- `reference/` is already there, holding the paper's digitized curves as CSV.
- Available: `oofempy`, `numpy`, `matplotlib` (use the `Agg` backend), `math`,
  `csv`, `pathlib`, and `gmsh` / `meshio` if you want them for meshing.
- No network access. Do not install anything, spawn subprocesses, or read
  outside the working directory.
- Wall-clock limit: {TIMEOUT} seconds. Choose a mesh and a time step that finish
  inside it. A coarse model that completes beats a fine one that is killed.

# results.csv -- exact format

Long format, three columns, one header row:

```
curve,x,y
<curve id>,<x value>,<y value>
```

One row per sample point. Long format because a case mixes x-axes -- some curves
are against time, others against position -- so a wide table cannot hold them.

Produce exactly these curve ids, with x in the stated x-quantity and y in the
stated y-quantity. **The units are the reference's units. Convert if your model
works in others (OOFEM is unit-agnostic; seconds vs days and Pa vs MPa are the
usual traps).** Sample each curve densely enough to interpolate -- the score
interpolates your curve onto the reference's own x samples.

{CURVES}

# plot.png

One figure, one subplot per curve id. On each subplot draw:

- your simulation result as a line,
- the reference data from `reference/<file>` as markers, labelled.

Title each subplot with the curve id, label both axes with the quantity and its
unit, and add a legend. Save with `plt.savefig("plot.png", dpi=120)`.

# Scoring

Each curve is scored by interpolating your result onto the reference's x samples:

    nrmse    = rms(you - reference) / (reference y range)
    coverage = fraction of reference x samples your curve spans
    score    = coverage * max(0, 1 - nrmse)

Covering the whole x range matters as much as being accurate on part of it. A
run that stops a tenth of the way through scores a tenth of what it otherwise
would, so make the simulation span the reference's full x range.

# If you cannot model something

Model what you can and still produce every curve id, using your best available
approximation. A partial result is scored; a missing curve scores zero. Do not
fabricate values that did not come out of the solver -- an honest bad curve is a
useful result, an invented good one is not.

# The case

{SCOPE}

# OOFEM Python bindings -- what exists

{OOFEM_API}

# OOFEM Python bindings -- worked example

These files come from the oofem checkout this script will run against. Follow
their API exactly; do not guess at names from other versions.

{OOFEM_EXAMPLES}

# The paper

{PAPER}
