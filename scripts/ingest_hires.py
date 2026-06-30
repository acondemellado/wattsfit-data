#!/usr/bin/env python3
"""
Ingiere rutas de ALTA RESOLUCIÓN y cura la elevación, sustituyendo los GPX
bastos de cyclingstage (que cortan curvas → pendientes fantasma del 17-30%).

Modos:
  # 1 export de VeloViewer (TCX o GPX) -> 1 ruta del catálogo (calidad ORO)
  python3 ingest_hires.py veloviewer <archivo.tcx|.gpx> --id <route_id>

  # 1 GPX multi-segmento de VisuGPX -> N etapas, re-perfilado con EU-DEM 25 m
  python3 ingest_hires.py visugpx --gpx <archivo.gpx> --prefix tdf-2026-stage- \
       --type grand-tour [--first 1] [--only 1,2,3]

Tras ingerir, ejecuta:  python3 scripts/detect_climbs_in_gpx.py
para regenerar route_climbs.json con la elevación ya limpia.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import gpxpy

import clean_lib as cl

INDEX = cl.INDEX


# ----------------------------------------------------------- parsers fuente
def parse_tcx(raw: str):
    """Devuelve (track_pts, waypoints) desde un TCX (con prefijos de ns)."""
    tp_re = re.compile(r"<Trackpoint>.*?</Trackpoint>", re.S)
    cp_re = re.compile(r"<CoursePoint>.*?</CoursePoint>", re.S)
    lat_re = re.compile(r"LatitudeDegrees>([-\d.]+)<")
    lon_re = re.compile(r"LongitudeDegrees>([-\d.]+)<")
    alt_re = re.compile(r"AltitudeMeters>([-\d.]+)<")
    name_re = re.compile(r"<Name>(.*?)</Name>", re.S)
    pt_re = re.compile(r"<PointType>(.*?)</PointType>", re.S)
    notes_re = re.compile(r"<Notes>(.*?)</Notes>", re.S)
    pts = []
    for blk in tp_re.findall(raw):
        la, lo, al = lat_re.search(blk), lon_re.search(blk), alt_re.search(blk)
        if la and lo:
            pts.append((float(la.group(1)), float(lo.group(1)),
                        float(al.group(1)) if al else 0.0))
    wpts = []
    for blk in cp_re.findall(raw):
        la, lo = lat_re.search(blk), lon_re.search(blk)
        if not (la and lo):
            continue
        nm = name_re.search(blk)
        ty = pt_re.search(blk)
        no = notes_re.search(blk)
        wpts.append({
            "lat": float(la.group(1)), "lon": float(lo.group(1)),
            "name": nm.group(1).strip() if nm else None,
            "type": ty.group(1).strip() if ty else None,
            "cmt": no.group(1).strip() if no else None,
        })
    return pts, wpts


def parse_gpx_file(raw: str):
    g = gpxpy.parse(raw)
    pts = [(p.latitude, p.longitude, p.elevation or 0.0)
           for t in g.tracks for s in t.segments for p in s.points]
    wpts = [{"lat": w.latitude, "lon": w.longitude, "ele": w.elevation,
             "name": w.name, "cmt": w.comment, "type": w.type}
            for w in g.waypoints]
    return pts, wpts


# ----------------------------------------------------------- índice
def update_index(route_id: str, stats: dict, source_url: str, note: str):
    idx = json.loads(INDEX.read_text())
    found = None
    for r in idx["routes"]:
        if r["id"] == route_id:
            found = r
            break
    if found is None:
        raise SystemExit(f"✗ id no encontrado en routes.json: {route_id}")
    found.update({
        "distance_km": stats["distance_km"],
        "elevation_gain_m": stats["elevation_gain_m"],
        "elevation_loss_m": stats["elevation_loss_m"],
        "min_ele_m": stats["min_ele_m"],
        "max_ele_m": stats["max_ele_m"],
        "bbox": stats["bbox"],
        "source_url": source_url,
        "notes": note,
    })
    idx["updated"] = "2026-06-30"
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=2))
    return found


def route_entry(route_id: str):
    idx = json.loads(INDEX.read_text())
    for r in idx["routes"]:
        if r["id"] == route_id:
            return r
    return None


# ----------------------------------------------------------- modos
def ingest_one(route_id, pts, wpts, source_url, note, reprofile=False, local_dem=None):
    entry = route_entry(route_id)
    if entry is None:
        raise SystemExit(f"✗ id no encontrado: {route_id}")
    name = entry["name"]
    gpx_path = cl.REPO / entry["gpx_path"]
    n0 = len(pts)
    cum = cl.cumulative(pts)
    if reprofile:
        # geometría VisuGPX (buena) + elevación re-perfilada (DEM local/IGN/EU):
        # reperfilar → decimar → pulir (min-spacing + suavizado + clamp 20%).
        ele = cl.reprofile_from_dem(pts, sample_m=50.0, local_dem=local_dem)
        pts = [(p[0], p[1], e) for p, e in zip(pts, ele)]
        pts = cl.decimate(pts, cl.EPSILON_M)
        pts = cl.polish_dem(pts)
    else:
        # elevación de alta resolución (VeloViewer): solo limpieza ligera.
        ele = cl.hampel([p[2] for p in pts], cum, win_m=200.0, n_sigmas=3.0)
        ele = cl.smooth(ele, cum, win_m=40.0)
        ele = cl.clamp_grades(ele, cum, max_grade=0.28)  # red anti-fantasma
        pts = [(p[0], p[1], e) for p, e in zip(pts, ele)]
        pts = cl.decimate(pts, cl.EPSILON_M)
    # 3) escribir GPX + stats + índice
    cl.write_gpx(gpx_path, name, pts, waypoints=wpts)
    stats = cl.compute_stats(pts)
    update_index(route_id, stats, source_url, note)
    print(f"  ✓ {route_id}: {n0}→{len(pts)} pts | {stats['distance_km']} km | "
          f"D+ {stats['elevation_gain_m']} m | {len(wpts)} wpts")
    return stats


def mode_veloviewer(args):
    f = Path(args.file)
    raw = f.read_text(encoding="utf-8", errors="ignore")
    if f.suffix.lower() == ".tcx" or "<TrainingCenterDatabase" in raw[:500]:
        pts, wpts = parse_tcx(raw)
    else:
        pts, wpts = parse_gpx_file(raw)
    if len(pts) < 2:
        raise SystemExit(f"✗ sin trackpoints: {f}")
    src = "VeloViewer export (ASO roadbook)"
    note = "Geometría y elevación de alta resolución (VeloViewer/ASO)."
    print(f"VeloViewer → {args.id}  ({len(pts)} pts, {len(wpts)} roadbook)")
    ingest_one(args.id, pts, wpts, src, note, reprofile=False)


def mode_visugpx(args):
    raw = Path(args.gpx).read_text(encoding="utf-8", errors="ignore")
    g = gpxpy.parse(raw)
    segs = [s for t in g.tracks for s in t.segments]
    print(f"VisuGPX: {len(segs)} segmentos")
    only = None
    if args.only:
        only = {int(x) for x in args.only.split(",")}
    countries = [c for c in (args.countries or "").split(",") if c]
    dem_lib = "DEM local " + "+".join(countries) if countries else "IGN/EU-DEM"
    src = ("VisuGPX https://www.visugpx.com/Nln1sO8mHz (alta resolución) + "
           "re-perfilado (" + dem_lib + ")")
    note = ("Geometría VisuGPX (sigue la carretera) + elevación re-perfilada "
            f"({dem_lib}, 5-25 m), pulida. Roadbook ASO solo en export VeloViewer.")
    for i, seg in enumerate(segs):
        stage = args.first + i
        if only and stage not in only:
            continue
        rid = f"{args.prefix}{stage:02d}"
        pts = [(p.latitude, p.longitude, p.elevation or 0.0) for p in seg.points]
        if len(pts) < 2:
            print(f"  · {rid}: segmento vacío, saltado")
            continue
        local_dem = None
        if countries:
            import dem_local
            dirs = []
            for c in countries:
                dem_local.ensure_corridor(pts, country=c)
                dirs.append(dem_local.DEM_DIR / c)
            local_dem = dem_local.LocalDem(dirs)
        print(f"VisuGPX → {rid}  ({len(pts)} pts) — re-perfilando ({dem_lib})…")
        ingest_one(rid, pts, [], src, note, reprofile=True, local_dem=local_dem)
        if local_dem:
            local_dem.close()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("veloviewer")
    a.add_argument("file")
    a.add_argument("--id", required=True)
    a.set_defaults(func=mode_veloviewer)
    b = sub.add_parser("visugpx")
    b.add_argument("--gpx", required=True)
    b.add_argument("--prefix", required=True)
    b.add_argument("--type", default="grand-tour")
    b.add_argument("--first", type=int, default=1)
    b.add_argument("--only", default=None)
    b.add_argument("--countries", default=None,
                   help="DEM local por país, p.ej. 'spain' o 'spain,france'")
    b.set_defaults(func=mode_visugpx)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
