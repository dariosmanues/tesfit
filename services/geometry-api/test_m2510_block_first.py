import json
from pathlib import Path
from shapely.geometry import box, LineString
from shapely.ops import unary_union
import app.main as m

# 1) Pure block test: 80m frontage x 30m depth = 2 exact 15m rows.
block = box(0, 0, 80, 30)
roads = [
    {'id':'R-bottom','kind':'local','width_m':6.0,'line':LineString([(0,-3),(80,-3)])},
    {'id':'R-top','kind':'local','width_m':6.0,'line':LineString([(0,33),(80,33)])},
]
for r in roads:
    r['corridor'] = r['line'].buffer(r['width_m']/2, cap_style=2, join_style=2)
lots, meta, info = m._pack_standard_blocks(block, roads, 8.0, 15.0, roads)
assert len(lots) == 20, len(lots)
assert all(abs(g.area-120.0) < 1e-6 for g in lots)
assert all(x.get('parcel_type') == 'standard' and x.get('source') == 'geometry_settings' for x in meta)
audit = m._standard_geometry_audit(lots, meta, 8.0, 15.0)
assert audit['invalid_standard_lot_count'] == 0, audit
res = block.difference(unary_union(lots))
assert res.area < 1e-6, res.area

# 2) 83m frontage: remainder stays at the block end, never between standard lots.
block83 = box(0, 0, 83, 30)
lots83, meta83, _ = m._pack_standard_blocks(block83, roads, 8.0, 15.0, roads)
assert len(lots83) == 20, len(lots83)
res83 = block83.difference(unary_union(lots83))
assert abs(res83.area - 90.0) < 0.1, res83.area
parts = m._poly_parts(res83)
assert len(parts) <= 2, [(p.area,p.bounds) for p in parts]
# No lot may deviate from 8x15 to consume the 3m remainder.
assert m._standard_geometry_audit(lots83, meta83, 8.0, 15.0)['invalid_standard_lot_count'] == 0

# 3) Real sample: Geometry Settings are the STANDARD contract.
ROOT=Path(__file__).resolve().parent
sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())
geom=sample['geometry'] if sample.get('type')=='Feature' else sample
site=m.generate_site_alternatives(m.SitePlanRequest(
    geometry=geom,setback_m=3,lot_width_m=8,lot_depth_m=15,
    main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,
    alternative_count=2,land_optimization_enabled=False
))
a=site['alternatives'][0]
assert a['parcelization']['strategy'] == 'road-block-standard-first'
assert a['parcelization']['standard_source'] == 'geometry_settings'
assert a['parcelization']['adaptive_source'] == 'residual_only'
assert a['stats']['invalid_standard_lot_count'] == 0
assert a['stats']['adaptive_lot_count'] == 0
assert a['stats']['standard_lot_count'] == a['stats']['lot_count']
for d in a['lot_details']:
    assert d['parcel_type']=='standard', d
    assert abs(d['area_m2']-120.0) <= 0.02, d
    assert abs(d['frontage_m']-8.0) <= 0.02, d
    assert abs(d['depth_est_m']-15.0) <= 0.02, d

# 4) Not hard-coded to 8x15: 7x14 must produce 98m2 STANDARD lots.
site714=m.generate_site_alternatives(m.SitePlanRequest(
    geometry=geom,setback_m=3,lot_width_m=7,lot_depth_m=14,
    main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,
    alternative_count=2,land_optimization_enabled=False
))
a714=site714['alternatives'][0]
assert a714['stats']['invalid_standard_lot_count']==0
for d in a714['lot_details'][:30]:
    assert abs(d['area_m2']-98.0) <= 0.02, d
    assert abs(d['frontage_m']-7.0) <= 0.02, d
    assert abs(d['depth_est_m']-14.0) <= 0.02, d

# 5) Optimizer: adaptive parcels may exist ONLY after standard packing from residual land.
req=m.YieldOptimizeRequest(
    parcel=site['parcel'],buildable=a['buildable'],
    road_segments=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in a['road_segments']],
    lots=a['lots'],rth=a['rth'],psu=a['psu'],
    target_lot_width_m=8,target_lot_depth_m=15,rth_pct=10,psu_pct=5,
    local_road_width_m=6,max_optimize_seconds=20
)
opt=m.optimize_land_utilization(req)
assert opt['optimization']['version'] in ('2.5.11', '2.5.12')
assert opt['optimization']['parcelization_strategy']=='road-block-standard-first'
assert opt['optimization']['standard_source']=='geometry_settings'
assert opt['optimization']['adaptive_source']=='residual_only'
assert opt['validation']['invalid_standard_lot_count']==0, opt['validation']
assert opt['validation']['adaptive_origin_violation_count']==0, opt['validation']
assert opt['stats']['residual_pct_total_land'] <= 3.01, opt['stats']
for d in opt['lot_details']:
    if d['parcel_type']=='standard':
        assert abs(d['area_m2']-120.0) <= 0.02, d
        assert abs(d['frontage_m']-8.0) <= 0.02, d
        assert abs(d['depth_est_m']-15.0) <= 0.02, d

print(json.dumps({
    'version':'2.5.11',
    'pure_block_standard_lots':len(lots),
    'remainder_block_standard_lots':len(lots83),
    'remainder_area_m2':round(res83.area,2),
    'sample_baseline_standard':a['stats']['standard_lot_count'],
    'sample_baseline_residual_pct':a['stats']['residual_pct_total_land'],
    'optimized_standard':opt['stats']['standard_lot_count'],
    'optimized_adaptive':opt['stats']['adaptive_lot_count'],
    'optimized_true_residual_pct':opt['stats']['residual_pct_total_land'],
    'invalid_standard':opt['validation']['invalid_standard_lot_count'],
    'adaptive_origin_violations':opt['validation']['adaptive_origin_violation_count'],
}, indent=2))
