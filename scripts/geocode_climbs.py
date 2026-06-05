#!/usr/bin/env python3
"""
Geocodifica las cimas de puertos del histórico que no tienen coordenadas,
usando Nominatim (OpenStreetMap). Escribe scripts/data/history/coords_overrides.json
(normname -> [lat, lon]) que el builder aplica. Idempotente: reanuda donde
quedó y no re-geocodifica lo ya resuelto.

Política: solo cols/puertos con nombre reconocible; valida bbox europeo;
respeta el rate-limit de Nominatim (>=1 s/petición, User-Agent propio).
"""
import json
import re
import ssl
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

REPO = Path(__file__).resolve().parent.parent
HIST = REPO / "climb_history.json"
OUT = REPO / "scripts" / "data" / "coords_overrides.json"

UA = "Wattsfit climb-history geocoder (acondemellado@gmail.com)"
# Solo nombres de col/puerto (las "Côte de [pueblo]" cat-4 se omiten: bajo valor
# y geocodifican al pueblo, no a la cima).
KW = re.compile(r"\b(col|coll|port|porte|puerto|alto|alt|passo|cima|mont|mont[ée]e|"
                r"pas|hourquette|cormet|cumbre|santuario|mirador|lagos|plateau|pla|"
                r"balc[oó]n|collada|collado|portillo|portel|portet|formigal|angliru|"
                r"morcuera|cotos|stelvio|mortirolo|zoncolan|gavia|giau|pordoi|fedaia|"
                r"tre cime|blockhaus|etna|aprica|tonale|izoard|galibier|madeleine|"
                r"tourmalet|aubisque|peyresourde|aspin|soulor|spandelles|granon|"
                r"arrate|jaizkibel|usartza|krabelin|urkiola|vallter|molina|ainé|"
                r"boixar|montserrat|queralt|taüll|ventoux|loze|bonette|vars|"
                r"glandon|croix de fer|telegraphe|t[ée]l[ée]graphe|turini|colmiane|"
                r"couillole|isola|peyragudes|hautacam|superbagn[èe]res|beille|adet)\b", re.I)

EUR_BBOX = (35.5, 53.0, -10.0, 18.0)  # lat_min, lat_max, lon_min, lon_max


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def clean_query(name: str) -> str:
    # quita anotaciones entre paréntesis (vertiente/pueblo) que despistan
    n = re.sub(r"\([^)]*\)", "", name).strip()
    return n


_PREFIX = re.compile(r"^(alto de l'|alto del |alto de la |alto de |alt del |alt de |"
                     r"puerto de la |puerto de |collada de |collado de |coll de la |"
                     r"coll de |mirador de |santuario de |lagos de |pla de |"
                     r"montee de |montée de |alto d'|alto de l |port de )", re.I)


def strip_prefix(name: str) -> str:
    return _PREFIX.sub("", name).strip()


def geocode(name: str, ccodes: str):
    q = urllib.parse.urlencode({
        "q": name, "format": "json", "limit": 1,
        "countrycodes": ccodes,
    })
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    if not data:
        return None
    lat = float(data[0]["lat"]); lon = float(data[0]["lon"])
    if not (EUR_BBOX[0] <= lat <= EUR_BBOX[1] and EUR_BBOX[2] <= lon <= EUR_BBOX[3]):
        return None
    return (round(lat, 5), round(lon, 5), data[0].get("type", ""), data[0].get("class", ""))


# País por carrera: restringe el geocoding para evitar homónimos en otro país.
RACE_CC = {
    "Giro d'Italia": "it",
    "Tour de France": "fr",
    "Vuelta a España": "es,ad",
    "Itzulia Basque Country": "es",
    "Volta a Catalunya": "es,ad",
    "Critérium du Dauphiné": "fr",
    "Tour de Suisse": "ch",
    "Tour of the Alps": "it,at",
    "Giro del Trentino": "it",
    "Tirreno-Adriatico": "it",
    "Paris-Nice": "fr",
}


def main():
    d = json.loads(HIST.read_text())
    # ADITIVO: conserva las coords ya geocodificadas y solo añade las nuevas.
    overrides = json.loads(OUT.read_text()) if OUT.exists() else {}

    # nombres distintos SIN coords (ni nativas ni override) y con nombre de
    # col/puerto; guardamos el país (códigos) según las carreras del puerto.
    targets = {}
    for c in d["climbs"]:
        if c["summitLat"] is not None:
            continue
        nn = norm_name(c["name"])
        if nn in targets or nn in overrides or not KW.search(c["name"]):
            continue
        ccs = set()
        for a in c["appearances"]:
            for code in RACE_CC.get(a["race"], "").split(","):
                if code:
                    ccs.add(code)
        targets[nn] = (clean_query(c["name"]), ",".join(sorted(ccs)) or "fr,es,it,ad")

    print(f"a geocodificar: {len(targets)}")
    ok = 0
    for i, (nn, (q, ccodes)) in enumerate(sorted(targets.items(), key=lambda x: x[1][0]), 1):
        res = geocode(q, ccodes)
        if res is None:
            alt = strip_prefix(q)
            if alt and alt != q:
                time.sleep(1.1)
                res = geocode(alt, ccodes)
        if res:
            overrides[nn] = [res[0], res[1]]
            ok += 1
            tag = f"{res[3]}/{res[2]}"
        else:
            tag = "—"
        if i % 25 == 0 or res is None:
            print(f"  [{i}/{len(targets)}] {q[:34]:<34} {tag}")
        # guardar cada 20 por si se corta
        if i % 20 == 0:
            OUT.write_text(json.dumps(overrides, ensure_ascii=False, indent=0))
        time.sleep(1.1)
    OUT.write_text(json.dumps(overrides, ensure_ascii=False, indent=0))
    print(f"geocodificados OK: {ok} | total overrides: {len(overrides)}")


if __name__ == "__main__":
    main()
