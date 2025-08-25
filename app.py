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
instance_path = os.path.join('/var/www/inventory-management', 'instance')
os.makedirs(instance_path, exist_ok=True)

app = Flask(__name__, instance_path=instance_path)
app.config['SECRET_KEY'] = 'your_super_secret_key_change_this_please'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db' # Sẽ tự động lưu trong thư mục instance
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.permanent_session_lifetime = timedelta(days=30)

db = SQLAlchemy(app)

# --- Models ---
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

# --- Context Processor ---
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

@app.route('/devices', methods=['GET', 'POST'])
def device_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = 10
    filter_device_code = request.args.get('filter_device_code', '')
    filter_name = request.args.get('filter_name', '')
    filter_device_type = request.args.get('filter_device_type', '')
    filter_status = request.args.get('filter_status', '')
    
    query = Device.query
    if filter_device_code:
        query = query.filter(Device.device_code.ilike(f'%{filter_device_code}%'))
    if filter_name:
        query = query.filter(Device.name.ilike(f'%{filter_name}%'))
    if filter_device_type:
        query = query.filter_by(device_type=filter_device_type)
    if filter_status:
        query = query.filter_by(status=filter_status)
    
    devices_pagination = query.order_by(Device.device_code).paginate(page=page, per_page=per_page, error_out=False)
    device_types = sorted([item[0] for item in db.session.query(Device.device_type).distinct().all()])
    statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì']
    return render_template(
        'devices.html',
        devices=devices_pagination,
        device_types=device_types,
        statuses=statuses,
        filter_device_code=filter_device_code,
        filter_name=filter_name,
        filter_device_type=filter_device_type,
        filter_status=filter_status
    )

@app.route('/export_devices_excel')
def export_devices_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    devices = Device.query.order_by(Device.device_code).all()
    data = []
    for device in devices:
        data.append({
            'Mã thiết bị': device.device_code,
            'Tên thiết bị': device.name,
            'Loại thiết bị': device.device_type,
            'Số serial': device.serial_number or '',
            'Ngày mua': device.purchase_date.strftime('%d-%m-%Y') if device.purchase_date else '',
            'Ngày nhập': device.import_date.strftime('%d-%m-%Y') if device.import_date else '',
            'Tình trạng': device.condition,
            'Trạng thái': device.status,
            'Người quản lý': device.manager.full_name if device.manager else '',
            'Ngày cấp phát': device.assign_date.strftime('%d-%m-%Y') if device.assign_date else '',
            'Cấu hình': device.configuration or '',
            'Ghi chú': device.notes or '',
            'Người mua': device.buyer or '',
            'Người nhập': device.importer or '',
            'Thương hiệu': device.brand or '',
            'Nhà cung cấp': device.supplier or '',
            'Bảo hành': device.warranty or ''
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Devices')
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'devices_list_{datetime.now().strftime("%Y%m%d")}.xlsx'
    )

@app.route('/import_devices', methods=['GET', 'POST'])
def import_devices():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Vui lòng chọn file Excel để nhập.', 'danger')
            return redirect(url_for('import_devices'))
        file = request.files['file']
        if file.filename == '':
            flash('Vui lòng chọn một file Excel.', 'danger')
            return redirect(url_for('import_devices'))
        if not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            flash('File phải có định dạng .xls hoặc .xlsx.', 'danger')
            return redirect(url_for('import_devices'))
        try:
            df = pd.read_excel(file, engine='openpyxl')
            expected_columns = [
                'Mã thiết bị', 'Tên thiết bị', 'Loại thiết bị', 'Số serial', 'Ngày mua',
                'Ngày nhập', 'Tình trạng', 'Trạng thái', 'Người quản lý', 'Ngày cấp phát',
                'Cấu hình', 'Ghi chú', 'Người mua', 'Người nhập', 'Thương hiệu', 'Nhà cung cấp', 'Bảo hành'
            ]
            if not all(col in df.columns for col in expected_columns):
                flash('File Excel thiếu một hoặc nhiều cột bắt buộc.', 'danger')
                return redirect(url_for('import_devices'))
            
            valid_conditions = ['Mới', 'Sử dụng bình thường', 'Cần bảo trì', 'Hỏng']
            valid_statuses = ['Sẵn sàng', 'Đã cấp phát', 'Bảo trì']
            valid_device_types = [
                'Laptop', 'Case máy tính', 'Màn hình', 'Bàn phím', 'Chuột', 'Ổ cứng',
                'Ram', 'Card màn hình', 'Máy in', 'Thiết bị mạng', 'Thiết bị khác'
            ]
            
            for index, row in df.iterrows():
                # Validate required fields
                if not row['Mã thiết bị'] or not row['Tên thiết bị'] or not row['Loại thiết bị'] or not row['Tình trạng'] or not row['Trạng thái']:
                    flash(f'Dòng {index+2}: Thiếu thông tin bắt buộc (Mã thiết bị, Tên thiết bị, Loại thiết bị, Tình trạng, hoặc Trạng thái).', 'danger')
                    continue
                if Device.query.filter_by(device_code=row['Mã thiết bị']).first():
                    flash(f'Dòng {index+2}: Mã thiết bị {row["Mã thiết bị"]} đã tồn tại.', 'danger')
                    continue
                if row['Loại thiết bị'] not in valid_device_types:
                    flash(f'Dòng {index+2}: Loại thiết bị {row["Loại thiết bị"]} không hợp lệ.', 'danger')
                    continue
                if row['Tình trạng'] not in valid_conditions:
                    flash(f'Dòng {index+2}: Tình trạng {row["Tình trạng"]} không hợp lệ.', 'danger')
                    continue
                if row['Trạng thái'] not in valid_statuses:
                    flash(f'Dòng {index+2}: Trạng thái {row["Trạng thái"]} không hợp lệ.', 'danger')
                    continue
                
                # Handle manager
                manager_id = None
                if pd.notna(row['Người quản lý']) and row['Người quản lý']:
                    manager = User.query.filter_by(full_name=row['Người quản lý']).first()
                    if not manager:
                        flash(f'Dòng {index+2}: Người quản lý {row["Người quản lý"]} không tồn tại.', 'danger')
                        continue
                    manager_id = manager.id
                
                # Handle dates
                purchase_date = None
                if pd.notna(row['Ngày mua']):
                    try:
                        purchase_date = pd.to_datetime(row['Ngày mua'], format='%d-%m-%Y').date()
                    except ValueError:
                        flash(f'Dòng {index+2}: Ngày mua {row["Ngày mua"]} không đúng định dạng (dd-mm-yyyy).', 'danger')
                        continue
                
                import_date = None
                if pd.notna(row['Ngày nhập']):
                    try:
                        import_date = pd.to_datetime(row['Ngày nhập'], format='%d-%m-%Y').date()
                    except ValueError:
                        flash(f'Dòng {index+2}: Ngày nhập {row["Ngày nhập"]} không đúng định dạng (dd-mm-yyyy).', 'danger')
                        continue
                
                assign_date = None
                if pd.notna(row['Ngày cấp phát']):
                    try:
                        assign_date = pd.to_datetime(row['Ngày cấp phát'], format='%d-%m-%Y').date()
                    except ValueError:
                        flash(f'Dòng {index+2}: Ngày cấp phát {row["Ngày cấp phát"]} không đúng định dạng (dd-mm-yyyy).', 'danger')
                        continue
                
                # Create new device
                new_device = Device(
                    device_code=row['Mã thiết bị'],
                    name=row['Tên thiết bị'],
                    device_type=row['Loại thiết bị'],
                    serial_number=row['Số serial'] if pd.notna(row['Số serial']) else None,
                    purchase_date=purchase_date,
                    import_date=import_date,
                    condition=row['Tình trạng'],
                    status=row['Trạng thái'],
                    manager_id=manager_id,
                    assign_date=assign_date,
                    configuration=row['Cấu hình'] if pd.notna(row['Cấu hình']) else None,
                    notes=row['Ghi chú'] if pd.notna(row['Ghi chú']) else None,
                    buyer=row['Người mua'] if pd.notna(row['Người mua']) else None,
                    importer=row['Người nhập'] if pd.notna(row['Người nhập']) else None,
                    brand=row['Thương hiệu'] if pd.notna(row['Thương hiệu']) else None,
                    supplier=row['Nhà cung cấp'] if pd.notna(row['Nhà cung cấp']) else None,
                    warranty=row['Bảo hành'] if pd.notna(row['Bảo hành']) else None
                )
                db.session.add(new_device)
            
            db.session.commit()
            flash('Nhập thiết bị từ Excel thành công!', 'success')
            return redirect(url_for('device_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi nhập file Excel: {str(e)}', 'danger')
            return redirect(url_for('import_devices'))
    return render_template('import_devices.html')

@app.route('/handover_list')
def handover_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = 10
    filter_device_code = request.args.get('filter_device_code', '')
    filter_giver_id = request.args.get('filter_giver_id', '')
    filter_receiver_id = request.args.get('filter_receiver_id', '')
    filter_device_type = request.args.get('filter_device_type', '')
    filter_start_date = request.args.get('filter_start_date', '')
    filter_end_date = request.args.get('filter_end_date', '')
    
    query = DeviceHandover.query.join(Device).join(User, DeviceHandover.giver_id == User.id).join(User, DeviceHandover.receiver_id == User.id, aliased=True)
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

# <<< CÁC LỆNH QUẢN TRỊ MỚI >>>
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
    click.echo("Đã tạo tài khoản admin thành công (Pass: admin123).")