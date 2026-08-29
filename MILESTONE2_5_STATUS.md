# Development OS — Milestone 2.5 Land Utilization Optimizer

## Status
Implemented as a best-yield optimization layer on top of the generated/parametric site plan.

## Goal
Reduce developable-land residuals and increase valid sellable lot yield without allowing the optimizer to silently violate the configured RTH/PSU targets.

## Implemented
- Residual polygon detection and classification (`potential_lot`, `large_residual`, `small_residual`).
- Block/frontage repacking against editable road segments.
- Variable lot frontage with configurable minimum / target / maximum.
- Variable lot depth with configurable minimum / target / maximum.
- Corner-lot rotation is evaluated by competing road-order frontage ownership (`main-first` vs `local-first`).
- Road-position optimization by testing bounded translations of the road network normal to its dominant/longest axis.
- RTH/PSU relocation candidates, including a residual-first strategy and several edge-placement strategies.
- RTH/PSU target guard: candidates below configured target tolerance are rejected.
- Selective local-road extension: large residual polygons can receive a short local branch only when the total optimization score improves.
- Best-yield scoring combines sellable area, unit count, land utilization, road area, residual area, and deviation from target lot dimensions.
- Before/after report: lot count, sellable area, residual area, land utilization, residual ratio, and road efficiency.
- Residual polygons are returned to the frontend and shown on the map as a yellow/orange diagnostic overlay.
- New UI panel: **M2.5 — Land Utilization Optimizer** with lot-size ranges and optimization switches.
- New API: `POST /site-plan/optimize-yield`.
- Large-layout validation/recalculation uses Shapely `STRtree` instead of unconditional O(n²) overlap scans.
- `/health` is async and reports version `2.5`.
- Windows launcher no longer needs the browser cache workaround from 2.4.1; frontend assets use `?v=2.5`.

## Smoke test — sample Pekanbaru
Using the existing sample and default M2.5 ranges (frontage 7–10 m, target 8 m; depth 13–18 m, target 15 m):

- Before: 105 lots.
- After: 112 lots.
- Sellable lot area: 12,600.00 m² → 12,782.38 m².
- Residual: 4,204.48 m² → 4,129.58 m².
- Land utilization: +0.60 percentage points.
- RTH target retained: 10.0%.
- PSU target retained: 5.0%.
- Selected road-network shift in this smoke test: normal -4 m.
- Selected facility strategy in this smoke test: edge-bottom-top.

The exact winning strategy depends on parcel geometry, road network, lot ranges, and target percentages.

## Performance hardening
A synthetic 1,600-lot overlap validation completed in about 0.03 s in the local test environment using `STRtree`. This specifically removes one of the previous large-layout O(n²) validation bottlenecks.

## Definition of Done covered
1. Residual polygons detected and classified.
2. Frontage blocks repacked.
3. Variable lot sizing supported.
4. Corner ownership/orientation alternatives evaluated.
5. Road position candidates evaluated.
6. RTH/PSU relocation evaluated while preserving targets.
7. Selective road extension implemented with score-based acceptance.
8. Multiple candidates are scored.
9. Best-yield candidate returned with before/after evidence.
10. Residual and utilization metrics are visible in the UI.

## Important limitation
M2.5 is still a conceptual residential land-yield optimizer. It does not yet enforce jurisdiction-specific minimum lot dimensions, official RDTR rules, cadastral/legal constraints, terrain, road grade, turning radii, drainage hydraulics, fire access, or DED-level engineering. Those belong to M3/M4+.

## Patch 2.5.1
Residual is now a hard constraint of maximum **3% of total parcel area**. See `MILESTONE2_5_1_STATUS.md` for the corrective implementation and transparency rules for any excess residual allocated to additional RTH/landscape.


## Patch 2.5.3 — Optional master switch
Land optimization is now opt-in with the **Optimalisasi Lahan (M2.5)** checkbox. OFF restores the exact generated baseline; ON runs Best Yield. Residual-to-Reserve absorption is disabled in the active optimizer path, so residual numbers remain true unallocated area.
