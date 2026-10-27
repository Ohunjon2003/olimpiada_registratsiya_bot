#!/usr/bin/env bash
# Yangi versiyani o'rnatish: loyiha papkasidan ishga tushiring.
#   sudo bash deploy/update.sh
# Ma'lumotlar (data/) va .env saqlanib qoladi. Oldin avtomatik zaxira olinadi.
set -euo pipefail
APP_DIR=/opt/olimpiada
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[[ $EUID -eq 0 ]] || { echo "sudo bilan ishga tushiring"; exit 1; }

echo "==> Zaxira nusxa"
(cd "$APP_DIR" && sudo -u olimpiada ./venv/bin/python -m app.cli backup)

echo "==> Fayllarni yangilash"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete --exclude data --exclude .env --exclude venv --exclude __pycache__ "$SRC_DIR"/ "$APP_DIR"/
fi
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
chown -R olimpiada:olimpiada "$APP_DIR"

echo "==> Qayta ishga tushirish (baza tuzilmasi avtomatik yangilanadi)"
systemctl restart olimpiada
sleep 3
systemctl is-active --quiet olimpiada && echo "✅ Yangilandi" || { echo "❌ Xato: journalctl -u olimpiada -n 50"; exit 1; }
