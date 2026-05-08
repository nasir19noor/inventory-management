"""Database models for the asset inventory app."""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    assets = db.relationship("Asset", backref="category", lazy="dynamic")

    def __repr__(self):
        return f"<Category {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "asset_count": self.assets.count(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    serial_number = db.Column(db.String(100), unique=True, nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    location = db.Column(db.String(200))
    status = db.Column(db.String(50), default="active", nullable=False, index=True)
    purchase_price = db.Column(db.Numeric(15, 2))
    purchase_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    audit_logs = db.relationship(
        "AuditLog", backref="asset", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Asset {self.serial_number} - {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "serial_number": self.serial_number,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "location": self.location,
            "status": self.status,
            "purchase_price": float(self.purchase_price) if self.purchase_price else None,
            "purchase_date": self.purchase_date.isoformat() if self.purchase_date else None,
            "notes": self.notes,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)  # CREATE, UPDATE, DELETE
    field_changed = db.Column(db.String(100))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    actor = db.Column(db.String(100), default="system")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self):
        return f"<AuditLog {self.action} on asset {self.asset_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "action": self.action,
            "field_changed": self.field_changed,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "actor": self.actor,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


def log_change(asset_id, action, field=None, old=None, new=None, actor="web-user"):
    """Helper to record an audit log entry."""
    log = AuditLog(
        asset_id=asset_id,
        action=action,
        field_changed=field,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        actor=actor,
    )
    db.session.add(log)
