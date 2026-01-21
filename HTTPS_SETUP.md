# Hướng Dẫn Triển Khai & Cấu Hình HTTPS

Hệ thống Inventory Management mặc định chạy trên giao thức HTTPS để đảm bảo bảo mật. Tài liệu này hướng dẫn cách thiết lập SSL và triển khai hệ thống an toàn.

## Yêu Cầu Cài Đặt
- Docker & Docker Compose
- Git
- Port 80 và 443 chưa được sử dụng (nếu chạy Production)

---

## 🚀 Quy Trình Triển Khai Nhanh

Chúng tôi cung cấp script tự động `setup_ssl.sh` giúp bạn cài đặt môi trường, sinh Secret Key và cài đặt SSL chỉ với một lệnh.

### Bước 1: Tải mã nguồn mới nhất
```bash
git pull origin main
```

### Bước 2: Chạy Script Cài Đặt
Script này sẽ kiểm tra mọi thứ cần thiết (file .env, SECRET_KEY, SSL Certificate).

```bash
bash setup_ssl.sh
```

Bạn sẽ thấy menu lựa chọn:
- **1) Generate Self-Signed Certificate**: Chọn nếu chạy test ở **Localhost**.
    - *Lưu ý*: Trình duyệt sẽ báo lỗi "Not Secure" (vì chứng chỉ tự ký), bạn cần chấp nhận rủi ro để tiếp tục.
- **2) Setup Let's Encrypt**: Chọn nếu chạy **Production** (Cần có Domain thật trỏ về IP server).
    - Script sẽ tự động cài Certbot, lấy chứng chỉ và lưu vào thư mục `./ssl`.
    - Tự động cấu hình Nginx để dùng chứng chỉ này.

### Bước 3: Khởi động hệ thống
Sau khi script chạy xong, hãy khởi động lại container để áp dụng cấu hình:

```bash
docker-compose down
docker-compose up -d --build
```

---

## 🔒 Xử lý các vấn đề thường gặp

### 1. Tại sao tôi bị đăng nhập lại liên tục?
Nếu bạn gặp tình trạng vừa đăng nhập xong, refresh trang lại bị văng ra (logout), đó là do `SECRET_KEY` thay đổi.
- **Nguyên nhân**: Flask mặc định sinh `SECRET_KEY` ngẫu nhiên mỗi khi restart app nếu không cấu hình cố định.
- **Khắc phục**: Script `setup_ssl.sh` ở trên đã tự động sinh một key cố định và lưu vào file `.env`.
- **Kiểm tra**: Mở file `.env` và đảm bảo dòng `SECRET_KEY=...` tồn tại và có giá trị.

### 2. Trình duyệt báo lỗi bảo mật (Warning: Potential Security Risk)
Đây là bình thường nếu bạn sử dụng **Option 1 (Self-Signed)**.
- Vì chứng chỉ do bạn tự tạo, không phải tổ chức uy tín xác thực.
- Hãy nhấn **Advanced** -> **Proceed to localhost (unsafe)**.

### 3. HTTPS không hoạt động (Connection Refused)
- Kiểm tra Docker container có đang chạy không:
  ```bash
  docker-compose ps
  ```
- Kiểm tra logs của Nginx:
  ```bash
  docker-compose logs nginx
  ```
- Đảm bảo firewall (AWS Security Group, UFW) đã mở port **443**.

---

## ⚙️ Chi Tiết Cấu Hình (Dành cho nâng cao)

### Cấu hình Nginx (`nginx.conf`)
Nginx đóng vai trò Reverse Proxy và SSL Termination:
- **Port 80**: Redirect 301 vĩnh viễn sang 443.
- **Port 443**: Xử lý SSL, thêm Security Headers (HSTS, X-Frame-Options).
- **Proxy Headers**: Thêm `X-Forwarded-Proto` để Flask biết request đến từ HTTPS.

### Cấu hình Flask (`config.py`)
Ứng dụng Flask tự động nhận diện môi trường Production:
- **Session Security**: `Secure=True` (Cookie chỉ gửi qua HTTPS), `HttpOnly=True`.
- **ProxyFix**: Tin cậy các headers từ Nginx để xử lý URL redirect chính xác.

---

## Backup & Restore
Hệ thống tự động backup mỗi ngày nếu được cấu hình trong `deploy.sh`. Để backup thủ công:
```bash
docker-compose exec app python backup_restore.py backup
```
