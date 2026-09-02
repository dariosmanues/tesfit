# Development OS Dashboard Integration

Branch: `feature/development-os-dashboard`

## Purpose
Add a non-destructive application dashboard in front of the existing M2.5.14 Site Planning workspace and surface only capabilities that currently exist in the repository.

## Architecture
- Existing `services/geometry-api/web/index.html` is preserved byte-for-byte as `services/geometry-api/web/siteplan.html`.
- New `services/geometry-api/web/index.html` becomes the Development OS dashboard shell.
- Dashboard opens the preserved M2.5.14 workspace in a same-origin iframe.
- Existing `market-reference.js` and `market-knn.js` are injected into the Site Plan workspace by the dashboard shell after the workspace is loaded.
- Core solver source (`app.js`, backend geometry solver, recovery monitor) is not modified.

## Dashboard truth model
Marked available/completed:
1. Land polygon and geospatial import.
2. Generative Site Plan Solver.
3. Strict Standard Lot geometry contract.
4. Adaptive/residual logic and M2.5.14 Recovery Solver.
5. Parametric Constraint Editor / Smart Reflow.
6. SQLite/PostGIS project persistence.
7. Market Reference Pekanbaru module and dataset.
8. Robust Spatial KNN module and audit controls.

Not marked complete:
- AI House Generator.
- House-plan AI Audit.
- RAB / Costing engine.
- Feasibility engine.
- Reports M9.
- AI Marketing M10.

## Safety
The dashboard intentionally does not refactor the existing geometry engine. The old Site Plan UI remains independently accessible at `/static/siteplan.html` for regression comparison.
