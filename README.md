# Development OS — Milestone 2.5.12 Gross Lot Efficiency >= 70% Acceptance Model

## CURRENT BEHAVIOR — M2.5.12
- **Hard Requirement: Gross Lot Efficiency >= 70%.** Formula: `lot_efficiency_pct = (standard_lot_area + adaptive_lot_area) / total_land_area * 100`. Total parcel area is used as the sole denominator.
- **Residual <= 3% hard gate removed:** Residual is now purely informational (`residual_true_area_m2`, `residual_true_pct`) and never causes layout rejection if lot efficiency >= 70%.
- **Runtime cleanup:** no active residual-3% sweep/save/recalculation gate remains; road/block topology and Standard packing are unchanged.
- **Geometry Settings is an immutable STANDARD module.** Example: 8 m x 15 m means every STANDARD lot is exactly 8 x 15 (120 m2); it is never clipped, shrunk, stretched, deformed, or transformed into Adaptive.
- **Masterplan optimization happens before lot packing.** Generate searches multiple road topology/orientation candidates, forms blocks, analyzes block quality, then packs exact STANDARD lots.
- **Candidate ranking priority:** Passing candidates (lot efficiency >= 70%) -> Maximum STANDARD count -> Highest lot efficiency % -> Fewest Adaptive count -> Smallest Road area -> Smallest Residual area -> Higher Block Regularity -> Higher Road Connectivity.
- **Adaptive = TRUE residual only.** Adaptive lots are created strictly after STANDARD packing from leftover road-fronting land that passes saleability validation.
- **Residual Optimizer is non-destructive.** It preserves STANDARD lots, roads, RTH, and PSU, converting leftover land into validated Adaptive lots to maximize gross lot efficiency towards >= 70%.
- **Planning diagnostics:** alternatives expose STANDARD/ADAPTIVE counts, lot efficiency %, block regularity, road connectivity, and residual %.
- **Parametric safety remains atomic.** Failed dependency reflow restores the pre-edit snapshot.

Local web prototype for generative residential site planning.

## Run on Windows

From the project root:

```powershell
.\setup_windows.ps1
.\run_windows.ps1
```

Open `http://localhost:8000`, then press `Ctrl+F5` after replacing an older frontend patch.

The setup script uses Python 3.11 when available. No GPU is required.

## Workflow

1. Draw/import a land polygon.
2. Set setback and lot dimensions.
3. Set main/local road widths, RTH target, PSU target and number of alternatives.
4. Click **Generate Alternatif Layout**.
5. Click alternative cards to compare them on the map (PASS/FAIL against >= 70% efficiency).
6. Select the best masterplan based on STANDARD yield / block quality, then run **M2.5.12 Residual Optimizer** to convert leftover residual land into Adaptive lots towards >= 70% efficiency without altering roads/Standard/RTH/PSU.
7. Compare before/after: standard vs adaptive count, lot efficiency %, sellable area, residual, utilization, and road efficiency.
8. Activate **Parametric Constraint Editor** if manual adjustments are needed.
9. Use Shift+click / Box Select and Parametric Auto-Reflow for local edits.
10. Save final validated layout (requires `validation.valid == True`).

## Supported input

- Draw on OpenStreetMap
- Manual coordinates (EPSG:4326 or custom EPSG/UTM)
- KML/KMZ
- GeoJSON
- CSV
- SHP as ZIP
- DXF with source EPSG
- DWG: convert to DXF first

## Generated conceptual layers

- Buildable area
- Main/local roads
- Lots
- RTH
- PSU
- Drainage lines along both road sides
- Ranked layout alternatives

## Database

Local mode uses SQLite fallback. Docker mode uses PostgreSQL + PostGIS. Selected M2 layout data is saved together with the project.

See `MILESTONE2_STATUS.md` for scope and limitations.


## Milestone 2.2 editor
Manual CAD-lite adjustment now includes individual road segment editing, endpoint handles, numeric angles, road width/type, lot dimensions, add/delete tools, and whole-layout orientation. See `MILESTONE2_2_STATUS.md`.


## Milestone 2.3 Smart Reflow Editor

M2.3 adds multi-selection, box selection, group move/rotate, numeric angle with snap, auto-reflow for road-linked lots, local repacking, overlap/frontage/buildable validation, adjusted-object highlight, and undo/redo. The solver may remove the minimum number of lots when a local edit cannot be made overlap-free. See `MILESTONE2_3_STATUS.md`.


## Milestone 2.4 Parametric Constraint Editor

M2.4 replaces the earlier proximity-based reflow with an explicit Road → Block → Lot dependency model. Moving a road propagates to linked and crossing blocks; moving/resizing a lot repacks its frontage block. The dependency graph is rebuilt after every exact solve. See `MILESTONE2_4_STATUS.md`.


## Milestone 2.5.4.1 Optional Land Utilization Optimizer

M2.5 adds residual detection, adaptive frontage/depth packing, competing corner-lot frontage orders, bounded road-network position search, RTH/PSU relocation candidates, selective road extension, and best-yield scoring. M2.5.2 additionally makes residual <=3% of total parcel area a global invariant for every generated option. Excess non-sellable area is shown separately as Landscape/Reserve, while RTH/PSU targets remain distinct. See `MILESTONE2_5_2_STATUS.md`.

## Milestone 2.5.1 — Strict Residual Cap

M2.5.1 makes residual a hard constraint: after optimization the remaining unallocated land may not exceed **3% of total parcel area**. The solver first attempts variable lot packing, road-position search and selective road extensions. If geometry still leaves unavoidable residual above 3%, the excess can be explicitly allocated as additional RTH/landscape buffer. This reassignment is reported in the UI and API and is never hidden. See `MILESTONE2_5_1_STATUS.md`.


## Milestone 2.5.4.1 — Optional optimizer mode
Use the **Optimalisasi Lahan (M2.5)** checkbox as the master switch. OFF restores the original generated scenario. ON runs Best Yield optimization for the selected alternative.


## Milestone 2.5.4.1 Release Gate

- Optimalisasi Lahan tetap opt-in melalui checkbox.
- OFF mempertahankan baseline generate asli dan tidak memaksa residual 3%.
- ON menjalankan adaptive residual frontage fill + bounded road/facility search.
- TRUE residual tidak pernah dipindahkan ke Landscape/Reserve.
- Jika geometri sellable belum cukup untuk mencapai batas, excess residual hanya boleh menjadi RTH tambahan fungsional dan dilaporkan eksplisit.
- Target residual saat ON: <= 3% dari total luas parcel.
- Optimizer memiliki time budget sehingga pencarian tidak menggantung tanpa batas.

Lihat `MILESTONE2_5_4_STATUS.md` untuk hasil smoke test release.

## Milestone 2.5.5 — Residual Parcelization Optimizer
M2.5.5 changes the optimization model so usable road-fronting leftover land is returned as explicit irregular **Kavling Sisa** instead of being forced to standard dimensions. Residual parcels are shown in amber and carry actual area/frontage/depth metadata. TRUE residual is only the land still unallocated after parcelization. See `MILESTONE2_5_5_STATUS.md`.


## Milestone 2.5.6.1 — Relocation Reflow
When parametric edits displace lots, the solver now searches the whole road network for valid vacant frontage and moves those lots there. `preserve_count=true` never silently drops lots; if all displaced lots cannot be relocated, the edit is rejected. See `MILESTONE2_5_6_STATUS.md`.

## Milestone 2.5.7 — Automatic 3% Residual Target
M2.5.7 simplifies land optimization to one opt-in rule: when enabled, TRUE residual must be at most 3% of total parcel area. All previous optimizer knobs were removed from the UI. Remaining developable geometry is represented as explicit amber residual parcels with actual polygon area/dimensions rather than being hidden as Reserve/RTH. See `MILESTONE2_5_7_STATUS.md`.


## Milestone 2.5.8 — Saleable Residual Lot Validation
M2.5.8 keeps the simple optimization target **TRUE residual <=3%**, but now an amber residual polygon is counted as a lot only when it is genuinely saleable by geometric rules: minimum 60 m2 actual area, minimum 4 m real shared-edge road frontage, inside buildable, no lot/road/RTH/PSU overlap, and anti-sliver shape checks. Invalid residual candidates return to TRUE residual. `/site-plan/optimize-yield` and Save both require final validation PASS. See `MILESTONE2_5_8_STATUS.md`.


## Milestone 2.5.10 — Block-First Parcelization
See `MILESTONE2_5_10_STATUS.md` for the release criteria, algorithm, regression evidence, and known limitations.


## Milestone 2.5.11 — Road & Block Topology Optimization
See `MILESTONE2_5_11_STATUS.md` for the current release criteria, topology search, block metrics, residual-only optimizer contract, regression evidence, and known limitations.
- M2.5.13 adds a real staged Recovery Solver Monitor; only geometry-valid alternatives with gross lot efficiency >=70% enter the selectable Alternative Layout pool.
