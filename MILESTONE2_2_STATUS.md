# Development OS — Milestone 2.2

## Manual Siteplan Editor / CAD-lite

Status: implemented and smoke-tested.

### Added
- Select/move individual lots, RTH, PSU, and individual road segments.
- Road segments are now preserved separately from the road union.
- Numeric object angle/orientation editor.
- Numeric whole-layout orientation editor.
- Road width and road kind editor.
- Drag road endpoint handles to change segment length/direction.
- Add road tool: click start point and end point.
- Duplicate/delete individual road segment.
- Add lot tool: click map to place a lot.
- Numeric lot width/depth editor.
- Duplicate/delete lots.
- Nudge selected geometry by a configurable meter step.
- Undo and reset to generated baseline.
- Road corridors and two-sided conceptual drainage are rebuilt after road edits.
- Manual geometry recalculation and validation remain active.

### Backend
- API version 0.4.0.
- `POST /site-plan/roads/rebuild` rebuilds corridor polygons + drainage from editable centerlines/widths.
- Generated alternatives now include `road_segments` with centerline, polygon, drainage, width, type, angle, and length.

### Verification
- Python compilation: PASS.
- Frontend JavaScript syntax (`node --check`): PASS.
- Existing M1 smoke test: PASS.
- M2 generation + road rebuild + recalculate API test: PASS.

### Important limitation
This remains a conceptual site-planning editor, not CAD/DED. Snapping, orthogonal constraints, vertex editing for arbitrary polygons, regulatory road geometry, and engineering-grade drainage elevations are later milestones.
