import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import aliased
from sqlalchemy import or_, func, event, text, inspect
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
from config import config, get_database_info, is_external_database
from backup_restore import DatabaseBackup

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
app.jinja_env.add_extension('jinja2.ext.do')
app.config.from_object(config[config_name])

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

# Permission catalogue
PERMISSIONS = [
    # Thiết bị
    ('devices.view', 'Xem danh sách/chi tiết thiết bị'),
    ('devices.edit', 'Thêm/Sửa thiết bị'),
    ('devices.delete', 'Xóa thiết bị'),
    # Nhóm thiết bị
    ('device_groups.view', 'Xem nhóm thiết bị'),
    ('device_groups.edit', 'Tạo/Sửa nhóm thiết bị'),
    ('device_groups.delete', 'Xóa nhóm thiết bị'),
    # Phòng server
    ('server_room.view', 'Xem phòng server'),
    ('server_room.edit', 'Thêm/Sửa thiết bị phòng server'),
    ('server_room.delete', 'Gỡ thiết bị khỏi phòng server'),
    # Bàn giao thiết bị
    ('handovers.view', 'Xem lịch sử/Tạo phiếu bàn giao'),
    ('handovers.edit', 'Sửa phiếu bàn giao'),
    ('handovers.delete', 'Xóa phiếu bàn giao'),
    # Phiếu nhập kho
    ('inventory.view', 'Xem danh sách phiếu nhập kho'),
    ('inventory.edit', 'Tạo/Sửa phiếu nhập kho'),
    ('inventory.delete', 'Xóa phiếu nhập kho'),
    # Đề xuất cấu hình
    ('config_proposals.view', 'Xem đề xuất cấu hình'),
    ('config_proposals.create', 'Tạo đề xuất cấu hình'),
    ('config_proposals.edit', 'Sửa đề xuất cấu hình (khi chưa duyệt)'),
    ('config_proposals.delete', 'Xóa đề xuất cấu hình'),
    ('config_proposals.approve_team', 'Duyệt đề xuất (Trưởng bộ phận)'),
    ('config_proposals.consult_it', 'Tư vấn kỹ thuật (IT)'),
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
                    print("✓ Added device_code column to bug_report table")
            except Exception as e:
                error_msg = str(e).lower()
                # Check if error is because column already exists
                if any(keyword in error_msg for keyword in ['already exists', 'duplicate column', 'column "device_code" of relation "bug_report" already exists']):
                    print("✓ device_code column already exists")
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
                print("✓ Added created_at column to role table")
        except Exception as e:
            msg = str(e).lower()
            if 'already exists' in msg or 'duplicate column' in msg:
                print("✓ created_at column already exists on role table")
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
                    print("✓ Created resource table")
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
                    print("✓ Updated resource table schema (v2)")
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
                print("✓ Created device_type table")
                
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
                    ('Thiết bị mạng', 'Thiết bị IT'),
                    ('Server', 'Thiết bị IT'),
                    ('Ổ điện', 'Thiết bị dùng chung'),
                    ('Dây mạng', 'Thiết bị IT'),
                    ('Cáp kết nối', 'Thiết bị IT'),
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
                
                print("✓ Verified device types seeding")
                
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
                            print(f"✓ Added column {col_name} to config_proposal")
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

# Ensure default admin exists on startup
with app.app_context():
    try:
        if not User.query.filter_by(username='admin').first():
            it_dept = Department.query.filter_by(name='IT').first()
            if not it_dept:
                it_dept = Department(name='IT', description='Phòng Công nghệ Thông tin')
                db.session.add(it_dept)
                db.session.flush()
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
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
            print('Default admin created (username=admin).')
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
    quantity = db.Column(db.Integer, default=1)  # Số lượng bộ thiết bị
    currency = db.Column(db.String(10), default='VND')
    status = db.Column(db.String(30), default='new')  # new, team_approved, it_consulted, finance_reviewed, approved, purchasing, payment_done, goods_received, handed_over, completed, rejected
    purchase_status = db.Column(db.String(30), default='Lấy báo giá')  # Deprecated in favor of workflow status, but kept for legacy
    notes = db.Column(db.Text)
    supplier_info = db.Column(db.Text) # Changed to Text for detailed info
    linked_receipt_id = db.Column(db.Integer, db.ForeignKey('inventory_receipt.id'))
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

    linked_receipt = db.relationship('InventoryReceipt', foreign_keys=[linked_receipt_id])
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_proposals')

class OrderTracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('config_proposal.id'), nullable=False)
    status_content = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.Text)
    updated_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    proposal = db.relationship('ConfigProposal', backref=db.backref('order_logs', lazy=True, cascade="all,delete"))
    updater = db.relationship('User', foreign_keys=[updated_by])

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

# --- Device Hierarchy Configuration ---
DEVICE_HIERARCHY = {
    'Thiết bị IT': ['Laptop', 'PC', 'Server', 'Linh phụ kiện', 'Thiết bị mạng'],
    'Thiết bị văn phòng': ['Máy in', 'Máy chiếu', 'Máy chấm công', 'Camera', 'Điện thoại bàn', 'Thiết bị văn phòng khác'],
    'Thiết bị dùng chung': ['Bàn', 'Ghế', 'Tủ', 'Két sắt', 'Phương tiện đi lại', 'Thiết bị dùng chung khác']
}

def _get_device_type_hierarchy():
    """Helper to return device hierarchy and flattened types"""
    return DEVICE_HIERARCHY

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

# --- Ensure tables exist and run lightweight schema migrations ---
_tables_initialized = False

@app.before_request
def ensure_tables_once():
    global _tables_initialized
    if not _tables_initialized:
        try:
            db.create_all()
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
                        conn.execute(text("CREATE TABLE IF NOT EXISTS server_room_device_info (device_id INTEGER PRIMARY KEY, ip_address VARCHAR(100), services_running TEXT, usage_status VARCHAR(30) DEFAULT 'Đang hoạt động', updated_at DATETIME, FOREIGN KEY(device_id) REFERENCES device(id))"))
                        set_version(1)

                    # Migration 2: add missing columns for inventory and proposals
                    if get_version() < 2:
                        info = conn.execute(text("PRAGMA table_info('inventory_receipt_item')")).fetchall()
                        cols = {row[1] for row in info}
                        if info:
                            if 'quantity' not in cols:
                                conn.execute(text("ALTER TABLE inventory_receipt_item ADD COLUMN quantity INTEGER DEFAULT 1"))
                            if 'device_condition' not in cols:
                                conn.execute(text("ALTER TABLE inventory_receipt_item ADD COLUMN device_condition VARCHAR(100)"))
                            if 'device_note' not in cols:
                                conn.execute(text("ALTER TABLE inventory_receipt_item ADD COLUMN device_note TEXT"))

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
                            if 'linked_receipt_id' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN linked_receipt_id INTEGER"))
                            if 'supplier_info' not in cols2:
                                conn.execute(text("ALTER TABLE config_proposal ADD COLUMN supplier_info VARCHAR(255)"))

                        info3 = conn.execute(text("PRAGMA table_info('inventory_receipt')")).fetchall()
                        cols3 = {row[1] for row in info3}
                        if info3 and 'config_proposal_id' not in cols3:
                            conn.execute(text("ALTER TABLE inventory_receipt ADD COLUMN config_proposal_id INTEGER"))

                        info4 = conn.execute(text("PRAGMA table_info('user')")).fetchall()
                        cols4 = {row[1] for row in info4}
                        if info4 and 'last_name_token' not in cols4:
                            conn.execute(text("ALTER TABLE user ADD COLUMN last_name_token VARCHAR(120)"))

                        info5 = conn.execute(text("PRAGMA table_info('config_proposal_item')")).fetchall()
                        cols5 = {row[1] for row in info5}
                        if info5 and 'product_code' not in cols5:
                            conn.execute(text("ALTER TABLE config_proposal_item ADD COLUMN product_code VARCHAR(100)"))

                        set_version(2)

                    # Migration 3: ensure server_room_device_info.usage_status exists
                    if get_version() < 3:
                        info6 = conn.execute(text("PRAGMA table_info('server_room_device_info')")).fetchall()
                        cols6 = {row[1] for row in info6}
                        if info6 and 'usage_status' not in cols6:
                            conn.execute(text("ALTER TABLE server_room_device_info ADD COLUMN usage_status VARCHAR(30) DEFAULT 'Đang hoạt động'"))
                        set_version(3)

                    conn.commit()
            except Exception:
                # Migration failures should not break app startup
                pass
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
        return dict(current_user=current_user, current_permissions=perm_codes)
    return dict(current_user=None, current_permissions=set())

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
        'Đề xuất cấu hình': ['config_proposals.view', 'config_proposals.edit', 'config_proposals.delete'],
        'Báo lỗi': ['bug_reports.create', 'bug_reports.view', 'bug_reports.edit', 'bug_reports.delete', 'bug_reports.assign'],
        'Người dùng': ['users.view', 'users.edit', 'users.delete'],
        'Phòng ban': ['departments.view', 'departments.edit', 'departments.delete'],
        'Dashboard': ['dashboard.view'],
        'Backup': ['backup.view', 'backup.edit', 'backup.delete'],
        'Phân quyền': ['rbac.view', 'rbac.edit', 'rbac.delete', 'rbac.manage'],
        'Bảo trì': ['maintenance.view', 'maintenance.add', 'maintenance.edit', 'maintenance.delete', 'maintenance.upload', 'maintenance.download'],
        'Bảo trì': ['maintenance.view', 'maintenance.add', 'maintenance.edit', 'maintenance.delete', 'maintenance.upload', 'maintenance.download'],
        'Báo lỗi nâng cao': ['bug_reports.manage_advanced'],
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
        'Đề xuất cấu hình': ['config_proposals.view', 'config_proposals.edit', 'config_proposals.delete'],
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
        ~User.status.in_(['Đã nghỉ', 'Nghỉ việc'])
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
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            # Kiểm tra trạng thái người dùng
            if user.status in ['Đã nghỉ', 'Nghỉ việc']:
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
        
    department = Department.query.get_or_404(id)
    available_users = User.query.filter(
        User.status.in_(['Đang làm', 'Thực tập']),
        User.department_id.is_(None)
    ).all()
    
    # Filter users currently in the department
    department_users = [u for u in department.users if u.status in ['Đang làm', 'Thực tập']]
    
    return render_template('departments/users.html',
                         department=department,
                         available_users=available_users,
                         department_users=department_users)

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
    per_page = request.args.get('per_page', 10, type=int)
    # Load current filters from query params or session-saved defaults
    saved_filters = session.get('devices_filters', {}) or {}
    filter_device_code = request.args.get('filter_device_code')
    filter_name = request.args.get('filter_name')
    filter_device_type = request.args.get('filter_device_type')
    filter_status = request.args.get('filter_status')
    filter_manager_id = request.args.get('filter_manager_id')
    filter_department = request.args.get('filter_department')
    filter_category = request.args.get('filter_category') # New Category Filter

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
    if filter_category is None or filter_category == '':
        filter_category = saved_filters.get('filter_category', '')
    
    query = Device.query

    # Apply category filter
    if filter_category and filter_category in DEVICE_HIERARCHY:
        category_types = DEVICE_HIERARCHY[filter_category]
        if filter_device_type:
             if filter_device_type in category_types:
                 query = query.filter(Device.device_type == filter_device_type)
             else:
                 query = query.filter(text('1=0'))
        else:
            query = query.filter(Device.device_type.in_(category_types))
    elif filter_device_type:
         query = query.filter(Device.device_type == filter_device_type)
    
    # Apply permission-based filtering
    # Admin or users with full devices.view permission see all devices
    is_admin = user.role == 'admin' if user else False
    has_full_view = 'devices.view' in current_permissions and is_admin
    
    if not has_full_view:
        # Personal accounts: only see devices they manage
        if user and user.department_id:
            dept = Department.query.get(user.department_id)
            if dept and dept.manager_id == user_id:
                # Manager: see devices of people in their department and sub-departments
                dept_ids = get_subordinate_department_ids(dept.id)
                # Get all users in these departments
                dept_user_ids = [u.id for u in User.query.filter(User.department_id.in_(dept_ids)).all()]
                dept_user_ids.append(user_id)  # Include self
                query = query.filter(Device.manager_id.in_(dept_user_ids))
            else:
                # Regular user: only see devices they manage
                query = query.filter(Device.manager_id == user_id)
        else:
            # No department: only see own devices
            query = query.filter(Device.manager_id == user_id)
    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_name:
        query = query.filter(Device.name.ilike(f'%{filter_name}%'))
    # filter_device_type handled above
    if filter_status:
        query = query.filter_by(status=filter_status)
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
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng', 'Thanh lý', 'Test', 'Mượn']
    user_query = User.query.order_by(func.lower(User.last_name_token), func.lower(User.full_name), func.lower(User.username))
    if user and user.role != 'admin':
        if user.department_id:
            user_query = user_query.filter(User.department_id == user.department_id)
        else:
            user_query = user_query.filter(User.id == user_id)
    users = user_query.all()
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
        filter_status=filter_status,
        filter_manager_id=filter_manager_id,
        filter_department=filter_department,
        filter_category=filter_category,
        device_hierarchy=DEVICE_HIERARCHY,
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
        if dept and dept.manager:
            receiver_user = dept.manager
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
    current_permissions = _get_current_permissions()
    return render_template('device_detail.html', device=device, handovers=handovers, current_permissions=current_permissions)

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
    filter_code = request.args.get('filter_code', '').strip()
    filter_supplier = request.args.get('filter_supplier', '').strip()
    filter_importer = request.args.get('filter_importer', '').strip()
    filter_notes = request.args.get('filter_notes', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # Build query with filters
    query = InventoryReceipt.query
    
    if filter_code:
        query = query.filter(InventoryReceipt.code.ilike(f'%{filter_code}%'))
    if filter_supplier:
        query = query.filter(InventoryReceipt.supplier.ilike(f'%{filter_supplier}%'))
    if filter_importer:
        query = query.filter(InventoryReceipt.importer.ilike(f'%{filter_importer}%'))
    if filter_notes:
        query = query.filter(InventoryReceipt.notes.ilike(f'%{filter_notes}%'))
    
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
                         filter_code=filter_code,
                         filter_supplier=filter_supplier,
                         filter_importer=filter_importer,
                         filter_notes=filter_notes,
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

        # Get selected device IDs from form (supports comma-separated single field and multiple inputs)
        raw_values = request.form.getlist('device_ids')
        selected_device_ids = set()
        for raw in raw_values:
            if not raw:
                continue
            for token in str(raw).split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    selected_device_ids.add(int(token))
                except ValueError:
                    continue

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
    db.session.delete(handover)
    db.session.commit()
    flash('Xóa phiếu bàn giao thành công!', 'success')
    return redirect(url_for('handover_list'))

# Xem chi tiết một phiếu bàn giao
@app.route('/handover/<int:handover_id>')
def handover_detail(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover = DeviceHandover.query.get_or_404(handover_id)
    device = handover.device
    giver = handover.giver
    receiver = handover.receiver
    return render_template('handover_detail.html', handover=handover, device=device, giver=giver, receiver=receiver)

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
        
        # Xóa hết các quyền UserRole cũ (nếu có - dù mới tạo thì chưa có nhưng để chắc chắn)
        # Hệ thống mới chỉ dùng user.role check
        
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
        
        # Tạo phiếu trả thiết bị cho từng thiết bị (vì mỗi handover chỉ handle 1 device)
        for device in devices:
            return_handover = DeviceHandover(
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
    # Lịch sử bàn giao liên quan
    given = DeviceHandover.query.filter_by(giver_id=user.id).order_by(DeviceHandover.handover_date.desc()).all()
    received = DeviceHandover.query.filter_by(receiver_id=user.id).order_by(DeviceHandover.handover_date.desc()).all()
    current_permissions = _get_current_permissions()
    return render_template('user_detail.html', user=user, devices=devices, given=given, received=received, current_permissions=current_permissions)

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
                    device_code=_s(row['Mã thiết bị']),
                    name=_s(row['Tên thiết bị']),
                    device_type=_s(row['Loại thiết bị']),
                    serial_number=_s(row.get('Số serial')),
                    purchase_date=purchase_date,
                    import_date=import_date,
                    condition=_s(row['Tình trạng']),
                    status=_s(row['Trạng thái']),
                    manager_id=manager_id,
                    assign_date=assign_date,
                    configuration=_s(row.get('Cấu hình')),
                    notes=_s(row.get('Ghi chú')),
                    buyer=_s(row.get('Người mua')),
                    importer=_s(row.get('Người nhập')),
                    brand=_s(row.get('Thương hiệu')),
                    supplier=_s(row.get('Nhà cung cấp')),
                    warranty=_s(row.get('Bảo hành')),
                    purchase_price=price
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
    system_admin = current_user.role == 'admin' if current_user else False
    can_manage = system_admin or ('bug_reports.manage_advanced' in current_permissions)
    base_perms = {'bug_reports.view', 'bug_reports.edit', 'bug_reports.assign'}
    can_view_all = can_manage or any(perm in current_permissions for perm in base_perms)
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
        created_at_local = _to_vietnam_time(user.created_at)
        last_login_local = _to_vietnam_time(user.last_login)
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
    users = User.query.filter(User.status.notin_(['Đã nghỉ', 'Nghỉ việc'])).order_by(User.full_name).all()
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
    # Admin/quyền mở rộng hoặc người tạo hoặc người được gán, hoặc báo lỗi công khai
    can_view = can_view_all_reports or is_creator or is_assignee or bug_report.visibility == 'public'
    if not can_view:
        flash('Bạn không có quyền xem báo lỗi này.', 'danger')
        return redirect(url_for('bug_reports'))
    
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
    
    # Người dùng thường chỉ thấy báo lỗi công khai, báo lỗi mình tạo, hoặc được gán
    # Admin và người có quyền xem tất cả thì thấy tất cả
    if can_view_all_reports:
        q = BugReport.query.filter(BugReport.merged_into.is_(None))  # Không hiển thị báo lỗi đã được gộp
    else:
        q = BugReport.query.filter(
            BugReport.merged_into.is_(None),  # Không hiển thị báo lỗi đã được gộp
            or_(
                BugReport.visibility == 'public',      # Báo lỗi công khai
                BugReport.created_by == user_id,       # Báo lỗi do chính mình tạo
                BugReport.assigned_to == user_id       # Báo lỗi được gán cho mình
            )
        )
    
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
    
    # Get list of users who created reports (for filter dropdown)
    creators = db.session.query(User).join(BugReport, User.id == BugReport.created_by).distinct().order_by(User.full_name, User.username).all()
    
    # Get list of users who are assigned reports
    assignees = db.session.query(User).join(BugReport, User.id == BugReport.assigned_to).distinct().order_by(User.full_name, User.username).all()

    # Get list of distinct device codes in reports (simple parsing or just rough list)
    # Since device_code is text and can be comma separated, getting distinct values is tricky. 
    # For simplicity, we fetch all non-empty device_code strings and split them python-side or just show distinct raw values.
    # A better approach given the comma separation: display distinct raw strings or improve this later.
    # Let's try to extract unique codes if possible, but for MVP standard distinct on the column is safest if single codes.
    # If they are comma separated "Code1, Code2", they will appear as such in the filter list.
    # Users can search via the filter text input if we change it to text later, but for now dropdown.
    # We will get all texts and split them in python to list unique codes.
    all_report_codes = db.session.query(BugReport.device_code).filter(BugReport.device_code != None, BugReport.device_code != '').all()
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
    
    # Chỉ hiển thị thiết bị được gán cho user (trừ khi là admin)
    if can_view_all_reports:
        devices = Device.query.order_by(Device.device_code).all()
    else:
        devices = Device.query.filter_by(manager_id=user_id).order_by(Device.device_code).all()
    
    # Get list of users for "báo lỗi hộ" feature - Allow selecting any active user
    reportable_users = User.query.filter(~User.status.in_(['Đã nghỉ', 'Nghỉ việc', 'Resigned', 'Retired'])).order_by(User.full_name, User.username).all()
 
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
                if any(u.id == report_for_id for u in reportable_users):
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
        if created_by_id != user_id and can_view_all_reports:
            # Admin can see all devices when reporting for someone
            devices = Device.query.order_by(Device.device_code).all()
        elif created_by_id != user_id:
            # Show devices assigned to the person being reported for
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
    # Lấy danh sách nhân viên để gán
    # Admin: tất cả nhân viên
    # Người khác: chỉ nhân viên trong phòng ban của mình và các phòng ban con
    user = current_user
    employees = []
    if can_view_all_reports:
        employees = User.query.order_by(User.full_name, User.username).all()
    elif user.department_id and ('bug_reports.assign' in current_permissions):
        dept = Department.query.get(user.department_id)
        if dept:
            dept_ids = get_subordinate_department_ids(dept.id)
            dept_ids.append(dept.id)  # Include own department
            employees = User.query.filter(User.department_id.in_(dept_ids)).order_by(User.full_name, User.username).all()
    
    is_closed = bug_report.status == 'Đã đóng'
    can_comment = (not is_closed) and (bug_report.is_public or can_view_all_reports or is_creator or is_assignee)
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
        available_reports = BugReport.query.filter(
            BugReport.id != report_id,
            BugReport.merged_into.is_(None)
        ).order_by(BugReport.created_at.desc()).limit(100).all()

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
            devices = Device.query.order_by(Device.device_code).all()
            return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices, 
                                 selected_device_codes=deduped_codes, selected_visibility=visibility, 
                                 selected_priority=priority, draft_title=title, draft_description=description)
        
        if len(title) > 100:
            flash('Tiêu đề không được vượt quá 100 ký tự.', 'danger')
            devices = Device.query.order_by(Device.device_code).all()
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
            devices = Device.query.order_by(Device.device_code).all()
            return render_template('bug_reports/edit.html', bug_report=bug_report, devices=devices,
                                 selected_device_codes=deduped_codes, selected_visibility=visibility,
                                 selected_priority=priority, draft_title=title, draft_description=description)
    
    # GET request - show edit form
    devices = Device.query.order_by(Device.device_code).all()
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
        flash('Đã cập nhật báo lỗi thành công.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi cập nhật: {str(e)}', 'danger')
    
    return redirect(url_for('bug_report_detail', report_id=report_id))

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
    _, can_view_all_reports = _bug_permission_flags(current_permissions, current_user)

    if bug_report.status == 'Đã đóng':
        flash('Vấn đề đã đóng. Vui lòng gửi yêu cầu mở lại để tiếp tục trao đổi.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    if not (bug_report.is_public or is_creator or can_view_all_reports or is_assignee):
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
    current_permissions = _get_current_permissions()
    is_creator = bug_report.created_by == user_id
    is_assignee = bug_report.assigned_to == user_id if bug_report.assigned_to else False
    can_manage_bug_reports, can_view_all_reports = _bug_permission_flags(current_permissions, User.query.get(user_id))

    if bug_report.status == 'Đã đóng':
        flash('Vấn đề đã đóng. Không thể tải thêm tệp đính kèm.', 'danger')
        return redirect(url_for('bug_report_detail', report_id=report_id))

    if not (can_manage_bug_reports or is_creator or is_assignee):
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
    current_permissions = _get_current_permissions()
    is_creator = bug_report.created_by == user_id
    is_assignee = bug_report.assigned_to == user_id if bug_report.assigned_to else False
    _, can_view_all_reports = _bug_permission_flags(current_permissions, User.query.get(user_id))

    if not (can_view_all_reports or is_creator or is_assignee or bug_report.is_public):
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
        data.append({'Ngày Bàn Giao': handover.handover_date.strftime('%d-%m-%Y'), 'Mã Thiết Bị': handover.device.device_code if handover.device else '', 'Tên Thiết Bị': handover.device.name if handover.device else '', 'Loại Thiết Bị': handover.device_type if handover.device else '', 'Người Giao': handover.giver.full_name if handover.giver else '', 'Người Nhận': handover.receiver.full_name if handover.receiver else '', 'Phòng ban Người Nhận': (handover.receiver.department_info.name if handover.receiver and handover.receiver.department_info else ''), 'Tình Trạng Thiết Bị': handover.device_condition, 'Lý Do': handover.reason, 'Nơi Đặt': handover.location, 'Ghi Chú': handover.notes})
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
    
    q = ConfigProposal.query
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
    proposers = [r.proposer_name for r in db.session.query(ConfigProposal.proposer_name).distinct().filter(ConfigProposal.proposer_name != None).order_by(ConfigProposal.proposer_name).all()]
    units = [r.proposer_unit for r in db.session.query(ConfigProposal.proposer_unit).distinct().filter(ConfigProposal.proposer_unit != None).order_by(ConfigProposal.proposer_unit).all()]
    statuses = [r.status for r in db.session.query(ConfigProposal.status).distinct().filter(ConfigProposal.status != None).order_by(ConfigProposal.status).all()]

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
                # status=status argument removed to favor default 'new' below or use explicit 'new'
                # purchase_status removed
                notes=notes,
                # supplier_info removed
                quantity=request.form.get('quantity', type=int) or 1,
                created_by=session['user_id'],
                status='new',
                current_stage_deadline=datetime.utcnow() + timedelta(days=1), # SLA for Team Lead
                general_requirements=request.form.get('general_requirements'),
                required_date=datetime.strptime(request.form.get('required_date'), '%Y-%m-%d').date() if request.form.get('required_date') else None
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
            # Calculate total based on quantity
            grand_subtotal = subtotal * proposal.quantity
            proposal.vat_amount = round(grand_subtotal * (vat_percent / 100.0), 2)
            proposal.total_amount = round(grand_subtotal + proposal.vat_amount, 2)
            db.session.commit()
            flash('Tạo đề xuất cấu hình thiết bị thành công.', 'success')
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
    dept_users = []
    if current_user.role == 'Admin':
        dept_users = User.query.all()
    elif current_user.department_id:
        dept_users = User.query.filter_by(department_id=current_user.department_id).all()
    else:
        dept_users = [current_user] # Fallback
        
    return render_template('add_config_proposal.html', default_date=default_date, users=dept_users, current_user=current_user)

@app.route('/config_proposals/<int:proposal_id>/action', methods=['POST'])
def proposal_action(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    current_user = User.query.get(session['user_id'])
    permissions = _get_current_permissions()
    
    action = request.form.get('action')
    note = request.form.get('note')
    
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
                flash('Bạn không có quyền tư vấn kỹ thuật.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            supplier_info = request.form.get('supplier_info')
            if supplier_info:
                p.supplier_info = supplier_info
            
            p.status = 'it_consulted'
            p.it_consultant_id = current_user.id
            p.it_consulted_at = datetime.utcnow()
            p.it_consultation_note = note
            p.current_stage_deadline = get_deadline(2) # SLA for Finance: 48h
            flash('Đã hoàn thành tư vấn kỹ thuật. Chuyển sang Tài chính.', 'success')

        elif action == 'review_finance':
            if 'config_proposals.review_finance' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền kiểm tra ngân sách.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            p.status = 'finance_reviewed'
            p.finance_reviewer_id = current_user.id
            p.finance_reviewed_at = datetime.utcnow()
            p.finance_review_note = note
            p.current_stage_deadline = get_deadline(2) # SLA for Director: 48h
            flash('Đã xác nhận ngân sách. Chuyển Giám đốc phê duyệt.', 'success')

        elif action == 'approve_director':
            if 'config_proposals.approve_director' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền phê duyệt.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            p.status = 'approved'
            p.director_approver_id = current_user.id
            p.director_approved_at = datetime.utcnow()
            p.director_approval_note = note
            # Finalize deadlines? Or set next deadline? 
            # Since it's a checklist now, deadlines are trickier. Let's set a general "Completion" deadline?
            p.current_stage_deadline = get_deadline(14) # ~2 weeks for full purchasing process
            flash('Đã phê duyệt đề xuất. Các bộ phận liên quan vui lòng thực hiện checklist mua sắm.', 'success')

        # --- Post-Approval Checklist Actions ---
        # Any of these can happen if status is 'approved'.
        
        elif action == 'start_purchasing':
             if 'config_proposals.execute_purchase' not in permissions and current_user.role != 'admin':
                flash('Bạn không có quyền thực hiện mua sắm.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
             
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
             
             p.accountant_invoice_id = current_user.id
             p.invoice_received_at = datetime.utcnow()
             
             if p.purchasing_at and p.payment_at and p.goods_received_at and p.handover_to_user_at and p.invoice_received_at:
                 p.status = 'completed'
                 flash('Đã nhận hóa đơn. Quy trình hoàn tất!', 'success')
             else:
                 flash('Đã nhận hóa đơn.', 'success')

        elif action == 'reject':
            # Simplified reject logic
            can_reject = False
            if p.status == 'new' and (is_manager or 'config_proposals.approve_team' in permissions): can_reject = True
            elif p.status == 'team_approved' and ('config_proposals.consult_it' in permissions): can_reject = True
            elif p.status == 'it_consulted' and ('config_proposals.review_finance' in permissions): can_reject = True
            elif p.status == 'finance_reviewed' and ('config_proposals.approve_director' in permissions): can_reject = True
            elif current_user.role == 'admin': can_reject = True
            
            if not can_reject:
                flash('Bạn không có quyền từ chối ở bước này.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))

            p.status = 'rejected'
            p.rejection_reason = note
            flash(f'Đã từ chối đề xuất. Lý do: {note}', 'warning')

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi xử lý: {str(e)}', 'danger')

    return redirect(url_for('config_proposal_detail', proposal_id=p.id))

@app.route('/config_proposals/<int:proposal_id>')
def config_proposal_detail(proposal_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    p = ConfigProposal.query.get_or_404(proposal_id)
    items = ConfigProposalItem.query.filter_by(proposal_id=proposal_id).order_by(ConfigProposalItem.order_no).all()
    p = ConfigProposal.query.get_or_404(proposal_id)
    items = ConfigProposalItem.query.filter_by(proposal_id=proposal_id).order_by(ConfigProposalItem.order_no).all()
    logs = OrderTracking.query.filter_by(proposal_id=proposal_id).order_by(OrderTracking.updated_at.desc()).all()
    return render_template('config_proposal_detail.html', p=p, items=items, logs=logs, current_permissions=_get_current_permissions())

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
    
    if current_user.role == 'Admin':
        dept_users = User.query.all()
    else:
        target_dept_id = current_user.department_id
        if p.creator and p.creator.department_id:
            target_dept_id = p.creator.department_id
        
        if target_dept_id:
            dept_users = User.query.filter_by(department_id=target_dept_id).all()
        else:
            dept_users = [current_user]

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
            p.proposer_name = request.form.get('proposer_name')
            p.proposer_unit = request.form.get('proposer_unit')
            p.scope = request.form.get('scope')
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
            grand_subtotal = subtotal * p.quantity
            p.vat_amount = round(grand_subtotal * (p.vat_percent / 100.0), 2)
            p.total_amount = round(grand_subtotal + p.vat_amount, 2)
            db.session.commit()
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
    
    admin_user = User(
        username='admin',
        password=generate_password_hash(os.environ.get('ADMIN_PASSWORD', 'admin123')),
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
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    # Kiểm tra phân quyền: chỉ admin hoặc người có quyền backup.view mới được truy cập
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    return render_template('backup.html')

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
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_backup_file = temp_file.name
        temp_file.close()

        # Use shared backup logic
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
        download_filename = f'backup_inventory_{timestamp}.zip'

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
            mimetype='application/zip'
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
    
    if not file.filename.endswith('.zip'):
        flash('File backup phải có định dạng .zip', 'danger')
        return redirect(url_for('backup_page'))
    
    try:
        # Lưu file tạm
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_path = temp_file.name
        temp_file.close()
        
        file.save(temp_path)
        
        # Use shared backup logic for restore
        backup = DatabaseBackup()
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

# --- Automatic Backup Functions ---
def create_automatic_backup():
    """Tạo backup tự động"""
    try:
        # Sử dụng thời gian GMT+7
        vietnam_time = datetime.now(VIETNAM_TZ)
        timestamp = vietnam_time.strftime('%Y%m%d_%H%M%S')
        backup_filename = f'auto_backup_{timestamp}.zip'
        backup_filepath = os.path.join(backup_path, backup_filename)
        
        # Use shared backup logic
        backup = DatabaseBackup()
        
        # Create backup in the backups directory
        backup_filename = f'auto_backup_{timestamp}.zip'
        backup_filepath = os.path.join(backup_path, backup_filename)
        
        final_path = backup.create_backup(backup_filepath)
        
        if final_path and os.path.exists(final_path):
            file_size = os.path.getsize(final_path)
            print(f"Automatic backup created: {backup_filename} ({file_size} bytes)")
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

@app.route('/backup/list')
def backup_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    
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
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.view' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    
    try:
        backup_filepath = os.path.join(backup_path, filename)
        if not os.path.exists(backup_filepath):
            flash('File backup không tồn tại.', 'danger')
            return redirect(url_for('backup_list'))
        
        # Use shared backup logic
        backup = DatabaseBackup()
        success = backup.restore_backup(backup_filepath)
        
        if success:
            flash(f'Khôi phục backup từ {filename} thành công!', 'success')
        else:
            flash(f'Lỗi khi khôi phục backup: Xem log để biết chi tiết', 'danger')
        return redirect(url_for('backup_list'))
        
    except Exception as e:
        flash(f'Lỗi khi khôi phục backup: {str(e)}', 'danger')
        return redirect(url_for('backup_list'))

@app.route('/backup/delete/<filename>', methods=['POST'])
def backup_delete(filename):
    if 'user_id' not in session: return redirect(url_for('login'))
    current_permissions = _get_current_permissions()
    current_user = _get_current_user()
    if not (current_user and current_user.role == 'admin') and 'backup.delete' not in current_permissions:
        flash('Bạn không có quyền truy cập chức năng này.', 'danger')
        return redirect(url_for('home'))
    
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
    grouped_types = {}
    for t in types:
        if t.category not in grouped_types:
            grouped_types[t.category] = []
        grouped_types[t.category].append(t)
        
    return render_template('device_types/list.html', grouped_types=grouped_types)

@app.route('/device_types/add', methods=['GET', 'POST'])
def add_device_type():
    if 'user_id' not in session: return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền thêm loại thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))
        
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        
        if not name or not category:
            flash('Tên và nhóm thiết bị là bắt buộc.', 'danger')
        elif DeviceType.query.filter_by(name=name).first():
            flash('Loại thiết bị đã tồn tại.', 'warning')
        else:
            try:
                dt = DeviceType(name=name, category=category, description=description)
                db.session.add(dt)
                db.session.commit()
                flash('Đã thêm loại thiết bị mới.', 'success')
                return redirect(url_for('device_type_list'))
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi: {str(e)}', 'danger')
                
    return render_template('device_types/form.html', device_type=None)

@app.route('/device_types/<int:id>/edit', methods=['GET', 'POST'])
def edit_device_type(id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if (session.get('role') != 'admin') and ('devices.edit' not in _get_current_permissions()):
        flash('Bạn không có quyền sửa loại thiết bị.', 'danger')
        return redirect(url_for('device_type_list'))
        
    dt = DeviceType.query.get_or_404(id)
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        
        if not name or not category:
            flash('Tên và nhóm thiết bị là bắt buộc.', 'danger')
        elif name != dt.name and DeviceType.query.filter_by(name=name).first():
            flash('Tên loại thiết bị đã tồn tại.', 'warning')
        else:
            try:
                dt.name = name
                dt.category = category
                dt.description = description
                db.session.commit()
                flash('Đã cập nhật loại thiết bị.', 'success')
                return redirect(url_for('device_type_list'))
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi: {str(e)}', 'danger')
                
    return render_template('device_types/form.html', device_type=dt)

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

if __name__ == '__main__':
    app.run(debug=True)