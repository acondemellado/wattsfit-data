#!/usr/bin/env python3
"""
Utilidades compartidas para INGERIR rutas de alta resolución (VeloViewer /
VisuGPX) y curar la elevación, sustituyendo los GPX bastos de cyclingstage que
cortan curvas y generan pendientes fantasma.

Pasos típicos por ruta:
  1) extraer el track (lat, lon, ele) de la fuente buena
  2) limpiar la elevación: Hampel (mata picos aislados de puentes/túneles/jitter)
     + media móvil por distancia
  3) decimar con Douglas-Peucker (conserva la forma dentro de EPSILON_M; NO
     reintroduce cortes de curva porque la fuente ya sigue la carretera)
  4) recomputar stats y reescribir el GPX en nuestro formato

La elevación se cura con ventana en METROS (no en nº de puntos), para que el
resultado sea independiente de la densidad de la fuente.
"""
from __future__ import annotations

import math
import ssl
from pathlib import Path

import certifi
import gpxpy
import gpxpy.gpx

REPO = Path(__file__).resolve().parent.parent
ROUTES_DIR = REPO / "routes"
INDEX = REPO / "routes.json"

EPSILON_M = 3.0  # RDP: más fino que fetch_routes (8 m) para preservar herraduras
SSL_CTX = ssl.create_default_context(cafile=certifi.where())


# ---------------------------------------------------------------- geometría
def haversine_m(a, b) -> float:
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def cumulative(pts) -> list[float]:
    """Distancia acumulada en metros para pts = [(lat,lon,ele), ...]."""
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + haversine_m(pts[i - 1], pts[i]))
    return cum


# ---------------------------------------------------------------- elevación
def hampel(ele, dist, win_m=150.0, n_sigmas=3.0):
    """Filtro de Hampel por ventana de distancia: sustituye por la mediana
    local los puntos que se desvían > n_sigmas·MAD. Elimina picos aislados
    (puentes, túneles, jitter barométrico) sin tocar las rampas reales."""
    n = len(ele)
    out = list(ele)
    half = win_m / 2
    lo = hi = 0
    for i in range(n):
        while lo < i and dist[i] - dist[lo] > half:
            lo += 1
        while hi < n - 1 and dist[hi + 1] - dist[i] <= half:
            hi += 1
        window = sorted(ele[lo:hi + 1])
        m = window[len(window) // 2]
        mad = sorted(abs(x - m) for x in window)[len(window) // 2]
        sigma = 1.4826 * mad
        if sigma > 0 and abs(ele[i] - m) > n_sigmas * sigma:
            out[i] = m
    return out


def clamp_grades(ele, dist, max_grade=0.28):
    """Red de seguridad: garantiza |pendiente| ≤ max_grade en TODO segmento
    mediante un limiter de pendiente de doble pasada (adelante/atrás). Las
    rampas reales más duras del ciclismo (~22-25%) quedan intactas; los picos
    de DEM/baro (puentes, túneles) se aplastan. 28% = imposible que sea real."""
    n = len(ele)
    f = list(ele)
    for i in range(1, n):
        md = max_grade * (dist[i] - dist[i - 1])
        f[i] = min(max(f[i], f[i - 1] - md), f[i - 1] + md)
    b = list(f)
    for i in range(n - 2, -1, -1):
        md = max_grade * (dist[i + 1] - dist[i])
        b[i] = min(max(b[i], b[i + 1] - md), b[i + 1] + md)
    return b


def min_spacing(pts, min_m=30.0):
    """Elimina puntos más juntos que min_m (sub-resolución del DEM). Evita que
    el error de elevación del DEM se amplifique en % sobre bases ridículas
    (p. ej. 3 m de error sobre un segmento de 11 m = 27% fantasma)."""
    out = [pts[0]]
    lastd = cum = 0.0
    for i in range(1, len(pts)):
        cum += haversine_m(pts[i - 1], pts[i])
        if cum - lastd >= min_m or i == len(pts) - 1:
            out.append(pts[i])
            lastd = cum
    return out


def polish_dem(pts, min_m=30.0, smooth_m=200.0, max_grade=0.20):
    """Pulido específico de elevación DERIVADA DE DEM: quita segmentos sub-
    resolución + suaviza + acota la pendiente. La rejilla de 25 m mete ruido
    sostenido en herraduras alpinas que el suavizado normal no quita."""
    pts = min_spacing(pts, min_m)
    cum = cumulative(pts)
    ele = smooth([p[2] for p in pts], cum, smooth_m)
    ele = clamp_grades(ele, cum, max_grade)
    return [(p[0], p[1], e) for p, e in zip(pts, ele)]


def smooth(ele, dist, win_m=120.0):
    """Media móvil por ventana de distancia (centrada)."""
    n = len(ele)
    out = [0.0] * n
    half = win_m / 2
    lo = hi = 0
    acc = 0.0
    # ventana deslizante con suma incremental
    for i in range(n):
        while dist[i] - dist[lo] > half:
            acc -= ele[lo]
            lo += 1
        while hi < n and dist[hi] - dist[i] <= half:
            acc += ele[hi]
            hi += 1
        out[i] = acc / (hi - lo)
    return out


# ---------------------------------------------------------------- decimado RDP
def _project(pts, lat0):
    k = math.cos(math.radians(lat0))
    return [(p[1] * k, p[0]) for p in pts]  # (x=lon*cos, y=lat) en grados


def _rdp_keep(proj, eps_deg):
    n = len(proj)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        xa, ya = proj[a]
        xb, yb = proj[b]
        dx, dy = xb - xa, yb - ya
        norm = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0.0, -1
        for i in range(a + 1, b):
            xi, yi = proj[i]
            d = abs((xi - xa) * dy - (yi - ya) * dx) / norm
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps_deg and idx != -1:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return keep


def decimate(pts, epsilon_m=EPSILON_M):
    """Douglas-Peucker sobre (lat,lon); conserva ele. pts=[(lat,lon,ele),...]."""
    if len(pts) <= 2:
        return list(pts)
    lat0 = sum(p[0] for p in pts) / len(pts)
    proj = _project(pts, lat0)
    eps_deg = epsilon_m / (111320.0)  # m -> grados aprox
    keep = _rdp_keep(proj, eps_deg)
    return [p for p, k in zip(pts, keep) if k]


# ---------------------------------------------------------------- stats / GPX
def compute_stats(pts):
    cum = cumulative(pts)
    gain = loss = 0.0
    for i in range(1, len(pts)):
        d = pts[i][2] - pts[i - 1][2]
        if d > 0:
            gain += d
        else:
            loss -= d
    eles = [p[2] for p in pts]
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return {
        "distance_km": round(cum[-1] / 1000.0, 2),
        "elevation_gain_m": round(gain),
        "elevation_loss_m": round(loss),
        "min_ele_m": round(min(eles)),
        "max_ele_m": round(max(eles)),
        "bbox": {
            "min_lat": round(min(lats), 5),
            "max_lat": round(max(lats), 5),
            "min_lon": round(min(lons), 5),
            "max_lon": round(max(lons), 5),
        },
        "points": len(pts),
    }


def write_gpx(path: Path, name: str, pts, waypoints=None, creator="wattsfit-ingest"):
    """Escribe un GPX (track + roadbook opcional) en nuestro formato."""
    gpx = gpxpy.gpx.GPX()
    gpx.creator = creator
    gpx.name = name
    trk = gpxpy.gpx.GPXTrack(name=name)
    seg = gpxpy.gpx.GPXTrackSegment()
    for la, lo, el in pts:
        seg.points.append(gpxpy.gpx.GPXTrackPoint(la, lo, elevation=round(el, 1)))
    trk.segments.append(seg)
    gpx.tracks.append(trk)
    for wp in (waypoints or []):
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
            latitude=wp["lat"], longitude=wp["lon"],
            elevation=wp.get("ele"),
            name=wp.get("name"), comment=wp.get("cmt"),
            type=wp.get("type"),
        ))
    path.write_text(gpx.to_xml(), encoding="utf-8")


# ---------------------------------------------------------------- EU-DEM
def dem_profile(latlon, dataset="eudem25m", batch=100, pause=1.05, log=print):
    """Devuelve la elevación EU-DEM 25 m para una lista de (lat,lon).
    Lotes de 100, 1 req/s (límite OpenTopoData). Reintenta en 429/errores."""
    import json
    import time
    import urllib.parse
    import urllib.request

    out = []
    for k in range(0, len(latlon), batch):
        chunk = latlon[k:k + batch]
        locs = "|".join(f"{la:.6f},{lo:.6f}" for la, lo in chunk)
        url = (f"https://api.opentopodata.org/v1/{dataset}?locations="
               + urllib.parse.quote(locs, safe="|,"))
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=45, context=SSL_CTX) as r:
                    data = json.load(r)
                vals = [x["elevation"] for x in data["results"]]
                # rellena posibles None (fuera de cobertura) con vecino
                for i, v in enumerate(vals):
                    if v is None:
                        vals[i] = vals[i - 1] if i > 0 else 0.0
                out.extend(vals)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(3)
                    continue
                if attempt == 5:
                    raise
                time.sleep(2)
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2)
        time.sleep(pause)
        if log and (k // batch) % 5 == 0:
            log(f"    DEM {min(k + batch, len(latlon))}/{len(latlon)}")
    return out


def ign_profile(latlon, batch=80, pause=0.4, log=print):
    """Elevación del IGN francés RGE ALTI (1-5 m) para una lista de (lat,lon).
    MUCHO más preciso que EU-DEM 25 m en montaña (RMSE ~2.7 m vs 8.7 m; D+ casi
    exacto). Solo cubre Francia → usar para el Tour. Reintenta en error."""
    import json
    import time
    import urllib.request

    out = []
    for k in range(0, len(latlon), batch):
        chunk = latlon[k:k + batch]
        lons = "|".join(f"{lo:.6f}" for la, lo in chunk)
        las = "|".join(f"{la:.6f}" for la, lo in chunk)
        url = ("https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/"
               f"elevation.json?lon={lons}&lat={las}"
               "&resource=ign_rge_alti_wld&delimiter=|&zonly=true")
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=45, context=SSL_CTX) as r:
                    vals = json.load(r)["elevations"]
                # IGN devuelve -99999 fuera de Francia → None (lo rellena EU-DEM)
                out.extend(None if (v is None or v < -1000) else v for v in vals)
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2)
        time.sleep(pause)
        if log and (k // batch) % 10 == 0:
            log(f"    IGN {min(k + batch, len(latlon))}/{len(latlon)}")
    return out


def reprofile_from_dem(pts, sample_m=50.0, provider="ign", log=print):
    """Re-perfila la elevación de pts=[(lat,lon,ele),...] muestreando un DEM
    cada ~sample_m y reinterpolando a todos los puntos. provider='ign' (IGN
    RGE ALTI 1-5 m, solo Francia) o 'eudem' (EU-DEM 25 m, toda Europa)."""
    cum = cumulative(pts)
    idxs = []
    nextd = 0.0
    for i, d in enumerate(cum):
        if d >= nextd:
            idxs.append(i)
            nextd = d + sample_m
    if idxs[-1] != len(pts) - 1:
        idxs.append(len(pts) - 1)
    coords = [(pts[i][0], pts[i][1]) for i in idxs]
    if provider == "ign":
        try:
            sampled = ign_profile(coords, log=log)
        except Exception as e:
            if log:
                log(f"    IGN falló ({e}); fallback EU-DEM")
            sampled = [None] * len(coords)
        # Rellena con EU-DEM los puntos fuera de Francia (None)
        miss = [i for i, v in enumerate(sampled) if v is None]
        if miss:
            if log:
                log(f"    {len(miss)} pts fuera de Francia → EU-DEM")
            eu = dem_profile([coords[i] for i in miss], log=log)
            for i, v in zip(miss, eu):
                sampled[i] = v
    else:
        sampled = dem_profile(coords, log=log)
    sd = [cum[i] for i in idxs]
    # Hampel + suavizado sobre las muestras (mata picos de puente/túnel)
    sampled = hampel(sampled, sd, win_m=200.0, n_sigmas=3.0)
    sampled = smooth(sampled, sd, win_m=150.0)
    # interpola linealmente a todos los puntos
    out = []
    j = 0
    for i in range(len(pts)):
        d = cum[i]
        while j < len(idxs) - 2 and sd[j + 1] < d:
            j += 1
        d0, d1 = sd[j], sd[j + 1]
        e0, e1 = sampled[j], sampled[j + 1]
        t = 0.0 if d1 <= d0 else (d - d0) / (d1 - d0)
        out.append(e0 + t * (e1 - e0))
    return out
