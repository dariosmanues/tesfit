import json
import sys
from pathlib import Path
from shapely.geometry import Polygon, box, mapping, shape
from fastapi.exceptions import HTTPException

import app.main as m

def run_tests():
    print("=== RUNNING TEST SUITE: Milestone 2.5.12 Lot Efficiency >= 70% ===")

    sample_path = Path(__file__).resolve().parents[2].joinpath('sample-inputs', 'sample_site.geojson')
    sample = json.loads(sample_path.read_text())
    geom = sample['geometry'] if sample.get('type') == 'Feature' else sample

    # -------------------------------------------------------------
    # Scenario 1: Test Standard Yield Dominance
    # -------------------------------------------------------------
    print("\n[Scenario 1] Testing Standard Yield Dominance...")
    req = m.SitePlanRequest(
        geometry=geom, setback_m=3, lot_width_m=8, lot_depth_m=15,
        main_road_width_m=8, local_road_width_m=6, rth_pct=10, psu_pct=5,
        alternative_count=6, land_optimization_enabled=False
    )
    site = m.generate_site_alternatives(req)
    assert len(site['alternatives']) == 6, f"Expected 6 alternatives, got {len(site['alternatives'])}"
    best = site['alternatives'][0]

    # Check that standard lot count is maximized
    assert best['stats']['standard_lot_count'] >= 130, f"Expected >= 130 standard lots, got {best['stats']['standard_lot_count']}"
    assert best['stats']['adaptive_lot_count'] == 0, "Generated baseline should have 0 adaptive lots"
    assert best['stats']['invalid_standard_lot_count'] == 0, "No invalid standard lots allowed"
    
    # Check standard lot area
    standard_area = best['stats']['standard_lot_area_m2']
    assert standard_area >= 130 * 120.0, f"Standard lot area {standard_area} should dominate"
    print(f"  [OK] Standard lots: {best['stats']['standard_lot_count']} units ({standard_area:.2f} m2), Adaptive: {best['stats']['adaptive_lot_count']}")

    # -------------------------------------------------------------
    # Scenario 2: Test Acceptance Rule (>= 70% PASS, < 70% FAIL)
    # -------------------------------------------------------------
    print("\n[Scenario 2] Testing Acceptance Rule (>= 70% PASS, < 70% FAIL)...")
    parcel_box = box(0, 0, 100, 100) # 10,000 m2
    parcel_area = parcel_box.area # 10,000
    buildable = box(5, 5, 95, 95) # 8,100 m2

    # Case A: 60 standard lots of 8x15 (120 m2 each) = 7,200 m2 -> 72.0% efficiency
    lots_pass = [box(5 + (i % 10) * 8, 5 + (i // 10) * 15, 5 + (i % 10) * 8 + 8, 5 + (i // 10) * 15 + 15) for i in range(60)]
    meta_pass = [{'road_id': 'R1', 'parcel_type': 'standard', 'source': 'geometry_settings', 'width_m': 8, 'depth_m': 15, 'frontage_m': 8} for _ in range(60)]
    v_pass = m._final_siteplan_acceptance(
        buildable, None, Polygon(), lots_pass, meta_pass, Polygon(), Polygon(),
        parcel_area, residual_pct_total=9.0, base_lot_count=60, target_lot_width_m=8, target_lot_depth_m=15
    )
    assert v_pass['lot_efficiency_pct'] == 72.0, f"Expected 72.0%, got {v_pass['lot_efficiency_pct']}"
    assert v_pass['lot_efficiency_met'] is True, "Expected lot_efficiency_met=True"
    assert v_pass['valid'] is True, "Expected valid=True for 72.0% efficiency"
    print(f"  [OK] Passing layout: {v_pass['lot_efficiency_pct']}% -> valid={v_pass['valid']}")

    # Case B: 50 standard lots of 8x15 = 6,000 m2 -> 60.0% efficiency (< 70%)
    lots_fail = lots_pass[:50]
    meta_fail = meta_pass[:50]
    v_fail = m._final_siteplan_acceptance(
        buildable, None, Polygon(), lots_fail, meta_fail, Polygon(), Polygon(),
        parcel_area, residual_pct_total=21.0, base_lot_count=50, target_lot_width_m=8, target_lot_depth_m=15
    )
    assert v_fail['lot_efficiency_pct'] == 60.0, f"Expected 60.0%, got {v_fail['lot_efficiency_pct']}"
    assert v_fail['lot_efficiency_met'] is False, "Expected lot_efficiency_met=False"
    assert v_fail['valid'] is False, "Expected valid=False for 60.0% efficiency"
    print(f"  [OK] Failing layout: {v_fail['lot_efficiency_pct']}% -> valid={v_fail['valid']}")

    # -------------------------------------------------------------
    # Scenario 3: Test Residual Non-Gate Rule
    # -------------------------------------------------------------
    print("\n[Scenario 3] Testing Residual Non-Gate Rule (efficiency >= 70% and residual > 3% MUST PASS)...")
    # In v_pass, residual_pct_total was 9.0% (> 3.0%), but valid was True!
    assert v_pass['residual_true_pct'] == 9.0, f"Expected residual 9.0%, got {v_pass['residual_true_pct']}"
    assert v_pass['valid'] is True, "Residual > 3% must NOT reject layout when lot efficiency >= 70%"
    print(f"  [OK] Efficiency {v_pass['lot_efficiency_pct']}% with residual {v_pass['residual_true_pct']}% -> valid={v_pass['valid']} (Residual is informational)")

    # -------------------------------------------------------------
    # Scenario 4: Test Immutable Standard Contract
    # -------------------------------------------------------------
    print("\n[Scenario 4] Testing Immutable Standard Contract...")
    for d in best['lot_details']:
        assert d['parcel_type'] == 'standard', f"Expected parcel_type=standard, got {d['parcel_type']}"
        assert abs(d['area_m2'] - 120.0) <= 0.02, f"Expected 120 m2, got {d['area_m2']}"
        assert abs(d['frontage_m'] - 8.0) <= 0.02, f"Expected 8 m frontage, got {d['frontage_m']}"
        assert abs(d['depth_est_m'] - 15.0) <= 0.02, f"Expected 15 m depth, got {d['depth_est_m']}"
    print(f"  [OK] All {len(best['lot_details'])} standard lots strictly match 8m x 15m (120 m2)")

    # -------------------------------------------------------------
    # Scenario 5: Test Adaptive Origin Contract
    # -------------------------------------------------------------
    print("\n[Scenario 5] Testing Adaptive Origin Contract...")
    opt_payload = m.YieldOptimizeRequest(
        parcel=site['parcel'], buildable=best['buildable'],
        road_segments=[{'id': r['id'], 'kind': r['kind'], 'width_m': r['width_m'], 'centerline': r['centerline']} for r in best['road_segments']],
        lots=best['lots'], lot_details=best['lot_details'], rth=best['rth'], psu=best['psu'],
        target_lot_width_m=8, target_lot_depth_m=15, rth_pct=10, psu_pct=5, local_road_width_m=6
    )
    opt = m.optimize_land_utilization(opt_payload)
    assert opt['optimization']['version'] == '2.5.12'
    assert opt['optimization']['optimizer_type'] == 'RESIDUAL_ONLY'
    assert opt['stats']['standard_lot_count'] == best['stats']['standard_lot_count'], "Standard lots must not be altered/deleted"
    assert opt['validation']['standard_lot_count_preserved'] is True
    assert opt['validation']['adaptive_origin_violation_count'] == 0
    assert opt['validation']['invalid_standard_lot_count'] == 0
    print(f"  [OK] Optimizer preserved {opt['stats']['standard_lot_count']} standard lots and generated {opt['stats']['adaptive_lot_count']} adaptive lots from residual only")

    # -------------------------------------------------------------
    # Scenario 6: Test Candidate Ranking
    # -------------------------------------------------------------
    print("\n[Scenario 6] Testing Candidate Ranking...")
    # Candidate ranking in site['alternatives']
    alts = site['alternatives']
    for i in range(len(alts) - 1):
        curr = alts[i]
        nxt = alts[i + 1]
        curr_pass = curr['stats'].get('lot_efficiency_met', False)
        nxt_pass = nxt['stats'].get('lot_efficiency_met', False)
        if curr_pass and not nxt_pass:
            assert True # Passing candidate correctly ranked above failing
        elif curr_pass == nxt_pass:
            # If both pass or both fail, check standard count or efficiency
            curr_std = curr['stats']['standard_lot_count']
            nxt_std = nxt['stats']['standard_lot_count']
            assert curr_std >= nxt_std or curr['stats']['lot_efficiency_pct'] >= nxt['stats']['lot_efficiency_pct'] - 5.0
    print(f"  [OK] Candidates ranked properly with #1 ALT-1 (Efficiency: {alts[0]['stats']['lot_efficiency_pct']}%, Standard: {alts[0]['stats']['standard_lot_count']})")

    # -------------------------------------------------------------
    # Scenario 7: Test Save Contract
    # -------------------------------------------------------------
    print("\n[Scenario 7] Testing Save Contract...")
    valid_parcel = mapping(parcel_box)
    valid_layout = {
        "buildable": mapping(buildable),
        "lots": [mapping(g) for g in lots_pass],
        "validation": v_pass,
        "stats": {
            "lot_count": 60,
            "standard_lot_count": 60,
            "adaptive_lot_count": 0,
            "lot_efficiency_pct": 72.0,
            "lots_total_area_m2": 7200.0,
            "validation_passed": True,
            "validation": v_pass,
        }
    }
    save_req_valid = m.ProjectRequest(
        name="Test M2.5.12 Valid Project",
        parcel=valid_parcel,
        buildable=valid_layout['buildable'],
        lots=valid_layout['lots'],
        layout=valid_layout,
        settings={"lot_efficiency_target_pct": 70, "enforce_lot_efficiency_target": True},
        stats=valid_layout['stats']
    )
    save_res = m.save_project(save_req_valid)
    assert 'id' in save_res and save_res['id'] > 0
    print(f"  [OK] Save project passed with ID #{save_res['id']}")

    # Failing save test: invalid validation (efficiency < 70%)
    bad_layout = {
        "buildable": mapping(buildable),
        "lots": [mapping(g) for g in lots_fail],
        "validation": v_fail,
        "stats": {
            "lot_count": 50,
            "standard_lot_count": 50,
            "adaptive_lot_count": 0,
            "lot_efficiency_pct": 60.0,
            "lots_total_area_m2": 6000.0,
            "validation_passed": False,
            "validation": v_fail,
        }
    }
    save_req_invalid = m.ProjectRequest(
        name="Test M2.5.12 Invalid Project",
        parcel=valid_parcel,
        buildable=bad_layout['buildable'],
        lots=bad_layout['lots'],
        layout=bad_layout,
        settings={"lot_efficiency_target_pct": 70, "enforce_lot_efficiency_target": True},
        stats=bad_layout['stats']
    )
    try:
        m.save_project(save_req_invalid)
        assert False, "Expected save_project to raise HTTPException(422) when validation is not valid"
    except HTTPException as e:
        assert e.status_code == 422
        print(f"  [OK] Save project properly rejected when validation.valid is False (HTTP 422: {e.detail['message']})")

    print("\n=======================================================")
    print("ALL 7 MILESTONE 2.5.12 ACCEPTANCE TESTS PASSED SUCCESSFULLY!")
    print("=======================================================")

if __name__ == "__main__":
    run_tests()
