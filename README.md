# M2VM Test Application — Asset Inventory CRUD

Aplikasi Flask + PostgreSQL untuk testing Google Cloud **Migrate to Virtual Machines (M2VM)**.

## Tujuan

Aplikasi ini dirancang khusus untuk validasi M2VM, dengan fitur yang membantu testing migrasi:

- **CRUD lengkap** untuk asset inventory (relevant untuk konteks Jakpro/BUMD)
- **Health check endpoint** (`/health`) — untuk LB health probe testing
- **Build info endpoint** (`/info`) — verify hostname dan boot time setelah migrate
- **Stats endpoint** (`/stats`) — quick row count untuk validasi data integrity
- **Activity simulator** — generate continuous writes untuk test delta replication
- **Database seeding** — populate sample data untuk realistic testing
- **Audit log** — track every change (test transactional consistency)

## Struktur Project

```
m2vm_testapp/
├── app.py                      # Main Flask application
├── models.py                   # SQLAlchemy models
├── config.py                   # Configuration (DB connection, etc.)
├── seed.py                     # Database seeder
├── activity_simulator.py       # Continuous write generator
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
├── templates/                  # Jinja2 templates
│   ├── base.html
│   ├── index.html
│   ├── asset_form.html
│   └── asset_detail.html
├── static/
│   └── style.css
├── deploy/
│   ├── install.sh              # One-shot install script for VM
│   ├── m2vm-testapp.service    # systemd unit
│   └── nginx.conf              # Nginx reverse proxy config
└── README.md                   # This file
```

## Prerequisites di VM (sebelum dijalankan)

- Ubuntu 20.04 / 22.04 atau Debian 11/12
- Python 3.9+
- PostgreSQL 13+
- (Opsional) Nginx untuk reverse proxy

## Quick Start (Manual)

### 1. Install dependencies

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib nginx

# Start PostgreSQL
sudo systemctl enable --now postgresql
```

### 2. Setup PostgreSQL

```bash
sudo -u postgres psql <<EOF
CREATE DATABASE m2vm_test;
CREATE USER m2vm_user WITH PASSWORD 'm2vm_pass_change_me';
GRANT ALL PRIVILEGES ON DATABASE m2vm_test TO m2vm_user;
ALTER DATABASE m2vm_test OWNER TO m2vm_user;
EOF
```

### 3. Setup application

```bash
cd /opt
sudo git clone <repo-url> m2vm_testapp  # atau copy folder ini
cd m2vm_testapp

# Virtual env
python3 -m venv venv
source venv/bin/activate

# Install Python deps
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env sesuai kebutuhan

# Initialize database (create tables + seed data)
python seed.py

# Test run
python app.py
# → akses http://VM-IP:5000
```

### 4. Production deployment dengan systemd

```bash
sudo cp deploy/m2vm-testapp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now m2vm-testapp

# Optional: Nginx reverse proxy
sudo cp deploy/nginx.conf /etc/nginx/sites-available/m2vm-testapp
sudo ln -s /etc/nginx/sites-available/m2vm-testapp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Start activity simulator (untuk test continuous replication)

```bash
# Run in background untuk generate continuous writes
nohup python activity_simulator.py > /var/log/m2vm-activity.log 2>&1 &
```

## One-Shot Install (Automated)

Kalau mau setup cepat, jalankan:

```bash
sudo bash deploy/install.sh
```

Script ini handle semua step di atas dalam 1 command.

## Testing M2VM Migration

### Pre-Migration Baseline

Sebelum migrate, capture baseline dari source VM:

```bash
# Capture baseline data
curl http://source-vm:5000/info > baseline_info.json
curl http://source-vm:5000/stats > baseline_stats.json
```

Output `/info` includes:
- hostname
- boot_time
- ip_addresses
- pid
- python_version

Output `/stats` includes:
- total_assets
- total_categories
- total_audit_logs
- last_modified_at
- database_size

### During Migration

Jalankan activity simulator di source untuk generate continuous writes selama replication:

```bash
# Active changes setiap 2 detik
python activity_simulator.py --interval 2 --duration 3600
```

Ini akan:
- Insert random new asset setiap interval
- Update random existing asset
- Delete random asset (dengan probability rendah)
- Log setiap operasi ke audit_log table

Selama M2VM continuous replication, perubahan ini akan tracked dan sync ke target.

### Post-Migration Validation

Setelah cutover ke target VM:

```bash
# Capture post-migration data
curl http://target-vm:5000/info > post_info.json
curl http://target-vm:5000/stats > post_stats.json

# Compare
diff baseline_stats.json post_stats.json
```

**Expected results:**
- `total_assets`, `total_categories`, `total_audit_logs` should match (atau slightly higher kalau activity simulator run sampai cutover)
- `last_modified_at` should be very recent (bukti delta replication works)
- `hostname` di `/info` should sama dengan source (proof same VM, bukan rebuild)
- `boot_time` should be different (target VM baru boot)

### Data Integrity Verification

```bash
# Quick checksum semua asset table
psql -U m2vm_user -d m2vm_test -c "
  SELECT 
    COUNT(*) AS row_count,
    MD5(STRING_AGG(id::text || name || serial_number, ',' ORDER BY id)) AS checksum
  FROM assets;
"
```

Run di source dan target — checksum harus match (kalau activity simulator stop saat cutover).

## API Endpoints

### Web UI (HTML)

- `GET /` — list all assets dengan filter dan pagination
- `GET /assets/new` — form tambah asset
- `POST /assets` — create asset
- `GET /assets/<id>` — view asset detail dengan audit log
- `GET /assets/<id>/edit` — form edit asset
- `POST /assets/<id>/update` — update asset
- `POST /assets/<id>/delete` — soft delete asset

### REST API (JSON)

- `GET /api/assets` — list assets
- `POST /api/assets` — create asset
- `GET /api/assets/<id>` — get asset
- `PUT /api/assets/<id>` — update asset
- `DELETE /api/assets/<id>` — delete asset

### Operational Endpoints

- `GET /health` — health check (return 200 jika DB reachable)
- `GET /info` — system info (hostname, boot time, IP)
- `GET /stats` — database statistics
- `GET /metrics` — Prometheus-style metrics (basic)

## Network Ports

| Port | Service | Notes |
|------|---------|-------|
| 5000 | Flask app (direct) | Dev only |
| 8000 | Gunicorn (production) | Behind Nginx |
| 80   | Nginx reverse proxy | Public-facing |
| 5432 | PostgreSQL | Internal only |

## Troubleshooting

**App tidak start:**
```bash
sudo journalctl -u m2vm-testapp -f
```

**Database connection error:**
```bash
# Check PostgreSQL running
sudo systemctl status postgresql

# Test connection
psql -h localhost -U m2vm_user -d m2vm_test -c "SELECT version();"
```

**Network tidak accessible:**
```bash
# Check firewall
sudo ufw status
sudo ufw allow 5000/tcp  # atau 80 untuk nginx
```

## License

Untuk testing internal, no license restriction.
