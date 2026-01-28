#!/bin/bash

# Script tự động cài đặt và cấu hình hệ thống (Nginx + Systemd + App)
# Dựa trên hướng dẫn trong DEPLOYMENT.md
# Chạy script này trên server Ubuntu (ví dụ: Ubuntu 24.04)

set -e

# Màu sắc thông báo
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Cấu hình mặc định
APP_DIR="/var/www/inventory-management"
SERVICE_NAME="inventory"
USER_NAME=$USER

echo -e "${GREEN}=== BẮT ĐẦU CÀI ĐẶT TỰ ĐỘNG ===${NC}"

# 1. Cập nhật hệ thống và cài đặt Dependencies
echo -e "${YELLOW}[1/6] Cài đặt dependencies hệ thống...${NC}"
sudo apt update
sudo apt install -y python3-pip python3-venv nginx git postgresql-client acl

# 2. Cấu hình thư mục ứng dụng
echo -e "${YELLOW}[2/6] Cấu hình mã nguồn ứng dụng...${NC}"
if [ -d "$APP_DIR" ]; then
    echo "Thư mục $APP_DIR đã tồn tại. Đang cập nhật code..."
    cd $APP_DIR
    sudo git pull
    sudo chown -R $USER:$USER $APP_DIR
else
    echo "Đang tải mã nguồn từ GitHub..."
    sudo mkdir -p /var/www
    sudo chown $USER:$USER /var/www
    git clone https://github.com/avhthang/inventory-management.git $APP_DIR
    sudo chown -R $USER:$USER $APP_DIR
    cd $APP_DIR
fi

# 3. Cài đặt môi trường Python
echo -e "${YELLOW}[3/6] Cài đặt môi trường Python (venv)...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install psycopg2-binary gunicorn

# 4. Cấu hình .env và Database
echo -e "${YELLOW}[4/6] Cấu hình Environment và Database...${NC}"
if [ ! -f ".env" ]; then
    echo "Tạo file .env từ mẫu..."
    cp .env.example .env
    # Tạo Secret Key mới
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$NEW_SECRET/" .env
fi

echo "Đang khởi tạo database..."
# Sử dụng script init có sẵn
python3 init_database.py || {
    echo -e "${RED}Lỗi khởi tạo database. Đang thử 'flask init-db'...${NC}"
    export FLASK_APP=app.py
    flask init-db
    flask create-admin
}

# 5. Cấu hình Systemd
echo -e "${YELLOW}[5/6] Cấu hình Systemd Service...${NC}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Nội dung file service (khớp với DEPLOYMENT.md)
cat <<EOF | sudo tee $SERVICE_FILE > /dev/null
[Unit]
Description=Gunicorn instance to serve inventory app
After=network.target

[Service]
User=$USER_NAME
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

echo "Đã tạo file service tại $SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME
echo "✅ Service $SERVICE_NAME đã được khởi động."

# 6. Cấu hình Nginx
echo -e "${YELLOW}[6/6] Cấu hình Nginx...${NC}"
NGINX_CONF="/etc/nginx/sites-available/$SERVICE_NAME"
read -p "Nhập Domain hoặc IP Server của bạn (Enter để dùng mặc định '_'): " DOMAIN_INPUT
DOMAIN=${DOMAIN_INPUT:-_}

# Nội dung file Nginx (khớp với DEPLOYMENT.md)
cat <<EOF | sudo tee $NGINX_CONF > /dev/null
server {
    listen 80;
    server_name $DOMAIN;

    # Cấu hình giới hạn upload size
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static {
        alias $APP_DIR/static;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }
}
EOF

echo "Đã tạo file cấu hình Nginx tại $NGINX_CONF"

# Kích hoạt site
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    sudo rm /etc/nginx/sites-enabled/default
fi
sudo ln -sf $NGINX_CONF /etc/nginx/sites-enabled/

# Kiểm tra và restart Nginx
sudo nginx -t
sudo systemctl restart nginx
echo "✅ Nginx đã được khởi động lại."

echo -e "${GREEN}=== CÀI ĐẶT HOÀN TẤT! ===${NC}"
echo "Ứng dụng đang chạy tại: http://$DOMAIN"
echo "Kiểm tra trạng thái: sudo systemctl status $SERVICE_NAME"
