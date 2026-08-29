import json
import time
from pathlib import Path

import app.main as m

SAMPLE = Path(__file__).resolve().parents[2] / 'sample-inputs' / 'sample_site.geojson'
obj = json.loads(SAMPLE.read_text(encoding='utf-8'))
geom = obj['geometry'] if obj.get('type') == 'Feature' else obj['features'][0]['geometry']

site = m.generate_site_alternatives(m.SitePlanRequest(geometry=geom, alternative_count=4))
alt = site['alternatives'][0]
req = m.YieldOptimizeRequest(
    parcel=site['parcel'],
    buildable=alt['buildable'],
    road_segments=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in alt['road_segments']],
    lots=alt['lots'], rth=alt['rth'], psu=alt['psu'],
    target_lot_width_m=8, min_lot_width_m=7, max_lot_width_m=10,
    target_lot_depth_m=15, min_lot_depth_m=13, max_lot_depth_m=18,
    rth_pct=10, psu_pct=5, local_road_width_m=6,
    road_shift_m=4, allow_road_shift=True, allow_rth_psu_relocation=True,
    allow_selective_extension=True, max_extensions=4,
    max_residual_pct_total=3.0, strict_residual_cap=False,
    allow_residual_rth_absorption=False,
)
start=time.time()
result=m.optimize_land_utilization(req)
elapsed=time.time()-start
opt=result['optimization']
assert result['stats']['optimized'] is True
assert opt['after']['lot_count'] >= opt['before']['lot_count']
assert opt['after']['lots_total_area_m2'] >= opt['before']['lots_total_area_m2'] - 1e-6
assert opt['after']['residual_area_m2'] <= opt['before']['residual_area_m2'] + 1e-6
assert result['stats']['rth_pct'] >= 9.85
assert result['stats']['psu_pct'] >= 4.925
assert result['stats']['residual_pct_total_land'] <= opt['before']['residual_pct_total_land'] + 1e-6
assert result['stats']['reserve_area_m2'] == 0
assert opt['residual_cap']['method'] == 'residual-only-adaptive-parcelization'
assert result['road_segments']
assert result['residuals'] is not None
print(json.dumps({
    'elapsed_s': round(elapsed,2),
    'before_lots': opt['before']['lot_count'],
    'after_lots': opt['after']['lot_count'],
    'before_residual_m2': opt['before']['residual_area_m2'],
    'after_residual_m2': opt['after']['residual_area_m2'],
    'after_residual_pct_total': opt['after']['residual_pct_total_land'],
    'absorbed_to_rth_m2': opt['residual_cap']['absorbed_to_rth_m2'],
    'utilization_delta_pp': opt['delta']['utilization_pct_point'],
    'road_shift': opt['road_shift'],
    'facility_strategy': opt['facility_strategy'],
    'packing_order': opt['packing_order'],
    'residual_count': opt['residual_count'],
}, indent=2))
