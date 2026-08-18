# hm-excavation — one-shot generation bug report

Every problem below was present in the FIRST generated `benchmark.json`.
Read this before authoring the next chorded / boolean-cut geometry.

---

## PROBLEM 1 — FATAL: all 8 `wall_chord_*` groups selected 0 entities

```
Selection box for 'wall_chord_0' (dim=1): found 0 entities
⚠️  Warning: No entities found for physical group 'wall_chord_0'
   ... x8 ...
ValueError: Location 'wall_chord_0' not found in boundary_sets or point_nodes
```

**CAUSE.** `selection.box` is `gmsh.model.getEntitiesInBoundingBox`, which
returns entities whose **bounding box is fully CONTAINED** in the query box.
The generated boxes were 0.180 x 0.180 and described in their own `_comment`
as *"center-selecting box"* — but each chord is **0.2450 m long**. A 0.18 m
box can never contain a 0.245 m curve, so every one matched nothing.

Measured chord bounding boxes (from the real cut geometry):

| curve | xmin | ymin | xmax | ymax | length |
|---|---|---|---|---|---|
| 1 | 1.225982 | 0.000000 | 1.250000 | 0.243863 | 0.2450 |
| 2 | 1.154849 | 0.243863 | 1.225982 | 0.478354 | 0.2450 |
| … | … | … | … | … | 0.2450 |
| 8 | 0.000000 | 1.225982 | 0.243863 | 1.250000 | 0.2450 |

**FIX.** Size every box to CONTAIN the whole entity: chord bbox + a small
pad (0.005 used here). There is no "center-selecting" mode.

```
box = [min(x0,x1)-pad, min(y0,y1)-pad, -1e-6,
       max(x0,x1)+pad, max(y0,y1)+pad,  1e-6]
```

Verified: each corrected box returns exactly 1 curve, no duplicates.

**RULE FOR NEXT TIME.** A dim-1 selection box must be at least as large as
the curve's bounding box. Compute it from the polygon vertices, never from
the midpoint. Padding must stay smaller than the gap to the neighbouring
chord (here 0.005 << 0.245) or one box swallows two curves.

---

## PROBLEM 2 — SILENT: `gallery_wall` selected 2 of 8 chords

```
Cylindrical selection 'gallery_wall' (r=1.25, z_span>0.125): 2 entities
```

No warning — the group was non-empty, so the pipeline was happy. **6 of the
8 wall segments would have carried no hydraulic BC at all.**

**CAUSE.** `distance_from_axis` estimates the radius from bounding-box
extremes:

```python
r_x = max(|bb.xmin - x0|, |bb.xmax - x0|)
r_y = max(|bb.ymin - y0|, |bb.ymax - y0|)
r_estimated = max(r_x, r_y)          # gmsh_backend.py
```

That equals 1.25 only for the two chords that TOUCH an axis (curves 1 and
8). Chord 2's bbox reaches x = 1.225982, y = 0.478354 -> r_estimated =
1.2260, off by 0.024 > tolerance 0.01. The selector is written for true
cylindrical SURFACES in 3D, where the bbox really does span +/- r. It does
not work on a polygonal (chorded) wall.

**FIX.** Do not use `distance_from_axis` for a chorded boundary. Use the
union of the chord boxes, `selection: {"boxes": [...]}` — verified 8/8.
(Here `gallery_wall` was removed entirely, see PROBLEM 3.)

**RULE FOR NEXT TIME.** Raising the tolerance would NOT have fixed this: the
mid-chord radius error grows toward the middle of the arc, so any tolerance
loose enough to catch chord 4 also catches unrelated geometry. Only use
`distance_from_axis` on a real arc/cylinder entity.

---

## PROBLEM 3 — FATAL and INVISIBLE: two physical groups on the same curves

After PROBLEM 1 was fixed the mesher reported every group as found:

```
Multi-box selection for 'gallery_wall' (dim=1): 8 entities
Selection box for 'wall_chord_0' (dim=1): found 1 entities   ... x8
```

and the run **still** died with `Location 'wall_chord_0' not found in
boundary_sets`.

**CAUSE.** `gallery_wall` and the 8 `wall_chord_*` groups covered the SAME 8
curves. In the written mesh:

```
groups in file:   gallery_wall, wall_chord_0..7        <- all present
line elements:    8 elements, ALL carrying physical tag 2 (gallery_wall)
```

MSH 2.2 stores **one physical tag per element**. When an entity belongs to
several physical groups only one survives into the file; the others exist as
names in `field_data` with zero elements, so they never appear in
`boundary_sets`. The mesh log looks perfect and the failure surfaces much
later, at BC resolution.

**FIX.** Keep the groups DISJOINT. `gallery_wall` was deleted and its
pore-pressure Dirichlet split into 8 entries, one per `wall_chord_i`.
Verified: line elements now carry tags 6..13, one per chord.

**RULE FOR NEXT TIME.** Never define an "umbrella" group over entities that
also have individual groups. If you need both a per-segment traction and a
whole-wall BC, write the whole-wall BC as N per-segment entries. Check the
mesh, not the mesher log:

```python
import meshio, numpy as np
m = meshio.read("input/geometry.msh")
carried = {t for cb, p in zip(m.cells, m.cell_data["gmsh:physical"])
           for t in np.asarray(p).tolist() if cb.type == "line"}
missing = {k: v[0] for k, v in m.field_data.items()
           if v[1] == 1 and v[0] not in carried}
assert not missing, f"declared but element-less: {missing}"
```

---

## PROBLEM 4 — probe points snap to distant nodes

```
⚠️  Warning: Point 'P2' is 1.51e-01 away from closest node!
⚠️  Warning: Point 'P3' is 2.46e-01 away from closest node!
```

P2 (1.9, 0) and P3 (2.5, 0) are validation sensors read against the
`reference/PW/fig7_*` curves. 0.15 and 0.25 m off in the near field, where
the pore-pressure gradient is steepest, silently moves the comparison.
(The sibling cox benchmark had this worse — P2 and P3 landed on the SAME
node and would have plotted identical curves.)

**FIX.** `geometry.points` snaps to the nearest EXISTING node. For an exact
probe use a dim-0 physical group with a 6-value box, which forces a mesh
vertex at the coordinate.

---

## PROBLEM 5 — solver: skyline is unusable at this size

46 378 nodes x 3 DOF ~ **139 000 unknowns** on an unstructured gmsh mesh.
Skyline's cost is bandwidth-bound: measured **107 s/step** on a mesh of only
11 656 unknowns, so this one would be far worse, over 140 steps.

**FIX.** `time_stepping.linear_solver: "dss"` (added). DSS reorders
internally and is unaffected by gmsh numbering — 0.8 s/step on the same
11.6k-unknown mesh, identical results. Safe here: the DSS accuracy caveat
only bites problems whose converged state carries ZERO external load, and
this model is externally loaded.

MEASURED after the fix: **~15-17 s per LDL factorization** at 139 k unknowns,
so the full 140-step run is roughly 35-40 min. Workable, but if you want it
faster the lever is `element_size` (0.5 m currently gives 45 975 quads over a
100 x 100 m domain, which is far finer than the far field needs) — a graded
mesh, fine only near the gallery, would cut this by an order of magnitude.

---

## OPEN — physics to verify against the paper (NOT mesh bugs)

These were not fixed; they need the paper.

1. **Is this an excavation, or a pressurised hole?** The wall carries a
   constant inward traction of |t| = 11.875 MPa on every chord, while
   `far_right`/`far_top` carry NO mechanical BC (traction-free) and there is
   no initial stress state. That is a hole being pressurised in an unstressed
   plate. A real excavation is the RELEASE of the wall traction in a body
   that already carries the in-situ stress — normally the in-situ stress on
   the far-field boundaries plus a wall traction ramped from in-situ down to
   the stated 5 % / 50 % over 24 h (a `time_function`, which no wall BC
   currently has).
2. **Pore-pressure datum.** IC = 0, far field = 0, wall = −4.6 MPa. That
   reads as EXCESS pressure relative to a 4.6 MPa in-situ value. The
   `reference/PW/fig7_*` curves are presumably absolute pore pressure —
   confirm both use the same datum before plotting, or the comparison is
   offset by 4.6 MPa.
3. **Wall discretisation.** Each chord meshed to exactly ONE line element
   (chord 0.245 m < `element_size` 0.5 m), so the whole gallery wall is 8
   edges. Fine for the traction, coarse for reading a near-wall pressure
   gradient. Consider `element_size` ~0.1 near the wall.

---

## Status after the fixes

Applied to `benchmark.json` in this directory:

- 8 `wall_chord_*` boxes resized to contain their chords (PROBLEM 1)
- `gallery_wall` group deleted; its pore-pressure Dirichlet split into 8
  per-chord entries (PROBLEMS 2 + 3)
- `time_stepping.linear_solver: "dss"` added (PROBLEM 5)

Verified end to end:

```
Selection box for 'wall_chord_0'..'wall_chord_7' (dim=1): found 1 entities   (x8)
line elements in geometry.msh carry physical tags 6..13                     (8/8)
build + 40 steps OK, ~15 s/step
p_P1 = -4.60e6 Pa exactly (the prescribed wall value)
```

PROBLEM 4 (probe snapping) and the OPEN physics items below are NOT fixed.

### ⚠️ PROBLEM 6 (NEW, introduced by the graded mesh) — results are INVALID

`mesh.refinement` was added to replace the uniform 0.25 m mesh (967 nodes,
0.139 m at the wall, vs 46 378 nodes at uniform 0.5 m). But grading made
perfect quad recombination harder and gmsh left **8 triangles** among 886
quads. The pipeline keeps only the dominant element type, so those 8 are
dropped, and the **4 nodes belonging solely to them** carry no stiffness:

```
nodes used ONLY by triangles: 4  ->  12 DOFs  ==  "Warning: 12 zero pivot"
p_P2 last = 1.65e17 Pa,  ur_P1 last = -1.19e8 m
```

The matrix is SINGULAR. This is not a solver choice — `DSS_unsym_LU`, which
pivots, returned byte-identical garbage, and switching back to `skyline`
will not help either.

**Do not trust this benchmark's output until the mesh is pure quads.** See
the KNOWN ISSUE block in `tools/mesh_generator/gmsh_backend/gmsh_backend.py`
for the fix (gate mixed meshes + force full-quad recombination).

---

## Checklist to run before declaring a chorded geometry done

1. Every `selection.box` CONTAINS its target entity's bounding box.
2. No entity belongs to two physical groups.
3. Every declared dim-(N−1) group carries elements in the written `.msh`
   (snippet in PROBLEM 3) — the mesher log is not sufficient.
4. No `distance_from_axis` on a polygonal boundary.
5. No probe-point distance warnings.
6. `linear_solver: "dss"` for anything above ~10 k unknowns on a gmsh mesh.

---

## PROBLEM 7 — FATAL and INVISIBLE: the time grid never reached OOFEM

Found 2026-08-16, present in every run of this benchmark so far.

The 140-step run reported t_end = 183.6 d but had actually simulated
**0.78 d**. Both 24 h ramps were only 78.1 % complete at the last step.

**SIGNATURE.** Every column of `query_results.db` is exactly linear in the
STEP INDEX, not in time:

```
p_P1 excess = -25669.542262631927 Pa x step      (12 digits, all 140 steps)
ur_P1       =   1.5119e-05 m      x step
25669.5423 / 4.6e6 * 86400 = 482.1409677 s  ==  grid.step_dt(1), bit for bit
```

**CAUSE.** `builder/physics/hydro_mechanical.make_engine` passed BOTH
`deltaT=grid.step_dt(1)` and `prescribedTimes=...` to `mpmProblem`.
`MPMProblem::initializeFrom` reads them as an if/else-if chain with
**deltat first**, so `prescribedTimes` was silently dropped and every step
advanced by the FIRST step's dt. `transientTransport` and `staticStructural`
read `prescribedTimes` first, which is why the catalog's "verified" note did
not transfer. Full table in `docs/oofem_catalog.md`.

The times in the DB and the plots come from the Python `TimeGrid`, not from
`tstep->giveTargetTime()`, so the mislabelling is invisible in every artifact
the run produces.

**FIX.** Pass `deltaT` only when `prescribed_times` is empty. Safe: all four
mpm assemblers take `tStep->giveTimeIncrement()`.

**RULE FOR NEXT TIME.** A constant per-step increment in a ramped quantity on
a NON-UNIFORM grid is always a bug. Assert against it, or read back
`tstep.giveTargetTime()` in the step loop.

---

## PROBLEM 8 — the near-wall mesh was too COARSE for the time step

The sign-alternating pore pressure blamed on "upquad11 inf-sup" in the old
OPEN DEFECT note was the Vermeer-Verruijt monotonicity floor: an equal-order
u-p step is monotone only for `dt >= h^2/(6c)`. With c = 1.35e-7 m2/s and
h = 0.1 m the floor is 12337 s, against `dt_min = 600 s` — 20x under.

Measured overshoot at the old settings (excess p along y=0, last step):

```
r=1.25  -3.59e6 | r=1.36 -5.28e5 | r=1.52 +8.88e4 | r=1.74 +9.05e4 | r=2.03 -3.93e4
```

Positive excess pressure around a draining hole, which is exactly why P3
read MORE drawdown than P2.

**FIX.** `refinement.element_size` 0.1 -> 0.04 (floor drops to 1974 s) and
segment-1 `dt_min` 600 -> 3600 s (3364 s after normalization). Note the
counter-intuitive direction: refining RELAXES the floor.

After the fix the profile is monotone, max positive excess +3.1 kPa (0.07 %).

---

## Status 2026-08-16 — VALIDATED

114/114 steps, 1465 nodes, 1362 pure quads, 107 s.

| | sim @ true probe radius | seven teams |
|---|---|---|
| ur P1 | 2.738 mm | 2.749 – 2.758 (closed form 2.757) |
| ur P2 | 1.975 mm | 1.903 – 2.001 |
| ur P3 | 1.604 mm | 1.541 – 1.611 |
| ur P4 | 0.724 mm | 0.711 – 0.745 |
| p P1 | 0.100 MPa (Patm, reached t=1.16 d) | 0.100 – 0.21 |
| p P2 | 1.849 MPa | 1.749 – 2.104 |
| p P3 | 2.923 MPa | 2.788 – 2.944 |
| p P4 | 4.673 MPa | 4.649 – 4.702 |

PROBLEM 4 (probe snapping) is STILL OPEN and is now the only error source
left: P2/P3/P4 snap to r = 1.830 / 2.444 / 6.448 instead of 1.9 / 2.5 / 6.25,
and since ur ~ 1/r that fully explains the +3.1 % / +3.0 % / -5.0 % the
`query_results.db` columns show against the band centre. It cannot be fixed
in this JSON: `gmsh_backend._embed_measurement_points` returns early when the
model has no 3D volume, so 2D benchmarks get no forced nodes, and a dim-0
physical group has no CAD vertex to select. The fix belongs in the mesh
generator — embed into the containing CURVE (all four probes lie on
`sym_bottom`).
