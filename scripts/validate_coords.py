#!/usr/bin/env python3
"""
Valida y limpia coords_overrides.json (geocodificadas) por FIDELIDAD:
descarta homónimos erróneos. Mejor sin coord que con coord falsa.

Reglas (sobre climb_history.json ya reconstruido):
  1. Fuera del país esperado de la carrera (+ vecinos) → descartar.
  2. Si el puerto tiene compañeros de etapa CON coords:
       - a >130 km del más cercano  → descartar (error claro).
       - a >40 km (validable)        → descartar (homónimo en otra región).
  3. Sin compañeros validables → se conserva (no se puede comprobar; el
     riesgo de un homónimo es "no empareja", no "dato falso").
"""
import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HIST = REPO / "climb_history.json"
OV = REPO / "scripts" / "data" / "coords_overrides.json"

BB = {"fr": (42.0, 51.3, -5.3, 8.4), "es": (35.8, 44.0, -9.5, 3.5),
      "it": (36.4, 47.2, 6.4, 18.7), "ad": (42.3, 42.7, 1.3, 1.8),
      "ch": (45.6, 47.9, 5.8, 10.6), "at": (46.3, 49.1, 9.5, 17.2),
      "pt": (36.9, 42.2, -9.6, -6.1), "pl": (49.0, 54.9, 14.0, 24.2),
      "ae": (22.6, 26.2, 51.5, 56.4), "co": (1.0, 13.0, -79.5, -66.8),
      "om": (16.5, 26.6, 51.8, 60.0)}
RACE_CC = {"Critérium du Dauphiné": ["fr"], "Tour de Suisse": ["ch"],
           "Tour of the Alps": ["it", "at"], "Giro del Trentino": ["it"],
           "Tirreno-Adriatico": ["it"], "Paris-Nice": ["fr"],
           "Giro d'Italia": ["it"], "Tour de France": ["fr"],
           "Vuelta a España": ["es", "ad"], "Itzulia Basque Country": ["es"],
           "Volta a Catalunya": ["es", "ad"], "Vuelta a Burgos": ["es"],
           "Vuelta a Andalucía": ["es"], "Volta ao Algarve": ["pt"],
           "Tour de Pologne": ["pl"], "UAE Tour": ["ae"], "Abu Dhabi Tour": ["ae"],
           "Route d'Occitanie": ["fr"], "Vuelta a Asturias": ["es"],
           "Tour de Romandie": ["ch"], "Tour Colombia": ["co"],
           "Tour of Oman": ["om"]}
NEIGHBORS = {"fr": ["fr", "es", "ad", "it", "ch"], "es": ["es", "ad", "fr", "pt"],
             "it": ["it", "ch", "at", "fr"], "ch": ["ch", "it", "fr", "at"],
             "ad": ["ad", "es", "fr"], "at": ["at", "it", "ch"],
             "pt": ["pt", "es"], "pl": ["pl"], "ae": ["ae"],
             "co": ["co"], "om": ["om"]}


def nn(s):
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", s)
                  .encode("ascii", "ignore").decode().lower())


def km(a, b, c, e):
    R = 6371
    p = math.pi / 180
    return 2 * R * math.asin(math.sqrt(
        math.sin((c - a) * p / 2) ** 2 +
        math.cos(a * p) * math.cos(c * p) * math.sin((e - b) * p / 2) ** 2))


def inbb(la, lo, cc):
    b = BB.get(cc)
    return b and b[0] <= la <= b[1] and b[2] <= lo <= b[3]


def main():
    d = json.loads(HIST.read_text())
    ov = json.loads(OV.read_text())
    stage = defaultdict(list)
    for c in d["climbs"]:
        if c["summitLat"] is None:
            continue
        for a in c["appearances"]:
            stage[(a["race"], a["year"], a["stage"])].append(
                (c["name"], c["summitLat"], c["summitLon"]))
    drop = set()
    for c in d["climbs"]:
        if c["summitLat"] is None or nn(c["name"]) not in ov:
            continue
        cc = set()
        for a in c["appearances"]:
            cc |= set(RACE_CC.get(a["race"], []))
        ok = set()
        for x in cc:
            ok |= set(NEIGHBORS.get(x, [x]))
        if ok and not any(inbb(c["summitLat"], c["summitLon"], x) for x in ok):
            drop.add(nn(c["name"]))
            continue
        dists = [km(c["summitLat"], c["summitLon"], o[1], o[2])
                 for a in c["appearances"]
                 for o in stage[(a["race"], a["year"], a["stage"])]
                 if o[0] != c["name"]]
        if dists:
            m = min(dists)
            if m > 40:
                drop.add(nn(c["name"]))
    for k in drop:
        ov.pop(k, None)
    OV.write_text(json.dumps(ov, ensure_ascii=False, indent=0))
    print(f"descartadas {len(drop)} | overrides restantes {len(ov)}")


if __name__ == "__main__":
    main()
