#!/usr/bin/env python3
"""
PostgreSQL setup script for inventory management system
Creates database and user, sets up tables
"""
import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from urllib.parse import urlparse
import subprocess

def create_database_and_user(host, port, admin_user, admin_password, db_name, db_user, db_password):
    """Create database and user for the application"""
    try:
        # Connect as admin user
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=admin_user,
            password=admin_password
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Create database
        print(f"Creating database: {db_name}")
        cursor.execute(f"DROP DATABASE IF EXISTS {db_name}")
        cursor.execute(f"CREATE DATABASE {db_name}")
        
        # Create user
        print(f"Creating user: {db_user}")
        cursor.execute(f"DROP USER IF EXISTS {db_user}")
        cursor.execute(f"CREATE USER {db_user} WITH PASSWORD '{db_password}'")
        
        # Grant privileges
        print("Granting privileges...")
        cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}")
        cursor.execute(f"GRANT ALL PRIVILEGES ON SCHEMA public TO {db_user}")
        
        cursor.close()
        conn.close()
        
        print("✅ Database and user created successfully")
        return True
        
    except Exception as e:
        print(f"❌ Error creating database and user: {e}")
        return False

def setup_tables(database_url):
    """Setup tables using Flask app"""
    try:
        # Set environment variable
        os.environ['DATABASE_URL'] = database_url
        
        # Import and run Flask app setup
        sys.path.append(os.getcwd())
        from app import app, db
        
        with app.app_context():
            print("Creating tables...")
            db.create_all()
            print("✅ Tables created successfully")
            
            # Seed initial data
            from app import seed_rbac_data
            seed_rbac_data()
            print("✅ Initial data seeded")
            
        return True
        
    except Exception as e:
        print(f"❌ Error setting up tables: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_connection(database_url):
    """Test database connection"""
    try:
        parsed = urlparse(database_url)
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            database=parsed.path[1:],
            user=parsed.username,
            password=parsed.password
        )
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        print(f"✅ Connection successful. PostgreSQL version: {version}")
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def main():
    print("PostgreSQL Setup for Inventory Management System")
    print("=" * 50)
    
    # Get configuration from user
    print("\nPlease provide PostgreSQL configuration:")
    
    host = input("PostgreSQL host [localhost]: ").strip() or "localhost"
    port = input("PostgreSQL port [5432]: ").strip() or "5432"
    admin_user = input("Admin username [postgres]: ").strip() or "postgres"
    admin_password = input("Admin password: ").strip()
    
    if not admin_password:
        print("❌ Admin password is required")
        sys.exit(1)
    
    db_name = input("Database name [inventory_db]: ").strip() or "inventory_db"
    db_user = input("Application username [inventory_user]: ").strip() or "inventory_user"
    db_password = input("Application password: ").strip()
    
    if not db_password:
        print("❌ Application password is required")
        sys.exit(1)
    
    # Create database and user
    if not create_database_and_user(host, port, admin_user, admin_password, db_name, db_user, db_password):
        sys.exit(1)
    
    # Create database URL
    database_url = f"postgresql://{db_user}:{db_password}@{host}:{port}/{db_name}"
    
    # Test connection
    if not test_connection(database_url):
        sys.exit(1)
    
    # Setup tables
    if not setup_tables(database_url):
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("✅ PostgreSQL setup completed successfully!")
    print("\nNext steps:")
    print(f"1. Set DATABASE_URL environment variable:")
    print(f"   export DATABASE_URL='{database_url}'")
    print("2. Update your .env file with the DATABASE_URL")
    print("3. Restart your application")
    print("\nTo migrate existing data:")
    print("   python3 migrate_to_postgres.py --confirm")
    print("=" * 50)

if __name__ == "__main__":
    main()