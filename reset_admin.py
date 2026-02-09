from app import app, db, User, Department
from werkzeug.security import generate_password_hash
import sys

def reset_admin_password(new_password):
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        if user:
            user.password = generate_password_hash(new_password) # Use 'password' column as per User model, but check app.py model definition
            # In app.py line 732: password = db.Column(db.String(200), nullable=False)
            # Wait, line 680 in app.py uses: password=generate_password_hash(admin_password)
            # So the column name is 'password'.
            
            # Re-confirming User model in app.py:
            # 732:     password = db.Column(db.String(200), nullable=False)
            # But seed_test_users.py used 'password_hash'. 
            # Let's check seed_test_users.py line 57: password_hash=generate_password_hash('123456')
            # Warning: In app.py User model (Step 146):
            # 732:     password = db.Column(db.String(200), nullable=False)
            # There is NO 'password_hash' column in the visible lines of User model in app.py Step 146.
            # However, seed_test_users.py might be using an older version or I missed something.
            # Let's check app.py content again carefully.
            # Line 732: password = db.Column(db.String(200), nullable=False)
            # So usage should be user.password = ...
            
            user.password = generate_password_hash(new_password)
            db.session.commit()
            print(f"Successfully reset password for user: {user.username}")
        else:
            print("User 'admin' not found! Creating it...")
            # Create IT Department if missing
            it_dept = Department.query.filter_by(name='IT').first()
            if not it_dept:
                it_dept = Department(name='IT', description='Phòng Công nghệ Thông tin')
                db.session.add(it_dept)
                db.session.flush()
            
            user = User(
                username='admin',
                password=generate_password_hash(new_password),
                full_name='Quản Trị Viên',
                email='admin@example.com',
                role='admin',
                department_id=it_dept.id
            )
            db.session.add(user)
            db.session.commit()
            print(f"Successfully created admin user with password: {new_password}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        new_password = sys.argv[1]
    else:
        new_password = "admin123"
    
    print(f"Resetting admin password to: {new_password}")
    reset_admin_password(new_password)
