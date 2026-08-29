from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "services/geometry-api/app/recovery_solver.py"
HTML = ROOT / "services/geometry-api/web/index.html"
JS = ROOT / "services/geometry-api/web/recovery-monitor.js"
TEST = ROOT / "services/geometry-api/test_m2513_recovery_solver.py"
STATUS = ROOT / "MILESTONE2_5_13_STATUS.md"


def replace_once(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    return text.replace(old, new, 1)


solver = SOLVER.read_text(encoding="utf-8")
solver = replace_once(
    solver,
    '    ("feasibility", "Feasibility Analysis"),\n]',
    '    ("feasibility", "Feasibility Analysis"),\n    ("mutation", "Topology Mutation Loop"),\n]',
    "stage mutation",
)

insert_anchor = '\n\ndef _run_recovery_solver(core: dict[str, Any], req: Any, job_id: str) -> None:\n'
mutation_code = r'''

def _cancel_requested(job_id: str) -> bool:
    with _JOBS_LOCK:
        return bool((_JOBS.get(job_id) or {}).get("cancel_requested", False))


def _mutation_specs(core: dict[str, Any], req: Any, round_no: int, batch_size: int = 12) -> list[dict[str, Any]]:
    """Deterministic low-discrepancy topology mutations for continued search.

    There is intentionally no random seed. Each round explores a new combination
    of orientation, road phase, spine position, topology and facility placement
    while keeping Geometry Settings and RTH/PSU percentages fixed.
    """
    geom = core["ensure_polygon"](req.geometry)
    epsg = core["utm_epsg_for_geometry"](geom)
    parcel = core["project_geom"](geom, 4326, epsg)
    buildable = core["_polygonal_only"](parcel.buffer(-req.setback_m, join_style=2))
    base = float(core["_dominant_angle_deg"](buildable))
    facility_pairs = [
        ("top", "bottom"), ("bottom", "top"), ("left", "right"),
        ("right", "left"), ("top", "right"), ("left", "bottom"),
    ]
    out = []
    for j in range(batch_size):
        n = round_no * batch_size + j + 1
        angle_frac = (n * 0.6180339887498949) % 1.0
        phase_frac = (n * 0.7548776662466927) % 1.0
        spine_frac = (n * 0.4142135623730950) % 1.0
        offset = angle_frac * 30.0 - 15.0
        shift = (phase_frac * 2.0 - 1.0) * float(req.lot_depth_m)
        spine_ratio = 0.12 + spine_frac * 0.76
        rth_side, psu_side = facility_pairs[n % len(facility_pairs)]
        mode = n % 4
        if mode == 0:
            pattern, topology = "parallel", "base"
        elif mode == 1:
            pattern, topology = "spine", "short-branches"
        elif mode == 2:
            pattern, topology = "spine", "hybrid"
        else:
            pattern, topology = "parallel", "base"
        angle = base + offset + (90.0 if (n // 4) % 2 else 0.0)
        out.append({
            "name": f"Mutation R{round_no+1}-{j+1} • {topology}",
            "pattern": pattern,
            "angle": angle % 180.0,
            "topology": topology,
            "spine_ratio": spine_ratio,
            "shift_m": shift,
            "rth_side": rth_side,
            "psu_side": psu_side,
            "facility_mode": "low-yield" if n % 5 == 0 else "edge",
        })
    return out


def _run_mutation_loop(core: dict[str, Any], req: Any, pool: list[dict[str, Any]], base_result: dict[str, Any], job_id: str, diagnosis: dict[str, Any]) -> None:
    """Continue searching until PASS, mathematical proof of infeasibility, or user cancel.

    This intentionally has no arbitrary candidate-count stop. If the conservative
    feasibility upper bound remains >=70%, the job stays RUNNING and keeps
    mutating topology. The UI exposes a Cancel button so the operator controls
    the search budget explicitly.
    """
    _mark_stage(job_id, "mutation", "running", "Upper bound masih >=70% • mutation search terus berjalan", total=0)
    tested = 0
    round_no = 0
    while not _cancel_requested(job_id):
        specs = _mutation_specs(core, req, round_no)
        round_batch = []
        for specdef in specs:
            if _cancel_requested(job_id):
                break
            tested += 1
            try:
                alt = _evaluate_spec(core, req, specdef, "mutation", tested)
            except Exception as exc:
                def failed(job: dict[str, Any], tested=tested, specdef=specdef, exc=exc) -> None:
                    stage = _stage_ref(job, "mutation")
                    stage["candidates_tested"] = tested
                    stage["current_candidate"] = tested
                    stage["current_strategy"] = specdef.get("name")
                    stage["message"] = f"Mutation error: {exc}"
                _with_job(job_id, failed)
                continue
            if alt is None:
                continue
            pool.append(alt)
            round_batch.append(alt)
            _record_candidate(job_id, "mutation", alt, tested, 0, specdef.get("name"))
            if strict_valid_alternatives(pool):
                _finish_success(job_id, req, base_result, pool, "mutation")
                return

        if bool(getattr(req, "land_optimization_enabled", False)) and round_batch and not _cancel_requested(job_id):
            seeds = sorted(
                round_batch,
                key=lambda a: float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)),
                reverse=True,
            )[:3]
            for seed in seeds:
                if _cancel_requested(job_id):
                    break
                try:
                    adaptive = _adaptive_candidate(core, req, seed, tested + 1)
                except Exception:
                    continue
                if adaptive is None:
                    continue
                tested += 1
                pool.append(adaptive)
                _record_candidate(job_id, "mutation", adaptive, tested, 0, "Mutation + Residual → Adaptive")
                if strict_valid_alternatives(pool):
                    _finish_success(job_id, req, base_result, pool, "mutation")
                    return

        round_no += 1
        best = max([float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)) for a in pool] or [0.0])
        def round_update(job: dict[str, Any], round_no=round_no, tested=tested, best=best) -> None:
            stage = _stage_ref(job, "mutation")
            stage["candidate_total"] = 0
            stage["candidates_tested"] = tested
            stage["message"] = f"Round {round_no} selesai • {tested} mutation tested • best {best:.2f}% • lanjut mencari"
            job["message"] = f"Solver belum konvergen: best {best:.2f}% <70% • mutation round {round_no+1} berjalan"
            job["feasibility"] = diagnosis
        _with_job(job_id, round_update)
        time.sleep(0.05)

    result = {
        "parcel": base_result.get("parcel"),
        "parcel_stats": base_result.get("parcel_stats"),
        "settings": req.model_dump() if hasattr(req, "model_dump") else {},
        "alternatives": [],
        "notice": "Solver dihentikan user. Candidate <70% tetap hanya ada di Search History.",
        "feasibility": diagnosis,
    }
    def cancelled(job: dict[str, Any]) -> None:
        job["status"] = "cancelled"
        job["active_stage"] = None
        job["result"] = result
        job["valid_count"] = 0
        job["message"] = "Recovery Solver dihentikan oleh user."
        stage = _stage_ref(job, "mutation")
        stage["status"] = "cancelled"
        stage["message"] = "Dihentikan user"
    _with_job(job_id, cancelled)
'''
if 'def _run_mutation_loop(' not in solver:
    if insert_anchor not in solver:
        raise RuntimeError("run solver anchor missing")
    solver = solver.replace(insert_anchor, mutation_code + insert_anchor, 1)

old_final = r'''        result = {
            "parcel": base_result.get("parcel"),
            "parcel_stats": base_result.get("parcel_stats"),
            "settings": req.model_dump() if hasattr(req, "model_dump") else {},
            "alternatives": [],
            "notice": "Tidak ada candidate valid >=70%. Candidate FAIL hanya tersedia di Search History; tidak masuk Alternatif Layout.",
            "feasibility": diagnosis,
        }

        def failed_target(job: dict[str, Any]) -> None:
            job["status"] = "completed"
            job["active_stage"] = None
            job["result"] = result
            job["feasibility"] = diagnosis
            job["valid_count"] = 0
            job["message"] = (
                "TARGET 70% SECARA MATEMATIS TIDAK MUNGKIN dengan fixed constraints."
                if diagnosis["mathematically_infeasible"]
                else "TARGET 70% BELUM DITEMUKAN — solver belum konvergen."
            )
        _with_job(job_id, failed_target)
'''
new_final = r'''        if diagnosis["mathematically_infeasible"]:
            result = {
                "parcel": base_result.get("parcel"),
                "parcel_stats": base_result.get("parcel_stats"),
                "settings": req.model_dump() if hasattr(req, "model_dump") else {},
                "alternatives": [],
                "notice": "70% terbukti melampaui optimistic mathematical upper bound untuk fixed constraints ini.",
                "feasibility": diagnosis,
            }
            def infeasible(job: dict[str, Any]) -> None:
                job["status"] = "completed"
                job["active_stage"] = None
                job["result"] = result
                job["feasibility"] = diagnosis
                job["valid_count"] = 0
                job["message"] = "TARGET 70% SECARA MATEMATIS TIDAK MUNGKIN dengan fixed constraints."
                mutation = _stage_ref(job, "mutation")
                mutation["status"] = "skipped"
                mutation["message"] = "Tidak dijalankan karena infeasibility sudah terbukti"
            _with_job(job_id, infeasible)
            return

        def continue_search(job: dict[str, Any]) -> None:
            job["feasibility"] = diagnosis
            job["message"] = "Upper bound masih >=70% — solver belum selesai; topology mutation dilanjutkan."
        _with_job(job_id, continue_search)
        _run_mutation_loop(core, req, pool, base_result, job_id, diagnosis)
'''
solver = replace_once(solver, old_final, new_final, "feasible must continue")

solver = replace_once(
    solver,
    '            "message": "Antri",\n            "created_at": now,',
    '            "message": "Antri",\n            "cancel_requested": False,\n            "created_at": now,',
    "cancel flag",
)

old_status = r'''    @app.get("/site-plan/solver/status/{job_id}")
    def solver_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                core["HTTPException"](404, "Solver job tidak ditemukan")
                raise core["HTTPException"](404, "Solver job tidak ditemukan")
            return copy.deepcopy(job)'''
new_status = r'''    @app.get("/site-plan/solver/status/{job_id}")
    def solver_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                raise core["HTTPException"](404, "Solver job tidak ditemukan")
            return copy.deepcopy(job)

    @app.post("/site-plan/solver/cancel/{job_id}")
    def cancel_solver(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                raise core["HTTPException"](404, "Solver job tidak ditemukan")
            if job.get("status") in ("completed", "failed", "cancelled"):
                return {"job_id": job_id, "status": job.get("status"), "cancel_requested": False}
            job["cancel_requested"] = True
            job["message"] = "Permintaan stop diterima; solver akan berhenti setelah candidate aktif selesai."
            job["updated_at"] = time.time()
            return {"job_id": job_id, "status": job.get("status"), "cancel_requested": True}'''
solver = replace_once(solver, old_status, new_status, "cancel endpoint")
SOLVER.write_text(solver, encoding="utf-8")

html = HTML.read_text(encoding="utf-8")
html = replace_once(
    html,
    '        <div class="solver-progress"><i id="solverProgressBar"></i></div>',
    '        <div class="solver-progress"><i id="solverProgressBar"></i></div>\n        <button id="solverCancelBtn" class="danger" disabled>Stop Recovery Solver</button>',
    "cancel button",
)
HTML.write_text(html, encoding="utf-8")

js = JS.read_text(encoding="utf-8")
js = replace_once(
    js,
    "  solverEl('solverProgressBar').style.width='0%';",
    "  solverEl('solverProgressBar').style.width='0%';\n  if(solverEl('solverCancelBtn'))solverEl('solverCancelBtn').disabled=true;",
    "reset cancel",
)
js = replace_once(
    js,
    "  renderSolverFeasibility(job.feasibility||job.result?.feasibility||null);",
    "  renderSolverFeasibility(job.feasibility||job.result?.feasibility||null);\n  if(solverEl('solverCancelBtn'))solverEl('solverCancelBtn').disabled=!(job.status==='running'||job.status==='queued');",
    "render cancel",
)
js = replace_once(
    js,
    "    if(job.status==='failed') throw new Error(job.error||job.message||'Recovery Solver gagal');\n    if(job.status==='completed') return job;",
    "    if(job.status==='failed') throw new Error(job.error||job.message||'Recovery Solver gagal');\n    if(job.status==='completed'||job.status==='cancelled') return job;",
    "poll cancelled",
)
insert_js_anchor = "\nasync function generateSitePlanRecovery(){\n"
cancel_js = r'''

async function cancelRecoverySolver(){
  if(!recoverySolverJobId||!recoverySolverRunning)return;
  const btn=solverEl('solverCancelBtn');
  try{
    if(btn){btn.disabled=true;btn.textContent='Menghentikan…';}
    await api(`/site-plan/solver/cancel/${encodeURIComponent(recoverySolverJobId)}`,{method:'POST'});
    msg('Permintaan stop dikirim. Solver berhenti setelah candidate aktif selesai.');
  }catch(e){msg(`Gagal menghentikan solver: ${e.message}`,'error');}
  finally{if(btn)btn.textContent='Stop Recovery Solver';}
}
'''
if 'async function cancelRecoverySolver()' not in js:
    if insert_js_anchor not in js:
        raise RuntimeError("generate recovery JS anchor missing")
    js = js.replace(insert_js_anchor, cancel_js + insert_js_anchor, 1)
js = replace_once(
    js,
    "    sitePlan=job.result;\n    if(!sitePlan)throw new Error('Solver selesai tanpa result payload');",
    "    if(job.status==='cancelled'){msg('Recovery Solver dihentikan. Layout sebelumnya dipertahankan.');return;}\n    sitePlan=job.result;\n    if(!sitePlan)throw new Error('Solver selesai tanpa result payload');",
    "cancel preserve layout",
)
js = replace_once(
    js,
    "  if(btn)btn.onclick=generateSitePlanRecovery;",
    "  if(btn)btn.onclick=generateSitePlanRecovery;\n  const cancelBtn=solverEl('solverCancelBtn');\n  if(cancelBtn)cancelBtn.onclick=cancelRecoverySolver;",
    "bind cancel",
)
JS.write_text(js, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    '    assert any(x["facility_mode"] == "low-yield" for x in facility)\n    print("[OK] six-stage recovery strategy definitions")',
    '    assert any(x["facility_mode"] == "low-yield" for x in facility)\n    assert any(x["id"] == "mutation" for x in rs.stage_definitions())\n    mutation_a = rs._mutation_specs(core, req, 0)\n    mutation_b = rs._mutation_specs(core, req, 1)\n    assert len(mutation_a) == 12 and len(mutation_b) == 12\n    assert {(x["angle"], x["shift_m"], x["spine_ratio"]) for x in mutation_a}.isdisjoint({(x["angle"], x["shift_m"], x["spine_ratio"]) for x in mutation_b})\n    print("[OK] staged recovery + deterministic continuing mutation definitions")',
    "mutation test",
)
test = replace_once(
    test,
    '    assert "/site-plan/solver/status/{job_id}" in paths\n    print("[OK] solver start/status routes registered")',
    '    assert "/site-plan/solver/status/{job_id}" in paths\n    assert "/site-plan/solver/cancel/{job_id}" in paths\n    print("[OK] solver start/status/cancel routes registered")',
    "cancel route test",
)
test = replace_once(
    test,
    "    assert '/site-plan/solver/status/' in js\n",
    "    assert '/site-plan/solver/status/' in js\n    assert '/site-plan/solver/cancel/' in js\n    assert 'solverCancelBtn' in html\n",
    "frontend cancel test",
)
TEST.write_text(test, encoding="utf-8")

status = STATUS.read_text(encoding="utf-8")
append = r'''

## Continuous mutation rule
If Feasibility Analysis still has a conservative theoretical upper bound >=70%, the solver does **not** finish with a sub-70 result. It enters `Topology Mutation Loop` and keeps testing deterministic new topology/orientation/phase/facility combinations until a valid >=70% candidate is found or the user explicitly presses **Stop Recovery Solver**. Candidate <70% remains REJECT throughout.
'''
if "## Continuous mutation rule" not in status:
    STATUS.write_text(status.rstrip() + append, encoding="utf-8")

print("M2.5.13 continuous mutation patch applied")
