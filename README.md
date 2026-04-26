# wattsfit-data

Catálogo público de puertos cicloturistas y profesionales que consume la app
[Wattsfit](https://github.com/acondemellado/Wattsfit).

## `climbs.json`

172 puertos curados a partir de fuentes públicas (Wikipedia, climbbybike,
cyclingcols, salite.ch, altimetrias.net) agrupados por carrera (TDF, Giro,
Vuelta, Itzulia, Volta a Catalunya, Tour de Suisse, Tour Colombia, etc.).

Estructura de cada entrada:

```json
{
  "name": "Alpe d'Huez",
  "region": "Alpes",
  "country": "FR",
  "lengthKm": 13.8,
  "avgGradientPct": 8.1,
  "summitM": 1860,
  "races": ["TDF", "Dauphiné"]
}
```

## Cómo lo consume la app

La app descarga `climbs.json` desde
`https://raw.githubusercontent.com/acondemellado/wattsfit-data/main/climbs.json`
con TTL de 24 h (stale-while-revalidate). Edita el JSON, haz `git push` y los
usuarios reciben el catálogo nuevo en la siguiente apertura de la app.

## Contribuir un puerto

1. Edita `climbs.json` añadiendo una entrada nueva.
2. Verifica que el JSON parsea: `python3 -c "import json; json.load(open('climbs.json'))"`.
3. PR o push directo.

Distancias y pendientes: vertiente más usada en carrera. Si añades una vertiente
alternativa, sufija el nombre con la población de salida — p.ej.
`Mont Ventoux (Sault)`, `Passo dello Stelvio (Bormio)`.
