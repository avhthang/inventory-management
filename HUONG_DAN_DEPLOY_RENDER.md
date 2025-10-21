# 🚀 Hướng dẫn Deploy lên Render.com và Database miễn phí

## 📋 Tổng quan

Hướng dẫn này sẽ giúp bạn deploy ứng dụng Flask quản lý thiết bị lên Render.com và sử dụng database PostgreSQL miễn phí từ các nhà cung cấp cloud.

## 🎯 Mục tiêu

- Deploy ứng dụng Flask lên Render.com (miễn phí)
- Sử dụng PostgreSQL database miễn phí
- Cấu hình domain tùy chỉnh (tùy chọn)
- Backup và monitoring cơ bản

## 📚 Các dịch vụ miễn phí được sử dụng

### 1. Render.com
- **Web Service**: Miễn phí 750 giờ/tháng
- **Database**: PostgreSQL miễn phí (1GB storage)
- **Static Site**: Miễn phí cho frontend

### 2. Database miễn phí (chọn 1)
- **Neon**: PostgreSQL serverless, 3GB storage miễn phí
- **Supabase**: PostgreSQL với API, 500MB storage miễn phí  
- **Railway**: PostgreSQL, 1GB storage miễn phí
- **PlanetScale**: MySQL serverless, 1GB storage miễn phí

## 🛠️ Chuẩn bị

### Bước 1: Chuẩn bị code

1. **Fork repository** (nếu chưa có)
2. **Clone về máy local**:
```bash
git clone https://github.com/your-username/inventory-management.git
cd inventory-management
```

3. **Tạo branch mới**:
```bash
git checkout -b render-deployment
```

### Bước 2: Cài đặt Render CLI (tùy chọn)

```bash
# Cài đặt Render CLI
npm install -g @render/cli

# Login vào Render
render login
```

## 🗄️ Bước 1: Setup Database miễn phí

### Option A: Neon (Khuyến nghị)

1. **Truy cập**: https://neon.tech
2. **Đăng ký** tài khoản miễn phí
3. **Tạo project mới**:
   - Project name: `inventory-management`
   - Database name: `inventory_db`
   - Region: chọn gần nhất (Singapore cho VN)

4. **Lấy connection string**:
```
postgresql://username:password@ep-xxx-xxx.us-east-1.aws.neon.tech/inventory_db?sslmode=require
```

### Option B: Supabase

1. **Truy cập**: https://supabase.com
2. **Đăng ký** tài khoản miễn phí
3. **Tạo project mới**:
   - Project name: `inventory-management`
   - Database password: tạo mật khẩu mạnh
   - Region: Singapore

4. **Lấy connection string**:
```
postgresql://postgres:[password]@db.xxx.supabase.co:5432/postgres
```

### Option C: Railway

1. **Truy cập**: https://railway.app
2. **Đăng ký** tài khoản miễn phí
3. **Tạo project mới**:
   - Click "New Project"
   - Chọn "Provision PostgreSQL"

4. **Lấy connection string** từ Variables tab

## 🔧 Bước 2: Cấu hình ứng dụng cho Render

### 1. Tạo file `render.yaml`

```yaml
services:
  - type: web
    name: inventory-management
    env: python
    plan: free
    buildCommand: pip install -r requirements.txt && python init_database.py
    startCommand: gunicorn app:app
    envVars:
      - key: FLASK_ENV
        value: production
      - key: SECRET_KEY
        generateValue: true
      - key: DATABASE_URL
        fromDatabase:
          name: inventory-db
          property: connectionString
      - key: BACKUP_ENABLED
        value: "False"
    healthCheckPath: /health
    autoDeploy: true
    branch: main

databases:
  - name: inventory-db
    plan: free
    databaseName: inventory_db
    user: inventory_user
```

### 2. Cập nhật `requirements.txt`

```txt
Flask==3.1.2
Flask-SQLAlchemy==3.1.1
Werkzeug==3.1.3
gunicorn==23.0.0
pandas==2.3.3
openpyxl==3.1.5
click==8.3.0
schedule==1.2.2
pytz==2025.2
psycopg2-binary==2.9.11
python-dotenv==1.1.1
boto3==1.40.51
PyJWT==2.10.1
cryptography==44.0.0
```

### 3. Tạo file `.env.example`

```env
# Production Environment Variables
FLASK_ENV=production
SECRET_KEY=your-secret-key-here
DATABASE_URL=postgresql://user:pass@host:port/db
BACKUP_ENABLED=False
```

### 4. Cập nhật `app.py` cho production

Thêm vào cuối file `app.py`:

```python
if __name__ == '__main__':
    # Chỉ chạy development server khi chạy trực tiếp
    if os.environ.get('FLASK_ENV') != 'production':
        app.run(debug=True, host='0.0.0.0', port=5000)
    # Production sẽ sử dụng Gunicorn
```

## 🚀 Bước 3: Deploy lên Render.com

### Cách 1: Deploy qua Web UI (Khuyến nghị)

1. **Truy cập**: https://render.com
2. **Đăng ký/Đăng nhập** tài khoản
3. **Kết nối GitHub**:
   - Click "New +"
   - Chọn "Web Service"
   - Connect GitHub repository

4. **Cấu hình service**:
   - **Name**: `inventory-management`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt && python init_database.py`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: `Free`

5. **Cấu hình Environment Variables**:
   ```
   FLASK_ENV=production
   SECRET_KEY=[Render sẽ tự tạo]
   DATABASE_URL=[URL từ database service]
   BACKUP_ENABLED=False
   ```

6. **Tạo Database**:
   - Click "New +"
   - Chọn "PostgreSQL"
   - **Name**: `inventory-db`
   - **Plan**: `Free`
   - **Database Name**: `inventory_db`
   - **User**: `inventory_user`

7. **Deploy**:
   - Click "Create Web Service"
   - Render sẽ tự động build và deploy

### Cách 2: Deploy qua Render CLI

```bash
# Login vào Render
render login

# Deploy service
render deploy

# Xem logs
render logs --service inventory-management
```

## 🔗 Bước 4: Cấu hình Domain tùy chỉnh (Tùy chọn)

1. **Mua domain** (nếu chưa có)
2. **Vào Render Dashboard**:
   - Chọn service
   - Settings → Custom Domains
   - Add domain

3. **Cấu hình DNS**:
   ```
   Type: CNAME
   Name: www (hoặc subdomain)
   Value: inventory-management.onrender.com
   ```

## 📊 Bước 5: Monitoring và Maintenance

### 1. Health Check

Render tự động monitor endpoint `/health`. Đảm bảo route này tồn tại:

```python
@app.route('/health')
def health_check():
    try:
        # Test database connection
        db.engine.execute('SELECT 1')
        return jsonify({'status': 'healthy', 'database': 'connected'}), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500
```

### 2. Logs

```bash
# Xem logs qua Render CLI
render logs --service inventory-management

# Hoặc qua Web UI
# Dashboard → Service → Logs
```

### 3. Backup Database

```bash
# Backup manual
pg_dump $DATABASE_URL > backup.sql

# Restore
psql $DATABASE_URL < backup.sql
```

## 🔧 Bước 6: Troubleshooting

### Lỗi thường gặp

#### 1. Build Failed
```bash
# Kiểm tra logs
render logs --service inventory-management

# Thường do:
# - Thiếu dependencies
# - Lỗi syntax Python
# - Database connection failed
```

#### 2. Database Connection Error
```bash
# Kiểm tra DATABASE_URL
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL -c "SELECT 1;"
```

#### 3. Service Won't Start
```bash
# Kiểm tra start command
# Phải là: gunicorn app:app

# Kiểm tra port
# Render sử dụng PORT environment variable
```

### Debug Commands

```bash
# Test local với production config
export FLASK_ENV=production
export DATABASE_URL="your-database-url"
python app.py

# Test database connection
python -c "
from app import app, db
with app.app_context():
    db.engine.execute('SELECT 1')
    print('Database OK')
"
```

## 💰 Chi phí và Giới hạn

### Render.com Free Plan
- **Web Service**: 750 giờ/tháng
- **Database**: 1GB storage
- **Bandwidth**: 100GB/tháng
- **Sleep**: Service sleep sau 15 phút không hoạt động

### Database Free Plans
- **Neon**: 3GB storage, không giới hạn connections
- **Supabase**: 500MB storage, 2GB bandwidth
- **Railway**: 1GB storage, $5 credit/tháng

## 🚀 Bước 7: Tối ưu hóa

### 1. Performance
```python
# Thêm caching
from flask_caching import Cache
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Optimize database queries
# Sử dụng indexes cho các trường thường query
```

### 2. Security
```python
# Thêm security headers
from flask_talisman import Talisman
Talisman(app)

# Rate limiting
from flask_limiter import Limiter
limiter = Limiter(app, key_func=get_remote_address)
```

### 3. Monitoring
```python
# Thêm metrics
from prometheus_flask_exporter import PrometheusMetrics
metrics = PrometheusMetrics(app)
```

## 📝 Checklist Deploy

- [ ] Fork và clone repository
- [ ] Tạo database miễn phí (Neon/Supabase/Railway)
- [ ] Cập nhật `requirements.txt`
- [ ] Tạo `render.yaml`
- [ ] Cấu hình environment variables
- [ ] Deploy lên Render
- [ ] Test ứng dụng
- [ ] Cấu hình domain (tùy chọn)
- [ ] Setup monitoring
- [ ] Tạo backup strategy

## 🆘 Hỗ trợ

### Render Support
- **Documentation**: https://render.com/docs
- **Community**: https://community.render.com
- **Status**: https://status.render.com

### Database Support
- **Neon**: https://neon.tech/docs
- **Supabase**: https://supabase.com/docs
- **Railway**: https://docs.railway.app

### Troubleshooting Resources
- **Render Logs**: Dashboard → Service → Logs
- **Database Logs**: Từ provider dashboard
- **Health Check**: `https://your-app.onrender.com/health`

## 🎉 Kết luận

Sau khi hoàn thành các bước trên, bạn sẽ có:

✅ Ứng dụng Flask chạy trên Render.com  
✅ Database PostgreSQL miễn phí  
✅ Domain tùy chỉnh (nếu cấu hình)  
✅ Monitoring cơ bản  
✅ Backup strategy  

Ứng dụng sẽ có URL dạng: `https://inventory-management.onrender.com`

**Lưu ý**: Free plan có thể sleep sau 15 phút không hoạt động, lần đầu truy cập sau khi sleep có thể mất 30-60 giây để wake up.