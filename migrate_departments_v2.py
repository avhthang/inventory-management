import sqlite3
from app import app, db, User, Department

def migrate_departments():
    print("Bắt đầu chuyển dữ liệu phòng ban...")
    
    # Kết nối với database cũ
    old_conn = sqlite3.connect('old_inventory.db')
    old_cur = old_conn.cursor()
    
    try:
        with app.app_context():
            # 1. Lấy tất cả department cũ từ bảng user trong DB cũ
            old_cur.execute("""
                SELECT DISTINCT department 
                FROM user 
                WHERE department IS NOT NULL AND department != ''
                ORDER BY department
            """)
            old_departments = [row[0] for row in old_cur.fetchall()]
            print(f"Tìm thấy {len(old_departments)} phòng ban cũ:", old_departments)
            
            # 2. Tạo các department mới
            dept_mapping = {}  # Map từ tên phòng ban cũ sang ID mới
            for dept_name in old_departments:
                # Kiểm tra xem department đã tồn tại chưa
                existing_dept = Department.query.filter_by(name=dept_name).first()
                if not existing_dept:
                    new_dept = Department(
                        name=dept_name,
                        description=f'Phòng {dept_name}'
                    )
                    db.session.add(new_dept)
                    db.session.flush()  # Để lấy ID của department vừa tạo
                    dept_mapping[dept_name] = new_dept.id
                    print(f"Đã tạo phòng ban mới: {dept_name} (ID: {new_dept.id})")
                else:
                    dept_mapping[dept_name] = existing_dept.id
                    print(f"Phòng ban đã tồn tại: {dept_name} (ID: {existing_dept.id})")
            
            # 3. Cập nhật department_id cho users
            old_cur.execute("""
                SELECT id, username, department 
                FROM user 
                WHERE department IS NOT NULL AND department != ''
            """)
            users_with_dept = old_cur.fetchall()
            
            for user_id, username, dept_name in users_with_dept:
                if dept_name in dept_mapping:
                    user = User.query.get(user_id)
                    if user:
                        user.department_id = dept_mapping[dept_name]
                        print(f"Đã cập nhật phòng ban cho user {username}: {dept_name} (ID: {dept_mapping[dept_name]})")
            
            # 4. Đặt trưởng phòng cho mỗi department
            for dept_name, dept_id in dept_mapping.items():
                # Lấy user đầu tiên trong phòng làm trưởng phòng
                manager = User.query.filter_by(department_id=dept_id).first()
                if manager:
                    department = Department.query.get(dept_id)
                    department.manager_id = manager.id
                    print(f"Đã đặt {manager.username} làm trưởng phòng {dept_name}")
            
            try:
                db.session.commit()
                print("Chuyển dữ liệu phòng ban thành công!")
            except Exception as e:
                db.session.rollback()
                print(f"Lỗi khi commit thay đổi vào database: {str(e)}")
                
    except Exception as e:
        print(f"Lỗi khi chuyển dữ liệu: {str(e)}")
    finally:
        old_conn.close()

if __name__ == '__main__':
    migrate_departments()