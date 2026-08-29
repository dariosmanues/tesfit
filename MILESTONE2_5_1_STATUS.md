# Development OS — Milestone 2.5.1 Strict Residual Cap

## Status
Implemented as a corrective patch to M2.5 so residual/lahan sisa is an explicit hard planning constraint rather than only a scoring penalty.

## User rule locked
- Maximum residual: **3% of total parcel area**.
- The cap is measured against **total land area**, not buildable area.
- Frontend sends `strict_residual_cap=true` and `max_residual_pct_total=3.0`.
- If the cap cannot be met and residual absorption is disabled, the optimizer returns an explicit 422 error instead of labeling the result “Best Yield”.

## Optimization order
1. Repack frontage blocks with variable lot width/depth.
2. Compare road-network shifts.
3. Try more aggressive selective local-road extensions into large residual polygons.
4. Re-evaluate lots after each road candidate.
5. If residual is still above the 3% hard cap, optionally classify only the **excess above 3%** as additional RTH/landscape buffer.
6. Keep the remaining true unallocated residual at or below 3% of total land.

## Transparency
Residual absorption is not hidden:
- response includes `optimization.residual_cap.absorbed_to_rth_m2`;
- final RTH area/percentage increases accordingly;
- UI explicitly states how many square metres were reassigned to RTH/landscape;
- residual percentage shown in Site Statistics is now residual / **total land area**.

## New request controls
`YieldOptimizeRequest` adds:
- `max_residual_pct_total` (default 3.0)
- `strict_residual_cap` (default true)
- `allow_residual_rth_absorption` (default true)
- `max_extensions` increased to maximum 8; UI default 4

## Smoke test — sample Pekanbaru
Existing sample with target lot 8x15 m, variable frontage 7–10 m and depth 13–18 m:
- Before lots: 105
- After lots: 121
- Before residual: 4,204.48 m²
- Final residual: 846.52 m²
- Final residual / total land: **3.00%**
- Residual cap met: **true**
- Additional residual explicitly reclassified to RTH/landscape in this sample: about 2,664.67 m²

This demonstrates the cap mechanism; the exact amount allocated to extra RTH depends on parcel geometry and whether the road/lot optimizer can productively absorb the residual first.

## Verification
- Python compile: PASS
- Frontend JavaScript syntax (`node --check`): PASS
- M2.5 optimizer test with strict 3% cap: PASS
- M2.4 parametric regression test: PASS
- M1 geometry smoke test: PASS

## Important limitation
A 3% residual cap is a user-defined planning rule, not yet a jurisdiction-specific regulatory requirement. M3/M4 must later determine whether extra RTH/landscape, road geometry, lot dimensions, access, drainage, and other allocations are legally and technically valid for the actual project.
