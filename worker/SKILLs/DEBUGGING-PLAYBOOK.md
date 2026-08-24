# Debugging a benchmark against its paper

Procedure for diagnosing why a simulation disagrees with the published figure it is
supposed to reproduce. Written to be followed in order. Do not skip ahead to editing
parameters — that is step 7, and most of the time you never get there because the
problem turns out to be structural.

---

## 0. Run it before you read it

The config is a *claim* about what the code does. The run is *evidence*.

```bash
thcm oofem <benchmark_dir>/          # or the project's equivalent
```

Then sort into one of three buckets. They need completely different work:

| Bucket | Meaning | Go to |
|---|---|---|
| A | Does not run | §1 |
| B | Runs, disagrees | §2–§6 |
| C | Runs, agrees | §7 — the dangerous one |

**Verify the outputs are from the current config.** Stale artifacts are the most common
way to debug a problem that no longer exists.

```bash
sqlite3 <out>/query_results.db ".schema"   # column names
grep '"id"' benchmark.json                 # query ids
# names disagree -> outputs predate the config; re-run before believing anything
```

Also check row count vs step count, and that every `reference_curves[].file` exists.

---

## 1. Follow the error down to physical evidence

An error names a symptom at the layer where it was *noticed*, usually several layers
above the cause. Descend until you are printing bytes on disk or reading third-party
source. Each step down must be something you can **print**, not something you reason about.

> **Rule:** when a tool reports success and its consumer reports absence, the loss is in
> the format between them. Print the intermediate artifact.

Worked case: `Location 'fixed_end' not found in boundary_sets`, one line below the mesh
generator logging `Created physical group 'fixed_end'`. Both true. Descent:
raising line → mesh loader → what `meshio` returns → the `$Entities` block of the `.msh`.
The curve carried **two** physical tags and the reader keeps only the first. Two boundary
groups had been defined on the same geometric edge.

```python
import meshio, numpy as np
m = meshio.read(path, file_format="gmsh")
print(m.field_data)                                    # name -> (tag, dim)
for cb, ph in zip(m.cells, m.cell_data["gmsh:physical"]):
    print(cb.type, len(cb.data), np.unique(ph))        # which tags SURVIVED
```

---

## 2. Build an independent model of the expected answer

**The highest-value step, and the one most often skipped.** Before deciding the solver is
wrong, write something small that predicts the same number a different way.

It does not need to be good. It needs to be **independent**. A 60-line finite-difference
script, a lumped-capacitance estimate, dimensional analysis. Its only job:

> Is the solver computing the wrong answer, or faithfully computing the answer to the
> **wrong question**?

Worked case — a cube peaking 9 K too hot:

| Configuration | Peak | At 24 h |
|---|---|---|
| Production solver, 2D | 64.0 °C | 36.0 °C |
| Scratch FD, 2D | 63.7 °C | 36.0 °C |
| Scratch FD, 3D | 57.0 °C | 27.5 °C |
| Measured | 55.1 °C | 27.6 °C |

Agreement to 0.3 K in 2D **cleared the solver entirely**. The error was that a cube cools
through six faces and a 2D cross-section through four — a modelling decision, invisible
while staring at the solver.

**Run the cheap model first, to predict.** A lumped estimate `τ = ρcV/hA` gave
32.5 °C @ 15.7 h; the full run later gave 32.4 °C @ 15.2 h. Everything after was
confirmation rather than discovery.

Useful order-of-magnitude checks before any simulation:
- surface/volume ratio (2D vs 3D, or a longitudinal slice vs a real cross-section)
- time constant `τ = ρc·V/(h·A)` against the driving event's duration
- adiabatic bound `ΔT_ad = Q·c/(ρ·cp)` vs the measured rise

---

## 3. Ground truth from the source, never from the comments

Comments are hypotheses written by a past author under time pressure. Every one of these
was written in good faith and was false:

- "Air temperature history is missing" — the file was in `input/`, used by a sibling.
- "Reproduces within ~5–8%" — the actual output was 46% short.
- "Expect ~700 d equivalent age" — correct value 328 d; the run gave 327.9.

**Digitize figures properly** rather than eyeballing. Render at 300 dpi, calibrate on the
tick marks, extract by colour or connected components. Minutes of work, and it converts an
argument into a measurement — including things you were not checking, e.g. an event the
config placed at 165 d sitting at 154.7 d in the figure.

```python
# calibrate: find the tick ladder, then map pixels -> data
t_of = lambda x: (x - X0) / (X1 - X0) * T_SPAN
v_of = lambda y: V0 + (Y0 - y) * (V1 - V0) / (Y0 - Y1)
# separate series by colour where possible; for black-on-white use
# scipy.ndimage.label + binary_fill_holes (open markers have a hole, lines do not)
```

**Audit the reference data itself.** One "experimental" file traced the paper's *model*
curve from 80 d onward and carried points where the figure has no data at all.

---

## 4. Sweep the suspect parameter before believing your own theory

You will form a hypothesis about which knob is wrong. Test it: set it to **zero** and to
**10×**, and see whether the output cares. Cheap, decisive, and it will regularly kill
hypotheses you were confident about.

| Setting | Result | Contribution |
|---|---|---|
| Target (paper) | −0.880e-3 | — |
| Mechanism **off** | −0.287e-3 | — |
| As configured | −0.292e-3 | 0.6% |
| ×3 | −0.301e-3 | 1.6% |
| ×10 | −0.331e-3 | 5.0% |

Turning the mechanism off entirely moved the answer by 0.6%. Hypothesis dead in one batch
of runs instead of a week of tuning.

**Check for degeneracy.** Two of those runs came out bit-identical (max diff 3e-18 over
613 steps) — meaning the two parameters only ever appear as a *product*. One knob, not two.

Run sweeps on **copies** in a scratch directory, never on the live benchmark.

---

## 5. Read the third-party source when a convention is in question

Units, key names, defaults and normalisation factors are not derivable from docs. Two
`grep`s, and both outcomes are valuable: it upgrades a guess to a verified fact, or kills
your theory.

Both happened here. The solver's C++ carried a comment defining the exact parameter
formula the config had inferred (mapping confirmed). The same read disproved my own theory
about a missing `1/T_room` factor — it was already applied internally.

And a refusal that looked like an over-cautious wrapper was real:

```
rheoChM.h:52        #define _IFT_RheoChainMaterial_n      "n"
doublepowerlaw.h:46 #define _IFT_DoublePowerLawMaterial_n "n"
```

Same input key, read twice into different physical quantities. A genuine upstream limit.

---

## 6. Explain the magnitude, not just the direction

"Too small" is an observation. "Too small by 100×, because this timescale is 9 days and
that one is 3 hours" is a diagnosis — and only the second tells you whether it is fixable.

Worked case: from the governing equation the relaxation time is `1/(2AB)` = **9.0 days**;
the event driving it is a **3 hour** ramp. So the state variable moves 1.2% during the
event, matching the measured 0.6% effect. Making it matter needs the parameter product
~5000× larger. Question closed — not "try a bigger value" but "no defensible value works,
here is the arithmetic."

The magnitude also classifies the error, which determines what you may do about it:

- **Scale error** — a clean multiplicative factor (e.g. `σ ∝ 1/(1−ν)`). *Recoverable*:
  publish the raw result plus a corrected one, and label both.
- **Kinetics error** — a wrong exponent or rate. *Not* recoverable by post-hoc scaling.

If forced to choose between them, sacrifice the scale error and keep the kinetics intact.

---

## 7. Report honestly — including cancellations

Agreement is not validation if two errors pull in opposite directions. One benchmark's
final stress landed at +2.98 MPa against a published +2.9 — excellent and largely
meaningless: a forced Poisson's ratio inflated it while a missing ageing law deflated it.
Write that into the file. It is the difference between a benchmark and a trap.

**Sweep up orphans** — three cheap inventories that repeatedly find real problems:

```python
import json, os
b = json.load(open("benchmark.json")); R = b["results"]
declared = {r["id"] for r in R["reference_curves"]}
plotted  = {c["ref_id"] for p in R["plots"] for c in p["curves"] if c.get("ref_id")}
files    = {r["file"] for r in R["reference_curves"]}
on_disk  = {f"reference/{f}" for f in os.listdir("reference")}
queried  = {q["id"] for q in R["queries"]}
shown    = {c["y"]["key"] for p in R["plots"] for c in p["curves"] if c.get("y")}

print("dangling ref_ids   :", plotted - declared)
print("declared, unplotted:", declared - plotted)
print("missing files      :", {f for f in files if not os.path.exists(f)})
print("on disk, unused    :", on_disk - files)
print("computed, unshown  :", queried - shown)
```

**Look for the working sibling.** Before deriving anything, check whether an adjacent case
in the same repository already solved it. One benchmark listed two problems as unsolved
that its sibling — same parent directory — had calibrated data for. Diffing the two beat
any amount of derivation.

---

## Working rules

1. **Evidence outranks logs, which outrank comments.** A tool reporting success does not
   mean its consumer received anything.
2. **Independence beats sophistication.** A crude model that disagrees is worth more than
   a refined one that agrees.
3. **Predict before you run.** Otherwise you cannot tell a surprising result from an
   expected one.
4. **Test the knob before blaming the knob.**
5. **Read the source for conventions.**
6. **Derive the magnitude.** It separates a mis-set parameter from a structural limit.
7. **Never silently tune to fit.** Fix mappings; document divergences. Tuning until the
   curve matches destroys the benchmark's only purpose.
8. **Correct yourself immediately and briefly.** State it in one sentence and continue.

## Where this goes wrong

- **Tuning before verifying.** You will find a value that fits, and it encodes the bug
  permanently.
- **Concluding from a partial read.** I called an append-only DB write a bug after reading
  the writer but not its caller — which deleted the file on every run. Read the call site
  before naming a defect.
- **Trusting a plausible first cause.** Every wrong hypothesis here was plausible; that is
  what made them expensive.
- **Believing agreement.** The reference data can be wrong, the published figure may not be
  reproducible from its own printed parameters, and two errors can cancel.
