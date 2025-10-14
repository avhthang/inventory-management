# Hướng dẫn Deployment với Database External

## Tổng quan

Hệ thống quản lý thiết bị đã được cập nhật để hỗ trợ database external, giúp tách rời dữ liệu khỏi server và đảm bảo tính khả dụng cao.

## Các tùy chọn Database

### 1. PostgreSQL (Khuyến nghị)
- **Ưu điểm**: Mạnh mẽ, hỗ trợ tốt, miễn phí
- **Dịch vụ cloud**: AWS RDS, Google Cloud SQL, DigitalOcean Managed Database

### 2. MySQL
- **Ưu điểm**: Phổ biến, dễ setup
- **Dịch vụ cloud**: AWS RDS, Google Cloud SQL, DigitalOcean Managed Database

### 3. Cloud Database Services
- **AWS RDS**: PostgreSQL/MySQL managed
- **Google Cloud SQL**: PostgreSQL/MySQL managed
- **DigitalOcean Managed Database**: PostgreSQL/MySQL managed
- **PlanetScale**: MySQL serverless
- **Supabase**: PostgreSQL với API

## Cài đặt Dependencies

```bash
# Cài đặt dependencies mới
pip install -r requirements.txt

# Cài đặt PostgreSQL client (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install postgresql-client

# Cài đặt MySQL client (nếu sử dụng MySQL)
sudo apt-get install mysql-client
```

## Cấu hình Environment

### 1. Tạo file .env

```bash
cp .env.example .env
```

### 2. Cấu hình Database URL

#### PostgreSQL Local
```env
DATABASE_URL=postgresql://username:password@localhost:5432/inventory_db
```

#### PostgreSQL Cloud (AWS RDS)
```env
DATABASE_URL=postgresql://username:password@your-rds-endpoint:5432/inventory_db
```

#### MySQL Cloud
```env
DATABASE_URL=mysql://username:password@your-mysql-endpoint:3306/inventory_db
```

#### SQLite (Development)
```env
DATABASE_URL=sqlite:///inventory.db
```

### 3. Cấu hình Backup (Optional)

```env
BACKUP_ENABLED=True
BACKUP_S3_BUCKET=your-backup-bucket
BACKUP_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

## Setup PostgreSQL

### Option 1: Sử dụng script tự động

```bash
python3 setup_postgres.py
```

### Option 2: Setup thủ công

```bash
# Kết nối PostgreSQL
psql -h localhost -U postgres

# Tạo database và user
CREATE DATABASE inventory_db;
CREATE USER inventory_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE inventory_db TO inventory_user;
\q

# Set environment variable
export DATABASE_URL="postgresql://inventory_user:your_password@localhost:5432/inventory_db"

# Tạo tables
python3 init_database.py
```

## Migration từ SQLite

### 1. Backup dữ liệu hiện tại

```bash
python3 backup_restore.py backup current_data_backup.zip
```

### 2. Setup PostgreSQL

```bash
python3 setup_postgres.py
```

### 3. Migrate dữ liệu

```bash
python3 migrate_to_postgres.py --confirm
```

### 4. Test ứng dụng

```bash
python3 -c "from app import app; print('App loaded successfully')"
```

## Backup và Restore

### Backup Local

```bash
# Tạo backup
python3 backup_restore.py backup

# Restore từ backup
python3 backup_restore.py restore backup_20231214_143022.zip
```

### Backup S3

```bash
# Upload backup lên S3
python3 backup_restore.py backup-s3

# Download và restore từ S3
python3 backup_restore.py restore-s3 backups/backup_20231214_143022.zip
```

## Deployment trên Production

### 1. Cấu hình Environment

```bash
# Set production environment
export FLASK_ENV=production
export DATABASE_URL="postgresql://user:pass@host:port/db"
export SECRET_KEY="your-production-secret-key"
```

### 2. Cài đặt Dependencies

```bash
pip install -r requirements.txt
```

### 3. Khởi tạo Database

```bash
python3 init_database.py
```

### 4. Chạy ứng dụng

```bash
# Development
python3 app.py

# Production với Gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

## Monitoring và Maintenance

### 1. Kiểm tra kết nối Database

```bash
python3 -c "
from config import get_database_info, is_external_database
print('Database type:', get_database_info()['type'])
print('Is external:', is_external_database())
"
```

### 2. Backup tự động

Thêm vào crontab:

```bash
# Backup hàng ngày lúc 2:00 AM
0 2 * * * cd /path/to/app && python3 backup_restore.py backup-s3

# Backup hàng tuần
0 3 * * 0 cd /path/to/app && python3 backup_restore.py backup-s3
```

### 3. Health Check

```bash
# Kiểm tra ứng dụng
curl http://localhost:8000/health

# Kiểm tra database
python3 -c "
from app import app, db
with app.app_context():
    db.engine.execute('SELECT 1')
    print('Database connection OK')
"
```

## Troubleshooting

### Lỗi kết nối Database

```bash
# Kiểm tra kết nối
python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://user:pass@host:port/db')
print('Connection OK')
conn.close()
"
```

### Lỗi Migration

```bash
# Kiểm tra dữ liệu SQLite
sqlite3 instance/inventory.db ".tables"

# Kiểm tra dữ liệu PostgreSQL
psql -h host -U user -d db -c "\dt"
```

### Lỗi Backup

```bash
# Kiểm tra AWS credentials
aws s3 ls s3://your-backup-bucket

# Test backup local
python3 backup_restore.py backup test_backup.zip
```

## Security Best Practices

1. **Sử dụng strong passwords** cho database
2. **Enable SSL** cho database connections
3. **Restrict database access** bằng firewall
4. **Regular backups** và test restore
5. **Monitor database logs** để phát hiện bất thường
6. **Update dependencies** thường xuyên

## Cost Optimization

### AWS RDS
- Sử dụng **t3.micro** cho development
- **Enable automated backups** với retention 7 days
- **Use reserved instances** cho production

### Google Cloud SQL
- Sử dụng **db-f1-micro** cho development
- **Enable point-in-time recovery**
- **Use committed use discounts**

### DigitalOcean Managed Database
- Sử dụng **Basic plan** cho development
- **Enable automated backups**
- **Monitor usage** để optimize

## Support

Nếu gặp vấn đề, hãy kiểm tra:

1. **Logs**: `journalctl -u inventory -f`
2. **Database connection**: Test với psql/mysql client
3. **Environment variables**: `env | grep DATABASE`
4. **Backup status**: Kiểm tra backup files
5. **Disk space**: `df -h`
6. **Memory usage**: `free -h`