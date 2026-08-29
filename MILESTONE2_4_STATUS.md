# Milestone 2.4 — Parametric Constraint Editor

## Status
Implemented as a replacement for the previous proximity/collision-based Auto-Reflow logic.

## Architectural change
M2.4 introduces an explicit dependency model:

`Road -> Side/Block -> Ordered Lots`

Each lot is assigned to a frontage road, road side, parametric position (`t_ratio`), frontage width and depth. This model is built when the editor is activated and rebuilt after every successful solve.

## Implemented behavior
- Road movement triggers reflow of lots structurally linked to that road.
- Crossing/adjacent blocks are propagated into the solve region when a moved road changes their usable envelope.
- Road angle/endpoint changes rebuild linked lot geometry against the new road frame.
- Moving a lot keeps the edited lot authoritative along its frontage axis and repacks neighboring lots.
- Multi-selected lots can trigger multiple block solves.
- RTH/PSU edits are treated as hard spatial obstacles and nearby affected blocks are repacked.
- Hard validation checks lot overlap, buildable containment, frontage, lot-vs-RTH/PSU overlap, and RTH-vs-PSU overlap.
- If a geometric edit genuinely removes enough usable frontage, the solver reports any lot that cannot be retained instead of allowing overlap.
- Dependency graph is refreshed after every exact solve so the next edit works from the new topology.

## Frontend behavior
- Existing Shift+click and Box Select remain available.
- Existing road/lot numeric angle tools remain available.
- Existing road endpoint handles remain available.
- `Auto-Reflow` now calls `/editor/parametric-reflow` rather than the old heuristic `/editor/reflow`.
- `Select Linked Lots` uses the dependency graph instead of a distance threshold.
- During road translation, the frontend preview moves linked lots with the road; mouse release invokes the exact parametric solver.

## New API
- `POST /editor/parametric-model`
- `POST /editor/parametric-reflow`

## Smoke test
Sample Pekanbaru layout:
- Base lots: 105
- Main road linked lots: 26
- Moving main road 1 m: 105 lots recalculated, 0 dropped, hard constraints valid
- Moving one lot 6 m along its frontage row: 66 lots recalculated, 0 dropped, hard constraints valid

Run:

```powershell
cd services\geometry-api
.\.venv\Scripts\python.exe test_m24_parametric.py
```

## Current limitation
This remains a conceptual residential site-planning solver, not a regulatory DED engine. Complex curved roads, irregular lots, easements, terrain, road grade, drainage hydraulics, and jurisdiction-specific subdivision rules still require later milestones.
