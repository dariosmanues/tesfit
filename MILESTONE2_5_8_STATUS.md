# Development OS — Milestone 2.5.8 Saleable Residual Lot Validation

## Status
Release candidate patch built on M2.5.7. The change is intentionally narrow: residual/irregular lots are no longer counted as sellable merely because they occupy leftover geometry.

## Locked rule
When **Optimalisasi Lahan** is ON:

- TRUE residual / total parcel area must be <= 3%.
- A residual lot is counted as a lot only if it passes saleability validation.
- Failed residual candidates return to TRUE residual; they are not hidden as Reserve/RTH.

## Saleability gate for amber residual lots
Internal defaults (no extra UI knobs):

- Minimum actual area: 60 m2.
- Minimum **real shared-edge road frontage**: 4 m.
- Minimum rotated-rectangle short dimension: 3 m.
- Maximum aspect ratio: 8:1.
- Minimum polygon fill ratio: 0.15.
- Must remain inside buildable area.
- Must not overlap another lot, road body, RTH or PSU.
- Point-touch / proximity to a road is not treated as frontage.

## Final acceptance gate
The optimizer returns a successful layout only when all are true:

1. TRUE residual <= 3% of total land.
2. Lots outside buildable = 0.
3. Lot-lot overlap pairs = 0.
4. Lot-road interior overlaps = 0.
5. Lot-RTH/PSU overlaps = 0.
6. RTH-PSU overlap = 0.
7. Invalid residual lots = 0.
8. Final lot count is not lower than the baseline lot count.

If any gate fails, `/site-plan/optimize-yield` returns HTTP 422 and the optimized layout is not accepted.

## Save protection
When optimization mode is enabled, `/projects` also requires both residual <=3% and `validation_passed=true` / final validation PASS. A stale or forged optimized result cannot be saved just because its residual number is small.

## Smoke tests executed

### Regression
- M1 geometry smoke: PASS.
- M2.4 Parametric Reflow: PASS.
- M2.5.6 relocation 6-lot test: PASS, 6 -> 6, dropped 0.
- M2.5.6 relocation 29-lot test: PASS, 29 -> 29, dropped 0.
- Python compile: PASS.
- Frontend JavaScript syntax: PASS.
- Uvicorn `/health`: PASS, version `2.5.8`.

### Saleability unit tests
- 72 m2 residual lot with 6 m real frontage: PASS.
- 50 m2 residual lot: REJECTED as expected.
- 60 m2 residual lot with only 3 m frontage: REJECTED as expected.
- 4 m x 60 m sliver (15:1 aspect): REJECTED as expected.
- Save request with stale/failed validation: HTTP 422 as expected.
- Untouched optimized layout with final validation PASS: Save accepted in smoke test.

### End-to-end optimizer smoke
Using 8x15 standard lot module, RTH 10%, PSU 5%:

| Case | Baseline lots | Final lots | Valid residual lots | TRUE residual | Runtime |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sample Pekanbaru | 105 | 118 | 43 | 1.54% | 5.62 s |
| Rectangle 220x120 m | 93 | 152 | 44 | 0.73% | 2.62 s |
| Rectangle 360x220 m | 330 | 387 | 138 | 2.42% | 6.88 s |

All three returned final validation PASS with zero lot-lot, lot-road, lot-RTH/PSU overlap and zero lots outside buildable.

## Important scope note
This is still conceptual site planning. The 60 m2 / 4 m residual-lot defaults are internal geometric saleability guards, not a claim that every jurisdiction or housing product legally permits those dimensions. Regulatory minimums belong to M3 and must later override these defaults.
