# Development OS — Milestone 2.5.7 Automatic 3% Residual Target

## Status
Release candidate built on M2.5.6.1.

## User rule implemented
When **Optimalisasi Lahan** is OFF, the application keeps the original generated scenario.
When **Optimalisasi Lahan** is ON, the optimizer has one fixed hard rule:

`TRUE residual / total parcel area <= 3%`

There are no user-facing min/target/max frontage/depth controls, road-shift controls, extension counts, or residual-cap inputs.

## Behaviour
- The 3% residual cap is hardcoded in the backend while optimization is ON.
- Stale values from older cached frontends cannot loosen the 3% rule or trigger the old min/target/max validation error.
- Standard lot dimensions are used only as an internal packing hint.
- Remaining developable polygons are converted into explicit amber **residual parcels** with their actual polygon geometry and actual area.
- Residual parcels do not have to match the standard lot size.
- Direct frontage is recorded when available; a residual parcel without direct frontage is kept visible with `access_status=needs_access` instead of being hidden as Reserve/RTH.
- No residual is reclassified into Landscape/Reserve to fake compliance.
- If optimization is OFF, the original baseline is restored.

## Smoke test — 2026-08-28
`python test_m257_auto3.py`

- sample_pekanbaru: 14.90% -> 0.71%, 105 -> 157 lots, 77 residual parcels, 0 m2 reserve, 0 overlap, 0 outside.
- regular_220x120: 17.64% -> 0.59%, 93 -> 157 lots, 37 residual parcels, 0 m2 reserve, 0 overlap, 0 outside.
- large_360x220: 15.38% -> 0.46%, 330 -> 438 lots, 158 residual parcels, 0 m2 reserve, 0 overlap, 0 outside.
- stale_invalid_ui_values: old broken values (frontage 3/3/3, depth 3/3/2, residual cap 19%, toggles OFF) were ignored by AUTO mode; result 0.71% residual and 3.0% backend hard cap.

Additional regression:
- `test_smoke.py` PASS.
- `test_m24_parametric.py` PASS.
- `test_m253_optional_toggle.py` PASS.
- `test_m256_relocation.py` PASS.
- `test_m256_relocation_29.py` PASS: 29 -> 29, relocated 29, dropped 0.
- Python compile PASS.
- Frontend JavaScript syntax PASS.
- Uvicorn `/health` PASS and reports version 2.5.7.
- Served HTML contains Milestone 2.5.7.

## Important scope note
This is still conceptual land parcelization. A residual parcel with `access_status=needs_access` is an allocated parcel polygon, not a claim that the parcel already satisfies future road-access or regulatory requirements. Those constraints belong to later regulatory/engineering milestones.
