# Hướng dẫn cấu hình server OFFLINE chạy HTTPS (từ đầu)

Mục tiêu: dựng hệ thống trong môi trường nội bộ/không Internet, chỉ dùng HTTPS với chứng chỉ tự ký (self-signed).

## 1) Chuẩn bị
- Docker và Docker Compose
- Cổng mở: 80, 443 (trên máy host)
- Không cần Internet (không dùng Let's Encrypt)

## 2) Lấy mã nguồn
```bash
git clone https://github.com/avhthang/inventory-management.git
cd inventory-management
```

## 3) Tạo chứng chỉ tự ký (self-signed) OFFLINE
Tùy chọn A: tạo trên host, mount vào container
```bash
mkdir -p ssl
openssl genrsa -out ssl/key.pem 2048
openssl req -new -x509 -key ssl/key.pem -out ssl/cert.pem -days 365 \
  -subj "/C=VN/ST=Offline/L=Local/O=Inventory/CN=localhost"
```

Tùy chọn B: để Docker tự tạo (init-ssl.sh đã có sẵn)
```bash
docker-compose up -d  # init-ssl.sh trong container nginx sẽ tự tạo cert nếu chưa có
```

## 4) Cấu hình nginx (đã mặc định HTTPS-only)
- `nginx.conf` đã redirect toàn bộ HTTP (port 80) sang HTTPS (port 443).
- Đường dẫn chứng chỉ mặc định trong container:
  - `/etc/nginx/ssl/cert.pem`
  - `/etc/nginx/ssl/key.pem`
- Nếu tự tạo cert trên host (Tùy chọn A), giữ nguyên mount:
  - `./ssl:/etc/nginx/ssl` trong `docker-compose.yml`

## 5) Cấu hình ứng dụng (tối thiểu)
Tạo file `.env` (hoặc set biến môi trường) với các giá trị tối thiểu:
```
FLASK_ENV=production
SECRET_KEY=your-secure-secret
DATABASE_URL=sqlite:///inventory.db   # hoặc PostgreSQL nội bộ nếu có
```

## 6) Khởi chạy
```bash
docker-compose down
docker-compose up -d
```

## 7) Kiểm tra
```bash
# HTTP sẽ bị redirect 301 sang HTTPS
curl -I http://localhost

# Kiểm tra health qua HTTPS (bỏ verify do self-signed)
curl -k https://localhost/health
```

## 8) Tùy chỉnh server_name (nếu dùng domain nội bộ)
- Sửa `server_name` trong `nginx.conf` (HTTPS server block, port 443) thành domain nội bộ của bạn.
- Cập nhật file hosts nội bộ trỏ domain đó về IP server (nếu không có DNS).

## 9) Chế độ HTTPS-only (đã bật sẵn)
- Nginx: redirect HTTP → HTTPS.
- Flask: middleware `force_https` kiểm tra `X-Forwarded-Proto` và redirect sang HTTPS.

## 10) Troubleshooting nhanh
- **Nginx không khởi động**: kiểm tra `/etc/nginx/ssl/cert.pem` và `key.pem` tồn tại; nếu chưa có, tạo lại cert (Bước 3) rồi `docker-compose restart nginx`.
- **Trình duyệt cảnh báo chứng chỉ**: self-signed là bình thường; chọn “Advanced” → “Proceed…”. Muốn hết cảnh báo, phải dùng CA nội bộ hoặc cert hợp lệ.
- **Connection refused HTTPS**: kiểm tra firewall mở port 443, và container nginx đang chạy (`docker-compose ps nginx`).

## 11) Dọn sạch HTTP
Không cần làm thêm: HTTP đã bị redirect toàn bộ. Mọi truy cập phải đi qua HTTPS.

## 12) Backup cấu hình SSL
Sao lưu thư mục `ssl/` (host) hoặc `/etc/nginx/ssl` (trong container) để giữ key/cert tránh phải tái tạo.

