import json
from pathlib import Path
import app.main as m

SAMPLE = Path(__file__).resolve().parents[2] / 'sample-inputs' / 'sample_site.geojson'
obj = json.loads(SAMPLE.read_text(encoding='utf-8'))
geom = obj['geometry'] if obj.get('type') == 'Feature' else obj['features'][0]['geometry']
site = m.generate_site_alternatives(m.SitePlanRequest(
    geometry=geom, alternative_count=4, max_residual_pct_total=3.0,
    land_optimization_enabled=False
))

# Since M2.5.4.1 the 3% cap is optimizer-only. Baseline must remain honest.
for alt in site['alternatives']:
    s = alt['stats']
    assert s['optimized'] is False
    assert s['reserve_area_m2'] == 0
    assert 'residual_pct_total_land' in s
    assert s['invalid_standard_lot_count'] == 0

alt = site['alternatives'][0]
r = m.recalculate_manual_layout(m.SitePlanRecalculateRequest(
    parcel=site['parcel'], buildable=alt['buildable'], roads=alt['roads'],
    rth=alt['rth'], psu=alt['psu'], reserve=alt['reserve'], drainage=alt['drainage'],
    lots=alt['lots'], previous_stats=alt['stats'], land_optimization_enabled=False,
))
assert abs(r['stats']['residual_pct_total_land'] - alt['stats']['residual_pct_total_land']) < 0.05

opt = m.optimize_land_utilization(m.YieldOptimizeRequest(
    parcel=site['parcel'], buildable=alt['buildable'],
    road_segments=[{'id':x['id'],'kind':x['kind'],'width_m':x['width_m'],'centerline':x['centerline']} for x in alt['road_segments']],
    lots=alt['lots'], rth=alt['rth'], psu=alt['psu'],
    target_lot_width_m=8, target_lot_depth_m=15, max_optimize_seconds=20
))
assert opt['stats']['residual_pct_total_land'] <= 3.01, opt['stats']
assert opt['stats']['residual_cap_met'] is True
assert opt['validation']['invalid_standard_lot_count'] == 0

print(json.dumps({
    'alternatives': len(site['alternatives']),
    'baseline_residual_pct_total': alt['stats']['residual_pct_total_land'],
    'recalc_residual_pct_total': r['stats']['residual_pct_total_land'],
    'optimized_residual_pct_total': opt['stats']['residual_pct_total_land'],
    'optimized_standard': opt['stats']['standard_lot_count'],
    'optimized_adaptive': opt['stats']['adaptive_lot_count'],
}, indent=2))
