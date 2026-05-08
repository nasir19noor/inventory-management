# M2VM Migration Testing Checklist

Skenario lengkap untuk testing kapabilitas Migrate to Virtual Machines (M2VM)
menggunakan aplikasi ini sebagai workload.

## Skenario Test

```
[Source VM (on-prem / source cloud)]                [Target VM (GCP)]
   ├─ Ubuntu 22.04                                     (akan dibuat oleh M2VM)
   ├─ PostgreSQL 14
   ├─ M2VM Test App + 200 sample assets
   ├─ Activity simulator (continuous writes)
   └─ Nginx reverse proxy
                          │
                          │  M2VM block-level replication
                          ▼
                  [Initial sync → Continuous → Cutover]
```

## Pre-Migration Checklist

### Source VM Preparation

- [ ] OS supported (Ubuntu/Debian/RHEL/CentOS — cek M2VM support matrix)
- [ ] Root SSH access dari Migrate Connector
- [ ] Free disk space >2 GB di setiap volume (untuk dirty bitmap)
- [ ] Aplikasi sudah jalan stable, tidak ada error di log
- [ ] PostgreSQL berjalan (`systemctl status postgresql`)
- [ ] Nginx berjalan (`systemctl status nginx`)
- [ ] Aplikasi accessible via HTTP

### Application Baseline Capture

Sebelum mulai migrate, capture state aplikasi sebagai baseline:

```bash
# From your laptop / management VM
SOURCE_IP="<source-vm-ip>"

# Capture baseline
curl -s http://$SOURCE_IP/info > /tmp/baseline_info.json
curl -s http://$SOURCE_IP/stats > /tmp/baseline_stats.json
curl -s http://$SOURCE_IP/health > /tmp/baseline_health.json

# Verify
cat /tmp/baseline_info.json | python3 -m json.tool
cat /tmp/baseline_stats.json | python3 -m json.tool
```

**Yang penting di-record:**
- `hostname` (dari /info) — harus sama setelah migrate
- `total_assets`, `total_audit_logs` — minimum value setelah migrate
- `last_modified_at` — proof bahwa replication capture changes

### Database-Level Checksum

Untuk validasi data integrity:

```bash
# Run di source VM
ssh source-vm
sudo -u postgres psql m2vm_test <<EOF
SELECT 
    COUNT(*) AS asset_count,
    MD5(STRING_AGG(id::text || serial_number, ',' ORDER BY id)) AS checksum
FROM assets WHERE is_deleted = false;

SELECT COUNT(*) AS audit_count FROM audit_logs;
EOF
```

Save output untuk komparasi nanti.

## M2VM Setup

### 1. GCP Side

```bash
# Enable APIs
gcloud services enable vmmigration.googleapis.com
gcloud services enable compute.googleapis.com

# Create network resources kalau belum ada
# (assume VPC vpc-jakpro dan subnet sudah ada)
```

### 2. Deploy Migrate Connector di On-Prem

Lewat GCP Console → Migrate to Virtual Machines → Sources → Add Source.

Untuk standalone server (bukan VMware/Hyper-V):
- Pilih **AWS** sebagai source kalau dari AWS
- Atau **VMware** kalau di vCenter
- Atau pakai **Migrate to VM Linux Agent** untuk standalone Linux

Kalau ini test environment di GCP-to-GCP, pakai:
- Source type: **AWS** atau **Azure** atau **VMware** sesuai infrastruktur

### 3. Add Source VM ke Migration

Di Cloud Console:
- Migrate to VM → Migrating VMs → Add VMs
- Pilih source yang sudah didaftarkan
- Pilih VM yang akan dimigrasikan

### 4. Configure Target Details

Per VM, set:
- **Target project** dan **zone** (asia-southeast2-a misalnya)
- **Machine type** (sesuaikan dengan source)
- **Network** (vpc-jakpro)
- **Subnet** yang sesuai
- **Service account** (default Compute SA atau custom)
- **Labels** untuk tracking

## Migration Phases

### Phase 1: Initial Replication

```bash
# Trigger replication
gcloud migration vms migrating-vms start-replication SOURCE_VM_NAME \
    --source SOURCE_NAME \
    --location asia-southeast2

# Monitor replication progress
gcloud migration vms migrating-vms describe SOURCE_VM_NAME \
    --source SOURCE_NAME \
    --location asia-southeast2
```

**Yang diperhatikan:**
- Initial replication time: tergantung disk size & bandwidth
- Untuk testing app ~5 GB total, biasanya 30-90 menit di network 100 Mbps

### Phase 2: Mulai Activity Simulator

Selama initial replication berjalan, mulai activity simulator di **source VM**:

```bash
# SSH ke source
ssh source-vm

# Jalankan simulator
cd /opt/m2vm_testapp
sudo -u m2vm ./venv/bin/python activity_simulator.py \
    --interval 2 \
    --duration 7200 \
    --actor "pre-cutover-sim" \
    > /var/log/m2vm-activity.log 2>&1 &

# Monitor dari host lain
watch -n 5 "curl -s http://$SOURCE_IP/stats | python3 -m json.tool | grep -E 'total_assets|total_audit'"
```

Simulator akan:
- Insert assets baru setiap ~2 detik
- Update random asset
- Delete random asset (rare)
- Setiap action ter-record di `audit_logs`

**Perhatikan:** rate of change tinggi → continuous replication M2VM akan struggle catch up. Real-world rate biasanya lebih rendah.

### Phase 3: Test Clone (sebelum cutover)

Sebelum cutover real, **bikin test clone** untuk validate:

```bash
# Test clone via console atau gcloud
gcloud migration vms migrating-vms create-clone SOURCE_VM_NAME \
    --source SOURCE_NAME \
    --location asia-southeast2

# Setelah clone running, dapatkan IP
TARGET_IP=$(gcloud compute instances describe TARGET_VM_NAME \
    --zone asia-southeast2-a \
    --format='get(networkInterfaces[0].networkIP)')
```

**Validasi test clone:**

```bash
# 1. Hostname check (harus sama dengan source)
curl -s http://$TARGET_IP/info | python3 -m json.tool

# 2. Application up?
curl -s http://$TARGET_IP/health

# 3. Data ada?
curl -s http://$TARGET_IP/stats | python3 -m json.tool

# 4. CRUD bisa?
curl -X POST http://$TARGET_IP/api/assets \
    -H "Content-Type: application/json" \
    -d '{"name":"Test from clone","serial_number":"CLONE-TEST-001","category_id":1}'

curl -s http://$TARGET_IP/api/assets/<id> | python3 -m json.tool
```

**Catatan:** test clone **tidak ganggu** continuous replication. Source VM masih running dan replicating. Clone bisa di-discard.

### Phase 4: Cutover (Real Migration)

**Penting:** koordinasi dengan stakeholder, ada window downtime.

#### Pre-cutover

```bash
# 1. Stop activity simulator di source
ssh source-vm "sudo pkill -f activity_simulator.py"

# 2. Stop application (graceful)
ssh source-vm "sudo systemctl stop m2vm-testapp"

# 3. Tunggu PostgreSQL transactions selesai
ssh source-vm "sudo -u postgres psql -c 'CHECKPOINT;'"

# 4. Capture FINAL state di source
curl -s http://$SOURCE_IP/stats > /tmp/final_source_stats.json 2>/dev/null || \
    ssh source-vm "sudo -u postgres psql -d m2vm_test -c '
    SELECT COUNT(*) AS assets FROM assets WHERE is_deleted=false;
    SELECT COUNT(*) AS audits FROM audit_logs;
    SELECT MAX(updated_at) FROM assets;
    SELECT MAX(timestamp) FROM audit_logs;'"
```

#### Trigger Cutover

```bash
# Dari GCP Console: Migrating VMs → SOURCE_VM_NAME → CUT-OVER
# Atau via gcloud:
gcloud migration vms migrating-vms cut-over SOURCE_VM_NAME \
    --source SOURCE_NAME \
    --location asia-southeast2
```

M2VM akan:
1. Trigger final delta replication
2. Boot VM target di GCP dengan data terbaru
3. Stop replication

**Total cutover time:** 5-30 menit tergantung delta size.

#### Post-cutover Validation

```bash
# Get target VM IP
TARGET_IP=$(gcloud compute instances describe TARGET_VM_NAME \
    --zone asia-southeast2-a \
    --format='get(networkInterfaces[0].networkIP)')

# 1. Wait sampai aplikasi running di target
until curl -s -f http://$TARGET_IP/health; do
    echo "Waiting for app..."
    sleep 5
done

echo "App is up!"

# 2. Verify hostname (HARUS SAMA dengan source — proof of M2VM)
curl -s http://$TARGET_IP/info | python3 -m json.tool

# 3. Verify boot time (HARUS LEBIH BARU dari source — proof VM baru boot)
# di /info, field `boot_time` should be very recent

# 4. Compare stats
curl -s http://$TARGET_IP/stats > /tmp/post_target_stats.json
diff /tmp/final_source_stats.json /tmp/post_target_stats.json

# 5. Database integrity check
ssh m2vm@$TARGET_IP "sudo -u postgres psql m2vm_test -c '
    SELECT 
        COUNT(*) AS asset_count,
        MD5(STRING_AGG(id::text || serial_number, \",\" ORDER BY id)) AS checksum
    FROM assets WHERE is_deleted = false;
'"

# Compare checksum dengan baseline
```

**Pass criteria:**
- ✅ `/health` returns 200
- ✅ `hostname` sama dengan source
- ✅ `boot_time` lebih baru dari source (post-cutover)
- ✅ `total_assets` sama atau slightly higher (kalau simulator masih jalan saat cutover)
- ✅ `total_audit_logs` consistent
- ✅ MD5 checksum match (kalau simulator stop sebelum cutover final)
- ✅ Random sample queries return correct data
- ✅ Bisa CRUD operations baru (write berhasil)
- ✅ Audit log baru ter-record

## Validation Test Suite

Run script validasi otomatis:

```bash
#!/bin/bash
# m2vm_validation_suite.sh

SOURCE_IP="${1:-source-vm-ip}"
TARGET_IP="${2:-target-vm-ip}"

echo "=== M2VM Migration Validation ==="
echo "Source: $SOURCE_IP"
echo "Target: $TARGET_IP"
echo ""

# Test 1: Hostname identity
SRC_HOST=$(curl -s http://$SOURCE_IP/info | python3 -c "import json,sys; print(json.load(sys.stdin)['hostname'])")
TGT_HOST=$(curl -s http://$TARGET_IP/info | python3 -c "import json,sys; print(json.load(sys.stdin)['hostname'])")

if [ "$SRC_HOST" = "$TGT_HOST" ]; then
    echo "✅ PASS: Hostname identity ($SRC_HOST)"
else
    echo "❌ FAIL: Hostname mismatch (src=$SRC_HOST, tgt=$TGT_HOST)"
fi

# Test 2: Database row count
SRC_ASSETS=$(curl -s http://$SOURCE_IP/stats | python3 -c "import json,sys; print(json.load(sys.stdin)['total_assets'])")
TGT_ASSETS=$(curl -s http://$TARGET_IP/stats | python3 -c "import json,sys; print(json.load(sys.stdin)['total_assets'])")

if [ "$TGT_ASSETS" -ge "$SRC_ASSETS" ]; then
    echo "✅ PASS: Asset count (src=$SRC_ASSETS, tgt=$TGT_ASSETS)"
else
    echo "❌ FAIL: Asset count mismatch (src=$SRC_ASSETS, tgt=$TGT_ASSETS)"
fi

# Test 3: Health
TGT_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://$TARGET_IP/health)
if [ "$TGT_HEALTH" = "200" ]; then
    echo "✅ PASS: Target health check (200)"
else
    echo "❌ FAIL: Target health check ($TGT_HEALTH)"
fi

# Test 4: Write capability
RESPONSE=$(curl -s -o /tmp/write_test.json -w "%{http_code}" \
    -X POST http://$TARGET_IP/api/assets \
    -H "Content-Type: application/json" \
    -d '{"name":"Post-migration test","serial_number":"POSTMIG-'$(date +%s)'","category_id":1}')

if [ "$RESPONSE" = "201" ]; then
    echo "✅ PASS: Write capability (201)"
else
    echo "❌ FAIL: Write capability ($RESPONSE)"
fi

echo ""
echo "=== Validation Complete ==="
```

## Cleanup

Setelah test selesai:

```bash
# Stop replication
gcloud migration vms migrating-vms finalize-migration SOURCE_VM_NAME \
    --source SOURCE_NAME \
    --location asia-southeast2

# (Opsional) Decommission source
ssh source-vm "sudo systemctl stop m2vm-testapp postgresql"

# Cleanup test data di GCP
# ...
```

## Common Issues

**1. Initial replication stuck**
- Check Migrate Connector connectivity ke googleapis.com
- Check bandwidth dan firewall

**2. Target VM tidak boot**
- Check Cloud Console → VM Instances → SOURCE_VM_NAME → Logs
- Common: driver issue (rare untuk modern Linux), boot disk corruption

**3. Application tidak up di target**
- SSH ke target: `sudo journalctl -u m2vm-testapp -f`
- Check PostgreSQL: `sudo systemctl status postgresql`
- Check connectivity: `curl -v http://localhost:8000/health`

**4. Database error setelah boot**
- PostgreSQL data file corrupt — sangat jarang dengan M2VM, tapi possible
- Check: `sudo -u postgres pg_dumpall > /tmp/test.sql`
- Recovery: restore dari backup terakhir kalau perlu

**5. Hostname berbeda**
- M2VM seharusnya preserve hostname
- Kalau berbeda, kemungkinan cloud-init reset hostname
- Edit `/etc/hostname` dan `/etc/cloud/cloud.cfg` untuk preserve

## Reporting Template

Setelah test selesai, isi:

| Metric | Source | Target | Status |
|--------|--------|--------|--------|
| Hostname | _ | _ | _ |
| Boot time | _ | _ | _ |
| OS version | _ | _ | _ |
| Total assets | _ | _ | _ |
| Total audit logs | _ | _ | _ |
| Database size | _ | _ | _ |
| Checksum | _ | _ | _ |
| Initial replication time | - | _ min | _ |
| Cutover downtime | - | _ min | _ |
| App accessible after cutover | - | _ | _ |
| New writes successful | - | _ | _ |
