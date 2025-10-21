#!/usr/bin/env python3
"""
Script để setup database cho Render.com deployment
"""

import os
import sys
from app import app, db
from config import get_database_info, is_external_database

def setup_database():
    """Setup database cho production"""
    print("🔧 Setting up database for Render.com...")
    
    # Kiểm tra database connection
    db_info = get_database_info()
    print(f"📊 Database type: {db_info['type']}")
    print(f"🌐 External database: {is_external_database()}")
    
    try:
        # Tạo tất cả tables
        with app.app_context():
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Kiểm tra connection
            db.engine.execute('SELECT 1')
            print("✅ Database connection test passed")
            
    except Exception as e:
        print(f"❌ Database setup failed: {e}")
        sys.exit(1)

def create_admin_user():
    """Tạo admin user mặc định"""
    print("👤 Creating default admin user...")
    
    try:
        from app import User
        from security import generate_secure_password
        
        with app.app_context():
            # Kiểm tra xem admin đã tồn tại chưa
            admin = User.query.filter_by(username='admin').first()
            if admin:
                print("ℹ️  Admin user already exists")
                return
            
            # Tạo admin user
            password = generate_secure_password()
            admin = User(
                username='admin',
                email='admin@example.com',
                password_hash=generate_password_hash(password),
                role='admin',
                is_active=True
            )
            
            db.session.add(admin)
            db.session.commit()
            
            print(f"✅ Admin user created successfully")
            print(f"📧 Username: admin")
            print(f"🔑 Password: {password}")
            print("⚠️  Please change the password after first login!")
            
    except Exception as e:
        print(f"❌ Failed to create admin user: {e}")

def main():
    """Main setup function"""
    print("🚀 Render.com Database Setup")
    print("=" * 40)
    
    # Setup database
    setup_database()
    
    # Create admin user
    create_admin_user()
    
    print("=" * 40)
    print("✅ Setup completed successfully!")
    print("🌐 Your app should be ready at: https://your-app.onrender.com")

if __name__ == '__main__':
    main()