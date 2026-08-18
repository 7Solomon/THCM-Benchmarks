# BenVaSim 1.1(a) — Analysis of the OOFEM runs

Round 2 (2026-08-17 10:52 run) is at the top; the round-1 analysis that produced the
confinement fix follows below and is still accurate for what it covers.

---

# ROUND 2 — the confinement fix worked; the remainder is a SOLVER BUG

Run analysed: `output/OOFEM/query_results.db` (2026-08-17 10:52) +
`output/OOFEM/vtu/*.vtu`, against `benchmark.json` (10:41). Not stale.

## TL;DR

**Finding 1 (lateral confinement) is closed.** Step 1 is now exact:

| step 1 | sim | analytical | error |
|---|---|---|---|
| undrained `p'` (x = 5 m) | 571,900 Pa | 571,809 Pa | +0.016 % |
| `u_lhs / L` | 4.0913e-5 | 4.086e-5 | +0.13 % |
| `\|uy\|` max | 8.08e-7 m | 0 | confinement active (was 2.18e-5) |

**The entire remaining deviation is caused by ONE node whose pressure DOF is dead,**
and the culprit is `time_stepping.linear_solver: "dss"`.

Node **2835** (1-based; index 2834) at **(0.03956, 0.45939)** — 0.0396 m from the
`p = 898,700 Pa` inlet face — sits at `p ≈ 0` at every step (−1.3e-7 at step 1,
−1.7e-2 at step 465) while its four neighbours are at 495,191 / 654,980 / 457,829 /
898,700 Pa. It acts as a **point sink pinned to zero right next to the inlet**, and it
short-circuits **15.6 %** of the inlet flux.

## Why the deviation looks like "a constant factor"

Because it is one. At 30 a the column is essentially at steady state, and a steady
state is a straight line — so a flux deficit rescales the whole profile by a single
constant. Excess-pressure ratio sim/reference at 30 a:

| probe | X=0.25 | X=0.75 | X=1.25 | X=5 | X=9.75 |
|---|---|---|---|---|---|
| sim/ref (excess) | 0.857 | 0.845 | 0.845 | 0.847 | 0.848 |

X = 9.75 m only *looks* "spot on" because the plot is in absolute MPa and its excess
pressure is tiny: 0.1205 vs 0.1239 MPa is −0.0034 MPa on the axis, but −15.2 % of the
excess — the same factor as everywhere else. X = 5 m looks worst simply because its
excess is mid-range, so the same 15 % is the largest number of MPa (−0.069).

X = 0.25 m is the one genuine outlier (0.857 vs 0.845): it sits inside the funnel
around the sink, where the profile has to bend back up to meet the 898,700 Pa Dirichlet.

## Proof that the one node explains all of it

I assembled the steady-state Laplace problem on the **same mesh** (at steady state the
Biot coupling and the storage drop out, so pure Laplace is exact) and solved it twice:

| x [m] | Laplace, clean | Laplace, node 2834 pinned to 0 | OOFEM @ 30 a | error |
|---|---|---|---|---|
| 0.05 | 894,206 | 872,840 | 872,875 | 0.004 % |
| 0.25 | 876,232 | 787,168 | 787,346 | 0.02 % |
| 0.50 | 853,765 | 732,320 | 732,677 | 0.05 % |
| 1.00 | 808,830 | 683,439 | 684,148 | 0.10 % |
| 2.50 | 674,025 | 569,101 | 570,729 | 0.29 % |
| 5.00 | 449,350 | 379,400 | 381,707 | 0.61 % |
| 9.75 | 22,467 | 18,970 | 19,151 | 0.95 % |

The clean solve reproduces the analytical line exactly. The pinned solve reproduces
the OOFEM field to under 1 % everywhere. **Nothing else is wrong with the run** — the
residual 0.0–1.0 % drift is the last ~1 % of the transient.

## PIPELINE FINDING — `linear_solver: "dss"` breaks `hydro_mechanical`

Controlled experiment: identical mesh, identical config, 3 uniform steps, only the
solver changed.

| | node 2834 `p` [Pa] | node 2834 `ux` [m] |
|---|---|---|
| `"dss"` | **−3.289e-07** | 4.0998e-04 |
| `"skyline"` | **657,831** | 4.0886e-04 |

`max |p_dss − p_sky|` over all 2835 nodes = **6.578e+05, attained at node 2834** —
that single node is the largest discrepancy in the entire field. Displacements agree
to 2.2e-6 m. Skyline's value sits sensibly between the neighbours; DSS's does not.

**Mechanism.** `hydro_mechanical` declares `symmetric_tangent = True`
(`builder/physics/hydro_mechanical.py:75`), so `builder/solver.py` maps `"dss"` to
`SMT_DSS_sym_LDL` (smtype 8) — an **unpivoted LDL^T**. But the u–p tangent is
symmetric **indefinite** (a saddle point), which the plugin's own docstring already
flags:

> "Symmetric-variant factorizations are therefore the right family, but a
> symmetric-indefinite system is where an LDL^T can meet a zero pivot — see the solver
> test matrix before trusting a new solver here."

That is exactly what happens, and the DOF it kills is the **last equation in the
system**: node 2835's `P_f` (blocked layout, pressure block last), where an unpivoted
factorization accumulates its worst breakdown.

`solver.py`'s "Why DSS is opt-in" section documents only an **accuracy floor on
free/unloaded problems**. This is a different and much worse failure mode — a silent
dropped equation on a *loaded* problem — and the benchmark's own
`_comment_linear_solver` reasoned against the wrong caveat ("Safe here: … this column
is loaded").

### Propagation

`debug/HITEC/hm-excavation` also runs `hydro_mechanical` on `"dss"` and shows the
**identical signature**: node 1464 — the last of 1465 — has `p = 2.118e-04` while its
neighbour mean is `−4.685e+05`. Its published results are affected.
`debug/HITEC/cox-isotropic-temperature` is also on `"dss"` but is pure conduction
(symmetric **positive definite**), where `DSS_sym_LDL` is sound — not affected.

The e2e suite does not catch this (`tests/test_hydro_mechanical_e2e.py`,
`tests/test_solver_choice.py`); `solver.py` claims Biot consolidation reproduces its
analytic reference on DSS, so the covering test presumably uses a mesh small or
structured enough not to trip the breakdown.

## Recommended actions

1. **Stop using `"dss"` on any `hydro_mechanical` benchmark.** Config-level: set
   `"linear_solver": "skyline"` here and in `debug/HITEC/hm-excavation`.
2. **Make the runtime affordable at the same time** (skyline is ~65 s/step here =
   8.4 h for 465 steps). Now that the top edge is clamped, the transverse direction
   carries *zero* physics, so the strip only needs to be **one element high**: change
   the rectangle `size` to `[10.0, 0.05]` and move every probe / `line_profile`
   y-coordinate from 0.25 to 0.025. That drops 2623 quads to ~200 and 8505 unknowns to
   ~1200, where skyline is instant. It also gives a structured mesh, removing the
   unstructured-corner patch that hosted the dead node.
3. **Backend (separate from this benchmark):** either reject `"dss"` for
   `hydro_mechanical` in `builder/solver.py`, or route it to the pivoting
   `SMT_DSS_unsym_LU` (smtype 10) variant, which handles an indefinite system. Add a
   regression test on an unstructured quad mesh — the current one does not reproduce it.

Expected after (1): every curve lands on the analytical line to within the ~1 %
remaining transient.

---

# ROUND 1 — the confinement fix (2026-08-16 13:18 run)

Run analysed: `output/OOFEM/query_results.db` (13:18) + `output/OOFEM/vtu/*.vtu`,
against `benchmark.json` (13:03). Output is newer than the config — not stale.

## TL;DR

**One root cause explains the entire initial-step error and most of the transient:
the column is not laterally confined.** `uy = 0` is prescribed only on `edge_bottom`;
the top edge `y = 0.5` is traction-free. With Biot coupling that makes
`sigma_yy = 0 -> eps_yy = b*p/M`, i.e. the strip swells sideways as pressure builds.
The reference model is a **1-D (oedometric) column** with `eps_yy = 0`.

The consequence is exact, not approximate:

```
free-y   :  p_u = sigma/(2 + E/Q) = 898700/2.5714 = 349,494 Pa     <- what the run does
confined :  p_u = sigma/(1 + E/Q) = 898700/1.5714 = 571,809 Pa     <- reference/analytical
```

| Metric (step 1, t = 999.7 s) | Sim | Analytical / reference | Ratio |
|---|---|---|---|
| undrained interior `p'` (x = 5 m) | **349,494 Pa** | **571,809 Pa** | **0.6112** |
| predicted ratio `(1+E/Q)/(2+E/Q)` | — | — | **0.6111** |
| `u_lhs / L` | 6.8633e-5 | 4.086e-5 | 1.680 |
| free-y prediction `(sigma - p_u)/E` | 6.865e-5 | — | ✓ |
| `uy` at top edge | 2.184e-5 m | 0 (confined) | — |
| free-y prediction `(p_u/E)*0.5` | 2.1843e-5 m | — | ✓ |
| momentum check `E*eps + p` | 898,559 Pa | 898,700 Pa | 0.016 % |

Three independent numbers (`p_u`, `u_x`, `u_y`) all land on the free-lateral-boundary
solution to 4 digits. Momentum is exact, so the load, the coupling and the moduli are
all correct — only the kinematic BC is wrong.

## Correction to `metadata.notes`

The JSON currently states:

> fingerprint p/(u/L)=5.093e9=(1/Q+1/E)^-1 indicates a **spurious 1/E = b^2/M term in
> the pipeline's storage/coupling assembly**

The arithmetic is right, the attribution is wrong. `S_used = eps_xx/p =
6.8633e-5/3.49494e5 = 1.9638e-10` does equal `1/Q + b^2/E = 1.96429e-10` — but that is
what you get when you invert the storage using `eps_xx` **alone** while `eps_yy` is
free. The extra `b^2/M` is not in the assembly; it is the transverse swelling the
inversion ignored. Measuring `uy` settles it: the top edge really has moved by
`2.184e-5 m`, exactly `eps_yy*h/2 = (p_u/E)*0.25*2`. **There is no pipeline bug here.**
No OOFEM source change is warranted, and the "cross-check against OOFEM's consolidation
tests" item can be dropped.

## What the run got right

- Momentum balance exact (0.016 % on step 1).
- Mobility and storage logged correctly: `1e-17 m2/(Pa.s)`, `1/Q = 7.14286e-11 1/Pa`.
- Time base honoured: non-uniform log grid, no constant-per-step fingerprint
  (Phase 0c.1 clean). 465 steps, `t = [0 -> 9.47e8]`, no truncation.
- Mesh integrity clean: 2623 quads, **zero** inverted or degenerate elements, area
  sums to 5.000 m2, median aspect ratio 1.14, p95 = 2.01, uniform sizing
  (median h = 0.0415 m near the left, 0.0435 m elsewhere).
- Probes are exact mesh vertices (dim-0 groups) — zero snap error.
- DSS solver: 465 steps completed; no convergence failures in the log.
- The far-field probe X = 9.75 m tracks the free-y model to within 3 % at every time,
  so the flow solver itself is behaving correctly for the problem it was given.

## Finding 1 (dominant) — missing lateral confinement

**Symptom.** Every simulated curve starts the plot at **0.4508 MPa abs** while the
analytical solution and all eight BenVaSim codes start at **0.6731 MPa abs**
(= 0.5718 MPa excess). This is the "wrong at the initial time step" the plots show.

**Cause.** `boundary_conditions` fixes `uy = 0` on `edge_bottom` only. The `_comment`
says "rigid-body-mode suppression only; no physical constraint (nu=0)". That is true
for pure mechanics, but **not under Biot coupling**: `sigma_yy = M*eps_yy - b*p`, so a
traction-free top gives `eps_yy = b*p/M` regardless of `nu`.

**Size of the effect.** Two separate consequences:

1. *Instantaneous:* undrained jump 0.3495 vs 0.5718 MPa excess (−38.9 %), and
   `u_lhs` 68 % too large (6.863e-4 vs 4.086e-4 m).
2. *Transient:* the effective storage becomes `S = 1/Q + 2b^2/M = 3.214e-10` instead of
   `1/Q + b^2/M = 1.964e-10`, so `c` drops from 5.09e-8 to 3.11e-8 m2/s and the
   fundamental time `L^2/(pi^2 c)` stretches from **6.31 a to 10.32 a**. The reference is
   at steady state by 30 a (4.8 t1); the run is only at 2.9 t1 and is still charging.

**Verification.** I integrated the 1-D equation for both storages with a 1000-cell
implicit solver:

| t [a] | X=0.25 | X=0.75 | X=1.25 | X=5 | X=9.75 |
|---|---|---|---|---|---|
| **30**  ref model | 0.9776 | 0.9329 | 0.8882 | 0.5520 | 0.1239 |
| 30 digitised analytical | 0.9775 | 0.9328 | 0.8881 | 0.5520 | 0.1239 |
| **30** free-y model | 0.9770 | 0.9310 | 0.8850 | 0.5436 | 0.1232 |
| **30** OOFEM run | 0.8520 | 0.8027 | 0.7644 | 0.4786 | 0.1201 |

(MPa absolute.) My reference model reproduces the digitised analytical to 4 digits at
every probe and every instant, so the comparison basis is sound.

**Fix.** Add a `face_top` physical group and clamp it:

```json
{ "name": "face_top", "dimension": 1,
  "selection": { "box": [-1e-06, 0.499999, -1e-06, 10.000001, 0.500001, 1e-06] } }
```

```json
{ "type": "dirichlet", "field": "displacement", "component": "uy",
  "value": 0.0, "location": "face_top",
  "_comment": "Lateral confinement: the reference is a 1-D oedometric column. Under Biot coupling a traction-free top gives eps_yy = b*p/M even at nu=0, which adds b^2/M to the apparent storage and halves the undrained pressure jump. Not optional." }
```

Expected after the fix: `p_u = 571,809 Pa` (0.6731 MPa abs), `u_lhs/L = 4.086e-5`,
`t1 = 6.31 a`, steady state reached inside the 30 a horizon, and the 2-D structure in
Finding 2 gone.

## Finding 2 — spurious 2-D near-field at the pressure boundary (same cause)

In a problem that must be strictly 1-D, the pressure varies strongly across the height
near `x = 0`:

| x [m] | p at y = 0.0 | p at y = 0.5 | spread |
|---|---|---|---|
| 0.05 | 872,807 | 457,660 | **415,147** |
| 0.25 | 787,001 | 668,015 | **118,986** |
| 0.50 | 731,986 | 708,024 | 23,962 |
| 1.00 | 682,776 | 681,739 | 1,037 |
| 5.00 | 377,251 | 377,250 | 1 |

(step 465, t = 30 a.) The disturbance decays over ≈ 0.5 m — **exactly one strip
height**, the St-Venant length. It is the restrained-swelling response: near the left
boundary `p` is large so the strip wants a large `eps_yy`, but `uy = 0` is imposed along
the bottom, so `uy(x,y) = eps_yy(x)*y` generates a shear field `gamma_xy = y*d(eps_yy)/dx`
wherever `dp/dx` is steep.

Consequence for the reported numbers: the probes sit at **y = 0.25**, inside this
disturbance for X = 0.25/0.75/1.25 m. At 30 a, `p_x025` reads 750,684 Pa at y = 0.25
against 787,001 Pa at y = 0 — a 4.8 % probe-position artifact on top of the transient
lag. This is why the three near-boundary probes deviate more from the free-y model
(−0.09 to −0.13 MPa) than X = 9.75 m does (−0.003 MPa). Clamping the top edge removes
it entirely.

## Finding 3 (minor) — `dt_min` below the u–p monotonicity floor

Step 1 rings on both boundaries (bottom-edge nodes, excess Pa):

```
x:  0.00     0.05     0.10     0.15     0.20     0.25     0.30   ...  interior
p:  898700   131927   392615   320900   347907   340244   344680 ...  349494
x:  ...      9.60     9.70     9.75     9.80     9.85     9.90     10.00
p:  ...      354039   347668   372458   300858   489419   ...      0
```

29 nodes undershoot the interior plateau. `upquad11` is unstabilised equal-order u–p;
monotonicity needs `dt >= h^2/(6c)`:

- as configured: `0.0415^2 / (6 * 3.11e-8) = 9,230 s` vs `dt_min = 1000 s` — violated 9x
- after Finding 1 is fixed (`c = 5.09e-8`): floor `5,640 s` — still violated 5.6x

The ringing is confined to the first ~15 steps and to ~0.3 m from each boundary; it
does **not** explain Findings 1 or 2 (both persist to 30 a, and Finding 1 is exact
arithmetic on the interior plateau, which is oscillation-free). Fix by raising
`time_stepping.dt_strategy.dt_min` to ~6e3 s — still 16 steps before the report's first
profile instant at 0.003 a = 9.47e4 s.

## Unexplained remainder

After Finding 1, the free-y 1-D model accounts for X = 9.75 m to −0.5…−3 % at every
instant and for the interior undrained plateau exactly. The residual at X ≤ 1.25 m
(−0.09 to −0.13 MPa at 30 a) is attributed to Finding 2 and is bounded by the measured
y-spread (up to 0.12 MPa at X = 0.25 m) — i.e. the two findings together close the gap.
**Nothing is left unattributed.**

## Recommended actions, by payoff

1. **Clamp `uy = 0` on the top edge** (Finding 1). Moves every curve from the 0.4508 MPa
   start to 0.6731 MPa, fixes `u_lhs` from 6.86e-4 to 4.09e-4 m, and cuts `t1` from
   10.3 a to 6.3 a so 30 a reaches steady state. Also removes Finding 2.
2. **Rewrite `metadata.notes`**: the "spurious `b^2/M` in the pipeline assembly" claim is
   incorrect. Replace with the free-lateral-boundary explanation above.
3. **`dt_min: 6000`** (Finding 3), to clear the u–p monotonicity floor.
4. *Optional, runtime:* the transverse direction carries no physics once the top is
   clamped. Shrinking the strip to one element high (0.05 m) and moving every probe /
   `line_profile` y-coordinate from 0.25 to 0.025 would cut ~2600 elements to ~200 and
   the run to minutes.
5. Re-run and re-score; the scorecard above is the target.

## Propagation

`grep -rl '"hydro_mechanical"' debug/*/*/benchmark.json` -> only this benchmark and
`debug/HITEC/hm-excavation`. HITEC does **not** carry the storage claim (its documented
issue was the time base), but the general lesson applies to every `hydro_mechanical`
config: **under Biot coupling, a traction-free face is never "physically inert", even at
`nu = 0`.** Any benchmark that leaves a face free because "nu = 0 means no transverse
effect" is silently running with an inflated storage coefficient.

## Earlier note (kept from the previous ANALYSIS.md)

Config gotcha, unrelated to the findings above: the `hydro_mechanical` material
requires `displacement.rho`. `CREATE.md`'s displacement table lists `rho` as optional,
but that applies to pure mechanics only — the `hydro_mechanical` plugin requires it.
