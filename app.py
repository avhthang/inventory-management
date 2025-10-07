import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import aliased
from sqlalchemy import or_, func, event
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import pandas as pd
import io
import click
import json
import sqlite3
import tempfile
import zipfile
import schedule
import threading
import time
import pytz

# --- Cấu hình ứng dụng ---
instance_path = os.path.join(os.getcwd(), 'instance')
os.makedirs(instance_path, exist_ok=True)

# Backup configuration
backup_path = os.path.join(os.getcwd(), 'backups')
os.makedirs(backup_path, exist_ok=True)

# Timezone configuration (GMT+7)
VIETNAM_TZ = pytz.timezone('Asia/Ho_Chi_Minh')

# Backup configuration variables
backup_config_daily_enabled = True
backup_config_weekly_enabled = True
backup_config_daily_time = "02:00"
backup_config_weekly_time = "03:00"

app = Flask(__name__, instance_path=instance_path)
app.config['SECRET_KEY'] = 'your_super_secret_key_change_this_please'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.permanent_session_lifetime = timedelta(days=30)

db = SQLAlchemy(app)

# Permission catalogue
PERMISSIONS = [
    ('maintenance.view', 'Xem nhật ký bảo trì'),
    ('maintenance.add', 'Thêm nhật ký bảo trì'),
    ('maintenance.edit', 'Sửa nhật ký bảo trì'),
    ('maintenance.delete', 'Xóa nhật ký bảo trì'),
    ('maintenance.upload', 'Tải lên tệp đính kèm'),
    ('maintenance.download', 'Tải xuống tệp đính kèm'),
]

# Register SQLite function last_token for sorting by given name
def _register_sqlite_functions(dbapi_conn, connection_record):
    try:
        def last_token(s):
            try:
                s = (s or '').strip()
                return s.split()[-1].lower() if s else ''
            except Exception:
                return ''
        dbapi_conn.create_function('last_token', 1, last_token)
    except Exception:
        pass

try:
    event.listen(db.engine, 'connect', _register_sqlite_functions)
except Exception:
    pass

# Eagerly register UDF on current connection as an extra safeguard (e.g., Gunicorn workers)
try:
    with app.app_context():
        try:
            with db.engine.connect() as _conn:
                _register_sqlite_functions(_conn.connection, None)
        except Exception:
            pass
except Exception:
    pass

# --- Database initialization ---
def init_db():
    with app.app_context():
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                # Create department table if not exists
                conn.execute(text('''
                    CREATE TABLE IF NOT EXISTS department (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name VARCHAR(120) NOT NULL,
                        description TEXT,
                        parent_id INTEGER REFERENCES department(id),
                        order_index INTEGER DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        manager_id INTEGER REFERENCES user(id)
                    )
                '''))
                
                # Add department_id column to user table if not exists
                try:
                    conn.execute(text('''
                        ALTER TABLE user ADD COLUMN department_id INTEGER REFERENCES department(id);
                    '''))
                except Exception as e:
                    # Column might already exist, ignore the error
                    pass

                # RBAC tables
                try:
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS role (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            description TEXT
                        )
                    '''))
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS permission (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            code TEXT NOT NULL UNIQUE,
                            name TEXT NOT NULL
                        )
                    '''))
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS role_permission (
                            role_id INTEGER NOT NULL REFERENCES role(id) ON DELETE CASCADE,
                            permission_id INTEGER NOT NULL REFERENCES permission(id) ON DELETE CASCADE,
                            PRIMARY KEY (role_id, permission_id)
                        )
                    '''))
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS user_role (
                            user_id INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
                            role_id INTEGER NOT NULL REFERENCES role(id) ON DELETE CASCADE,
                            PRIMARY KEY (user_id, role_id)
                        )
                    '''))
                except Exception:
                    pass
                
                # Create device maintenance log table if not exists
                try:
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS device_maintenance_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            device_id INTEGER NOT NULL REFERENCES device(id),
                            log_date DATE NOT NULL,
                            condition TEXT,
                            issue TEXT,
                            status TEXT,
                            last_action TEXT,
                            notes TEXT,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    '''))
                except Exception:
                    pass

                # Create maintenance attachments table if not exists
                try:
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS device_maintenance_attachment (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            log_id INTEGER NOT NULL REFERENCES device_maintenance_log(id) ON DELETE CASCADE,
                            file_name TEXT NOT NULL,
                            file_path TEXT NOT NULL,
                            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    '''))
                except Exception:
                    pass

                conn.commit()
            
        except Exception as e:
            print(f"Database initialization error: {e}")

        # Seed permissions and a default Admin role
        try:
            # insert permissions
            for code, name in PERMISSIONS:
                if not Permission.query.filter_by(code=code).first():
                    db.session.add(Permission(code=code, name=name))
            db.session.commit()
            # ensure Admin role
            admin_role = Role.query.filter_by(name='Admin').first()
            if not admin_role:
                admin_role = Role(name='Admin', description='Quyền đầy đủ')
                db.session.add(admin_role)
                db.session.commit()
            # grant all permissions to Admin
            perms = Permission.query.all()
            for p in perms:
                exists = RolePermission.query.filter_by(role_id=admin_role.id, permission_id=p.id).first()
                if not exists:
                    db.session.add(RolePermission(role_id=admin_role.id, permission_id=p.id))
            db.session.commit()
            # assign Admin role to existing admin user if any
            admin_user = User.query.filter_by(role='admin').first()
            if admin_user:
                if not UserRole.query.filter_by(user_id=admin_user.id, role_id=admin_role.id).first():
                    db.session.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))
                    db.session.commit()
        except Exception as e:
            print(f"RBAC seed error: {e}")

# Initialize database on startup
init_db()

# --- Models (Không thay đổi) ---
class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    parent_id = db.Column(db.Integer, db.ForeignKey('department.id'))
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    parent = db.relationship('Department', remote_side=[id], backref=db.backref('children', order_by=order_index))
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    manager = db.relationship('User', foreign_keys=[manager_id], backref='managed_departments')
    users = db.relationship('User', back_populates='department_info', foreign_keys='User.department_id')

    def get_hierarchy_level(self, max_depth: int = 50):
        """Return depth in hierarchy with cycle protection.

        Limits traversal by tracking visited department ids and a max depth to
        avoid infinite loops if parent relationships contain a cycle.
        """
        level = 0
        current = self.parent
        visited_ids = set()
        while current is not None and level < max_depth:
            current_id = getattr(current, 'id', None)
            if current_id in visited_ids:
                break
            if current_id is not None:
                visited_ids.add(current_id)
            level += 1
            current = getattr(current, 'parent', None)
        return level

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(120))
    last_name_token = db.Column(db.String(120))
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(20), default='user')
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'))
    department_info = db.relationship('Department', foreign_keys=[department_id], back_populates='users')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    position = db.Column(db.String(100))
    date_of_birth = db.Column(db.Date)
    phone_number = db.Column(db.String(20))
    notes = db.Column(db.Text)
    status = db.Column(db.String(50), default='Đang làm')
    onboard_date = db.Column(db.Date)
    offboard_date = db.Column(db.Date)
    given_handovers = db.relationship('DeviceHandover', foreign_keys='DeviceHandover.giver_id', back_populates='giver', lazy='dynamic')
    received_handovers = db.relationship('DeviceHandover', foreign_keys='DeviceHandover.receiver_id', back_populates='receiver', lazy='dynamic')

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    device_type = db.Column(db.String(50), nullable=False)
    serial_number = db.Column(db.String(80))
    purchase_date = db.Column(db.Date, nullable=False)
    import_date = db.Column(db.Date, nullable=False)
    condition = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Sẵn sàng')
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    assign_date = db.Column(db.Date)
    configuration = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.Column(db.String(120))
    importer = db.Column(db.String(120))
    brand = db.Column(db.String(100))
    supplier = db.Column(db.String(150))
    warranty = db.Column(db.String(50))
    manager = db.relationship('User', foreign_keys=[manager_id])
    purchase_price = db.Column(db.Float)

class DeviceMaintenanceLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    device = db.relationship('Device', backref=db.backref('maintenance_logs', cascade='all, delete-orphan'))
    log_date = db.Column(db.Date, nullable=False, default=date.today)
    condition = db.Column(db.Text)  # Tình trạng
    issue = db.Column(db.Text)      # Vấn đề
    status = db.Column(db.String(100))  # Trạng thái xử lý
    last_action = db.Column(db.Text)    # Xử lý cuối
    notes = db.Column(db.Text)          # Ghi chú
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DeviceMaintenanceAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey('device_maintenance_log.id'), nullable=False)
    file_name = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.Text, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    log = db.relationship('DeviceMaintenanceLog', backref=db.backref('attachments', cascade='all, delete-orphan'))

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)

class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)

class RolePermission(db.Model):
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), primary_key=True)
    permission_id = db.Column(db.Integer, db.ForeignKey('permission.id'), primary_key=True)
    role = db.relationship('Role', backref=db.backref('role_permissions', cascade='all, delete-orphan'))
    permission = db.relationship('Permission')

class UserRole(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), primary_key=True)
    role = db.relationship('Role')

class DeviceHandover(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    handover_date = db.Column(db.Date, nullable=False, default=date.today)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    device = db.relationship('Device', backref='handovers')
    giver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    giver = db.relationship('User', foreign_keys=[giver_id], back_populates='given_handovers')
    receiver = db.relationship('User', foreign_keys=[receiver_id], back_populates='received_handovers')
    device_condition = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.String(255))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)

# --- Device Grouping Models ---
class DeviceGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DeviceGroupDevice(db.Model):
    group_id = db.Column(db.Integer, db.ForeignKey('device_group.id'), primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group = db.relationship('DeviceGroup', backref=db.backref('device_links', cascade='all, delete-orphan'))
    device = db.relationship('Device', backref=db.backref('group_links', cascade='all, delete-orphan'))

class UserDeviceGroup(db.Model):
    group_id = db.Column(db.Integer, db.ForeignKey('device_group.id'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    role = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group = db.relationship('DeviceGroup', backref=db.backref('user_links', cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('group_links', cascade='all, delete-orphan'))

# --- Server Room Extra Info ---
class ServerRoomDeviceInfo(db.Model):
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), primary_key=True)
    ip_address = db.Column(db.String(100))
    services_running = db.Column(db.Text)
    usage_status = db.Column(db.String(30), default='Đang hoạt động')
    department = db.Column(db.String(100))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    device = db.relationship('Device', backref=db.backref('server_room_info', uselist=False, cascade='all, delete-orphan'))

# --- Inventory Receipt Models ---
class InventoryReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False)
    supplier = db.Column(db.String(150))
    importer = db.Column(db.String(120))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    config_proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'))

class InventoryReceiptItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('inventory_receipt.id'), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    device_condition = db.Column(db.String(100))
    device_note = db.Column(db.Text)
    receipt = db.relationship('InventoryReceipt', backref=db.backref('items', cascade='all, delete-orphan'))
    device = db.relationship('Device')

# --- Configuration Proposal Models ---
class ConfigProposal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    proposal_date = db.Column(db.Date, nullable=False)
    proposer_name = db.Column(db.String(120))
    proposer_unit = db.Column(db.String(120))
    scope = db.Column(db.String(50))  # Dùng chung | Cá nhân
    currency = db.Column(db.String(10), default='VND')
    status = db.Column(db.String(30), default='Mới tạo')  # Mới tạo, Lưu nháp, Đang xin ý kiến, Đã xin ý kiến, Đang mua hàng, Hủy
    purchase_status = db.Column(db.String(30), default='Lấy báo giá')  # Lấy báo giá, Chờ thanh toán, Chờ giao hàng, Chờ xuất hóa đơn, Đã hoàn thành
    notes = db.Column(db.Text)
    supplier_info = db.Column(db.String(255))
    linked_receipt_id = db.Column(db.Integer, db.ForeignKey('inventory_receipt.id'))
    subtotal = db.Column(db.Float, default=0.0)
    vat_percent = db.Column(db.Float, default=10.0)
    vat_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    linked_receipt = db.relationship('InventoryReceipt', foreign_keys=[linked_receipt_id])

class ConfigProposalItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'), nullable=False)
    order_no = db.Column(db.Integer, default=0)
    product_name = db.Column(db.String(255))
    product_link = db.Column(db.String(255))  # Link tham khảo sản phẩm
    warranty = db.Column(db.String(120))
    product_code = db.Column(db.String(100))
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, default=0.0)
    proposal = db.relationship('ConfigProposal', backref=db.backref('items', cascade='all, delete-orphan'))

# --- Audit Log ---
class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    changes = db.Column(db.Text)  # JSON string: { field: {"from": ..., "to": ...}, ... }

def _serialize_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        try:
            return value.strftime('%Y-%m-%d')
        except Exception:
            return str(value)
    return value

def _diff_changes(old_dict, new_dict):
    diff = {}
    for key in new_dict.keys():
        old_v = _serialize_value(old_dict.get(key))
        new_v = _serialize_value(new_dict.get(key))
        if old_v != new_v:
            diff[key] = { 'from': old_v, 'to': new_v }
    return diff

def _log_audit(entity_type, entity_id, old_dict, new_dict):
    try:
        changes = _diff_changes(old_dict, new_dict)
        if not changes:
            return
        changed_by = session.get('user_id')
        db.session.add(AuditLog(entity_type=entity_type, entity_id=entity_id, changed_by=changed_by, changes=json.dumps(changes, ensure_ascii=False)))
    except Exception:
        # Do not break main flow if logging fails
        pass

# --- Ensure tables exist in case CLI init wasn't run (Flask 3 compatible) ---
_tables_initialized = False

@app.before_request
def ensure_tables_once():
    global _tables_initialized
    if not _tables_initialized:
        try:
            db.create_all()
            # Ensure new columns exist for InventoryReceiptItem if the table was created earlier
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    # InventoryReceiptItem columns
                    info = conn.execute(text("PRAGMA table_info('inventory_receipt_item')")).fetchall()
                    cols = {row[1] for row in info}
                    alter_stmts = []
                    if 'quantity' not in cols:
                        alter_stmts.append("ALTER TABLE inventory_receipt_item ADD COLUMN quantity INTEGER DEFAULT 1")
                    if 'device_condition' not in cols:
                        alter_stmts.append("ALTER TABLE inventory_receipt_item ADD COLUMN device_condition VARCHAR(100)")
                    if 'device_note' not in cols:
                        alter_stmts.append("ALTER TABLE inventory_receipt_item ADD COLUMN device_note TEXT")
                    # ConfigProposal new columns
                    info2 = conn.execute(text("PRAGMA table_info('config_proposal')")).fetchall()
                    cols2 = {row[1] for row in info2}
                    if info2:  # table exists
                        if 'currency' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN currency VARCHAR(10) DEFAULT 'VND'")
                        if 'status' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN status VARCHAR(30) DEFAULT 'Mới tạo'")
                        if 'purchase_status' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN purchase_status VARCHAR(30) DEFAULT 'Lấy báo giá'")
                        if 'notes' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN notes TEXT")
                        if 'linked_receipt_id' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN linked_receipt_id INTEGER")
                        if 'supplier_info' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN supplier_info VARCHAR(255)")
                    # InventoryReceipt new link column
                    info3 = conn.execute(text("PRAGMA table_info('inventory_receipt')")).fetchall()
                    cols3 = {row[1] for row in info3}
                    if info3:
                        if 'config_proposal_id' not in cols3:
                            alter_stmts.append("ALTER TABLE inventory_receipt ADD COLUMN config_proposal_id INTEGER")
                    # Users last_name_token for sorting by given name
                    info4 = conn.execute(text("PRAGMA table_info('user')")).fetchall()
                    cols4 = {row[1] for row in info4}
                    if info4 and 'last_name_token' not in cols4:
                        alter_stmts.append("ALTER TABLE user ADD COLUMN last_name_token VARCHAR(120)")
                    # ConfigProposalItem new product_code
                    info5 = conn.execute(text("PRAGMA table_info('config_proposal_item')")).fetchall()
                    cols5 = {row[1] for row in info5}
                    if info5 and 'product_code' not in cols5:
                        alter_stmts.append("ALTER TABLE config_proposal_item ADD COLUMN product_code VARCHAR(100)")
                    if info5 and 'supplier_info' in cols5:
                        pass
                    # AuditLog table creation (if not exists)
                    conn.execute(text("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type VARCHAR(50) NOT NULL, entity_id INTEGER NOT NULL, changed_by INTEGER, changed_at DATETIME DEFAULT CURRENT_TIMESTAMP, changes TEXT)"))
                    # ServerRoomDeviceInfo table ensure & migrate
                    conn.execute(text("CREATE TABLE IF NOT EXISTS server_room_device_info (device_id INTEGER PRIMARY KEY, ip_address VARCHAR(100), services_running TEXT, usage_status VARCHAR(30) DEFAULT 'Đang hoạt động', updated_at DATETIME, FOREIGN KEY(device_id) REFERENCES device(id))"))
                    info6 = conn.execute(text("PRAGMA table_info('server_room_device_info')")).fetchall()
                    cols6 = {row[1] for row in info6}
                    if info6 and 'usage_status' not in cols6:
                        conn.execute(text("ALTER TABLE server_room_device_info ADD COLUMN usage_status VARCHAR(30) DEFAULT 'Đang hoạt động'"))
                    for stmt in alter_stmts:
                        conn.execute(text(stmt))
                    if alter_stmts:
                        conn.commit()
            except Exception:
                pass
        except Exception:
            pass
        _tables_initialized = True

# --- (Các hàm context_processor, home, auth, device routes giữ nguyên) ---
@app.context_processor
def inject_user():
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
        # derive permission codes for template checks
        role_ids = [ur.role_id for ur in UserRole.query.filter_by(user_id=current_user.id).all()]
        perm_codes = set()
        if role_ids:
            for rp in RolePermission.query.filter(RolePermission.role_id.in_(role_ids)).all():
                perm = Permission.query.get(rp.permission_id)
                if perm:
                    perm_codes.add(perm.code)
        return dict(current_user=current_user, current_permissions=perm_codes)
    return dict(current_user=None)

@app.template_filter('vnd')
def format_vnd(value):
    try:
        n = float(value or 0)
    except Exception:
        n = 0
    return f"{int(round(n, 0)):,}".replace(',', '.')

@app.route('/')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # Get filter parameters
    filter_department = request.args.get('department', '')
    filter_device_type = request.args.get('device_type', '')
    
    # Base queries
    device_query = Device.query
    user_query = User.query
    
    # Apply filters
    if filter_department:
        dept_query = Department.query.filter(Department.name == filter_department).first()
        if dept_query:
            device_query = device_query.join(User, Device.manager_id == User.id).filter(User.department_id == dept_query.id)
    
    if filter_device_type:
        device_query = device_query.filter(Device.device_type == filter_device_type)
    
    # Get statistics
    total_devices = device_query.count()
    in_use_devices = device_query.filter_by(status='Đã cấp phát').count()
    maintenance_devices = device_query.filter_by(status='Bảo trì').count()
    
    # Get device type statistics (convert to plain list for JSON serialization)
    _device_type_rows = db.session.query(
        Device.device_type, 
        db.func.count(Device.id).label('count')
    ).group_by(Device.device_type).all()
    device_type_stats = [(row[0], int(row[1] or 0)) for row in _device_type_rows]
    
    # Get department statistics (convert to plain list for JSON serialization)
    _department_rows = db.session.query(
        Department.name,
        db.func.count(Device.id).label('count')
    ).join(User, Department.id == User.department_id)\
     .join(Device, User.id == Device.manager_id)\
     .group_by(Department.name).all()
    department_stats = [(row[0], int(row[1] or 0)) for row in _department_rows]
    
    # Get all departments and device types for filters
    departments = [d[0] for d in db.session.query(Department.name).all()]
    device_types = [dt[0] for dt in db.session.query(Device.device_type).distinct().all()]
    
    # Get saved chart preferences
    selected_device_types = session.get('dashboard_device_types', device_types)
    selected_departments = session.get('dashboard_departments', departments)

    return render_template('dashboard.html', 
                         total_devices=total_devices, 
                         in_use_devices=in_use_devices, 
                         maintenance_devices=maintenance_devices,
                         device_type_stats=device_type_stats,
                         department_stats=department_stats,
                         departments=departments,
                         device_types=device_types,
                         filter_department=filter_department,
                         filter_device_type=filter_device_type,
                         selected_device_types=selected_device_types,
                         selected_departments=selected_departments)

# ... (Auth routes) ...
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('home'))
        flash('Tên đăng nhập hoặc mật khẩu không đúng', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))

@app.route('/save_dashboard_device_types', methods=['POST'])
def save_dashboard_device_types():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    selected_types = request.form.getlist('selected_device_types')
    session['dashboard_device_types'] = selected_types
    flash('Đã lưu cài đặt thống kê theo loại thiết bị.', 'success')
    return redirect(url_for('home'))

@app.route('/save_dashboard_departments', methods=['POST'])
def save_dashboard_departments():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    selected_departments = request.form.getlist('selected_departments')
    session['dashboard_departments'] = selected_departments
    flash('Đã lưu cài đặt thống kê theo phòng ban.', 'success')
    return redirect(url_for('home'))

# --- Department Management Routes ---
@app.route('/departments')
def list_departments():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    departments = Department.query.all()
    all_departments = Department.query.order_by(Department.order_index).all()
    users = User.query.filter_by(status='Đang làm').all()
    
    return render_template('departments/list.html', 
                         departments=departments,
                         all_departments=all_departments,
                         users=users)

@app.route('/departments/<int:id>/users')
def department_users(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    department = Department.query.get_or_404(id)
    available_users = User.query.filter(
        User.status == 'Đang làm',
        User.department_id.is_(None)
    ).all()
    
    return render_template('departments/users.html',
                         department=department,
                         available_users=available_users)

@app.route('/departments/<int:id>/users/add', methods=['POST'])
def add_department_user(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    department = Department.query.get_or_404(id)
    user_id = request.form.get('user_id')
    
    if not user_id:
        flash('Vui lòng chọn người dùng', 'danger')
        return redirect(url_for('department_users', id=id))
        
    user = User.query.get_or_404(user_id)
    user.department_id = department.id
    db.session.commit()
    
    flash(f'Đã thêm {user.username} vào phòng {department.name}', 'success')
    return redirect(url_for('department_users', id=id))

@app.route('/departments/<int:dept_id>/users/<int:user_id>/remove', methods=['POST'])
def remove_department_user(dept_id, user_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
        
    user = User.query.get_or_404(user_id)
    department = Department.query.get_or_404(dept_id)
    
    if user.department_id != department.id:
        return jsonify({'success': False, 'message': 'User not in this department'})
        
    user.department_id = None
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/departments/add', methods=['POST'])
def add_department():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    name = request.form.get('name')
    description = request.form.get('description')
    parent_id = request.form.get('parent_id')
    manager_id = request.form.get('manager_id')
    
    if not name:
        flash('Tên phòng ban không được để trống', 'danger')
        return redirect(url_for('list_departments'))
    
    # Get max order_index in the same parent level
    max_order = db.session.query(func.max(Department.order_index)).filter_by(
        parent_id=parent_id if parent_id else None
    ).scalar() or 0
    
    new_dept = Department(
        name=name,
        description=description,
        parent_id=parent_id if parent_id else None,
        manager_id=manager_id if manager_id else None,
        order_index=max_order + 1
    )
    
    try:
        db.session.add(new_dept)
        db.session.commit()
        flash('Thêm phòng ban thành công', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Có lỗi xảy ra khi thêm phòng ban', 'danger')
        print(e)
    
    return redirect(url_for('list_departments'))

@app.route('/departments/<int:id>/edit', methods=['POST'])
def edit_department(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    dept = Department.query.get_or_404(id)
    name = request.form.get('name')
    description = request.form.get('description')
    parent_id = request.form.get('parent_id')
    manager_id = request.form.get('manager_id')
    
    if not name:
        flash('Tên phòng ban không được để trống', 'danger')
        return redirect(url_for('list_departments'))
    
    try:
        dept.name = name
        dept.description = description
        dept.parent_id = parent_id if parent_id else None
        dept.manager_id = manager_id if manager_id else None
        db.session.commit()
        flash('Cập nhật phòng ban thành công', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Có lỗi xảy ra khi cập nhật phòng ban', 'danger')
        print(e)
    
    return redirect(url_for('list_departments'))

@app.route('/departments/<int:id>/delete', methods=['POST'])
def delete_department(id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    dept = Department.query.get_or_404(id)
    
    # Check if department has children
    if dept.children:
        return jsonify({
            'success': False, 
            'message': 'Không thể xóa phòng ban có phòng ban con'
        })
    
    # Check if department has users
    if dept.users:
        return jsonify({
            'success': False, 
            'message': 'Không thể xóa phòng ban có người dùng'
        })
    
    try:
        db.session.delete(dept)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(e)
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi xóa phòng ban'
        })

@app.route('/departments/reorder', methods=['POST'])
def reorder_departments():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    data = request.get_json()
    dept_id = data.get('dept_id')
    new_parent_id = data.get('parent_id')
    new_index = data.get('new_index')
    
    dept = Department.query.get_or_404(dept_id)
    old_parent_id = dept.parent_id
    
    try:
        # Update parent if changed
        if str(old_parent_id) != str(new_parent_id):
            dept.parent_id = new_parent_id if new_parent_id else None
        
        # Update order_index of other departments
        other_depts = Department.query.filter_by(
            parent_id=new_parent_id if new_parent_id else None
        ).order_by(Department.order_index).all()
        
        # Remove current department from list if it exists
        other_depts = [d for d in other_depts if d.id != dept.id]
        
        # Insert department at new position
        other_depts.insert(new_index, dept)
        
        # Update order_index for all departments
        for i, d in enumerate(other_depts):
            d.order_index = i
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(e)
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi sắp xếp phòng ban'
        })

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        if not username or not password or not confirm_password:
            flash('Tên đăng nhập và mật khẩu là bắt buộc.', 'danger'); return render_template('register.html')
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp.', 'danger'); return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger'); return render_template('register.html')
        if email and User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng.', 'danger'); return render_template('register.html')
        new_user = User(username=username, password=generate_password_hash(password), full_name=full_name, email=email, role='user')
        db.session.add(new_user); db.session.commit()
        session['user_id'] = new_user.id; session.permanent = True
        flash('Đăng ký tài khoản thành công! Bạn đã được đăng nhập.', 'success')
        return redirect(url_for('home'))
    return render_template('register.html')
    
@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if not check_password_hash(user.password, current_password):
            flash('Mật khẩu hiện tại không đúng.', 'danger')
        elif new_password != confirm_password:
            flash('Mật khẩu mới không khớp.', 'danger')
        else:
            user.password = generate_password_hash(new_password)
            db.session.commit()
            flash('Đổi mật khẩu thành công.', 'success')
            return redirect(url_for('home'))
    return render_template('change_password.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if 'user_id' in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')

        user = User.query.filter_by(username=username, email=email).first()

        if user:
            default_password = 'Password123'
            user.password = generate_password_hash(default_password)
            db.session.commit()
            
            flash(f'Mật khẩu cho tài khoản "{username}" đã được reset thành công về giá trị mặc định: {default_password}', 'success')
            return redirect(url_for('login'))
        else:
            flash('Tên đăng nhập hoặc Email không chính xác. Vui lòng thử lại.', 'danger')

    return render_template('forgot_password.html')

# ... (Device routes) ...
@app.route('/devices')
def device_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    # Load current filters from query params or session-saved defaults
    saved_filters = session.get('devices_filters', {}) or {}
    filter_device_code = request.args.get('filter_device_code')
    filter_name = request.args.get('filter_name')
    filter_device_type = request.args.get('filter_device_type')
    filter_status = request.args.get('filter_status')
    filter_manager_id = request.args.get('filter_manager_id')
    filter_department = request.args.get('filter_department')

    if filter_device_code is None or filter_device_code == '':
        filter_device_code = saved_filters.get('filter_device_code', '')
    if filter_name is None or filter_name == '':
        filter_name = saved_filters.get('filter_name', '')
    if filter_device_type is None or filter_device_type == '':
        filter_device_type = saved_filters.get('filter_device_type', '')
    if filter_status is None or filter_status == '':
        # prefer saved filters; fallback to legacy default status
        filter_status = saved_filters.get('filter_status', session.get('default_device_status', ''))
    if filter_manager_id is None or filter_manager_id == '':
        filter_manager_id = saved_filters.get('filter_manager_id', '')
    if filter_department is None or filter_department == '':
        filter_department = saved_filters.get('filter_department', '')
    
    query = Device.query
    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_name:
        query = query.filter(Device.name.ilike(f'%{filter_name}%'))
    if filter_device_type:
        query = query.filter_by(device_type=filter_device_type)
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_manager_id:
        query = query.filter(Device.manager_id == filter_manager_id)
    if filter_department:
        dept = Department.query.filter_by(name=filter_department).first()
        if dept:
            query = query.join(User, Device.manager_id == User.id).filter(User.department_id == dept.id)
    
    devices_pagination = query.order_by(Device.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng', 'Thanh lý', 'Test', 'Mượn']
    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    departments = [d.name for d in Department.query.order_by(Department.name).all()]

    return render_template(
        'devices.html',
        devices=devices_pagination,
        device_types=device_types,
        statuses=statuses,
        users=users,
        departments=departments,
        filter_device_code=filter_device_code,
        filter_name=filter_name,
        filter_device_type=filter_device_type,
        filter_status=filter_status,
        filter_manager_id=filter_manager_id,
        filter_department=filter_department
    )

@app.route('/devices/default_status', methods=['POST'])
def set_devices_default_status():
    if 'user_id' not in session: return redirect(url_for('login'))
    status = request.form.get('filter_status')
    if status is None:
        status = request.form.get('status')
    session['default_device_status'] = status if status is not None else session.get('default_device_status', '')
    flash('Đã lưu trạng thái thiết bị mặc định.', 'success')
    # Preserve current filters when redirecting
    current_filters = {}
    for key in ['filter_device_code', 'filter_name', 'filter_device_type', 'filter_manager_id']:
        current_filters[key] = request.form.get(key, '')
    current_filters['filter_status'] = status
    return redirect(url_for('device_list', **{k: v for k, v in current_filters.items() if v}))

@app.route('/devices/save_filters', methods=['POST'])
def save_device_filters():
    if 'user_id' not in session: return redirect(url_for('login'))
    filters = {
        'filter_device_code': request.form.get('filter_device_code', '').strip(),
        'filter_name': request.form.get('filter_name', '').strip(),
        'filter_device_type': request.form.get('filter_device_type', '').strip(),
        'filter_status': request.form.get('filter_status', '').strip(),
        'filter_manager_id': request.form.get('filter_manager_id', '').strip(),
        'filter_department': request.form.get('filter_department', '').strip(),
    }
    session['devices_filters'] = filters
    flash('Đã lưu bộ lọc thiết bị.', 'success')
    # Redirect back with filters as query so UI reflects saved state
    return redirect(url_for('device_list', **{k: v for k, v in filters.items() if v}))

@app.route('/devices/bulk_update', methods=['POST'])
def devices_bulk_update():
    if 'user_id' not in session: return redirect(url_for('login'))
    device_ids = request.form.getlist('device_ids')
    if not device_ids:
        flash('Vui lòng chọn ít nhất một thiết bị.', 'warning')
        return redirect(url_for('device_list'))
    new_status = request.form.get('new_status')
    new_manager_id = request.form.get('new_manager_id')
    updated = 0
    for did in device_ids:
        device = Device.query.get(did)
        if not device: continue
        if new_status:
            device.status = new_status
        if new_manager_id:
            try:
                device.manager_id = int(new_manager_id)
            except ValueError:
                pass
        updated += 1
    db.session.commit()
    flash(f'Đã cập nhật {updated} thiết bị.', 'success')
    return redirect(url_for('device_list'))
    
@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        device_code = request.form.get('device_code')
        if not device_code:
            last_device = Device.query.order_by(Device.id.desc()).first()
            last_id = last_device.id if last_device else 0
            device_code = f"TB{last_id + 1:05d}"
            
        if Device.query.filter_by(device_code=device_code).first():
            flash(f'Mã thiết bị {device_code} đã tồn tại.', 'danger')
            return redirect(url_for('add_device'))
            
        new_device = Device(
            device_code=device_code,
            name=request.form['name'],
            device_type=request.form['device_type'],
            serial_number=request.form.get('serial_number'),
            brand=request.form.get('brand'),
            supplier=request.form.get('supplier'),
            warranty=request.form.get('warranty'),
            configuration=request.form.get('configuration'),
            purchase_date=datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date(),
            purchase_price=request.form.get('purchase_price', type=float, default=None),
            buyer=request.form.get('buyer'),
            importer=request.form.get('importer'),
            import_date=datetime.strptime(request.form['import_date'], '%Y-%m-%d').date(),
            condition=request.form['condition'],
            manager_id=request.form.get('manager_id') if request.form.get('manager_id') else None,
            assign_date=datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None,
            notes=request.form.get('notes')
        )
        db.session.add(new_device)
        db.session.commit()
        flash('Thêm thiết bị mới thành công!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    return render_template('add_device.html', managers=managers)
    
@app.route('/edit_device/<int:device_id>', methods=['GET', 'POST'])
def edit_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    if request.method == 'POST':
        # snapshot before
        old = {
            'device_code': device.device_code,
            'name': device.name,
            'device_type': device.device_type,
            'serial_number': device.serial_number,
            'brand': device.brand,
            'supplier': device.supplier,
            'warranty': device.warranty,
            'configuration': device.configuration,
            'purchase_date': device.purchase_date,
            'purchase_price': device.purchase_price,
            'buyer': device.buyer,
            'importer': device.importer,
            'import_date': device.import_date,
            'condition': device.condition,
            'status': device.status,
            'manager_id': device.manager_id,
            'assign_date': device.assign_date,
            'notes': device.notes,
        }
        # Cho phép sửa mã thiết bị với kiểm tra trùng lặp
        new_device_code = request.form.get('device_code', '').strip()
        if not new_device_code:
            flash('Mã thiết bị không được để trống.', 'danger')
            return redirect(url_for('edit_device', device_id=device_id))
        if new_device_code != device.device_code:
            if Device.query.filter_by(device_code=new_device_code).first():
                flash(f'Mã thiết bị {new_device_code} đã tồn tại.', 'danger')
                return redirect(url_for('edit_device', device_id=device_id))
            device.device_code = new_device_code
        device.name = request.form['name']
        device.device_type = request.form['device_type']
        device.serial_number = request.form.get('serial_number')
        device.brand = request.form.get('brand')
        device.supplier = request.form.get('supplier')
        device.warranty = request.form.get('warranty')
        device.configuration = request.form.get('configuration')
        device.purchase_date = datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date()
        device.purchase_price = request.form.get('purchase_price', type=float, default=None)
        device.buyer = request.form.get('buyer')
        device.importer = request.form.get('importer')
        device.import_date = datetime.strptime(request.form['import_date'], '%Y-%m-%d').date()
        device.condition = request.form['condition']
        device.status = request.form['status']
        manager_id_str = request.form.get('manager_id')
        device.manager_id = int(manager_id_str) if manager_id_str else None
        device.assign_date = datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None
        device.notes = request.form.get('notes')
        
        db.session.commit()
        # snapshot after
        new = {
            'device_code': device.device_code,
            'name': device.name,
            'device_type': device.device_type,
            'serial_number': device.serial_number,
            'brand': device.brand,
            'supplier': device.supplier,
            'warranty': device.warranty,
            'configuration': device.configuration,
            'purchase_date': device.purchase_date,
            'purchase_price': device.purchase_price,
            'buyer': device.buyer,
            'importer': device.importer,
            'import_date': device.import_date,
            'condition': device.condition,
            'status': device.status,
            'manager_id': device.manager_id,
            'assign_date': device.assign_date,
            'notes': device.notes,
        }
        _log_audit('device', device.id, old, new)
        flash('Cập nhật thông tin thiết bị thành công!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng']
    return render_template('edit_device.html', device=device, managers=managers, statuses=statuses)

@app.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    if device.handovers:
        flash('Không thể xóa thiết bị đã có lịch sử bàn giao.', 'danger')
        return redirect(url_for('device_list'))
    # Gỡ liên kết nhóm thiết bị (nếu có)
    try:
        for link in DeviceGroupDevice.query.filter_by(device_id=device.id).all():
            db.session.delete(link)
    except Exception:
        pass
    # Xóa các item trong phiếu nhập kho tham chiếu tới thiết bị (nếu có)
    try:
        for it in InventoryReceiptItem.query.filter_by(device_id=device.id).all():
            db.session.delete(it)
    except Exception:
        pass
    db.session.delete(device)
    db.session.commit()
    flash('Xóa thiết bị thành công!', 'success')
    return redirect(url_for('device_list'))

@app.route('/devices/bulk_delete', methods=['POST'])
def bulk_delete_devices():
    if 'user_id' not in session: return redirect(url_for('login'))
    device_ids = request.form.getlist('device_ids')
    if not device_ids:
        flash('Vui lòng chọn ít nhất một thiết bị để xóa.', 'warning')
        return redirect(url_for('device_list'))

    deleted_count = 0
    skipped_count = 0

    for device_id in device_ids:
        device = Device.query.get(device_id)
        if not device: continue

        # Kiểm tra điều kiện xóa: thiết bị không được có lịch sử bàn giao và không được có người quản lý
        if device.handovers or device.manager_id is not None:
            skipped_count += 1
            continue

        # Gỡ liên kết nhóm thiết bị (nếu có)
        try:
            for link in DeviceGroupDevice.query.filter_by(device_id=device.id).all():
                db.session.delete(link)
        except Exception:
            pass
        # Xóa các item trong phiếu nhập kho tham chiếu tới thiết bị (nếu có)
        try:
            for it in InventoryReceiptItem.query.filter_by(device_id=device.id).all():
                db.session.delete(it)
        except Exception:
            pass

        db.session.delete(device)
        deleted_count += 1

    db.session.commit()

    if deleted_count > 0:
        message = f'Đã xóa thành công {deleted_count} thiết bị.'
        if skipped_count > 0:
            message += f' {skipped_count} thiết bị không thể xóa do đã được gán cho người dùng hoặc có lịch sử bàn giao.'
        flash(message, 'success')
    else:
        flash('Không có thiết bị nào được xóa. Tất cả thiết bị đã được gán hoặc có lịch sử bàn giao.', 'warning')

    return redirect(url_for('device_list'))

@app.route('/device/<int:device_id>')
def device_detail(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    handovers = DeviceHandover.query.filter_by(device_id=device_id).order_by(DeviceHandover.handover_date.desc()).all()
    return render_template('device_detail.html', device=device, handovers=handovers)

# --- Device Groups Routes ---
@app.route('/device_groups', methods=['GET', 'POST'])
def device_groups():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        notes = request.form.get('notes')
        device_ids = request.form.getlist('device_ids')
        
        if not name:
            flash('Tên nhóm là bắt buộc.', 'danger')
            return redirect(url_for('device_groups'))
        
        # Tạo nhóm mới
        group = DeviceGroup(
            name=name, 
            description=description, 
            notes=notes,
            created_by=session.get('user_id')
        )
        db.session.add(group)
        db.session.flush()  # Để lấy ID
        
        # Thêm thiết bị vào nhóm (đảm bảo 1 thiết bị chỉ ở 1 nhóm)
        for device_id in device_ids:
            if device_id:
                # Xóa thiết bị khỏi nhóm cũ nếu có
                old_link = DeviceGroupDevice.query.filter_by(device_id=device_id).first()
                if old_link:
                    db.session.delete(old_link)
                
                # Thêm vào nhóm mới
                new_link = DeviceGroupDevice(group_id=group.id, device_id=device_id)
                db.session.add(new_link)
        
        db.session.commit()
        flash('Tạo nhóm thiết bị thành công!', 'success')
        return redirect(url_for('device_groups'))

    # Filters
    filter_name = request.args.get('name', '').strip()
    filter_user_id = request.args.get('user_id', '').strip()
    filter_device_code = request.args.get('device_code', '').strip()
    filter_device_name = request.args.get('device_name', '').strip()
    filter_device_type = request.args.get('device_type', '').strip()
    filter_device_status = request.args.get('device_status', '').strip()
    filter_manager_id = request.args.get('manager_id', '').strip()
    filter_ip = request.args.get('ip', '').strip()
    filter_start_date = request.args.get('start_date', '').strip()
    filter_end_date = request.args.get('end_date', '').strip()
    filter_created_by = request.args.get('created_by', '').strip()

    q = DeviceGroup.query
    if filter_name:
        q = q.filter(DeviceGroup.name.ilike(f"%{filter_name}%"))
    if filter_created_by:
        try:
            q = q.filter(DeviceGroup.created_by == int(filter_created_by))
        except ValueError:
            pass
    if filter_start_date:
        try:
            dt = datetime.strptime(filter_start_date, '%Y-%m-%d')
            q = q.filter(DeviceGroup.created_at >= dt)
        except ValueError:
            pass
    if filter_end_date:
        try:
            dt2 = datetime.strptime(filter_end_date, '%Y-%m-%d') + timedelta(days=1)
            q = q.filter(DeviceGroup.created_at < dt2)
        except ValueError:
            pass
    if filter_user_id:
        try:
            uid = int(filter_user_id)
            q = q.join(UserDeviceGroup, UserDeviceGroup.group_id == DeviceGroup.id).filter(UserDeviceGroup.user_id == uid)
        except ValueError:
            pass
    if filter_device_code:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.device_code.ilike(f"%{filter_device_code}%"))
    if filter_device_name:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.name.ilike(f"%{filter_device_name}%"))
    if filter_device_type:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.device_type == filter_device_type)
    if filter_device_status:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.status == filter_device_status)
    if filter_manager_id:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.manager_id == filter_manager_id)
    if filter_ip:
        q = q.join(DeviceGroupDevice, DeviceGroupDevice.group_id == DeviceGroup.id) \
             .join(Device, Device.id == DeviceGroupDevice.device_id) \
             .filter(Device.configuration.ilike(f"%{filter_ip}%"))

    groups = q.order_by(DeviceGroup.id.desc()).all()
    group_summaries = []
    for g in groups:
        device_count = DeviceGroupDevice.query.filter_by(group_id=g.id).count()
        user_count = UserDeviceGroup.query.filter_by(group_id=g.id).count()
        group_summaries.append({'group': g, 'device_count': device_count, 'user_count': user_count})

    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    creators = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    # Chỉ hiển thị thiết bị chưa thuộc bất kỳ nhóm nào để chọn
    assigned_device_ids = [l.device_id for l in DeviceGroupDevice.query.all()]
    if assigned_device_ids:
        devices = Device.query.filter(~Device.id.in_(assigned_device_ids)).order_by(Device.device_code).all()
    else:
        devices = Device.query.order_by(Device.device_code).all()
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng', 'Thanh lý', 'Test', 'Mượn']
    managers = User.query.filter(User.id.in_([d.manager_id for d in Device.query.filter(Device.manager_id.isnot(None)).all()])).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    return render_template(
        'device_groups.html',
        group_summaries=group_summaries,
        users=users,
        creators=creators,
        devices=devices,
        device_types=device_types,
        statuses=statuses,
        managers=managers,
        filter_name=filter_name,
        filter_user_id=filter_user_id,
        filter_device_code=filter_device_code,
        filter_device_name=filter_device_name,
        filter_device_type=filter_device_type,
        filter_device_status=filter_device_status,
        filter_manager_id=filter_manager_id,
        filter_ip=filter_ip,
        filter_start_date=filter_start_date,
        filter_end_date=filter_end_date,
        filter_created_by=filter_created_by
    )

@app.route('/device_groups/<int:group_id>', methods=['GET', 'POST'])
def device_group_detail(group_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    group = DeviceGroup.query.get_or_404(group_id)
    # Thiết bị trong nhóm
    device_links = DeviceGroupDevice.query.filter_by(group_id=group_id).all()
    device_ids_in_group = [l.device_id for l in device_links] if device_links else []
    devices_in_group = Device.query.filter(Device.id.in_(device_ids_in_group)).order_by(Device.device_code).all() if device_ids_in_group else []
    # Thiết bị chưa thuộc bất kỳ nhóm nào (để đảm bảo 1 thiết bị chỉ ở 1 nhóm)
    all_assigned_ids = [l.device_id for l in DeviceGroupDevice.query.all()]
    if all_assigned_ids:
        devices_not_in_group = Device.query.filter(~Device.id.in_(all_assigned_ids)).order_by(Device.device_code).all()
    else:
        devices_not_in_group = Device.query.order_by(Device.device_code).all()
    # Người dùng trong nhóm
    user_links = UserDeviceGroup.query.filter_by(group_id=group_id).all()
    user_ids_in_group = [l.user_id for l in user_links] if user_links else []
    users_in_group = User.query.filter(User.id.in_(user_ids_in_group)).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all() if user_ids_in_group else []
    users_not_in_group = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all() if not user_ids_in_group else User.query.filter(~User.id.in_(user_ids_in_group)).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    return render_template('device_group_detail.html', group=group, devices_in_group=devices_in_group, devices_not_in_group=devices_not_in_group, users_in_group=users_in_group, users_not_in_group=users_not_in_group)

# --- Inventory Receipt Routes ---
@app.route('/inventory_receipts')
def inventory_receipts():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # Get filter parameters
    sort_by = request.args.get('sort', 'date_desc')
    filter_supplier = request.args.get('filter_supplier', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # Build query with filters
    query = InventoryReceipt.query
    
    if filter_supplier:
        query = query.filter(InventoryReceipt.supplier.ilike(f'%{filter_supplier}%'))
    
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            query = query.filter(InventoryReceipt.date >= from_date)
        except ValueError:
            pass
            
    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            query = query.filter(InventoryReceipt.date <= to_date)
        except ValueError:
            pass
    
    # Apply sorting
    if sort_by == 'date_asc':
        query = query.order_by(InventoryReceipt.date.asc())
    elif sort_by == 'date_desc':
        query = query.order_by(InventoryReceipt.date.desc())
    elif sort_by == 'code_asc':
        query = query.order_by(InventoryReceipt.code.asc())
    elif sort_by == 'code_desc':
        query = query.order_by(InventoryReceipt.code.desc())
    else:
        query = query.order_by(InventoryReceipt.id.desc())
    
    receipts = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get list of suppliers for filter dropdown
    suppliers = [s[0] for s in db.session.query(InventoryReceipt.supplier).distinct().filter(InventoryReceipt.supplier.isnot(None)).order_by(InventoryReceipt.supplier).all()]
    
    return render_template('inventory_receipts.html', 
                         receipts=receipts, 
                         sort_by=sort_by,
                         filter_supplier=filter_supplier,
                         date_from=date_from,
                         date_to=date_to,
                         suppliers=suppliers)

@app.route('/inventory_receipts/<int:receipt_id>')
def inventory_receipt_detail(receipt_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    receipt = InventoryReceipt.query.get_or_404(receipt_id)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    items_query = InventoryReceiptItem.query.filter_by(receipt_id=receipt.id)
    items_pagination = items_query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('inventory_receipt_detail.html', receipt=receipt, items_pagination=items_pagination)

@app.route('/inventory_receipts/<int:receipt_id>/export_mof')
def inventory_receipt_export_mof(receipt_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if receipt_id == 0:
        # Tạo phiếu nhập kho mới (mẫu trống)
        return render_template('inventory_receipt_mof.html', receipt=None, items=[])
    receipt = InventoryReceipt.query.get_or_404(receipt_id)
    items = InventoryReceiptItem.query.filter_by(receipt_id=receipt.id).all()
    # Render mẫu in theo chuẩn Bộ Tài chính (bản HTML in ấn)
    return render_template('inventory_receipt_mof.html', receipt=receipt, items=items)

@app.route('/save_btc_receipt', methods=['POST'])
def save_btc_receipt():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        # Tạo phiếu nhập kho mới
        code = request.form.get('code')
        date_str = request.form.get('date')
        supplier = request.form.get('supplier')
        importer = request.form.get('importer')
        
        if not code or not date_str:
            flash('Vui lòng nhập đầy đủ số phiếu và ngày.', 'danger')
            return redirect(url_for('inventory_receipt_export_mof', receipt_id=0))
        
        # Kiểm tra mã phiếu trùng
        if InventoryReceipt.query.filter_by(code=code).first():
            flash('Mã phiếu đã tồn tại.', 'danger')
            return redirect(url_for('inventory_receipt_export_mof', receipt_id=0))
        
        receipt = InventoryReceipt(
            code=code,
            date=datetime.strptime(date_str, '%Y-%m-%d').date(),
            supplier=supplier or None,
            importer=importer or None,
            created_by=session.get('user_id')
        )
        db.session.add(receipt)
        db.session.flush()  # Để lấy ID
        
        # Thêm các item
        device_codes = request.form.getlist('device_code[]')
        device_names = request.form.getlist('device_name[]')
        device_types = request.form.getlist('device_type[]')
        quantities = request.form.getlist('quantity[]')
        conditions = request.form.getlist('condition[]')
        notes = request.form.getlist('note[]')
        
        for i, device_code in enumerate(device_codes):
            if device_code and device_names[i]:
                # Tạo thiết bị mới
                device = Device(
                    device_code=device_code,
                    name=device_names[i],
                    device_type=device_types[i] or 'Thiết bị khác',
                    serial_number='',
                    purchase_date=receipt.date,
                    import_date=receipt.date,
                    condition=conditions[i] or 'Mới',
                    status='Sẵn sàng',
                    created_at=datetime.utcnow()
                )
                db.session.add(device)
                db.session.flush()
                
                # Tạo item
                item = InventoryReceiptItem(
                    receipt_id=receipt.id,
                    device_id=device.id,
                    quantity=int(quantities[i]) if quantities[i] else 1,
                    device_condition=conditions[i] or 'Mới',
                    device_note=notes[i] or None
                )
                db.session.add(item)
        
        db.session.commit()
        flash('Lưu phiếu nhập kho thành công!', 'success')
        return redirect(url_for('inventory_receipt_detail', receipt_id=receipt.id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi lưu phiếu: {str(e)}', 'danger')
        return redirect(url_for('inventory_receipt_export_mof', receipt_id=0))

@app.route('/inventory_receipts/<int:receipt_id>/edit', methods=['GET', 'POST'])
def inventory_receipt_edit(receipt_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    receipt = InventoryReceipt.query.get_or_404(receipt_id)
    if request.method == 'POST':
        old = {
            'code': receipt.code,
            'date': receipt.date,
            'supplier': receipt.supplier,
            'importer': receipt.importer,
            'notes': receipt.notes,
            'config_proposal_id': getattr(receipt, 'config_proposal_id', None)
        }
        receipt.code = request.form.get('code') or receipt.code
        date_str = request.form.get('date')
        if date_str:
            try:
                receipt.date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Ngày nhập không hợp lệ (YYYY-MM-DD).', 'danger')
                return redirect(url_for('inventory_receipt_edit', receipt_id=receipt_id))
        receipt.supplier = request.form.get('supplier') or None
        receipt.importer = request.form.get('importer') or None
        receipt.notes = request.form.get('notes') or None
        # Optional link to proposal
        try:
            from sqlalchemy import text
            cfg_id_str = request.form.get('config_proposal_id')
            receipt.config_proposal_id = int(cfg_id_str) if cfg_id_str else None
        except Exception:
            pass
        db.session.commit()
        new = {
            'code': receipt.code,
            'date': receipt.date,
            'supplier': receipt.supplier,
            'importer': receipt.importer,
            'notes': receipt.notes,
            'config_proposal_id': getattr(receipt, 'config_proposal_id', None)
        }
        _log_audit('inventory_receipt', receipt.id, old, new)
        flash('Cập nhật phiếu nhập kho thành công.', 'success')
        return redirect(url_for('inventory_receipt_detail', receipt_id=receipt.id))
    return render_template('inventory_receipt_edit.html', receipt=receipt)

@app.route('/inventory_receipts/<int:receipt_id>/delete', methods=['POST'])
def inventory_receipt_delete(receipt_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    receipt = InventoryReceipt.query.get_or_404(receipt_id)
    # Xóa kèm items
    for it in InventoryReceiptItem.query.filter_by(receipt_id=receipt.id).all():
        db.session.delete(it)
    db.session.delete(receipt)
    db.session.commit()
    flash('Đã xóa phiếu nhập kho.', 'success')
    return redirect(url_for('inventory_receipts'))
    return render_template('device_group_detail.html', group=group, devices_in_group=devices_in_group, devices_not_in_group=devices_not_in_group, users_in_group=users_in_group, users_not_in_group=users_not_in_group)

@app.route('/device_groups/<int:group_id>/edit', methods=['POST'])
def edit_device_group(group_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    group = DeviceGroup.query.get_or_404(group_id)
    old = {
        'name': group.name,
        'description': group.description,
        'notes': group.notes,
    }
    name = request.form.get('name')
    description = request.form.get('description')
    notes = request.form.get('notes')
    device_ids = request.form.getlist('device_ids')
    
    if not name:
        flash('Tên nhóm là bắt buộc.', 'danger')
        return redirect(url_for('device_groups'))
    
    group.name = name
    group.description = description
    group.notes = notes
    
    # Update device membership (ensure 1 device per group)
    # First, remove all existing device links for this group
    DeviceGroupDevice.query.filter_by(group_id=group_id).delete()
    
    # Then add new device links
    for device_id in device_ids:
        if device_id:
            # Remove device from any other group first
            old_link = DeviceGroupDevice.query.filter_by(device_id=device_id).first()
            if old_link:
                db.session.delete(old_link)
            
            # Add to this group
            new_link = DeviceGroupDevice(group_id=group_id, device_id=device_id)
            db.session.add(new_link)
    
    db.session.commit()
    new = {
        'name': group.name,
        'description': group.description,
        'notes': group.notes,
    }
    _log_audit('device_group', group.id, old, new)
    flash('Cập nhật nhóm thiết bị thành công!', 'success')
    return redirect(url_for('device_groups'))

@app.route('/device_groups/<int:group_id>/delete', methods=['POST'])
def delete_device_group(group_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    group = DeviceGroup.query.get_or_404(group_id)
    db.session.delete(group)
    db.session.commit()
    flash('Xóa nhóm thiết bị thành công!', 'success')
    return redirect(url_for('device_groups'))

@app.route('/device_groups/<int:group_id>/assign_devices', methods=['POST'])
def assign_devices_to_group(group_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device_ids = request.form.getlist('device_ids')
    if not device_ids:
        flash('Vui lòng chọn ít nhất một thiết bị.', 'danger')
        return redirect(url_for('device_group_detail', group_id=group_id))
    created = 0
    for d_id in device_ids:
        if not d_id:
            continue
        d_int = int(d_id)
        # Gỡ khỏi nhóm cũ nếu đang thuộc nhóm khác (đảm bảo unique)
        old_link = DeviceGroupDevice.query.filter_by(device_id=d_int).first()
        if old_link and old_link.group_id != group_id:
            db.session.delete(old_link)
        exists = DeviceGroupDevice.query.filter_by(group_id=group_id, device_id=d_int).first()
        if not exists:
            db.session.add(DeviceGroupDevice(group_id=group_id, device_id=d_int))
            created += 1
    db.session.commit()
    flash(f'Đã thêm {created} thiết bị vào nhóm.', 'success')
    return redirect(url_for('device_group_detail', group_id=group_id))

@app.route('/server_room', methods=['GET', 'POST'])
def server_room():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Tạo/lấy nhóm đặc biệt "Phòng server"
    group = DeviceGroup.query.filter(func.lower(DeviceGroup.name) == func.lower('Phòng server')).first()
    if not group:
        group = DeviceGroup(name='Phòng server', description='Nhóm thiết bị phòng server', created_by=session.get('user_id'))
        db.session.add(group)
        db.session.commit()
    if request.method == 'POST':
        # Get existing device IDs in server room
        current_device_ids = set(link.device_id for link in DeviceGroupDevice.query.filter_by(group_id=group.id).all())
        
        # Get selected device IDs from form
        selected_device_ids = set(int(d_id) for d_id in request.form.getlist('device_ids') if d_id)
        
        # Find devices to add and remove
        devices_to_add = selected_device_ids - current_device_ids
        devices_to_remove = current_device_ids - selected_device_ids
        
        # Remove devices that are unselected
        for d_id in devices_to_remove:
            link = DeviceGroupDevice.query.filter_by(group_id=group.id, device_id=d_id).first()
            if link:
                db.session.delete(link)
        
        # Add newly selected devices
        for d_id in devices_to_add:
            # Remove from old group if exists
            old_link = DeviceGroupDevice.query.filter_by(device_id=d_id).first()
            if old_link and old_link.group_id != group.id:
                db.session.delete(old_link)
            # Add to server room group
            db.session.add(DeviceGroupDevice(group_id=group.id, device_id=d_id))
        
        db.session.commit()
        flash(f'Đã cập nhật danh sách thiết bị trong Phòng server.', 'success')
        return redirect(url_for('server_room'))

    # Danh sách thiết bị trong phòng server (phân trang)
    links = DeviceGroupDevice.query.filter_by(group_id=group.id).all()
    ids_in_server = [l.device_id for l in links]
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # Load current filters from query params or session-saved defaults
    saved_filters = session.get('server_room_filters', {}) or {}
    search_name = request.args.get('search_name')
    search_code = request.args.get('search_code')
    filter_team = request.args.get('filter_team')
    filter_type = request.args.get('filter_type')
    filter_status = request.args.get('filter_status')
    filter_ip = request.args.get('filter_ip')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    if search_name is None or search_name == '':
        search_name = saved_filters.get('search_name', '')
    if search_code is None or search_code == '':
        search_code = saved_filters.get('search_code', '')
    if filter_team is None or filter_team == '':
        filter_team = saved_filters.get('filter_team', '')
    if filter_type is None or filter_type == '':
        filter_type = saved_filters.get('filter_type', '')
    if filter_status is None or filter_status == '':
        filter_status = saved_filters.get('filter_status', '')
    if filter_ip is None or filter_ip == '':
        filter_ip = saved_filters.get('filter_ip', '')
    if date_from is None or date_from == '':
        date_from = saved_filters.get('date_from', '')
    if date_to is None or date_to == '':
        date_to = saved_filters.get('date_to', '')
    
    devices_pagination = None
    if ids_in_server:
        id_to_added_at = {l.device_id: l.created_at for l in links}
        base_q = Device.query.filter(Device.id.in_(ids_in_server)).join(User, Device.manager_id == User.id, isouter=True)
        
        # Apply filters
        if search_name:
            base_q = base_q.filter(Device.name.ilike(f'%{search_name}%'))
        if search_code:
            base_q = base_q.filter(Device.device_code.ilike(f'%{search_code}%'))
        if filter_team:
            base_q = base_q.filter(User.full_name.ilike(f'%{filter_team}%'))
        if filter_type:
            base_q = base_q.filter(Device.device_type == filter_type)
        # Join ServerRoomDeviceInfo if needed for filtering
        needs_server_room_join = filter_status or filter_ip
        if needs_server_room_join:
            try:
                base_q = base_q.join(ServerRoomDeviceInfo, Device.id == ServerRoomDeviceInfo.device_id, isouter=True)
            except Exception:
                # If ServerRoomDeviceInfo table doesn't exist yet, skip the join
                pass
        
        if filter_status and needs_server_room_join:
            try:
                if filter_status == 'online':
                    base_q = base_q.filter(ServerRoomDeviceInfo.usage_status.ilike('Đang hoạt động') | ServerRoomDeviceInfo.usage_status.ilike('Online'))
                elif filter_status == 'offline':
                    base_q = base_q.filter(ServerRoomDeviceInfo.usage_status.ilike('Đã ngừng') | ServerRoomDeviceInfo.usage_status.ilike('Offline') | ServerRoomDeviceInfo.usage_status.ilike('Ngừng hoạt động') | ServerRoomDeviceInfo.usage_status.isnull())
            except Exception as e:
                print(f"Error filtering server room status: {str(e)}")
                pass
        if filter_ip and needs_server_room_join:
            try:
                base_q = base_q.filter(ServerRoomDeviceInfo.ip_address.ilike(f'%{filter_ip}%'))
            except Exception:
                pass
        if date_from:
            try:
                from_date = datetime.strptime(date_from, '%Y-%m-%d')
                base_q = base_q.filter(Device.created_at >= from_date)
            except ValueError:
                pass
        if date_to:
            try:
                to_date = datetime.strptime(date_to, '%Y-%m-%d')
                base_q = base_q.filter(Device.created_at <= to_date)
            except ValueError:
                pass
        
        base_q = base_q.order_by(Device.device_code)
        devices_pagination = base_q.paginate(page=page, per_page=per_page, error_out=False)
        for d in devices_pagination.items:
            setattr(d, '_server_added_at', id_to_added_at.get(d.id))
    
    # Get filter options
    all_teams = [u[0] for u in db.session.query(User.full_name).distinct().all() if u[0]]
    all_types = [d[0] for d in db.session.query(Device.device_type).distinct().all() if d[0]]
    all_statuses = [d[0] for d in db.session.query(Device.status).distinct().all() if d[0]]
    
    # Thiết bị sẵn có để thêm: tất cả, trừ thiết bị đã trong phòng server
    if ids_in_server:
        devices_available = Device.query.filter(~Device.id.in_(ids_in_server)).order_by(Device.device_code).all()
    else:
        devices_available = Device.query.order_by(Device.device_code).all()
    
    return render_template('server_room.html', group=group, devices_pagination=devices_pagination, devices_available=devices_available, 
                         search_name=search_name, search_code=search_code, filter_team=filter_team, filter_type=filter_type, 
                         filter_status=filter_status, filter_ip=filter_ip, date_from=date_from, date_to=date_to,
                         all_teams=all_teams, all_types=all_types, all_statuses=all_statuses)

@app.route('/server_room/<int:device_id>')
def server_room_device_detail(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    info = ServerRoomDeviceInfo.query.get(device_id)
    return render_template('server_room_device_detail.html', device=device, info=info)

@app.route('/server_room/<int:device_id>/edit', methods=['GET', 'POST'])
def server_room_device_edit(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    info = ServerRoomDeviceInfo.query.get(device_id)
    if request.method == 'POST':
        # Cập nhật các trường của thiết bị
        device.name = request.form.get('name') or device.name
        device.device_code = request.form.get('device_code') or device.device_code
        device.device_type = request.form.get('device_type') or device.device_type
        device.status = request.form.get('status') or device.status
        try:
            mgr = request.form.get('manager_id')
            device.manager_id = int(mgr) if mgr else None
        except Exception:
            pass
        device.configuration = request.form.get('configuration') or device.configuration
        device.notes = request.form.get('notes') or device.notes
        # Cập nhật IP, dịch vụ, trạng thái sử dụng, phòng ban
        ip = request.form.get('ip_address')
        services = request.form.get('services_running')
        usage_status = request.form.get('usage_status') or 'Đang hoạt động'
        department = request.form.get('department')
        if info is None:
            info = ServerRoomDeviceInfo(device_id=device.id)
            db.session.add(info)
        info.ip_address = ip or None
        info.services_running = services or None
        info.usage_status = usage_status
        info.department = department or None
        db.session.commit()
        flash('Đã cập nhật thông tin phòng server của thiết bị.', 'success')
        return redirect(url_for('server_room'))
    # Render form gồm trường thiết bị và 2 trường phòng server
    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng']
    departments = [d.name for d in Department.query.all()]
    return render_template('edit_server_room_device.html', device=device, info=info, users=users, statuses=statuses, departments=departments)

@app.route('/server_room/<int:device_id>/remove', methods=['POST'])
def server_room_device_remove(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    # remove link from Phòng server group
    group = DeviceGroup.query.filter(func.lower(DeviceGroup.name) == func.lower('Phòng server')).first()
    if group:
        link = DeviceGroupDevice.query.filter_by(group_id=group.id, device_id=device_id).first()
        if link:
            db.session.delete(link)
            db.session.commit()
            flash('Đã gỡ thiết bị khỏi Phòng server.', 'success')
    return redirect(url_for('server_room'))

@app.route('/device_groups/<int:group_id>/remove_device/<int:device_id>', methods=['POST'])
def remove_device_from_group(group_id, device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    link = DeviceGroupDevice.query.filter_by(group_id=group_id, device_id=device_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
        flash('Đã gỡ thiết bị khỏi nhóm.', 'success')
    return redirect(url_for('device_group_detail', group_id=group_id))

@app.route('/device_groups/<int:group_id>/assign_users', methods=['POST'])
def assign_users_to_group(group_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_ids = request.form.getlist('user_ids')
    role = request.form.get('role')
    if not user_ids:
        flash('Vui lòng chọn ít nhất một người dùng.', 'danger')
        return redirect(url_for('device_group_detail', group_id=group_id))
    created = 0
    for u_id in user_ids:
        if not u_id: continue
        exists = UserDeviceGroup.query.filter_by(group_id=group_id, user_id=int(u_id)).first()
        if not exists:
            db.session.add(UserDeviceGroup(group_id=group_id, user_id=int(u_id), role=role))
            created += 1
    db.session.commit()
    flash(f'Đã thêm {created} người dùng vào nhóm.', 'success')
    return redirect(url_for('device_group_detail', group_id=group_id))

@app.route('/device_groups/<int:group_id>/remove_user/<int:user_id>', methods=['POST'])
def remove_user_from_group(group_id, user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    link = UserDeviceGroup.query.filter_by(group_id=group_id, user_id=user_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
        flash('Đã gỡ người dùng khỏi nhóm.', 'success')
    return redirect(url_for('device_group_detail', group_id=group_id))

@app.route('/server_room/save_filters', methods=['POST'])
def save_server_room_filters():
    if 'user_id' not in session: return redirect(url_for('login'))
    filters = {
        'search_name': request.form.get('search_name', '').strip(),
        'search_code': request.form.get('search_code', '').strip(),
        'filter_team': request.form.get('filter_team', '').strip(),
        'filter_type': request.form.get('filter_type', '').strip(),
        'filter_status': request.form.get('filter_status', '').strip(),
        'filter_ip': request.form.get('filter_ip', '').strip(),
        'date_from': request.form.get('date_from', '').strip(),
        'date_to': request.form.get('date_to', '').strip(),
    }
    session['server_room_filters'] = filters
    flash('Đã lưu bộ lọc phòng server.', 'success')
    # Redirect back with filters as query so UI reflects saved state
    return redirect(url_for('server_room', **{k: v for k, v in filters.items() if v}))

@app.route('/add_devices_bulk', methods=['GET', 'POST'])
def add_devices_bulk():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        # Trường chung
        shared_purchase_date = request.form.get('shared_purchase_date')
        shared_import_date = request.form.get('shared_import_date')
        shared_condition = request.form.get('shared_condition')  # deprecated: now per-device, kept for backward compat
        shared_status = request.form.get('shared_status', 'Sẵn sàng')
        shared_buyer = request.form.get('shared_buyer')
        shared_importer = request.form.get('shared_importer')
        shared_brand = request.form.get('shared_brand')  # deprecated: now per-device, used as fallback
        shared_supplier = request.form.get('shared_supplier')
        shared_warranty = request.form.get('shared_warranty_fallback')
        shared_notes = request.form.get('shared_notes')
        shared_group_ids = request.form.getlist('shared_group_ids')
        shared_manager_id = request.form.get('shared_manager_id')
        shared_assign_date = request.form.get('shared_assign_date')

        # Trường riêng theo từng thiết bị (mảng)
        names = request.form.getlist('name[]')
        device_codes = request.form.getlist('device_code[]')
        serial_numbers = request.form.getlist('serial_number[]')
        configurations = request.form.getlist('configuration[]')
        purchase_prices = request.form.getlist('purchase_price[]')
        notes_list = request.form.getlist('notes[]')
        device_types = request.form.getlist('device_type[]')
        quantities = request.form.getlist('quantity[]')
        brands = request.form.getlist('brand[]')
        warranties = request.form.getlist('warranty[]')
        suppliers = request.form.getlist('supplier[]')
        device_conditions = request.form.getlist('device_condition[]')

        # Validation cơ bản
        if not shared_purchase_date or not shared_import_date:
            flash('Vui lòng nhập đầy đủ các trường chung bắt buộc.', 'danger')
            return redirect(url_for('add_devices_bulk'))
        if not names or not any(n.strip() for n in names):
            flash('Vui lòng nhập ít nhất một thiết bị ở danh sách chi tiết.', 'danger')
            return redirect(url_for('add_devices_bulk'))

        try:
            # Tạo phiếu nhập kho
            today_str = datetime.utcnow().strftime('%Y%m%d')
            last_receipt = InventoryReceipt.query.order_by(InventoryReceipt.id.desc()).first()
            next_seq = (last_receipt.id + 1) if last_receipt else 1
            receipt_code = f"PNK{today_str}-{next_seq:04d}"
            receipt = InventoryReceipt(
                code=receipt_code,
                date=datetime.strptime(shared_import_date, '%Y-%m-%d').date(),
                supplier=shared_supplier or None,
                importer=shared_importer or None,
                created_by=session.get('user_id'),
                notes=shared_notes or None
            )
            db.session.add(receipt)
            db.session.flush()

            created_count = 0

            # Helper: map device type to code prefix and width
            def _type_to_prefix(device_type: str):
                t = (device_type or '').strip().lower()
                if 'laptop' in t:
                    return ('LT', 3)
                if 'case' in t or 'case máy tính' in t or 'desktop' in t:
                    return ('Case', 3)
                if 'màn hình' in t or 'monitor' in t:
                    return ('MH', 3)
                if 'server' in t:
                    return ('SV', 3)
                if 'chuột' in t or 'mouse' in t:
                    return ('C', 3)
                if 'bàn phím' in t or 'keyboard' in t:
                    return ('BP', 3)
                return ('TB', 5)

            # Precompute next sequence per prefix from DB
            unique_prefixes = set(_type_to_prefix(dt)[0] for dt in device_types if (dt or '').strip())
            next_seq_by_prefix = {}
            for pref in unique_prefixes:
                existing_codes = [row[0] for row in db.session.query(Device.device_code).filter(Device.device_code.like(f"{pref}_%")) .all()]
                max_num = 0
                for code in existing_codes:
                    try:
                        tail = code.split('_', 1)[1]
                        num = int(''.join(ch for ch in tail if ch.isdigit()))
                        if num > max_num:
                            max_num = num
                    except Exception:
                        continue
                next_seq_by_prefix[pref] = max_num + 1
            for idx, name in enumerate(names):
                if not name or not name.strip():
                    continue
                dtype = (device_types[idx] if idx < len(device_types) else '').strip()
                if not dtype:
                    db.session.rollback()
                    flash('Vui lòng chọn loại thiết bị cho từng dòng.', 'danger')
                    return redirect(url_for('add_devices_bulk'))
                qty = 1
                if idx < len(quantities) and quantities[idx]:
                    try:
                        qty = max(1, int(quantities[idx]))
                    except ValueError:
                        qty = 1
                for k in range(qty):
                    device_code = device_codes[idx].strip() if idx < len(device_codes) and device_codes[idx] and k == 0 else ''
                    if not device_code:
                        pref, width = _type_to_prefix(dtype)
                        seq = next_seq_by_prefix.get(pref, 1)
                        device_code = f"{pref}_{seq:0{width}d}"
                        next_seq_by_prefix[pref] = seq + 1

                    if Device.query.filter_by(device_code=device_code).first():
                        db.session.rollback()
                        flash(f'Mã thiết bị {device_code} đã tồn tại. Dừng thao tác.', 'danger')
                        return redirect(url_for('add_devices_bulk'))

                    new_device = Device(
                        device_code=device_code,
                        name=name.strip(),
                        device_type=dtype,
                        serial_number=(serial_numbers[idx] if idx < len(serial_numbers) else None) or None,
                        brand=((brands[idx] if idx < len(brands) and brands[idx] else shared_brand) or None),
                        supplier=((suppliers[idx] if idx < len(suppliers) and suppliers[idx] else shared_supplier) or None),
                        warranty=((warranties[idx] if idx < len(warranties) and warranties[idx] else shared_warranty) or None),
                        configuration=(configurations[idx] if idx < len(configurations) else None) or None,
                        purchase_date=datetime.strptime(shared_purchase_date, '%Y-%m-%d').date(),
                        purchase_price=(float(purchase_prices[idx]) if idx < len(purchase_prices) and purchase_prices[idx] else None),
                        buyer=(shared_buyer or None),
                        importer=(shared_importer or None),
                        import_date=datetime.strptime(shared_import_date, '%Y-%m-%d').date(),
                        condition=((device_conditions[idx] if idx < len(device_conditions) and device_conditions[idx] else None) or 'Sử dụng bình thường'),
                        status=shared_status,
                        manager_id=(int(shared_manager_id) if shared_manager_id else None),
                        assign_date=(datetime.strptime(shared_assign_date, '%Y-%m-%d').date() if shared_assign_date else None),
                        notes=(notes_list[idx] if idx < len(notes_list) else shared_notes)
                    )
                    db.session.add(new_device)
                    db.session.flush()

                    db.session.add(InventoryReceiptItem(
                        receipt_id=receipt.id,
                        device_id=new_device.id,
                        quantity=1,
                        device_condition=new_device.condition,
                        device_note=new_device.notes
                    ))

                    # Gán nhóm mặc định nếu có
                    for gid in shared_group_ids:
                        if not gid: continue
                        exists = DeviceGroupDevice.query.filter_by(group_id=int(gid), device_id=new_device.id).first()
                        if not exists:
                            db.session.add(DeviceGroupDevice(group_id=int(gid), device_id=new_device.id))

                    created_count += 1

            if created_count == 0:
                db.session.rollback()
                flash('Không có thiết bị hợp lệ để tạo.', 'danger')
                return redirect(url_for('add_devices_bulk'))

            db.session.commit()
            flash(f'Thêm thành công {created_count} thiết bị! Đã tạo phiếu nhập kho {receipt.code}.', 'success')
            return redirect(url_for('inventory_receipts'))
        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi khi thêm thiết bị hàng loạt: {str(e)}', 'danger')
            return redirect(url_for('add_devices_bulk'))

    managers = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    groups = DeviceGroup.query.order_by(DeviceGroup.name).all()
    return render_template('add_devices_bulk.html', managers=managers, groups=groups)

# --- Handover Routes ---
@app.route('/handover_report', methods=['GET'])
def handover_report():
    # Get a list of all devices and users for the form
    devices = Device.query.all()
    users = User.query.all()
    return render_template('handover_report.html', devices=devices, users=users)

@app.route('/handover_list')
def handover_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_device_code = request.args.get('filter_device_code', '')
    filter_giver_id = request.args.get('filter_giver_id', '')
    filter_receiver_id = request.args.get('filter_receiver_id', '')
    filter_device_type = request.args.get('filter_device_type', '')
    filter_start_date = request.args.get('filter_start_date', '')
    filter_end_date = request.args.get('filter_end_date', '')
    
    query = DeviceHandover.query.join(Device)

    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_giver_id:
        query = query.filter(DeviceHandover.giver_id == filter_giver_id)
    if filter_receiver_id:
        query = query.filter(DeviceHandover.receiver_id == filter_receiver_id)
    if filter_device_type:
        query = query.filter(Device.device_type == filter_device_type)
    if filter_start_date:
        query = query.filter(DeviceHandover.handover_date >= datetime.strptime(filter_start_date, '%Y-%m-%d').date())
    if filter_end_date:
        query = query.filter(DeviceHandover.handover_date <= datetime.strptime(filter_end_date, '%Y-%m-%d').date())

    handovers_pagination = query.order_by(DeviceHandover.handover_date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    return render_template('handovers.html', handovers=handovers_pagination, users=users, device_types=device_types, filter_device_code=filter_device_code, filter_giver_id=filter_giver_id, filter_receiver_id=filter_receiver_id, filter_device_type=filter_device_type, filter_start_date=filter_start_date, filter_end_date=filter_end_date)

# Thêm route mới này vào file app.py (trong khu vực Handover Routes)

@app.route('/download_handover_template')
def download_handover_template():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Định nghĩa các cột và dữ liệu mẫu
    columns = [
        'Mã thiết bị', 'Tên đăng nhập người giao', 'Tên đăng nhập người nhận', 
        'Ngày bàn giao', 'Tình trạng thiết bị', 'Lý do bàn giao', 
        'Nơi đặt thiết bị', 'Ghi chú'
    ]
    sample_data = [
        ['TB00001', 'admin', 'nhanvienA', '28-08-2025', 'Sử dụng bình thường', 'Cấp mới cho nhân viên', 'Phòng Kế toán', 'Ghi chú thêm nếu có']
    ]
    
    # Tạo DataFrame từ dữ liệu mẫu
    df = pd.DataFrame(sample_data, columns=columns)
    
    # Tạo file Excel trong bộ nhớ
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Handover_Template')
    output.seek(0)
    
    # Gửi file về cho người dùng
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='handover_import_template.xlsx'
    )

# --- CẬP NHẬT HÀM ADD_HANDOVER ---
@app.route('/add_handover', methods=['GET', 'POST'])
def add_handover():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Lấy danh sách ID thiết bị từ form và nhóm
        raw_device_ids = request.form.getlist('device_ids')
        group_ids = request.form.getlist('group_ids')
        device_ids_set = set([d for d in raw_device_ids if d])
        if group_ids:
            try:
                group_int_ids = [int(g) for g in group_ids if g]
                rows = db.session.query(DeviceGroupDevice.device_id).filter(DeviceGroupDevice.group_id.in_(group_int_ids)).all()
                for (did,) in rows:
                    device_ids_set.add(str(did))
            except Exception:
                pass
        device_ids = list(device_ids_set)
        receiver_id = request.form.get('receiver_id')
        handover_date_str = request.form.get('handover_date')
        
        # Validation cơ bản
        if not device_ids or not any(d_id for d_id in device_ids if d_id) or not receiver_id or not handover_date_str:
            flash('Vui lòng chọn ít nhất một thiết bị và điền đầy đủ các trường bắt buộc.', 'danger')
            return redirect(url_for('add_handover'))
            
        handover_date = datetime.strptime(handover_date_str, '%Y-%m-%d').date()
        
        handovers_created_count = 0
        for device_id in device_ids:
            if not device_id: continue # Bỏ qua các giá trị rỗng

            device_to_update = Device.query.get(device_id)
            # Kiểm tra xem thiết bị có hợp lệ và sẵn sàng không
            if not device_to_update or device_to_update.status != 'Sẵn sàng':
                flash(f'Thiết bị có mã "{device_to_update.device_code if device_to_update else "không xác định"}" không hợp lệ hoặc không sẵn sàng để bàn giao.', 'warning')
                continue

            new_handover = DeviceHandover(
                handover_date=handover_date, 
                device_id=device_id, 
                giver_id=request.form['giver_id'], 
                receiver_id=receiver_id, 
                device_condition=request.form['device_condition'], 
                reason=request.form.get('reason', ''), 
                location=request.form.get('location', ''), 
                notes=request.form.get('notes', '')
            )
            db.session.add(new_handover)
            
            # Cập nhật trạng thái thiết bị
            device_to_update.manager_id = int(receiver_id)
            device_to_update.assign_date = new_handover.handover_date
            device_to_update.status = 'Đã cấp phát'
            
            handovers_created_count += 1

        if handovers_created_count > 0:
            db.session.commit()
            flash(f'Tạo thành công {handovers_created_count} phiếu bàn giao!', 'success')
        else:
            db.session.rollback() # Hoàn tác nếu không có phiếu nào được tạo
            flash('Không có phiếu bàn giao nào được tạo. Vui lòng kiểm tra lại thông tin thiết bị.', 'danger')

        return redirect(url_for('handover_list'))
    
    # Logic cho phương thức GET
    preselected_device_id = request.args.get('device_id', type=int)
    # Chỉ hiển thị các thiết bị sẵn sàng để chọn
    devices = Device.query.filter_by(status='Sẵn sàng').order_by(Device.device_code).all()
    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    groups = DeviceGroup.query.order_by(DeviceGroup.name).all()
    
    return render_template('add_handover.html', 
                           devices=devices, 
                           users=users,
                           groups=groups,
                           now=datetime.now(),
                           preselected_device_id=preselected_device_id)

# ... (Các hàm edit_handover, delete_handover giữ nguyên) ...
# Thay thế hàm này trong file app.py

@app.route('/edit_handover/<int:handover_id>', methods=['GET', 'POST'])
def edit_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    handover = DeviceHandover.query.get_or_404(handover_id)
    
    # Lưu lại thông tin cũ trước khi thay đổi
    old_device_id = handover.device_id
    
    if request.method == 'POST':
        old = {
            'handover_date': handover.handover_date,
            'device_id': handover.device_id,
            'giver_id': handover.giver_id,
            'receiver_id': handover.receiver_id,
            'device_condition': handover.device_condition,
            'reason': handover.reason,
            'location': handover.location,
            'notes': handover.notes,
        }
        # Lấy thông tin mới từ form
        new_device_id = int(request.form['device_id'])
        new_receiver_id = int(request.form['receiver_id'])
        new_handover_date = datetime.strptime(request.form['handover_date'], '%Y-%m-%d').date()

        # Cập nhật thông tin trên phiếu bàn giao
        handover.handover_date = new_handover_date
        handover.device_id = new_device_id
        handover.giver_id = int(request.form['giver_id'])
        handover.receiver_id = new_receiver_id
        handover.device_condition = request.form['device_condition']
        handover.reason = request.form.get('reason', '')
        handover.location = request.form.get('location', '')
        handover.notes = request.form.get('notes', '')

        # --- LOGIC CẬP NHẬT THIẾT BỊ ---

        # 1. Xử lý thiết bị MỚI được chọn trong phiếu
        new_device = Device.query.get(new_device_id)
        if new_device:
            new_device.status = 'Đã cấp phát'
            new_device.manager_id = new_receiver_id
            new_device.assign_date = new_handover_date

        # 2. Xử lý thiết bị CŨ (nếu người dùng thay đổi thiết bị trong phiếu)
        if old_device_id != new_device_id:
            old_device = Device.query.get(old_device_id)
            if old_device:
                # Tìm xem thiết bị cũ này còn phiếu bàn giao nào khác không
                last_handover_for_old_device = DeviceHandover.query \
                    .filter(DeviceHandover.device_id == old_device_id) \
                    .filter(DeviceHandover.id != handover_id) \
                    .order_by(DeviceHandover.handover_date.desc()).first()
                
                if last_handover_for_old_device:
                    # Nếu còn, trả nó về cho người nhận của phiếu gần nhất
                    old_device.status = 'Đã cấp phát'
                    old_device.manager_id = last_handover_for_old_device.receiver_id
                    old_device.assign_date = last_handover_for_old_device.handover_date
                else:
                    # Nếu không còn phiếu nào khác, trả về trạng thái "Sẵn sàng"
                    old_device.status = 'Sẵn sàng'
                    old_device.manager_id = None
                    old_device.assign_date = None
        
        db.session.commit()
        new = {
            'handover_date': handover.handover_date,
            'device_id': handover.device_id,
            'giver_id': handover.giver_id,
            'receiver_id': handover.receiver_id,
            'device_condition': handover.device_condition,
            'reason': handover.reason,
            'location': handover.location,
            'notes': handover.notes,
        }
        _log_audit('device_handover', handover.id, old, new)
        flash('Cập nhật phiếu bàn giao và thông tin thiết bị thành công!', 'success')
        return redirect(url_for('handover_list'))
        
    # Phần logic cho phương thức GET giữ nguyên
    devices = Device.query.order_by(Device.device_code).all()
    users = User.query.order_by(User.full_name).all()
    return render_template('edit_handover.html', handover=handover, devices=devices, users=users)

@app.route('/delete_handover/<int:handover_id>', methods=['POST'])
def delete_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover = DeviceHandover.query.get_or_404(handover_id)
    db.session.delete(handover)
    db.session.commit()
    flash('Xóa phiếu bàn giao thành công!', 'success')
    return redirect(url_for('handover_list'))

# Thêm route mới này vào file app.py (trong khu vực Handover Routes)

@app.route('/import_handovers', methods=['GET', 'POST'])
def import_handovers():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Thêm kiểm tra quyền admin nếu cần

    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ (.xls, .xlsx).', 'danger')
            return redirect(url_for('import_handovers'))

        try:
            df = pd.read_excel(file, engine='openpyxl')
            required_columns = ['Mã thiết bị', 'Tên đăng nhập người giao', 'Tên đăng nhập người nhận', 'Ngày bàn giao', 'Tình trạng thiết bị']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_handovers'))

            errors = []
            handovers_to_add = []
            
            for index, row in df.iterrows():
                device_code = str(row['Mã thiết bị'])
                giver_username = str(row['Tên đăng nhập người giao'])
                receiver_username = str(row['Tên đăng nhập người nhận'])
                handover_date_str = str(row['Ngày bàn giao'])

                device = Device.query.filter_by(device_code=device_code).first()
                giver = User.query.filter_by(username=giver_username).first()
                receiver = User.query.filter_by(username=receiver_username).first()

                # --- Validation ---
                current_row_errors = []
                if not device:
                    current_row_errors.append(f'Mã thiết bị "{device_code}" không tồn tại.')
                if not giver:
                    current_row_errors.append(f'Người giao "{giver_username}" không tồn tại.')
                if not receiver:
                    current_row_errors.append(f'Người nhận "{receiver_username}" không tồn tại.')
                
                if device and device.status == 'Đã cấp phát':
                     current_row_errors.append(f'Thiết bị "{device_code}" đã được cấp phát, không thể bàn giao.')

                if current_row_errors:
                    errors.append(f"Dòng {index + 2}: " + ", ".join(current_row_errors))
                    continue 

                try:
                    handover_date = pd.to_datetime(handover_date_str).date()
                except (ValueError, TypeError):
                    errors.append(f'Dòng {index + 2}: Định dạng ngày "{handover_date_str}" không hợp lệ.')
                    continue

                new_handover = DeviceHandover(
                    device_id=device.id,
                    giver_id=giver.id,
                    receiver_id=receiver.id,
                    handover_date=handover_date,
                    device_condition=row['Tình trạng thiết bị'],
                    reason=row.get('Lý do bàn giao'),
                    location=row.get('Nơi đặt thiết bị'),
                    notes=row.get('Ghi chú')
                )
                handovers_to_add.append(new_handover)
                
                # Cập nhật trạng thái của thiết bị
                device.status = 'Đã cấp phát'
                device.manager_id = receiver.id
                device.assign_date = handover_date
                
            if errors:
                for error in errors:
                    flash(error, 'danger')
                db.session.rollback() # Hoàn tác tất cả nếu có lỗi
            else:
                db.session.add_all(handovers_to_add)
                db.session.commit()
                flash(f'Đã nhập thành công {len(handovers_to_add)} phiếu bàn giao!', 'success')
                return redirect(url_for('handover_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi trong quá trình xử lý file: {str(e)}', 'danger')

    return render_template('import_handovers.html')

# --- (User Management Routes giữ nguyên) ---
@app.route('/users')
def user_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_username = request.args.get('filter_username', '')
    filter_role = request.args.get('filter_role', '')
    filter_department = request.args.get('filter_department', '')
    filter_position = request.args.get('filter_position', '')
    filter_status = request.args.get('filter_status', session.get('default_user_status', 'Đang làm'))

    query = User.query
    if filter_username:
        query = query.filter(User.username.ilike(f'%{filter_username}%'))
    if filter_role:
        query = query.filter_by(role=filter_role)
    if filter_department:
        # Tìm department theo tên
        dept = Department.query.filter_by(name=filter_department).first()
        if dept:
            query = query.filter(User.department_id == dept.id)
    if filter_position:
        query = query.filter(User.position == filter_position)
    if filter_status:
        query = query.filter(User.status == filter_status)

    # Sắp xếp danh sách người dùng theo token tên cuối (tên gọi) để đúng ABC theo tên
    users_pagination = query.order_by(
        func.lower(User.last_name_token),
        func.lower(User.username)
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    departments = [d.name for d in Department.query.order_by(Department.name).all()]
    positions = [p[0] for p in db.session.query(User.position).filter(User.position.isnot(None)).distinct().order_by(User.position)]
    statuses = ['Đang làm', 'Thử việc', 'Đã nghỉ', 'Nghỉ việc', 'Khác']

    return render_template('users.html', 
                           users=users_pagination, 
                           filter_username=filter_username, 
                           filter_role=filter_role, 
                           filter_department=filter_department,
                           filter_position=filter_position,
                           filter_status=filter_status,
                           departments=departments,
                           positions=positions,
                           statuses=statuses)

@app.route('/users/default_status', methods=['POST'])
def set_users_default_status():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Nhận đúng giá trị từ select (filter_status) hoặc fallback 'status'
    status = request.form.get('filter_status')
    if status is None:
        status = request.form.get('status')
    # Cho phép lưu rỗng để hiển thị Tất cả
    session['default_user_status'] = status if status is not None else session.get('default_user_status', 'Đang làm')
    flash('Đã lưu cấu hình trạng thái mặc định.', 'success')
    return redirect(url_for('user_list'))

@app.route('/users/<int:user_id>/reset_password', methods=['POST'])
def reset_user_password(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    user.password = generate_password_hash('Password123@')
    db.session.commit()
    flash(f'Đã reset mật khẩu cho {user.full_name or user.username} về Password123@', 'success')
    return redirect(url_for('user_list'))

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return redirect(url_for('add_user'))
        if email and User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng.', 'danger')
            return redirect(url_for('add_user'))
            
        # Handle department_id and set department name
        department_id = request.form.get('department_id')
        department_name = None
        if department_id:
            department = Department.query.get(department_id)
            if department:
                department_name = department.name
        
        new_user = User(
            username=username,
            password=generate_password_hash(request.form['password']),
            full_name=request.form.get('full_name'),
            email=email,
            date_of_birth=datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d').date() if request.form.get('date_of_birth') else None,
            role=request.form.get('role', 'user'),
            department_id=department_id,
            position=request.form.get('position'),
            phone_number=request.form.get('phone_number'),
            notes=request.form.get('notes'),
            status=request.form.get('status', 'Đang làm'),
            onboard_date=datetime.strptime(request.form['onboard_date'], '%Y-%m-%d').date() if request.form.get('onboard_date') else None,
            offboard_date=datetime.strptime(request.form['offboard_date'], '%Y-%m-%d').date() if request.form.get('offboard_date') else None,
        )
        if new_user.full_name:
            try:
                new_user.last_name_token = (str(new_user.full_name).strip().split()[-1] or '').lower()
            except Exception:
                new_user.last_name_token = None
        db.session.add(new_user)
        db.session.commit()
        flash('Thêm người dùng mới thành công!', 'success')
        return redirect(url_for('user_list'))
    departments = Department.query.all()
    return render_template('add_user.html', departments=departments)

def create_return_handover_for_user(user_id, current_user_id):
    """Tạo phiếu trả thiết bị về kho khi nhân viên nghỉ việc"""
    user = User.query.get(user_id)
    if not user:
        return False
    
    # Lấy tất cả thiết bị đang được nhân viên quản lý
    devices = Device.query.filter_by(manager_id=user_id, status='Đã cấp phát').all()
    
    if not devices:
        return True  # Không có thiết bị nào cần trả
    
    try:
        handovers_created = 0
        
        # Tạo phiếu trả thiết bị cho từng thiết bị (vì mỗi handover chỉ handle 1 device)
        for device in devices:
            return_handover = DeviceHandover(
                handover_date=datetime.now().date(),
                device_id=device.id,
                giver_id=user_id,  # Người giao là nhân viên nghỉ việc
                receiver_id=current_user_id,  # Người nhận là admin hiện tại
                device_condition=device.condition or 'Sử dụng bình thường',
                reason='Nhân viên nghỉ việc - Trả thiết bị về kho',
                location='Kho thiết bị',
                notes=f'Tự động tạo khi nhân viên {user.full_name or user.username} nghỉ việc'
            )
            db.session.add(return_handover)
            
            # Cập nhật trạng thái thiết bị về "Sẵn sàng"
            device.status = 'Sẵn sàng'
            device.manager_id = None
            device.assign_date = None
            
            handovers_created += 1
        
        db.session.commit()
        print(f"Created {handovers_created} return handovers for user {user_id}")
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error creating return handover: {e}")
        return False

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        old = {
            'full_name': user.full_name,
            'email': user.email,
            'date_of_birth': user.date_of_birth,
            'role': user.role,
            'department': user.department_info.name if user.department_info else None,
            'position': user.position,
            'phone_number': user.phone_number,
            'notes': user.notes,
            'status': user.status,
            'onboard_date': user.onboard_date,
            'offboard_date': user.offboard_date,
        }
        user.full_name = request.form.get('full_name')
        if user.full_name:
            try:
                user.last_name_token = (str(user.full_name).strip().split()[-1] or '').lower()
            except Exception:
                user.last_name_token = None
        user.email = request.form.get('email')
        user.date_of_birth = datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d').date() if request.form.get('date_of_birth') else None
        user.role = request.form.get('role')
        # Handle department_id instead of department string
        department_id = request.form.get('department_id')
        if department_id:
            user.department_id = department_id
        else:
            user.department_id = None
        user.position = request.form.get('position')
        user.phone_number = request.form.get('phone_number')
        user.notes = request.form.get('notes')
        
        new_status = request.form.get('status')
        old_status = user.status
        user.status = new_status
        user.onboard_date = datetime.strptime(request.form['onboard_date'], '%Y-%m-%d').date() if request.form.get('onboard_date') else None
        user.offboard_date = datetime.strptime(request.form['offboard_date'], '%Y-%m-%d').date() if request.form.get('offboard_date') else None

        new_password = request.form.get('password')
        if new_password:
            user.password = generate_password_hash(new_password)
        
        # Xử lý nghỉ việc - tự động tạo phiếu trả thiết bị
        if new_status == 'Nghỉ việc' and old_status != 'Nghỉ việc':
            success = create_return_handover_for_user(user_id, session.get('user_id'))
            if success:
                flash('Cập nhật thông tin người dùng thành công! Đã tự động tạo phiếu trả thiết bị về kho.', 'success')
            else:
                flash('Cập nhật thông tin người dùng thành công! Tuy nhiên có lỗi khi tạo phiếu trả thiết bị.', 'warning')
        else:
            flash('Cập nhật thông tin người dùng thành công!', 'success')
            
        db.session.commit()
        new = {
            'full_name': user.full_name,
            'email': user.email,
            'date_of_birth': user.date_of_birth,
            'role': user.role,
            'department': user.department_info.name if user.department_info else None,
            'position': user.position,
            'phone_number': user.phone_number,
            'notes': user.notes,
            'status': user.status,
            'onboard_date': user.onboard_date,
            'offboard_date': user.offboard_date,
        }
        _log_audit('user', user.id, old, new)
        return redirect(url_for('user_list'))
    departments = Department.query.all()
    return render_template('edit_user.html', user=user, departments=departments)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    if user.given_handovers.count() > 0 or user.received_handovers.count() > 0:
        flash(f'Không thể xóa người dùng "{user.full_name}" vì họ đã có lịch sử bàn giao thiết bị.', 'danger')
        return redirect(url_for('user_list'))
    if user_id == session.get('user_id'):
        flash('Bạn không thể tự xóa tài khoản của mình.', 'danger')
        return redirect(url_for('user_list'))
        
    db.session.delete(user)
    db.session.commit()
    flash('Xóa người dùng thành công!', 'success')
    return redirect(url_for('user_list'))

# --- API Routes ---
# Xem thông tin người dùng
@app.route('/user/<int:user_id>')
def user_detail(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    # Thiết bị đang quản lý
    devices = Device.query.filter_by(manager_id=user.id).order_by(Device.device_code).all()
    # Lịch sử bàn giao liên quan
    given = DeviceHandover.query.filter_by(giver_id=user.id).order_by(DeviceHandover.handover_date.desc()).all()
    received = DeviceHandover.query.filter_by(receiver_id=user.id).order_by(DeviceHandover.handover_date.desc()).all()
    return render_template('user_detail.html', user=user, devices=devices, given=given, received=received)

# --- CẬP NHẬT API ĐỂ TRẢ VỀ THÊM SERIAL NUMBER ---
@app.route('/api/device_info/<int:device_id>')
def device_info(device_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    device = Device.query.get(device_id)
    if device:
        return jsonify({
            'id': device.id, 
            'name': device.name, 
            'device_code': device.device_code,
            'serial_number': device.serial_number or 'N/A' # Thêm serial number
        })
    return jsonify({'error': 'Device not found'}), 404

# --- Import/Export Routes ---
# ... (Các hàm import/export giữ nguyên) ...
@app.route('/import_devices', methods=['GET', 'POST'])
def import_devices():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ.', 'danger')
            return redirect(url_for('import_devices'))
        
        try:
            df = pd.read_excel(file, engine='openpyxl')
            expected_columns = [
                'Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Số serial', 'Ngày mua', 'Giá mua', 'Người mua',
                'Ngày nhập', 'Tình trạng', 'Trạng thái', 'Người quản lý', 'Ngày cấp phát',
                'Cấu hình', 'Ghi chú', 'Người nhập', 'Thương hiệu', 'Nhà cung cấp', 'Bảo hành'
            ]
            if not all(col in df.columns for col in expected_columns):
                flash('File Excel thiếu một hoặc nhiều cột bắt buộc. Vui lòng kiểm tra lại tiêu đề các cột.', 'danger')
                return redirect(url_for('import_devices'))
            
            valid_conditions = ['Mới', 'Sử dụng bình thường', 'Cần bảo trì', 'Hỏng']
            valid_statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì']
            valid_device_types = [
                'Laptop', 'Case máy tính', 'Màn hình', 'Bàn phím', 'Chuột', 'Ổ cứng',
                'Ram', 'Card màn hình', 'Máy in', 'Thiết bị mạng', 'Server', 'Thiết bị khác'
            ]
            
            devices_to_add = []
            errors = []

            for index, row in df.iterrows():
                manager_id = None
                
                if not all(pd.notna(row[col]) for col in ['Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Tình trạng', 'Trạng thái']):
                    errors.append(f'Dòng {index+2}: Thiếu thông tin ở các cột bắt buộc.')
                    continue
                if Device.query.filter_by(device_code=row['Mã thiết bị']).first():
                    errors.append(f'Dòng {index+2}: Mã thiết bị {row["Mã thiết bị"]} đã tồn tại.')
                    continue
                if row['Loại thiết bị'] not in valid_device_types:
                    errors.append(f'Dòng {index+2}: Loại thiết bị "{row["Loại thiết bị"]}" không hợp lệ.')
                    continue

                if pd.notna(row['Người quản lý']) and row['Người quản lý']:
                    manager = User.query.filter_by(full_name=row['Người quản lý']).first()
                    if not manager:
                        errors.append(f'Dòng {index+2}: Người quản lý {row["Người quản lý"]} không tồn tại.')
                        continue
                    manager_id = manager.id
                
                try:
                    purchase_date = pd.to_datetime(row['Ngày mua']).date() if pd.notna(row['Ngày mua']) else None
                    import_date = pd.to_datetime(row['Ngày nhập']).date() if pd.notna(row['Ngày nhập']) else None
                    assign_date = pd.to_datetime(row['Ngày cấp phát']).date() if pd.notna(row['Ngày cấp phát']) else None
                except ValueError:
                    errors.append(f'Dòng {index+2}: Định dạng ngày không hợp lệ.'); continue
                
                new_device = Device(
                    device_code=row['Mã thiết bị'], name=row['Tên thiết bị'], device_type=row['Loại thiết bị'],
                    serial_number=row.get('Số serial'),
                    purchase_date=purchase_date,
                    import_date=import_date,
                    condition=row['Tình trạng'], status=row['Trạng thái'],
                    manager_id=manager_id,
                    assign_date=assign_date,
                    configuration=row.get('Cấu hình'),
                    notes=row.get('Ghi chú'),
                    buyer=row.get('Người mua'),
                    importer=row.get('Người nhập'),
                    brand=row.get('Thương hiệu'),
                    supplier=row.get('Nhà cung cấp'),
                    warranty=row.get('Bảo hành'),
                    purchase_price=pd.to_numeric(row.get('Giá mua'), errors='coerce')
                )
                devices_to_add.append(new_device)
            
            if errors:
                for error in errors:
                    flash(error, 'danger')
                db.session.rollback()
            else:
                db.session.add_all(devices_to_add)
                db.session.commit()
                flash('Nhập thiết bị từ Excel thành công!', 'success')
                return redirect(url_for('device_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi không xác định khi xử lý file: {str(e)}', 'danger')
            
    return render_template('import_devices.html')

@app.route('/export_devices_excel')
def export_devices_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    devices = Device.query.order_by(Device.device_code).all()
    data = []
    for device in devices:
        data.append({
            'Mã thiết bị': device.device_code, 'Tên thiết bị': device.name, 'Loại thiết bị': device.device_type,
            'Số serial': device.serial_number or '', 
            'Ngày mua': device.purchase_date.strftime('%d-%m-%Y') if device.purchase_date else '',
            'Giá mua': device.purchase_price,
            'Người mua': device.buyer or '',
            'Ngày nhập': device.import_date.strftime('%d-%m-%Y') if device.import_date else '', 'Tình trạng': device.condition,
            'Trạng thái': device.status, 'Người quản lý': device.manager.full_name if device.manager else '',
            'Ngày cấp phát': device.assign_date.strftime('%d-%m-%Y') if device.assign_date else '',
            'Cấu hình': device.configuration or '', 'Ghi chú': device.notes or '',
            'Người nhập': device.importer or '', 'Thương hiệu': device.brand or '', 'Nhà cung cấp': device.supplier or '',
            'Bảo hành': device.warranty or ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Devices')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'devices_list_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/download/maintenance/<int:log_id>/<path:filename>')
def download_maintenance_file(log_id, filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.download' not in (current_permissions or set()):
        flash('Bạn không có quyền tải tệp.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    directory = os.path.join(instance_path, 'maintenance_attachments', str(log_id))
    return send_from_directory(directory, filename, as_attachment=True)

@app.route('/import_users', methods=['GET', 'POST'])
def import_users():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ (.xls, .xlsx).', 'danger')
            return redirect(url_for('import_users'))
        
        try:
            df = pd.read_excel(file, engine='openpyxl')
            required_columns = ['Tên đăng nhập', 'Mật khẩu', 'Họ và tên', 'Email', 'Vai trò']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_users'))

            errors = []
            users_to_add = []
            
            for index, row in df.iterrows():
                username = str(row['Tên đăng nhập'])
                password = str(row['Mật khẩu'])
                email = str(row['Email'])

                if not username or not password or not email:
                    errors.append(f'Dòng {index + 2}: Tên đăng nhập, Mật khẩu, và Email không được để trống.')
                    continue
                if User.query.filter_by(username=username).first():
                    errors.append(f'Dòng {index + 2}: Tên đăng nhập "{username}" đã tồn tại.')
                    continue
                if User.query.filter_by(email=email).first():
                    errors.append(f'Dòng {index + 2}: Email "{email}" đã tồn tại.')
                    continue
                
                onboard_date_val = row.get('Ngày Onboard')
                offboard_date_val = row.get('Ngày Offboard')

                dept_name = row.get('Phòng ban')
                dept = None
                if pd.notna(dept_name) and str(dept_name).strip() != '':
                    dept = Department.query.filter_by(name=str(dept_name).strip()).first()

                new_user = User(
                    username=username,
                    password=generate_password_hash(password),
                    full_name=row.get('Họ và tên'),
                    email=email,
                    role=row.get('Vai trò', 'user'),
                    department_id=(dept.id if dept else None),
                    position=row.get('Chức vụ'),
                    phone_number=str(row.get('SĐT', '')) if pd.notna(row.get('SĐT')) else None,
                    notes=row.get('Ghi chú'),
                    status=row.get('Trạng thái', 'Đang làm'),
                    onboard_date=pd.to_datetime(onboard_date_val).date() if pd.notna(onboard_date_val) else None,
                    offboard_date=pd.to_datetime(offboard_date_val).date() if pd.notna(offboard_date_val) else None
                )
                if new_user.full_name:
                    try:
                        new_user.last_name_token = (str(new_user.full_name).strip().split()[-1] or '').lower()
                    except Exception:
                        new_user.last_name_token = None
                users_to_add.append(new_user)

            if errors:
                for error in errors:
                    flash(error, 'danger')
            else:
                db.session.add_all(users_to_add)
                db.session.commit()
                flash(f'Đã nhập thành công {len(users_to_add)} người dùng mới!', 'success')
                return redirect(url_for('user_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi trong quá trình xử lý file: {str(e)}', 'danger')

    return render_template('import_users.html')

@app.route('/export_users_excel')
def export_users_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    users = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    data = []
    for user in users:
        data.append({
            'ID': user.id,
            'Tên đăng nhập': user.username,
            'Họ và tên': user.full_name,
            'Email': user.email,
            'Phòng ban': user.department_info.name if user.department_info else None,
            'Chức vụ': user.position,
            'Trạng thái': user.status,
            'Ngày Onboard': user.onboard_date.strftime('%d-%m-%Y') if user.onboard_date else '',
            'Ngày Offboard': user.offboard_date.strftime('%d-%m-%Y') if user.offboard_date else '',
            'SĐT': user.phone_number,
            'Ngày sinh': user.date_of_birth.strftime('%d-%m-%Y') if user.date_of_birth else '',
            'Vai trò': user.role,
            'Ngày tạo': user.created_at.strftime('%d-%m-%Y %H:%M:%S'),
            'Đăng nhập lần cuối': user.last_login.strftime('%d-%m-%Y %H:%M:%S') if user.last_login else ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Users')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'users_list_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/maintenance_logs')
def maintenance_logs():
    if 'user_id' not in session: return redirect(url_for('login'))
    # permission check
    if 'maintenance.view' not in (current_permissions or set()):
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    device_code = request.args.get('device_code', '').strip()
    status = request.args.get('status', '').strip()

    query = DeviceMaintenanceLog.query.join(Device)
    if device_code:
        query = query.filter(Device.device_code.ilike(f"%{device_code}%"))
    if status:
        query = query.filter(DeviceMaintenanceLog.status.ilike(f"%{status}%"))

    logs = query.order_by(DeviceMaintenanceLog.log_date.desc(), DeviceMaintenanceLog.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('maintenance_logs/list.html', logs=logs, device_code=device_code, status=status)

@app.route('/maintenance_logs/add', methods=['GET', 'POST'])
def add_maintenance_log():
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.add' not in (current_permissions or set()):
        flash('Bạn không có quyền thêm nhật ký.', 'danger')
        return redirect(url_for('maintenance_logs'))
    if request.method == 'POST':
        device_id = request.form.get('device_id')
        log_date_str = request.form.get('log_date')
        condition = request.form.get('condition')
        issue = request.form.get('issue')
        status = request.form.get('status')
        last_action = request.form.get('last_action')
        notes = request.form.get('notes')

        try:
            log_date = datetime.strptime(log_date_str, '%Y-%m-%d').date() if log_date_str else date.today()
            new_log = DeviceMaintenanceLog(
                device_id=device_id,
                log_date=log_date,
                condition=condition,
                issue=issue,
                status=status,
                last_action=last_action,
                notes=notes
            )
            db.session.add(new_log)
            db.session.commit()
            flash('Đã thêm nhật ký bảo trì.', 'success')
            return redirect(url_for('maintenance_logs'))
        except Exception as e:
            db.session.rollback()
            flash('Có lỗi xảy ra khi thêm nhật ký.', 'danger')
    devices = Device.query.order_by(Device.device_code).all()
    return render_template('maintenance_logs/add.html', devices=devices)

@app.route('/maintenance_logs/<int:log_id>')
def maintenance_log_detail(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.view' not in (current_permissions or set()):
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    device = log.device
    all_logs = DeviceMaintenanceLog.query.filter_by(device_id=device.id).order_by(DeviceMaintenanceLog.log_date.asc(), DeviceMaintenanceLog.id.asc()).all()
    return render_template('maintenance_logs/detail.html', log=log, device=device, all_logs=all_logs)

@app.route('/maintenance_logs/<int:log_id>/edit', methods=['GET', 'POST'])
def edit_maintenance_log(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.edit' not in (current_permissions or set()):
        flash('Bạn không có quyền sửa nhật ký.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    if request.method == 'POST':
        try:
            log_date_str = request.form.get('log_date')
            log.log_date = datetime.strptime(log_date_str, '%Y-%m-%d').date() if log_date_str else log.log_date
            log.condition = request.form.get('condition')
            log.issue = request.form.get('issue')
            log.status = request.form.get('status')
            log.last_action = request.form.get('last_action')
            log.notes = request.form.get('notes')
            db.session.commit()
            flash('Đã cập nhật nhật ký.', 'success')
            return redirect(url_for('maintenance_log_detail', log_id=log.id))
        except Exception:
            db.session.rollback()
            flash('Có lỗi xảy ra khi cập nhật.', 'danger')
    devices = Device.query.order_by(Device.device_code).all()
    return render_template('maintenance_logs/edit.html', log=log, devices=devices)

@app.route('/maintenance_logs/<int:log_id>/delete', methods=['POST'])
def delete_maintenance_log(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.delete' not in (current_permissions or set()):
        flash('Bạn không có quyền xóa nhật ký.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    try:
        # delete attachments files on disk if exist
        for att in list(log.attachments):
            try:
                if att.file_path and os.path.exists(att.file_path):
                    os.remove(att.file_path)
            except Exception:
                pass
            db.session.delete(att)
        db.session.delete(log)
        db.session.commit()
        flash('Đã xóa nhật ký.', 'success')
    except Exception:
        db.session.rollback()
        flash('Không thể xóa nhật ký.', 'danger')
    return redirect(url_for('maintenance_logs'))

@app.route('/maintenance_logs/<int:log_id>/attachments', methods=['POST'])
def upload_maintenance_attachments(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.upload' not in (current_permissions or set()):
        flash('Bạn không có quyền tải tệp.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    files = request.files.getlist('files')
    saved = 0
    upload_dir = os.path.join(instance_path, 'maintenance_attachments', str(log_id))
    os.makedirs(upload_dir, exist_ok=True)
    try:
        for f in files:
            if not f or not f.filename:
                continue
            filename = f.filename
            # naive secure-ish name
            filename = filename.replace('..','_').replace('/','_').replace('\\','_')
            dest = os.path.join(upload_dir, filename)
            f.save(dest)
            db.session.add(DeviceMaintenanceAttachment(log_id=log.id, file_name=filename, file_path=dest))
            saved += 1
        db.session.commit()
        if saved:
            flash(f'Đã tải lên {saved} tệp.', 'success')
        else:
            flash('Không có tệp nào được tải lên.', 'info')
    except Exception:
        db.session.rollback()
        flash('Lỗi khi tải tệp.', 'danger')
    return redirect(url_for('maintenance_log_detail', log_id=log.id))

@app.route('/export_handovers_excel')
def export_handovers_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    handovers = DeviceHandover.query.order_by(DeviceHandover.handover_date.desc()).all()
    data = []
    for handover in handovers:
        data.append({'Ngày Bàn Giao': handover.handover_date.strftime('%d-%m-%Y'), 'Mã Thiết Bị': handover.device.device_code if handover.device else '', 'Tên Thiết Bị': handover.device.name if handover.device else '', 'Loại Thiết Bị': handover.device_type if handover.device else '', 'Người Giao': handover.giver.full_name if handover.giver else '', 'Người Nhận': handover.receiver.full_name if handover.receiver else '', 'Phòng ban Người Nhận': (handover.receiver.department_info.name if handover.receiver and handover.receiver.department_info else ''), 'Tình Trạng Thiết Bị': handover.device_condition, 'Lý Do': handover.reason, 'Nơi Đặt': handover.location, 'Ghi Chú': handover.notes})
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Handovers')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'handover_history_{datetime.now().strftime("%Y%m%d")}.xlsx')

# --- Configuration Proposal Routes ---
@app.route('/config_proposals')
def config_proposals():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    q = ConfigProposal.query
    filter_name = request.args.get('name', '').strip()
    filter_unit = request.args.get('unit', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    
    if filter_name:
        q = q.filter(ConfigProposal.name.ilike(f"%{filter_name}%"))
    if filter_unit:
        q = q.filter(ConfigProposal.proposer_unit.ilike(f"%{filter_unit}%"))
    if start_date:
        try:
            dt = datetime.strptime(start_date, '%Y-%m-%d')
            q = q.filter(ConfigProposal.proposal_date >= dt)
        except ValueError:
            pass
    if end_date:
        try:
            dt2 = datetime.strptime(end_date, '%Y-%m-%d')
            q = q.filter(ConfigProposal.proposal_date <= dt2)
        except ValueError:
            pass
    
    proposals_pagination = q.order_by(ConfigProposal.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('config_proposals.html', proposals=proposals_pagination, filter_name=filter_name, filter_unit=filter_unit, start_date=start_date, end_date=end_date)

@app.route('/config_proposals/add', methods=['GET', 'POST'])
def add_config_proposal():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            proposal_date_str = request.form.get('proposal_date')
            proposer_name = request.form.get('proposer_name')
            proposer_unit = request.form.get('proposer_unit')
            scope = request.form.get('scope')
            vat_percent = request.form.get('vat_percent', type=float) or 10.0
            currency = request.form.get('currency') or 'VND'
            status = request.form.get('status') or 'Mới tạo'
            purchase_status = request.form.get('purchase_status') or 'Lấy báo giá'
            notes = request.form.get('notes')
            supplier_info_hdr = request.form.get('supplier_info')

            if not name or not proposal_date_str:
                flash('Vui lòng nhập Tên đề xuất và Ngày đề xuất.', 'danger')
                return redirect(url_for('add_config_proposal'))

            proposal_date = datetime.strptime(proposal_date_str, '%Y-%m-%d').date()

            proposal = ConfigProposal(
                name=name,
                proposal_date=proposal_date,
                proposer_name=proposer_name,
                proposer_unit=proposer_unit,
                scope=scope,
                vat_percent=vat_percent,
                currency=currency,
                status=status,
                purchase_status=purchase_status,
                notes=notes,
                supplier_info=supplier_info_hdr
            )
            db.session.add(proposal)
            db.session.flush()

            subtotal = 0.0
            rows = int(request.form.get('rows_count', 8))
            for i in range(rows):
                prefix = f'rows[{i}]'
                product_name = request.form.get(f'{prefix}[product_name]')
                product_link = request.form.get(f'{prefix}[product_link]')
                product_code = request.form.get(f'{prefix}[product_code]')
                warranty = request.form.get(f'{prefix}[warranty]')
                quantity = request.form.get(f'{prefix}[quantity]', type=int) or 0
                unit_price = request.form.get(f'{prefix}[unit_price]', type=float) or 0.0
                if not product_name and quantity == 0 and unit_price == 0.0:
                    continue
                line_total = max(0, quantity) * max(0.0, unit_price)
                subtotal += line_total
                db.session.add(ConfigProposalItem(
                    proposal_id=proposal.id,
                    order_no=i + 1,
                    product_name=product_name,
                    product_link=product_link,
                    product_code=product_code,
                    warranty=warranty,
                    quantity=max(0, quantity),
                    unit_price=max(0.0, unit_price),
                    line_total=line_total
                ))

            proposal.subtotal = subtotal
            proposal.vat_amount = round(subtotal * (vat_percent / 100.0), 2)
            proposal.total_amount = round(subtotal + proposal.vat_amount, 2)
            db.session.commit()
            flash('Tạo đề xuất cấu hình thiết bị thành công.', 'success')
            return redirect(url_for('config_proposals'))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi tạo đề xuất: {str(e)}', 'danger')
            return redirect(url_for('add_config_proposal'))
    # GET
    default_date = datetime.utcnow().strftime('%Y-%m-%d')
    return render_template('add_config_proposal.html', default_date=default_date)

@app.route('/config_proposals/<int:proposal_id>')
def config_proposal_detail(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    items = ConfigProposalItem.query.filter_by(proposal_id=proposal_id).order_by(ConfigProposalItem.order_no).all()
    return render_template('config_proposal_detail.html', p=p, items=items)

@app.route('/config_proposals/<int:proposal_id>/delete', methods=['POST'])
def delete_config_proposal(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    # cascade will remove items
    db.session.delete(p)
    db.session.commit()
    flash('Đã xóa đề xuất.', 'success')
    return redirect(url_for('config_proposals'))

@app.route('/config_proposals/<int:proposal_id>/clone', methods=['POST'])
def clone_config_proposal(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    new_p = ConfigProposal(
        name=f"{p.name} (bản sao)",
        proposal_date=p.proposal_date,
        proposer_name=p.proposer_name,
        proposer_unit=p.proposer_unit,
        scope=p.scope,
        currency=p.currency,
        status='Mới tạo',
        subtotal=p.subtotal,
        vat_percent=p.vat_percent,
        vat_amount=p.vat_amount,
        total_amount=p.total_amount
    )
    db.session.add(new_p)
    db.session.flush()
    for it in ConfigProposalItem.query.filter_by(proposal_id=p.id).all():
        db.session.add(ConfigProposalItem(
            proposal_id=new_p.id,
            order_no=it.order_no,
            product_name=it.product_name,
            warranty=it.warranty,
            supplier_info=it.supplier_info,
            quantity=it.quantity,
            unit_price=it.unit_price,
            line_total=it.line_total
        ))
    db.session.commit()
    flash('Đã nhân bản đề xuất.', 'success')
    return redirect(url_for('config_proposals'))

@app.route('/config_proposals/<int:proposal_id>/edit', methods=['GET', 'POST'])
def edit_config_proposal(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    if request.method == 'POST':
        try:
            p.name = request.form.get('name') or p.name
            date_str = request.form.get('proposal_date')
            if date_str:
                p.proposal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            p.proposer_name = request.form.get('proposer_name')
            p.proposer_unit = request.form.get('proposer_unit')
            p.scope = request.form.get('scope')
            p.currency = request.form.get('currency') or 'VND'
            p.status = request.form.get('status') or p.status
            p.purchase_status = request.form.get('purchase_status') or p.purchase_status
            p.notes = request.form.get('notes')
            p.supplier_info = request.form.get('supplier_info')
            p.vat_percent = request.form.get('vat_percent', type=float) or p.vat_percent
            linked_id = request.form.get('linked_receipt_id')
            try:
                p.linked_receipt_id = int(linked_id) if linked_id else None
            except ValueError:
                pass

            # Replace items
            for it in ConfigProposalItem.query.filter_by(proposal_id=p.id).all():
                db.session.delete(it)
            db.session.flush()

            subtotal = 0.0
            rows = int(request.form.get('rows_count', 0))
            for i in range(rows):
                prefix = f'rows[{i}]'
                product_name = request.form.get(f'{prefix}[product_name]')
                product_link = request.form.get(f'{prefix}[product_link]')
                product_code = request.form.get(f'{prefix}[product_code]')
                warranty = request.form.get(f'{prefix}[warranty]')
                quantity = request.form.get(f'{prefix}[quantity]', type=int) or 0
                unit_price = request.form.get(f'{prefix}[unit_price]', type=float) or 0.0
                if not product_name and quantity == 0 and unit_price == 0.0:
                    continue
                line_total = max(0, quantity) * max(0.0, unit_price)
                subtotal += line_total
                db.session.add(ConfigProposalItem(
                    proposal_id=p.id,
                    order_no=i + 1,
                    product_name=product_name,
                    product_link=product_link,
                    product_code=product_code,
                    warranty=warranty,
                    quantity=max(0, quantity),
                    unit_price=max(0.0, unit_price),
                    line_total=line_total
                ))

            p.subtotal = subtotal
            p.vat_amount = round(subtotal * (p.vat_percent / 100.0), 2)
            p.total_amount = round(subtotal + p.vat_amount, 2)
            db.session.commit()
            flash('Đã cập nhật đề xuất.', 'success')
            return redirect(url_for('config_proposal_detail', proposal_id=p.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi cập nhật: {str(e)}', 'danger')
            return redirect(url_for('edit_config_proposal', proposal_id=p.id))
    # GET
    items = ConfigProposalItem.query.filter_by(proposal_id=p.id).order_by(ConfigProposalItem.order_no).all()
    return render_template('edit_config_proposal.html', p=p, items=items)

# --- CLI Commands ---
@app.cli.command("init-db")
def init_db_command():
    """Tạo mới các bảng trong cơ sở dữ liệu."""
    db.create_all()
    click.echo("Đã khởi tạo cơ sở dữ liệu.")

@app.cli.command("create-admin")
def create_admin_command():
    """Tạo tài khoản admin mặc định."""
    if User.query.filter_by(username='admin').first():
        click.echo("Tài khoản admin đã tồn tại.")
        return
    
    # Tạo department IT nếu chưa có
    it_dept = Department.query.filter_by(name='IT').first()
    if not it_dept:
        it_dept = Department(name='IT', description='Phòng Công nghệ Thông tin')
        db.session.add(it_dept)
        db.session.flush()  # Để lấy id của department vừa tạo
    
    admin_user = User(
        username='admin',
        password=generate_password_hash('admin123'),
        full_name='Quản Trị Viên',
        email='admin@example.com',
        role='admin',
        department_id=it_dept.id  # Sử dụng department_id thay vì department
    )
    db.session.add(admin_user)
    
    # Set admin user làm manager của IT department
    it_dept.manager_id = admin_user.id
    
    db.session.commit()
    click.echo("Đã tạo tài khoản admin thành công (Username: admin, Pass: admin123).")

# --- Backup/Restore Routes ---
@app.route('/backup')
def backup_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    return render_template('backup.html')

@app.route('/backup/export')
def backup_export():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        # Tạo file backup
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'backup_inventory_{timestamp}.zip'
        
        # Tạo file tạm
        temp_dir = tempfile.mkdtemp()
        backup_path = os.path.join(temp_dir, backup_filename)
        
        files_added = 0
        
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Backup database
            db_path = os.path.join(instance_path, 'inventory.db')
            if os.path.exists(db_path):
                zipf.write(db_path, 'inventory.db')
                files_added += 1
            else:
                # Create a placeholder file if database doesn't exist
                placeholder_content = "# Database file not found - system may not be initialized yet\n"
                zipf.writestr('database_placeholder.txt', placeholder_content)
                files_added += 1
            
            # Backup upload folder if exists
            upload_path = os.path.join(os.getcwd(), 'upload')
            if os.path.exists(upload_path):
                for root, dirs, files in os.walk(upload_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, os.getcwd())
                        zipf.write(file_path, arcname)
                        files_added += 1
        
        # Check if backup has content
        if files_added == 0:
            flash('Không có dữ liệu để backup. Hệ thống có thể chưa được khởi tạo.', 'warning')
            return redirect(url_for('backup_page'))
        
        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_filename,
            mimetype='application/zip'
        )
    except Exception as e:
        flash(f'Lỗi khi tạo backup: {str(e)}', 'danger')
        return redirect(url_for('backup_page'))

@app.route('/backup/import', methods=['POST'])
def backup_import():
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'backup_file' not in request.files:
        flash('Vui lòng chọn file backup.', 'danger')
        return redirect(url_for('backup_page'))
    
    file = request.files['backup_file']
    if file.filename == '':
        flash('Vui lòng chọn file backup.', 'danger')
        return redirect(url_for('backup_page'))
    
    if not file.filename.endswith('.zip'):
        flash('File backup phải có định dạng .zip', 'danger')
        return redirect(url_for('backup_page'))
    
    try:
        # Lưu file tạm
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename)
        file.save(temp_path)
        
        # Giải nén và restore
        with zipfile.ZipFile(temp_path, 'r') as zipf:
            # Restore database
            if 'inventory.db' in zipf.namelist():
                db_path = os.path.join(instance_path, 'inventory.db')
                # Backup database hiện tại
                if os.path.exists(db_path):
                    backup_db_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    os.rename(db_path, backup_db_path)
                
                # Extract new database
                zipf.extract('inventory.db', instance_path)
            
            # Restore upload folder
            upload_path = os.path.join(os.getcwd(), 'upload')
            for name in zipf.namelist():
                if name.startswith('upload/'):
                    zipf.extract(name, os.getcwd())
        
        # Cleanup
        os.remove(temp_path)
        os.rmdir(temp_dir)
        
        flash('Import backup thành công!', 'success')
        return redirect(url_for('backup_page'))
        
    except Exception as e:
        flash(f'Lỗi khi import backup: {str(e)}', 'danger')
        return redirect(url_for('backup_page'))

# --- Automatic Backup Functions ---
def create_automatic_backup():
    """Tạo backup tự động"""
    try:
        # Sử dụng thời gian GMT+7
        vietnam_time = datetime.now(VIETNAM_TZ)
        timestamp = vietnam_time.strftime('%Y%m%d_%H%M%S')
        backup_filename = f'auto_backup_{timestamp}.zip'
        backup_filepath = os.path.join(backup_path, backup_filename)
        
        files_added = 0
        
        with zipfile.ZipFile(backup_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Backup database
            db_path = os.path.join(instance_path, 'inventory.db')
            if os.path.exists(db_path):
                zipf.write(db_path, 'inventory.db')
                files_added += 1
                print(f"Added database to backup: {db_path}")
            else:
                print(f"Database file not found: {db_path}")
            
            # Backup upload folder if exists
            upload_path = os.path.join(os.getcwd(), 'upload')
            if os.path.exists(upload_path):
                for root, dirs, files in os.walk(upload_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, os.getcwd())
                        zipf.write(file_path, arcname)
                        files_added += 1
                print(f"Added {files_added - 1} files from upload folder")
            else:
                print(f"Upload folder not found: {upload_path}")
        
        # Check if backup file was created and has content
        if os.path.exists(backup_filepath):
            file_size = os.path.getsize(backup_filepath)
            print(f"Automatic backup created: {backup_filename} ({file_size} bytes, {files_added} files)")
            if file_size == 0:
                print("WARNING: Backup file is empty!")
                os.remove(backup_filepath)  # Remove empty backup
                return None
            return backup_filename
        else:
            print("ERROR: Backup file was not created")
            return None
            
    except Exception as e:
        print(f"Error creating automatic backup: {str(e)}")
        return None

def cleanup_old_backups():
    """Xóa backup cũ hơn 30 ngày"""
    try:
        # Sử dụng thời gian GMT+7
        vietnam_time = datetime.now(VIETNAM_TZ)
        cutoff_date = vietnam_time - timedelta(days=30)
        for filename in os.listdir(backup_path):
            if filename.startswith('auto_backup_') and filename.endswith('.zip'):
                filepath = os.path.join(backup_path, filename)
                file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                # Convert to Vietnam timezone for comparison
                file_time_vietnam = pytz.utc.localize(file_time).astimezone(VIETNAM_TZ)
                if file_time_vietnam < cutoff_date:
                    os.remove(filepath)
                    print(f"Deleted old backup: {filename}")
    except Exception as e:
        print(f"Error cleaning up old backups: {str(e)}")

def backup_scheduler():
    """Chạy scheduler cho backup tự động"""
    while True:
        # Get current Vietnam time
        vietnam_time = datetime.now(VIETNAM_TZ)
        current_time = vietnam_time.strftime('%H:%M')
        
        # Check if it's time for daily backup (configurable time Vietnam time)
        if backup_config_daily_enabled and current_time == backup_config_daily_time:
            create_automatic_backup()
        
        # Check if it's time for weekly backup (configurable time Vietnam time on Sunday)
        if backup_config_weekly_enabled and current_time == backup_config_weekly_time and vietnam_time.weekday() == 6:  # Sunday
            create_automatic_backup()
        
        # Check if it's time for cleanup (4 AM Vietnam time)
        if current_time == "04:00":
            cleanup_old_backups()
        
        time.sleep(60)  # Check every minute

# Setup automatic backup schedule
schedule.every().day.at("02:00").do(create_automatic_backup)  # Daily at 2 AM
schedule.every().sunday.at("03:00").do(create_automatic_backup)  # Weekly on Sunday at 3 AM
schedule.every().day.at("04:00").do(cleanup_old_backups)  # Cleanup at 4 AM

# Start backup scheduler in background thread
backup_thread = threading.Thread(target=backup_scheduler, daemon=True)
backup_thread.start()

@app.route('/backup/config', methods=['GET', 'POST'])
def backup_config():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Update backup configuration
        daily_enabled = request.form.get('daily_enabled') == 'on'
        weekly_enabled = request.form.get('weekly_enabled') == 'on'
        daily_time = request.form.get('daily_time', '02:00')
        weekly_time = request.form.get('weekly_time', '03:00')
        
        # Store backup configuration in database or config file
        # For now, we'll use a simple approach with global variables
        # In production, you might want to store this in a config table
        global backup_config_daily_enabled, backup_config_weekly_enabled, backup_config_daily_time, backup_config_weekly_time
        backup_config_daily_enabled = daily_enabled
        backup_config_weekly_enabled = weekly_enabled
        backup_config_daily_time = daily_time
        backup_config_weekly_time = weekly_time
        
        # Fix DB permissions after config change
        try:
            import subprocess
            import getpass
            username = getpass.getuser()
            db_path = os.path.join(instance_path, 'inventory.db')
            if os.path.exists(db_path):
                subprocess.run(['sudo', 'chown', f'{username}:www-data', db_path], check=True)
                subprocess.run(['sudo', 'chmod', '664', db_path], check=True)
        except Exception as e:
            print(f"Warning: Could not fix DB permissions: {e}")
        
        flash('Cấu hình backup tự động đã được cập nhật! (Thời gian theo GMT+7)', 'success')
        return redirect(url_for('backup_config'))
    
    # GET - show current configuration
    # Use global backup configuration variables
    daily_enabled = backup_config_daily_enabled
    weekly_enabled = backup_config_weekly_enabled
    daily_time = backup_config_daily_time
    weekly_time = backup_config_weekly_time
    
    # Get current Vietnam time for display
    current_time = datetime.now(VIETNAM_TZ).strftime('%H:%M')
    current_date = datetime.now(VIETNAM_TZ).strftime('%d/%m/%Y')
    
    return render_template('backup_config.html', 
                         daily_enabled=daily_enabled,
                         weekly_enabled=weekly_enabled,
                         daily_time=daily_time,
                         weekly_time=weekly_time,
                         current_time=current_time,
                         current_date=current_date)

@app.route('/backup/list')
def backup_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    backups = []
    try:
        for filename in os.listdir(backup_path):
            if filename.endswith('.zip'):
                filepath = os.path.join(backup_path, filename)
                file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                file_size = os.path.getsize(filepath)
                backups.append({
                    'filename': filename,
                    'created': file_time,
                    'size': file_size,
                    'type': 'auto' if filename.startswith('auto_backup_') else 'manual'
                })
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x['created'], reverse=True)
    except Exception as e:
        flash(f'Lỗi khi lấy danh sách backup: {str(e)}', 'danger')
        backups = []
    
    return render_template('backup_list.html', backups=backups)

@app.route('/backup/restore/<filename>')
def backup_restore_from_file(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        backup_filepath = os.path.join(backup_path, filename)
        if not os.path.exists(backup_filepath):
            flash('File backup không tồn tại.', 'danger')
            return redirect(url_for('backup_list'))
        
        # Backup current database
        db_path = os.path.join(instance_path, 'inventory.db')
        if os.path.exists(db_path):
            backup_db_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(db_path, backup_db_path)
        
        # Restore from backup
        with zipfile.ZipFile(backup_filepath, 'r') as zipf:
            if 'inventory.db' in zipf.namelist():
                zipf.extract('inventory.db', instance_path)
            
            # Restore upload folder
            upload_path = os.path.join(os.getcwd(), 'upload')
            for name in zipf.namelist():
                if name.startswith('upload/'):
                    zipf.extract(name, os.getcwd())
        
        flash(f'Khôi phục backup từ {filename} thành công!', 'success')
        return redirect(url_for('backup_list'))
        
    except Exception as e:
        flash(f'Lỗi khi khôi phục backup: {str(e)}', 'danger')
        return redirect(url_for('backup_list'))

@app.route('/backup/delete/<filename>', methods=['POST'])
def backup_delete(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        backup_filepath = os.path.join(backup_path, filename)
        if os.path.exists(backup_filepath):
            os.remove(backup_filepath)
            flash(f'Đã xóa file backup {filename} thành công!', 'success')
        else:
            flash('File backup không tồn tại.', 'danger')
    except Exception as e:
        flash(f'Lỗi khi xóa file backup: {str(e)}', 'danger')
    
    return redirect(url_for('backup_list'))

@app.route('/api/group_devices/<int:group_id>')
def api_group_devices(group_id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    group = DeviceGroup.query.get_or_404(group_id)
    device_links = DeviceGroupDevice.query.filter_by(group_id=group_id).all()
    devices = []
    for link in device_links:
        device = Device.query.get(link.device_id)
        if device:
            devices.append({
                'id': device.id,
                'device_code': device.device_code,
                'name': device.name,
                'device_type': device.device_type,
                'serial_number': device.serial_number or ''
            })
    return jsonify({'devices': devices})

if __name__ == '__main__':
    app.run(debug=True)