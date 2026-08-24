# SKILL: Getting a paper's figure into a reference CSV

Your job is a CSV of `x,y` pairs that faithfully reproduces a published curve. There
are four routes. **Pick by what the figure is**, not by habit — the table below is the
whole decision.

Every route ends the same way: **`verify`, then look at the overlay.** A digitization
nobody checked is not evidence, and calibration errors are silent — every value comes
out affinely wrong while the curve still looks completely plausible.

## Route table

| The figure is… | Route | Why |
|---|---|---|
| a **vector** plot (the PDF stores the polyline) | `pdfplot2csv.py` if available in this deployment | exact — no pixels, no calibration error. Check first; it is strictly better when it applies. |
| raster, series in **distinct colours** | `plot_digitize series --mode colour` | a colour mask separates series cleanly even where they cross |
| raster, **open markers** on black/white | `plot_digitize series --mode markers --ring` | the ring test tells a data marker from the line it sits on |
| raster, **line curves**, possibly overlapping | `plot_digitize series --mode curve --pick upper\|lower\|mean` | per-column tracking; the envelope picks one of a near-coincident set |
| defeats all of the above | park it for a human — `handoff.py` | a wrong number is worse than a missing one |
| you specifically want the browser digitizer | `EXTRACT-WEB.md` | an option, not the default. It needs Claude in Chrome to be working. |

## The workflow

```bash
# 1. find the page, render it, LOOK at it
{{PY}} {{RENDER}} pages "{{PDF}}"
{{PY}} {{RENDER}} render "{{PDF}}" 9 ".run/figures/" --dpi 300

# 2. crop the ONE panel — tight: axes, ticks and legend in; caption and
#    neighbouring panels out. Coordinates are fractions of width/height.
{{PY}} {{RENDER}} crop ".run/figures/page9.png" 0.06 0.05 0.50 0.30 ".run/figures/fig8a.png"

# 3. propose a calibration, then PIN it against the printed tick labels
{{PY}} {{DIGITIZE}} axes ".run/figures/fig8a.png"
{{PY}} {{DIGITIZE}} calibrate ".run/figures/fig8a.png" \
     --x 137 0 1002 200  --y 578 0.0 34 -1.6  -o ".run/figures/fig8a.cal.json"

# 4. extract
{{PY}} {{DIGITIZE}} series ".run/figures/fig8a.png" -c ".run/figures/fig8a.cal.json" \
     --mode markers --ring --recover-hidden --min-hole 100 \
     --roi 140 38 1000 523 --exclude 453 278 783 405 \
     --x-col time_days --y-col strain_1e-3 -o "<case>/reference/fig8a.csv"

# 5. VERIFY — not optional
{{PY}} {{DIGITIZE}} verify ".run/figures/fig8a.png" -c ".run/figures/fig8a.cal.json" \
     --csv "<case>/reference/fig8a.csv" -o ".run/figures/fig8a_check.png"
```

Then **open the check image**. Every replotted marker must sit on its curve. Offset or
sheared means the *calibration* is wrong, not the extraction — go back to step 3.

## Image size is a hard gate

`render` and `crop` clamp the longest edge to `--max-side` (default 1600) and say so.
This is not cosmetic: an agent harness that hands an image to a model refuses
oversized ones outright. An A4 page at 300 dpi is ~2481×3308 and will be rejected.

Render at whatever dpi makes the tick labels sharp — the clamp keeps the file
readable. If a clamped panel is blurry, crop *tighter* rather than raising the clamp.

## Calibration is the failure mode that matters

`axes` **proposes**; it does not decide. Auto-detection of tick ladders is unreliable
on real figures — a temperature strip under the axis, a legend box, or a boxed title
all read as frame lines. Read its output, compare against the printed labels, and pin
the numbers yourself.

Two reference points per axis, in pixels and in data units. Inverted axes need no
special handling: a stress plot printed −1.6 at the top is just `--y <bottom_px> 0.0
<top_px> -1.6`.

## Tuning, in the order you should reach for it

| Symptom | Knob |
|---|---|
| nothing selected | widen `--tol`, drop `--roi`, check `--colour` |
| legend/title/labels picked up | `--exclude X0 Y0 X1 Y1` (repeatable) |
| an annotation's letter counted as a marker | raise `--min-hole` — a data ring encloses far more white than an “o”. Measure one of each. |
| the same marker twice | `--dedupe` (default 6 px) — the component and hole passes both find it |
| text touching a curve | `--monotonic inc\|dec` |
| three near-coincident curves | `--pick upper` / `lower`, and say in the CSV comment which one you took |
| markers hidden under the curve | `--recover-hidden` finds them by the white they enclose |

## Honesty rules

- **Record what you took.** In the `reference_curves` `_comment`: which figure, which
  series, the calibration points, and any sign or axis flip.
- **Digitize the paper's own model curve too, when it plots one.** A model-vs-model
  comparison separates your error from the paper's — this is how you find out that a
  published figure is not reproducible from its own printed parameters.
- **Do not invent points.** If a curve is only drawn from 45 d onward, the CSV starts
  at 45 d. Extrapolated early points are indistinguishable from data later.
- **Do not trace the model line and call it the experiment.** If the experimental
  markers stop, the experimental CSV stops.
- **Recall is not accuracy.** Getting 10 of 13 markers with every one correct beats 13
  with three guessed. State what you could not recover.
