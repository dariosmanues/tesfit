from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "services/geometry-api/app/recovery_solver.py"
JS = ROOT / "services/geometry-api/web/recovery-monitor.js"
TEST = ROOT / "services/geometry-api/test_m2513_structural_corridor.py"
STATUS = ROOT / "MILESTONE2_5_13_STATUS.md"

rec = REC.read_text(encoding="utf-8")
rec = rec.replace('(\"mutation\", \"Topology Mutation Loop\")', '(\"mutation\", \"Structural Corridor Mutation Loop\")', 1)

# Expose structural parameters to the live UI/search history.
needle = '        "pattern": alt.get("pattern"),\n        "timestamp": time.time(),\n'
replacement = '        "pattern": alt.get("pattern"),\n        "structural": copy.deepcopy(alt.get("solver_params") or {}),\n        "timestamp": time.time(),\n'
if needle not in rec:
    raise RuntimeError("candidate summary anchor not found")
rec = rec.replace(needle, replacement, 1)

road_impl = r'''def _structural_road_specs(rot, req: Any, specdef: dict[str, Any]):
    """Generate structural road alternatives without changing STANDARD dimensions.

    Search variables intentionally operate on the road/block system only:
    corridor count/spacing, short-branch count/length, single/dual spine,
    double-loaded coverage, road termination, perimeter-assisted access and
    block-depth combinations. Geometry Settings remain the only source of
    STANDARD lot width/depth.
    """
    minx, miny, maxx, maxy = rot.bounds
    width = max(0.0, maxx - minx)
    height = max(0.0, maxy - miny)
    lot_d = float(req.lot_depth_m)
    main_w = float(req.main_road_width_m)
    local_w = float(req.local_road_width_m)

    requested_count = max(1, min(16, int(specdef.get("corridor_count", 1) or 1)))
    nominal_spacing = max(lot_d, float(specdef.get("corridor_spacing_m", 2.0 * lot_d) or 2.0 * lot_d))
    combo_raw = specdef.get("block_depth_combo_m") or [nominal_spacing]
    combo = [max(lot_d, float(x)) for x in combo_raw if float(x) > 0.0] or [nominal_spacing]
    phase = float(specdef.get("shift_m", 0.0) or 0.0)

    # Reduce corridor count only when the requested structural system cannot fit.
    # We reserve one lot-depth at each outer edge so edge frontage can still pack.
    corridor_count = requested_count
    widths = []
    gaps = []
    while corridor_count >= 1:
        main_i = corridor_count // 2 if bool(specdef.get("main_corridor", True)) else -1
        widths = [main_w if i == main_i else local_w for i in range(corridor_count)]
        gaps = [combo[i % len(combo)] for i in range(max(0, corridor_count - 1))]
        required = 2.0 * lot_d + sum(widths) + sum(gaps)
        if required <= height + 1e-6 or corridor_count == 1:
            break
        corridor_count -= 1

    required = 2.0 * lot_d + sum(widths) + sum(gaps)
    slack = max(0.0, height - required)
    # Phase is bounded to the available edge slack; it shifts the entire corridor family.
    edge_bottom = slack / 2.0 + max(-slack / 2.0, min(slack / 2.0, phase))
    cursor = miny + edge_bottom + lot_d
    ys = []
    for i, rw in enumerate(widths):
        center = cursor + rw / 2.0
        ys.append((center, rw, "main" if rw == main_w and i == corridor_count // 2 and bool(specdef.get("main_corridor", True)) else "local"))
        cursor = center + rw / 2.0
        if i < len(gaps):
            cursor += gaps[i]

    spine_count = max(0, min(2, int(specdef.get("spine_count", 0) or 0)))
    center_ratio = max(0.15, min(0.85, float(specdef.get("spine_ratio", 0.5) or 0.5)))
    spread = max(0.12, min(0.60, float(specdef.get("spine_spread", 0.34) or 0.34)))
    if spine_count == 0:
        spine_xs = []
    elif spine_count == 1:
        spine_xs = [minx + center_ratio * width]
    else:
        mid = minx + center_ratio * width
        half = spread * width / 2.0
        spine_xs = [max(minx + 0.10 * width, mid - half), min(maxx - 0.10 * width, mid + half)]
        spine_xs = sorted(spine_xs)

    specs = []
    def add_h(y, rw, kind, x1, x2, role="corridor"):
        if x2 - x1 <= max(2.0, float(req.lot_width_m)):
            return
        specs.append({"axis": "h", "coord": y, "width": rw, "kind": kind,
                      "role": role, "line": LineString([(x1, y), (x2, y)])})

    def add_v(x, rw, kind, role="spine"):
        specs.append({"axis": "v", "coord": x, "width": rw, "kind": kind,
                      "role": role, "line": LineString([(x, miny - 5.0), (x, maxy + 5.0)])})

    for x in spine_xs:
        add_v(x, main_w, "main", "dual-spine" if spine_count == 2 else "spine")

    coverage = max(0.0, min(1.0, float(specdef.get("double_loaded_coverage", 1.0) or 0.0)))
    full_target = max(0, min(corridor_count, int(round(corridor_count * coverage))))
    # Spread full double-loaded corridors across the site instead of clustering them.
    if full_target >= corridor_count:
        full_indices = set(range(corridor_count))
    elif full_target <= 0:
        full_indices = set()
    else:
        full_indices = {int(round(i * (corridor_count - 1) / max(1, full_target - 1))) for i in range(full_target)}

    branch_budget = max(0, min(corridor_count, int(specdef.get("short_branch_count", corridor_count) or 0)))
    branch_ratio = max(0.15, min(1.0, float(specdef.get("short_branch_length_ratio", 0.55) or 0.55)))
    termination = str(specdef.get("road_termination", "alternating-spine"))
    branches_used = 0

    for i, (y, rw, kind) in enumerate(ys):
        if termination == "boundary" or i in full_indices or not spine_xs:
            add_h(y, rw, kind, minx - 5.0, maxx + 5.0, "double-loaded")
            continue
        if branches_used >= branch_budget:
            continue
        branches_used += 1

        if spine_count == 2 and termination == "dual-spine" and i % 3 == 1:
            add_h(y, rw, kind, spine_xs[0], spine_xs[1], "dual-spine-link")
            continue

        side_left = (i + int(specdef.get("termination_phase", 0) or 0)) % 2 == 0
        anchor = spine_xs[0] if side_left else spine_xs[-1]
        if termination == "staggered" and spine_count == 2 and i % 4 == 2:
            anchor = spine_xs[1] if side_left else spine_xs[0]
            side_left = not side_left

        available = (anchor - minx) if side_left else (maxx - anchor)
        length = max(float(req.lot_depth_m) + float(req.lot_width_m), available * branch_ratio)
        if side_left:
            add_h(y, rw, kind, max(minx - 5.0, anchor - length), anchor, "short-branch")
        else:
            add_h(y, rw, kind, anchor, min(maxx + 5.0, anchor + length), "short-branch")

    if bool(specdef.get("perimeter_assisted_access", False)):
        sides = str(specdef.get("perimeter_access_sides", "both"))
        inset = lot_d + local_w / 2.0
        perimeter_xs = []
        if sides in ("left", "both"):
            perimeter_xs.append(minx + inset)
        if sides in ("right", "both"):
            perimeter_xs.append(maxx - inset)
        for x in perimeter_xs:
            if all(abs(x - sx) > max(main_w, local_w) * 1.5 for sx in spine_xs):
                add_v(x, local_w, "local", "perimeter-assisted")

    return specs


def _road_specs_for(core: dict[str, Any], rot, req: Any, specdef: dict[str, Any]):
    if bool(specdef.get("structural_mode", False)):
        return _structural_road_specs(rot, req, specdef)

    specs = core["_road_specs"](
        rot,
        specdef["pattern"],
        float(req.lot_depth_m),
        float(req.main_road_width_m),
        float(req.local_road_width_m),
        spine_ratio=float(specdef.get("spine_ratio", 0.5)),
    )
    shift = float(specdef.get("shift_m", 0.0) or 0.0)
    if abs(shift) > 1e-9:
        moved = []
        for s in specs:
            ss = dict(s)
            if ss.get("axis") == "h":
                ss["coord"] = float(ss.get("coord", 0.0)) + shift
                ss["line"] = affinity.translate(ss["line"], yoff=shift)
            moved.append(ss)
        specs = moved

    topology = specdef.get("topology", "base")
    if topology not in ("short-branches", "hybrid"):
        return specs

    minx, miny, maxx, maxy = rot.bounds
    vertical = [s for s in specs if s.get("axis") == "v"]
    spine_x = float(vertical[0].get("coord")) if vertical else minx + float(specdef.get("spine_ratio", 0.5)) * (maxx - minx)
    out = []
    h_index = 0
    for s in specs:
        ss = dict(s)
        if ss.get("axis") != "h":
            out.append(ss)
            continue
        y = float(ss.get("coord", (miny + maxy) / 2.0))
        if topology == "short-branches":
            if h_index % 2 == 0:
                ss["line"] = LineString([(spine_x, y), (maxx + 5.0, y)])
            else:
                ss["line"] = LineString([(minx - 5.0, y), (spine_x, y)])
        else:
            if h_index % 2 == 1:
                if (h_index // 2) % 2 == 0:
                    ss["line"] = LineString([(spine_x, y), (maxx + 5.0, y)])
                else:
                    ss["line"] = LineString([(minx - 5.0, y), (spine_x, y)])
        out.append(ss)
        h_index += 1
    return out
'''

rec, n = re.subn(r'def _road_specs_for\(.*?\n(?=def _place_facilities)', road_impl + '\n\n', rec, count=1, flags=re.S)
if n != 1:
    raise RuntimeError("_road_specs_for replacement failed")

mutation_impl = r'''def _mutation_specs(core: dict[str, Any], req: Any, round_no: int, batch_size: int = 16) -> list[dict[str, Any]]:
    """Deterministic structural search over road/block topology.

    Unlike the earlier angle/phase-only loop, every round also explores:
    corridor count, corridor spacing, short branch count/length, single/dual
    spine, double-loaded coverage, road termination, perimeter access and
    block-depth combinations. No variable changes STANDARD width/depth.
    """
    geom = core["ensure_polygon"](req.geometry)
    epsg = core["utm_epsg_for_geometry"](geom)
    parcel = core["project_geom"](geom, 4326, epsg)
    buildable = core["_polygonal_only"](parcel.buffer(-req.setback_m, join_style=2))
    base = float(core["_dominant_angle_deg"](buildable))
    minx, miny, maxx, maxy = buildable.bounds
    span = max(1.0, maxy - miny)
    d = float(req.lot_depth_m)
    lw = float(req.local_road_width_m)

    ideal = max(1, int((max(0.0, span - 2.0 * d) + lw) // max(1.0, 2.0 * d + lw)))
    corridor_options = sorted({max(1, ideal + k) for k in (-2, -1, 0, 1, 2, 3)})
    spacing_options = [d, 2.0 * d, 3.0 * d, 4.0 * d]
    block_combos = [
        [2.0 * d],
        [d, 2.0 * d],
        [2.0 * d, 3.0 * d],
        [2.0 * d, 4.0 * d],
        [d, 2.0 * d, 2.0 * d],
    ]
    spine_options = [0, 1, 2]
    coverage_options = [0.50, 0.67, 0.80, 1.00]
    branch_length_options = [0.35, 0.50, 0.70, 0.90]
    terminations = ["boundary", "alternating-spine", "staggered", "dual-spine"]
    facility_pairs = [
        ("top", "bottom"), ("bottom", "top"), ("left", "right"),
        ("right", "left"), ("top", "right"), ("left", "bottom"),
    ]

    out = []
    for j in range(batch_size):
        n = round_no * batch_size + j + 1
        # Coprime/irrational stepping prevents short cycles across the product space.
        angle_frac = (n * 0.6180339887498949) % 1.0
        phase_frac = (n * 0.7548776662466927) % 1.0
        spine_frac = (n * 0.4142135623730950) % 1.0
        offset = angle_frac * 30.0 - 15.0
        angle = (base + offset + (90.0 if (n // 5) % 2 else 0.0)) % 180.0

        corridor_count = corridor_options[n % len(corridor_options)]
        spacing = spacing_options[(n * 3) % len(spacing_options)]
        combo = block_combos[(n * 2 + round_no) % len(block_combos)]
        spine_count = spine_options[(n + round_no) % len(spine_options)]
        coverage = coverage_options[(n * 3 + round_no) % len(coverage_options)]
        branch_length = branch_length_options[(n * 5 + round_no) % len(branch_length_options)]
        termination = terminations[(n * 7 + round_no) % len(terminations)]
        if spine_count == 0 and termination != "boundary":
            termination = "boundary"
        if termination == "dual-spine" and spine_count < 2:
            spine_count = 2

        branch_options = [0, max(1, corridor_count // 3), max(1, corridor_count // 2), corridor_count]
        branch_count = branch_options[(n * 11 + round_no) % len(branch_options)]
        rth_side, psu_side = facility_pairs[n % len(facility_pairs)]
        perimeter = ((n + round_no) % 3 == 0)
        perimeter_sides = ("left", "right", "both")[(n * 5 + round_no) % 3]
        phase_limit = max(0.0, min(d, max(0.0, span - (2.0 * d + corridor_count * lw)) / 2.0))
        shift = (phase_frac * 2.0 - 1.0) * phase_limit
        spine_ratio = 0.20 + spine_frac * 0.60
        spine_spread = 0.22 + ((n * 0.3027756377) % 1.0) * 0.34

        label = (
            f"Structural R{round_no+1}-{j+1} • C{corridor_count} • gap {spacing:.0f}m • "
            f"spine {spine_count} • branch {branch_count}@{branch_length:.2f} • DL {coverage:.0%} • {termination}"
        )
        out.append({
            "name": label,
            "pattern": "parallel" if spine_count == 0 else "spine",
            "angle": angle,
            "topology": "structural",
            "structural_mode": True,
            "corridor_count": corridor_count,
            "corridor_spacing_m": spacing,
            "block_depth_combo_m": combo,
            "short_branch_count": branch_count,
            "short_branch_length_ratio": branch_length,
            "spine_count": spine_count,
            "spine_ratio": spine_ratio,
            "spine_spread": spine_spread,
            "double_loaded_coverage": coverage,
            "road_termination": termination,
            "termination_phase": n % 2,
            "perimeter_assisted_access": perimeter,
            "perimeter_access_sides": perimeter_sides,
            "shift_m": shift,
            "rth_side": rth_side,
            "psu_side": psu_side,
            "facility_mode": "low-yield" if n % 5 == 0 else "edge",
        })
    return out
'''

rec, n = re.subn(r'def _mutation_specs\(.*?\n(?=def _run_mutation_loop)', mutation_impl + '\n\n', rec, count=1, flags=re.S)
if n != 1:
    raise RuntimeError("_mutation_specs replacement failed")

# Preserve structural settings on each alternative for monitor/debugging.
needle = '        "angle_deg": round(angle, 2),\n        "buildable": core["_mapping_wgs"](rot, angle, origin, epsg),\n'
params = '''        "angle_deg": round(angle, 2),
        "solver_params": {k: copy.deepcopy(specdef.get(k)) for k in (
            "corridor_count", "corridor_spacing_m", "block_depth_combo_m",
            "short_branch_count", "short_branch_length_ratio", "spine_count",
            "spine_ratio", "spine_spread", "double_loaded_coverage",
            "road_termination", "perimeter_assisted_access", "perimeter_access_sides",
        ) if k in specdef},
        "buildable": core["_mapping_wgs"](rot, angle, origin, epsg),
'''
if needle not in rec:
    raise RuntimeError("alt solver_params anchor not found")
rec = rec.replace(needle, params, 1)

rec = rec.replace('"Upper bound masih >=70% • mutation search terus berjalan"', '"Upper bound masih >=70% • structural corridor search terus berjalan"')
rec = rec.replace('f"Round {round_no} selesai • {tested} mutation tested • best {best:.2f}% • lanjut mencari"', 'f"Round {round_no} selesai • {tested} structural candidates • best {best:.2f}% • lanjut mencari"')
rec = rec.replace('f"Solver belum konvergen: best {best:.2f}% <70% • mutation round {round_no+1} berjalan"', 'f"Solver belum konvergen: best {best:.2f}% <70% • structural corridor round {round_no+1} berjalan"')

REC.write_text(rec, encoding="utf-8")

js = JS.read_text(encoding="utf-8")
old = "function renderSolverCurrent(candidate){\n  const root=solverEl('solverCurrent'); if(!root)return;\n  if(!candidate){root.innerHTML='<div class=\"empty-state\">Belum ada candidate aktif.</div>';return;}\n  const status=candidate.pass?'PASS':'REJECT';\n"
new = "function renderSolverCurrent(candidate){\n  const root=solverEl('solverCurrent'); if(!root)return;\n  if(!candidate){root.innerHTML='<div class=\"empty-state\">Belum ada candidate aktif.</div>';return;}\n  const status=candidate.pass?'PASS':'REJECT';\n  const sp=candidate.structural||{};\n  const structural=Object.keys(sp).length?`<div class=\"wide structural-detail\"><span>Structural topology</span><b>C${sp.corridor_count??'—'} • gap ${solverNum(sp.corridor_spacing_m,0)}m • spine ${sp.spine_count??'—'} • branch ${sp.short_branch_count??'—'} @ ${solverNum(sp.short_branch_length_ratio,2)} • DL ${solverNum(Number(sp.double_loaded_coverage||0)*100,0)}% • ${sp.road_termination||'—'}${sp.perimeter_assisted_access?' • perimeter access':''}</b><small>Block depth: ${(sp.block_depth_combo_m||[]).map(x=>solverNum(x,0)+'m').join(' / ')||'—'}</small></div>`:'';\n"
if old not in js:
    raise RuntimeError("renderSolverCurrent anchor not found")
js = js.replace(old, new, 1)
old2 = "    <div><span>Residual</span><b>${solverNum(candidate.residual_pct,2)}%</b></div>\n  </div>`;"
new2 = "    <div><span>Residual</span><b>${solverNum(candidate.residual_pct,2)}%</b></div>\n    ${structural}\n  </div>`;"
if old2 not in js:
    raise RuntimeError("renderSolverCurrent grid anchor not found")
js = js.replace(old2, new2, 1)
JS.write_text(js, encoding="utf-8")

TEST.write_text(r'''from shapely import affinity

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
    print("=== M2.5.13 Structural Corridor Recovery tests ===")
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
''', encoding="utf-8")

status = STATUS.read_text(encoding="utf-8") if STATUS.exists() else "# Milestone 2.5.13\n"
section = r'''

## Structural Corridor Recovery upgrade
The continuing mutation loop now changes the road/block structure rather than only angle/phase. Search dimensions include:
- corridor count;
- clear spacing between corridors;
- short-branch count and branch length;
- zero/single/dual spine;
- double-loaded corridor coverage;
- road termination strategy (boundary, alternating-spine, staggered, dual-spine);
- perimeter-assisted access;
- alternating block-depth combinations.

These variables never change Geometry Settings. STANDARD remains exact width x depth from `geometry_settings`; Adaptive remains `residual_only`.
The live Candidate Aktif panel exposes the structural parameters being evaluated.
'''
if "## Structural Corridor Recovery upgrade" not in status:
    STATUS.write_text(status.rstrip() + section, encoding="utf-8")

print("structural corridor patch applied")
