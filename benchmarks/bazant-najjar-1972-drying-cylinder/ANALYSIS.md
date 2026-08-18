At 3 d the FEM profile is too wet near the surface (0.87 vs 0.77 at 5 mm); by 180 d and 3 y it's systematically wetter throughout the interior (0.795 vs 0.73, and 0.615 vs 0.56 at 75 mm). Only the 28-day curve tracks well.

Two candidates, both in the config rather than the pipeline: the Bažant-Najjar diffusivity parameters, or the surface boundary condition. The config comment already flags the second — it uses Dirichlet h=0.5 and notes "could use Robin moisture film if bp known". Hanson's own 28-day surface reading is 0.59, above the imposed 0.5, which is a hint the real surface wasn't pinned. I haven't touched either; tuning those is a calibration decision, not a fix.
PROBABLY NOT DIRICHTET BC the problem

HERE ARE DIFRNT VALUES
| Parameter | MY config (Pesavento Ch. 7) | Bazant–Najjar 1972, Table 1 (Hanson data) |
|---|---|---|
| C₁ (= D0) | 5.79xe-10 m^2/s | 0.239 cm^2/day ≈ **2.77xe-10 m²/s** |
| α₀ | 0.002 | **0.025** |
| h_c | 0.85 | **0.792** |
| n | 3 | **6** |


ALSO the cylinder requires (1/r)∂/∂r(C r ∂h/∂r) NOT SURE IF IMPLEMENTED