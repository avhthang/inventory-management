import sqlite3
from app import app, db, Department, User
from sqlalchemy import text

def migrate_data():
    print("Bắt đầu chuyển dữ liệu từ database cũ...")
    
    try:
        # Kết nối với database cũ
        old_conn = sqlite3.connect('old_inventory.db')
        old_cur = old_conn.cursor()
        
        with app.app_context():
            # 1. Migrate departments
            print("\n1. Đang chuyển dữ liệu phòng ban...")
            
            # Lấy danh sách phòng ban từ bảng user cũ
            old_cur.execute("""
                SELECT DISTINCT department 
                FROM user 
                WHERE department IS NOT NULL AND department != ''
                ORDER BY department
            """)
            old_departments = [row[0] for row in old_cur.fetchall()]
            
            # Tạo dict để map department cũ với department mới
            dept_mapping = {}
            
            # Tạo departments mới
            for dept_name in old_departments:
                existing_dept = Department.query.filter_by(name=dept_name).first()
                if not existing_dept:
                    new_dept = Department(
                        name=dept_name,
                        description=f'Phòng {dept_name}'
                    )
                    db.session.add(new_dept)
                    db.session.flush()  # Để lấy ID
                    dept_mapping[dept_name] = new_dept.id
                    print(f"Đã tạo phòng ban: {dept_name} (ID: {new_dept.id})")
                else:
                    dept_mapping[dept_name] = existing_dept.id
                    print(f"Phòng ban đã tồn tại: {dept_name} (ID: {existing_dept.id})")
            
            # 2. Update department_id cho users
            print("\n2. Đang cập nhật phòng ban cho users...")
            
            # Lấy danh sách users từ DB cũ
            old_cur.execute("""
                SELECT id, username, department 
                FROM user 
                WHERE department IS NOT NULL AND department != ''
            """)
            users_with_dept = old_cur.fetchall()
            
            # Cập nhật department_id cho từng user
            for user_id, username, old_dept_name in users_with_dept:
                if old_dept_name in dept_mapping:
                    user = User.query.get(user_id)
                    if user:
                        user.department_id = dept_mapping[old_dept_name]
                        print(f"Cập nhật phòng ban cho user {username}: {old_dept_name} (ID: {dept_mapping[old_dept_name]})")
            
            # 3. Commit thay đổi
            try:
                db.session.commit()
                print("\nChuyển dữ liệu thành công!")
            except Exception as e:
                db.session.rollback()
                print(f"\nLỗi khi commit thay đổi vào database: {str(e)}")
                
    except Exception as e:
        print(f"\nLỗi khi chuyển dữ liệu: {str(e)}")
    finally:
        old_conn.close()

if __name__ == '__main__':
    migrate_data()