from shapely import affinity

from app import main as m
from app import recovery_solver as rs


def sample_req():
    return m.SitePlanRequest(
        geometry={"type":"Polygon","coordinates":[[[101.43870,0.51055],[101.44010,0.51057],[101.44013,0.50920],[101.43963,0.50874],[101.43868,0.50916],[101.43870,0.51055]]]},
        setback_m=3, lot_width_m=8, lot_depth_m=15,
        main_road_width_m=8, local_road_width_m=6,
        rth_pct=10, psu_pct=5, alternative_count=4,
        land_optimization_enabled=True,
    )


def run_tests():
    print("=== M2.5.14 Structural Corridor Recovery tests ===")
    req = sample_req()
    core = vars(m)
    specs = rs._mutation_specs(core, req, 0, batch_size=64)
    required = {
        "corridor_count", "corridor_spacing_m", "short_branch_count",
        "short_branch_length_ratio", "spine_count", "double_loaded_coverage",
        "road_termination", "perimeter_assisted_access", "block_depth_combo_m",
    }
    assert specs and all(s.get("structural_mode") is True for s in specs)
    assert all(required.issubset(s) for s in specs)
    assert len({s["corridor_count"] for s in specs}) >= 3
    assert len({s["corridor_spacing_m"] for s in specs}) >= 3
    assert {s["spine_count"] for s in specs} >= {0,1,2}
    assert len({s["short_branch_count"] for s in specs}) >= 2
    assert len({s["short_branch_length_ratio"] for s in specs}) >= 3
    assert len({s["double_loaded_coverage"] for s in specs}) >= 3
    assert {s["road_termination"] for s in specs} >= {"boundary","alternating-spine","staggered","dual-spine"}
    assert any(s["perimeter_assisted_access"] for s in specs)
    assert len({tuple(s["block_depth_combo_m"]) for s in specs}) >= 3
    print("[OK] all requested structural search dimensions vary")

    geom = m.ensure_polygon(req.geometry)
    epsg = m.utm_epsg_for_geometry(geom)
    parcel = m.project_geom(geom, 4326, epsg)
    buildable = m._polygonal_only(parcel.buffer(-req.setback_m, join_style=2))
    angle = float(m._dominant_angle_deg(buildable))
    rot = affinity.rotate(buildable, -angle, origin=buildable.centroid, use_radians=False)

    dual = next(s for s in specs if s["spine_count"] == 2 and s["road_termination"] == "dual-spine")
    roads = rs._road_specs_for(core, rot, req, dual)
    vertical = [r for r in roads if r.get("axis") == "v"]
    horizontal = [r for r in roads if r.get("axis") == "h"]
    assert len(vertical) >= 2, "dual-spine must create at least two vertical spines"
    assert horizontal, "structural solver must create corridor/branch roads"
    assert any(r.get("role") in ("short-branch","dual-spine-link","double-loaded") for r in horizontal)
    print("[OK] dual spine + branches generate real road geometry")

    perimeter = next(s for s in specs if s["perimeter_assisted_access"] and s["spine_count"] <= 1)
    p_roads = rs._road_specs_for(core, rot, req, perimeter)
    assert any(r.get("role") == "perimeter-assisted" for r in p_roads), "perimeter-assisted access must create access road"
    print("[OK] perimeter-assisted access generates real geometry")

    # Execute a real candidate through the unchanged STANDARD block packer.
    candidate = rs._evaluate_spec(core, req, dual, "mutation", 9001)
    assert candidate is not None
    assert candidate["parcelization"]["standard_source"] == "geometry_settings"
    assert candidate["parcelization"]["adaptive_source"] == "residual_only"
    assert candidate["stats"]["invalid_standard_lot_count"] == 0
    assert candidate["solver_params"]["spine_count"] == 2
    assert candidate["solver_params"]["corridor_count"] == dual["corridor_count"]
    print(f"[OK] structural candidate executes with {candidate['stats']['standard_lot_count']} exact STANDARD lots")

    summary = rs._candidate_summary(candidate, "mutation", dual["name"])
    assert summary["structural"]["corridor_count"] == dual["corridor_count"]
    assert summary["structural"]["road_termination"] == "dual-spine"
    print("[OK] structural parameters exposed to live solver monitor")

    print("ALL STRUCTURAL CORRIDOR RECOVERY TESTS PASSED")


if __name__ == "__main__":
    run_tests()
