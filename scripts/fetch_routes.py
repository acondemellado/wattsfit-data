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
STAGE_DATES_FILE = Path(__file__).resolve().parent / "data" / "stage_dates.json"


def _load_stage_dates() -> dict:
    """Fechas verificadas por id de etapa (yyyy-mm-dd) para 'Etapa de hoy'."""
    try:
        raw = json.loads(STAGE_DATES_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


STAGE_DATES = _load_stage_dates()

UA = "Wattsfit/1.0 (catalog builder; contact: acondemellado@gmail.com)"
TIMEOUT = 30
TARGET_POINTS = 1000

# Entradas curadas a mano que NO deben re-descargarse de la fuente (GPX de
# origen roto/parcial). Ver nota en main().
SKIP_REFETCH = {
    # Rutas curadas a mano (alta resolución); NO re-bajar de cyclingstage
    "tdf-2026-stage-01",
    "tdf-2026-stage-02",
    "tdf-2026-stage-03",
    "tdf-2026-stage-04",
    "tdf-2026-stage-05",
    "tdf-2026-stage-06",
    "tdf-2026-stage-07",
    "tdf-2026-stage-08",
    "tdf-2026-stage-09",
    "tdf-2026-stage-10",
    "tdf-2026-stage-11",
    "tdf-2026-stage-12",
    "tdf-2026-stage-13",
    "tdf-2026-stage-14",
    "tdf-2026-stage-15",
    "tdf-2026-stage-16",
    "tdf-2026-stage-17",
    "tdf-2026-stage-18",
    "tdf-2026-stage-19",
    "tdf-2026-stage-20",
    "tdf-2026-stage-21",
    "tour-de-france-femmes-2026-stage-01",
    "tour-de-france-femmes-2026-stage-02",
    "tour-de-france-femmes-2026-stage-03",
    "tour-de-france-femmes-2026-stage-04",
    "tour-de-france-femmes-2026-stage-05",
    "tour-de-france-femmes-2026-stage-06",
    "tour-de-france-femmes-2026-stage-07",
    "tour-de-france-femmes-2026-stage-08",
    "tour-de-france-femmes-2026-stage-09",
    "vuelta-2026-stage-01",
    "vuelta-2026-stage-02",
    "vuelta-2026-stage-03",
    "vuelta-2026-stage-04",
    "vuelta-2026-stage-05",
    "vuelta-2026-stage-06",
    "vuelta-2026-stage-07",
    "vuelta-2026-stage-08",
    "vuelta-2026-stage-09",
    "vuelta-2026-stage-10",
    "vuelta-2026-stage-11",
    "vuelta-2026-stage-12",
    "vuelta-2026-stage-13",
    "vuelta-2026-stage-14",
    "vuelta-2026-stage-15",
    "vuelta-2026-stage-16",
    "vuelta-2026-stage-17",
    "vuelta-2026-stage-18",
    "vuelta-2026-stage-19",
    "vuelta-2026-stage-20",
    "vuelta-2026-stage-21",
}


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
    gender: str = "men"       # "men" | "women" — pelotón masculino o femenino


# ── Fuentes ────────────────────────────────────────────────────────────────

def _cs(slug: str) -> str:
    return f"https://cdn.cyclingstage.com/images/{slug}/2026/route.gpx"


def _cs_tdf(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/tour-de-france/2026/stage-{n}-route.gpx"


def _cs_vuelta(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/vuelta-spain/2026/stage-{n}-route.gpx"


def _cs_giro(n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/giro-italy/2026/stage-{n}-route.gpx"


def _cs_stage(slug: str, n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/{slug}/2026/stage-{n}-route.gpx"


def _maratona_url() -> str:
    return "https://www.maratona.it/public/sitemin/GPX_Maratona_Course.zip"


# Slug y etapas reales (verificadas con HEAD 200) para carreras cubiertas
# por cyclingstage. Lista de stages: usamos lista explícita en lugar de
# rango porque algunas carreras saltan números (p.ej. TDU 2026 no tiene
# stage 2 ni 6 publicados).
STAGE_RACES_2026: list[tuple[str, str, str, str, list[int]]] = [
    # (id_prefix, race_name, country, slug, stage_numbers)
    ("paris-nice-2026",            "Paris-Nice 2026",                        "FR", "paris-nice",                 [1, 2, 3, 4, 5, 6, 7, 8]),
    ("tirreno-adriatico-2026",     "Tirreno-Adriatico 2026",                 "IT", "tirreno-adriatico",          [1, 2, 3, 4, 5, 6, 7]),
    ("tour-down-under-2026",       "Tour Down Under 2026",                   "AU", "tour-down-under",            [1, 3, 4, 5]),
    ("volta-ao-algarve-2026",      "Volta ao Algarve 2026",                  "PT", "volta-ao-algarve",           [1, 2, 3, 4, 5]),
    ("itzulia-basque-country-2026","Itzulia Basque Country 2026",            "ES", "tour-of-the-basque-country", [1, 2, 3, 4, 5, 6]),
    ("volta-catalunya-2026",       "Volta a Catalunya 2026",                 "ES", "volta-a-catalunya",          [1, 2, 3, 4, 5, 6, 7]),
    ("ruta-del-sol-2026",          "Vuelta a Andalucía (Ruta del Sol) 2026", "ES", "ruta-del-sol",               [1, 2, 3, 4, 5]),
    ("tour-of-valencia-2026",      "Tour of Valencia 2026",                  "ES", "tour-of-valencia",           [1, 2, 3, 4, 5]),
    ("uae-tour-2026",              "UAE Tour 2026",                          "AE", "uae-tour",                   [1, 2, 3, 4, 5, 6, 7]),
    ("o-gran-camino-2026",         "O Gran Camiño 2026",                     "ES", "o-gran-camino",              [1, 2, 3, 4]),
    ("tour-of-the-alps-2026",      "Tour of the Alps 2026",                  "AT", "tour-of-the-alps",           [1, 2, 3, 4, 5]),
    # Tour Auvergne-Rhône-Alpes (ex Critérium du Dauphiné) — 7-14 jun 2026.
    # cyclingstage publicará el GPX 2-4 semanas antes; el script lo recogerá
    # cuando exista (404 hoy 13 may 2026). Reintentar a partir del 17 may.
    ("tour-aura-2026",             "Tour Auvergne-Rhône-Alpes 2026",         "FR", "tour-auvergne-rhone-alpes",  [1, 2, 3, 4, 5, 6, 7, 8]),
]


SOURCES: list[Source] = []

# ── Carga dinámica desde scripts/discovered_races.json si existe ────────
# El script `discover_stage_races.py` sondea cyclingstage y guarda el
# inventario de slugs/etapas disponibles por año. Esto permite añadir
# carreras nuevas sin editar este archivo.
DISCOVERED_FILE = REPO_ROOT / "scripts" / "discovered_races.json"

# Map de slug → id-prefix custom (para grand tours usamos tdf/giro/vuelta)
_GRAND_TOUR_IDS = {
    "tour-de-france": "tdf",
    "giro-italy": "giro",
    "vuelta-spain": "vuelta",
}


def _stage_id(slug: str, year: int, n: int) -> str:
    if slug in _GRAND_TOUR_IDS:
        return f"{_GRAND_TOUR_IDS[slug]}-{year}-stage-{n:02d}"
    return f"{slug}-{year}-stage-{n:02d}"


def _event_name(name: str, year: int) -> str:
    return f"{name} {year}"


def _load_from_discovered() -> bool:
    """Devuelve True si pudo cargar SOURCES desde discovered_races.json."""
    if not DISCOVERED_FILE.exists():
        return False
    data = json.loads(DISCOVERED_FILE.read_text())

    for year_key, slugs in data.get("grand_tours", {}).items():
        year = int(year_key)
        for slug, info in slugs.items():
            for n in info["stages"]:
                stage_label = "prologue" if n == 0 else f"Stage {n}"
                SOURCES.append(Source(
                    id=(f"{_GRAND_TOUR_IDS.get(slug, slug)}-{year}-prologue"
                        if n == 0
                        else _stage_id(slug, year, n)),
                    name=f"{info['name']} {year} — {stage_label}",
                    type="grand-tour",
                    country=info["country"],
                    source_url=(f"https://cdn.cyclingstage.com/images/{slug}/{year}/prologue-route.gpx"
                                if n == 0 else _cs_stage(slug, n)),
                    event=_event_name(info["name"], year),
                    stage=n if n > 0 else None,
                    year=year,
                ))

    for year_key, slugs in data.get("stage_races", {}).items():
        year = int(year_key)
        for slug, info in slugs.items():
            for n in info["stages"]:
                stage_label = "Prologue" if n == 0 else f"Stage {n}"
                SOURCES.append(Source(
                    id=(f"{slug}-{year}-prologue" if n == 0
                        else f"{slug}-{year}-stage-{n:02d}"),
                    name=f"{info['name']} {year} — {stage_label}",
                    type="stage-race",
                    country=info["country"],
                    source_url=(f"https://cdn.cyclingstage.com/images/{slug}/{year}/prologue-route.gpx"
                                if n == 0 else _cs_stage(slug, n)),
                    event=_event_name(info["name"], year),
                    stage=n if n > 0 else None,
                    year=year,
                ))

    for year_key, slugs in data.get("classics", {}).items():
        year = int(year_key)
        for slug, info in slugs.items():
            SOURCES.append(Source(
                id=f"{slug}-{year}",
                name=f"{info['name']} {year}",
                type="classic",
                country=info["country"],
                source_url=f"https://cdn.cyclingstage.com/images/{slug}/{year}/route.gpx",
                event=_event_name(info["name"], year),
                year=year,
            ))
    return True


# Fallback hardcoded (uso si no hay discovered_races.json)
def _load_hardcoded_2026() -> None:
    for n in range(1, 22):
        SOURCES.append(Source(
            id=f"tdf-2026-stage-{n:02d}",
            name=f"Tour de France 2026 — Stage {n}",
            type="grand-tour", country="FR", source_url=_cs_tdf(n),
            event="Tour de France 2026", stage=n,
        ))
    for n in range(1, 22):
        SOURCES.append(Source(
            id=f"giro-2026-stage-{n:02d}",
            name=f"Giro d'Italia 2026 — Stage {n}",
            type="grand-tour", country="IT", source_url=_cs_giro(n),
            event="Giro d'Italia 2026", stage=n,
        ))
    for n in range(1, 22):
        SOURCES.append(Source(
            id=f"vuelta-2026-stage-{n:02d}",
            name=f"Vuelta a España 2026 — Stage {n}",
            type="grand-tour", country="ES", source_url=_cs_vuelta(n),
            event="Vuelta a España 2026", stage=n,
        ))
    for sid, name, country, url in [
        ("omloop-het-nieuwsblad-2026", "Omloop Het Nieuwsblad 2026", "BE", _cs("omloop-het-nieuwsblad")),
        ("kuurne-brussel-kuurne-2026", "Kuurne-Brussels-Kuurne 2026", "BE", _cs("kuurne-brussels-kuurne")),
        ("strade-bianche-2026",        "Strade Bianche 2026",         "IT", _cs("strade-bianche")),
        ("milan-san-remo-2026",        "Milán-San Remo 2026",         "IT", _cs("milan-san-remo")),
        ("e3-saxo-classic-2026",       "E3 Saxo Classic 2026",        "BE", _cs("e3-saxo-classic")),
        ("gent-wevelgem-2026",         "Gent-Wevelgem 2026",          "BE", _cs("in-flanders-fields")),
        ("dwars-door-vlaanderen-2026", "Dwars door Vlaanderen 2026",  "BE", _cs("dwars-door-vlaanderen")),
        ("ronde-vlaanderen-2026",      "Tour of Flanders 2026",       "BE", _cs("tour-of-flanders")),
        ("paris-roubaix-2026",         "Paris-Roubaix 2026",          "FR", _cs("paris-roubaix")),
        ("brabantse-pijl-2026",        "De Brabantse Pijl 2026",      "BE", _cs("brabantse-pijl")),
        ("amstel-gold-race-2026",      "Amstel Gold Race 2026",       "NL", _cs("amstel-gold-race")),
        ("fleche-wallonne-2026",       "La Flèche Wallonne 2026",     "BE", _cs("la-fleche-wallonne")),
        ("liege-bastogne-liege-2026",  "Liège-Bastogne-Liège 2026",   "BE", _cs("liege-bastogne-liege")),
    ]:
        SOURCES.append(Source(
            id=sid, name=name, type="classic", country=country,
            source_url=url, event=name,
        ))
    for id_prefix, race_name, country, slug, stage_numbers in STAGE_RACES_2026:
        for n in stage_numbers:
            SOURCES.append(Source(
                id=f"{id_prefix}-stage-{n:02d}",
                name=f"{race_name} — Stage {n}",
                type="stage-race", country=country,
                source_url=_cs_stage(slug, n),
                event=race_name, stage=n,
            ))


if not _load_from_discovered():
    _load_hardcoded_2026()

# Cicloturistas con descarga directa verificada
GRAN_FONDOS = [
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
    Source(
        id="sportful-dolomiti-race-2026-medium",
        name="Sportful Dolomiti Race 2026 — Medium 125 km",
        type="gran-fondo", country="IT",
        source_url="https://www.sportfuldolomitirace.it/wp-content/uploads/2024/05/SDR-definitivo-GPX125.zip",
        event="Granfondo Sportful Dolomiti Race 2026",
        notes="Archivo del 2024 (organizador no ha republicado).",
        is_zip=True,
    ),
    Source(
        id="maratona-dles-dolomites-2026",
        name="Maratona dles Dolomites 2026 — 138 km",
        type="gran-fondo", country="IT",
        source_url=_maratona_url(),
        event="Maratona dles Dolomites 2026",
        notes="Distancia 138 km / 4230 m D+. Recorrido fijo.",
        is_zip=True,
    ),
]
SOURCES.extend(GRAN_FONDOS)


# ── Pelotón femenino 2026 ────────────────────────────────────────────────────
# cyclingstage publica GPX femeninos con bases CDN PROPIAS (distintas de la
# masculina) y, en clásicas, con sufijo `route-women.gpx`. Verificadas con
# HEAD 200 el 02/06/2026. Tour de France Femmes y Vuelta Femenina 2026 aún no
# tienen GPX publicado (carreras de ago/may): se añadirán al salir.

def _cs_women_stage(base: str, n: int) -> str:
    return f"https://cdn.cyclingstage.com/images/{base}/2026/stage-{n}-route.gpx"


def _cs_women_classic(base: str) -> str:
    return f"https://cdn.cyclingstage.com/images/{base}/2026/route-women.gpx"


def _load_women_2026() -> None:
    # Grandes vueltas femeninas. (id_prefix, race, country, cdn_base, n_stages)
    # Giro Women: GPX ya publicado. Tour de France Femmes y Vuelta Femenina:
    # cyclingstage aún no publica sus GPX 2026 (404 hoy); el fetch los salta
    # sin error y los recogerá automáticamente en cuanto existan. Bases CDN
    # verificadas con la edición 2025.
    for prefix, race, country, base, n_stages in [
        ("giro-women-2026",            "Giro d'Italia Women 2026",          "IT", "giro-women",          9),
        ("tour-de-france-femmes-2026", "Tour de France Femmes 2026",        "FR", "tour-de-france-femmes", 9),
        ("vuelta-femenina-2026",       "La Vuelta Femenina 2026",           "ES", "vuelta-femenina",     7),
        # Tour de Suisse Women 2026: 5 etapas (17-21 jun), recorridos PROPIOS
        # (más cortos que los masculinos) — NO se duplican los del hombre. Se
        # recogerá fiel en cuanto cyclingstage publique su GPX (404 a día de hoy).
        ("tour-de-suisse-women-2026",  "Tour de Suisse Women 2026",         "CH", "tour-de-suisse-women",  5),
    ]:
        type_ = "grand-tour" if n_stages >= 7 else "stage-race"
        for n in range(1, n_stages + 1):
            SOURCES.append(Source(
                id=f"{prefix}-stage-{n:02d}",
                name=f"{race} — Stage {n}",
                type=type_, country=country,
                source_url=_cs_women_stage(base, n),
                event=race, stage=n, gender="women",
            ))
    # Grandes clásicas femeninas (un día).
    for sid, name, country, base in [
        ("strade-bianche-women-2026",        "Strade Bianche Donne 2026",        "IT", "strade-bianche"),
        ("ronde-vlaanderen-women-2026",      "Tour of Flanders Women 2026",      "BE", "tour-of-flanders"),
        ("paris-roubaix-femmes-2026",        "Paris-Roubaix Femmes 2026",        "FR", "paris-roubaix"),
        ("amstel-gold-race-women-2026",      "Amstel Gold Race Ladies 2026",     "NL", "amstel-gold-race"),
        ("fleche-wallonne-femmes-2026",      "La Flèche Wallonne Femmes 2026",   "BE", "la-fleche-wallonne"),
        ("liege-bastogne-liege-femmes-2026", "Liège-Bastogne-Liège Femmes 2026", "BE", "liege-bastogne-liege"),
    ]:
        SOURCES.append(Source(
            id=sid, name=name, type="classic", country=country,
            source_url=_cs_women_classic(base), event=name, gender="women",
        ))


_load_women_2026()


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

# Tolerancia de simplificación Douglas-Peucker (metros). Garantiza que la
# línea simplificada NUNCA se aleja más de esto del track real (también en las
# horquillas), a diferencia del submuestreo uniforme, que recortaba curvas.
EPSILON_M = 8.0


def _project(points, lat0):
    """Proyección equirectangular local a metros (suficiente a escala de etapa
    para medir distancias perpendiculares en RDP)."""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    return [(p.longitude * kx, p.latitude * ky) for p in points]


def _perp_dist(px, py, ax, ay, bx, by) -> float:
    """Distancia del punto (px,py) al SEGMENTO (a→b), en las mismas unidades."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _rdp_keep(proj, eps: float) -> list[bool]:
    """Máscara de puntos a conservar (Douglas-Peucker iterativo)."""
    n = len(proj)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = proj[i]
        bx, by = proj[j]
        dmax, idx = 0.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(proj[k][0], proj[k][1], ax, ay, bx, by)
            if d > dmax:
                dmax, idx = d, k
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return keep


def decimate(gpx: gpxpy.gpx.GPX, epsilon_m: float = EPSILON_M) -> None:
    """
    Simplifica cada segmento con Douglas-Peucker: elimina puntos redundantes
    pero CONSERVANDO la forma real dentro de `epsilon_m` metros. No inventa ni
    desplaza la traza — solo descarta puntos que ya quedan sobre la línea.
    """
    for track in gpx.tracks:
        for seg in track.segments:
            pts = seg.points
            if len(pts) <= 2:
                continue
            lat0 = sum(p.latitude for p in pts) / len(pts)
            proj = _project(pts, lat0)
            keep = _rdp_keep(proj, epsilon_m)
            seg.points = [p for p, k in zip(pts, keep) if k]


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
        "gender": src.gender,
        **({"date": STAGE_DATES[src.id]} if src.id in STAGE_DATES else {}),
        **stats,
    }
    print(f"  ✓ {src.id} — {stats['distance_km']} km / {stats['elevation_gain_m']} m D+ / {size_kb:.0f} KB")
    return entry


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only == "women":
        selected = [s for s in SOURCES if s.gender == "women"]
    elif only:
        selected = [s for s in SOURCES if only in s.id]
    else:
        selected = list(SOURCES)
    # El GPX de cyclingstage de esta etapa está truncado en origen (sin Aspin
    # ni Tourmalet). La entrada de routes.json se reconstruyó a mano con OSRM +
    # EU-DEM; NO la sobreescribas re-descargando la fuente rota.
    selected = [s for s in selected if s.id not in SKIP_REFETCH]
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
