# Benchmark authoring dataset

**Derived from `benchmarks/`. Do not hand-edit — re-run the builder.**

```bash
python worker/tools/build_dataset.py
```

21 cases, 16 unique papers,
2 cases with no paper in the repo.

| what | where |
|---|---|
| the index | `manifest.json` |
| reference specs | `json/<case with / as __>.json` |
| papers | `pdf/` (deduplicated by content hash) |
| papers as text | `text/` (`pdf_render.py text`, for models with no PDF input) |

Each spec carries `metadata.paper_pdf` naming its paper, or `null` when there is
none. `metadata` is unconstrained in `benchmark.schema.json`, so a dataset spec
validates exactly as its corpus original does.

`our_verdict` comes from `viewer/status.json` and is our own judgement of whether
that benchmark reproduces — useful for excluding cases we already know are wrong.

## Cases

| case | physics | paper | our verdict |
|---|---|---|---|
| `ATENA/GFM` | mechanical | `atena-gfm.pdf` | unreviewed |
| `ATENA/TPBB` | mechanical | `atena-tpbb.pdf` | correct |
| `ATENA/UAC` | mechanical | `atena-uac.pdf` | unreviewed |
| `BenVaSim/1.1` | hydro-mechanical (Biot poroelasticity) | `benvasim.pdf` | correct |
| `ConCracksRestrainedBeam` | hydro-thermo-mechanical | `concracksrestrainedbeam.pdf` | unreviewed |
| `EarlyAgeThermalCracking` | thermo_mechanical | `earlyagethermalcracking.pdf` | unreviewed |
| `HAMSTAD/B2` | hygric (isothermal moisture transport, analytical validation) | `hamstad.pdf` | correct |
| `HITEC/cox-isotropic-temperature` | thermal (conduction-only leg of the THM benchmark) | `hitec.pdf` | correct |
| `HITEC/hm-excavation` | hydro-mechanical (Biot poroelasticity) | `hitec.pdf` | correct |
| `OpenGeoSys/consolidation-with-staggered-scheme` | hydro-mechanical (Biot poroelasticity, linear) | — | correct |
| `bazant-1995-b3-basic-creep` | viscoelasticity (basic creep) | `bazant-1995-b3-basic-creep.pdf` | correct |
| `bazant-2004-fahmi-transitional-thermal-creep` | thermo-mechanical | `bazant-2004-fahmi-transitional-thermal-creep.pdf` | unreviewed |
| `bazant-najjar-1972-drying-cylinder` | hygral | — | correct |
| `briffaut-2010-civaux-wall` | thermo-chemo | `briffaut-2010-civaux-wall.pdf` | unreviewed |
| `cervera-1999-thermo-chemo-partI` | thermo-chemo | `cervera-1999-thermo-chemo-parti.pdf` | correct |
| `faria-2006-slab/thermal` | thermo-chemo | `faria-2006-slab.pdf` | correct |
| `faria-2006-slab/thermo_mechanical` | thermo-chemo-mechanical | `faria-2006-slab.pdf` | correct |
| `jedrzejewska-2018-cost-tu1404/cube` | thermo-chemo | `jedrzejewska-2018-cost-tu1404.pdf` | correct |
| `jedrzejewska-2018-cost-tu1404/restrained` | thermo-chemo-mechanical | `jedrzejewska-2018-cost-tu1404.pdf` | correct |
| `klemczak-knoppik-wrobel-early-age-cracks` | thermo-chemical (linear heat conduction + affinity hydration heat source) | `klemczak-knoppik-wrobel-early-age-cracks.pdf` | unreviewed |
| `xi-bazant-jennings-1994-moisture-diffusion` | moisture-diffusion | `xi-bazant-jennings-1994-moisture-diffusion.pdf` | unreviewed |

## No paper in the repo

- `OpenGeoSys/consolidation-with-staggered-scheme`
- `bazant-najjar-1972-drying-cylinder`
