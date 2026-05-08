"""Flask application — Asset Inventory CRUD for M2VM testing."""
import os
import socket
import sys
import time
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, abort
)
from sqlalchemy import func, text

from config import Config
from models import db, Asset, Category, AuditLog, log_change

# Capture boot time once at import
BOOT_TIME = datetime.utcnow()
HOSTNAME = socket.gethostname()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app)
    register_routes(app)
    return app


def register_routes(app):
    # ============================================================
    # WEB UI ROUTES (HTML)
    # ============================================================

    @app.route("/")
    def index():
        page = request.args.get("page", 1, type=int)
        search = request.args.get("q", "").strip()
        status_filter = request.args.get("status", "")
        category_filter = request.args.get("category", "", type=str)

        query = Asset.query.filter_by(is_deleted=False)

        if search:
            query = query.filter(
                db.or_(
                    Asset.name.ilike(f"%{search}%"),
                    Asset.serial_number.ilike(f"%{search}%"),
                    Asset.location.ilike(f"%{search}%"),
                )
            )
        if status_filter:
            query = query.filter(Asset.status == status_filter)
        if category_filter and category_filter.isdigit():
            query = query.filter(Asset.category_id == int(category_filter))

        query = query.order_by(Asset.updated_at.desc())
        pagination = query.paginate(
            page=page, per_page=app.config["PAGE_SIZE"], error_out=False
        )

        categories = Category.query.order_by(Category.name).all()
        statuses = ["active", "in_repair", "retired", "lost"]

        return render_template(
            "index.html",
            assets=pagination.items,
            pagination=pagination,
            categories=categories,
            statuses=statuses,
            search=search,
            status_filter=status_filter,
            category_filter=category_filter,
        )

    @app.route("/assets/new", methods=["GET", "POST"])
    def asset_new():
        categories = Category.query.order_by(Category.name).all()

        if request.method == "POST":
            try:
                asset = Asset(
                    name=request.form["name"].strip(),
                    serial_number=request.form["serial_number"].strip(),
                    category_id=int(request.form["category_id"]),
                    location=request.form.get("location", "").strip() or None,
                    status=request.form.get("status", "active"),
                    purchase_price=parse_decimal(request.form.get("purchase_price")),
                    purchase_date=parse_date(request.form.get("purchase_date")),
                    notes=request.form.get("notes", "").strip() or None,
                )
                db.session.add(asset)
                db.session.flush()
                log_change(asset.id, "CREATE", "all", None, asset.serial_number)
                db.session.commit()
                flash(f"Asset '{asset.name}' created successfully.", "success")
                return redirect(url_for("asset_detail", asset_id=asset.id))
            except Exception as e:
                db.session.rollback()
                flash(f"Error creating asset: {e}", "error")

        return render_template("asset_form.html", asset=None, categories=categories)

    @app.route("/assets/<int:asset_id>")
    def asset_detail(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        if asset.is_deleted:
            abort(404)
        logs = (
            AuditLog.query.filter_by(asset_id=asset_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(50)
            .all()
        )
        return render_template("asset_detail.html", asset=asset, logs=logs)

    @app.route("/assets/<int:asset_id>/edit", methods=["GET", "POST"])
    def asset_edit(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        if asset.is_deleted:
            abort(404)
        categories = Category.query.order_by(Category.name).all()

        if request.method == "POST":
            try:
                # Track changes for audit log
                changes = []
                fields = [
                    ("name", request.form["name"].strip()),
                    ("serial_number", request.form["serial_number"].strip()),
                    ("category_id", int(request.form["category_id"])),
                    ("location", request.form.get("location", "").strip() or None),
                    ("status", request.form.get("status", "active")),
                    ("purchase_price", parse_decimal(request.form.get("purchase_price"))),
                    ("purchase_date", parse_date(request.form.get("purchase_date"))),
                    ("notes", request.form.get("notes", "").strip() or None),
                ]
                for field_name, new_val in fields:
                    old_val = getattr(asset, field_name)
                    if old_val != new_val:
                        changes.append((field_name, old_val, new_val))
                        setattr(asset, field_name, new_val)

                for field_name, old_val, new_val in changes:
                    log_change(asset.id, "UPDATE", field_name, old_val, new_val)

                db.session.commit()
                flash(f"Asset updated. {len(changes)} field(s) changed.", "success")
                return redirect(url_for("asset_detail", asset_id=asset.id))
            except Exception as e:
                db.session.rollback()
                flash(f"Error updating asset: {e}", "error")

        return render_template("asset_form.html", asset=asset, categories=categories)

    @app.route("/assets/<int:asset_id>/delete", methods=["POST"])
    def asset_delete(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        try:
            asset.is_deleted = True
            log_change(asset.id, "DELETE", "is_deleted", False, True)
            db.session.commit()
            flash(f"Asset '{asset.name}' deleted (soft delete).", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error deleting asset: {e}", "error")
        return redirect(url_for("index"))

    # ============================================================
    # REST API ROUTES (JSON)
    # ============================================================

    @app.route("/api/assets", methods=["GET"])
    def api_assets_list():
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = min(per_page, 200)

        query = Asset.query.filter_by(is_deleted=False).order_by(Asset.id)
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            "assets": [a.to_dict() for a in pagination.items],
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        })

    @app.route("/api/assets/<int:asset_id>", methods=["GET"])
    def api_asset_get(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        if asset.is_deleted:
            abort(404)
        return jsonify(asset.to_dict())

    @app.route("/api/assets", methods=["POST"])
    def api_asset_create():
        data = request.get_json() or {}
        try:
            asset = Asset(
                name=data["name"],
                serial_number=data["serial_number"],
                category_id=int(data["category_id"]),
                location=data.get("location"),
                status=data.get("status", "active"),
                purchase_price=parse_decimal(data.get("purchase_price")),
                purchase_date=parse_date(data.get("purchase_date")),
                notes=data.get("notes"),
            )
            db.session.add(asset)
            db.session.flush()
            log_change(asset.id, "CREATE", "all", None, asset.serial_number, actor="api")
            db.session.commit()
            return jsonify(asset.to_dict()), 201
        except KeyError as e:
            return jsonify({"error": f"Missing field: {e}"}), 400
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route("/api/assets/<int:asset_id>", methods=["PUT", "PATCH"])
    def api_asset_update(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        if asset.is_deleted:
            abort(404)
        data = request.get_json() or {}
        try:
            updatable = [
                "name", "serial_number", "category_id", "location",
                "status", "purchase_price", "purchase_date", "notes",
            ]
            changes = []
            for field in updatable:
                if field in data:
                    new_val = data[field]
                    if field == "purchase_price":
                        new_val = parse_decimal(new_val)
                    elif field == "purchase_date":
                        new_val = parse_date(new_val)
                    elif field == "category_id":
                        new_val = int(new_val) if new_val else None

                    old_val = getattr(asset, field)
                    if old_val != new_val:
                        changes.append((field, old_val, new_val))
                        setattr(asset, field, new_val)

            for field, old, new in changes:
                log_change(asset.id, "UPDATE", field, old, new, actor="api")

            db.session.commit()
            return jsonify(asset.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route("/api/assets/<int:asset_id>", methods=["DELETE"])
    def api_asset_delete(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        try:
            asset.is_deleted = True
            log_change(asset.id, "DELETE", "is_deleted", False, True, actor="api")
            db.session.commit()
            return "", 204
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    # ============================================================
    # OPERATIONAL ENDPOINTS
    # ============================================================

    @app.route("/health")
    def health():
        """Health check endpoint — returns 200 only if DB reachable."""
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "database": "connected",
            }), 200
        except Exception as e:
            return jsonify({
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "database": "disconnected",
                "error": str(e),
            }), 503

    @app.route("/info")
    def info():
        """System info — verify VM identity post-migration."""
        try:
            ip_addresses = []
            try:
                hostname_ip = socket.gethostbyname(HOSTNAME)
                ip_addresses.append(hostname_ip)
            except Exception:
                pass

            return jsonify({
                "hostname": HOSTNAME,
                "boot_time": BOOT_TIME.isoformat(),
                "uptime_seconds": (datetime.utcnow() - BOOT_TIME).total_seconds(),
                "current_time": datetime.utcnow().isoformat(),
                "ip_addresses": ip_addresses,
                "pid": os.getpid(),
                "python_version": sys.version,
                "platform": sys.platform,
                "app_name": app.config["APP_NAME"],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stats")
    def stats():
        """Database stats — verify data integrity post-migration."""
        try:
            total_assets = Asset.query.filter_by(is_deleted=False).count()
            total_deleted = Asset.query.filter_by(is_deleted=True).count()
            total_categories = Category.query.count()
            total_audit = AuditLog.query.count()

            last_modified = (
                db.session.query(func.max(Asset.updated_at)).scalar()
            )
            last_audit = (
                db.session.query(func.max(AuditLog.timestamp)).scalar()
            )

            # Database size
            try:
                db_size = db.session.execute(
                    text("SELECT pg_database_size(current_database())")
                ).scalar()
            except Exception:
                db_size = None

            # Per-status breakdown
            status_breakdown = (
                db.session.query(Asset.status, func.count(Asset.id))
                .filter_by(is_deleted=False)
                .group_by(Asset.status)
                .all()
            )

            return jsonify({
                "total_assets": total_assets,
                "total_deleted_assets": total_deleted,
                "total_categories": total_categories,
                "total_audit_logs": total_audit,
                "last_modified_at": last_modified.isoformat() if last_modified else None,
                "last_audit_at": last_audit.isoformat() if last_audit else None,
                "database_size_bytes": db_size,
                "status_breakdown": {s: c for s, c in status_breakdown},
                "snapshot_at": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/metrics")
    def metrics():
        """Prometheus-style metrics."""
        try:
            total_assets = Asset.query.filter_by(is_deleted=False).count()
            total_audit = AuditLog.query.count()
            uptime = (datetime.utcnow() - BOOT_TIME).total_seconds()

            output = [
                f"# HELP m2vm_assets_total Total non-deleted assets",
                f"# TYPE m2vm_assets_total gauge",
                f"m2vm_assets_total {total_assets}",
                f"# HELP m2vm_audit_logs_total Total audit log entries",
                f"# TYPE m2vm_audit_logs_total counter",
                f"m2vm_audit_logs_total {total_audit}",
                f"# HELP m2vm_uptime_seconds App uptime in seconds",
                f"# TYPE m2vm_uptime_seconds gauge",
                f"m2vm_uptime_seconds {uptime}",
            ]
            return "\n".join(output) + "\n", 200, {"Content-Type": "text/plain"}
        except Exception as e:
            return f"# error: {e}\n", 500, {"Content-Type": "text/plain"}

    # ============================================================
    # ERROR HANDLERS
    # ============================================================

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("base.html", error="Page not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal server error"}), 500
        return render_template("base.html", error="Internal server error"), 500

    # ============================================================
    # CONTEXT PROCESSORS
    # ============================================================

    @app.context_processor
    def inject_globals():
        return {
            "app_name": app.config["APP_NAME"],
            "hostname": HOSTNAME,
            "current_year": datetime.utcnow().year,
        }


# ============================================================
# HELPERS
# ============================================================

def parse_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_date(value):
    if not value:
        return None
    try:
        if isinstance(value, date):
            return value
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ============================================================
# ENTRY POINT
# ============================================================

app = create_app()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(
        host=app.config["FLASK_HOST"],
        port=app.config["FLASK_PORT"],
        debug=False,
    )
