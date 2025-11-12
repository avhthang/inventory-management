# Hướng dẫn Triển khai Hệ thống Quản lý Thiết bị

Hướng dẫn này cung cấp các bước chi tiết để triển khai ứng dụng lên các nền tảng khác nhau: Google Cloud Platform, AWS, và Server riêng.

## Mục lục
1. [Triển khai trên Google Cloud Platform](#1-triển-khai-trên-google-cloud-platform)
2. [Triển khai trên AWS](#2-triển-khai-trên-aws)
3. [Triển khai trên Server riêng](#3-triển-khai-trên-server-riêng)
4. [Cấu hình PostgreSQL](#4-cấu-hình-postgresql)
5. [Quản lý và Bảo trì](#5-quản-lý-và-bảo-trì)

---

## 1. Triển khai trên Google Cloud Platform

### 1.1. Tạo Cloud SQL (PostgreSQL)

```bash
# Cài đặt Google Cloud SDK (nếu chưa có)
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init

# Tạo Cloud SQL instance (PostgreSQL)
gcloud sql instances create inventory-db \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=asia-southeast1 \
    --root-password=YOUR_DB_PASSWORD

# Tạo database
gcloud sql databases create inventory \
    --instance=inventory-db

# Tạo user
gcloud sql users create app_user \
    --instance=inventory-db \
    --password=YOUR_APP_PASSWORD
```

### 1.2. Tạo Compute Engine Instance

```bash
# Tạo VM instance
gcloud compute instances create inventory-app \
    --zone=asia-southeast1-a \
    --machine-type=e2-small \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=20GB \
    --tags=http-server,https-server

# Mở firewall
gcloud compute firewall-rules create allow-http \
    --allow tcp:80 \
    --source-ranges 0.0.0.0/0 \
    --target-tags http-server

gcloud compute firewall-rules create allow-https \
    --allow tcp:443 \
    --source-ranges 0.0.0.0/0 \
    --target-tags https-server
```

### 1.3. Cấu hình trên VM

SSH vào VM và thực hiện:

```bash
# Cập nhật hệ thống
sudo apt update && sudo apt upgrade -y

# Cài đặt dependencies
sudo apt install -y python3-pip python3-venv nginx git postgresql-client

# Tải code
cd /var/www
sudo git clone https://github.com/avhthang/inventory-management.git
sudo chown -R $USER:$USER inventory-management
cd inventory-management

# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# Cài đặt dependencies
pip install -r requirements.txt
pip install psycopg2-binary gunicorn

# Cấu hình biến môi trường
cat > .env << EOF
DATABASE_URL=postgresql://app_user:YOUR_APP_PASSWORD@/inventory?host=/cloudsql/PROJECT_ID:asia-southeast1:inventory-db
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
EOF

# Khởi tạo database
export DATABASE_URL=$(cat .env | grep DATABASE_URL | cut -d '=' -f2)
flask init-db
flask create-admin

# Cấu hình Nginx
sudo nano /etc/nginx/sites-available/inventory
```

Nội dung file Nginx:

```nginx
server {
    listen 80;
    server_name YOUR_EXTERNAL_IP;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /static {
        alias /var/www/inventory-management/static;
    }
}
```

```bash
# Kích hoạt Nginx
sudo ln -s /etc/nginx/sites-available/inventory /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# Cấu hình Systemd
sudo nano /etc/systemd/system/inventory.service
```

Nội dung file service:

```ini
[Unit]
Description=Gunicorn instance for inventory app
After=network.target

[Service]
User=YOUR_USERNAME
Group=www-data
WorkingDirectory=/var/www/inventory-management
Environment="PATH=/var/www/inventory-management/venv/bin"
EnvironmentFile=/var/www/inventory-management/.env
ExecStart=/var/www/inventory-management/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Khởi động service
sudo systemctl daemon-reload
sudo systemctl start inventory
sudo systemctl enable inventory
```

### 1.4. Cấu hình Cloud SQL Proxy (Tùy chọn)

Để kết nối an toàn từ VM đến Cloud SQL:

```bash
# Tải Cloud SQL Proxy
wget https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64 -O cloud_sql_proxy
chmod +x cloud_sql_proxy

# Chạy proxy
./cloud_sql_proxy -instances=PROJECT_ID:asia-southeast1:inventory-db=tcp:5432 &
```

---

## 2. Triển khai trên AWS

### 2.1. Tạo RDS PostgreSQL

```bash
# Sử dụng AWS CLI
aws rds create-db-instance \
    --db-instance-identifier inventory-db \
    --db-instance-class db.t3.micro \
    --engine postgres \
    --engine-version 15.4 \
    --master-username admin \
    --master-user-password YOUR_DB_PASSWORD \
    --allocated-storage 20 \
    --vpc-security-group-ids sg-xxxxx \
    --db-name inventory \
    --backup-retention-period 7 \
    --storage-encrypted
```

### 2.2. Tạo EC2 Instance

```bash
# Tạo EC2 instance
aws ec2 run-instances \
    --image-id ami-0c55b159cbfafe1f0 \
    --instance-type t3.small \
    --key-name your-key-pair \
    --security-group-ids sg-xxxxx \
    --subnet-id subnet-xxxxx \
    --user-data file://user-data.sh
```

File `user-data.sh`:

```bash
#!/bin/bash
apt update
apt install -y python3-pip python3-venv nginx git postgresql-client
cd /var/www
git clone https://github.com/avhthang/inventory-management.git
cd inventory-management
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install psycopg2-binary gunicorn
```

### 2.3. Cấu hình trên EC2

SSH vào EC2 và thực hiện tương tự như Google Cloud, nhưng cập nhật DATABASE_URL:

```bash
# Lấy endpoint RDS
aws rds describe-db-instances --db-instance-identifier inventory-db

# Cấu hình .env
cat > .env << EOF
DATABASE_URL=postgresql://admin:YOUR_DB_PASSWORD@RDS_ENDPOINT:5432/inventory
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
EOF
```

### 2.4. Cấu hình Security Group

- Mở port 80, 443 cho HTTP/HTTPS
- Mở port 5432 từ EC2 đến RDS (nội bộ VPC)

---

## 3. Triển khai trên Server riêng

### 3.1. Cài đặt PostgreSQL

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y postgresql postgresql-contrib

# Tạo database và user
sudo -u postgres psql << EOF
CREATE DATABASE inventory;
CREATE USER app_user WITH PASSWORD 'YOUR_PASSWORD';
ALTER ROLE app_user SET client_encoding TO 'utf8';
ALTER ROLE app_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE app_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE inventory TO app_user;
\q
EOF
```

### 3.2. Cài đặt Ứng dụng

```bash
# Cập nhật hệ thống
sudo apt update && sudo apt upgrade -y

# Cài đặt dependencies
sudo apt install -y python3-pip python3-venv nginx git postgresql-client

# Tải code
cd /var/www
sudo git clone https://github.com/avhthang/inventory-management.git
sudo chown -R $USER:$USER inventory-management
cd inventory-management

# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# Cài đặt dependencies
pip install -r requirements.txt
pip install psycopg2-binary gunicorn

# Cấu hình biến môi trường
cat > .env << EOF
DATABASE_URL=postgresql://app_user:YOUR_PASSWORD@localhost:5432/inventory
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
EOF

# Khởi tạo database
export DATABASE_URL=$(cat .env | grep DATABASE_URL | cut -d '=' -f2)
flask init-db
flask create-admin
```

### 3.3. Cấu hình Nginx

```bash
sudo nano /etc/nginx/sites-available/inventory
```

Nội dung:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN_OR_IP;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /static {
        alias /var/www/inventory-management/static;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/inventory /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 3.4. Cấu hình Systemd

```bash
sudo nano /etc/systemd/system/inventory.service
```

Nội dung:

```ini
[Unit]
Description=Gunicorn instance for inventory app
After=network.target postgresql.service

[Service]
User=YOUR_USERNAME
Group=www-data
WorkingDirectory=/var/www/inventory-management
Environment="PATH=/var/www/inventory-management/venv/bin"
EnvironmentFile=/var/www/inventory-management/.env
ExecStart=/var/www/inventory-management/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl start inventory
sudo systemctl enable inventory
```

---

## 4. Cấu hình PostgreSQL

### 4.1. Cập nhật app.py để sử dụng PostgreSQL

Đảm bảo file `app.py` có cấu hình:

```python
import os
from urllib.parse import urlparse

# Database configuration
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Parse DATABASE_URL for PostgreSQL
    result = urlparse(database_url)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Fallback to SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_path, "inventory.db")}'
```

### 4.2. Migration từ SQLite sang PostgreSQL

```bash
# Export từ SQLite
sqlite3 instance/inventory.db .dump > backup.sql

# Import vào PostgreSQL (cần chỉnh sửa backup.sql cho phù hợp)
psql -U app_user -d inventory -f backup.sql
```

Hoặc sử dụng script migration:

```python
# migrate_to_postgres.py
from app import app, db
from sqlalchemy import create_engine
import pandas as pd

# Kết nối cả 2 database
sqlite_engine = create_engine('sqlite:///instance/inventory.db')
postgres_engine = create_engine(os.environ.get('DATABASE_URL'))

# Migrate từng bảng
tables = ['user', 'device', 'department', 'role', 'permission', ...]
for table in tables:
    df = pd.read_sql_table(table, sqlite_engine)
    df.to_sql(table, postgres_engine, if_exists='append', index=False)
```

---

## 5. Quản lý và Bảo trì

### 5.1. Các lệnh quản lý

```bash
# Kiểm tra trạng thái
sudo systemctl status inventory

# Xem logs
sudo journalctl -u inventory -f

# Khởi động lại
sudo systemctl restart inventory

# Cập nhật code
cd /var/www/inventory-management
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart inventory
```

### 5.2. Backup Database

**PostgreSQL:**
```bash
# Backup
pg_dump -U app_user -d inventory > backup_$(date +%Y%m%d).sql

# Restore
psql -U app_user -d inventory < backup_YYYYMMDD.sql
```

**SQLite:**
```bash
# Backup
cp instance/inventory.db instance/backup_$(date +%Y%m%d).db
```

### 5.3. Cấu hình SSL/HTTPS (Let's Encrypt)

```bash
# Cài đặt Certbot
sudo apt install certbot python3-certbot-nginx

# Cấu hình SSL
sudo certbot --nginx -d your-domain.com

# Tự động gia hạn
sudo certbot renew --dry-run
```

### 5.4. Monitoring và Logging

```bash
# Cài đặt monitoring tools
sudo apt install htop iotop

# Xem resource usage
htop
df -h
free -h
```

### 5.5. Tối ưu hiệu suất

```python
# Cấu hình Gunicorn workers
# Số workers = (2 x CPU cores) + 1
# Ví dụ: 2 cores = 5 workers

ExecStart=/var/www/inventory-management/venv/bin/gunicorn \
    --workers 5 \
    --worker-class sync \
    --timeout 120 \
    --bind 127.0.0.1:8000 \
    app:app
```

---

## 6. Troubleshooting

### 6.1. Lỗi kết nối database

```bash
# Kiểm tra kết nối PostgreSQL
psql -U app_user -d inventory -h localhost

# Kiểm tra service
sudo systemctl status postgresql
```

### 6.2. Lỗi permission

```bash
# Sửa quyền file
sudo chown -R YOUR_USERNAME:www-data /var/www/inventory-management
sudo chmod -R 755 /var/www/inventory-management
```

### 6.3. Lỗi port đã sử dụng

```bash
# Kiểm tra port
sudo netstat -tulpn | grep 8000
sudo lsof -i :8000

# Kill process
sudo kill -9 PID
```

---

## 7. Checklist Triển khai

- [ ] Database đã được tạo và cấu hình
- [ ] Ứng dụng đã được cài đặt và cấu hình
- [ ] Nginx đã được cấu hình và chạy
- [ ] Systemd service đã được tạo và enable
- [ ] Firewall đã được mở các port cần thiết
- [ ] SSL/HTTPS đã được cấu hình (nếu có domain)
- [ ] Backup đã được thiết lập
- [ ] Monitoring đã được cấu hình
- [ ] Đã test đăng nhập và các chức năng cơ bản

---

## Liên hệ và Hỗ trợ

Nếu gặp vấn đề trong quá trình triển khai, vui lòng kiểm tra logs và tài liệu này trước khi liên hệ hỗ trợ.

**Logs quan trọng:**
- Application logs: `sudo journalctl -u inventory -f`
- Nginx logs: `/var/log/nginx/error.log`
- PostgreSQL logs: `/var/log/postgresql/postgresql-*.log`
