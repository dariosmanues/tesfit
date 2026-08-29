# Milestone 2.5.13 — Recovery Solver Monitor

## Hard business rule
- Gross Lot Efficiency **>= 70%** remains a strict gate.
- Any candidate below 70% is **REJECT** and never enters the selectable/saveable Alternative Layout pool.
- Once a candidate passes, ranking is: Standard count -> efficiency -> fewer Adaptive -> smaller road area -> smaller residual -> block regularity -> connectivity.

## Real staged solver
The backend runs an in-memory job and the frontend polls real state from `/site-plan/solver/status/{job_id}`. No fake progress percentages are generated in the browser.

Stages:
1. Initial Search — unchanged M2.5.12 baseline generator.
2. Road Topology Recovery — double-loaded parallel, single spine, short branches, perimeter-assisted and hybrid candidates.
3. Block Spacing Recovery — shifts the 30 m double-loaded module phase without changing the 8x15 Standard product.
4. Orientation Recovery — dominant +/-2/4/6/8/10/15 degrees plus boundary-aligned angles.
5. Perimeter Recovery — pushes irregularity toward perimeter-oriented short-branch layouts.
6. RTH/PSU Placement Recovery — required percentages stay fixed while edge/low-yield placement changes.
7. Adaptive Recovery — only TRUE residual may become saleable Adaptive; Standard/roads/RTH/PSU are immutable in this pass.
8. Feasibility Analysis — only declares mathematical infeasibility when an optimistic upper bound that ignores all roads/residual is already below 70%; otherwise reports solver-not-converged.

## UI monitor
Shows live stage, tested/total candidates, current strategy, Standard/Adaptive counts, road %, residual %, best efficiency, gap to 70%, rejected search history, and feasibility diagnosis.

## Baseline protection
The existing `generate_site_alternatives`, `_road_specs`, and `_pack_standard_blocks` implementations are not rewritten by this milestone. Recovery candidates are implemented in `app/recovery_solver.py` and registered as separate endpoints.
