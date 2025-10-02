from app import app, db, User, Department
from sqlalchemy import text

def migrate_departments():
    print("Bắt đầu chuyển dữ liệu phòng ban...")
    
    with app.app_context():
        # 1. Lấy tất cả department cũ từ bảng user
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT DISTINCT department 
                FROM user 
                WHERE department IS NOT NULL 
                ORDER BY department
            """))
            old_departments = [row[0] for row in result]
            
        # 2. Tạo các department mới
        dept_mapping = {}  # Map từ tên phòng ban cũ sang ID mới
        for dept_name in old_departments:
            existing_dept = Department.query.filter_by(name=dept_name).first()
            if not existing_dept:
                new_dept = Department(
                    name=dept_name,
                    description=f'Phòng {dept_name}'
                )
                db.session.add(new_dept)
                db.session.flush()  # Để lấy ID của department vừa tạo
                dept_mapping[dept_name] = new_dept.id
            else:
                dept_mapping[dept_name] = existing_dept.id
        
        # 3. Cập nhật department_id cho users
        with db.engine.connect() as conn:
            for dept_name, dept_id in dept_mapping.items():
                conn.execute(text("""
                    UPDATE user 
                    SET department_id = :dept_id 
                    WHERE department = :dept_name
                """), {"dept_id": dept_id, "dept_name": dept_name})
                
        # 4. Đặt trưởng phòng cho mỗi department
        for dept_name, dept_id in dept_mapping.items():
            # Lấy user đầu tiên trong phòng làm trưởng phòng
            manager = User.query.filter_by(department_id=dept_id).first()
            if manager:
                department = Department.query.get(dept_id)
                department.manager_id = manager.id
        
        try:
            db.session.commit()
            print("Chuyển dữ liệu phòng ban thành công!")
        except Exception as e:
            db.session.rollback()
            print(f"Lỗi khi chuyển dữ liệu: {str(e)}")

if __name__ == '__main__':
    migrate_departments()