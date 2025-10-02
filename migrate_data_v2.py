{% extends "base.html" %}

{% block title %}Thêm Người Dùng Mới{% endblock %}

{% block content %}
<div class="card">
    <div class="card-header"><h2 class="card-title mb-0">Thêm Người Dùng Mới</h2></div>
    <div class="card-body">
        <form method="POST">
            <div class="row">
                <div class="col-md-6">
                    <div class="mb-3"><label class="form-label required">Tên đăng nhập <span class="required-asterisk">*</span></label><input type="text" name="username" class="form-control" required></div>
                    <div class="mb-3"><label class="form-label required">Mật khẩu <span class="required-asterisk">*</span></label><input type="password" name="password" class="form-control" required></div>
                    <div class="mb-3"><label class="form-label">Họ và tên</label><input type="text" name="full_name" class="form-control"></div>
                    <div class="mb-3"><label class="form-label">Email</label><input type="email" name="email" class="form-control"></div>
                    <div class="mb-3"><label class="form-label">Ngày sinh</label><input type="date" name="date_of_birth" class="form-control"></div>
                    <div class="mb-3"><label class="form-label">Vai trò</label><select name="role" class="form-control"><option value="user">Người dùng</option><option value="admin">Quản trị viên</option></select></div>
                </div>
                <div class="col-md-6">
                    <div class="mb-3">
                        <label class="form-label">Phòng ban</label>
                        <select name="department_id" class="form-control">
                            <option value="">-- Chọn phòng ban --</option>
                            {% for dept in departments %}
                                <option value="{{ dept.id }}">{{ dept.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="mb-3"><label class="form-label">Chức vụ</label><input type="text" name="position" class="form-control"></div>
                    <div class="mb-3"><label class="form-label">Số điện thoại</label><input type="text" name="phone_number" class="form-control"></div>
                    <div class="mb-3"><label class="form-label">Ghi chú</label><textarea name="notes" class="form-control" rows="2"></textarea></div>
                </div>
            </div>
            <hr>
            <h5 class="mt-4">Thông tin nhân sự</h5>
            <div class="row">
                <div class="col-md-4">
                    <div class="mb-3">
                        <label class="form-label">Trạng thái</label>
                        <select name="status" class="form-select">
                            <option value="Đang làm" selected>Đang làm</option>
                            <option value="Thử việc">Thử việc</option>
                            <option value="Đã nghỉ">Đã nghỉ</option>
                            <option value="Khác">Khác</option>
                        </select>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="mb-3"><label class="form-label">Ngày onboard</label><input type="date" name="onboard_date" class="form-control"></div>
                </div>
                <div class="col-md-4">
                    <div class="mb-3"><label class="form-label">Ngày offboard</label><input type="date" name="offboard_date" class="form-control"></div>
                </div>
            </div>

            <div class="form-group mt-3">
                <a href="{{ url_for('user_list') }}" class="btn btn-secondary">Hủy bỏ</a>
                <button type="submit" class="btn btn-primary">Lưu người dùng</button>
            </div>
        </form>
    </div>
</div>
{% endblock %}import os
import sqlite3
from app import app, db, Department, User
from sqlalchemy import text

def migrate_data():
    old_db_path = 'old_inventory.db'
    
    print("Kiểm tra database cũ...")
    if not os.path.exists(old_db_path):
        print(f"Lỗi: Không tìm thấy file database cũ ({old_db_path})")
        print("Vui lòng copy file database cũ vào thư mục hiện tại và đặt tên là 'old_inventory.db'")
        return
        
    print("Bắt đầu chuyển dữ liệu từ database cũ...")
    
    try:
        # Kết nối với database cũ
        old_conn = sqlite3.connect(old_db_path)
        old_cur = old_conn.cursor()
        
        # Kiểm tra xem bảng user có tồn tại trong database cũ không
        old_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
        if not old_cur.fetchone():
            print("Lỗi: Không tìm thấy bảng 'user' trong database cũ")
            print("Hãy chắc chắn rằng bạn đã copy đúng file database")
            return
            
        # Kiểm tra cấu trúc bảng user
        old_cur.execute("PRAGMA table_info(user)")
        columns = [col[1] for col in old_cur.fetchall()]
        if 'department' not in columns:
            print("Lỗi: Không tìm thấy cột 'department' trong bảng user")
            print("Cấu trúc bảng user hiện tại:", columns)
            return
        
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
            
            if not old_departments:
                print("Không tìm thấy dữ liệu phòng ban nào trong database cũ")
                return
                
            print(f"Tìm thấy {len(old_departments)} phòng ban:", old_departments)
            
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
            
            if not users_with_dept:
                print("Không tìm thấy người dùng nào có phòng ban trong database cũ")
                return
                
            # Cập nhật department_id cho từng user
            updated_count = 0
            for user_id, username, old_dept_name in users_with_dept:
                if old_dept_name in dept_mapping:
                    user = User.query.get(user_id)
                    if user:
                        user.department_id = dept_mapping[old_dept_name]
                        updated_count += 1
                        print(f"Cập nhật phòng ban cho user {username}: {old_dept_name} (ID: {dept_mapping[old_dept_name]})")
            
            if updated_count == 0:
                print("Không tìm thấy user nào để cập nhật phòng ban")
                return
                
            # 3. Commit thay đổi
            try:
                db.session.commit()
                print(f"\nChuyển dữ liệu thành công!")
                print(f"- Đã tạo {len(dept_mapping)} phòng ban")
                print(f"- Đã cập nhật {updated_count} người dùng")
            except Exception as e:
                db.session.rollback()
                print(f"\nLỗi khi commit thay đổi vào database: {str(e)}")
                
    except Exception as e:
        print(f"\nLỗi khi chuyển dữ liệu: {str(e)}")
    finally:
        old_conn.close()

if __name__ == '__main__':
    migrate_data()