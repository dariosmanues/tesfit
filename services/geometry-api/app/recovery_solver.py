from __future__ import annotations

import copy
import math
import threading
import time
import traceback
import uuid
from typing import Any, Callable

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Polygon, box, mapping
from shapely.ops import unary_union


TARGET_EFFICIENCY_PCT = 70.0

STAGE_DEFINITIONS = [
    ("initial", "Initial Search"),
    ("road_topology", "Road Topology Recovery"),
    ("block_spacing", "Block Spacing Recovery"),
    ("orientation", "Orientation Recovery"),
    ("perimeter", "Perimeter Recovery"),
    ("facility", "RTH / PSU Placement Recovery"),
    ("adaptive", "Adaptive Recovery"),
    ("feasibility", "Feasibility Analysis"),
]

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.RLock()


def stage_definitions() -> list[dict[str, Any]]:
    return [
        {
            "id": sid,
            "name": name,
            "status": "pending",
            "candidates_tested": 0,
            "candidate_total": 0,
            "best_efficiency_pct": 0.0,
            "best_road_pct": None,
            "current_strategy": None,
            "current_candidate": 0,
            "message": "Menunggu",
        }
        for sid, name in STAGE_DEFINITIONS
    ]


def strict_valid_alternatives(alternatives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only hard-PASS alternatives and rank them by the business rule.

    <70% is never a valid alternative, regardless of road area or residual.
    Once a candidate is inside the PASS pool, ranking is:
    STANDARD count -> efficiency -> fewer Adaptive -> smaller road area ->
    smaller residual -> block regularity -> road connectivity.
    """
    valid = []
    for alt in alternatives or []:
        stats = alt.get("stats") or {}
        validation = alt.get("validation") or {}
        eff = float(stats.get("lot_efficiency_pct", validation.get("lot_efficiency_pct", 0.0)) or 0.0)
        if eff + 1e-9 < TARGET_EFFICIENCY_PCT:
            continue
        if validation and validation.get("valid") is not True:
            continue
        if stats.get("validation_passed") is False:
            continue
        valid.append(alt)

    valid.sort(
        key=lambda a: (
            int((a.get("stats") or {}).get("standard_lot_count", 0)),
            float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)),
            -int((a.get("stats") or {}).get("adaptive_lot_count", 0)),
            -float((a.get("stats") or {}).get("road_area_m2", 1e30)),
            -float((a.get("stats") or {}).get("residual_true_area_m2", (a.get("stats") or {}).get("unused_area_m2", 1e30))),
            float((a.get("stats") or {}).get("average_block_regularity", 0.0)),
            float((a.get("stats") or {}).get("road_connectivity_score", 0.0)),
        ),
        reverse=True,
    )
    for i, alt in enumerate(valid, 1):
        alt["rank"] = i
        alt["recommended"] = i == 1
    return valid


def feasibility_diagnosis(core: dict[str, Any], req: Any, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Conservative feasibility diagnosis.

    `theoretical_upper_bound_pct` is a true upper bound because it ignores all
    roads and all residual/geometric waste. Therefore we only call a target
    mathematically infeasible when even that optimistic bound is below 70%.
    Otherwise a failed search is reported as solver-not-converged, never as a
    false mathematical impossibility claim.
    """
    geom = core["ensure_polygon"](req.geometry)
    epsg = core["utm_epsg_for_geometry"](geom)
    parcel = core["project_geom"](geom, 4326, epsg)
    buildable = core["_polygonal_only"](parcel.buffer(-req.setback_m, join_style=2))
    parcel_area = float(parcel.area)
    fixed_facility_area = parcel_area * (float(req.rth_pct) + float(req.psu_pct)) / 100.0
    theoretical_area = max(0.0, float(buildable.area) - fixed_facility_area)
    theoretical_upper = (theoretical_area / parcel_area * 100.0) if parcel_area else 0.0

    best_eff = max([float(h.get("efficiency_pct", 0.0)) for h in history] or [0.0])
    road_values = [float(h["road_pct"]) for h in history if h.get("road_pct") is not None]
    residual_values = [float(h["residual_pct"]) for h in history if h.get("residual_pct") is not None]
    min_road = min(road_values) if road_values else None
    min_residual = min(residual_values) if residual_values else None
    observed_estimate = None
    if min_road is not None and min_residual is not None:
        observed_estimate = max(
            0.0,
            100.0
            - float(req.rth_pct)
            - float(req.psu_pct)
            - min_road
            - min_residual,
        )

    proven = theoretical_upper + 1e-9 < TARGET_EFFICIENCY_PCT
    return {
        "target_efficiency_pct": TARGET_EFFICIENCY_PCT,
        "best_actual_efficiency_pct": round(best_eff, 2),
        "theoretical_upper_bound_pct": round(theoretical_upper, 2),
        "observed_search_estimate_pct": round(observed_estimate, 2) if observed_estimate is not None else None,
        "minimum_observed_road_pct": round(min_road, 2) if min_road is not None else None,
        "minimum_observed_residual_pct": round(min_residual, 2) if min_residual is not None else None,
        "rth_pct": float(req.rth_pct),
        "psu_pct": float(req.psu_pct),
        "buildable_pct_of_total": round((float(buildable.area) / parcel_area * 100.0) if parcel_area else 0.0, 2),
        "mathematically_infeasible": bool(proven),
        "status": "MATHEMATICALLY_INFEASIBLE" if proven else "SOLVER_NOT_CONVERGED",
        "message": (
            "Bahkan upper bound tanpa jalan/residual berada di bawah 70%."
            if proven
            else "Upper bound masih memungkinkan >=70%; search belum menemukan layout PASS."
        ),
    }


def _stage_ref(job: dict[str, Any], stage_id: str) -> dict[str, Any]:
    return next(s for s in job["stages"] if s["id"] == stage_id)


def _with_job(job_id: str, fn: Callable[[dict[str, Any]], None]) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            fn(job)
            job["updated_at"] = time.time()


def _mark_stage(job_id: str, stage_id: str, status: str, message: str | None = None, total: int | None = None) -> None:
    def update(job: dict[str, Any]) -> None:
        stage = _stage_ref(job, stage_id)
        stage["status"] = status
        if message is not None:
            stage["message"] = message
        if total is not None:
            stage["candidate_total"] = int(total)
        job["active_stage"] = stage_id if status == "running" else job.get("active_stage")
    _with_job(job_id, update)


def _candidate_summary(alt: dict[str, Any], stage_id: str, strategy: str | None = None) -> dict[str, Any]:
    s = alt.get("stats") or {}
    eff = float(s.get("lot_efficiency_pct", 0.0) or 0.0)
    return {
        "stage": stage_id,
        "strategy": strategy or alt.get("solver_strategy") or alt.get("name") or "candidate",
        "candidate_id": alt.get("id"),
        "name": alt.get("name"),
        "efficiency_pct": round(eff, 2),
        "pass": bool(eff >= TARGET_EFFICIENCY_PCT and (alt.get("validation") or {}).get("valid") is True),
        "standard_count": int(s.get("standard_lot_count", s.get("lot_count", 0)) or 0),
        "adaptive_count": int(s.get("adaptive_lot_count", 0) or 0),
        "road_pct": round(float(s.get("road_pct", 0.0) or 0.0), 2),
        "residual_pct": round(float(s.get("residual_pct_total_land", s.get("residual_true_pct", 0.0)) or 0.0), 2),
        "angle_deg": round(float(alt.get("angle_deg", 0.0) or 0.0), 2),
        "pattern": alt.get("pattern"),
        "timestamp": time.time(),
    }


def _record_candidate(job_id: str, stage_id: str, alt: dict[str, Any], current: int, total: int, strategy: str | None = None) -> None:
    summary = _candidate_summary(alt, stage_id, strategy)

    def update(job: dict[str, Any]) -> None:
        stage = _stage_ref(job, stage_id)
        stage["candidates_tested"] = max(int(stage.get("candidates_tested", 0)), int(current))
        stage["candidate_total"] = int(total)
        stage["current_candidate"] = int(current)
        stage["current_strategy"] = summary["strategy"]
        if summary["efficiency_pct"] >= float(stage.get("best_efficiency_pct", 0.0)):
            stage["best_efficiency_pct"] = summary["efficiency_pct"]
            stage["best_road_pct"] = summary["road_pct"]
        stage["message"] = (
            f"PASS {summary['efficiency_pct']:.2f}%" if summary["pass"]
            else f"REJECT {summary['efficiency_pct']:.2f}% < 70%"
        )
        job["current_candidate"] = summary
        job["search_history"].append(summary)
        if len(job["search_history"]) > 160:
            job["search_history"] = job["search_history"][-160:]
        best = job.get("best_seen")
        if best is None or summary["efficiency_pct"] > float(best.get("efficiency_pct", 0.0)):
            job["best_seen"] = summary
        job["valid_count"] = sum(1 for h in job["search_history"] if h.get("pass"))
    _with_job(job_id, update)


def _skip_remaining(job_id: str, after_stage: str, message: str) -> None:
    ids = [x[0] for x in STAGE_DEFINITIONS]
    start = ids.index(after_stage) + 1 if after_stage in ids else 0

    def update(job: dict[str, Any]) -> None:
        for sid in ids[start:]:
            s = _stage_ref(job, sid)
            if s["status"] == "pending":
                s["status"] = "skipped"
                s["message"] = message
    _with_job(job_id, update)


def _boundary_angles(rot) -> list[float]:
    angles: list[tuple[float, float]] = []
    parts = []
    if rot.geom_type == "Polygon":
        parts = [rot]
    elif rot.geom_type == "MultiPolygon":
        parts = list(rot.geoms)
    for part in parts:
        cs = list(part.exterior.coords)
        for a, b in zip(cs, cs[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length <= 1.0:
                continue
            angles.append((length, math.degrees(math.atan2(dy, dx)) % 180.0))
    out: list[float] = []
    for _, angle in sorted(angles, reverse=True):
        if all(min(abs(angle - x), 180.0 - abs(angle - x)) > 1.0 for x in out):
            out.append(angle)
        if len(out) >= 4:
            break
    return out


def _stage_specs(core: dict[str, Any], req: Any, stage_id: str) -> list[dict[str, Any]]:
    geom = core["ensure_polygon"](req.geometry)
    epsg = core["utm_epsg_for_geometry"](geom)
    parcel = core["project_geom"](geom, 4326, epsg)
    buildable = core["_polygonal_only"](parcel.buffer(-req.setback_m, join_style=2))
    base = float(core["_dominant_angle_deg"](buildable))

    specs: list[dict[str, Any]] = []
    def add(name: str, pattern: str, angle: float, topology: str = "base", spine_ratio: float = 0.5,
            shift_m: float = 0.0, rth_side: str = "top", psu_side: str = "bottom",
            facility_mode: str = "edge") -> None:
        specs.append({
            "name": name,
            "pattern": pattern,
            "angle": angle % 180.0,
            "topology": topology,
            "spine_ratio": spine_ratio,
            "shift_m": shift_m,
            "rth_side": rth_side,
            "psu_side": psu_side,
            "facility_mode": facility_mode,
        })

    if stage_id == "road_topology":
        add("Double-loaded parallel", "parallel", base)
        add("Double-loaded parallel silang", "parallel", base + 90)
        for ratio, label in ((0.22, "Single spine kiri"), (0.5, "Single spine tengah"), (0.78, "Single spine kanan")):
            add(label, "spine", base, "base", ratio)
            add(label + " + short branches", "spine", base, "short-branches", ratio)
        add("Perimeter-assisted kiri", "spine", base, "short-branches", 0.18, rth_side="left", psu_side="bottom")
        add("Perimeter-assisted kanan", "spine", base, "short-branches", 0.82, rth_side="right", psu_side="top")
        add("Hybrid grid", "spine", base, "hybrid", 0.5)
        add("Hybrid grid silang", "spine", base + 90, "hybrid", 0.5)
    elif stage_id == "block_spacing":
        d = float(req.lot_depth_m)
        shifts = (-0.50 * d, -0.25 * d, 0.25 * d, 0.50 * d)
        for angle, label in ((base, "utama"), (base + 90, "silang")):
            for shift in shifts:
                sign = "+" if shift >= 0 else ""
                add(f"Block phase {label} {sign}{shift:.2f}m", "parallel", angle, "base", 0.5, shift)
    elif stage_id == "orientation":
        offsets = (-15, -10, -8, -6, -4, -2, 2, 4, 6, 8, 10, 15)
        for off in offsets:
            add(f"Orientation {off:+d}° parallel", "parallel", base + off)
        for angle in _boundary_angles(buildable):
            add(f"Boundary aligned {angle:.1f}°", "parallel", angle)
            add(f"Boundary spine {angle:.1f}°", "spine", angle, "short-branches", 0.5)
    elif stage_id == "perimeter":
        for ratio, side in ((0.15, "kiri luar"), (0.25, "kiri"), (0.75, "kanan"), (0.85, "kanan luar")):
            add(f"Perimeter recovery {side}", "spine", base, "short-branches", ratio,
                rth_side="left" if ratio < 0.5 else "right",
                psu_side="bottom" if ratio < 0.5 else "top")
            add(f"Perimeter recovery silang {side}", "spine", base + 90, "short-branches", ratio,
                rth_side="bottom" if ratio < 0.5 else "top",
                psu_side="left" if ratio < 0.5 else "right")
    elif stage_id == "facility":
        combos = [
            ("top", "bottom"), ("bottom", "top"), ("left", "right"), ("right", "left"),
            ("top", "right"), ("left", "bottom"),
        ]
        for rth_side, psu_side in combos:
            add(f"Facility edge {rth_side}/{psu_side}", "parallel", base, "base", 0.5, 0.0, rth_side, psu_side, "edge")
        add("Facility low-yield parallel", "parallel", base, "base", 0.5, 0.0, "top", "bottom", "low-yield")
        add("Facility low-yield spine", "spine", base, "short-branches", 0.5, 0.0, "top", "bottom", "low-yield")
    return specs


def _road_specs_for(core: dict[str, Any], rot, req: Any, specdef: dict[str, Any]):
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
        else:  # hybrid: alternate full roads and short branches
            if h_index % 2 == 1:
                if (h_index // 2) % 2 == 0:
                    ss["line"] = LineString([(spine_x, y), (maxx + 5.0, y)])
                else:
                    ss["line"] = LineString([(minx - 5.0, y), (spine_x, y)])
        out.append(ss)
        h_index += 1
    return out


def _place_facilities(core: dict[str, Any], free, roads, parcel_area: float, req: Any, specdef: dict[str, Any]):
    target_rth = parcel_area * float(req.rth_pct) / 100.0
    target_psu = parcel_area * float(req.psu_pct) / 100.0
    mode = specdef.get("facility_mode", "edge")

    if mode == "low-yield":
        try:
            frontage_band = core["_polygonal_only"](roads.buffer(float(req.lot_depth_m) + float(req.lot_width_m) * 0.50))
            low_yield = core["_polygonal_only"](free.difference(frontage_band))
            if low_yield.area >= (target_rth + target_psu) * 0.98:
                rth = core["_take_target_area"](low_yield, target_rth)
                remaining_low = core["_polygonal_only"](low_yield.difference(rth))
                psu = core["_take_target_area"](remaining_low, target_psu)
                if rth.area >= target_rth * 0.97 and psu.area >= target_psu * 0.97:
                    return rth, psu
        except Exception:
            pass

    rth = core["_reserve_strip"](free, target_rth, specdef.get("rth_side", "top"))
    free2 = core["_polygonal_only"](free.difference(rth))
    psu = core["_reserve_strip"](free2, target_psu, specdef.get("psu_side", "bottom"))
    return rth, psu


def _evaluate_spec(core: dict[str, Any], req: Any, specdef: dict[str, Any], stage_id: str, serial: int) -> dict[str, Any] | None:
    geom_wgs = core["ensure_polygon"](req.geometry)
    epsg = core["utm_epsg_for_geometry"](geom_wgs)
    parcel = core["project_geom"](geom_wgs, 4326, epsg)
    buildable = core["_polygonal_only"](parcel.buffer(-req.setback_m, join_style=2))
    if buildable.is_empty:
        return None

    angle = float(specdef["angle"]) % 180.0
    origin = buildable.centroid
    rot = affinity.rotate(buildable, -angle, origin=origin, use_radians=False)
    specs = _road_specs_for(core, rot, req, specdef)
    roads, drainage, road_len, drain_len, road_segments_proj = core["_roads_from_specs"](rot, specs)
    free = core["_polygonal_only"](rot.difference(roads))
    rth, psu = _place_facilities(core, free, roads, float(parcel.area), req, specdef)
    developable = core["_polygonal_only"](free.difference(unary_union([g for g in (rth, psu) if g is not None and not g.is_empty])))

    roads_model = [
        {
            "id": f"R{j+1}",
            "kind": seg["kind"],
            "width_m": float(seg["width_m"]),
            "line": seg["centerline"],
            "corridor": seg["polygon"],
        }
        for j, seg in enumerate(road_segments_proj)
        if seg.get("centerline") is not None and not seg["centerline"].is_empty
    ]
    lots, lot_meta, block_info = core["_pack_standard_blocks"](
        developable,
        roads_model,
        float(req.lot_width_m),
        float(req.lot_depth_m),
        road_priority=roads_model,
    )
    standard_audit = core["_standard_geometry_audit"](lots, lot_meta, float(req.lot_width_m), float(req.lot_depth_m))
    lots_area = sum(float(x.area) for x in lots)
    lot_union = core["_polygonal_only"](unary_union(lots)) if lots else Polygon()
    occupied = core["_polygonal_only"](unary_union([g for g in (roads, rth, psu, lot_union) if g is not None and not g.is_empty]))
    residual = core["_polygonal_only"](rot.difference(occupied))

    parcel_area = float(parcel.area)
    road_pct = (float(roads.area) / parcel_area * 100.0) if parcel_area else 0.0
    residual_pct = (float(residual.area) / parcel_area * 100.0) if parcel_area else 0.0
    efficiency = (lots_area / parcel_area * 100.0) if parcel_area else 0.0
    actual_rth_pct = (float(rth.area) / parcel_area * 100.0) if parcel_area else 0.0
    actual_psu_pct = (float(psu.area) / parcel_area * 100.0) if parcel_area else 0.0
    network_quality = core["_road_network_quality"](roads_model, rot)
    block_reg = float(block_info.get("average_block_regularity", 0.0))

    validation = core["_final_siteplan_acceptance"](
        rot,
        roads_model,
        roads,
        lots,
        lot_meta,
        rth,
        psu,
        parcel_area,
        residual_pct,
        base_lot_count=len(lots),
        target_lot_width_m=float(req.lot_width_m),
        target_lot_depth_m=float(req.lot_depth_m),
    )

    road_segments = []
    for j, seg in enumerate(road_segments_proj):
        centerline = seg.get("centerline")
        polygon = seg.get("polygon")
        seg_drainage = seg.get("drainage")
        road_segments.append({
            "id": f"R{j+1}",
            "kind": seg.get("kind", "local"),
            "width_m": round(float(seg.get("width_m", 0.0)), 2),
            "angle_deg": round(float(angle + (0 if seg.get("axis") == "h" else 90)) % 180.0, 2),
            "length_m": round(float(seg.get("length_m", 0.0)), 2),
            "centerline": core["_mapping_wgs"](centerline, angle, origin, epsg) if centerline is not None and not centerline.is_empty else None,
            "polygon": core["_mapping_wgs"](polygon, angle, origin, epsg) if polygon is not None and not polygon.is_empty else None,
            "drainage": core["_mapping_wgs"](seg_drainage, angle, origin, epsg) if seg_drainage is not None and not seg_drainage.is_empty else None,
        })

    alt = {
        "id": f"REC-{stage_id}-{serial}",
        "name": specdef["name"],
        "pattern": specdef.get("pattern", "parallel"),
        "angle_deg": round(angle, 2),
        "buildable": core["_mapping_wgs"](rot, angle, origin, epsg),
        "roads": core["_mapping_wgs"](roads, angle, origin, epsg) if not roads.is_empty else None,
        "road_segments": road_segments,
        "rth": core["_mapping_wgs"](rth, angle, origin, epsg) if rth is not None and not rth.is_empty else None,
        "psu": core["_mapping_wgs"](psu, angle, origin, epsg) if psu is not None and not psu.is_empty else None,
        "reserve": None,
        "drainage": core["_mapping_wgs"](drainage, angle, origin, epsg) if drainage is not None and not drainage.is_empty else None,
        "lots": core["_list_mapping_wgs"](lots, angle, origin, epsg),
        "lot_details": core["_lot_detail_records"](lots, lot_meta),
        "parcelization": {
            "strategy": "recovery-road-block-standard-first",
            "standard_source": "geometry_settings",
            "adaptive_source": "residual_only",
            "solver_stage": stage_id,
            "solver_strategy": specdef["name"],
            **block_info,
            **standard_audit,
        },
        "residuals": [
            {
                "id": f"REC-RES-{k+1}",
                "area_m2": round(float(g.area), 2),
                "classification": "small_residual" if g.area < float(req.lot_width_m) * float(req.lot_depth_m) else "potential_lot",
                "geometry": core["_mapping_wgs"](g, angle, origin, epsg),
            }
            for k, g in enumerate(sorted(core["_poly_parts"](residual), key=lambda x: x.area, reverse=True)[:80])
        ],
        "stats": {
            "lot_count": len(lots),
            "standard_lot_count": len(lots),
            "adaptive_lot_count": 0,
            "invalid_standard_lot_count": int(standard_audit.get("invalid_standard_lot_count", 0)),
            "block_count": int(block_info.get("block_count", 0)),
            "active_block_count": int(block_info.get("active_block_count", 0)),
            "average_block_regularity": block_reg,
            "irregular_block_count": int(block_info.get("irregular_block_count", 0)),
            "double_loaded_block_count": int(block_info.get("double_loaded_block_count", 0)),
            "road_intersection_count": int(network_quality.get("intersection_count", 0)),
            "internal_dead_end_count": int(network_quality.get("internal_dead_end_count", 0)),
            "road_connectivity_score": float(network_quality.get("connectivity_score", 0.0)),
            "lots_total_area_m2": round(lots_area, 2),
            "standard_lot_area_m2": round(lots_area, 2),
            "adaptive_lot_area_m2": 0.0,
            "lot_efficiency_pct": round(efficiency, 2),
            "lot_efficiency_target_pct": TARGET_EFFICIENCY_PCT,
            "lot_efficiency_met": bool(efficiency + 1e-9 >= TARGET_EFFICIENCY_PCT),
            "road_area_m2": round(float(roads.area), 2),
            "road_pct": round(road_pct, 2),
            "road_length_m": round(float(road_len), 2),
            "rth_area_m2": round(float(rth.area), 2),
            "rth_pct": round(actual_rth_pct, 2),
            "psu_area_m2": round(float(psu.area), 2),
            "psu_pct": round(actual_psu_pct, 2),
            "reserve_area_m2": 0.0,
            "reserve_pct": 0.0,
            "drainage_length_m": round(float(drain_len), 2),
            "unused_area_m2": round(float(residual.area), 2),
            "residual_true_area_m2": round(float(residual.area), 2),
            "residual_true_pct": round(residual_pct, 2),
            "residual_pct_total_land": round(residual_pct, 2),
            "land_utilization_pct": round((lots_area / max(float(buildable.area) - float(roads.area) - float(rth.area) - float(psu.area), 1e-9)) * 100.0, 2),
            "road_efficiency": round((lots_area / float(roads.area)) if roads.area else 0.0, 3),
            "optimized": False,
            "manual_adjusted": False,
            "validation_passed": bool(validation.get("valid")),
        },
        "validation": validation,
        "solver_stage": stage_id,
        "solver_strategy": specdef["name"],
    }
    return alt


def _generate_recovery_batch(core: dict[str, Any], req: Any, stage_id: str, job_id: str) -> list[dict[str, Any]]:
    specs = _stage_specs(core, req, stage_id)
    _mark_stage(job_id, stage_id, "running", f"Menguji {len(specs)} candidate nyata", total=len(specs))
    out: list[dict[str, Any]] = []
    for i, specdef in enumerate(specs, 1):
        try:
            alt = _evaluate_spec(core, req, specdef, stage_id, i)
        except Exception as exc:
            def failed(job: dict[str, Any], i=i, specdef=specdef, exc=exc) -> None:
                stage = _stage_ref(job, stage_id)
                stage["candidates_tested"] = i
                stage["current_candidate"] = i
                stage["current_strategy"] = specdef.get("name")
                stage["message"] = f"Candidate error: {exc}"
            _with_job(job_id, failed)
            continue
        if alt is None:
            continue
        out.append(alt)
        _record_candidate(job_id, stage_id, alt, i, len(specs), specdef.get("name"))
    best = max([float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)) for a in out] or [0.0])
    _mark_stage(job_id, stage_id, "completed", f"{len(out)} candidate selesai • best {best:.2f}%", total=len(specs))
    return out


def _adaptive_candidate(core: dict[str, Any], req: Any, alt: dict[str, Any], serial: int) -> dict[str, Any] | None:
    road_segments = []
    for seg in alt.get("road_segments") or []:
        if not seg.get("centerline"):
            continue
        road_segments.append({
            "id": seg.get("id"),
            "kind": seg.get("kind", "local"),
            "width_m": float(seg.get("width_m", req.local_road_width_m)),
            "centerline": seg["centerline"],
        })
    yreq = core["YieldOptimizeRequest"](
        parcel=req.geometry,
        buildable=alt["buildable"],
        road_segments=road_segments,
        lots=alt.get("lots") or [],
        lot_details=alt.get("lot_details") or [],
        rth=alt.get("rth"),
        psu=alt.get("psu"),
        target_lot_width_m=float(req.lot_width_m),
        target_lot_depth_m=float(req.lot_depth_m),
        rth_pct=float(req.rth_pct),
        psu_pct=float(req.psu_pct),
        local_road_width_m=float(req.local_road_width_m),
    )
    result = core["optimize_land_utilization"](yreq)
    result["id"] = f"REC-adaptive-{serial}"
    result["name"] = f"Adaptive recovery • {alt.get('name', serial)}"
    result["pattern"] = alt.get("pattern")
    result["angle_deg"] = alt.get("angle_deg", 0.0)
    result["solver_stage"] = "adaptive"
    result["solver_strategy"] = "Residual → saleable Adaptive"
    result["recommended"] = False
    result["rank"] = 0
    result.setdefault("stats", {})["average_block_regularity"] = float((alt.get("stats") or {}).get("average_block_regularity", 0.0))
    result["stats"]["road_connectivity_score"] = float((alt.get("stats") or {}).get("road_connectivity_score", 0.0))
    return result


def _run_adaptive_stage(core: dict[str, Any], req: Any, pool: list[dict[str, Any]], job_id: str) -> list[dict[str, Any]]:
    if not bool(getattr(req, "land_optimization_enabled", False)):
        _mark_stage(job_id, "adaptive", "skipped", "Adaptive recovery OFF pada UI")
        return []

    seeds = sorted(
        [a for a in pool if float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)) < TARGET_EFFICIENCY_PCT],
        key=lambda a: float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)),
        reverse=True,
    )[:6]
    _mark_stage(job_id, "adaptive", "running", f"Menguji residual saleable pada {len(seeds)} candidate terbaik", total=len(seeds))
    out = []
    for i, seed in enumerate(seeds, 1):
        try:
            candidate = _adaptive_candidate(core, req, seed, i)
        except Exception as exc:
            def failed(job: dict[str, Any], i=i, exc=exc) -> None:
                stage = _stage_ref(job, "adaptive")
                stage["candidates_tested"] = i
                stage["current_candidate"] = i
                stage["message"] = f"Adaptive candidate error: {exc}"
            _with_job(job_id, failed)
            continue
        if candidate is None:
            continue
        out.append(candidate)
        _record_candidate(job_id, "adaptive", candidate, i, len(seeds), "Residual → saleable Adaptive")
    best = max([float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)) for a in out] or [0.0])
    _mark_stage(job_id, "adaptive", "completed", f"{len(out)} adaptive candidate selesai • best {best:.2f}%", total=len(seeds))
    return out


def _initial_batch(core: dict[str, Any], req: Any, job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _mark_stage(job_id, "initial", "running", "Menjalankan baseline M2.5.12", total=8)
    initial_req = req.model_copy(deep=True) if hasattr(req, "model_copy") else copy.deepcopy(req)
    initial_req.alternative_count = 8
    result = core["generate_site_alternatives"](initial_req)
    alts = result.get("alternatives") or []
    for i, alt in enumerate(alts, 1):
        _record_candidate(job_id, "initial", alt, i, len(alts), alt.get("name"))
    best = max([float((a.get("stats") or {}).get("lot_efficiency_pct", 0.0)) for a in alts] or [0.0])
    _mark_stage(job_id, "initial", "completed", f"Baseline selesai • best {best:.2f}%", total=len(alts))
    return result, alts


def _finish_success(job_id: str, req: Any, base_result: dict[str, Any], pool: list[dict[str, Any]], after_stage: str) -> None:
    valid = strict_valid_alternatives(pool)
    valid = valid[: int(req.alternative_count)]
    _skip_remaining(job_id, after_stage, "Target >=70% sudah ditemukan")

    result = {
        "parcel": base_result.get("parcel"),
        "parcel_stats": base_result.get("parcel_stats"),
        "settings": req.model_dump() if hasattr(req, "model_dump") else {},
        "alternatives": valid,
        "notice": "M2.5.13 Recovery Solver: hanya candidate >=70% dan final geometry validation PASS yang masuk Alternatif Layout.",
    }

    def update(job: dict[str, Any]) -> None:
        job["status"] = "completed"
        job["active_stage"] = None
        job["result"] = result
        job["valid_count"] = len(valid)
        job["message"] = f"Target tercapai. {len(valid)} valid alternative >=70%."
    _with_job(job_id, update)


def _run_recovery_solver(core: dict[str, Any], req: Any, job_id: str) -> None:
    try:
        def start(job: dict[str, Any]) -> None:
            job["status"] = "running"
            job["message"] = "Recovery Solver dimulai"
        _with_job(job_id, start)

        base_result, initial = _initial_batch(core, req, job_id)
        pool: list[dict[str, Any]] = list(initial)
        if strict_valid_alternatives(pool):
            _finish_success(job_id, req, base_result, pool, "initial")
            return

        for stage_id in ("road_topology", "block_spacing", "orientation", "perimeter", "facility"):
            batch = _generate_recovery_batch(core, req, stage_id, job_id)
            pool.extend(batch)
            if strict_valid_alternatives(pool):
                _finish_success(job_id, req, base_result, pool, stage_id)
                return

        adaptive = _run_adaptive_stage(core, req, pool, job_id)
        pool.extend(adaptive)
        if strict_valid_alternatives(pool):
            _finish_success(job_id, req, base_result, pool, "adaptive")
            return

        _mark_stage(job_id, "feasibility", "running", "Menghitung conservative feasibility upper bound", total=1)
        with _JOBS_LOCK:
            history = copy.deepcopy((_JOBS.get(job_id) or {}).get("search_history") or [])
        diagnosis = feasibility_diagnosis(core, req, history)
        _mark_stage(job_id, "feasibility", "completed", diagnosis["message"], total=1)

        result = {
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
    except Exception as exc:
        tb = traceback.format_exc(limit=8)
        def fail(job: dict[str, Any]) -> None:
            job["status"] = "failed"
            job["active_stage"] = None
            job["message"] = str(exc)
            job["error"] = str(exc)
            job["traceback"] = tb
        _with_job(job_id, fail)


def register_recovery_solver(app: Any, core: dict[str, Any]) -> None:
    """Register M2.5.13 async/polling Recovery Solver endpoints."""

    @app.post("/site-plan/solver/start")
    def start_solver(payload: dict[str, Any]):
        req = core["SitePlanRequest"].model_validate(payload)
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        job = {
            "job_id": job_id,
            "status": "queued",
            "target_efficiency_pct": TARGET_EFFICIENCY_PCT,
            "active_stage": None,
            "stages": stage_definitions(),
            "best_seen": None,
            "current_candidate": None,
            "valid_count": 0,
            "search_history": [],
            "feasibility": None,
            "result": None,
            "message": "Antri",
            "created_at": now,
            "updated_at": now,
        }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
            # Keep memory bounded in long-running local sessions.
            if len(_JOBS) > 24:
                old_ids = sorted(_JOBS, key=lambda k: _JOBS[k].get("updated_at", 0.0))[:-20]
                for oid in old_ids:
                    _JOBS.pop(oid, None)

        thread = threading.Thread(target=_run_recovery_solver, args=(core, req, job_id), daemon=True, name=f"recovery-{job_id}")
        thread.start()
        return {
            "job_id": job_id,
            "status": "queued",
            "target_efficiency_pct": TARGET_EFFICIENCY_PCT,
        }

    @app.get("/site-plan/solver/status/{job_id}")
    def solver_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                core["HTTPException"](404, "Solver job tidak ditemukan")
                raise core["HTTPException"](404, "Solver job tidak ditemukan")
            return copy.deepcopy(job)
