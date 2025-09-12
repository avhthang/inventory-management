
# Hướng dẫn Triển khai: Ứng dụng Quản lý Thiết bị trên Ubuntu 24.04 🚀

Tài liệu này hướng dẫn triển khai ứng dụng Flask lên server production, đảm bảo ứng dụng chạy liên tục 24/7, tự động khởi động và được bảo mật cơ bản.

### Công nghệ sử dụng:
* **Ubuntu 24.04**: Hệ điều hành cho server.
* **Nginx**: Reverse Proxy, xử lý truy cập từ người dùng.
* **Gunicorn**: WSGI Server, "động cơ" chạy ứng dụng Flask.
* **Systemd**: Trình quản lý dịch vụ, giúp ứng dụng chạy nền và tự khởi động lại.
* **Git**: Dùng để tải và cập nhật mã nguồn.


## 1. Cấu hình Server Ubuntu

Bây giờ, chúng ta sẽ làm việc trên server.

#### 1.1. Cập nhật và Cài đặt Gói cần thiết

Bash

```
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv nginx git -y

```

#### 1.2. Cấu hình Tường lửa (Firewall)

Bash

```
sudo ufw allow 'OpenSSH'
sudo ufw allow 'Nginx Full'
sudo ufw enable

```

_(Nhấn `y` và Enter để xác nhận.)_

----------

## 2. Tải Code và Cài đặt Môi trường Ứng dụng

#### 2.1. Tải Code từ GitHub

Bash

```
# Tạo thư mục và cấp quyền (thay your_username bằng tên người dùng của bạn)
sudo mkdir -p /var/www/inventory-management
sudo chown -R $USER:$USER /var/www/inventory-management

# Di chuyển vào thư mục và tải code
cd /var/www/inventory-management
# Thay bằng URL repository của bạn
git clone https://github.com/avhthang/inventory-management.git .

```

#### 2.2. Cài đặt Môi trường Ảo

Bash

```
# Tạo môi trường ảo
python3 -m venv venv

# Kích hoạt môi trường ảo
source venv/bin/activate

# Cài đặt các thư viện Python
pip install -r requirements.txt

```

----------

## 3. Khởi tạo Database và Tạo Tài khoản Admin

Bước này giúp tránh các lỗi `no such table` hay không đăng nhập được lần đầu.

1.  **Khởi tạo Cơ sở dữ liệu:** (Trong khi `venv` vẫn đang được kích hoạt)
    
    Bash
    
    ```
    flask init-db
    
    ```
    
    _Kết quả mong đợi:_ `Đã khởi tạo cơ sở dữ liệu.`
    
2.  **Tạo Tài khoản Admin:**
    
    Bash
    
    ```
    flask create-admin
    
    ```
    
    _Kết quả mong đợi:_ `Đã tạo tài khoản admin thành công (Pass: admin123).`
    
3.  **Cấp quyền ghi cho file Database:**
    
    Bash
    
    ```
    # Thay 'your_username' bằng tên người dùng của bạn
    sudo chown your_username:www-data instance/inventory.db
    sudo chmod 664 instance/inventory.db
    
    ```
    

----------

## 4. Cấu hình Chạy Tự động với Nginx và Systemd

#### 4.1. Cấu hình Nginx

Bash

```
sudo nano /etc/nginx/sites-available/inventory

```

Dán nội dung sau vào, thay `your_server_ip` bằng địa chỉ IP của server:

Nginx

```
server {
    listen 80;
    server_name your_server_ip;

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

**Kích hoạt cấu hình Nginx:**

Bash

```
sudo ln -s /etc/nginx/sites-available/inventory /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

```

#### 5.2. Cấu hình Systemd

Bash

```
sudo nano /etc/systemd/system/inventory.service

```

Dán nội dung sau vào, thay `your_username` bằng tên người dùng của bạn:

Ini, TOML

```
[Unit]
Description=Gunicorn instance to serve the inventory app
After=network.target

[Service]
User=your_username
Group=www-data
WorkingDirectory=/var/www/inventory-management
ExecStart=/var/www/inventory-management/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target

```

**Khởi động và kích hoạt dịch vụ:**

Bash

```
sudo systemctl start inventory
sudo systemctl enable inventory

```

----------

## 5. Hoàn tất và Quản lý Ứng dụng

**Chúc mừng!** Ứng dụng đã được triển khai hoàn chỉnh.

-   **Truy cập:** `http://your_server_ip`
    
-   **Đăng nhập lần đầu:** `admin` / `admin123`
    

### Các lệnh quản lý hữu ích:

-   **Kiểm tra trạng thái ứng dụng:** `sudo systemctl status inventory`
    
-   **Xem log (nhật ký) lỗi của ứng dụng:** `sudo journalctl -u inventory -f`
    
-   **Khởi động lại ứng dụng (sau khi cập nhật code):** `sudo systemctl restart inventory`
    
-   **Quy trình cập nhật code:**
    
    Bash
    
    ```
    cd /var/www/inventory-management
    git pull
    sudo systemctl restart inventory
    ```
