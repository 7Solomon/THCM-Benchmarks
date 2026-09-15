# SKILL: Authoring Benchmark JSON for the OOFEM Simulation Pipeline

## Purpose

Translate a published benchmark paper into one `benchmark.json` that the OOFEM
pipeline runs without modification — one shot, no post-run fixes.

**This document targets the OOFEM backend only.**

### Three layers, three jobs — know which one owns your question

| Layer | Owns | You |
|---|---|---|
| **`SKILLs/benchmark.schema.json`** | *shape*: which keys exist, which are required, enum values, the exact arity of every vector and box | validate against it |
| **`worker/tools/check_benchmark.py`** | *mechanics*: cross-key constraints, unit conventions, backend restrictions — everything checkable from the file itself | run it |
| **this page + `SKILLs/modules/`** | *judgement*: which physics, which BC type, what the paper's numbers mean, the hand calculations | read it |

A rule that can be checked **is** checked. If you are about to write a rule here that
a script could enforce, put it in `check_benchmark.py` instead — prose rules are
followed probabilistically, script rules deterministically.

```bash
python worker/tools/check_benchmark.py <case>/benchmark.json --schema SKILLs/benchmark.schema.json
```

Exit 0 = clean. Every ERROR is a hard stop; every WARN needs a decision recorded in a
`_comment`. This replaces the old hand-walked checklist — run it, don't re-derive it.

---

## START HERE — decision tree

Answer these four questions before writing anything. **Answer 1 names the module
files to read; read only those.** Everything else on this page applies to every
benchmark.

**1. Which physics does the paper validate?**

| Paper measures | Fields you need | Read `modules/` |
|---|---|---|
| temperature history only | `temperature` | `thermal-hydration.md` |
| temperature + hydration heat | `temperature` + `degree_of_hydration` | `thermal-hydration.md` |
| moisture / RH / drying | `humidity` | `hygrothermal.md` |
| coupled heat AND moisture in one material | `temperature` + `humidity` + `heat_moisture_transport` | `hygrothermal.md` ⚠️ **KELVIN** |
| load–deflection, cracking | `displacement` | `mechanics-damage.md` |
| pore pressure, consolidation, settlement | `displacement` + `pore_pressure` + `hydro_mechanical` | `poromechanics.md` |
| early-age creep/relaxation under self-heating | `temperature` + `displacement` + `b3_creep` + `thermal_aging` | `thermal-hydration.md`, `creep-shrinkage.md` |
| restrained shrinkage, thermal cracking | `temperature` (+`humidity`) + `displacement` + couplings | all of them |
| a wall driven by a measured climate file | any of the above + file-backed BC tables | `hygrothermal.md` (climate CSV) |

Read `modules/results-queries.md` when you reach `results` and need anything beyond
the template's block below — a stress/strain component, a `line_profile`, a reaction,
an mps decomposition.

**2. Is the geometry 2D or 3D?**
2D → triangle or quad mesh. 3D → **tetrahedra only**. `metadata.dimension` is checked
against the mesh at load time; a mismatch is a hard error.

**3. Does the paper give hydration kinetics (calorimetry / affinity fit)?**
No → you cannot model early-age heat release. Say so; do not guess `B1`/`B2`.

**4. Does the paper give creep or shrinkage data?**
No → omit those couplings entirely. A guessed `q2` or `k_sh` poisons the validation
silently and invisibly.

---

## Authoring Workflow — the gates are gates

1. **Extract a value table from the paper**: parameter → value → unit → where in the
   paper (table / figure / section). **No JSON exists before this table exists.**
2. **Convert to the unit conventions below.** Most one-shot failures are unit failures.
3. **Run the Sanity Checks by hand** against the paper's reference curves. If the
   numbers don't reproduce the paper's stiffness / peak load / peak temperature to
   within tens of percent, the JSON is wrong before you run it. **No JSON before these
   numbers exist**, and they go into `_comment`s.
4. Write the JSON with a **provenance tag on every value**.
5. **Run `check_benchmark.py`.** Zero ERRORs before you run the solver.
6. End with the **Assumptions Report** (mandatory).

### Provenance tags (inside `_comment` keys)

| Tag | Meaning |
|---|---|
| `PAPER_VALUE` | stated in the paper — cite table/section/figure |
| `GENERIC_LITERATURE_VALUE` | handbook value — say for which material class |
| `INFERRED` | back-calculated from other paper data — show the calculation |
| `CALIBRATED` | the paper's authors tuned this — note their target |
| `ASSUMED` | your assumption — must appear in the Assumptions Report |
| `MISSING` / `CALIBRATE` | placeholder the user must supply — must appear in the report |

Unknown keys are ignored by the pipeline, which is why `_comment` keys are safe and
required practice.

### Hard rules

- **Never invent a value.** Every number is from the paper, from literature, derived,
  or an explicit placeholder. Tag which.
- **Never silently default.** Missing thickness, ambient temperature, casting
  temperature, convection coefficients must become explicit, justified assumptions
  with an impact estimate.
- If a **validation-critical** parameter is missing and cannot be soundly inferred:
  say so, put `RISKY:` in `metadata.notes`, and do not present the benchmark as
  validated.


*(Rules for editing this page itself live in `SKILLs/MAINTAINING.md`.)*

---
## ⚠️ UNITS — the one box to read twice

Everything is SI **except temperature**, which has a split convention. Getting this
wrong does not produce a clean error.

| Quantity | Unit | Paper traps |
|---|---|---|
| **temperature field**: `initial_conditions.value`, Robin `u_ref`, Dirichlet `value` | **°C** — *except* on a `heat_moisture_transport` domain, where it is **KELVIN** | K → −273.15 (and the reverse for hygrothermal) |
| **`thermal_strain.T_strain_free_ref`** | **°C** | must match the temperature field |
| **`degree_of_hydration.T_ref`** | **KELVIN** | °C → +273.15 |
| E, ft, fc, q1–q4 | Pa, 1/Pa | MPa ×1e6, GPa ×1e9 |
| Gf | J/m² = N/m | N/mm ×1000 |
| all times (`t_end`, `start_time`, `time_table`) | s | h ×3600, d ×86400 |
| hydration `B1` | 1/s | 1/h ÷3600 |
| `Hu` | J/g | kJ/kg = J/g; J/kg ÷1000 |
| lengths, displacements, `wd` | m | mm ÷1000 |
| Robin `alpha` thermal | W/(m²·K) | **kJ/(h·m²·K) ÷ 3.6** |
| Robin `alpha` spring | N/m³ | |
| strain | m/m | με ×1e-6 |
| **pore pressure** field: IC `value`, Dirichlet `value` | Pa | compression **positive** |
| `pore_pressure.mobility` | m²/(Pa·s) | **not** a permeability — see `modules/poromechanics.md` |
| `pore_pressure.storage`, bulk moduli | 1/Pa, Pa | |
| `neumann` `value` | Pa (traction) / W·m⁻² (flux) | a **density**, integrated over the boundary |
| `point_load` `value` | N / W | a **total**, added at each node, not integrated |

> ### Why temperature is °C but `T_ref` is Kelvin
> The hydration material works internally in °C and adds 273.15 itself, so the
> temperature field must be °C. The affinity reference temperature is converted
> *from* Kelvin, so it must be Kelvin.
>
> **A °C value in `T_ref` is not rejected by the schema and does not raise a physics
> error.** It becomes a ~20 K reference, the Arrhenius factor reaches ~1e94, and the
> solver produces astronomically large increments and never converges sensibly.
> A guard rejects `T_ref < 200`, but get it right rather than relying on the guard.
>
> **IF the paper quotes temperatures in °C THEN** use them directly for IC / `u_ref` /
> `T_strain_free_ref`, and add 273.15 for `T_ref` only.

**Consequence for result queries:** the temperature field is already °C. Do **not**
write a `formula` query subtracting 273.15 — plot the field query directly.

---

## What this backend CAN and CANNOT do

If the benchmark needs unsupported physics, **state which physics are missing and do
not work around it with unsupported keys.** Emit a JSON skeleton with
`"TODO: NOT SUPPORTED – <reason>"` and stop.

### ✅ Fields

| Field | Material section | Models | Elements |
|---|---|---|---|
| `temperature` | `temperature` | linear conduction (no `model` key) | triangle, quad, tetra |
| `humidity` | `humidity` | `bazant_najjar`, `isolin_moisture`; `kunzel` **only** under `heat_moisture_transport` | triangle, quad, tetra |
| `displacement` | `displacement` | `linear_elastic` (alias `elastic`, or omit `model`), `damage_rankine`, `fcm_viscoelastic` | triangle, quad (plane stress/strain), tetra (3D) |
| `degree_of_hydration` | `degree_of_hydration` | `affinity`; requires `temperature` active on the same domain | — |
| `pore_pressure` | `pore_pressure` | `biot` (or omit `model`); requires `displacement` active and the `hydro_mechanical` coupling | **quad only** (2D plane strain) |

### ✅ Couplings

| `physics` key | Effect | Needs material section of the same name |
|---|---|---|
| `thermal_strain` | temperature → displacement expansion | yes |
| `autogenous_shrinkage` | autogenous strain → displacement (**maturity**-driven here, α-driven on legacy — D9) | yes |
| `b3_creep` | B3/MPS creep → displacement | yes |
| `power_law_creep` | Bažant–Osman **double power law** creep → displacement. An *alternative chain* to `b3_creep`, **not** an addition — see the trap below | yes |
| `drying_shrinkage` | humidity → displacement shrinkage | yes (+ `b3_creep`, see below) |
| `thermal_aging` | temperature → mps **equivalent-time** aging of the creep chain | yes (+ `b3_creep`, see below) |
| `heat_moisture_transport` | **monolithic** coupled heat+moisture in ONE solve | `humidity` model `kunzel` |
| `hydro_mechanical` | **monolithic** Biot poroelasticity: displacement + pore pressure in ONE solve, both cross terms in one tangent | `pore_pressure` (+ `displacement`) |

The two monolithic keys take **no `target_field`/`source_field`** — the fields are
solved together, there is no direction. Write them bare:
`"hydro_mechanical": {"type": "coupling"}`.

### Status vocabulary — four states, not two

**Read the state before you conclude anything.** "Unavailable" is four different
situations and your next action differs in each. Getting this wrong has already cost
a benchmark: `power_law_creep` sat in a flat *NOT supported* list while OOFEM's
`doublepowerlaw` constructs fine today, and the benchmark was abandoned on the
strength of that line. (It is **wired** now — see below — which is the point:
it was always effort, never a wall.)

| state | meaning | what you do |
|---|---|---|
| **wired** | builder exists here, validated against a reference | use it |
| **unverified** | builder exists, never validated | use it, state the caveat in `metadata.notes` |
| **not wired** | the physics **exists in OOFEM**, no builder yet | **stop, but report it as effort** — name the OOFEM class |
| **legacy only** | runs on `fem/`, **silently ignored** here | run the benchmark on the legacy backend |
| **impossible** | not in OOFEM at all | stop and say so |

The machine-readable copy lives in the schema (`x-model-status`, `x-section-status`,
`x-oofem-class`) and `tests/test_skill_contract.py` fails if this page and the
registries disagree.

### 🔧 NOT WIRED — exists in OOFEM, no builder yet

Report these as **work**, not as walls. Each names what would be wired.

| want | OOFEM has | note |
|---|---|---|
| axisymmetric analysis | `l4axisymm`, `q4axisymm` (mechanics); `quadaxisym1ht`, `traxisym1ht` (transport); `domainType._3dAxisymmMode` | needs an `explicit_dof_mask` (the mode's default node DOFs are `{D_u, D_v, R_w}`) **and** a Voigt branch keeping the hoop component — the 2D reorder `(0,1,5)` currently drops `zz`, which on an axisymmetric run is the governing stress |
| autogenous curve of arbitrary shape (swelling then reversal) | mps `autoshrinkagetf` (`mps.h:92`) | takes a time function evaluated at **equivalent age** and applies its increment; the pipeline already builds `piecewiseLinFunction` from every `time_table`/CSV column |
| more creep laws | `b3mat`, `b3solidmat`, `ec2creepmat`, `cebfip78` | each is one module against the chain registry (`solid_mechanics/chain.py`) — the same shape `doublepowerlaw` already uses. `ec2creepmat` (Eurocode 2) and `b3solidmat` are the two people actually benchmark against |
| anisotropic (tensorial) damage | `mdm` | constructs today. Two decisions block it, not typing: its required `mode` (2D/3D) duplicates what the plugin knows from the element type but `build_material` gets no element type; and its required `nonloc` flag selects between local and *nonlocal* parameter sets, so half of it needs the nonlocal work below. See D16(e) |
| **mesh-objective** cracking, gradient form | `idmgrad`, `rankmatgrad`, `misesmatgrad` + the `*graddamage` elements (`trplanestressgraddamage`, `quad1planestraingraddamage`, `qspacegraddamage`, …) | **the cheapest real fix for mesh dependence.** Those elements carry `{D_u, D_v, G_0}` *uniformly per node* — architecturally identical to the working `hydro_mechanical` u–p plugin (`explicit_dof_mask`), not the Taylor–Hood shape that ruled out `uptetra21`. Needs an element table, a BC/IC slot for `G_0`, and the material |
| **mesh-objective** cracking, nonlocal form | `idmnl1`, `mazarsmodelnl`, `rcsdnl`, `frcfcmnl`, `rankmatnl`, `misesmatnl` | no element changes — standard elements work. Needs new *domain* objects: interaction radius `r`, averaging type, and barriers (`polylinebarrier`, `symmetrybarrier`) so averaging does not cross a notch or free surface. `permanentnonloctableflag` is a real memory/time trade-off on a fine mesh |
| cohesive-zone / bond-slip interfaces | `cohint`, `intmatisodamage`, `bondceb` | needs zero-thickness **interface elements**, i.e. duplicated nodes along a prescribed crack path. The gmsh backend does not emit that — this is meshing work, not material work |
| discrete crack networks | `latticedamage`, `latticeplastdam`, `latticedamageviscoelastic` | needs a Voronoi/Delaunay beam network. A different pipeline, not a different material |
| triaxial microplane concrete | `microplane_m4`, `microplane_m1` | constructs; ~10 parameters and very expensive per integration point |

> ### ⚠️ Probe the class factory, not `dir(oofem)`
> Most OOFEM materials have **no pybind wrapper** and are reached through
> `oofem.materialOfType("<name>", num, domain, …)`. They therefore do **not** appear
> in `dir(oofem)` or in `docs/oofem_catalog.md`'s class list, and concluding "OOFEM
> does not have it" from their absence is how `power_law_creep` got gated as
> impossible for a month (D15). The registered name is often not the paper's name
> either: CDPM2 is **`con2dpm`**, DruckerPragerCut is **`druckerpragercutmat`**, and
> lattice damage is **`latticedamage`** — `cdpm2`, `druckerpragercut` and
> `latticedamage2d` are all absent. The authority is
> `grep -rhoP '#define _IFT_\w+_Name\s+"\K[^"]+' <oofem>/src/sm/Materials/`.

### ↩️ LEGACY ONLY — validates here, then silently does nothing

These are accepted by the schema (`additionalProperties: true`) and **ignored** by the
OOFEM builder. If the benchmark needs them, run it on `fem/`.

- `materials.<mat>.displacement`: `alpha_0`, `a_E`, `E_min`, `alpha_min` — the legacy
  hydration-driven stiffness `E = max(E_min, E28·((α−α_0)/(α_inf−α_0))^a_E)`
  ([`fem/physics/kernels/mechanical/mechanics.py:260`](../tools/simulations/fem/physics/kernels/mechanical/mechanics.py#L260)).
  **This is a genuine α→stiffness aging law and OOFEM has no equivalent** — mps ages on
  a maturity clock instead (see `thermal_aging`). For a paper whose stiffness is
  hydration-driven, the legacy backend is the more faithful route.
- `materials.<mat>.displacement`: `beta_shear` (the legacy fixed-crack kernel's
  shear-retention factor, [`fem/.../damage/fixed_crack.py:22`](../tools/simulations/fem/physics/kernels/mechanical/damage/fixed_crack.py#L22))
  and `wd`. On OOFEM shear retention is reachable only through
  `fcm_viscoelastic`'s `fixed_crack.beta` — writing `beta_shear` next to
  `damage_rankine` does nothing at all.
- `displacement.model`: `rotating_crack`, `fixed_crack`, `compression_softening`,
  `rotating_crack_crush`. ⚠️ A fixed-crack law **is** reachable here — as the
  `fcm_viscoelastic` model plus its `fixed_crack` sub-section. Same word, different
  thing.
- `humidity.model`: `linear`.
- `materials.<mat>.ionic` and chloride/Nernst–Planck transport.
- `temperature.k_alpha_reduction`: **not applied** — conductivity stays constant.
  Note the gap in `metadata.notes`; expect a somewhat higher simulated peak.

> **Before you read the next list:** free-fluid flow is absent, but *saturated Darcy
> flow through a deforming skeleton* is fully wired — see the `pore_pressure` section.
> The two get confused constantly; check which one your paper actually needs.

### ❌ IMPOSSIBLE — not in OOFEM, stop and say so

Free-fluid flow (Navier–Stokes / Stokes — there is no velocity field),
dynamic/inertial analysis, embedded reinforcement, 1-D primary domains,
non-tetrahedral 3D meshes, and a genuinely **continuous** initial-condition field
(see `initial_conditions` — per-group piecewise-uniform ICs *are* supported).

Absent from the poromechanics side specifically: **anisotropic** elasticity or
permeability (both isotropic scalars), temperature-dependent fluid viscosity,
thermal pressurization / any u–p–**T** coupling, gravity in the mass balance
(pressures are excess pore pressures relative to the IC datum), and 3D u–p
(`upbrick11` needs a hex mesh, `uptetra21` 10-node tets — neither is meshed here).

---

## Choosing boundary conditions — physical situation → JSON

**This is where benchmarks succeed or fail.** Work from what the paper describes
physically, not from what is convenient to write.

> **Master rule:** if you cannot name the physical mechanism that *holds* a boundary at
> a fixed value, it is a **Robin**, not a Dirichlet.

| The paper says… | Use | Why |
|---|---|---|
| "exposed to air at T_amb", "ambient conditions" | **robin**, `alpha` = h, `u_ref` = T_amb | Air cannot pin the surface to its own temperature; the surface floats between air and core. |
| "formwork kept until t = X", "insulated with Y" | **robin**, `alpha` = 1/(1/h_air + R_layer), step `time_table` | Formwork is a series thermal resistance, not a fixed temperature. |
| "formwork kept throughout" | **robin**, **constant** `alpha` | Do not invent a stripping event the paper does not describe. |
| "sealed", "wrapped", "adiabatic", or a symmetry plane | **omit the BC entirely** | No BC = zero flux. Never write a zero-value Neumann for this. |
| "drained boundary", "free-draining face", "atmospheric pressure" | **dirichlet** on `pore_pressure`, value = 0 | Excess pore pressure is zero there by definition. |
| "impervious base", "undrained boundary", sealed rock | **omit the pore_pressure BC** | Same rule as adiabatic: no BC = zero flux. |
| "cast on a foundation / older lift / the ground" | mesh that body, **or** robin with a low `alpha` | An adiabatic bottom traps heat and inflates the peak. If you cannot mesh it, say so in `metadata.notes`. |
| "specimen dried at RH = 60 %" | **robin** on `humidity`, `alpha` = film coeff., `u_ref` = 0.60 | A humidity Dirichlet is a drying **shock**: effectively infinite surface transfer in one step. |
| "held at constant temperature", thermostat bath | **dirichlet** | Only when the boundary genuinely is temperature-controlled. |
| "restrained by an end frame / adjacent structure" | **robin** on a displacement component, `alpha` = k [N/m³], `u_ref` = 0 | Real restraint is finite. Check the implied restraint factor (Sanity Checks). |
| "fully fixed", "clamped" | **dirichlet**, component = 0 | |
| symmetry plane | **dirichlet**, fix only the **normal** component | Fixing all components over-constrains. |
| "load P applied at a point" | **point_load**, value = P [N] | Concentrated: added to EVERY node of the location, so point it at a dim-0 group or a single-node location. |
| "pressure q on a face", "traction t on the boundary" | **neumann**, value = q [Pa] | Integrated over the boundary — mesh-independent. `location` must be a dim-(N−1) group. Do **not** hand-integrate into a `point_load`. |
| "heat flux q on a surface" | **neumann** on `temperature`, value = q [W/m²] | Same mechanism, same units convention. |
| displacement-controlled test | **dirichlet** on displacement + `time_function: "linear"` | The dt grid sets the increment size. |

### Robin `alpha` reference values (thermal)

| Situation | h [W/(m²·K)] |
|---|---|
| still indoor air | 5–10 |
| free upper concrete surface | 9–10 |
| light wind / outdoor | 10–25 |
| 2 cm timber formwork (equivalent) | ~7 |
| steel formwork | 15–20 |

**IF the paper quotes kJ/(h·m²·K) THEN divide by 3.6** to get W/(m²·K).

⚠️ A quoted `h` may be the film coefficient on the **outside of a covering**, not on
the concrete. For a specimen wrapped in formwork + insulation the equivalent surface
coefficient is `1/(1/h + ΣR_layer)` and can be an order of magnitude smaller. Check it
against Sanity Check 3 before assuming the paper's number is the one you want.

### Robin / Dirichlet restrictions

The mechanical ones — Robin `alpha` tables need `"interp": "step"`, no `start_time` on
a Dirichlet, a mechanical Robin needs a constant `alpha` with `u_ref` exactly 0, no
`neumann` on a hygrothermal domain, no table *and* `time_function` on one entry — are
all enforced by `check_benchmark.py`. What it cannot decide for you:

  BC-local window: `constant`/`step` (default, full value immediately), `linear`,
  `smooth`/`cosine`, `quadratic`, `cubic`.

> ### Prefer a `time_table` whenever the timing is physical
> `time_function` and `time_table` are two spellings of ONE mechanism — every named
> ramp is converted to a `piecewiseLinFunction` before it reaches the solver. The
> difference is the clock:
>
> - **`time_function` is relative to `t_end` of the whole run.** It cannot express
>   "ramp over the first 24 h, then hold", and changing `t_end` (or passing a
>   `--t-end` override) silently rescales every ramp in the file.
> - **`time_table` is absolute seconds** and immune to both.
>
> Use a table for anything with real timing — an excavation stress release, a climate
> series, a staged load — and keep `time_function` for quasi-static pseudo-time 0→1
> tests, where "ramp over the run" *is* the intent.
>
> ```json
> {"type":"neumann","field":"displacement","component":"ux","location":"wall",
>  "value":{"time_table":[[0.0,-1.18e7],[86400.0,-5.9e5]],"interp":"linear"}}
> ```
>
> Available on `value` for `dirichlet`, `neumann` and `point_load`, and on `u_ref`
> and `alpha` for `robin`. Writing **both** a table and a `time_function` on one entry
> is rejected — the table used to win silently.


---

## Section-by-Section Reference

> Shape is enforced by the schema; cross-key rules by `check_benchmark.py`. What
> follows is only what neither can express.

### `metadata`

Documentation, except **`dimension`** (2 or 3), compared against the mesh at load
time. Recommended: `id` (kebab-case), `title`, `source` {authors, year, journal, doi},
`physics_class`, `validation_method`, `validation_target`, `notes` (put `RISKY:`
caveats here).

### `geometry`

**Units: metres.** No conversion anywhere.

**Arity:** every coordinate vector in `geometry` is 3 values `[x, y, z]` (z = 0 in
2D); every `selection.box` is 6 values `[xmin,ymin,zmin,xmax,ymax,zmax]`, also in 2D.
The two exceptions are rectangle `size` (2) and prism `base_polygon` vertices (2
each). `results.queries` `line_profile` **inverts** this rule — see
`modules/results-queries.md`. The linter checks all of it.

| `type` | Required `parameters` |
|---|---|
| `cube` | `origin` (3), `size` (3) |
| `rectangle` | `origin` (3), `size` (2) — surface in the z = origin_z plane |
| `cylinder` | `base_center` (3), `axis` (3, unit), `radius`, `height` |
| `sphere` | `center` (3), `radius` |
| `prism` | `base_polygon` ([[x,y],…], ≥3), `height` — extruded +Z from z=0 |
| `line` | `start` (3), `end` (3) |

Optional per-entity **`transform`** (⚠️ singular; `transforms` is silently ignored):
`translate` / `rotate` / `scale`, applied in order. `geometry.operations` booleans
(`cut|fuse|intersect|fragment`): after any boolean the original tags are invalid —
**use `selection.box`, never `entity_ids`.**

**Three techniques you actually need:**

1. **Pad every nominal coordinate by ±1e-6.** A face at y=0 → `ymin=-1e-6, ymax=1e-6`.
   A face at x=0.5 → `xmin=0.499999, xmax=0.500001`.
   ⚠️ **Write the computed number. JSON has no arithmetic.** `[0.5 - 1e-6, ...]` and
   `[6.0+1e-6, ...]` are not JSON: the parser stops at the operator and the whole
   file is lost. This is the single most common way a well-authored spec fails to
   load — 3 of 60 candidates in the 2026-08-27 eval died on exactly this, all of
   them in a `selection.box` where the nominal coordinate was not zero.
2. **A dim-0 group with a 6-value box auto-creates a mesh vertex at the box centre.**
   The only way to guarantee an exact node — for a point support, a point load, or a
   validation probe. `geometry.points` does **not** do this.
3. **`geometry.points` snaps to the nearest existing node**, with only a warning if it
   is far. Fine for smooth fields, wrong in steep gradients. **Read the run log for
   "Point 'x' is N away from closest node".**

⚠️ **Groups must not overlap.** A gmsh entity carrying two physical tags loses one
when the mesh is read back, and the BC on the losing group then resolves to nothing —
while the mesher's log still reports that it created both groups. One edge, one group;
several BCs may name the same group.

**An empty selection is not an error.** The mesher warns and continues, and every BC,
IC or query on that group silently contributes nothing. **Read the mesh console output
and confirm each group reports ≥1 entity** — the linter cannot see the mesh.

Robin BCs require a dim-(N−1) group. Names are case-sensitive.

`geometry.mesh`: `{"element_size": 0.02, "order": 1, "recombine": false}` — always set
`element_size` explicitly. **Fracture/bending needs ≥10 elements over the bending
depth.** The CLI `--mesh-size` preset *multiplies* it; set the physically sufficient
value and keep presets for refinement studies.

### `physics`

Top-level keys are arbitrary block names, **except coupling keys, matched by exact
name**.

```json
"heat_transport": {"type":"field","dependent_variable":
  {"name":"temperature","symbol":"T","type":"scalar","components":["T"]}},
"hydration": {"type":"ode_source","dependent_variable":
  {"name":"degree_of_hydration","symbol":"alpha","type":"ode",
   "components":["alpha"],"managed_by":"hydration"}},
"thermal_strain": {"type":"coupling","source_field":"temperature","target_field":"displacement"}
```

`dependent_variable.name` must be `temperature`, `displacement`, or `humidity`.
`displacement` uses `"type":"vector"` with `["ux","uy"]` or `["ux","uy","uz"]`.
**Declare only fields you solve.** A declared coupling with no matching material
section is a hard error, and so is the reverse — the backend refuses to run
"elastically by accident".

### `initial_conditions`

One entry per field per region. `location` may be a physical group, or `"all"` (the
legacy every-node default). **Per-group piecewise-uniform ICs work** — one entry per
region with different numbers. A genuinely continuous IC field does not.

### `boundary_conditions`

```json
{"type":"dirichlet","field":"displacement","component":"ux","value":0.0,"location":"left"}
{"type":"robin","field":"temperature","location":"top","alpha":7.5,"u_ref":17.0}
{"type":"neumann","field":"displacement","component":"uy","value":-1.0e6,"location":"top"}
{"type":"point_load","field":"displacement","component":"uy","value":-5000.0,"location":"node_a"}
```

⚠️ Robin keys are **`alpha` and `u_ref`** — not `h_conv` / `T_ext`. `neumann` is a
**density** integrated over the boundary; `point_load` is a **total** added at every
node of the location.

### `assignment`

`{"domains": {"<group>": {"active_fields": [...], "material": "<name>"}}}`. All
domains must declare the same `active_fields`.

### `time_stepping`

`t_start`, `t_end` [s], `analysis_type: "transient"`, and either `dt` or a
`dt_strategy` (`uniform` | `log` | `piecewise` of segments). Put fine segments where
the physics is fast (the first hours of hydration, a load step) and **align segment
boundaries with `time_table` breakpoints** — Sanity Check 7.

---

## Sanity Checks — do these on paper BEFORE running

*(each of these caught a real failure)*

1. **Plane state / thickness (the ×4 trap).** Verify elastic stiffness **and** peak
   load against the reference. Unstated thickness: back-calculate from initial
   stiffness AND peak — they must agree — and tag `INFERRED`.
2. **Adiabatic temperature rise:** ΔT_ad = Hu·c_cement·α_inf/(ρ·cp). It must **exceed**
   the measured peak rise; the difference is what the thermal BCs carry away. IF ΔT_ad
   is below the measured rise THEN the heat source is too small — no BC tuning fixes it.
3. **Cooling time constant:** τ = ρ·cp·V/(h·A_exposed). Compare against the reference
   cooling rate. A too-slow simulated tail almost always means `alpha` is too low or
   `u_ref` too warm; a too-**fast** one usually means the paper's `h` is the film
   coefficient on the OUTSIDE of a covering, not on the specimen.
4. **Surface/volume ratio (2D models).** A 2D cross-section cools through fewer faces
   than the real body — a cube cools through 6, its 2D section through 4. Compare A/V
   between model and specimen; if they differ, model it in 3D or scale `alpha` by the
   ratio and record it in `metadata.notes`.
5. **Restraint factor:** R = k/(k + 2E/L) per unit area. Check the spring `alpha`
   reproduces the paper's stated restraint at 28-d E.
6. **Free-strain magnitudes:** α_T·ΔT_peak and k_sh·Δh in με versus the paper's strain
   plots — orders of magnitude must match.
7. **Event alignment:** `time_table` breakpoints and fine dt segments sit on the same
   event times (casting, strip, loading).
8. **Ambient reality check:** if the paper supplies a measured ambient history, feed it
   in as a file-backed `u_ref` table rather than averaging. Only fall back to a
   constant when the paper gives none — then use the *time-weighted mean*, not the
   maximum, and say so in `metadata.notes`.

Record checks 2, 3 (and 4 if 2D) in `_comment`s. Pre-Flight looks for them.

---

## Traps that no script can check

Everything mechanically checkable now lives in `check_benchmark.py`; run it instead of
reading a list. These are the ones that need you.

- **IF** a physical group selects nothing **THEN** the mesher warns and every BC/IC/
  query on it silently does nothing. Only the mesh console shows this.
- **IF** two physical groups cover the same gmsh entity **THEN** one tag is dropped
  when the mesh is read back and its BC resolves to nothing — while the mesher's log
  still says it created both groups.
- **IF** a `time_column` is not in absolute seconds **THEN** nothing complains and the
  BC history is silently stretched or squashed. Convert in the prep script.
- **IF** you declare `thermal_aging` expecting it to read `degree_of_hydration`
  **THEN** you have the wrong model: mps runs its OWN Arrhenius clock with its own
  activation energy. Set `Q_E_over_R` = `degree_of_hydration.Ea/8.314` to make them
  agree, or accept two maturity histories in one run.
- **IF** you expect `thermal_aging` to reduce creep **THEN** you will see the
  opposite: the viscosity term (`Q_R/R`=5000) outweighs the aging term (`Q_E/R`=2700),
  so elevated temperature nets MORE creep and MORE relaxation.
- **IF** you start a poroelastic run from a uniform non-zero `pore_pressure` IC with
  zero displacement **THEN** it collapses to p = 0 on the first step. That state is not
  admissible. Drive the problem with a load from a consistent zero state instead.
- **IF** you write a distributed pressure as a `point_load` **THEN** the total scales
  with the node count and changes whenever you remesh. Use `neumann`.
- **IF** `mesh.recombine` is combined with `mesh.refinement` **THEN** check the element
  counts. Grading makes perfect quad recombination harder; the pipeline keeps only the
  DOMINANT element type, so stray triangles are dropped and their nodes end up with no
  stiffness. Symptom: `Warning: N zero pivots` and a solution ~1e17 — a SINGULAR
  matrix, not a solver problem. Verify `set(tri.ravel()) - set(quad.ravel())` is empty.
- **IF** `element_size` is too coarse for `damage_rankine` **THEN** the solve throws on
  snap-back (Gf/(ft·h_elem) < ft/E). Refine.

---

## Pre-Flight — run before declaring done

```bash
python worker/tools/check_benchmark.py <case>/benchmark.json --schema SKILLs/benchmark.schema.json
```

Zero ERRORs, and every WARN either fixed or explained in a `_comment`. Then the four
things the linter cannot see:

1. ☐ **Mesh console read** — every physical group reports ≥1 entity, and no
   "Point 'x' is N away from closest node" warning on a validation probe.
2. ☐ **Sanity Checks 2, 3 (and 4 if 2D) computed** and recorded in `_comment`s.
3. ☐ **Every BC type re-justified** against the physical-situation table — especially
   every Dirichlet: can you name what holds it?
4. ☐ **Assumptions Report written.**


---

## When the paper omits required values

1. **Never invent kinetics or strength parameters.** Mark `MISSING` with the literature
   range and stop short of claiming a validated benchmark.
2. **Geometry/BC assumptions** (gage coordinates, ambient T, casting T, formwork h):
   assume the standard value, tag `ASSUMED`, justify, estimate impact.
3. **Missing creep/shrinkage data:** omit those couplings entirely and record it in
   `metadata.notes`.
4. **If the paper's own simulation calibrated a value**, prefer the calibrated value
   over the nominal design value, and say so.

5. **Check for a sibling.** If an adjacent case in the same corpus models the same
   material or the same specimen, diff against it before deriving anything — a
   calibrated parameter set next door beats a fresh assumption.

## Assumptions Report (mandatory output)

```
## Assumptions Report
| # | Parameter | Value used | Tag | Why | Impact on validation target |
|---|-----------|-----------|-----|-----|------------------------------|
```

- Impact: qualitative + direction + magnitude class
  ("peak load scales ~1/thickness — HIGH"; "ν 0.18→0.20 shifts deflection <2% — LOW").
- If any HIGH-impact parameter is `ASSUMED`/`MISSING`: state plainly that the benchmark
  is not validated until confirmed, and put `RISKY:` in `metadata.notes`.
- List digitized reference figures and every sign/axis convention flip.

---
- **If two errors could cancel in the validation target, say so.** Agreement is not
  validation when you know two things pull in opposite directions.


---

## Minimal Working Template — 2D early-age thermal (temperature + hydration)

Drop sections you don't need. For pure mechanics: remove temperature/hydration/
couplings, use pseudo-time 0→1, `time_function: "linear"` displacement control, and a
`reaction` query.

```json
{
  "metadata": {
    "id": "author-year-name", "title": "…",
    "source": {"authors": ["…"], "year": 0, "doi": "…"},
    "dimension": 2, "physics_class": "thermo-chemo",
    "validation_target": "Core temperature history over 140 h",
    "notes": "…"
  },
  "geometry": {
    "entities": [
      {"id": "wall", "type": "rectangle",
       "parameters": {"origin": [0.0, 0.0, 0.0], "size": [1.2, 1.9]},
       "_comment": "PAPER_VALUE: 1.2 m thick, 1.9 m high (Sec. 3)"}
    ],
    "physical_groups": [
      {"name": "concrete_domain", "dimension": 2, "entity_ids": ["wall"]},
      {"name": "surface_left",  "dimension": 1,
       "selection": {"box": [-1e-6, -1e-6, -1e-6, 1e-6, 1.901, 1e-6]}},
      {"name": "surface_right", "dimension": 1,
       "selection": {"box": [1.199, -1e-6, -1e-6, 1.201, 1.901, 1e-6]}},
      {"name": "surface_top",   "dimension": 1,
       "selection": {"box": [-1e-6, 1.899, -1e-6, 1.201, 1.901, 1e-6]},
       "_comment": "bottom face intentionally has NO group: cast on a foundation, adiabatic"}
    ],
    "points": [
      {"name": "gage_core", "coordinates": [0.6, 0.95, 0.0],
       "_comment": "PAPER_VALUE: core sensor, mid-height (Fig. 4)"}
    ],
    "mesh": {"element_size": 0.05}
  },
  "physics": {
    "heat_transport": {"type": "field", "dependent_variable":
      {"name": "temperature", "symbol": "T", "type": "scalar", "components": ["T"]}},
    "hydration": {"type": "ode_source", "dependent_variable":
      {"name": "degree_of_hydration", "symbol": "alpha", "type": "ode",
       "components": ["alpha"], "managed_by": "hydration"}}
  },
  "materials": {
    "concrete": {
      "temperature": {"k": 2.8, "rho": 2387.0, "cp": 950.0,
                      "_comment_k": "PAPER_VALUE: Annexe C"},
      "degree_of_hydration": {"model": "affinity",
        "B1": 3.6e-4, "B2": 5.0e-4, "alpha_inf": 0.85, "eta": 6.0,
        "Ea": 36583.8, "R": 8.314, "T_ref": 298.15,
        "Hu": 470.0, "c_cement": 350.0,
        "_comment_T_ref": "KELVIN — the only Kelvin temperature in this file",
        "_comment_sanity": "dT_ad = Hu*c_cement*alpha_inf/(rho*cp) = 470e3*350*0.85/(2387*950) = 61.7 K; measured rise 39 K -> the BCs must carry off 22.7 K. dT_ad EXCEEDS the measured rise, as it must."}
    }
  },
  "initial_conditions": [
    {"field": "temperature", "value": 20.0, "location": "concrete_domain",
     "_comment": "PAPER_VALUE: casting temperature 20 degC. DEGREES CELSIUS."},
    {"field": "degree_of_hydration", "value": 0.0, "location": "concrete_domain"}
  ],
  "boundary_conditions": [
    {"type": "robin", "field": "temperature", "location": "surface_left",
     "alpha": 7.29, "u_ref": 10.0,
     "_comment": "PAPER_VALUE: 2 cm timber formwork, 26.25 kJ/(h.m2.K) / 3.6 = 7.29 W/(m2.K); kept throughout, so constant. u_ref = time-weighted mean of the measured ambient — the paper plots no ambient history; if it did, use a file-backed table instead."},
    {"type": "robin", "field": "temperature", "location": "surface_right",
     "alpha": 7.29, "u_ref": 10.0},
    {"type": "robin", "field": "temperature", "location": "surface_top",
     "alpha": 9.72, "u_ref": 10.0,
     "_comment": "PAPER_VALUE: free upper surface, 35 kJ/(h.m2.K) / 3.6"}
  ],
  "assignment": {"domains": {"concrete_domain": {
    "active_fields": ["temperature", "degree_of_hydration"],
    "material": "concrete"}}},
  "time_stepping": {
    "t_start": 0.0, "t_end": 504000.0, "analysis_type": "transient",
    "dt_strategy": {"type": "piecewise", "segments": [
      {"t_end": 14400,  "strategy": {"type": "log", "dt_min": 40,   "dt_max": 480,   "n_steps": 40}},
      {"t_end": 172800, "strategy": {"type": "log", "dt_min": 390,  "dt_max": 3900,  "n_steps": 80}},
      {"t_end": 504000, "strategy": {"type": "log", "dt_min": 1840, "dt_max": 14750, "n_steps": 60}}]}
  },
  "results": {
    "reference_curves": [
      {"id": "sonde3_exp", "file": "reference/sonde3.csv", "has_header": true,
       "x_col": "time_h", "y_col": "T_degC", "label": "Sensor 3 (Experiment, Fig. 7)"}
    ],
    "queries": [
      {"id": "t", "type": "time", "display": false},
      {"id": "t_hours", "type": "formula", "formula": "t / 3600.0",
       "depends_on": ["t"], "label": "Time [hours]"},
      {"id": "T_core", "type": "field", "field": "temperature", "component": "T",
       "at": "gage_core", "reduce": "avg", "evaluate_in": "fem",
       "label": "Core temperature [degC]",
       "_comment": "field is already degC — NO 273.15 conversion"}
    ],
    "plots": [
      {"name": "temperature_history", "title": "Core Temperature", "grid": true,
       "x_axis": {"label": "Time [hours]"}, "y_axis": {"label": "Temperature [degC]"},
       "curves": [
         {"source": "simulation", "x": {"key": "t_hours"}, "y": {"key": "T_core"},
          "label": "FEM"},
         {"source": "reference", "ref_id": "sonde3_exp", "label": "Experiment"}]}
    ],
    "console": {"summary_every_steps": 20,
      "reference_deviation": {"enabled": true, "show_in_progress_bar": true,
        "pairs": [{"x": "t_hours", "y": "T_core", "ref_id": "sonde3_exp"}]}},
    "save_checkpoint": true, "aux_fields": ["*"], "export_vtu": true, "save_db": true
  }
}
```
