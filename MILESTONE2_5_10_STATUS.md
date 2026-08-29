# Milestone 2.5.10 — Block-First Residential Parcelization

## Why this revision exists

M2.5.8 could generate exact rectangles initially, but its road spacing / road-centric packing and later optimizer could create visually fragmented rows, large unused strips, and variable-size lots that were still treated as ordinary lots. M2.5.9/2.5.9.1 experiments were discarded as release baselines. M2.5.10 restarts from the stable M2.5.8 codebase and changes only the parcelization logic needed to enforce real residential subdivision structure.

## Non-negotiable planning rules

1. Geometry Settings defines the STANDARD product. `8 x 15` means an exact 8 m frontage, 15 m depth and 120 m² STANDARD lot.
2. The planning order is **road network → block polygons → STANDARD packing → residual analysis → Adaptive lots**.
3. Adaptive lots are allowed only from actual leftover residual geometry after Standard packing.
4. Standard rows are contiguous modular runs. Leftover length is kept at row/block ends; it is not intentionally spread between Standard lots.
5. Road centerline spacing accounts for the actual widths of neighboring roads so the clear block depth can preserve two exact lot depths.
6. Optimizer scoring treats residual-cap compliance as a gate and exact Standard count as the primary yield metric inside that gate. Variable residual parcels are not a substitute for Standard yield.
7. Failed parametric reflow is atomic from the UI: the previous layout snapshot is restored so a failed solve does not leave disappearing lots on screen.

## Core implementation

- `_road_specs()` now derives parallel road spacing from lot depth and the actual road widths.
- `_pack_standard_blocks()` packs each road-defined block rather than independently spraying lots from every road.
- `_standard_row_candidates()` only emits complete, exact module rectangles and keeps one contiguous run per frontage.
- `_standard_geometry_audit()` is a hard validator for Standard dimensions/area.
- `_pack_yield_lots()` no longer uses min/max width/depth to deform Standard lots. Those ranges are retained only for backward-compatible residual/adaptive processing.
- Adaptive/residual metadata is tagged with `source=residual`.
- Final acceptance includes `invalid_standard_lot_count` and `adaptive_origin_violation_count`; both must be zero.

## Regression evidence

### Synthetic exact block
- Block: 80 m × 30 m
- Geometry Settings: 8 m × 15 m
- Result: 20 STANDARD lots
- Invalid Standard lots: 0
- Residual: 0 m²

### Synthetic remainder block
- Block: 83 m × 30 m
- Geometry Settings: 8 m × 15 m
- Result: 20 STANDARD lots
- Unused remainder: 90 m² = 3 m × 30 m, concentrated at block edge
- No Standard lot is stretched to consume the 3 m remainder.

### Pekanbaru sample site
Baseline M2.5.10 (optimizer OFF):
- 131 STANDARD lots
- 0 Adaptive lots
- all Standard lots = 8×15 / 120 m²
- invalid Standard = 0
- TRUE residual = 6.70% (shown honestly; baseline is not forced to fake 3%)

Optimizer ON:
- 128 STANDARD lots
- 3 Adaptive lots, sourced only from residual land
- total sellable lots = 131
- TRUE residual = 2.28%
- invalid Standard = 0
- adaptive-origin violations = 0
- lot overlaps = 0
- lot/road overlaps = 0
- lot/RTH/PSU overlaps = 0
- final validation = PASS

### Geometry Settings not hard-coded
A second run with 7 m × 14 m produces STANDARD lots of 98 m² with zero invalid Standard modules.

## Release-gate tests

Run from `services/geometry-api`:

```powershell
python test_smoke.py
python test_m253_optional_toggle.py
python test_m258_saleable_residual.py
python test_m2510_block_first.py
```

All four pass in the release workspace.

## Known limitations

- This is still conceptual site planning, not municipal approval / cadastral survey / DED.
- Curved road geometry and highly complex cul-de-sac topology are not yet a dedicated solver.
- Facility allocation is still heuristic. It is now scored against Standard yield, but a later milestone should make park/PSU placement block-aware from the beginning.
- Parametric edits that make the fixed Standard module impossible may be rejected and rolled back instead of deforming lots. This is intentional.
