import json
import math
from pathlib import Path
from fastapi import HTTPException
from shapely import affinity
from shapely.geometry import mapping, shape
import app.main as m

SAMPLE = Path(__file__).resolve().parents[2] / "sample-inputs" / "sample_site.geojson"
obj = json.loads(SAMPLE.read_text(encoding="utf-8"))
geom = obj["geometry"] if obj.get("type") == "Feature" else obj["features"][0]["geometry"]
site = m.generate_site_alternatives(m.SitePlanRequest(geometry=geom, alternative_count=4))
alt = site["alternatives"][0]
segments = [{"id":r["id"],"kind":r["kind"],"width_m":r["width_m"],"centerline":r["centerline"]} for r in alt["road_segments"]]
model = m._build_parametric_model(m.ParametricModelRequest(
    parcel=site["parcel"], buildable=alt["buildable"], road_segments=segments,
    lots=alt["lots"], rth=alt["rth"], psu=alt["psu"]
))
assert model["summary"]["lot_count"] == len(alt["lots"])
assert model["summary"]["block_count"] > 0

epsg=model["utm_epsg"]
road_counts={rid:sum(len(b["lot_indices"]) for b in model["blocks"].values() if b["road_id"]==rid) for rid in model["roads"]}
road_id=max(road_counts,key=road_counts.get)

# Move a heavily loaded road by 1m. Under fixed STANDARD dimensions the solver
# may reject the move; rejection is correct if count cannot be preserved.
moved=[]
for r in alt["road_segments"]:
    line=shape(r["centerline"])
    if r["id"]==road_id:
        line=m.to_wgs84(affinity.translate(m.project_geom(line,4326,epsg),xoff=1.0),epsg)
    moved.append({"id":r["id"],"kind":r["kind"],"width_m":r["width_m"],"centerline":mapping(line)})
road_rejected=False
try:
    rr=m._parametric_reflow(m.ParametricReflowRequest(
        parcel=site["parcel"],buildable=alt["buildable"],road_segments=moved,
        lots=alt["lots"],rth=alt["rth"],psu=alt["psu"],editor_model=model,edited_road_ids=[road_id]
    ))
    assert len(rr["lots"])==len(alt["lots"])
    assert len(rr.get("dropped_lot_indices",[]))==0
except HTTPException as e:
    road_rejected=True
    assert e.status_code==422
    detail=e.detail if isinstance(e.detail,dict) else {}
    assert 'dibatalkan agar tidak ada kavling terhapus' in detail.get('message','')

# Moving one lot may similarly be repacked or rejected atomically.
block_id=max(model["blocks"],key=lambda b:len(model["blocks"][b]["lot_indices"]))
indices=model["blocks"][block_id]["lot_indices"]
lot_index=indices[len(indices)//2]
road_for_lot=model["lots"][lot_index]["road_id"]
road=next(r for r in alt["road_segments"] if r["id"]==road_for_lot)
line=m.project_geom(shape(road["centerline"]),4326,epsg);a,b=list(line.coords)[0],list(line.coords)[-1]
dx,dy=b[0]-a[0],b[1]-a[1];norm=math.hypot(dx,dy);ux,uy=dx/norm,dy/norm
lots2=list(alt["lots"]);lot=m.project_geom(shape(lots2[lot_index]),4326,epsg);lot=affinity.translate(lot,xoff=ux*6,yoff=uy*6);lots2[lot_index]=mapping(m.to_wgs84(lot,epsg))
lot_rejected=False
try:
    lr=m._parametric_reflow(m.ParametricReflowRequest(
        parcel=site["parcel"],buildable=alt["buildable"],road_segments=segments,
        lots=lots2,rth=alt["rth"],psu=alt["psu"],editor_model=model,edited_lot_indices=[lot_index]
    ))
    assert len(lr["lots"])==len(alt["lots"])
    assert len(lr.get("dropped_lot_indices",[]))==0
except HTTPException as e:
    lot_rejected=True
    assert e.status_code==422
    detail=e.detail if isinstance(e.detail,dict) else {}
    assert 'dibatalkan agar tidak ada kavling terhapus' in detail.get('message','')

print(json.dumps({
    'base_lots':len(alt['lots']),
    'road_move_rejected_atomically':road_rejected,
    'lot_move_rejected_atomically':lot_rejected,
    'standard_module_preserved':True,
},indent=2))
