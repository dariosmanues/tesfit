import math
from shapely import affinity
from shapely.geometry import Polygon, mapping
import app.main as m

def wgs(poly):
    return mapping(m.to_wgs84(poly,32647))

base=Polygon([(771400,56300),(771620,56300),(771620,56420),(771400,56420)])
shapes={
    'rectangular':base,
    'trapezoid':Polygon([(771400,56300),(771630,56310),(771600,56430),(771420,56420)]),
    'rotated_rectangle':affinity.rotate(base,17,origin='centroid'),
    'l_shape':Polygon([(771400,56300),(771650,56300),(771650,56380),(771540,56380),(771540,56460),(771400,56460)]),
    'irregular_diagonal':Polygon([(771400,56300),(771610,56290),(771670,56370),(771600,56460),(771430,56430),(771380,56360)]),
    'narrow_long':Polygon([(771350,56300),(771700,56300),(771700,56385),(771350,56385)]),
}
results=[]
for name,poly in shapes.items():
    site=m.generate_site_alternatives(m.SitePlanRequest(
        geometry=wgs(poly),setback_m=3,lot_width_m=8,lot_depth_m=15,
        main_road_width_m=8,local_road_width_m=6,rth_pct=10,psu_pct=5,
        alternative_count=4,land_optimization_enabled=False))
    assert site['alternatives'],name
    a=site['alternatives'][0]
    assert a['stats']['invalid_standard_lot_count']==0,(name,a['stats'])
    assert a['stats']['standard_lot_count']>0,(name,a['stats'])
    assert 0<=a['stats']['average_block_regularity']<=1,(name,a['stats'])
    assert 0<=a['stats']['road_connectivity_score']<=1,(name,a['stats'])
    for d in a['lot_details']:
        assert d['parcel_type']=='standard',(name,d)
        assert abs(d['area_m2']-120.0)<=0.02,(name,d)
        assert abs(d['frontage_m']-8.0)<=0.02,(name,d)
        assert abs(d['depth_est_m']-15.0)<=0.02,(name,d)
    results.append((name,a['stats']['standard_lot_count'],a['stats']['average_block_regularity'],a['stats']['residual_pct_total_land']))
print({'version':'2.5.11','cases':results})
