#!/usr/bin/env python3
"""
Database initialization script
Creates the database and initial data for the inventory management system
"""
import sys
import os
from datetime import datetime
import hashlib

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db, User, Department, DeviceGroup

def init_database():
    """Initialize the database with required tables and initial data"""
    with app.app_context():
        print("Initializing database...")
        
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
            # Hash password 'admin123'
            password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
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
            print("✓ Created admin user (username: admin, password: admin123)")
        else:
            print("✓ Admin user already exists")
        
        # Create server room device group
        server_group = DeviceGroup.query.filter_by(name='Phòng server').first()
        if not server_group:
            server_group = DeviceGroup(
                name='Phòng server',
                description='Nhóm thiết bị phòng server',
                created_by=admin.id
            )
            db.session.add(server_group)
            db.session.commit()
            print("✓ Created server room device group")
        else:
            print("✓ Server room device group already exists")
        
        print("\n" + "="*50)
        print("Database initialization completed successfully!")
        print("="*50)
        print("Login credentials:")
        print("Username: admin")
        print("Password: admin123")
        print("="*50)

if __name__ == "__main__":
    init_database()