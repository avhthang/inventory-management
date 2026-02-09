from app import app, db, User, Role, Permission, RolePermission, UserRole
from werkzeug.security import generate_password_hash

def seed_test_users():
    with app.app_context():
        print("Creating test roles and users...")
        
        # Define roles and their permissions
        roles_data = {
            'Team Lead': ['config_proposals.create', 'config_proposals.approve_team', 'config_proposals.view'],
            'IT Staff': ['config_proposals.view', 'config_proposals.consult_it', 'config_proposals.confirm_delivery'],
            'Finance Staff': ['config_proposals.view', 'config_proposals.review_finance'],
            'Director': ['config_proposals.view', 'config_proposals.approve_director'],
            'Purchaser': ['config_proposals.view', 'config_proposals.execute_purchase'],
            'Accountant': ['config_proposals.view', 'config_proposals.execute_accounting']
        }

        created_roles = {}

        for role_name, perm_codes in roles_data.items():
            # Create Role
            role = Role.query.filter_by(name=role_name).first()
            if not role:
                role = Role(name=role_name, description=f'Test Role: {role_name}')
                db.session.add(role)
                db.session.commit()
                print(f"Created role: {role_name}")
            else:
                print(f"Role exists: {role_name}")
            
            created_roles[role_name] = role

            # Assign Permissions to Role
            for code in perm_codes:
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    if not RolePermission.query.filter_by(role_id=role.id, permission_id=perm.id).first():
                        db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
                        print(f"  + Added perm {code} to {role_name}")
            db.session.commit()

        # Create Users
        users_data = [
            {'username': 'team.lead', 'role': 'Team Lead', 'full_name': 'Nguyen Van A (Team Lead)'},
            {'username': 'it.staff', 'role': 'IT Staff', 'full_name': 'Tran Van B (IT)'},
            {'username': 'finance.staff', 'role': 'Finance Staff', 'full_name': 'Le Thi C (Finance)'},
            {'username': 'director', 'role': 'Director', 'full_name': 'Pham Van D (Director)'},
            {'username': 'purchaser', 'role': 'Purchaser', 'full_name': 'Hoang Van E (Purchasing)'},
            {'username': 'accountant', 'role': 'Accountant', 'full_name': 'Vu Thi F (Accounting)'}
        ]

        for u_data in users_data:
            user = User.query.filter_by(username=u_data['username']).first()
            if not user:
                user = User(
                    username=u_data['username'],
                    password_hash=generate_password_hash('123456'),
                    full_name=u_data['full_name'],
                    email=f"{u_data['username']}@example.com",
                    role='User' # Default role string column (legacy) - we depend on UserRole table now?
                                # Actually app.py uses `user.role` column for basic checks (admin/user)
                                # but `_get_current_permissions` checks UserRole table.
                                # Let's keep `role` column as 'User' so they are not admin.
                )
                db.session.add(user)
                db.session.commit()
                print(f"Created user: {u_data['username']}")
            else:
                print(f"User exists: {u_data['username']}")
            
            # Assign Role to User
            role = created_roles[u_data['role']]
            if not UserRole.query.filter_by(user_id=user.id, role_id=role.id).first():
                db.session.add(UserRole(user_id=user.id, role_id=role.id))
                print(f"  + Assigned {u_data['role']} to {u_data['username']}")
            
            db.session.commit()
            
        print("\nTest data seeded successfully!")
        print("Default password for all users: 123456")

if __name__ == "__main__":
    seed_test_users()
