# Milestone 2.5.11 — Road & Block Topology Optimization

## Goal
M2.5.11 moves optimization one level above individual lots. The system now treats the residential masterplan pipeline as:

`Boundary -> Buildable -> Road topology candidates -> Blocks -> Block analysis/orientation -> exact STANDARD packing -> TRUE residual -> ADAPTIVE residual parcelization`.

Geometry Settings remains the product contract. A STANDARD lot is never resized to improve residual/yield.

## Implemented
- Expanded masterplan candidate search around the site's dominant axis: 0°, ±5°, ±10°, 90° family, centered/offset spine variants, and cross-grid variants.
- Road spacing remains derived from actual lot depth and actual road width, so double-loaded blocks are formed around the requested lot module.
- Block Analyzer now records block type, minimum-rectangle dimensions, regularity, frontage-road count, predicted standard capacity, capacity capture, standard density, and orientation.
- Road Network Analyzer records intersection count, internal dead-end count, and a simple connectivity score.
- Masterplan ranking is lexicographic in intent: invalid STANDARD is rejected; STANDARD lot count dominates; block regularity/connectivity are secondary; irregular blocks, residual and road area are penalties.
- UI alternative cards now show STANDARD, ADAPTIVE, road %, block regularity, and residual %.
- Site Statistics adds STANDARD count, ADAPTIVE count, block regularity, and road connectivity.
- `/site-plan/optimize-yield` is now a **Residual Optimizer only**. Roads, STANDARD lots, RTH and PSU are immutable. Existing Adaptive lots are discarded on re-run and rebuilt only from current TRUE residual.
- Residual Optimizer returns an explicit validation flag that STANDARD count is preserved and that roads/RTH/PSU were immutable.
- No road shift, facility relocation or selective road extension is allowed inside the Residual Optimizer. Those belong to masterplan generation/topology search.

## Release evidence
Sample Pekanbaru regression (8 x 15 m STANDARD):
- Best generated masterplan: 131 STANDARD, 0 ADAPTIVE before residual optimization.
- Invalid STANDARD: 0.
- Average block regularity: 0.8966.
- Road connectivity score: 1.0.
- Residual Optimizer: STANDARD remains 131; 3 ADAPTIVE created from residual; TRUE residual 1.26%; final validation PASS.
- Roads unchanged by Residual Optimizer.
- RTH/PSU unchanged by Residual Optimizer.

The sample STANDARD count ties the M2.5.10 baseline (131); M2.5.11 does not claim a yield improvement on every geometry. It adds a broader topology search, planning-quality metrics, and a non-destructive separation between masterplan optimization and residual optimization.

## Hard invariants
- `STANDARD source = geometry_settings`.
- STANDARD width/depth/area must match Geometry Settings within numerical tolerance.
- `ADAPTIVE source = residual` only.
- Residual Optimizer cannot delete/move/resize STANDARD.
- Residual Optimizer cannot move roads.
- Residual Optimizer cannot move/resize RTH or PSU.
- No lot/road/obstacle overlap.
- TRUE residual must be <= 3% for an optimized option to PASS.

## Tests
- `test_m2510_block_first.py` — exact modular packing / no distributed remainder.
- `test_m2511_masterplan_topology.py` — masterplan search metrics + immutable residual optimization.
- Historical M2.5.x smoke/regression tests retained and updated only where the milestone version/optimizer contract intentionally changed.

## Known limitations
- Candidate topology families are deterministic heuristics, not a full street-network graph optimizer.
- No cul-de-sac, roundabout, traffic-capacity, terrain/slope, stormwater or utility engineering optimization yet.
- RTH/PSU placement is still conceptual; M2.5.11 prevents residual optimizer from using them as a residual dump but does not yet perform landscape/facility demand planning.
- Block regularity/connectivity are heuristic planning metrics, not regulatory approval metrics.
- A topology with more STANDARD lots is preferred, but real planning approval still requires local road, fire access, turning, drainage, RTH/PSU and subdivision regulations.
