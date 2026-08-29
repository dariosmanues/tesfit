import json, time
from pathlib import Path
from shapely.geometry import Polygon, LineString, mapping, shape
import app.main as m

ROOT=Path(__file__).resolve().parent
sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())
geom=sample['geometry'] if sample.get('type')=='Feature' else sample

# 1) Unit-level saleability tests: actual shared-edge frontage, area and anti-sliver.
build=Polygon([(0,3),(100,3),(100,60),(0,60)])
line=LineString([(0,0),(100,0)])
road={'id':'R1','kind':'local','width_m':6.0,'line':line,'corridor':line.buffer(3,cap_style=2,join_style=2)}
valid=Polygon([(10,3),(16,3),(16,15),(10,15)])  # 72m2, frontage 6m
small=Polygon([(20,3),(25,3),(25,13),(20,13)]) # 50m2
narrow_front=Polygon([(30,3),(33,3),(33,23),(30,23)]) # 60m2, frontage 3m
sliver=Polygon([(40,3),(44,3),(44,63),(40,63)]) # aspect ratio 15

v=m._residual_saleability(valid,[road],build)
s=m._residual_saleability(small,[road],build)
n=m._residual_saleability(narrow_front,[road],build)
sl=m._residual_saleability(sliver,[road],build.buffer(5))
assert v['saleable'] and v['frontage_m'] >= 5.99 and v['area_m2'] >= 71.99, v
assert not s['saleable'] and 'area_below_minimum' in s['reasons'], s
assert not n['saleable'] and 'frontage_below_minimum' in n['reasons'], n
assert not sl['saleable'] and 'aspect_ratio_too_high' in sl['reasons'], sl

# 2) End-to-end optimizer on real sample.
site=m.generate_site_alternatives(m.SitePlanRequest(
    geometry=geom,setback_m=3,lot_width_m=8,lot_depth_m=15,
    main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,
    alternative_count=2,land_optimization_enabled=False
))
a=site['alternatives'][0]
req=m.YieldOptimizeRequest(
    parcel=site['parcel'],buildable=a['buildable'],
    road_segments=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in a['road_segments']],
    lots=a['lots'],rth=a['rth'],psu=a['psu'],target_lot_width_m=8,target_lot_depth_m=15,
    rth_pct=10,psu_pct=5,local_road_width_m=6,max_optimize_seconds=20
)
t=time.perf_counter();res=m.optimize_land_utilization(req);elapsed=time.perf_counter()-t
assert res['optimization']['version'] in ('2.5.8', '2.5.11', '2.5.12'), res['optimization']['version']
assert res['stats']['residual_pct_total_land'] <= 3.01, res['stats']
val=res['validation']
assert val['lots_outside_buildable']==0, val
assert val['lot_overlap_pairs']==0, val
assert val['lot_road_overlaps']==0, val
assert val['lot_obstacle_overlaps']==0, val
assert val['rth_psu_overlap']==0, val
assert val['invalid_residual_lot_count']==0, val
assert val['lot_count_preserved'] is True, val
assert val['rth_psu_overlap']==0, val
assert val['invalid_residual_lot_count']==0, val
assert val['lot_count_preserved'] is True, val
residual_details=[d for d in res['lot_details'] if d['parcel_type']=='residual']
assert residual_details, 'expected residual lots'
assert all(d['area_m2'] >= 59.99 for d in residual_details), [d for d in residual_details if d['area_m2']<59.99][:3]
assert all(d['frontage_m'] >= 3.99 for d in residual_details), [d for d in residual_details if d['frontage_m']<3.99][:3]

# 3) Save hard gate must reject a forged/stale optimized layout that lacks final validation.
try:
    m.save_project(m.ProjectRequest(
        name='invalid-save-test',parcel=site['parcel'],buildable=a['buildable'],lots=a['lots'],
        layout={'stats':{'residual_pct_total_land':1.0}},
        settings={'enforce_residual_cap':True,'max_residual_pct_total':3},
        stats={'residual_pct_total_land':1.0,'validation_passed':False}
    ))
    raise AssertionError('save should have been rejected')
except Exception as e:
    assert getattr(e,'status_code',None)==422, repr(e)

print(json.dumps({
    'version':'2.5.11','all_passed':True,
    'baseline_lots':a['stats']['lot_count'],
    'final_lots':res['stats']['lot_count'],
    'residual_lots':res['stats']['residual_lot_count'],
    'true_residual_pct':res['stats']['residual_pct_total_land'],
    'final_validation':val,
    'elapsed_s':round(elapsed,3),
},indent=2))
