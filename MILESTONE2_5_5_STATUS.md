# Development OS — Milestone 2.5.5 Residual Parcelization Optimizer

## Status
Release candidate implementing the clarified M2.5 concept: road-fronting leftover land becomes explicit irregular residual lots rather than being forced to standard lot dimensions or hidden as RTH/Reserve.

## Behavior
- `Optimalisasi Lahan` remains optional. OFF preserves the original generated scenario.
- ON runs best-yield optimization and residual parcelization.
- Standard lots remain teal.
- Residual lots are amber and are returned with actual parcel metadata: ID, area, perimeter, frontage, estimated depth, and minimum-rectangle dimensions.
- Clicking a lot on the map shows parcel type, area, frontage, and estimated depth.
- TRUE residual means only land that remains unallocated after standard lots + residual parcels + roads + RTH + PSU.
- No residual-to-Reserve trick.
- No automatic extra-RTH absorption in M2.5.5.
- Strict mode rejects a result when TRUE residual still exceeds the configured cap.

## API additions
`POST /site-plan/optimize-yield` now returns:
- `lot_details[]`
- `stats.standard_lot_count`
- `stats.residual_lot_count`
- `stats.residual_lot_area_m2`
- `optimization.residual_parcel_count`
- `optimization.residual_parcel_area_m2`

## Smoke test — 2026-08-28
`test_m255_residual_parcelization.py`

### Sample Pekanbaru
- Baseline: 105 lots, TRUE residual 14.90%
- Optimized: 151 lots
- Standard lots: 98
- Residual lots: 53
- Residual-lot area: 5,572.77 m²
- TRUE residual: 271.18 m² / 0.96%
- Lot overlap: 0.0 m²
- Lot vs road/RTH/PSU overlap: 0.0 m²
- Outside buildable: 0.0 m²
- Runtime: 2.215 s

### Rectangle 220 × 120 m
- Baseline: 93 lots, TRUE residual 17.64%
- Optimized: 152 lots
- Standard lots: 108
- Residual lots: 44
- Residual-lot area: 2,911.26 m²
- TRUE residual: 192.65 m² / 0.73%
- Lot overlap: 0.0 m²
- Lot vs road/RTH/PSU overlap: 0.0 m²
- Outside buildable: 0.0 m²
- Runtime: 1.265 s

### Large rectangle 360 × 220 m
- Baseline: 330 lots, TRUE residual 15.38%
- Optimized: 434 lots
- Standard lots: 280
- Residual lots: 154
- Residual-lot area: 17,217.34 m²
- TRUE residual: 353.06 m² / 0.45%
- Lot overlap: 0.0 m²
- Lot vs road/RTH/PSU overlap: 0.0 m²
- Outside buildable: 0.0 m²
- Runtime: 4.040 s

## Regression gates
- M1 geometry smoke: PASS
- M2.4 parametric reflow smoke: PASS
- M2.5 optional-toggle smoke: PASS
- Python compile: PASS
- Frontend JavaScript syntax: PASS
- Uvicorn health: PASS (`version=2.5.5`)
- Served HTML version header: PASS (`Milestone 2.5.5`)

## Important interpretation
A residual lot is not required to equal the standard 8×15 / target lot dimensions. It is a real irregular parcel carved from leftover developable geometry, provided it has road frontage and passes basic geometric sanity checks. Jurisdiction-specific legal minimum lot dimensions remain a later regulatory rule set.
