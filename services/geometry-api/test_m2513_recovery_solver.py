import json
from pathlib import Path

from app import main as m
from app import recovery_solver as rs


def fake_alt(eff, standard, adaptive=0, road=15.0, residual=0.5, valid=True, name="x"):
    return {
        "id": name,
        "name": name,
        "stats": {
            "lot_efficiency_pct": eff,
            "standard_lot_count": standard,
            "adaptive_lot_count": adaptive,
            "road_area_m2": road * 100.0,
            "road_pct": road,
            "residual_true_area_m2": residual * 100.0,
            "residual_pct_total_land": residual,
            "average_block_regularity": 0.9,
            "road_connectivity_score": 0.8,
            "validation_passed": valid,
        },
        "validation": {"valid": valid, "lot_efficiency_pct": eff},
    }


def sample_req(**overrides):
    payload = {
        "geometry": {"type":"Polygon","coordinates":[[[101.43870,0.51055],[101.44010,0.51057],[101.44013,0.50920],[101.43963,0.50874],[101.43868,0.50916],[101.43870,0.51055]]]},
        "setback_m": 3,
        "lot_width_m": 8,
        "lot_depth_m": 15,
        "main_road_width_m": 8,
        "local_road_width_m": 6,
        "rth_pct": 10,
        "psu_pct": 5,
        "alternative_count": 4,
        "land_optimization_enabled": True,
    }
    payload.update(overrides)
    return m.SitePlanRequest(**payload)


def run_tests():
    print("=== M2.5.13 Recovery Solver tests ===")

    # 1. STRICT 70% gate: 69.99 is never a valid alternative.
    pool = [fake_alt(69.99, 200, name="fail"), fake_alt(70.0, 180, name="pass")]
    valid = rs.strict_valid_alternatives(pool)
    assert [x["id"] for x in valid] == ["pass"]
    print("[OK] strict >=70% gate")

    # 2. Once PASS, STANDARD count ranks before efficiency.
    pool = [fake_alt(75.0, 179, name="high-eff"), fake_alt(70.5, 180, name="more-standard")]
    valid = rs.strict_valid_alternatives(pool)
    assert valid[0]["id"] == "more-standard"
    assert valid[0]["recommended"] is True
    print("[OK] PASS-pool ranking: Standard -> efficiency")

    # 3. Recovery stages are real strategy definitions, not UI-only labels.
    req = sample_req()
    core = vars(m)
    road = rs._stage_specs(core, req, "road_topology")
    block = rs._stage_specs(core, req, "block_spacing")
    orientation = rs._stage_specs(core, req, "orientation")
    perimeter = rs._stage_specs(core, req, "perimeter")
    facility = rs._stage_specs(core, req, "facility")
    assert any(x["topology"] == "short-branches" for x in road)
    assert any(abs(x["shift_m"]) > 0 for x in block)
    assert len(orientation) >= 12
    assert any("Perimeter" in x["name"] for x in perimeter)
    assert any(x["facility_mode"] == "low-yield" for x in facility)
    assert any(x["id"] == "mutation" for x in rs.stage_definitions())
    mutation_a = rs._mutation_specs(core, req, 0)
    mutation_b = rs._mutation_specs(core, req, 1)
    assert len(mutation_a) == 12 and len(mutation_b) == 12
    assert {(x["angle"], x["shift_m"], x["spine_ratio"]) for x in mutation_a}.isdisjoint({(x["angle"], x["shift_m"], x["spine_ratio"]) for x in mutation_b})
    print("[OK] staged recovery + deterministic continuing mutation definitions")

    # 4. At least one real recovery candidate executes through road->block->STANDARD.
    cand = rs._evaluate_spec(core, req, road[0], "road_topology", 1)
    assert cand is not None
    assert cand["stats"]["invalid_standard_lot_count"] == 0
    assert cand["parcelization"]["standard_source"] == "geometry_settings"
    assert cand["parcelization"]["adaptive_source"] == "residual_only"
    print(f"[OK] real candidate executed: {cand['stats']['standard_lot_count']} Standard, {cand['stats']['lot_efficiency_pct']}%")

    # 5. Feasibility is only called mathematically impossible when an optimistic upper bound is <70%.
    normal = rs.feasibility_diagnosis(core, req, [{"efficiency_pct":67,"road_pct":16,"residual_pct":2}])
    assert normal["mathematically_infeasible"] is False
    impossible_req = sample_req(setback_m=0, rth_pct=50, psu_pct=30)
    impossible = rs.feasibility_diagnosis(core, impossible_req, [])
    assert impossible["mathematically_infeasible"] is True
    assert impossible["theoretical_upper_bound_pct"] < 70
    print("[OK] conservative mathematical feasibility classification")

    # 6. API routes are registered on main app.
    paths = {r.path for r in m.app.routes}
    assert "/site-plan/solver/start" in paths
    assert "/site-plan/solver/status/{job_id}" in paths
    assert "/site-plan/solver/cancel/{job_id}" in paths
    print("[OK] solver start/status/cancel routes registered")

    # 7. Frontend contains monitor and polls real backend status.
    web = Path(__file__).resolve().parent / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "recovery-monitor.js").read_text(encoding="utf-8")
    css = (web / "recovery-monitor.css").read_text(encoding="utf-8")
    appjs = (web / "app.js").read_text(encoding="utf-8")
    assert 'id="solverMonitor"' in html
    assert '/static/recovery-monitor.js?v=2.5.13' in html
    assert '/site-plan/solver/start' in js
    assert '/site-plan/solver/status/' in js
    assert '/site-plan/solver/cancel/' in js
    assert 'solverCancelBtn' in html
    assert 'candidate <70% hanya tampil pada Search History' not in js  # exact wording intentionally not hard-coded in logic
    assert '.solver-stage' in css
    assert 'const DEVOS_FRONTEND_VERSION = "2.5.13";' in appjs
    print("[OK] live monitor frontend wiring")

    # 8. User-facing alternatives are strict valid only.
    assert all(float(x["stats"]["lot_efficiency_pct"]) >= 70 for x in rs.strict_valid_alternatives([
        fake_alt(61.1, 180, name="a"), fake_alt(67.1, 179, name="b"), fake_alt(70.2, 175, name="c")
    ]))
    print("[OK] rejected candidates never enter Alternatif Layout")

    print("ALL M2.5.13 RECOVERY SOLVER TESTS PASSED")


if __name__ == "__main__":
    run_tests()
