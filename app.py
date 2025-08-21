from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import aliased
from sqlalchemy import or_, func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import pandas as pd
import io

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key_change_this_please'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.permanent_session_lifetime = timedelta(days=30) # <<< THỜI GIAN LƯU PHIÊN ĐĂNG NHẬP

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

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    device_type = db.Column(db.String(50), nullable=False)
    serial_number = db.Column(db.String(80))
    purchase_date = db.Column(db.Date, nullable=False)
    import_date = db.Column(db.Date, nullable=False)
    condition = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False)
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

class DeviceHandover(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    handover_date = db.Column(db.Date, nullable=False, default=date.today)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    device = db.relationship('Device', backref='handovers')
    giver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    giver = db.relationship('User', foreign_keys=[giver_id])
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    device_condition = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.String(255))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)

@app.context_processor
def inject_user():
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
        return dict(current_user=current_user)
    return dict(current_user=None)

# --- Main & Auth Routes ---
@app.route('/')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    total_devices = Device.query.count()
    in_use_devices = Device.query.filter_by(status='Đã cấp phát').count()
    maintenance_devices = Device.query.filter_by(status='Bảo trì').count()
    return render_template('dashboard.html', total_devices=total_devices, in_use_devices=in_use_devices, maintenance_devices=maintenance_devices)

@app.route('/login', methods=['GET', 'POST'])
def login():
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

# <<< ROUTE MỚI: ĐĂNG KÝ TÀI KHOẢN >>>
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name')
        email = request.form.get('email')

        if not username or not password or not confirm_password:
            flash('Tên đăng nhập và mật khẩu là bắt buộc.', 'danger')
            return render_template('register.html')
        
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp.', 'danger')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return render_template('register.html')
        
        if email and User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng.', 'danger')
            return render_template('register.html')

        new_user = User(
            username=username,
            password=generate_password_hash(password),
            full_name=full_name,
            email=email,
            role='user' # Mặc định tài khoản mới là 'user'
        )
        db.session.add(new_user)
        db.session.commit()
        
        # Tự động đăng nhập sau khi đăng ký
        session['user_id'] = new_user.id
        session.permanent = True
        flash('Đăng ký tài khoản thành công!', 'success')
        return redirect(url_for('home'))

    return render_template('register.html')

# <<< ROUTE MỚI: QUÊN MẬT KHẨU >>>
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')

        if not username or not email:
            flash('Vui lòng nhập tên đăng nhập và email.', 'danger')
            return redirect(url_for('forgot_password'))

        user = User.query.filter_by(username=username, email=email).first()

        if user:
            # Reset mật khẩu về giá trị mặc định
            user.password = generate_password_hash('SecretPassword')
            db.session.commit()
            flash('Mật khẩu của bạn đã được reset về "SecretPassword". Vui lòng đăng nhập và đổi lại mật khẩu ngay.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Thông tin Tên đăng nhập hoặc Email không chính xác. Vui lòng nhập lại.', 'warning')
            return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')


@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        if not check_password_hash(user.password, current_password):
            flash('Mật khẩu hiện tại không đúng.', 'danger'); return redirect(url_for('change_password'))
        if not new_password:
            flash('Mật khẩu mới không được để trống.', 'danger'); return redirect(url_for('change_password'))
        if new_password != confirm_password:
            flash('Mật khẩu mới và xác nhận mật khẩu không khớp.', 'danger'); return redirect(url_for('change_password'))
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash('Đổi mật khẩu thành công!', 'success')
        return redirect(url_for('home'))
    return render_template('change_password.html')

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        if not check_password_hash(user.password, current_password):
            flash('Mật khẩu hiện tại không đúng.', 'danger'); return redirect(url_for('change_password'))
        if not new_password:
            flash('Mật khẩu mới không được để trống.', 'danger'); return redirect(url_for('change_password'))
        if new_password != confirm_password:
            flash('Mật khẩu mới và xác nhận mật khẩu không khớp.', 'danger'); return redirect(url_for('change_password'))
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash('Đổi mật khẩu thành công!', 'success')
        return redirect(url_for('home'))
    return render_template('change_password.html')

# --- User Management Routes ---
@app.route('/users')
def user_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in [10, 20, 50, 100]: per_page = 10
    filter_username=request.args.get('filter_username', '').strip()
    filter_role=request.args.get('filter_role', '').strip()
    filter_department=request.args.get('filter_department', '').strip()
    query=User.query
    if filter_username: query=query.filter(User.username.contains(filter_username))
    if filter_role: query=query.filter_by(role=filter_role)
    if filter_department: query=query.filter_by(department=filter_department)
    users_pagination=query.order_by(User.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('users.html', users=users_pagination, filter_username=filter_username, filter_role=filter_role, filter_department=filter_department)

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        username=request.form.get('username')
        password=request.form.get('password')
        email=request.form.get('email')
        if not username or not password:
            flash('Tên đăng nhập và mật khẩu là bắt buộc.', 'danger'); return render_template('add_user.html')
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại!', 'danger'); return render_template('add_user.html')
        if email and User.query.filter_by(email=email).first():
            flash('Email đã tồn tại!', 'danger'); return render_template('add_user.html')
        dob_str = request.form.get('date_of_birth')
        new_user=User(
            username=username, password=generate_password_hash(password),
            full_name=request.form.get('full_name'), email=email, role=request.form.get('role'),
            department=request.form.get('department'), position=request.form.get('position'),
            date_of_birth=(datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None),
            phone_number=request.form.get('phone_number'), notes=request.form.get('notes'))
        db.session.add(new_user); db.session.commit()
        flash('Thêm người dùng thành công!', 'success')
        return redirect(url_for('user_list'))
    return render_template('add_user.html')

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user=User.query.get_or_404(user_id)
    if request.method == 'POST':
        username=request.form['username']
        email=request.form.get('email')
        existing_user=User.query.filter_by(username=username).first()
        if existing_user and existing_user.id != user.id:
            flash('Tên đăng nhập đã tồn tại!', 'danger'); return render_template('edit_user.html', user=user)
        if email:
            existing_email=User.query.filter_by(email=email).first()
            if existing_email and existing_email.id != user.id:
                flash('Email đã tồn tại!', 'danger'); return render_template('edit_user.html', user=user)
        user.username=username; user.full_name=request.form.get('full_name'); user.email=email
        user.role=request.form.get('role'); user.department=request.form.get('department')
        user.position=request.form.get('position')
        dob_str = request.form.get('date_of_birth')
        user.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None
        user.phone_number=request.form.get('phone_number'); user.notes=request.form.get('notes')
        if request.form.get('password'): user.password=generate_password_hash(request.form['password'])
        db.session.commit()
        flash('Cập nhật người dùng thành công!', 'success')
        return redirect(url_for('user_list'))
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user=User.query.get_or_404(user_id)
    db.session.delete(user); db.session.commit()
    flash('Xóa người dùng thành công!', 'success')
    return redirect(url_for('user_list'))

# --- Device Management Routes ---
@app.route('/devices')
def device_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in [10, 20, 50, 100]: per_page = 10
    filter_code = request.args.get('filter_code', '').strip()
    filter_condition = request.args.get('filter_condition', '').strip()
    filter_status = request.args.get('filter_status', '').strip()
    filter_manager = request.args.get('filter_manager', '').strip()
    filter_device_type = request.args.get('filter_device_type', '').strip()
    query = Device.query
    if filter_code: query = query.filter(or_(Device.device_code.contains(filter_code), Device.name.contains(filter_code)))
    if filter_device_type: query = query.filter_by(device_type=filter_device_type)
    if filter_condition: query = query.filter_by(condition=filter_condition)
    if filter_status: query = query.filter_by(status=filter_status)
    if filter_manager: query = query.filter_by(manager_id=filter_manager)
    devices_pagination = query.order_by(Device.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    managers = User.query.order_by(User.full_name).all()
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    return render_template('devices.html', devices=devices_pagination, managers=managers, device_types=device_types, filter_code=filter_code, filter_device_type=filter_device_type, filter_condition=filter_condition, filter_status=filter_status, filter_manager=filter_manager)

@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    if 'user_id' not in session: return redirect(url_for('login'))
    managers = User.query.order_by(User.full_name).all()
    if request.method == 'POST':
        device_code = request.form['device_code'].strip()
        device_type = request.form['device_type']
        name = request.form['name']
        if not device_code:
            prefix_map = {'Laptop': 'LT', 'Case máy tính': 'CASE', 'Màn hình': 'MH', 'Bàn phím': 'BP', 'Chuột': 'C', 'Ổ cứng': 'DISK', 'Ram': 'RAM', 'Card màn hình': 'VGA', 'Máy in': 'PRINT', 'Thiết bị mạng': 'NET', 'Thiết bị khác': 'TB'}
            prefix = prefix_map.get(device_type, 'TB')
            last_device = Device.query.filter(Device.device_code.startswith(prefix + '_')).order_by(func.length(Device.device_code).desc(), Device.device_code.desc()).first()
            new_number = 1
            if last_device:
                try: new_number = int(last_device.device_code.split('_')[-1]) + 1
                except (ValueError, IndexError): new_number = 1
            device_code = f"{prefix}_{new_number:03d}"
        if Device.query.filter_by(device_code=device_code).first():
            flash(f'Mã thiết bị "{device_code}" đã tồn tại!', 'danger'); return render_template('add_device.html', managers=managers)
        assign_date_str = request.form.get('assign_date')
        new_device = Device(device_code=device_code, name=name, device_type=device_type, serial_number=request.form.get('serial_number'), brand=request.form.get('brand'), supplier=request.form.get('supplier'), warranty=request.form.get('warranty'), purchase_date=datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date(), import_date=datetime.strptime(request.form['import_date'], '%Y-%m-%d').date(), condition=request.form['condition'], status=request.form.get('status', 'Sẵn sàng'), manager_id=request.form.get('manager_id') or None, assign_date=datetime.strptime(assign_date_str, '%Y-%m-%d').date() if assign_date_str else None, configuration=request.form.get('configuration'), notes=request.form.get('notes'), buyer=request.form.get('buyer'), importer=request.form.get('importer'))
        db.session.add(new_device); db.session.commit()
        flash(f'Thêm thiết bị "{name}" với mã "{device_code}" thành công!', 'success')
        return redirect(url_for('device_list'))
    return render_template('add_device.html', managers=managers)

@app.route('/edit_device/<int:device_id>', methods=['GET', 'POST'])
def edit_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    managers = User.query.order_by(User.full_name).all()
    if request.method == 'POST':
        device_code = request.form['device_code']
        existing = Device.query.filter_by(device_code=device_code).first()
        if existing and existing.id != device.id:
            flash('Mã thiết bị đã tồn tại!', 'danger'); return render_template('edit_device.html', device=device, managers=managers)
        device.device_code = device_code; device.name = request.form['name']; device.device_type = request.form['device_type']
        device.serial_number = request.form.get('serial_number'); device.brand = request.form.get('brand')
        device.supplier = request.form.get('supplier'); device.warranty = request.form.get('warranty')
        device.import_date = datetime.strptime(request.form['import_date'], '%Y-%m-%d').date()
        device.purchase_date = datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date()
        device.condition = request.form['condition']; device.status = request.form['status']
        device.manager_id = request.form.get('manager_id') or None
        assign_date_str = request.form.get('assign_date')
        device.assign_date = datetime.strptime(assign_date_str, '%Y-%m-%d').date() if assign_date_str else None
        device.configuration = request.form.get('configuration'); device.notes = request.form.get('notes')
        db.session.commit()
        flash('Cập nhật thiết bị thành công!', 'success')
        return redirect(url_for('device_list'))
    return render_template('edit_device.html', device=device, managers=managers)

@app.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    db.session.delete(device); db.session.commit()
    flash('Xóa thiết bị thành công!', 'success')
    return redirect(url_for('device_list'))

@app.route('/device/<int:device_id>')
def device_detail(device_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    device = Device.query.get_or_404(device_id)
    return render_template('device_detail.html', device=device)

@app.route('/export_devices_excel')
def export_devices_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    devices = Device.query.all()
    data = []
    for device in devices:
        data.append({'Mã thiết bị': device.device_code, 'Tên thiết bị': device.name, 'Loại thiết bị': device.device_type, 'Số serial': device.serial_number, 'Thương hiệu': device.brand, 'Nhà cung cấp': device.supplier, 'Bảo hành': device.warranty, 'Ngày mua': device.purchase_date.strftime('%d-%m-%Y'), 'Ngày nhập': device.import_date.strftime('%d-%m-%Y'), 'Tình trạng': device.condition, 'Trạng thái': device.status, 'Người quản lý': device.manager.full_name if device.manager else '', 'Ngày cấp phát': device.assign_date.strftime('%d-%m-%Y') if device.assign_date else '', 'Cấu hình': device.configuration, 'Ghi chú': device.notes, 'Người mua': device.buyer, 'Người nhập': device.importer})
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Devices')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'devices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')

@app.route('/import_devices_excel', methods=['GET', 'POST'])
def import_devices_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        if 'file' not in request.files: flash('Không tìm thấy file!', 'danger'); return redirect(request.url)
        file = request.files['file']
        if file.filename == '': flash('Không có file được chọn!', 'danger'); return redirect(request.url)
        if file and file.filename.endswith(('.xls', '.xlsx')):
            try:
                df = pd.read_excel(file)
                for index, row in df.iterrows():
                    if row.get('Mã thiết bị') is None or pd.isna(row.get('Mã thiết bị')): continue
                    if Device.query.filter_by(device_code=row['Mã thiết bị']).first(): continue
                    manager_id=None
                    manager_name=row.get('Người quản lý')
                    if manager_name and not pd.isna(manager_name):
                        manager=User.query.filter_by(full_name=str(manager_name).strip()).first()
                        if manager: manager_id=manager.id
                    new_device=Device(device_code=row['Mã thiết bị'], name=row['Tên thiết bị'], device_type=row.get('Loại thiết bị', 'Thiết bị khác'), serial_number=str(row.get('Số serial', '')) if not pd.isna(row.get('Số serial')) else '', brand=str(row.get('Thương hiệu', '')) if not pd.isna(row.get('Thương hiệu')) else '', supplier=str(row.get('Nhà cung cấp', '')) if not pd.isna(row.get('Nhà cung cấp')) else '', warranty=str(row.get('Bảo hành', '')) if not pd.isna(row.get('Bảo hành')) else '', purchase_date=pd.to_datetime(row['Ngày mua']).date(), import_date=pd.to_datetime(row['Ngày nhập']).date(), condition=row.get('Tình trạng', 'Sử dụng bình thường'), status=row.get('Trạng thái', 'Sẵn sàng'), manager_id=manager_id, assign_date=pd.to_datetime(row.get('Ngày cấp phát')).date() if row.get('Ngày cấp phát') and not pd.isna(row.get('Ngày cấp phát')) else None, configuration=str(row.get('Cấu hình', '')) if not pd.isna(row.get('Cấu hình')) else '', notes=str(row.get('Ghi chú', '')) if not pd.isna(row.get('Ghi chú')) else '', buyer=str(row.get('Người mua', '')) if not pd.isna(row.get('Người mua')) else '', importer=str(row.get('Người nhập', '')) if not pd.isna(row.get('Người nhập')) else '')
                    db.session.add(new_device)
                db.session.commit()
                flash('Nhập dữ liệu thành công!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Lỗi khi nhập dữ liệu: {str(e)}', 'danger')
            return redirect(url_for('device_list'))
    return render_template('import_devices.html')

# --- Handover Management Routes ---
@app.route('/api/device_info/<int:device_id>')
def get_device_info(device_id):
    device = Device.query.get(device_id)
    return jsonify({'name': device.name}) if device else (jsonify({'name': ''}), 404)

@app.route('/handovers')
def handover_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in [10, 20, 50, 100]: per_page = 10
    filter_device_code = request.args.get('filter_device_code', '').strip()
    filter_giver_id = request.args.get('filter_giver_id', '').strip()
    filter_receiver_id = request.args.get('filter_receiver_id', '').strip()
    filter_device_type = request.args.get('filter_device_type', '').strip()
    filter_start_date = request.args.get('filter_start_date', '').strip()
    filter_end_date = request.args.get('filter_end_date', '').strip()
    Giver=aliased(User, name='giver'); Receiver=aliased(User, name='receiver')
    query=DeviceHandover.query.join(Device, DeviceHandover.device_id == Device.id).join(Giver, DeviceHandover.giver_id == Giver.id).join(Receiver, DeviceHandover.receiver_id == Receiver.id)
    if filter_device_code: query=query.filter(Device.device_code.contains(filter_device_code))
    if filter_giver_id: query=query.filter(DeviceHandover.giver_id == filter_giver_id)
    if filter_receiver_id: query=query.filter(DeviceHandover.receiver_id == filter_receiver_id)
    if filter_device_type: query=query.filter(Device.device_type == filter_device_type)
    if filter_start_date: query = query.filter(DeviceHandover.handover_date >= datetime.strptime(filter_start_date, '%Y-%m-%d').date())
    if filter_end_date: query = query.filter(DeviceHandover.handover_date <= datetime.strptime(filter_end_date, '%Y-%m-%d').date())
    handovers_pagination=query.order_by(DeviceHandover.handover_date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    users=User.query.order_by(User.full_name).all()
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    return render_template('handovers.html', handovers=handovers_pagination, users=users, device_types=device_types, filter_device_code=filter_device_code, filter_giver_id=filter_giver_id, filter_receiver_id=filter_receiver_id, filter_device_type=filter_device_type, filter_start_date=filter_start_date, filter_end_date=filter_end_date)

@app.route('/add_handover', methods=['GET', 'POST'])
def add_handover():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        new_handover=DeviceHandover(handover_date=datetime.strptime(request.form['handover_date'], '%Y-%m-%d').date(), device_id=request.form['device_id'], giver_id=request.form['giver_id'], receiver_id=request.form['receiver_id'], device_condition=request.form['device_condition'], reason=request.form.get('reason', ''), location=request.form.get('location', ''), notes=request.form.get('notes', ''))
        device_to_update=Device.query.get(request.form['device_id'])
        if device_to_update:
            device_to_update.manager_id = request.form['receiver_id']
            device_to_update.assign_date = new_handover.handover_date
            device_to_update.status='Đã cấp phát'
        db.session.add(new_handover); db.session.commit()
        flash('Tạo phiếu bàn giao thành công!', 'success')
        return redirect(url_for('handover_list'))
    devices=Device.query.order_by(Device.device_code).all()
    users=User.query.order_by(User.full_name).all()
    return render_template('add_handover.html', devices=devices, users=users, now=datetime.now())

@app.route('/edit_handover/<int:handover_id>', methods=['GET', 'POST'])
def edit_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover=DeviceHandover.query.get_or_404(handover_id)
    if request.method == 'POST':
        handover.handover_date=datetime.strptime(request.form['handover_date'], '%Y-%m-%d').date()
        handover.device_id=request.form['device_id']; handover.giver_id=request.form['giver_id']
        handover.receiver_id=request.form['receiver_id']; handover.device_condition=request.form['device_condition']
        handover.reason=request.form.get('reason', ''); handover.location=request.form.get('location', '')
        handover.notes=request.form.get('notes', ''); db.session.commit()
        flash('Cập nhật phiếu bàn giao thành công!', 'success')
        return redirect(url_for('handover_list'))
    devices=Device.query.order_by(Device.device_code).all()
    users=User.query.order_by(User.full_name).all()
    return render_template('edit_handover.html', handover=handover, devices=devices, users=users)

@app.route('/delete_handover/<int:handover_id>', methods=['POST'])
def delete_handover(handover_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    handover=DeviceHandover.query.get_or_404(handover_id)
    db.session.delete(handover); db.session.commit()
    flash('Xóa phiếu bàn giao thành công!', 'success')
    return redirect(url_for('handover_list'))

@app.route('/export_users_excel')
def export_users_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    users = User.query.order_by(User.id.desc()).all()
    data = []
    for user in users:
        data.append({'ID': user.id, 'Tên đăng nhập': user.username, 'Họ và tên': user.full_name, 'Email': user.email, 'Phòng ban': user.department, 'Chức vụ': user.position, 'SĐT': user.phone_number, 'Ngày sinh': user.date_of_birth.strftime('%d-%m-%Y') if user.date_of_birth else '', 'Vai trò': user.role, 'Ngày tạo': user.created_at.strftime('%d-%m-%Y %H:%M:%S'), 'Đăng nhập lần cuối': user.last_login.strftime('%d-%m-%Y %H:%M:%S') if user.last_login else ''})
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Create a default admin user if no users exist
        if not User.query.first():
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
    app.run(debug=False) # Changed to False for production readiness