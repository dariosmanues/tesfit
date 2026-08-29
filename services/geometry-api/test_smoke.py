import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
geom = {
    "type": "Polygon",
    "coordinates": [[
        [101.43870, 0.51055], [101.44010, 0.51057], [101.44013, 0.50920],
        [101.43963, 0.50874], [101.43868, 0.50916], [101.43870, 0.51055]
    ]],
}

r = client.get('/health')
assert r.status_code == 200

r = client.post('/geometry/analyze', json={
    "geometry": geom, "setback_m": 3, "lot_width_m": 8, "lot_depth_m": 15, "angle_step_deg": 10
})
assert r.status_code == 200, r.text
j = r.json()
assert j['stats']['parcel_area_m2'] > 28000
assert j['stats']['lot_count'] > 0
print(json.dumps(j['stats'], indent=2))
