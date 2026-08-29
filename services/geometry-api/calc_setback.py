import json
from pathlib import Path
from shapely.geometry import shape

sample_path = Path('d:/project/tesfit/sample-inputs/sample_site.geojson')
data = json.loads(sample_path.read_text())
geom = shape(data['geometry'] if data.get('type') == 'Feature' else data)

import app.main as m
epsg = m.utm_epsg_for_geometry(geom)
poly_utm = m.project_geom(geom, 4326, epsg)

total_area = poly_utm.area
perimeter = poly_utm.length

print(f"Total Area: {total_area:.2f} m2")
print(f"Perimeter: {perimeter:.2f} m")

# Setback 3m
b3 = poly_utm.buffer(-3.0)
area_b3 = b3.area
loss_3m = total_area - area_b3
pct_loss_3m = (loss_3m / total_area) * 100

print(f"Buildable area with 3m setback: {area_b3:.2f} m2 ({(area_b3/total_area)*100:.2f}%)")
print(f"Area lost to 3m setback buffer: {loss_3m:.2f} m2 ({pct_loss_3m:.2f}%)")
print(f"Penambahan luas buildable jika setback = 0: +{loss_3m:.2f} m2 (+{pct_loss_3m:.2f}%)")

# Run generation with setback=3
req3 = m.SitePlanRequest(geometry=data['geometry'] if data.get('type')=='Feature' else data, setback_m=3, lot_width_m=8, lot_depth_m=15, main_road_width_m=8, local_road_width_m=6, rth_pct=10, psu_pct=5, alternative_count=2, land_optimization_enabled=False)
res3 = m.generate_site_alternatives(req3)
opt3 = m.optimize_land_utilization(m.YieldOptimizeRequest(parcel=res3['parcel'], buildable=res3['alternatives'][0]['buildable'], road_segments=[{'id': r['id'], 'kind': r['kind'], 'width_m': r['width_m'], 'centerline': r['centerline']} for r in res3['alternatives'][0]['road_segments']], lots=res3['alternatives'][0]['lots'], rth=res3['alternatives'][0]['rth'], psu=res3['alternatives'][0]['psu'], target_lot_width_m=8, target_lot_depth_m=15, rth_pct=10, psu_pct=5, local_road_width_m=6))

# Run generation with setback=0
req0 = m.SitePlanRequest(geometry=data['geometry'] if data.get('type')=='Feature' else data, setback_m=0, lot_width_m=8, lot_depth_m=15, main_road_width_m=8, local_road_width_m=6, rth_pct=10, psu_pct=5, alternative_count=2, land_optimization_enabled=False)
res0 = m.generate_site_alternatives(req0)
opt0 = m.optimize_land_utilization(m.YieldOptimizeRequest(parcel=res0['parcel'], buildable=res0['alternatives'][0]['buildable'], road_segments=[{'id': r['id'], 'kind': r['kind'], 'width_m': r['width_m'], 'centerline': r['centerline']} for r in res0['alternatives'][0]['road_segments']], lots=res0['alternatives'][0]['lots'], rth=res0['alternatives'][0]['rth'], psu=res0['alternatives'][0]['psu'], target_lot_width_m=8, target_lot_depth_m=15, rth_pct=10, psu_pct=5, local_road_width_m=6))

s3 = opt3['stats']
s0 = opt0['stats']

print("\n--- HASIL OPTIMIZER REAL PADA LAHAN CONTOH ---")
print(f"Setback 3m: {s3['lot_count']} unit ({s3['standard_lot_count']} Std + {s3['adaptive_lot_count']} Adapt), Efisiensi: {s3['lot_efficiency_pct']}%, Luas Kavling: {s3['lots_total_area_m2']} m2")
print(f"Setback 0m: {s0['lot_count']} unit ({s0['standard_lot_count']} Std + {s0['adaptive_lot_count']} Adapt), Efisiensi: {s0['lot_efficiency_pct']}%, Luas Kavling: {s0['lots_total_area_m2']} m2")
delta_eff = s0['lot_efficiency_pct'] - s3['lot_efficiency_pct']
delta_unit = s0['lot_count'] - s3['lot_count']
delta_area = s0['lots_total_area_m2'] - s3['lots_total_area_m2']
print(f"\nPENAMBAHAN: +{delta_eff:.2f}% Efisiensi Kavling, +{delta_unit} unit kavling (+{delta_area:.2f} m2)")
