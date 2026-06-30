# Curación de GPX de carreras — guía de continuación (handoff)

> Documento para retomar este trabajo **desde cualquier ordenador** con el repo
> clonado, Dropbox sincronizado y git con acceso. Pensado para dárselo a Claude
> Code y que continúe sin re-explicar nada.

## 1. Qué es / por qué

Los GPX de carreras que veníamos usando (de **cyclingstage.com**, vía
`scripts/fetch_routes.py`) son contornos bastos: ~96 m entre puntos y saltos de
km que **cortan curvas** → la distancia horizontal sale corta → aparecen
**pendientes fantasma del 17-30 %** y el track cruza campo/bosque. La app
(`lib/engine/route_parser.dart`) recalcula D+ y pendientes desde los puntos del
GPX, así que esa geometría/elevación malas se notan en ruta.

**La cura** = partir de datos buenos:
1. **Geometría** que siga la carretera de verdad (alta resolución).
2. **Elevación** re-perfilada desde un DEM fiel al terreno.

## 2. Setup en un ordenador NUEVO (una sola vez)

```bash
# Repos (si no están)
git clone git@github.com:acondemellado/wattsfit-data.git   # repo de datos
# (la app está en github.com/acondemellado/Wattsfit, no hace falta para curar)

# Dependencia para muestrear DEMs locales (binario nativo, por máquina)
pip3 install --user rasterio

# Dropbox debe estar sincronizado: los DEMs locales viven en
#   ~/Dropbox/wattsfit-dem/   (se comparte solo entre tus equipos)
# El script lo encuentra por la env var WATTSFIT_DEM_DIR (default ~/Dropbox/wattsfit-dem)
```

No hace falta ningún ordenador concreto: **scripts** van por git, **DEMs** por
Dropbox, **rasterio** se instala en cada máquina.

## 3. Arquitectura

- **Geometría (alta resolución):**
  - **VisuGPX** (automatizable): el Tour masc está en
    `https://www.visugpx.com/download.php?id=Nln1sO8mHz` (1 GPX, 21 segmentos =
    21 etapas). NO tiene Vuelta ni Giro.
  - **VeloViewer** (export manual, ORO): TCX/GPX con 11 m + elevación limpia +
    roadbook ASO. Es la vía para carreras sin VisuGPX (Vuelta, Giro…). El
    usuario los exporta a `~/Downloads/`.
- **Elevación — `clean_lib.reprofile_from_dem` hace cascada por punto:**
  1. **DEM local** (`scripts/dem_local.py`, rasterio): GeoTIFF nacionales
     recortados al corredor. **España = IGN MDT05 5 m** (WCS
     `Elevacion4258_5`, vía **curl** porque la CA FNMT no está en certifi).
  2. **IGN Francia API** (RGE ALTI 1-5 m) — solo Francia, excelente.
  3. **EU-DEM 25 m** (OpenTopoData) — fallback Europa.
  - Una etapa España→Francia usa MDT05 en ES e IGN en FR automáticamente.
- **Pulido** (`polish_dem`): min-spacing 30 m + suavizado 200 m + clamp 20 %
  (mata ruido de segmentos cortos; NO tocar, aflojarlo solo añade ruido).
- **Scripts:** `scripts/clean_lib.py` (helpers), `scripts/ingest_hires.py`
  (modos `veloviewer` y `visugpx`), `scripts/dem_local.py` (DEM local),
  `scripts/detect_climbs_in_gpx.py` (regenera `route_climbs.json`).

## 4. Qué hay en local (Dropbox)

```
~/Dropbox/wattsfit-dem/
  spain/   ← MDT05 5 m, tiles 0.05° del corredor (Tour 1-3 ya bajados, ~260 MB)
  france/  ← (vacío; Francia se cura por API, no hace falta local)
  italy/   ← (vacío; para el Giro: TINITALY 10 m)
  global/  ← (Copernicus GLO-30 si hiciera falta fuera de cobertura nacional)
```

Los tiles se descargan solos al curar (`ensure_corridor`), se cachean y se
reutilizan. NO van al repo git (son datos, viven en Dropbox).

## 5. Cómo curar una carrera

**Caso A — hay VisuGPX (p.ej. Tour masc):**
```bash
cd ~/Developer/velotactic/wattsfit-data/scripts
# bajar la geometría una vez:
curl -L "https://www.visugpx.com/download.php?id=Nln1sO8mHz" -o data/visugpx_tdf2026.gpx
# curar (España con MDT05 local; Francia cae a la API IGN):
python3 ingest_hires.py visugpx --gpx data/visugpx_tdf2026.gpx \
    --prefix tdf-2026-stage- --first 1 --countries spain
```

**Caso B — VeloViewer (export manual, ORO; Vuelta/Giro):**
```bash
# el usuario exporta cada etapa de VeloViewer a ~/Downloads (TCX o GPX)
python3 ingest_hires.py veloviewer "~/Downloads/<archivo>.tcx" --id <route_id>
# (VeloViewer trae elevación limpia + roadbook → NO necesita DEM)
```

**Tras curar (siempre):**
```bash
python3 detect_climbs_in_gpx.py      # regenera route_climbs.json
# flujo git (ver §6)
```

`<route_id>` = id en `routes.json` (p.ej. `vuelta-2026-stage-07`,
`tour-de-france-femmes-2026-stage-03`).

## 6. Flujo git + PROTECCIÓN

El job semanal (`weekly_update.sh` → `fetch_routes.py`) re-baja cyclingstage y
**pisaría** la cura. Por eso las rutas curadas se añaden a `SKIP_REFETCH` en
`scripts/fetch_routes.py`. **Al curar una carrera nueva, añadir sus ids a
`SKIP_REFETCH`.**

```bash
cd ~/Developer/velotactic/wattsfit-data
git fetch origin && git status        # ¿el remoto avanzó? (puede haber weekly)
git add -A
git commit -m "Curar <carrera>: alta resolución + elevación fiel"
git push origin main
# Si el push se rechaza por el weekly: integrar (reset a origin/main + git checkout
# <mi-sha> -- <los GPX curados> + recomputar routes.json + detect_climbs).
```

La app coge los datos en runtime con auto-refresh del catálogo (build +46), así
que **no hace falta recompilar la app**.

## 7. Estado actual (jun 2026)

| Carrera | Geometría | Elevación | Estado |
|---|---|---|---|
| Tour Femmes 2026 (9) | VeloViewer (TCX) | VeloViewer (limpia) + roadbook | ✅ ORO |
| Tour masc 2026 (21) | VisuGPX | IGN 1-5 m (FR) / MDT05 5 m (ES 1-3) | ✅ ±1-2 % FR |
| **Vuelta 2026 (21)** | ❌ falta (no hay VisuGPX) | MDT05 listo | ⏳ **need VeloViewer export** |
| Giro 2027 | — | TINITALY pendiente | 🔜 año que viene |

Validación: Tour FR vs oficial ±1-2 %; total -2,6 %. cyclingstage tenía
fantasmas (etapa 5: +126 %). MDT05 mejoró España (etapa 2 +8 %→-3 %).

**Nota desniveles:** el D+ "oficial" de los organizadores suele ir inflado
~10 %; nuestras cifras (D+ real de carretera) salen un poco por debajo y eso es
correcto, no un error.

## 8. Gotchas

- **WCS español por curl, no urllib** (CA FNMT fuera del bundle certifi).
- **IGN RGE ALTI solo cubre Francia**; fuera devuelve nulo → cascada a EU-DEM.
- **VisuGPX incluye la zona neutralizada** de salida (~7 km) → distancias algo
  mayores que la oficial.
- **rasterio** no se comparte por Dropbox (binario); instalar en cada máquina.
- El **fichero VisuGPX** (`scripts/data/visugpx_*.gpx`, ~13 MB) está en
  `.gitignore` (re-descargable).
