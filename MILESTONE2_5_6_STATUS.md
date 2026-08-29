# Development OS — Milestone 2.5.6 Relocation Reflow

## Goal
Parametric reflow must preserve kavling count. A lot that no longer fits in its original frontage block is relocated to another valid vacant road-fronting slot instead of being deleted.

## Behavior
- `preserve_count=true` now triggers a second-stage global vacancy search across every road segment and both road sides.
- Relocated lots keep their original width/depth and are assigned to the nearest valid vacant frontage slot.
- If no valid slot exists for every displaced lot, the reflow is rejected with HTTP 422; no lot is silently deleted.
- Response adds `relocated_lot_indices`.
- Frontend status reports `N kavling dipindahkan ke area kosong` instead of `N kavling tidak muat`.
- Existing M2.5.5 Residual Parcelization behavior remains unchanged.

## Smoke tests
- Synthetic 6-lot upper-block saturation: 6 before -> 6 after, 6 relocated, 0 dropped, validation valid.
- Synthetic 29-lot saturation with vacant lower frontage: 29 before -> 29 after, 29 relocated, 0 dropped, validation valid.
- M1 smoke: PASS.
- M2.4 parametric regression: PASS.
- M2.5 optional-toggle regression: PASS.
- M2.5.5 residual parcelization regression: PASS.
