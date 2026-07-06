#!/usr/bin/env python3
"""
Initialize database tables and seed baseline data.

The first admin account is created through /setup by default. For unattended
installs, set INVENTORY_CREATE_ADMIN_FROM_ENV=true and provide ADMIN_PASSWORD.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db, User, Department, create_initial_admin, seed_rbac_data
from config import get_database_info, is_external_database


def init_database():
    """Initialize the database with required tables and optional first admin."""
    with app.app_context():
        db_info = get_database_info()

        print("Initializing database...")
        print(f"Database type: {db_info['type']}")
        print(f"External database: {is_external_database()}")

        db.create_all()
        print("Database tables created")

        dept = Department.query.filter_by(name='IT Department').first()
        if not dept:
            dept = Department(
                name='IT Department',
                description='Information Technology Department',
                order_index=1
            )
            db.session.add(dept)
            db.session.commit()
            print("Created IT Department")
        else:
            print("IT Department already exists")

        seed_rbac_data()

        create_admin_from_env = os.environ.get('INVENTORY_CREATE_ADMIN_FROM_ENV', 'false').lower() == 'true'
        admin_password = os.environ.get('ADMIN_PASSWORD')
        admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@company.com')

        if User.query.first():
            print("Initial admin setup skipped because users already exist")
        elif create_admin_from_env and admin_password:
            create_initial_admin(
                username=admin_username,
                password=admin_password,
                full_name=os.environ.get('ADMIN_FULL_NAME', 'System Administrator'),
                email=admin_email
            )
            print(f"Created initial admin user from environment (username: {admin_username})")
        else:
            print("Initial admin setup skipped. Open /setup to create the first admin user.")

        print("=" * 50)
        print("Database initialization completed successfully")
        print("Recommended database: PostgreSQL")
        print("=" * 50)


if __name__ == "__main__":
    init_database()
