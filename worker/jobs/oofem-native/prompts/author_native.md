You are given a published benchmark paper. Set up and run the corresponding
simulation in OOFEM -- through its native input deck, NOT its Python bindings --
extract the curves the benchmark is judged on, and plot them against the paper's
digitized reference data.

# What you hand in

Up to three files, each in its own fenced block whose info string is the file
name, and nothing else -- no explanation before or after:

````
```model.in
... the OOFEM input deck ...
```

```make_deck.py
... OPTIONAL: Python that writes model.in ...
```

```postprocess.py
... Python that reads OOFEM's output and writes results.csv + plot.png ...
```
````

- `postprocess.py` is required.
- Give `model.in` directly, or `make_deck.py` that writes it (use the generator
  when the mesh has more than a handful of nodes -- loop in Python, do not
  type out hundreds of node records). If both are given, the generator runs
  and its `model.in` is the one solved.

# How it is run

1. `python make_deck.py` (if given)
2. `oofem -f model.in` -- run BY THE HARNESS. You never start the solver.
3. `python postprocess.py`

All three in the same, already-set working directory; use relative paths.

- `reference/` is there, holding the paper's digitized curves as CSV.
- Python has `numpy`, `matplotlib` (use the `Agg` backend), `scipy`, `math`,
  `csv`, `glob`, `pathlib`, and `gmsh` / `meshio` for meshing.
- Your Python must NOT import `oofempy` or `oofem` -- the point of this
  benchmark is the input deck. Also refused: `subprocess`, `socket`, network
  modules. No network access; do not install anything.
- Wall-clock limit for all three steps together: {TIMEOUT} seconds. Choose a
  mesh and time step that finish inside it. A coarse model that completes
  beats a fine one that is killed.

Ask OOFEM for the output you need via export modules in the deck (see below),
and make postprocess.py read exactly those files.

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

# OOFEM input decks

{DECK_PRIMER}

# Record keywords this build accepts

{OOFEM_RECORDS}

# Worked decks

These come from the OOFEM checkout whose executable will run your deck. Follow
their syntax; do not guess at keywords from other versions.

{OOFEM_EXAMPLES}

# The paper

{PAPER}

# Now answer

Reply with the fenced blocks described under "What you hand in" and nothing
else: a `model.in` block (or a `make_deck.py` block that writes it) AND a
`postprocess.py` block. A reply without `postprocess.py` is not run at all.
