"""Database seeder — initialize tables and populate sample data."""
import random
from datetime import datetime, date, timedelta
from decimal import Decimal

from app import create_app
from models import db, Asset, Category, AuditLog, log_change


CATEGORIES = [
    ("Laptop", "Notebook computer untuk staf operasional"),
    ("Desktop", "Komputer desktop untuk workstation"),
    ("Server", "Server fisik dan virtual"),
    ("Network Equipment", "Switch, router, access point, firewall"),
    ("Printer", "Printer, scanner, MFP"),
    ("Monitor", "Display dan monitor"),
    ("Phone", "IP phone dan handphone kantor"),
    ("Office Equipment", "Proyektor, AC, peralatan kantor lain"),
    ("Vehicle", "Kendaraan operasional"),
    ("Furniture", "Meja, kursi, lemari kantor"),
]

ASSET_TEMPLATES = {
    "Laptop": [
        ("Lenovo ThinkPad", ["T14", "X1 Carbon", "L14", "T490", "P15"]),
        ("Dell Latitude", ["7420", "5420", "5520", "9420"]),
        ("HP EliteBook", ["840 G8", "850 G7", "1040 G9"]),
        ("Apple MacBook", ["Pro 14", "Pro 16", "Air M2"]),
    ],
    "Desktop": [
        ("Dell OptiPlex", ["7090", "5090", "3090"]),
        ("HP ProDesk", ["600 G6", "400 G7"]),
        ("Lenovo ThinkCentre", ["M70q", "M90s"]),
    ],
    "Server": [
        ("Dell PowerEdge", ["R740", "R650", "R250"]),
        ("HPE ProLiant", ["DL380 Gen10", "DL360 Gen10"]),
        ("Cisco UCS", ["C220 M5", "C240 M5"]),
    ],
    "Network Equipment": [
        ("Cisco Catalyst", ["9300-48P", "9200-24T", "2960-X"]),
        ("Fortinet FortiGate", ["100F", "60F", "200F"]),
        ("Aruba", ["6300M", "6200F", "AP-535"]),
        ("Mikrotik", ["CCR2004", "CRS328", "RB4011"]),
    ],
    "Printer": [
        ("HP LaserJet", ["M404dn", "M607dn", "Pro M428"]),
        ("Canon imageRUNNER", ["2535i", "C3025i"]),
        ("Epson EcoTank", ["L3210", "L6190"]),
    ],
    "Monitor": [
        ("Dell UltraSharp", ["U2722D", "U2422H", "U2723QE"]),
        ("LG", ['27" 4K', '24" Full HD', '32" QHD']),
        ("Samsung", ['ViewFinity S6', 'Odyssey G5']),
    ],
    "Phone": [
        ("Cisco IP Phone", ["8841", "8851", "7841"]),
        ("Yealink", ["T54W", "T46U"]),
        ("Polycom", ["VVX 350", "VVX 450"]),
    ],
    "Office Equipment": [
        ("Epson Projector", ["EB-X51", "EB-W52"]),
        ("Daikin AC", ['1.5 PK Inverter', '2 PK Standard']),
        ("Panasonic AC", ['1 PK Inverter', '2 PK Inverter']),
    ],
    "Vehicle": [
        ("Toyota", ["Avanza", "Innova", "Hilux"]),
        ("Suzuki", ["Carry Pickup", "Ertiga"]),
        ("Mitsubishi", ["L300", "Xpander"]),
    ],
    "Furniture": [
        ("Office Chair", ["Ergonomic Mesh", "Executive Leather", "Standard"]),
        ("Office Desk", ["Standing Desk", "L-Shape", "Standard 120cm"]),
        ("Filing Cabinet", ["4-Drawer", "Mobile Pedestal"]),
    ],
}

LOCATIONS = [
    "HQ Lt. 1 - Lobby", "HQ Lt. 2 - Operations", "HQ Lt. 3 - Finance",
    "HQ Lt. 4 - IT Department", "HQ Lt. 5 - Director's Office",
    "Data Center - Rack A1", "Data Center - Rack A2", "Data Center - Rack B1",
    "Branch Office - Bandung", "Branch Office - Surabaya",
    "Warehouse - Cikarang", "Warehouse - Tangerang",
    "Field Site - Project A", "Field Site - Project B",
]

STATUSES = ["active", "active", "active", "active", "in_repair", "retired"]


def generate_serial(prefix, year):
    """Generate a unique-ish serial number."""
    return f"{prefix}-{year}-{random.randint(10000, 99999)}"


def seed_categories():
    """Insert categories if not exists."""
    print("Seeding categories...")
    existing = {c.name for c in Category.query.all()}
    inserted = 0
    for name, desc in CATEGORIES:
        if name not in existing:
            db.session.add(Category(name=name, description=desc))
            inserted += 1
    db.session.commit()
    print(f"  Inserted {inserted} categories ({len(CATEGORIES)} total)")


def seed_assets(target_count=200):
    """Generate sample assets with audit log."""
    current_count = Asset.query.count()
    if current_count >= target_count:
        print(f"Already have {current_count} assets, skipping seed.")
        return

    to_create = target_count - current_count
    print(f"Seeding {to_create} assets...")

    categories = {c.name: c for c in Category.query.all()}
    serial_set = {a.serial_number for a in Asset.query.all()}

    created = 0
    for _ in range(to_create):
        cat_name = random.choice(list(categories.keys()))
        cat = categories[cat_name]

        templates = ASSET_TEMPLATES.get(cat_name, [("Generic", ["Item"])])
        brand, models = random.choice(templates)
        model = random.choice(models)

        prefix = "".join([w[0].upper() for w in cat_name.split()][:3])
        year = random.randint(2019, 2025)

        # Ensure unique serial
        for _ in range(10):
            serial = generate_serial(prefix, year)
            if serial not in serial_set:
                serial_set.add(serial)
                break

        purchase_date = date(year, random.randint(1, 12), random.randint(1, 28))
        purchase_price = Decimal(random.randint(500, 50000)) * Decimal("1000")  # IDR

        asset = Asset(
            name=f"{brand} {model}",
            serial_number=serial,
            category_id=cat.id,
            location=random.choice(LOCATIONS),
            status=random.choice(STATUSES),
            purchase_price=purchase_price,
            purchase_date=purchase_date,
            notes=f"Initial seeded data for {brand} {model}.",
        )
        db.session.add(asset)
        db.session.flush()
        log_change(asset.id, "CREATE", "all", None, asset.serial_number, actor="seeder")

        created += 1
        if created % 50 == 0:
            db.session.commit()
            print(f"  ...{created}/{to_create} created")

    db.session.commit()
    print(f"  Inserted {created} assets")


def main():
    app = create_app()
    with app.app_context():
        print("Creating tables (if not exist)...")
        db.create_all()
        print("  Tables ready.\n")

        seed_categories()
        seed_assets(target_count=200)

        # Summary
        total_cat = Category.query.count()
        total_assets = Asset.query.filter_by(is_deleted=False).count()
        total_audit = AuditLog.query.count()

        print("\n" + "=" * 50)
        print("Seed complete!")
        print(f"  Categories : {total_cat}")
        print(f"  Assets     : {total_assets}")
        print(f"  Audit Logs : {total_audit}")
        print("=" * 50)


if __name__ == "__main__":
    main()
