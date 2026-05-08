"""
Activity Simulator — generates continuous writes to the database.

Useful for testing M2VM continuous replication:
- Run on source VM during replication window
- Continuously inserts, updates, deletes assets
- Each operation logged in audit_logs
- Helps verify that delta replication captures changes correctly

Usage:
    python activity_simulator.py [--interval 2] [--duration 3600] [--actor sim-1]
"""
import argparse
import random
import signal
import sys
import time
from datetime import datetime, date
from decimal import Decimal

from app import create_app
from models import db, Asset, Category, AuditLog, log_change


def random_action(actor="simulator"):
    """Pick a random action and execute it. Returns operation summary."""
    # Weighted: more INSERT/UPDATE than DELETE
    action = random.choices(
        ["insert", "update", "delete"],
        weights=[40, 55, 5],
        k=1,
    )[0]

    try:
        if action == "insert":
            return do_insert(actor)
        elif action == "update":
            return do_update(actor)
        elif action == "delete":
            return do_delete(actor)
    except Exception as e:
        db.session.rollback()
        return f"ERROR: {e}"


def do_insert(actor):
    categories = Category.query.all()
    if not categories:
        return "no categories — skip"

    cat = random.choice(categories)
    suffix = random.randint(100000, 999999)
    serial = f"SIM-{datetime.utcnow().strftime('%Y%m%d')}-{suffix}"

    asset = Asset(
        name=f"Simulated Asset #{suffix}",
        serial_number=serial,
        category_id=cat.id,
        location=f"Test Location {random.randint(1, 100)}",
        status=random.choice(["active", "in_repair"]),
        purchase_price=Decimal(random.randint(1000, 100000) * 1000),
        purchase_date=date(2024, random.randint(1, 12), random.randint(1, 28)),
        notes=f"Created by activity simulator at {datetime.utcnow().isoformat()}",
    )
    db.session.add(asset)
    db.session.flush()
    log_change(asset.id, "CREATE", "all", None, serial, actor=actor)
    db.session.commit()
    return f"INSERT id={asset.id} sn={serial}"


def do_update(actor):
    # Pick a random non-deleted asset
    count = Asset.query.filter_by(is_deleted=False).count()
    if count == 0:
        return "no assets — skip"

    offset = random.randint(0, count - 1)
    asset = (
        Asset.query.filter_by(is_deleted=False)
        .offset(offset)
        .limit(1)
        .first()
    )
    if not asset:
        return "no asset selected — skip"

    # Random field to change
    field = random.choice(["location", "status", "notes", "purchase_price"])

    if field == "location":
        old_val = asset.location
        new_val = f"Updated Location {random.randint(1, 1000)}"
        asset.location = new_val
    elif field == "status":
        old_val = asset.status
        new_val = random.choice(["active", "in_repair", "retired"])
        if new_val == old_val:
            new_val = "active" if old_val != "active" else "in_repair"
        asset.status = new_val
    elif field == "notes":
        old_val = asset.notes
        new_val = f"Updated note at {datetime.utcnow().isoformat()}"
        asset.notes = new_val
    elif field == "purchase_price":
        old_val = asset.purchase_price
        new_val = Decimal(random.randint(1000, 100000) * 1000)
        asset.purchase_price = new_val

    log_change(asset.id, "UPDATE", field, old_val, new_val, actor=actor)
    db.session.commit()
    return f"UPDATE id={asset.id} field={field}"


def do_delete(actor):
    """Soft delete a random asset (only simulated ones, to be safe)."""
    asset = (
        Asset.query.filter(
            Asset.is_deleted == False,
            Asset.serial_number.like("SIM-%"),
        )
        .order_by(db.func.random())
        .first()
    )
    if not asset:
        return "no simulated asset to delete — skip"

    asset.is_deleted = True
    log_change(asset.id, "DELETE", "is_deleted", False, True, actor=actor)
    db.session.commit()
    return f"DELETE id={asset.id} sn={asset.serial_number}"


def main():
    parser = argparse.ArgumentParser(description="Activity simulator for M2VM testing")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Seconds between operations (default: 2.0)")
    parser.add_argument("--duration", type=int, default=0,
                        help="Total runtime in seconds (0 = infinite)")
    parser.add_argument("--actor", default="simulator",
                        help="Actor name in audit log")
    args = parser.parse_args()

    app = create_app()

    # Graceful shutdown
    stop = {"flag": False}
    def handle_signal(sig, frame):
        print("\n[!] Stop signal received, finishing current op...")
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Activity simulator starting...")
    print(f"  Interval : {args.interval}s")
    print(f"  Duration : {'infinite' if args.duration == 0 else f'{args.duration}s'}")
    print(f"  Actor    : {args.actor}")
    print("=" * 60)

    start = time.time()
    op_count = 0

    with app.app_context():
        while not stop["flag"]:
            now = time.time()
            elapsed = now - start
            if args.duration > 0 and elapsed >= args.duration:
                print(f"\n[!] Duration reached ({args.duration}s), stopping.")
                break

            try:
                result = random_action(actor=args.actor)
                op_count += 1
                ts = datetime.utcnow().strftime("%H:%M:%S")
                print(f"[{ts}] op#{op_count:5d}  {result}")
            except Exception as e:
                print(f"[ERROR] {e}")

            time.sleep(args.interval)

    elapsed = time.time() - start
    print("=" * 60)
    print(f"Simulator finished. {op_count} operations in {elapsed:.1f}s")
    print(f"Average rate: {op_count/elapsed:.2f} ops/sec")


if __name__ == "__main__":
    main()
