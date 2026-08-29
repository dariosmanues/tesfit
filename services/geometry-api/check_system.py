import json
from fastapi.testclient import TestClient
from app.main import app

def check():
    client = TestClient(app)

    # 1. Health check
    r = client.get('/health')
    assert r.status_code == 200, r.text
    print('1. /health OK:', r.json())

    # 2. Web static check
    r = client.get('/')
    assert r.status_code == 200, r.text
    assert 'Milestone 2.5.12' in r.text
    print('2. / (index.html) OK')

    # 3. Analyze endpoint
    sample_geom = {
        'type': 'Polygon',
        'coordinates': [[
            [101.43870, 0.51055], [101.44010, 0.51057], [101.44013, 0.50920],
            [101.43963, 0.50874], [101.43868, 0.50916], [101.43870, 0.51055]
        ]]
    }
    r = client.post('/geometry/analyze', json={
        'geometry': sample_geom, 'setback_m': 3, 'lot_width_m': 8, 'lot_depth_m': 15, 'angle_step_deg': 10
    })
    assert r.status_code == 200, r.text
    print('3. /geometry/analyze OK')

    # 4. Generate alternatives
    r = client.post('/site-plan/generate', json={
        'geometry': sample_geom, 'setback_m': 3, 'lot_width_m': 8, 'lot_depth_m': 15,
        'main_road_width_m': 8, 'local_road_width_m': 6, 'rth_pct': 10, 'psu_pct': 5,
        'alternative_count': 2, 'land_optimization_enabled': False
    })
    assert r.status_code == 200, r.text
    res = r.json()
    alt = res['alternatives'][0]
    print(f'4. /site-plan/generate OK: {len(res["alternatives"])} alternatives, best lots = {alt["stats"]["lot_count"]}')

    # 5. Optimize land utilization
    r = client.post('/site-plan/optimize-yield', json={
        'parcel': res['parcel'],
        'buildable': alt['buildable'],
        'road_segments': [{'id': x['id'], 'kind': x['kind'], 'width_m': x['width_m'], 'centerline': x['centerline']} for x in alt['road_segments']],
        'lots': alt['lots'],
        'rth': alt['rth'],
        'psu': alt['psu'],
        'target_lot_width_m': 8,
        'target_lot_depth_m': 15,
        'rth_pct': 10,
        'psu_pct': 5,
        'local_road_width_m': 6
    })
    assert r.status_code == 200, r.text
    opt_res = r.json()
    print(f'5. /site-plan/optimize-yield OK: Std={opt_res["stats"]["standard_lot_count"]}, Adapt={opt_res["stats"]["adaptive_lot_count"]}, Eff={opt_res["stats"]["lot_efficiency_pct"]}%, Valid={opt_res["validation"]["valid"]}')

    # 6. Recalculate endpoint
    r = client.post('/site-plan/recalculate', json={
        'parcel': res['parcel'],
        'buildable': alt['buildable'],
        'roads': alt['roads'],
        'rth': alt['rth'],
        'psu': alt['psu'],
        'reserve': None,
        'drainage': alt['drainage'],
        'lots': alt['lots'],
        'previous_stats': alt['stats'],
        'land_optimization_enabled': False
    })
    assert r.status_code == 200, r.text
    print('6. /site-plan/recalculate OK')

    # 7. Projects list endpoint
    r = client.get('/projects')
    assert r.status_code == 200, r.text
    print(f'7. /projects OK ({len(r.json())} projects in database)')

    print('\n=========================================')
    print('ALL SYSTEM INTEGRATION CHECKS PASSED!')
    print('=========================================')

if __name__ == '__main__':
    check()
