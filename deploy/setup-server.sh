#!/usr/bin/env bash
# Bootstrap platform-api di Ubuntu + nginx
# Jalankan di server sebagai user yang bisa sudo, dari folder mana saja:
#   bash setup-server.sh
set -euo pipefail

APP_DIR=/var/www/api_brand
REPO_URL=${REPO_URL:-https://github.com/kikarin/api_brand.git}
APP_USER=${APP_USER:-www-data}
DOMAIN_OR_IP=${DOMAIN_OR_IP:-103.31.204.10}
DB_NAME=${DB_NAME:-brand_api}
DB_USER=${DB_USER:-brand_api}
PYTHON_BIN=${PYTHON_BIN:-python3}

echo "==> 1) Paket sistem"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git nginx python3 python3-venv python3-pip \
  postgresql postgresql-contrib redis-server \
  build-essential libpq-dev curl

echo "==> 2) Pastikan Postgres + Redis jalan"
sudo systemctl enable --now postgresql redis-server

echo "==> 3) Clone / update repo"
if [[ -d "$APP_DIR/.git" ]]; then
  sudo git -C "$APP_DIR" fetch --all --prune
  sudo git -C "$APP_DIR" reset --hard origin/main
else
  sudo rm -rf "$APP_DIR"
  sudo git clone "$REPO_URL" "$APP_DIR"
fi
sudo chown -R "$USER:$USER" "$APP_DIR"

echo "==> 4) Python venv + deps"
cd "$APP_DIR"
$PYTHON_BIN -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> 5) Database role + DB (idempotent)"
# Generate password jika belum ada file .env
if [[ ! -f "$APP_DIR/.env" ]]; then
  DB_PASS=$(openssl rand -hex 16)
  SECRET=$(openssl rand -hex 32)
  cat > "$APP_DIR/.env" <<EOF
DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
REDIS_URL=redis://127.0.0.1:6379/0
TRYON_MODE=mock
DECART_API_KEY=
SESSION_MAX_SECONDS_STUDIO=120
SESSION_MAX_SECONDS_FITTING=300
SESSION_MAX_SECONDS_ECOM=60
SESSION_PAUSE_MAX_SECONDS=86400
PERSON_DETECTION_HARD_GATE=false
QR_ROTATE_SECONDS=60
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=brand-mockups
R2_PUBLIC_BASE_URL=
CF_IMAGES_ACCOUNT_HASH=
SECRET_KEY=${SECRET}
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
ENVIRONMENT=production
REGISTER_ENABLED=false
EOF
  echo "Created $APP_DIR/.env — EDIT sebelum lanjut (Decart, R2, CORS Vercel)."
else
  # Ambil password dari DATABASE_URL yang ada
  DB_PASS=$(python3 - <<'PY'
import re
from pathlib import Path
url = Path("/var/www/api_brand/.env").read_text()
m = re.search(r"DATABASE_URL=postgresql\+psycopg://[^:]+:([^@]+)@", url)
print(m.group(1) if m else "")
PY
)
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

echo "==> 6) Migrasi"
source "$APP_DIR/.venv/bin/activate"
cd "$APP_DIR"
alembic upgrade head

echo "==> 7) Systemd unit"
sudo cp "$APP_DIR/deploy/platform-api.service" /etc/systemd/system/platform-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now platform-api
sudo systemctl restart platform-api

echo "==> 8) Nginx site"
sudo cp "$APP_DIR/deploy/nginx-api_brand.conf" /etc/nginx/sites-available/api_brand
sudo sed -i "s/103.31.204.10/${DOMAIN_OR_IP}/g" /etc/nginx/sites-available/api_brand
sudo ln -sfn /etc/nginx/sites-available/api_brand /etc/nginx/sites-enabled/api_brand
sudo nginx -t
sudo systemctl reload nginx

echo "==> 9) Ownership untuk runtime"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# Biarkan user deploy tetap bisa git pull: group write opsional
sudo chmod -R u+rwX,g+rX "$APP_DIR"

echo "==> 10) Health check"
sleep 2
curl -fsS "http://127.0.0.1:8000/health" || true
echo
curl -fsS -o /dev/null -w "nginx_http=%{http_code}\n" "http://127.0.0.1/health" || true

cat <<EOF

============================================
SETUP SELESAI (dasar)
App dir : $APP_DIR
Service : sudo systemctl status platform-api
Nginx   : http://${DOMAIN_OR_IP}/health  dan  /docs

WAJIB edit $APP_DIR/.env sebelum production:
  - DECART_API_KEY
  - R2_*  (jika upload gambar)
  - CORS_ORIGINS = domain Vercel studio + fitting
  - SECRET_KEY (sudah di-generate jika .env baru)
  - TRYON_MODE=mock dulu; ganti live setelah FE siap

Lalu:
  sudo systemctl restart platform-api
============================================
EOF
