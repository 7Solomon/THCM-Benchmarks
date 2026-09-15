# MODULE: Results: queries and plots

> reference curves, every query type, plots and console output

<!-- Loaded on demand by CREATE.md's START-HERE routing table. Structure is
enforced by SKILLs/benchmark.schema.json; the mechanical cross-key rules by
worker/tools/check_benchmark.py. This file carries only what needs judgement. -->

### `results`

#### `results.reference_curves`

```json
{ "id":"exp", "file":"reference/xxx.csv", "has_header":true,
  "x_col":"time_h", "y_col":"T_degC", "label":"Experiment (Fig. 7)" }
```
Digitize the paper's validation figures into CSVs under `reference/` (path relative to
the benchmark directory). Note in a `_comment` which figure was digitized and any
sign/axis flips. **If the paper also plots its own model, digitize that too** — a
model-vs-model comparison isolates your errors from the paper's.

#### `results.queries`

Common keys: `id` (required), `label`, `display: false`, `evaluate_in: "fem"`.

| type | keys |
|---|---|
| `time` | – |
| `field` | `field`, `component`, `at` (point or group; omit = whole domain), `reduce` |
| `reaction` | `component`, `at`, `reduce: "sum"` |
| `stress` / `strain` | ⚠️ **COMSOL-ONLY — see below.** On fem/oofem use a `field` query. |
| `difference` | `field`, `at_a`, `at_b`, `reduce` |
| `line_profile` | `field`, `axis` (0/1/2), `start`, `end`, `n_samples` |
| `formula` | `formula`, `depends_on`; runs last; scope = all other query ids |

`reduce` ∈ `avg|mean|max|min|sum|norm`. ⚠️ **Anything else — including `"value"` —
silently falls back to mean.**

`pore_pressure` IS an ordinary nodal field — query it like any other
(`{"type":"field","field":"pore_pressure","component":"p","at":...}`). `damage` does
not exist on a `hydro_mechanical` run — the Biot material has none.

> ### ⚠️ `type: "stress"` / `type: "strain"` do NOT work on fem or oofem
> `q_stress`/`q_strain` call `ctx.stress_component()`/`ctx.strain_component()`, which
> are implemented **only** in `comsol/evaluate_adapter.py`. `FEMContextAdapter` — the
> adapter used by BOTH the fem and oofem backends — does not define them, so the query
> raises `AttributeError` on **every step** and its id is simply absent from the
> results. Nothing about your JSON causes this; the query type is COMSOL-only.
>
> **Use a `field` query against the element tensor instead.** The oofem extractor
> already publishes the IP state under the names `stress` (alias `mechanical_stress`),
> `strain` (alias `total_strain`) and `damage`:
>
> ```json
> {"id":"sigma_yy","type":"field","field":"stress","component":"syy",
>  "at":"probe_mid","reduce":"avg","label":"sigma_yy [Pa]"}
> ```
>
> **The component name carries a prefix** — `s` for stress, `e` for strain — because it
> comes from `Tensor.to_dict()`: `sxx syy szz sxy sxz syz` / `exx eyy ezz exy exz eyz`.
> In 2D only `xx`, `yy`, `xy` exist. A bare `"yy"` is rejected with the list of valid
> names. IP values are averaged onto nodes.
>
> `coordinate_system: "cylindrical"` (hoop/radial) has **no** equivalent on this path;
> it is COMSOL-only along with the query types themselves.
>
> Sign/meaning caveat on `hydro_mechanical`: whether `upm` reports the EFFECTIVE or the
> TOTAL stress is not documented. Pin it against a known state before using it as a
> validation curve — and if `upm` turns out not to publish `IST_StressTensor` at all,
> the key is silently omitted and you get `Unknown field 'stress'` instead.

> ### ⚠️ `line_profile` `start`/`end` take **mesh-dimension** coordinates, not 3
> This is the one place that **inverts** the "every coordinate vector is 3 values,
> z = 0 in 2D" rule. On a 2D mesh the endpoints must have exactly **2** values; a third
> raises `line_profile 'start' has 3 coordinates but the mesh is 2-dimensional` on the
> first step. The reason is that these endpoints are used to *sample the field* rather
> than snapped to the nearest node (which is what makes the padding safe for
> `geometry.points`), and padding a genuine axis would silently sample the wrong plane.
> A bare scalar is also accepted and is placed on `axis`.
>
> **The failure comes with a misleading second warning.** When a query raises, its id is
> absent from the results, and `_warn_unfired_at_time_queries` reports every absent
> `at_time` query as *"never fired: at_time=X is outside the simulated window"* —
> without checking whether that is true. So a 3-coordinate endpoint on an `at_time`
> profile produces a `ValueError` **and** a bogus at_time complaint. Fix the arity; the
> at_time message goes away. (`at_time` exactly equal to `t_end` **does** fire: the test
> is `prev_t < at_time <= t`, and the last step crosses it.)

> ### `degree_of_hydration` IS queryable — as an element field, with **no `component`**
> It has no DOFs (it is material-internal state at the integration points), so it does
> NOT go through `dm.fields` like temperature. It arrives on the same path as `stress`
> and `damage` — the element-output branch — which changes two things about how you
> write the query: **omit `component`** (it is a bare scalar, there is no `alpha`
> component name), and the value at a probe is the **nearest element's** IP average,
> not a nodal value.
>
> ```json
> {"id":"alpha","type":"field","field":"degree_of_hydration",
>  "at":"gage_core","reduce":"avg","evaluate_in":"fem","label":"Degree of hydration"}
> ```
>
> Three names come off the hydration material, all scalars, all spelled this way:
>
> | field | quantity | unit |
> |---|---|---|
> | `degree_of_hydration` | α | – |
> | `equivalent_age` | Arrhenius-maturity clock t_e | s |
> | `maturity` | Nurse–Saul maturity ∫(T − T₀)dt | °C·h |
>
> ⚠️ `maturity`'s datum T₀ is oofem's `maturityT0`, which this backend never sets, so
> it is **0 °C** — not the −10 °C of the usual Nurse–Saul definition. A paper's
> maturity curve will not line up without shifting by T₀·t. Prefer `equivalent_age`,
> which needs no datum.
>
> They appear **only where a `degree_of_hydration` material section is present** — on
> any other material the key is absent and the query fails with `Unknown field`.
> That is deliberate and worth trusting: oofem itself would happily answer
> `IST_HydrationDegree` with a literal **0.0** on a plain `isoHeat`
> (isoheatmat.C:121), so the backend declares what each material actually carries
> rather than asking. Without that you would get a complete, plausible, entirely
> fictional α ≡ 0 history instead of an error.
>
> They also reach the VTU export automatically (as `IST_HydrationDegree` /
> `IST_EquivalentTime`) whenever hydration is active and `export_vtu` is on — the
> same declaration drives both, so a plot and a ParaView view cannot disagree.
>
> ⚠️ **Cross-backend comparison caveat (D2).** The oofem backend runs oofem's own
> `hydratingConcreteMat`; the legacy backend runs the Python affinity kernel. They are
> the *same* Cervera model with a direct parameter translation, but a plot combining
> both must say so in its label.

> ### The mps creep/shrinkage decomposition is queryable too
> Wherever an mps chain was built (`b3_creep`, `autogenous_shrinkage`,
> `drying_shrinkage` or `thermal_aging` on the material), the mechanics engine
> also publishes — same element-field path, same rules:
>
> | field | quantity | component |
> |---|---|---|
> | `creep_equivalent_age` | mps's own maturity clock [s] | none (scalar) |
> | `creep_strain` | creep strain tensor | `exx`, `eyy`, `exy`, … |
> | `autogenous_shrinkage_strain` | autogenous part | ditto |
> | `drying_shrinkage_strain` | drying part | ditto |
> | `total_shrinkage_strain` | autogenous + drying | ditto |
>
> This is how you separate the strain components a paper plots separately.
> `total_strain` (the tensor block) minus `creep_strain` minus the shrinkage
> terms is the elastic part; there is no query that returns it directly.
>
> ### The damage-plastic models publish their own state
> With `model: cdpm2` or `cdpm1` the mechanics engine also publishes — same
> element-field path, same rules:
>
> | field | model | quantity | component |
> |---|---|---|---|
> | `damage_tension` | cdpm2 | tensile damage [0..1] | none (scalar) |
> | `damage_compression` | cdpm2 | compressive damage [0..1] | none (scalar) |
> | `dissipated_work` | cdpm2 | dissipated energy density | none (scalar) |
> | `cumulative_plastic_strain` | cdpm1 | κ, the hardening variable | none (scalar) |
> | `plastic_strain` | both | plastic strain tensor | `exx`, `eyy`, `exy`, … |
> | `damage` | cdpm1, damage_mazars, damage_rankine | scalar damage [0..1] | none |
>
> ⚠️ **`cdpm2` does NOT publish `damage`** — query `damage_tension` /
> `damage_compression`. It never implements OOFEM's `IST_DamageScalar`, and its
> `IST_DamageTensor` is not a tensor but a bag of three scalars, which is why the
> backend declares them one by one (D16(c)).

> ⚠️ `creep_equivalent_age` is published whatever the `coupledanalysistype` — with
> no `thermal_aging` section it simply runs at real-time rate. A flat comparison
> against real time is the quickest check that your aging coupling actually took.

Always define: a natural-time formula (`t/3600` → hours, `t*1.157e-5` → days), and the
paper's validation quantity in the paper's plotted units and sign convention.
**Temperature needs no conversion — the field is already °C.**

#### `results.plots`

```json
{ "name":"temperature_history", "title":"…", "grid":true,
  "x_axis":{"label":"Time [hours]"}, "y_axis":{"label":"Temperature [°C]"},
  "curves":[
    {"source":"simulation","x":{"key":"t_hours"},"y":{"key":"T_core"},"label":"FEM"},
    {"source":"reference","ref_id":"exp","label":"Experiment"}]}
```
Curve `x`/`y` keys reference query ids in final units.

**Three curve sources**, and they take *different* keys:

| `source` | keys | plots |
|---|---|---|
| `simulation` | `x: {key,…}`, `y: {key, scale, offset}` | two scalar query series against each other |
| `reference` | `ref_id` | a digitised CSV |
| `query` | `query_key`, **`axis`**, `scale_x/y`, `offset_x/y` | a **spatial** result — this is how a `line_profile` is drawn |

> ### ⚠️ A `line_profile` needs `axis` set TWICE — the two are unrelated
> `axis` is the name of two different keys, and only the **plot-curve** one selects
> what the x-axis shows:
>
> | where | what it does |
> |---|---|
> | `results.queries[].axis` | used **only** to place a *scalar* endpoint on that axis. When `start`/`end` are points — the normal case — it is **completely inert**, and it is never carried into the result object. |
> | `results.plots[].curves[].axis` | which coordinate column becomes the plot's x. **Defaults to `0`.** |
>
> So a profile running down a column (`start: [0.04,-1.0]`, `end: [0.04,0.0]`,
> `axis: 1` on the query) plots the **constant x = 0.04** unless the curve *also* says
> `"axis": 1`. The symptom is unmistakable once you know it: **the simulated curve is a
> single vertical stripe** at the profile line's fixed coordinate, with the right value
> range but no shape. The values are fine — only the abscissa is wrong.
>
> ```json
> {"source":"query","query_key":"p_profile_t10","axis":1,"label":"FEM, t = 10 s"}
> ```
>
> **Rule: `curves[].axis` must equal the index of the coordinate that actually varies
> between `start` and `end`.** Horizontal profile → `0`; vertical → `1`; through-depth
> in 3D → `2`. Write it on the curve every time, even when it is 0.

#### `results.console`

```json
"console": { "summary_every_steps":20,
  "reference_deviation": { "enabled":true, "show_in_progress_bar":true,
    "pairs":[{"x":"t_hours","y":"T_core","ref_id":"exp"}] } }
```
Each pair creates a live `dev_<y>__<ref_id>` column — the primary validation signal.

#### Export flags

```json
"aux_fields": ["*"], "save_checkpoint": true, "export_vtu": true, "save_db": true
```

