import xml.etree.ElementTree as ET
import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory, make_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import aliased
from sqlalchemy import or_, and_, not_, func, event, text, inspect, case, cast, String, Date
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import pandas as pd
import io
import csv
import math
import click
import json
import sqlite3
import tempfile
import zipfile
import schedule
import threading
import time
import pytz
import re
import unicodedata
from contextlib import contextmanager
from config import config, get_database_info, is_external_database
from backup_restore import DatabaseBackup

# --- Cấu hình ứng dụng ---
instance_path = os.environ.get('INVENTORY_INSTANCE_DIR') or os.path.join(os.getcwd(), 'instance')
os.makedirs(instance_path, exist_ok=True)

# Backup configuration
backup_path = os.environ.get('INVENTORY_BACKUP_DIR') or os.path.join(os.getcwd(), 'backups')
os.makedirs(backup_path, exist_ok=True)

# Attachment directories
os.makedirs(os.path.join(instance_path, 'bug_report_attachments'), exist_ok=True)
os.makedirs(os.path.join(instance_path, 'maintenance_attachments'), exist_ok=True)
os.makedirs(os.path.join(instance_path, 'proposal_attachments'), exist_ok=True)


# Timezone configuration (GMT+7)
VIETNAM_TZ = pytz.timezone('Asia/Ho_Chi_Minh')

def get_now():
    """Lấy thời gian hiện tại theo múi giờ Việt Nam"""
    return datetime.now(VIETNAM_TZ)

# Backup configuration variables
backup_config_daily_enabled = True
backup_config_weekly_enabled = True
backup_config_daily_time = "02:00"
backup_config_weekly_time = "03:00"

# Load persisted backup configuration if available
_backup_cfg_path = os.path.join(instance_path, 'backup_config.json')
try:
    if os.path.exists(_backup_cfg_path):
        with open(_backup_cfg_path, 'r', encoding='utf-8') as f:
            _cfg = json.load(f)
            backup_config_daily_enabled = bool(_cfg.get('daily_enabled', backup_config_daily_enabled))
            backup_config_weekly_enabled = bool(_cfg.get('weekly_enabled', backup_config_weekly_enabled))
            backup_config_daily_time = _cfg.get('daily_time', backup_config_daily_time)
            backup_config_weekly_time = _cfg.get('weekly_time', backup_config_weekly_time)
except Exception:
    pass

# Load persisted DB configuration if available
_db_cfg_path = os.path.join(instance_path, 'db_config.json')
_db_config_custom_url = None
_db_config_secondary_url = None
try:
    if os.path.exists(_db_cfg_path):
        with open(_db_cfg_path, 'r', encoding='utf-8') as f:
            _db_cfg = json.load(f)
            _db_config_custom_url = _db_cfg.get('database_url')
            _db_config_secondary_url = _db_cfg.get('secondary_database_url')
            if _db_config_custom_url:
                # Override DATABASE_URL if custom config exists
                if _db_config_custom_url.startswith('postgres://'):
                    _db_config_custom_url = _db_config_custom_url.replace('postgres://', 'postgresql://', 1)
            if _db_config_secondary_url:
                if _db_config_secondary_url.startswith('postgres://'):
                    _db_config_secondary_url = _db_config_secondary_url.replace('postgres://', 'postgresql://', 1)
except Exception:
    pass

# Get configuration based on environment
config_name = os.environ.get('FLASK_ENV', 'development')
app = Flask(__name__, instance_path=instance_path)
app.config.from_object(config[config_name])
os.makedirs(os.path.join(app.root_path, 'static', 'uploads', 'devices'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static', 'uploads', 'handovers'), exist_ok=True)

# Override with environment variables if present and normalize postgres scheme
_env_db_url = os.environ.get('DATABASE_URL')
if _env_db_url:
    if _env_db_url.startswith('postgres://'):
        _env_db_url = _env_db_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = _env_db_url
elif _db_config_custom_url:
    # Use custom DB URL from configuration file if no environment variable
    app.config['SQLALCHEMY_DATABASE_URI'] = _db_config_custom_url
app.permanent_session_lifetime = timedelta(days=30)

# Initialize app with configuration (this sets up HTTPS/proxy support in production)
config[config_name].init_app(app)

db = SQLAlchemy(app)

# Load persisted Company configuration
_company_cfg_path = os.path.join(instance_path, 'company_config.json')
company_config_defaults = {
    'company_name': 'CÔNG TY CỔ PHẦN THIẾT BỊ & CÔNG NGHỆ',
    'branch_name': 'Phòng Quản lý Thiết bị',
    'company_address': '',
    'company_tax_id': '',
    'company_email': '',
    'company_phone': '',
    'company_bank_account': ''
}

def get_company_config():
    cfg = dict(company_config_defaults)
    try:
        if os.path.exists(_company_cfg_path):
            with open(_company_cfg_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update({k: v for k, v in saved.items() if v is not None})
    except Exception:
        pass
    return cfg

@app.context_processor
def inject_global_template_context():
    perms = _get_current_permissions()
    if 'user_id' in session:
        session['permissions'] = list(perms)
    return {
        'company_config': get_company_config(),
        'current_permissions': perms,
        'user_permissions': perms
    }

# Permission catalogue
PERMISSIONS = [
    # Thiết bị
    ('devices.view', 'Xem danh sách/chi tiết thiết bị'),
    ('devices.edit', 'Thêm/Sửa thiết bị'),
    ('devices.delete', 'Xóa thiết bị'),
    # Bàn giao thiết bị
    ('handovers.view', 'Xem lịch sử/Tạo phiếu bàn giao'),
    ('handovers.edit', 'Sửa phiếu bàn giao'),
    ('handovers.delete', 'Xóa phiếu bàn giao'),
    # Đề xuất thiết bị
    ('config_proposals.view', 'Xem đề xuất thiết bị'),
    ('config_proposals.create', 'Tạo đề xuất thiết bị'),
    ('config_proposals.edit', 'Sửa đề xuất thiết bị'),
    ('config_proposals.delete', 'Xóa đề xuất thiết bị'),
    ('config_proposals.approve_team', 'Duyệt đề xuất (Trưởng bộ phận)'),
    ('config_proposals.consult_it', 'IT lập phương án thiết bị'),
    ('config_proposals.review_finance', 'Kiểm tra ngân sách (Tài chính/Kế toán)'),
    ('config_proposals.approve_director', 'Phê duyệt (Giám đốc)'),
    ('config_proposals.execute_purchase', 'Thực hiện mua sắm (Mua hàng)'),
    ('config_proposals.execute_accounting', 'Thực hiện thanh toán/Hóa đơn (Kế toán)'),
    ('config_proposals.confirm_delivery', 'Xác nhận nhận hàng (Kỷ thuật/Người dùng)'),
    # Người dùng
    ('users.view', 'Xem danh sách/chi tiết người dùng'),
    ('users.edit', 'Thêm/Sửa người dùng, reset mật khẩu'),
    ('users.delete', 'Xóa người dùng'),
    # Phòng ban
    ('departments.view', 'Xem phòng ban'),
    ('departments.edit', 'Thêm/Sửa phòng ban, gán người dùng'),
    ('departments.delete', 'Xóa phòng ban'),
    # Dashboard
    ('dashboard.view', 'Truy cập Dashboard'),
    # Backup
    ('backup.view', 'Xem trang backup'),
    ('backup.edit', 'Cấu hình backup'),
    ('backup.delete', 'Xóa bản backup'),
    # Phân quyền
    ('rbac.view', 'Xem trang phân quyền'),
    ('rbac.edit', 'Chỉnh sửa phân quyền'),
    ('rbac.delete', 'Xóa quyền'),
    ('rbac.manage', 'Quản lý phân quyền (tổng quát)'),
    # Bảo trì (nhật ký sửa chữa thiết bị)
    ('maintenance.view', 'Xem nhật ký bảo trì'),
    ('maintenance.add', 'Thêm nhật ký bảo trì'),
    ('maintenance.edit', 'Sửa nhật ký bảo trì'),
    ('maintenance.delete', 'Xóa nhật ký bảo trì'),
    ('maintenance.upload', 'Tải lên tệp đính kèm'),
    ('maintenance.download', 'Tải xuống tệp đính kèm'),
    # Báo lỗi
    ('bug_reports.create', 'Tạo báo lỗi'),
    ('bug_reports.view', 'Xem báo lỗi'),
    ('bug_reports.edit', 'Sửa/Cập nhật báo lỗi'),
    ('bug_reports.delete', 'Xóa báo lỗi'),
    ('bug_reports.assign', 'Gán báo lỗi cho quản trị viên'),
    ('bug_reports.manage_advanced', 'Quản trị báo lỗi nâng cao'),
    # Tài nguyên (Resource Management)
    ('resources.view', 'Xem danh sách tài nguyên'),
    ('resources.edit', 'Thêm/Sửa tài nguyên'),
    ('resources.delete', 'Xóa tài nguyên'),
    # Vật tư và phụ kiện
    ('stock_items.view', 'Xem vật tư và phụ kiện'),
    ('stock_items.edit', 'Thêm/Sửa mặt hàng, nhập/xuất kho'),
    ('stock_items.delete', 'Xóa mặt hàng và nhóm vật tư'),
    # Chấm công & Máy chấm công Hikvision
    ('attendance.view', 'Xem nhật ký & tổng hợp chấm công cá nhân'),
    ('attendance.view_all', 'Xem toàn bộ dữ liệu chấm công tất cả nhân viên'),
    ('attendance.sync', 'Thực hiện đồng bộ dữ liệu từ máy chấm công Hikvision'),
    ('attendance.manage_users', 'Quản lý danh sách người chấm công (Thêm/Sửa/Xóa/Liên kết)'),
    ('attendance.config', 'Cấu hình kết nối thiết bị Hikvision'),
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
        # Skip SQLite-specific initialization when using external databases (e.g., PostgreSQL)
        if is_external_database():
            return
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
                            description TEXT,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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

                # Create bug report tables if not exists
                try:
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS bug_report (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            title VARCHAR(100) NOT NULL,
                            device_code VARCHAR(50),
                            description TEXT NOT NULL,
                            status VARCHAR(50) DEFAULT 'Mới tạo',
                            priority VARCHAR(50) DEFAULT 'Trung bình',
                            created_by INTEGER NOT NULL REFERENCES user(id),
                            assigned_to INTEGER REFERENCES user(id),
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            resolved_at DATETIME,
                            resolution TEXT
                        )
                    '''))
                    # Add device_code column if table exists but column doesn't
                    try:
                        # Check if column exists by trying to select it
                        conn.execute(text('SELECT device_code FROM bug_report LIMIT 1'))
                    except Exception:
                        # Column doesn't exist, add it
                        try:
                            conn.execute(text('ALTER TABLE bug_report ADD COLUMN device_code VARCHAR(50)'))
                        except Exception:
                            pass  # Column might already exist or table doesn't exist yet
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS bug_report_comment (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            bug_report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
                            comment TEXT NOT NULL,
                            created_by INTEGER NOT NULL REFERENCES user(id),
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    '''))
                    conn.execute(text('''
                        CREATE TABLE IF NOT EXISTS bug_report_attachment (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            bug_report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
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

# Initialize database on startup
init_db()

# --- Database Migration Functions ---
def migrate_bug_report_table():
    """Migrate bug_report table to add device_code column if it doesn't exist"""
    with app.app_context():
        try:
            from sqlalchemy import text, inspect
            
            # Check if bug_report table exists
            try:
                inspector = inspect(db.engine)
                table_names = inspector.get_table_names()
                if 'bug_report' not in table_names:
                    return  # Table doesn't exist yet, will be created by SQLAlchemy
            except Exception:
                # If inspector fails, try direct query
                try:
                    with db.engine.connect() as conn:
                        if is_external_database():
                            result = conn.execute(text("""
                                SELECT EXISTS (
                                    SELECT FROM information_schema.tables 
                                    WHERE table_schema = 'public' AND table_name = 'bug_report'
                                );
                            """))
                            if not result.scalar():
                                return
                        else:
                            result = conn.execute(text("""
                                SELECT name FROM sqlite_master 
                                WHERE type='table' AND name='bug_report';
                            """))
                            if result.fetchone() is None:
                                return
                except Exception:
                    return  # Can't check, skip migration
            
            # Try to add device_code column
            try:
                with db.engine.connect() as conn:
                    conn.execute(text('ALTER TABLE bug_report ADD COLUMN device_code VARCHAR(50)'))
                    conn.commit()
                    print("[OK] Added device_code column to bug_report table")
            except Exception as e:
                error_msg = str(e).lower()
                # Check if error is because column already exists
                if any(keyword in error_msg for keyword in ['already exists', 'duplicate column', 'column "device_code" of relation "bug_report" already exists']):
                    print("[OK] device_code column already exists")
                else:
                    # Other error - might be table doesn't exist or other issue
                    print(f"Migration note: {e}")
        except Exception as e:
            print(f"Migration error (non-critical): {e}")
            # Don't fail app startup if migration fails

def migrate_role_created_at():
    """Ensure role table has created_at column."""
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            columns = {col['name'] for col in inspector.get_columns('role')}
        except Exception:
            columns = set()

        if 'created_at' in columns:
            return

        try:
            with db.engine.connect() as conn:
                dialect = conn.dialect.name
                if dialect == 'postgresql':
                    conn.execute(text("ALTER TABLE role ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
                else:
                    conn.execute(text("ALTER TABLE role ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"))
                conn.commit()
                print("[OK] Added created_at column to role table")
        except Exception as e:
            msg = str(e).lower()
            if 'already exists' in msg or 'duplicate column' in msg:
                print("[OK] created_at column already exists on role table")
            else:
                print(f"Migration note (role created_at): {e}")

def migrate_bug_report_enhancements():
    """Ensure new columns related to bug report workflow exist."""
    with app.app_context():
        try:
            from sqlalchemy import text, inspect

            try:
                inspector = inspect(db.engine)
                if 'bug_report' not in inspector.get_table_names():
                    return
                columns = {col['name'] for col in inspector.get_columns('bug_report')}
            except Exception:
                columns = set()

            if not columns:
                try:
                    with db.engine.connect() as conn:
                        if is_external_database():
                            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'bug_report';"))
                            columns = {row[0] for row in result}
                        else:
                            result = conn.execute(text("PRAGMA table_info(bug_report)"))
                            columns = {row[1] for row in result}
                except Exception:
                    columns = set()

            if not columns:
                return

            with db.engine.connect() as conn:
                def _add_column(sql: str):
                    try:
                        conn.execute(text(sql))
                        conn.commit()
                    except Exception as ex:
                        msg = str(ex).lower()
                        if 'already exists' in msg or 'duplicate column' in msg:
                            return
                        print(f"Migration note: {ex}")

                if 'visibility' not in columns:
                    _add_column("ALTER TABLE bug_report ADD COLUMN visibility VARCHAR(20) DEFAULT 'private'")
                if 'reopen_requested' not in columns:
                    ddl = "ALTER TABLE bug_report ADD COLUMN reopen_requested BOOLEAN DEFAULT FALSE" if is_external_database() else "ALTER TABLE bug_report ADD COLUMN reopen_requested BOOLEAN DEFAULT 0"
                    _add_column(ddl)
                if 'rating' not in columns:
                    _add_column('ALTER TABLE bug_report ADD COLUMN rating INTEGER')
                if 'error_type' not in columns:
                    _add_column("ALTER TABLE bug_report ADD COLUMN error_type VARCHAR(50) DEFAULT 'Thiết bị'")
                if 'merged_into' not in columns:
                    _add_column('ALTER TABLE bug_report ADD COLUMN merged_into INTEGER REFERENCES bug_report(id)')

                # Create bug_report_relations table if it doesn't exist
                try:
                    if is_external_database():
                        conn.execute(text('''
                            CREATE TABLE IF NOT EXISTS bug_report_relations (
                                report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
                                related_report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
                                PRIMARY KEY (report_id, related_report_id)
                            )
                        '''))
                    else:
                        conn.execute(text('''
                            CREATE TABLE IF NOT EXISTS bug_report_relations (
                                report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
                                related_report_id INTEGER NOT NULL REFERENCES bug_report(id) ON DELETE CASCADE,
                                PRIMARY KEY (report_id, related_report_id)
                            )
                        '''))
                    conn.commit()
                except Exception as ex:
                    msg = str(ex).lower()
                    if 'already exists' not in msg and 'duplicate' not in msg:
                        print(f"Migration note (bug_report_relations): {ex}")

                if is_external_database():
                    try:
                        conn.execute(text('ALTER TABLE bug_report ALTER COLUMN device_code TYPE TEXT'))
                        conn.commit()
                    except Exception as ex:
                        if 'cannot cast' in str(ex).lower():
                            print('Migration note: không thể chuyển device_code sang TEXT tự động.')

                try:
                    conn.execute(text("UPDATE bug_report SET visibility = 'private' WHERE visibility IS NULL"))
                    conn.execute(text("UPDATE bug_report SET reopen_requested = FALSE WHERE reopen_requested IS NULL"))
                    conn.execute(text("UPDATE bug_report SET error_type = 'Thiết bị' WHERE error_type IS NULL"))
                    conn.commit()
                except Exception:
                    pass
        except Exception as e:
            print(f"Migration error (non-critical): {e}")

# Run migrations on startup
migrate_bug_report_table()
migrate_bug_report_enhancements()
migrate_role_created_at()

def migrate_resource_table():
    """Create resource table if it doesn't exist, and ensure new columns exist."""
    with app.app_context():
        try:
            from sqlalchemy import text, inspect
            
            # 1. Create table if not exists
            try:
                inspector = inspect(db.engine)
                if 'resource' not in inspector.get_table_names():
                    with db.engine.connect() as conn:
                        conn.execute(text('''
                            CREATE TABLE IF NOT EXISTS resource (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                ip_address VARCHAR(100) NOT NULL,
                                service VARCHAR(255),
                                web_ui VARCHAR(255),
                                service_name VARCHAR(255),
                                status VARCHAR(50) DEFAULT 'Offline',
                                device_id INTEGER REFERENCES device(id),
                                notes TEXT,
                                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                            )
                        '''))
                        conn.commit()
                    print("[OK] Created resource table")
                    return # Created with all columns, done.
            except Exception as e:
                print(f"Migration error (resource create): {e}")

            # 2. Add new columns if table exists (Migrate v1 -> v2)
            try:
                inspector = inspect(db.engine)
                columns = {col['name'] for col in inspector.get_columns('resource')}
                
                with db.engine.connect() as conn:
                    if 'web_ui' not in columns:
                        conn.execute(text("ALTER TABLE resource ADD COLUMN web_ui VARCHAR(255)"))
                        # Optional: migrate data from 'service' if needed, but assuming empty or manual
                    if 'service_name' not in columns:
                        conn.execute(text("ALTER TABLE resource ADD COLUMN service_name VARCHAR(255)"))
                        # Optional: migrate data from 'notes' -> 'service_name' ??
                        # User request: "Ghi chú thành tên dịch vụ".
                        # Let's try to update service_name from notes if notes is not null
                        conn.execute(text("UPDATE resource SET service_name = notes WHERE service_name IS NULL"))
                        conn.execute(text("UPDATE resource SET notes = NULL")) # Clear notes to be "new column"
                    
                    conn.commit()
                    print("[OK] Updated resource table schema (v2)")
            except Exception as e:
                print(f"Migration error (resource alter): {e}")

        except Exception as e:
            print(f"Migration error (resource wrapper): {e}")

migrate_resource_table()

def migrate_device_type_table():
    """Create device_type table and seed initial data if needed."""
    with app.app_context():
        try:
            # Use SQLAlchemy to create table if not exists (compatible with all DBs)
            if not inspect(db.engine).has_table("device_type"):
                DeviceType.__table__.create(db.engine)
                print("[OK] Created device_type table")
                
                # Seed initial data
                initial_types = [
                    ('Laptop', 'Thiết bị IT'),
                    ('Case máy tính', 'Thiết bị IT'),
                    ('Màn hình', 'Thiết bị IT'),
                    ('Bàn phím', 'Thiết bị IT'),
                    ('Chuột', 'Thiết bị IT'),
                    ('Ổ cứng', 'Thiết bị IT'),
                    ('Ram', 'Thiết bị IT'),
                    ('Card màn hình', 'Thiết bị IT'),
                    ('Máy in', 'Thiết bị văn phòng'),
                    ('Máy chiếu', 'Thiết bị văn phòng'),
                    ('Máy scan', 'Thiết bị văn phòng'),
                    ('Thiết bị mạng', 'Hạ tầng IT'),
                    ('Server', 'Hạ tầng IT'),
                    ('Switch mạng', 'Hạ tầng IT'),
                    ('Router', 'Hạ tầng IT'),
                    ('Firewall', 'Hạ tầng IT'),
                    ('Access Point', 'Hạ tầng IT'),
                    ('Thiết bị cân bằng tải', 'Hạ tầng IT'),
                    ('Camera', 'Hạ tầng IT'),
                    ('Camera IP', 'Hạ tầng IT'),
                    ('Đầu ghi camera', 'Hạ tầng IT'),
                    ('Camera chấm công', 'Hạ tầng IT'),
                    ('Máy chấm công', 'Hạ tầng IT'),
                    ('Ổ điện', 'Thiết bị dùng chung'),
                    ('Dây mạng', 'Thiết bị tiêu hao'),
                    ('Cáp kết nối', 'Thiết bị tiêu hao'),
                    ('Thiết bị điện khác', 'Thiết bị dùng chung'),
                    ('Thiết bị khác', 'Khác')
                ]
                
                for name, cat in initial_types:
                    if not DeviceType.query.filter_by(name=name).first():
                        try:
                            db.session.add(DeviceType(name=name, category=cat))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                
                print("[OK] Verified device types seeding")
                
        except Exception as e:
            print(f"Migration error (device_type): {e}")

migrate_device_type_table()

def migrate_config_proposal_workflow():
    """Add workflow columns to config_proposal table if they don't exist"""
    with app.app_context():
        try:
            from sqlalchemy import text, inspect
            
            # Check if table exists
            try:
                inspector = inspect(db.engine)
                if 'config_proposal' not in inspector.get_table_names():
                    return
                columns = {col['name'] for col in inspector.get_columns('config_proposal')}
            except Exception:
                # If inspection fails, fallback to simple query check or exit
                return

            with db.engine.connect() as conn:
                def _add_col(col_name, col_type):
                    if col_name not in columns:
                        try:
                            # External DB (Postgres) vs SQLite
                            stmt = f"ALTER TABLE config_proposal ADD COLUMN {col_name} {col_type}"
                            conn.execute(text(stmt))
                            conn.commit()
                            print(f"[OK] Added column {col_name} to config_proposal")
                        except Exception as e:
                            print(f"Migration note ({col_name}): {e}")

                # Add new columns
                _add_col('created_by', 'INTEGER REFERENCES user(id)')
                _add_col('team_lead_approver_id', 'INTEGER REFERENCES user(id)')
                _add_col('team_lead_approved_at', 'DATETIME')
                _add_col('it_consultant_id', 'INTEGER REFERENCES user(id)')
                _add_col('it_consulted_at', 'DATETIME')
                _add_col('it_consultation_note', 'TEXT')
                _add_col('finance_reviewer_id', 'INTEGER REFERENCES user(id)')
                _add_col('finance_reviewed_at', 'DATETIME')
                _add_col('finance_review_note', 'TEXT')
                _add_col('director_approver_id', 'INTEGER REFERENCES user(id)')
                _add_col('director_approved_at', 'DATETIME')
                _add_col('director_approval_note', 'TEXT')
                _add_col('cat_purchaser_id', 'INTEGER REFERENCES user(id)')
                _add_col('purchasing_at', 'DATETIME')
                _add_col('accountant_payer_id', 'INTEGER REFERENCES user(id)')
                _add_col('payment_at', 'DATETIME')
                _add_col('tech_receiver_id', 'INTEGER REFERENCES user(id)')
                _add_col('goods_received_at', 'DATETIME')
                _add_col('handover_to_user_at', 'DATETIME')
                _add_col('accountant_invoice_id', 'INTEGER REFERENCES user(id)')
                _add_col('invoice_received_at', 'DATETIME')
                _add_col('rejection_reason', 'TEXT')
                _add_col('current_stage_deadline', 'DATETIME')
                _add_col('general_requirements', 'TEXT')
                _add_col('required_date', 'DATE')
                
                # Check status column length/type if needed, but usually can't easy alter limit in standard SQL without table recreation.
                # Assuming 30 chars is enough or we utilize it carefully. New statuses are under 30 chars.
                
                # Data migration: Set created_by = 1 (Admin) or proposer if null
                if 'created_by' not in columns: # Just added
                     # Try to map proposer_name to user?? No, too risky. Just set admin for legacy.
                     pass 
                
                # Data Migration: Map legacy statuses to new codes
                try:
                    conn.execute(text("UPDATE config_proposal SET status = 'new' WHERE status = 'Mới tạo'"))
                    conn.execute(text("UPDATE config_proposal SET status = 'purchasing' WHERE status = 'Đang mua hàng'"))
                    conn.execute(text("UPDATE config_proposal SET status = 'rejected' WHERE status = 'Hủy'"))
                    conn.execute(text("UPDATE config_proposal SET status = 'completed' WHERE status = 'Hoàn thành'"))
                    conn.commit()
                except Exception as e:
                    print(f"Data migration error: {e}")

        except Exception as e:
            print(f"Migration error (config_proposal_workflow): {e}")

migrate_config_proposal_workflow()

def migrate_missing_columns_v3():
    """Add new columns to existing tables if they don't exist"""
    with app.app_context():
        try:
            from sqlalchemy import text, inspect
            
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()

            with db.engine.connect() as conn:
                def _add_col_if_missing(table_name, col_name, col_type):
                    if table_name in tables:
                        cols = {col['name'] for col in inspector.get_columns(table_name)}
                        if col_name not in cols:
                            try:
                                stmt = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                                conn.execute(text(stmt))
                                conn.commit()
                                print(f"[OK] Added column {col_name} to {table_name}")
                            except Exception as e:
                                print(f"Migration note ({table_name}.{col_name}): {e}")

                # order_tracking
                _add_col_if_missing('order_tracking', 'edited_at', 'TIMESTAMP')
                _add_col_if_missing('order_tracking', 'updated_by', 'INTEGER')
                
                # bug_report_comment
                _add_col_if_missing('bug_report_comment', 'edited_at', 'TIMESTAMP')

                # device
                _add_col_if_missing('device', 'purchase_price', 'FLOAT')
                _add_col_if_missing('device', 'brand', 'VARCHAR(100)')
                _add_col_if_missing('device', 'supplier', 'VARCHAR(150)')
                _add_col_if_missing('device', 'warranty', 'VARCHAR(50)')
                _add_col_if_missing('device', 'image_filename', 'VARCHAR(255)')
                _add_col_if_missing('device', 'image_filenames', 'TEXT')
                _add_col_if_missing('device', 'mainboard', 'VARCHAR(120)')

                # device_handover
                _add_col_if_missing('device_handover', 'batch_id', 'VARCHAR(64)')
                _add_col_if_missing('device_handover', 'condition_images', 'TEXT')
                try:
                    conn.execute(text('ALTER TABLE device_handover ALTER COLUMN device_id DROP NOT NULL'))
                    conn.commit()
                except Exception:
                    pass

                # consumable_item
                _add_col_if_missing('consumable_item', 'group_name', 'VARCHAR(100)')
                _add_col_if_missing('consumable_item', 'manufacturer', 'VARCHAR(120)')
                _add_col_if_missing('consumable_item', 'model', 'VARCHAR(120)')
                _add_col_if_missing('consumable_item', 'standard', 'VARCHAR(120)')
                _add_col_if_missing('consumable_item', 'speed', 'VARCHAR(120)')
                _add_col_if_missing('consumable_item', 'length', 'VARCHAR(80)')
                _add_col_if_missing('consumable_item', 'connector_a', 'VARCHAR(80)')
                _add_col_if_missing('consumable_item', 'connector_b', 'VARCHAR(80)')
                _add_col_if_missing('consumable_item', 'fiber_type', 'VARCHAR(80)')
                _add_col_if_missing('consumable_item', 'color', 'VARCHAR(80)')
                _add_col_if_missing('consumable_item', 'track_after_handover', 'BOOLEAN DEFAULT FALSE')
                _add_col_if_missing('consumable_item', 'is_active', 'BOOLEAN DEFAULT TRUE')
                _add_col_if_missing('consumable_item', 'manager_id', 'INTEGER')
                _add_col_if_missing('consumable_item', 'image_filenames', 'TEXT')

                # consumable_transaction
                _add_col_if_missing('consumable_transaction', 'batch_id', 'VARCHAR(64)')
                _add_col_if_missing('consumable_transaction', 'location', 'VARCHAR(150)')

                # bug_report
                _add_col_if_missing('config_proposal_item', 'warranty', 'VARCHAR(120)')
                _add_col_if_missing('config_proposal_item', 'product_code', 'VARCHAR(100)')
                
                # user
                _add_col_if_missing('user', 'last_name_token', 'VARCHAR(120)')

        except Exception as e:
            print(f"Migration error (missing columns v3): {e}")

migrate_missing_columns_v3()

def migrate_user_avatar():
    with app.app_context():
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'user' in inspector.get_table_names():
                cols = {c['name'] for c in inspector.get_columns('user')}
                with db.engine.connect() as conn:
                    table_name = '"user"' if 'postgres' in str(db.engine.url) else 'user'
                    if 'avatar' not in cols:
                        try:
                            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN avatar VARCHAR(255)"))
                            conn.commit()
                            print("[OK] Added avatar to user table")
                        except Exception as inner_e:
                            print(f"Error adding avatar: {inner_e}")
                    if 'telegram_chat_id' not in cols:
                        try:
                            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN telegram_chat_id VARCHAR(100)"))
                            conn.commit()
                            print("[OK] Added telegram_chat_id to user table")
                        except Exception as inner_e:
                            print(f"Error adding telegram_chat_id: {inner_e}")
        except Exception as e:
            print(f"Migration error (avatar/telegram): {e}")

migrate_user_avatar()

# Ensure default admin exists on startup
with app.app_context():
    try:
        if not User.query.filter_by(username='admin').first():
            it_dept = Department.query.filter_by(name='IT').first()
            if not it_dept:
                it_dept = Department(name='IT', description='Phòng Công nghệ Thông tin')
                db.session.add(it_dept)
                db.session.flush()
            create_admin_from_env = os.environ.get('INVENTORY_CREATE_ADMIN_FROM_ENV', 'false').lower() == 'true'
            admin_password = os.environ.get('ADMIN_PASSWORD')
            if not create_admin_from_env or not admin_password:
                raise RuntimeError('Bootstrap admin is not enabled')
            admin_user = User(
                username='admin',
                password=generate_password_hash(admin_password),
                full_name='Quản Trị Viên',
                email='admin@example.com',
                role='admin',
                department_id=it_dept.id
            )
            db.session.add(admin_user)
            it_dept.manager_id = admin_user.id
            db.session.commit()
            print('Bootstrap admin created from environment.')
    except Exception as _e:
        # Do not block app start if admin creation fails
        pass

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
    avatar = db.Column(db.String(255))
    telegram_chat_id = db.Column(db.String(100))
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

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('notifications', order_by='Notification.created_at.desc()', lazy='dynamic'))

import urllib.request
import urllib.parse
import json
import threading

def send_telegram_message(chat_id, text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({'chat_id': str(chat_id), 'text': text}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            pass
    except Exception as e:
        print(f"Telegram error: {e}")

def notify_user(user_id, message, link=""):
    try:
        user = User.query.get(user_id)
        if not user: return
        notif = Notification(user_id=user_id, message=message, link=link)
        db.session.add(notif)
        db.session.commit()
        if user.telegram_chat_id:
            text_msg = f"THÔNG BÁO\n{message}"
            threading.Thread(target=send_telegram_message, args=(user.telegram_chat_id, text_msg)).start()
    except Exception as e:
        print(f"Notify error: {e}")

def notify_group(message, link=""):
    try:
        group_id = os.environ.get('TELEGRAM_GROUP_CHAT_ID')
        if not group_id: return
        text_msg = f"THÔNG BÁO HỆ THỐNG\n{message}"
        threading.Thread(target=send_telegram_message, args=(group_id, text_msg)).start()
    except Exception as e:
        print(f"Notify group error: {e}")

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
    cpu = db.Column(db.String(120))
    mainboard = db.Column(db.String(120))
    ram_gb = db.Column(db.Integer)
    ssd = db.Column(db.String(120))
    hdd = db.Column(db.String(120))
    vga = db.Column(db.String(120))
    wifi_card = db.Column(db.String(120))
    network_card = db.Column(db.String(120))
    manager = db.relationship('User', foreign_keys=[manager_id])
    purchase_price = db.Column(db.Float)
    image_filename = db.Column(db.String(255))
    image_filenames = db.Column(db.Text)

DEVICE_PC_SPEC_FIELDS = {
    'cpu': 'CPU',
    'mainboard': 'Main',
    'ram_gb': 'RAM (GB)',
    'ssd': 'SSD',
    'hdd': 'HDD',
    'vga': 'VGA',
    'network_card': 'Card mạng',
}

def _parse_ram_gb(value):
    if value is None or pd.isna(value):
        return None
    match = re.search(r'\d+', str(value))
    return int(match.group(0)) if match else None

def _config_key_values(config_text):
    if not config_text:
        return []
    text_value = re.sub(r'^\s*[-•]\s*', '', str(config_text).strip())
    pattern = re.compile(r'(?:^|\r?\n\s*[-•]?\s*|\s+-\s*)([^:：\n]+?)\s*[:：]\s*')
    matches = list(pattern.finditer(text_value))
    pairs = []
    for index, match in enumerate(matches):
        key = match.group(1).strip().lower()
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text_value)
        value = text_value[match.end():next_start].strip()
        value = re.sub(r'\s+-\s*$', '', value).strip()
        if key and value:
            pairs.append((key, value))
    return pairs

def _pick_config_value(config_text, keys):
    for key, value in _config_key_values(config_text):
        if value and any(token in key for token in keys):
            return value
    return None

def _device_pc_specs_from_config_text(config_text):
    drive_value = _pick_config_value(config_text, ['ổ cứng', 'o cung', 'disk', 'drive'])
    ssd_value = _pick_config_value(config_text, ['ssd'])
    hdd_value = _pick_config_value(config_text, ['hdd'])
    if drive_value:
        ssd_match = re.search(r'\bssd\b\s*:?\s*([^,+;]+)', drive_value, re.IGNORECASE)
        hdd_match = re.search(r'\bhdd\b\s*:?\s*([^,+;]+)', drive_value, re.IGNORECASE)
        if not ssd_match:
            ssd_match = re.search(r'([^,+;]+)\s*\bssd\b', drive_value, re.IGNORECASE)
        if not hdd_match:
            hdd_match = re.search(r'([^,+;]+)\s*\bhdd\b', drive_value, re.IGNORECASE)
        if ssd_match and not ssd_value:
            ssd_value = ssd_match.group(1).strip()
        if hdd_match and not hdd_value:
            hdd_value = hdd_match.group(1).strip()
        if not ssd_value and not hdd_value:
            ssd_value = drive_value
    return {
        'cpu': _pick_config_value(config_text, ['cpu', 'chip']),
        'mainboard': _pick_config_value(config_text, ['main', 'mainboard', 'bo mạch', 'bo mach']),
        'ram_gb': _parse_ram_gb(_pick_config_value(config_text, ['ram'])),
        'ssd': ssd_value,
        'hdd': hdd_value,
        'vga': _pick_config_value(config_text, ['vga', 'card màn hình', 'card man hinh', 'gpu']),
        'wifi_card': None,
        'network_card': _pick_config_value(config_text, ['card mạng', 'card mang', 'lan', 'network', 'wifi', 'wi-fi']),
    }

def _device_pc_specs_from_form():
    config_specs = _device_pc_specs_from_config_text(request.form.get('configuration'))
    return {
        'cpu': (request.form.get('cpu') or '').strip() or config_specs.get('cpu') or None,
        'mainboard': (request.form.get('mainboard') or '').strip() or config_specs.get('mainboard') or None,
        'ram_gb': _parse_ram_gb(request.form.get('ram_gb')) or config_specs.get('ram_gb'),
        'ssd': (request.form.get('ssd') or '').strip() or config_specs.get('ssd') or None,
        'hdd': (request.form.get('hdd') or '').strip() or config_specs.get('hdd') or None,
        'vga': (request.form.get('vga') or '').strip() or config_specs.get('vga') or None,
        'wifi_card': None,
        'network_card': None,
    }

DEVICE_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}

def _device_images_dir():
    path = os.path.join(app.root_path, 'static', 'uploads', 'devices')
    os.makedirs(path, exist_ok=True)
    return path

def _save_device_image_file(file_storage, device_id):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext not in DEVICE_IMAGE_EXTENSIONS:
        raise ValueError('Ảnh thiết bị phải có định dạng JPG, PNG, WEBP hoặc GIF.')
    import uuid
    image_filename = f"{device_id}_{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(_device_images_dir(), image_filename))
    return image_filename

def _device_image_list(image_value):
    if not image_value:
        return []
    if isinstance(image_value, list):
        return [os.path.basename(item) for item in image_value if item]
    text = str(image_value).strip()
    if not text:
        return []
    if text.startswith('['):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [os.path.basename(item) for item in data if item]
        except Exception:
            pass
    return [os.path.basename(text)]

def _device_image_storage_value(image_filenames):
    image_filenames = [os.path.basename(name) for name in image_filenames or [] if name]
    if not image_filenames:
        return None
    if len(image_filenames) == 1:
        return image_filenames[0]
    return json.dumps(image_filenames[:5], ensure_ascii=False)

def _save_device_image_files(files, device_id, limit=5):
    saved = []
    for file_storage in files or []:
        if not file_storage or not file_storage.filename:
            continue
        if len(saved) >= limit:
            break
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        saved_name = _save_device_image_file(file_storage, device_id)
        if saved_name:
            saved.append(saved_name)
    return saved

def _delete_device_image_file(image_filename):
    if not image_filename:
        return
    safe_name = os.path.basename(image_filename)
    path = os.path.join(_device_images_dir(), safe_name)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def _delete_device_image_files(image_filenames):
    for image_filename in _device_image_list(image_filenames):
        _delete_device_image_file(image_filename)

def _consumable_images_dir():
    path = os.path.join(app.root_path, 'static', 'uploads', 'consumables')
    os.makedirs(path, exist_ok=True)
    return path

def _save_consumable_image_file(file_storage, item_id):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext not in DEVICE_IMAGE_EXTENSIONS:
        raise ValueError('Ảnh vật tư phải có định dạng JPG, PNG, WEBP hoặc GIF.')
    import uuid
    image_filename = f"{item_id}_{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(_consumable_images_dir(), image_filename))
    return image_filename

def _save_consumable_image_files(files, item_id, limit=5):
    saved = []
    for file_storage in files or []:
        if not file_storage or not file_storage.filename:
            continue
        if len(saved) >= limit:
            break
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        saved_name = _save_consumable_image_file(file_storage, item_id)
        if saved_name:
            saved.append(saved_name)
    return saved

def _delete_consumable_image_file(image_filename):
    if not image_filename:
        return
    safe_name = os.path.basename(image_filename)
    path = os.path.join(_consumable_images_dir(), safe_name)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def _delete_consumable_image_files(image_filenames):
    for image_filename in _device_image_list(image_filenames):
        _delete_consumable_image_file(image_filename)

def _handover_images_dir():
    path = os.path.join(app.root_path, 'static', 'uploads', 'handovers')
    os.makedirs(path, exist_ok=True)
    return path

def _save_handover_condition_images(files, batch_id):
    selected_files = [f for f in files if f and f.filename]
    if len(selected_files) > 5:
        raise ValueError('Chỉ được thêm tối đa 5 ảnh tình trạng thiết bị.')
    filenames = []
    import uuid
    for file_storage in selected_files:
        filename = secure_filename(file_storage.filename)
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if ext not in DEVICE_IMAGE_EXTENSIONS:
            raise ValueError('Ảnh tình trạng thiết bị phải có định dạng JPG, PNG, WEBP hoặc GIF.')
        saved_name = f"{batch_id}_{uuid.uuid4().hex}.{ext}"
        file_storage.save(os.path.join(_handover_images_dir(), saved_name))
        filenames.append(saved_name)
    return filenames

def _handover_image_list(handover):
    if not handover or not handover.condition_images:
        return []
    try:
        data = json.loads(handover.condition_images)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _delete_handover_condition_images(image_filenames):
    for image_filename in image_filenames or []:
        safe_name = os.path.basename(image_filename)
        path = os.path.join(_handover_images_dir(), safe_name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

def _device_pc_specs_from_row(row):
    config_specs = _device_pc_specs_from_config_text(_cell_text(row.get('Cấu hình')))
    return {
        'cpu': _cell_text(row.get('CPU')) or config_specs.get('cpu') or None,
        'mainboard': _cell_text(row.get('Main')) or _cell_text(row.get('Mainboard')) or config_specs.get('mainboard') or None,
        'ram_gb': _parse_ram_gb(row.get('RAM (GB)')) or config_specs.get('ram_gb'),
        'ssd': _cell_text(row.get('SSD')) or config_specs.get('ssd') or None,
        'hdd': _cell_text(row.get('HDD')) or config_specs.get('hdd') or None,
        'vga': _cell_text(row.get('VGA')) or config_specs.get('vga') or None,
        'wifi_card': None,
        'network_card': _cell_text(row.get('Card mạng')) or _cell_text(row.get('Card Wi-Fi')) or config_specs.get('network_card') or None,
    }

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
    reported_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reporter = db.relationship('User', foreign_keys=[reported_by])

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DeviceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(100), nullable=False) # 'Thiết bị IT', 'Thiết bị văn phòng', etc.
    code_prefix = db.Column(db.String(20))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)

class RolePermission(db.Model):
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), primary_key=True)
    permission_id = db.Column(db.Integer, db.ForeignKey('permission.id'), primary_key=True)
    # Corrected relationship definition to check for backref conflicts
    role = db.relationship('Role', backref=db.backref('role_permissions', cascade='all, delete-orphan'))
    permission = db.relationship('Permission')

class UserRole(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), primary_key=True)
    role = db.relationship('Role')

class DeviceHandover(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(64))
    handover_date = db.Column(db.Date, nullable=False, default=date.today)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'))
    device = db.relationship('Device', backref='handovers')
    giver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    giver = db.relationship('User', foreign_keys=[giver_id], back_populates='given_handovers')
    receiver = db.relationship('User', foreign_keys=[receiver_id], back_populates='received_handovers')
    device_condition = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.String(255))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)
    condition_images = db.Column(db.Text)

class ConsumableItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    group_name = db.Column(db.String(100))
    category = db.Column(db.String(100), default='Thiết bị tiêu hao')
    manufacturer = db.Column(db.String(120))
    model = db.Column(db.String(120))
    standard = db.Column(db.String(120))
    speed = db.Column(db.String(120))
    length = db.Column(db.String(80))
    connector_a = db.Column(db.String(80))
    connector_b = db.Column(db.String(80))
    fiber_type = db.Column(db.String(80))
    color = db.Column(db.String(80))
    image_filenames = db.Column(db.Text)
    unit = db.Column(db.String(30), default='cái')
    current_quantity = db.Column(db.Integer, nullable=False, default=0)
    min_quantity = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(150))
    track_after_handover = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manager = db.relationship('User', foreign_keys=[manager_id])

    transactions = db.relationship(
        'ConsumableTransaction',
        back_populates='item',
        cascade='all, delete-orphan'
    )

class ConsumableTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consumable_id = db.Column(db.Integer, db.ForeignKey('consumable_item.id'), nullable=False)
    transaction_type = db.Column(db.String(30), nullable=False)  # Nhập, Xuất, Điều chỉnh
    quantity = db.Column(db.Integer, nullable=False)
    before_quantity = db.Column(db.Integer, nullable=False, default=0)
    after_quantity = db.Column(db.Integer, nullable=False, default=0)
    transaction_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    issued_to_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    batch_id = db.Column(db.String(64))
    location = db.Column(db.String(150))
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)

    item = db.relationship('ConsumableItem', back_populates='transactions')
    issued_to = db.relationship('User', foreign_keys=[issued_to_id])
    actor = db.relationship('User', foreign_keys=[actor_id])

class ConsumableHandoverItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(64), nullable=False, index=True)
    consumable_id = db.Column(db.Integer, db.ForeignKey('consumable_item.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    giver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    quantity = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(150))
    handover_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(50), default='Đang sử dụng')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship('ConsumableItem')
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    giver = db.relationship('User', foreign_keys=[giver_id])

class StockItemCategory(db.Model):
    """A reusable stock category with its own custom specification fields."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    code_prefix = db.Column(db.String(20), nullable=False)
    specification_fields = db.Column(db.Text, default='[]')
    description = db.Column(db.Text)
    image_filename = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('StockItem', back_populates='category')

class StockItem(db.Model):
    """Stock-managed accessory/supply item, independent from consumable devices."""
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('stock_item_category.id'), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(180), nullable=False)
    manufacturer = db.Column(db.String(120))
    model = db.Column(db.String(120))
    unit = db.Column(db.String(30), nullable=False, default='cái')
    current_quantity = db.Column(db.Integer, nullable=False, default=0)
    min_quantity = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(150))
    specifications = db.Column(db.Text, default='{}')
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    track_units = db.Column(db.Boolean, nullable=False, default=False)
    image_filenames = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('StockItemCategory', back_populates='items')
    movements = db.relationship('StockItemMovement', back_populates='item', cascade='all, delete-orphan')
    units = db.relationship('StockItemUnit', back_populates='item', cascade='all, delete-orphan')

class StockItemUnit(db.Model):
    """Individual physical unit of a StockItem when track_units is enabled."""
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('stock_item.id'), nullable=False)
    unit_code = db.Column(db.String(80), unique=True, nullable=False)
    serial_number = db.Column(db.String(120))
    status = db.Column(db.String(30), nullable=False, default='Trong kho')  # 'Trong kho', 'Đã xuất', 'Hỏng', 'Thanh lý'
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    location = db.Column(db.String(150))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    item = db.relationship('StockItem', back_populates='units')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    unit_movements = db.relationship('StockItemUnitMovement', back_populates='unit', cascade='all, delete-orphan')

class StockItemMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('stock_item.id'), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)  # Nhập, Xuất, Điều chỉnh
    quantity = db.Column(db.Integer, nullable=False)
    before_quantity = db.Column(db.Integer, nullable=False, default=0)
    after_quantity = db.Column(db.Integer, nullable=False, default=0)
    movement_date = db.Column(db.Date, nullable=False, default=date.today)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    supplier = db.Column(db.String(150))
    reference_code = db.Column(db.String(100))
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship('StockItem', back_populates='movements')
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    actor = db.relationship('User', foreign_keys=[actor_id])
    unit_movements = db.relationship('StockItemUnitMovement', back_populates='movement', cascade='all, delete-orphan')

class StockItemUnitMovement(db.Model):
    """Junction table linking StockItemMovement to StockItemUnit."""
    id = db.Column(db.Integer, primary_key=True)
    movement_id = db.Column(db.Integer, db.ForeignKey('stock_item_movement.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('stock_item_unit.id'), nullable=False)
    action = db.Column(db.String(30), nullable=False)  # 'Nhập', 'Xuất'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    movement = db.relationship('StockItemMovement', back_populates='unit_movements')
    unit = db.relationship('StockItemUnit', back_populates='unit_movements')


# --- (Deleted Device Group Models) ---

# --- (Deleted Server Room Extra Info) ---

# --- Attendance & Hikvision Timekeeping Models ---
class AttendanceUser(db.Model):
    """Attendance user catalog, managed independently from warehouse users."""
    id = db.Column(db.Integer, primary_key=True)
    employee_no = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    user_type = db.Column(db.String(50), nullable=False, default='Nhân viên')  # 'Nhân viên', 'Bảo vệ', 'Lao công', 'Khách đặc biệt', 'Khác'
    card_no = db.Column(db.String(50))
    department = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    system_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    system_user = db.relationship('User', foreign_keys=[system_user_id])

class AttendanceRecord(db.Model):
    """Access control and timekeeping logs fetched from Hikvision device."""
    id = db.Column(db.Integer, primary_key=True)
    employee_no = db.Column(db.String(50), nullable=False, index=True)
    user_name = db.Column(db.String(120))
    event_time = db.Column(db.DateTime, nullable=False, index=True)
    verify_mode = db.Column(db.String(50), default='Vân tay')  # 'Vân tay', 'Thẻ', 'Khuôn mặt', 'Mật khẩu', 'Khác'
    event_type = db.Column(db.String(50), default='Check-in')  # 'Check-in', 'Check-out', 'Quẹt vân tay'
    device_name = db.Column(db.String(100), default='Hikvision Device')
    raw_event_id = db.Column(db.String(100), unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(100), nullable=False)
    # service field kept for backward compatibility or can be deprecated
    service = db.Column(db.String(255)) 
    web_ui = db.Column(db.String(255))
    service_name = db.Column(db.String(255))
    status = db.Column(db.String(50), default='Offline')  # Online, Offline, Maintenance
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    device = db.relationship('Device', backref=db.backref('resources', lazy='dynamic'))


# --- Configuration Proposal Models ---
class ConfigProposal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    proposal_date = db.Column(db.Date, nullable=False)
    proposer_name = db.Column(db.String(120))
    proposer_unit = db.Column(db.String(120))
    scope = db.Column(db.String(50))  # Dùng chung | Cá nhân
    quantity = db.Column(db.Integer, default=1)  # Số lượng bộ thiết bị
    currency = db.Column(db.String(10), default='VND')
    status = db.Column(db.String(30), default='new')  # new, team_approved, it_consulted, approved, purchasing, payment_done, goods_received, handed_over, completed, rejected
    purchase_status = db.Column(db.String(30), default='Lấy báo giá')  # Deprecated in favor of workflow status, but kept for legacy
    priority = db.Column(db.String(50), default='Trung bình')
    is_from_stock = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    supplier_info = db.Column(db.Text) # Changed to Text for detailed info
    subtotal = db.Column(db.Float, default=0.0)
    vat_percent = db.Column(db.Float, default=10.0)
    vat_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Workflow tracking logs
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    team_lead_approver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    team_lead_approved_at = db.Column(db.DateTime)
    
    it_consultant_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    it_consulted_at = db.Column(db.DateTime)
    it_consultation_note = db.Column(db.Text)
    
    finance_reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    finance_reviewed_at = db.Column(db.DateTime)
    finance_review_note = db.Column(db.Text)
    
    director_approver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    director_approved_at = db.Column(db.DateTime)
    director_approval_note = db.Column(db.Text)
    
    cat_purchaser_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Purchasing staff
    purchasing_at = db.Column(db.DateTime)
    
    accountant_payer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    payment_at = db.Column(db.DateTime)
    
    tech_receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    goods_received_at = db.Column(db.DateTime)
    
    handover_to_user_at = db.Column(db.DateTime)
    
    accountant_invoice_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    invoice_received_at = db.Column(db.DateTime)
    
    rejection_reason = db.Column(db.Text)
    current_stage_deadline = db.Column(db.DateTime) # SLA deadline
    general_requirements = db.Column(db.Text) # Yêu cầu chung
    required_date = db.Column(db.Date) # Thời hạn cần thiết bị

    creator = db.relationship('User', foreign_keys=[created_by], backref='created_proposals')
    attachments = db.relationship('ConfigProposalAttachment', backref='proposal', cascade='all, delete-orphan')

class ConfigProposalAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'), nullable=False)
    step = db.Column(db.String(50), nullable=False) # purchasing, receiving, handover, invoice, payment
    file_name = db.Column(db.String(255), nullable=False) # Original name
    file_path = db.Column(db.String(500), nullable=False) # Stored server path/name
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploader = db.relationship('User', foreign_keys=[uploaded_by])

class BackupLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    action = db.Column(db.String(50), nullable=False) # 'backup' or 'restore'
    status = db.Column(db.String(20), default='success') # 'success', 'failed', 'processing'
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    user = db.relationship('User', backref='backup_logs')



class OrderTracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'), nullable=False)
    status_content = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.Text)
    updated_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    edited_at = db.Column(db.DateTime)
    
    proposal = db.relationship('ConfigProposal', backref=db.backref('order_logs', lazy=True, cascade="all,delete"))
    updater = db.relationship('User', foreign_keys=[updated_by])

class ConfigProposalItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'), nullable=False)
    order_no = db.Column(db.Integer, default=0)
    option_name = db.Column(db.String(120))
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

# --- Bug Report Models ---
# Association table for related bug reports (many-to-many)
bug_report_relations = db.Table('bug_report_relations',
    db.Column('report_id', db.Integer, db.ForeignKey('bug_report.id'), primary_key=True),
    db.Column('related_report_id', db.Integer, db.ForeignKey('bug_report.id'), primary_key=True)
)

class BugReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)  # Giảm độ dài tiêu đề
    device_code = db.Column(db.Text)  # Lưu danh sách mã thiết bị (phân tách bằng dấu phẩy)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default='Mới tạo')  # Mới tạo, Đang xử lý, Đã xử lý, Đã đóng
    priority = db.Column(db.String(50), default='Trung bình')  # Thấp, Trung bình, Cao, Khẩn cấp
    error_type = db.Column(db.String(50), default='Thiết bị')  # Thiết bị, Phần mềm, Văn phòng
    visibility = db.Column(db.String(20), default='private')  # private | public
    reopen_requested = db.Column(db.Boolean, default=False)
    rating = db.Column(db.Integer)  # 1-5 sao khi vấn đề đóng
    merged_into = db.Column(db.Integer, db.ForeignKey('bug_report.id'))  # ID của báo lỗi đã được gộp vào
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'))  # Quản trị viên được gán
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)
    resolution = db.Column(db.Text)  # Giải pháp/ghi chú khi xử lý xong
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_bug_reports')
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_bug_reports')
    # Related reports (many-to-many)
    related_reports = db.relationship('BugReport',
                                     secondary=bug_report_relations,
                                     primaryjoin=id == bug_report_relations.c.report_id,
                                     secondaryjoin=id == bug_report_relations.c.related_report_id,
                                     backref='related_to_reports',
                                     lazy='dynamic')
    # Parent-child relationship for merged tickets
    parent_report = db.relationship(
        'BugReport',
        remote_side=[id],
        foreign_keys=[merged_into],
        backref=db.backref('merged_reports', cascade='all, delete-orphan')
    )

    @property
    def device_code_list(self):
        codes = []
        try:
            raw = self.device_code or ''
            codes = [code.strip() for code in raw.split(',') if code and code.strip()]
        except Exception:
            pass
        return codes

    @property
    def is_public(self):
        return (self.visibility or '').lower() == 'public'

class BugReportComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bug_report_id = db.Column(db.Integer, db.ForeignKey('bug_report.id'), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    edited_at = db.Column(db.DateTime)
    bug_report = db.relationship('BugReport', backref=db.backref('comments', cascade='all, delete-orphan', order_by='BugReportComment.created_at'))
    creator = db.relationship('User', foreign_keys=[created_by])

class BugReportAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bug_report_id = db.Column(db.Integer, db.ForeignKey('bug_report.id'), nullable=False)
    file_name = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.Text, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    bug_report = db.relationship('BugReport', backref=db.backref('attachments', cascade='all, delete-orphan'))

def seed_rbac_data():
    """Seed RBAC permissions and roles after models are defined"""
    with app.app_context():
        try:
            # Insert permissions
            for code, name in PERMISSIONS:
                if not Permission.query.filter_by(code=code).first():
                    db.session.add(Permission(code=code, name=name))
            db.session.commit()
            
            # Ensure Admin role
            admin_role = Role.query.filter_by(name='Admin').first()
            if not admin_role:
                admin_role = Role(name='Admin', description='Quyền đầy đủ')
                db.session.add(admin_role)
                db.session.commit()
            
            # Ensure User role (view-only devices)
            user_role = Role.query.filter_by(name='User').first()
            if not user_role:
                user_role = Role(name='User', description='Người dùng - chỉ xem thiết bị')
                db.session.add(user_role)
                db.session.commit()

            manager_role = Role.query.filter_by(name='Manager').first()
            if not manager_role:
                manager_role = Role(name='Manager', description='Quản lý phòng ban - xem dữ liệu trong phạm vi phụ trách')
                db.session.add(manager_role)
                db.session.commit()
            
            # Grant all permissions to Admin
            perms = Permission.query.all()
            for p in perms:
                exists = RolePermission.query.filter_by(role_id=admin_role.id, permission_id=p.id).first()
                if not exists:
                    db.session.add(RolePermission(role_id=admin_role.id, permission_id=p.id))
            db.session.commit()
            
            # Grant only devices.view to User role by default
            dev_view = Permission.query.filter_by(code='devices.view').first()
            if dev_view and not RolePermission.query.filter_by(role_id=user_role.id, permission_id=dev_view.id).first():
                db.session.add(RolePermission(role_id=user_role.id, permission_id=dev_view.id))
                db.session.commit()

            manager_permission_codes = [
                'dashboard.view',
                'devices.view',
                'handovers.view',
                'config_proposals.view',
                'config_proposals.create',
                'config_proposals.edit',
                'config_proposals.approve_team',
                'bug_reports.create',
                'bug_reports.view',
                'bug_reports.assign',
            ]
            for code in manager_permission_codes:
                perm = Permission.query.filter_by(code=code).first()
                if perm and not RolePermission.query.filter_by(role_id=manager_role.id, permission_id=perm.id).first():
                    db.session.add(RolePermission(role_id=manager_role.id, permission_id=perm.id))
            db.session.commit()

            for manager_id in {dept.manager_id for dept in Department.query.filter(Department.manager_id != None).all()}:
                if manager_id and not UserRole.query.filter_by(user_id=manager_id, role_id=manager_role.id).first():
                    db.session.add(UserRole(user_id=manager_id, role_id=manager_role.id))
            db.session.commit()
            
            # Assign Admin role to existing admin user if any
            admin_user = User.query.filter_by(role='admin').first()
            if admin_user:
                if not UserRole.query.filter_by(user_id=admin_user.id, role_id=admin_role.id).first():
                    db.session.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))
                    db.session.commit()
            
            print("RBAC data seeded successfully")
        except Exception as e:
            print(f"RBAC seed error: {e}")

# Seed RBAC data after models are defined
seed_rbac_data()

def _users_exist():
    try:
        return User.query.count() > 0
    except Exception:
        return False

def _get_or_create_initial_department():
    dept = Department.query.filter_by(name='IT Department').first()
    if not dept:
        dept = Department(
            name='IT Department',
            description='Initial administration department',
            order_index=1
        )
        db.session.add(dept)
        db.session.flush()
    return dept

def _assign_admin_role(user):
    seed_rbac_data()
    admin_role = Role.query.filter_by(name='Admin').first()
    if admin_role and not UserRole.query.filter_by(user_id=user.id, role_id=admin_role.id).first():
        db.session.add(UserRole(user_id=user.id, role_id=admin_role.id))

def _assign_manager_role(user_id):
    if not user_id:
        return
    manager_role = Role.query.filter_by(name='Manager').first()
    if manager_role and not UserRole.query.filter_by(user_id=int(user_id), role_id=manager_role.id).first():
        db.session.add(UserRole(user_id=int(user_id), role_id=manager_role.id))

def create_initial_admin(username, password, full_name=None, email=None):
    """Create the first administrator account for a fresh installation."""
    if _users_exist():
        raise ValueError("Initial setup is locked because a user already exists")

    dept = _get_or_create_initial_department()
    admin = User(
        username=username,
        password=generate_password_hash(password),
        full_name=full_name or 'System Administrator',
        email=email or None,
        role='admin',
        department_id=dept.id
    )
    db.session.add(admin)
    db.session.flush()
    dept.manager_id = admin.id
    _assign_admin_role(admin)
    db.session.commit()
    return admin

# --- Device Hierarchy Configuration ---
DEVICE_TYPE_CATEGORIES = (
    'Thiết bị IT',
    'Hạ tầng IT',
    'Thiết bị văn phòng',
    'Thiết bị dùng chung',
    'Thiết bị tiêu hao',
    'Khác',
)

def _get_device_type_category_choices():
    existing = {
        (category or '').strip()
        for category, in db.session.query(DeviceType.category).distinct().all()
        if (category or '').strip()
    }
    if not existing:
        return list(DEVICE_TYPE_CATEGORIES)
    ordered = [category for category in DEVICE_TYPE_CATEGORIES if category in existing]
    ordered.extend(sorted(existing.difference(ordered)))
    return ordered

# Helper to return device hierarchy from database dynamically.
def _get_device_type_hierarchy():
    hierarchy = {}
    
    # 1. Fetch all DeviceType records
    try:
        all_types = DeviceType.query.order_by(DeviceType.name).all()
        for dt in all_types:
            cat = (dt.category or '').strip() or 'Khác'
            name = (dt.name or '').strip()
            if not name:
                continue
            if cat not in hierarchy:
                hierarchy[cat] = []
            hierarchy[cat].append(name)
    except Exception:
        pass
        
    if not hierarchy:
        hierarchy['Thiết bị IT'] = []
    
    # Sort types within each category
    for cat in hierarchy:
        hierarchy[cat].sort()
        
    return hierarchy

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

def _default_device_type_prefixes():
    return {
        'Laptop': 'LT',
        'Case máy tính': 'PC',
        'Màn hình': 'MH',
        'Màn hình máy tính': 'MH',
        'Bàn phím': 'BP',
        'Chuột': 'CH',
        'Ổ cứng': 'OC',
        'Thiết bị lưu trữ': 'LUTRU',
        'Ram': 'RAM',
        'Card màn hình': 'VGA',
        'Máy in': 'MI',
        'Máy chiếu': 'MC',
        'Máy scan': 'SC',
        'Máy chấm công': 'MCC',
        'Thiết bị mạng': 'NET',
        'Server': 'SV',
        'Camera': 'CAM',
        'Điện thoại': 'DT',
        'Ổ điện': 'OD',
        'Dây mạng': 'DM',
        'Cáp kết nối': 'CAP',
        'Linh kiện khác': 'LK',
        'Thiết bị dùng chung khác': 'TBDC',
        'Thiết bị điện khác': 'TDD',
        'Thiết bị điện văn phòng': 'TDVP',
        'Thiết bị khác': 'TBK',
        'Thiết bị văn phòng khác': 'TBVP',
        'Switch mạng': 'SW',
        'Router': 'RTR',
        'Firewall': 'FW',
        'Access Point': 'AP',
        'Thiết bị cân bằng tải': 'LB',
        'Camera IP': 'CAM',
        'Đầu ghi camera': 'NVR',
        'Camera chấm công': 'CAMCC',
    }

def _default_device_type_categories():
    return {
        'Laptop': 'Thiết bị IT',
        'Case máy tính': 'Thiết bị IT',
        'Màn hình': 'Thiết bị IT',
        'Màn hình máy tính': 'Thiết bị IT',
        'Bàn phím': 'Thiết bị IT',
        'Chuột': 'Thiết bị IT',
        'Ổ cứng': 'Thiết bị IT',
        'Thiết bị lưu trữ': 'Thiết bị tiêu hao',
        'Ram': 'Thiết bị IT',
        'Card màn hình': 'Thiết bị IT',
        'Máy in': 'Thiết bị văn phòng',
        'Máy chiếu': 'Thiết bị văn phòng',
        'Máy scan': 'Thiết bị văn phòng',
        'Điện thoại': 'Thiết bị văn phòng',
        'Thiết bị điện văn phòng': 'Thiết bị văn phòng',
        'Thiết bị văn phòng khác': 'Thiết bị văn phòng',
        'Ổ điện': 'Thiết bị dùng chung',
        'Thiết bị dùng chung khác': 'Thiết bị dùng chung',
        'Thiết bị điện khác': 'Thiết bị dùng chung',
        'Thiết bị mạng': 'Hạ tầng IT',
        'Switch mạng': 'Hạ tầng IT',
        'Router': 'Hạ tầng IT',
        'Firewall': 'Hạ tầng IT',
        'Access Point': 'Hạ tầng IT',
        'Thiết bị cân bằng tải': 'Hạ tầng IT',
        'Server': 'Hạ tầng IT',
        'Camera': 'Hạ tầng IT',
        'Camera IP': 'Hạ tầng IT',
        'Đầu ghi camera': 'Hạ tầng IT',
        'Camera chấm công': 'Hạ tầng IT',
        'Máy chấm công': 'Hạ tầng IT',
        'Dây mạng': 'Thiết bị tiêu hao',
        'Cáp kết nối': 'Thiết bị tiêu hao',
        'Linh kiện khác': 'Hạ tầng IT',
        'Thiết bị khác': 'Thiết bị dùng chung',
    }

def _normalize_device_type_prefix(prefix):
    return (prefix or '').strip().upper()

def _is_valid_device_type_prefix(prefix):
    return bool(re.fullmatch(r'[A-Z0-9]{1,20}', prefix or ''))

def sync_device_type_prefixes():
    """Seed default device type prefixes for old and fresh databases."""
    try:
        defaults = _default_device_type_prefixes()
        default_categories = _default_device_type_categories()
        if DeviceType.query.count() == 0:
            for name, prefix in defaults.items():
                db.session.add(DeviceType(
                    name=name,
                    category=default_categories.get(name, 'Khác'),
                    code_prefix=prefix
                ))
        else:
            for dt in DeviceType.query.all():
                default_prefix = defaults.get(dt.name)
                if default_prefix and (not dt.code_prefix or dt.name in {'Case máy tính', 'Màn hình', 'Màn hình máy tính'}):
                    dt.code_prefix = default_prefix
                default_category = default_categories.get(dt.name)
                if default_category and not (dt.category or '').strip():
                    dt.category = default_category
            existing_names = {dt.name for dt in DeviceType.query.all()}
            for name in [
                'Switch mạng', 'Router', 'Firewall', 'Access Point',
                'Thiết bị cân bằng tải', 'Camera IP', 'Đầu ghi camera', 'Camera chấm công'
            ]:
                if name not in existing_names:
                    db.session.add(DeviceType(
                        name=name,
                        category=default_categories[name],
                        code_prefix=defaults.get(name)
                    ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Device type prefix sync error: {e}")

STOCK_CATEGORY_DEFAULTS = [
    ('Cáp mạng', 'CAP', ['Chuẩn / tốc độ', 'Độ dài', 'Màu sắc', 'Đầu nối']),
    ('Cáp quang', 'FO', ['Loại sợi quang', 'Số core', 'Độ dài', 'Đầu nối']),
    ('Cáp màn hình', 'DISP', ['Chuẩn', 'Độ dài', 'Đầu nối A', 'Đầu nối B']),
    ('Module quang', 'SFP', ['Tốc độ', 'Bước sóng', 'Khoảng cách', 'Chuẩn / đầu nối']),
    ('Thiết bị lưu trữ', 'STO', ['Dung lượng', 'Chuẩn kết nối', 'Tốc độ đọc/ghi']),
    ('Nguồn & adapter', 'PWR', ['Công suất', 'Điện áp vào', 'Điện áp ra', 'Đầu nối']),
    ('Phụ kiện mạng', 'NET', ['Số cổng', 'Tốc độ', 'Chuẩn kết nối']),
    ('Linh kiện IT', 'COMP', ['Chuẩn tương thích', 'Dung lượng / thông số', 'Bảo hành']),
]

def _stock_category_fields(category):
    try:
        values = json.loads(category.specification_fields or '[]')
        if not isinstance(values, list):
            return []
        return [str(value).strip() for value in values if str(value).strip()][:20]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

def _stock_item_specifications(item):
    try:
        values = json.loads(item.specifications or '{}')
        if not isinstance(values, dict):
            return {}
        return {str(key): str(value) for key, value in values.items() if str(key).strip() and str(value).strip()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

def _seed_stock_item_categories():
    try:
        for name, prefix, fields in STOCK_CATEGORY_DEFAULTS:
            if not StockItemCategory.query.filter_by(name=name).first():
                db.session.add(StockItemCategory(
                    name=name,
                    code_prefix=prefix,
                    specification_fields=json.dumps(fields, ensure_ascii=False),
                ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"Stock category seed error: {exc}")

STOCK_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}

def _stock_images_dir():
    path = os.path.join(app.root_path, 'static', 'uploads', 'stock')
    os.makedirs(path, exist_ok=True)
    return path

def _save_stock_image_file(file_storage, prefix='stock'):
    if not file_storage or not getattr(file_storage, 'filename', None):
        return None
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in STOCK_IMAGE_EXTENSIONS:
        return None
    filename = f"{prefix}_{uuid.uuid4().hex[:12]}.{ext}"
    file_storage.save(os.path.join(_stock_images_dir(), filename))
    return filename

def _stock_image_list(image_value):
    if not image_value:
        return []
    try:
        data = json.loads(image_value)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    if isinstance(image_value, str) and image_value.strip():
        return [image_value.strip()]
    return []

def _normalize_stock_prefix(prefix):
    return re.sub(r'[^A-Z0-9]', '', (prefix or '').upper())[:20] or 'VT'

def _generate_stock_item_code(category):
    prefix = _normalize_stock_prefix(category.code_prefix if category else '')
    pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.IGNORECASE)
    max_number = 0
    for (code,) in db.session.query(StockItem.code).filter(StockItem.code.ilike(f'{prefix}-%')).all():
        match = pattern.match(code or '')
        if match:
            max_number = max(max_number, int(match.group(1)))
    for number in range(max_number + 1, max_number + 10000):
        candidate = f'{prefix}-{number:03d}'
        if not StockItem.query.filter(func.upper(StockItem.code) == candidate).first():
            return candidate
    return f'{prefix}-{datetime.utcnow().strftime("%H%M%S")}'

def _parse_stock_specifications(raw, category):
    try:
        values = json.loads(raw or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError('Thông số riêng không hợp lệ.')
    if not isinstance(values, dict):
        raise ValueError('Thông số riêng không hợp lệ.')
    allowed_fields = set(_stock_category_fields(category))
    return {
        field: str(values.get(field) or '').strip()
        for field in allowed_fields
        if str(values.get(field) or '').strip()
    }

def _generate_unit_codes(item, count=1):
    prefix = f"QLTB-{item.code}"
    pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.IGNORECASE)
    existing_codes = db.session.query(StockItemUnit.unit_code).filter(StockItemUnit.unit_code.ilike(f'{prefix}-%')).all()
    max_num = 0
    for (code,) in existing_codes:
        match = pattern.match(code or '')
        if match:
            max_num = max(max_num, int(match.group(1)))
    
    codes = []
    for i in range(1, count + 1):
        num = max_num + i
        codes.append(f"{prefix}-{num:03d}")
    return codes

def _record_stock_item_movement(item, movement_type, quantity, *, movement_date=None,
                                receiver_id=None, supplier='', reference_code='', reason='', notes='',
                                unit_serials=None, selected_unit_ids=None):
    before_quantity = item.current_quantity or 0
    if movement_type == 'Nhập':
        after_quantity = before_quantity + quantity
    elif movement_type == 'Xuất':
        if item.track_units and selected_unit_ids:
            quantity = len(selected_unit_ids)
        if quantity > before_quantity:
            raise ValueError('Số lượng xuất lớn hơn tồn kho hiện tại.')
        after_quantity = before_quantity - quantity
    elif movement_type == 'Điều chỉnh':
        if quantity < 0:
            raise ValueError('Tồn kho điều chỉnh không được âm.')
        after_quantity = quantity
        quantity = abs(after_quantity - before_quantity)
    else:
        raise ValueError('Loại phiếu không hợp lệ.')

    item.current_quantity = after_quantity
    item.updated_at = datetime.utcnow()
    movement = StockItemMovement(
        item=item,
        movement_type=movement_type,
        quantity=quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        movement_date=movement_date or date.today(),
        receiver_id=receiver_id,
        actor_id=session.get('user_id'),
        supplier=supplier,
        reference_code=reference_code,
        reason=reason,
        notes=notes,
    )
    db.session.add(movement)
    db.session.flush()

    if item.track_units:
        if movement_type == 'Nhập':
            codes = _generate_unit_codes(item, quantity)
            serials = unit_serials or []
            for i in range(quantity):
                sn = serials[i].strip() if i < len(serials) and serials[i] and str(serials[i]).strip() else None
                unit = StockItemUnit(
                    item_id=item.id,
                    unit_code=codes[i],
                    serial_number=sn,
                    status='Trong kho',
                    location=item.location,
                )
                db.session.add(unit)
                db.session.flush()
                um = StockItemUnitMovement(
                    movement_id=movement.id,
                    unit_id=unit.id,
                    action='Nhập'
                )
                db.session.add(um)
        elif movement_type == 'Xuất':
            if selected_unit_ids:
                units = StockItemUnit.query.filter(
                    StockItemUnit.id.in_(selected_unit_ids),
                    StockItemUnit.item_id == item.id
                ).all()
                for unit in units:
                    if unit.status != 'Trong kho':
                        raise ValueError(f'Thiết bị {unit.unit_code} không ở trạng thái Trong kho.')
                    unit.status = 'Đã xuất'
                    unit.assigned_to_id = receiver_id
                    unit.updated_at = datetime.utcnow()
                    um = StockItemUnitMovement(
                        movement_id=movement.id,
                        unit_id=unit.id,
                        action='Xuất'
                    )
                    db.session.add(um)

    return movement

def _get_device_type_code_prefix(device_type_name):
    name = (device_type_name or '').strip()
    if not name:
        return None
    device_type = DeviceType.query.filter(func.lower(DeviceType.name) == name.lower()).first()
    prefix = _normalize_device_type_prefix(device_type.code_prefix if device_type else None)
    if not prefix:
        prefix = _default_device_type_prefixes().get(name, '')
    return prefix if _is_valid_device_type_prefix(prefix) else None

def generate_device_code_for_type(device_type_name, reserved_codes=None):
    prefix = _get_device_type_code_prefix(device_type_name)
    if not prefix:
        return None

    pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.IGNORECASE)
    max_num = 0
    existing_codes = db.session.query(Device.device_code).filter(Device.device_code.ilike(f'{prefix}-%')).all()
    for row in existing_codes:
        match = pattern.match(row[0] or '')
        if match:
            max_num = max(max_num, int(match.group(1)))

    reserved = set(reserved_codes or [])
    next_num = max_num + 1
    while True:
        code = f'{prefix}-{next_num:03d}'
        if code not in reserved and not Device.query.filter_by(device_code=code).first():
            return code
        next_num += 1

# --- Ensure tables exist and run lightweight schema migrations ---
_tables_initialized = False

def _sql_literal(value):
    """Render a safe SQL literal for simple defaults used in migrations."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"

def ensure_missing_model_columns():
    """Check all existing tables and add newly introduced model columns."""
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            existing_tables = set(inspector.get_table_names())
            model_tables = db.metadata.tables

            with db.engine.connect() as conn:
                for table_name, table in model_tables.items():
                    if table_name not in existing_tables:
                        continue

                    db_columns = {col['name'] for col in inspector.get_columns(table_name)}
                    for col in table.columns:
                        if col.primary_key or col.name in db_columns:
                            continue

                        # Avoid risky NOT NULL adds when no default exists.
                        default_value = None
                        has_scalar_default = bool(col.default is not None and getattr(col.default, 'is_scalar', False))
                        if has_scalar_default:
                            default_value = col.default.arg

                        if not col.nullable and default_value is None:
                            print(f"[SKIP] {table_name}.{col.name} is NOT NULL without default")
                            continue

                        col_type = col.type.compile(dialect=db.engine.dialect)
                        stmt = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}'
                        if default_value is not None:
                            stmt += f" DEFAULT {_sql_literal(default_value)}"
                        if not col.nullable:
                            stmt += " NOT NULL"

                        try:
                            conn.execute(text(stmt))
                            conn.commit()
                            print(f"[OK] Added missing column {table_name}.{col.name}")
                        except Exception as e:
                            # Keep startup resilient if a specific column cannot be added.
                            print(f"[WARN] Could not add {table_name}.{col.name}: {e}")
        except Exception as e:
            print(f"Schema check error: {e}")

@app.before_request
def ensure_tables_once():
    global _tables_initialized
    if not _tables_initialized:
        try:
            db.create_all()
            ensure_missing_model_columns()
            sync_device_type_prefixes()
            _seed_stock_item_categories()
            # Skip SQLite-specific migrations when using external DBs (e.g., PostgreSQL)
            if is_external_database():
                _tables_initialized = True
                return
            # Lightweight schema versioning and migrations (SQLite-safe)
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    # Create schema_version table if not exists
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS schema_version (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            version INTEGER NOT NULL
                        )
                    """))
                    # Initialize to version 0 if empty
                    existing = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).fetchone()
                    if not existing:
                        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 0)"))

                    def get_version():
                        row = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).fetchone()
                        return int(row[0]) if row and row[0] is not None else 0

                    def set_version(v):
                        conn.execute(text("UPDATE schema_version SET version=:v WHERE id=1"), {"v": v})

                    # Define forward-only migrations
                    current_version = get_version()
                    target_version = 3  # bump when adding new migrations

                    # Migration 1: ensure audit_log and server_room_device_info base
                    if current_version < 1:
                        conn.execute(text("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type VARCHAR(50) NOT NULL, entity_id INTEGER NOT NULL, changed_by INTEGER, changed_at DATETIME DEFAULT CURRENT_TIMESTAMP, changes TEXT)"))
                        # REMOVED server_room_device_info creation
                        set_version(1)

                    # Migration 2: add missing columns for inventory and proposals
                    if get_version() < 2:
                        # Removed inventory_receipt_item migrations
                        info2 = conn.execute(text("PRAGMA table_info('config_proposal')")).fetchall()
                        cols2 = {row[1] for row in info2}
                        if info2:
                            if 'currency' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN currency VARCHAR(10) DEFAULT 'VND'"))
                            if 'status' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN status VARCHAR(30) DEFAULT 'Mới tạo'"))
                            if 'purchase_status' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN purchase_status VARCHAR(30) DEFAULT 'Lấy báo giá'"))
                            if 'notes' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN notes TEXT"))
                            # Removed linked_receipt_id migration
                            if 'supplier_info' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN supplier_info VARCHAR(255)"))

                        # Removed inventory_receipt config_proposal_id migration

                        info4 = conn.execute(text("PRAGMA table_info('user')")).fetchall()
                        cols4 = {row[1] for row in info4}
                        if info4 and 'last_name_token' not in cols4:
                            conn.execute(text("ALTER TABLE user ADD COLUMN last_name_token VARCHAR(120)"))

                        info5 = conn.execute(text("PRAGMA table_info('config_proposal_item')")).fetchall()
                        cols5 = {row[1] for row in info5}
                        if info5 and 'product_code' not in cols5:
                            conn.execute(text("ALTER TABLE config_proposal_item ADD COLUMN product_code VARCHAR(100)"))

                        set_version(2)

                    # Migration 3: Was server_room_device_info.usage_status - REMOVED
                    if get_version() < 3:
                        set_version(3)

                    conn.commit()
            except Exception:
                # Migration failures should not break app startup
                pass
            # Ensure new columns exist for InventoryReceiptItem if the table was created earlier
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    # Removed InventoryReceiptItem columns
                    # ConfigProposal new columns
                    info2 = conn.execute(text("PRAGMA table_info('config_proposal')")).fetchall()
                    cols2 = {row[1] for row in info2}
                    alter_stmts = []
                    if info2:  # table exists
                        if 'currency' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN currency VARCHAR(10) DEFAULT 'VND'")
                        if 'status' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN status VARCHAR(30) DEFAULT 'Mới tạo'")
                        if 'purchase_status' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN purchase_status VARCHAR(30) DEFAULT 'Lấy báo giá'")
                        if 'notes' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN notes TEXT")
                        # Removed linked_receipt_id migration
                        if 'supplier_info' not in cols2:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN supplier_info VARCHAR(255)")
                    # Removed InventoryReceipt new link column
                    
                    # Migration 4: ConfigProposal quantity and MaintenanceLog reported_by
                    info7 = conn.execute(text("PRAGMA table_info('config_proposal')")).fetchall()
                    cols7 = {row[1] for row in info7}
                    if info7:
                        if 'quantity' not in cols7:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN quantity INTEGER DEFAULT 1")
                        if 'vat_percent' not in cols7:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN vat_percent FLOAT DEFAULT 10.0")
                        if 'vat_amount' not in cols7:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN vat_amount FLOAT DEFAULT 0.0")
                        if 'subtotal' not in cols7:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN subtotal FLOAT DEFAULT 0.0")
                        if 'total_amount' not in cols7:
                            alter_stmts.append("ALTER TABLE config_proposal ADD COLUMN total_amount FLOAT DEFAULT 0.0")
                    
                    info8 = conn.execute(text("PRAGMA table_info('device_maintenance_log')")).fetchall()
                    cols8 = {row[1] for row in info8}
                    if info8 and 'reported_by' not in cols8:
                        alter_stmts.append("ALTER TABLE device_maintenance_log ADD COLUMN reported_by INTEGER")
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
                    # ServerRoomDeviceInfo table ensure & migrate - REMOVED
                    for stmt in alter_stmts:
                        conn.execute(text(stmt))
                    if alter_stmts:
                        conn.commit()
            except Exception:
                pass
        except Exception:
            pass
        _tables_initialized = True

# Health endpoint for container health checks is defined below as '/health'

# --- (Các hàm context_processor, home, auth, device routes giữ nguyên) ---
@app.context_processor
def inject_user():
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
        # Admin always has all permissions
        if current_user and current_user.role == 'admin':
            try:
                perm_codes = {p.code for p in Permission.query.all()}
            except Exception:
                perm_codes = set()
        else:
            # derive permission codes for template checks
            role_ids = [ur.role_id for ur in UserRole.query.filter_by(user_id=current_user.id).all()] if current_user else []
            perm_codes = set()
            if role_ids:
                try:
                    for rp in RolePermission.query.filter(RolePermission.role_id.in_(role_ids)).all():
                        perm = Permission.query.get(rp.permission_id)
                        if perm:
                            perm_codes.add(perm.code)
                except Exception:
                    pass
        unread_notifications = current_user.notifications.filter_by(is_read=False).all() if current_user else []
        return dict(
            current_user=current_user,
            current_permissions=perm_codes,
            unread_notifications=unread_notifications,
            device_image_list=_device_image_list,
        )
    return dict(current_user=None, current_permissions=set(), unread_notifications=[], device_image_list=_device_image_list)

from werkzeug.exceptions import abort

@app.route('/notifications/read/<int:notif_id>', methods=['POST', 'GET'])
def read_notification(notif_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id != session['user_id']: return abort(403)
    notif.is_read = True
    db.session.commit()
    if notif.link:
        return redirect(notif.link)
    return redirect(request.referrer or url_for('home'))

@app.route('/notifications/read_all', methods=['POST'])
def read_all_notifications():
    if 'user_id' not in session: return redirect(url_for('login'))
    Notification.query.filter_by(user_id=session['user_id'], is_read=False).update({'is_read': True})
    db.session.commit()
    flash('Đã đánh dấu đọc tất cả.', 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/notifications/delete/<int:notif_id>', methods=['POST'])
def delete_notification(notif_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id != session['user_id']: return abort(403)
    db.session.delete(notif)
    db.session.commit()
    flash('Đã xóa thông báo.', 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/notifications')
def all_notifications():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    pagination = user.notifications.order_by(Notification.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('notifications.html', pagination=pagination)

@app.template_filter('vnd')
def format_vnd(value):
    try:
        n = float(value or 0)
    except Exception:
        n = 0
    return f"{int(round(n, 0)):,}".replace(',', '.')

@app.template_filter('localtime')
def format_localtime(value, fmt='%d-%m-%Y %H:%M'):
    local_dt = _to_vietnam_time(value)
    if not local_dt:
        return ''
    try:
        return local_dt.strftime(fmt)
    except Exception:
        return str(local_dt)

@app.route('/config/roles_permissions', methods=['GET', 'POST'])
def roles_permissions():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Only admin or users with rbac.manage can access
    user = User.query.get(session['user_id'])
    if (user.role != 'admin') and ('rbac.manage' not in _get_current_permissions()):
        flash('Bạn không có quyền truy cập trang phân quyền.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_role_perms':
            # Update role-permission assignments based on form data
            role_id = request.form.get('role_id', type=int)
            perm_codes = request.form.getlist('perm_codes')
            role = Role.query.get_or_404(role_id)
            # Clear existing
            RolePermission.query.filter_by(role_id=role.id).delete()
            # Insert selected
            for code in perm_codes:
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
            db.session.commit()
            flash('Cập nhật quyền của vai trò thành công.', 'success')
            return redirect(url_for('roles_permissions'))
            flash('Cập nhật quyền của vai trò thành công.', 'success')
            return redirect(url_for('roles_permissions'))
        elif action == 'delete_role':
            role_id = request.form.get('role_id', type=int)
            role = Role.query.get_or_404(role_id)
            if role.name == 'Admin':
                flash('Không thể xóa vai trò Admin.', 'danger')
                return redirect(url_for('roles_permissions'))
            # Remove role assignments and role-permissions, then delete role
            UserRole.query.filter_by(role_id=role.id).delete()
            RolePermission.query.filter_by(role_id=role.id).delete()
            db.session.delete(role)
            db.session.commit()
            flash('Đã xóa vai trò.', 'success')
            return redirect(url_for('roles_permissions'))
        elif action == 'add_permission':
            code = (request.form.get('new_perm_code') or '').strip()
            name = (request.form.get('new_perm_name') or '').strip()
            if not code or not name:
                flash('Mã và tên quyền không được để trống.', 'danger')
            elif Permission.query.filter_by(code=code).first():
                flash('Quyền đã tồn tại.', 'warning')
            else:
                db.session.add(Permission(code=code, name=name))
                db.session.commit()
                flash('Đã thêm quyền mới.', 'success')
            return redirect(url_for('roles_permissions'))
        elif action == 'delete_permission':
            perm_id = request.form.get('perm_id', type=int)
            perm = Permission.query.get_or_404(perm_id)
            # Also remove role links
            RolePermission.query.filter_by(permission_id=perm.id).delete()
            db.session.delete(perm)
            db.session.commit()
            flash('Đã xóa quyền.', 'success')
            return redirect(url_for('roles_permissions'))

    roles = Role.query.order_by(Role.name).all()
    permissions = Permission.query.order_by(Permission.code).all()
    role_to_perms = {r.id: [rp.permission.code for rp in r.role_permissions] for r in roles}
    
    # Group permissions by module/feature
    permission_groups = {
        'Thiết bị': ['devices.view', 'devices.edit', 'devices.delete'],
        'Nhóm thiết bị': ['device_groups.view', 'device_groups.edit', 'device_groups.delete'],
        'Phòng server': ['server_room.view', 'server_room.edit', 'server_room.delete'],
        'Bàn giao thiết bị': ['handovers.view', 'handovers.edit', 'handovers.delete'],
        'Phiếu nhập kho': ['inventory.view', 'inventory.edit', 'inventory.delete'],
        'Đề xuất thiết bị': ['config_proposals.view', 'config_proposals.edit', 'config_proposals.delete'],
        'Báo lỗi': ['bug_reports.create', 'bug_reports.view', 'bug_reports.edit', 'bug_reports.delete', 'bug_reports.assign'],
        'Người dùng': ['users.view', 'users.edit', 'users.delete'],
        'Phòng ban': ['departments.view', 'departments.edit', 'departments.delete'],
        'Dashboard': ['dashboard.view'],
        'Backup': ['backup.view', 'backup.edit', 'backup.delete'],
        'Phân quyền': ['rbac.view', 'rbac.edit', 'rbac.delete', 'rbac.manage'],
        'Bảo trì': ['maintenance.view', 'maintenance.add', 'maintenance.edit', 'maintenance.delete', 'maintenance.upload', 'maintenance.download'],
        'Bảo trì': ['maintenance.view', 'maintenance.add', 'maintenance.edit', 'maintenance.delete', 'maintenance.upload', 'maintenance.download'],
        'Báo lỗi nâng cao': ['bug_reports.manage_advanced'],
        'Chấm công & Hikvision': ['attendance.view', 'attendance.view_all', 'attendance.sync', 'attendance.manage_users', 'attendance.config'],
        'Quy trình mua sắm': ['config_proposals.create', 'config_proposals.approve_team', 'config_proposals.consult_it', 'config_proposals.review_finance', 'config_proposals.approve_director', 'config_proposals.execute_purchase', 'config_proposals.execute_accounting', 'config_proposals.confirm_delivery']
    }
    
    # Build actual groups from existing permissions
    actual_groups = {}
    perm_code_to_name = {p.code: p.name for p in permissions}
    for group_name, codes in permission_groups.items():
        actual_groups[group_name] = []
        for code in codes:
            perm = next((p for p in permissions if p.code == code), None)
            if perm:
                actual_groups[group_name].append(perm)
    
    # Add any permissions not in groups to "Khác"
    grouped_codes = set()
    for codes in permission_groups.values():
        grouped_codes.update(codes)
    other_perms = [p for p in permissions if p.code not in grouped_codes]
    if other_perms:
        actual_groups['Khác'] = other_perms
    
    return render_template('roles_permissions.html', roles=roles, permissions=permissions, role_to_perms=role_to_perms, permission_groups=actual_groups)

@app.route('/roles')
def roles_list():
    """Danh sách quyền với các cột: STT, Tên quyền, mô tả, ngày tạo, Hành động"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if (user.role != 'admin') and ('rbac.manage' not in _get_current_permissions()):
        flash('Bạn không có quyền truy cập trang phân quyền.', 'danger')
        return redirect(url_for('home'))
    
    roles = Role.query.order_by(Role.created_at.desc()).all()
    return render_template('roles/list.html', roles=roles)

@app.route('/roles/add', methods=['GET', 'POST'])
def add_role():
    """Thêm vai trò mới"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if (user.role != 'admin') and ('rbac.manage' not in _get_current_permissions()):
        flash('Bạn không có quyền truy cập trang phân quyền.', 'danger')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        
        if not name:
            flash('Tên vai trò không được để trống.', 'danger')
        elif Role.query.filter_by(name=name).first():
            flash('Tên vai trò đã tồn tại. Vui lòng chọn tên khác.', 'warning')
        else:
            try:
                new_role = Role(name=name, description=description)
                db.session.add(new_role)
                db.session.commit()
                flash('Đã thêm vai trò mới thành công.', 'success')
                return redirect(url_for('roles_list'))
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi khi thêm vai trò: {str(e)}', 'danger')
                
    return render_template('roles/add.html')

@app.route('/roles/<int:role_id>', methods=['GET', 'POST'])
def role_detail(role_id):
    """Chi tiết quyền với 2 tab: Chức năng và Danh sách người dùng"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if (user.role != 'admin') and ('rbac.manage' not in _get_current_permissions()):
        flash('Bạn không có quyền truy cập trang phân quyền.', 'danger')
        return redirect(url_for('home'))
    
    role = Role.query.get_or_404(role_id)
    tab = request.args.get('tab', 'permissions')
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save_role_perms':
            # Cập nhật quyền của vai trò
            perm_codes = request.form.getlist('perm_codes')
            RolePermission.query.filter_by(role_id=role.id).delete()
            for code in perm_codes:
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
            db.session.commit()
            flash('Cập nhật quyền của vai trò thành công.', 'success')
            return redirect(url_for('role_detail', role_id=role_id, tab='permissions'))
        
        elif action == 'update_role':
            # Cập nhật tên và mô tả quyền
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            if name and name != role.name:
                # Kiểm tra trùng tên
                if Role.query.filter(Role.name == name, Role.id != role.id).first():
                    flash('Tên quyền đã tồn tại.', 'danger')
                    return redirect(url_for('role_detail', role_id=role_id, tab='permissions'))
                else:
                    role.name = name
            role.description = description
            db.session.commit()
            flash('Cập nhật quyền thành công.', 'success')
            return redirect(url_for('role_detail', role_id=role_id, tab='permissions'))
        
        elif action == 'add_user_to_role':
            # Thêm người dùng vào quyền
            user_id = request.form.get('user_id', type=int)
            if user_id:
                existing = UserRole.query.filter_by(user_id=user_id, role_id=role.id).first()
                if not existing:
                    db.session.add(UserRole(user_id=user_id, role_id=role.id))
                    db.session.commit()
                    flash('Đã thêm người dùng vào quyền.', 'success')
                else:
                    flash('Người dùng đã có quyền này.', 'warning')
            return redirect(url_for('role_detail', role_id=role_id, tab='users'))
        
        elif action == 'remove_user_from_role':
            # Xóa người dùng khỏi quyền
            user_id = request.form.get('user_id', type=int)
            if user_id:
                UserRole.query.filter_by(user_id=user_id, role_id=role.id).delete()
                db.session.commit()
                flash('Đã xóa người dùng khỏi quyền.', 'success')
            return redirect(url_for('role_detail', role_id=role_id, tab='users'))
    
    # GET request
    permissions = Permission.query.order_by(Permission.code).all()
    role_perms = [rp.permission.code for rp in role.role_permissions]
    
    # Group permissions
    permission_groups = {
        'Thiết bị': ['devices.view', 'devices.edit', 'devices.delete'],
        'Nhóm thiết bị': ['device_groups.view', 'device_groups.edit', 'device_groups.delete'],
        'Phòng server': ['server_room.view', 'server_room.edit', 'server_room.delete'],
        'Bàn giao thiết bị': ['handovers.view', 'handovers.edit', 'handovers.delete'],
        'Phiếu nhập kho': ['inventory.view', 'inventory.edit', 'inventory.delete'],
        'Đề xuất thiết bị': ['config_proposals.view', 'config_proposals.edit', 'config_proposals.delete'],
        'Báo lỗi': ['bug_reports.create', 'bug_reports.view', 'bug_reports.edit', 'bug_reports.delete', 'bug_reports.assign'],
        'Người dùng': ['users.view', 'users.edit', 'users.delete'],
        'Phòng ban': ['departments.view', 'departments.edit', 'departments.delete'],
        'Backup': ['backup.view', 'backup.edit', 'backup.delete'],
        'Phân quyền': ['rbac.view', 'rbac.edit', 'rbac.delete', 'rbac.manage'],
        'Bảo trì': ['maintenance.view', 'maintenance.add', 'maintenance.edit', 'maintenance.delete', 'maintenance.upload', 'maintenance.download']
    }
    
    actual_groups = {}
    for group_name, codes in permission_groups.items():
        actual_groups[group_name] = []
        for code in codes:
            perm = next((p for p in permissions if p.code == code), None)
            if perm:
                actual_groups[group_name].append(perm)
    
    grouped_codes = set()
    for codes in permission_groups.values():
        grouped_codes.update(codes)
    other_perms = [p for p in permissions if p.code not in grouped_codes]
    if other_perms:
        actual_groups['Khác'] = other_perms
    
    # Lấy danh sách người dùng trong quyền với phân trang
    user_roles = UserRole.query.filter_by(role_id=role.id).all()
    user_ids_in_role = [ur.user_id for ur in user_roles]
    
    # Phân trang cho danh sách người dùng trong quyền
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    if user_ids_in_role:
        users_query = User.query.filter(User.id.in_(user_ids_in_role)).filter(
            ~User.status.in_(['Đã nghỉ', 'Nghỉ việc'])
        ).order_by(User.full_name, User.username)
        users_in_role_pagination = users_query.paginate(page=page, per_page=per_page, error_out=False)
        users_in_role = users_in_role_pagination.items
    else:
        users_in_role_pagination = None
        users_in_role = []
    
    # Lấy danh sách tất cả người dùng để thêm vào quyền (loại trừ người đã nghỉ việc)
    all_users = User.query.filter(
        ~User.status.in_(['Nghỉ không lương', 'Nghỉ việc'])
    ).order_by(User.full_name, User.username).all()
    
    return render_template('roles/detail.html', role=role, permissions=permissions, 
                         role_perms=role_perms, permission_groups=actual_groups,
                         users_in_role=users_in_role, users_in_role_pagination=users_in_role_pagination,
                         all_users=all_users, tab=tab)

@app.route('/health')
def health_check():
    """Health check endpoint for load balancers and monitoring"""
    try:
        # Check database connection
        with db.engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_status = 'healthy'
    except Exception as e:
        db_status = f'unhealthy: {str(e)}'
    
    # Check if we can access the database
    try:
        user_count = User.query.count()
        user_status = 'healthy'
    except Exception as e:
        user_status = f'unhealthy: {str(e)}'
    
    health_data = {
        'status': 'healthy' if db_status == 'healthy' and user_status == 'healthy' else 'unhealthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': db_status,
        'users': user_status,
        'version': '2.0.0'
    }
    
    status_code = 200 if health_data['status'] == 'healthy' else 503
    return jsonify(health_data), status_code

@app.route('/')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not _has_dashboard_access(current_permissions, current_user):
        flash('Bạn không có quyền truy cập Dashboard. Đang chuyển đến danh sách thiết bị.', 'warning')
        return redirect(url_for('device_list'))
    
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
@app.route('/setup', methods=['GET', 'POST'])
def first_run_setup():
    if _users_exist():
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip()

        if not username or not password:
            flash('Ten dang nhap va mat khau la bat buoc.', 'danger')
            return render_template('setup.html')
        if password != confirm_password:
            flash('Mat khau xac nhan khong khop.', 'danger')
            return render_template('setup.html')
        if len(password) < 8:
            flash('Mat khau admin nen co it nhat 8 ky tu.', 'danger')
            return render_template('setup.html')

        try:
            admin = create_initial_admin(username, password, full_name, email)
            session['user_id'] = admin.id
            session.permanent = True
            flash('Da tao tai khoan quan tri dau tien.', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Khong the tao tai khoan quan tri: {str(e)}', 'danger')

    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not _users_exist():
        return redirect(url_for('first_run_setup'))
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        user = User.query.filter(func.lower(User.username) == func.lower(username)).first()
        if user and check_password_hash(user.password, password):
            # Kiểm tra trạng thái người dùng
            if user.status in ['Nghỉ không lương', 'Nghỉ việc']:
                flash('Tài khoản của bạn đã bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.', 'danger')
                return render_template('login.html')
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Đăng nhập thành công!', 'success')
            perms = _get_current_permissions()
            if _has_dashboard_access(perms, user):
                return redirect(url_for('home'))
            return redirect(url_for('device_list'))
        flash('Tên đăng nhập hoặc mật khẩu không đúng', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
def user_profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        user.telegram_chat_id = request.form.get('telegram_chat_id', '').strip()
        file = request.files.get('avatar')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            import uuid
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            new_filename = f"{user.id}_{uuid.uuid4().hex}.{ext}" if ext else f"{user.id}_{uuid.uuid4().hex}"
            
            upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'avatars')
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, new_filename))
            
            user.avatar = new_filename
            
        db.session.commit()
        flash('Cập nhật thông tin thành công!', 'success')
        return redirect(url_for('user_profile'))
            
    return render_template('profile.html', user=user)

@app.route('/save_dashboard_device_types', methods=['POST'])
def save_dashboard_device_types():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if not _has_dashboard_access():
        flash('Bạn không có quyền chỉnh sửa Dashboard.', 'danger')
        return redirect(url_for('device_list'))
    
    selected_types = request.form.getlist('selected_device_types')
    session['dashboard_device_types'] = selected_types
    flash('Đã lưu cài đặt thống kê theo loại thiết bị.', 'success')
    return redirect(url_for('home'))

@app.route('/save_dashboard_departments', methods=['POST'])
def save_dashboard_departments():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if not _has_dashboard_access():
        flash('Bạn không có quyền chỉnh sửa Dashboard.', 'danger')
        return redirect(url_for('device_list'))
    
    selected_departments = request.form.getlist('selected_departments')
    session['dashboard_departments'] = selected_departments
    flash('Đã lưu cài đặt thống kê theo phòng ban.', 'success')
    return redirect(url_for('home'))

# --- Department Management Routes ---
@app.route('/departments')
def list_departments():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    # Kiểm tra phân quyền: chỉ admin hoặc người có quyền departments.view mới được truy cập
    if not (current_user and current_user.role == 'admin') and 'departments.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    
    departments = Department.query.all()
    all_departments = Department.query.order_by(Department.order_index).all()
    users = User.query.filter_by(status='Đang làm').all()
    current_permissions = _get_current_permissions()
    
    return render_template('departments/list.html', 
                         departments=departments,
                         all_departments=all_departments,
                         users=users,
                         current_permissions=current_permissions)

@app.route('/departments/<int:id>/users')
def department_users(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    current_permissions = _get_current_permissions()
    user = _get_current_user()
    if not (user and user.role == 'admin') and 'departments.view' not in current_permissions and 'departments.edit' not in current_permissions:
        flash('Bạn không có quyền xem người dùng phòng ban.', 'danger')
        return redirect(url_for('list_departments'))
        
    department = Department.query.get_or_404(id)
    available_users = User.query.filter(
        User.status.in_(['Đang làm', 'Thực tập']),
        User.department_id.is_(None)
    ).all()
    
    # Sort and Paginate department users
    page = request.args.get('page', 1, type=int)
    
    # Custom ordering: 
    # 1. Manager (admin or manager role vs user role) 
    # 2. Position containing 'thực tập' (put them last)
    from sqlalchemy import case, String, cast
    
    # Define sort order: 
    # - role='admin' or 'manager' -> order 1
    # - status='Đang làm' -> order 2
    # - status='Thử việc' -> order 3
    # - position like '%thực tập%' -> order 4
    # - else -> order 5
    order_case = case(
        (User.role.in_(['admin', 'manager']), 1),
        (User.status == 'Đang làm', 2),
        (User.status == 'Thử việc', 3),
        (db.func.lower(User.position).like('%thực tập%'), 4),
        else_=5
    )

    pagination = User.query.filter(
        User.department_id == department.id,
        User.status.in_(['Đang làm', 'Thực tập', 'Thử việc'])
    ).order_by(
        order_case,
        db.func.lower(User.last_name_token),
        db.func.lower(User.full_name),
        db.func.lower(User.username)
    ).paginate(page=page, per_page=20, error_out=False)
    
    return render_template('departments/users.html',
                         department=department,
                         available_users=available_users,
                         department_users=pagination.items,
                         pagination=pagination,
                         current_permissions=current_permissions)

@app.route('/departments/<int:id>/users/add', methods=['POST'])
def add_department_user(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    current_permissions = _get_current_permissions()
    user = _get_current_user()
    if not (user and user.role == 'admin') and 'departments.edit' not in current_permissions:
        flash('Bạn không có quyền.', 'danger')
        return redirect(url_for('list_departments'))
        
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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Unauthorized'})
        return redirect(url_for('login'))
        
    current_permissions = _get_current_permissions()
    user = _get_current_user()
    if not (user and user.role == 'admin') and 'departments.edit' not in current_permissions:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Bạn không có quyền quản lý người dùng phòng ban.'})
        flash('Bạn không có quyền.', 'danger')
        return redirect(url_for('list_departments'))
        
    user = User.query.get_or_404(user_id)
    department = Department.query.get_or_404(dept_id)
    
    if user.department_id != department.id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'User not in this department'})
        flash('Người dùng không thuộc phòng ban này.', 'warning')
        return redirect(url_for('department_users', id=dept_id))
        
    user.department_id = None
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True})
    
    flash(f'Đã xóa {user.username} khỏi phòng ban.', 'success')
    return redirect(url_for('department_users', id=dept_id))

@app.route('/departments/<int:id>/users/partial')
def department_users_partial(id):
    if 'user_id' not in session:
        return "Unauthorized", 401
        
    current_permissions = _get_current_permissions()
    user = _get_current_user()
    if not (user and user.role == 'admin') and 'departments.view' not in current_permissions and 'departments.edit' not in current_permissions:
        return "<div class='alert alert-danger'>Bạn không có quyền xem người dùng phòng ban.</div>"
        
    department = Department.query.get_or_404(id)
    current_permissions = _get_current_permissions()
    
    page = request.args.get('page', 1, type=int)
    
    from sqlalchemy import case
    order_case = case(
        (User.role.in_(['admin', 'manager']), 1),
        (User.status == 'Đang làm', 2),
        (User.status == 'Thử việc', 3),
        (db.func.lower(User.position).like('%thực tập%'), 4),
        else_=5
    )

    pagination = User.query.filter(
        User.department_id == department.id,
        User.status.in_(['Đang làm', 'Thực tập', 'Thử việc'])
    ).order_by(
        order_case,
        db.func.lower(User.last_name_token),
        db.func.lower(User.full_name),
        db.func.lower(User.username)
    ).paginate(page=page, per_page=20, error_out=False)
    
    return render_template('departments/_user_list_partial.html',
                         department=department,
                         department_users=pagination.items,
                         pagination=pagination,
                         current_permissions=current_permissions)

@app.route('/departments/add', methods=['GET', 'POST'])
def add_department():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'GET':
        return redirect(url_for('list_departments'))
    
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
        _assign_manager_role(manager_id)
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
        _assign_manager_role(manager_id)
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

@app.route('/departments/export_excel')
def export_departments_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    departments = Department.query.order_by(Department.id).all()
    data = []
    for dept in departments:
        manager_name = dept.manager.full_name if dept.manager else ''
        parent_name = dept.parent.name if dept.parent else ''
        data.append({
            'ID': dept.id,
            'Tên phòng ban': dept.name,
            'Mô tả': dept.description,
            'Phòng ban cha': parent_name,
            'Quản lý': manager_name
        })
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Departments')
    output.seek(0)
    
    return send_file(output, 
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                     as_attachment=True, 
                     download_name=f'departments_list_{datetime.now(VIETNAM_TZ).strftime("%Y%m%d")}.xlsx')

@app.route('/departments/import', methods=['GET', 'POST'])
def import_departments():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ (.xls, .xlsx).', 'danger')
            return redirect(url_for('import_departments'))
            
        try:
            df = pd.read_excel(file, engine='openpyxl')
            
            errors = []
            added_count = 0
            
            for index, row in df.iterrows():
                # Safe header access
                name = str(row.get('Tên phòng ban', '')).strip()
                description = str(row.get('Mô tả', '')).strip() if pd.notna(row.get('Mô tả')) else ''
                manager_username = str(row.get('Tên đăng nhập quản lý', '')).strip() if pd.notna(row.get('Tên đăng nhập quản lý')) else ''
                parent_name = str(row.get('Phòng ban cha', '')).strip() if pd.notna(row.get('Phòng ban cha')) else ''
                
                if not name or name.lower() == 'nan':
                    continue
                
                if Department.query.filter_by(name=name).first():
                    errors.append(f'Dòng {index + 2}: Phòng ban "{name}" đã tồn tại.')
                    continue
                
                manager_id = None
                if manager_username:
                    manager = User.query.filter_by(username=manager_username).first()
                    if manager:
                        manager_id = manager.id
                    else:
                        errors.append(f'Dòng {index + 2}: User quản lý "{manager_username}" không tồn tại.')
                
                parent_id = None
                if parent_name:
                    parent = Department.query.filter_by(name=parent_name).first()
                    if parent:
                        parent_id = parent.id
                
                # Max order logic
                max_order = db.session.query(func.max(Department.order_index)).filter_by(parent_id=parent_id).scalar() or 0
                
                new_dept = Department(
                    name=name,
                    description=description,
                    manager_id=manager_id,
                    parent_id=parent_id,
                    order_index=max_order + 1
                )
                db.session.add(new_dept)
                added_count += 1
                
            if errors:
                for error in errors[:10]:
                    flash(error, 'danger')
                if len(errors) > 10:
                    flash(f'... và {len(errors) - 10} lỗi khác.', 'danger')
                if added_count == 0:
                    db.session.rollback()
                    return redirect(url_for('import_departments'))
                    
            db.session.commit()
            if added_count > 0:
                flash(f'Đã nhập thành công {added_count} phòng ban.', 'success')
            return redirect(url_for('list_departments'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi xử lý file: {str(e)}', 'danger')
            return redirect(url_for('import_departments'))
            
    return render_template('departments/import.html')

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
        new_user = User(username=username, password=generate_password_hash(password), full_name=full_name, email=email, role='user', status='Đang làm')
        db.session.add(new_user); db.session.commit()
        
        default_role = Role.query.filter_by(name='Người dùng').first()
        if default_role:
            db.session.add(UserRole(user_id=new_user.id, role_id=default_role.id))
            db.session.commit()
            
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
            from security import generate_secure_password
            default_password = generate_secure_password()
            user.password = generate_password_hash(default_password)
            db.session.commit()
            
            flash(f'Mật khẩu cho tài khoản "{username}" đã được reset thành công về giá trị mặc định: {default_password}', 'success')
            return redirect(url_for('login'))
        else:
            flash('Tên đăng nhập hoặc Email không chính xác. Vui lòng thử lại.', 'danger')

    return render_template('forgot_password.html')

# ... (Device routes) ...
def get_subordinate_department_ids(dept_id):
    """Get all subordinate department IDs recursively"""
    dept = Department.query.get(dept_id)
    if not dept:
        return []
    
    result = [dept_id]
    for child in dept.children:
        result.extend(get_subordinate_department_ids(child.id))
    return result


@app.route('/devices')
def device_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    user = User.query.get(user_id)
    
    page = request.args.get('page', 1, type=int)
    per_page_arg = request.args.get('per_page', type=int)
    if per_page_arg in (10, 20, 50, 100):
        session['devices_per_page'] = per_page_arg
    per_page = session.get('devices_per_page', 10)
    # Load current filters from query params or session-saved defaults
    saved_filters = session.get('devices_filters', {}) or {}
    filter_device_code = request.args.get('filter_device_code')
    filter_name = request.args.get('filter_name')
    filter_device_types = [value for value in request.args.getlist('filter_device_type') if value]
    filter_device_type = request.args.get('filter_device_type')
    filter_status = request.args.get('filter_status')
    filter_manager_id = request.args.get('filter_manager_id')
    filter_department = request.args.get('filter_department')
    filter_category = request.args.get('filter_category') # New Category Filter
    filter_q = request.args.get('q', '').strip()
    show_all_devices = request.args.get('all') == '1'

    if show_all_devices:
        filter_device_code = ''
        filter_name = ''
        filter_device_types = []
        filter_device_type = ''
        filter_status = ''
        filter_manager_id = ''
        filter_department = ''
        filter_category = ''
        filter_q = ''
    else:
        if filter_device_code is None or filter_device_code == '':
            filter_device_code = saved_filters.get('filter_device_code', '')
        if filter_name is None or filter_name == '':
            filter_name = saved_filters.get('filter_name', '')
        if filter_device_type is None:
            filter_device_type = ''
        if not filter_device_types and filter_device_type:
            filter_device_types = [filter_device_type]
        filter_device_type = filter_device_types[0] if len(filter_device_types) == 1 else ''
        if filter_status is None:
            filter_status = ''
        if filter_manager_id is None or filter_manager_id == '':
            filter_manager_id = saved_filters.get('filter_manager_id', '')
        if filter_department is None or filter_department == '':
            filter_department = saved_filters.get('filter_department', '')
        if filter_category is None or filter_category == '':
            filter_category = saved_filters.get('filter_category', '')
    query = _visible_devices_query_for(user)

    # Apply category filter
    device_hierarchy = _get_device_type_hierarchy()
    ordered_hierarchy = {}
    for preferred in ['Thiết bị IT', 'Hạ tầng IT']:
        if preferred in device_hierarchy:
            ordered_hierarchy[preferred] = device_hierarchy.pop(preferred)
    consumable_types = device_hierarchy.pop('Thiết bị tiêu hao', None)
    for category in sorted(device_hierarchy.keys()):
        ordered_hierarchy[category] = device_hierarchy[category]
    if consumable_types is not None:
        ordered_hierarchy['Thiết bị tiêu hao'] = consumable_types
    device_hierarchy = ordered_hierarchy
    if filter_category and filter_category not in device_hierarchy:
        filter_category = ''
    if not show_all_devices and not filter_category and not filter_device_types and not filter_device_code and not filter_name and not filter_status and not filter_manager_id and not filter_department and not filter_q:
        visible_categories = [category for category in device_hierarchy if category != 'Thiết bị tiêu hao']
        if 'Thiết bị IT' in visible_categories:
            filter_category = 'Thiết bị IT'
        elif visible_categories:
            filter_category = visible_categories[0]
    if filter_category and filter_category in device_hierarchy:
        category_types = device_hierarchy[filter_category]
        if filter_device_types:
             selected_category_types = [item for item in filter_device_types if item in category_types]
             if selected_category_types:
                 query = query.filter(Device.device_type.in_(selected_category_types))
             else:
                 query = query.filter(text('1=0'))
        else:
            query = query.filter(Device.device_type.in_(category_types))
    elif filter_device_types:
         query = query.filter(Device.device_type.in_(filter_device_types))
    
    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_name:
        query = query.filter(Device.name.ilike(f'%{filter_name}%'))
    if filter_q:
        like_q = f'%{filter_q}%'
        query = query.filter(or_(
            Device.device_code.ilike(like_q),
            Device.name.ilike(like_q),
            Device.manager.has(or_(
                User.full_name.ilike(like_q),
                User.username.ilike(like_q)
            ))
        ))
    # filter_device_type handled above
    if filter_status:
        query = query.filter_by(status=filter_status)
    else:
        query = query.filter(Device.status != CONVERTED_CONSUMABLE_STATUS)
    manager_filter_id = None
    if filter_manager_id:
        try:
            manager_filter_id = int(filter_manager_id)
        except ValueError:
            filter_manager_id = ''
            manager_filter_id = None
    if manager_filter_id is not None:
        query = query.filter(Device.manager_id == manager_filter_id)
    if filter_department:
        dept = Department.query.filter_by(name=filter_department).first()
        if dept:
            query = query.join(User, Device.manager_id == User.id).filter(User.department_id == dept.id)
    
    devices_pagination = query.order_by(Device.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng', 'Thanh lý', 'Test', 'Mượn', CONVERTED_CONSUMABLE_STATUS]
    users = _visible_users_query_for(user).all()
    if manager_filter_id is not None and all(u.id != manager_filter_id for u in users):
        extra_user = User.query.get(manager_filter_id)
        if extra_user:
            users.append(extra_user)
            users = sorted(users, key=lambda u: ((u.last_name_token or '') if hasattr(u, 'last_name_token') else '', u.full_name or u.username or ''))
    departments = [d.name for d in Department.query.order_by(Department.name).all()]
    primary_admin = User.query.filter_by(role='admin').order_by(User.id).first()

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
        filter_device_types=filter_device_types,
        filter_status=filter_status,
        filter_manager_id=filter_manager_id,
        filter_department=filter_department,
        filter_q=filter_q,
        filter_category=filter_category,
        device_hierarchy=device_hierarchy,
        primary_admin=primary_admin
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

@app.route('/devices/<int:device_id>/return', methods=['POST'])
def return_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    device = Device.query.get_or_404(device_id)
    current_permissions = _get_current_permissions()
    current_user = User.query.get(user_id)
    
    can_manage_devices = 'devices.edit' in current_permissions
    if not (device.manager_id == user_id or can_manage_devices):
        flash('Bạn không có quyền trả thiết bị này.', 'danger')
        return redirect(url_for('device_list'))
    
    return_option = request.form.get('return_option', 'manager')
    reason = (request.form.get('reason') or '').strip()
    if not reason:
        flash('Vui lòng nhập lý do hoàn trả.', 'danger')
        return redirect(url_for('device_list'))
    
    receiver_user = None
    if return_option == 'manager':
        manager_user = device.manager
        dept = manager_user.department_info if manager_user else None
        
        # Traverse up the hierarchy to find a department manager
        while dept:
            if dept.manager:
                receiver_user = dept.manager
                break
            dept = dept.parent
    elif return_option == 'admin':
        if current_user and current_user.role == 'admin':
            receiver_user = current_user
        else:
            receiver_user = User.query.filter_by(role='admin').order_by(User.id).first()
    else:
        flash('Lựa chọn người nhận không hợp lệ.', 'danger')
        return redirect(url_for('device_list'))
    
    if not receiver_user:
        flash('Không tìm thấy người nhận phù hợp cho yêu cầu trả thiết bị.', 'danger')
        return redirect(url_for('device_list'))
    
    try:
        handover = DeviceHandover(
            handover_date=datetime.now(VIETNAM_TZ).date(),
            device_id=device.id,
            device_condition=device.condition or 'Sử dụng bình thường',
            reason=reason,
            location='Kho thiết bị' if return_option == 'admin' else (receiver_user.department_info.name if receiver_user.department_info else 'Phòng ban'),
            notes=f'Trả thiết bị bởi {current_user.full_name or current_user.username}' if current_user else 'Trả thiết bị',
            giver_id=device.manager_id or user_id,
            receiver_id=receiver_user.id
        )
        db.session.add(handover)
        
        device.manager_id = receiver_user.id
        if return_option == 'admin':
            device.status = 'Sẵn sàng'
            device.assign_date = None
        else:
            device.status = 'Đã cấp phát'
            device.assign_date = datetime.now(VIETNAM_TZ).date()
        db.session.commit()
        flash('Đã tạo phiếu trả thiết bị và cập nhật người quản lý mới.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Không thể xử lý yêu cầu trả thiết bị: {str(e)}', 'danger')
    return redirect(url_for('device_list'))
    
@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        shared_purchase_date = request.form.get('purchase_date')
        if not shared_purchase_date:
            flash('Vui lòng nhập ngày mua.', 'danger')
            return redirect(url_for('add_device'))

        names = request.form.getlist('name[]')
        device_types = request.form.getlist('device_type[]')
        device_codes = request.form.getlist('device_code[]')
        serial_numbers = request.form.getlist('serial_number[]')
        configurations = request.form.getlist('configuration[]')
        quantities = request.form.getlist('quantity[]')
        conditions = request.form.getlist('condition[]')
        purchase_prices = request.form.getlist('purchase_price[]')
        cpus = request.form.getlist('cpu[]')
        mainboards = request.form.getlist('mainboard[]')
        ram_values = request.form.getlist('ram_gb[]')
        ssds = request.form.getlist('ssd[]')
        hdds = request.form.getlist('hdd[]')
        vgas = request.form.getlist('vga[]')
        brands = request.form.getlist('brand[]')
        warranties = request.form.getlist('warranty[]')
        notes_list = request.form.getlist('notes[]')

        if not names or not any(name.strip() for name in names):
            flash('Vui lòng nhập ít nhất một thiết bị.', 'danger')
            return redirect(url_for('add_device'))

        reserved_codes = set()
        created_devices = []
        saved_images = []
        try:
            purchase_date = datetime.strptime(shared_purchase_date, '%Y-%m-%d').date()
            assign_date = datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None
            manager_id = int(request.form.get('manager_id')) if request.form.get('manager_id') else None

            for index, raw_name in enumerate(names):
                name = (raw_name or '').strip()
                if not name:
                    continue
                device_type = (device_types[index] if index < len(device_types) else '').strip()
                if not device_type:
                    db.session.rollback()
                    flash('Vui lòng chọn loại thiết bị cho từng dòng.', 'danger')
                    return redirect(url_for('add_device'))

                quantity = 1
                if index < len(quantities) and quantities[index]:
                    try:
                        quantity = max(1, int(quantities[index]))
                    except ValueError:
                        quantity = 1

                base_device_code = (device_codes[index] if index < len(device_codes) else '').strip()
                custom_prefix = ''
                custom_sequence = None
                custom_width = 0
                if base_device_code:
                    match = re.search(r'^(.*?)(\d+)$', base_device_code)
                    if match:
                        custom_prefix = match.group(1)
                        custom_sequence = int(match.group(2))
                        custom_width = len(match.group(2))

                configuration = (configurations[index] if index < len(configurations) else '').strip()
                config_specs = _device_pc_specs_from_config_text(configuration)
                pc_specs = {
                    'cpu': (cpus[index] if index < len(cpus) else '').strip() or config_specs.get('cpu') or None,
                    'mainboard': (mainboards[index] if index < len(mainboards) else '').strip() or config_specs.get('mainboard') or None,
                    'ram_gb': _parse_ram_gb(ram_values[index] if index < len(ram_values) else None) or config_specs.get('ram_gb'),
                    'ssd': (ssds[index] if index < len(ssds) else '').strip() or config_specs.get('ssd') or None,
                    'hdd': (hdds[index] if index < len(hdds) else '').strip() or config_specs.get('hdd') or None,
                    'vga': (vgas[index] if index < len(vgas) else '').strip() or config_specs.get('vga') or None,
                    'wifi_card': None,
                    'network_card': None,
                }
                if not ('case' in device_type.lower() or 'laptop' in device_type.lower()):
                    pc_specs = {
                        'cpu': None,
                        'mainboard': None,
                        'ram_gb': None,
                        'ssd': None,
                        'hdd': None,
                        'vga': None,
                        'wifi_card': None,
                        'network_card': None,
                    }
                row_purchase_price = None
                if index < len(purchase_prices) and purchase_prices[index]:
                    try:
                        row_purchase_price = float(str(purchase_prices[index]).replace(',', '').strip())
                    except ValueError:
                        row_purchase_price = None
                row_condition = (conditions[index] if index < len(conditions) and conditions[index] else None) or 'Sử dụng bình thường'
                row_image_files = request.files.getlist(f'device_images_{index}[]')[:5]

                for item_offset in range(quantity):
                    if base_device_code:
                        if item_offset == 0:
                            device_code = base_device_code
                        elif custom_sequence is not None:
                            device_code = f'{custom_prefix}{custom_sequence + item_offset:0{custom_width}d}'
                        else:
                            device_code = f'{base_device_code}_{item_offset + 1}'
                    else:
                        device_code = generate_device_code_for_type(device_type, reserved_codes)
                        if not device_code:
                            db.session.rollback()
                            flash(f'Vui lòng cấu hình mã loại thiết bị cho "{device_type}" trước khi để trống mã thiết bị.', 'danger')
                            return redirect(url_for('add_device'))

                    if device_code in reserved_codes or Device.query.filter_by(device_code=device_code).first():
                        db.session.rollback()
                        flash(f'Mã thiết bị {device_code} đã tồn tại.', 'danger')
                        return redirect(url_for('add_device'))
                    reserved_codes.add(device_code)

                    new_device = Device(
                        device_code=device_code,
                        name=name,
                        device_type=device_type,
                        serial_number=(serial_numbers[index] if index < len(serial_numbers) else None) or None,
                        brand=(brands[index] if index < len(brands) else None) or None,
                        supplier=request.form.get('supplier') or None,
                        warranty=(warranties[index] if index < len(warranties) else None) or None,
                        configuration=configuration or None,
                        purchase_date=purchase_date,
                        import_date=purchase_date,
                        purchase_price=row_purchase_price,
                        buyer=request.form.get('buyer') or None,
                        condition=row_condition,
                        status=request.form.get('status') or 'Sẵn sàng',
                        manager_id=manager_id,
                        assign_date=assign_date,
                        notes=(notes_list[index] if index < len(notes_list) else None) or None,
                        **pc_specs
                    )
                    db.session.add(new_device)
                    db.session.flush()

                    image_filenames = _save_device_image_files(row_image_files, new_device.id, limit=5)
                    if image_filenames:
                        new_device.image_filename = image_filenames[0]
                        new_device.image_filenames = _device_image_storage_value(image_filenames)
                        saved_images.extend(image_filenames)
                    created_devices.append(new_device)

            if not created_devices:
                db.session.rollback()
                flash('Không có thiết bị hợp lệ để tạo.', 'danger')
                return redirect(url_for('add_device'))

            db.session.commit()
        except ValueError as e:
            db.session.rollback()
            for filename in saved_images:
                _delete_device_image_file(filename)
            flash(str(e), 'danger')
            return redirect(url_for('add_device'))
        except Exception as e:
            db.session.rollback()
            for filename in saved_images:
                _delete_device_image_file(filename)
            flash(f'Không thể thêm thiết bị: {str(e)}', 'danger')
            return redirect(url_for('add_device'))
        flash(f'Thêm thành công {len(created_devices)} thiết bị!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    
    # Fetch device types for dropdown
    types = DeviceType.query.order_by(DeviceType.category, DeviceType.name).all()
    grouped_device_types = {}
    for t in types:
        if t.category not in grouped_device_types:
            grouped_device_types[t.category] = []
        grouped_device_types[t.category].append(t)
        
    return render_template('add_device.html', managers=managers, grouped_device_types=grouped_device_types)
    
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
            'cpu': device.cpu,
            'mainboard': device.mainboard,
            'ram_gb': device.ram_gb,
            'ssd': device.ssd,
            'hdd': device.hdd,
            'vga': device.vga,
            'wifi_card': device.wifi_card,
            'network_card': device.network_card,
            'purchase_date': device.purchase_date,
            'purchase_price': device.purchase_price,
            'buyer': device.buyer,
            'condition': device.condition,
            'status': device.status,
            'manager_id': device.manager_id,
            'assign_date': device.assign_date,
            'notes': device.notes,
            'image_filename': device.image_filename,
            'image_filenames': device.image_filenames,
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
        for field, value in _device_pc_specs_from_form().items():
            setattr(device, field, value)
        device.purchase_date = datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date()
        device.purchase_price = request.form.get('purchase_price', type=float, default=None)
        device.buyer = request.form.get('buyer')
        device.condition = request.form['condition']
        device.status = request.form['status']
        manager_id_str = request.form.get('manager_id')
        device.manager_id = int(manager_id_str) if manager_id_str else None
        device.assign_date = datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None
        device.notes = request.form.get('notes')
        if request.form.get('remove_device_image') == '1':
            _delete_device_image_files(device.image_filenames or device.image_filename)
            device.image_filename = None
            device.image_filenames = None
        try:
            image_filenames = _save_device_image_files(request.files.getlist('device_image'), device.id, limit=5)
            if image_filenames:
                _delete_device_image_files(device.image_filenames or device.image_filename)
                device.image_filename = image_filenames[0]
                device.image_filenames = _device_image_storage_value(image_filenames)
        except ValueError as e:
            flash(str(e), 'danger')
            return redirect(url_for('edit_device', device_id=device_id))
        
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
            'cpu': device.cpu,
            'mainboard': device.mainboard,
            'ram_gb': device.ram_gb,
            'ssd': device.ssd,
            'hdd': device.hdd,
            'vga': device.vga,
            'wifi_card': device.wifi_card,
            'network_card': device.network_card,
            'purchase_date': device.purchase_date,
            'purchase_price': device.purchase_price,
            'buyer': device.buyer,
            'condition': device.condition,
            'status': device.status,
            'manager_id': device.manager_id,
            'assign_date': device.assign_date,
            'notes': device.notes,
            'image_filename': device.image_filename,
            'image_filenames': device.image_filenames,
        }
        _log_audit('device', device.id, old, new)
        flash('Cập nhật thông tin thiết bị thành công!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng']
    
    # Fetch device types for dropdown
    types = DeviceType.query.order_by(DeviceType.category, DeviceType.name).all()
    grouped_device_types = {}
    for t in types:
        if t.category not in grouped_device_types:
            grouped_device_types[t.category] = []
        grouped_device_types[t.category].append(t)
        
    return render_template('edit_device.html', device=device, managers=managers, statuses=statuses, grouped_device_types=grouped_device_types)

@app.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    if device.handovers:
        flash('Không thể xóa thiết bị đã có lịch sử bàn giao.', 'danger')
        return redirect(url_for('device_list'))
    # Removed InventoryReceiptItem deletion
    _delete_device_image_files(device.image_filenames or device.image_filename)
    db.session.delete(device)
    db.session.commit()
    flash('Xóa thiết bị thành công!', 'success')
    return redirect(url_for('device_list'))

@app.route('/devices/bulk_delete', methods=['POST'])
def bulk_delete_devices():
    if 'user_id' not in session: return redirect(url_for('login'))
    device_ids = request.form.getlist('device_ids')
    if not device_ids:
        flash('Vui lòng chọn ít nhất một thiết bị.', 'warning')
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

        # Removed InventoryReceiptItem deletion
        _delete_device_image_files(device.image_filenames or device.image_filename)

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
    handovers = DeviceHandover.query.filter_by(device_id=device.id).order_by(DeviceHandover.handover_date.desc()).all()
    current_permissions = _get_current_permissions()
    return render_template('device_detail.html', device=device, handovers=handovers, current_permissions=current_permissions)



@app.route('/add_devices_bulk', methods=['GET', 'POST'])
def add_devices_bulk():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    flash('Màn thêm nhiều thiết bị đã được gộp vào trang Thêm thiết bị.', 'info')
    return redirect(url_for('add_device'))

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
    
    query = DeviceHandover.query.outerjoin(Device)

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

    batch_key = func.coalesce(DeviceHandover.batch_id, cast(DeviceHandover.id, String))
    representative_ids = query.with_entities(func.min(DeviceHandover.id).label('id')).group_by(batch_key).subquery()
    handovers_pagination = DeviceHandover.query\
        .join(representative_ids, DeviceHandover.id == representative_ids.c.id)\
        .order_by(DeviceHandover.handover_date.desc(), DeviceHandover.id.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)

    visible_batch_ids = [handover.batch_id for handover in handovers_pagination.items if handover.batch_id]
    batch_items = {}
    if visible_batch_ids:
        rows = DeviceHandover.query\
            .filter(DeviceHandover.batch_id.in_(visible_batch_ids))\
            .order_by(DeviceHandover.batch_id, DeviceHandover.id)\
            .all()
        for row in rows:
            batch_items.setdefault(row.batch_id, []).append(row)
    for handover in handovers_pagination.items:
        handover.display_items = batch_items.get(handover.batch_id, [handover]) if handover.batch_id else [handover]
    consumable_batch_items = {}
    if visible_batch_ids:
        consumable_rows = ConsumableHandoverItem.query\
            .filter(ConsumableHandoverItem.batch_id.in_(visible_batch_ids))\
            .order_by(ConsumableHandoverItem.batch_id, ConsumableHandoverItem.id)\
            .all()
        for row in consumable_rows:
            consumable_batch_items.setdefault(row.batch_id, []).append(row)
    for handover in handovers_pagination.items:
        handover.consumable_items = consumable_batch_items.get(handover.batch_id, []) if handover.batch_id else []

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
    current_user = _get_current_user()
    current_permissions = _get_current_permissions()
    can_manage_all_devices = current_user and (current_user.role == 'admin' or 'devices.edit' in current_permissions or 'handovers.edit' in current_permissions)
    
    if request.method == 'POST':
        # Lấy danh sách ID thiết bị từ form và nhóm
        raw_device_ids = request.form.getlist('device_ids')
        device_ids = [d for d in raw_device_ids if d]
        device_conditions = request.form.getlist('device_conditions')
        consumable_ids = [value for value in request.form.getlist('consumable_ids') if value]
        consumable_quantities = request.form.getlist('consumable_quantities')
        consumable_locations = request.form.getlist('consumable_locations')
        receiver_id = request.form.get('receiver_id')
        handover_date_str = request.form.get('handover_date')
        
        # Validation cơ bản
        if (not device_ids and not consumable_ids) or not receiver_id or not handover_date_str:
            flash('Vui lòng chọn ít nhất một thiết bị hoặc vật tư và điền đầy đủ các trường bắt buộc.', 'danger')
            return redirect(url_for('add_handover'))
        if len(device_ids) != len(set(device_ids)):
            flash('Một thiết bị chỉ được chọn một lần trong cùng phiếu bàn giao.', 'danger')
            return redirect(url_for('add_handover'))
            
        handover_date = datetime.strptime(handover_date_str, '%Y-%m-%d').date()
        import uuid
        batch_id = uuid.uuid4().hex
        try:
            condition_images = _save_handover_condition_images(request.files.getlist('condition_images'), batch_id)
        except ValueError as e:
            flash(str(e), 'danger')
            return redirect(url_for('add_handover'))
        condition_images_json = json.dumps(condition_images, ensure_ascii=False) if condition_images else None
        
        handovers_created_count = 0
        consumables_created_count = 0
        for index, device_id in enumerate(device_ids):
            if not device_id: continue # Bỏ qua các giá trị rỗng

            device_to_update = Device.query.get(device_id)
            if not device_to_update:
                flash('Thiết bị không hợp lệ.', 'warning')
                continue
            if not can_manage_all_devices and device_to_update.manager_id != current_user.id:
                flash(f'Bạn chỉ có thể bàn giao thiết bị mình đang quản lý: {device_to_update.device_code}.', 'warning')
                continue
            if can_manage_all_devices and device_to_update.status == CONVERTED_CONSUMABLE_STATUS:
                flash(f'Thiết bị {device_to_update.device_code} không hợp lệ để bàn giao.', 'warning')
                continue

            new_handover = DeviceHandover(
                batch_id=batch_id,
                handover_date=handover_date, 
                device_id=device_id, 
                giver_id=request.form['giver_id'], 
                receiver_id=receiver_id, 
                device_condition=(device_conditions[index] if index < len(device_conditions) and device_conditions[index] else (device_to_update.condition or 'Sử dụng bình thường')),
                reason=request.form.get('reason', ''), 
                location=request.form.get('location', ''), 
                notes=request.form.get('notes', ''),
                condition_images=condition_images_json
            )
            db.session.add(new_handover)
            
            # Cập nhật trạng thái thiết bị
            device_to_update.manager_id = int(receiver_id)
            device_to_update.assign_date = new_handover.handover_date
            device_to_update.status = 'Đã cấp phát'
            
            handovers_created_count += 1

        try:
            for index, consumable_id in enumerate(consumable_ids):
                item = ConsumableItem.query.get(consumable_id)
                if not item or item.is_active is False:
                    raise ValueError('Vật tư không hợp lệ hoặc đã ngừng sử dụng.')
                quantity = _parse_positive_int(consumable_quantities[index] if index < len(consumable_quantities) else None)
                location = (consumable_locations[index] if index < len(consumable_locations) else '').strip() or item.location
                tx = _record_consumable_transaction(
                    item,
                    'Xuất',
                    quantity,
                    issued_to_id=int(receiver_id),
                    reason='Xuất theo phiếu bàn giao',
                    notes=request.form.get('notes', ''),
                    batch_id=batch_id,
                    location=location,
                )
                if item.track_after_handover:
                    db.session.add(ConsumableHandoverItem(
                        batch_id=batch_id,
                        consumable_id=item.id,
                        receiver_id=int(receiver_id),
                        giver_id=int(request.form['giver_id']) if request.form.get('giver_id') else None,
                        quantity=quantity,
                        location=location,
                        handover_date=handover_date,
                        notes=f'Tạo từ nhật ký xuất kho #{tx.id}' if tx.id else ''
                    ))
                consumables_created_count += 1
        except ValueError as e:
            db.session.rollback()
            _delete_handover_condition_images(condition_images)
            flash(str(e), 'danger')
            return redirect(url_for('add_handover'))

        if handovers_created_count == 0 and consumables_created_count > 0:
            db.session.add(DeviceHandover(
                batch_id=batch_id,
                handover_date=handover_date,
                device_id=None,
                giver_id=request.form['giver_id'],
                receiver_id=receiver_id,
                device_condition='Không áp dụng',
                reason=request.form.get('reason', ''),
                location=request.form.get('location', ''),
                notes=request.form.get('notes', ''),
                condition_images=condition_images_json
            ))

        if handovers_created_count > 0 or consumables_created_count > 0:
            db.session.commit()
            flash(f'Tạo thành công phiếu bàn giao gồm {handovers_created_count} thiết bị và {consumables_created_count} vật tư.', 'success')
            notify_user(int(receiver_id), f"Bạn vừa nhận bàn giao {handovers_created_count} thiết bị và {consumables_created_count} vật tư.", url_for('handover_list'))
            notify_group(f"Thực hiện bàn giao {handovers_created_count} thiết bị và {consumables_created_count} vật tư thành công.", url_for('handover_list'))
        else:
            db.session.rollback() # Hoàn tác nếu không có phiếu nào được tạo
            _delete_handover_condition_images(condition_images)
            flash('Không có phiếu bàn giao nào được tạo. Vui lòng kiểm tra lại thông tin thiết bị.', 'danger')

        return redirect(url_for('handover_list'))
    
    # Logic cho phương thức GET
    preselected_device_id = request.args.get('device_id', type=int)
    devices_query = Device.query.filter(Device.status != CONVERTED_CONSUMABLE_STATUS)
    if not can_manage_all_devices:
        devices_query = devices_query.filter(Device.manager_id == current_user.id)
    devices = devices_query.order_by(Device.device_code).all()
    users = User.query.filter(User.status.notin_(['Nghỉ việc', 'Nghỉ không lương']))\
        .order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    device_options = [{
        'id': device.id,
        'code': device.device_code,
        'name': device.name,
        'type': device.device_type,
        'serial_number': device.serial_number or '',
        'condition': device.condition or '',
        'configuration': device.configuration or '',
        'specs': {
            'cpu': device.cpu or '',
            'mainboard': device.mainboard or '',
            'ram_gb': device.ram_gb,
            'ssd': device.ssd or '',
            'hdd': device.hdd or '',
            'vga': device.vga or '',
            'wifi_card': device.wifi_card or '',
            'network_card': device.network_card or '',
        },
    } for device in devices]
    consumables = ConsumableItem.query.filter(ConsumableItem.is_active != False)\
        .order_by(func.lower(ConsumableItem.group_name), func.lower(ConsumableItem.category), func.lower(ConsumableItem.name)).all()
    consumable_options = [{
        'id': item.id,
        'code': item.code,
        'name': item.name,
        'group': item.group_name or '',
        'category': item.category or '',
        'unit': item.unit or '',
        'quantity': item.current_quantity or 0,
        'min_quantity': item.min_quantity or 0,
        'location': item.location or '',
        'track_after_handover': bool(item.track_after_handover),
    } for item in consumables]
    user_options = [{
        'id': user.id,
        'name': user.full_name or user.username,
        'username': user.username,
        'department': user.department_info.name if user.department_info else 'Chưa có phòng ban',
        'position': user.position or '',
    } for user in users]
    device_types = sorted({device.device_type for device in devices if device.device_type})
    
    return render_template('add_handover.html', 
                           devices=devices, 
                           users=users,
                           device_options=device_options,
                           user_options=user_options,
                           consumable_options=consumable_options,
                           device_types=device_types,
                           now=datetime.now(VIETNAM_TZ),
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
    images_to_delete = _handover_image_list(handover)
    batch_id = handover.batch_id
    should_restore_consumables = bool(batch_id) and DeviceHandover.query.filter_by(batch_id=batch_id).count() <= 1
    if should_restore_consumables:
        consumable_transactions = ConsumableTransaction.query.filter_by(batch_id=batch_id, transaction_type='Xuất').all()
        for tx in consumable_transactions:
            if tx.item:
                _record_consumable_transaction(
                    tx.item,
                    'Nhập',
                    tx.quantity,
                    issued_to_id=tx.issued_to_id,
                    reason='Hoàn kho do hủy phiếu bàn giao',
                    notes=f'Hoàn từ phiếu {batch_id}',
                    batch_id=batch_id,
                    location=tx.location,
                )
        ConsumableHandoverItem.query.filter_by(batch_id=batch_id).delete()
    db.session.delete(handover)
    db.session.commit()
    if batch_id:
        remaining = DeviceHandover.query.filter_by(batch_id=batch_id).count()
        if remaining == 0:
            _delete_handover_condition_images(images_to_delete)
    else:
        _delete_handover_condition_images(images_to_delete)
    flash('Xóa phiếu bàn giao thành công!', 'success')
    return redirect(url_for('handover_list'))

# Xem chi tiết một phiếu bàn giao
@app.route('/handover/<int:handover_id>')
def handover_detail(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover = DeviceHandover.query.get_or_404(handover_id)
    handover_items = [handover]
    if handover.batch_id:
        handover_items = DeviceHandover.query.filter_by(batch_id=handover.batch_id).order_by(DeviceHandover.id).all()
    consumable_items = []
    if handover.batch_id:
        consumable_items = ConsumableHandoverItem.query.filter_by(batch_id=handover.batch_id).order_by(ConsumableHandoverItem.id).all()
    device = handover.device
    giver = handover.giver
    receiver = handover.receiver
    return render_template(
        'handover_detail.html',
        handover=handover,
        handover_items=handover_items,
        consumable_items=consumable_items,
        condition_images=_handover_image_list(handover),
        device=device,
        giver=giver,
        receiver=receiver
    )

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
            df = _normalize_excel_columns(df, {
                'Mã thiết bị': ['Mã Thiết Bị'],
                'Tên đăng nhập người giao': ['Người Giao', 'Người giao'],
                'Tên đăng nhập người nhận': ['Người Nhận', 'Người nhận'],
                'Ngày bàn giao': ['Ngày Bàn Giao', 'Ngày Bàn giao'],
                'Tình trạng thiết bị': ['Tình Trạng Thiết Bị', 'Tình trạng'],
                'Lý do bàn giao': ['Lý Do', 'Lý do'],
                'Nơi đặt thiết bị': ['Nơi Đặt', 'Nơi đặt'],
                'Ghi chú': ['Ghi Chú']
            })
            required_columns = ['Mã thiết bị', 'Tên đăng nhập người giao', 'Tên đăng nhập người nhận', 'Ngày bàn giao', 'Tình trạng thiết bị']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_handovers'))

            errors = []
            handovers_to_add = []
            
            for index, row in df.iterrows():
                device_code = _cell_text(row['Mã thiết bị'])
                giver_username = _cell_text(row['Tên đăng nhập người giao'])
                receiver_username = _cell_text(row['Tên đăng nhập người nhận'])
                handover_date_str = _cell_text(row['Ngày bàn giao'])

                device = Device.query.filter_by(device_code=device_code).first()
                giver = User.query.filter_by(username=giver_username).first() or User.query.filter_by(full_name=giver_username).first()
                receiver = User.query.filter_by(username=receiver_username).first() or User.query.filter_by(full_name=receiver_username).first()

                # --- Validation ---
                current_row_errors = []
                if not device:
                    current_row_errors.append(f'Mã thiết bị "{device_code}" không tồn tại.')
                if not giver:
                    current_row_errors.append(f'Người giao "{giver_username}" không tồn tại.')
                if not receiver:
                    current_row_errors.append(f'Người nhận "{receiver_username}" không tồn tại.')
                
                if current_row_errors:
                    errors.append(f"Dòng {index + 2}: " + ", ".join(current_row_errors))
                    continue 

                try:
                    handover_date = pd.to_datetime(handover_date_str).date()
                except (ValueError, TypeError):
                    errors.append(f'Dòng {index + 2}: Định dạng ngày "{handover_date_str}" không hợp lệ.')
                    continue

                # Coerce possibly numeric-parsed text cells back to strings
                def _s(v):
                    if pd.isna(v):
                        return None
                    return str(v)
                new_handover = DeviceHandover(
                    device_id=device.id,
                    giver_id=giver.id,
                    receiver_id=receiver.id,
                    handover_date=handover_date,
                    device_condition=_s(row['Tình trạng thiết bị']),
                    reason=_s(row.get('Lý do bàn giao')),
                    location=_s(row.get('Nơi đặt thiết bị')),
                    notes=_s(row.get('Ghi chú'))
                )
                # Insert row-by-row to avoid PG executemany casts
                db.session.add(new_handover)
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
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    # Kiểm tra phân quyền: chỉ admin hoặc người có quyền users.view mới được truy cập
    if not (current_user and current_user.role == 'admin') and 'users.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_username = request.args.get('filter_username', '')
    filter_role = request.args.get('filter_role', '')
    filter_department = request.args.get('filter_department', '')
    filter_position = request.args.get('filter_position', '')
    filter_status = request.args.get('filter_status', session.get('default_user_status', 'Đang làm'))

    query = User.query
    if filter_username:
        query = query.filter(or_(
            User.username.ilike(f'%{filter_username}%'),
            User.full_name.ilike(f'%{filter_username}%')
        ))
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
    statuses = ['Đang làm', 'Thử việc', 'Nghỉ không lương', 'Nghỉ việc', 'Khác']
    current_permissions = _get_current_permissions()

    return render_template('users.html', 
                           users=users_pagination, 
                           filter_username=filter_username, 
                           filter_role=filter_role, 
                           filter_department=filter_department,
                           filter_position=filter_position,
                           filter_status=filter_status,
                           departments=departments,
                           positions=positions,
                           statuses=statuses,
                           current_permissions=current_permissions)

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
    if 'user_id' not in session: 
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        return redirect(url_for('login'))
        
    user = User.query.get_or_404(user_id)
    try:
        from security import generate_secure_password
        new_password = generate_secure_password()
        user.password = generate_password_hash(new_password)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'new_password': new_password})
            
        flash(f'Đã reset mật khẩu cho {user.full_name or user.username} về: {new_password}', 'success')
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': str(e)}), 500
        flash('Không thể reset mật khẩu do cơ sở dữ liệu chỉ đọc. Kiểm tra quyền ghi file DB.', 'danger')
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
        department_id_str = request.form.get('department_id')
        department_id = int(department_id_str) if department_id_str else None
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
        db.session.flush()  # Để lấy ID của user mới
        
        # Đảm bảo role chỉ là 'admin' hoặc 'user'
        role = request.form.get('role', 'user')
        if role not in ['admin', 'user']:
            role = 'user'
        new_user.role = role
        
        db.session.commit()
        
        if role == 'user':
            default_role = Role.query.filter_by(name='Người dùng').first()
            if default_role:
                db.session.add(UserRole(user_id=new_user.id, role_id=default_role.id))
                db.session.commit()
        flash('Thêm người dùng mới thành công!', 'success')
        return redirect(url_for('user_list'))
    departments = Department.query.all()
    # all_roles không cần thiết nữa cho giao diện mới
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
        import uuid
        batch_id = uuid.uuid4().hex
        
        # Tạo phiếu trả thiết bị cho từng thiết bị (vì mỗi handover chỉ handle 1 device)
        for device in devices:
            return_handover = DeviceHandover(
                batch_id=batch_id,
                handover_date=datetime.now(VIETNAM_TZ).date(),
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

@app.route('/users/<int:user_id>/quit', methods=['POST'])
def quit_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    user_executing = _get_current_user()
    
    if not (user_executing and user_executing.role == 'admin') and 'users.edit' not in current_permissions:
        flash('Bạn không có quyền thực hiện thao tác này.', 'danger')
        return redirect(url_for('user_list'))
        
    user = User.query.get_or_404(user_id)
    if user.status == 'Nghỉ việc':
        flash('Người dùng này đã nghỉ việc rồi.', 'info')
        return redirect(url_for('user_list'))
        
    success = create_return_handover_for_user(user_id, session.get('user_id'))
    user.status = 'Nghỉ việc'
    user.offboard_date = datetime.now(VIETNAM_TZ).date()
    db.session.commit()
    
    if success:
        flash(f'Đã xử lý nghỉ việc cho {user.username}. Đã tự động tạo phiếu thu hồi thiết bị.', 'success')
    else:
        flash(f'Đã chuyển trạng thái nghỉ việc cho {user.username} nhưng có lỗi khi tạo phiếu thu hồi.', 'warning')
        
    return redirect(request.referrer or url_for('user_list'))

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
        department_id_str = request.form.get('department_id')
        user.department_id = int(department_id_str) if department_id_str else None
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
        
        # Xử lý phân quyền mới: chỉ dựa vào cột role
        role = request.form.get('role')
        if role not in ['admin', 'user']:
             role = 'user'
        user.role = role
        
        # Xóa TẤT CẢ các quyền UserRole cũ để tránh xung đột quyền lẻ
        UserRole.query.filter_by(user_id=user_id).delete()
        
        # Xử lý nghỉ việc - tự động tạo phiếu trả thiết bị
        
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
        # Redirect back to previous page or provided 'next' param
        next_url = request.args.get('next') or request.form.get('next') or request.referrer
        try:
            if next_url:
                return redirect(next_url)
        except Exception:
            pass
        return redirect(url_for('user_list'))
    departments = Department.query.all()
    
    # Preserve next/back url
    next_url = request.referrer if request.referrer and ('/edit_user/' not in request.referrer) else url_for('user_list')
    return render_template('edit_user.html', user=user, departments=departments, next_url=next_url)

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
    
    page_given = request.args.get('page_given', 1, type=int)
    page_receiver = request.args.get('page_receiver', 1, type=int)

    given_pagination = DeviceHandover.query.filter_by(giver_id=user.id).order_by(DeviceHandover.handover_date.desc()).paginate(page=page_given, per_page=10, error_out=False)
    received_pagination = DeviceHandover.query.filter_by(receiver_id=user.id).order_by(DeviceHandover.handover_date.desc()).paginate(page=page_receiver, per_page=10, error_out=False)

    current_permissions = _get_current_permissions()
    return render_template(
        'user_detail.html',
        user=user,
        devices=devices,
        given=given_pagination.items,
        given_pagination=given_pagination,
        received=received_pagination.items,
        received_pagination=received_pagination,
        current_permissions=current_permissions
    )

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
            required_columns = ['Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Tình trạng', 'Trạng thái']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash(f'File Excel thiếu cột bắt buộc: {", ".join(missing_columns)}.', 'danger')
                return redirect(url_for('import_devices'))
            
            valid_conditions = ['Mới', 'Sử dụng bình thường', 'Cần bảo trì', 'Hỏng']
            valid_statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì']
            valid_device_types = {dt.name for dt in DeviceType.query.all()}
            
            devices_to_add = []
            errors = []

            for index, row in df.iterrows():
                manager_id = None
                device_code = _cell_text(row['Mã thiết bị'])
                device_name = _cell_text(row['Tên thiết bị'])
                device_type = _cell_text(row['Loại thiết bị'])
                condition = _cell_text(row['Tình trạng'])
                status = _cell_text(row['Trạng thái'])
                
                if not all([device_code, device_name, device_type, condition, status]):
                    errors.append(f'Dòng {index+2}: Thiếu thông tin ở các cột bắt buộc.')
                    continue
                if Device.query.filter_by(device_code=device_code).first():
                    errors.append(f'Dòng {index+2}: Mã thiết bị {device_code} đã tồn tại.')
                    continue
                if valid_device_types and device_type not in valid_device_types:
                    errors.append(f'Dòng {index+2}: Loại thiết bị "{device_type}" không hợp lệ.')
                    continue

                manager_name = row.get('Người quản lý')
                if pd.notna(manager_name) and manager_name:
                    manager = User.query.filter_by(full_name=manager_name).first() or User.query.filter_by(username=str(manager_name).strip()).first()
                    if not manager:
                        errors.append(f'Dòng {index+2}: Người quản lý {manager_name} không tồn tại.')
                        continue
                    manager_id = manager.id
                
                try:
                    purchase_date_val = row.get('Ngày mua')
                    assign_date_val = row.get('Ngày cấp phát')
                    purchase_date = pd.to_datetime(purchase_date_val).date() if pd.notna(purchase_date_val) else None
                    assign_date = pd.to_datetime(assign_date_val).date() if pd.notna(assign_date_val) else None
                except ValueError:
                    errors.append(f'Dòng {index+2}: Định dạng ngày không hợp lệ.'); continue
                
                # Robust numeric parsing for purchase_price
                raw_price = row.get('Giá mua')
                price = None
                try:
                    if pd.notna(raw_price):
                        if isinstance(raw_price, str):
                            # Remove thousand separators and non-numeric symbols
                            cleaned = raw_price.replace('.', '').replace(',', '').replace('₫', '').replace('đ', '').strip()
                            price = float(cleaned) if cleaned else None
                        else:
                            price = float(raw_price)
                except Exception:
                    price = None

                # Coerce text fields to str to avoid numeric miscasts in PG
                def _s(v):
                    if pd.isna(v):
                        return None
                    return str(v)

                device = Device(
                    device_code=device_code,
                    name=device_name,
                    device_type=device_type,
                    serial_number=_s(row.get('Số serial')),
                    purchase_date=purchase_date,
                    import_date=purchase_date,
                    condition=condition,
                    status=status,
                    manager_id=manager_id,
                    assign_date=assign_date,
                    configuration=_s(row.get('Cấu hình')),
                    notes=_s(row.get('Ghi chú')),
                    buyer=_s(row.get('Người mua')),
                    brand=_s(row.get('Thương hiệu')),
                    supplier=_s(row.get('Nhà cung cấp')),
                    warranty=_s(row.get('Bảo hành')),
                    purchase_price=price,
                    **_device_pc_specs_from_row(row)
                )
                # Insert row-by-row to avoid large executemany translation issues on PG
                db.session.add(device)
            
            if errors:
                for error in errors:
                    flash(error, 'danger')
                db.session.rollback()
            else:
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
            'CPU': device.cpu or '', 'Main': device.mainboard or '', 'RAM (GB)': device.ram_gb or '', 'SSD': device.ssd or '',
            'HDD': device.hdd or '', 'VGA': device.vga or '',
            'Card mạng': device.network_card or device.wifi_card or '',
            'Người nhập': device.importer or '', 'Thương hiệu': device.brand or '', 'Nhà cung cấp': device.supplier or '',
            'Bảo hành': device.warranty or ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Devices')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'devices_list_{datetime.now(VIETNAM_TZ).strftime("%Y%m%d")}.xlsx')

@app.route('/download/maintenance/<int:log_id>/<path:filename>')
def _get_current_permissions():
    """Utility: return a set of permission codes for the current user."""
    try:
        if 'user_id' not in session:
            return set()
        user = User.query.get(session['user_id'])
        # Admin always has full permissions
        if user and user.role == 'admin':
            return {p.code for p in Permission.query.all()}
        role_ids = [ur.role_id for ur in UserRole.query.filter_by(user_id=user.id).all()] if user else []
        perm_codes = set()
        if role_ids:
            for rp in RolePermission.query.filter(RolePermission.role_id.in_(role_ids)).all():
                perm = Permission.query.get(rp.permission_id)
                if perm:
                    perm_codes.add(perm.code)
        return perm_codes
    except Exception:
        return set()

def _get_current_user():
    """Return currently logged in user object (or None)."""
    try:
        if 'user_id' not in session:
            return None
        return User.query.get(session['user_id'])
    except Exception:
        return None

def _is_admin_user(user=None):
    """Return True for legacy admin users or users assigned to the Admin role."""
    try:
        if user is None:
            user = _get_current_user()
        if not user:
            return False
        if (user.role or '').lower() == 'admin':
            return True
        admin_role = Role.query.filter(func.lower(Role.name) == 'admin').first()
        if not admin_role:
            return False
        return UserRole.query.filter_by(user_id=user.id, role_id=admin_role.id).first() is not None
    except Exception:
        return False

def _managed_department_ids(user=None):
    """Departments directly managed by user, including nested child departments."""
    try:
        if user is None:
            user = _get_current_user()
        if not user:
            return []
        ids = []
        for dept in Department.query.filter_by(manager_id=user.id).all():
            ids.extend(get_subordinate_department_ids(dept.id))
        return sorted(set(ids))
    except Exception:
        return []

def _is_manager_user(user=None):
    return bool(_managed_department_ids(user))

def _visible_user_ids_for(user=None):
    """Admin: all users. Manager: users in managed departments. User: self only."""
    try:
        if user is None:
            user = _get_current_user()
        if not user:
            return []
        if _is_admin_user(user):
            return [row[0] for row in db.session.query(User.id).all()]
        dept_ids = _managed_department_ids(user)
        if dept_ids:
            ids = [row[0] for row in db.session.query(User.id).filter(User.department_id.in_(dept_ids)).all()]
            ids.append(user.id)
            return sorted(set(ids))
        return [user.id]
    except Exception:
        return [user.id] if user else []

def _visible_users_query_for(user=None):
    if user is None:
        user = _get_current_user()
    ids = _visible_user_ids_for(user)
    return User.query.filter(User.id.in_(ids)).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username))

def _visible_devices_query_for(user=None):
    if user is None:
        user = _get_current_user()
    if _is_admin_user(user):
        return Device.query
    return Device.query.filter(Device.manager_id.in_(_visible_user_ids_for(user)))

def _can_access_user(target_user_id, user=None):
    try:
        return int(target_user_id) in set(_visible_user_ids_for(user))
    except Exception:
        return False

def _managed_department_names(user=None):
    try:
        dept_ids = _managed_department_ids(user)
        if not dept_ids:
            return []
        return [row[0] for row in db.session.query(Department.name).filter(Department.id.in_(dept_ids)).all()]
    except Exception:
        return []

def _apply_config_proposal_scope(query, user=None):
    if user is None:
        user = _get_current_user()
    if _is_admin_user(user):
        return query
    visible_user_ids = _visible_user_ids_for(user)
    own_names = [user.full_name, user.username] if user else []
    own_names = [name for name in own_names if name]
    dept_names = _managed_department_names(user)
    conditions = [ConfigProposal.created_by.in_(visible_user_ids)]
    if own_names:
        conditions.append(ConfigProposal.proposer_name.in_(own_names))
    if dept_names:
        conditions.append(ConfigProposal.proposer_unit.in_(dept_names))
    return query.filter(or_(*conditions))

def _can_access_config_proposal(proposal, user=None):
    if user is None:
        user = _get_current_user()
    if not proposal or not user:
        return False
    if _is_admin_user(user):
        return True
    if proposal.created_by in _visible_user_ids_for(user):
        return True
    if proposal.proposer_name in [user.full_name, user.username]:
        return True
    return bool(proposal.proposer_unit and proposal.proposer_unit in _managed_department_names(user))

def _apply_bug_report_scope(query, user=None):
    if user is None:
        user = _get_current_user()
    if _is_admin_user(user):
        return query
    visible_user_ids = _visible_user_ids_for(user)
    return query.filter(or_(BugReport.created_by.in_(visible_user_ids), BugReport.assigned_to.in_(visible_user_ids)))

def _can_access_bug_report(report, user=None):
    if user is None:
        user = _get_current_user()
    if not report or not user:
        return False
    if _is_admin_user(user):
        return True
    visible_user_ids = set(_visible_user_ids_for(user))
    return report.created_by in visible_user_ids or report.assigned_to in visible_user_ids

def _has_dashboard_access(current_permissions=None, current_user=None):
    """Check if current user can access dashboard."""
    if current_user is None:
        current_user = _get_current_user()
    if current_permissions is None:
        current_permissions = _get_current_permissions()
    if current_user and current_user.role == 'admin':
        return True
    return 'dashboard.view' in (current_permissions or set())

def _bug_permission_flags(current_permissions=None, current_user=None):
    """Return tuple (can_manage_bug_reports, can_view_all_reports)."""
    if current_user is None:
        current_user = _get_current_user()
    if current_permissions is None:
        current_permissions = _get_current_permissions()
    can_manage = _is_admin_user(current_user) or ('bug_reports.manage_advanced' in current_permissions)
    can_view_all = can_manage
    return can_manage, can_view_all

def _to_vietnam_time(dt):
    """Convert naive UTC/aware datetime to Vietnam timezone."""
    if not dt:
        return None
    try:
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        else:
            dt = dt.astimezone(pytz.utc)
        return dt.astimezone(VIETNAM_TZ)
    except Exception:
        return dt

def _normalize_excel_columns(df, aliases):
    """Rename known Excel column aliases to canonical import names."""
    rename_map = {}
    for canonical, alternatives in aliases.items():
        if canonical in df.columns:
            continue
        for alternative in alternatives:
            if alternative in df.columns:
                rename_map[alternative] = canonical
                break
    return df.rename(columns=rename_map)

def _cell_text(value):
    if pd.isna(value):
        return ''
    return str(value).strip()

def download_maintenance_file(log_id, filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.download' not in _get_current_permissions():
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
            required_columns = ['Tên đăng nhập', 'Họ và tên', 'Email', 'Vai trò']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_users'))

            errors = []
            users_to_add = []
            
            for index, row in df.iterrows():
                username = _cell_text(row['Tên đăng nhập'])
                password = _cell_text(row.get('Mật khẩu'))
                email = _cell_text(row['Email'])

                if not username or not email:
                    errors.append(f'Dòng {index + 2}: Tên đăng nhập và Email không được để trống.')
                    continue
                if not password:
                    from security import generate_secure_password
                    password = generate_secure_password()
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
        created_at_local = _to_vietnam_time(user.created_at)
        last_login_local = _to_vietnam_time(user.last_login)
        data.append({
            'ID': user.id,
            'Tên đăng nhập': user.username,
            'Mật khẩu': '',
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
            'Ngày tạo': created_at_local.strftime('%d-%m-%Y %H:%M:%S') if created_at_local else '',
            'Đăng nhập lần cuối': last_login_local.strftime('%d-%m-%Y %H:%M:%S') if last_login_local else ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Users')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'users_list_{datetime.now(VIETNAM_TZ).strftime("%Y%m%d")}.xlsx')

@app.route('/maintenance_logs')
def maintenance_logs():
    if 'user_id' not in session: return redirect(url_for('login'))
    # permission check
    if 'maintenance.view' not in _get_current_permissions():
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    device_code = request.args.get('device_code', '').strip()
    device_name = request.args.get('device_name', '').strip()
    status = request.args.get('status', '').strip()
    device_type = request.args.get('device_type', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    manager_name = request.args.get('filter_manager_name', '').strip()

    query = DeviceMaintenanceLog.query.join(Device)
    if device_code:
        query = query.filter(Device.device_code.ilike(f"%{device_code}%"))
    if device_name:
        query = query.filter(Device.name.ilike(f"%{device_name}%"))
    if device_type:
        query = query.filter(Device.device_type.ilike(f"%{device_type}%"))
    if status:
        query = query.filter(DeviceMaintenanceLog.status.ilike(f"%{status}%"))
    if manager_name:
        query = query.join(User, Device.manager_id == User.id).filter(
            or_(User.full_name.ilike(f"%{manager_name}%"), User.username.ilike(f"%{manager_name}%"))
        )
    if start_date:
        try:
            sd = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(DeviceMaintenanceLog.log_date >= sd)
        except ValueError:
            pass
    if end_date:
        try:
            ed = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(DeviceMaintenanceLog.log_date <= ed)
        except ValueError:
            pass

    logs = query.order_by(DeviceMaintenanceLog.log_date.desc(), DeviceMaintenanceLog.id.desc()).paginate(page=page, per_page=per_page, error_out=False)

    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    return render_template(
        'maintenance_logs/list.html',
        logs=logs,
        device_code=device_code,
        device_name=device_name,
        status=status,
        device_type=device_type,
        start_date=start_date,
        end_date=end_date,
        device_types=device_types,
        filter_manager_name=manager_name
    )

@app.route('/maintenance_logs/add', methods=['GET', 'POST'])
def add_maintenance_log():
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.add' not in _get_current_permissions():
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
        reported_by = request.form.get('reported_by', type=int)

        try:
            log_date = datetime.strptime(log_date_str, '%Y-%m-%d').date() if log_date_str else date.today()
            new_log = DeviceMaintenanceLog(
                device_id=device_id,
                log_date=log_date,
                condition=condition,
                issue=issue,
                status=status,
                last_action=last_action,
                notes=notes,
                reported_by=reported_by
            )
            db.session.add(new_log)
            db.session.commit()
            flash('Đã thêm nhật ký bảo trì.', 'success')
            return redirect(url_for('maintenance_logs'))
        except Exception as e:
            db.session.rollback()
            flash('Có lỗi xảy ra khi thêm nhật ký.', 'danger')
    devices = Device.query.order_by(Device.device_code).all()
    users = User.query.filter(User.status.notin_(['Nghỉ không lương', 'Nghỉ việc'])).order_by(User.full_name).all()
    return render_template('maintenance_logs/add.html', devices=devices, users=users)

@app.route('/maintenance_logs/<int:log_id>')
def maintenance_log_detail(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.view' not in _get_current_permissions():
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    device = log.device
    all_logs = DeviceMaintenanceLog.query.filter_by(device_id=device.id).order_by(DeviceMaintenanceLog.log_date.asc(), DeviceMaintenanceLog.id.asc()).all()
    return render_template('maintenance_logs/detail.html', log=log, device=device, all_logs=all_logs)

@app.route('/maintenance_logs/<int:log_id>/edit', methods=['GET', 'POST'])
def edit_maintenance_log(log_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.edit' not in _get_current_permissions():
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
    if 'maintenance.delete' not in _get_current_permissions():
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
    if 'maintenance.upload' not in _get_current_permissions():
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

@app.route('/maintenance_logs/<int:log_id>/files/<filename>')
def download_maintenance_file(log_id, filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'maintenance.download' not in _get_current_permissions():
        flash('Bạn không có quyền tải tệp.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    log = DeviceMaintenanceLog.query.get_or_404(log_id)
    att = next((a for a in log.attachments if a.file_name == filename), None)
    if not att or not os.path.exists(att.file_path):
        flash('Tệp không tồn tại.', 'danger')
        return redirect(url_for('maintenance_log_detail', log_id=log_id))
    return send_file(att.file_path, as_attachment=True, download_name=filename)

# --- Bug Report Routes ---
@app.route('/bug_reports')
def bug_reports():
    """Danh sách báo lỗi - người dùng chỉ thấy báo lỗi của mình, admin thấy tất cả"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    current_user = User.query.get(user_id)
    can_manage_bug_reports, can_view_all_reports = _bug_permission_flags(current_permissions, current_user)
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # Load saved filters from session first
    saved_filters = session.get('bug_reports_filters', {}) or {}
    
    # Get filters from query params, fallback to saved filters
    status_filter = request.args.get('status', '').strip() or saved_filters.get('status', '')
    priority_filter = request.args.get('priority', '').strip() or saved_filters.get('priority', '')
    error_type_filter = request.args.get('error_type', '').strip() or saved_filters.get('error_type', '')
    date_filter = request.args.get('date_filter', '').strip() or saved_filters.get('date_filter', '')
    date_from = request.args.get('date_from', '').strip() or saved_filters.get('date_from', '')
    date_to = request.args.get('date_to', '').strip() or saved_filters.get('date_to', '')
    creator_filter = request.args.get('creator', '').strip() or saved_filters.get('creator', '')
    assignee_filter = request.args.get('assignee', '').strip() or saved_filters.get('assignee', '')
    device_code_filter = request.args.get('device_code', '').strip() or saved_filters.get('device_code', '')
    
    q = _apply_bug_report_scope(BugReport.query.filter(BugReport.merged_into.is_(None)), current_user)
    
    if status_filter:
        q = q.filter(BugReport.status == status_filter)
    if priority_filter:
        q = q.filter(BugReport.priority == priority_filter)
    if error_type_filter:
        q = q.filter(BugReport.error_type == error_type_filter)
    
    # Date filtering
    if date_filter:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        if date_filter == '1':
            # 1 ngày
            q = q.filter(BugReport.created_at >= now - timedelta(days=1))
        elif date_filter == '7':
            # 7 ngày
            q = q.filter(BugReport.created_at >= now - timedelta(days=7))
        elif date_filter == '30':
            # 30 ngày
            q = q.filter(BugReport.created_at >= now - timedelta(days=30))
        elif date_filter == '90':
            # 3 tháng
            q = q.filter(BugReport.created_at >= now - timedelta(days=90))
        elif date_filter == 'custom':
            # Khoảng thời gian
            if date_from and date_to:
                try:
                    date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                    if date_from_obj > date_to_obj:
                        flash('Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc!', 'danger')
                    else:
                        q = q.filter(BugReport.created_at >= date_from_obj)
                        q = q.filter(BugReport.created_at < date_to_obj + timedelta(days=1))
                except ValueError:
                    pass
            elif date_from:
                try:
                    date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                    q = q.filter(BugReport.created_at >= date_from_obj)
                except ValueError:
                    pass
            elif date_to:
                try:
                    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                    q = q.filter(BugReport.created_at < date_to_obj)
                except ValueError:
                    pass
    
    # Filter by creator
    if creator_filter:
        try:
            creator_id = int(creator_filter)
            q = q.filter(BugReport.created_by == creator_id)
        except ValueError:
            pass

    # Filter by assignee
    if assignee_filter:
        try:
            if assignee_filter == 'none':
                q = q.filter(BugReport.assigned_to == None)
            else:
                assignee_id = int(assignee_filter)
                q = q.filter(BugReport.assigned_to == assignee_id)
        except ValueError:
            pass
            
    # Filter by device_code
    if device_code_filter:
        q = q.filter(BugReport.device_code.ilike(f'%{device_code_filter}%'))
    
    reports = q.order_by(BugReport.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    visible_user_ids = _visible_user_ids_for(current_user)
    creator_ids = [
        row[0] for row in _apply_bug_report_scope(
            db.session.query(BugReport.created_by).filter(BugReport.created_by != None),
            current_user
        ).distinct().all()
    ]
    assignee_ids = [
        row[0] for row in _apply_bug_report_scope(
            db.session.query(BugReport.assigned_to).filter(BugReport.assigned_to != None),
            current_user
        ).distinct().all()
    ]
    creator_ids = [uid for uid in creator_ids if uid in visible_user_ids]
    assignee_ids = [uid for uid in assignee_ids if uid in visible_user_ids]
    creators = User.query.filter(User.id.in_(creator_ids)).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all() if creator_ids else []
    assignees = User.query.filter(User.id.in_(assignee_ids)).order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all() if assignee_ids else []

    # Get list of distinct device codes in reports (simple parsing or just rough list)
    # Since device_code is text and can be comma separated, getting distinct values is tricky. 
    # For simplicity, we fetch all non-empty device_code strings and split them python-side or just show distinct raw values.
    # A better approach given the comma separation: display distinct raw strings or improve this later.
    # Let's try to extract unique codes if possible, but for MVP standard distinct on the column is safest if single codes.
    # If they are comma separated "Code1, Code2", they will appear as such in the filter list.
    # Users can search via the filter text input if we change it to text later, but for now dropdown.
    # We will get all texts and split them in python to list unique codes.
    all_report_codes = _apply_bug_report_scope(db.session.query(BugReport.device_code).filter(BugReport.device_code != None, BugReport.device_code != ''), current_user).all()
    unique_device_codes = set()
    for r in all_report_codes:
        if r.device_code:
            for c in r.device_code.split(','):
                unique_device_codes.add(c.strip())
    sorted_device_codes = sorted(list(unique_device_codes))

    return render_template('bug_reports/list.html', 
                         reports=reports, 
                         status_filter=status_filter, 
                         priority_filter=priority_filter,
                         error_type_filter=error_type_filter,
                         date_filter=date_filter,
                         date_from=date_from,
                         date_to=date_to,
                         creator_filter=creator_filter,
                         assignee_filter=assignee_filter,
                         device_code_filter=device_code_filter,
                         creators=creators,
                         assignees=assignees,
                         device_codes=sorted_device_codes,
                         current_user_id=user_id, 
                         current_permissions=current_permissions,
                         can_manage_bug_reports=can_manage_bug_reports)

@app.route('/bug_reports/save_filters', methods=['POST'])
def save_bug_report_filters():
    """Lưu trạng thái lọc báo lỗi"""
    if 'user_id' not in session: return redirect(url_for('login'))
    filters = {
        'date_filter': request.form.get('date_filter', '').strip(),
        'date_from': request.form.get('date_from', '').strip(),
        'date_to': request.form.get('date_to', '').strip(),
        'creator': request.form.get('creator', '').strip(),
        'assignee': request.form.get('assignee', '').strip(),
        'device_code': request.form.get('device_code', '').strip(),
        'status': request.form.get('status', '').strip(),
        'priority': request.form.get('priority', '').strip(),
        'error_type': request.form.get('error_type', '').strip(),
    }
    session['bug_reports_filters'] = filters
    flash('Đã lưu bộ lọc báo lỗi.', 'success')
    return redirect(url_for('bug_reports'))

@app.route('/bug_reports/create', methods=['GET', 'POST'])
def create_bug_report():
    """Tạo báo lỗi - bất kỳ người dùng nào đã đăng nhập đều có thể tạo"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    current_user = User.query.get(user_id)
    can_manage_bug_reports, can_view_all_reports = _bug_permission_flags(current_permissions, current_user)
    
    devices = _visible_devices_query_for(current_user).order_by(Device.device_code).all()
    
    # Get list of users for "báo lỗi hộ" feature - Allow selecting any active user
    reportable_users = _visible_users_query_for(current_user).filter(~User.status.in_(['Nghỉ không lương', 'Nghỉ việc', 'Resigned', 'Retired'])).all()
 
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        priority = request.form.get('priority', 'Trung bình')
        error_type = request.form.get('error_type', 'Thiết bị')
        if error_type not in ['Thiết bị', 'Phần mềm', 'Văn phòng']:
            error_type = 'Thiết bị'
        visibility = (request.form.get('visibility') or 'private').strip().lower()
        if visibility not in ['private', 'public']:
            visibility = 'private'
        
        # Handle "báo lỗi hộ" - created_by can be different from current user
        created_by_id = user_id
        report_for_user = request.form.get('report_for_user', '').strip()
        if report_for_user:
            try:
                report_for_id = int(report_for_user)
                # Verify that user can report for this person
                if _can_access_user(report_for_id, current_user):
                    created_by_id = report_for_id
            except ValueError:
                pass

        # Hỗ trợ chọn nhiều mã thiết bị hoặc nhập thủ công
        device_codes = request.form.getlist('device_codes')
        if len(device_codes) == 1 and ',' in device_codes[0]:
            # Khi trình duyệt gửi dạng chuỗi duy nhất với dấu phẩy
            device_codes = [code.strip() for code in device_codes[0].split(',')]
        device_codes = [code.strip() for code in device_codes if code and code.strip()]
        # Loại bỏ trùng lặp nhưng giữ thứ tự
        seen = set()
        deduped_codes = []
        for code in device_codes:
            key = code.lower()
            if key not in seen:
                seen.add(key)
                deduped_codes.append(code)
        device_codes_str = ','.join(deduped_codes) if deduped_codes else None
        
        # If reporting for someone else, also show their devices
        if created_by_id != user_id:
            devices = Device.query.filter_by(manager_id=created_by_id).order_by(Device.device_code).all()
 
        if not title or not description:
            flash('Vui lòng nhập tiêu đề và mô tả.', 'danger')
            return render_template('bug_reports/create.html', devices=devices, selected_device_codes=deduped_codes, selected_visibility=visibility, selected_priority=priority, selected_error_type=error_type, draft_title=title, draft_description=description, reportable_users=reportable_users, selected_report_for=report_for_user)

        # Validate title length
        if len(title) > 100:
            flash('Tiêu đề không được vượt quá 100 ký tự.', 'danger')
            return render_template('bug_reports/create.html', devices=devices, selected_device_codes=deduped_codes, selected_visibility=visibility, selected_priority=priority, selected_error_type=error_type, draft_title=title, draft_description=description, reportable_users=reportable_users, selected_report_for=report_for_user)
 
        try:
            bug_report = BugReport(
                title=title,
                description=description,
                priority=priority,
                error_type=error_type,
                device_code=device_codes_str,
                visibility=visibility,
                created_by=created_by_id,
                status='Mới tạo'
            )
            db.session.add(bug_report)
            db.session.flush()
            
            # Xử lý file đính kèm nếu có
            files = request.files.getlist('attachments')
            if files and any(f.filename for f in files):
                upload_dir = os.path.join(instance_path, 'bug_report_attachments', str(bug_report.id))
                os.makedirs(upload_dir, exist_ok=True)
                for f in files:
                    if f and f.filename:
                        filename = f.filename.replace('..', '_').replace('/', '_').replace('\\', '_')
                        dest = os.path.join(upload_dir, filename)
                        f.save(dest)
                        db.session.add(BugReportAttachment(
                            bug_report_id=bug_report.id,
                            file_name=filename,
                            file_path=dest
                        ))
            
            db.session.commit()
            
            # Notifications
            notify_user(created_by_id, f"Báo lỗi '{title}' đã được tạo thành công.", url_for('bug_report_detail', report_id=bug_report.id, _external=True))
            notify_group(f"Báo lỗi mới: '{title}'", url_for('bug_report_detail', report_id=bug_report.id, _external=True))

            flash('Đã tạo báo lỗi thành công! Quản trị viên sẽ xem xét và xử lý.', 'success')
            return redirect(url_for('bug_report_detail', report_id=bug_report.id))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error creating bug report: {str(e)}', exc_info=True)
            flash(f'Lỗi khi tạo báo lỗi: {str(e)}', 'danger')
            return render_template('bug_reports/create.html', devices=devices, selected_device_codes=deduped_codes, selected_visibility=visibility, selected_priority=priority, selected_error_type=error_type, draft_title=title, draft_description=description, reportable_users=reportable_users, selected_report_for='')
    
    return render_template('bug_reports/create.html', devices=devices, selected_device_codes=[], selected_visibility='private', selected_priority='Trung bình', selected_error_type='Thiết bị', draft_title='', draft_description='', reportable_users=reportable_users, selected_report_for='')

@app.route('/bug_reports/<int:report_id>')
def bug_report_detail(report_id):
    """Chi tiết báo lỗi"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Đánh dấu đã đọc
    if 'read_reports' not in session:
        session['read_reports'] = []
    if report_id not in session['read_reports']:
        session['read_reports'].append(report_id)
        session.modified = True
    
    # Xác định vai trò người truy cập
    is_creator = bug_report.created_by == user_id
    is_assignee = bool(bug_report.assigned_to == user_id) if bug_report.assigned_to else False
    current_user = User.query.get(user_id)
    can_manage_bug_reports, can_view_all_reports = _bug_permission_flags(current_permissions, current_user)
    if not _can_access_bug_report(bug_report, current_user):
        flash('Bạn không có quyền xem báo lỗi này.', 'danger')
        return redirect(url_for('bug_reports'))
    # Lấy danh sách nhân viên để gán
    # Admin: tất cả nhân viên
    # Người khác: chỉ nhân viên trong phòng ban của mình và các phòng ban con
    user = current_user
    employees = []
    if can_view_all_reports or _is_manager_user(user):
        employees = _visible_users_query_for(user).all()
    
    is_closed = bug_report.status == 'Đã đóng'
    can_comment = (not is_closed) and _can_access_bug_report(bug_report, current_user)
    can_upload = (not is_closed) and (can_manage_bug_reports or is_creator or is_assignee)
    can_request_reopen = is_closed and is_creator and not bug_report.reopen_requested
    can_rate = is_closed and is_creator
    can_close = (not is_closed) and is_creator
    can_manage_related = can_manage_bug_reports or is_creator
    
    # Lấy danh sách báo lỗi liên quan
    related_reports = bug_report.related_reports.all() if bug_report.related_reports else []
    
    # Lấy danh sách báo lỗi có thể liên kết (không bao gồm chính nó và các báo lỗi đã được gộp)
    available_reports = []
    if can_manage_related:
        available_reports = _apply_bug_report_scope(BugReport.query.filter(
            BugReport.id != report_id,
            BugReport.merged_into.is_(None)
        ), current_user).order_by(BugReport.created_at.desc()).limit(100).all()

    return render_template(
        'bug_reports/detail.html',
        bug_report=bug_report,
        employees=employees,
        related_reports=related_reports,
        available_reports=available_reports,
        current_user_id=user_id,
        current_permissions=current_permissions,
        is_admin=can_manage_bug_reports,
        is_creator=is_creator,
        is_assignee=is_assignee,
        can_comment=can_comment,
        can_upload=can_upload,
        can_request_reopen=can_request_reopen,
        can_rate=can_rate,
        can_close=can_close,
        can_manage_related=can_manage_related
    )

@app.route('/bug_reports/<int:report_id>/edit', methods=['GET', 'POST'])
def edit_bug_report(report_id):
    """Sửa báo lỗi - cho phép sửa tiêu đề, mô tả, mã thiết bị"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Check permission: only creator can edit their own reports
    is_creator = bug_report.created_by == user_id
    
    if not is_creator:
        flash('Bạn không có quyền sửa báo lỗi này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        priority = request.form.get('priority', 'Trung bình')
        error_type = request.form.get('error_type', 'Thiết bị')
        if error_type not in ['Thiết bị', 'Phần mềm', 'Văn phòng']:
            error_type = 'Thiết bị'
        visibility = (request.form.get('visibility') or 'private').strip().lower()
        if visibility not in ['private', 'public']:
            visibility = 'private'
        
        # Handle device codes
        device_codes = request.form.getlist('device_codes')
        if len(device_codes) == 1 and ',' in device_codes[0]:
            device_codes = [code.strip() for code in device_codes[0].split(',')]
        device_codes = [code.strip() for code in device_codes if code and code.strip()]
        seen = set()
        deduped_codes = []
        for code in device_codes:
            key = code.lower()
            if key not in seen:
                seen.add(key)
                deduped_codes.append(code)
        device_codes_str = ','.join(deduped_codes) if deduped_codes else None
        
        if not title or not description:
            flash('Vui lòng nhập tiêu đề và mô tả.', 'danger')
            devices = _visible_devices_query_for(User.query.get(user_id)).order_by(Device.device_code).all()
            return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices, 
                                 selected_device_codes=deduped_codes, selected_visibility=visibility, 
                                 selected_priority=priority, draft_title=title, draft_description=description)
        
        if len(title) > 100:
            flash('Tiêu đề không được vượt quá 100 ký tự.', 'danger')
            devices = _visible_devices_query_for(User.query.get(user_id)).order_by(Device.device_code).all()
            return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices,
                                 selected_device_codes=deduped_codes, selected_visibility=visibility,
                                 selected_priority=priority, draft_title=title, draft_description=description)
        
        try:
            bug_report.title = title
            bug_report.description = description
            bug_report.priority = priority
            bug_report.error_type = error_type
            bug_report.visibility = visibility
            bug_report.device_code = device_codes_str
            bug_report.updated_at = datetime.utcnow()
            
            # Handle new attachments
            files = request.files.getlist('attachments')
            if files and any(f.filename for f in files):
                upload_dir = os.path.join(instance_path, 'bug_report_attachments', str(bug_report.id))
                os.makedirs(upload_dir, exist_ok=True)
                for f in files:
                    if f and f.filename:
                        filename = f.filename.replace('..', '_').replace('/', '_').replace('\\', '_')
                        dest = os.path.join(upload_dir, filename)
                        f.save(dest)
                        db.session.add(BugReportAttachment(
                            bug_report_id=bug_report.id,
                            file_name=filename,
                            file_path=dest
                        ))
            
            db.session.commit()
            flash('Đã cập nhật báo lỗi thành công.', 'success')
            return redirect(url_for('bug_report_detail', report_id=report_id))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error editing bug report: {str(e)}', exc_info=True)
            flash(f'Lỗi khi cập nhật báo lỗi: {str(e)}', 'danger')
            devices = _visible_devices_query_for(User.query.get(user_id)).order_by(Device.device_code).all()
            return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices,
                                 selected_device_codes=deduped_codes, selected_visibility=visibility,
                                 selected_priority=priority, draft_title=title, draft_description=description)
    
    # GET request - show edit form
    devices = _visible_devices_query_for(User.query.get(user_id)).order_by(Device.device_code).all()
    selected_codes = bug_report.device_code_list
    return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices,
                         selected_device_codes=selected_codes, selected_visibility=bug_report.visibility,
                         selected_priority=bug_report.priority, selected_error_type=bug_report.error_type or 'Thiết bị',
                         draft_title=bug_report.title, draft_description=bug_report.description)

@app.route('/bug_reports/<int:report_id>/update', methods=['POST'])
def update_bug_report(report_id):
    """Cập nhật trạng thái báo lỗi - chỉ admin"""
    if 'user_id' not in session: return redirect(url_for('login'))
    current_user = User.query.get(session.get('user_id'))
    current_permissions = _get_current_permissions()
    can_manage_bug_reports, _ = _bug_permission_flags(current_permissions, current_user)
    
    # Only admin or users with advanced perm can update bug reports
    if not can_manage_bug_reports:
        flash('Bạn không có quyền cập nhật báo lỗi. Chức năng này chỉ dành cho quản trị viên.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    try:
        status = request.form.get('status')
        priority = request.form.get('priority')
        error_type = request.form.get('error_type')
        assigned_to = request.form.get('assigned_to')
        resolution = request.form.get('resolution', '').strip()
        visibility = (request.form.get('visibility') or bug_report.visibility or 'private').strip().lower()
 
        if status:
            bug_report.status = status
            if status in ['Đã xử lý', 'Đã đóng']:
                bug_report.resolved_at = datetime.utcnow()
            elif status == 'Mới tạo':
                bug_report.resolved_at = None
 
            if status == 'Đã đóng':
                bug_report.reopen_requested = False
                if not bug_report.rating:
                    bug_report.rating = 5
            else:
                bug_report.reopen_requested = False
 
        if priority:
            bug_report.priority = priority
        
        if error_type and error_type in ['Thiết bị', 'Phần mềm', 'Văn phòng']:
            bug_report.error_type = error_type
 
        if assigned_to:
            try:
                bug_report.assigned_to = int(assigned_to) if assigned_to else None
            except ValueError:
                pass
 
        if resolution:
            bug_report.resolution = resolution
 
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        if status in ['Đã xử lý', 'Đã đóng']:
            notify_user(bug_report.created_by, f"Báo lỗi '{bug_report.title}' của bạn đã chuyển sang trạng thái: {status}", url_for('bug_report_detail', report_id=bug_report.id, _external=True))
            notify_group(f"Báo lỗi '{bug_report.title}' đã chuyển sang trạng thái: {status}.", url_for('bug_report_detail', report_id=bug_report.id, _external=True))
        flash('Đã cập nhật báo lỗi thành công.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi cập nhật: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/comments/<int:comment_id>/edit', methods=['POST'])
def edit_bug_report_comment(comment_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    comment = BugReportComment.query.get_or_404(comment_id)
    if comment.created_by != session.get('user_id') and session.get('role') != 'admin':
        flash('Bạn không có quyền sửa bình luận này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=comment.bug_report_id))
    
    new_text = request.form.get('comment')
    if new_text and new_text.strip():
        comment.comment = new_text.strip()
        from datetime import datetime
        comment.edited_at = datetime.utcnow()
        db.session.commit()
        flash('Đã sửa bình luận.', 'success')
    return redirect(url_for('bug_report_detail', report_id=comment.bug_report_id))

@app.route('/bug_reports/comments/<int:comment_id>/delete', methods=['POST'])
def delete_bug_report_comment(comment_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    comment = BugReportComment.query.get_or_404(comment_id)
    if comment.created_by != session.get('user_id') and session.get('role') != 'admin':
        flash('Bạn không có quyền xóa bình luận này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=comment.bug_report_id))
    
    b_id = comment.bug_report_id
    db.session.delete(comment)
    db.session.commit()
    flash('Đã xóa bình luận.', 'success')
    return redirect(url_for('bug_report_detail', report_id=b_id))

@app.route('/bug_reports/<int:report_id>/comment', methods=['POST'])
def add_bug_report_comment(report_id):
    """Thêm comment vào báo lỗi"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Kiểm tra quyền: người tạo hoặc admin
    current_permissions = _get_current_permissions()
    is_creator = bug_report.created_by == user_id
    is_assignee = bug_report.assigned_to == user_id if bug_report.assigned_to else False
    current_user = User.query.get(user_id)

    if bug_report.status == 'Đã đóng':
        flash('Vấn đề đã đóng. Vui lòng gửi yêu cầu mở lại để tiếp tục trao đổi.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    if not _can_access_bug_report(bug_report, current_user):
        flash('Bạn không có quyền bình luận báo lỗi này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    comment_text = request.form.get('comment', '').strip()
    if not comment_text:
        flash('Vui lòng nhập nội dung bình luận.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    try:
        comment = BugReportComment(
            bug_report_id=report_id,
            comment=comment_text,
            created_by=user_id
        )
        db.session.add(comment)
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Đã thêm bình luận.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi thêm bình luận: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/attachments', methods=['POST'])
def upload_bug_report_attachment(report_id):
    """Tải file đính kèm"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Kiểm tra quyền: người tạo hoặc admin
    is_creator = bug_report.created_by == user_id
    is_assignee = bug_report.assigned_to == user_id if bug_report.assigned_to else False
    current_user = User.query.get(user_id)
    can_manage_bug_reports, _ = _bug_permission_flags(_get_current_permissions(), current_user)

    if bug_report.status == 'Đã đóng':
        flash('Vấn đề đã đóng. Không thể tải thêm tệp đính kèm.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    if not _can_access_bug_report(bug_report, current_user):
        flash('Bạn không có quyền tải file cho báo lỗi này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    files = request.files.getlist('files')
    saved = 0
    upload_dir = os.path.join(instance_path, 'bug_report_attachments', str(report_id))
    os.makedirs(upload_dir, exist_ok=True)
    
    try:
        for f in files:
            if not f or not f.filename:
                continue
            filename = f.filename.replace('..', '_').replace('/', '_').replace('\\', '_')
            dest = os.path.join(upload_dir, filename)
            f.save(dest)
            db.session.add(BugReportAttachment(
                bug_report_id=report_id,
                file_name=filename,
                file_path=dest
            ))
            saved += 1
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        if saved:
            flash(f'Đã tải lên {saved} tệp.', 'success')
        else:
            flash('Không có tệp nào được tải lên.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi tải tệp: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/files/<filename>')
def download_bug_report_file(report_id, filename):
    """Tải file đính kèm"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Kiểm tra quyền: người tạo hoặc admin
    if not _can_access_bug_report(bug_report, User.query.get(user_id)):
        flash('Bạn không có quyền tải file.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    att = next((a for a in bug_report.attachments if a.file_name == filename), None)
    if not att or not os.path.exists(att.file_path):
        flash('Tệp không tồn tại.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    return send_file(att.file_path, as_attachment=True, download_name=filename)

@app.route('/bug_reports/<int:report_id>/delete', methods=['POST'])
def delete_bug_report(report_id):
    """Xóa báo lỗi - chỉ người tạo"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    bug_report = BugReport.query.get_or_404(report_id)
    
    # Only creator can delete their own reports
    if bug_report.created_by != user_id:
        flash('Bạn không có quyền xóa báo lỗi này. Chỉ người tạo mới có quyền xóa.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    try:
        # Xóa file đính kèm
        for att in list(bug_report.attachments):
            try:
                if att.file_path and os.path.exists(att.file_path):
                    os.remove(att.file_path)
            except Exception:
                pass
        
        # Xóa thư mục đính kèm nếu rỗng
        upload_dir = os.path.join(instance_path, 'bug_report_attachments', str(report_id))
        try:
            if os.path.exists(upload_dir) and not os.listdir(upload_dir):
                os.rmdir(upload_dir)
        except Exception:
            pass
        
        db.session.delete(bug_report)
        db.session.commit()
        flash('Đã xóa báo lỗi.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi xóa: {str(e)}', 'danger')
    
    return redirect(url_for('bug_reports'))

@app.route('/export_handovers_excel')
def export_handovers_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    handovers = DeviceHandover.query.order_by(DeviceHandover.handover_date.desc()).all()
    data = []
    for handover in handovers:
        data.append({
            'Mã thiết bị': handover.device.device_code if handover.device else '',
            'Tên đăng nhập người giao': handover.giver.username if handover.giver else '',
            'Tên đăng nhập người nhận': handover.receiver.username if handover.receiver else '',
            'Ngày bàn giao': handover.handover_date.strftime('%d-%m-%Y') if handover.handover_date else '',
            'Tình trạng thiết bị': handover.device_condition,
            'Lý do bàn giao': handover.reason,
            'Nơi đặt thiết bị': handover.location,
            'Ghi chú': handover.notes,
            'Tên thiết bị': handover.device.name if handover.device else '',
            'Loại thiết bị': handover.device.device_type if handover.device else '',
            'Người giao': handover.giver.full_name if handover.giver else '',
            'Người nhận': handover.receiver.full_name if handover.receiver else '',
            'Phòng ban người nhận': (handover.receiver.department_info.name if handover.receiver and handover.receiver.department_info else '')
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Handovers')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'handover_history_{datetime.now(VIETNAM_TZ).strftime("%Y%m%d")}.xlsx')

# --- Configuration Proposal Routes ---
@app.route('/config_proposals')
def config_proposals():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    current_user = _get_current_user()
    q = _apply_config_proposal_scope(ConfigProposal.query, current_user)
    filter_name = request.args.get('name', '').strip()
    filter_unit = request.args.get('unit', '').strip()
    filter_proposer = request.args.get('proposer', '').strip()
    filter_status = request.args.get('status', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    
    if filter_name:
        q = q.filter(ConfigProposal.name.ilike(f"%{filter_name}%"))
    if filter_unit:
        q = q.filter(ConfigProposal.proposer_unit == filter_unit)
    if filter_proposer:
        q = q.filter(ConfigProposal.proposer_name == filter_proposer)
    if filter_status:
        q = q.filter(ConfigProposal.status == filter_status)
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

    # Fetch distinct values for dropdowns
    scoped_for_filters = _apply_config_proposal_scope(ConfigProposal.query, current_user).subquery()
    proposers = [r[0] for r in db.session.query(scoped_for_filters.c.proposer_name).distinct().filter(scoped_for_filters.c.proposer_name != None).order_by(scoped_for_filters.c.proposer_name).all()]
    units = [r[0] for r in db.session.query(scoped_for_filters.c.proposer_unit).distinct().filter(scoped_for_filters.c.proposer_unit != None).order_by(scoped_for_filters.c.proposer_unit).all()]
    statuses = [r[0] for r in db.session.query(scoped_for_filters.c.status).distinct().filter(scoped_for_filters.c.status != None).order_by(scoped_for_filters.c.status).all()]

    return render_template('config_proposals.html', 
                           proposals=proposals_pagination, 
                           filter_name=filter_name, 
                           filter_unit=filter_unit,
                           filter_proposer=filter_proposer, filter_status=filter_status,
                           start_date=start_date, end_date=end_date,
                           units=units, proposers=proposers, statuses=statuses,
                           current_permissions=_get_current_permissions())


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
            # purchase_status removed
            notes = request.form.get('notes')
            supplier_info_hdr = request.form.get('supplier_info')
            general_requirements = request.form.get('general_requirements', '').strip()

            if not name or not proposal_date_str or not general_requirements:
                flash('Vui lòng nhập Tên đề xuất, Ngày đề xuất và Nhu cầu sử dụng.', 'danger')
                return redirect(url_for('add_config_proposal'))

            current_user = User.query.get(session['user_id'])
            selected_proposer = None
            try:
                selected_proposer = _visible_users_query_for(current_user).filter(User.id == int(proposer_name)).first()
            except Exception:
                selected_proposer = _visible_users_query_for(current_user).filter(
                    or_(User.full_name == proposer_name, User.username == proposer_name)
                ).first()
            if selected_proposer:
                proposer_name = selected_proposer.full_name or selected_proposer.username
                proposer_unit = selected_proposer.department_info.name if selected_proposer.department_info else ''
            else:
                proposer_name = current_user.full_name or current_user.username
                proposer_unit = current_user.department_info.name if current_user.department_info else ''

            proposal_date = datetime.strptime(proposal_date_str, '%Y-%m-%d').date()

            proposal = ConfigProposal(
                name=name,
                proposal_date=proposal_date,
                proposer_name=proposer_name,
                proposer_unit=proposer_unit,
                scope=scope,
                priority=request.form.get('priority') or 'Trung bình',
                vat_percent=vat_percent,
                currency=currency,
                # status=status argument removed to favor default 'new' below or use explicit 'new'
                # purchase_status removed
                notes=notes,
                # supplier_info removed
                quantity=request.form.get('quantity', type=int) or 1,
                created_by=session['user_id'],
                status='new',
                current_stage_deadline=datetime.utcnow() + timedelta(days=1), # SLA for Team Lead
                general_requirements=general_requirements,
                required_date=datetime.strptime(request.form.get('required_date'), '%Y-%m-%d').date() if request.form.get('required_date') else None
            )
            db.session.add(proposal)
            db.session.flush()

            subtotal = 0.0
            rows = int(request.form.get('rows_count', 8))
            for i in range(rows):
                prefix = f'rows[{i}]'
                option_name = request.form.get(f'{prefix}[option_name]')
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
                    option_name=option_name,
                    product_name=product_name,
                    product_link=product_link,
                    product_code=product_code,
                    warranty=warranty,
                    quantity=max(0, quantity),
                    unit_price=max(0.0, unit_price),
                    line_total=line_total
                ))

            proposal.subtotal = subtotal
            # Calculate total based on quantity
            grand_subtotal = subtotal * proposal.quantity
            proposal.vat_amount = round(grand_subtotal * (vat_percent / 100.0), 2)
            proposal.total_amount = round(grand_subtotal + proposal.vat_amount, 2)
            db.session.commit()
            
            # Notifications
            notify_user(session['user_id'], f"Đề xuất thiết bị '{name}' đã được tạo.", url_for('config_proposal_detail', proposal_id=proposal.id, _external=True))
            notify_group(f"Đề xuất thiết bị mới: '{name}'", url_for('config_proposal_detail', proposal_id=proposal.id, _external=True))
            
            flash('Tạo đề xuất thiết bị thành công.', 'success')
            return redirect(url_for('config_proposals'))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi tạo đề xuất: {str(e)}', 'danger')
            return redirect(url_for('add_config_proposal'))
    # GET
    default_date = datetime.utcnow().strftime('%Y-%m-%d')
    current_user = User.query.get(session['user_id'])
    # Fetch users for Proposer selection
    # If Admin, show ALL users. Else, show only Department users.
    dept_users = _visible_users_query_for(current_user).all()
        
    return render_template('add_config_proposal.html', default_date=default_date, users=dept_users, current_user=current_user)


@app.route('/config_proposals/attachments/<int:attachment_id>')
def download_proposal_attachment(attachment_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    attachment = ConfigProposalAttachment.query.get_or_404(attachment_id)
    return send_from_directory(
        os.path.join(instance_path, 'proposal_attachments'),
        attachment.file_path,
        as_attachment=True,
        download_name=attachment.file_name
    )

@app.route('/config_proposals/<int:proposal_id>/action', methods=['POST'])

def proposal_action(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    current_user = User.query.get(session['user_id'])
    if not _can_access_config_proposal(p, current_user):
        flash('Bạn không có quyền truy cập đề xuất này.', 'danger')
        return redirect(url_for('config_proposals'))
    permissions = _get_current_permissions()
    

    action = request.form.get('action')
    note = request.form.get('note')

    # helper for processing file attachments
    def handle_attachments(step_name):
        import uuid
        files = request.files.getlist('attachments')
        if not files:
            return
        
        for file in files:
            if file and file.filename:
                unique_filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                save_path = os.path.join(instance_path, 'proposal_attachments', unique_filename)
                file.save(save_path)
                attachment = ConfigProposalAttachment(
                    proposal_id=p.id,
                    step=step_name,
                    file_name=file.filename,
                    file_path=unique_filename,
                    uploaded_by=current_user.id
                )
                db.session.add(attachment)

    
    # helper for SLA calculation
    def get_deadline(days):
         # simple skip weekends logic could be added here, currently just calendar days
         return datetime.utcnow() + timedelta(days=days)

    try:
        if action == 'approve_team':
            # Check permission: User is manager of proposer's department OR Admin
            is_manager = False
            if p.creator and p.creator.department_info and p.creator.department_info.manager_id == current_user.id:
                is_manager = True
            
            if not (is_manager or 'config_proposals.approve_team' in permissions or current_user.role == 'admin'):
                flash('Bạn không có quyền duyệt cấp bộ phận.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))

            p.status = 'team_approved'
            p.team_lead_approver_id = current_user.id
            p.team_lead_approved_at = datetime.utcnow()
            p.current_stage_deadline = get_deadline(2) # SLA for IT: 48h
            flash('Đã duyệt đề xuất (Cấp bộ phận). Chuyển sang IT tư vấn.', 'success')

        elif action == 'consult_it':
            if 'config_proposals.consult_it' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền lập phương án thiết bị.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            supplier_info = request.form.get('supplier_info')
            if supplier_info:
                p.supplier_info = supplier_info
                
            if not ConfigProposalItem.query.filter_by(proposal_id=p.id).first():
                flash('IT cần cập nhật ít nhất một phương án thiết bị trước khi chuyển bước.', 'danger')
                return redirect(url_for('edit_config_proposal', proposal_id=p.id))

            is_from_stock = request.form.get('is_from_stock') == 'on'
            p.is_from_stock = is_from_stock
            
            p.status = 'it_consulted'
            p.it_consultant_id = current_user.id
            p.it_consulted_at = datetime.utcnow()
            p.it_consultation_note = note
            p.current_stage_deadline = get_deadline(2) # SLA for Director: 48h
            flash('Đã hoàn thành phương án thiết bị. Chuyển sang Giám đốc phê duyệt.', 'success')



        elif action == 'approve_director':
            if 'config_proposals.approve_director' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền phê duyệt.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            p.status = 'approved'
            p.director_approver_id = current_user.id
            p.director_approved_at = datetime.utcnow()
            p.director_approval_note = note
            
            if p.is_from_stock:
                p.status = 'completed'
                p.current_stage_deadline = None
                flash('Đã phê duyệt đề xuất. Thiết bị được cấp từ kho và quy trình đã hoàn tất.', 'success')
            else:
                p.status = 'approved'
                p.current_stage_deadline = get_deadline(14) # ~2 weeks for full purchasing process
                flash('Đã phê duyệt đề xuất. Các bộ phận liên quan vui lòng thực hiện checklist mua sắm.', 'success')

        # --- Post-Approval Checklist Actions ---
        # Any of these can happen if status is 'approved'.
        
        elif action == 'start_purchasing':
             if 'config_proposals.execute_purchase' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền thực hiện mua sắm.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
             
             handle_attachments('purchasing')
             p.cat_purchaser_id = current_user.id
             p.purchasing_at = datetime.utcnow()
             
             if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                 p.status = 'completed'
                 flash('Đã xác nhận đang mua sắm. Quy trình hoàn tất!', 'success')
             else:
                 flash('Đã xác nhận đang mua sắm.', 'success')
        
        elif action == 'confirm_payment':
             if 'config_proposals.execute_accounting' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền xác nhận thanh toán.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
             
             handle_attachments('payment')
             p.accountant_payer_id = current_user.id
             p.payment_at = datetime.utcnow()
             
             if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                 p.status = 'completed'
                 flash('Đã xác nhận thanh toán. Quy trình hoàn tất!', 'success')
             else:
                 flash('Đã xác nhận thanh toán.', 'success')

        elif action == 'confirm_goods_received':
             if 'config_proposals.confirm_delivery' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền xác nhận nhận hàng.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
             
             handle_attachments('receiving')
             p.tech_receiver_id = current_user.id
             p.goods_received_at = datetime.utcnow()
             
             if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                 p.status = 'completed'
                 flash('Đã xác nhận nhận hàng @ IT. Quy trình hoàn tất!', 'success')
             else:
                 flash('Đã xác nhận nhận hàng @ IT.', 'success')

        elif action == 'confirm_handover':
             if 'config_proposals.confirm_delivery' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền xác nhận bàn giao.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
             
             handle_attachments('handover')
             p.handover_to_user_at = datetime.utcnow()
             
             if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                 p.status = 'completed'
                 flash('Đã xác nhận bàn giao User. Quy trình hoàn tất!', 'success')
             else:
                flash('Đã xác nhận bàn giao User.', 'success')

        elif action == 'confirm_invoice':
            if 'config_proposals.execute_accounting' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền xác nhận hóa đơn.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            # Check if handover is done and invoice not yet received
            if p.handover_to_user_at and not p.invoice_received_at:
                handle_attachments('invoice')
                p.accountant_invoice_id = current_user.id
                p.invoice_received_at = datetime.utcnow()
                # Assuming _log_audit is defined elsewhere for logging changes
                # _log_audit('config_proposal', p.id, {'invoice_received_at': None}, {'invoice_received_at': str(p.invoice_received_at)})
                db.session.commit() # Commit here to ensure invoice_received_at is saved before checking for completion
                flash('Đã xác nhận nhận hóa đơn.', 'success')
                
                # Auto-complete when all checklist items are done
                # Note: The original code checked p.purchasing_at and p.payment_at.
                # The new instruction uses p.payment_requested_at. I'll use p.payment_at for consistency with existing code.
                if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                    old_status = p.status
                    p.status = 'completed'
                    # _log_audit('config_proposal', p.id, {'status': old_status}, {'status': 'completed'})
                    flash('Checklist hoàn tất. Đề xuất đã được chuyển sang trạng thái Hoàn thành.', 'success')
            else:
                flash('Không thể xác nhận hóa đơn. Đảm bảo đã bàn giao cho người dùng và hóa đơn chưa được nhận.', 'danger')

        elif action == 'reject':
            # Simplified reject logic
            can_reject = False
            if p.status == 'new' and (is_manager or 'config_proposals.approve_team' in permissions): can_reject = True
            elif p.status == 'it_consulted' and ('config_proposals.approve_director' in permissions): can_reject = True
            elif current_user.role == 'admin': can_reject = True
            
            if not can_reject:
                flash('Bạn không có quyền từ chối ở bước này.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))

            p.status = 'rejected'
            p.rejection_reason = note
            
            log = OrderTracking(
                proposal_id=p.id,
                status_content="Từ chối đề xuất",
                note=f"Lý do: {note}",
                updated_by=current_user.id
            )
            db.session.add(log)
            
            flash(f'Đã từ chối đề xuất. Lý do: {note}', 'warning')

        elif action == 'resubmit':
            if p.status != 'rejected':
                flash('Chỉ có thể gửi lại đề xuất khi đã bị từ chối.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            if current_user.id != p.created_by and current_user.role != 'admin':
                flash('Chỉ người tạo mới có thể gửi lại đề xuất này.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            p.status = 'new'
            p.rejection_reason = None
            p.team_lead_approved_at = None
            p.team_lead_approver_id = None
            p.it_consulted_at = None
            p.it_consultant_id = None
            p.it_consultation_note = None
            p.finance_reviewed_at = None
            p.finance_reviewer_id = None
            p.finance_review_note = None
            p.director_approved_at = None
            p.director_approver_id = None
            p.director_approval_note = None
            p.current_stage_deadline = None
            
            log = OrderTracking(
                proposal_id=p.id,
                status_content="Gửi duyệt lại",
                note=note or "Đề xuất đã được cập nhật và gửi duyệt lại.",
                updated_by=current_user.id
            )
            db.session.add(log)
            flash('Đã gửi duyệt lại thành công.', 'success')

        db.session.commit()
        
        # Notifications for Proposal Actions
        action_names = {
            'approve_team': 'Duyệt (Bộ phận)',
            'consult_it': 'IT đã lập phương án',
            'approve_director': 'Phê duyệt',
            'start_purchasing': 'Bắt đầu mua sắm',
            'confirm_payment': 'Thanh toán hoàn tất',
            'confirm_goods_received': 'Đã nhận hàng',
            'confirm_handover': 'Bàn giao thiết bị xong',
            'confirm_invoice': 'Nhận hóa đơn',
            'reject': 'Từ chối'
        }
        if action in action_names:
            msg_user = f"Đề xuất '{p.name}' của bạn vừa được: {action_names[action]}."
            msg_group = f"Đề xuất '{p.name}' vừa được {action_names[action]}."
            notify_user(p.created_by, msg_user, url_for('config_proposal_detail', proposal_id=p.id))
            notify_group(msg_group, url_for('config_proposal_detail', proposal_id=p.id))
            
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi xử lý: {str(e)}', 'danger')

    return redirect(url_for('config_proposal_detail', proposal_id=p.id))

@app.route('/config_proposals/<int:proposal_id>')
def config_proposal_detail(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    if not _can_access_config_proposal(p):
        flash('Bạn không có quyền xem đề xuất này.', 'danger')
        return redirect(url_for('config_proposals'))
    items = ConfigProposalItem.query.filter_by(proposal_id=proposal_id).order_by(ConfigProposalItem.order_no).all()
    p = ConfigProposal.query.get_or_404(proposal_id)
    items = ConfigProposalItem.query.filter_by(proposal_id=proposal_id).order_by(ConfigProposalItem.order_no).all()
    logs = OrderTracking.query.filter_by(proposal_id=proposal_id).order_by(OrderTracking.updated_at.desc()).all()
    return render_template('config_proposal_detail.html', p=p, items=items, logs=logs, current_permissions=_get_current_permissions())

@app.route('/config_proposals/tracking/<int:tracking_id>/edit', methods=['POST'])
def edit_proposal_order_tracking(tracking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    log = OrderTracking.query.get_or_404(tracking_id)
    if log.updated_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền sửa ghi chú này.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))
    
    note = request.form.get('note')
    if note is not None:
        log.note = note
        from datetime import datetime
        log.edited_at = datetime.utcnow()
        db.session.commit()
        flash('Đã sửa ghi chú bổ sung.', 'success')
    return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))

@app.route('/config_proposals/tracking/<int:tracking_id>/delete', methods=['POST'])
def delete_proposal_order_tracking(tracking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    log = OrderTracking.query.get_or_404(tracking_id)
    if log.updated_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền xóa ghi chú này.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))
    
    p_id = log.proposal_id
    db.session.delete(log)
    db.session.commit()
    flash('Đã xóa ghi chú bổ sung.', 'success')
    return redirect(url_for('config_proposal_detail', proposal_id=p_id))

@app.route('/config_proposals/<int:proposal_id>/add_tracking', methods=['POST'])
def add_proposal_order_tracking(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    
    status_content = request.form.get('status_content')
    note = request.form.get('note')
    
    if not status_content:
        flash('Vui lòng nhập trạng thái.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=proposal_id))
        
    log = OrderTracking(
        proposal_id=p.id,
        status_content=status_content,
        note=note,
        updated_by=session['user_id']
    )
    db.session.add(log)
    db.session.commit()
    flash('Đã cập nhật theo dõi đơn hàng.', 'success')
    return redirect(url_for('config_proposal_detail', proposal_id=proposal_id))

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
        priority=p.priority,
        currency=p.currency,
        status='new', # Reset to new
        subtotal=p.subtotal,
        vat_percent=p.vat_percent,
        vat_amount=p.vat_amount,
        total_amount=p.total_amount,
        quantity=p.quantity,
        supplier_info=p.supplier_info, # Copy supplier info
        created_by=session['user_id'] # Set creator to current user
    )
    db.session.add(new_p)
    db.session.flush()
    for it in ConfigProposalItem.query.filter_by(proposal_id=p.id).all():
        db.session.add(ConfigProposalItem(
            proposal_id=new_p.id,
            order_no=it.order_no,
            option_name=it.option_name,
            product_name=it.product_name,
            product_link=it.product_link,
            product_code=it.product_code,
            warranty=it.warranty,
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
    if not _can_access_config_proposal(p):
        flash('Bạn không có quyền sửa đề xuất này.', 'danger')
        return redirect(url_for('config_proposals'))
    current_permissions = _get_current_permissions()
    current_user = User.query.get(session['user_id']) # Ensure we have user obj
    
    # Check edit permission logic
    can_edit = False
    
    # 1. Unconditional Edit: Super Admin (Admin) or Creator
    # User request: "người tạo và người quản lý super admin có thể sửa ... bất kỳ lúc nào"
    # Assuming 'Admin' role is the "super admin" equivalent here.
    if session.get('role') == 'Admin' or (p.created_by and p.created_by == session['user_id']):
        can_edit = True
    
    # 2. Phase-specific Edit: IT Consultant during 'team_approved'
    # 2. Phase-specific Edit: IT Consultant during 'team_approved' or 'it_consulted' (updates)
    elif p.status in ['team_approved', 'it_consulted']:
        if 'config_proposals.consult_it' in current_permissions:
             can_edit = True
    
    # 3. Phase-specific Edit (legacy/fallback): New/Rejected for others?
    # Usually covered by Creator check, but maybe someone else with 'edit' perm needs access?
    elif p.status in ['new', 'rejected'] and 'config_proposals.edit' in current_permissions:
        can_edit = True
             
    if not can_edit:
        flash('Bạn không có quyền sửa đề xuất này ở trạng thái hiện tại.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=p.id))

    # Fetch users for proposer selection (same dept as creator or current user?)
    # Usually editing allows changing proposer within same dept? Or just list current user's dept?
    # Let's list Creator's department users if possible, or Current User's. 
    # Current User is likely Creator or IT. If IT, they might want to see Creator's dept.
    # Safe bet: Users in Proposer's Unit if matched to a Dept, otherwise Current User's Dept.
    # Fetch users for proposer selection
    # If Admin, show ALL users. 
    # Else: Usually Creator's Dept, or Current User's Dept if new.
    dept_users = []
    
    dept_users = _visible_users_query_for(current_user).all()

    if request.method == 'POST':
        try:
            # If IT is editing (status=team_approved), don't allow changing core info like Proposer? 
            # For simplicity, allow editing most fields, or maybe just items/prices.
            # User request: "IT support sửa cấu hình và đơn giá".
            # Let's keep it simple: allow full edit form but maybe we should ideally restrict some fields.
            # Given the simple codebase, reusing the whole form is incorrectly easier and acceptable.
            
            p.name = request.form.get('name') or p.name
            
            # Only allow changing date/proposer if new/rejected?
            # if p.status in ['new', 'rejected']: ...
            # Let's trust IT not to mess up Proposer info.
            
            date_str = request.form.get('proposal_date')
            if date_str:
                p.proposal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            selected_proposer_name = request.form.get('proposer_name')
            try:
                selected_proposer = _visible_users_query_for(current_user).filter(User.id == int(selected_proposer_name)).first()
            except Exception:
                selected_proposer = _visible_users_query_for(current_user).filter(
                    or_(User.full_name == selected_proposer_name, User.username == selected_proposer_name)
                ).first()
            if selected_proposer:
                p.proposer_name = selected_proposer.full_name or selected_proposer.username
                p.proposer_unit = selected_proposer.department_info.name if selected_proposer.department_info else ''
            p.scope = request.form.get('scope')
            p.priority = request.form.get('priority') or p.priority
            p.currency = request.form.get('currency') or 'VND'
            # Status update via edit removed to prevent workflow disruption.
            # p.status should only change via action buttons.

            # purchase_status removed
            p.notes = request.form.get('notes')
            p.supplier_info = request.form.get('supplier_info') # Restored as per user request
            p.general_requirements = request.form.get('general_requirements')
            req_date = request.form.get('required_date')
            if req_date:
                p.required_date = datetime.strptime(req_date, '%Y-%m-%d').date()
            
            # VAT Logic: Only update if provided, otherwise keep existing
            new_vat = request.form.get('vat_percent', type=float)
            if new_vat is not None:
                p.vat_percent = new_vat
            elif p.vat_percent is None:
                 p.vat_percent = 10.0 # Default if both new and old are None
                 
            p.quantity = request.form.get('quantity', type=int) or 1
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
                option_name = request.form.get(f'{prefix}[option_name]')
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
                    option_name=option_name,
                    product_name=product_name,
                    product_link=product_link,
                    product_code=product_code,
                    warranty=warranty,
                    quantity=max(0, quantity),
                    unit_price=max(0.0, unit_price),
                    line_total=line_total
                ))

            p.subtotal = subtotal
            grand_subtotal = subtotal * p.quantity
            p.vat_amount = round(grand_subtotal * (p.vat_percent / 100.0), 2)
            p.total_amount = round(grand_subtotal + p.vat_amount, 2)
            db.session.commit()
            
            if request.form.get('submit_action') == 'resubmit' and p.status == 'rejected':
                p.status = 'new'
                p.rejection_reason = None
                p.team_lead_approved_at = None
                p.team_lead_approver_id = None
                p.it_consulted_at = None
                p.it_consultant_id = None
                p.it_consultation_note = None
                p.finance_reviewed_at = None
                p.finance_reviewer_id = None
                p.finance_review_note = None
                p.director_approved_at = None
                p.director_approver_id = None
                p.director_approval_note = None
                p.current_stage_deadline = None
                
                log_update = OrderTracking(
                    proposal_id=p.id,
                    status_content="Sửa thông tin đề xuất",
                    note="Đã cập nhật lại thông tin cấu hình/giá.",
                    updated_by=current_user.id
                )
                db.session.add(log_update)
                
                log_resubmit = OrderTracking(
                    proposal_id=p.id,
                    status_content="Gửi duyệt lại",
                    note="Đề xuất đã được gửi duyệt lại.",
                    updated_by=current_user.id
                )
                db.session.add(log_resubmit)
                db.session.commit()
                flash('Đã lưu cấu hình và khởi tạo lại phiên duyệt.', 'success')
            else:
                flash('Đã cập nhật đề xuất.', 'success')
            return redirect(url_for('config_proposal_detail', proposal_id=p.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi cập nhật: {str(e)}', 'danger')
            return redirect(url_for('edit_config_proposal', proposal_id=p.id))
    
    # Redundant check removed. Permissions are handled at the start of the function.
    # if p.status not in ['new', 'rejected'] and session.get('role') != 'admin': ...

    # GET
    items = ConfigProposalItem.query.filter_by(proposal_id=p.id).order_by(ConfigProposalItem.order_no).all()
    return render_template('edit_config_proposal.html', p=p, items=items, users=dept_users)

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
    
    admin_password = os.environ.get('ADMIN_PASSWORD')
    if not admin_password:
        click.echo("ADMIN_PASSWORD must be set before running flask create-admin.")
        return

    admin_user = User(
        username='admin',
        password=generate_password_hash(admin_password),
        full_name='Quản Trị Viên',
        email='admin@example.com',
        role='admin',
        department_id=it_dept.id  # Sử dụng department_id thay vì department
    )
    db.session.add(admin_user)
    
    # Set admin user làm manager của IT department
    it_dept.manager_id = admin_user.id
    
    db.session.commit()
    click.echo("Admin account created.")



@app.route('/backup/export')
def backup_export():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    # Kiểm tra phân quyền: chỉ admin hoặc người có quyền backup.view mới được truy cập
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    temp_backup_file = None
    try:
        # Create a temporary file for the zip
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.bak')
        temp_backup_file = temp_file.name
        temp_file.close()

        # Use shared backup logic
        with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
            if not lock_acquired:
                if temp_backup_file and os.path.exists(temp_backup_file):
                    os.unlink(temp_backup_file)
                flash('Đang có tiến trình backup/restore khác chạy. Vui lòng chờ hoàn tất rồi thử lại.', 'warning')
                return redirect(url_for('backup_page'))
            backup = DatabaseBackup()
            backup.create_backup(temp_backup_file)
        
        # Check if backup file was created and has content
        if not os.path.exists(temp_backup_file) or os.path.getsize(temp_backup_file) == 0:
            if os.path.exists(temp_backup_file):
                os.unlink(temp_backup_file)
            flash('Không thể tạo file backup.', 'danger')
            return redirect(url_for('backup_page'))

        backup_filename = os.path.basename(temp_backup_file)
        # Rename to user-friendly name if needed, but DatabaseBackup might have named it based on time
        # Actually DatabaseBackup doesn't change filename if provided.
        # Let's give it a nice name for download
        timestamp = datetime.now(VIETNAM_TZ).strftime('%Y%m%d_%H%M%S')
        download_filename = f'backup_inventory_{timestamp}.bak'

        def remove_file(response, path=temp_backup_file):
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass
            return response
        
        return remove_file(send_file(
            temp_backup_file,
            as_attachment=True,
            download_name=download_filename,
            mimetype='application/octet-stream'
        ))
    except Exception as e:
        try:
            if temp_backup_file and os.path.exists(temp_backup_file):
                os.unlink(temp_backup_file)
        except Exception:
            pass
        flash(f'Lỗi khi tạo backup: {str(e)}', 'danger')
        return redirect(url_for('backup_page'))

@app.route('/backup/import', methods=['POST'])
def backup_import():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    if 'backup_file' not in request.files:
        flash('Vui lòng chọn file backup.', 'danger')
        return redirect(url_for('backup_page'))
    
    file = request.files['backup_file']
    if file.filename == '':
        flash('Vui lòng chọn file backup.', 'danger')
        return redirect(url_for('backup_page'))
    
    if not _is_backup_filename(file.filename):
        flash('File backup phải có định dạng .bak hoặc .zip backup cũ.', 'danger')
        return redirect(url_for('backup_page'))
    
    try:
        # Lưu file tạm
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.bak')
        temp_path = temp_file.name
        temp_file.close()
        
        file.save(temp_path)
        
        # Use shared backup logic for restore
        with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
            if not lock_acquired:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                flash('Đang có tiến trình backup/restore khác chạy. Vui lòng chờ hoàn tất rồi thử lại.', 'warning')
                return redirect(url_for('backup_page'))
            backup = DatabaseBackup()
            db.session.remove()
            db.engine.dispose()
            success = backup.restore_backup(temp_path)
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
        if success:
            flash('Import backup thành công!', 'success')
        else:
            flash('Lỗi khi import backup. Vui lòng kiểm tra log.', 'danger')
            
        return redirect(url_for('backup_page'))
        
    except Exception as e:
        flash(f'Lỗi khi import backup: {str(e)}', 'danger')
        return redirect(url_for('backup_page'))

@app.route('/backup/config', methods=['GET', 'POST'])
def backup_config():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        # Update backup configuration
        daily_enabled = request.form.get('daily_enabled') == 'on'
        weekly_enabled = request.form.get('weekly_enabled') == 'on'
        daily_time = request.form.get('daily_time', '02:00')
        weekly_time = request.form.get('weekly_time', '03:00')

        # Persist in globals and save to instance/backup_config.json
        global backup_config_daily_enabled, backup_config_weekly_enabled, backup_config_daily_time, backup_config_weekly_time
        backup_config_daily_enabled = daily_enabled
        backup_config_weekly_enabled = weekly_enabled
        backup_config_daily_time = daily_time
        backup_config_weekly_time = weekly_time
        try:
            with open(_backup_cfg_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'daily_enabled': backup_config_daily_enabled,
                    'weekly_enabled': backup_config_weekly_enabled,
                    'daily_time': backup_config_daily_time,
                    'weekly_time': backup_config_weekly_time
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        flash('Cấu hình backup tự động đã được cập nhật! (Thời gian theo GMT+7)', 'success')
        return redirect(url_for('backup_config'))
    
    # GET - show current configuration
    # Use global backup configuration variables
    daily_enabled = backup_config_daily_enabled
    weekly_enabled = backup_config_weekly_enabled
    daily_time = backup_config_daily_time
    weekly_time = backup_config_weekly_time
    
    # Get current Vietnam time for display
    now_vn = datetime.now(VIETNAM_TZ)
    current_time = now_vn.strftime('%H:%M:%S')
    current_date = now_vn.strftime('%d/%m/%Y')
    
    return render_template('backup_config.html', 
                         daily_enabled=daily_enabled,
                         weekly_enabled=weekly_enabled,
                         daily_time=daily_time,
                         weekly_time=weekly_time,
                         current_time=current_time,
                         current_date=current_date)





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

@app.route('/bug_reports/<int:report_id>/request_reopen', methods=['POST'])
def request_reopen_bug_report(report_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')

    bug_report = BugReport.query.get_or_404(report_id)
    if bug_report.status != 'Đã đóng':
        flash('Vấn đề hiện chưa được đóng.', 'info')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    if bug_report.created_by != user_id:
        flash('Chỉ người tạo mới có thể yêu cầu mở lại vấn đề.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    if bug_report.reopen_requested:
        flash('Bạn đã gửi yêu cầu mở lại. Vui lòng chờ quản trị viên xử lý.', 'info')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    try:
        bug_report.reopen_requested = True
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Đã gửi yêu cầu mở lại. Quản trị viên sẽ xem xét.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi gửi yêu cầu mở lại: {str(e)}', 'danger')

    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/rate', methods=['POST'])
def rate_bug_report(report_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')

    bug_report = BugReport.query.get_or_404(report_id)
    if bug_report.created_by != user_id:
        flash('Chỉ người tạo mới có thể đánh giá.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    if bug_report.status != 'Đã đóng':
        flash('Chỉ có thể đánh giá khi vấn đề đã được đóng.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    try:
        rating = request.form.get('rating', type=int)
        if rating not in [1, 2, 3, 4, 5]:
            flash('Giá trị đánh giá không hợp lệ.', 'danger')
            return redirect(url_for('bug_report_detail', report_id=report_id))

        bug_report.rating = rating
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Đã ghi nhận đánh giá của bạn. Cảm ơn!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi lưu đánh giá: {str(e)}', 'danger')

    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/add_related', methods=['POST'])
def add_related_bug_report(report_id):
    """Thêm báo lỗi liên quan"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    bug_report = BugReport.query.get_or_404(report_id)
    can_manage_bug_reports, _ = _bug_permission_flags(current_permissions, User.query.get(user_id))
    is_creator = bug_report.created_by == user_id
    
    if not (can_manage_bug_reports or is_creator):
        flash('Bạn không có quyền thêm báo lỗi liên quan.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    related_id = request.form.get('related_id', type=int)
    if not related_id or related_id == report_id:
        flash('Báo lỗi liên quan không hợp lệ.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    related_report = BugReport.query.get(related_id)
    if not related_report:
        flash('Báo lỗi không tồn tại.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    try:
        # Kiểm tra xem đã liên kết chưa
        if related_report not in bug_report.related_reports.all():
            bug_report.related_reports.append(related_report)
            # Tạo liên kết 2 chiều
            if bug_report not in related_report.related_reports.all():
                related_report.related_reports.append(bug_report)
            db.session.commit()
            flash('Đã thêm báo lỗi liên quan.', 'success')
        else:
            flash('Báo lỗi này đã được liên kết.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi thêm báo lỗi liên quan: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/remove_related/<int:related_id>', methods=['POST'])
def remove_related_bug_report(report_id, related_id):
    """Xóa báo lỗi liên quan"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    bug_report = BugReport.query.get_or_404(report_id)
    can_manage_bug_reports, _ = _bug_permission_flags(current_permissions, User.query.get(user_id))
    is_creator = bug_report.created_by == user_id
    
    if not (can_manage_bug_reports or is_creator):
        flash('Bạn không có quyền xóa báo lỗi liên quan.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    try:
        related_report = BugReport.query.get(related_id)
        if related_report and related_report in bug_report.related_reports.all():
            bug_report.related_reports.remove(related_report)
            # Xóa liên kết 2 chiều
            if bug_report in related_report.related_reports.all():
                related_report.related_reports.remove(bug_report)
            db.session.commit()
            flash('Đã xóa báo lỗi liên quan.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi xóa báo lỗi liên quan: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/merge', methods=['POST'])
def merge_bug_reports(report_id):
    """Gộp nhiều báo lỗi vào một báo lỗi chính"""
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()
    
    bug_report = BugReport.query.get_or_404(report_id)
    can_manage_bug_reports, _ = _bug_permission_flags(current_permissions, User.query.get(user_id))
    
    if not can_manage_bug_reports:
        flash('Chỉ quản trị viên mới có quyền gộp báo lỗi.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    merge_ids = request.form.getlist('merge_ids')
    if not merge_ids:
        flash('Vui lòng chọn ít nhất một báo lỗi để gộp.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    
    try:
        merged_count = 0
        for merge_id_str in merge_ids:
            try:
                merge_id = int(merge_id_str)
                if merge_id == report_id:
                    continue
                merge_report = BugReport.query.get(merge_id)
                if merge_report and not merge_report.merged_into:
                    merge_report.merged_into = report_id
                    # Cập nhật mô tả của báo lỗi chính để tham chiếu các báo lỗi đã gộp
                    if merge_report.title:
                        note = f"\n\n[Đã gộp từ báo lỗi #{merge_id}: {merge_report.title}]"
                        if bug_report.resolution:
                            bug_report.resolution += note
                        else:
                            bug_report.resolution = note
                    merged_count += 1
            except (ValueError, TypeError):
                continue
        
        if merged_count > 0:
            bug_report.updated_at = datetime.utcnow()
            db.session.commit()
            flash(f'Đã gộp {merged_count} báo lỗi vào báo lỗi này.', 'success')
        else:
            flash('Không có báo lỗi nào được gộp.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi gộp báo lỗi: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

@app.route('/bug_reports/<int:report_id>/close', methods=['POST'])
def close_bug_report(report_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    current_permissions = _get_current_permissions()

    bug_report = BugReport.query.get_or_404(report_id)
    is_creator = bug_report.created_by == user_id
    can_manage_bug_reports, _ = _bug_permission_flags(current_permissions, User.query.get(user_id))

    if not (is_creator or can_manage_bug_reports):
        flash('Bạn không có quyền đóng vấn đề này.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))
    if bug_report.status == 'Đã đóng':
        flash('Vấn đề đã được đóng trước đó.', 'info')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    try:
        bug_report.status = 'Đã đóng'
        bug_report.resolved_at = datetime.utcnow()
        bug_report.reopen_requested = False
        if not bug_report.rating:
            bug_report.rating = 5
        bug_report.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Đã đóng vấn đề.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi đóng vấn đề: {str(e)}', 'danger')

    return redirect(url_for('bug_report_detail', report_id=report_id))


# --- Resource Management Routes ---
@app.route('/resources')
def resources():
    if 'user_id' not in session: return redirect(url_for('login'))
    perms = _get_current_permissions()
    if 'resources.view' not in perms and session.get('role') != 'admin':
        flash('Bạn không có quyền xem danh sách tài nguyên.', 'danger')
        return redirect(url_for('home'))
        
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search', '').strip()
    
    query = Resource.query
    
    if search_query:
        # Search by IP or Service Name or Web UI
        query = query.filter(or_(
            Resource.ip_address.ilike(f'%{search_query}%'),
            Resource.service_name.ilike(f'%{search_query}%'),
            Resource.web_ui.ilike(f'%{search_query}%')
        ))
    
    # Order by ID desc
    query = query.order_by(Resource.id.desc())
    
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    devices = Device.query.all()
    
    return render_template('resources/index.html', resources=pagination, devices=devices, search=search_query)

@app.route('/resources/add', methods=['POST'])
def add_resource():
    if 'user_id' not in session: return redirect(url_for('login'))
    perms = _get_current_permissions()
    if 'resources.edit' not in perms and session.get('role') != 'admin':
        flash('Bạn không có quyền thêm tài nguyên.', 'danger')
        return redirect(url_for('resources'))
    
    ip_address = request.form.get('ip_address')
    web_ui = request.form.get('web_ui')
    service_name = request.form.get('service_name')
    status = request.form.get('status', 'Offline')
    device_id = request.form.get('device_id')
    notes = request.form.get('notes')
    
    if not ip_address:
        flash('Vui lòng nhập địa chỉ IP.', 'danger')
        return redirect(url_for('resources'))
        
    resource = Resource(
        ip_address=ip_address,
        web_ui=web_ui,
        service_name=service_name,
        status=status,
        device_id=int(device_id) if device_id else None,
        notes=notes
    )
    db.session.add(resource)
    db.session.commit()
    flash('Thêm tài nguyên thành công!', 'success')
    return redirect(url_for('resources'))

@app.route('/resources/edit/<int:id>', methods=['POST'])
def edit_resource(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    perms = _get_current_permissions()
    if 'resources.edit' not in perms and session.get('role') != 'admin':
        flash('Bạn không có quyền sửa tài nguyên.', 'danger')
        return redirect(url_for('resources'))
    
    resource = Resource.query.get_or_404(id)
    
    resource.ip_address = request.form.get('ip_address')
    resource.web_ui = request.form.get('web_ui')
    resource.service_name = request.form.get('service_name')
    resource.status = request.form.get('status')
    device_id = request.form.get('device_id')
    resource.device_id = int(device_id) if device_id else None
    resource.notes = request.form.get('notes')
    
    db.session.commit()
    flash('Cập nhật tài nguyên thành công!', 'success')
    return redirect(url_for('resources'))

# ---------- Backup Management ----------
import json, os, shutil, threading, time
from datetime import datetime
from flask import send_from_directory, flash, redirect, url_for, request, render_template

# Helper to list backup files
def _backup_storage_dir():
    return os.path.abspath(backup_path)

def _is_backup_filename(filename):
    return bool(filename and filename.lower().endswith(('.bak', '.zip')))

def _list_backups():
    backup_dir = _backup_storage_dir()
    files = []
    if os.path.isdir(backup_dir):
        for f in os.listdir(backup_dir):
            if _is_backup_filename(f):
                path = os.path.join(backup_dir, f)
                files.append({
                    'name': f,
                    'size': os.path.getsize(path),
                    'date': datetime.fromtimestamp(os.path.getmtime(path), pytz.utc).astimezone(VIETNAM_TZ).strftime('%Y-%m-%d %H:%M:%S')
                })
    return sorted(files, key=lambda item: item['date'], reverse=True)

def _lock_dir():
    path = os.path.join(instance_path, 'locks')
    os.makedirs(path, exist_ok=True)
    return path

def _pid_is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _read_lock_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _process_start_time(pid):
    try:
        with open(f'/proc/{int(pid)}/stat', 'r', encoding='utf-8') as f:
            return f.read().split()[21]
    except Exception:
        return None

def _lock_is_stale(path, stale_after_seconds):
    data = _read_lock_file(path)
    pid = data.get('pid')
    pid_start_time = data.get('pid_start_time')
    created_at = data.get('created_at', 0)
    age = time.time() - float(created_at or 0)
    if os.path.basename(path) == 'backup_task.lock' and (
        data.get('lock_name') != 'backup_task' or not pid_start_time
    ):
        return True
    if age > stale_after_seconds:
        return True
    if pid and not _pid_is_running(int(pid)):
        return True
    if pid and pid_start_time and _process_start_time(pid) != str(pid_start_time):
        return True
    return False

@contextmanager
def _exclusive_file_lock(lock_name, stale_after_seconds=7200):
    path = os.path.join(_lock_dir(), f'{lock_name}.lock')
    fd = None
    acquired = False
    try:
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = json.dumps({
                    'pid': os.getpid(),
                    'created_at': time.time(),
                    'pid_start_time': _process_start_time(os.getpid()),
                    'lock_name': lock_name
                }).encode('utf-8')
                os.write(fd, payload)
                acquired = True
                break
            except FileExistsError:
                if _lock_is_stale(path, stale_after_seconds):
                    try:
                        os.remove(path)
                        continue
                    except FileNotFoundError:
                        continue
                break
        yield acquired
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

_backup_scheduler_lock_fd = None

def _acquire_backup_scheduler_lock():
    global _backup_scheduler_lock_fd
    path = os.path.join(_lock_dir(), 'backup_scheduler.lock')
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = json.dumps({'pid': os.getpid(), 'created_at': time.time()}).encode('utf-8')
            os.write(fd, payload)
            _backup_scheduler_lock_fd = fd
            return True
        except FileExistsError:
            if _lock_is_stale(path, 3600):
                try:
                    os.remove(path)
                    continue
                except FileNotFoundError:
                    continue
            return False

def _backup_schedule_enabled():
    cfg_path = os.path.join(instance_path, 'backup_config.json')
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if cfg.get('frequency') == 'none':
                return False
            return True
    except Exception:
        pass
    return bool(backup_config_daily_enabled)

def _backup_task_active():
    lock_path = os.path.join(_lock_dir(), 'backup_task.lock')
    if not os.path.exists(lock_path):
        return False
    lock_data = _read_lock_file(lock_path)
    if lock_data.get('lock_name') != 'backup_task' or not lock_data.get('pid_start_time'):
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
        return False
    if _lock_is_stale(lock_path, 1800):
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
        return False
    return True

def _cleanup_stale_backup_logs():
    task_active = _backup_task_active()
    try:
        stale_cutoff = datetime.utcnow() - timedelta(minutes=30)
        query = BackupLog.query.filter(BackupLog.status == 'processing')
        if task_active:
            query = query.filter(BackupLog.created_at < stale_cutoff)
        stale_logs = query.all() if not task_active else []
        for log in stale_logs:
            log.status = 'failed'
            log.details = 'Tiến trình backup/restore không còn chạy hoặc đã quá thời gian chờ.'
        if stale_logs:
            db.session.commit()
    except Exception:
        db.session.rollback()
    return task_active

# Route: backup management page
@app.route('/backup', methods=['GET'])
def backup_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
        
    backup_task_active = _cleanup_stale_backup_logs()

    backups = _list_backups()
    logs = BackupLog.query.order_by(BackupLog.created_at.desc()).limit(50).all()
    
    # Cleanup old logs (older than 30 days)
    try:
        cutoff = datetime.utcnow() - timedelta(days=30)
        BackupLog.query.filter(BackupLog.created_at < cutoff).delete()
        db.session.commit()
    except:
        db.session.rollback()

    return render_template('backup.html', backups=backups, logs=logs, backup_task_active=backup_task_active)

# Route: manual backup creation
@app.route('/backup/create', methods=['POST'])
def backup_create():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền tạo backup.', 'danger')
        return redirect(url_for('backup_page'))
        
    try:
        with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
            if not lock_acquired:
                flash('Đang có tiến trình backup/restore khác chạy. Vui lòng chờ hoàn tất rồi thử lại.', 'warning')
                return redirect(url_for('backup_page'))

            backup_dir = _backup_storage_dir()
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir, exist_ok=True)

            timestamp = get_now().strftime("%Y%m%d_%H%M%S")
            filename = f"manual_backup_{timestamp}.bak"
            dest_path = os.path.join(backup_dir, filename)

            backup = DatabaseBackup()
            backup.create_backup(dest_path)

        # Log the action
        log = BackupLog(
            filename=filename,
            action='backup',
            status='success',
            user_id=session.get('user_id')
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f"Đã tạo bản sao lưu thành công: {filename}", 'success')
        
    except Exception as e:
        db.session.rollback()
        log = BackupLog(
            filename='N/A',
            action='backup',
            status='failed',
            details=str(e),
            user_id=session.get('user_id')
        )
        db.session.add(log)
        db.session.commit()
        flash(f"Lỗi khi tạo backup: {str(e)}", 'danger')
        
    return redirect(url_for('backup_page'))

# Route: download backup
@app.route('/backup/download/<filename>', methods=['GET'])
def backup_download(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền tải backup.', 'danger')
        return redirect(url_for('backup_page'))
        
    if not _is_backup_filename(filename):
        flash('File sao lưu không hợp lệ.', 'danger')
        return redirect(url_for('backup_page'))
    backup_dir = _backup_storage_dir()
    return send_from_directory(backup_dir, filename, as_attachment=True)

def _record_restore_result(log_id, filename, user_id, success, details):
    db.session.remove()
    log = BackupLog.query.get(log_id) if log_id else None
    if log is None:
        log = BackupLog(filename=filename, action='restore', user_id=user_id)
        db.session.add(log)
    log.status = 'success' if success else 'failed'
    log.details = details
    db.session.commit()

def _start_restore_thread(backup_dir, backup_path, filename, user_id):
    def run_restore_task():
        with app.app_context():
            log_id = None
            snapshot_filename = None
            try:
                with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
                    if not lock_acquired:
                        _record_restore_result(
                            None,
                            filename,
                            user_id,
                            False,
                            'Đang có tiến trình backup/restore khác chạy.'
                        )
                        return

                    snapshot_filename = f"pre_restore_snapshot_{get_now().strftime('%Y%m%d_%H%M%S')}.bak"
                    snapshot_path = os.path.join(backup_dir, snapshot_filename)

                    engine = DatabaseBackup()
                    engine.create_backup(snapshot_path)

                    log = BackupLog(
                        filename=filename,
                        action='restore',
                        status='processing',
                        details=f'Đang khôi phục. Snapshot dự phòng: {snapshot_filename}',
                        user_id=user_id
                    )
                    db.session.add(log)
                    db.session.commit()
                    log_id = log.id

                    db.session.remove()
                    db.engine.dispose()
                    success = engine.restore_backup(backup_path)

                if success:
                    details = f'Khôi phục thành công. Snapshot dự phòng: {snapshot_filename}'
                else:
                    details = 'Khôi phục thất bại. Xem log server để biết chi tiết.'
                _record_restore_result(log_id, filename, user_id, success, details)
            except Exception as e:
                print(f"Background Restore Error: {e}")
                try:
                    db.session.rollback()
                    _record_restore_result(log_id, filename, user_id, False, f'Error: {str(e)}')
                except Exception as log_error:
                    print(f"Could not write restore error log: {log_error}")

    thread = threading.Thread(target=run_restore_task, daemon=True)
    thread.start()
    return thread

# Route: restore backup
@app.route('/backup/restore/<filename>', methods=['POST'])
def backup_restore(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền khôi phục dữ liệu.', 'danger')
        return redirect(url_for('backup_page'))
        
    if not _is_backup_filename(filename):
        flash('File sao lưu không hợp lệ.', 'danger')
        return redirect(url_for('backup_page'))
    backup_dir = _backup_storage_dir()
    backup_path = os.path.join(backup_dir, filename)
    if not os.path.isfile(backup_path):
        flash('Không tìm thấy file sao lưu.', 'danger')
        return redirect(url_for('backup_page'))
    
    user_id = session.get('user_id')
    _start_restore_thread(backup_dir, backup_path, filename, user_id)
    flash('Tiến trình khôi phục đang chạy nền. Vui lòng kiểm tra Nhật ký để xem kết quả.', 'info')
    return redirect(url_for('backup_page'))
    
    def run_restore_task(app_context, b_path, b_filename, u_id):
        with app_context:
            log_id = None
            snapshot_filename = None
            try:
                # Log start
                log = BackupLog(
                    filename=b_filename,
                    action='restore',
                    status='processing',
                    details='Đang bắt đầu khôi phục...',
                    user_id=u_id
                )
                db.session.add(log)
                db.session.commit()
                log_id = log.id

                with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
                    if not lock_acquired:
                        final_log = BackupLog.query.get(log_id)
                        final_log.status = 'failed'
                        final_log.details = 'Đang có tiến trình backup/restore khác chạy.'
                        db.session.commit()
                        return

                    # Create snapshot
                    snapshot_filename = f"pre_restore_snapshot_{get_now().strftime('%Y%m%d_%H%M%S')}.bak"
                    snapshot_path = os.path.join(backup_dir, snapshot_filename)

                    engine = DatabaseBackup()
                    engine.create_backup(snapshot_path)

                    # Perform restore
                    success = engine.restore_backup(b_path)
                
                # Update log
                final_log = BackupLog.query.get(log_id)
                if success:
                    final_log.status = 'success'
                    final_log.details = f"Khôi phục thành công. Snapshot: {snapshot_filename}"
                else:
                    final_log.status = 'failed'
                    final_log.details = "Lỗi trong quá trình khôi phục (xem log server)"
                db.session.commit()
                
            except Exception as e:
                print(f"Background Restore Error: {e}")
                try:
                    # Logic to log error if possible
                    error_log = BackupLog(
                        filename=b_filename,
                        action='restore',
                        status='failed',
                        details=f"Error: {str(e)}",
                        user_id=u_id
                    )
                    db.session.add(error_log)
                    db.session.commit()
                except:
                    pass

    # Start background thread
    thread = threading.Thread(
        target=run_restore_task, 
        args=(app.app_context(), backup_path, filename, user_id)
    )
    thread.daemon = True
    thread.start()
    
    flash('Tiến trình khôi phục đang được thực hiện dưới nền. Vui lòng kiểm tra Nhật ký sau 1-2 phút để xem kết quả.', 'info')
    return redirect(url_for('backup_page'))


# Route: configure automatic backup schedule
@app.route('/backup/schedule', methods=['POST'])
def backup_schedule():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền cấu hình backup.', 'danger')
        return redirect(url_for('backup_page'))
        
    hour = int(request.form.get('hour', 2))
    minute = int(request.form.get('minute', 0))
    frequency = request.form.get('frequency', 'daily')
    
    # Save config
    cfg = {
        'frequency': frequency,
        'hour': hour,
        'minute': minute
    }
    cfg_path = os.path.join(instance_path, 'backup_config.json')
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f)
    
    freq_map = {
        'none': 'đã tắt',
        'daily': 'hàng ngày',
        'weekly': 'hàng tuần',
        'monthly': 'hàng tháng'
    }
    flash(f"Đã cập nhật lịch sao lưu tự động: {freq_map.get(frequency, frequency)} lúc {hour:02d}:{minute:02d}", 'success')
    return redirect(url_for('backup_page'))

# Background scheduler thread using schedule library
def _run_scheduler():
    def job():
        try:
            with _exclusive_file_lock('backup_task', stale_after_seconds=7200) as lock_acquired:
                if not lock_acquired:
                    print("Scheduled backup skipped because another backup/restore is running.")
                    return
                backup_dir = _backup_storage_dir()
                os.makedirs(backup_dir, exist_ok=True)
                timestamp = get_now().strftime("%Y%m%d_%H%M%S")
                filename = f"auto_backup_{timestamp}.bak"
                dest_path = os.path.join(backup_dir, filename)
                backup = DatabaseBackup()
                backup.create_backup(dest_path)
                print(f"✅ Scheduled backup saved to {dest_path}")
        except Exception as e:
            print(f"❌ Scheduled backup failed: {str(e)}")
            
    # Load schedule config
    try:
        cfg_path = os.path.join(instance_path, 'backup_config.json')
        frequency = 'daily'
        hour, minute = 2, 0
        
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                frequency = cfg.get('frequency', 'daily')
                hour = cfg.get('hour', 2)
                minute = cfg.get('minute', 0)
        
        if frequency == 'none':
            print("Auto backup is disabled.")
            return

        time_str = f"{hour:02d}:{minute:02d}"
        if frequency == 'daily':
            schedule.every().day.at(time_str).do(job)
            print(f"Auto backup scheduled: Daily at {time_str}")
        elif frequency == 'weekly':
            schedule.every().monday.at(time_str).do(job)
            print(f"Auto backup scheduled: Weekly (Mon) at {time_str}")
        elif frequency == 'monthly':
            # schedule doesn't have every().month directly easily without custom logic
            # but we can use every(30).days or similar, or just check day 1
            def monthly_job():
                if datetime.now().day == 1:
                    job()
            schedule.every().day.at(time_str).do(monthly_job)
            print(f"Auto backup scheduled: Monthly (Day 1) at {time_str}")
            
    except Exception as e:
        print(f"Error initializing backup scheduler: {e}")
        schedule.every().day.at("02:00").do(job)
        
    while True:
        schedule.run_pending()
        time.sleep(60)

# Start scheduler thread if enabled
if _backup_schedule_enabled() and _acquire_backup_scheduler_lock():
    t = threading.Thread(target=_run_scheduler, daemon=True)
    t.start()
elif _backup_schedule_enabled():
    print("Backup scheduler already running in another worker; skipping this worker.")
else:
    print("Auto backup scheduler disabled by configuration.")

@app.route('/backup/upload_restore', methods=['POST'])
def backup_upload_restore():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền khôi phục dữ liệu.', 'danger')
        return redirect(url_for('backup_page'))
        
    if 'backup_file' not in request.files:
        flash('Không có file nào được tải lên.', 'warning')
        return redirect(url_for('backup_page'))
        
    file = request.files['backup_file']
    if file.filename == '':
        flash('Chưa chọn file.', 'warning')
        return redirect(url_for('backup_page'))
        
    if not _is_backup_filename(file.filename):
        flash('Chỉ chấp nhận file .bak của hệ thống hoặc file .zip backup cũ.', 'danger')
        return redirect(url_for('backup_page'))
        
    try:
        backup_dir = _backup_storage_dir()
        os.makedirs(backup_dir, exist_ok=True)
        
        filename = secure_filename(file.filename)
        # Add timestamp to avoid collision
        filename = f"uploaded_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
        file_path = os.path.join(backup_dir, filename)
        file.save(file_path)
        
        # Now call restore
        return backup_restore(filename)
        
    except Exception as e:
        flash(f"Lỗi khi tải lên và khôi phục: {str(e)}", 'danger')
        return redirect(url_for('backup_page'))

@app.route('/backup/delete/<filename>', methods=['POST'])
def backup_delete(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.edit' not in current_permissions:
        flash('Bạn không có quyền xóa bản sao lưu.', 'danger')
        return redirect(url_for('backup_page'))
        
    try:
        backup_dir = _backup_storage_dir()
        filepath = os.path.join(backup_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            flash(f"Đã xóa bản sao lưu: {filename}", 'success')
        else:
            flash("Không tìm thấy file cần xóa.", 'warning')
    except Exception as e:
        flash(f"Lỗi khi xóa: {str(e)}", 'danger')
        
    return redirect(url_for('backup_page'))

# ---------- End of Backup Management ----------

def _require_device_permission(permission_code='devices.view'):
    if 'user_id' not in session:
        return False
    current_user = _get_current_user()
    current_permissions = _get_current_permissions()
    return bool(
        (current_user and current_user.role == 'admin') or
        (current_permissions and permission_code in current_permissions)
    )

def _parse_positive_int(value, field_name='Số lượng'):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} phải là số nguyên.')
    if parsed <= 0:
        raise ValueError(f'{field_name} phải lớn hơn 0.')
    return parsed

def _format_vietnam_datetime(value, fmt='%d-%m-%Y %H:%M'):
    if not value:
        return ''
    if value.tzinfo is None:
        value = pytz.utc.localize(value)
    value = value.astimezone(VIETNAM_TZ)
    return value.strftime(fmt)

@app.template_filter('vietnam_datetime')
def vietnam_datetime_filter(value, fmt='%d-%m-%Y %H:%M'):
    return _format_vietnam_datetime(value, fmt)

CONVERTED_CONSUMABLE_STATUS = 'Đã chuyển tiêu hao'

def _consumable_code_base_from_device_type(device_type):
    prefix = _get_device_type_code_prefix(device_type)
    if prefix:
        return prefix
    ascii_code = re.sub(r'[^A-Za-z0-9]+', '', device_type or '').upper()
    if ascii_code:
        return ascii_code[:20]
    return 'TH'

def _ascii_slug(value):
    normalized = unicodedata.normalize('NFKD', value or '')
    return ''.join(ch for ch in normalized if not unicodedata.combining(ch))

def _consumable_prefix_from_text(value):
    ascii_value = _ascii_slug(value).upper()
    words = re.findall(r'[A-Z0-9]+', ascii_value)
    if not words:
        return 'TH'
    if len(words) == 1:
        return words[0][:8]
    prefix = ''.join(word[0] for word in words[:4])
    return prefix[:8] or 'TH'

def _normalize_consumable_category(category):
    candidate = (category or '').strip()
    if not candidate:
        return 'Thiết bị tiêu hao'
    existing = ConsumableItem.query.filter(func.lower(ConsumableItem.category) == candidate.lower()).first()
    return existing.category if existing and existing.category else candidate

def _generate_consumable_code(category, name):
    prefix = _consumable_prefix_from_text(category or name)
    rows = db.session.query(ConsumableItem.code).filter(ConsumableItem.code.ilike(f'{prefix}-%')).all()
    max_number = 0
    pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.IGNORECASE)
    for row in rows:
        match = pattern.match(row[0] or '')
        if match:
            max_number = max(max_number, int(match.group(1)))
    for number in range(max_number + 1, max_number + 10000):
        code = f'{prefix}-{number:03d}'
        if not ConsumableItem.query.filter(func.upper(ConsumableItem.code) == code.upper()).first():
            return code
    return _unique_consumable_code(prefix)

def _unique_consumable_code(base_code):
    base_code = (base_code or 'TH').strip().upper()[:20]
    code = base_code
    counter = 1
    while ConsumableItem.query.filter(func.upper(ConsumableItem.code) == code).first():
        suffix = str(counter)
        code = f"{base_code[:20-len(suffix)]}{suffix}"
        counter += 1
    return code

def _record_consumable_transaction(item, transaction_type, quantity, *, issued_to_id=None, reason='', notes='', batch_id=None, location=None):
    before_quantity = item.current_quantity or 0
    if transaction_type == 'Nhập':
        after_quantity = before_quantity + quantity
    elif transaction_type == 'Xuất':
        if quantity > before_quantity:
            raise ValueError('Số lượng xuất lớn hơn tồn kho hiện tại.')
        after_quantity = before_quantity - quantity
    elif transaction_type == 'Điều chỉnh':
        after_quantity = quantity
        quantity = abs(after_quantity - before_quantity)
    else:
        raise ValueError('Loại giao dịch không hợp lệ.')

    item.current_quantity = after_quantity
    item.updated_at = get_now()
    transaction = ConsumableTransaction(
        item=item,
        transaction_type=transaction_type,
        quantity=quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        transaction_date=datetime.utcnow(),
        issued_to_id=issued_to_id,
        actor_id=session.get('user_id'),
        batch_id=batch_id,
        location=location or item.location,
        reason=reason,
        notes=notes
    )
    db.session.add(transaction)
    return transaction

def _consumable_categories():
    rows = db.session.query(ConsumableItem.category).filter(ConsumableItem.category != None).distinct().all()
    defaults = [
        'Dây mạng', 'Dây nhảy quang', 'Dây HDMI', 'Dây VGA', 'Dây DisplayPort',
        'Dây nguồn', 'Adapter', 'USB lưu trữ', 'Ổ cứng di động',
        'Đầu chuyển', 'Đầu nối mạng', 'Module quang', 'Phụ kiện mạng khác'
    ]
    categories = {row[0] for row in rows if row[0]}
    categories.update(defaults)
    return sorted(categories)

def _consumable_groups():
    rows = db.session.query(ConsumableItem.group_name).filter(ConsumableItem.group_name != None).distinct().all()
    defaults = [
        'Cáp mạng', 'Cáp quang', 'Module quang', 'Dây màn hình',
        'Thiết bị lưu trữ', 'Đầu chuyển / Adapter', 'Dây nguồn',
        'Phụ kiện mạng', 'Thiết bị USB'
    ]
    groups = {row[0] for row in rows if row[0]}
    groups.update(defaults)
    return sorted(groups)

def _consumable_convert_candidates():
    rows = db.session.query(
        Device.device_type,
        func.count(Device.id).label('total'),
        func.sum(
            case((Device.manager_id != None, 1), else_=0)
        ).label('assigned')
    ).filter(Device.status != CONVERTED_CONSUMABLE_STATUS)\
     .group_by(Device.device_type)\
     .order_by(func.lower(Device.device_type))\
     .all()
    candidates = []
    for row in rows:
        device_type = row[0]
        total = int(row[1] or 0)
        assigned = int(row[2] or 0)
        available = max(0, total - assigned)
        candidates.append({
            'device_type': device_type,
            'total': total,
            'assigned': assigned,
            'available': available,
            'suggested_code': _consumable_code_base_from_device_type(device_type),
            'existing_item': ConsumableItem.query.filter(func.lower(ConsumableItem.name) == device_type.lower()).first()
        })
    return candidates

@app.route('/consumables')
def consumable_list():
    if not _require_device_permission('devices.view'):
        flash('Bạn không có quyền xem thiết bị tiêu hao.', 'danger')
        return redirect(url_for('login') if 'user_id' not in session else url_for('home'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    tx_page = request.args.get('tx_page', 1, type=int)
    tx_per_page = request.args.get('tx_per_page', 20, type=int)
    q = (request.args.get('q') or '').strip()
    category = (request.args.get('category') or '').strip()
    low_stock = request.args.get('low_stock') == '1'

    query = ConsumableItem.query
    if q:
        query = query.filter(or_(ConsumableItem.code.ilike(f'%{q}%'), ConsumableItem.name.ilike(f'%{q}%')))
    if category:
        query = query.filter(ConsumableItem.category == category)
    if low_stock:
        query = query.filter(ConsumableItem.current_quantity <= ConsumableItem.min_quantity)

    items = query.order_by(func.lower(ConsumableItem.name)).paginate(page=page, per_page=per_page, error_out=False)
    transactions = ConsumableTransaction.query.order_by(ConsumableTransaction.transaction_date.desc())\
        .paginate(page=tx_page, per_page=tx_per_page, error_out=False)
    users = User.query.filter(User.status.notin_(['Nghỉ việc', 'Nghỉ không lương']))\
        .order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)).all()
    stats = {
        'total_items': ConsumableItem.query.count(),
        'total_quantity': db.session.query(func.coalesce(func.sum(ConsumableItem.current_quantity), 0)).scalar() or 0,
        'low_stock': ConsumableItem.query.filter(
            ConsumableItem.current_quantity > 0,
            ConsumableItem.current_quantity <= ConsumableItem.min_quantity
        ).count(),
        'out_stock': ConsumableItem.query.filter(ConsumableItem.current_quantity <= 0).count(),
        'issued_count': ConsumableTransaction.query.filter_by(transaction_type='Xuất').count(),
    }

    return render_template(
        'consumables.html',
        items=items,
        transactions=transactions,
        users=users,
        categories=_consumable_categories(),
        groups=_consumable_groups(),
        stats=stats,
        q=q,
        category=category,
        low_stock=low_stock,
        tx_page=tx_page,
        tx_per_page=tx_per_page,
        can_edit=_require_device_permission('devices.edit')
    )

@app.route('/consumables/convert', methods=['GET', 'POST'])
def convert_devices_to_consumables():
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền chuyển đổi thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))

    if request.method == 'POST':
        selected_types = [t.strip() for t in request.form.getlist('device_types') if t.strip()]
        if not selected_types:
            flash('Vui lòng chọn ít nhất một loại thiết bị cần chuyển đổi.', 'warning')
            return redirect(url_for('convert_devices_to_consumables'))

        converted_devices = 0
        converted_types = 0
        try:
            for device_type in selected_types:
                devices = Device.query.filter(
                    Device.device_type == device_type,
                    Device.status != CONVERTED_CONSUMABLE_STATUS
                ).order_by(Device.id).all()
                if not devices:
                    continue

                item = ConsumableItem.query.filter(func.lower(ConsumableItem.name) == device_type.lower()).first()
                if not item:
                    item = ConsumableItem(
                        code=_unique_consumable_code(_consumable_code_base_from_device_type(device_type)),
                        name=device_type,
                        category='Thiết bị tiêu hao',
                        unit='cái',
                        current_quantity=0,
                        min_quantity=0,
                        location='Kho IT',
                        notes=f'Tự tạo khi chuyển đổi từ danh mục thiết bị ngày {get_now().strftime("%d-%m-%Y")}.'
                    )
                    db.session.add(item)
                    db.session.flush()

                total_quantity = len(devices)
                _record_consumable_transaction(
                    item,
                    'Nhập',
                    total_quantity,
                    reason=f'Chuyển đổi từ danh mục thiết bị: {device_type}',
                    notes='Gom các thiết bị cũ thành tồn kho tiêu hao.'
                )

                issued_by_user = {}
                for device in devices:
                    if device.manager_id:
                        issued_by_user[device.manager_id] = issued_by_user.get(device.manager_id, 0) + 1

                for receiver_id, quantity in issued_by_user.items():
                    _record_consumable_transaction(
                        item,
                        'Xuất',
                        quantity,
                        issued_to_id=receiver_id,
                        reason='Chuyển đổi thiết bị đã bàn giao',
                        notes=f'Tự tạo từ {quantity} thiết bị cũ loại {device_type}.'
                    )

                stamp = get_now().strftime('%d-%m-%Y %H:%M')
                for device in devices:
                    old_status = device.status
                    device.status = CONVERTED_CONSUMABLE_STATUS
                    note_line = f'[{stamp}] Đã chuyển sang thiết bị tiêu hao: {item.code} - {item.name}. Trạng thái cũ: {old_status}.'
                    device.notes = f"{device.notes}\n{note_line}" if device.notes else note_line

                converted_devices += total_quantity
                converted_types += 1

            db.session.commit()
            flash(f'Đã chuyển đổi {converted_devices} thiết bị thuộc {converted_types} loại sang thiết bị tiêu hao.', 'success')
            return redirect(url_for('consumable_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Không thể chuyển đổi: {str(e)}', 'danger')
            return redirect(url_for('convert_devices_to_consumables'))

    candidates = _consumable_convert_candidates()
    likely_keywords = ['dây', 'cáp', 'usb', 'đầu chuyển', 'module', 'quang', 'adapter', 'sạc', 'chuột', 'bàn phím']
    for candidate in candidates:
        name = (candidate['device_type'] or '').lower()
        candidate['suggested'] = any(keyword in name for keyword in likely_keywords)

    return render_template('convert_consumables.html', candidates=candidates)

@app.route('/consumables/add', methods=['POST'])
def add_consumable():
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền thêm thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))

    name = (request.form.get('name') or '').strip()
    group_name = (request.form.get('group_name') or '').strip()
    category = _normalize_consumable_category(request.form.get('category'))
    code = (request.form.get('code') or '').strip().upper()
    if not code and name:
        code = _generate_consumable_code(category, name)
    unit = (request.form.get('unit') or '').strip() or 'cái'
    location = (request.form.get('location') or '').strip()
    notes = (request.form.get('notes') or '').strip()
    try:
        initial_quantity = max(0, int(request.form.get('initial_quantity') or 0))
        min_quantity = max(0, int(request.form.get('min_quantity') or 0))
    except ValueError:
        flash('Số lượng ban đầu và tồn tối thiểu phải là số nguyên.', 'danger')
        return redirect(url_for('consumable_list'))

    if not name:
        flash('Tên mặt hàng là bắt buộc.', 'danger')
    elif ConsumableItem.query.filter(func.upper(ConsumableItem.code) == code).first():
        flash('Mã thiết bị tiêu hao đã tồn tại.', 'warning')
    else:
        item = ConsumableItem(
            code=code,
            name=name,
            group_name=group_name,
            category=category,
            manufacturer=(request.form.get('manufacturer') or '').strip(),
            model=(request.form.get('model') or '').strip(),
            standard=(request.form.get('standard') or '').strip(),
            speed=(request.form.get('speed') or '').strip(),
            length=(request.form.get('length') or '').strip(),
            connector_a=(request.form.get('connector_a') or '').strip(),
            connector_b=(request.form.get('connector_b') or '').strip(),
            fiber_type=(request.form.get('fiber_type') or '').strip(),
            color=(request.form.get('color') or '').strip(),
            unit=unit,
            current_quantity=0,
            min_quantity=min_quantity,
            location=location,
            track_after_handover=request.form.get('track_after_handover') == '1',
            manager_id=int(request.form.get('manager_id')) if request.form.get('manager_id') else None,
            notes=notes
        )
        db.session.add(item)
        db.session.flush()
        try:
            image_filenames = _save_consumable_image_files(request.files.getlist('consumable_images'), item.id, limit=5)
            if image_filenames:
                item.image_filenames = _device_image_storage_value(image_filenames)
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
            return redirect(url_for('consumable_list'))
        if initial_quantity > 0:
            _record_consumable_transaction(item, 'Nhập', initial_quantity, reason='Tạo mặt hàng ban đầu')
        db.session.commit()
        flash('Đã thêm thiết bị tiêu hao.', 'success')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/<int:item_id>/edit', methods=['POST'])
def edit_consumable(item_id):
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền sửa thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))
    item = ConsumableItem.query.get_or_404(item_id)
    code = (request.form.get('code') or '').strip().upper()
    duplicate = ConsumableItem.query.filter(func.upper(ConsumableItem.code) == code, ConsumableItem.id != item.id).first()
    if not code or not (request.form.get('name') or '').strip():
        flash('Mã và tên mặt hàng là bắt buộc.', 'danger')
    elif duplicate:
        flash('Mã thiết bị tiêu hao đã tồn tại.', 'warning')
    else:
        item.code = code
        item.name = (request.form.get('name') or '').strip()
        item.group_name = (request.form.get('group_name') or '').strip()
        item.category = _normalize_consumable_category(request.form.get('category'))
        item.manufacturer = (request.form.get('manufacturer') or '').strip()
        item.model = (request.form.get('model') or '').strip()
        item.standard = (request.form.get('standard') or '').strip()
        item.speed = (request.form.get('speed') or '').strip()
        item.length = (request.form.get('length') or '').strip()
        item.connector_a = (request.form.get('connector_a') or '').strip()
        item.connector_b = (request.form.get('connector_b') or '').strip()
        item.fiber_type = (request.form.get('fiber_type') or '').strip()
        item.color = (request.form.get('color') or '').strip()
        item.unit = (request.form.get('unit') or '').strip() or 'cái'
        item.location = (request.form.get('location') or '').strip()
        item.track_after_handover = request.form.get('track_after_handover') == '1'
        item.manager_id = int(request.form.get('manager_id')) if request.form.get('manager_id') else None
        item.is_active = request.form.get('is_active') == '1'
        item.notes = (request.form.get('notes') or '').strip()
        new_images = request.files.getlist('consumable_images')
        has_new_images = any(file_storage and file_storage.filename for file_storage in new_images)
        try:
            if request.form.get('remove_consumable_images') == '1':
                _delete_consumable_image_files(item.image_filenames)
                item.image_filenames = None
            if has_new_images:
                image_filenames = _save_consumable_image_files(new_images, item.id, limit=5)
                if image_filenames:
                    _delete_consumable_image_files(item.image_filenames)
                    item.image_filenames = _device_image_storage_value(image_filenames)
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
            return redirect(url_for('consumable_list'))
        try:
            item.min_quantity = max(0, int(request.form.get('min_quantity') or 0))
        except ValueError:
            item.min_quantity = 0
        db.session.commit()
        flash('Đã cập nhật thiết bị tiêu hao.', 'success')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/<int:item_id>/delete', methods=['POST'])
def delete_consumable(item_id):
    if not _require_device_permission('devices.delete'):
        flash('Bạn không có quyền xóa thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))
    item = ConsumableItem.query.get_or_404(item_id)
    if item.transactions:
        flash('Không thể xóa mặt hàng đã có lịch sử nhập/xuất. Hãy giữ lại để bảo toàn nhật ký.', 'warning')
    else:
        _delete_consumable_image_files(item.image_filenames)
        db.session.delete(item)
        db.session.commit()
        flash('Đã xóa thiết bị tiêu hao.', 'success')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/<int:item_id>/stock_in', methods=['POST'])
def consumable_stock_in(item_id):
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền nhập kho thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))
    item = ConsumableItem.query.get_or_404(item_id)
    try:
        quantity = _parse_positive_int(request.form.get('quantity'))
        _record_consumable_transaction(
            item, 'Nhập', quantity,
            reason=(request.form.get('reason') or '').strip() or 'Nhập kho',
            notes=(request.form.get('notes') or '').strip()
        )
        db.session.commit()
        flash('Đã nhập kho thiết bị tiêu hao.', 'success')
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/<int:item_id>/issue', methods=['POST'])
def consumable_issue(item_id):
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền xuất thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))
    item = ConsumableItem.query.get_or_404(item_id)
    try:
        quantity = _parse_positive_int(request.form.get('quantity'))
        issued_to_id = int(request.form.get('issued_to_id') or 0)
        if not User.query.get(issued_to_id):
            raise ValueError('Vui lòng chọn người nhận.')
        _record_consumable_transaction(
            item, 'Xuất', quantity,
            issued_to_id=issued_to_id,
            reason=(request.form.get('reason') or '').strip() or 'Xuất sử dụng',
            notes=(request.form.get('notes') or '').strip()
        )
        db.session.commit()
        flash('Đã xuất thiết bị tiêu hao cho người dùng.', 'success')
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/<int:item_id>/adjust', methods=['POST'])
def consumable_adjust(item_id):
    if not _require_device_permission('devices.edit'):
        flash('Bạn không có quyền điều chỉnh tồn kho.', 'danger')
        return redirect(url_for('consumable_list'))
    item = ConsumableItem.query.get_or_404(item_id)
    try:
        new_quantity = int(request.form.get('new_quantity'))
        if new_quantity < 0:
            raise ValueError('Tồn kho mới không được âm.')
        _record_consumable_transaction(
            item, 'Điều chỉnh', new_quantity,
            reason=(request.form.get('reason') or '').strip() or 'Điều chỉnh tồn kho',
            notes=(request.form.get('notes') or '').strip()
        )
        db.session.commit()
        flash('Đã điều chỉnh tồn kho.', 'success')
    except (TypeError, ValueError) as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('consumable_list'))

@app.route('/consumables/export')
def export_consumables_excel():
    if not _require_device_permission('devices.view'):
        flash('Bạn không có quyền xuất dữ liệu thiết bị tiêu hao.', 'danger')
        return redirect(url_for('consumable_list'))
    items = ConsumableItem.query.order_by(ConsumableItem.name).all()
    transactions = ConsumableTransaction.query.order_by(ConsumableTransaction.transaction_date.desc()).all()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame([{
            'Mã': item.code,
            'Tên thiết bị tiêu hao': item.name,
            'Loại vật tư': item.category,
            'Đơn vị': item.unit,
            'Tồn kho': item.current_quantity,
            'Tồn tối thiểu': item.min_quantity,
            'Vị trí': item.location or '',
            'Ghi chú': item.notes or ''
        } for item in items]).to_excel(writer, index=False, sheet_name='Ton_kho')
        pd.DataFrame([{
            'Thời gian': _format_vietnam_datetime(tx.transaction_date),
            'Mã': tx.item.code if tx.item else '',
            'Tên thiết bị tiêu hao': tx.item.name if tx.item else '',
            'Loại giao dịch': tx.transaction_type,
            'Số lượng': tx.quantity,
            'Tồn trước': tx.before_quantity,
            'Tồn sau': tx.after_quantity,
            'Người nhận': tx.issued_to.full_name if tx.issued_to else '',
            'Người thao tác': tx.actor.full_name if tx.actor else '',
            'Lý do': tx.reason or '',
            'Ghi chú': tx.notes or ''
        } for tx in transactions]).to_excel(writer, index=False, sheet_name='Nhat_ky')
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'consumables_{datetime.now(VIETNAM_TZ).strftime("%Y%m%d")}.xlsx'
    )


# --- Stock accessories and supplies (separate from the consumable-device warehouse) ---
def _require_stock_permission(permission_code='stock_items.view'):
    if 'user_id' not in session:
        return False
    current_user = _get_current_user()
    current_permissions = _get_current_permissions()
    return bool((current_user and current_user.role == 'admin') or permission_code in current_permissions)

def _stock_category_payload(categories):
    return [
        {
            'id': category.id,
            'name': category.name,
            'code_prefix': category.code_prefix,
            'fields': _stock_category_fields(category),
        }
        for category in categories
    ]

def _stock_item_redirect():
    return redirect(request.referrer or url_for('stock_item_list'))

@app.route('/stock-items')
def stock_item_list():
    if not _require_stock_permission('stock_items.view'):
        flash('Bạn không có quyền xem vật tư và phụ kiện.', 'danger')
        return redirect(url_for('login') if 'user_id' not in session else url_for('home'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    movement_page = request.args.get('movement_page', 1, type=int)
    q = (request.args.get('q') or '').strip()
    category_id = request.args.get('category_id', type=int)
    low_stock = request.args.get('low_stock') == '1'

    categories = StockItemCategory.query.order_by(func.lower(StockItemCategory.name)).all()
    query = StockItem.query
    if q:
        query = query.filter(or_(StockItem.code.ilike(f'%{q}%'), StockItem.name.ilike(f'%{q}%'),
                                 StockItem.manufacturer.ilike(f'%{q}%'), StockItem.model.ilike(f'%{q}%')))
    if category_id:
        query = query.filter(StockItem.category_id == category_id)
    if low_stock:
        query = query.filter(StockItem.current_quantity <= StockItem.min_quantity)
    items = query.order_by(StockItem.is_active.desc(), func.lower(StockItem.name)).paginate(
        page=page, per_page=per_page if per_page in (10, 20, 50, 100) else 20, error_out=False)
    movement_stats = db.session.query(
        StockItemMovement.item_id,
        func.coalesce(func.sum(case((StockItemMovement.movement_type == 'Nhập', StockItemMovement.quantity), else_=0)), 0).label('total_imported'),
        func.coalesce(func.sum(case((StockItemMovement.movement_type == 'Xuất', StockItemMovement.quantity), else_=0)), 0).label('total_exported')
    ).group_by(StockItemMovement.item_id).all()
    stats_map = {item_id: (imp, exp) for item_id, imp, exp in movement_stats}

    for item in items.items:
        item.specification_values = _stock_item_specifications(item)
        item.images = _stock_image_list(item.image_filenames)
        imp, exp = stats_map.get(item.id, (0, 0))
        item.total_imported = imp
        item.total_exported = exp

    movements = StockItemMovement.query.order_by(
        StockItemMovement.movement_date.desc(), StockItemMovement.id.desc()
    ).paginate(page=movement_page, per_page=15, error_out=False)
    users = User.query.filter(User.status.notin_(['Nghỉ việc', 'Nghỉ không lương'])).order_by(
        func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username)
    ).all()
    stats = {
        'total_items': StockItem.query.filter_by(is_active=True).count(),
        'total_quantity': db.session.query(func.coalesce(func.sum(StockItem.current_quantity), 0)).scalar() or 0,
        'low_stock': StockItem.query.filter(
            StockItem.is_active.is_(True),
            StockItem.current_quantity > 0,
            StockItem.current_quantity <= StockItem.min_quantity
        ).count(),
        'out_stock': StockItem.query.filter(
            StockItem.is_active.is_(True),
            StockItem.current_quantity <= 0
        ).count(),
        'issued_this_month': StockItemMovement.query.filter(
            StockItemMovement.movement_type == 'Xuất',
            StockItemMovement.movement_date >= date.today().replace(day=1),
        ).count(),
    }
    return render_template(
        'stock_items.html',
        items=items,
        movements=movements,
        users=users,
        categories=categories,
        category_payload=_stock_category_payload(categories),
        stats=stats,
        q=q,
        category_id=category_id,
        low_stock=low_stock,
        can_edit=_require_stock_permission('stock_items.edit'),
        can_delete=_require_stock_permission('stock_items.delete'),
    )

@app.route('/stock-items/add', methods=['POST'])
def add_stock_item():
    if not _require_stock_permission('stock_items.edit'):
        flash('Bạn không có quyền thêm mặt hàng kho.', 'danger')
        return _stock_item_redirect()
    category = StockItemCategory.query.get(request.form.get('category_id', type=int))
    name = (request.form.get('name') or '').strip()
    code = (request.form.get('code') or '').strip().upper()
    try:
        initial_quantity = max(0, int(request.form.get('initial_quantity') or 0))
        min_quantity = max(0, int(request.form.get('min_quantity') or 0))
        track_units = request.form.get('track_units') == '1'
        if not category or not name:
            raise ValueError('Vui lòng chọn nhóm vật tư và nhập tên mặt hàng.')
        if not code:
            code = _generate_stock_item_code(category)
        if StockItem.query.filter(func.upper(StockItem.code) == code).first():
            raise ValueError('Mã mặt hàng đã tồn tại.')

        uploaded_images = []
        for file_obj in request.files.getlist('image_files'):
            saved_name = _save_stock_image_file(file_obj, 'item')
            if saved_name:
                uploaded_images.append(saved_name)

        item = StockItem(
            category=category,
            code=code,
            name=name,
            manufacturer=(request.form.get('manufacturer') or '').strip(),
            model=(request.form.get('model') or '').strip(),
            unit=(request.form.get('unit') or '').strip() or 'cái',
            current_quantity=0,
            min_quantity=min_quantity,
            location=(request.form.get('location') or '').strip(),
            track_units=track_units,
            image_filenames=json.dumps(uploaded_images, ensure_ascii=False) if uploaded_images else None,
            specifications=json.dumps(_parse_stock_specifications(request.form.get('specifications_json'), category), ensure_ascii=False),
            notes=(request.form.get('notes') or '').strip(),
        )
        db.session.add(item)
        db.session.flush()
        if initial_quantity:
            unit_serials = request.form.getlist('initial_unit_serials')
            _record_stock_item_movement(
                item, 'Nhập', initial_quantity,
                movement_date=date.today(),
                reason='Tạo mặt hàng và nhập tồn ban đầu',
                unit_serials=unit_serials
            )
        db.session.commit()
        flash('Đã thêm mặt hàng kho.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    return _stock_item_redirect()

@app.route('/stock-items/<int:item_id>/edit', methods=['POST'])
def edit_stock_item(item_id):
    if not _require_stock_permission('stock_items.edit'):
        flash('Bạn không có quyền sửa mặt hàng kho.', 'danger')
        return _stock_item_redirect()
    item = StockItem.query.get_or_404(item_id)
    category = StockItemCategory.query.get(request.form.get('category_id', type=int))
    name = (request.form.get('name') or '').strip()
    code = (request.form.get('code') or '').strip().upper()
    try:
        if not category or not name or not code:
            raise ValueError('Nhóm vật tư, mã và tên mặt hàng là bắt buộc.')
        duplicate = StockItem.query.filter(StockItem.id != item.id, func.upper(StockItem.code) == code).first()
        if duplicate:
            raise ValueError('Mã mặt hàng đã tồn tại.')

        existing_images = _stock_image_list(item.image_filenames)
        new_uploaded = []
        for file_obj in request.files.getlist('image_files'):
            saved_name = _save_stock_image_file(file_obj, 'item')
            if saved_name:
                new_uploaded.append(saved_name)
        if new_uploaded:
            existing_images.extend(new_uploaded)
            item.image_filenames = json.dumps(existing_images, ensure_ascii=False)

        item.category = category
        item.code = code
        item.name = name
        item.manufacturer = (request.form.get('manufacturer') or '').strip()
        item.model = (request.form.get('model') or '').strip()
        item.unit = (request.form.get('unit') or '').strip() or 'cái'
        item.min_quantity = max(0, int(request.form.get('min_quantity') or 0))
        item.location = (request.form.get('location') or '').strip()
        item.is_active = request.form.get('is_active') == '1'
        item.track_units = request.form.get('track_units') == '1'
        item.specifications = json.dumps(_parse_stock_specifications(request.form.get('specifications_json'), category), ensure_ascii=False)
        item.notes = (request.form.get('notes') or '').strip()
        db.session.commit()
        flash('Đã cập nhật mặt hàng kho.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    return _stock_item_redirect()

@app.route('/stock-items/<int:item_id>/movement', methods=['POST'])
def record_stock_item_movement(item_id):
    if not _require_stock_permission('stock_items.edit'):
        flash('Bạn không có quyền nhập/xuất kho.', 'danger')
        return _stock_item_redirect()
    item = StockItem.query.get_or_404(item_id)
    movement_type = (request.form.get('movement_type') or '').strip()
    try:
        quantity = int(request.form.get('quantity') or 0)
        selected_unit_ids = [int(x) for x in request.form.getlist('selected_unit_ids') if str(x).isdigit()]
        unit_serials = request.form.getlist('unit_serials')

        if movement_type == 'Xuất' and item.track_units:
            if not selected_unit_ids:
                raise ValueError('Vui lòng chọn ít nhất 1 thiết bị cụ thể để xuất kho.')
            quantity = len(selected_unit_ids)

        if movement_type in ('Nhập', 'Xuất') and quantity <= 0:
            raise ValueError('Số lượng phải lớn hơn 0.')
        if movement_type == 'Điều chỉnh' and quantity < 0:
            raise ValueError('Tồn kho điều chỉnh không được âm.')
        receiver_id = request.form.get('receiver_id', type=int)
        if movement_type == 'Xuất' and not User.query.get(receiver_id):
            raise ValueError('Vui lòng chọn người nhận khi xuất kho.')
        movement_date_value = request.form.get('movement_date') or ''
        movement_date = datetime.strptime(movement_date_value, '%Y-%m-%d').date() if movement_date_value else date.today()
        _record_stock_item_movement(
            item, movement_type, quantity,
            movement_date=movement_date,
            receiver_id=receiver_id if movement_type == 'Xuất' else None,
            supplier=(request.form.get('supplier') or '').strip() if movement_type == 'Nhập' else '',
            reference_code=(request.form.get('reference_code') or '').strip(),
            reason=(request.form.get('reason') or '').strip() or f'{movement_type} kho',
            notes=(request.form.get('notes') or '').strip(),
            unit_serials=unit_serials,
            selected_unit_ids=selected_unit_ids,
        )
        db.session.commit()
        flash(f'Đã ghi nhận {movement_type.lower()} kho.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    return _stock_item_redirect()

@app.route('/config/company', methods=['GET', 'POST'])
def company_config_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    current_user = _get_current_user()
    db_role = str(getattr(current_user, 'role', '') or '').lower()
    db_username = str(getattr(current_user, 'username', '') or '').lower()
    
    is_admin = (
        user_role in ('admin', 'quản trị viên', 'administrator') or
        db_role in ('admin', 'quản trị viên', 'administrator') or
        db_username == 'admin' or
        getattr(current_user, 'is_admin', False) or
        (current_user_id == 1)
    )

    cfg = get_company_config()
    if request.method == 'POST':
        if not is_admin:
            flash('Chỉ có tài khoản Quản trị viên (Admin) mới có quyền chỉnh sửa thông tin công ty.', 'danger')
            return redirect(url_for('company_config_page'))

        cfg['company_name'] = (request.form.get('company_name') or '').strip() or company_config_defaults['company_name']
        cfg['branch_name'] = (request.form.get('branch_name') or '').strip() or company_config_defaults['branch_name']
        cfg['company_address'] = (request.form.get('company_address') or '').strip()
        cfg['company_tax_id'] = (request.form.get('company_tax_id') or '').strip()
        cfg['company_email'] = (request.form.get('company_email') or '').strip()
        cfg['company_phone'] = (request.form.get('company_phone') or '').strip()
        cfg['company_bank_account'] = (request.form.get('company_bank_account') or '').strip()

        try:
            with open(_company_cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            flash('Đã cập nhật thông tin công ty / đơn vị thành công.', 'success')
        except Exception as e:
            flash(f'Lỗi khi lưu cấu hình: {str(e)}', 'danger')
        return redirect(url_for('company_config_page'))

    return render_template('company_config.html', cfg=cfg, is_admin=is_admin)

@app.route('/stock-items/<int:item_id>/units')
def stock_item_units_api(item_id):
    if not _require_stock_permission('stock_items.view'):
        return jsonify({'error': 'Unauthorized'}), 401
    item = StockItem.query.get_or_404(item_id)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    q = (request.args.get('q') or '').strip()
    status_filter = (request.args.get('status') or '').strip()

    query = StockItemUnit.query.filter_by(item_id=item_id)
    if q:
        query = query.outerjoin(User, StockItemUnit.assigned_to_id == User.id).filter(
            or_(
                StockItemUnit.unit_code.ilike(f'%{q}%'),
                StockItemUnit.serial_number.ilike(f'%{q}%'),
                StockItemUnit.location.ilike(f'%{q}%'),
                User.full_name.ilike(f'%{q}%'),
                User.username.ilike(f'%{q}%')
            )
        )
    if status_filter:
        query = query.filter(StockItemUnit.status == status_filter)

    pagination = query.order_by(StockItemUnit.id.asc()).paginate(page=page, per_page=per_page if per_page in (5, 10, 20, 50, 100) else 10, error_out=False)

    result = []
    for u in pagination.items:
        result.append({
            'id': u.id,
            'unit_code': u.unit_code,
            'serial_number': u.serial_number or '',
            'status': u.status,
            'assigned_to': u.assigned_to.full_name if u.assigned_to else '',
            'assigned_to_id': u.assigned_to_id,
            'location': u.location or '',
            'notes': u.notes or '',
            'created_at': u.created_at.strftime('%d-%m-%Y %H:%M') if u.created_at else ''
        })
    return jsonify({
        'item_code': item.code,
        'item_name': item.name,
        'track_units': item.track_units,
        'units': result,
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages or 1,
        'per_page': pagination.per_page,
        'has_prev': pagination.has_prev,
        'has_next': pagination.has_next
    })

@app.route('/stock-items/units/<int:unit_id>/edit', methods=['POST'])
def edit_stock_item_unit(unit_id):
    if not _require_stock_permission('stock_items.edit'):
        return jsonify({'success': False, 'error': 'Bạn không có quyền sửa thông tin thiết bị.'}), 403
    unit = StockItemUnit.query.get_or_404(unit_id)
    unit_code = (request.form.get('unit_code') or '').strip().upper()
    serial_number = (request.form.get('serial_number') or '').strip()
    status = (request.form.get('status') or '').strip()
    location = (request.form.get('location') or '').strip()
    notes = (request.form.get('notes') or '').strip()

    try:
        if not unit_code:
            raise ValueError('Mã QLTB không được để trống.')
        duplicate = StockItemUnit.query.filter(StockItemUnit.id != unit.id, func.upper(StockItemUnit.unit_code) == unit_code).first()
        if duplicate:
            raise ValueError(f'Mã QLTB {unit_code} đã tồn tại trên một thiết bị khác.')

        unit.unit_code = unit_code
        unit.serial_number = serial_number or None
        if status in ['Trong kho', 'Đã xuất', 'Hỏng', 'Thanh lý']:
            unit.status = status
        unit.location = location or None
        unit.notes = notes or None
        unit.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Đã cập nhật thông tin thiết bị.'})
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400

@app.route('/stock-items/movements/<int:movement_id>/handover')
def stock_item_movement_handover(movement_id):
    if not _require_stock_permission('stock_items.view'):
        flash('Bạn không có quyền xem phiếu kho.', 'danger')
        return _stock_item_redirect()
    movement = StockItemMovement.query.get_or_404(movement_id)
    
    unit_movements = StockItemUnitMovement.query.filter_by(movement_id=movement.id).all()
    units = [um.unit for um in unit_movements]
    company_cfg = get_company_config()
    
    return render_template(
        'stock_movement_handover.html',
        movement=movement,
        units=units,
        company_cfg=company_cfg,
        now=get_now()
    )

@app.route('/stock-items/<int:item_id>/delete', methods=['POST'])
def delete_stock_item(item_id):
    if not _require_stock_permission('stock_items.delete'):
        flash('Bạn không có quyền xóa mặt hàng kho.', 'danger')
        return _stock_item_redirect()
    item = StockItem.query.get_or_404(item_id)
    if item.movements:
        flash('Không thể xóa mặt hàng đã có phiếu nhập/xuất. Hãy ngừng sử dụng mặt hàng này.', 'warning')
        return _stock_item_redirect()
    db.session.delete(item)
    db.session.commit()
    flash('Đã xóa mặt hàng kho.', 'success')
    return _stock_item_redirect()

@app.route('/stock-items/categories', methods=['GET', 'POST'])
def stock_item_categories():
    if not _require_stock_permission('stock_items.edit'):
        flash('Bạn không có quyền quản lý nhóm vật tư.', 'danger')
        return redirect(url_for('stock_item_list'))
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        prefix = _normalize_stock_prefix(request.form.get('code_prefix'))
        fields = [field.strip() for field in (request.form.get('specification_fields') or '').splitlines() if field.strip()]
        image_file = request.files.get('image_file')
        saved_img = _save_stock_image_file(image_file, 'cat') if image_file else None

        if not name:
            flash('Tên nhóm vật tư là bắt buộc.', 'danger')
        elif StockItemCategory.query.filter(func.lower(StockItemCategory.name) == name.lower()).first():
            flash('Nhóm vật tư đã tồn tại.', 'warning')
        else:
            db.session.add(StockItemCategory(
                name=name,
                code_prefix=prefix,
                specification_fields=json.dumps(fields[:20], ensure_ascii=False),
                description=(request.form.get('description') or '').strip(),
                image_filename=saved_img,
            ))
            db.session.commit()
            flash('Đã tạo nhóm vật tư.', 'success')
        return redirect(url_for('stock_item_categories'))
    categories = StockItemCategory.query.order_by(func.lower(StockItemCategory.name)).all()
    for category in categories:
        category.spec_fields = _stock_category_fields(category)
    return render_template('stock_item_categories.html', categories=categories, can_delete=_require_stock_permission('stock_items.delete'))

@app.route('/stock-items/categories/<int:category_id>/edit', methods=['POST'])
def edit_stock_item_category(category_id):
    if not _require_stock_permission('stock_items.edit'):
        flash('Bạn không có quyền sửa nhóm vật tư.', 'danger')
        return redirect(url_for('stock_item_categories'))
    category = StockItemCategory.query.get_or_404(category_id)
    name = (request.form.get('name') or '').strip()
    image_file = request.files.get('image_file')
    try:
        if not name:
            raise ValueError('Tên nhóm vật tư là bắt buộc.')
        duplicate = StockItemCategory.query.filter(StockItemCategory.id != category.id, func.lower(StockItemCategory.name) == name.lower()).first()
        if duplicate:
            raise ValueError('Nhóm vật tư đã tồn tại.')

        if image_file and image_file.filename:
            saved_img = _save_stock_image_file(image_file, 'cat')
            if saved_img:
                category.image_filename = saved_img

        category.name = name
        category.code_prefix = _normalize_stock_prefix(request.form.get('code_prefix'))
        category.specification_fields = json.dumps([
            field.strip() for field in (request.form.get('specification_fields') or '').splitlines() if field.strip()
        ][:20], ensure_ascii=False)
        category.description = (request.form.get('description') or '').strip()
        db.session.commit()
        flash('Đã cập nhật nhóm vật tư.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    return redirect(url_for('stock_item_categories'))

@app.route('/stock-items/categories/<int:category_id>/delete', methods=['POST'])
def delete_stock_item_category(category_id):
    if not _require_stock_permission('stock_items.delete'):
        flash('Bạn không có quyền xóa nhóm vật tư.', 'danger')
        return redirect(url_for('stock_item_categories'))
    category = StockItemCategory.query.get_or_404(category_id)
    if category.items:
        flash('Không thể xóa nhóm đang có mặt hàng.', 'warning')
    else:
        db.session.delete(category)
        db.session.commit()
        flash('Đã xóa nhóm vật tư.', 'success')
    return redirect(url_for('stock_item_categories'))

@app.route('/resources/delete/<int:id>', methods=['POST'])
def delete_resource(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    perms = _get_current_permissions()
    if 'resources.delete' not in perms and session.get('role') != 'admin':
        flash('Bạn không có quyền xóa tài nguyên.', 'danger')
        return redirect(url_for('resources'))
    
    resource = Resource.query.get_or_404(id)
    db.session.delete(resource)
    db.session.commit()
    flash('Đã xóa tài nguyên.', 'success')
    return redirect(url_for('resources'))


# --- Device Type Management Routes ---
@app.route('/device_types')
def device_type_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Only admin or resource managers should access (using devices.view or similar)
    if (session.get('role') != 'admin') and ('devices.view' not in _get_current_permissions()) and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền truy cập.', 'danger')
        return redirect(url_for('home'))
        
    types = DeviceType.query.order_by(DeviceType.category, DeviceType.name).all()
    
    # Group by category
    grouped_types = {category: [] for category in DEVICE_TYPE_CATEGORIES}
    for t in types:
        category = (t.category or '').strip() or 'Khác'
        grouped_types.setdefault(category, []).append(t)
    grouped_types = {category: rows for category, rows in grouped_types.items() if rows}
        
    return render_template('device_types/list.html', grouped_types=grouped_types)

@app.route('/device_types/add', methods=['GET', 'POST'])
def add_device_type():
    if 'user_id' not in session: return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền thêm loại thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))
        
    category_choices = _get_device_type_category_choices()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        code_prefix = _normalize_device_type_prefix(request.form.get('code_prefix', ''))
        description = request.form.get('description', '').strip()
        
        if not name or not category:
            flash('Tên và nhóm thiết bị là bắt buộc.', 'danger')
        elif category not in category_choices:
            flash('Nhóm thiết bị không hợp lệ.', 'danger')
        elif not code_prefix:
            flash('Mã loại thiết bị là bắt buộc.', 'danger')
        elif not _is_valid_device_type_prefix(code_prefix):
            flash('Mã loại chỉ được gồm chữ cái A-Z và số, tối đa 20 ký tự.', 'danger')
        elif DeviceType.query.filter_by(name=name).first():
            flash('Loại thiết bị đã tồn tại.', 'warning')
        elif DeviceType.query.filter(func.upper(DeviceType.code_prefix) == code_prefix).first():
            flash('Mã loại thiết bị đã tồn tại.', 'warning')
        else:
            try:
                dt = DeviceType(name=name, category=category, code_prefix=code_prefix, description=description)
                db.session.add(dt)
                db.session.commit()
                flash('Đã thêm loại thiết bị mới.', 'success')
                return redirect(url_for('device_type_list'))
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi: {str(e)}', 'danger')
                
    return render_template(
        'device_types/form.html',
        device_type=None,
        device_type_categories=category_choices,
    )

@app.route('/device_types/<int:id>/edit', methods=['GET', 'POST'])
def edit_device_type(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền sửa loại thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))
        
    dt = DeviceType.query.get_or_404(id)
    
    category_choices = _get_device_type_category_choices()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        code_prefix = _normalize_device_type_prefix(request.form.get('code_prefix', ''))
        description = request.form.get('description', '').strip()
        
        if not name or not category:
            flash('Tên và nhóm thiết bị là bắt buộc.', 'danger')
        elif category not in category_choices:
            flash('Nhóm thiết bị không hợp lệ.', 'danger')
        elif not code_prefix:
            flash('Mã loại thiết bị là bắt buộc.', 'danger')
        elif not _is_valid_device_type_prefix(code_prefix):
            flash('Mã loại chỉ được gồm chữ cái A-Z và số, tối đa 20 ký tự.', 'danger')
        elif name != dt.name and DeviceType.query.filter_by(name=name).first():
            flash('Tên loại thiết bị đã tồn tại.', 'warning')
        elif DeviceType.query.filter(DeviceType.id != dt.id, func.upper(DeviceType.code_prefix) == code_prefix).first():
            flash('Mã loại thiết bị đã tồn tại.', 'warning')
        else:
            try:
                old_name = dt.name
                if old_name != name:
                    Device.query.filter_by(device_type=old_name).update(
                        {'device_type': name},
                        synchronize_session=False,
                    )
                dt.name = name
                dt.category = category
                dt.code_prefix = code_prefix
                dt.description = description
                db.session.commit()
                flash('Đã cập nhật loại thiết bị.', 'success')
                return redirect(url_for('device_type_list'))
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi: {str(e)}', 'danger')
                
    return render_template(
        'device_types/form.html',
        device_type=dt,
        device_type_categories=category_choices,
    )

@app.route('/device_types/categories/rename', methods=['POST'])
def rename_device_type_category():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền sửa nhóm thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))

    old_name = request.form.get('old_name', '').strip()
    new_name = request.form.get('new_name', '').strip()
    if not old_name or not new_name:
        flash('Tên nhóm thiết bị không được để trống.', 'danger')
        return redirect(url_for('device_type_list'))
    if len(new_name) > 100:
        flash('Tên nhóm thiết bị không được vượt quá 100 ký tự.', 'danger')
        return redirect(url_for('device_type_list'))

    categories = _get_device_type_category_choices()
    if old_name not in categories:
        flash('Nhóm thiết bị cần sửa không còn tồn tại.', 'warning')
        return redirect(url_for('device_type_list'))
    duplicate = next(
        (category for category in categories if category != old_name and category.casefold() == new_name.casefold()),
        None,
    )
    if duplicate:
        flash(f'Nhóm thiết bị "{duplicate}" đã tồn tại.', 'warning')
        return redirect(url_for('device_type_list'))
    if old_name == new_name:
        flash('Tên nhóm thiết bị không thay đổi.', 'info')
        return redirect(url_for('device_type_list'))

    try:
        updated = DeviceType.query.filter_by(category=old_name).update(
            {'category': new_name},
            synchronize_session=False,
        )
        db.session.commit()
        flash(f'Đã đổi tên nhóm "{old_name}" thành "{new_name}" cho {updated} loại thiết bị.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Không thể đổi tên nhóm thiết bị: {exc}', 'danger')
    return redirect(url_for('device_type_list'))

@app.route('/device_types/<int:id>/delete', methods=['POST'])
def delete_device_type(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.delete' not in _get_current_permissions()):
        flash('Bạn không có quyền xóa loại thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))
        
    dt = DeviceType.query.get_or_404(id)
    
    # Check if used
    if Device.query.filter_by(device_type=dt.name).first():
        flash(f'Không thể xóa loại "{dt.name}" vì đang có thiết bị sử dụng loại này.', 'warning')
        return redirect(url_for('device_type_list'))
        
    try:
        db.session.delete(dt)
        db.session.commit()
        flash('Đã xóa loại thiết bị.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi: {str(e)}', 'danger')
        
    return redirect(url_for('device_type_list'))

# ==============================================================================
# HIKVISION TIMEKEEPING & ATTENDANCE SYSTEM
# ==============================================================================

_hikvision_cfg_path = os.path.join(instance_path, 'hikvision_config.json')
hikvision_config_defaults = {
    'host': '192.168.111.94',
    'port': '8000',
    'username': 'admin',
    'password': '',
    'auto_sync': False,
    'sync_interval': 15
}

def get_hikvision_config():
    cfg = dict(hikvision_config_defaults)
    try:
        if os.path.exists(_hikvision_cfg_path):
            with open(_hikvision_cfg_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update({k: v for k, v in saved.items() if v is not None})
    except Exception:
        pass
    
    host_val = str(cfg.get('host') or '').strip()
    if host_val == '192.168.11.94':
        cfg['host'] = '192.168.111.94'
        try:
            with open(_hikvision_cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return cfg

def save_hikvision_config(data):
    cfg = get_hikvision_config()
    cfg.update(data)
    with open(_hikvision_cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

_cached_hikvision_base_url = None
_hikvision_session = None

def get_hikvision_session():
    global _hikvision_session
    if _hikvision_session is None:
        import requests
        _hikvision_session = requests.Session()
    return _hikvision_session

def _hikvision_request(endpoint, method='GET', payload=None, timeout=3, content_type='application/json'):
    global _cached_hikvision_base_url
    cfg = get_hikvision_config()
    host = (cfg.get('host') or '192.168.111.94').strip()
    port = str(cfg.get('port') or '8000').strip()
    username = (cfg.get('username') or 'admin').strip()
    password = cfg.get('password') or ''

    base_urls = []
    if _cached_hikvision_base_url:
        base_urls.append(_cached_hikvision_base_url)

    u1 = f"http://{host}:80"
    u2 = f"http://{host}:{port}"
    u3 = f"https://{host}:443"
    u4 = f"https://{host}:{port}"
    for u in [u1, u2, u3, u4]:
        if u not in base_urls: base_urls.append(u)

    errors_log = []

    try:
        import requests
        from requests.auth import HTTPDigestAuth, HTTPBasicAuth
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = get_hikvision_session()
        headers = {'Content-Type': content_type, 'Connection': 'keep-alive'}

        for base_url in base_urls:
            url = f"{base_url}{endpoint}"
            for auth_class in [HTTPDigestAuth, HTTPBasicAuth]:
                try:
                    auth = auth_class(username, password)
                    
                    if method.upper() == 'POST':
                        warmup_url = f"{base_url}/ISAPI/System/deviceInfo"
                        try:
                            session.get(warmup_url, auth=auth, timeout=1.5, verify=False)
                        except Exception:
                            pass

                        if isinstance(payload, str):
                            data_arg = payload.encode('utf-8')
                            resp = session.post(url, data=data_arg, auth=auth, headers=headers, timeout=timeout, verify=False)
                        else:
                            resp = session.post(url, json=payload, auth=auth, headers=headers, timeout=timeout, verify=False)
                    else:
                        resp = session.get(url, auth=auth, headers=headers, timeout=timeout, verify=False)
                    
                    if resp.status_code == 200:
                        _cached_hikvision_base_url = base_url
                        try:
                            return True, resp.json()
                        except Exception:
                            return True, resp.text
                    elif resp.status_code in (401, 403):
                        errors_log.append(f"[{url}] Sai tài khoản/mật khẩu (Mã {resp.status_code})")
                    else:
                        errors_log.append(f"[{url}] Lỗi HTTP {resp.status_code}: {resp.text[:120]}")
                except requests.exceptions.ConnectTimeout:
                    errors_log.append(f"[{url}] Hết thời gian kết nối ({timeout}s)")
                except Exception as exc:
                    errors_log.append(f"[{url}] Lỗi: {str(exc)[:100]}")

        return False, "Không thể kết nối Hikvision. Chi tiết thử nghiệm: " + " | ".join(errors_log[:3])
    except ImportError:
        pass

    import urllib.request
    import urllib.error
    import ssl

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    for base_url in base_urls:
        url = f"{base_url}{endpoint}"
        try:
            passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            passman.add_password(None, url, username, password)
            digest_handler = urllib.request.HTTPDigestAuthHandler(passman)
            basic_handler = urllib.request.HTTPBasicAuthHandler(passman)
            https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
            opener = urllib.request.build_opener(digest_handler, basic_handler, https_handler)

            if isinstance(payload, str):
                data_bytes = payload.encode('utf-8')
            elif payload is not None:
                data_bytes = json.dumps(payload).encode('utf-8')
            else:
                data_bytes = None

            req = urllib.request.Request(url, data=data_bytes, headers={'Content-Type': content_type, 'Connection': 'close'}, method=method.upper())

            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', errors='ignore')
                _cached_hikvision_base_url = base_url
                try:
                    return True, json.loads(body)
                except Exception:
                    return True, body
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                errors_log.append(f"[{url}] Sai tài khoản/mật khẩu (Mã {e.code})")
            else:
                errors_log.append(f"[{url}] Lỗi HTTP {e.code}")
        except Exception as e:
            errors_log.append(f"[{url}] Lỗi: {str(e)[:100]}")

    return False, "Không thể kết nối Hikvision. Chi tiết: " + " | ".join(errors_log[:3])

def _hikvision_test_connection():
    ok, data = _hikvision_request('/ISAPI/System/deviceInfo', method='GET', timeout=3)
    if ok:
        dev_name = 'Hikvision Terminal'
        if isinstance(data, dict):
            dev_name = data.get('DeviceInfo', {}).get('deviceName') or dev_name
        return True, f"Kết nối thành công tới thiết bị Hikvision ({dev_name})."
    else:
        return False, data

def _parse_hikvision_events_response(resp_data):
    events = []
    if isinstance(resp_data, dict):
        search_obj = resp_data.get('AcsEvent', {}) or resp_data.get('AcsEventSearch', {}) or resp_data.get('AcsEventCond', {})
        evts = search_obj.get('InfoList', [])
        if isinstance(evts, dict): evts = [evts]
        return evts

    if isinstance(resp_data, str) and ('<Info>' in resp_data or '<AcsEvent' in resp_data):
        try:
            xml_clean = re.sub(r'xmlns="[^"]+"', '', resp_data)
            root = ET.fromstring(xml_clean)
            for info in root.findall('.//Info'):
                evt = {}
                for child in info:
                    evt[child.tag] = child.text
                events.append(evt)
            if not events:
                for info in root.findall('.//AcsEvent'):
                    evt = {}
                    for child in info:
                        evt[child.tag] = child.text
                    events.append(evt)
        except Exception as exc:
            print(f"XML parse error: {exc}")
    return events

def _hikvision_fetch_users():
    try:
        pos = 0
        max_res = 10
        all_users = []
        seen_emp_nos = set()
        
        while pos < 1000:
            payload = {
                "UserInfoSearchCond": {
                    "searchID": "1",
                    "searchResultPosition": pos,
                    "maxResults": max_res
                }
            }
            ok, data = _hikvision_request('/ISAPI/AccessControl/UserInfo/Search?format=json', method='POST', payload=payload, timeout=3)
            if not ok:
                if pos == 0:
                    return False, f"Lỗi lấy danh sách người dùng từ thiết bị: {data}"
                break
            
            page_users = []
            if isinstance(data, dict):
                search_obj = data.get('UserInfoSearch', {})
                page_users = search_obj.get('UserInfo', [])
                if isinstance(page_users, dict):
                    page_users = [page_users]

            if not page_users:
                break

            new_added = 0
            for u in page_users:
                emp_no = str(u.get('employeeNo') or '').strip()
                if emp_no and emp_no not in seen_emp_nos:
                    seen_emp_nos.add(emp_no)
                    all_users.append(u)
                    new_added += 1
            
            pos += len(page_users)
            if len(page_users) < max_res or new_added == 0:
                break

        added = 0
        updated = 0
        fetched_emp_nos = set()

        for u in all_users:
            emp_no = str(u.get('employeeNo') or '').strip()
            name = str(u.get('name') or f"User {emp_no}").strip()
            card_no = None
            card_val = u.get('Valid', {}).get('cardNo') or u.get('cardNo')
            if card_val: card_no = str(card_val).strip()
            
            if not emp_no: continue
            fetched_emp_nos.add(emp_no)
            
            existing = AttendanceUser.query.filter_by(employee_no=emp_no).first()
            if existing:
                existing.name = name or existing.name
                if card_no: existing.card_no = card_no
                existing.is_active = True
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                db.session.add(AttendanceUser(
                    employee_no=emp_no,
                    name=name,
                    user_type='Nhân viên',
                    card_no=card_no,
                    is_active=True
                ))
                added += 1

        if fetched_emp_nos:
            missing_users = AttendanceUser.query.filter(AttendanceUser.employee_no.notin_(fetched_emp_nos)).all()
            for mu in missing_users:
                mu.is_active = False
        
        db.session.commit()
        return True, f"Đã đồng bộ danh sách user (Hoạt động: {len(fetched_emp_nos)}, Thêm mới: {added}, Cập nhật: {updated})."
    except Exception as exc:
        db.session.rollback()
        return False, f"Lỗi xử lý danh sách user: {exc}"

def _hikvision_sync_events(start_date=None, end_date=None, days=7):
    try:
        today_date = date.today()
        if days == 1:
            start_date = today_date
            end_date = today_date
        else:
            if not start_date:
                days_int = int(days) if (days and str(days).isdigit()) else 7
                start_date = today_date - timedelta(days=(days_int - 1) if days_int > 0 else 6)
            if not end_date:
                end_date = today_date

        start_str_plain = start_date.strftime('%Y-%m-%dT00:00:00')
        end_str_plain = end_date.strftime('%Y-%m-%dT23:59:59')
        start_str_tz = f"{start_str_plain}+07:00"
        end_str_tz = f"{end_str_plain}+07:00"

        time_pairs = [
            (start_str_plain, end_str_plain),
            (start_str_tz, end_str_tz)
        ]

        all_events = []
        last_error = None
        users_map = {u.employee_no: u for u in AttendanceUser.query.all()}
        new_count = 0

        search_pos = 0
        max_pages = 200

        for page_idx in range(max_pages):
            page_events = []

            # Try plain timestamps first, then tz timestamps
            for st_val, et_val in time_pairs:
                for major_val in [5, 0]:
                    xml_payload = f'<?xml version="1.0" encoding="utf-8"?><AcsEventCond version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><searchID>1</searchID><searchResultPosition>{search_pos}</searchResultPosition><maxResults>30</maxResults><major>{major_val}</major><minor>0</minor><startTime>{st_val}</startTime><endTime>{et_val}</endTime></AcsEventCond>'
                    ok_xml, data_xml = _hikvision_request('/ISAPI/AccessControl/AcsEvent', method='POST', payload=xml_payload, content_type='application/xml', timeout=3)
                    if ok_xml:
                        page_events = _parse_hikvision_events_response(data_xml)
                        if page_events: break
                    else:
                        last_error = data_xml
                if page_events: break

            if not page_events:
                for st_val, et_val in time_pairs:
                    json_payload = {
                        "AcsEventCond": {
                            "searchID": "1",
                            "searchResultPosition": search_pos,
                            "maxResults": 30,
                            "major": 5,
                            "minor": 0,
                            "startTime": st_val,
                            "endTime": et_val
                        }
                    }
                    ok_j, data_j = _hikvision_request('/ISAPI/AccessControl/AcsEvent?format=json', method='POST', payload=json_payload, content_type='application/json', timeout=3)
                    if ok_j:
                        page_events = _parse_hikvision_events_response(data_j)
                        if page_events: break
                    else:
                        last_error = data_j
                    if page_events: break

            if not page_events:
                for st_val, et_val in time_pairs:
                    json_payload_alt = {
                        "AcsEventSearchCond": {
                            "searchID": "1",
                            "searchResultPosition": search_pos,
                            "maxResults": 30,
                            "major": 5,
                            "minor": 0,
                            "startTime": st_val,
                            "endTime": et_val
                        }
                    }
                    ok_j2, data_j2 = _hikvision_request('/ISAPI/AccessControl/AcsEvent/Search?format=json', method='POST', payload=json_payload_alt, content_type='application/json', timeout=3)
                    if ok_j2:
                        page_events = _parse_hikvision_events_response(data_j2)
                        if page_events: break
                    else:
                        last_error = data_j2
                    if page_events: break

            if not page_events:
                break

            page_added = 0
            for evt in page_events:
                emp_no = str(evt.get('employeeNoString') or evt.get('employeeNo') or '').strip()
                time_str = evt.get('time') or evt.get('eventTime') or evt.get('event_time') or evt.get('Date') or evt.get('Time')
                serial_no = str(evt.get('serialNo') or evt.get('serial') or evt.get('eventID') or '')
                card_no = str(evt.get('cardNo') or '')
                
                if not time_str: continue

                event_dt = None
                try:
                    ts_clean = str(time_str).strip()
                    if 'T' in ts_clean:
                        dt_part = ts_clean.split('.')[0]
                        if '+' in dt_part[10:]:
                            dt_base = dt_part.rsplit('+', 1)[0]
                        elif '-' in dt_part[10:]:
                            dt_base = dt_part.rsplit('-', 1)[0]
                        else:
                            dt_base = dt_part.replace('Z', '')
                        event_dt = datetime.strptime(dt_base, '%Y-%m-%dT%H:%M:%S')
                    else:
                        event_dt = datetime.strptime(ts_clean[:19], '%Y-%m-%d %H:%M:%S')
                except Exception as exc:
                    print(f"Timestamp parse error on {time_str}: {exc}")
                    continue

                raw_id = f"HIK_{serial_no}_{emp_no}_{event_dt.strftime('%Y%m%d%H%M%S')}"
                if not serial_no and not emp_no: continue
                
                if AttendanceRecord.query.filter_by(raw_event_id=raw_id).first():
                    continue
                
                user_obj = users_map.get(emp_no)
                user_name = user_obj.name if user_obj else (evt.get('name') or (f"NV #{emp_no}" if emp_no else "Chưa rõ"))
                
                verify_mode = 'Vân tay'
                evt_str = json.dumps(evt).lower()
                v_mode_val = str(evt.get('verifyMode') or evt.get('currentVerifyMode') or '').lower()
                if 'face' in v_mode_val or ('face' in evt_str and ('faceid' in evt_str or 'facial' in evt_str)):
                    verify_mode = 'Khuôn mặt'
                elif 'card' in v_mode_val or (card_no and card_no.strip() not in ('', '0', 'null', 'None')):
                    verify_mode = 'Thẻ'
                elif 'pwd' in v_mode_val or 'password' in v_mode_val:
                    verify_mode = 'Mật khẩu'
                else:
                    verify_mode = 'Vân tay'

                event_type = 'Check-in' if event_dt.hour < 12 else 'Check-out'

                record = AttendanceRecord(
                    employee_no=emp_no or 'UNKNOWN',
                    user_name=user_name,
                    event_time=event_dt,
                    verify_mode=verify_mode,
                    event_type=event_type,
                    device_name='Hikvision Terminal',
                    raw_event_id=raw_id
                )
                db.session.add(record)
                new_count += 1
                page_added += 1

            db.session.commit()
            search_pos += len(page_events)

            if len(page_events) == 0:
                break

        date_info_str = f"từ {start_date.strftime('%d/%m')} đến {end_date.strftime('%d/%m')}"
        if new_count == 0 and last_error and search_pos == 0:
            return False, f"Lỗi lấy nhật ký từ thiết bị: {last_error}"

        return True, f"Đã đồng bộ {new_count} lượt quẹt vân tay mới ({date_info_str}) từ thiết bị Hikvision."
    except Exception as exc:
        db.session.rollback()
        return False, f"Lỗi xử lý nhật ký sự kiện: {exc}"

def get_all_attendance_user_types():
    try:
        types = [r[0] for r in db.session.query(AttendanceUser.user_type).distinct().all() if r[0]]
        defaults = ['Nhân viên', 'VIP', 'Khách', 'Khác']
        for d in defaults:
            if d not in types:
                types.append(d)
        return types
    except Exception as exc:
        db.session.rollback()
        return ['Nhân viên', 'VIP', 'Khách', 'Khác']

@app.route('/attendance')
def attendance_logs():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    start_date_val = request.args.get('start_date')
    end_date_val = request.args.get('end_date')
    quick_range = request.args.get('quick_range', '')
    q = (request.args.get('q') or '').strip()
    user_type_filter = (request.args.get('user_type') or '').strip()
    sort_by = (request.args.get('sort') or 'date_desc').strip()
    
    page = request.args.get('page', 1, type=int)
    summary_page = request.args.get('summary_page', 1, type=int)
    active_tab = request.args.get('active_tab', 'summary')

    today = date.today()

    # Determine date range
    if quick_range == 'all':
        start_date = date(2000, 1, 1)
        end_date = date(2099, 12, 31)
    elif quick_range == '1':
        start_date = today
        end_date = today
    elif quick_range == '7':
        start_date = today - timedelta(days=6)
        end_date = today
    elif quick_range == '30':
        start_date = today - timedelta(days=29)
        end_date = today
    elif start_date_val or end_date_val:
        try:
            start_date = datetime.strptime(start_date_val, '%Y-%m-%d').date() if start_date_val else (today - timedelta(days=29))
            end_date = datetime.strptime(end_date_val, '%Y-%m-%d').date() if end_date_val else today
        except Exception:
            start_date = today - timedelta(days=29)
            end_date = today
    else:
        # Default to Today
        start_date = today
        end_date = today
        quick_range = '1'

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    summary_list = []
    records = None
    stats = {
        'total_records': 0,
        'total_users': 0,
        'last_sync_time': 'Chưa có dữ liệu',
        'hikvision_cfg': get_hikvision_config()
    }

    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    user_permissions = session.get('permissions') or []
    current_user_obj = User.query.get(current_user_id) if current_user_id else None
    db_role = str(getattr(current_user_obj, 'role', '') or '').lower()
    db_username = str(getattr(current_user_obj, 'username', '') or '').lower()
    
    is_admin = (
        user_role in ('admin', 'quản trị viên', 'administrator') or
        db_role in ('admin', 'quản trị viên', 'administrator') or
        db_username == 'admin' or
        ('attendance.view_all' in user_permissions) or
        (session.get('is_admin') is True) or
        getattr(current_user_obj, 'is_admin', False) or
        getattr(current_user_obj, 'is_superuser', False) or
        (current_user_id == 1)
    )
    
    linked_att_user = None
    permission_notice = None

    try:
        query = AttendanceRecord.query.filter(AttendanceRecord.event_time >= start_dt, AttendanceRecord.event_time <= end_dt)

        if not is_admin:
            linked_att_user = AttendanceUser.query.filter_by(system_user_id=current_user_id).first()
            if linked_att_user and linked_att_user.employee_no:
                query = query.filter(AttendanceRecord.employee_no == linked_att_user.employee_no)
                permission_notice = f"Đang hiển thị dữ liệu chấm công cá nhân của bạn (Mã NV: {linked_att_user.employee_no})."
            else:
                query = query.filter(AttendanceRecord.employee_no == '___NO_PERM___')
                permission_notice = "Tài khoản của bạn chưa được liên kết với Mã chấm công nào. Vui lòng liên hệ Quản trị viên."

        if q:
            query = query.filter(or_(
                AttendanceRecord.employee_no.ilike(f'%{q}%'),
                AttendanceRecord.user_name.ilike(f'%{q}%')
            ))

        if user_type_filter:
            matching_emp_nos = [u.employee_no for u in AttendanceUser.query.filter_by(user_type=user_type_filter).all()]
            query = query.filter(AttendanceRecord.employee_no.in_(matching_emp_nos))

        if 'per_page' in request.args:
            try:
                per_page = int(request.args.get('per_page'))
                session['per_page_attendance_logs'] = per_page
            except Exception:
                per_page = session.get('per_page_attendance_logs', 20)
        else:
            per_page = session.get('per_page_attendance_logs', 20)

        # Tab 2 Paginated Records
        records = query.order_by(AttendanceRecord.event_time.desc()).paginate(page=page, per_page=per_page, error_out=False)

        # Python-based DB-agnostic Summary Aggregation
        all_raw_records = query.order_by(AttendanceRecord.event_time.asc()).all()
        users_map = {}
        try:
            users_map = {u.employee_no: u for u in AttendanceUser.query.all()}
        except Exception as u_exc:
            db.session.rollback()
            print(f"Users map query info: {u_exc}")
        
        summary_map = {}
        for rec in all_raw_records:
            log_date_str = rec.event_time.strftime('%Y-%m-%d')
            key = (rec.employee_no, log_date_str)
            if key not in summary_map:
                u_obj = users_map.get(rec.employee_no)
                u_name = getattr(u_obj, 'name', None) if u_obj else None
                u_type = getattr(u_obj, 'user_type', None) if u_obj else None
                u_dept = getattr(u_obj, 'department', None) if u_obj else None
                summary_map[key] = {
                    'employee_no': rec.employee_no,
                    'user_name': u_name or rec.user_name or 'Chưa rõ',
                    'user_type': u_type or 'Nhân viên',
                    'department': u_dept or '-',
                    'log_date': log_date_str,
                    'first_in': rec.event_time,
                    'last_out': rec.event_time,
                    'total_scans': 1
                }
            else:
                summary_map[key]['last_out'] = rec.event_time
                summary_map[key]['total_scans'] += 1

        for s in summary_map.values():
            if s['last_out'] == s['first_in']:
                s['last_out'] = None
            summary_list.append(s)

        # Apply Numeric & Date Sorting on Summary List
        if sort_by == 'first_in_asc':
            summary_list.sort(key=lambda x: x['first_in'])
        elif sort_by == 'first_in_desc':
            summary_list.sort(key=lambda x: x['first_in'], reverse=True)
        elif sort_by == 'last_out_desc':
            summary_list.sort(key=lambda x: (x['last_out'] or datetime.min), reverse=True)
        elif sort_by == 'last_out_asc':
            summary_list.sort(key=lambda x: (x['last_out'] or datetime.max))
        elif sort_by == 'emp_asc':
            summary_list.sort(key=lambda x: emp_sort_key(x['employee_no']))
        elif sort_by == 'emp_desc':
            summary_list.sort(key=lambda x: emp_sort_key(x['employee_no']), reverse=True)
        else: # date_desc
            summary_list.sort(key=lambda x: (x['log_date'], x['first_in']), reverse=True)

        today_date = date.today()
        latest_rec = AttendanceRecord.query.order_by(AttendanceRecord.event_time.desc()).first()
        if latest_rec and latest_rec.event_time:
            latest_date = latest_rec.event_time.date()
            if latest_date == today_date or abs((today_date - latest_date).days) <= 1:
                today_date = latest_date

        start_today = datetime.combine(today_date, datetime.min.time())
        end_today = datetime.combine(today_date, datetime.max.time())

        records_today_query = AttendanceRecord.query.filter(
            AttendanceRecord.event_time >= start_today,
            AttendanceRecord.event_time <= end_today
        )
        
        users_today_query = db.session.query(func.count(db.distinct(AttendanceRecord.employee_no))).filter(
            AttendanceRecord.event_time >= start_today,
            AttendanceRecord.event_time <= end_today,
            AttendanceRecord.employee_no != 'UNKNOWN'
        )

        if not is_admin:
            emp_no_check = linked_att_user.employee_no if linked_att_user else '___NO_PERM___'
            records_today_query = records_today_query.filter(AttendanceRecord.employee_no == emp_no_check)
            users_today_query = users_today_query.filter(AttendanceRecord.employee_no == emp_no_check)

        stats['total_records'] = records_today_query.count()
        stats['today_users'] = users_today_query.scalar() or 0
        stats['total_users'] = AttendanceUser.query.filter_by(is_active=True).count()
        stats['today_date_str'] = today_date.strftime('%d/%m/%Y')
        
        last_rec = AttendanceRecord.query.order_by(AttendanceRecord.id.desc()).first()
        if last_rec and last_rec.created_at:
            stats['last_sync_time'] = last_rec.created_at.strftime('%H:%M:%S %d/%m/%Y')
        else:
            stats['last_sync_time'] = 'Chưa có dữ liệu'
    except Exception as exc:
        db.session.rollback()
        import traceback
        err_stack = traceback.format_exc()
        print(f"CRITICAL ATTENDANCE_LOGS ERROR:\n{err_stack}")
        permission_notice = f"⚠️ Phát hiện sự cố dữ liệu: {exc}"

    # Summary List Manual Pagination
    summary_per_page = session.get('per_page_attendance_summary', 20)
    total_summary_items = len(summary_list)
    summary_total_pages = max(1, (total_summary_items + summary_per_page - 1) // summary_per_page)
    summary_page = min(max(1, summary_page), summary_total_pages)
    
    start_idx = (summary_page - 1) * summary_per_page
    end_idx = start_idx + summary_per_page
    summary_paged_list = summary_list[start_idx:end_idx]

    summary_pagination = {
        'page': summary_page,
        'per_page': summary_per_page,
        'total': total_summary_items,
        'pages': summary_total_pages,
        'has_prev': summary_page > 1,
        'has_next': summary_page < summary_total_pages,
        'prev_num': summary_page - 1,
        'next_num': summary_page + 1
    }

    if records is None:
        try:
            db.session.rollback()
            records = AttendanceRecord.query.filter_by(id=-1).paginate(page=1, per_page=20, error_out=False)
        except Exception:
            records = None

    try:
        user_types_list = get_all_attendance_user_types()
    except Exception:
        user_types_list = []

    display_start = start_date.strftime('%Y-%m-%d') if start_date.year > 2001 else ''
    display_end = end_date.strftime('%Y-%m-%d') if end_date.year < 2090 else ''

    return render_template(
        'attendance_logs.html',
        records=records,
        summary_list=summary_paged_list,
        summary_pagination=summary_pagination,
        stats=stats,
        start_date=display_start,
        end_date=display_end,
        quick_range=quick_range,
        q=q,
        sort_by=sort_by,
        user_type=user_type_filter,
        active_tab=active_tab,
        is_admin=is_admin,
        permission_notice=permission_notice,
        user_types=user_types_list
    )

@app.route('/attendance/export')
def attendance_export():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        export_type = request.args.get('type', 'summary')
        start_date_val = request.args.get('start_date')
        end_date_val = request.args.get('end_date')
        quick_range = request.args.get('quick_range', '')
        q = (request.args.get('q') or '').strip()
        user_type_filter = (request.args.get('user_type') or '').strip()
        sort_by = (request.args.get('sort') or 'date_desc').strip()
        
        today = date.today()
        if quick_range == '1':
            start_date = today
            end_date = today
        elif quick_range == '7':
            start_date = today - timedelta(days=6)
            end_date = today
        elif quick_range == '30':
            start_date = today - timedelta(days=29)
            end_date = today
        else:
            try:
                start_date = datetime.strptime(start_date_val, '%Y-%m-%d').date() if start_date_val else (today - timedelta(days=6))
                end_date = datetime.strptime(end_date_val, '%Y-%m-%d').date() if end_date_val else today
            except Exception:
                start_date = today - timedelta(days=6)
                end_date = today

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        current_user_id = session.get('user_id')
        user_role = str(session.get('role') or '').lower()
        user_permissions = session.get('permissions') or []
        current_user_obj = User.query.get(current_user_id) if current_user_id else None
        db_role = str(getattr(current_user_obj, 'role', '') or '').lower()
        
        is_admin = (
            user_role in ('admin', 'quản trị viên', 'administrator') or
            db_role in ('admin', 'quản trị viên', 'administrator') or
            ('attendance.view_all' in user_permissions) or
            (session.get('is_admin') is True) or
            getattr(current_user_obj, 'is_admin', False) or
            (current_user_id == 1)
        )

        output = io.StringIO()
        writer = csv.writer(output)

        if export_type == 'summary':
            writer.writerow(['STT', 'Ngày', 'Mã NV', 'Account', 'Đối tượng', 'Phòng ban', 'Giờ Vào (Sớm nhất)', 'Giờ Ra (Muộn nhất)', 'Tổng lượt'])
            
            query = AttendanceRecord.query.filter(AttendanceRecord.event_time >= start_dt, AttendanceRecord.event_time <= end_dt)

            if not is_admin:
                linked_att_user = AttendanceUser.query.filter_by(system_user_id=current_user_id).first()
                if linked_att_user and linked_att_user.employee_no:
                    query = query.filter(AttendanceRecord.employee_no == linked_att_user.employee_no)
                else:
                    query = query.filter(AttendanceRecord.employee_no == '___NO_PERM___')

            if q:
                query = query.filter(or_(AttendanceRecord.employee_no.ilike(f'%{q}%'), AttendanceRecord.user_name.ilike(f'%{q}%')))
            if user_type_filter:
                matching_emp_nos = [u.employee_no for u in AttendanceUser.query.filter_by(user_type=user_type_filter).all()]
                query = query.filter(AttendanceRecord.employee_no.in_(matching_emp_nos))

            all_raw_records = query.order_by(AttendanceRecord.event_time.asc()).all()
            users_map = {u.employee_no: u for u in AttendanceUser.query.all()}
            
            summary_map = {}
            for rec in all_raw_records:
                log_date_str = rec.event_time.strftime('%Y-%m-%d')
                key = (rec.employee_no, log_date_str)
                if key not in summary_map:
                    u_obj = users_map.get(rec.employee_no)
                    summary_map[key] = {
                        'employee_no': rec.employee_no,
                        'user_name': u_obj.name if u_obj else rec.user_name,
                        'user_type': u_obj.user_type if u_obj else 'Nhân viên',
                        'department': u_obj.department if u_obj else '-',
                        'log_date': log_date_str,
                        'first_in': rec.event_time,
                        'last_out': rec.event_time,
                        'total_scans': 1
                    }
                else:
                    summary_map[key]['last_out'] = rec.event_time
                    summary_map[key]['total_scans'] += 1

            summary_list = []
            for s in summary_map.values():
                if s['last_out'] == s['first_in']:
                    s['last_out'] = None
                summary_list.append(s)

            if sort_by == 'first_in_asc': summary_list.sort(key=lambda x: x['first_in'])
            elif sort_by == 'first_in_desc': summary_list.sort(key=lambda x: x['first_in'], reverse=True)
            elif sort_by == 'last_out_desc': summary_list.sort(key=lambda x: (x['last_out'] or datetime.min), reverse=True)
            elif sort_by == 'last_out_asc': summary_list.sort(key=lambda x: (x['last_out'] or datetime.max))
            elif sort_by == 'emp_asc': summary_list.sort(key=lambda x: emp_sort_key(x['employee_no']))
            elif sort_by == 'emp_desc': summary_list.sort(key=lambda x: emp_sort_key(x['employee_no']), reverse=True)
            else: summary_list.sort(key=lambda x: (x['log_date'], x['first_in']), reverse=True)

            for idx, r in enumerate(summary_list, 1):
                first_in_str = r['first_in'].strftime('%H:%M:%S') if r['first_in'] else ''
                last_out_str = r['last_out'].strftime('%H:%M:%S') if r['last_out'] else ''
                writer.writerow([
                    idx,
                    r['log_date'],
                    r['employee_no'],
                    r['user_name'],
                    r['user_type'],
                    r['department'],
                    first_in_str,
                    last_out_str,
                    r['total_scans']
                ])
            filename = f"Tong_hop_cham_cong_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
        else:
            writer.writerow(['STT', 'Thời gian quẹt', 'Mã NV', 'Account', 'Phương thức', 'Sự kiện', 'Thiết bị', 'Mã sự kiện'])
            query = AttendanceRecord.query.filter(AttendanceRecord.event_time >= start_dt, AttendanceRecord.event_time <= end_dt)
            if not is_admin:
                linked_att_user = AttendanceUser.query.filter_by(system_user_id=current_user_id).first()
                if linked_att_user and linked_att_user.employee_no:
                    query = query.filter(AttendanceRecord.employee_no == linked_att_user.employee_no)
                else:
                    query = query.filter(AttendanceRecord.employee_no == '___NO_PERM___')

            if q:
                query = query.filter(or_(AttendanceRecord.employee_no.ilike(f'%{q}%'), AttendanceRecord.user_name.ilike(f'%{q}%')))
            if user_type_filter:
                matching_emp_nos = [u.employee_no for u in AttendanceUser.query.filter_by(user_type=user_type_filter).all()]
                query = query.filter(AttendanceRecord.employee_no.in_(matching_emp_nos))

            records = query.order_by(AttendanceRecord.event_time.desc()).all()
            for idx, rec in enumerate(records, 1):
                writer.writerow([
                    idx,
                    rec.event_time.strftime('%d-%m-%Y %H:%M:%S'),
                    rec.employee_no,
                    rec.user_name,
                    rec.verify_mode,
                    rec.event_type,
                    rec.device_name,
                    rec.raw_event_id
                ])
            filename = f"Nhat_ky_cham_cong_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

        csv_data = output.getvalue().encode('utf-8-sig')
        response = make_response(csv_data)
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return response
    except Exception as exc:
        print(f"Export error: {exc}")
        flash(f"Lỗi khi xuất dữ liệu: {exc}", "danger")
        return redirect(url_for('attendance_logs'))

@app.route('/attendance/users', methods=['GET', 'POST'])
def attendance_users():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_permissions = session.get('permissions') or []
    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    is_admin = (user_role in ('admin', 'quản trị viên', 'administrator')) or ('attendance.view_all' in user_permissions) or (session.get('is_admin') is True) or (current_user_id == 1)
    if not is_admin and 'attendance.manage_users' not in user_permissions:
        flash('Bạn không có quyền quản lý người chấm công.', 'danger')
        return redirect(url_for('attendance_logs'))
    
    if request.method == 'POST':
        emp_no = (request.form.get('employee_no') or '').strip()
        name = (request.form.get('name') or '').strip()
        user_type = (request.form.get('user_type') or 'Nhân viên').strip()
        department = (request.form.get('department') or '').strip()
        card_no = (request.form.get('card_no') or '').strip()
        system_user_id = request.form.get('system_user_id', type=int)
        
        page = request.form.get('page', 1, type=int)
        q = request.form.get('q_filter', '')
        user_type_f = request.form.get('user_type_filter', '')

        if system_user_id:
            sys_user = User.query.get(system_user_id)
            if sys_user and sys_user.department_info:
                department = sys_user.department_info.name

        if not emp_no or not name:
            flash('Mã chấm công và Họ tên là bắt buộc.', 'danger')
        elif AttendanceUser.query.filter_by(employee_no=emp_no).first():
            flash(f'Mã chấm công {emp_no} đã tồn tại.', 'warning')
        else:
            db.session.add(AttendanceUser(
                employee_no=emp_no,
                name=name,
                user_type=user_type,
                department=department,
                card_no=card_no,
                system_user_id=system_user_id if system_user_id else None
            ))
            db.session.commit()
            flash('Đã thêm người chấm công mới.', 'success')
        return redirect(url_for('attendance_users', page=page, q=q, user_type=user_type_f))

    q = (request.args.get('q') or '').strip()
    user_type_filter = (request.args.get('user_type') or '').strip()
    status_filter = (request.args.get('status') or '').strip()
    page = request.args.get('page', 1, type=int)

    query = AttendanceUser.query
    if q:
        query = query.filter(or_(AttendanceUser.employee_no.ilike(f'%{q}%'), AttendanceUser.name.ilike(f'%{q}%')))
    if user_type_filter:
        query = query.filter_by(user_type=user_type_filter)
    if status_filter == 'active':
        query = query.filter_by(is_active=True)
    elif status_filter == 'inactive':
        query = query.filter_by(is_active=False)

    if 'per_page' in request.args:
        try:
            per_page = int(request.args.get('per_page'))
            session['per_page_attendance_users'] = per_page
        except Exception:
            per_page = session.get('per_page_attendance_users', 20)
    else:
        per_page = session.get('per_page_attendance_users', 20)

    users_pagination = query.order_by(AttendanceUser.employee_no).paginate(page=page, per_page=per_page, error_out=False)
    system_users = User.query.order_by(func.lower(User.full_name)).all()

    return render_template(
        'attendance_users.html',
        users=users_pagination,
        system_users=system_users,
        q=q,
        user_type=user_type_filter,
        status=status_filter,
        user_types=get_all_attendance_user_types()
    )

@app.route('/attendance/users/<int:user_id>/edit', methods=['POST'])
def edit_attendance_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_permissions = session.get('permissions') or []
    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    is_admin = (user_role in ('admin', 'quản trị viên', 'administrator')) or ('attendance.view_all' in user_permissions) or (session.get('is_admin') is True) or (current_user_id == 1)
    if not is_admin and 'attendance.manage_users' not in user_permissions:
        flash('Bạn không có quyền chỉnh sửa người chấm công.', 'danger')
        return redirect(url_for('attendance_logs'))
    user_obj = AttendanceUser.query.get_or_404(user_id)
    
    emp_no = (request.form.get('employee_no') or '').strip()
    name = (request.form.get('name') or '').strip()
    user_type = (request.form.get('user_type') or 'Nhân viên').strip()
    department = (request.form.get('department') or '').strip()
    card_no = (request.form.get('card_no') or '').strip()
    system_user_id = request.form.get('system_user_id', type=int)

    try:
        if not emp_no or not name:
            raise ValueError('Mã chấm công và Họ tên là bắt buộc.')
        duplicate = AttendanceUser.query.filter(AttendanceUser.id != user_obj.id, AttendanceUser.employee_no == emp_no).first()
        if duplicate:
            raise ValueError(f'Mã chấm công {emp_no} đã trùng với người khác.')
        
        user_obj.employee_no = emp_no
        user_obj.name = name
        user_obj.user_type = user_type
        user_obj.department = department
        user_obj.card_no = card_no
        user_obj.system_user_id = system_user_id if system_user_id else None
        if system_user_id:
            sys_user = User.query.get(system_user_id)
            if sys_user and sys_user.department_info:
                user_obj.department = sys_user.department_info.name
        user_obj.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Đã cập nhật thông tin người chấm công.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')

    return redirect(url_for('attendance_users'))

@app.route('/attendance/users/<int:user_id>/delete', methods=['POST'])
def delete_attendance_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_permissions = session.get('permissions') or []
    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    is_admin = (user_role in ('admin', 'quản trị viên', 'administrator')) or ('attendance.view_all' in user_permissions) or (session.get('is_admin') is True) or (current_user_id == 1)
    if not is_admin and 'attendance.manage_users' not in user_permissions:
        flash('Bạn không có quyền xóa người chấm công.', 'danger')
        return redirect(url_for('attendance_logs'))
    user_obj = AttendanceUser.query.get_or_404(user_id)
    db.session.delete(user_obj)
    db.session.commit()
    flash('Đã xóa người chấm công.', 'success')
    return redirect(url_for('attendance_users'))

@app.route('/attendance/config', methods=['GET', 'POST'])
def attendance_config_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_permissions = session.get('permissions') or []
    current_user_id = session.get('user_id')
    user_role = str(session.get('role') or '').lower()
    is_admin = (user_role in ('admin', 'quản trị viên', 'administrator')) or ('attendance.view_all' in user_permissions) or (session.get('is_admin') is True) or (current_user_id == 1)
    if not is_admin and 'attendance.config' not in user_permissions:
        flash('Bạn không có quyền truy cập cấu hình máy chấm công.', 'danger')
        return redirect(url_for('attendance_logs'))
    if request.method == 'POST':
        host = (request.form.get('host') or '192.168.11.94').strip()
        port = (request.form.get('port') or '8000').strip()
        username = (request.form.get('username') or 'admin').strip()
        password = request.form.get('password') or ''
        
        save_hikvision_config({
            'host': host,
            'port': port,
            'username': username,
            'password': password
        })
        flash('Đã lưu cấu hình kết nối máy chấm công Hikvision.', 'success')
        return redirect(url_for('attendance_config_page'))

    cfg = get_hikvision_config()
    return render_template('attendance_config.html', cfg=cfg)

@app.route('/attendance/test-connection', methods=['POST'])
def attendance_test_connection():
    if 'user_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    host = request.form.get('host')
    if host:
        save_hikvision_config({
            'host': (request.form.get('host') or '').strip(),
            'port': (request.form.get('port') or '8000').strip(),
            'username': (request.form.get('username') or 'admin').strip(),
            'password': request.form.get('password') or ''
        })

    ok, msg = _hikvision_test_connection()
    return jsonify({'success': ok, 'message': msg})

@app.route('/attendance/sync', methods=['POST'])
def attendance_sync_api():
    if 'user_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        sync_type = request.form.get('sync_type') or (request.json.get('sync_type') if request.is_json else None) or 'all'
        days_raw = request.form.get('days') or (request.json.get('days') if request.is_json else None) or '7'
        days = int(days_raw) if str(days_raw).isdigit() else None

        start_date_str = request.form.get('start_date') or (request.json.get('start_date') if request.is_json else None)
        end_date_str = request.form.get('end_date') or (request.json.get('end_date') if request.is_json else None)

        custom_start = None
        custom_end = None
        if start_date_str and start_date_str.strip():
            try: custom_start = datetime.strptime(start_date_str.strip(), '%Y-%m-%d').date()
            except Exception: pass
        if end_date_str and end_date_str.strip():
            try: custom_end = datetime.strptime(end_date_str.strip(), '%Y-%m-%d').date()
            except Exception: pass

        res_msgs = []
        success_all = True

        if sync_type in ('all', 'users_only'):
            ok_u, msg_u = _hikvision_fetch_users()
            res_msgs.append(msg_u)
            if not ok_u: success_all = False

        if sync_type in ('all', 'logs_only'):
            ok_e, msg_e = _hikvision_sync_events(start_date=custom_start, end_date=custom_end, days=days)
            res_msgs.append(msg_e)
            if not ok_e: success_all = False

        return jsonify({
            'success': success_all,
            'message': " | ".join(res_msgs)
        })
    except Exception as exc:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f"Lỗi máy chủ (500): {str(exc)}"
        }), 500

def _safe_add_column(table_name, col_name, col_type):
    try:
        with db.engine.connect() as conn:
            inspector = inspect(db.engine)
            if inspector.has_table(table_name):
                cols = [c['name'] for c in inspector.get_columns(table_name)]
                if col_name not in cols:
                    try:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                    except Exception as e1:
                        conn.rollback()
                        print(f"Migration {table_name}.{col_name} info: {e1}")
    except Exception as exc:
        print(f"Migration helper info: {exc}")

_startup_db_initialized = False

@app.before_request
def _run_lazy_startup_migrations():
    global _startup_db_initialized
    if not _startup_db_initialized:
        _startup_db_initialized = True
        try:
            db.create_all()
        except Exception as e_db:
            print(f"Lazy startup db.create_all info: {e_db}")
        _safe_add_column('work_account', 'server_type', 'VARCHAR(50)')
        _safe_add_column('work_account', 'provider', 'VARCHAR(255)')
        _safe_add_column('work_account', 'access_ip', 'VARCHAR(255)')
        _safe_add_column('work_account', 'mgmt_ip', 'VARCHAR(255)')
        _safe_add_column('work_account', 'expiration_date', 'DATE')
        _safe_add_column('work_account', 'billing_cycle', 'VARCHAR(50)')
        _safe_add_column('work_account', 'cpu_info', 'VARCHAR(255)')
        _safe_add_column('work_account', 'ram_info', 'VARCHAR(255)')
        _safe_add_column('work_account', 'disk_info', 'VARCHAR(255)')
        _safe_add_column('attendance_user', 'department', 'VARCHAR(100)')
        _safe_add_column('attendance_user', 'system_user_id', 'INTEGER')
        _safe_add_column('stock_item_movement', 'reason', 'VARCHAR(255)')
        _safe_add_column('stock_item_movement', 'notes', 'TEXT')

        try:
            with app.app_context():
                AttendanceRecord.query.filter_by(verify_mode='Thẻ').update({'verify_mode': 'Vân tay'})
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f"Lazy record verify_mode update info: {exc}")



# --- License, IT Services & Work Accounts Models ---
class SoftwareLicense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    software_name = db.Column(db.String(255), nullable=False)
    license_type = db.Column(db.String(100), default='Hệ điều hành')
    license_key = db.Column(db.Text, nullable=False)
    max_seats = db.Column(db.Integer, default=1)
    supplier = db.Column(db.String(255))
    purchase_date = db.Column(db.Date)
    expiration_date = db.Column(db.Date)
    is_perpetual = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(50), default='Đang sử dụng')
    notes = db.Column(db.Text)
    contract_file = db.Column(db.String(255))
    invoice_file = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    device_assignments = db.relationship('LicenseDevice', backref='license', cascade='all, delete-orphan')

class LicenseDevice(db.Model):
    license_id = db.Column(db.Integer, db.ForeignKey('software_license.id'), primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), primary_key=True)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    device = db.relationship('Device')

class ITService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    service_name = db.Column(db.String(255), nullable=False)
    service_type = db.Column(db.String(100), default='Internet cáp quang')
    branch = db.Column(db.String(100))
    department = db.Column(db.String(100))
    contract_number = db.Column(db.String(100))
    provider = db.Column(db.String(255))
    bandwidth = db.Column(db.String(100))
    endpoint_device = db.Column(db.String(255))
    static_ip_range = db.Column(db.String(255))
    monthly_cost = db.Column(db.Float, default=0.0)
    expiration_date = db.Column(db.Date)
    is_perpetual = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(50), default='Đang sử dụng')
    tech_support_info = db.Column(db.Text)
    invoice_file = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class WorkAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    account_name = db.Column(db.String(255), nullable=False)
    platform = db.Column(db.String(100), default='Office 365')
    username_email = db.Column(db.String(255), nullable=False)
    password_text = db.Column(db.String(255))
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    assigned_to_device_id = db.Column(db.Integer, db.ForeignKey('device.id'))
    status = db.Column(db.String(50), default='Đang sử dụng')
    notes = db.Column(db.Text)
    server_type = db.Column(db.String(50)) # 'Server thuê', 'Server offline'
    provider = db.Column(db.String(255))   # AWS, Viettel Cloud, CMC...
    access_ip = db.Column(db.String(255))  # IP / Domain truy cập
    mgmt_ip = db.Column(db.String(255))    # IP iDRAC / ILO / HDM / IPMI
    cpu_info = db.Column(db.String(255))   # Thông tin CPU
    ram_info = db.Column(db.String(255))   # Thông tin RAM
    disk_info = db.Column(db.String(255))  # Thông tin Ổ cứng
    expiration_date = db.Column(db.Date)   # Ngày hết hạn SaaS / Server
    billing_cycle = db.Column(db.String(50), default='Theo năm') # 'Theo tháng', 'Theo năm', 'Vĩnh viễn'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    assigned_user = db.relationship('User', foreign_keys=[assigned_to_user_id])
    assigned_device = db.relationship('Device', foreign_keys=[assigned_to_device_id])

class SoftwareLicenseType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class ITServiceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)



# ==========================================
# LICENSE, IT SERVICES & WORK ACCOUNTS ROUTES
# ==========================================

@app.route('/licenses')
def license_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        db.create_all()
    except Exception as db_err:
        db.session.rollback()

    try:
        active_tab = request.args.get('tab', 'account')
        sub_tab = request.args.get('sub_tab', 'all')
        q = (request.args.get('q') or '').strip()
        status_filter = (request.args.get('status') or '').strip()
        type_filter = (request.args.get('type') or '').strip()
        supplier_filter = (request.args.get('supplier') or '').strip()
        device_filter = request.args.get('device_id', type=int)
        branch_filter = (request.args.get('branch') or '').strip()
        dept_filter = (request.args.get('department') or '').strip()

        today = date.today()
        expiring_threshold = today + timedelta(days=30)

        # 1. License Stats & Queries
        license_query = SoftwareLicense.query
        if q:
            license_query = license_query.filter(or_(
                SoftwareLicense.software_name.ilike(f'%{q}%'),
                SoftwareLicense.code.ilike(f'%{q}%'),
                SoftwareLicense.license_key.ilike(f'%{q}%'),
                SoftwareLicense.supplier.ilike(f'%{q}%')
            ))
        if status_filter:
            license_query = license_query.filter_by(status=status_filter)
        if type_filter:
            license_query = license_query.filter_by(license_type=type_filter)
        if supplier_filter:
            license_query = license_query.filter_by(supplier=supplier_filter)
        if device_filter:
            license_query = license_query.join(LicenseDevice).filter(LicenseDevice.device_id == device_filter)

        all_licenses_raw = SoftwareLicense.query.all()
        total_licenses_count = len(all_licenses_raw)
        expiring_licenses_count = sum(1 for l in all_licenses_raw if not l.is_perpetual and l.expiration_date and l.expiration_date <= expiring_threshold)
        unassigned_licenses_count = sum(1 for l in all_licenses_raw if len(l.device_assignments) < (l.max_seats or 1))

        if sub_tab == 'available':
            license_query = license_query.filter(SoftwareLicense.id.in_([l.id for l in all_licenses_raw if len(l.device_assignments) < (l.max_seats or 1)]))
        elif sub_tab == 'full':
            license_query = license_query.filter(SoftwareLicense.id.in_([l.id for l in all_licenses_raw if len(l.device_assignments) >= (l.max_seats or 1)]))
        elif sub_tab == 'expiring':
            license_query = license_query.filter(and_(SoftwareLicense.is_perpetual == False, SoftwareLicense.expiration_date <= expiring_threshold))

        page = request.args.get('page', 1, type=int)
        licenses_paged = license_query.order_by(SoftwareLicense.id.desc()).paginate(page=page, per_page=20, error_out=False)

        license_stats = {
            'total': total_licenses_count,
            'expiring': expiring_licenses_count,
            'unassigned': unassigned_licenses_count
        }

        # 2. IT Services Stats & Queries
        service_query = ITService.query
        if q:
            service_query = service_query.filter(or_(
                ITService.service_name.ilike(f'%{q}%'),
                ITService.code.ilike(f'%{q}%'),
                ITService.contract_number.ilike(f'%{q}%'),
                ITService.provider.ilike(f'%{q}%'),
                ITService.static_ip_range.ilike(f'%{q}%')
            ))
        if type_filter:
            service_query = service_query.filter_by(service_type=type_filter)
        if branch_filter:
            service_query = service_query.filter_by(branch=branch_filter)
        if dept_filter:
            service_query = service_query.filter_by(department=dept_filter)
        if status_filter:
            service_query = service_query.filter_by(status=status_filter)
        if supplier_filter:
            service_query = service_query.filter_by(provider=supplier_filter)

        all_services_raw = ITService.query.all()
        total_services_count = len(all_services_raw)
        expiring_services_count = sum(1 for s in all_services_raw if not s.is_perpetual and s.expiration_date and s.expiration_date <= expiring_threshold)
        unassigned_branch_services_count = sum(1 for s in all_services_raw if not s.branch)

        services_paged = service_query.order_by(ITService.id.desc()).paginate(page=page, per_page=20, error_out=False)

        service_stats = {
            'total': total_services_count,
            'expiring': expiring_services_count,
            'unassigned': unassigned_branch_services_count
        }

        # 3. Work Accounts & Server Accounts Queries
        is_server_expr = or_(
            WorkAccount.platform == 'Server',
            WorkAccount.platform.ilike('%server%'),
            WorkAccount.platform.ilike('%vps%'),
            WorkAccount.platform.ilike('%ssh%'),
            WorkAccount.server_type.in_(['Server thuê', 'Server offline'])
        )

        if active_tab == 'server':
            tab_query = WorkAccount.query.filter(is_server_expr)
        else:
            tab_query = WorkAccount.query.filter(not_(is_server_expr))

        if q:
            tab_query = tab_query.filter(or_(
                WorkAccount.account_name.ilike(f'%{q}%'),
                WorkAccount.code.ilike(f'%{q}%'),
                WorkAccount.username_email.ilike(f'%{q}%'),
                WorkAccount.platform.ilike(f'%{q}%')
            ))
        if status_filter:
            tab_query = tab_query.filter_by(status=status_filter)

        accounts_paged = tab_query.order_by(WorkAccount.id.desc()).paginate(page=page, per_page=20, error_out=False)

        tab_records = WorkAccount.query.filter(is_server_expr if active_tab == 'server' else not_(is_server_expr)).all()
        account_stats = {
            'total': len(tab_records),
            'active': sum(1 for a in tab_records if a.status == 'Đang sử dụng'),
            'unassigned': sum(1 for a in tab_records if not a.assigned_to_user_id and not a.assigned_to_device_id)
        }

        # Master Auxiliary Dropdowns
        devices_list = Device.query.order_by(Device.device_code).all()
        users_list = User.query.order_by(User.full_name).all()
        branches_list = [r[0] for r in db.session.query(ITService.branch).distinct().all() if r[0]]
        departments_list = [d.name for d in Department.query.all()]
        suppliers_list = [r[0] for r in db.session.query(SoftwareLicense.supplier).distinct().all() if r[0]]
        service_providers_list = [r[0] for r in db.session.query(ITService.provider).distinct().all() if r[0]]
        license_types = [t.name for t in SoftwareLicenseType.query.all()] or ['Hệ điều hành', 'Văn phòng', 'Chuyên dụng', 'Bảo mật', 'Khác']
        service_types = [t.name for t in ITServiceType.query.all()] or ['Internet cáp quang', 'Thuê chỗ đặt máy chủ', 'Tên miền / Hosting', 'Tổng đài IP', 'Khác']

        current_permissions = _get_current_permissions()

        return render_template(
            'license_management.html',
            active_tab=active_tab,
            sub_tab=sub_tab,
            q=q,
            status_filter=status_filter,
            type_filter=type_filter,
            supplier_filter=supplier_filter,
            device_filter=device_filter,
            branch_filter=branch_filter,
            dept_filter=dept_filter,
            licenses_paged=licenses_paged,
            license_stats=license_stats,
            services_paged=services_paged,
            service_stats=service_stats,
            accounts_paged=accounts_paged,
            account_stats=account_stats,
            devices_list=devices_list,
            users_list=users_list,
            branches_list=branches_list,
            departments_list=departments_list,
            suppliers_list=suppliers_list,
            service_providers_list=service_providers_list,
            license_types=license_types,
            service_types=service_types,
            current_permissions=current_permissions
        )
    except Exception as exc:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(f'Lỗi khi tải dữ liệu Tài khoản & Dịch vụ: {exc}', 'danger')
        return redirect(url_for('home'))

@app.route('/licenses/add', methods=['POST'])
def add_license():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        code = (request.form.get('code') or '').strip()
        if not code:
            code = f"LIC-{datetime.now().strftime('%y%m%d')}-{SoftwareLicense.query.count() + 1:03d}"
        
        sw_name = (request.form.get('software_name') or '').strip()
        l_type = (request.form.get('license_type') or 'Hệ điều hành').strip()
        l_key = (request.form.get('license_key') or '').strip()
        max_seats = request.form.get('max_seats', 1, type=int)
        supplier = (request.form.get('supplier') or '').strip()
        p_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        
        is_perp = bool(request.form.get('is_perpetual'))
        exp_date = None if is_perp else (datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None)
        notes = (request.form.get('notes') or '').strip()

        lic = SoftwareLicense(
            code=code,
            software_name=sw_name,
            license_type=l_type,
            license_key=l_key,
            max_seats=max_seats,
            supplier=supplier,
            purchase_date=p_date,
            expiration_date=exp_date,
            is_perpetual=is_perp,
            notes=notes
        )
        db.session.add(lic)
        db.session.commit()
        flash(f'Thêm thành công License "{sw_name}".', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi thêm License: {exc}', 'danger')
    return redirect(url_for('license_list', tab='license'))

@app.route('/licenses/<int:license_id>/edit', methods=['POST'])
def edit_license(license_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    lic = SoftwareLicense.query.get_or_404(license_id)
    try:
        lic.software_name = (request.form.get('software_name') or '').strip()
        lic.license_type = (request.form.get('license_type') or 'Hệ điều hành').strip()
        lic.license_key = (request.form.get('license_key') or '').strip()
        lic.max_seats = request.form.get('max_seats', 1, type=int)
        lic.supplier = (request.form.get('supplier') or '').strip()
        lic.purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        
        lic.is_perpetual = bool(request.form.get('is_perpetual'))
        lic.expiration_date = None if lic.is_perpetual else (datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None)
        lic.status = (request.form.get('status') or 'Đang sử dụng').strip()
        lic.notes = (request.form.get('notes') or '').strip()

        db.session.commit()
        flash(f'Đã cập nhật License "{lic.software_name}".', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi cập nhật License: {exc}', 'danger')
    return redirect(url_for('license_list', tab='license'))

@app.route('/licenses/<int:license_id>/delete', methods=['POST'])
def delete_license(license_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    lic = SoftwareLicense.query.get_or_404(license_id)
    try:
        db.session.delete(lic)
        db.session.commit()
        flash('Đã xóa License thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi xóa License: {exc}', 'danger')
    return redirect(url_for('license_list', tab='license'))

@app.route('/licenses/<int:license_id>/assign-device', methods=['POST'])
def assign_license_device(license_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    lic = SoftwareLicense.query.get_or_404(license_id)
    device_id = request.form.get('device_id', type=int)
    if not device_id:
        flash('Vui lòng chọn thiết bị để gán.', 'warning')
        return redirect(url_for('license_list', tab='license'))
    
    if len(lic.device_assignments) >= (lic.max_seats or 1):
        flash(f'License "{lic.software_name}" đã đạt giới hạn số lượng gán tối đa ({lic.max_seats}).', 'danger')
        return redirect(url_for('license_list', tab='license'))

    exists = LicenseDevice.query.filter_by(license_id=lic.id, device_id=device_id).first()
    if not exists:
        ld = LicenseDevice(license_id=lic.id, device_id=device_id)
        db.session.add(ld)
        db.session.commit()
        flash('Đã gán License cho thiết bị thành công.', 'success')
    return redirect(url_for('license_list', tab='license'))

@app.route('/licenses/<int:license_id>/unassign-device/<int:device_id>', methods=['POST'])
def unassign_license_device(license_id, device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    ld = LicenseDevice.query.filter_by(license_id=license_id, device_id=device_id).first()
    if ld:
        db.session.delete(ld)
        db.session.commit()
        flash('Đã hủy gán thiết bị khỏi License.', 'success')
    return redirect(url_for('license_list', tab='license'))

@app.route('/it-services/add', methods=['POST'])
def add_it_service():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        code = (request.form.get('code') or '').strip()
        if not code:
            code = f"DV-{datetime.now().strftime('%y%m%d')}-{ITService.query.count() + 1:03d}"
        
        s_name = (request.form.get('service_name') or '').strip()
        s_type = (request.form.get('service_type') or 'Internet cáp quang').strip()
        branch = (request.form.get('branch') or '').strip()
        dept = (request.form.get('department') or '').strip()
        contract_no = (request.form.get('contract_number') or '').strip()
        provider = (request.form.get('provider') or '').strip()
        bandwidth = (request.form.get('bandwidth') or '').strip()
        endpoint_device = (request.form.get('endpoint_device') or '').strip()
        static_ip = (request.form.get('static_ip_range') or '').strip()
        monthly_cost = request.form.get('monthly_cost', 0.0, type=float)
        
        is_perp = bool(request.form.get('is_perpetual'))
        exp_date = None if is_perp else (datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None)
        tech_info = (request.form.get('tech_support_info') or '').strip()

        srv = ITService(
            code=code,
            service_name=s_name,
            service_type=s_type,
            branch=branch,
            department=dept,
            contract_number=contract_no,
            provider=provider,
            bandwidth=bandwidth,
            endpoint_device=endpoint_device,
            static_ip_range=static_ip,
            monthly_cost=monthly_cost,
            expiration_date=exp_date,
            is_perpetual=is_perp,
            tech_support_info=tech_info
        )
        db.session.add(srv)
        db.session.commit()
        flash(f'Thêm Dịch vụ CNTT "{s_name}" thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi thêm Dịch vụ CNTT: {exc}', 'danger')
    return redirect(url_for('license_list', tab='itservice'))

@app.route('/it-services/<int:service_id>/edit', methods=['POST'])
def edit_it_service(service_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    srv = ITService.query.get_or_404(service_id)
    try:
        srv.service_name = (request.form.get('service_name') or '').strip()
        srv.service_type = (request.form.get('service_type') or 'Internet cáp quang').strip()
        srv.branch = (request.form.get('branch') or '').strip()
        srv.department = (request.form.get('department') or '').strip()
        srv.contract_number = (request.form.get('contract_number') or '').strip()
        srv.provider = (request.form.get('provider') or '').strip()
        srv.bandwidth = (request.form.get('bandwidth') or '').strip()
        srv.endpoint_device = (request.form.get('endpoint_device') or '').strip()
        srv.static_ip_range = (request.form.get('static_ip_range') or '').strip()
        srv.monthly_cost = request.form.get('monthly_cost', 0.0, type=float)
        
        srv.is_perpetual = bool(request.form.get('is_perpetual'))
        srv.expiration_date = None if srv.is_perpetual else (datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None)
        srv.status = (request.form.get('status') or 'Đang sử dụng').strip()
        srv.tech_support_info = (request.form.get('tech_support_info') or '').strip()

        db.session.commit()
        flash(f'Cập nhật Dịch vụ CNTT "{srv.service_name}" thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi cập nhật Dịch vụ CNTT: {exc}', 'danger')
    return redirect(url_for('license_list', tab='itservice'))

@app.route('/it-services/<int:service_id>/delete', methods=['POST'])
def delete_it_service(service_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    srv = ITService.query.get_or_404(service_id)
    try:
        db.session.delete(srv)
        db.session.commit()
        flash('Đã xóa Dịch vụ CNTT thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi xóa Dịch vụ CNTT: {exc}', 'danger')
    return redirect(url_for('license_list', tab='itservice'))

@app.route('/work-accounts/add', methods=['POST'])
def add_work_account():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        code = (request.form.get('code') or '').strip()
        if not code:
            code = f"ACC-{datetime.now().strftime('%y%m%d')}-{WorkAccount.query.count() + 1:03d}"
        
        acc_name = (request.form.get('account_name') or '').strip()
        platform = (request.form.get('platform') or 'Office 365').strip()
        user_email = (request.form.get('username_email') or '').strip()
        password_text = (request.form.get('password_text') or '').strip()
        assigned_user = request.form.get('assigned_to_user_id', type=int)
        assigned_device = request.form.get('assigned_to_device_id', type=int)
        notes = (request.form.get('notes') or '').strip()

        server_type = (request.form.get('server_type') or '').strip()
        provider = (request.form.get('provider') or '').strip()
        access_ip = (request.form.get('access_ip') or '').strip()
        mgmt_ip = (request.form.get('mgmt_ip') or '').strip()
        cpu_info = (request.form.get('cpu_info') or '').strip()
        ram_info = (request.form.get('ram_info') or '').strip()
        disk_info = (request.form.get('disk_info') or '').strip()
        billing_cycle = (request.form.get('billing_cycle') or 'Theo năm').strip()
        exp_date = datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None

        acc = WorkAccount(
            code=code,
            account_name=acc_name,
            platform=platform,
            username_email=user_email,
            password_text=password_text,
            assigned_to_user_id=assigned_user,
            assigned_to_device_id=assigned_device,
            notes=notes,
            server_type=server_type,
            provider=provider,
            access_ip=access_ip,
            mgmt_ip=mgmt_ip,
            cpu_info=cpu_info,
            ram_info=ram_info,
            disk_info=disk_info,
            expiration_date=exp_date,
            billing_cycle=billing_cycle
        )
        db.session.add(acc)
        db.session.commit()
        flash(f'Thêm Tài khoản làm việc "{acc_name}" thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi thêm Tài khoản: {exc}', 'danger')
    return redirect(url_for('license_list', tab='account'))

@app.route('/work-accounts/<int:account_id>/edit', methods=['POST'])
def edit_work_account(account_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    acc = WorkAccount.query.get_or_404(account_id)
    try:
        acc.account_name = (request.form.get('account_name') or '').strip()
        acc.platform = (request.form.get('platform') or 'Office 365').strip()
        acc.username_email = (request.form.get('username_email') or '').strip()
        acc.password_text = (request.form.get('password_text') or '').strip()
        acc.assigned_to_user_id = request.form.get('assigned_to_user_id', type=int)
        acc.assigned_to_device_id = request.form.get('assigned_to_device_id', type=int)
        acc.status = (request.form.get('status') or 'Đang sử dụng').strip()
        acc.notes = (request.form.get('notes') or '').strip()
        acc.server_type = (request.form.get('server_type') or '').strip()
        acc.provider = (request.form.get('provider') or '').strip()
        acc.access_ip = (request.form.get('access_ip') or '').strip()
        acc.mgmt_ip = (request.form.get('mgmt_ip') or '').strip()
        acc.cpu_info = (request.form.get('cpu_info') or '').strip()
        acc.ram_info = (request.form.get('ram_info') or '').strip()
        acc.disk_info = (request.form.get('disk_info') or '').strip()
        acc.billing_cycle = (request.form.get('billing_cycle') or 'Theo năm').strip()
        acc.expiration_date = datetime.strptime(request.form.get('expiration_date'), '%Y-%m-%d').date() if request.form.get('expiration_date') else None

        db.session.commit()
        flash(f'Đã cập nhật Tài khoản "{acc.account_name}".', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi cập nhật Tài khoản: {exc}', 'danger')
    return redirect(url_for('license_list', tab='account'))

@app.route('/work-accounts/<int:account_id>/delete', methods=['POST'])
def delete_work_account(account_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    acc = WorkAccount.query.get_or_404(account_id)
    try:
        db.session.delete(acc)
        db.session.commit()
        flash('Đã xóa Tài khoản thành công.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Lỗi xóa Tài khoản: {exc}', 'danger')
    return redirect(url_for('license_list', tab='account'))


if __name__ == '__main__':
    app.run(debug=True)
