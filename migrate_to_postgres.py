#!/usr/bin/env python3
"""
Migration script from SQLite to PostgreSQL
This script migrates data from SQLite database to PostgreSQL
"""
import os
import sys
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse
from datetime import datetime
import json

def get_sqlite_connection():
    """Get SQLite connection"""
    sqlite_path = os.path.join(os.getcwd(), 'instance', 'inventory.db')
    if not os.path.exists(sqlite_path):
        print(f"SQLite database not found at {sqlite_path}")
        return None
    return sqlite3.connect(sqlite_path)

def get_postgres_connection(database_url):
    """Get PostgreSQL connection"""
    try:
        parsed = urlparse(database_url)
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            database=parsed.path[1:],  # Remove leading slash
            user=parsed.username,
            password=parsed.password
        )
        return conn
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return None

def migrate_table(sqlite_conn, postgres_conn, table_name, columns):
    """Migrate a single table from SQLite to PostgreSQL"""
    print(f"Migrating table: {table_name}")
    
    # Get data from SQLite
    cursor_sqlite = sqlite_conn.cursor()
    cursor_sqlite.execute(f"SELECT * FROM {table_name}")
    rows = cursor_sqlite.fetchall()
    
    if not rows:
        print(f"  No data in {table_name}")
        return True
    
    # Insert data into PostgreSQL
    cursor_postgres = postgres_conn.cursor()
    
    # Create placeholders for INSERT statement
    placeholders = ', '.join(['%s'] * len(columns))
    columns_str = ', '.join(columns)
    
    insert_sql = f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders})"
    
    try:
        cursor_postgres.executemany(insert_sql, rows)
        postgres_conn.commit()
        print(f"  Migrated {len(rows)} rows to {table_name}")
        return True
    except Exception as e:
        print(f"  Error migrating {table_name}: {e}")
        postgres_conn.rollback()
        return False

def migrate_database():
    """Main migration function"""
    print("Starting database migration from SQLite to PostgreSQL...")
    
    # Get database URLs
    sqlite_conn = get_sqlite_connection()
    if not sqlite_conn:
        return False
    
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("DATABASE_URL environment variable not set")
        return False
    
    postgres_conn = get_postgres_connection(database_url)
    if not postgres_conn:
        return False
    
    try:
        # Define table migration order (respecting foreign key constraints)
        tables_to_migrate = [
            ('department', ['id', 'name', 'description', 'parent_id', 'order_index', 'created_at', 'updated_at', 'manager_id']),
            ('user', ['id', 'username', 'password', 'full_name', 'last_name_token', 'email', 'role', 'department_id', 'created_at', 'last_login', 'position', 'date_of_birth', 'phone_number', 'notes', 'status', 'onboard_date', 'offboard_date']),
            ('device', ['id', 'device_code', 'name', 'device_type', 'serial_number', 'purchase_date', 'import_date', 'condition', 'status', 'manager_id', 'assign_date', 'configuration', 'notes', 'created_at', 'buyer', 'importer', 'brand', 'supplier', 'warranty', 'purchase_price']),
            ('device_maintenance_log', ['id', 'device_id', 'log_date', 'condition', 'issue', 'status', 'last_action', 'notes', 'created_at']),
            ('device_maintenance_attachment', ['id', 'log_id', 'file_name', 'file_path', 'uploaded_at']),
            ('role', ['id', 'name', 'description']),
            ('permission', ['id', 'code', 'name']),
            ('role_permission', ['role_id', 'permission_id']),
            ('user_role', ['user_id', 'role_id', 'role']),
            ('device_handover', ['id', 'handover_date', 'device_id', 'giver_id', 'receiver_id', 'device_condition', 'reason', 'location', 'notes']),
            ('device_group', ['id', 'name', 'description', 'notes', 'created_by', 'created_at', 'updated_at']),
            ('device_group_device', ['group_id', 'device_id', 'created_at']),
            ('user_device_group', ['group_id', 'user_id', 'role', 'created_at']),
            ('server_room_device_info', ['device_id', 'ip_address', 'services_running', 'usage_status', 'department', 'updated_at']),
            ('inventory_receipt', ['id', 'code', 'date', 'supplier', 'importer', 'created_by', 'notes', 'created_at', 'config_proposal_id']),
            ('inventory_receipt_item', ['id', 'receipt_id', 'device_id', 'quantity', 'device_condition', 'device_note']),
            ('config_proposal', ['id', 'name', 'proposal_date', 'proposer_name', 'proposer_unit', 'scope', 'currency', 'status', 'purchase_status', 'notes', 'supplier_info', 'linked_receipt_id', 'subtotal', 'vat_percent', 'vat_amount', 'total_amount', 'created_at']),
            ('config_proposal_item', ['id', 'proposal_id', 'order_no', 'product_name', 'product_link', 'warranty', 'product_code', 'quantity', 'unit_price', 'line_total']),
            ('audit_log', ['id', 'entity_type', 'entity_id', 'changed_by', 'changed_at', 'changes'])
        ]
        
        # Migrate each table
        success_count = 0
        for table_name, columns in tables_to_migrate:
            if migrate_table(sqlite_conn, postgres_conn, table_name, columns):
                success_count += 1
            else:
                print(f"Failed to migrate {table_name}")
        
        print(f"\nMigration completed: {success_count}/{len(tables_to_migrate)} tables migrated successfully")
        
        # Update sequences for auto-increment fields
        print("Updating sequences...")
        cursor_postgres = postgres_conn.cursor()
        
        sequence_updates = [
            "SELECT setval('department_id_seq', (SELECT MAX(id) FROM department))",
            "SELECT setval('user_id_seq', (SELECT MAX(id) FROM user))",
            "SELECT setval('device_id_seq', (SELECT MAX(id) FROM device))",
            "SELECT setval('device_maintenance_log_id_seq', (SELECT MAX(id) FROM device_maintenance_log))",
            "SELECT setval('device_maintenance_attachment_id_seq', (SELECT MAX(id) FROM device_maintenance_attachment))",
            "SELECT setval('role_id_seq', (SELECT MAX(id) FROM role))",
            "SELECT setval('permission_id_seq', (SELECT MAX(id) FROM permission))",
            "SELECT setval('device_handover_id_seq', (SELECT MAX(id) FROM device_handover))",
            "SELECT setval('device_group_id_seq', (SELECT MAX(id) FROM device_group))",
            "SELECT setval('inventory_receipt_id_seq', (SELECT MAX(id) FROM inventory_receipt))",
            "SELECT setval('inventory_receipt_item_id_seq', (SELECT MAX(id) FROM inventory_receipt_item))",
            "SELECT setval('config_proposal_id_seq', (SELECT MAX(id) FROM config_proposal))",
            "SELECT setval('config_proposal_item_id_seq', (SELECT MAX(id) FROM config_proposal_item))",
            "SELECT setval('audit_log_id_seq', (SELECT MAX(id) FROM audit_log))"
        ]
        
        for update_sql in sequence_updates:
            try:
                cursor_postgres.execute(update_sql)
            except Exception as e:
                print(f"  Warning: Could not update sequence: {e}")
        
        postgres_conn.commit()
        print("Sequences updated successfully")
        
        return success_count == len(tables_to_migrate)
        
    except Exception as e:
        print(f"Migration failed: {e}")
        return False
    finally:
        sqlite_conn.close()
        postgres_conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--confirm':
        success = migrate_database()
        if success:
            print("\n✅ Migration completed successfully!")
            print("You can now update your DATABASE_URL to use PostgreSQL")
        else:
            print("\n❌ Migration failed!")
            sys.exit(1)
    else:
        print("This script will migrate your SQLite database to PostgreSQL.")
        print("Make sure you have:")
        print("1. Set DATABASE_URL environment variable to your PostgreSQL connection")
        print("2. Created the PostgreSQL database and tables (run: python3 init_database.py)")
        print("3. Backed up your current data")
        print("\nTo proceed, run: python3 migrate_to_postgres.py --confirm")