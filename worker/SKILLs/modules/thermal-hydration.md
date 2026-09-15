# MODULE: Thermal + hydration

> temperature conduction, the affinity hydration model, thermal expansion

<!-- Loaded on demand by CREATE.md's START-HERE routing table. Structure is
enforced by SKILLs/benchmark.schema.json; the mechanical cross-key rules by
worker/tools/check_benchmark.py. This file carries only what needs judgement. -->

#### `temperature`

| key | unit | required |
|---|---|---|
| `k` | W/(m·K) | yes |
| `rho` | kg/m³ | yes |
| `cp` | J/(kg·K) | yes |

`k_alpha_reduction` is accepted by the schema but **not applied** — see above.

#### `displacement`

| key | unit | notes |
|---|---|---|
| `E` | Pa | >0 |
| `nu` | – | 0 ≤ ν < 0.5 (ν = 0 is allowed — it makes the plane-strain constrained modulus M = E) |
| `rho` | kg/m³ | optional |
| `plane_stress` | bool | 2D only; default `false` = **plane strain** |
| `thickness` | m | **only with `plane_stress: true`** |

**Choosing the plane state — the ×4 trap.** Default plane strain simulates a 1 m
slice. IF the specimen is a thin member loaded in its plane (a beam, a slab strip)
THEN set `plane_stress: true` + the real `thickness`, or every force scales by 1/b.
IF you are modelling a cross-section of a long structure (a wall, a dam) THEN plane
strain is correct.

| model | extra required keys |
|---|---|
| `linear_elastic` (or omit `model`) | none |
| `damage_rankine` | `ft` [Pa], `Gf` [J/m²] |
| `damage_mazars` | a `mazars` sub-section — see below |
| `cdpm2` | `fc`, `ft` [Pa] + a `damage_plastic` sub-section with `wf` — see below |
| `cdpm1` | `fc`, `ft` [Pa] + a `damage_plastic` sub-section with `wf` **or** `Gf` |
| `fcm_viscoelastic` | `ft`, `Gf`, **plus a `b3_creep` section** — see below |

> ### ⚠️ Only ONE damage model can carry creep
> `mpsDamMat` (= `damage_rankine` + `b3_creep`) is the **only** OOFEM class that is
> both a rheologic chain and a damage model. `cdpm2`, `cdpm1` and `damage_mazars`
> **reject** a `b3_creep` / `power_law_creep` section rather than dropping it — on an
> early-age restrained member, silently losing relaxation just over-predicts stress
> with no other symptom. If you need creep **and** cracking, that combination is
> `damage_rankine` + `b3_creep`, full stop.

##### `cdpm2` / `cdpm1` — damage-plastic concrete (Grassl–Jirásek)

Plasticity in effective stress + isotropic damage, crack-band regularized. **`cdpm2`
carries separate tensile and compressive damage; `cdpm1` carries one scalar** — for a
member that cracks in tension while its compressive struts stay intact, that is the
reason to pick CDPM2.

`fc` and `ft` are **positive magnitudes** in the `displacement` section. A negative
`fc`, or the two swapped, silently inverts the failure surface (OOFEM's
`m = 3(fc²−ft²)/(fc·ft)`) — the backend rejects `fc ≤ ft` for exactly that reason.

`displacement.damage_plastic` sub-section:

| key | notes |
|---|---|
| `wf` [m] | crack opening at zero stress. **Required for `cdpm2`**; for `cdpm1` give this **or** `Gf`, never both (OOFEM reads `wf` and ignores `Gf`) |
| `Gf` [J/m²] | `cdpm1` only — OOFEM derives `wf = Gf/ft` |
| `h_elem` [m] | crack band width. **Leave it out.** The default 0 makes OOFEM use the *element* size, which is what regularizes the results |
| `softening` | `cdpm2` only: 0 linear, 1 bilinear (default), 2 exponential. `ft_1`/`wf_1` are the bilinear kink and OOFEM reads them **only** when `softening=1` — the backend rejects the mismatch |
| `ecc`, `k_init`, `h_p`, `A_hard`…`D_hard`, `A_soft`, `eps_fc`, `dilation`, `damage_flag`, `yield_tol`, `newton_iter` | all optional; omit to keep OOFEM's own defaults |

Extractable IP fields: `damage_tension`, `damage_compression`, `dissipated_work`,
`plastic_strain` (cdpm2); `damage`, `cumulative_plastic_strain`, `plastic_strain`
(cdpm1).

> ### ⚠️ `cdpm2` does NOT answer `damage`
> Query `damage_tension` / `damage_compression` instead. OOFEM's CDPM2 never
> implements `IST_DamageScalar`, and its `IST_DamageTensor` is not a tensor at all —
> it is `[damage_tension, damage_compression, dissipated_work, 0, 0, 0]`. `cdpm1` and
> `damage_mazars` *do* answer plain `damage`.

##### `damage_mazars` — Mazars (1984) isotropic damage

`displacement.mazars` sub-section. **Two versions taking different parameters**, and
OOFEM silently never reads the other one's — the backend rejects a mismatch:

| `version` | required | notes |
|---|---|---|
| `original` (default) | `e0`, `A_c`, `A_t`, `B_t` | reproduces papers quoting At/Bt |
| `mod_tension` | `e0`, `A_c`, `eps_f` | crack-band-regularized tensile branch — **prefer this** unless matching a paper |

Optional in both: `B_c` (default `(A_c−1)/(A_c·e0)`, which keeps the curve smooth),
`beta`, `h_ref_t`, `h_ref_c`.

All three are tagged **unverified**: they construct and solve past the peak
(`test_damage_plastic_e2e.py` runs a displacement-controlled tension test), but none
is validated against a reference solution. Say so in `metadata.notes`.

> ### ⚠️ `alpha_0`, `a_E`, `E_min`, `alpha_min` are LEGACY-ONLY here
> The schema accepts them, and the OOFEM builder **ignores them silently**. They
> implement the legacy hydration-driven stiffness
> `E = max(E_min, E28·((α−α_0)/(α_inf−α_0))^a_E)`
> ([`mechanics.py:260`](../tools/simulations/fem/physics/kernels/mechanical/mechanics.py#L260)),
> which is a genuine α→stiffness aging law with **no OOFEM equivalent** — mps ages on a
> maturity clock instead (`thermal_aging`). If your paper's stiffness is
> hydration-driven, that is a reason to run it on `fem/`, not a reason to write these
> keys here and hope.

##### `fcm_viscoelastic` — fixed-crack law on a creep chain

`concreteFcmViscoelastic` delegates its **entire** elastic and viscoelastic response to
a companion `mps` chain, so a `b3_creep` section is **mandatory** (without one the
material has no stiffness at all). The config's `E` is inert — stiffness comes from
`q1`. Tagged **unverified**: it is construction-tested only, never validated against a
reference, so say so in `metadata.notes`.

Optional `displacement.fixed_crack` sub-section:

| key | values | default |
|---|---|---|
| `softening` | `none`, `exponential`, `linear`, `hordijk` | `hordijk` |
| `shear_retention` | `none`, `const_factor`, `const_coeff` | `none` |
| `shear_strength` | `none`, `const_ft`, `collins_interlock`, `residual_ft` | `const_ft` |
| `multiple_crack_shear` | bool | `true` |
| `beta`, `sf`, `fc`, `ag`, `n_cracks`, `crack_spacing`, `length_scale` | numbers | — |

Optional `displacement.time_dependent_fracture` (fib MC2010) — **ft and Gf evolve with
the chain's equivalent age**, which `mpsDamMat` has no equivalent for. Requires
`fib_s` and `fcm28` [Pa]; optional `ft28`, `gf28`. ⚠️ Setting it makes OOFEM overwrite
the static `ft`/`Gf`, so those become inert.

Estimation from fc only (tag `INFERRED`): `E ≈ 9500·(fc+8)^(1/3)` MPa;
`ft ≈ 0.3·fc^(2/3)` MPa (fc ≤ 50); `Gf ≈ 73·fc^0.18` N/m (fib MC2010, fc in MPa).

⚠️ `damage_rankine` **throws on snap-back**: if the element is too large,
Gf/(ft·h_elem) < ft/E, and the solve aborts. Refine the mesh.

#### `degree_of_hydration` (affinity — all keys required)

```json
{ "model": "affinity", "B1": 5.0e-4, "B2": 1.0e-5, "alpha_inf": 0.90,
  "eta": 7.0, "Ea": 38300.0, "R": 8.314, "T_ref": 298.15,
  "Hu": 498.2, "c_cement": 320.0 }
```

- **`B1` is in 1/s.** Papers often quote 1/h — convert and note both in `_comment`.
- **`T_ref` is in KELVIN** (the only Kelvin temperature in the file). See the Units box.
- `Hu` [J/g], `c_cement` [kg/m³] from the mix design. If the paper's own simulation
  **calibrated** one to hit a measured peak, use the calibrated value and tag it.
- B1/B2/eta/alpha_inf come from calorimetry fits. Without them or a temperature-rise
  curve: mark `MISSING`, do not guess.
- Literature: Ea 35–45 kJ/mol (CEM I); Hu 400–500 J/g (CEM I).
- `R` is accepted but the gas constant is fixed internally.

#### `thermal_strain`

| key | unit | notes |
|---|---|---|
| `alpha_T` | 1/K | concrete 8–12e-6 |
| `T_strain_free_ref` | **°C** | stress-free temperature = casting temperature |

Any other key in this section is **ignored with a warning** — check the log.

