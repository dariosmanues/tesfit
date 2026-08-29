import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from app.main import SitePlanRequest, generate_site_alternatives, SitePlanRecalculateRequest, recalculate_manual_layout, YieldOptimizeRequest, optimize_land_utilization

sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())
geom=sample['features'][0]['geometry'] if sample.get('type')=='FeatureCollection' else sample.get('geometry',sample)
req=SitePlanRequest(geometry=geom,setback_m=3,lot_width_m=8,lot_depth_m=15,main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,alternative_count=4,land_optimization_enabled=False)
out=generate_site_alternatives(req)
a=out['alternatives'][0]
assert a['reserve'] is None, a['reserve']
assert a['stats']['land_optimization_enabled'] is False
assert a['stats']['unused_area_m2'] > 0
# OFF recalc must not create reserve or fake a 3% residual.
rr=recalculate_manual_layout(SitePlanRecalculateRequest(parcel=out['parcel'],buildable=a['buildable'],roads=a['roads'],rth=a['rth'],psu=a['psu'],reserve=None,drainage=a['drainage'],lots=a['lots'],previous_stats=a['stats'],land_optimization_enabled=False))
assert rr['reserve'] is None
assert rr['stats']['land_optimization_enabled'] is False
# Optimizer request explicitly disables residual absorption. It may succeed or explicitly fail,
# but it must never report landscape-reserve as the cap method.
opt=YieldOptimizeRequest(parcel=out['parcel'],buildable=a['buildable'],road_segments=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in a['road_segments']],lots=a['lots'],rth=a['rth'],psu=a['psu'],allow_residual_rth_absorption=False,max_residual_pct_total=3,strict_residual_cap=False,max_extensions=1)
res=optimize_land_utilization(opt)
cap=res['optimization']['residual_cap']
assert cap.get('method') != 'landscape-reserve', cap
print(json.dumps({
 'baseline_lots':a['stats']['lot_count'],
 'baseline_residual_pct_total':a['stats']['residual_pct_total_land'],
 'baseline_reserve_area':a['stats']['reserve_area_m2'],
 'recalc_residual_pct_total':rr['stats']['residual_pct_total_land'],
 'optimizer_cap_method':cap.get('method'),
 'optimizer_true_residual_pct_total':res['stats']['residual_pct_total_land'],
},indent=2))
