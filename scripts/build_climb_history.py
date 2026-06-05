#!/usr/bin/env python3
"""
Construye climb_history.json fusionando todos los ficheros de edición en
scripts/data/history/<race>-<year>.json.

Cada fichero de edición:
{
  "race": "Tour de France", "year": 2019,
  "climbs": [ {stage,name,side,category,lengthKm,avgGradientPct,summitFinish,summitLat,summitLon}, ... ],
  "times":  [ {name,side,stage,group,rider,timeS,lengthKm,note,sourceUrl}, ... ]
}

Política de fidelidad: solo tiempos REALES medidos (measured=true, con
source_url) + presencia. Las estimaciones NO se almacenan (la app las calcula
en vivo). Separado de routes.json: aquí no hay GPX de carreras pasadas.

Dedup: un puerto = (nombre normalizado + vertiente). La misma vertiente a lo
largo de ediciones acumula apariciones y tiempos; vertientes distintas quedan
como entradas separadas (mejor para emparejar por longitud en la app).
"""
import glob
import json
import re
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO / "scripts" / "data" / "history"
OUT = REPO / "climb_history.json"

_STOP = {"from", "via", "de", "del", "desde", "the", "col", "côte", "cote",
         "este", "oeste", "norte", "sur", "north", "south", "east", "west",
         "par", "side", "approach", "la", "le", "les"}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def side_token(side) -> str:
    if not side:
        return ""
    s = unicodedata.normalize("NFKD", side).encode("ascii", "ignore").decode()
    words = [w for w in re.split(r"[^a-zA-Z]+", s.lower()) if w and w not in _STOP]
    if not words:
        return ""
    return max(words, key=len)


def ckey(name, side):
    return f"{norm_name(name)}|{side_token(side)}"


climbs = {}


def get_entry(name, side, **geo):
    k = ckey(name, side)
    e = climbs.get(k)
    if e is None:
        e = {
            "name": name, "side": side, "category": geo.get("category"),
            "lengthKm": geo.get("lengthKm"), "avgGradientPct": geo.get("avgGradientPct"),
            "summitLat": geo.get("summitLat"), "summitLon": geo.get("summitLon"),
            "appearances": [], "performances": [],
        }
        climbs[k] = e
    else:
        # rellenar huecos de geometría/coords si llegan en otra edición
        for f in ("category", "lengthKm", "avgGradientPct", "summitLat", "summitLon"):
            if e.get(f) is None and geo.get(f) is not None:
                e[f] = geo.get(f)
    return e


_all = sorted(glob.glob(str(HISTORY_DIR / "*.json")))
files = [f for f in _all if "race" in json.loads(Path(f).read_text())]
n_editions = 0
for fp in files:
    data = json.loads(Path(fp).read_text())
    race, year = data["race"], data["year"]
    n_editions += 1
    for c in data.get("climbs", []):
        e = get_entry(c["name"], c.get("side"),
                      category=c.get("category"), lengthKm=c.get("lengthKm"),
                      avgGradientPct=c.get("avgGradientPct"),
                      summitLat=c.get("summitLat"), summitLon=c.get("summitLon"))
        app = {"race": race, "year": year, "stage": c.get("stage"),
               "role": "summit-finish" if c.get("summitFinish") else "intermediate"}
        if app not in e["appearances"]:
            e["appearances"].append(app)
    for t in data.get("times", []):
        # localizar el puerto: nombre + (vertiente si está) o aparición misma etapa
        cand = [e for k, e in climbs.items() if norm_name(e["name"]) == norm_name(t["name"])]
        target = None
        st = side_token(t.get("side"))
        if st:
            for e in cand:
                if side_token(e["side"]) == st:
                    target = e
                    break
        if target is None:
            for e in cand:
                if any(a["race"] == race and a["year"] == year and a["stage"] == t.get("stage")
                       for a in e["appearances"]):
                    target = e
                    break
        if target is None and cand:
            target = cand[0]
        if target is None:
            target = get_entry(t["name"], t.get("side"), lengthKm=t.get("lengthKm"))
            target["appearances"].append({"race": race, "year": year,
                                          "stage": t.get("stage"), "role": "intermediate"})
        target["performances"].append({
            "race": race, "year": year, "stage": t.get("stage"),
            "group": t.get("group", "leading"), "rider": t.get("rider"),
            "timeS": t["timeS"], "lengthKm": t.get("lengthKm"),
            "note": t.get("note"), "measured": True, "sourceUrl": t.get("sourceUrl"),
        })

# ── Coordenadas: propagación intra-dataset + overrides geocodificados ──
# La cima de un puerto es la misma sea cual sea la vertiente, así que las
# coords de cualquier entrada con el mismo nombre sirven para las que no las
# tienen. Luego se rellenan los huecos con coords_overrides.json (geocodif.).
OVERRIDES_FILE = Path(__file__).resolve().parent / "data" / "coords_overrides.json"
overrides = {}
if OVERRIDES_FILE.exists():
    overrides = json.loads(OVERRIDES_FILE.read_text())

coords_by_name = {}
for e in climbs.values():
    if e["summitLat"] is not None:
        coords_by_name.setdefault(norm_name(e["name"]), (e["summitLat"], e["summitLon"]))
# los overrides solo rellenan nombres sin coords nativas
for k, v in overrides.items():
    coords_by_name.setdefault(k, (v[0], v[1]))

n_filled = 0
for e in climbs.values():
    if e["summitLat"] is None:
        c = coords_by_name.get(norm_name(e["name"]))
        if c:
            e["summitLat"], e["summitLon"] = c
            n_filled += 1

# orden de apariciones y rendimientos (recientes primero)
for e in climbs.values():
    e["appearances"].sort(key=lambda a: (a["year"], a["stage"] or 0), reverse=True)
    e["performances"].sort(key=lambda p: (p["year"], p["stage"] or 0), reverse=True)

races_covered = sorted({(json.loads(Path(fp).read_text())["race"],
                         json.loads(Path(fp).read_text())["year"]) for fp in files})
out = {
    "version": 1,
    "updated": "2026-06-03",
    "note": "Histórico de puertos para comparativas. Solo tiempos reales medidos "
            "(measured=true, con source_url) + presencia. Las estimaciones las "
            "calcula la app en vivo, no se almacenan aquí. Separado de routes.json.",
    "races_covered": [{"race": r, "year": y} for r, y in races_covered],
    "climbs": sorted(climbs.values(), key=lambda e: e["name"]),
}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))

n_perf = sum(len(c["performances"]) for c in out["climbs"])
n_coords = sum(1 for c in out["climbs"] if c["summitLat"] is not None)
print(f"{n_editions} ediciones | {len(out['climbs'])} puertos | "
      f"{n_perf} tiempos reales | {n_coords} con coords")
