# Development OS — Milestone 2 Status

## Implemented

- OpenStreetMap basemap via MapLibre.
- Draw polygon, manual coordinates, KML/KMZ, GeoJSON, CSV, SHP ZIP, DXF import.
- Uniform setback and buildable area.
- Automatic conceptual road networks with main/local road widths.
- Automatic RTH reservation by target percentage.
- Automatic PSU reservation by target percentage.
- Conceptual drainage on both sides of generated road corridors.
- Automatic lot placement with road frontage heuristics.
- 2–8 automatic layout alternatives (parallel, spine, cross-grid and rotated variants).
- Alternative ranking and click-to-compare UI.
- Statistics: lot count, lot efficiency, road area/length, RTH, PSU, drainage length, unused area.
- Save selected alternative to SQLite/PostGIS.

## Important limitation

Milestone 2 is a **conceptual heuristic site planner**, not a regulatory-approved site plan or DED. Road access points, exact frontage constraints, turning radii, fire access, drainage elevations/hydraulics, topography, cut-fill, local PSU/RTH rules and detailed RDTR checks are not yet engineering-validated.

## Smoke test — sample Pekanbaru

With setback 3 m, lot 8 x 15 m, main road 8 m, local road 6 m, RTH 10%, PSU 5%, the current heuristic produced four ranked alternatives. The best smoke-test option generated 105 lots, about 5,257 m² road area, 10% RTH, 5% PSU, and about 1,715 m conceptual drainage.

## Next recommended milestone

1. User-selected road access point/frontage.
2. Setback front/side/rear separately.
3. Road turning radius, dead-end/cul-de-sac rules and max block length.
4. RTH/PSU regulation profiles per project type.
5. Topography/contour + drainage flow direction + cut/fill.
6. Multi-objective optimizer (units, road cost, RTH, infrastructure, IRR).
