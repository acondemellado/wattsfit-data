#!/usr/bin/env python3
"""
Detecta puertos (POIs) en cada GPX de routes/ replicando el algoritmo del
ClimbDetector de la app (mismas constantes), y escribe la lista de puertos
con COORDENADAS PRECISAS de la cima dentro de cada entrada de routes.json
(campo "climbs"). Sirve para situar puntos de interés a partir del GPX y para
"cuadrar" mejor los puertos con el histórico.

Constantes (idénticas a lib/engine/climb_detector.dart):
  smoothingWindowM=400, minLengthM=1500, minGainM=100,
  minAvgGradePct=3.0, maxDescentInsideM=300.
"""
import json
import math
from pathlib import Path

import gpxpy

REPO = Path(__file__).resolve().parent.parent
ROUTES_DIR = REPO / "routes"
INDEX = REPO / "routes.json"
# Sidecar: POIs por ruta, aparte de routes.json (que la app carga entero).
OUT = REPO / "route_climbs.json"

SMOOTH_M = 400.0
MIN_LEN_M = 1500.0
MIN_GAIN_M = 100.0
MIN_AVG_PCT = 3.0
MAX_DESCENT_M = 300.0


def haversine_m(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_points(gpx_path):
    g = gpxpy.parse(gpx_path.read_text(encoding="utf-8", errors="ignore"))
    pts = [p for t in g.tracks for s in t.segments for p in s.points]
    if not pts:
        return []
    out = []
    cum = 0.0
    prev = None
    for p in pts:
        if prev is not None:
            cum += haversine_m((prev.latitude, prev.longitude), (p.latitude, p.longitude))
        out.append((p.latitude, p.longitude, p.elevation if p.elevation is not None else 0.0, cum))
        prev = p
    return out


def smooth_elev(pts):
    n = len(pts)
    out = [0.0] * n
    half = SMOOTH_M / 2
    lo = hi = 0
    s = 0.0
    for i in range(n):
        center = pts[i][3]
        while lo < n and pts[lo][3] < center - half:
            s -= pts[lo][2]
            lo += 1
        while hi < n and pts[hi][3] <= center + half:
            s += pts[hi][2]
            hi += 1
        cnt = hi - lo
        out[i] = s / cnt if cnt > 0 else pts[i][2]
    return out


def build_climb(pts, sm, start, end):
    if end <= start:
        return None
    length = pts[end][3] - pts[start][3]
    gain = sm[end] - sm[start]
    if length < MIN_LEN_M or gain < MIN_GAIN_M:
        return None
    avg = gain / length * 100
    if avg < MIN_AVG_PCT:
        return None
    # max grade sobre tramos de 500 m
    max_g = 0.0
    i = start
    while i < end:
        d0 = pts[i][3]
        j = i + 1
        while j < end and pts[j][3] - d0 < 500.0:
            j += 1
        seg = pts[j][3] - d0
        if seg >= 200:
            g = (sm[j] - sm[i]) / seg * 100
            max_g = max(max_g, g)
        i += 1
    # cima = punto de mayor elevación suavizada en el run
    top = start
    for i in range(start, end + 1):
        if sm[i] > sm[top]:
            top = i
    L = pts[top][3] - pts[start][3]
    if L < MIN_LEN_M:
        return None
    return {
        "summitLat": round(pts[top][0], 5),
        "summitLon": round(pts[top][1], 5),
        "summitEleM": round(sm[top]),
        "startKm": round(pts[start][3] / 1000, 2),
        "summitKm": round(pts[top][3] / 1000, 2),
        "lengthKm": round(L / 1000, 2),
        "gainM": round(sm[top] - sm[start]),
        "avgGradePct": round((sm[top] - sm[start]) / L * 100, 1),
        "maxGradePct": round(max_g, 1),
    }


def detect(pts):
    if len(pts) < 5:
        return []
    sm = smooth_elev(pts)
    climbs = []
    start = None
    descent = 0.0
    for i in range(1, len(pts)):
        d_ele = sm[i] - sm[i - 1]
        d_dist = pts[i][3] - pts[i - 1][3]
        if start is None:
            if d_ele > 0 and d_dist > 0:
                start = i - 1
                descent = 0.0
        else:
            if d_ele >= 0:
                descent = 0.0
            else:
                descent += d_dist
                if descent > MAX_DESCENT_M:
                    c = build_climb(pts, sm, start, i - 1)
                    if c:
                        climbs.append(c)
                    start = None
                    descent = 0.0
    if start is not None:
        c = build_climb(pts, sm, start, len(pts) - 1)
        if c:
            climbs.append(c)
    return climbs


def main():
    idx = json.loads(INDEX.read_text())
    routes = idx["routes"]
    out = {"version": 1, "note": "POIs de puertos detectados desde cada GPX "
           "(coords precisas de cima). Generado por detect_climbs_in_gpx.py.",
           "routes": {}}
    total_climbs = 0
    done = 0
    for r in routes:
        gpx = REPO / r["gpx_path"]
        if not gpx.exists():
            continue
        pts = load_points(gpx)
        climbs = detect(pts)
        if climbs:
            out["routes"][r["id"]] = climbs
        total_climbs += len(climbs)
        done += 1
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{done} rutas | {total_climbs} POIs | {len(out['routes'])} rutas con puertos")


if __name__ == "__main__":
    main()
