from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ezdxf
import geopandas as gpd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pyproj import CRS, Transformer
from shapely import affinity, set_precision
from shapely.geometry import Polygon, MultiPolygon, LineString, GeometryCollection, Point, box, mapping, shape
from shapely.ops import transform, unary_union, nearest_points
from shapely.validation import make_valid
from shapely.strtree import STRtree
from shapely.errors import GEOSException

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./development_os.db")

app = FastAPI(title="Development OS Geometry API", version="0.7.11")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/", include_in_schema=False)
def web_index():
    return FileResponse(WEB_DIR / "index.html")


# -----------------------------
# Models
# -----------------------------
class GeometryRequest(BaseModel):
    geometry: dict[str, Any]


class AnalyzeRequest(BaseModel):
    geometry: dict[str, Any]
    setback_m: float = Field(default=3.0, ge=0, le=100)
    lot_width_m: float = Field(default=8.0, gt=0, le=200)
    lot_depth_m: float = Field(default=15.0, gt=0, le=300)
    angle_step_deg: int = Field(default=10, ge=1, le=45)


class SitePlanRequest(BaseModel):
    geometry: dict[str, Any]
    setback_m: float = Field(default=3.0, ge=0, le=100)
    lot_width_m: float = Field(default=8.0, gt=0, le=200)
    lot_depth_m: float = Field(default=15.0, gt=0, le=300)
    main_road_width_m: float = Field(default=8.0, ge=4, le=30)
    local_road_width_m: float = Field(default=6.0, ge=3, le=20)
    rth_pct: float = Field(default=10.0, ge=0, le=50)
    psu_pct: float = Field(default=5.0, ge=0, le=30)
    alternative_count: int = Field(default=4, ge=2, le=8)
    max_residual_pct_total: float = Field(default=3.0, gt=0, le=20)
    land_optimization_enabled: bool = False


class CoordinatesRequest(BaseModel):
    text: str
    epsg: int = 4326
    order: str = "latlon"  # latlon for EPSG:4326, xy otherwise


class RoadSegmentInput(BaseModel):
    id: str | None = None
    kind: str = "local"
    width_m: float = Field(default=6.0, ge=2.0, le=40.0)
    centerline: dict[str, Any]


class RoadRebuildRequest(BaseModel):
    buildable: dict[str, Any] | None = None
    segments: list[RoadSegmentInput] = Field(default_factory=list)


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parcel: dict[str, Any]
    buildable: dict[str, Any] | None = None
    lots: list[dict[str, Any]] = Field(default_factory=list)
    layout: dict[str, Any] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)


class SitePlanRecalculateRequest(BaseModel):
    parcel: dict[str, Any]
    buildable: dict[str, Any]
    roads: dict[str, Any] | None = None
    rth: dict[str, Any] | None = None
    psu: dict[str, Any] | None = None
    reserve: dict[str, Any] | None = None
    drainage: dict[str, Any] | None = None
    lots: list[dict[str, Any]] = Field(default_factory=list)
    previous_stats: dict[str, Any] = Field(default_factory=dict)
    land_optimization_enabled: bool = False


class SmartReflowRequest(BaseModel):
    parcel: dict[str, Any]
    buildable: dict[str, Any]
    road_segments: list[RoadSegmentInput] = Field(default_factory=list)
    lots: list[dict[str, Any]] = Field(default_factory=list)
    rth: dict[str, Any] | None = None
    psu: dict[str, Any] | None = None
    edited_road_ids: list[str] = Field(default_factory=list)
    edited_lot_indices: list[int] = Field(default_factory=list)
    edited_special_types: list[str] = Field(default_factory=list)
    lot_width_m: float = Field(default=8.0, gt=0, le=200)
    lot_depth_m: float = Field(default=15.0, gt=0, le=300)
    frontage_tolerance_m: float = Field(default=1.5, ge=0.1, le=10)
    reflow_radius_m: float = Field(default=28.0, ge=2, le=100)


class EditorValidateRequest(BaseModel):
    parcel: dict[str, Any]
    buildable: dict[str, Any]
    road_segments: list[RoadSegmentInput] = Field(default_factory=list)
    lots: list[dict[str, Any]] = Field(default_factory=list)
    rth: dict[str, Any] | None = None
    psu: dict[str, Any] | None = None
    frontage_tolerance_m: float = Field(default=1.5, ge=0.1, le=10)


# -----------------------------
# DB helpers: SQLite dev fallback, PostGIS in Docker/production
# -----------------------------
def is_postgres() -> bool:
    return DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")


def _sqlite_path() -> str:
    if not DATABASE_URL.startswith("sqlite:///"):
        return "./development_os.db"
    return DATABASE_URL.replace("sqlite:///", "", 1)


def init_db() -> None:
    if is_postgres():
        try:
            import psycopg
        except ImportError as e:
            raise RuntimeError("psycopg is required for PostgreSQL/PostGIS") from e
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        id BIGSERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        parcel_geom geometry(Geometry, 4326) NOT NULL,
                        parcel_geojson JSONB NOT NULL,
                        buildable_geojson JSONB,
                        lots_geojson JSONB,
                        layout_geojson JSONB,
                        settings JSONB,
                        stats JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS layout_geojson JSONB")
            conn.commit()
    else:
        con = sqlite3.connect(_sqlite_path())
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parcel_geojson TEXT NOT NULL,
                buildable_geojson TEXT,
                lots_geojson TEXT,
                layout_geojson TEXT,
                settings TEXT,
                stats TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cols = {row[1] for row in con.execute("PRAGMA table_info(projects)").fetchall()}
        if "layout_geojson" not in cols:
            con.execute("ALTER TABLE projects ADD COLUMN layout_geojson TEXT")
        con.commit()
        con.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# -----------------------------
# Geometry helpers
# -----------------------------
def ensure_polygon(geometry: dict[str, Any]) -> Polygon | MultiPolygon:
    try:
        geom = shape(geometry)
    except Exception as e:
        raise HTTPException(422, f"Invalid GeoJSON geometry: {e}")
    if geom.is_empty:
        raise HTTPException(422, "Geometry is empty")
    geom = make_valid(geom)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(422, f"Expected Polygon/MultiPolygon, got {geom.geom_type}")
    if not geom.is_valid:
        raise HTTPException(422, "Geometry is invalid and could not be repaired")
    return geom


def utm_epsg_for_geometry(geom: Polygon | MultiPolygon) -> int:
    c = geom.centroid
    lon, lat = c.x, c.y
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def project_geom(geom, src_epsg: int, dst_epsg: int):
    transformer = Transformer.from_crs(CRS.from_epsg(src_epsg), CRS.from_epsg(dst_epsg), always_xy=True)
    return transform(transformer.transform, geom)


def to_wgs84(geom, src_epsg: int):
    return project_geom(geom, src_epsg, 4326)

def _safe_wgs84_polygonal(geom, src_epsg: int):
    """Transform polygonal geometry to WGS84 and repair rare projection-induced ring invalidity."""
    w=to_wgs84(geom,src_epsg)
    if not w.is_valid:
        w=make_valid(w)
    return _polygonal_only(w)


def geometry_stats_wgs84(geom: Polygon | MultiPolygon) -> dict[str, Any]:
    epsg = utm_epsg_for_geometry(geom)
    projected = project_geom(geom, 4326, epsg)
    minx, miny, maxx, maxy = projected.bounds
    return {
        "area_m2": round(projected.area, 2),
        "perimeter_m": round(projected.length, 2),
        "utm_epsg": epsg,
        "bbox_projected": [round(minx, 2), round(miny, 2), round(maxx, 2), round(maxy, 2)],
        "centroid": {"lat": round(geom.centroid.y, 7), "lon": round(geom.centroid.x, 7)},
    }


def generate_lots(buildable_proj, width: float, depth: float, angle_step: int = 10):
    if buildable_proj.is_empty:
        return [], 0.0

    center = buildable_proj.centroid
    best: list[Polygon] = []
    best_angle = 0.0

    # Search orientation + small grid offsets. This is intentionally deterministic and simple for M1.
    for angle in range(0, 180, angle_step):
        rot = affinity.rotate(buildable_proj, -angle, origin=center, use_radians=False)
        minx, miny, maxx, maxy = rot.bounds
        for ox in (0.0, width / 2.0):
            for oy in (0.0, depth / 2.0):
                lots: list[Polygon] = []
                x = minx + ox
                while x + width <= maxx + 1e-9:
                    y = miny + oy
                    while y + depth <= maxy + 1e-9:
                        candidate = box(x, y, x + width, y + depth)
                        # Covers allows boundary contact while preventing spill-out.
                        if rot.covers(candidate):
                            lots.append(candidate)
                        y += depth
                    x += width
                if len(lots) > len(best):
                    best = lots
                    best_angle = float(angle)

    if best_angle:
        best = [affinity.rotate(lot, best_angle, origin=center, use_radians=False) for lot in best]
    return best, best_angle


def analyze_geometry(req: AnalyzeRequest) -> dict[str, Any]:
    geom_wgs = ensure_polygon(req.geometry)
    epsg = utm_epsg_for_geometry(geom_wgs)
    geom_proj = project_geom(geom_wgs, 4326, epsg)
    buildable = geom_proj.buffer(-req.setback_m, join_style=2)
    if buildable.is_empty:
        raise HTTPException(422, "Setback is too large; buildable area is empty")
    buildable = make_valid(buildable)

    lots, angle = generate_lots(buildable, req.lot_width_m, req.lot_depth_m, req.angle_step_deg)
    buildable_wgs = to_wgs84(buildable, epsg)
    lots_wgs = [to_wgs84(lot, epsg) for lot in lots]

    parcel_area = geom_proj.area
    buildable_area = buildable.area
    lots_area = sum(l.area for l in lots)

    return {
        "parcel": mapping(geom_wgs),
        "buildable": mapping(buildable_wgs),
        "lots": [mapping(l) for l in lots_wgs],
        "stats": {
            "parcel_area_m2": round(parcel_area, 2),
            "parcel_perimeter_m": round(geom_proj.length, 2),
            "buildable_area_m2": round(buildable_area, 2),
            "buildable_pct": round((buildable_area / parcel_area) * 100, 2) if parcel_area else 0,
            "lot_count": len(lots),
            "lot_area_each_m2": round(req.lot_width_m * req.lot_depth_m, 2),
            "lots_total_area_m2": round(lots_area, 2),
            "lot_efficiency_pct_of_parcel": round((lots_area / parcel_area) * 100, 2) if parcel_area else 0,
            "best_orientation_deg": angle,
            "utm_epsg": epsg,
        },
    }


# -----------------------------
# Milestone 2: heuristic site planning
# -----------------------------
def _polygonal_only(geom):
    if geom is None or geom.is_empty:
        return Polygon()
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        return make_valid(unary_union(polys)) if polys else Polygon()
    return Polygon()


def _safe_polygon_overlay(a, b, operation="intersection", grid_size=0.001):
    """Robust polygon overlay for numerically fragile/invalid GIS geometry.

    Shapely/GEOS may raise TopologyException even after upstream operations
    create geometries that look valid.  Repair both operands and retry on a
    millimetre precision grid before allowing the exception to escape.
    """
    if a is None or b is None or a.is_empty or b.is_empty:
        return Polygon()

    def _repair(g):
        try:
            g = make_valid(g)
        except Exception:
            pass
        try:
            g = set_precision(g, grid_size, mode="valid_output")
        except Exception:
            try:
                g = g.buffer(0)
            except Exception:
                pass
        return g

    left, right = a, b
    for attempt in range(3):
        try:
            if operation == "intersection":
                return _polygonal_only(left.intersection(right))
            if operation == "difference":
                return _polygonal_only(left.difference(right))
            raise ValueError(f"Unsupported overlay operation: {operation}")
        except GEOSException:
            left, right = _repair(left), _repair(right)
            if left.is_empty or right.is_empty:
                return Polygon()
            if attempt == 1:
                # Last-resort cleanup for side-location conflicts caused by
                # coincident/sliver boundaries.
                try:
                    left = left.buffer(0)
                    right = right.buffer(0)
                except Exception:
                    pass
    # If GEOS still cannot overlay this tiny fragment, skip the fragment
    # instead of crashing the whole optimization request.
    return Polygon()


def _reserve_strip(area_geom, target_area: float, side: str):
    """Reserve a contiguous edge strip with approximately target_area."""
    area_geom = _polygonal_only(area_geom)
    if area_geom.is_empty or target_area <= 0:
        return Polygon()
    target_area = min(target_area, area_geom.area * 0.85)
    minx, miny, maxx, maxy = area_geom.bounds
    width, height = maxx - minx, maxy - miny
    vertical = side in ("top", "bottom")
    full = height if vertical else width
    lo, hi = 0.0, full
    best = Polygon()
    for _ in range(36):
        t = (lo + hi) / 2
        if side == "top":
            cutter = box(minx - 1, maxy - t, maxx + 1, maxy + 1)
        elif side == "bottom":
            cutter = box(minx - 1, miny - 1, maxx + 1, miny + t)
        elif side == "left":
            cutter = box(minx - 1, miny - 1, minx + t, maxy + 1)
        else:
            cutter = box(maxx - t, miny - 1, maxx + 1, maxy + 1)
        candidate = _polygonal_only(area_geom.intersection(cutter))
        best = candidate
        if candidate.area < target_area:
            lo = t
        else:
            hi = t
    return best


def _dominant_angle_deg(geom) -> float:
    rect = geom.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    edges = []
    for a, b in zip(coords, coords[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length > 1e-9:
            edges.append((length, math.degrees(math.atan2(dy, dx)) % 180))
    if not edges:
        return 0.0
    return round(max(edges, key=lambda x: x[0])[1], 2)


def _road_specs(rot, pattern: str, lot_depth: float, main_w: float, local_w: float, spine_ratio: float = 0.5):
    """Create road centerlines from the lot module, not the other way around.

    M2.5.11 block-first rule: the clear distance between two parallel road
    corridors is exactly ``2 * lot_depth`` whenever the site can accommodate
    it.  That gives one standard row on each frontage without shrinking lots.
    A main road may be wider than a local road; centerline spacing therefore
    uses the actual half-widths of the two neighboring roads.
    """
    minx, miny, maxx, maxy = rot.bounds
    cx, cy = rot.centroid.x, rot.centroid.y
    spine_x = minx + max(0.15, min(0.85, float(spine_ratio))) * (maxx - minx)
    specs = []

    def add_h(y, width, kind):
        specs.append({"axis": "h", "coord": y, "width": width, "kind": kind,
                      "line": LineString([(minx - 5, y), (maxx + 5, y)])})

    def add_v(x, width, kind):
        specs.append({"axis": "v", "coord": x, "width": width, "kind": kind,
                      "line": LineString([(x, miny - 5), (x, maxy + 5)])})

    def sequence(lo: float, hi: float, include_main: bool):
        span = max(0.0, hi - lo)
        best = None
        # n roads consume: n road widths + n double-loaded lot depths.
        for n in range(1, 40):
            main_i = n // 2 if include_main else -1
            widths = [main_w if i == main_i else local_w for i in range(n)]
            required = 2.0 * lot_depth * n + sum(widths)
            if required <= span + 1e-9:
                best = (n, main_i, widths, required)
            else:
                break
        if best is None:
            return []
        n, main_i, widths, required = best
        # Keep unavoidable remainder at the site edges, not between standard rows.
        edge_remainder = (span - required) / 2.0
        cursor = lo + edge_remainder + lot_depth
        out = []
        for i, width in enumerate(widths):
            center = cursor + width / 2.0
            out.append((center, width, "main" if i == main_i else "local"))
            cursor = center + width / 2.0 + 2.0 * lot_depth
        return out

    if pattern == "parallel":
        rows = sequence(miny, maxy, True)
        if not rows:
            add_h(cy, main_w, "main")
        else:
            for y, width, kind in rows:
                add_h(y, width, kind)
    elif pattern == "spine":
        add_v(spine_x, main_w, "main")
        rows = sequence(miny, maxy, False)
        if not rows:
            add_h(cy, local_w, "local")
        else:
            for y, width, _ in rows:
                add_h(y, width, "local")
    else:  # cross-grid
        add_v(spine_x, main_w, "main")
        rows = sequence(miny, maxy, True)
        if not rows:
            add_h(cy, main_w, "main")
        else:
            for y, width, kind in rows:
                add_h(y, width, kind)
    return specs


def _roads_from_specs(rot, specs):
    road_parts = []
    drainage_parts = []
    segments = []
    road_length = 0.0
    drainage_length = 0.0
    for spec in specs:
        line = spec["line"].intersection(rot)
        if line.is_empty:
            continue
        road_length += line.length
        corridor = _polygonal_only(line.buffer(spec["width"] / 2, cap_style=2, join_style=2).intersection(rot))
        if not corridor.is_empty:
            road_parts.append(corridor)
        seg_drains = []
        if spec["axis"] == "h":
            offsets = [affinity.translate(spec["line"], yoff=d) for d in (-spec["width"] / 2, spec["width"] / 2)]
        else:
            offsets = [affinity.translate(spec["line"], xoff=d) for d in (-spec["width"] / 2, spec["width"] / 2)]
        for edge in offsets:
            dl = edge.intersection(rot)
            if not dl.is_empty:
                drainage_parts.append(dl)
                seg_drains.append(dl)
                drainage_length += dl.length
        segments.append({
            "kind": spec.get("kind", "local"),
            "width_m": float(spec["width"]),
            "axis": spec.get("axis", "h"),
            "centerline": line,
            "polygon": corridor,
            "drainage": unary_union(seg_drains) if seg_drains else GeometryCollection(),
            "length_m": float(line.length),
        })
    roads = _polygonal_only(unary_union(road_parts)) if road_parts else Polygon()
    drainage = unary_union(drainage_parts) if drainage_parts else GeometryCollection()
    return roads, drainage, road_length, drainage_length, segments


def _lots_along_roads(developable, specs, lot_w: float, lot_d: float, offset_seed: int = 0):
    if developable.is_empty:
        return []
    minx, miny, maxx, maxy = developable.bounds
    accepted = []
    # two offsets create meaningful alternative packing without randomness
    offset = (lot_w / 2) if offset_seed % 2 else 0.0

    def accept(candidate):
        if not developable.covers(candidate):
            return
        # avoid duplicates where main and local roads meet
        for old in accepted:
            if old.intersection(candidate).area > 0.05:
                return
        accepted.append(candidate)

    for spec in specs:
        w = spec["width"]
        if spec["axis"] == "h":
            y = spec["coord"]
            x = minx + offset
            while x + lot_w <= maxx + 1e-9:
                accept(box(x, y + w / 2, x + lot_w, y + w / 2 + lot_d))
                accept(box(x, y - w / 2 - lot_d, x + lot_w, y - w / 2))
                x += lot_w
        else:
            x = spec["coord"]
            y = miny + offset
            while y + lot_w <= maxy + 1e-9:
                accept(box(x + w / 2, y, x + w / 2 + lot_d, y + lot_w))
                accept(box(x - w / 2 - lot_d, y, x - w / 2, y + lot_w))
                y += lot_w
    return accepted




def _angle_distance_180(a: float, b: float) -> float:
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def _standard_row_candidates(block, road, lot_w: float, lot_d: float):
    """Pack one contiguous run of exact standard lots along one road frontage.

    A run never distributes remainder between lots.  We try a few frontage
    phases only to absorb floating-point / irregular-boundary effects, then
    keep the longest contiguous exact-module run.  Every accepted polygon is
    exactly ``lot_w x lot_d`` in the road's local frame.
    """
    fr = _road_frame(road.get("line", LineString()))
    if not fr or block is None or block.is_empty:
        return []
    corridor = road.get("corridor")
    if corridor is not None and not corridor.is_empty and block.distance(corridor) > 0.50:
        return []

    rp = block.representative_point()
    vx, vy = rp.x - fr["a"][0], rp.y - fr["a"][1]
    side_sign = 1 if vx * fr["nx"] + vy * fr["ny"] >= 0 else -1
    normal = float(road.get("width_m", 0.0)) / 2.0 + lot_d / 2.0

    # Project the block boundary to the frontage axis.  This bounds where a
    # complete row can possibly exist without creating artificial interior gaps.
    coords = []
    for poly in _poly_parts(block):
        if hasattr(poly, "exterior"):
            coords.extend(list(poly.exterior.coords))
    if not coords:
        return []
    ts = [
        (x - fr["a"][0]) * fr["ux"] + (y - fr["a"][1]) * fr["uy"]
        for x, y in coords
    ]
    tmin = max(0.0, min(ts))
    tmax = min(fr["length"], max(ts))
    if tmax - tmin < lot_w - 1e-6:
        return []

    best_run = []
    # Remainder may sit at either end of a real block. Quarter-module probes
    # avoid losing a complete row because of a small taper at one block end.
    phases = (0.0, lot_w * 0.25, lot_w * 0.50, lot_w * 0.75)
    for phase in phases:
        valid = []
        t = tmin + phase + lot_w / 2.0
        while t + lot_w / 2.0 <= tmax + 1e-9:
            cx = fr["a"][0] + fr["ux"] * t + fr["nx"] * side_sign * normal
            cy = fr["a"][1] + fr["uy"] * t + fr["ny"] * side_sign * normal
            cand = _rect_centered(cx, cy, lot_w, lot_d, fr["angle"])
            if block.buffer(0.002).covers(cand):
                valid.append((t, cand))
            else:
                valid.append((t, None))
            t += lot_w

        # Do not jump over an invalid slot inside a row.  That would recreate
        # the exact "orange strip between standard lots" failure from M2.5.8.
        current = []
        runs = []
        for t, cand in valid:
            if cand is None:
                if current:
                    runs.append(current)
                    current = []
                continue
            current.append((t, cand))
        if current:
            runs.append(current)
        if not runs:
            continue
        run = max(runs, key=lambda x: (len(x), -x[0][0]))
        if len(run) > len(best_run):
            best_run = run

    return [g for _, g in best_run]


def _block_quality_metrics(block, adjacent_count: int, standard_count: int, lot_w: float, lot_d: float):
    """Compact block-quality metrics used by the M2.5.11 masterplan scorer."""
    if block is None or block.is_empty:
        return {"regularity":0.0,"short_m":0.0,"long_m":0.0,"block_type":"EMPTY","standard_density":0.0}
    rect = block.minimum_rotated_rectangle
    dims=[]
    if rect is not None and not rect.is_empty and hasattr(rect,'exterior'):
        cs=list(rect.exterior.coords)
        dims=sorted([math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(cs,cs[1:]) if math.hypot(b[0]-a[0],b[1]-a[1])>0.01])
    short=float(dims[0]) if dims else 0.0
    long=float(dims[-1]) if dims else 0.0
    rect_area=max(float(rect.area) if rect is not None and not rect.is_empty else 0.0,1e-9)
    regularity=max(0.0,min(1.0,float(block.area)/rect_area))
    if regularity < 0.76:
        btype='IRREGULAR_BLOCK'
    elif adjacent_count >= 2 and short >= (2.0*lot_d-0.25):
        btype='DOUBLE_LOADED'
    elif adjacent_count >= 1:
        btype='SINGLE_LOADED'
    else:
        btype='EDGE_BLOCK'
    standard_density=(standard_count*lot_w*lot_d/max(float(block.area),1e-9))*100.0
    possible_rows = 2 if (adjacent_count >= 2 and short >= 2.0*lot_d-0.25) else (1 if adjacent_count >= 1 and short >= lot_d-0.25 else 0)
    predicted_capacity = int(max(0, math.floor(long/max(lot_w,1e-9))) * possible_rows)
    capture_pct = (standard_count/max(predicted_capacity,1))*100.0 if predicted_capacity else 0.0
    return {
        'regularity':round(regularity,4),'short_m':round(short,2),'long_m':round(long,2),
        'block_type':btype,'standard_density':round(standard_density,2),'frontage_road_count':int(adjacent_count),
        'predicted_standard_capacity':predicted_capacity,'capacity_capture_pct':round(capture_pct,2),
    }


def _road_network_quality(roads, buildable):
    """Estimate connectivity without traffic simulation. Internal dead ends are penalized."""
    if not roads:
        return {'intersection_count':0,'internal_dead_end_count':0,'connectivity_score':0.0}
    lines=[r.get('line') for r in roads if r.get('line') is not None and not r.get('line').is_empty]
    intersections=set()
    for i,a in enumerate(lines):
        for b in lines[i+1:]:
            try:
                inter=a.intersection(b)
            except Exception:
                continue
            if inter.is_empty:
                continue
            for pt in ([inter] if inter.geom_type=='Point' else list(getattr(inter,'geoms',[]))):
                if getattr(pt,'geom_type',None)=='Point':
                    intersections.add((round(pt.x,2),round(pt.y,2)))
    dead=0
    boundary=buildable.boundary if buildable is not None and not buildable.is_empty else None
    for i,line in enumerate(lines):
        cs=list(line.coords) if line.geom_type=='LineString' else []
        if len(cs)<2: continue
        for xy in (cs[0],cs[-1]):
            p=Point(xy)
            if boundary is not None and p.distance(boundary)<=0.75:
                continue
            connected=False
            for j,other in enumerate(lines):
                if i==j: continue
                if p.distance(other)<=0.75:
                    connected=True; break
            if not connected: dead+=1
    score=max(0.0,1.0-dead/max(1.0,2.0*len(lines)))
    return {'intersection_count':len(intersections),'internal_dead_end_count':dead,'connectivity_score':round(score,4)}


def _pack_standard_blocks(developable, roads, lot_w: float, lot_d: float, road_priority=None):
    """Road -> block -> standard-lot parcelization.

    * roads already cut the developable land into block polygons;
    * each block chooses ONE coherent frontage axis (horizontal-ish or
      vertical-ish) that yields the most exact standard modules;
    * standard lots are never clipped, shrunk or stretched;
    * adaptive lots are intentionally NOT created here.  They belong to the
      later residual pass only.
    """
    blocks = sorted(_poly_parts(_polygonal_only(developable)), key=lambda g: g.area, reverse=True)
    road_priority = road_priority or roads
    priority_rank = {r.get("id"): i for i, r in enumerate(road_priority)}
    all_lots = []
    all_meta = []
    block_records = []

    for block_no, block in enumerate(blocks, start=1):
        if block.is_empty or block.area < lot_w * lot_d * 0.80:
            continue

        candidates_by_axis = []
        # Site-plan roads are usually orthogonal after rotation, but optimizer
        # extensions can be diagonal.  Group them by two perpendicular families
        # relative to each candidate road rather than forcing global X/Y only.
        adjacent = []
        for road in roads:
            fr = _road_frame(road.get("line", LineString()))
            if not fr:
                continue
            corridor = road.get("corridor")
            if corridor is None or corridor.is_empty:
                corridor = _polygonal_only(fr and road["line"].buffer(float(road.get("width_m", 0.0)) / 2.0, cap_style=2, join_style=2))
            if corridor is None or corridor.is_empty or block.distance(corridor) > 0.50:
                continue
            adjacent.append((road, fr))

        # Evaluate every adjacent road angle as a possible block orientation;
        # near-parallel roads are packed together (double-loaded blocks).
        orientation_seeds = []
        for road, fr in adjacent:
            a = fr["angle"] % 180.0
            if not any(_angle_distance_180(a, x) <= 12.0 for x in orientation_seeds):
                orientation_seeds.append(a)
        if not orientation_seeds:
            qm=_block_quality_metrics(block,len(adjacent),0,lot_w,lot_d)
            block_records.append({"block_id": f"B{block_no}", "standard_lot_count": 0, "area_m2": round(float(block.area),2), "orientation_deg": None, **qm})
            continue

        for seed in orientation_seeds:
            rows = []
            for road, fr in adjacent:
                if _angle_distance_180(fr["angle"] % 180.0, seed) > 12.0:
                    continue
                row = _standard_row_candidates(block, road, lot_w, lot_d)
                if row:
                    rows.append((road, row))

            # Prefer longer rows, then road hierarchy/order.  Overlap only
            # occurs when a block is too shallow for two opposing 15 m rows;
            # in that case keep the better frontage instead of shrinking lots.
            rows.sort(key=lambda rr: (-len(rr[1]), priority_rank.get(rr[0].get("id"), 10_000)))
            placed = []
            meta = []
            idx = _LotGridIndex(max(lot_w, lot_d) * 1.2)
            for road, row in rows:
                fr = _road_frame(road["line"])
                for cand in row:
                    if idx.conflicts(cand, tol=0.01):
                        continue
                    idx.add(cand)
                    placed.append(cand)
                    meta.append({
                        "road_id": road.get("id"),
                        "parcel_type": "standard",
                        "source": "geometry_settings",
                        "width_m": float(lot_w),
                        "depth_m": float(lot_d),
                        "frontage_m": float(lot_w),
                        "standard_width_m": float(lot_w),
                        "standard_depth_m": float(lot_d),
                        "block_id": f"B{block_no}",
                    })
            candidates_by_axis.append((len(placed), sum(g.area for g in placed), -seed, placed, meta, seed))

        if not candidates_by_axis:
            continue
        _, _, _, chosen, chosen_meta, chosen_seed = max(candidates_by_axis, key=lambda x: (x[0], x[1], x[2]))
        all_lots.extend(chosen)
        all_meta.extend(chosen_meta)
        qm=_block_quality_metrics(block,len(adjacent),len(chosen),lot_w,lot_d)
        block_records.append({
            "block_id": f"B{block_no}",
            "standard_lot_count": len(chosen),
            "area_m2": round(float(block.area), 2),
            "orientation_deg": round(float(chosen_seed), 2),
            **qm,
        })

    total_block_area=sum(float(b.get('area_m2',0.0)) for b in block_records)
    avg_reg=(sum(float(b.get('regularity',0.0))*float(b.get('area_m2',0.0)) for b in block_records)/total_block_area) if total_block_area else 0.0
    return all_lots, all_meta, {
        "block_count": len(blocks),
        "active_block_count": sum(1 for b in block_records if b.get("standard_lot_count", 0) > 0),
        "standard_lot_count": len(all_lots),
        "average_block_regularity": round(avg_reg,4),
        "irregular_block_count": sum(1 for b in block_records if b.get('block_type')=='IRREGULAR_BLOCK'),
        "double_loaded_block_count": sum(1 for b in block_records if b.get('block_type')=='DOUBLE_LOADED'),
        "blocks": block_records,
    }


def _standard_geometry_audit(lots, meta, target_w: float, target_d: float, tol=0.06):
    """Hard audit that STANDARD really means the Geometry Settings module."""
    invalid = []
    target_area = target_w * target_d
    for i, (g, m) in enumerate(zip(lots or [], meta or [])):
        if m.get("parcel_type") == "residual":
            continue
        rect = g.minimum_rotated_rectangle
        dims = []
        if rect is not None and not rect.is_empty and hasattr(rect, "exterior"):
            cs = list(rect.exterior.coords)
            dims = [math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(cs,cs[1:])]
            dims = sorted([d for d in dims if d > 0.05])
        short = dims[0] if dims else 0.0
        long = dims[-1] if dims else 0.0
        expected_short, expected_long = sorted((target_w, target_d))
        ok = (
            abs(short - expected_short) <= tol
            and abs(long - expected_long) <= tol
            and abs(g.area - target_area) <= max(0.20, target_area * 0.002)
        )
        if not ok:
            invalid.append({"index": i, "area_m2": round(g.area, 3), "short_m": round(short, 3), "long_m": round(long, 3)})
    return {
        "invalid_standard_lot_count": len(invalid),
        "invalid_standard_lots": invalid[:50],
        "target_width_m": float(target_w),
        "target_depth_m": float(target_d),
        "target_area_m2": float(target_area),
    }


def _back_rotate(geom, angle, origin):
    return affinity.rotate(geom, angle, origin=origin, use_radians=False)


def _mapping_wgs(geom, angle, origin, epsg):
    if geom is None or geom.is_empty:
        return None
    return mapping(to_wgs84(_back_rotate(geom, angle, origin), epsg))


def _list_mapping_wgs(geoms, angle, origin, epsg):
    return [mapping(to_wgs84(_back_rotate(g, angle, origin), epsg)) for g in geoms if not g.is_empty]


def _strict_residual_reserve(buildable, parcel_area: float, used_geoms, max_residual_pct_total: float):
    """Allocate unavoidable excess residual to a transparent landscape/reserve layer.

    The returned `residual` is genuine unallocated land and is never allowed to exceed
    the configured percentage of TOTAL parcel area. The `reserve` remains visible and
    separate from regulatory RTH/PSU so the cap is not achieved by relabeling RTH.
    """
    used=[g for g in used_geoms if g is not None and not g.is_empty]
    occupied=_polygonal_only(unary_union(used)) if used else Polygon()
    raw=_polygonal_only(buildable.difference(occupied))
    cap_area=max(0.0, parcel_area*max_residual_pct_total/100.0)
    reserve=Polygon()
    if raw.area > cap_area + 0.05:
        reserve=_take_target_area(raw, raw.area-cap_area)
    occupied2=_polygonal_only(unary_union(used+[reserve])) if not reserve.is_empty else occupied
    residual=_polygonal_only(buildable.difference(occupied2))
    return reserve,residual,cap_area


def generate_site_alternatives(req: SitePlanRequest) -> dict[str, Any]:
    geom_wgs = ensure_polygon(req.geometry)
    epsg = utm_epsg_for_geometry(geom_wgs)
    parcel = project_geom(geom_wgs, 4326, epsg)
    buildable = _polygonal_only(parcel.buffer(-req.setback_m, join_style=2))
    if buildable.is_empty:
        raise HTTPException(422, "Setback is too large; buildable area is empty")

    base = _dominant_angle_deg(buildable)
    # M2.5.11: masterplan candidates vary road topology/orientation, never lot dimensions.
    specs_to_try = []
    angle_offsets=(0,-5,5,-10,10)
    for off in angle_offsets:
        specs_to_try.append((f"Parallel {off:+d}°" if off else "Parallel — sumbu utama", "parallel", (base+off)%180, "top", "bottom", 0, 0.50))
    for off in (0,-5,5):
        specs_to_try.append((f"Parallel silang {off:+d}°" if off else "Parallel — silang 90°", "parallel", (base+90+off)%180, "right", "left", 1, 0.50))
    for ratio,label in ((0.35,'Spine kiri'),(0.50,'Spine tengah'),(0.65,'Spine kanan')):
        specs_to_try.append((label, "spine", base, "top", "right", 0, ratio))
        specs_to_try.append((label+" — silang", "spine", (base+90)%180, "left", "bottom", 1, ratio))
    for ratio,label in ((0.50,'Cross Grid'),(0.35,'Cross Grid offset'),(0.65,'Cross Grid offset kanan')):
        specs_to_try.append((label, "cross", base, "top", "bottom", 0, ratio))

    alternatives = []
    for idx, (name, pattern, angle, rth_side, psu_side, offset_seed, spine_ratio) in enumerate(specs_to_try):
        origin = buildable.centroid
        rot = affinity.rotate(buildable, -angle, origin=origin, use_radians=False)
        specs = _road_specs(rot, pattern, req.lot_depth_m, req.main_road_width_m, req.local_road_width_m, spine_ratio=spine_ratio)
        roads, drainage, road_len, drain_len, road_segments_proj = _roads_from_specs(rot, specs)
        free = _polygonal_only(rot.difference(roads))

        target_rth = parcel.area * req.rth_pct / 100.0
        rth = _reserve_strip(free, target_rth, rth_side)
        free2 = _polygonal_only(free.difference(rth))
        target_psu = parcel.area * req.psu_pct / 100.0
        psu = _reserve_strip(free2, target_psu, psu_side)
        developable = _polygonal_only(free2.difference(psu))

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
        lots, lot_meta, block_info = _pack_standard_blocks(
            developable, roads_model, req.lot_width_m, req.lot_depth_m, road_priority=roads_model
        )
        standard_audit = _standard_geometry_audit(lots, lot_meta, req.lot_width_m, req.lot_depth_m)
        lots_area = sum(x.area for x in lots)
        lot_union = _polygonal_only(unary_union(lots)) if lots else Polygon()
        # M2.5.4.1: generation is always the original/baseline scenario.
        # Land optimization is opt-in from the frontend and never rewrites baseline geometry.
        reserve = Polygon()
        occupied = _polygonal_only(unary_union([g for g in (roads, rth, psu, lot_union) if g is not None and not g.is_empty]))
        residual = _polygonal_only(rot.difference(occupied))
        actual_rth_pct = (rth.area / parcel.area * 100) if parcel.area else 0
        actual_psu_pct = (psu.area / parcel.area * 100) if parcel.area else 0
        reserve_pct = 0.0
        road_pct = (roads.area / parcel.area * 100) if parcel.area else 0
        lot_pct = (lots_area / parcel.area * 100) if parcel.area else 0
        unused_area = residual.area
        net_developable = max(buildable.area - roads.area - rth.area - psu.area, 1e-9)
        land_utilization_pct = lots_area / net_developable * 100.0
        residual_ratio_pct = unused_area / buildable.area * 100.0 if buildable.area else 0.0
        residual_pct_total_land = unused_area / parcel.area * 100.0 if parcel.area else 0.0
        road_efficiency = lots_area / roads.area if roads.area else 0.0
        network_quality=_road_network_quality(roads_model,rot)
        block_reg = float(block_info.get('average_block_regularity', 0.0))
        gross_efficiency_pct = round((lots_area / parcel.area * 100.0) if parcel.area else 0.0, 2)
        efficiency_met = bool(gross_efficiency_pct >= 70.0 - 1e-4)

        validation = {
            "lot_efficiency_pct": gross_efficiency_pct,
            "lot_efficiency_target_pct": 70.0,
            "lot_efficiency_met": efficiency_met,
            "standard_lot_count": len(lots),
            "adaptive_lot_count": 0,
            "standard_lot_area_m2": round(lots_area, 2),
            "adaptive_lot_area_m2": 0.0,
            "invalid_standard_lot_count": standard_audit["invalid_standard_lot_count"],
            "invalid_standard_lots": standard_audit["invalid_standard_lots"][:50],
            "adaptive_origin_violation_count": 0,
            "adaptive_origin_violations": [],
            "lot_overlap_pairs": 0,
            "lot_road_overlaps": 0,
            "lot_road_overlap_area_m2": 0.0,
            "lot_obstacle_overlaps": 0,
            "lot_obstacle_overlap_area_m2": 0.0,
            "lots_outside_buildable": 0,
            "rth_psu_overlap": 0,
            "invalid_residual_lot_count": 0,
            "invalid_residual_lots": [],
            "residual_true_area_m2": round(unused_area, 2),
            "residual_true_pct": round(residual_pct_total_land, 2),
            "lot_count_preserved": True,
            "base_lot_count": len(lots),
            "final_lot_count": len(lots),
            "valid": bool(efficiency_met and standard_audit["invalid_standard_lot_count"] == 0),
        }

        score = (
            (1_000_000_000.0 if efficiency_met else 0.0)
            + len(lots) * 1_000_000.0
            + gross_efficiency_pct * 100_000.0
            + block_reg * 25_000.0
            + float(network_quality.get('connectivity_score', 0.0)) * 15_000.0
            - float(block_info.get('irregular_block_count', 0)) * 1_500.0
            - residual_pct_total_land * 50.0
            - road_pct * 10.0
        )
        alternatives.append({
            "id": f"ALT-{idx+1}",
            "name": name,
            "pattern": pattern,
            "angle_deg": round(angle, 2),
            "buildable": _mapping_wgs(rot, angle, origin, epsg),
            "roads": _mapping_wgs(roads, angle, origin, epsg),
            "road_segments": [
                {
                    "id": f"R{j+1}",
                    "kind": seg["kind"],
                    "width_m": round(seg["width_m"], 2),
                    "angle_deg": round((angle + (0 if seg["axis"] == "h" else 90)) % 180, 2),
                    "length_m": round(seg["length_m"], 2),
                    "centerline": _mapping_wgs(seg["centerline"], angle, origin, epsg),
                    "polygon": _mapping_wgs(seg["polygon"], angle, origin, epsg),
                    "drainage": _mapping_wgs(seg["drainage"], angle, origin, epsg),
                }
                for j, seg in enumerate(road_segments_proj)
            ],
            "rth": _mapping_wgs(rth, angle, origin, epsg),
            "psu": _mapping_wgs(psu, angle, origin, epsg),
            "reserve": _mapping_wgs(reserve, angle, origin, epsg),
            "drainage": _mapping_wgs(drainage, angle, origin, epsg),
            "lots": _list_mapping_wgs(lots, angle, origin, epsg),
            "lot_details": _lot_detail_records(lots, lot_meta),
            "parcelization": {
                "strategy": "road-block-standard-first",
                "standard_source": "geometry_settings",
                "adaptive_source": "residual_only",
                **block_info,
                **standard_audit,
            },
            "residuals": [
                {"id": f"RAW-RES-{k+1}", "area_m2": round(g.area,2),
                 "classification": "small_residual" if g.area < req.lot_width_m*req.lot_depth_m else "potential_lot",
                 "geometry": _mapping_wgs(g, angle, origin, epsg)}
                for k,g in enumerate(sorted(_poly_parts(residual), key=lambda x:x.area, reverse=True)[:80])
            ],
            "stats": {
                "lot_count": len(lots),
                "standard_lot_count": len(lots),
                "adaptive_lot_count": 0,
                "invalid_standard_lot_count": standard_audit["invalid_standard_lot_count"],
                "block_count": block_info["block_count"],
                "active_block_count": block_info["active_block_count"],
                "average_block_regularity": block_info.get("average_block_regularity",0.0),
                "irregular_block_count": block_info.get("irregular_block_count",0),
                "double_loaded_block_count": block_info.get("double_loaded_block_count",0),
                "road_intersection_count": network_quality.get("intersection_count",0),
                "internal_dead_end_count": network_quality.get("internal_dead_end_count",0),
                "road_connectivity_score": network_quality.get("connectivity_score",0.0),
                "lots_total_area_m2": round(lots_area, 2),
                "standard_lot_area_m2": round(lots_area, 2),
                "adaptive_lot_area_m2": 0.0,
                "lot_efficiency_pct": gross_efficiency_pct,
                "lot_efficiency_target_pct": 70.0,
                "lot_efficiency_met": efficiency_met,
                "road_area_m2": round(roads.area, 2),
                "road_pct": round(road_pct, 2),
                "road_length_m": round(road_len, 2),
                "rth_area_m2": round(rth.area, 2),
                "rth_pct": round(actual_rth_pct, 2),
                "psu_area_m2": round(psu.area, 2),
                "psu_pct": round(actual_psu_pct, 2),
                "reserve_area_m2": round(reserve.area, 2),
                "reserve_pct": round(reserve_pct, 2),
                "drainage_length_m": round(drain_len, 2),
                "unused_area_m2": round(unused_area, 2),
                "residual_true_area_m2": round(unused_area, 2),
                "residual_true_pct": round(residual_pct_total_land, 2),
                "land_utilization_pct": round(land_utilization_pct, 2),
                "residual_ratio_pct": round(residual_ratio_pct, 2),
                "residual_pct_total_land": round(residual_pct_total_land, 2),
                "land_optimization_enabled": False,
                "optimized": False,
                "road_efficiency": round(road_efficiency, 3),
                "validation_passed": bool(validation["valid"]),
                "score": round(score, 2),
            },
            "validation": validation,
        })

    alternatives.sort(key=lambda a: (
        -int(a["stats"].get("invalid_standard_lot_count",0)),
        1 if bool(a["stats"].get("lot_efficiency_met", False)) else 0,
        int(a["stats"].get("standard_lot_count",0)),
        float(a["stats"].get("lot_efficiency_pct",0.0)),
        -int(a["stats"].get("adaptive_lot_count",0)),
        -float(a["stats"].get("road_area_m2",999999.0)),
        -float(a["stats"].get("residual_true_area_m2", a["stats"].get("unused_area_m2", 999999.0))),
        float(a["stats"].get("average_block_regularity",0.0)),
        float(a["stats"].get("road_connectivity_score",0.0)),
    ), reverse=True)
    alternatives = alternatives[: req.alternative_count]
    for rank, alt in enumerate(alternatives, 1):
        alt["rank"] = rank
        alt["recommended"] = rank == 1

    return {
        "parcel": mapping(geom_wgs),
        "parcel_stats": {
            "parcel_area_m2": round(parcel.area, 2),
            "parcel_perimeter_m": round(parcel.length, 2),
            "buildable_area_m2": round(buildable.area, 2),
            "utm_epsg": epsg,
            "dominant_angle_deg": base,
        },
        "settings": req.model_dump(),
        "alternatives": alternatives,
        "notice": "Heuristic conceptual layout only; road, RTH, PSU and drainage require regulatory/engineering validation before DED.",
    }


# -----------------------------
# Milestone 2.2: editable road segments
# -----------------------------
def _line_only(geometry: dict[str, Any]):
    try:
        geom = shape(geometry)
    except Exception as e:
        raise HTTPException(422, f"Invalid road centerline: {e}")
    if geom.is_empty or geom.geom_type not in ("LineString", "MultiLineString"):
        raise HTTPException(422, "Road centerline must be LineString/MultiLineString")
    return geom


def _offset_drainage(line, half_width: float, clip_geom=None):
    drains = []
    for side in ("left", "right"):
        try:
            d = line.parallel_offset(half_width, side, join_style=2)
        except Exception:
            d = GeometryCollection()
        if clip_geom is not None and not d.is_empty:
            d = d.intersection(clip_geom)
        if not d.is_empty:
            drains.append(d)
    return unary_union(drains) if drains else GeometryCollection()


def rebuild_road_segments(req: RoadRebuildRequest) -> dict[str, Any]:
    if not req.segments:
        return {"roads": None, "drainage": None, "road_segments": [], "stats": {"road_area_m2": 0.0, "road_length_m": 0.0, "drainage_length_m": 0.0}}

    ref_geom = ensure_polygon(req.buildable) if req.buildable else None
    if ref_geom is None:
        ref_geom = _line_only(req.segments[0].centerline).buffer(0.0001)
    epsg = utm_epsg_for_geometry(ref_geom)
    clip_proj = project_geom(ensure_polygon(req.buildable), 4326, epsg) if req.buildable else None

    road_parts = []
    drain_parts = []
    out_segments = []
    road_length = 0.0
    drain_length = 0.0
    for i, seg in enumerate(req.segments):
        line_wgs = _line_only(seg.centerline)
        line = project_geom(line_wgs, 4326, epsg)
        if clip_proj is not None:
            line = line.intersection(clip_proj)
        if line.is_empty:
            continue
        corridor = _polygonal_only(line.buffer(seg.width_m / 2, cap_style=2, join_style=2))
        if clip_proj is not None:
            corridor = _polygonal_only(corridor.intersection(clip_proj))
        drains = _offset_drainage(line, seg.width_m / 2, clip_proj)
        road_parts.append(corridor)
        if not drains.is_empty:
            drain_parts.append(drains)
        road_length += line.length
        drain_length += drains.length if not drains.is_empty else 0.0
        coords = list(line.coords) if line.geom_type == "LineString" else []
        angle = 0.0
        if len(coords) >= 2:
            a, b = coords[0], coords[-1]
            angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
        out_segments.append({
            "id": seg.id or f"R{i+1}",
            "kind": seg.kind,
            "width_m": round(seg.width_m, 2),
            "angle_deg": round(angle, 2),
            "length_m": round(line.length, 2),
            "centerline": mapping(to_wgs84(line, epsg)),
            "polygon": mapping(to_wgs84(corridor, epsg)) if not corridor.is_empty else None,
            "drainage": mapping(to_wgs84(drains, epsg)) if not drains.is_empty else None,
        })

    roads = _polygonal_only(unary_union(road_parts)) if road_parts else Polygon()
    drainage = unary_union(drain_parts) if drain_parts else GeometryCollection()
    return {
        "roads": mapping(to_wgs84(roads, epsg)) if not roads.is_empty else None,
        "drainage": mapping(to_wgs84(drainage, epsg)) if not drainage.is_empty else None,
        "road_segments": out_segments,
        "stats": {
            "road_area_m2": round(roads.area, 2) if not roads.is_empty else 0.0,
            "road_length_m": round(road_length, 2),
            "drainage_length_m": round(drain_length, 2),
            "utm_epsg": epsg,
        },
    }


@app.post("/site-plan/roads/rebuild")
def site_plan_roads_rebuild(req: RoadRebuildRequest):
    return rebuild_road_segments(req)


# -----------------------------
# Milestone 2.1: manual adjustment recalculation
# -----------------------------
def _shape_optional(geometry: dict[str, Any] | None):
    if not geometry:
        return GeometryCollection()
    try:
        geom = shape(geometry)
    except Exception as e:
        raise HTTPException(422, f"Invalid layout geometry: {e}")
    if geom.is_empty:
        return GeometryCollection()
    return make_valid(geom) if geom.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") else geom


def recalculate_manual_layout(req: SitePlanRecalculateRequest) -> dict[str, Any]:
    parcel_wgs = ensure_polygon(req.parcel)
    buildable_wgs = ensure_polygon(req.buildable)
    epsg = utm_epsg_for_geometry(parcel_wgs)
    parcel = project_geom(parcel_wgs, 4326, epsg)
    buildable = project_geom(buildable_wgs, 4326, epsg)

    roads = project_geom(_shape_optional(req.roads), 4326, epsg) if req.roads else GeometryCollection()
    rth = project_geom(_shape_optional(req.rth), 4326, epsg) if req.rth else GeometryCollection()
    psu = project_geom(_shape_optional(req.psu), 4326, epsg) if req.psu else GeometryCollection()
    reserve = project_geom(_shape_optional(req.reserve), 4326, epsg) if (req.land_optimization_enabled and req.reserve) else GeometryCollection()
    drainage = project_geom(_shape_optional(req.drainage), 4326, epsg) if req.drainage else GeometryCollection()

    lots = []
    for item in req.lots:
        try:
            g = shape(item)
            if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            lots.append(project_geom(make_valid(g), 4326, epsg))
        except Exception:
            continue

    lots_area = sum(g.area for g in lots)
    parcel_area = parcel.area

    # M2.5.4.1: no automatic residual-to-reserve conversion.
    # OFF = original scenario; ON = preserve only reserve explicitly returned by the optimizer.

    outside = sum(1 for g in lots if not buildable.covers(g))
    overlaps = 0
    if len(lots) > 1:
        try:
            tree = STRtree(lots)
            pairs = tree.query(lots, predicate="intersects")
            seen = set()
            for a, b in zip(pairs[0].tolist(), pairs[1].tolist()):
                if a >= b: continue
                key = (int(a), int(b))
                if key in seen: continue
                seen.add(key)
                if lots[a].intersection(lots[b]).area > 0.05:
                    overlaps += 1
        except Exception:
            for i in range(len(lots)):
                for j in range(i + 1, len(lots)):
                    if lots[i].intersection(lots[j]).area > 0.05:
                        overlaps += 1

    road_area = roads.area if not roads.is_empty else 0.0
    rth_area = rth.area if not rth.is_empty else 0.0
    psu_area = psu.area if not psu.is_empty else 0.0
    drainage_length = drainage.length if not drainage.is_empty else 0.0

    used_geoms = [g for g in (roads, rth, psu, reserve) if not g.is_empty] + lots
    if used_geoms:
        used = unary_union(used_geoms).intersection(buildable)
        unused_area = max(buildable.area - used.area, 0.0)
    else:
        unused_area = buildable.area

    stats = dict(req.previous_stats or {})
    stats.update({
        "lot_count": len(lots),
        "lots_total_area_m2": round(lots_area, 2),
        "lot_efficiency_pct": round((lots_area / parcel_area * 100) if parcel_area else 0, 2),
        "road_area_m2": round(road_area, 2),
        "road_pct": round((road_area / parcel_area * 100) if parcel_area else 0, 2),
        # Translating/rotating a generated road network does not change its centerline length.
        "road_length_m": round(float(stats.get("road_length_m", 0) or 0), 2),
        "rth_area_m2": round(rth_area, 2),
        "rth_pct": round((rth_area / parcel_area * 100) if parcel_area else 0, 2),
        "psu_area_m2": round(psu_area, 2),
        "psu_pct": round((psu_area / parcel_area * 100) if parcel_area else 0, 2),
        "reserve_area_m2": round(reserve.area if not reserve.is_empty else 0.0, 2),
        "reserve_pct": round(((reserve.area if not reserve.is_empty else 0.0) / parcel_area * 100) if parcel_area else 0, 2),
        "drainage_length_m": round(drainage_length, 2),
        "unused_area_m2": round(unused_area, 2),
        "land_utilization_pct": round((lots_area / max(buildable.area - road_area - rth_area - psu_area - (reserve.area if not reserve.is_empty else 0.0), 1e-9)) * 100, 2),
        "residual_ratio_pct": round((unused_area / buildable.area * 100) if buildable.area else 0, 2),
        "residual_pct_total_land": round((unused_area / parcel_area * 100) if parcel_area else 0, 2),
        "residual_cap_pct_total": 3.0,
        "residual_cap_met": ((unused_area / parcel_area * 100) if parcel_area else 0) <= 3.01,
        "land_optimization_enabled": bool(req.land_optimization_enabled),
        "road_efficiency": round((lots_area / road_area) if road_area else 0, 3),
        "lots_outside_buildable": outside,
        "lot_overlap_pairs": overlaps,
        "manual_adjusted": True,
    })
    return {"stats": stats, "utm_epsg": epsg,
            "reserve": mapping(to_wgs84(reserve, epsg)) if not reserve.is_empty else None}


@app.post("/site-plan/recalculate")
def site_plan_recalculate(req: SitePlanRecalculateRequest):
    return recalculate_manual_layout(req)


# -----------------------------
# Milestone 2.3: smart reflow / validation
# -----------------------------
def _line_primary(line):
    if line.geom_type == "LineString":
        return line
    if line.geom_type == "MultiLineString":
        parts = list(line.geoms)
        return max(parts, key=lambda g: g.length) if parts else LineString()
    return LineString()


def _road_frame(line):
    line = _line_primary(line)
    if line.is_empty or len(line.coords) < 2:
        return None
    a = line.coords[0]
    b = line.coords[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    return {"a": a, "b": b, "ux": ux, "uy": uy, "nx": nx, "ny": ny, "length": length, "angle": math.degrees(math.atan2(uy, ux))}


def _rect_centered(cx: float, cy: float, width: float, depth: float, angle_deg: float):
    g = box(-width / 2, -depth / 2, width / 2, depth / 2)
    g = affinity.rotate(g, angle_deg, origin=(0, 0), use_radians=False)
    return affinity.translate(g, cx, cy)


def _prepare_editor_state(req: SmartReflowRequest):
    parcel_wgs = ensure_polygon(req.parcel)
    buildable_wgs = ensure_polygon(req.buildable)
    epsg = utm_epsg_for_geometry(parcel_wgs)
    parcel = project_geom(parcel_wgs, 4326, epsg)
    buildable = project_geom(buildable_wgs, 4326, epsg)
    def _optional_polygon(item):
        if not item:
            return None
        try:
            g = shape(item)
            if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
                return None
            return project_geom(make_valid(g), 4326, epsg)
        except Exception:
            return None
    rth = _optional_polygon(req.rth)
    psu = _optional_polygon(req.psu)
    roads = []
    for i, seg in enumerate(req.road_segments):
        try:
            lw = _line_only(seg.centerline)
            line = project_geom(lw, 4326, epsg)
            line = line.intersection(buildable)
            if line.is_empty:
                continue
            roads.append({
                "id": seg.id or f"R{i+1}",
                "kind": seg.kind,
                "width_m": float(seg.width_m),
                "line": line,
                "corridor": _polygonal_only(line.buffer(float(seg.width_m) / 2, cap_style=2, join_style=2)),
            })
        except Exception:
            continue
    lots = []
    for i, item in enumerate(req.lots):
        try:
            g = shape(item)
            if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
                lots.append(None)
                continue
            lots.append(project_geom(make_valid(g), 4326, epsg))
        except Exception:
            lots.append(None)
    return epsg, parcel, buildable, roads, lots, rth, psu


def _nearest_road_id(lot, roads):
    if lot is None or lot.is_empty or not roads:
        return None, float("inf")
    c = lot.centroid
    best = None
    for r in roads:
        d = c.distance(r["line"])
        if best is None or d < best[1]:
            best = (r["id"], d)
    return best if best else (None, float("inf"))


def _repack_lots_on_road(lots, road, candidate_indices, buildable, lot_width, lot_depth, locked_indices=None):
    locked_indices = set(locked_indices or [])
    frame = _road_frame(road["line"])
    if not frame:
        return set(), [f"{road['id']}: centerline tidak valid"]
    a = frame["a"]
    ux, uy, nx, ny = frame["ux"], frame["uy"], frame["nx"], frame["ny"]
    length = frame["length"]
    road_half = road["width_m"] / 2
    records = {1: [], -1: []}
    warnings = []
    for idx in candidate_indices:
        g = lots[idx] if 0 <= idx < len(lots) else None
        if g is None or g.is_empty:
            continue
        c = g.centroid
        vx, vy = c.x - a[0], c.y - a[1]
        t = vx * ux + vy * uy
        side_val = vx * nx + vy * ny
        side = 1 if side_val >= 0 else -1
        records[side].append({"idx": idx, "t": t})

    adjusted = set()
    min_t, max_t = lot_width / 2, max(length - lot_width / 2, lot_width / 2)
    spacing = lot_width + 0.05
    for side, row in records.items():
        if not row:
            continue
        row.sort(key=lambda r: r["t"])
        # Preserve current ordering but enforce non-overlapping frontage spacing.
        vals = [min(max(r["t"], min_t), max_t) for r in row]
        for i in range(1, len(vals)):
            vals[i] = max(vals[i], vals[i - 1] + spacing)
        if vals and vals[-1] > max_t:
            shift = vals[-1] - max_t
            vals = [v - shift for v in vals]
        for i in range(len(vals) - 2, -1, -1):
            vals[i] = min(vals[i], vals[i + 1] - spacing)
        if vals and vals[0] < min_t:
            shift = min_t - vals[0]
            vals = [v + shift for v in vals]

        for rec, t in zip(row, vals):
            idx = rec["idx"]
            if idx in locked_indices:
                continue
            cx = a[0] + ux * t + nx * side * (road_half + lot_depth / 2)
            cy = a[1] + uy * t + ny * side * (road_half + lot_depth / 2)
            candidate = _rect_centered(cx, cy, lot_width, lot_depth, frame["angle"])
            if buildable.covers(candidate):
                lots[idx] = candidate
                adjusted.add(idx)
            else:
                warnings.append(f"Kavling #{idx+1} tidak muat setelah reflow pada {road['id']}")
    return adjusted, warnings


def _resolve_lot_overlaps(lots, buildable, preferred_fixed=None, roads=None, road_union=None, frontage_tolerance: float = 1.5, max_iter: int = 20):
    preferred_fixed = set(preferred_fixed or [])
    moved = set()
    for _ in range(max_iter):
        changed = False
        unresolved = False
        for i in range(len(lots)):
            gi = lots[i]
            if gi is None or gi.is_empty:
                continue
            for j in range(i + 1, len(lots)):
                gj = lots[j]
                if gj is None or gj.is_empty:
                    continue
                inter = gi.intersection(gj)
                if inter.area <= 0.05:
                    continue
                unresolved = True
                if i in preferred_fixed and j not in preferred_fixed:
                    mover, anchor = j, i
                elif j in preferred_fixed and i not in preferred_fixed:
                    mover, anchor = i, j
                else:
                    mover, anchor = j, i
                gm, ga = lots[mover], lots[anchor]
                vx, vy = gm.centroid.x - ga.centroid.x, gm.centroid.y - ga.centroid.y
                norm = math.hypot(vx, vy)
                if norm < 1e-6:
                    vx, vy, norm = 1.0, 0.0, 1.0
                ux, uy = vx / norm, vy / norm
                dirs = [(ux, uy), (-ux, -uy), (-uy, ux), (uy, -ux)]
                if roads:
                    nearest = min(roads, key=lambda r: gm.centroid.distance(r["line"]))
                    fr = _road_frame(nearest["line"])
                    if fr:
                        dirs = [(fr["ux"], fr["uy"]), (-fr["ux"], -fr["uy"]), (ux, uy), (-ux, -uy), (-uy, ux), (uy, -ux)]
                base = max(math.sqrt(inter.area) + 0.15, 0.35)
                best = None
                best_overlap = float("inf")
                for dx, dy in dirs:
                    for mult in (1, 1.5, 2, 3, 4, 6, 8, 10):
                        cand = affinity.translate(gm, xoff=dx * base * mult, yoff=dy * base * mult)
                        if not buildable.covers(cand):
                            continue
                        if road_union is not None and not road_union.is_empty and cand.distance(road_union) > frontage_tolerance:
                            continue
                        total = 0.0
                        for k, other in enumerate(lots):
                            if k == mover or other is None or other.is_empty:
                                continue
                            total += cand.intersection(other).area
                        if total < best_overlap:
                            best, best_overlap = cand, total
                        if total <= 0.05:
                            break
                    if best_overlap <= 0.05:
                        break
                if best is not None and best_overlap < sum(gm.intersection(o).area for k,o in enumerate(lots) if k != mover and o is not None and not o.is_empty):
                    lots[mover] = best
                    moved.add(mover)
                    changed = True
        if not unresolved or not changed:
            break
    return moved


def _editor_validation(buildable, roads, lots, frontage_tolerance: float, rth=None, psu=None):
    """Hard validation using STRtree so 1k-5k lot layouts do not do O(n²) pair scans."""
    overlaps = 0
    outside = 0
    missing_frontage = 0
    lot_obstacle_overlaps = 0
    corridors = [r["corridor"] for r in roads if not r["corridor"].is_empty]
    road_union = unary_union(corridors) if corridors else GeometryCollection()
    obstacles = [g for g in (rth, psu) if g is not None and not g.is_empty]
    valid_lots = [g for g in lots if g is not None and not g.is_empty]
    for g in valid_lots:
        # Covers can be false for numerically coincident boundaries even when
        # difference area is exactly zero. A 1 mm tolerance avoids false rejects
        # without permitting visible spill outside the buildable polygon.
        if not buildable.buffer(0.001).covers(g):
            outside += 1
        if road_union.is_empty or g.distance(road_union) > frontage_tolerance:
            missing_frontage += 1
        for obs in obstacles:
            if g.intersection(obs).area > 0.05:
                lot_obstacle_overlaps += 1
    if len(valid_lots) > 1:
        try:
            tree = STRtree(valid_lots)
            pairs = tree.query(valid_lots, predicate='intersects')
            if getattr(pairs, 'shape', None) is not None and len(pairs.shape) == 2:
                seen = set()
                for a, b in zip(pairs[0].tolist(), pairs[1].tolist()):
                    if a >= b: continue
                    key = (int(a), int(b))
                    if key in seen: continue
                    seen.add(key)
                    if valid_lots[a].intersection(valid_lots[b]).area > 0.05:
                        overlaps += 1
            else:
                # Defensive fallback for older Shapely return forms.
                for i, g in enumerate(valid_lots):
                    for j in tree.query(g):
                        j = int(j)
                        if j <= i: continue
                        if g.intersection(valid_lots[j]).area > 0.05:
                            overlaps += 1
        except Exception:
            # Never fail validation because spatial indexing is unavailable.
            for i, g in enumerate(valid_lots):
                for j in range(i + 1, len(valid_lots)):
                    if g.intersection(valid_lots[j]).area > 0.05:
                        overlaps += 1
    rth_psu_overlap = 0
    if rth is not None and psu is not None and not rth.is_empty and not psu.is_empty:
        if rth.intersection(psu).area > 0.05:
            rth_psu_overlap = 1
    return {
        "lot_overlap_pairs": overlaps,
        "lots_outside_buildable": outside,
        "lots_missing_frontage": missing_frontage,
        "lot_obstacle_overlaps": lot_obstacle_overlaps,
        "rth_psu_overlap": rth_psu_overlap,
        "valid": overlaps == 0 and outside == 0 and missing_frontage == 0 and lot_obstacle_overlaps == 0 and rth_psu_overlap == 0,
    }


def _resolve_lot_obstacles(lots, obstacles, buildable, roads, road_union, frontage_tolerance: float, protected_indices=None, max_iter: int = 24):
    """Push non-protected lots away from RTH/PSU while keeping frontage and buildable constraints."""
    protected = set(protected_indices or [])
    obstacles = [g for g in obstacles if g is not None and not g.is_empty]
    moved = set()
    if not obstacles:
        return moved
    for _ in range(max_iter):
        changed = False
        for i, lot in enumerate(lots):
            if lot is None or lot.is_empty or i in protected:
                continue
            collisions = [obs for obs in obstacles if lot.intersection(obs).area > 0.05]
            if not collisions:
                continue
            nearest = min(roads, key=lambda r: lot.centroid.distance(r["line"])) if roads else None
            dirs = []
            if nearest:
                fr = _road_frame(nearest["line"])
                if fr:
                    dirs.extend([(fr["ux"], fr["uy"]), (-fr["ux"], -fr["uy"]), (fr["nx"], fr["ny"]), (-fr["nx"], -fr["ny"])])
            if not dirs:
                dirs = [(1,0),(-1,0),(0,1),(0,-1)]
            base = max(0.5, math.sqrt(sum(lot.intersection(obs).area for obs in collisions)) + 0.2)
            best = None
            best_score = float("inf")
            for dx, dy in dirs:
                for mult in (1,1.5,2,3,4,6,8,10,14,18):
                    cand = affinity.translate(lot, xoff=dx*base*mult, yoff=dy*base*mult)
                    if not buildable.covers(cand):
                        continue
                    if road_union is not None and not road_union.is_empty and cand.distance(road_union) > frontage_tolerance:
                        continue
                    obs_overlap = sum(cand.intersection(obs).area for obs in obstacles)
                    lot_overlap = sum(cand.intersection(other).area for j,other in enumerate(lots) if j != i and other is not None and not other.is_empty)
                    score = obs_overlap*1000 + lot_overlap
                    if score < best_score:
                        best, best_score = cand, score
                    if obs_overlap <= 0.05 and lot_overlap <= 0.05:
                        break
                if best_score <= 0.05:
                    break
            if best is not None and best_score < sum(lot.intersection(obs).area for obs in obstacles)*1000 + sum(lot.intersection(other).area for j,other in enumerate(lots) if j != i and other is not None and not other.is_empty):
                lots[i] = best
                moved.add(i)
                changed = True
        if not changed:
            break
    return moved


def smart_reflow(req: SmartReflowRequest) -> dict[str, Any]:
    epsg, parcel, buildable, roads, lots, rth, psu = _prepare_editor_state(req)
    road_by_id = {r["id"]: r for r in roads}
    target_ids = set(req.edited_road_ids or [])
    # If a lot was edited, infer its nearest road and reflow that local frontage row.
    for idx in req.edited_lot_indices:
        if 0 <= idx < len(lots) and lots[idx] is not None:
            rid, dist = _nearest_road_id(lots[idx], roads)
            if rid and dist <= req.reflow_radius_m:
                target_ids.add(rid)
    # When RTH/PSU is moved, the moved object is authoritative; nearby lots must reflow around it.
    special_obstacles = []
    if rth is not None and not rth.is_empty:
        special_obstacles.append(rth)
    if psu is not None and not psu.is_empty:
        special_obstacles.append(psu)
    if req.edited_special_types:
        edited_obs = []
        if "rth" in req.edited_special_types and rth is not None: edited_obs.append(rth)
        if "psu" in req.edited_special_types and psu is not None: edited_obs.append(psu)
        for obs in edited_obs:
            for lot in lots:
                if lot is None or lot.is_empty:
                    continue
                if lot.intersects(obs.buffer(req.reflow_radius_m)):
                    rid, dist = _nearest_road_id(lot, roads)
                    if rid and dist <= req.reflow_radius_m * 2:
                        target_ids.add(rid)
    if not target_ids and roads:
        # Fallback for explicit Reflow Local with only current selection context missing.
        target_ids.add(roads[0]["id"])

    adjusted = set()
    warnings = []
    assigned = set()
    for rid in target_ids:
        road = road_by_id.get(rid)
        if not road:
            continue
        candidate_indices = []
        for i, g in enumerate(lots):
            if g is None or g.is_empty or i in assigned:
                continue
            # Assign to target road only if it is the nearest target and within local radius.
            d = g.centroid.distance(road["line"])
            if d <= req.reflow_radius_m:
                nearest_id, nearest_d = _nearest_road_id(g, [road_by_id[x] for x in target_ids if x in road_by_id])
                if nearest_id == rid and nearest_d <= req.reflow_radius_m:
                    candidate_indices.append(i)
                    assigned.add(i)
        moved, notes = _repack_lots_on_road(
            lots, road, candidate_indices, buildable,
            req.lot_width_m, req.lot_depth_m,
            locked_indices=set(),
        )
        adjusted.update(moved)
        warnings.extend(notes)

    # Final safety pass pushes residual collisions locally while preserving road frontage.
    road_union = unary_union([r["corridor"] for r in roads if not r["corridor"].is_empty]) if roads else GeometryCollection()
    adjusted.update(_resolve_lot_overlaps(lots, buildable, preferred_fixed=set(req.edited_lot_indices), roads=roads, road_union=road_union, frontage_tolerance=req.frontage_tolerance_m))
    # RTH/PSU are static obstacles unless the user moved them; in either case lots are the auto-adjustable objects.
    adjusted.update(_resolve_lot_obstacles(lots, special_obstacles, buildable, roads, road_union, req.frontage_tolerance_m, protected_indices=set(req.edited_lot_indices)))
    adjusted.update(_resolve_lot_overlaps(lots, buildable, preferred_fixed=set(req.edited_lot_indices), roads=roads, road_union=road_union, frontage_tolerance=req.frontage_tolerance_m))
    validation = _editor_validation(buildable, roads, lots, req.frontage_tolerance_m, rth, psu)
    removed = set()
    # If a local solve still cannot resolve an intersection (commonly at a road junction),
    # remove the minimum number of conflicting lots instead of returning an overlapping siteplan.
    if validation["lot_overlap_pairs"]:
        for i in range(len(lots)):
            gi = lots[i]
            if gi is None or gi.is_empty or i in removed:
                continue
            for j in range(i + 1, len(lots)):
                gj = lots[j]
                if gj is None or gj.is_empty or j in removed:
                    continue
                if gi.intersection(gj).area > 0.05:
                    if i in req.edited_lot_indices and j not in req.edited_lot_indices:
                        removed.add(j)
                    elif j in req.edited_lot_indices and i not in req.edited_lot_indices:
                        removed.add(i)
                    else:
                        removed.add(j)
        if removed:
            warnings.append(f"{len(removed)} kavling dilepas otomatis karena tidak dapat dipasang tanpa overlap")
            lots = [g for i, g in enumerate(lots) if i not in removed]
            validation = _editor_validation(buildable, roads, lots, req.frontage_tolerance_m, rth, psu)
    # Last-resort obstacle cleanup: if an edited RTH/PSU occupies space that cannot be repacked,
    # drop the minimum non-edited conflicting lots rather than returning a visually overlapping plan.
    if validation.get("lot_obstacle_overlaps"):
        obstacle_removed = set()
        for i, g in enumerate(lots):
            if g is None or g.is_empty or i in req.edited_lot_indices:
                continue
            if any(g.intersection(obs).area > 0.05 for obs in special_obstacles):
                obstacle_removed.add(i)
        if obstacle_removed:
            warnings.append(f"{len(obstacle_removed)} kavling dilepas otomatis karena ruang ditempati RTH/PSU yang diedit")
            removed.update(obstacle_removed)
            lots = [g for i, g in enumerate(lots) if i not in obstacle_removed]
            validation = _editor_validation(buildable, roads, lots, req.frontage_tolerance_m, rth, psu)
    if validation["lot_overlap_pairs"]:
        warnings.append(f"Masih ada {validation['lot_overlap_pairs']} pasangan kavling overlap setelah reflow")
    if validation["lots_outside_buildable"]:
        warnings.append(f"{validation['lots_outside_buildable']} kavling berada di luar buildable area")
    if validation["lots_missing_frontage"]:
        warnings.append(f"{validation['lots_missing_frontage']} kavling belum menempel ke frontage jalan")
    if validation.get("lot_obstacle_overlaps"):
        warnings.append(f"{validation['lot_obstacle_overlaps']} kavling masih bertabrakan dengan RTH/PSU")
    if validation.get("rth_psu_overlap"):
        warnings.append("RTH dan PSU saling overlap")
    lots_wgs = [mapping(to_wgs84(g, epsg)) for g in lots if g is not None and not g.is_empty]
    return {
        "lots": lots_wgs,
        "adjusted_lot_indices": sorted(adjusted),
        "removed_lot_indices": sorted(removed),
        "target_road_ids": sorted(target_ids),
        "validation": validation,
        "warnings": warnings,
        "utm_epsg": epsg,
    }


@app.post("/editor/reflow")
def editor_reflow(req: SmartReflowRequest):
    # M2.4.1 compatibility bridge: older cached M2.3 frontends may still call
    # /editor/reflow. Route those requests through the M2.4 parametric engine
    # instead of the obsolete proximity/collision solver.
    preq = ParametricReflowRequest(
        parcel=req.parcel, buildable=req.buildable, road_segments=req.road_segments,
        lots=req.lots, rth=req.rth, psu=req.psu,
        lot_width_m=req.lot_width_m, lot_depth_m=req.lot_depth_m,
        edited_road_ids=req.edited_road_ids, edited_lot_indices=req.edited_lot_indices,
        edited_special_types=req.edited_special_types,
        frontage_tolerance_m=req.frontage_tolerance_m, preserve_count=True,
    )
    return _parametric_reflow(preq)


@app.post("/editor/repack-block")
def editor_repack_block(req: SmartReflowRequest):
    preq = ParametricReflowRequest(
        parcel=req.parcel, buildable=req.buildable, road_segments=req.road_segments,
        lots=req.lots, rth=req.rth, psu=req.psu,
        lot_width_m=req.lot_width_m, lot_depth_m=req.lot_depth_m,
        edited_road_ids=req.edited_road_ids, edited_lot_indices=req.edited_lot_indices,
        edited_special_types=req.edited_special_types,
        frontage_tolerance_m=req.frontage_tolerance_m, preserve_count=True,
    )
    return _parametric_reflow(preq)


@app.post("/editor/validate")
def editor_validate(req: EditorValidateRequest):
    smart = SmartReflowRequest(
        parcel=req.parcel,
        buildable=req.buildable,
        road_segments=req.road_segments,
        lots=req.lots,
        rth=req.rth,
        psu=req.psu,
        frontage_tolerance_m=req.frontage_tolerance_m,
    )
    epsg, parcel, buildable, roads, lots, rth, psu = _prepare_editor_state(smart)
    return {"validation": _editor_validation(buildable, roads, lots, req.frontage_tolerance_m, rth, psu), "utm_epsg": epsg}



# -----------------------------
# Milestone 2.4: parametric constraint editor
# -----------------------------
class ParametricModelRequest(BaseModel):
    parcel: dict[str, Any]
    buildable: dict[str, Any]
    road_segments: list[RoadSegmentInput] = Field(default_factory=list)
    lots: list[dict[str, Any]] = Field(default_factory=list)
    rth: dict[str, Any] | None = None
    psu: dict[str, Any] | None = None
    lot_width_m: float = Field(default=8.0, gt=0, le=200)
    lot_depth_m: float = Field(default=15.0, gt=0, le=300)


class ParametricReflowRequest(ParametricModelRequest):
    editor_model: dict[str, Any] | None = None
    edited_road_ids: list[str] = Field(default_factory=list)
    edited_lot_indices: list[int] = Field(default_factory=list)
    edited_special_types: list[str] = Field(default_factory=list)
    frontage_tolerance_m: float = Field(default=1.5, ge=0.1, le=10)
    preserve_count: bool = True


def _angle_diff_180(a: float, b: float) -> float:
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def _polygon_short_edge_angle(poly) -> float:
    try:
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
    except Exception:
        return 0.0
    edges = []
    for a, b in zip(coords, coords[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length > 1e-9:
            edges.append((length, math.degrees(math.atan2(dy, dx)) % 180.0))
    if not edges:
        return 0.0
    return min(edges, key=lambda x: x[0])[1]


def _frame_extents(poly, frame):
    if not frame:
        return 0.0, 0.0
    a = frame["a"]
    ux, uy, nx, ny = frame["ux"], frame["uy"], frame["nx"], frame["ny"]
    pts = []
    if poly.geom_type == "Polygon":
        pts.extend(list(poly.exterior.coords))
    elif poly.geom_type == "MultiPolygon":
        for g in poly.geoms:
            pts.extend(list(g.exterior.coords))
    if not pts:
        return 0.0, 0.0
    ts, ns = [], []
    for x, y in pts:
        vx, vy = x - a[0], y - a[1]
        ts.append(vx * ux + vy * uy)
        ns.append(vx * nx + vy * ny)
    return max(ts) - min(ts), max(ns) - min(ns)


def _lot_assignment(lot, roads, default_w: float, default_d: float):
    if lot is None or lot.is_empty or not roads:
        return None
    frontage_angle = _polygon_short_edge_angle(lot)
    best = None
    for r in roads:
        fr = _road_frame(r["line"])
        if not fr:
            continue
        dist = lot.distance(r["corridor"])
        angle_pen = _angle_diff_180(frontage_angle, fr["angle"])
        c = lot.centroid
        vx, vy = c.x - fr["a"][0], c.y - fr["a"][1]
        t = vx * fr["ux"] + vy * fr["uy"]
        n = vx * fr["nx"] + vy * fr["ny"]
        # Distance is dominant; angle breaks ties at intersections.
        score = dist * 10.0 + angle_pen * 0.08 + abs(n) * 0.002
        if best is None or score < best[0]:
            width, depth = _frame_extents(lot, fr)
            if width <= 0.2:
                width = default_w
            if depth <= 0.2:
                depth = default_d
            best = (score, {
                "road_id": r["id"],
                "side": 1 if n >= 0 else -1,
                "t_m": float(t),
                "t_ratio": float(t / fr["length"]) if fr["length"] > 1e-9 else 0.5,
                "frontage_m": float(width),
                "depth_m": float(depth),
                "angle_deg": float(fr["angle"] % 180.0),
                "distance_to_road_m": float(dist),
            })
    return best[1] if best else None


def _build_parametric_model(req: ParametricModelRequest) -> dict[str, Any]:
    smart = SmartReflowRequest(
        parcel=req.parcel, buildable=req.buildable, road_segments=req.road_segments,
        lots=req.lots, rth=req.rth, psu=req.psu,
        lot_width_m=req.lot_width_m, lot_depth_m=req.lot_depth_m,
    )
    epsg, parcel, buildable, roads, lots, rth, psu = _prepare_editor_state(smart)
    road_meta = {}
    for r in roads:
        fr = _road_frame(r["line"])
        if not fr:
            continue
        road_meta[r["id"]] = {
            "id": r["id"], "kind": r["kind"], "width_m": r["width_m"],
            "length_m": fr["length"], "angle_deg": fr["angle"] % 180.0,
        }
    lots_meta = []
    blocks: dict[str, dict[str, Any]] = {}
    for i, lot in enumerate(lots):
        assn = _lot_assignment(lot, roads, req.lot_width_m, req.lot_depth_m) if lot is not None else None
        if assn is None:
            lots_meta.append({"index": i, "road_id": None, "block_id": None})
            continue
        block_id = f"{assn['road_id']}:{'L' if assn['side'] > 0 else 'R'}"
        rec = {"index": i, "block_id": block_id, **assn}
        lots_meta.append(rec)
        block = blocks.setdefault(block_id, {
            "id": block_id, "road_id": assn["road_id"], "side": assn["side"], "lot_indices": []
        })
        block["lot_indices"].append(i)
    for block in blocks.values():
        block["lot_indices"].sort(key=lambda idx: lots_meta[idx].get("t_ratio", 0.0))
    return {
        "version": "2.4", "utm_epsg": epsg, "roads": road_meta,
        "lots": lots_meta, "blocks": blocks,
        "summary": {"road_count": len(road_meta), "block_count": len(blocks), "lot_count": len(lots_meta)},
    }


def _candidate_clear(candidate, buildable, roads_union, obstacles_union, fixed_union, placed, tol_area=0.05):
    if candidate is None or candidate.is_empty or not buildable.covers(candidate):
        return False
    if roads_union is not None and not roads_union.is_empty and candidate.intersection(roads_union).area > tol_area:
        return False
    if obstacles_union is not None and not obstacles_union.is_empty and candidate.intersection(obstacles_union).area > tol_area:
        return False
    if fixed_union is not None and not fixed_union.is_empty and candidate.intersection(fixed_union).area > tol_area:
        return False
    for g in placed:
        if candidate.intersection(g).area > tol_area:
            return False
    return True


def _snap_lot_to_road(current, road, meta, width, depth):
    fr = _road_frame(road["line"])
    if not fr:
        return current, 0.5
    c = current.centroid
    vx, vy = c.x - fr["a"][0], c.y - fr["a"][1]
    t = vx * fr["ux"] + vy * fr["uy"]
    t = max(width / 2.0, min(fr["length"] - width / 2.0, t))
    side = 1 if meta.get("side", 1) >= 0 else -1
    normal = road["width_m"] / 2.0 + depth / 2.0
    cx = fr["a"][0] + fr["ux"] * t + fr["nx"] * side * normal
    cy = fr["a"][1] + fr["uy"] * t + fr["ny"] * side * normal
    return _rect_centered(cx, cy, width, depth, fr["angle"]), t / fr["length"] if fr["length"] else 0.5


def _pack_parametric_block(block, model, lots, roads_by_id, buildable, roads_union, obstacles_union, fixed_union, edited_set):
    rid = block["road_id"]
    road = roads_by_id.get(rid)
    if not road:
        return {}, [], [f"Block {block['id']}: jalan {rid} tidak ditemukan"]
    fr = _road_frame(road["line"])
    if not fr:
        return {}, [], [f"Block {block['id']}: frame jalan tidak valid"]
    meta_by_idx = {m.get("index"): m for m in model.get("lots", [])}
    indices = [i for i in block.get("lot_indices", []) if 0 <= i < len(lots) and lots[i] is not None]
    indices.sort(key=lambda i: meta_by_idx.get(i, {}).get("t_ratio", 0.0))
    placed = []
    out = {}
    dropped = []
    warnings = []

    # Edited lots are authoritative along the road axis; snap them to the frontage first.
    edited_in_block = [i for i in indices if i in edited_set]
    for idx in edited_in_block:
        m = meta_by_idx.get(idx, {})
        width = max(0.5, float(m.get("frontage_m") or 8.0))
        depth = max(0.5, float(m.get("depth_m") or 15.0))
        cand, ratio = _snap_lot_to_road(lots[idx], road, m, width, depth)
        if _candidate_clear(cand, buildable, roads_union, obstacles_union, fixed_union, placed):
            out[idx] = cand; placed.append(cand); m["t_ratio"] = ratio
        else:
            warnings.append(f"Kavling #{idx+1} yang diedit tidak dapat dipertahankan pada frontage tanpa konflik")

    # Repack all remaining lots by their original parametric order.
    for idx in indices:
        if idx in out:
            continue
        m = meta_by_idx.get(idx, {})
        width = max(0.5, float(m.get("frontage_m") or 8.0))
        depth = max(0.5, float(m.get("depth_m") or 15.0))
        side = 1 if m.get("side", 1) >= 0 else -1
        desired = float(m.get("t_ratio", 0.5)) * fr["length"]
        min_t = width / 2.0
        max_t = max(min_t, fr["length"] - width / 2.0)
        desired = max(min_t, min(max_t, desired))
        step = max(width * 0.5, 0.5)
        offsets = [0.0]
        max_steps = int(max(fr["length"] / step, 1)) + 2
        for k in range(1, max_steps):
            offsets.extend([k * step, -k * step])
        best = None
        for off in offsets:
            t = desired + off
            if t < min_t - 1e-6 or t > max_t + 1e-6:
                continue
            normal = road["width_m"] / 2.0 + depth / 2.0
            cx = fr["a"][0] + fr["ux"] * t + fr["nx"] * side * normal
            cy = fr["a"][1] + fr["uy"] * t + fr["ny"] * side * normal
            cand = _rect_centered(cx, cy, width, depth, fr["angle"])
            if _candidate_clear(cand, buildable, roads_union, obstacles_union, fixed_union, placed):
                best = cand
                break
        if best is None:
            dropped.append(idx)
            warnings.append(f"Kavling #{idx+1} dilepas: block {block['id']} tidak punya slot valid")
        else:
            out[idx] = best
            placed.append(best)
    return out, dropped, warnings



def _relocate_unplaced_lots(dropped_indices, model, lots, roads, buildable, roads_union, obstacles_union, occupied_union, default_w, default_d):
    """Relocate lots that no longer fit in their original block into any valid vacant frontage slot.

    This is intentionally global across all road segments/sides. It preserves lot count when a
    valid empty frontage exists instead of silently deleting the lot.
    """
    if not dropped_indices:
        return {}, [], []
    meta_by_idx = {m.get("index"): m for m in model.get("lots", [])}
    road_candidates = []
    for road in roads:
        fr = _road_frame(road["line"])
        if not fr or fr["length"] <= 0.5:
            continue
        road_candidates.append((road, fr))

    relocated = {}
    unresolved = []
    notes = []
    dynamic_union = occupied_union if occupied_union is not None else GeometryCollection()

    for idx in dropped_indices:
        if idx < 0 or idx >= len(lots) or lots[idx] is None or lots[idx].is_empty:
            unresolved.append(idx)
            continue
        meta = meta_by_idx.get(idx, {})
        width = max(0.5, float(meta.get("frontage_m") or default_w))
        depth = max(0.5, float(meta.get("depth_m") or default_d))
        origin = lots[idx].centroid
        best = None
        # Search every road and both sides at sub-frontage increments. This allows a lot from a
        # saturated block to migrate into an empty block elsewhere (e.g. the vacant lower strip).
        for road, fr in road_candidates:
            if fr["length"] + 1e-9 < width:
                continue
            step = max(min(width * 0.25, 2.0), 0.5)
            min_t = width / 2.0
            max_t = fr["length"] - width / 2.0
            nsteps = max(1, int(math.floor((max_t - min_t) / step)) + 1)
            for side in (1, -1):
                normal = road["width_m"] / 2.0 + depth / 2.0
                for k in range(nsteps + 1):
                    t = min(max_t, min_t + k * step)
                    cx = fr["a"][0] + fr["ux"] * t + fr["nx"] * side * normal
                    cy = fr["a"][1] + fr["uy"] * t + fr["ny"] * side * normal
                    cand = _rect_centered(cx, cy, width, depth, fr["angle"])
                    if not _candidate_clear(cand, buildable, roads_union, obstacles_union, dynamic_union, []):
                        continue
                    # Prefer the nearest valid vacancy; tiny tie-breaker prefers larger road segments.
                    dist = origin.distance(cand.centroid) - fr["length"] * 1e-6
                    if best is None or dist < best[0]:
                        best = (dist, cand, road["id"], side)
        if best is None:
            unresolved.append(idx)
            continue
        _, cand, rid, side = best
        relocated[idx] = cand
        dynamic_union = unary_union([dynamic_union, cand]) if not dynamic_union.is_empty else cand
        notes.append(f"Kavling #{idx+1} dipindahkan ke slot kosong pada jalan {rid} ({'kiri' if side > 0 else 'kanan'})")
    return relocated, unresolved, notes

def _parametric_reflow(req: ParametricReflowRequest) -> dict[str, Any]:
    smart = SmartReflowRequest(
        parcel=req.parcel, buildable=req.buildable, road_segments=req.road_segments, lots=req.lots,
        rth=req.rth, psu=req.psu, lot_width_m=req.lot_width_m, lot_depth_m=req.lot_depth_m,
        frontage_tolerance_m=req.frontage_tolerance_m,
    )
    epsg, parcel, buildable, roads, lots, rth, psu = _prepare_editor_state(smart)
    roads_by_id = {r["id"]: r for r in roads}
    model = req.editor_model or _build_parametric_model(req)
    blocks = model.get("blocks", {})
    lot_meta = model.get("lots", [])
    meta_by_idx = {m.get("index"): m for m in lot_meta}

    affected_blocks = set()
    for rid in req.edited_road_ids:
        affected_blocks.update(bid for bid, b in blocks.items() if b.get("road_id") == rid)
    for idx in req.edited_lot_indices:
        bid = meta_by_idx.get(idx, {}).get("block_id")
        if bid:
            affected_blocks.add(bid)
    # RTH/PSU edits invalidate nearby blocks; identify them from the current obstacle intersection/buffer.
    edited_obstacles = []
    if "rth" in req.edited_special_types and rth is not None and not rth.is_empty:
        edited_obstacles.append(rth)
    if "psu" in req.edited_special_types and psu is not None and not psu.is_empty:
        edited_obstacles.append(psu)
    if edited_obstacles:
        obs_region = unary_union([g.buffer(max(req.lot_depth_m, 5.0)) for g in edited_obstacles])
        for i, lot in enumerate(lots):
            if lot is None or lot.is_empty or not lot.intersects(obs_region):
                continue
            bid = meta_by_idx.get(i, {}).get("block_id")
            if bid:
                affected_blocks.add(bid)

    if not affected_blocks:
        # Explicit local reflow: use block(s) nearest to selected/edit context, otherwise no-op.
        for idx in req.edited_lot_indices:
            bid = meta_by_idx.get(idx, {}).get("block_id")
            if bid:
                affected_blocks.add(bid)
    # Propagate the dependency graph beyond the directly edited road/block.
    # A moved road changes the usable envelope of crossing/adjacent blocks too; those blocks
    # must be repacked instead of being treated as fixed obstacles.
    max_depth = max([float(m.get("depth_m") or req.lot_depth_m) for m in lot_meta if m.get("road_id")] or [req.lot_depth_m])
    max_frontage = max([float(m.get("frontage_m") or req.lot_width_m) for m in lot_meta if m.get("road_id")] or [req.lot_width_m])
    regions = []
    for rid in req.edited_road_ids:
        r = roads_by_id.get(rid)
        if r:
            regions.append(r["line"].buffer(r["width_m"] / 2.0 + max_depth + max_frontage * 0.25, cap_style=2))
    for idx in req.edited_lot_indices:
        if 0 <= idx < len(lots) and lots[idx] is not None:
            regions.append(lots[idx].buffer(max_frontage * 1.25))
    for bid in list(affected_blocks):
        gs = [lots[i] for i in blocks.get(bid, {}).get("lot_indices", []) if 0 <= i < len(lots) and lots[i] is not None]
        if gs:
            regions.append(unary_union(gs).buffer(0.25))
    if regions:
        influence = unary_union(regions)
        # Two bounded propagation passes are enough to catch crossing rows without
        # turning a local edit into a full-site regeneration.
        for _ in range(2):
            added = set()
            for i, lot in enumerate(lots):
                if lot is None or lot.is_empty or not lot.intersects(influence):
                    continue
                bid = meta_by_idx.get(i, {}).get("block_id")
                if bid and bid not in affected_blocks:
                    added.add(bid)
            if not added:
                break
            affected_blocks.update(added)
            new_geoms = []
            for bid in added:
                new_geoms.extend([lots[i] for i in blocks.get(bid, {}).get("lot_indices", []) if 0 <= i < len(lots) and lots[i] is not None])
            if new_geoms:
                influence = unary_union([influence, unary_union(new_geoms).buffer(0.25)])

    affected_indices = set()
    for bid in affected_blocks:
        affected_indices.update(blocks.get(bid, {}).get("lot_indices", []))

    corridors = [r["corridor"] for r in roads if r.get("corridor") is not None and not r["corridor"].is_empty]
    roads_union = unary_union(corridors) if corridors else GeometryCollection()
    obstacles = [g for g in (rth, psu) if g is not None and not g.is_empty]
    obstacles_union = unary_union(obstacles) if obstacles else GeometryCollection()
    fixed = [g for i, g in enumerate(lots) if i not in affected_indices and g is not None and not g.is_empty]
    fixed_union = unary_union(fixed) if fixed else GeometryCollection()

    new_by_idx = {}
    dropped = []
    warnings = []
    edited_set = set(req.edited_lot_indices)
    # Solve each block independently but include already placed blocks in the fixed obstacle set.
    dynamic_fixed = fixed_union
    priority_roads = set(req.edited_road_ids or [])
    ordered_blocks = sorted(affected_blocks, key=lambda bid: (0 if blocks.get(bid, {}).get("road_id") in priority_roads else 1, bid))
    for bid in ordered_blocks:
        block = blocks.get(bid)
        if not block:
            continue
        out, drop, notes = _pack_parametric_block(
            block, model, lots, roads_by_id, buildable, roads_union, obstacles_union, dynamic_fixed, edited_set
        )
        new_by_idx.update(out)
        dropped.extend(drop)
        warnings.extend(notes)
        if out:
            block_union = unary_union(list(out.values()))
            dynamic_fixed = unary_union([dynamic_fixed, block_union]) if not dynamic_fixed.is_empty else block_union

    # Preserve lot count: lots that cannot stay in their original block are migrated to any
    # valid vacant frontage elsewhere before we consider the solve complete.
    relocated_by_idx = {}
    unresolved = list(dropped)
    if req.preserve_count and dropped:
        solved_geoms = [g for i, g in new_by_idx.items() if i not in dropped and g is not None and not g.is_empty]
        unchanged_geoms = [g for i, g in enumerate(lots) if i not in affected_indices and g is not None and not g.is_empty]
        occupied_parts = unchanged_geoms + solved_geoms
        occupied_union = unary_union(occupied_parts) if occupied_parts else GeometryCollection()
        relocated_by_idx, unresolved, relocation_notes = _relocate_unplaced_lots(
            dropped, model, lots, roads, buildable, roads_union, obstacles_union, occupied_union,
            req.lot_width_m, req.lot_depth_m
        )
        warnings.extend(relocation_notes)
        for idx, geom in relocated_by_idx.items():
            new_by_idx[idx] = geom

    # If preserve_count is requested and some lots still have no valid vacancy, reject the edit
    # rather than silently deleting them. The frontend keeps the pre-edit layout intact.
    if req.preserve_count and unresolved:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{len(unresolved)} kavling belum menemukan slot kosong yang valid; reflow dibatalkan agar tidak ada kavling terhapus",
                "unresolved_lot_indices": sorted(unresolved),
                "relocated_lot_indices": sorted(relocated_by_idx),
            },
        )

    effective_dropped = unresolved if not req.preserve_count else []
    result_lots = []
    old_to_new = {}
    for i, g in enumerate(lots):
        if i in effective_dropped:
            continue
        ng = new_by_idx.get(i, g)
        if ng is None or ng.is_empty:
            continue
        old_to_new[i] = len(result_lots)
        result_lots.append(ng)

    validation = _editor_validation(buildable, roads, result_lots, req.frontage_tolerance_m, rth, psu)
    # Rebuild the dependency model against the actual solved geometry; this is crucial for the next edit.
    solved_req = ParametricModelRequest(
        parcel=req.parcel, buildable=req.buildable,
        road_segments=req.road_segments,
        lots=[mapping(to_wgs84(g, epsg)) for g in result_lots],
        rth=req.rth, psu=req.psu, lot_width_m=req.lot_width_m, lot_depth_m=req.lot_depth_m,
    )
    new_model = _build_parametric_model(solved_req)
    adjusted_old = sorted(i for i in new_by_idx if i not in effective_dropped)
    adjusted_new = sorted(old_to_new[i] for i in adjusted_old if i in old_to_new)
    return {
        "lots": [mapping(to_wgs84(g, epsg)) for g in result_lots],
        "editor_model": new_model,
        "affected_block_ids": sorted(affected_blocks),
        "adjusted_lot_indices": adjusted_new,
        "dropped_lot_indices": sorted(effective_dropped),
        "relocated_lot_indices": sorted(old_to_new[i] for i in relocated_by_idx if i in old_to_new),
        "validation": validation,
        "warnings": warnings,
        "utm_epsg": epsg,
    }


@app.post("/editor/parametric-model")
def editor_parametric_model(req: ParametricModelRequest):
    return _build_parametric_model(req)


@app.post("/editor/parametric-reflow")
def editor_parametric_reflow(req: ParametricReflowRequest):
    return _parametric_reflow(req)


# -----------------------------
# Milestone 2.5: Land Utilization Optimizer
# -----------------------------
class YieldOptimizeRequest(BaseModel):
    parcel: dict[str, Any]
    buildable: dict[str, Any]
    road_segments: list[RoadSegmentInput] = Field(default_factory=list)
    lots: list[dict[str, Any]] = Field(default_factory=list)
    lot_details: list[dict[str, Any]] = Field(default_factory=list)
    rth: dict[str, Any] | None = None
    psu: dict[str, Any] | None = None
    target_lot_width_m: float = Field(default=8.0, gt=0, le=200)
    min_lot_width_m: float = Field(default=7.0, gt=0, le=200)
    max_lot_width_m: float = Field(default=10.0, gt=0, le=200)
    target_lot_depth_m: float = Field(default=15.0, gt=0, le=300)
    min_lot_depth_m: float = Field(default=13.0, gt=0, le=300)
    max_lot_depth_m: float = Field(default=18.0, gt=0, le=300)
    rth_pct: float = Field(default=10.0, ge=0, le=50)
    psu_pct: float = Field(default=5.0, ge=0, le=30)
    local_road_width_m: float = Field(default=6.0, ge=3, le=20)
    road_shift_m: float = Field(default=4.0, ge=0, le=20)
    allow_road_shift: bool = True
    allow_rth_psu_relocation: bool = True
    allow_selective_extension: bool = True
    max_extensions: int = Field(default=4, ge=0, le=8)
    max_residual_pct_total: float = Field(default=3.0, gt=0, le=20)
    strict_residual_cap: bool = True
    allow_residual_rth_absorption: bool = True
    max_optimize_seconds: float = Field(default=12.0, ge=2.0, le=60.0)
    max_extra_rth_pct_total: float = Field(default=5.0, ge=0.0, le=15.0)


def _poly_parts(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    if g.geom_type == 'MultiPolygon':
        return [x for x in g.geoms if not x.is_empty]
    if g.geom_type == 'GeometryCollection':
        out = []
        for x in g.geoms:
            if x.geom_type == 'Polygon': out.append(x)
            elif x.geom_type == 'MultiPolygon': out.extend([y for y in x.geoms if not y.is_empty])
        return out
    return []


def _line_parts(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'LineString':
        return [g]
    if g.geom_type == 'MultiLineString':
        return [x for x in g.geoms if not x.is_empty]
    if g.geom_type == 'GeometryCollection':
        out = []
        for x in g.geoms:
            if x.geom_type == 'LineString': out.append(x)
            elif x.geom_type == 'MultiLineString': out.extend([y for y in x.geoms if not y.is_empty])
        return out
    return []


def _take_target_area(area_geom, target_area: float):
    """Take approximately target_area from least-useful polygon parts without requiring one strip."""
    area_geom = _polygonal_only(area_geom)
    if area_geom.is_empty or target_area <= 0:
        return Polygon()
    target_area = min(target_area, area_geom.area)
    parts = sorted(_poly_parts(area_geom), key=lambda g: g.area, reverse=True)
    picked = []
    remaining = target_area
    for part in parts:
        if remaining <= 0.05:
            break
        if part.area <= remaining + 0.05:
            picked.append(part)
            remaining -= part.area
        else:
            # Trim the final component as a compact edge strip.
            candidate = _reserve_strip(part, remaining, 'top')
            if not candidate.is_empty:
                picked.append(candidate)
                remaining -= candidate.area
    return _polygonal_only(unary_union(picked)) if picked else Polygon()


class _LotGridIndex:
    """Cheap mutable spatial hash for thousands of generated lots."""
    def __init__(self, cell_size: float):
        self.cell = max(float(cell_size), 2.0)
        self.cells: dict[tuple[int,int], list[Any]] = {}

    def _keys(self, geom):
        minx, miny, maxx, maxy = geom.bounds
        ix0, iy0 = math.floor(minx / self.cell), math.floor(miny / self.cell)
        ix1, iy1 = math.floor(maxx / self.cell), math.floor(maxy / self.cell)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                yield (ix, iy)

    def conflicts(self, geom, tol=0.05):
        seen = set()
        for key in self._keys(geom):
            for other in self.cells.get(key, []):
                oid = id(other)
                if oid in seen: continue
                seen.add(oid)
                if geom.intersection(other).area > tol:
                    return True
        return False

    def add(self, geom):
        for key in self._keys(geom):
            self.cells.setdefault(key, []).append(geom)


def _adaptive_widths(run_length: float, target_w: float, min_w: float, max_w: float, gap: float = 0.08):
    run = max(0.0, float(run_length) - 0.10)
    if run < min_w:
        return []
    n_min = max(1, int(math.ceil((run + gap) / (max_w + gap))))
    n_max = max(1, int(math.floor((run + gap) / (min_w + gap))))
    candidates = []
    for n in range(n_min, n_max + 1):
        w = (run - gap * max(0, n - 1)) / n
        if min_w - 1e-6 <= w <= max_w + 1e-6:
            candidates.append((abs(w - target_w), -n, n, w))
    if not candidates:
        n = max(1, int(run // max(target_w, min_w)))
        w = (run - gap * max(0, n - 1)) / n if n else 0
        if n and min_w <= w <= max_w:
            return [w] * n
        return []
    _, _, n, w = min(candidates)
    return [w] * n


def _yield_road_orders(roads):
    main_first = sorted(roads, key=lambda r: (0 if r.get('kind') == 'main' else 1, -r['line'].length, r['id']))
    local_first = sorted(roads, key=lambda r: (0 if r.get('kind') == 'local' else 1, -r['line'].length, r['id']))
    return [('main-first', main_first), ('local-first/corner-rotation', local_first)]


def _pack_yield_lots(developable, roads, target_w, min_w, max_w, target_d, min_d, max_d, order_name, road_order):
    """M2.5.10: STANDARD packing is fixed by Geometry Settings.

    ``min/max`` remain in the request for backward compatibility, but they are
    NOT allowed to deform STANDARD lots.  Those ranges are used only by later
    residual/adaptive processing.
    """
    lots, meta, block_info = _pack_standard_blocks(
        developable, roads, float(target_w), float(target_d), road_priority=road_order
    )
    audit = _standard_geometry_audit(lots, meta, float(target_w), float(target_d))
    return lots, meta, {
        'packing_order': order_name,
        'strategy': 'road-block-standard-first',
        'standard_source': 'geometry_settings',
        'adaptive_source': 'residual_only',
        **block_info,
        **audit,
    }



def _adaptive_residual_frontage_fill(buildable, roads, roads_union, rth, psu, lots, meta, req: YieldOptimizeRequest):
    """Convert road-fronting residual polygons into real sellable lot polygons.

    This is deliberately geometry-based: every added lot is a polygon carved from actual
    unused developable land and must touch a road corridor. It does not reclassify land as
    reserve. Irregular edge lots are allowed only in this optimization pass and remain
    visible as ordinary lot polygons in the returned layout.
    """
    developable=_polygonal_only(buildable.difference(unary_union([g for g in (roads_union,rth,psu) if g is not None and not g.is_empty])))
    if developable.is_empty:
        return lots,meta,{'adaptive_residual_lots':0,'adaptive_residual_area_m2':0.0}
    out_lots=list(lots); out_meta=list(meta)
    adaptive_index=_LotGridIndex(max(req.max_lot_width_m,req.max_lot_depth_m)*1.5)
    for _g in out_lots:
        adaptive_index.add(_g)
    added_count=0; added_area=0.0
    # Two passes are enough: the second pass catches components split by the first pass.
    for _pass in range(2):
        lot_union=_polygonal_only(unary_union(out_lots)) if out_lots else Polygon()
        residual=_polygonal_only(developable.difference(lot_union))
        if residual.is_empty:
            break
        pass_added=0
        for comp in sorted(_poly_parts(residual),key=lambda g:g.area,reverse=True)[:30]:
            if comp.area < req.min_lot_width_m*req.min_lot_depth_m*0.45:
                continue
            touching=[]
            for road in roads:
                corridor=road.get('corridor')
                if corridor is None or corridor.is_empty:
                    corridor=_polygonal_only(road['line'].buffer(road['width_m']/2.0,cap_style=2,join_style=2).intersection(buildable))
                dist=comp.distance(corridor)
                if dist <= 0.35:
                    contact=comp.buffer(0.20).intersection(corridor.buffer(0.10)).area
                    touching.append((contact,road))
            if not touching:
                continue
            _,road=max(touching,key=lambda x:x[0])
            fr=_road_frame(road['line'])
            if not fr:
                continue
            angle=fr['angle']
            rc=affinity.rotate(comp,-angle,origin=(0,0))
            minx,miny,maxx,maxy=rc.bounds
            run=maxx-minx
            widths=_adaptive_widths(run,req.target_lot_width_m,req.min_lot_width_m,req.max_lot_width_m,gap=0.03)
            if not widths:
                continue
            x=minx
            for width in widths:
                strip=box(x,miny-0.20,x+width,maxy+0.20)
                x += width+0.03
                sliced=_safe_polygon_overlay(rc,strip,"intersection")
                if sliced.is_empty:
                    continue
                for part in _poly_parts(sliced):
                    lot=_polygonal_only(make_valid(affinity.rotate(part,angle,origin=(0,0))))
                    if lot.is_empty:
                        continue
                    # make_valid can split a numerically fragile slice; retain the largest polygonal part.
                    lparts=_poly_parts(lot)
                    if not lparts:
                        continue
                    lot=max(lparts,key=lambda g:g.area)
                    if lot.area < req.min_lot_width_m*req.min_lot_depth_m*0.50:
                        continue
                    if lot.distance(roads_union) > 0.40:
                        continue
                    # Reject needle-like scraps. We intentionally allow irregular corner lots,
                    # but still require a practical minimum short dimension.
                    rect=lot.minimum_rotated_rectangle
                    if rect.is_empty or not hasattr(rect,'exterior'):
                        continue
                    cs=list(rect.exterior.coords)
                    dims=[math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(cs,cs[1:])]
                    short=min(dims) if dims else 0.0
                    if short < max(4.5,req.min_lot_width_m*0.60):
                        continue
                    # `lot` is carved from current residual; overlap can only occur through
                    # numerical edge effects. Remove a tiny tolerance before accepting.
                    if adaptive_index.conflicts(lot,tol=0.05):
                        continue
                    adaptive_index.add(lot)
                    depth_est=lot.area/max(width,1e-9)
                    out_lots.append(lot)
                    out_meta.append({'road_id':road['id'],'side':0,'width_m':float(width),'depth_m':float(depth_est),'t_m':0.0,'adaptive_residual_lot':True,'parcel_type':'residual','source':'residual'})
                    added_count+=1; added_area+=lot.area; pass_added+=1
        if pass_added==0:
            break
    return out_lots,out_meta,{'adaptive_residual_lots':added_count,'adaptive_residual_area_m2':added_area}



# M2.5.8 saleability thresholds for irregular residual lots.
# These are deliberately internal defaults, not extra UI knobs.
RESIDUAL_MIN_AREA_M2 = 60.0
RESIDUAL_MIN_FRONTAGE_M = 4.0
RESIDUAL_MIN_SHORT_DIM_M = 3.0
RESIDUAL_MAX_ASPECT_RATIO = 8.0
RESIDUAL_MIN_FILL_RATIO = 0.15
GEOM_AREA_TOL_M2 = 0.05


def _frontage_length_estimate(lot, road_corridor, edge_tolerance_m: float = 0.08):
    """Measure real shared-edge frontage between a lot and a road corridor.

    M2.5.8 deliberately does NOT count simple proximity as frontage. A small
    precision snap is allowed only to reconcile millimetre-scale GIS noise.
    """
    if lot is None or lot.is_empty or road_corridor is None or road_corridor.is_empty:
        return 0.0
    try:
        lotv = _polygonal_only(make_valid(lot))
        corr = _polygonal_only(make_valid(road_corridor))
        if lotv.is_empty or corr.is_empty:
            return 0.0
        if _safe_polygon_overlay(lotv, corr, "intersection").area > GEOM_AREA_TOL_M2:
            return 0.0
        try:
            exact = float(lotv.boundary.intersection(corr.boundary).length)
        except GEOSException:
            exact = 0.0
        if exact >= 0.05:
            return exact

        # Snap both geometries to the same centimetre grid and retry. This can
        # heal numerical ring noise but cannot turn a merely nearby lot into
        # a frontage lot.
        try:
            lot_s = set_precision(lotv, 0.01, mode="valid_output")
            corr_s = set_precision(corr, 0.01, mode="valid_output")
            return float(lot_s.boundary.intersection(corr_s.boundary).length)
        except Exception:
            return 0.0
    except Exception:
        return 0.0


def _lot_shape_metrics(lot):
    """Return practical geometric metrics used by residual-lot validation."""
    rect = lot.minimum_rotated_rectangle
    dims = []
    if not rect.is_empty and hasattr(rect, 'exterior'):
        cs = list(rect.exterior.coords)
        dims = sorted([
            math.hypot(b[0]-a[0], b[1]-a[1])
            for a,b in zip(cs,cs[1:])
            if math.hypot(b[0]-a[0], b[1]-a[1]) > 0.01
        ])
    short = float(dims[0]) if dims else 0.0
    long = float(dims[-1]) if dims else 0.0
    bbox_area = max(short * long, 1e-9)
    fill_ratio = float(lot.area / bbox_area) if short > 0 and long > 0 else 0.0
    aspect_ratio = float(long / max(short, 1e-9)) if short > 0 else float("inf")
    return short, long, fill_ratio, aspect_ratio


def _best_true_frontage(lot, roads, buildable=None):
    """Find the road with the longest real shared-edge frontage for a lot."""
    best = (0.0, None, None)
    for road in roads or []:
        corridor = road.get('corridor')
        if corridor is None or corridor.is_empty:
            line = road.get('line')
            if line is None or line.is_empty:
                continue
            corridor = _polygonal_only(line.buffer(
                float(road.get('width_m', 6.0))/2.0,
                cap_style=2, join_style=2
            ))
            if buildable is not None and not buildable.is_empty:
                corridor = _safe_polygon_overlay(corridor, buildable, "intersection")
        frontage = _frontage_length_estimate(lot, corridor)
        if frontage > best[0]:
            best = (frontage, road, corridor)
    return best


def _residual_saleability(lot, roads, buildable, rth=None, psu=None):
    """Validate whether an irregular residual polygon is a real saleable parcel."""
    lot = _polygonal_only(make_valid(lot)) if lot is not None and not lot.is_empty else Polygon()
    reasons = []
    if lot.is_empty:
        return {
            'saleable': False, 'reasons': ['empty_geometry'], 'area_m2': 0.0,
            'frontage_m': 0.0, 'road_id': None, 'short_m': 0.0,
            'long_m': 0.0, 'fill_ratio': 0.0, 'aspect_ratio': 0.0,
        }

    area = float(lot.area)
    if area + 1e-6 < RESIDUAL_MIN_AREA_M2:
        reasons.append('area_below_minimum')

    if buildable is None or buildable.is_empty or not buildable.buffer(0.03).covers(lot):
        reasons.append('outside_buildable')

    frontage, road, corridor = _best_true_frontage(lot, roads, buildable)
    if frontage + 1e-6 < RESIDUAL_MIN_FRONTAGE_M:
        reasons.append('frontage_below_minimum')

    road_overlap = 0.0
    if corridor is not None and not corridor.is_empty:
        road_overlap = float(_safe_polygon_overlay(lot, corridor, "intersection").area)
    if road_overlap > GEOM_AREA_TOL_M2:
        reasons.append('road_overlap')

    obstacle_overlap = 0.0
    for obs in (rth, psu):
        if obs is None or obs.is_empty:
            continue
        obstacle_overlap += float(_safe_polygon_overlay(lot, obs, "intersection").area)
    if obstacle_overlap > GEOM_AREA_TOL_M2:
        reasons.append('rth_psu_overlap')

    short, long, fill_ratio, aspect_ratio = _lot_shape_metrics(lot)
    if short + 1e-6 < RESIDUAL_MIN_SHORT_DIM_M:
        reasons.append('sliver_short_dimension')
    if aspect_ratio > RESIDUAL_MAX_ASPECT_RATIO:
        reasons.append('aspect_ratio_too_high')
    if fill_ratio + 1e-9 < RESIDUAL_MIN_FILL_RATIO:
        reasons.append('shape_fill_ratio_too_low')

    return {
        'saleable': len(reasons) == 0,
        'reasons': reasons,
        'area_m2': area,
        'frontage_m': float(frontage),
        'road_id': road.get('id') if road else None,
        'short_m': short,
        'long_m': long,
        'fill_ratio': fill_ratio,
        'aspect_ratio': aspect_ratio,
        'road_overlap_m2': road_overlap,
        'obstacle_overlap_m2': obstacle_overlap,
    }


def _residual_parcelization_pass(buildable, roads, roads_union, rth, psu, lots, meta, req: YieldOptimizeRequest):
    """Turn only *saleable* road-fronting leftovers into irregular residual lots."""
    developable = _safe_polygon_overlay(
        buildable,
        _polygonal_only(unary_union([g for g in (roads_union,rth,psu) if g is not None and not g.is_empty])),
        "difference"
    )
    if developable.is_empty:
        return list(lots), list(meta), {
            'residual_parcel_count': 0,
            'residual_parcel_area_m2': 0.0,
            'rejected_residual_candidates': 0,
        }

    out_lots = list(lots)
    out_meta = list(meta)
    parcel_index = _LotGridIndex(max(req.max_lot_width_m, req.max_lot_depth_m) * 1.5)
    for _g in out_lots:
        parcel_index.add(_g)

    added_count = 0
    added_area = 0.0
    rejected = 0

    for _ in range(3):
        occupied = _polygonal_only(unary_union(out_lots)) if out_lots else Polygon()
        residual = _safe_polygon_overlay(developable, occupied, "difference") if not occupied.is_empty else developable
        if residual.is_empty:
            break

        changed = False
        for comp in sorted(_poly_parts(residual), key=lambda g:g.area, reverse=True)[:80]:
            if comp.area < RESIDUAL_MIN_AREA_M2:
                continue

            frontage, road, corridor = _best_true_frontage(comp, roads, buildable)
            if road is None:
                rejected += 1
                continue

            fr = _road_frame(road['line'])
            if not fr:
                rejected += 1
                continue

            # User intent for residual parcels: they do NOT need to copy the
            # standard lot module. Prefer the actual leftover polygon as one
            # parcel when it is already saleable. Split only when the whole
            # component fails practical shape/frontage rules.
            whole_check = _residual_saleability(comp, roads, buildable, rth, psu)
            if whole_check['saleable'] and not parcel_index.conflicts(comp, tol=GEOM_AREA_TOL_M2):
                pieces = [comp]
            else:
                angle = fr['angle']
                rc = affinity.rotate(comp, -angle, origin=(0,0))
                minx,miny,maxx,maxy = rc.bounds
                run = maxx-minx
                target_area = max(req.target_lot_width_m * req.target_lot_depth_m, 1.0)
                if comp.area <= target_area*4.0 or run <= req.target_lot_width_m*2.2:
                    pieces = [comp]
                else:
                    n = max(2, int(round(run/max(req.target_lot_width_m*1.35, 1.0))))
                    n = min(n, 24)
                    step = run/n
                    pieces = []
                    for i in range(n):
                        x0 = minx + i*step
                        x1 = maxx if i == n-1 else minx + (i+1)*step
                        part = _safe_polygon_overlay(rc, box(x0,miny-0.5,x1,maxy+0.5), "intersection")
                        for pr in _poly_parts(part):
                            pg = _polygonal_only(make_valid(affinity.rotate(pr, angle, origin=(0,0))))
                            if not pg.is_empty:
                                pieces.extend(_poly_parts(pg))

            for lot in pieces:
                if parcel_index.conflicts(lot, tol=GEOM_AREA_TOL_M2):
                    rejected += 1
                    continue
                check = _residual_saleability(lot, roads, buildable, rth, psu)
                if not check['saleable']:
                    rejected += 1
                    continue

                parcel_index.add(lot)
                out_lots.append(lot)
                out_meta.append({
                    'road_id': check['road_id'],
                    'side': 0,
                    'width_m': float(check['frontage_m']),
                    'depth_m': float(lot.area/max(check['frontage_m'],0.1)),
                    't_m': 0.0,
                    'parcel_type': 'residual',
                    'source': 'residual',
                    'residual_parcel': True,
                    'actual_area_m2': float(lot.area),
                    'frontage_m': float(check['frontage_m']),
                    'bbox_short_m': float(check['short_m']),
                    'bbox_long_m': float(check['long_m']),
                    'saleability_validated': True,
                    'access_status': 'road_frontage',
                })
                added_count += 1
                added_area += lot.area
                changed = True

        if not changed:
            break

    return out_lots, out_meta, {
        'residual_parcel_count': added_count,
        'residual_parcel_area_m2': added_area,
        'rejected_residual_candidates': rejected,
    }


def _final_cap_parcelization_sweep(buildable, roads, roads_union, rth, psu, lots, meta, parcel_area, req: YieldOptimizeRequest):
    """Final sweep: only saleable leftovers may reduce TRUE residual."""
    cap_area = max(0.0, float(parcel_area) * 0.03)
    developable = _safe_polygon_overlay(
        _polygonal_only(make_valid(buildable)),
        _polygonal_only(unary_union([g for g in (roads_union,rth,psu) if g is not None and not g.is_empty])),
        'difference'
    )
    if developable.is_empty:
        return list(lots), list(meta), {
            'final_cap_parcels': 0,
            'final_cap_parcel_area_m2': 0.0,
            'rejected_final_cap_candidates': 0,
        }

    out_lots = list(lots)
    out_meta = list(meta)
    occupied = _polygonal_only(unary_union(out_lots)) if out_lots else Polygon()
    residual = _safe_polygon_overlay(developable, occupied, 'difference') if not occupied.is_empty else developable

    if residual.is_empty or residual.area <= cap_area + 0.25:
        return out_lots, out_meta, {
            'final_cap_parcels': 0,
            'final_cap_parcel_area_m2': 0.0,
            'rejected_final_cap_candidates': 0,
            'true_residual_after_m2': float(residual.area if residual is not None and not residual.is_empty else 0.0),
        }

    added = 0
    added_area = 0.0
    rejected = 0
    index = _LotGridIndex(max(req.max_lot_width_m, req.max_lot_depth_m) * 1.5)
    for g in out_lots:
        index.add(g)

    for comp in sorted(_poly_parts(residual), key=lambda g:g.area, reverse=True):
        if residual.area <= cap_area + 0.25:
            break
        comp = _polygonal_only(make_valid(comp))
        if comp.is_empty or comp.area < RESIDUAL_MIN_AREA_M2:
            rejected += 1
            continue
        # `comp` comes from developable - occupied, so it is already a vacancy.
        # Do not reject a valid residual component merely because shared-boundary
        # precision produces a tiny spatial-index intersection.
        check = _residual_saleability(comp, roads, buildable, rth, psu)
        if not check['saleable']:
            rejected += 1
            continue

        index.add(comp)
        out_lots.append(comp)
        out_meta.append({
            'road_id': check['road_id'],
            'side': 0,
            'width_m': float(check['frontage_m']),
            'depth_m': float(comp.area/max(check['frontage_m'],0.1)),
            't_m': 0.0,
            'parcel_type': 'residual',
            'source': 'residual',
            'residual_parcel': True,
            'final_cap_parcel': True,
            'actual_area_m2': float(comp.area),
            'frontage_m': float(check['frontage_m']),
            'bbox_short_m': float(check['short_m']),
            'bbox_long_m': float(check['long_m']),
            'saleability_validated': True,
            'access_status': 'road_frontage',
        })
        added += 1
        added_area += comp.area

        occupied = _polygonal_only(unary_union(out_lots))
        residual = _safe_polygon_overlay(developable, occupied, 'difference')
        if residual.is_empty:
            break

    return out_lots, out_meta, {
        'final_cap_parcels': added,
        'final_cap_parcel_area_m2': added_area,
        'rejected_final_cap_candidates': rejected,
        'true_residual_after_m2': float(residual.area if residual is not None and not residual.is_empty else 0.0),
    }


def _lot_detail_records(lots, meta):
    details=[]
    residual_no=0
    for i,(g,m) in enumerate(zip(lots,meta)):
        ptype='residual' if (m.get('parcel_type')=='residual' or m.get('adaptive_residual_lot') or m.get('residual_parcel')) else 'standard'
        if ptype=='residual': residual_no+=1
        rect=g.minimum_rotated_rectangle
        dims=[]
        if not rect.is_empty and hasattr(rect,'exterior'):
            cs=list(rect.exterior.coords)
            dims=sorted([math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(cs,cs[1:]) if math.hypot(b[0]-a[0],b[1]-a[1])>0.05])
        short=dims[0] if dims else 0.0; long=dims[-1] if dims else 0.0
        frontage=float(m['frontage_m'] if 'frontage_m' in m else (m.get('width_m') or short))
        depth=float(m.get('depth_m') or (g.area/max(frontage,0.1)))
        details.append({
            'index':i,'id':f'R-{residual_no:02d}' if ptype=='residual' else f'K-{i+1:03d}',
            'parcel_type':ptype,'area_m2':round(g.area,2),'perimeter_m':round(g.length,2),
            'frontage_m':round(frontage,2),'depth_est_m':round(depth,2),
            'min_rect_short_m':round(short,2),'min_rect_long_m':round(long,2),
            'road_id':m.get('road_id'),'access_status':m.get('access_status','road_frontage'),
        })
    return details



def _filter_unsaleable_residual_lots(lots, meta, roads, buildable, rth=None, psu=None):
    """Return invalid residual parcels to TRUE residual instead of counting them as lots."""
    kept_lots=[]
    kept_meta=[]
    rejected=[]
    for i,g in enumerate(lots or []):
        m=meta[i] if i < len(meta) else {}
        is_residual=(
            m.get('parcel_type')=='residual'
            or m.get('adaptive_residual_lot')
            or m.get('residual_parcel')
        )
        if not is_residual:
            kept_lots.append(g); kept_meta.append(m)
            continue
        check=_residual_saleability(g,roads,buildable,rth,psu)
        if not check['saleable']:
            rejected.append({
                'index':i,
                'area_m2':round(float(g.area),2),
                'frontage_m':round(float(check.get('frontage_m',0.0)),2),
                'reasons':list(check.get('reasons',[])),
            })
            continue
        nm=dict(m)
        nm['parcel_type']='residual'
        nm['source']='residual'
        nm['residual_parcel']=True
        nm['saleability_validated']=True
        nm['frontage_m']=float(check['frontage_m'])
        nm['width_m']=float(check['frontage_m'])
        nm['road_id']=check['road_id']
        nm['access_status']='road_frontage'
        kept_lots.append(g); kept_meta.append(nm)
    return kept_lots,kept_meta,rejected


def _final_siteplan_acceptance(buildable, roads, roads_union, lots, meta, rth, psu, parcel_area, residual_pct_total=0.0, base_lot_count=0, target_lot_width_m=None, target_lot_depth_m=None):
    """Final hard gate for M2.5.12 layouts.

    A layout is accepted only when:
    1. Gross Lot Efficiency >= 70.0% of total land area
    2. Invalid standard lots == 0 (exact modular compliance)
    3. No lot overlap pairs (overlap_pairs == 0)
    4. No lots outside buildable (lots_outside_buildable == 0)
    5. No lot/road overlaps (lot_road_overlaps == 0)
    6. No lot/obstacle overlaps (lot_obstacle_overlaps == 0)
    7. No RTH/PSU overlap (rth_psu_overlap == 0)
    8. All lots have valid frontage/access (invalid_residual_lot_count == 0)
    9. Adaptive origin violation == 0 (adaptive only from residual)
    10. Lot count preserved (standard lots preserved)
    Residual is informational only.
    """
    valid_lots = []
    for g in lots or []:
        if g is None or g.is_empty:
            valid_lots.append(Polygon())
        else:
            valid_lots.append(_polygonal_only(make_valid(g)))

    outside = 0
    lot_road_overlaps = 0
    lot_obstacle_overlaps = 0
    invalid_residual_lots = []
    road_overlap_area = 0.0
    obstacle_overlap_area = 0.0

    standard_indices = []
    adaptive_indices = []

    for i, g in enumerate(valid_lots):
        if g.is_empty:
            outside += 1
            continue
        if not buildable.buffer(0.001).covers(g):
            outside += 1

        if roads_union is not None and not roads_union.is_empty:
            a = float(_safe_polygon_overlay(g, roads_union, "intersection").area)
            if a > GEOM_AREA_TOL_M2:
                lot_road_overlaps += 1
                road_overlap_area += a

        for obs in (rth, psu):
            if obs is None or obs.is_empty:
                continue
            a = float(_safe_polygon_overlay(g, obs, "intersection").area)
            if a > GEOM_AREA_TOL_M2:
                lot_obstacle_overlaps += 1
                obstacle_overlap_area += a
                break

        m = meta[i] if i < len(meta) else {}
        is_residual = (
            m.get('parcel_type') == 'residual'
            or m.get('adaptive_residual_lot')
            or m.get('residual_parcel')
            or m.get('source') == 'residual'
        )
        if is_residual:
            adaptive_indices.append(i)
            check = _residual_saleability(g, roads, buildable, rth, psu)
            if not check['saleable']:
                invalid_residual_lots.append({
                    'index': i,
                    'area_m2': round(float(g.area), 2),
                    'frontage_m': round(float(check.get('frontage_m', 0.0)), 2),
                    'reasons': list(check.get('reasons', [])),
                })
            else:
                if i < len(meta):
                    meta[i]['frontage_m'] = float(check['frontage_m'])
                    meta[i]['width_m'] = float(check['frontage_m'])
                    meta[i]['road_id'] = check['road_id']
                    meta[i]['access_status'] = 'road_frontage'
                    meta[i]['saleability_validated'] = True
        else:
            standard_indices.append(i)

    overlap_pairs = 0
    nonempty = [(i, g) for i, g in enumerate(valid_lots) if not g.is_empty]
    geoms = [g for _, g in nonempty]
    if len(geoms) > 1:
        try:
            tree = STRtree(geoms)
            pairs = tree.query(geoms, predicate='intersects')
            if getattr(pairs, 'shape', None) is not None and len(pairs.shape) == 2:
                seen = set()
                for a, b in zip(pairs[0].tolist(), pairs[1].tolist()):
                    a, b = int(a), int(b)
                    if a >= b:
                        continue
                    key = (a, b)
                    if key in seen:
                        continue
                    seen.add(key)
                    if _safe_polygon_overlay(geoms[a], geoms[b], "intersection").area > GEOM_AREA_TOL_M2:
                        overlap_pairs += 1
            else:
                for i, g in enumerate(geoms):
                    for j in tree.query(g):
                        j = int(j)
                        if j <= i:
                            continue
                        if _safe_polygon_overlay(g, geoms[j], "intersection").area > GEOM_AREA_TOL_M2:
                            overlap_pairs += 1
        except Exception:
            for i, g in enumerate(geoms):
                for j in range(i + 1, len(geoms)):
                    if _safe_polygon_overlay(g, geoms[j], "intersection").area > GEOM_AREA_TOL_M2:
                        overlap_pairs += 1

    rth_psu_overlap = 0
    if rth is not None and psu is not None and not rth.is_empty and not psu.is_empty:
        if _safe_polygon_overlay(rth, psu, "intersection").area > GEOM_AREA_TOL_M2:
            rth_psu_overlap = 1

    standard_area = sum(valid_lots[i].area for i in standard_indices)
    adaptive_area = sum(valid_lots[i].area for i in adaptive_indices)
    total_lot_area = standard_area + adaptive_area
    lot_efficiency_pct = round((total_lot_area / parcel_area * 100.0) if parcel_area else 0.0, 2)
    lot_efficiency_met = bool(lot_efficiency_pct >= 70.0 - 1e-4)

    lot_count_preserved = len([g for g in valid_lots if not g.is_empty]) >= int(base_lot_count or 0)
    if target_lot_width_m and target_lot_depth_m:
        standard_audit = _standard_geometry_audit(valid_lots, meta, float(target_lot_width_m), float(target_lot_depth_m))
    else:
        standard_audit = {'invalid_standard_lot_count': 0, 'invalid_standard_lots': []}

    adaptive_origin_violations = []
    for i, m in enumerate(meta or []):
        is_residual = m.get('parcel_type') == 'residual' or m.get('adaptive_residual_lot') or m.get('residual_parcel') or m.get('source') == 'residual'
        if is_residual:
            if m.get('source') not in (None, 'residual'):
                adaptive_origin_violations.append(i)
        else:
            if m.get('source') == 'residual':
                adaptive_origin_violations.append(i)

    residual_true_pct = round(float(residual_pct_total), 2)
    residual_true_area = round((parcel_area * float(residual_pct_total) / 100.0) if parcel_area else 0.0, 2)

    result = {
        'lot_efficiency_pct': lot_efficiency_pct,
        'lot_efficiency_target_pct': 70.0,
        'lot_efficiency_met': lot_efficiency_met,
        'standard_lot_count': len(standard_indices),
        'adaptive_lot_count': len(adaptive_indices),
        'standard_lot_area_m2': round(standard_area, 2),
        'adaptive_lot_area_m2': round(adaptive_area, 2),
        'invalid_standard_lot_count': int(standard_audit.get('invalid_standard_lot_count', 0)),
        'invalid_standard_lots': standard_audit.get('invalid_standard_lots', [])[:50],
        'adaptive_origin_violation_count': len(adaptive_origin_violations),
        'adaptive_origin_violations': adaptive_origin_violations[:50],
        'lot_overlap_pairs': overlap_pairs,
        'lot_road_overlaps': lot_road_overlaps,
        'lot_road_overlap_area_m2': round(road_overlap_area, 3),
        'lot_obstacle_overlaps': lot_obstacle_overlaps,
        'lot_obstacle_overlap_area_m2': round(obstacle_overlap_area, 3),
        'lots_outside_buildable': outside,
        'rth_psu_overlap': rth_psu_overlap,
        'invalid_residual_lot_count': len(invalid_residual_lots),
        'invalid_residual_lots': invalid_residual_lots[:50],
        'residual_true_area_m2': residual_true_area,
        'residual_true_pct': residual_true_pct,
        'lot_count_preserved': lot_count_preserved,
        'base_lot_count': int(base_lot_count or 0),
        'final_lot_count': len([g for g in valid_lots if not g.is_empty]),
    }
    result['valid'] = bool(
        result['lot_efficiency_met']
        and outside == 0
        and overlap_pairs == 0
        and lot_road_overlaps == 0
        and lot_obstacle_overlaps == 0
        and rth_psu_overlap == 0
        and len(invalid_residual_lots) == 0
        and result['invalid_standard_lot_count'] == 0
        and result['adaptive_origin_violation_count'] == 0
        and lot_count_preserved
    )
    return result


def _roads_union(roads, buildable):
    corridors=[]
    for r in roads:
        c=_polygonal_only(r['line'].buffer(r['width_m']/2.0, cap_style=2, join_style=2).intersection(buildable))
        r['corridor']=c
        if not c.is_empty: corridors.append(c)
    return _polygonal_only(unary_union(corridors)) if corridors else Polygon()


def _facility_candidates(buildable, roads_union, parcel_area, req: YieldOptimizeRequest, current_rth=None, current_psu=None):
    available = _polygonal_only(buildable.difference(roads_union))
    target_rth = parcel_area * req.rth_pct / 100.0
    target_psu = parcel_area * req.psu_pct / 100.0
    out=[]
    if current_rth is not None and current_psu is not None and not current_rth.is_empty and not current_psu.is_empty:
        rr=_polygonal_only(current_rth.intersection(available)); pp=_polygonal_only(current_psu.intersection(available.difference(rr)))
        if rr.area >= target_rth*0.99 and pp.area >= target_psu*0.99: out.append(('current',rr,pp))
    if not req.allow_rth_psu_relocation:
        return out or [('none',Polygon(),Polygon())]
    # First try to consume land that is too far from frontage to be valuable for lots.
    low_yield = _polygonal_only(available.difference(roads_union.buffer(req.max_lot_depth_m + req.max_lot_width_m*0.75)))
    if low_yield.area >= min(target_rth+target_psu, available.area*0.9):
        rr=_take_target_area(low_yield,target_rth)
        remain=_polygonal_only(low_yield.difference(rr))
        pp=_take_target_area(remain,target_psu)
        if rr.area>0 or target_rth<=0:
            out.append(('residual-first',rr,pp))
    for rside, pside in [('top','bottom'),('bottom','top'),('left','right'),('right','left'),('top','right'),('left','bottom')]:
        rr=_reserve_strip(available,target_rth,rside)
        rem=_polygonal_only(available.difference(rr))
        pp=_reserve_strip(rem,target_psu,pside)
        out.append((f'edge-{rside}-{pside}',rr,pp))
    # dedupe near-identical facility geometries
    uniq=[]
    seen=set()
    for name,r,p in out:
        key=(round(r.area,1),round(p.area,1),round(r.centroid.x if not r.is_empty else 0,1),round(r.centroid.y if not r.is_empty else 0,1),round(p.centroid.x if not p.is_empty else 0,1),round(p.centroid.y if not p.is_empty else 0,1))
        if key in seen: continue
        seen.add(key); uniq.append((name,r,p))
    return uniq[:4]


def _yield_stats(buildable, parcel_area, roads_union, rth, psu, lots, reserve=None):
    buildable=_polygonal_only(make_valid(buildable)) if not buildable.is_valid else _polygonal_only(buildable)
    roads_union=_polygonal_only(make_valid(roads_union)) if roads_union is not None and not roads_union.is_empty and not roads_union.is_valid else (roads_union if roads_union is not None else Polygon())
    rth=_polygonal_only(make_valid(rth)) if rth is not None and not rth.is_empty and not rth.is_valid else (rth if rth is not None else Polygon())
    psu=_polygonal_only(make_valid(psu)) if psu is not None and not psu.is_empty and not psu.is_valid else (psu if psu is not None else Polygon())
    reserve=reserve if reserve is not None else Polygon()
    if not reserve.is_empty and not reserve.is_valid: reserve=_polygonal_only(make_valid(reserve))
    clean_lots=[]
    for g in lots or []:
        if g is None or g.is_empty: continue
        gg=_polygonal_only(make_valid(g)) if not g.is_valid else _polygonal_only(g)
        if not gg.is_empty: clean_lots.append(gg)
    lot_union = _polygonal_only(unary_union(clean_lots)) if clean_lots else Polygon()
    occupied = _polygonal_only(unary_union([g for g in (roads_union,rth,psu,reserve,lot_union) if g is not None and not g.is_empty]))
    residual = _polygonal_only(buildable.difference(occupied))
    lots_area=sum(g.area for g in clean_lots)
    net=max(buildable.area-roads_union.area-(rth.area if rth is not None else 0)-(psu.area if psu is not None else 0)-(reserve.area if reserve is not None else 0),1e-9)
    return {
        'lot_count':len(clean_lots), 'lots_total_area_m2':lots_area,
        'road_area_m2':roads_union.area, 'rth_area_m2':rth.area if rth is not None else 0,
        'psu_area_m2':psu.area if psu is not None else 0, 'reserve_area_m2':reserve.area if reserve is not None else 0, 'residual_area_m2':residual.area,
        'land_utilization_pct':lots_area/net*100.0,
        'residual_ratio_pct':residual.area/buildable.area*100.0 if buildable.area else 0,
        'residual_pct_total_land':residual.area/parcel_area*100.0 if parcel_area else 0,
        'road_efficiency':lots_area/roads_union.area if roads_union.area else 0,
        'lot_efficiency_pct':lots_area/parcel_area*100.0 if parcel_area else 0,
        'residual':residual,
    }


def _yield_score(stats, meta, req: YieldOptimizeRequest):
    """M2.5.12 Lexicographic planning objective:
    1) Hard: gross lot efficiency >= 70.0%
    2) Maximize exact STANDARD lots from Geometry Settings
    3) Maximize lot efficiency
    4) Minimize adaptive/residual lots and remaining waste
    5) Minimize road area
    6) Minimize residual
    """
    standard_count = sum(1 for m in meta if m.get('parcel_type') != 'residual')
    adaptive_count = sum(1 for m in meta if m.get('parcel_type') == 'residual')
    lot_efficiency = float(stats.get('lot_efficiency_pct', 0.0))
    efficiency_met = lot_efficiency >= 70.0 - 1e-4
    return (
        (1_000_000_000.0 if efficiency_met else 0.0)
        + standard_count * 1_000_000.0
        + lot_efficiency * 100_000.0
        - adaptive_count * 10_000.0
        + stats.get('lots_total_area_m2', 0.0) * 20.0
        - stats.get('road_area_m2', 0.0) * 5.0
        - stats.get('residual_area_m2', 0.0) * 2.0
    )


def _translated_roads(base_roads, buildable, dx, dy):
    out=[]
    for r in base_roads:
        line=affinity.translate(r['line'],xoff=dx,yoff=dy).intersection(buildable)
        parts=_line_parts(line)
        if not parts: continue
        line=max(parts,key=lambda g:g.length)
        out.append({'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'line':line})
    return out


def _evaluate_yield_network(roads, buildable, parcel_area, req, current_rth, current_psu, shift_label='baseline'):
    roads=[dict(r) for r in roads]
    road_union=_roads_union(roads,buildable)
    best=None
    target_rth = parcel_area * req.rth_pct / 100.0
    target_psu = parcel_area * req.psu_pct / 100.0
    for facility_name,rth,psu in _facility_candidates(buildable,road_union,parcel_area,req,current_rth,current_psu):
        if (target_rth > 0 and rth.area < target_rth*0.985) or (target_psu > 0 and psu.area < target_psu*0.985):
            continue
        developable=_polygonal_only(buildable.difference(unary_union([g for g in (road_union,rth,psu) if g is not None and not g.is_empty])))
        if developable.is_empty: continue
        for order_name,road_order in _yield_road_orders(roads):
            lots,meta,pack_info=_pack_yield_lots(developable,roads,req.target_lot_width_m,req.min_lot_width_m,req.max_lot_width_m,
                                                 req.target_lot_depth_m,req.min_lot_depth_m,req.max_lot_depth_m,order_name,road_order)
            lots,meta,adaptive_info=_adaptive_residual_frontage_fill(buildable,roads,road_union,rth,psu,lots,meta,req)
            # M2.5.8: do not let weak adaptive scraps fragment the residual field.
            # Remove unsaleable adaptive residual lots first, then parcelize the clean
            # leftover geometry into properly validated irregular lots.
            lots,meta,rejected_adaptive=_filter_unsaleable_residual_lots(
                lots,meta,roads,buildable,rth,psu
            )
            lots,meta,parcel_info=_residual_parcelization_pass(buildable,roads,road_union,rth,psu,lots,meta,req)
            adaptive_info={
                **adaptive_info,
                **parcel_info,
                'rejected_unsaleable_adaptive':len(rejected_adaptive),
            }
            stats=_yield_stats(buildable,parcel_area,road_union,rth,psu,lots)
            score=_yield_score(stats,meta,req)
            rec={'roads':roads,'roads_union':road_union,'rth':rth,'psu':psu,'reserve':Polygon(),'lots':lots,'lot_meta':meta,'stats':stats,
                 'score':score,'facility_strategy':facility_name,'packing_order':pack_info['packing_order'],'road_shift':shift_label,
                 'adaptive_info':adaptive_info}
            if best is None or score>best['score']: best=rec
    return best


def _residual_records(residual, road_union, req, epsg, limit=80):
    parts=sorted(_poly_parts(residual),key=lambda g:g.area,reverse=True)
    out=[]
    min_lot=req.min_lot_width_m*req.min_lot_depth_m
    for i,g in enumerate(parts[:limit]):
        d=g.distance(road_union) if road_union is not None and not road_union.is_empty else 9999
        if g.area >= min_lot*2.5: cls='large_residual'
        elif g.area >= min_lot*0.80 and d <= req.max_lot_depth_m+req.max_lot_width_m: cls='potential_lot'
        else: cls='small_residual'
        out.append({'id':f'RES-{i+1}','area_m2':round(g.area,2),'distance_to_road_m':round(d,2),'classification':cls,
                    'geometry':mapping(to_wgs84(g,epsg))})
    return out


def _polygon_major_axis(poly):
    """Return a long line through a polygon along its minimum-rectangle major axis."""
    poly=_polygonal_only(poly)
    if poly.is_empty:
        return LineString()
    rect=poly.minimum_rotated_rectangle
    if rect.is_empty or not hasattr(rect,'exterior'):
        return LineString()
    coords=list(rect.exterior.coords)
    edges=[]
    for a,b in zip(coords,coords[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=math.hypot(dx,dy)
        if length>1e-6:
            edges.append((length,dx/length,dy/length))
    if not edges:
        return LineString()
    length,ux,uy=max(edges,key=lambda x:x[0])
    c=poly.centroid
    span=max(length*1.4, math.sqrt(max(poly.area,1.0))*2.0)
    raw=LineString([(c.x-ux*span,c.y-uy*span),(c.x+ux*span,c.y+uy*span)])
    clipped=raw.intersection(poly.buffer(-0.20))
    parts=_line_parts(clipped)
    return max(parts,key=lambda g:g.length) if parts else LineString()


def _extension_line_candidates(comp, roads_union, buildable, req):
    """Generate several plausible local-road branches for one large residual."""
    comp=_polygonal_only(comp)
    if comp.is_empty or roads_union is None or roads_union.is_empty:
        return []
    c=comp.centroid
    p0,_=nearest_points(roads_union,c)
    major=_polygon_major_axis(comp)
    raw=[]

    # A. Direct connection from existing road into the residual centroid.
    raw.append(LineString([(p0.x,p0.y),(c.x,c.y)]))

    # B. Connection plus a spine across the major axis of the residual.
    if not major.is_empty:
        mcoords=list(major.coords)
        if len(mcoords)>=2:
            a,b=Point(mcoords[0]),Point(mcoords[-1])
            near_end=a if a.distance(p0)<=b.distance(p0) else b
            far_end=b if near_end.equals(a) else a
            raw.append(LineString([(p0.x,p0.y),(near_end.x,near_end.y),(far_end.x,far_end.y)]))
            raw.append(LineString([(p0.x,p0.y),(c.x,c.y),(far_end.x,far_end.y)]))

    # C. A branch aimed slightly past the centroid to give a double-loaded frontage run.
    vx,vy=c.x-p0.x,c.y-p0.y
    norm=math.hypot(vx,vy)
    if norm>1e-6:
        ux,uy=vx/norm,vy/norm
        extension=max(req.target_lot_depth_m*2.0,min(math.sqrt(comp.area)*1.25,req.target_lot_depth_m*8.0))
        raw.append(LineString([(p0.x,p0.y),(c.x+ux*extension,c.y+uy*extension)]))

    obstacle_free=_polygonal_only(buildable)
    out=[]; seen=set()
    for line in raw:
        clipped=line.intersection(obstacle_free)
        parts=_line_parts(clipped)
        if not parts:
            continue
        line=max(parts,key=lambda g:g.length)
        if line.length < req.min_lot_depth_m*1.15:
            continue
        coords=list(line.coords)
        key=(round(coords[0][0],1),round(coords[0][1],1),round(coords[-1][0],1),round(coords[-1][1],1))
        if key in seen:
            continue
        seen.add(key); out.append(line)
    return out[:4]


def _try_selective_extension(best, buildable, parcel_area, req, current_rth, current_psu, deadline=None):
    if not req.allow_selective_extension or req.max_extensions <= 0 or best is None:
        return best, []
    actions=[]
    current=best
    cap_area=parcel_area*req.max_residual_pct_total/100.0
    for ext_no in range(req.max_extensions):
        if deadline is not None and time.monotonic() >= deadline: break
        residual=current['stats']['residual']
        if residual.area <= cap_area + 0.5:
            break
        parts=[g for g in sorted(_poly_parts(residual),key=lambda g:g.area,reverse=True) if g.area >= req.min_lot_width_m*req.min_lot_depth_m*3.0]
        if not parts: break
        improved=None
        for comp in parts[:2]:
            if deadline is not None and time.monotonic() >= deadline: break
            roads_union=current['roads_union']
            if roads_union.is_empty: continue
            for ci,line in enumerate(_extension_line_candidates(comp,roads_union,buildable,req)[:2],start=1):
                if deadline is not None and time.monotonic() >= deadline: break
                new_roads=[{'id':r['id'],'kind':r['kind'],'width_m':r['width_m'],'line':r['line']} for r in current['roads']]
                rid=f'EXT{ext_no+1}_{ci}'
                new_roads.append({'id':rid,'kind':'local','width_m':req.local_road_width_m,'line':line})
                cand=_evaluate_yield_network(new_roads,buildable,parcel_area,req,current['rth'],current['psu'],shift_label=current['road_shift'])
                if not cand:
                    continue
                # Residual-cap progress is primary. Score remains the tie-breaker.
                cur_res=current['stats']['residual_area_m2']
                new_res=cand['stats']['residual_area_m2']
                progress=cur_res-new_res
                if progress < max(25.0, req.min_lot_width_m*req.min_lot_depth_m*0.35) and cand['score'] <= current['score']:
                    continue
                key=(1 if cand['stats'].get('residual_pct_total_land',999) <= req.max_residual_pct_total else 0, -new_res, cand['score'])
                if improved is None or key > improved['_pick_key']:
                    cand['_pick_key']=key; cand['_extension_road_id']=rid; improved=cand
        if improved is None: break
        actions.append({'type':'selective_road_extension','road_id':improved.pop('_extension_road_id',f'EXT{ext_no+1}'),
                        'score_gain':round(improved['score']-current['score'],2),
                        'residual_reduction_m2':round(current['stats']['residual_area_m2']-improved['stats']['residual_area_m2'],2)})
        improved.pop('_pick_key',None)
        current=improved
    return current,actions


def _enforce_residual_cap(best, buildable, parcel_area, req):
    """Report the TRUE residual cap. No RTH/reserve relabeling is allowed in M2.5.7."""
    if best is None:
        return best, {'cap_met':False,'reason':'no_candidate'}
    cap_area=max(0.0,parcel_area*req.max_residual_pct_total/100.0)
    stats=best['stats']
    before=float(stats['residual_area_m2'])
    return best,{
        'target_pct_total':req.max_residual_pct_total,'cap_area_m2':cap_area,
        'residual_before_cap_m2':before,'residual_after_cap_m2':before,
        'residual_after_pct_total':stats.get('residual_pct_total_land',0.0),
        'absorbed_to_rth_m2':0.0,'allocated_to_landscape_reserve_m2':0.0,
        'cap_met':before <= cap_area + 0.5,'method':'residual-parcelization-only',
    }

def _network_geo_output(rec, buildable, epsg):
    road_parts=[]; drains=[]; segs=[]; road_len=0.0; drain_len=0.0
    for r in rec['roads']:
        line=_line_primary(r['line'])
        if line.is_empty: continue
        corridor=_polygonal_only(line.buffer(r['width_m']/2.0,cap_style=2,join_style=2).intersection(buildable))
        ds=[]
        for side in ('left','right'):
            try: d=line.parallel_offset(r['width_m']/2.0,side,join_style=2).intersection(buildable)
            except Exception: d=GeometryCollection()
            if not d.is_empty:
                ds.append(d); drains.append(d); drain_len += d.length
        road_parts.append(corridor); road_len += line.length
        fr=_road_frame(line)
        segs.append({'id':r['id'],'kind':r['kind'],'width_m':round(r['width_m'],2),'angle_deg':round((fr['angle']%180) if fr else 0,2),
                     'length_m':round(line.length,2),'centerline':mapping(to_wgs84(line,epsg)),
                     'polygon':mapping(to_wgs84(corridor,epsg)) if not corridor.is_empty else None,
                     'drainage':mapping(to_wgs84(unary_union(ds),epsg)) if ds else None})
    roads_union=_polygonal_only(unary_union(road_parts)) if road_parts else Polygon()
    drainage=unary_union(drains) if drains else GeometryCollection()
    return roads_union,drainage,segs,road_len,drain_len


def optimize_land_utilization(req: YieldOptimizeRequest):
    """M2.5.11 residual-only optimizer.

    STANDARD lots, roads, RTH and PSU are immutable here.  Masterplan topology
    is optimized during /site-plan/generate candidate search; this endpoint may
    only convert genuine leftover land into validated ADAPTIVE parcels.
    """
    req.max_residual_pct_total = 3.0
    req.strict_residual_cap = True
    req.allow_road_shift = False
    req.allow_rth_psu_relocation = False
    req.allow_selective_extension = False
    req.allow_residual_rth_absorption = False
    base_w = float(req.target_lot_width_m if req.target_lot_width_m >= 4.0 else 8.0)
    base_d = float(req.target_lot_depth_m if req.target_lot_depth_m >= 8.0 else 15.0)
    req.target_lot_width_m = base_w
    req.target_lot_depth_m = base_d
    req.min_lot_width_m = max(2.5, base_w * 0.70)
    req.max_lot_width_m = max(base_w, base_w * 1.35)
    req.min_lot_depth_m = max(4.0, base_d * 0.60)
    req.max_lot_depth_m = max(base_d, base_d * 1.40)

    optimize_started = time.monotonic()
    smart = SmartReflowRequest(
        parcel=req.parcel, buildable=req.buildable, road_segments=req.road_segments,
        lots=req.lots, rth=req.rth, psu=req.psu,
        lot_width_m=req.target_lot_width_m, lot_depth_m=req.target_lot_depth_m,
    )
    epsg, parcel, buildable, roads, incoming_lots, current_rth, current_psu = _prepare_editor_state(smart)
    if not roads:
        raise HTTPException(422, 'Tidak ada road segment yang dapat dioptimalkan')
    parcel_area = parcel.area
    rth = current_rth or Polygon()
    psu = current_psu or Polygon()
    roads_union = _roads_union(roads, buildable)

    # Re-optimization discards old ADAPTIVE parcels and rebuilds them strictly
    # from current TRUE residual. STANDARD geometry is copied byte-for-byte.
    standard_lots = []
    standard_meta = []
    incoming_details = list(req.lot_details or [])
    for i, g in enumerate(incoming_lots):
        if g is None or g.is_empty:
            continue
        d = incoming_details[i] if i < len(incoming_details) else {}
        is_adaptive = d.get('parcel_type') == 'residual' or d.get('source') == 'residual'
        if is_adaptive:
            continue
        road_id = d.get('road_id')
        if not road_id:
            road_id, _ = _nearest_road_id(g, roads)
        standard_lots.append(g)
        standard_meta.append({
            'road_id': road_id,
            'parcel_type': 'standard',
            'source': 'geometry_settings',
            'width_m': base_w,
            'depth_m': base_d,
            'frontage_m': base_w,
            'standard_width_m': base_w,
            'standard_depth_m': base_d,
        })

    if not standard_lots:
        raise HTTPException(422, 'Tidak ada Kavling Standar pada opsi terpilih')

    immutable_audit = _standard_geometry_audit(standard_lots, standard_meta, base_w, base_d)
    if immutable_audit['invalid_standard_lot_count']:
        raise HTTPException(422, detail={
            'message': 'Baseline mempunyai Kavling Standar yang tidak sesuai Geometry Settings',
            'validation': immutable_audit,
            'hint': 'Pilih ulang alternatif masterplan yang valid; Residual Optimizer tidak akan memperbaiki atau mengubah Kavling Standar.'
        })

    base_stats = _yield_stats(buildable, parcel_area, roads_union, rth, psu, standard_lots)
    lots, meta, parcel_info = _residual_parcelization_pass(
        buildable, roads, roads_union, rth, psu, standard_lots, standard_meta, req
    )
    lots, meta, rejected_pre = _filter_unsaleable_residual_lots(lots, meta, roads, buildable, rth, psu)
    lots, meta, sweep_info = _final_cap_parcelization_sweep(
        buildable, roads, roads_union, rth, psu, lots, meta, parcel_area, req
    )
    lots, meta, rejected_post = _filter_unsaleable_residual_lots(lots, meta, roads, buildable, rth, psu)

    stats = _yield_stats(buildable, parcel_area, roads_union, rth, psu, lots)
    final_residual_pct_total = float(stats.get('residual_pct_total_land', 0.0))
    cap_met = final_residual_pct_total <= 3.01
    final_validation = _final_siteplan_acceptance(
        buildable, roads, roads_union, lots, meta, rth, psu, parcel_area,
        final_residual_pct_total,
        base_lot_count=len(standard_lots),
        target_lot_width_m=base_w,
        target_lot_depth_m=base_d,
    )
    final_standard_count = sum(1 for m in meta if m.get('parcel_type') != 'residual')
    immutable_preserved = final_standard_count == len(standard_lots)
    final_validation['standard_lot_count_preserved'] = immutable_preserved
    final_validation['base_standard_lot_count'] = len(standard_lots)
    final_validation['final_standard_lot_count'] = final_standard_count
    final_validation['roads_immutable'] = True
    final_validation['rth_psu_immutable'] = True
    final_validation['valid'] = bool(final_validation.get('valid') and immutable_preserved)

    efficiency_info = {
        'target_efficiency_pct': 70.0,
        'efficiency_before_pct': round((base_stats['lots_total_area_m2'] / parcel_area * 100.0) if parcel_area else 0.0, 2),
        'efficiency_after_pct': final_validation['lot_efficiency_pct'],
        'lot_efficiency_met': final_validation['lot_efficiency_met'],
        'residual_before_m2': round(base_stats['residual_area_m2'], 2),
        'residual_after_m2': round(stats['residual_area_m2'], 2),
        'residual_after_pct_total': round(final_residual_pct_total, 2),
        'method': 'residual-only-adaptive-parcelization',
    }

    rec = {'roads': roads}
    roads_union_out, drainage, segs, road_len, drain_len = _network_geo_output(rec, buildable, epsg)
    residual_records = _residual_records(stats['residual'], roads_union_out, req, epsg)
    lot_details = _lot_detail_records(lots, meta)
    adaptive_details = [d for d in lot_details if d['parcel_type'] == 'residual']
    actual_rth_pct = rth.area / parcel_area * 100 if parcel_area else 0.0
    actual_psu_pct = psu.area / parcel_area * 100 if parcel_area else 0.0
    road_pct = roads_union_out.area / parcel_area * 100 if parcel_area else 0.0

    final_stats = {
        'lot_count': stats['lot_count'],
        'standard_lot_count': final_standard_count,
        'adaptive_lot_count': len(adaptive_details),
        'residual_lot_count': len(adaptive_details),
        'lots_total_area_m2': round(stats['lots_total_area_m2'], 2),
        'standard_lot_area_m2': final_validation['standard_lot_area_m2'],
        'adaptive_lot_area_m2': final_validation['adaptive_lot_area_m2'],
        'lot_efficiency_pct': final_validation['lot_efficiency_pct'],
        'lot_efficiency_target_pct': 70.0,
        'lot_efficiency_met': final_validation['lot_efficiency_met'],
        'road_area_m2': round(roads_union_out.area, 2),
        'road_pct': round(road_pct, 2),
        'road_length_m': round(road_len, 2),
        'rth_area_m2': round(rth.area, 2), 'rth_pct': round(actual_rth_pct, 2),
        'psu_area_m2': round(psu.area, 2), 'psu_pct': round(actual_psu_pct, 2),
        'reserve_area_m2': 0.0, 'reserve_pct': 0.0,
        'drainage_length_m': round(drain_len, 2),
        'unused_area_m2': round(stats['residual_area_m2'], 2),
        'residual_true_area_m2': final_validation['residual_true_area_m2'],
        'residual_true_pct': final_validation['residual_true_pct'],
        'land_utilization_pct': round(stats['land_utilization_pct'], 2),
        'residual_ratio_pct': round(stats['residual_ratio_pct'], 2),
        'residual_pct_total_land': round(final_residual_pct_total, 2),
        'road_efficiency': round(stats['road_efficiency'], 3),
        'optimized': True, 'manual_adjusted': False,
        'invalid_standard_lot_count': int(final_validation.get('invalid_standard_lot_count', 0)),
        'adaptive_origin_violation_count': int(final_validation.get('adaptive_origin_violation_count', 0)),
        'residual_lot_area_m2': round(sum(d['area_m2'] for d in adaptive_details), 2),
        'validation_passed': bool(final_validation['valid']),
        'invalid_residual_lot_count': int(final_validation['invalid_residual_lot_count']),
        'lot_road_overlaps': int(final_validation['lot_road_overlaps']),
        'lot_obstacle_overlaps': int(final_validation['lot_obstacle_overlaps']),
        'lot_overlap_pairs': int(final_validation['lot_overlap_pairs']),
        'lots_outside_buildable': int(final_validation['lots_outside_buildable']),
        'lot_count_preserved': bool(final_validation['lot_count_preserved']),
        'standard_lot_count_preserved': immutable_preserved,
    }
    before = {
        'lot_count': len(standard_lots), 'standard_lot_count': len(standard_lots), 'adaptive_lot_count': 0,
        'lots_total_area_m2': round(base_stats['lots_total_area_m2'], 2),
        'lot_efficiency_pct': round((base_stats['lots_total_area_m2'] / parcel_area * 100.0) if parcel_area else 0.0, 2),
        'residual_area_m2': round(base_stats['residual_area_m2'], 2),
        'land_utilization_pct': round(base_stats['land_utilization_pct'], 2),
        'residual_ratio_pct': round(base_stats['residual_ratio_pct'], 2),
        'residual_pct_total_land': round(base_stats.get('residual_pct_total_land', 0.0), 2),
        'road_efficiency': round(base_stats['road_efficiency'], 3),
    }
    after = {
        'lot_count': final_stats['lot_count'], 'standard_lot_count': final_standard_count,
        'adaptive_lot_count': len(adaptive_details),
        'lots_total_area_m2': final_stats['lots_total_area_m2'],
        'lot_efficiency_pct': final_stats['lot_efficiency_pct'],
        'residual_area_m2': final_stats['unused_area_m2'],
        'land_utilization_pct': final_stats['land_utilization_pct'],
        'residual_ratio_pct': final_stats['residual_ratio_pct'],
        'residual_pct_total_land': final_stats['residual_pct_total_land'],
        'road_efficiency': final_stats['road_efficiency'],
    }
    return {
        'buildable': mapping(to_wgs84(buildable, epsg)),
        'roads': mapping(to_wgs84(roads_union_out, epsg)) if not roads_union_out.is_empty else None,
        'road_segments': segs,
        'rth': mapping(to_wgs84(rth, epsg)) if not rth.is_empty else None,
        'psu': mapping(to_wgs84(psu, epsg)) if not psu.is_empty else None,
        'reserve': None,
        'drainage': mapping(to_wgs84(drainage, epsg)) if not drainage.is_empty else None,
        'lots': [mapping(_safe_wgs84_polygonal(g, epsg)) for g in lots],
        'lot_details': lot_details,
        'residuals': residual_records,
        'stats': final_stats,
        'optimization': {
            'version': '2.5.12', 'optimizer_type': 'RESIDUAL_ONLY',
            'before': before, 'after': after,
            'delta': {
                'lot_count': after['lot_count'] - before['lot_count'],
                'standard_lot_count': 0,
                'adaptive_lot_count': after['adaptive_lot_count'],
                'sellable_area_m2': round(after['lots_total_area_m2'] - before['lots_total_area_m2'], 2),
                'lot_efficiency_point': round(after['lot_efficiency_pct'] - before['lot_efficiency_pct'], 2),
                'residual_area_m2': round(after['residual_area_m2'] - before['residual_area_m2'], 2),
                'utilization_pct_point': round(after['land_utilization_pct'] - before['land_utilization_pct'], 2),
            },
            'road_shift': 'DISABLED — roads immutable',
            'facility_strategy': 'FIXED — RTH/PSU immutable',
            'packing_order': 'FROZEN STANDARD → RESIDUAL → ADAPTIVE',
            'standard_module': {'width_m': round(base_w,2), 'depth_m': round(base_d,2), 'source':'geometry_settings'},
            'standard_source': 'geometry_settings', 'adaptive_source': 'residual_only',
            'parcelization_strategy': 'road-block-standard-first',
            'optimizer_separation': 'MASTERPLAN_TOPOLOGY_DURING_GENERATE; RESIDUAL_ONLY_AFTER_SELECTION',
            'selective_road_extensions': [],
            'residual_count': len(residual_records), 'efficiency_info': efficiency_info,
            'residual_parcel_count': int(parcel_info.get('residual_parcel_count',0)) + int(sweep_info.get('final_cap_parcels',0)),
            'residual_parcel_area_m2': round(float(parcel_info.get('residual_parcel_area_m2',0.0)) + float(sweep_info.get('final_cap_parcel_area_m2',0.0)),2),
            'rejected_unsaleable_before_final': len(rejected_pre),
            'rejected_unsaleable_after_final': len(rejected_post),
            'final_validation': final_validation,
            'elapsed_s': round(time.monotonic() - optimize_started, 3),
        },
        'utm_epsg': epsg, 'validation': final_validation,
        'notice': 'M2.5.12: Residual Optimizer cannot move/delete STANDARD lots or alter roads/RTH/PSU. It converts genuine leftover land into validated ADAPTIVE lots to improve gross lot efficiency towards >= 70%.'
    }


@app.post('/site-plan/optimize-yield')
def site_plan_optimize_yield(req: YieldOptimizeRequest):
    return optimize_land_utilization(req)

# -----------------------------
# Parsers
# -----------------------------
def feature_to_polygon(obj: dict[str, Any]):
    if obj.get("type") == "Feature":
        return ensure_polygon(obj["geometry"])
    if obj.get("type") == "FeatureCollection":
        polys = []
        for feat in obj.get("features", []):
            try:
                g = shape(feat.get("geometry"))
                if g.geom_type in ("Polygon", "MultiPolygon"):
                    polys.append(make_valid(g))
            except Exception:
                continue
        if not polys:
            raise HTTPException(422, "No polygon features found")
        return unary_union(polys)
    return ensure_polygon(obj)


def parse_coordinate_text(text: str, epsg: int, order: str) -> Polygon | MultiPolygon:
    points: list[tuple[float, float]] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        # supports commas, semicolons, whitespace, optional labels such as P1,lat,lon
        tokens = [t for t in re.split(r"[,;\s]+", line) if t]
        nums = []
        for t in tokens:
            try:
                nums.append(float(t))
            except ValueError:
                pass
        if len(nums) < 2:
            continue
        a, b = nums[-2], nums[-1]
        if epsg == 4326 and order.lower() == "latlon":
            x, y = b, a
        else:
            x, y = a, b
        points.append((x, y))
    if len(points) < 3:
        raise HTTPException(422, "At least 3 coordinate rows are required")
    if points[0] != points[-1]:
        points.append(points[0])
    poly = make_valid(Polygon(points))
    if epsg != 4326:
        poly = to_wgs84(poly, epsg)
    return ensure_polygon(mapping(poly))


def parse_csv_bytes(data: bytes, epsg: int | None) -> Polygon | MultiPolygon:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(422, "CSV has no header")
    names = {n.strip().lower(): n for n in reader.fieldnames}
    pts: list[tuple[float, float]] = []

    lat_key = names.get("latitude") or names.get("lat")
    lon_key = names.get("longitude") or names.get("lon") or names.get("lng")
    x_key = names.get("easting") or names.get("x")
    y_key = names.get("northing") or names.get("y")

    if lat_key and lon_key:
        for r in reader:
            if r.get(lat_key) and r.get(lon_key):
                pts.append((float(r[lon_key]), float(r[lat_key])))
        source_epsg = 4326
    elif x_key and y_key:
        if not epsg:
            raise HTTPException(422, "CSV with Easting/Northing requires EPSG")
        for r in reader:
            if r.get(x_key) and r.get(y_key):
                pts.append((float(r[x_key]), float(r[y_key])))
        source_epsg = epsg
    else:
        raise HTTPException(422, "CSV must contain latitude/longitude or easting/northing columns")

    if len(pts) < 3:
        raise HTTPException(422, "CSV contains fewer than 3 valid points")
    poly = Polygon(pts)
    if source_epsg != 4326:
        poly = to_wgs84(poly, source_epsg)
    return ensure_polygon(mapping(poly))


def parse_kml_bytes(data: bytes) -> Polygon | MultiPolygon:
    # Lightweight parser: extract KML coordinate rings without depending on GDAL KML driver availability.
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data)
    except Exception as e:
        raise HTTPException(422, f"Invalid KML: {e}")
    polys = []
    for elem in root.iter():
        if elem.tag.endswith("coordinates") and elem.text:
            pts = []
            for part in elem.text.strip().split():
                vals = part.split(",")
                if len(vals) >= 2:
                    try:
                        pts.append((float(vals[0]), float(vals[1])))
                    except ValueError:
                        pass
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                p = Polygon(pts)
                if p.area > 0:
                    polys.append(make_valid(p))
    if not polys:
        raise HTTPException(422, "No polygon coordinate ring found in KML")
    return ensure_polygon(mapping(unary_union(polys)))


def parse_shp_zip(data: bytes) -> Polygon | MultiPolygon:
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "input.zip"
        zpath.write_bytes(data)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(td)
        shp_files = list(Path(td).glob("**/*.shp"))
        if not shp_files:
            raise HTTPException(422, "ZIP does not contain a .shp file")
        gdf = gpd.read_file(shp_files[0])
        if gdf.empty:
            raise HTTPException(422, "Shapefile is empty")
        if gdf.crs is None:
            raise HTTPException(422, "Shapefile CRS is missing (.prj required)")
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        if gdf.empty:
            raise HTTPException(422, "Shapefile contains no polygon geometry")
        gdf = gdf.to_crs(4326)
        return ensure_polygon(mapping(unary_union(gdf.geometry.tolist())))


def parse_dxf_bytes(data: bytes, epsg: int | None) -> Polygon | MultiPolygon:
    if not epsg:
        raise HTTPException(422, "DXF import requires source EPSG (for example 32647 for UTM 47N)")
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        doc = ezdxf.readfile(path)
        msp = doc.modelspace()
        polys = []
        for ent in msp:
            pts = None
            if ent.dxftype() == "LWPOLYLINE" and ent.closed:
                pts = [(p[0], p[1]) for p in ent.get_points()]
            elif ent.dxftype() == "POLYLINE" and getattr(ent, "is_closed", False):
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in ent.vertices]
            if pts and len(pts) >= 3:
                p = make_valid(Polygon(pts))
                if not p.is_empty:
                    polys.append(p)
        if not polys:
            raise HTTPException(422, "No closed polygon/polyline found in DXF")
        geom = unary_union(polys)
        if epsg != 4326:
            geom = to_wgs84(geom, epsg)
        return ensure_polygon(mapping(geom))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
async def health():
    return {"ok": True, "database": "postgis" if is_postgres() else "sqlite-dev-fallback", "version": "2.5.12"}


@app.post("/geometry/stats")
def geometry_stats(req: GeometryRequest):
    geom = ensure_polygon(req.geometry)
    return {"geometry": mapping(geom), "stats": geometry_stats_wgs84(geom)}


@app.post("/geometry/analyze")
def geometry_analyze(req: AnalyzeRequest):
    return analyze_geometry(req)


@app.post("/site-plan/generate")
def site_plan_generate(req: SitePlanRequest):
    return generate_site_alternatives(req)


@app.post("/geometry/from-coordinates")
def geometry_from_coordinates(req: CoordinatesRequest):
    geom = parse_coordinate_text(req.text, req.epsg, req.order)
    return {"geometry": mapping(geom), "stats": geometry_stats_wgs84(geom)}


@app.post("/geometry/import")
async def geometry_import(file: UploadFile = File(...), epsg: int | None = Form(default=None)):
    data = await file.read()
    ext = Path(file.filename or "").suffix.lower()
    if ext in (".geojson", ".json"):
        try:
            obj = json.loads(data.decode("utf-8-sig"))
        except Exception as e:
            raise HTTPException(422, f"Invalid GeoJSON/JSON: {e}")
        geom = feature_to_polygon(obj)
    elif ext == ".kml":
        geom = parse_kml_bytes(data)
    elif ext == ".kmz":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise HTTPException(422, "KMZ contains no KML file")
            geom = parse_kml_bytes(zf.read(kml_names[0]))
    elif ext == ".zip":
        geom = parse_shp_zip(data)
    elif ext == ".csv":
        geom = parse_csv_bytes(data, epsg)
    elif ext == ".dxf":
        geom = parse_dxf_bytes(data, epsg)
    elif ext == ".dwg":
        raise HTTPException(415, "DWG is not supported in Milestone 1. Convert DWG to DXF first.")
    else:
        raise HTTPException(415, "Supported: KML, KMZ, GeoJSON, CSV, SHP ZIP, DXF")
    return {"geometry": mapping(geom), "stats": geometry_stats_wgs84(geom)}


@app.post("/projects")
def save_project(req: ProjectRequest):
    geom = ensure_polygon(req.parcel)
    enforce_gate = bool(
        req.settings.get("enforce_lot_efficiency_target", False)
        or req.settings.get("enforce_residual_cap", False)
        or req.settings.get("land_optimization_enabled", False)
    )
    if enforce_gate:
        layout = req.layout or {}
        validation = layout.get("validation") if isinstance(layout, dict) else None
        if not validation and isinstance(req.stats.get("validation"), dict):
            validation = req.stats.get("validation")
        if not validation and isinstance(layout.get("optimization"), dict) and isinstance(layout["optimization"].get("final_validation"), dict):
            validation = layout["optimization"]["final_validation"]

        manual_adjusted = bool(req.stats.get("manual_adjusted") or (layout.get("stats") or {}).get("manual_adjusted"))
        if manual_adjusted:
            raise HTTPException(422, detail={
                "message": "Layout optimal sudah diedit manual. Jalankan Optimasi Ulang agar final validation M2.5.12 dihitung ulang sebelum Save."
            })

        stats_valid = req.stats.get("validation_passed")
        is_valid = bool(validation.get("valid")) if validation else (stats_valid is True)
        if not validation or not is_valid or stats_valid is False:
            reasons = []
            if validation and not validation.get("lot_efficiency_met", True):
                reasons.append("lot_efficiency_pct < 70%")
            if validation and validation.get("invalid_standard_lot_count", 0) > 0:
                reasons.append("invalid_standard_lot_count > 0")
            if validation and validation.get("adaptive_origin_violation_count", 0) > 0:
                reasons.append("adaptive_origin_violation_count > 0")
            msg = f"Layout M2.5.12 ditolak: final validation belum PASS ({', '.join(reasons)})" if reasons else "Layout M2.5.12 ditolak: final validation belum PASS"
            raise HTTPException(422, detail={
                "message": msg,
                "validation": validation,
                "validation_passed": stats_valid,
                "lot_efficiency_pct": req.stats.get("lot_efficiency_pct") or (validation.get("lot_efficiency_pct") if validation else None),
            })
    now = datetime.now(timezone.utc).isoformat()
    parcel_json = json.dumps(mapping(geom))
    buildable_json = json.dumps(req.buildable) if req.buildable else None
    lots_json = json.dumps(req.lots)
    layout_json = json.dumps(req.layout) if req.layout else None
    settings_json = json.dumps(req.settings)
    stats_json = json.dumps(req.stats)

    if is_postgres():
        import psycopg
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO projects(name, parcel_geom, parcel_geojson, buildable_geojson, lots_geojson, layout_geojson, settings, stats)
                    VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s),4326), %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                    RETURNING id
                    """,
                    (req.name, parcel_json, parcel_json, buildable_json, lots_json, layout_json, settings_json, stats_json),
                )
                project_id = cur.fetchone()[0]
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS layout_geojson JSONB")
            conn.commit()
    else:
        con = sqlite3.connect(_sqlite_path())
        cur = con.execute(
            """
            INSERT INTO projects(name, parcel_geojson, buildable_geojson, lots_geojson, layout_geojson, settings, stats, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (req.name, parcel_json, buildable_json, lots_json, layout_json, settings_json, stats_json, now),
        )
        project_id = cur.lastrowid
        con.commit()
        con.close()

    return {"ok": True, "id": project_id, "name": req.name}


@app.get("/projects")
def list_projects():
    if is_postgres():
        import psycopg
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, stats, created_at FROM projects ORDER BY id DESC LIMIT 100")
                rows = cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "stats": r[2] or {}, "created_at": r[3].isoformat() if r[3] else None}
            for r in rows
        ]
    con = sqlite3.connect(_sqlite_path())
    rows = con.execute("SELECT id, name, stats, created_at FROM projects ORDER BY id DESC LIMIT 100").fetchall()
    con.close()
    return [
        {"id": r[0], "name": r[1], "stats": json.loads(r[2] or "{}"), "created_at": r[3]}
        for r in rows
    ]
