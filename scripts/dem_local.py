#!/usr/bin/env python3
"""
DEM LOCAL: descarga DEMs nacionales de alta resolución recortados al CORREDOR de
cada ruta (solo la franja por donde pasa, no el país entero) y los muestrea en
local con rasterio. Sin límites de rate, offline, reproducible y compartible vía
Dropbox (`WATTSFIT_DEM_DIR`, por defecto ~/Dropbox/wattsfit-dem).

Fuentes (GeoTIFF, todas gratuitas):
  - España: WCS IGN MDT05 5 m  (cobertura Elevacion4258_5)
  - Francia: WCS IGN RGE ALTI   (Géoplateforme; ver france_wcs)
  - Italia / resto: tiles estáticos (TINITALY 10 m / Copernicus GLO-30) caídos
    en la carpeta correspondiente — LocalDem los usa sin más.

El sampler indexa TODOS los .tif de un directorio por bounds y, para cada punto,
usa el primero que lo cubra con dato válido; lo no cubierto → None (fallback).
"""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

import rasterio

DEM_DIR = Path(os.environ.get("WATTSFIT_DEM_DIR",
                              str(Path.home() / "Dropbox" / "wattsfit-dem")))

# Servicios WCS por país (GetCoverage devuelve GeoTIFF recortado al bbox).
WCS = {
    "spain": {
        "url": "https://servicios.idee.es/wcs-inspire/mdt",
        "coverage": "Elevacion4258_5",  # MDT05 5 m
        "res_m": 5,
    },
    # Francia: RGE ALTI por WCS de la Géoplateforme (1-5 m). Se añade al curar FR.
    "france": {
        "url": "https://data.geopf.fr/wcs/ows",
        "coverage": "RGEALTI-MNT_PYR-ZIP_FXX_LAMB93",  # placeholder, ver curar FR
        "res_m": 5,
    },
}


# --------------------------------------------------------------- descarga WCS
def _wcs_tile(country, lon0, lat0, lon1, lat1, out_path: Path, timeout=120):
    # Se usa curl (no urllib): los WCS de gobiernos (FNMT en España) usan CAs que
    # están en el almacén del sistema pero no en el bundle certifi de Python.
    cfg = WCS[country]
    url = (f"{cfg['url']}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
           f"&COVERAGEID={cfg['coverage']}"
           f"&SUBSET=long({lon0},{lon1})&SUBSET=lat({lat0},{lat1})"
           f"&FORMAT=image/tiff")
    tmp = out_path.with_suffix(".tmp")
    r = subprocess.run(
        ["curl", "-fsS", "--max-time", str(timeout), "-A", "wattsfit-dem/1.0",
         "-o", str(tmp), url],
        capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"curl WCS {country}: {r.stderr.strip()[:120]}")
    head = tmp.read_bytes()[:2]
    if head not in (b"II", b"MM"):  # no es TIFF (error XML)
        snippet = tmp.read_bytes()[:120]
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"WCS {country} no devolvió TIFF: {snippet!r}")
    tmp.replace(out_path)
    return out_path


def ensure_corridor(pts, country="spain", tile_deg=0.05, margin=0.004, log=print):
    """Descarga (si faltan) los tiles del DEM nacional que cubren el corredor
    de la ruta pts=[(lat,lon,...)]. Devuelve el directorio con los tiles."""
    out_dir = DEM_DIR / country
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles = set()
    for p in pts:
        la, lo = p[0], p[1]
        tiles.add((math.floor(la / tile_deg), math.floor(lo / tile_deg)))
    n_new = 0
    for (i, j) in sorted(tiles):
        lat0, lon0 = i * tile_deg, j * tile_deg
        lat1, lon1 = lat0 + tile_deg, lon0 + tile_deg
        path = out_dir / f"{country}_{i}_{j}.tif"
        if path.exists() and path.stat().st_size > 0:
            continue
        try:
            _wcs_tile(country, lon0 - margin, lat0 - margin,
                      lon1 + margin, lat1 + margin, path)
            n_new += 1
            if log:
                log(f"    DEM tile {country} {i},{j} ({path.stat().st_size//1024} KB)")
        except Exception as e:
            if log:
                log(f"    ✗ tile {country} {i},{j}: {e}")
    if log:
        log(f"  corredor {country}: {len(tiles)} tiles ({n_new} nuevos)")
    return out_dir


# --------------------------------------------------------------- muestreo
class LocalDem:
    """Muestrea elevación desde todos los GeoTIFF de uno o varios directorios.
    Para cada (lon,lat) usa el primer raster que lo cubre con dato válido."""

    def __init__(self, dirs):
        if isinstance(dirs, (str, Path)):
            dirs = [dirs]
        self._srcs = []
        for d in dirs:
            d = Path(d)
            if not d.exists():
                continue
            for tif in sorted(d.glob("*.tif")):
                try:
                    src = rasterio.open(tif)
                    self._srcs.append(src)
                except Exception:
                    pass

    def sample(self, lon, lat):
        for src in self._srcs:
            b = src.bounds
            if not (b.left <= lon <= b.right and b.bottom <= lat <= b.top):
                continue
            try:
                v = float(list(src.sample([(lon, lat)]))[0][0])
            except Exception:
                continue
            nd = src.nodata
            if nd is not None and v == nd:
                continue
            if v < -200 or v > 5000:  # fill/fuera de cobertura
                continue
            return v
        return None

    def profile(self, coords, log=print):
        # coords = [(lat, lon), ...] (mismo orden que ign_profile/dem_profile)
        out = []
        miss = 0
        for lat, lon in coords:
            v = self.sample(lon, lat)
            if v is None:
                miss += 1
            out.append(v)
        if log and miss:
            log(f"    LocalDem: {miss}/{len(coords)} pts sin cobertura local")
        return out

    def close(self):
        for s in self._srcs:
            try:
                s.close()
            except Exception:
                pass
