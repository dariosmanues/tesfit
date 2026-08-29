# Development OS — Milestone 2.5.2 Residual Invariant

## Status
Implemented as a correction to M2.5.1 after UI verification showed that raw generated alternatives could still display residual well above 3% until the optimizer button was run.

## Hard invariant
For every generated or recalculated layout:

`genuine unallocated residual / total parcel area <= 3%`

A layout above the cap is not saveable as a valid M2.5+ layout.

## Key correction
- The 3% cap now applies **during initial alternative generation**, not only after clicking Optimize Land Utilization.
- Excess non-sellable/unallocated geometry is moved to a separate visible `Landscape/Reserve` layer.
- `Landscape/Reserve` is not counted as regulatory RTH or PSU.
- RTH and PSU therefore remain separately measurable against their configured targets.
- Residual statistics are calculated against **total parcel area**, not buildable area.
- Generated alternatives include `residual_pct_total_land`, `residual_cap_met`, `reserve_area_m2`, and `reserve_pct`.
- Manual recalculation treats Landscape/Reserve as elastic: lots/roads may reclaim it, then reserve is rebuilt only as needed to preserve the 3% invariant.
- Project save is blocked when `enforce_residual_cap=true` and residual is above the configured cap.
- Residual polygons remain visible as diagnostic overlays; Landscape/Reserve is rendered as its own green diagnostic layer.

## Example cap
For a parcel of 23,546.71 m², maximum genuine residual is:

`23,546.71 x 3% = 706.40 m²`

Any additional area that cannot yet be made sellable/road/RTH/PSU is explicitly classified as Landscape/Reserve instead of being reported as unallocated residual.

## Verification
- Python compile: PASS
- Frontend JavaScript syntax: PASS
- M1 smoke test: PASS
- M2.4 parametric test: PASS
- M2.5 optimizer test: PASS
- Sample generated alternatives: all return residual <=3.01% total parcel area before manual optimization.
- Manual recalculation preserves residual <=3.01% by rebuilding the elastic Landscape/Reserve layer.

## Important interpretation
The 3% constraint controls **unallocated land**, not sellable-land efficiency. A layout can satisfy the cap by allocating difficult geometry to Landscape/Reserve; M2.5 Best Yield still tries to reduce that reserve by creating more valid sellable lots and more efficient road/block geometry.
