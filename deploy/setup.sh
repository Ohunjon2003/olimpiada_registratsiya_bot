#!/usr/bin/env bash
# =====================================================================
#  Olimpiada tizimini DigitalOcean (Ubuntu 22.04 / 24.04) serverga o'rnatish
#  Ishlatish (root sifatida, loyiha papkasi ichida):
#     sudo bash deploy/setup.sh                 # faqat IP bilan
#     sudo bash deploy/setup.sh olimpiada.uz    # domen + bepul SSL (HTTPS)
# =====================================================================
set -euo pipefail

DOMAIN="${1:-}"
APP_DIR=/opt/olimpiada
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "❌ root sifatida ishga tushiring: sudo bash deploy/setup.sh"; exit 1; fi

echo "==> 1/8 Tizim paketlari"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx sqlite3 ufw fonts-dejavu-core rsync
if [[ -n "$DOMAIN" ]]; then apt-get install -y certbot python3-certbot-nginx; fi

echo "==> 2/8 Foydalanuvchi va papka"
id olimpiada &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin olimpiada
mkdir -p "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete --exclude data --exclude .env --exclude venv --exclude __pycache__ "$SRC_DIR"/ "$APP_DIR"/
fi
mkdir -p "$APP_DIR/data"

echo "==> 3/8 Python muhiti va kutubxonalar"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "==> 4/8 Sozlamalar (.env)"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  SECRET=$("$APP_DIR/venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET/" "$APP_DIR/.env"
  read -rp "Telegram bot tokeni (@BotFather): " TOKEN
  read -rp "Admin login [admin]: " AU; AU=${AU:-admin}
  while true; do
    read -rsp "Admin paroli (kamida 8 belgi, harf+raqam): " AP; echo
    [[ ${#AP} -ge 8 ]] && break || echo "Juda qisqa"
  done
  # Qiymatlarni Python orqali yozamiz (maxsus belgilar sed'ni buzmasligi uchun)
  TOKEN="$TOKEN" AU="$AU" AP="$AP" "$APP_DIR/venv/bin/python" - "$APP_DIR/.env" <<'PY'
import os, re, sys
path = sys.argv[1]
text = open(path).read()
for key, env in (("BOT_TOKEN", "TOKEN"), ("ADMIN_USERNAME", "AU"), ("ADMIN_PASSWORD", "AP")):
    val = os.environ[env].replace("\n", "")
    text = re.sub(rf"^{key}=.*$", lambda m: f"{key}={val}", text, flags=re.M)
open(path, "w").write(text)
PY
  if [[ -n "$DOMAIN" ]]; then sed -i "s/^COOKIE_SECURE=.*/COOKIE_SECURE=true/" "$APP_DIR/.env"; fi
else
  echo "   .env mavjud — o'zgartirilmadi"
fi
chmod 600 "$APP_DIR/.env"
chown -R olimpiada:olimpiada "$APP_DIR"

echo "==> 5/8 systemd xizmati"
cp "$APP_DIR/deploy/olimpiada.service" /etc/systemd/system/olimpiada.service
systemctl daemon-reload
systemctl enable olimpiada
systemctl restart olimpiada

echo "==> 6/8 Nginx"
sed "s/DOMEN_YOKI_IP/${DOMAIN:-_}/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/olimpiada
ln -sf /etc/nginx/sites-available/olimpiada /etc/nginx/sites-enabled/olimpiada
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> 7/8 Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

if [[ -n "$DOMAIN" ]]; then
  echo "==> 8/8 SSL sertifikat (Let's Encrypt)"
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || \
    echo "⚠️  SSL olinmadi. Domen DNS'i server IP'siga yo'naltirilganini tekshirib, qayta: certbot --nginx -d $DOMAIN"
else
  echo "==> 8/8 SSL o'tkazib yuborildi (domen berilmagan)"
fi

sleep 3
if systemctl is-active --quiet olimpiada; then
  IP=$(curl -s -4 ifconfig.me || hostname -I | awk '{print $1}')
  echo
  echo "✅ Tayyor!"
  echo "   Panel: ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://$IP}"
  echo "   Loglar: journalctl -u olimpiada -f"
  echo "   Kirgandan keyin parolni almashtiring va .env dagi ADMIN_PASSWORD ni tozalang."
else
  echo "❌ Xizmat ishga tushmadi. Sababini ko'ring: journalctl -u olimpiada -n 50"
  exit 1
fi
