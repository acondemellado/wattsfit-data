#!/usr/bin/env python3
"""
MAP-MATCHING: pega un track basto (cyclingstage) a la red de carreteras OSM con
Valhalla `trace_route` (shape_match=map_snap), densificándolo y recuperando las
curvas cortadas SIN sobre-rutear (a diferencia de un /route por waypoints, que
mete desvíos). Es la vía para conseguir geometría de alta resolución de carreras
que NO están en VisuGPX/VeloViewer (Vuelta, etc.), de forma automática.

Servidor público FOSSGIS (sin clave). Devuelve la geometría como polyline6.
"""
from __future__ import annotations

import json
import math
import ssl
import time
import urllib.request

import certifi

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
VALHALLA = "https://valhalla1.openstreetmap.de/trace_route"
VALHALLA_ROUTE = "https://valhalla1.openstreetmap.de/route"


def _hav(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def _length(pts):
    return sum(_hav(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def _maxgap(pts):
    return max((_hav(pts[i - 1], pts[i]) for i in range(1, len(pts))), default=0.0)


def _decode6(s):
    """Decodifica polyline de precisión 1e6 (Valhalla) → [(lat,lon), ...]."""
    coords = []
    idx = lat = lon = 0
    n = len(s)
    while idx < n:
        for is_lat in (True, False):
            shift = res = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                res |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if (res & 1) else (res >> 1)
            if is_lat:
                lat += d
            else:
                lon += d
        coords.append((lat / 1e6, lon / 1e6))
    return coords


def _post(url, body, retries=5):
    """POST JSON con reintentos/backoff ante 429/502/503/504 (el servidor
    público FOSSGIS da 502 esporádicos bajo carga)."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _snap_chunk(chunk, costing="auto"):
    d = _post(VALHALLA, {"shape": [{"lat": la, "lon": lo} for la, lo in chunk],
                         "costing": costing, "shape_match": "map_snap"})
    out = []
    for leg in d["trip"]["legs"]:
        out.extend(_decode6(leg["shape"]))
    return out


OSRM = "https://router.project-osrm.org/route/v1/driving"


def _osrm_route(a, b):
    url = (f"{OSRM}/{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}"
           "?overview=full&geometries=geojson")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=40, context=SSL_CTX) as r:
                d = json.load(r)
            if d.get("code") == "Ok":
                return [(c[1], c[0]) for c in d["routes"][0]["geometry"]["coordinates"]]
            raise RuntimeError("osrm " + str(d.get("code")))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def _valhalla_route(a, b, costing="auto"):
    d = _post(VALHALLA_ROUTE,
              {"locations": [{"lat": a[0], "lon": a[1]},
                             {"lat": b[0], "lon": b[1]}], "costing": costing},
              retries=2)
    out = []
    for leg in d["trip"]["legs"]:
        out.extend(_decode6(leg["shape"]))
    return out


def _route(a, b, costing="auto"):
    """Ruta por carretera entre dos puntos (rellena huecos). OSRM primero (rápido
    y disponible); Valhalla de respaldo."""
    try:
        return _osrm_route(a, b)
    except Exception:
        return _valhalla_route(a, b, costing)


def _fill_gap(a, b, costing, pause, max_ratio=1.9):
    """Intenta rellenar el hueco a→b rutando por carretera. Devuelve los puntos
    INTERIORES (sin extremos) o None si el router se desvía demasiado (posible
    carretera equivocada) → se deja la recta del origen."""
    straight = _hav(a, b)
    try:
        fill = _route(a, b, costing)
        time.sleep(pause)
        if len(fill) >= 3 and straight <= _length(fill) <= max_ratio * straight:
            return fill[1:-1]
    except Exception:
        pass
    return None


def fill_track_gaps(pts, gap_m=1200, pause=0.2, log=print):
    """Post-proceso: rellena SOLO los huecos rectos largos (>gap_m) de una traza
    ya buena (p.ej. map-snapeada), rutando por carretera entre los extremos con
    validación anti-desvío. Deja intacto el resto. pts=[(lat,lon), ...]."""
    out = [pts[0]]
    filled = 0
    for i in range(1, len(pts)):
        if _hav(pts[i - 1], pts[i]) > gap_m:
            interior = _fill_gap(out[-1], pts[i], "auto", pause)
            if interior:
                out.extend(interior)
                filled += 1
        out.append(pts[i])
    if log:
        log(f"  fill-gaps: {len(pts)} → {len(out)} pts, {filled} huecos cerrados")
    return out


def snap_track(pts, chunk=60, pause=0.25, costing="auto", gap_split_m=1200,
               fill_gaps=True, log=print):
    """pts=[(lat,lon), ...] basto → traza densa pegada a la carretera, ROBUSTO:
    - parte la traza en los huecos grandes del origen (no pide a Valhalla
      puentear un gap de km, que lo amplificaría);
    - valida cada chunk (longitud y sin saltos internos); si el snap empeora,
      conserva los puntos crudos de ese chunk;
    - validación global: si el total se desvía mucho, devuelve la traza cruda
      (mejor coarse que rota). Así el map-match NUNCA empeora el origen."""
    # 1) trocear el origen en subtramos cortando por huecos grandes
    subs, cur = [], [pts[0]]
    for i in range(1, len(pts)):
        if _hav(pts[i - 1], pts[i]) > gap_split_m:
            subs.append(cur)
            cur = [pts[i]]
        else:
            cur.append(pts[i])
    subs.append(cur)

    out, raw_chunks, filled_gaps = [], 0, 0
    for sub in subs:
        if len(sub) < 2:
            if out:
                out.append(sub[0])  # mantiene el hueco del origen (recta)
            else:
                out.extend(sub)
            continue
        sub_out = []
        for k in range(0, len(sub), chunk - 1):
            ch = sub[k:k + chunk]
            if len(ch) < 2:
                break
            raw_len = _length(ch)
            snp = None
            try:
                snp = _snap_chunk(ch, costing)
            except Exception:
                snp = None
            ok = (snp and len(snp) >= 2
                  and 0.7 * raw_len <= _length(snp) <= 1.3 * raw_len
                  and _maxgap(snp) <= max(2.5 * _maxgap(ch), 400))
            seg = snp if ok else ch
            if not ok:
                raw_chunks += 1
            if sub_out and seg:
                seg = seg[1:]  # evita duplicar el solape
            sub_out.extend(seg)
            time.sleep(pause)
        if out and sub_out and fill_gaps:
            interior = _fill_gap(out[-1], sub_out[0], costing, pause)
            if interior:
                out.extend(interior)  # hueco cerrado por carretera
                filled_gaps += 1
        out.extend(sub_out)

    # 2) validación global
    if not (0.85 * _length(pts) <= _length(out) <= 1.2 * _length(pts)) \
            or len(out) < len(pts):
        if log:
            log(f"  map-match descartado (len {_length(out)/1000:.1f} vs "
                f"{_length(pts)/1000:.1f} km) → track crudo")
        return list(pts)
    if log:
        log(f"  map-match: {len(pts)} → {len(out)} pts "
            f"({raw_chunks} chunks crudos, {filled_gaps} huecos cerrados)")
    return out
