import json
from pathlib import Path
from shapely.geometry import shape
import app.main as m

sample=json.loads(Path(__file__).resolve().parents[2].joinpath('sample-inputs','sample_site.geojson').read_text())
geom=sample['geometry'] if sample.get('type')=='Feature' else sample
req=m.SitePlanRequest(
    geometry=geom,setback_m=3,lot_width_m=8,lot_depth_m=15,
    main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,
    alternative_count=6,land_optimization_enabled=False
)
site=m.generate_site_alternatives(req)
assert len(site['alternatives'])==6
best=site['alternatives'][0]
# M2.5.10 sample baseline was 131 exact STANDARD lots. M2.5.11 may tie or improve,
# but must not regress the standard-product yield.
assert best['stats']['standard_lot_count'] >= 130, best['stats']
assert best['stats']['invalid_standard_lot_count']==0
assert best['stats']['average_block_regularity'] > 0
assert 'road_connectivity_score' in best['stats']
assert best['parcelization']['standard_source']=='geometry_settings'
assert best['parcelization']['adaptive_source']=='residual_only'
for d in best['lot_details']:
    assert d['parcel_type']=='standard'
    assert abs(d['area_m2']-120.0)<=0.02
    assert abs(d['frontage_m']-8.0)<=0.02
    assert abs(d['depth_est_m']-15.0)<=0.02

payload=m.YieldOptimizeRequest(
    parcel=site['parcel'],buildable=best['buildable'],
    road_segments=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in best['road_segments']],
    lots=best['lots'],lot_details=best['lot_details'],rth=best['rth'],psu=best['psu'],
    target_lot_width_m=8,target_lot_depth_m=15,rth_pct=10,psu_pct=5,local_road_width_m=6
)
opt=m.optimize_land_utilization(payload)
assert opt['optimization']['version'] in ('2.5.11', '2.5.12')
assert opt['optimization']['optimizer_type']=='RESIDUAL_ONLY'
assert opt['stats']['standard_lot_count']==best['stats']['standard_lot_count']
assert opt['validation']['standard_lot_count_preserved'] is True
assert opt['validation']['roads_immutable'] is True
assert opt['validation']['rth_psu_immutable'] is True
assert opt['validation']['invalid_standard_lot_count']==0
assert opt['validation']['adaptive_origin_violation_count']==0
assert opt['stats']['residual_pct_total_land']<=3.01
# Roads and facilities must be geometrically unchanged by residual optimization.
assert shape(opt['roads']).symmetric_difference(shape(best['roads'])).area < 1e-12
if best['rth'] and opt['rth']:
    assert shape(opt['rth']).symmetric_difference(shape(best['rth'])).area < 1e-12
if best['psu'] and opt['psu']:
    assert shape(opt['psu']).symmetric_difference(shape(best['psu'])).area < 1e-12
print(json.dumps({
    'version':'2.5.11','best_masterplan':best['name'],
    'standard_lots':best['stats']['standard_lot_count'],
    'block_regularity':best['stats']['average_block_regularity'],
    'road_connectivity':best['stats']['road_connectivity_score'],
    'adaptive_after_residual_optimizer':opt['stats']['adaptive_lot_count'],
    'true_residual_pct':opt['stats']['residual_pct_total_land'],
},indent=2))
