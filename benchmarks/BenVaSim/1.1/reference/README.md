# Reference data — BenVaSim Model 1.1, scenario (a)

Two kinds of file live here.

| | |
|---|---|
| `steady_state_profiles.csv` | **analytical**, computed from the report's equations. Not digitised. 101 rows; agrees with the report's own Figs 4.10/4.11 to 2.8e-4 MPa and 2.3e-4 mm. |
| `transient_probes.csv` | ⚠️ **EMPTY — header row only, no data.** Regenerate it or leave it alone, but do not wire it into `benchmark.json`: it used to back `an_transient`, which meant the transient plot and `console.reference_deviation` were comparing against nothing at all. Both now use the digitised Fig. 4.8 analytical curves instead. |
| `fig4.*/` | **digitised from the report's figures**, one directory per figure. |

Source PDF: `debug/BenVaSim/Synthesebericht_BenVaSim_Final.pdf`
(Lux et al. 2021, synthesis report, BMWi joint project BenVaSim 02E11567A-B).

## The digitised figures

| directory | figure | PDF page | x | y | curves |
|---|---|---|---|---|---|
| `fig4.7_pw_time_linear`    | 4.7  | 40 | time, a (linear) | pore-water pressure, MPa | 45 |
| `fig4.8_pw_time_log`       | 4.8  | 40 | time, a (**log**) | pore-water pressure, MPa | 45 |
| `fig4.9_pw_time_log_first` | 4.9  | 41 | time, a (**log**) | pore-water pressure, MPa | 45 |
| `fig4.10_pw_profile`       | 4.10 | 41 | position, m | pore-water pressure, MPa | 64 |
| `fig4.11_u_profile`        | 4.11 | 42 | position, m | displacement, m | 57 |

256 curves, 33 366 points in total.

Figs 4.7/4.8 are the same data on linear and log time axes: 8 partner codes × 5
evaluation positions (0.25, 0.75, 1.25, 5, 9.75 m) + 5 analytical curves.
Figs 4.10/4.11 are 8 partner codes × 7 profile times (0.003, 0.1, 0.3, 1, 3, 10,
30 a) + the analytical profiles.

**`fig4.9` is not validation data.** The caption reads "*First* simulation
results"; it shows the partners' pre-refinement submissions, i.e. the results
that were subsequently corrected. Fig 4.8 is the same plot after refinement.
Use 4.8, and keep 4.9 only to see what the early disagreement looked like.

### How the data was obtained

Read from the figures' **vector path geometry** with `tools/extract`
(`pdfplot2csv.py`), not traced off pixels: every curve comes out complete even
where it is drawn underneath twenty others, which matters a great deal here —
in Fig 4.7 all eight codes lie on top of one another for most of the run.
Calibration is a least-squares fit through the axis tick positions the PDF
stores.

Each directory holds one CSV per curve, a stacked `<prefix>_ALL.csv` with
`name,time|position,team,x,y` columns, `raw_unclipped/` (the part of each path
the figure clipped away), `_meta.json` (calibration + per-curve colour/dash) and
`_verify.json`.

### Verification

All five panels **PASS** all three checks. The one that matters for occluded
curves is C, which compares geometry to geometry per curve:

| figure | A original→extracted | C worst per-curve offset |
|---|---|---|
| 4.7  | mean 0.001 px, 0.00 % over 3 px | 0.0002 pt |
| 4.8  | mean 0.000 px, 0.00 % over 3 px | 0.0002 pt |
| 4.9  | mean 0.002 px, 0.00 % over 3 px | 0.0002 pt |
| 4.10 | mean 0.000 px, 0.00 % over 3 px | 0.0010 pt |
| 4.11 | mean 0.011 px, 0.00 % over 3 px | 0.0017 pt |

(at 8× print size; printed line width is 1.1 pt, so C is ~1000× finer than the
ink.) Checks A and B were run with the in-plot legends and position labels
masked off — those are chart furniture we neither extract nor should.

No automated check can catch a *mis-read tick label*, which produces a perfectly
self-consistent wrong answer. So the calibration was closed against numbers
taken from the report's text and Table 4.1 that the extraction never used:

| quantity | report | extracted | Δ |
|---|---|---|---|
| undrained interior jump, all 5 analytical curves, Figs 4.7/4.8/4.9 | 0.67320 MPa (0.1013 + 5.719e5 Pa) | 0.67312 MPa | 8e-5 MPa |
| steady-state p(x), Fig 4.10 analytical, at x = 1, 2.5, 5, 7.5, 9 m | 0.1013 + 0.8987(1−x/10) | — | ≤ 1.7e-4 MPa (0.017 % of axis) |
| steady-state u(0), Fig 4.11 analytical | 5.6169e-4 m | 5.6168e-4 m | 8.5e-9 m |
| steady-state u(x), Fig 4.11, at x = 2.5, 5, 7.5 m | (Δp/E)(100−x²)/20 | — | ≤ 1.7e-7 m |
| lhs boundary p(x=0), mean of 64 curves, Fig 4.10 | 1.0000 MPa | 0.99992 MPa | 8e-5 MPa |
| rhs boundary p(x=10), mean of 64 curves, Fig 4.10 | 0.1013 MPa | 0.10126 MPa | 4e-5 MPa |

## Units — read before comparing against a simulation

The figures plot **absolute** pore-water pressure in **MPa**. `benchmark.json`
works in **excess** pressure in **Pa**, relative to the 0.1013 MPa initial
datum. To compare:

```
p_excess_Pa = (pw_MPa - 0.1013) * 1e6
t_seconds   = time_a * 31557600.0          # 365.25 d
```

Displacement in Fig 4.11 is already in metres and is plotted
**compression-positive**, the same sign convention as the corrected
`u_lhs = +5.617e-4 m` in `benchmark.json`.

## Limitations

- **Point spacing is not the solvers' time stepping.** These are faithful
  samples of the *drawn* polylines. The shape is exact; the x-values are the
  vertices the chart was drawn with, not the original output steps.
- **Fig 4.8/4.9 begin at t = 0.001 a**, the left end of the log axis. Earlier
  behaviour — including the instantaneous undrained response at t = 0 — cannot
  be drawn on a log axis and is not recoverable from those two figures. Fig 4.7
  (linear axis) does reach t = 0.
- **Team identity in Fig 4.9 is inferred, not read.** That panel has no legend
  (the report says its curves are "distinguished only according to the
  positions"), so the eight curves per colour were assigned to teams by draw
  order. The inference is corroborated: four of the eight slots match Fig 4.8's
  corresponding curve exactly in point count and x-extent (BGR 42 points from
  x≥0.106, ENSI-CM 159 from x≥0.223, GRS-K 329, LBNL 18) and land in the
  expected slots — those are the partners who did not revise their submission.
  The other four differ, which is precisely what Fig 4.9 exists to show.
  **Treat the per-team labels in `fig4.9_*` as provisional.** Position labels
  and the analytical curves in that figure are certain.
- Team identity everywhere else is read from the PDF's own legend key lines
  (their stroke RGB matched to the legend text boxes), not guessed from colour
  order.
- Fig 4.10's analytical set has **eight** bands for **seven** profile times; the
  extra one, `tsteady_analytical`, is the exact steady-state limit (it matches
  0.1013 + 0.8987(1−x/10) to 1.7e-4 MPa). Fig 4.11's single black band is the
  same steady-state solution.

## Reproducing

```bash
tools/extract/examples/benvasim-fig4.7-4.11/run.sh \
    debug/BenVaSim/Synthesebericht_BenVaSim_Final.pdf \
    debug/BenVaSim/1.1/reference
```

That script carries the tick labels, the `names.json` legend maps and the verify
masks for all five panels, and re-runs the verification.
