import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import aliased
from sqlalchemy import or_, func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import pandas as pd
import io
import click

# --- Cấu hình ứng dụng ---
instance_path = os.path.join(os.getcwd(), 'instance')
os.makedirs(instance_path, exist_ok=True)

app = Flask(__name__, instance_path=instance_path)
app.config['SECRET_KEY'] = 'your_super_secret_key_change_this_please'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.permanent_session_lifetime = timedelta(days=30)

db = SQLAlchemy(app)

# --- Models (Không thay đổi) ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(20), default='user')
    department = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    position = db.Column(db.String(100))
    date_of_birth = db.Column(db.Date)
    phone_number = db.Column(db.String(20))
    notes = db.Column(db.Text)
    status = db.Column(db.String(50), default='Đang làm')
    onboard_date = db.Column(db.Date)
    offboard_date = db.Column(db.Date)
    given_handovers = db.relationship('DeviceHandover', foreign_keys='DeviceHandover.giver_id', back_populates='giver', lazy='dynamic')
    received_handovers = db.relationship('DeviceHandover', foreign_keys='DeviceHandover.receiver_id', back_populates='receiver', lazy='dynamic')

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    device_type = db.Column(db.String(50), nullable=False)
    serial_number = db.Column(db.String(80))
    purchase_date = db.Column(db.Date, nullable=False)
    import_date = db.Column(db.Date, nullable=False)
    condition = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Sẵn sàng')
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    assign_date = db.Column(db.Date)
    configuration = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.Column(db.String(120))
    importer = db.Column(db.String(120))
    brand = db.Column(db.String(100))
    supplier = db.Column(db.String(150))
    warranty = db.Column(db.String(50))
    manager = db.relationship('User', foreign_keys=[manager_id])
    purchase_price = db.Column(db.Float)

class DeviceHandover(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    handover_date = db.Column(db.Date, nullable=False, default=date.today)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    device = db.relationship('Device', backref='handovers')
    giver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    giver = db.relationship('User', foreign_keys=[giver_id], back_populates='given_handovers')
    receiver = db.relationship('User', foreign_keys=[receiver_id], back_populates='received_handovers')
    device_condition = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.String(255))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)

# --- (Các hàm context_processor, home, auth, device routes giữ nguyên) ---
@app.context_processor
def inject_user():
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
        return dict(current_user=current_user)
    return dict(current_user=None)

@app.route('/')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    total_devices = Device.query.count()
    in_use_devices = Device.query.filter_by(status='Đã cấp phát').count()
    maintenance_devices = Device.query.filter_by(status='Bảo trì').count()
    return render_template('dashboard.html', total_devices=total_devices, in_use_devices=in_use_devices, maintenance_devices=maintenance_devices)

# ... (Auth routes) ...
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('home'))
        flash('Tên đăng nhập hoặc mật khẩu không đúng', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        if not username or not password or not confirm_password:
            flash('Tên đăng nhập và mật khẩu là bắt buộc.', 'danger'); return render_template('register.html')
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp.', 'danger'); return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger'); return render_template('register.html')
        if email and User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng.', 'danger'); return render_template('register.html')
        new_user = User(username=username, password=generate_password_hash(password), full_name=full_name, email=email, role='user')
        db.session.add(new_user); db.session.commit()
        session['user_id'] = new_user.id; session.permanent = True
        flash('Đăng ký tài khoản thành công! Bạn đã được đăng nhập.', 'success')
        return redirect(url_for('home'))
    return render_template('register.html')
    
@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if not check_password_hash(user.password, current_password):
            flash('Mật khẩu hiện tại không đúng.', 'danger')
        elif new_password != confirm_password:
            flash('Mật khẩu mới không khớp.', 'danger')
        else:
            user.password = generate_password_hash(new_password)
            db.session.commit()
            flash('Đổi mật khẩu thành công.', 'success')
            return redirect(url_for('home'))
    return render_template('change_password.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if 'user_id' in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')

        user = User.query.filter_by(username=username, email=email).first()

        if user:
            default_password = 'Password123'
            user.password = generate_password_hash(default_password)
            db.session.commit()
            
            flash(f'Mật khẩu cho tài khoản "{username}" đã được reset thành công về giá trị mặc định: {default_password}', 'success')
            return redirect(url_for('login'))
        else:
            flash('Tên đăng nhập hoặc Email không chính xác. Vui lòng thử lại.', 'danger')

    return render_template('forgot_password.html')

# ... (Device routes) ...
@app.route('/devices')
def device_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_device_code = request.args.get('filter_device_code', '')
    filter_name = request.args.get('filter_name', '')
    filter_device_type = request.args.get('filter_device_type', '')
    filter_status = request.args.get('filter_status', '')
    filter_manager_id = request.args.get('filter_manager_id', '')
    
    query = Device.query
    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_name:
        query = query.filter(Device.name.ilike(f'%{filter_name}%'))
    if filter_device_type:
        query = query.filter_by(device_type=filter_device_type)
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_manager_id:
        query = query.filter(Device.manager_id == filter_manager_id)
    
    devices_pagination = query.order_by(Device.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng']
    users = User.query.order_by(User.full_name).all()

    return render_template(
        'devices.html',
        devices=devices_pagination,
        device_types=device_types,
        statuses=statuses,
        users=users,
        filter_device_code=filter_device_code,
        filter_name=filter_name,
        filter_device_type=filter_device_type,
        filter_status=filter_status,
        filter_manager_id=filter_manager_id
    )
    
@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        device_code = request.form.get('device_code')
        if not device_code:
            last_device = Device.query.order_by(Device.id.desc()).first()
            last_id = last_device.id if last_device else 0
            device_code = f"TB{last_id + 1:05d}"
            
        if Device.query.filter_by(device_code=device_code).first():
            flash(f'Mã thiết bị {device_code} đã tồn tại.', 'danger')
            return redirect(url_for('add_device'))
            
        new_device = Device(
            device_code=device_code,
            name=request.form['name'],
            device_type=request.form['device_type'],
            serial_number=request.form.get('serial_number'),
            brand=request.form.get('brand'),
            supplier=request.form.get('supplier'),
            warranty=request.form.get('warranty'),
            configuration=request.form.get('configuration'),
            purchase_date=datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date(),
            purchase_price=request.form.get('purchase_price', type=float, default=None),
            buyer=request.form.get('buyer'),
            importer=request.form.get('importer'),
            import_date=datetime.strptime(request.form['import_date'], '%Y-%m-%d').date(),
            condition=request.form['condition'],
            manager_id=request.form.get('manager_id') if request.form.get('manager_id') else None,
            assign_date=datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None,
            notes=request.form.get('notes')
        )
        db.session.add(new_device)
        db.session.commit()
        flash('Thêm thiết bị mới thành công!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(User.full_name).all()
    return render_template('add_device.html', managers=managers)
    
@app.route('/edit_device/<int:device_id>', methods=['GET', 'POST'])
def edit_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    if request.method == 'POST':
        device.name = request.form['name']
        device.device_type = request.form['device_type']
        device.serial_number = request.form.get('serial_number')
        device.brand = request.form.get('brand')
        device.supplier = request.form.get('supplier')
        device.warranty = request.form.get('warranty')
        device.configuration = request.form.get('configuration')
        device.purchase_date = datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date()
        device.purchase_price = request.form.get('purchase_price', type=float, default=None)
        device.buyer = request.form.get('buyer')
        device.importer = request.form.get('importer')
        device.import_date = datetime.strptime(request.form['import_date'], '%Y-%m-%d').date()
        device.condition = request.form['condition']
        device.status = request.form['status']
        manager_id_str = request.form.get('manager_id')
        device.manager_id = int(manager_id_str) if manager_id_str else None
        device.assign_date = datetime.strptime(request.form['assign_date'], '%Y-%m-%d').date() if request.form.get('assign_date') else None
        device.notes = request.form.get('notes')
        
        db.session.commit()
        flash('Cập nhật thông tin thiết bị thành công!', 'success')
        return redirect(url_for('device_list'))
        
    managers = User.query.order_by(User.full_name).all()
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì', 'Hỏng']
    return render_template('edit_device.html', device=device, managers=managers, statuses=statuses)

@app.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    if device.handovers:
        flash('Không thể xóa thiết bị đã có lịch sử bàn giao.', 'danger')
        return redirect(url_for('device_list'))
        
    db.session.delete(device)
    db.session.commit()
    flash('Xóa thiết bị thành công!', 'success')
    return redirect(url_for('device_list'))

@app.route('/device/<int:device_id>')
def device_detail(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    handovers = DeviceHandover.query.filter_by(device_id=device_id).order_by(DeviceHandover.handover_date.desc()).all()
    return render_template('device_detail.html', device=device, handovers=handovers)

# --- Handover Routes ---
@app.route('/handover_list')
def handover_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_device_code = request.args.get('filter_device_code', '')
    filter_giver_id = request.args.get('filter_giver_id', '')
    filter_receiver_id = request.args.get('filter_receiver_id', '')
    filter_device_type = request.args.get('filter_device_type', '')
    filter_start_date = request.args.get('filter_start_date', '')
    filter_end_date = request.args.get('filter_end_date', '')
    
    query = DeviceHandover.query.join(Device)

    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_giver_id:
        query = query.filter(DeviceHandover.giver_id == filter_giver_id)
    if filter_receiver_id:
        query = query.filter(DeviceHandover.receiver_id == filter_receiver_id)
    if filter_device_type:
        query = query.filter(Device.device_type == filter_device_type)
    if filter_start_date:
        query = query.filter(DeviceHandover.handover_date >= datetime.strptime(filter_start_date, '%Y-%m-%d').date())
    if filter_end_date:
        query = query.filter(DeviceHandover.handover_date <= datetime.strptime(filter_end_date, '%Y-%m-%d').date())

    handovers_pagination = query.order_by(DeviceHandover.handover_date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    users = User.query.order_by(User.full_name).all()
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    return render_template('handovers.html', handovers=handovers_pagination, users=users, device_types=device_types, filter_device_code=filter_device_code, filter_giver_id=filter_giver_id, filter_receiver_id=filter_receiver_id, filter_device_type=filter_device_type, filter_start_date=filter_start_date, filter_end_date=filter_end_date)

# --- CẬP NHẬT HÀM ADD_HANDOVER ---
@app.route('/add_handover', methods=['GET', 'POST'])
def add_handover():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Lấy danh sách ID thiết bị từ form
        device_ids = request.form.getlist('device_ids') 
        receiver_id = request.form.get('receiver_id')
        handover_date_str = request.form.get('handover_date')
        
        # Validation cơ bản
        if not device_ids or not any(d_id for d_id in device_ids if d_id) or not receiver_id or not handover_date_str:
            flash('Vui lòng chọn ít nhất một thiết bị và điền đầy đủ các trường bắt buộc.', 'danger')
            return redirect(url_for('add_handover'))
            
        handover_date = datetime.strptime(handover_date_str, '%Y-%m-%d').date()
        
        handovers_created_count = 0
        for device_id in device_ids:
            if not device_id: continue # Bỏ qua các giá trị rỗng

            device_to_update = Device.query.get(device_id)
            # Kiểm tra xem thiết bị có hợp lệ và sẵn sàng không
            if not device_to_update or device_to_update.status != 'Sẵn sàng':
                flash(f'Thiết bị có mã "{device_to_update.device_code if device_to_update else "không xác định"}" không hợp lệ hoặc không sẵn sàng để bàn giao.', 'warning')
                continue

            new_handover = DeviceHandover(
                handover_date=handover_date, 
                device_id=device_id, 
                giver_id=request.form['giver_id'], 
                receiver_id=receiver_id, 
                device_condition=request.form['device_condition'], 
                reason=request.form.get('reason', ''), 
                location=request.form.get('location', ''), 
                notes=request.form.get('notes', '')
            )
            db.session.add(new_handover)
            
            # Cập nhật trạng thái thiết bị
            device_to_update.manager_id = int(receiver_id)
            device_to_update.assign_date = new_handover.handover_date
            device_to_update.status = 'Đã cấp phát'
            
            handovers_created_count += 1

        if handovers_created_count > 0:
            db.session.commit()
            flash(f'Tạo thành công {handovers_created_count} phiếu bàn giao!', 'success')
        else:
            db.session.rollback() # Hoàn tác nếu không có phiếu nào được tạo
            flash('Không có phiếu bàn giao nào được tạo. Vui lòng kiểm tra lại thông tin thiết bị.', 'danger')

        return redirect(url_for('handover_list'))
    
    # Logic cho phương thức GET
    preselected_device_id = request.args.get('device_id', type=int)
    # Chỉ hiển thị các thiết bị sẵn sàng để chọn
    devices = Device.query.filter_by(status='Sẵn sàng').order_by(Device.device_code).all()
    users = User.query.order_by(User.full_name).all()
    
    return render_template('add_handover.html', 
                           devices=devices, 
                           users=users, 
                           now=datetime.now(),
                           preselected_device_id=preselected_device_id)

# ... (Các hàm edit_handover, delete_handover giữ nguyên) ...
@app.route('/edit_handover/<int:handover_id>', methods=['GET', 'POST'])
def edit_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover = DeviceHandover.query.get_or_404(handover_id)
    if request.method == 'POST':
        handover.handover_date = datetime.strptime(request.form['handover_date'], '%Y-%m-%d').date()
        handover.device_id = request.form['device_id']
        handover.giver_id = request.form['giver_id']
        handover.receiver_id = request.form['receiver_id']
        handover.device_condition = request.form['device_condition']
        handover.reason = request.form.get('reason', '')
        handover.location = request.form.get('location', '')
        handover.notes = request.form.get('notes', '')
        db.session.commit()
        flash('Cập nhật phiếu bàn giao thành công!', 'success')
        return redirect(url_for('handover_list'))
        
    devices = Device.query.order_by(Device.device_code).all()
    users = User.query.order_by(User.full_name).all()
    return render_template('edit_handover.html', handover=handover, devices=devices, users=users)

@app.route('/delete_handover/<int:handover_id>', methods=['POST'])
def delete_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover = DeviceHandover.query.get_or_404(handover_id)
    db.session.delete(handover)
    db.session.commit()
    flash('Xóa phiếu bàn giao thành công!', 'success')
    return redirect(url_for('handover_list'))

# Thêm route mới này vào file app.py (trong khu vực Handover Routes)

@app.route('/import_handovers', methods=['GET', 'POST'])
def import_handovers():
    if 'user_id' not in session: return redirect(url_for('login'))
    # Thêm kiểm tra quyền admin nếu cần

    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ (.xls, .xlsx).', 'danger')
            return redirect(url_for('import_handovers'))

        try:
            df = pd.read_excel(file, engine='openpyxl')
            required_columns = ['Mã thiết bị', 'Tên đăng nhập người giao', 'Tên đăng nhập người nhận', 'Ngày bàn giao', 'Tình trạng thiết bị']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_handovers'))

            errors = []
            handovers_to_add = []
            
            for index, row in df.iterrows():
                device_code = str(row['Mã thiết bị'])
                giver_username = str(row['Tên đăng nhập người giao'])
                receiver_username = str(row['Tên đăng nhập người nhận'])
                handover_date_str = str(row['Ngày bàn giao'])

                device = Device.query.filter_by(device_code=device_code).first()
                giver = User.query.filter_by(username=giver_username).first()
                receiver = User.query.filter_by(username=receiver_username).first()

                # --- Validation ---
                current_row_errors = []
                if not device:
                    current_row_errors.append(f'Mã thiết bị "{device_code}" không tồn tại.')
                if not giver:
                    current_row_errors.append(f'Người giao "{giver_username}" không tồn tại.')
                if not receiver:
                    current_row_errors.append(f'Người nhận "{receiver_username}" không tồn tại.')
                
                if device and device.status == 'Đã cấp phát':
                     current_row_errors.append(f'Thiết bị "{device_code}" đã được cấp phát, không thể bàn giao.')

                if current_row_errors:
                    errors.append(f"Dòng {index + 2}: " + ", ".join(current_row_errors))
                    continue 

                try:
                    handover_date = pd.to_datetime(handover_date_str).date()
                except (ValueError, TypeError):
                    errors.append(f'Dòng {index + 2}: Định dạng ngày "{handover_date_str}" không hợp lệ.')
                    continue

                new_handover = DeviceHandover(
                    device_id=device.id,
                    giver_id=giver.id,
                    receiver_id=receiver.id,
                    handover_date=handover_date,
                    device_condition=row['Tình trạng thiết bị'],
                    reason=row.get('Lý do bàn giao'),
                    location=row.get('Nơi đặt thiết bị'),
                    notes=row.get('Ghi chú')
                )
                handovers_to_add.append(new_handover)
                
                # Cập nhật trạng thái của thiết bị
                device.status = 'Đã cấp phát'
                device.manager_id = receiver.id
                device.assign_date = handover_date
                
            if errors:
                for error in errors:
                    flash(error, 'danger')
                db.session.rollback() # Hoàn tác tất cả nếu có lỗi
            else:
                db.session.add_all(handovers_to_add)
                db.session.commit()
                flash(f'Đã nhập thành công {len(handovers_to_add)} phiếu bàn giao!', 'success')
                return redirect(url_for('handover_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi trong quá trình xử lý file: {str(e)}', 'danger')

    return render_template('import_handovers.html')

# --- (User Management Routes giữ nguyên) ---
@app.route('/users')
def user_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    filter_username = request.args.get('filter_username', '')
    filter_role = request.args.get('filter_role', '')
    filter_department = request.args.get('filter_department', '')
    filter_status = request.args.get('filter_status', '')

    query = User.query
    if filter_username:
        query = query.filter(User.username.ilike(f'%{filter_username}%'))
    if filter_role:
        query = query.filter_by(role=filter_role)
    if filter_department:
        query = query.filter(User.department == filter_department)
    if filter_status:
        query = query.filter(User.status == filter_status)

    users_pagination = query.order_by(User.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    departments = [d[0] for d in db.session.query(User.department).filter(User.department.isnot(None)).distinct().order_by(User.department)]
    statuses = ['Đang làm', 'Thử việc', 'Đã nghỉ', 'Khác']

    return render_template('users.html', 
                           users=users_pagination, 
                           filter_username=filter_username, 
                           filter_role=filter_role, 
                           filter_department=filter_department,
                           filter_status=filter_status,
                           departments=departments,
                           statuses=statuses)


@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return redirect(url_for('add_user'))
        if email and User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng.', 'danger')
            return redirect(url_for('add_user'))
            
        new_user = User(
            username=username,
            password=generate_password_hash(request.form['password']),
            full_name=request.form.get('full_name'),
            email=email,
            date_of_birth=datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d').date() if request.form.get('date_of_birth') else None,
            role=request.form.get('role', 'user'),
            department=request.form.get('department'),
            position=request.form.get('position'),
            phone_number=request.form.get('phone_number'),
            notes=request.form.get('notes'),
            status=request.form.get('status', 'Đang làm'),
            onboard_date=datetime.strptime(request.form['onboard_date'], '%Y-%m-%d').date() if request.form.get('onboard_date') else None,
            offboard_date=datetime.strptime(request.form['offboard_date'], '%Y-%m-%d').date() if request.form.get('offboard_date') else None,
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Thêm người dùng mới thành công!', 'success')
        return redirect(url_for('user_list'))
    return render_template('add_user.html')

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.full_name = request.form.get('full_name')
        user.email = request.form.get('email')
        user.date_of_birth = datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d').date() if request.form.get('date_of_birth') else None
        user.role = request.form.get('role')
        user.department = request.form.get('department')
        user.position = request.form.get('position')
        user.phone_number = request.form.get('phone_number')
        user.notes = request.form.get('notes')
        
        user.status = request.form.get('status')
        user.onboard_date = datetime.strptime(request.form['onboard_date'], '%Y-%m-%d').date() if request.form.get('onboard_date') else None
        user.offboard_date = datetime.strptime(request.form['offboard_date'], '%Y-%m-%d').date() if request.form.get('offboard_date') else None

        new_password = request.form.get('password')
        if new_password:
            user.password = generate_password_hash(new_password)
            
        db.session.commit()
        flash('Cập nhật thông tin người dùng thành công!', 'success')
        return redirect(url_for('user_list'))
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    if user.given_handovers.count() > 0 or user.received_handovers.count() > 0:
        flash(f'Không thể xóa người dùng "{user.full_name}" vì họ đã có lịch sử bàn giao thiết bị.', 'danger')
        return redirect(url_for('user_list'))
    if user_id == session.get('user_id'):
        flash('Bạn không thể tự xóa tài khoản của mình.', 'danger')
        return redirect(url_for('user_list'))
        
    db.session.delete(user)
    db.session.commit()
    flash('Xóa người dùng thành công!', 'success')
    return redirect(url_for('user_list'))

# --- API Routes ---
# --- CẬP NHẬT API ĐỂ TRẢ VỀ THÊM SERIAL NUMBER ---
@app.route('/api/device_info/<int:device_id>')
def device_info(device_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    device = Device.query.get(device_id)
    if device:
        return jsonify({
            'id': device.id, 
            'name': device.name, 
            'device_code': device.device_code,
            'serial_number': device.serial_number or 'N/A' # Thêm serial number
        })
    return jsonify({'error': 'Device not found'}), 404

# --- Import/Export Routes ---
# ... (Các hàm import/export giữ nguyên) ...
@app.route('/import_devices', methods=['GET', 'POST'])
def import_devices():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ.', 'danger')
            return redirect(url_for('import_devices'))
        
        try:
            df = pd.read_excel(file, engine='openpyxl')
            expected_columns = [
                'Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Số serial', 'Ngày mua', 'Giá mua', 'Người mua',
                'Ngày nhập', 'Tình trạng', 'Trạng thái', 'Người quản lý', 'Ngày cấp phát',
                'Cấu hình', 'Ghi chú', 'Người nhập', 'Thương hiệu', 'Nhà cung cấp', 'Bảo hành'
            ]
            if not all(col in df.columns for col in expected_columns):
                flash('File Excel thiếu một hoặc nhiều cột bắt buộc. Vui lòng kiểm tra lại tiêu đề các cột.', 'danger')
                return redirect(url_for('import_devices'))
            
            valid_conditions = ['Mới', 'Sử dụng bình thường', 'Cần bảo trì', 'Hỏng']
            valid_statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì']
            valid_device_types = [
                'Laptop', 'Case máy tính', 'Màn hình', 'Bàn phím', 'Chuột', 'Ổ cứng',
                'Ram', 'Card màn hình', 'Máy in', 'Thiết bị mạng', 'Server', 'Thiết bị khác'
            ]
            
            devices_to_add = []
            errors = []

            for index, row in df.iterrows():
                manager_id = None
                
                if not all(pd.notna(row[col]) for col in ['Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Tình trạng', 'Trạng thái']):
                    errors.append(f'Dòng {index+2}: Thiếu thông tin ở các cột bắt buộc.')
                    continue
                if Device.query.filter_by(device_code=row['Mã thiết bị']).first():
                    errors.append(f'Dòng {index+2}: Mã thiết bị {row["Mã thiết bị"]} đã tồn tại.')
                    continue
                if row['Loại thiết bị'] not in valid_device_types:
                    errors.append(f'Dòng {index+2}: Loại thiết bị "{row["Loại thiết bị"]}" không hợp lệ.')
                    continue

                if pd.notna(row['Người quản lý']) and row['Người quản lý']:
                    manager = User.query.filter_by(full_name=row['Người quản lý']).first()
                    if not manager:
                        errors.append(f'Dòng {index+2}: Người quản lý {row["Người quản lý"]} không tồn tại.')
                        continue
                    manager_id = manager.id
                
                try:
                    purchase_date = pd.to_datetime(row['Ngày mua']).date() if pd.notna(row['Ngày mua']) else None
                    import_date = pd.to_datetime(row['Ngày nhập']).date() if pd.notna(row['Ngày nhập']) else None
                    assign_date = pd.to_datetime(row['Ngày cấp phát']).date() if pd.notna(row['Ngày cấp phát']) else None
                except ValueError:
                    errors.append(f'Dòng {index+2}: Định dạng ngày không hợp lệ.'); continue
                
                new_device = Device(
                    device_code=row['Mã thiết bị'], name=row['Tên thiết bị'], device_type=row['Loại thiết bị'],
                    serial_number=row.get('Số serial'),
                    purchase_date=purchase_date,
                    import_date=import_date,
                    condition=row['Tình trạng'], status=row['Trạng thái'],
                    manager_id=manager_id,
                    assign_date=assign_date,
                    configuration=row.get('Cấu hình'),
                    notes=row.get('Ghi chú'),
                    buyer=row.get('Người mua'),
                    importer=row.get('Người nhập'),
                    brand=row.get('Thương hiệu'),
                    supplier=row.get('Nhà cung cấp'),
                    warranty=row.get('Bảo hành'),
                    purchase_price=pd.to_numeric(row.get('Giá mua'), errors='coerce')
                )
                devices_to_add.append(new_device)
            
            if errors:
                for error in errors:
                    flash(error, 'danger')
                db.session.rollback()
            else:
                db.session.add_all(devices_to_add)
                db.session.commit()
                flash('Nhập thiết bị từ Excel thành công!', 'success')
                return redirect(url_for('device_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi không xác định khi xử lý file: {str(e)}', 'danger')
            
    return render_template('import_devices.html')

@app.route('/export_devices_excel')
def export_devices_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    devices = Device.query.order_by(Device.device_code).all()
    data = []
    for device in devices:
        data.append({
            'Mã thiết bị': device.device_code, 'Tên thiết bị': device.name, 'Loại thiết bị': device.device_type,
            'Số serial': device.serial_number or '', 
            'Ngày mua': device.purchase_date.strftime('%d-%m-%Y') if device.purchase_date else '',
            'Giá mua': device.purchase_price,
            'Người mua': device.buyer or '',
            'Ngày nhập': device.import_date.strftime('%d-%m-%Y') if device.import_date else '', 'Tình trạng': device.condition,
            'Trạng thái': device.status, 'Người quản lý': device.manager.full_name if device.manager else '',
            'Ngày cấp phát': device.assign_date.strftime('%d-%m-%Y') if device.assign_date else '',
            'Cấu hình': device.configuration or '', 'Ghi chú': device.notes or '',
            'Người nhập': device.importer or '', 'Thương hiệu': device.brand or '', 'Nhà cung cấp': device.supplier or '',
            'Bảo hành': device.warranty or ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Devices')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'devices_list_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/import_users', methods=['GET', 'POST'])
def import_users():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('Vui lòng chọn một file Excel hợp lệ (.xls, .xlsx).', 'danger')
            return redirect(url_for('import_users'))
        
        try:
            df = pd.read_excel(file, engine='openpyxl')
            required_columns = ['Tên đăng nhập', 'Mật khẩu', 'Họ và tên', 'Email', 'Vai trò']
            if not all(col in df.columns for col in required_columns):
                flash(f'File Excel phải chứa các cột bắt buộc: {", ".join(required_columns)}.', 'danger')
                return redirect(url_for('import_users'))

            errors = []
            users_to_add = []
            
            for index, row in df.iterrows():
                username = str(row['Tên đăng nhập'])
                password = str(row['Mật khẩu'])
                email = str(row['Email'])

                if not username or not password or not email:
                    errors.append(f'Dòng {index + 2}: Tên đăng nhập, Mật khẩu, và Email không được để trống.')
                    continue
                if User.query.filter_by(username=username).first():
                    errors.append(f'Dòng {index + 2}: Tên đăng nhập "{username}" đã tồn tại.')
                    continue
                if User.query.filter_by(email=email).first():
                    errors.append(f'Dòng {index + 2}: Email "{email}" đã tồn tại.')
                    continue
                
                onboard_date_val = row.get('Ngày Onboard')
                offboard_date_val = row.get('Ngày Offboard')

                new_user = User(
                    username=username,
                    password=generate_password_hash(password),
                    full_name=row.get('Họ và tên'),
                    email=email,
                    role=row.get('Vai trò', 'user'),
                    department=row.get('Phòng ban'),
                    position=row.get('Chức vụ'),
                    phone_number=str(row.get('SĐT', '')) if pd.notna(row.get('SĐT')) else None,
                    notes=row.get('Ghi chú'),
                    status=row.get('Trạng thái', 'Đang làm'),
                    onboard_date=pd.to_datetime(onboard_date_val).date() if pd.notna(onboard_date_val) else None,
                    offboard_date=pd.to_datetime(offboard_date_val).date() if pd.notna(offboard_date_val) else None
                )
                users_to_add.append(new_user)

            if errors:
                for error in errors:
                    flash(error, 'danger')
            else:
                db.session.add_all(users_to_add)
                db.session.commit()
                flash(f'Đã nhập thành công {len(users_to_add)} người dùng mới!', 'success')
                return redirect(url_for('user_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'Đã xảy ra lỗi trong quá trình xử lý file: {str(e)}', 'danger')

    return render_template('import_users.html')

@app.route('/export_users_excel')
def export_users_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    users = User.query.order_by(User.id.desc()).all()
    data = []
    for user in users:
        data.append({
            'ID': user.id,
            'Tên đăng nhập': user.username,
            'Họ và tên': user.full_name,
            'Email': user.email,
            'Phòng ban': user.department,
            'Chức vụ': user.position,
            'Trạng thái': user.status,
            'Ngày Onboard': user.onboard_date.strftime('%d-%m-%Y') if user.onboard_date else '',
            'Ngày Offboard': user.offboard_date.strftime('%d-%m-%Y') if user.offboard_date else '',
            'SĐT': user.phone_number,
            'Ngày sinh': user.date_of_birth.strftime('%d-%m-%Y') if user.date_of_birth else '',
            'Vai trò': user.role,
            'Ngày tạo': user.created_at.strftime('%d-%m-%Y %H:%M:%S'),
            'Đăng nhập lần cuối': user.last_login.strftime('%d-%m-%Y %H:%M:%S') if user.last_login else ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Users')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'users_list_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/export_handovers_excel')
def export_handovers_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    handovers = DeviceHandover.query.order_by(DeviceHandover.handover_date.desc()).all()
    data = []
    for handover in handovers:
        data.append({'Ngày Bàn Giao': handover.handover_date.strftime('%d-%m-%Y'), 'Mã Thiết Bị': handover.device.device_code if handover.device else '', 'Tên Thiết Bị': handover.device.name if handover.device else '', 'Loại Thiết Bị': handover.device.device_type if handover.device else '', 'Người Giao': handover.giver.full_name if handover.giver else '', 'Người Nhận': handover.receiver.full_name if handover.receiver else '', 'Phòng ban Người Nhận': handover.receiver.department if handover.receiver else '', 'Tình Trạng Thiết Bị': handover.device_condition, 'Lý Do': handover.reason, 'Nơi Đặt': handover.location, 'Ghi Chú': handover.notes})
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Handovers')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'handover_history_{datetime.now().strftime("%Y%m%d")}.xlsx')

# --- CLI Commands ---
@app.cli.command("init-db")
def init_db_command():
    """Tạo mới các bảng trong cơ sở dữ liệu."""
    db.create_all()
    click.echo("Đã khởi tạo cơ sở dữ liệu.")

@app.cli.command("create-admin")
def create_admin_command():
    """Tạo tài khoản admin mặc định."""
    if User.query.filter_by(username='admin').first():
        click.echo("Tài khoản admin đã tồn tại.")
        return
    
    admin_user = User(
        username='admin',
        password=generate_password_hash('admin123'),
        full_name='Quản Trị Viên',
        email='admin@example.com',
        role='admin',
        department='IT'
    )
    db.session.add(admin_user)
    db.session.commit()
    click.echo("Đã tạo tài khoản admin thành công (Username: admin, Pass: admin123).")

if __name__ == '__main__':
    app.run(debug=True)