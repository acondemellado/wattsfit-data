#!/usr/bin/env python3
"""
Discovery automático de carreras en cyclingstage.com para el año actual y
el siguiente. Sondea HEAD a la CDN para detectar:
  - Nuevas carreras (slugs candidatos que devuelvan 200)
  - Nuevas etapas en carreras ya conocidas
  - Carreras de una nueva temporada (year+1)

Salida: imprime un fragmento Python listo para pegar en fetch_routes.py
o, con --json, genera un fichero `discovered_races.json` con la estructura
{year: {slug: [stage_numbers]}}.

Usa solo HEAD para minimizar tráfico. ~5 segundos por slug-año.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

import certifi

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
UA = "Wattsfit/1.0 catalog discovery"
TIMEOUT = 12

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = REPO_ROOT / "scripts" / "discovered_races.json"

# Catálogo amplio de slugs candidatos que cyclingstage suele cubrir.
# Si un slug devuelve 404 todo el año, simplemente lo ignoramos en silencio.
GRAND_TOUR_SLUGS: dict[str, tuple[str, str, str]] = {
    # slug → (country, race_name, id_prefix)
    "tour-de-france":  ("FR", "Tour de France",   "tdf"),
    "giro-italy":      ("IT", "Giro d'Italia",    "giro"),
    "vuelta-spain":    ("ES", "Vuelta a España",  "vuelta"),
}

CANDIDATE_SLUGS: list[tuple[str, str, str]] = [
    # (slug, country, race_name)
    ("paris-nice",                       "FR", "Paris-Nice"),
    ("tirreno-adriatico",                "IT", "Tirreno-Adriatico"),
    ("tour-down-under",                  "AU", "Tour Down Under"),
    ("volta-ao-algarve",                 "PT", "Volta ao Algarve"),
    ("tour-of-the-basque-country",       "ES", "Itzulia Basque Country"),
    ("volta-a-catalunya",                "ES", "Volta a Catalunya"),
    ("ruta-del-sol",                     "ES", "Vuelta a Andalucía (Ruta del Sol)"),
    ("tour-of-valencia",                 "ES", "Tour of Valencia"),
    ("uae-tour",                         "AE", "UAE Tour"),
    ("o-gran-camino",                    "ES", "O Gran Camiño"),
    ("tour-of-the-alps",                 "AT", "Tour of the Alps"),
    ("tour-de-france",                   "FR", "Tour de France"),
    ("giro-italy",                       "IT", "Giro d'Italia"),
    ("vuelta-spain",                     "ES", "Vuelta a España"),
    ("tour-auvergne-rhone-alpes",        "FR", "Tour Auvergne-Rhône-Alpes"),
    ("criterium-du-dauphine",            "FR", "Critérium du Dauphiné"),
    ("tour-de-suisse",                   "CH", "Tour de Suisse"),
    ("tour-de-romandie",                 "CH", "Tour de Romandie"),
    ("vuelta-a-burgos",                  "ES", "Vuelta a Burgos"),
    ("vuelta-a-asturias",                "ES", "Vuelta a Asturias"),
    ("tour-de-pologne",                  "PL", "Tour de Pologne"),
    ("tour-of-britain",                  "GB", "Tour of Britain"),
    ("tour-de-poland",                   "PL", "Tour of Poland"),
    ("benelux-tour",                     "BE", "Renewi Tour"),
    ("renewi-tour",                      "BE", "Renewi Tour"),
    ("4-jours-de-dunkerque",             "FR", "4 Jours de Dunkerque"),
    ("etoile-de-besseges",               "FR", "Étoile de Bessèges"),
    ("tour-de-la-provence",              "FR", "Tour de la Provence"),
    ("tour-of-norway",                   "NO", "Tour of Norway"),
    ("arctic-race-of-norway",            "NO", "Arctic Race of Norway"),
    ("tour-of-slovenia",                 "SI", "Tour of Slovenia"),
    ("tour-of-turkey",                   "TR", "Tour of Turkey"),
    ("tour-of-guangxi",                  "CN", "Tour of Guangxi"),
    ("tour-of-japan",                    "JP", "Tour of Japan"),
    ("settimana-coppi-bartali",          "IT", "Settimana Coppi e Bartali"),
    ("giro-di-sicilia",                  "IT", "Giro di Sicilia"),
    ("giro-del-veneto",                  "IT", "Giro del Veneto"),
    ("tour-de-france",                   "FR", "Tour de France"),
    ("giro-italy",                       "IT", "Giro d'Italia"),
    ("vuelta-spain",                     "ES", "Vuelta a España"),
]

# Filtramos las grand tours del listado de stage races para no duplicarlas.
CANDIDATE_SLUGS = [
    e for e in CANDIDATE_SLUGS if e[0] not in GRAND_TOUR_SLUGS
]

# Clásicas (formato /YEAR/route.gpx)
CANDIDATE_CLASSICS: list[tuple[str, str, str]] = [
    ("omloop-het-nieuwsblad",   "BE", "Omloop Het Nieuwsblad"),
    ("kuurne-brussels-kuurne",  "BE", "Kuurne-Brussels-Kuurne"),
    ("strade-bianche",          "IT", "Strade Bianche"),
    ("milan-san-remo",          "IT", "Milán-San Remo"),
    ("e3-saxo-classic",         "BE", "E3 Saxo Classic"),
    ("in-flanders-fields",      "BE", "Gent-Wevelgem"),
    ("dwars-door-vlaanderen",   "BE", "Dwars door Vlaanderen"),
    ("tour-of-flanders",        "BE", "Tour of Flanders"),
    ("paris-roubaix",           "FR", "Paris-Roubaix"),
    ("brabantse-pijl",          "BE", "De Brabantse Pijl"),
    ("amstel-gold-race",        "NL", "Amstel Gold Race"),
    ("la-fleche-wallonne",      "BE", "La Flèche Wallonne"),
    ("liege-bastogne-liege",    "BE", "Liège-Bastogne-Liège"),
    ("eschborn-frankfurt",      "DE", "Eschborn-Frankfurt"),
    ("clasica-san-sebastian",   "ES", "Clásica San Sebastián"),
    ("bretagne-classic",        "FR", "Bretagne Classic Ouest-France"),
    ("gp-quebec",               "CA", "GP Cycliste Québec"),
    ("gp-montreal",              "CA", "GP Cycliste Montréal"),
    ("paris-tours",             "FR", "Paris-Tours"),
    ("il-lombardia",            "IT", "Il Lombardia"),
    ("tour-of-lombardy",        "IT", "Il Lombardia"),
    ("gp-cerami",               "BE", "GP de Wallonie"),
]


def head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


def discover_stage_race(slug: str, year: int, max_stages: int = 25) -> list[int]:
    """Devuelve la lista de etapas con GPX 200 OK."""
    stages: list[int] = []
    # Algunas carreras tienen prologue → lo tratamos como stage 0.
    if head_ok(f"https://cdn.cyclingstage.com/images/{slug}/{year}/prologue-route.gpx"):
        stages.append(0)
    consecutive_misses = 0
    for n in range(1, max_stages + 1):
        url = f"https://cdn.cyclingstage.com/images/{slug}/{year}/stage-{n}-route.gpx"
        if head_ok(url):
            stages.append(n)
            consecutive_misses = 0
        else:
            consecutive_misses += 1
            # Si llevamos 3 fallos consecutivos por encima de 10, paramos.
            if consecutive_misses >= 3 and n >= 10:
                break
    return stages


def discover_classic(slug: str, year: int) -> bool:
    url = f"https://cdn.cyclingstage.com/images/{slug}/{year}/route.gpx"
    return head_ok(url)


def main() -> int:
    p = argparse.ArgumentParser()
    today = date.today()
    default_years = [today.year, today.year + 1]
    p.add_argument("--years", type=int, nargs="*", default=default_years,
                   help=f"Años a sondear (default: {default_years})")
    p.add_argument("--print-only", action="store_true",
                   help="No escribir JSON, solo imprimir resumen")
    args = p.parse_args()

    print(f"Discovery cyclingstage para años: {args.years}", file=sys.stderr)
    out: dict[str, dict] = {"grand_tours": {}, "stage_races": {}, "classics": {}}

    for year in args.years:
        year_key = str(year)
        out["grand_tours"][year_key] = {}
        out["stage_races"][year_key] = {}
        out["classics"][year_key] = {}

        for slug, (country, name, id_prefix) in GRAND_TOUR_SLUGS.items():
            stages = discover_stage_race(slug, year, max_stages=23)
            if stages:
                out["grand_tours"][year_key][slug] = {
                    "country": country, "name": name,
                    "id_prefix": id_prefix, "stages": stages,
                }
                print(f"  [{year}] GT {slug}: {len(stages)} etapas",
                      file=sys.stderr)
            time.sleep(0.1)

        for slug, country, name in CANDIDATE_SLUGS:
            stages = discover_stage_race(slug, year)
            if stages:
                out["stage_races"][year_key][slug] = {
                    "country": country, "name": name, "stages": stages,
                }
                print(f"  [{year}] {slug}: {len(stages)} etapas {stages}",
                      file=sys.stderr)
            time.sleep(0.1)

        for slug, country, name in CANDIDATE_CLASSICS:
            if discover_classic(slug, year):
                out["classics"][year_key][slug] = {
                    "country": country, "name": name,
                }
                print(f"  [{year}] CLÁSICA {slug}", file=sys.stderr)
            time.sleep(0.1)

    if args.print_only:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    else:
        OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"Guardado en {OUT_FILE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
