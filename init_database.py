#!/usr/bin/env python3
"""
Database initialization script
Creates the database and initial data for the inventory management system
Supports both SQLite and external databases (PostgreSQL/MySQL)
"""
import sys
import os
from datetime import datetime
import hashlib

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db, User, Department
from config import get_database_info, is_external_database
from security import generate_secure_password

def init_database():
    """Initialize the database with required tables and initial data"""
    with app.app_context():
        db_info = get_database_info()
        is_external = is_external_database()
        
        print("Initializing database...")
        print(f"Database type: {db_info['type']}")
        print(f"External database: {is_external}")
        
        # Create all tables
        db.create_all()
        print("✓ Database tables created")
        
        # Create default department
        dept = Department.query.filter_by(name='IT Department').first()
        if not dept:
            dept = Department(
                name='IT Department',
                description='Information Technology Department',
                order_index=1
            )
            db.session.add(dept)
            db.session.commit()
            print("✓ Created IT Department")
        else:
            print("✓ IT Department already exists")
        
        # Create admin user
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            # Generate secure password
            admin_password = os.environ.get('ADMIN_PASSWORD', generate_secure_password())
            password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
            admin = User(
                username='admin',
                password=password_hash,
                full_name='System Administrator',
                email='admin@company.com',
                role='admin',
                department_id=dept.id,
                status='Đang làm'
            )
            db.session.add(admin)
            db.session.commit()
            print(f"✓ Created admin user (username: admin, password: {admin_password})")
        else:
            print("✓ Admin user already exists")
        
        
        # Seed RBAC data
        from app import seed_rbac_data
        seed_rbac_data()
        
        print("\n" + "="*50)
        print("Database initialization completed successfully!")
        print("="*50)
        print("Login credentials:")
        print("Username: admin")
        if 'ADMIN_PASSWORD' in os.environ:
            print(f"Password: {os.environ['ADMIN_PASSWORD']}")
        else:
            print("Password: Generated securely (check console output above)")
        print("="*50)
        print("RBAC roles and permissions have been seeded.")
        print("Admin user has been assigned Admin role with all permissions.")
        print("="*50)

if __name__ == "__main__":
    init_database()