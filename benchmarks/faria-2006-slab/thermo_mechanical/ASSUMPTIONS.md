# Thermomechanical no-creep benchmark — Assumptions Report

## Scope

Runnable thermo-chemo-mechanical diagnostic using the same thermal model as the thermal benchmark plus linear elasticity and thermal strain. It intentionally omits creep and therefore does **not** reproduce the paper's principal creep-enabled prediction. It is conceptually comparable only to the paper's no-creep result in Fig. 9b.

## Why `thermal_aging` is not enabled

The updated backend's `thermal_aging` feature requires a B3/MPS chain with `q1`–`q4`, `E28`, `kTm`, and `mus`. The paper instead supplies a Double Power Law with `phi1=2.26`, `m=0.35`, and `n=0.3`. No defensible direct parameter mapping is given. Inventing B3/MPS values would violate the authoring rules, so `b3_creep` and `thermal_aging` are omitted.

## Assumptions Report

| # | Parameter | Value used | Tag | Why | Impact on validation target |
|---:|---|---|---|---|---|
| 1 | Creep | omitted | MODEL_SCOPE | Provides a transparent no-creep diagnostic; paper explicitly plots a no-creep comparison. | HIGH — not valid for the paper's creep-enabled stress history. |
| 2 | Young's modulus | 29 GPa constant | PAPER_VALUE/MODEL_GAP | Table 2 gives `E=29e9 alpha^0.5` Pa; 29 GPa is its alpha=1 asymptote. Direct E(alpha) is not supplied by thermal aging. | HIGH — over-stiffens early concrete and inflates early restraint stress. |
| 3 | Poisson ratio | 0.2 | PAPER_VALUE | Table 2. | LOW/MEDIUM. |
| 4 | Thermal expansion | 10e-6 K⁻¹ | PAPER_VALUE | Table 2. | HIGH — restraint stress scales nearly linearly. |
| 5 | Stress-free temperature | 17.25 °C | INFERRED | Set equal to uniform thermal IC. | HIGH — shifts all thermal strain. |
| 6 | Mechanical idealization | x fixed on both vertical edges; plane strain out of plane | ASSUMED | Approximates biaxial in-plane restraint in a through-thickness section while allowing thickness expansion. | HIGH; not the detailed pile-supported 2D plan model. |
| 7 | Vertical anchor | one exact bottom-centre uy constraint | NUMERICAL_CONSTRAINT | Removes rigid translation without clamping the thickness faces. | LOW. |
| 8 | Reinforcement | omitted | PAPER_MODEL_VALUE | Paper also omitted reinforcement in the early-age analysis. | Consistent with paper simplification. |
| 9 | Shrinkage | omitted | PAPER_MODEL_VALUE | Paper states shrinkage was disregarded for this normal-strength concrete and early-age window. | Consistent with paper. |
| 10 | Thermal inputs | same as thermal benchmark | MIXED | See thermal assumptions: inferred capacity, calibrated affinity, exported ambient. | HIGH for stress because temperature drives eigenstrain. |
| 11 | No-creep stress references | Fig. 9b top/centre/bottom | USER_EXPORTED | `data (10).csv` was split by series; values and sign were preserved exactly. | LOW conversion risk; enables quantitative comparison of this no-creep variant. |
| 11a | Creep-enabled stress references | Fig. 9a top/centre/bottom | USER_EXPORTED/NOT_USED | `data (9).csv` was normalized and retained for a future calibrated creep model. It is not a valid target for this no-creep JSON. | HIGH if compared to the wrong model. |
| 12 | Hydration query | `equivalent_age` only | SCHEMA_LIMITATION | Supplied schema still rejects `field: degree_of_hydration`, contrary to the supplied skill. | MEDIUM. |
| 13 | Fig. 17 stress histories | normalized but not plotted against this simulation | USER_EXPORTED/NOT_COMPARABLE | Fig. 17 compares the paper's creep-enabled 2D and 3D plan models at critical point P3. The current model is a no-creep through-thickness section and has no P3 equivalent. | HIGH if used as a validation target for the wrong geometry/physics. |
| 14 | Fig. 18 strain histories | normalized but not plotted against this simulation | USER_EXPORTED/NOT_COMPARABLE | Fig. 18 compares measured and calculated X strain at the bottom sensor under realistic slab/pile restraint and creep. The current fully restrained section has essentially prescribed in-plane strain and cannot reproduce that measurement. | HIGH if used against the current model. |
| 15 | Fig. 18 temperature | normalized separately | USER_EXPORTED | The source combines temperature and microstrain in one value column; it was split into a °C file. It may be used as another bottom-temperature reference after confirming the sensor/location matches Fig. 7b. | LOW conversion risk; MEDIUM interpretation risk. |

## Sanity estimate

With full restraint and constant 29 GPa, a 15 K rise gives an elastic stress scale `E alpha_T DeltaT ≈ 29e9 × 10e-6 × 15 = 4.35 MPa` before Poisson/plane-strain effects. This is an upper-bound order of magnitude and explains why constant final-age stiffness is unsuitable for early-time quantitative validation.

## Status

**RISKY / diagnostic only.** The JSON is schema-valid and runnable, but the mechanical result must not be presented as a reproduction of the paper's creep-enabled model. Export Fig. 9b to add a no-creep reference, or calibrate a B3/MPS approximation before enabling `thermal_aging`.
