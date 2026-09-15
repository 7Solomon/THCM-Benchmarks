# MODULE: Hygrothermal (heat + moisture)

> the humidity field and the monolithic Kunzel coupling. THIS DOMAIN IS KELVIN.

<!-- Loaded on demand by CREATE.md's START-HERE routing table. Structure is
enforced by SKILLs/benchmark.schema.json; the mechanical cross-key rules by
worker/tools/check_benchmark.py. This file carries only what needs judgement. -->

#### `humidity`

- `bazant_najjar`: `capacity` [-], `D0` [m²/s at h=1], `hc` (0–1), `n`, `alpha` (0–1).
  Bažant–Najjar's sigmoid D(h); set `alpha` = 1 to make it **linear** (D ≡ D0).
- `isolin_moisture`: `capacity` [-], `D0` [m²/s] — plain linear diffusion, the right
  choice when the paper's own formulation is linear. Prefer it over a
  `bazant_najjar` degenerated with `alpha` = 1: same physics, no inert knobs to
  explain.
- `kunzel` (**only** under `heat_moisture_transport` — on a moisture-only field it is
  rejected, because oofem would silently answer with the material's THERMAL
  conductivity block): `wf` [kg/m³], `iso_b` **or** `w80`, `A` [kg/(m²·s^0.5)],
  `mu` [-], `lambda_moisture_coeff` [-]; optional `cw`, `hv`, `pl`, `rhoh2o`.
  The isotherm is fixed to Kunzel's two-parameter form
  w(h) = wf·(b−1)·h/(b−h); a paper whose isotherm is van-Genuchten- or
  Hansen-shaped cannot be reproduced exactly — say so in `metadata.notes` rather
  than fitting silently. λ follows λ0·(1 + b·w/ρ), so a paper quoting
  λ = λ0 + c·w maps exactly with `lambda_moisture_coeff` = c·ρ/λ0.


> ### The hygrothermal temperature field is KELVIN — the opposite of every other
> The Kunzel material (`heat_moisture_transport`) evaluates saturation pressure and
> latent heat from `T − 273.15` internally, so its temperature DOF must be absolute.
> Hydration's material does the opposite. **The two conventions cannot coexist**, and
> combining `degree_of_hydration` with `heat_moisture_transport` is rejected for
> exactly that reason.
>
> A guard rejects hygrothermal temperature IC/BC values below 200 as "looks like °C",
> so this fails loudly — but write it right: on a `heat_moisture_transport` domain
> every temperature IC, Dirichlet `value` and Robin `u_ref` is in **Kelvin**
> (20 °C → 293.15), and so is every temperature column of a climate CSV.
>
> The humidity DOF of that same domain is **relative humidity, 0–1** (not w, not
> vapour pressure). A vapour-pressure boundary history must be converted to φ before
> it reaches the config — which is what the `climate2csv` prep step does.
> ⚠️ hemokunzel **throws** on h > 1 ("Relative humidity is out of range"), so check
> the converted φ range: outdoor climate data legitimately goes supersaturated during
> radiative night-cooling, and the run will abort at the first such step.


## A long climate table comes from a CSV

> ### A long table comes from a CSV, not from the JSON
> Anywhere a `time_table` is allowed, the rows may instead be read from a **column of
> a CSV** — the same thing, spelled differently. A year of hourly climate data is
> 8760 rows per series; it does not belong inline.
>
> ```json
> {"type":"robin","field":"temperature","location":"face_exterior",
>  "alpha": 25.0,
>  "u_ref": {"file":"input/bench1.csv","column":"T_eq_e","interp":"linear",
>            "period": 31536000.0,
>            "_comment":"PAPER_VALUE: T_eq,e from ClimateBench1.txt, one year repeated"}}
> ```
>
> | key | meaning |
> |---|---|
> | `file` | CSV **relative to the benchmark directory**, same convention as `reference_curves`. Must have a header row. Put it in **`input/`** — it is a model input, next to `input/geometry.msh`; `reference/` is for the paper's validation data only. |
> | `column` | value column name |
> | `time_column` | time column name, **absolute seconds** (default `t_s`) |
> | `interp` | `step` (default) or `linear` — how the value behaves *between* rows |
> | `scale`, `offset` | `value = column*scale + offset`. Keeps a paper's own coefficient in this file next to its citation. |
> | `period` | seconds; tile the file over the run, for data covering one cycle that repeats |
>
> The table is expanded to the inline form at config load, so a dumped config shows
> exactly what the solver will use, and each expanded entry carries a `_source` key
> naming the file and column it came from.
>
> **Raw data files are converted once, up front** — `tools/extract/climate2csv.py`
> turns a HAMSTAD `ClimateBench*.txt` into a canonical CSV in SI units (K, not °C)
> with relative humidity and `p_sat` already derived. The pipeline reads CSV only.
> `p_sat` there is oofem's own correlation (`tools/simulations/model/psychrometrics.py`),
> which matters: computing φ = p/p_sat with a *different* correlation than the material
> uses internally is a permanent, invisible bias.
>
> ⚠️ **The time grid must resolve the table.** Hourly data on a six-hourly grid drops
> five of every six rows and the run still looks healthy. A `step` table whose
> value-changing rows miss every solver step is a **hard error** naming the first
> missed breakpoint and the `n_steps` that would fix it; a `linear` table warns.
> Rows that merely repeat the previous value never trip it.
- Time functions must span the whole run. A `piecewise_linear` table that ends before
  `t_end` clamps to its last entry.

