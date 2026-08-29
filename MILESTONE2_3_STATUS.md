# Milestone 2.3 — Smart Reflow Editor

## Implemented

- Shift+click multi-selection
- Box/marquee selection
- Group drag and nudge
- Group rotation and numeric angle input
- Optional angle snapping
- Select lots linked to a road
- Duplicate/delete multi-selection
- Auto-Reflow after road/lot edits
- Road-linked lot repacking
- Local Reflow and Repack Block actions
- Buildable / overlap / frontage validation
- Yellow highlight for auto-adjusted lots
- Undo + Redo history
- Smart solver endpoints: `/editor/reflow`, `/editor/repack-block`, `/editor/validate`
- Residual overlap safety: if geometry cannot be solved without overlap, the minimum conflicting lot is removed instead of returning an overlapping layout

## Important limitation

This is a deterministic conceptual site-planning solver, not a regulatory or DED approval engine. Road hierarchy, turning geometry, fire access, local PSU/RTH rules, drainage gradients, terrain/cut-fill, and official road connections still require later milestones and engineering validation.

## Quick test

1. Load Sample Pekanbaru.
2. Generate 4 alternatives.
3. Activate Smart Editor.
4. Shift+click several lots or choose Box Select.
5. Drag group or rotate using Angle.
6. Select one road and move/rotate it.
7. Confirm nearby lots reflow and yellow-highlight.
8. Click Validate; overlap/outside/frontage should be reported.
9. Undo/Redo and save final layout.
