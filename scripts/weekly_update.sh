#!/bin/bash
# Re-descubre carreras, refresca GPX y publica cambios al repo.
# Pensado para correr como launchd/cron semanal.
#
# Comportamiento:
#   1. cd al repo y `git pull --rebase`
#   2. Ejecuta discover_stage_races.py (sondea cyclingstage)
#   3. Ejecuta fetch_routes.py (descarga GPX, regenera routes.json)
#   4. Si hay cambios → commit + push
#   5. Loguea todo a logs/weekly_update.log
#
# Limitaciones:
#   - El Mac debe estar despierto a la hora programada.
#   - `git push` necesita la clave SSH disponible (ssh-agent + keychain).
#     Si falla, el commit queda local y el siguiente run lo intenta de
#     nuevo.

set -euo pipefail

REPO_DIR="/Users/Beto/wattsfit-data"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/weekly_update.log"

mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

exec >> "$LOG_FILE" 2>&1
echo
echo "===== $(ts) — weekly_update start ====="

cd "$REPO_DIR"

# 1) Sincronizar con remoto
echo "[$(ts)] git pull --rebase"
if ! git pull --rebase --autostash; then
  echo "[$(ts)] git pull falló — abortando"
  exit 1
fi

# 2) Descubrir carreras
echo "[$(ts)] running discover_stage_races.py"
python3 scripts/discover_stage_races.py || {
  echo "[$(ts)] discover falló (continuo con JSON previo)"
}

# 3) Bajar GPX
echo "[$(ts)] running fetch_routes.py"
python3 scripts/fetch_routes.py || {
  echo "[$(ts)] fetch falló"
  exit 2
}

# 4) Diff
if [[ -z "$(git status --porcelain)" ]]; then
  echo "[$(ts)] sin cambios"
  echo "===== $(ts) — weekly_update done (no-op) ====="
  exit 0
fi

# Reporta qué cambió
echo "[$(ts)] cambios detectados:"
git status --short

# Commit con resumen
NEW_COUNT=$(python3 -c "import json; print(json.load(open('routes.json'))['count'])" 2>/dev/null || echo "?")
git add scripts/discovered_races.json routes/ routes.json
git commit -m "data(auto): weekly refresh — ${NEW_COUNT} rutas totales

Generado por scripts/weekly_update.sh.
$(date '+Fecha: %Y-%m-%d %H:%M')
" || true

if git push origin main; then
  echo "[$(ts)] push OK"
else
  echo "[$(ts)] push falló — commit queda local"
fi

echo "===== $(ts) — weekly_update done ====="
