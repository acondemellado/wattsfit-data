#!/usr/bin/env python3
"""
Descarga, decima y cataloga GPX de carreras y cicloturistas para Wattsfit.

Fuentes verificadas el 13/05/2026 (ver agentes de investigación):
- cyclingstage.com (Grand Tours + clásicas) — patrón estable de URL
- ASO storage (Paris-Roubaix Challenge, Étape du Tour vía visugpx)
- Sitios oficiales con descarga directa (Maratona Dolomites, Mallorca 312,
  Pedro Delgado, Lieja Challenge)

Salida:
- GPX decimados a ~1000 puntos en routes/{type}/{id}.gpx
- Índice combinado en routes.json (similar a climbs.json / tyres.json)
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import certifi
import gpxpy

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# Vamos a la raíz del repo independientemente desde dónde se invoque.
REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = REPO_ROOT / "routes"
INDEX_FILE = REPO_ROOT / "routes.json"

UA = "Wattsfit/1.0 (catalog builder; contact: acondemellado@gmail.com)"
TIMEOUT = 30
TARGET_POINTS = 1000


@dataclass
class Source:
    id: str
    name: str
    type: str  # "grand-tour" | "classic" | "gran-fondo"
    country: str  # ISO 3166-1 alpha-2 ("ES", "FR", "IT", ...)
    source_url: str
    event: str = ""           # "Tour de France 2026" / "Paris-Roubaix Challenge"
    stage: int | None = None  # 1..21 si aplica
    year: int = 2026
    notes: str = ""
    is_zip: bool = False
    gpx_path_in_zip: str | None = None


# ── Fuentes ────────────────────────────────────────────────────────────────

def _cs(slug: str) -> str:
    return f"https://cdn.cyclingstage.com/images/{slug}/2026/route.gpx"


def _cs_tdf(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/tour-de-france/2026/stage-{n}-route.gpx"


def _cs_vuelta(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/vuelta-spain/2026/stage-{n}-route.gpx"


def _cs_giro(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/giro-italy/2026/stage-{n}-route.gpx"


SOURCES: list[Source] = []

# Tour de France 2026 (21 etapas) — cyclingstage
for n in range(1, 22):
    SOURCES.append(Source(
        id=f"tdf-2026-stage-{n:02d}",
        name=f"Tour de France 2026 — Stage {n}",
        type="grand-tour",
        country="FR",
        source_url=_cs_tdf(n),
        event="Tour de France 2026",
        stage=n,
    ))

# Giro d'Italia 2026 (21 etapas) — cyclingstage slug 'giro-italy'
for n in range(1, 22):
    SOURCES.append(Source(
        id=f"giro-2026-stage-{n:02d}",
        name=f"Giro d'Italia 2026 — Stage {n}",
        type="grand-tour",
        country="IT",
        source_url=_cs_giro(n),
        event="Giro d'Italia 2026",
        stage=n,
    ))

# Vuelta a España 2026 (21 etapas)
for n in range(1, 22):
    SOURCES.append(Source(
        id=f"vuelta-2026-stage-{n:02d}",
        name=f"Vuelta a España 2026 — Stage {n}",
        type="grand-tour",
        country="ES",
        source_url=_cs_vuelta(n),
        event="Vuelta a España 2026",
        stage=n,
    ))

# Clásicas 2026 — cyclingstage (slug verificado)
CLASSICS = [
    ("omloop-het-nieuwsblad-2026", "Omloop Het Nieuwsblad 2026",         "BE", _cs("omloop-het-nieuwsblad")),
    ("kuurne-brussel-kuurne-2026", "Kuurne-Brussels-Kuurne 2026",        "BE", _cs("kuurne-brussels-kuurne")),
    ("strade-bianche-2026",        "Strade Bianche 2026",                 "IT", _cs("strade-bianche")),
    ("milan-san-remo-2026",        "Milán-San Remo 2026",                 "IT", _cs("milan-san-remo")),
    ("e3-saxo-classic-2026",       "E3 Saxo Classic 2026",                "BE", _cs("e3-saxo-classic")),
    ("gent-wevelgem-2026",         "Gent-Wevelgem 2026",                  "BE", _cs("in-flanders-fields")),
    ("dwars-door-vlaanderen-2026", "Dwars door Vlaanderen 2026",          "BE", _cs("dwars-door-vlaanderen")),
    ("ronde-vlaanderen-2026",      "Tour of Flanders 2026",               "BE", _cs("tour-of-flanders")),
    ("paris-roubaix-2026",         "Paris-Roubaix 2026",                  "FR", _cs("paris-roubaix")),
    ("brabantse-pijl-2026",        "De Brabantse Pijl 2026",              "BE", _cs("brabantse-pijl")),
    ("amstel-gold-race-2026",      "Amstel Gold Race 2026",               "NL", _cs("amstel-gold-race")),
    ("fleche-wallonne-2026",       "La Flèche Wallonne 2026",             "BE", _cs("la-fleche-wallonne")),
    ("liege-bastogne-liege-2026",  "Liège-Bastogne-Liège 2026",           "BE", _cs("liege-bastogne-liege")),
]
for sid, name, country, url in CLASSICS:
    event = name.rsplit(" 2026", 1)[0] + " 2026"
    SOURCES.append(Source(
        id=sid, name=name, type="classic", country=country,
        source_url=url, event=event,
    ))

# Cicloturistas con descarga directa verificada
GRAN_FONDOS = [
    Source(
        id="maratona-dles-dolomites-2026",
        name="Maratona dles Dolomites 2026 — Maratona 138 km",
        type="gran-fondo", country="IT",
        source_url="https://www.maratona.it/maratona.gpx",
        event="Maratona dles Dolomites 2026",
        notes="Distancia 138 km / 4320 m D+. Recorrido fijo histórico.",
    ),
    Source(
        id="mallorca-312-2026",
        name="Mallorca 312 OK Mobility 2026",
        type="gran-fondo", country="ES",
        source_url="https://mallorca312.com/wp-content/uploads/2025/03/Mallorca312_OKMobility_2025.gpx",
        event="Mallorca 312 OK Mobility 2026",
        notes="Track 2025 — el organizador actualiza al de 2026 cerca de la fecha.",
    ),
    Source(
        id="paris-roubaix-challenge-2026-170",
        name="Paris-Roubaix Challenge 2026 — 170 km",
        type="gran-fondo", country="FR",
        source_url="https://storage-aso.lequipe.fr/ASO/publicEvents_prc/prc26-170kmvf.gpx",
        event="Paris-Roubaix Challenge 2026",
        notes="Distancia larga oficial ASO (30 sectores pavé).",
    ),
    Source(
        id="paris-roubaix-challenge-2026-145",
        name="Paris-Roubaix Challenge 2026 — 145 km",
        type="gran-fondo", country="FR",
        source_url="https://storage-aso.lequipe.fr/ASO/publicEvents_prc/prc26-145kmvf.gpx",
        event="Paris-Roubaix Challenge 2026",
        notes="Distancia media (19 sectores).",
    ),
    Source(
        id="paris-roubaix-challenge-2026-70",
        name="Paris-Roubaix Challenge 2026 — 70 km",
        type="gran-fondo", country="FR",
        source_url="https://storage-aso.lequipe.fr/ASO/publicEvents_prc/prc26-70km-gpx.gpx",
        event="Paris-Roubaix Challenge 2026",
        notes="Distancia corta (8 sectores).",
    ),
    Source(
        id="pedro-delgado-2026-gran-fondo",
        name="Marcha Pedro Delgado 2026 — Gran Fondo",
        type="gran-fondo", country="ES",
        source_url="https://pedrodelgado.com/wp-content/uploads/2020/11/La_Perico-Gran_Fondo.zip",
        event="Marcha Pedro Delgado 2026 (La Perico)",
        notes="164 km / 3200 m D+ (track histórico, ediciones recientes usan el mismo).",
        is_zip=True,
    ),
    Source(
        id="pedro-delgado-2026-fondo",
        name="Marcha Pedro Delgado 2026 — Fondo",
        type="gran-fondo", country="ES",
        source_url="https://pedrodelgado.com/wp-content/uploads/2020/11/La_Perico-Fondo.zip",
        event="Marcha Pedro Delgado 2026 (La Perico)",
        notes="119 km / 2100 m D+.",
        is_zip=True,
    ),
    Source(
        id="sportful-dolomiti-race-2026-long",
        name="Sportful Dolomiti Race 2026 — Long 206 km",
        type="gran-fondo", country="IT",
        source_url="https://www.sportfuldolomitirace.it/wp-content/uploads/2025/06/Sportful_25_lungo.gpx_.zip",
        event="Granfondo Sportful Dolomiti Race 2026",
        notes="Archivo de 2025 (organizador actualiza cerca de fecha).",
        is_zip=True,
    ),
]
SOURCES.extend(GRAN_FONDOS)


# ── Descarga ───────────────────────────────────────────────────────────────

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
        return resp.read()


def extract_gpx_from_zip(blob: bytes, hint: str | None = None) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        gpx_files = [n for n in zf.namelist() if n.lower().endswith(".gpx")]
        if not gpx_files:
            return None
        target = gpx_files[0]
        if hint:
            for n in gpx_files:
                if hint.lower() in n.lower():
                    target = n
                    break
        return zf.read(target)


def fetch_gpx(src: Source) -> bytes | None:
    try:
        blob = fetch_bytes(src.source_url)
    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP {e.code} — {src.source_url}")
        return None
    except urllib.error.URLError as e:
        print(f"  ✗ URL error: {e.reason} — {src.source_url}")
        return None
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return None

    if src.is_zip:
        gpx_bytes = extract_gpx_from_zip(blob, src.gpx_path_in_zip)
        if gpx_bytes is None:
            print(f"  ✗ ZIP sin .gpx interno")
            return None
        return gpx_bytes
    # heurística: si parece ZIP por la cabecera, intentamos también
    if blob[:2] == b"PK":
        return extract_gpx_from_zip(blob)
    return blob


# ── Procesado ──────────────────────────────────────────────────────────────

def decimate(gpx: gpxpy.gpx.GPX, target: int = TARGET_POINTS) -> None:
    """
    Decima cada segmento a aprox. target puntos repartidos uniformemente.
    Mantiene siempre primero y último.
    """
    for track in gpx.tracks:
        for seg in track.segments:
            n = len(seg.points)
            if n <= target:
                continue
            step = n / target
            keep_idx = {round(i * step) for i in range(target)}
            keep_idx.add(0)
            keep_idx.add(n - 1)
            seg.points = [p for i, p in enumerate(seg.points) if i in keep_idx]


def compute_stats(gpx: gpxpy.gpx.GPX) -> dict:
    total_distance = 0.0
    elevation_gain = 0.0
    elevation_loss = 0.0
    min_ele = math.inf
    max_ele = -math.inf
    min_lat = math.inf
    max_lat = -math.inf
    min_lon = math.inf
    max_lon = -math.inf
    point_count = 0
    for track in gpx.tracks:
        for seg in track.segments:
            prev = None
            for p in seg.points:
                point_count += 1
                if p.elevation is not None:
                    min_ele = min(min_ele, p.elevation)
                    max_ele = max(max_ele, p.elevation)
                min_lat = min(min_lat, p.latitude)
                max_lat = max(max_lat, p.latitude)
                min_lon = min(min_lon, p.longitude)
                max_lon = max(max_lon, p.longitude)
                if prev is not None:
                    total_distance += p.distance_2d(prev) or 0
                    if p.elevation is not None and prev.elevation is not None:
                        diff = p.elevation - prev.elevation
                        if diff > 0:
                            elevation_gain += diff
                        else:
                            elevation_loss -= diff
                prev = p
    return {
        "distance_km": round(total_distance / 1000.0, 2),
        "elevation_gain_m": round(elevation_gain),
        "elevation_loss_m": round(elevation_loss),
        "min_ele_m": round(min_ele) if min_ele != math.inf else None,
        "max_ele_m": round(max_ele) if max_ele != -math.inf else None,
        "bbox": {
            "min_lat": round(min_lat, 5) if min_lat != math.inf else None,
            "max_lat": round(max_lat, 5) if max_lat != -math.inf else None,
            "min_lon": round(min_lon, 5) if min_lon != math.inf else None,
            "max_lon": round(max_lon, 5) if max_lon != -math.inf else None,
        },
        "points": point_count,
    }


def process(src: Source) -> dict | None:
    gpx_bytes = fetch_gpx(src)
    if gpx_bytes is None:
        return None
    try:
        gpx = gpxpy.parse(gpx_bytes.decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  ✗ parse error: {e}")
        return None
    if not gpx.tracks or all(not t.segments for t in gpx.tracks):
        print(f"  ✗ GPX sin tracks/segments")
        return None
    decimate(gpx)
    stats = compute_stats(gpx)
    out_dir = ROUTES_DIR / src.type
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.id}.gpx"
    out_path.write_text(gpx.to_xml(), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    entry = {
        "id": src.id,
        "name": src.name,
        "type": src.type,
        "country": src.country,
        "event": src.event,
        "year": src.year,
        "stage": src.stage,
        "gpx_path": f"routes/{src.type}/{src.id}.gpx",
        "source_url": src.source_url,
        "notes": src.notes,
        **stats,
    }
    print(f"  ✓ {src.id} — {stats['distance_km']} km / {stats['elevation_gain_m']} m D+ / {size_kb:.0f} KB")
    return entry


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    selected = [s for s in SOURCES if not only or only in s.id]
    print(f"Procesando {len(selected)} fuentes…")
    entries: list[dict] = []
    failures: list[str] = []
    for i, src in enumerate(selected, 1):
        print(f"[{i}/{len(selected)}] {src.id}")
        result = process(src)
        if result is not None:
            entries.append(result)
        else:
            failures.append(src.id)
        time.sleep(0.5)  # rate limit suave

    # Merge con índice previo si existe (sobrescribe entradas con mismo id)
    existing: dict[str, dict] = {}
    if INDEX_FILE.exists():
        try:
            data = json.loads(INDEX_FILE.read_text())
            for e in data.get("routes", []):
                existing[e["id"]] = e
        except Exception:
            pass
    for e in entries:
        existing[e["id"]] = e
    out_routes = sorted(existing.values(), key=lambda e: (e["type"], e["country"], e["id"]))

    INDEX_FILE.write_text(json.dumps({
        "version": 1,
        "updated": time.strftime("%Y-%m-%d"),
        "source": "Compilación automática (ver source_url en cada entrada). "
                  "Atribución a organizadores y cyclingstage.com/ASO/sitios oficiales.",
        "count": len(out_routes),
        "routes": out_routes,
    }, ensure_ascii=False, indent=2))
    print()
    print(f"Total OK: {len(entries)} / {len(selected)} — failures: {len(failures)}")
    if failures:
        print("Fallidos:")
        for f in failures:
            print(f"  - {f}")
    print(f"Índice → {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
