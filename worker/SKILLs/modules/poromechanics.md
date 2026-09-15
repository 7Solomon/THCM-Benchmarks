# MODULE: Poromechanics (Biot u-p)

> pore_pressure, hydro_mechanical, and the mobility conversion

<!-- Loaded on demand by CREATE.md's START-HERE routing table. Structure is
enforced by SKILLs/benchmark.schema.json; the mechanical cross-key rules by
worker/tools/check_benchmark.py. This file carries only what needs judgement. -->

#### `pore_pressure` (Biot poroelasticity)

Only under the `hydro_mechanical` coupling. **E, ν and ρ are NOT repeated here** —
they come from the same material's `displacement` section, exactly like the
temperature/hygrothermal split.

```json
"soil": {
  "displacement":  {"E": 1.0e7, "nu": 0.0, "rho": 2000.0},
  "pore_pressure": {"model": "biot", "biot_alpha": 1.0,
                    "intrinsic_permeability": 1.0e-12, "fluid_viscosity": 1.0e-3,
                    "porosity": 0.33, "fluid_bulk_modulus": 2.2e9}
}
```

| key | unit | notes |
|---|---|---|
| `biot_alpha` | – | **required.** Biot coefficient b = 1 − K_drained/K_solid, in (0, 1] |
| permeability | — | exactly ONE route: `mobility` [m²/(Pa·s)], or `intrinsic_permeability` [m²] + `fluid_viscosity` [Pa·s], or `hydraulic_conductivity` [m/s] + `fluid_unit_weight` [N/m³] |
| storage | — | `storage` = 1/Q [1/Pa], **or** `porosity` + `fluid_bulk_modulus` (+ optional `solid_bulk_modulus`; omitted = incompressible grains, consistent only with b = 1) |

Governing system, so you can check your parameters against the paper's:

```
div(sigma' - b p I) = 0                            sigma' = D : eps
b d(div u)/dt + (1/Q) dp/dt = div((k/mu) grad p)
```

⚠️ **The underlying oofem material defaults EVERY parameter silently** (E = 1 Pa,
ν = 0.15, b = 1, mobility = 1, 1/Q = 0.1) if a keyword is missing — so this backend
validates them itself and raises. Do not rely on a run that "worked" to mean the
parameters arrived; they arrive because the builder checked.

Hard constraints, all from the element:
- **quad meshes only**, and **plane strain only** — ε_zz ≡ 0 identically. No
  plane-stress or axisymmetric variant, and `displacement.plane_stress` /
  `displacement.thickness` are rejected.
- **out-of-plane depth is fixed at 1 m** and has no knob. Give every per-metre
  quantity the paper reports per unit depth.
- `analysis_type` must be `transient` — consolidation *is* the time evolution.
- displacement `model` must be `linear_elastic` (or omitted); no damage/plasticity.
- **equal-order u/p** interpolation does not satisfy inf-sup. Near the undrained
  limit (low permeability, small dt) the pressure field can checkerboard. Check a
  pressure profile for oscillation rather than assuming; refine dt or the mesh.


> ### Why `mobility` is not the permeability the paper quotes
> The oofem material stores one number for Darcy transport and its water viscosity
> is hard-wired to 1.0 and unsettable, so that number is the **mobility** k/μ.
> Papers quote either intrinsic permeability [m²] or hydraulic conductivity [m/s],
> and feeding either straight in is wrong by ~1e3 / ~1e4.
>
> **Do not convert by hand** — state which one you have and let the pipeline do it:
> `intrinsic_permeability` + `fluid_viscosity`, or `hydraulic_conductivity` +
> `fluid_unit_weight`, or `mobility` directly. Exactly one route; the others are
> rejected. Same for storage: `storage` [1/Pa] directly, or `porosity` +
> `fluid_bulk_modulus` (+ `solid_bulk_modulus`) and 1/Q = n/K_f + (b−n)/K_s is
> computed for you.

