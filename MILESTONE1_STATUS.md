# Milestone 1 — Status

## Done

- [x] Web UI with MapLibre map
- [x] Draw parcel polygon
- [x] Manual coordinate input
- [x] EPSG/custom projected-coordinate conversion
- [x] KML import
- [x] KMZ import
- [x] GeoJSON import
- [x] CSV import
- [x] SHP ZIP import
- [x] DXF closed polyline import
- [x] Explicit DWG-to-DXF requirement
- [x] Geometry validation + repair
- [x] Automatic UTM calculation CRS
- [x] Parcel area + perimeter
- [x] Uniform setback
- [x] Buildable-area geometry
- [x] Simple 8x15-style lot grid generator
- [x] Orientation search
- [x] Lot count + efficiency statistics
- [x] Save project endpoint
- [x] PostGIS schema/geometry storage in Docker mode
- [x] SQLite fallback for quick local development
- [x] Windows setup/run scripts
- [x] Smoke tests for geometry engine

## Verified in smoke tests

Sample Pekanbaru polygon:

- Parcel area: 28,217.45 m²
- Perimeter: 652.33 m
- Setback: 3 m
- Buildable area: 26,294.06 m²
- Lot size: 8 x 15 m
- Lots generated: 188
- Lot efficiency: 79.95%
- Calculation CRS: EPSG:32647 (UTM 47N)

Manual coordinates, GeoJSON, KML, CSV, SHP ZIP and DXF all resolved to the same parcel area in the automated smoke test.

## Intentionally NOT in Milestone 1

- Road generation
- Frontage logic
- Separate front/side/rear setbacks
- RTH / PSU allocation
- Drainage
- Multiple development alternatives / Pareto optimization
- RDTR/BHUMI integration
- DED/BIM/RAB

These are Milestone 2+ so the geometry foundation stays testable.

## Patch 1.0.1 — Frontend bootstrap
- MapLibre CDN dipin ke v5.12.0 untuk classic-script compatibility.
- UI event handlers dipasang sebelum map initialization.
- API health check tetap berjalan meski map/CDN gagal.
- Manual coordinate/import tetap bisa dipakai jika basemap tidak termuat.
- Windows setup menggunakan Python 3.11 dengan fallback ke Python 3 default.
