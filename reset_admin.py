from app import app, db, User
from werkzeug.security import generate_password_hash
import sys

def reset_admin_password(new_password):
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        if user:
            user.password = generate_password_hash(new_password)
            db.session.commit()
            print(f"Successfully reset password for user: {user.username}")
        else:
            print("User 'admin' not found!")
            sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        new_password = sys.argv[1]
    else:
        new_password = "admin123"
    
    print(f"Resetting admin password to: {new_password}")
    reset_admin_password(new_password)
