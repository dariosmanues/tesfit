import json, time
from pathlib import Path
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid
import app.main as m

ROOT=Path(__file__).resolve().parent
sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())

def road_segments(alt):
    return [{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in alt['road_segments']]

def run_case(name, geometry, seconds=20, stale=False):
    site=m.generate_site_alternatives(m.SitePlanRequest(geometry=geometry,setback_m=3,lot_width_m=8,lot_depth_m=15,main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,alternative_count=2,land_optimization_enabled=False))
    alt=site['alternatives'][0]
    kwargs=dict(parcel=site['parcel'],buildable=alt['buildable'],road_segments=road_segments(alt),lots=alt['lots'],rth=alt['rth'],psu=alt['psu'],rth_pct=10,psu_pct=5,local_road_width_m=6,max_optimize_seconds=seconds)
    if stale:
        # Simulate the broken old UI values. M2.5.8 must still ignore these stale min/max knobs.
        kwargs.update(target_lot_width_m=3,min_lot_width_m=3,max_lot_width_m=3,target_lot_depth_m=3,min_lot_depth_m=3,max_lot_depth_m=2,max_residual_pct_total=19,strict_residual_cap=False,allow_road_shift=False,allow_rth_psu_relocation=False,allow_selective_extension=False,max_extensions=0)
    else:
        kwargs.update(target_lot_width_m=8,target_lot_depth_m=15)
    t=time.perf_counter(); result=m.optimize_land_utilization(m.YieldOptimizeRequest(**kwargs)); elapsed=time.perf_counter()-t
    assert result['stats']['residual_pct_total_land'] <= 3.0 + 0.01
    assert result['stats']['residual_cap_pct_total'] == 3.0
    assert result['stats']['residual_cap_met'] is True
    assert result['stats']['reserve_area_m2'] == 0
    assert result['optimization']['version']=='2.5.11'
    assert len(result['lot_details'])==len(result['lots'])

    epsg=result['utm_epsg']
    build=m._polygonal_only(make_valid(m.project_geom(shape(result['buildable']),4326,epsg)))
    roads=m._polygonal_only(make_valid(m.project_geom(shape(result['roads']),4326,epsg))) if result.get('roads') else Polygon()
    rth=m._polygonal_only(make_valid(m.project_geom(shape(result['rth']),4326,epsg))) if result.get('rth') else Polygon()
    psu=m._polygonal_only(make_valid(m.project_geom(shape(result['psu']),4326,epsg))) if result.get('psu') else Polygon()
    lots=[m._polygonal_only(make_valid(m.project_geom(shape(g),4326,epsg))) for g in result['lots']]
    outside=sum(g.difference(build.buffer(0.03)).area for g in lots)
    tree=STRtree(lots); overlap=0.0
    for i,g in enumerate(lots):
        for j in tree.query(g):
            j=int(j)
            if j<=i: continue
            overlap += g.intersection(lots[j]).area
    blocked=unary_union([x for x in (roads,rth,psu) if not x.is_empty])
    blocked_overlap=sum(g.intersection(blocked).area for g in lots)
    assert outside < 1.0
    assert overlap < 1.0
    assert blocked_overlap < 2.0
    return {
        'case':name,
        'baseline_residual_pct':round(alt['stats']['residual_pct_total_land'],2),
        'optimized_residual_pct':round(result['stats']['residual_pct_total_land'],2),
        'baseline_lots':alt['stats']['lot_count'],
        'optimized_lots':result['stats']['lot_count'],
        'residual_lots':result['stats']['residual_lot_count'],
        'reserve_m2':result['stats']['reserve_area_m2'],
        'outside_m2':round(outside,4),
        'overlap_m2':round(overlap,4),
        'blocked_overlap_m2':round(blocked_overlap,4),
        'elapsed_s':round(elapsed,3),
    }

geom1=sample['geometry'] if sample.get('type')=='Feature' else sample['features'][0]['geometry']
rect=m.to_wgs84(Polygon([(771400,56300),(771620,56300),(771620,56420),(771400,56420)]),32647)
rect2=m.to_wgs84(Polygon([(771300,56250),(771660,56250),(771660,56470),(771300,56470)]),32647)
results=[
    run_case('sample_pekanbaru',geom1,20),
    run_case('regular_220x120',mapping(rect),20),
    run_case('large_360x220',mapping(rect2),25),
    run_case('stale_invalid_ui_values',geom1,20,stale=True),
]
print(json.dumps({'version':'2.5.8','all_passed':True,'results':results},indent=2))
