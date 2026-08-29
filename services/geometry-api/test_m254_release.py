import json, time, math
from pathlib import Path
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree
import app.main as m

ROOT=Path(__file__).resolve().parent
sample=json.loads((ROOT.parent.parent/'sample-inputs'/'sample_site.geojson').read_text())

def road_segments(alt):
    return [{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'centerline':r['centerline']} for r in alt['road_segments']]

def verify_geometry(result, label):
    epsg=result['utm_epsg']
    build=m.project_geom(shape(result['buildable']),4326,epsg)
    roads=m.project_geom(shape(result['roads']),4326,epsg) if result.get('roads') else Polygon()
    rth=m.project_geom(shape(result['rth']),4326,epsg) if result.get('rth') else Polygon()
    psu=m.project_geom(shape(result['psu']),4326,epsg) if result.get('psu') else Polygon()
    lots=[m.project_geom(shape(g),4326,epsg) for g in result.get('lots',[])]
    # all lots inside buildable (tiny numerical tolerance)
    outside=sum(g.difference(build.buffer(0.03)).area for g in lots)
    assert outside < 0.5, (label,'outside',outside)
    # lots must not overlap each other materially
    tree=STRtree(lots)
    overlaps=0.0
    for i,g in enumerate(lots):
        for j in tree.query(g):
            j=int(j)
            if j<=i: continue
            overlaps += g.intersection(lots[j]).area
    assert overlaps < 0.5, (label,'overlap',overlaps)
    # lots cannot materially overlap roads/facilities
    blocked=unary_union([x for x in (roads,rth,psu) if not x.is_empty])
    blocked_overlap=sum(g.intersection(blocked).area for g in lots)
    assert blocked_overlap < 1.0, (label,'blocked_overlap',blocked_overlap)
    assert result['stats']['reserve_area_m2']==0, (label,'reserve',result['stats']['reserve_area_m2'])
    assert result['stats']['residual_pct_total_land'] <= 3.01, (label,result['stats']['residual_pct_total_land'])
    assert result['stats']['rth_pct'] >= 9.85 and result['stats']['psu_pct'] >= 4.925
    return {'outside_m2':round(outside,4),'lot_overlap_m2':round(overlaps,4),'blocked_overlap_m2':round(blocked_overlap,4)}

def run_case(name,geom,seconds=10):
    site=m.generate_site_alternatives(m.SitePlanRequest(geometry=geom,setback_m=3,lot_width_m=8,lot_depth_m=15,main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,alternative_count=4,land_optimization_enabled=False))
    alt=site['alternatives'][0]
    # OFF must be original scenario and must not fabricate reserve/cap.
    assert alt['stats']['land_optimization_enabled'] is False
    assert alt.get('reserve') is None
    baseline_sig=(alt['stats']['lot_count'],round(alt['stats']['unused_area_m2'],2),round(alt['stats']['road_area_m2'],2))
    req=m.YieldOptimizeRequest(parcel=site['parcel'],buildable=alt['buildable'],road_segments=road_segments(alt),lots=alt['lots'],rth=alt['rth'],psu=alt['psu'],
        target_lot_width_m=8,min_lot_width_m=7,max_lot_width_m=10,target_lot_depth_m=15,min_lot_depth_m=13,max_lot_depth_m=18,
        rth_pct=10,psu_pct=5,local_road_width_m=6,road_shift_m=4,allow_road_shift=True,allow_rth_psu_relocation=True,
        allow_selective_extension=True,max_extensions=4,max_residual_pct_total=3,strict_residual_cap=True,allow_residual_rth_absorption=True,max_optimize_seconds=seconds)
    t=time.perf_counter(); result=m.optimize_land_utilization(req); elapsed=time.perf_counter()-t
    geo=verify_geometry(result,name)
    assert elapsed <= seconds+2.5,(name,'elapsed',elapsed)
    return {'case':name,'elapsed_s':round(elapsed,3),'baseline_lots':baseline_sig[0],'baseline_residual_pct':alt['stats']['residual_pct_total_land'],
            'optimized_lots':result['stats']['lot_count'],'optimized_residual_pct':result['stats']['residual_pct_total_land'],
            'rth_pct':result['stats']['rth_pct'],'extra_rth_m2':round(result['optimization']['residual_cap'].get('absorbed_to_rth_m2',0),2),
            'adaptive_lots':result['optimization'].get('adaptive_residual_lots',0),'reserve_m2':result['stats']['reserve_area_m2'],**geo}

# Case 1: project sample
geom1=sample['geometry'] if sample.get('type')=='Feature' else sample['features'][0]['geometry']
results=[run_case('sample_pekanbaru',geom1,10)]

# Case 2: regular 220x120m parcel near Pekanbaru, generated from UTM 47N
rect=m.to_wgs84(Polygon([(771400,56300),(771620,56300),(771620,56420),(771400,56420)]),32647)
results.append(run_case('regular_220x120',mapping(rect),10))

# Case 3: larger 360x220m parcel to exercise performance on a denser layout
rect2=m.to_wgs84(Polygon([(771300,56250),(771660,56250),(771660,56470),(771300,56470)]),32647)
results.append(run_case('large_360x220',mapping(rect2),12))

print(json.dumps({'version':'2.5.4','all_passed':True,'results':results},indent=2))
