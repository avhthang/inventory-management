# Hướng dẫn Triển khai: Ứng dụng Quản lý Thiết bị (Docker) 🚀

Tài liệu này hướng dẫn triển khai ứng dụng Inventory Management trên một server đơn (Ubuntu 20.04/22.04/24.04) sử dụng **Docker** và **Docker Compose**.

Đây là phương pháp triển khai được khuyến nghị để đảm bảo môi trường đồng nhất và tránh lỗi thiếu thư viện/cấu hình.

---

## 1. Chuẩn bị Server

Đăng nhập vào server Ubuntu của bạn với quyền `root` hoặc user có quyền `sudo`.

### 1.1. Cập nhật hệ thống
```bash
sudo apt update && sudo apt upgrade -y
```

### 1.2. Cài đặt Docker và Docker Compose Plugin
Chạy các lệnh sau để cài đặt Docker Engine mới nhất:

```bash
# Gỡ cài đặt các phiên bản cũ (nếu có)
sudo apt-remove docker docker-engine docker.io containerd runc

# Cài đặt các gói cần thiết
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release

# Thêm GPG key chính thức của Docker
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Thiết lập repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Cài đặt Docker Engine
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

**Kiểm tra cài đặt:**
```bash
sudo docker run hello-world
docker compose version
```
*(Lưu ý: Docker Compose v2 sử dụng lệnh `docker compose`, không phải `docker-compose`)*.

---

## 2. Tải Mã Nguồn và Cấu Hình

### 2.1. Tải code từ GitHub
```bash
# Di chuyển đến thư mục web (hoặc thư mục home)
cd /var/www/
# Nếu thư mục chưa tồn tại: sudo mkdir -p /var/www && sudo chown $USER:$USER /var/www

# Clone source code
git clone https://github.com/avhthang/inventory-management.git inventory
cd inventory
```

### 2.2. Cấu hình biến môi trường
Tạo file `.env` từ file mẫu:

```bash
cp .env.example .env
nano .env
```

**Cập nhật các thông tin quan trọng trong `.env`:**
- `SECRET_KEY`: Thay đổi thành một chuỗi ngẫu nhiên bảo mật.
- `ADMIN_PASSWORD`: Mật khẩu cho tài khoản admin mặc định.
- `DATABASE_URL`: Để mặc định nếu dùng Postgres trong Docker (đã cấu hình sẵn trong `docker-compose.yml`).

---

## 3. Khởi chạy Ứng dụng

Sử dụng Docker Compose để build và chạy toàn bộ hệ thống (App, Database, Nginx, Redis).

```bash
# Build và chạy ngầm (detached mode)
docker compose up -d --build
```

**Kiểm tra các container đang chạy:**
```bash
docker compose ps
```
Bạn sẽ thấy các service: `app`, `db`, `nginx`, `redis` đều ở trạng thái `Up`.

---

## 4. Khởi tạo Dữ liệu

Sau khi container đã chạy, bạn cần khởi tạo cơ sở dữ liệu và tài khoản admin.

```bash
# Chạy lệnh init-db bên trong container app
docker compose exec app flask init-db

# Tạo tài khoản admin (check log để lấy password hoặc dùng password trong .env)
docker compose exec app flask create-admin
```

✅ **Hoàn tất!**
Truy cập ứng dụng tại: `http://<IP-Server-Của-Bạn>`

> [!NOTE]
> **Lưu ý về truy cập qua IP:**
> Nếu bạn truy cập bằng địa chỉ IP (ví dụ: `http://192.168.1.100`) và bị chuyển hướng sang HTTPS (gây lỗi kết nối), hãy kiểm tra file cấu hình Nginx. Phiên bản mới nhất đã cho phép truy cập HTTP mặc định qua cổng 80. Hãy đảm bảo bạn đã pull code mới nhất.

---

## 5. Các lệnh Quản lý Thường dùng

### **Xem log (Nhật ký lỗi)**
```bash
# Xem log toàn bộ hệ thống
docker compose logs -f

# Xem log riêng service app
docker compose logs -f app
```

### **Khởi động lại Server**
```bash
docker compose restart
```

### **Cập nhật Ứng dụng (Code mới)**
Khi có code mới trên GitHub:

```bash
# 1. Kéo code mới về
git pull origin main

# 2. Build và khởi động lại container (chỉ services thay đổi mới được build lại)
docker compose up -d --build
```

### **Sao lưu Dữ liệu (Backup)**
Dữ liệu database được lưu trong volume Docker `src_postgres_data`.
Để backup thủ công:
```bash
docker compose exec app python3 backup_restore.py backup
```
File backup sẽ nằm trong thư mục `backups/` trên server.

---

## 6. Cấu hình HTTPS (SSL)

Hiện tại `docker-compose.yml` hỗ trợ mount chứng chỉ SSL từ thư mục `./ssl`.
1. Copy chứng chỉ (`cert.pem`, `key.pem`) vào thư mục `ssl/`.
2. Truy cập qua `https://<Domain-Của-Bạn>`.

*(Để tự động hóa SSL với Let's Encrypt, vui lòng tham khảo file `setup_ssl.sh` hoặc cấu hình thêm Certbot).*
