# Development OS — Milestone 2.5.3 Optional Land Optimization Toggle

## Behavior
- Land optimization is OFF by default.
- OFF keeps the original generated M2.4-style scenario: no automatic reserve allocation, no 3% save gate, and no optimizer geometry is applied.
- ON immediately optimizes the selected alternative; after Generate it automatically optimizes option #1.
- Selecting another alternative while ON runs the optimizer for that selected alternative.
- Turning the checkbox OFF restores that alternative exactly from the generated baseline stored in `baselineByAltId`.
- The optimizer panel is disabled while OFF.
- A secondary `Optimasi Ulang Opsi Terpilih` button remains available while ON to rerun after changing optimizer parameters.
- Residual-to-Landscape/Reserve absorption is disabled in optimizer requests. Strict 3% mode therefore uses true unallocated residual; if it cannot reach 3%, the optimizer reports failure instead of relabeling residual.
- Project save enforces the 3% residual cap only while optimization mode is ON.

## Master control
`Optimalisasi Lahan (M2.5)` in Site Planning Settings.
