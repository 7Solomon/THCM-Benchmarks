# MODULE: Creep + shrinkage

> b3_creep, power_law_creep, autogenous and drying shrinkage, thermal_aging

<!-- Loaded on demand by CREATE.md's START-HERE routing table. Structure is
enforced by SKILLs/benchmark.schema.json; the mechanical cross-key rules by
worker/tools/check_benchmark.py. This file carries only what needs judgement. -->

#### `autogenous_shrinkage`

> ⚠️ **On OOFEM this is NOT ε = shrinkage_max·(α/α_inf), and it does NOT require
> hydration.** That is the *legacy* `fem/` law. OOFEM uses mps's **B4** curve on
> **equivalent age** — `shrinkage_max` is only its asymptote, and the B4 time-shape
> (`tau_au`/`alpha`/`r_t`) is fixed by the builder. Documented divergence **D9**
> ([`mps/autogenous.py`](../tools/simulations/oofem/src/builder/materials/solid_mechanics/mps/autogenous.py)).
> The two backends therefore give *different curves from the same config* — say so on
> any plot comparing them.

| key | unit | notes |
|---|---|---|
| `shrinkage_max` | – | ultimate autogenous strain. **Sign is NOT constrained** — see below. **Omit the whole section if the paper doesn't quantify it.** |
| `alpha_inf` | – | legacy normalisation; **inert on OOFEM** (B4 has no α term) |
| `age_at_start_days` | d | material age at t=0, needed for shrinkage-only runs |

**Swelling is legitimate.** A positive `shrinkage_max` is passed straight through;
OOFEM's own B4 cement table yields positive `eps_au_infty` for some cement types
(`mps.C:410`). Early-age concrete at high w/b genuinely swells before it shrinks, and
the old "≤0" rule in this file was both unenforced and wrong.

⚠️ What B4 *cannot* do is **reverse** — it is monotone in equivalent age, so
swelling-then-contraction is out of reach through this section. That shape is
reachable via mps `autoshrinkagetf` (an arbitrary time function on equivalent age),
which is **not wired** — see the NOT WIRED table.

#### `b3_creep`

`q1 q2 q3 q4` [1/Pa] (q1 ≈ 1/E28), `E28` [Pa], `n` (0.1), `m` (0.5), `r` (1.7), and
**`age_at_start_days`** (required on this backend — the material age at t=0).
If the paper has no creep data: **omit the coupling entirely** rather than guessing.

#### `power_law_creep`

The **double power law** (oofem `doublepowerlaw`), an alternative to `b3_creep`:

```
J(t,t') = 1/E0 + (fi1/E0)·(t−t')^n·(t'^−m + α),    E0 = 1.5·E28
```

| key | unit | notes |
|---|---|---|
| `E28` | Pa | 28-day modulus. **The instantaneous compliance is 1/(1.5·E28), not 1/E28** — oofem takes the asymptotic modulus as 1.5× the 28-day one. Pass `E28 = E/1.5` if your source quotes an instantaneous `E` |
| `phi_inf` | – | basic creep coefficient (oofem `fi1`) |
| `n` | – | (t−t') exponent — **and Poisson's ratio, see the trap** |
| `m` | – | loading-age exponent; `0` = age-independent creep |
| `alpha` | – | age-independent floor added to t'^−m |
| `age_at_start_days` | d | **required**, must be > 0 — the compliance goes as t'^−m and is singular at t'=0 |

`t1` and `t_load` are legacy-`fem/` keys: the double power law has no t1, and the
loading age is `age_at_start_days`. Both are ignored **loudly** here.

> ### ⚠️ TRAP — `power_law_creep.n` IS Poisson's ratio
> OOFEM reads the input key `"n"` **twice**: `RheoChainMaterial` takes it as
> Poisson's ratio (`rheoChM.C:589`) and `DoublePowerLawMaterial` takes the *same
> key* as the power-law exponent (`doublepowerlaw.C:47`). There is no second
> spelling for either. A material with `nu = 0.2` and `n = 0.13` is **not
> expressible on this build**.
>
> This backend **rejects `displacement.nu ≠ power_law_creep.n`** rather than
> silently setting Poisson's ratio to the creep exponent. If your paper needs them
> to differ, use `b3_creep` (mps hardcodes its B3 exponents, so nothing collides)
> or patch OOFEM to give the exponent its own keyword.

> ### ⚠️ It is a *plain Maxwell chain* — nothing rides on it
> `autogenous_shrinkage`, `drying_shrinkage`, `thermal_aging` and
> `damage_rankine` are all **mps** features. Pairing any of them with
> `power_law_creep` is **rejected**, not silently dropped. Declaring both
> `b3_creep` and `power_law_creep` is rejected too — a material is one chain.

#### `drying_shrinkage`

`k_sh` [-] (ε_sh = k_sh·Δh), `h_ref`.

Three hard requirements on this backend:
1. a **`b3_creep` section on the same material** (one material hosts both);
2. **`mus`** [1/(Pa·s)] — microprestress flow viscosity;
3. **`h_ref` MUST equal the humidity initial condition.** Shrinkage is integrated in
   rate form from the IC, so it only equals k_sh·(h − h_ref) when the IC *is* h_ref.
   A mismatch is rejected.

#### `thermal_aging`

Switches the mps creep chain from **real time** to **temperature-adjusted
equivalent time** — the aging half of a thermo-chemo-mechanical run.

| key | unit | notes |
|---|---|---|
| `kTm` | – | **required** — replaces ln(h) in the microprestress equation when temperature is the only driver |
| `mus` | 1/(Pa·s) | **required** — mps microprestress flow viscosity, same parameter `drying_shrinkage` needs |
| `Q_E_over_R` | K | activation energy / R for the **aging** term. Default 2700 (Bažant 1995) |
| `Q_R_over_R` | K | ditto for the **viscosity** term. Default 5000 |
| `Q_S_over_R` | K | ditto for **microprestress**. Default 3000 |

Requires a `b3_creep` section on the same material — there must be a chain to age.

> ### ⚠️ `thermal_aging` does NOT read `degree_of_hydration`
> This is the single most common wrong mental model. oofem's mps has **no α
> input**. It integrates its own Arrhenius maturity clock
> `t_e = ∫exp(Q_E/R·(1/298.15 − 1/T))dt` off the same temperature field that
> `hydratingConcreteMat` uses — two independent clocks, and by default
> **different activation energies** (mps 2700 K vs a typical hydration
> Ea/R ≈ 4600 K).
>
> If your paper drives stiffness from a single hydration state, set
> `Q_E_over_R` to your `degree_of_hydration.Ea / 8.314` so both clocks
> agree, and say so in a `_comment`. Otherwise the run silently contains two
> different maturity histories.
>
> Query them as `equivalent_age` (hydration) and `creep_equivalent_age`
> (mps) — deliberately separate names, because both come from the same
> oofem IST and would otherwise shadow each other.

> ### ⚠️ Elevated temperature gives you MORE creep, not less
> The coupling switches on **three** temperature terms that pull in opposite
> directions: aging (`Q_E/R` = 2700 → stiffer), viscosity (`Q_R/R` = 5000 →
> faster creep) and microprestress (`Q_S/R` = 3000 → faster creep). At 45 °C
> those factors are 1.77 / 2.87 / 1.88 — the viscosity term dominates, so the
> net effect of enabling `thermal_aging` is a **larger** creep strain.
>
> If you expected restraint stresses to go *up* because "the concrete matures
> faster", expect the opposite: more creep means more relaxation.

