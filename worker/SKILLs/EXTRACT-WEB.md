---
name: web-plot-extraction
description: >
  The extraction route for the AutoBench queue: digitizes plot figures by
  driving plot2data.com through the claude-in-chrome browser tools — render the
  page, crop the panel, upload the PNG, let the site's AI read it, then verify
  and normalize the CSV yourself. Applies to every figure, vector or raster:
  the result is an approximation of the printed ink, so everything produced
  here is labeled AI-DIGITIZED downstream.
---

# Extracting data from raster figures via plot2data.com

## When this applies

Every validation figure the worker needs, vector or raster alike. There is no
vector extraction path in this pipeline — the exact-coordinate tool lives in the
solver repo and is not available here. What you get from this skill is an AI's
reading of the pixels, not the coordinates the figure was drawn from. That gap in
confidence is why everything produced here is labeled `AI-DIGITIZED` downstream,
and why the two verification steps in section 4 are not optional.

## Prerequisites

- Session started with `claude --chrome` (or `/chrome` enabled mid-session) and the
  Claude in Chrome extension connected. The first browser action asks for the
  `claude-in-chrome` skill permission — approve it.
- The browser stays open and visible for the whole run. This is why the queue is
  attended: `run_queue.sh` passes `--chrome`, and a worker whose browser is not
  connected cannot extract anything and will terminate `extract_failed`.
- plot2data.com is free, needs no account, and has no SLA. Max **2 upload attempts
  per figure**, then mark that figure failed and move on. Do not hammer the site.

## Inputs

- `{{PDF}}` and the figure's page number (from the worker's Phase 1 page inventory,
  `{{PY}} {{RENDER}} pages`).
- `{{WORKDIR}}/<case>` — the case directory holding `benchmark.json`. Paths below are
  written relative to it.

## Workflow

### 1. Render the page, then crop the panel

```bash
{{PY}} {{RENDER}} render "{{PDF}}" <page> ".run/figures/" --dpi 300
{{PY}} {{RENDER}} crop ".run/figures/page<N>.png" <x0> <y0> <x1> <y1> \
       ".run/figures/fig<N>_<panel>.png"
```

Open the rendered page and read the panel's bounding box off it as fractions of page
width/height (origin top-left). Crop tight: axes, tick labels and legend in; caption,
neighbouring panels and body text out. A tight crop is the single biggest accuracy
lever for the site's AI — never upload a full page.

Then open the crop and note: title, units, major tick labels on both axes, log axes,
legend entries, in-plot annotations. If the tick labels are not crisply readable at
this size, re-render at a higher `--dpi` and crop again. You are the verifier; the
site is the reader.

### 2. Upload and extract (browser tools)

1. Navigate to `https://www.plot2data.com`.
2. Use the file-upload control with the panel PNG path (drag-drop and Ctrl+V paste
   also exist, but the file input is the reliable route for automation).
3. Wait for the AI result table. If the site offers options, enable log-scale
   detection when the axis is log.
4. **Read the table and check it against the PNG you have open**: axis ranges, units,
   curve count, series names vs. the legend. If it is visibly wrong (axes swapped,
   curves merged, limits misread), retry ONCE with a tighter crop of the same panel.
   Still wrong → record the figure as failed, say why, move to the next figure.

### 3. Save raw, then normalize

- Save the site's CSV export unmodified to `<case>/.run/_web_raw/`
  (check the browser's download directory, usually `~/Downloads`). Provenance: if the
  normalized data is ever questioned, the raw export answers for it.
- Write the normalized reference CSVs to `<case>/reference/`, one file per
  curve, named by the reference-curve id, with a header row and the columns
  `results.reference_curves` expects. Convert units to the benchmark's conventions
  NOW — x in seconds, SI everywhere (CREATE.md units box applies to reference data
  too). You do this conversion yourself; never trust the site's column semantics.

### 4. Verify — two checks, both mandatory

1. **Overlay**: plot the digitized CSVs on top of the panel PNG (matplotlib, PNG as
   background image, curves on top) and save `<case>/reference/_web_overlay.png`.
   Open it. Curves must sit on the printed ink. Nothing else verifies the reading —
   there is no exact reference to diff against, so skipping this means shipping
   unverified numbers.
2. **Cross-check outside the figure**: find a number in the paper's text or tables the
   curve must reproduce — initial condition, plateau, analytical value — and check it.
   Report both numbers. A curve that passes the overlay but misses a stated value has
   a calibration error the overlay cannot see.

### 5. Label and wire up

- Add to `metadata.notes`: `AI-DIGITIZED reference curves <ids> from raster figure
  <fig> p.<page> — approximate; occluded data unrecoverable`.
- Wire the CSVs into `results.reference_curves`.
- Record each figure in `STATUS.json` under `extraction.figures_digitized` /
  `extraction.figures_failed`, then continue the worker contract (WORKER.md) at
  Phase 4 (run) through Phase 7.

## Traps

1. **Log axes** are the most common AI misread. Check the extracted x-range spans the
   decades the axis shows; if values cluster at round numbers, the axis was read as
   linear.
2. **Multi-panel figures**: one upload per panel. Never upload a 6-panel page and
   hope.
3. **One colour, several curves**: the AI will merge them. If the legend can't
   separate them on the PNG either, extract the envelope and say so — do not invent
   per-curve data.
4. **Occluded curves are gone.** A raster figure contains no hidden geometry; say in
   the report what was not recoverable.
5. **The first point on a log axis is the axis limit, not zero.**
6. **Point spacing is the AI's sampling**, not the instrument's. Say so when it
   matters.

## Reporting back

State plainly: which curves came from which figure and page; the overlay verdict; the cross-check numbers; and every limitation —
merged curves, unreadable regions, log-axis early behaviour. Every curve here is AI-digitized, and one
presented without its error character invites exactly the trust it has not earned:
Phase 5 compares model output against these numbers, so their reading error is part
of every deviation you later report.
