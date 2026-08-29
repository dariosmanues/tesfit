from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "services/geometry-api/app/main.py"
INDEX = ROOT / "services/geometry-api/web/index.html"
APPJS = ROOT / "services/geometry-api/web/app.js"
TEST = ROOT / "services/geometry-api/test_m2513_recovery_solver.py"
STATUS = ROOT / "MILESTONE2_5_13_STATUS.md"
README = ROOT / "README.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    return text.replace(old, new, 1)


main = MAIN.read_text(encoding="utf-8")
registration = '''\n\n# -----------------------------\n# Milestone 2.5.13: staged Recovery Solver + live monitor API\n# -----------------------------\ntry:\n    from .recovery_solver import register_recovery_solver as _register_recovery_solver\nexcept ImportError:  # direct script fallback\n    from recovery_solver import register_recovery_solver as _register_recovery_solver\n\n_register_recovery_solver(app, globals())\n'''
if "_register_recovery_solver(app, globals())" not in main:
    main = main.rstrip() + registration

# Health endpoint only: keep historical optimizer metadata intact where it documents M2.5.12 internals.
health_patterns = [
    (r'(def health\(\).*?"version"\s*:\s*")2\.5\.12(".*?\n)', r'\g<1>2.5.13\2'),
    (r'(@app\.get\(["\']\/health["\']\).*?"version"\s*:\s*")2\.5\.12(".*?\n)', r'\g<1>2.5.13\2'),
]
for pat, repl in health_patterns:
    changed, n = re.subn(pat, repl, main, count=1, flags=re.S)
    if n:
        main = changed
        break
MAIN.write_text(main, encoding="utf-8")

appjs = APPJS.read_text(encoding="utf-8")
appjs = appjs.replace('const DEVOS_FRONTEND_VERSION = "2.5.12";', 'const DEVOS_FRONTEND_VERSION = "2.5.13";', 1)
APPJS.write_text(appjs, encoding="utf-8")

index = INDEX.read_text(encoding="utf-8")
index = index.replace("Milestone 2.5.12", "Milestone 2.5.13")
index = index.replace("M2.5.12 membentuk", "M2.5.13 membentuk")
index = index.replace('/static/app.css?v=2.5.12', '/static/app.css?v=2.5.13')
if '/static/recovery-monitor.css?v=2.5.13' not in index:
    index = index.replace(
        '<link rel="stylesheet" href="/static/app.css?v=2.5.13" />',
        '<link rel="stylesheet" href="/static/app.css?v=2.5.13" />\n  <link rel="stylesheet" href="/static/recovery-monitor.css?v=2.5.13" />',
        1,
    )

monitor = '''      <div class="panel solver-monitor" id="solverMonitor">
        <div class="solver-head">
          <div><h2>M2.5.13 — Recovery Solver Monitor</h2><div class="small">Progress berasal dari candidate yang benar-benar dihitung backend. Rule final tetap STRICT: Efisiensi Kavling ≥ 70%.</div></div>
          <span id="solverOverallStatus" class="solver-state">Siap</span>
        </div>
        <div class="solver-kpis">
          <div class="solver-kpi"><span>Best saat ini</span><b id="solverBest">—</b></div>
          <div class="solver-kpi"><span>Target wajib</span><b id="solverTarget">70.00%</b></div>
          <div class="solver-kpi" id="solverGapBox"><span>Gap</span><b id="solverGap">—</b></div>
        </div>
        <div class="solver-progress"><i id="solverProgressBar"></i></div>
        <div id="solverStages" class="solver-stage-list"><div class="empty-state">Solver belum dijalankan.</div></div>
        <div class="solver-subpanel">
          <div class="solver-subpanel-title">Candidate aktif</div>
          <div id="solverCurrent"><div class="empty-state">Belum ada candidate aktif.</div></div>
        </div>
        <div class="solver-subpanel">
          <div class="solver-subpanel-title">Search History — candidate &lt;70% tetap REJECT</div>
          <div id="solverHistory" class="solver-history"><div class="empty-state">Search History masih kosong.</div></div>
        </div>
        <div class="solver-subpanel">
          <div class="solver-subpanel-title">Feasibility Analysis</div>
          <div id="solverFeasibility"><div class="empty-state">Dijalankan jika seluruh recovery stage belum mencapai 70%.</div></div>
        </div>
      </div>

'''
anchor = '      <div class="panel" id="alternativesPanel">'
if 'id="solverMonitor"' not in index:
    if anchor not in index:
        raise RuntimeError("alternativesPanel anchor not found")
    index = index.replace(anchor, monitor + anchor, 1)

index = index.replace('/static/app.js?v=2.5.12', '/static/app.js?v=2.5.13')
if '/static/recovery-monitor.js?v=2.5.13' not in index:
    index = index.replace(
        '<script src="/static/app.js?v=2.5.13"></script>',
        '<script src="/static/app.js?v=2.5.13"></script>\n  <script src="/static/recovery-monitor.js?v=2.5.13"></script>',
        1,
    )
INDEX.write_text(index, encoding="utf-8")

TEST.write_text(r'''import json
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
    print("[OK] six-stage recovery strategy definitions")

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
    print("[OK] solver start/status routes registered")

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
''', encoding="utf-8")

STATUS.write_text(r'''# Milestone 2.5.13 — Recovery Solver Monitor

## Hard business rule
- Gross Lot Efficiency **>= 70%** remains a strict gate.
- Any candidate below 70% is **REJECT** and never enters the selectable/saveable Alternative Layout pool.
- Once a candidate passes, ranking is: Standard count -> efficiency -> fewer Adaptive -> smaller road area -> smaller residual -> block regularity -> connectivity.

## Real staged solver
The backend runs an in-memory job and the frontend polls real state from `/site-plan/solver/status/{job_id}`. No fake progress percentages are generated in the browser.

Stages:
1. Initial Search — unchanged M2.5.12 baseline generator.
2. Road Topology Recovery — double-loaded parallel, single spine, short branches, perimeter-assisted and hybrid candidates.
3. Block Spacing Recovery — shifts the 30 m double-loaded module phase without changing the 8x15 Standard product.
4. Orientation Recovery — dominant +/-2/4/6/8/10/15 degrees plus boundary-aligned angles.
5. Perimeter Recovery — pushes irregularity toward perimeter-oriented short-branch layouts.
6. RTH/PSU Placement Recovery — required percentages stay fixed while edge/low-yield placement changes.
7. Adaptive Recovery — only TRUE residual may become saleable Adaptive; Standard/roads/RTH/PSU are immutable in this pass.
8. Feasibility Analysis — only declares mathematical infeasibility when an optimistic upper bound that ignores all roads/residual is already below 70%; otherwise reports solver-not-converged.

## UI monitor
Shows live stage, tested/total candidates, current strategy, Standard/Adaptive counts, road %, residual %, best efficiency, gap to 70%, rejected search history, and feasibility diagnosis.

## Baseline protection
The existing `generate_site_alternatives`, `_road_specs`, and `_pack_standard_blocks` implementations are not rewritten by this milestone. Recovery candidates are implemented in `app/recovery_solver.py` and registered as separate endpoints.
''', encoding="utf-8")

readme = README.read_text(encoding="utf-8") if README.exists() else ""
line = "\n- M2.5.13 adds a real staged Recovery Solver Monitor; only geometry-valid alternatives with gross lot efficiency >=70% enter the selectable Alternative Layout pool.\n"
if "M2.5.13 adds a real staged Recovery Solver Monitor" not in readme:
    README.write_text(readme.rstrip() + line, encoding="utf-8")

print("M2.5.13 wiring patch applied")
