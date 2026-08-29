# Milestone 2.5.12 — Gross Lot Efficiency >= 70% Acceptance Model

## Goal
Milestone 2.5.12 simplifies and recalibrates the parcelization acceptance model.

The previous hard acceptance gate (`TRUE residual <= 3%`) has been removed and replaced by:
**`GROSS LOT EFFICIENCY >= 70%`** as the primary acceptance requirement.

Residual is no longer an acceptance gate and is tracked strictly for informational purposes.

## Core Formula & Definitions

### Gross Lot Efficiency
Lot efficiency is calculated strictly against **total land area** (gross parcel area):

$$\text{lot\_efficiency\_pct} = \frac{\text{standard\_lot\_area\_m2} + \text{adaptive\_lot\_area\_m2}}{\text{total\_land\_area\_m2}} \times 100$$

- **Total Land Area**: Gross land area within the parcel boundary. Buildable area / net area is NOT used as the denominator.
- **Standard Lot Area**: Sum of all exact modular Standard lots placed from Geometry Settings.
- **Adaptive Lot Area**: Sum of all valid, saleable Adaptive lots generated from residual land leftover after standard packing.
- **Residual (Informational)**: True leftover unallocated land (`residual_true_area_m2`, `residual_true_pct`). Residual $> 3\%$ never causes layout rejection if lot efficiency $\ge 70\%$.

## Hard Contracts & Invariants

1. **Standard Lot Contract (Immutable Module)**:
   - Geometry Settings ($W \times D$) is an absolute, non-negotiable contract (e.g., $8\text{ m} \times 15\text{ m} = 120\text{ m}^2$).
   - Standard lots cannot be shrunk, stretched, clipped, resized, deformed, or transformed into Adaptive lots.
   - Standard lot yield is maximized as the top priority.
2. **Adaptive Origin Contract**:
   - Adaptive lots are generated strictly from genuine residual land remaining after standard packing (`adaptive_source = residual_only`).
   - Adaptive lots must meet minimum area ($\ge 60\text{ m}^2$), minimum real road frontage ($\ge 4\text{ m}$), and geometric sanity checks (no slivers, no overlaps).
3. **Acceptance Model**:
   - `lot_efficiency_pct >= 70.0` $\rightarrow$ `PASS` (when no geometry defects).
   - `lot_efficiency_pct < 70.0` $\rightarrow$ `FAIL`.
   - Layouts with `lot_efficiency_pct >= 70.0` and `residual > 3.0%` **PASS**.
4. **Candidate Ranking**:
   - Passing candidates ($\text{efficiency} \ge 70\%$) rank above failing candidates.
   - Rank sorting order:
     1. Highest Standard lot count
     2. Highest Lot Efficiency %
     3. Fewest Adaptive lot count
     4. Smallest Road area
     5. Smallest Residual area
     6. Higher Block Regularity
     7. Higher Road Connectivity
5. **Residual Optimizer**:
   - Optimizer preserves all Standard lots, roads, RTH, and PSU.
   - Converts genuine leftover land into validated Adaptive lots to improve gross lot efficiency towards $\ge 70\%$.
6. **Save Contract**:
   - Save succeeds if `validation.valid == True` (gross lot efficiency $\ge 70\%$ and zero geometry errors).
   - Save is rejected with HTTP 422 if `validation.valid != True` or if an optimized layout was manually modified without recalculating validation.

## New Validation Object Schema
```json
{
  "lot_efficiency_pct": 72.40,
  "lot_efficiency_target_pct": 70.0,
  "lot_efficiency_met": true,
  "standard_lot_count": 130,
  "adaptive_lot_count": 3,
  "standard_lot_area_m2": 15600.0,
  "adaptive_lot_area_m2": 1608.03,
  "invalid_standard_lot_count": 0,
  "invalid_standard_lots": [],
  "adaptive_origin_violation_count": 0,
  "adaptive_origin_violations": [],
  "lot_overlap_pairs": 0,
  "lot_road_overlaps": 0,
  "lot_road_overlap_area_m2": 0.0,
  "lot_obstacle_overlaps": 0,
  "lot_obstacle_overlap_area_m2": 0.0,
  "lots_outside_buildable": 0,
  "rth_psu_overlap": 0,
  "invalid_residual_lot_count": 0,
  "invalid_residual_lots": [],
  "residual_true_area_m2": 401.95,
  "residual_true_pct": 1.42,
  "lot_count_preserved": true,
  "base_lot_count": 130,
  "final_lot_count": 133,
  "valid": true
}
```

## Automated Acceptance Test Suite
Automated test suite `test_m2512_lot_efficiency.py` verifies all 7 mandatory acceptance scenarios:
- **Scenario 1**: Standard Yield Dominance — Standard lot count and area maximized, adaptive is filler only.
- **Scenario 2**: Acceptance Rule — $\ge 70\%$ PASS, $< 70\%$ FAIL.
- **Scenario 3**: Residual Non-Gate Rule — Efficiency $\ge 70\%$ with residual $> 3\%$ PASSES.
- **Scenario 4**: Immutable Standard Contract — Standard lots strictly match $W \times D = 8 \times 15 = 120\text{ m}^2$.
- **Scenario 5**: Adaptive Origin Contract — Adaptive lots generated only from leftover residual land.
- **Scenario 6**: Candidate Ranking — $\ge 70\%$ efficiency candidates prioritized; highest standard count first.
- **Scenario 7**: Save Contract — Valid layouts saved; invalid layouts rejected with HTTP 422.

All 7 scenarios passed successfully.
Full regression suite (`test_m2511_topology_shapes.py`, `test_m2511_masterplan_topology.py`, `test_m2510_block_first.py`, `test_m258_saleable_residual.py`) passed with zero errors.

## Runtime cleanup — active residual 3% target removed
- The active Residual Optimizer no longer forces `max_residual_pct_total = 3.0` or `strict_residual_cap = True`.
- The final Adaptive sweep is driven by the **>=70% gross lot-efficiency target**; TRUE residual is informational.
- Manual recalculation reports `lot_efficiency_target_pct` / `lot_efficiency_met`, not residual-cap acceptance flags.
- Frontend runtime/version residue is cleaned (`DEVOS_FRONTEND_VERSION = 2.5.12`, CSS cache tag 2.5.12, no active residual-3% save gate).
- Regression ranking is strict lexicographic and dedicated tests prevent the 3% runtime gate from returning.
- `_road_specs`, `_pack_standard_blocks`, and `generate_site_alternatives` are intentionally unchanged.
