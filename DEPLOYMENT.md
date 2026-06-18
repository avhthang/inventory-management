# Hướng Dẫn Triển Khai Hệ Thống Quản Lý Tài Sản lên Server (Có Domain)

Tài liệu này hướng dẫn chi tiết cách đưa ứng dụng lên một máy chủ thực tế (VPS/Server) và cấu hình tên miền tùy chỉnh (Custom Domain) với chứng chỉ bảo mật SSL miễn phí từ Let's Encrypt.

## 1. Yêu Cầu Chuẩn Bị (Prerequisites)

- Một máy chủ / VPS chạy hệ điều hành Linux (khuyến nghị **Ubuntu 20.04** hoặc **Ubuntu 22.04**).
- Đã cài đặt **Docker** và **Docker Compose** trên Server.
- Một tên miền (Domain) hợp lệ.
- Bạn đã trỏ tên miền (A Record) về địa chỉ IP của máy chủ VPS của bạn.

> [!IMPORTANT]
> Hãy chắc chắn rằng bản ghi DNS (A Record) đã cập nhật thành công và trỏ đúng về IP của server trước khi chạy lệnh cấu hình SSL, nếu không quá trình cấp phát chứng chỉ sẽ thất bại.

## 2. Các Bước Triển Khai

### Bước 1: Clone Mã Nguồn Về Server

Đăng nhập vào server của bạn qua SSH, sau đó clone mã nguồn từ GitHub về máy chủ:

```bash
git clone https://github.com/avhthang/inventory-management.git
cd inventory-management
```

### Bước 2: Chạy Script Cấu Hình Tự Động

Hệ thống đã được tích hợp sẵn một script (`setup_ssl.sh`) để giúp bạn cấu hình các biến môi trường và thiết lập SSL tự động.

Chạy script bằng lệnh:
```bash
chmod +x setup_ssl.sh
./setup_ssl.sh
```

**Các quá trình diễn ra trong Script:**
1. **Khởi tạo `.env`**: Script sẽ tự động copy file `.env.example` thành `.env` nếu chưa có.
2. **Tạo `SECRET_KEY`**: Tự động sinh ra một mã bảo mật ngẫu nhiên mạnh mẽ cho ứng dụng Flask và lưu vào file `.env`.
3. **Cấu hình SSL**: Script sẽ hiển thị menu lựa chọn SSL:
   ```text
   Select SSL certificate type:
   1) Generate Self-Signed Certificate (Development/Local)
   2) Setup Let's Encrypt with Certbot (Production with Domain)
   3) Skip (Use existing certificates)
   ```
   👉 Hãy chọn số **2** để thiết lập Let's Encrypt cho tên miền của bạn.

**Tiến trình Let's Encrypt:**
- Script sẽ yêu cầu bạn nhập tên miền (Ví dụ: `quanly.tenmiencuaban.com`).
- Yêu cầu nhập email để nhận thông báo gia hạn SSL.
- Certbot sẽ được cài đặt (nếu chưa có), sau đó hệ thống tự động xác thực tên miền qua port 80 và tải chứng chỉ về.
- Chứng chỉ sẽ được copy vào thư mục `./ssl` để Docker có thể đọc được và file `nginx.conf` sẽ được tự động cập nhật `server_name` tương ứng.

> [!CAUTION]
> Trong quá trình chạy script (Bước 2), Port 80 trên server phải rảnh (không có web server nào khác đang chạy trên cổng 80). Nếu bạn đang chạy Nginx trực tiếp trên máy chủ gốc (không qua Docker), hãy dừng nó lại trước: `sudo systemctl stop nginx`.

### Bước 3: Kiểm Tra File Môi Trường (.env)

Mở file `.env` để kiểm tra và cấu hình các thông số cần thiết (Đặc biệt là mật khẩu admin mặc định và thông tin Telegram Bot nếu bạn có sử dụng):

```bash
nano .env
```
Đảm bảo đã đổi `ADMIN_PASSWORD` nếu cần thiết (Mặc định là `admin123`).

### Bước 4: Khởi Động Hệ Thống Bằng Docker Compose

Sau khi thiết lập SSL và biến môi trường thành công, khởi động toàn bộ hệ thống bằng Docker:

```bash
docker compose up -d --build
```
*Lưu ý: Nếu server của bạn sử dụng phiên bản Docker cũ, lệnh có thể là `docker-compose up -d --build`.*

Hệ thống sẽ tải các Image cần thiết, cài đặt thư viện và khởi chạy các container (App, Database Postgres, Nginx, Redis). Quá trình này sẽ mất khoảng vài phút trong lần chạy đầu tiên.

### Bước 5: Kiểm Tra Trạng Thái

Kiểm tra xem tất cả các container đã chạy ổn định chưa:
```bash
docker compose ps
```
Nếu tất cả đều `Up`, bạn có thể truy cập hệ thống qua tên miền của mình: `https://tenmiencuaban.com`

> Hệ thống tự động chuyển hướng mọi truy cập HTTP (Port 80) sang HTTPS (Port 443).

---

## 3. Quản Lý và Bảo Trì

### Gia Hạn Chứng Chỉ SSL (Let's Encrypt)
Chứng chỉ của Let's Encrypt chỉ có thời hạn 90 ngày. Việc chạy qua `--standalone` yêu cầu cổng 80 trống, do đó quy trình gia hạn thủ công như sau:

1. Dừng Nginx container: `docker compose stop nginx`
2. Cập nhật chứng chỉ: `sudo certbot renew`
3. Copy chứng chỉ mới vào thư mục local: 
   ```bash
   sudo cp /etc/letsencrypt/live/tenmiencuaban.com/fullchain.pem ./ssl/cert.pem
   sudo cp /etc/letsencrypt/live/tenmiencuaban.com/privkey.pem ./ssl/key.pem
   sudo chown -R $USER:$USER ./ssl
   ```
4. Khởi động lại Nginx: `docker compose start nginx`

*(Bạn có thể đưa đoạn script copy này vào cronjob cùng với certbot renew để tự động hoá 100%)*

### Xem Log Lỗi Của Hệ Thống
Để kiểm tra lỗi của ứng dụng khi gặp sự cố 500/504:
```bash
docker compose logs -f --tail=100 app
```

### Sao Lưu / Cập Nhật Mã Nguồn
Khi có phiên bản mới trên GitHub, hãy làm theo các bước sau để cập nhật server:
```bash
# 1. Tải code mới
git pull origin main

# 2. Build và khởi động lại
docker compose up -d --build
```
Dữ liệu Postgres của bạn được lưu trong Docker Volume `postgres_data` và các file Upload/Backup được lưu trong thư mục vật lý `./backups`, `./instance` nên sẽ **không bị mất** khi bạn rebuild hoặc pull code mới.
