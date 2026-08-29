from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import Polygon, LineString, mapping
from shapely.ops import transform
from app.main import app
client=TestClient(app)
inv=Transformer.from_crs(32647,4326,always_xy=True)
def wgs(g): return mapping(transform(inv.transform,g))
x0,y0=650000,540000
parcel=Polygon([(x0,y0),(x0+270,y0),(x0+270,y0+60),(x0,y0+60)])
buildable=parcel.buffer(-1)
road=LineString([(x0+2,y0+30),(x0+268,y0+30)])
lots=[]
for k in range(29):
    a=x0+5+k*9
    lots.append(Polygon([(a,y0+33),(a+8,y0+33),(a+8,y0+48),(a,y0+48)]))
rth=Polygon([(x0+2,y0+32.8),(x0+268,y0+32.8),(x0+268,y0+49),(x0+2,y0+49)])
req={'parcel':wgs(parcel),'buildable':wgs(buildable),'road_segments':[{'id':'R1','kind':'local','width_m':6,'centerline':wgs(road)}],'lots':[wgs(g) for g in lots],'rth':wgs(rth),'psu':None,'lot_width_m':8,'lot_depth_m':15,'edited_special_types':['rth'],'frontage_tolerance_m':1.5,'preserve_count':True}
m=client.post('/editor/parametric-model',json=req); assert m.status_code==200,m.text
req['editor_model']=m.json()
r=client.post('/editor/parametric-reflow',json=req); assert r.status_code==200,r.text
j=r.json()
assert len(j['lots'])==29,j
assert len(j.get('relocated_lot_indices',[]))==29,j
assert len(j.get('dropped_lot_indices',[]))==0,j
assert j['validation']['valid'],j['validation']
print({'before':29,'after':len(j['lots']),'relocated':len(j['relocated_lot_indices']),'dropped':len(j['dropped_lot_indices']),'valid':j['validation']['valid']})
