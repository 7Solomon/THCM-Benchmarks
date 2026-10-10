## The input deck (`model.in`)

A plain-text file of records, one per line (end a line with `\` to continue it).
Lines starting with `#` are comments. `@include "mesh.in"` pastes in another
file. Keywords are case-insensitive; attribute order within a record is free.

The ORDER of records is fixed:

```
model.out                                   1. output file name
Short description of the job                2. job description (free text)
NonLinearStatic nsteps 20 ... nmodules 2    3. analysis record
gnuplot tstep_all reactionforces 1          4. exactly `nmodules` export-module
dm tstep_all                                   records, right after the analysis
domain 2dPlaneStress                        5. domain type (sets default DOFs per node)
OutputManager tstep_all dofman_all element_all
ndofman 4 nelem 1 ncrosssect 1 nmat 1 nbc 2 nic 0 nltf 2 nset 2
node 1 coords 3 0. 0. 0.                    6. nodes        (ndofman of them)
...
planestress2d 1 nodes 4 1 2 3 4 crossSect 1 mat 1   7. elements (nelem)
Set 1 elements 1 1                          8. sets         (nset)
SimpleCS 1 thick 0.1 material 1 set 1       9. cross sections (ncrosssect)
isole 1 d 2400. E 30e9 n 0.2 tAlpha 1e-5   10. materials    (nmat)
BoundaryCondition 1 loadTimeFunction 1 dofs 2 1 2 values 2 0. 0. set 2
                                           11. BCs and loads (nbc)
                                           12. initial conditions (nic)
ConstantFunction 1 f(t) 1.0                13. time functions (nltf)
```

The counts on the size record must match the records that follow EXACTLY, and
every record is numbered 1..n within its kind. A mismatch is a hard error.

Value types: integer array `nodes 3 1 4 23` and real array `coords 3 1. 2. 3.`
both lead with their LENGTH. Sets: `Set 2 nodes 3 1 5 9`,
`Set 3 elements 2 1 2`, `Set 4 elementboundaries 4 1 2 7 2` (pairs:
element, local side), also `elementranges {(1 100)}`, `noderanges`,
`allElements`, `allNodes`.

Boundary conditions are applied to sets and scaled in time by a time function:
`BoundaryCondition 2 loadTimeFunction 2 dofs 1 2 values 1 -1.e-3 set 3`
prescribes DOF 2 (v) = -1e-3 × f₂(t) on set 3. `PiecewiseLinFunction 2
npoints 2 t 2 0. 20. f(t) 2 0. 20.` ramps it linearly over 20 time units.

## How the harness runs it

`oofem -f model.in` in the working directory. On a bad deck it aborts, and
the message you need is the line starting `OOFEM Error exception:` -- for
example `Missing keyword "d" on input 17  Record: "hydratingconcretemat 1 ..."`
or `Couldn't create element: tr9zz`. A misspelled or unknown ATTRIBUTE is
not an error: OOFEM prints `Warning:Unread token(s) detected in the following
record ... [bogus][7]` and runs on without it.

## Getting curves out: export modules

The analysis record's `nmodules N` announces N export-module records, which
follow it immediately. `tstep_all` exports every step; `tstep_step 10` every
10th. These write simple text files into the working directory, which
postprocess.py then reads:

- **`dm tstep_all`** (optional `dmlist 3 1 5 9`: only these nodes) writes one
  file per step, `<output file>.m<N>.<step>.dm`, e.g. `model.out.m1.7.dm`.
  OOFEM assigns `<N>` itself and it is NOT reliably the module's position in
  the list -- glob for `model.out.m*.*.dm` and sort by the step number (the
  last number before the extension), numerically. Format:
  ```
  % DofMan DataFile Version 1.1
  % Output for time 64800.000000
  % Primary fields
  1 1 0 0  20
  2 2.2 0 0  20
  ```
  Each row: node number, x, y, z, then that node's DOF values in DOF order
  (temperature for heat transfer; u v (w) for structures).

- **`gpexportmodule tstep_all vars 2 37 56 ncoords 2`** writes
  `<output file>.m<N>.<step>.gp` (glob it the same way), one row per
  integration point:
  element, -1, integration rule, point number, volume, then `ncoords` and the
  coordinates, then for each requested variable its size followed by its
  values. `vars` are InternalStateType numbers (list below). Header lines
  start with `%`; the second one carries the time: `%# output for time 72000`.

- **`gnuplot tstep_all reactionforces 1`** (structural analyses) writes
  `ReactionForceGnuplotBC<bc#><X|Y>.dat`: for each prescribed-displacement BC,
  two columns `#u Fx` -- the prescribed value and the summed reaction force,
  one row per step. That is a load-displacement curve directly.

- The `.out` file (the OutputManager) has everything in verbose text, including
  `R E A C T I O N S  O U T P U T` blocks per step. Parse it only as a fallback.

Reading the time: the analysis time of step k is k × deltaT for transport
problems; for NonLinearStatic in displacement control the "time" is the load
multiplier driving your time functions. Keep the unit bookkeeping (s vs h vs
days, Pa vs MPa) explicit in postprocess.py.
