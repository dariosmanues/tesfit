import json, time
from pathlib import Path
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid
import app.main as m

ROOT=Path(__file__).resolve().parent
sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())

def road_segments(alt):
    return [{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in alt['road_segments']]

def check_case(name, geometry, seconds=12):
    site=m.generate_site_alternatives(m.SitePlanRequest(geometry=geometry,setback_m=3,lot_width_m=8,lot_depth_m=15,main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,alternative_count=4,land_optimization_enabled=False))
    alt=site['alternatives'][0]
    assert alt['stats']['optimized'] is False
    baseline=(alt['stats']['lot_count'], round(alt['stats']['unused_area_m2'],2), round(alt['stats']['residual_pct_total_land'],2))
    req=m.YieldOptimizeRequest(parcel=site['parcel'],buildable=alt['buildable'],road_segments=road_segments(alt),lots=alt['lots'],rth=alt['rth'],psu=alt['psu'],
        target_lot_width_m=8,min_lot_width_m=7,max_lot_width_m=10,target_lot_depth_m=15,min_lot_depth_m=13,max_lot_depth_m=18,
        rth_pct=10,psu_pct=5,local_road_width_m=6,road_shift_m=4,allow_road_shift=True,allow_rth_psu_relocation=True,
        allow_selective_extension=True,max_extensions=4,max_residual_pct_total=3,strict_residual_cap=True,allow_residual_rth_absorption=False,max_optimize_seconds=seconds)
    t=time.perf_counter(); result=m.optimize_land_utilization(req); elapsed=time.perf_counter()-t
    assert elapsed <= seconds+3.0, (name, elapsed)
    assert result['stats']['residual_pct_total_land'] <= 3.01, (name,result['stats']['residual_pct_total_land'])
    assert result['stats']['reserve_area_m2'] == 0
    assert result['optimization']['residual_cap']['absorbed_to_rth_m2'] == 0
    details=result.get('lot_details',[])
    assert len(details)==len(result['lots'])
    residual=[d for d in details if d['parcel_type']=='residual']
    assert residual, (name,'no residual parcels')
    assert all(d['area_m2']>0 and d['frontage_m']>0 and d['depth_est_m']>0 for d in residual)
    # Round-trip geometry through WGS84 and repair tiny projection ring artifacts before QA.
    epsg=result['utm_epsg']
    build=m._polygonal_only(make_valid(m.project_geom(shape(result['buildable']),4326,epsg)))
    roads=m._polygonal_only(make_valid(m.project_geom(shape(result['roads']),4326,epsg))) if result.get('roads') else Polygon()
    rth=m._polygonal_only(make_valid(m.project_geom(shape(result['rth']),4326,epsg))) if result.get('rth') else Polygon()
    psu=m._polygonal_only(make_valid(m.project_geom(shape(result['psu']),4326,epsg))) if result.get('psu') else Polygon()
    lots=[m._polygonal_only(make_valid(m.project_geom(shape(g),4326,epsg))) for g in result['lots']]
    outside=sum(g.difference(build.buffer(0.03)).area for g in lots)
    assert outside < 1.0,(name,'outside',outside)
    tree=STRtree(lots); overlap=0.0
    for i,g in enumerate(lots):
        for j in tree.query(g):
            j=int(j)
            if j<=i: continue
            overlap += g.intersection(lots[j]).area
    assert overlap < 1.0,(name,'overlap',overlap)
    blocked=unary_union([x for x in (roads,rth,psu) if not x.is_empty])
    blocked_overlap=sum(g.intersection(blocked).area for g in lots)
    assert blocked_overlap < 2.0,(name,'blocked',blocked_overlap)
    return {
      'case':name,'elapsed_s':round(elapsed,3),'baseline_lots':baseline[0],'baseline_residual_pct':baseline[2],
      'optimized_lots':result['stats']['lot_count'],'standard_lots':result['stats']['standard_lot_count'],
      'residual_lots':result['stats']['residual_lot_count'],'residual_lot_area_m2':result['stats']['residual_lot_area_m2'],
      'true_residual_pct':result['stats']['residual_pct_total_land'],'true_residual_m2':result['stats']['unused_area_m2'],
      'overlap_m2':round(overlap,4),'blocked_overlap_m2':round(blocked_overlap,4),'outside_m2':round(outside,4),
      'first_residual_detail':residual[0]
    }

geom1=sample['geometry'] if sample.get('type')=='Feature' else sample['features'][0]['geometry']
rect=m.to_wgs84(Polygon([(771400,56300),(771620,56300),(771620,56420),(771400,56420)]),32647)
rect2=m.to_wgs84(Polygon([(771300,56250),(771660,56250),(771660,56470),(771300,56470)]),32647)
results=[check_case('sample_pekanbaru',geom1,12),check_case('regular_220x120',mapping(rect),12),check_case('large_360x220',mapping(rect2),15)]
print(json.dumps({'version':'2.5.5','all_passed':True,'results':results},indent=2))
