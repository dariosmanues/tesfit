# Development OS — Milestone 2.5.6.1 Topology Overlay Hotfix

## Fixed
- Prevents `/site-plan/optimize-yield` from crashing on GEOS `TopologyException: side location conflict` during residual parcel slicing.
- Repairs invalid polygon operands with `make_valid`, precision snapping (1 mm), and a final `buffer(0)` cleanup before retrying overlay.
- Applies the robust overlay path to both adaptive residual frontage fill and residual parcelization strip slicing.
- If a numerically broken micro-fragment still cannot be overlaid after repair, that fragment is skipped instead of returning HTTP 500 for the whole optimization request.

## Scope
This hotfix addresses the exact backend exception captured from M2.5.5/M2.5.6. It does not change the residual-parcelization or relocation design.
