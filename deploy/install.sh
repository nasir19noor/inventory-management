#!/bin/bash
# M2VM Test App — One-shot install script
# Tested on Ubuntu 22.04 / Debian 12
#
# Usage: sudo bash deploy/install.sh

set -euo pipefail

APP_DIR="/opt/m2vm_testapp"
APP_USER="m2vm"
DB_NAME="m2vm_test"
DB_USER="m2vm_user"
DB_PASS="${M2VM_DB_PASS:-m2vm_pass_change_me}"

echo "============================================================"
echo "M2VM Test App Installer"
echo "============================================================"
echo "Target dir : $APP_DIR"
echo "DB user    : $DB_USER"
echo "DB name    : $DB_NAME"
echo "============================================================"

# ============================================================
# 1. SYSTEM PACKAGES
# ============================================================
echo "[1/6] Installing system packages..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-pip python3-venv \
    postgresql postgresql-contrib \
    nginx \
    git curl

systemctl enable --now postgresql

# ============================================================
# 2. CREATE APP USER
# ============================================================
echo "[2/6] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home "$APP_DIR" --shell /bin/bash "$APP_USER"
fi

# ============================================================
# 3. POSTGRESQL SETUP
# ============================================================
echo "[3/6] Setting up PostgreSQL..."
sudo -u postgres psql <<EOF
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = '$DB_USER') THEN
        CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
    END IF;
END
\$\$;

SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec

GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
EOF

# ============================================================
# 4. APP CODE
# ============================================================
echo "[4/6] Setting up application code..."

# Assume install.sh is run from the project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

mkdir -p "$APP_DIR"
cp -r "$PROJECT_ROOT"/* "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Setup .env if not exists
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    sed -i "s|m2vm_pass_change_me|$DB_PASS|g" "$APP_DIR/.env"
fi

# ============================================================
# 5. PYTHON VIRTUALENV
# ============================================================
echo "[5/6] Setting up Python virtualenv..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

# Initialize database (create tables + seed)
echo "  → Initializing database (this may take a moment)..."
cd "$APP_DIR"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" seed.py

# ============================================================
# 6. SYSTEMD + NGINX
# ============================================================
echo "[6/6] Configuring systemd and nginx..."
cp "$APP_DIR/deploy/m2vm-testapp.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now m2vm-testapp

# Nginx
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/m2vm-testapp
ln -sf /etc/nginx/sites-available/m2vm-testapp /etc/nginx/sites-enabled/m2vm-testapp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Allow firewall (if ufw active)
if command -v ufw &>/dev/null; then
    if ufw status | grep -q "Status: active"; then
        ufw allow 80/tcp
        ufw allow 22/tcp
    fi
fi

# ============================================================
# DONE
# ============================================================
echo ""
echo "============================================================"
echo "Installation complete!"
echo "============================================================"
echo ""
echo "App URL    : http://$(hostname -I | awk '{print $1}')/"
echo "Direct app : http://$(hostname -I | awk '{print $1}'):8000/"
echo ""
echo "Service status:"
systemctl --no-pager status m2vm-testapp | head -10
echo ""
echo "Endpoints to test M2VM:"
echo "  GET /         - Asset list (web UI)"
echo "  GET /health   - Health check"
echo "  GET /info     - VM identity (hostname, boot time)"
echo "  GET /stats    - Database statistics"
echo "  GET /metrics  - Prometheus metrics"
echo ""
echo "To start activity simulator (for replication testing):"
echo "  cd $APP_DIR"
echo "  sudo -u $APP_USER ./venv/bin/python activity_simulator.py --interval 2 &"
echo ""
echo "Logs:"
echo "  journalctl -u m2vm-testapp -f"
echo "============================================================"
