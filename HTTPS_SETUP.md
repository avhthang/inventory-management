# Hướng dẫn thiết lập HTTPS

Hệ thống hiện đã hỗ trợ cả HTTP và HTTPS. Bạn có thể truy cập ứng dụng qua cả hai giao thức.

## Cấu hình hiện tại

- **HTTP**: Port 80 - Hoạt động ngay lập tức
- **HTTPS**: Port 443 - Cần cấu hình SSL certificate

## Các bước thiết lập HTTPS

### 1. Tạo SSL Certificate

Có 3 cách để thiết lập SSL certificate:

#### Cách 1: Sử dụng script tự động (Khuyến nghị)

```bash
./setup_ssl.sh
```

Script sẽ hướng dẫn bạn qua các bước:
- Tạo self-signed certificate (cho development/testing)
- Thiết lập Let's Encrypt certificate (cho production)
- Sử dụng certificate có sẵn

#### Cách 2: Tạo self-signed certificate thủ công (Development)

```bash
# Tạo thư mục SSL
mkdir -p ssl

# Tạo private key
openssl genrsa -out ssl/key.pem 2048

# Tạo certificate
openssl req -new -x509 -key ssl/key.pem -out ssl/cert.pem -days 365 \
    -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"
```

**Lưu ý**: Self-signed certificate sẽ hiển thị cảnh báo bảo mật trên trình duyệt. Chỉ dùng cho development/testing.

#### Cách 3: Sử dụng Let's Encrypt (Production)

```bash
# Cài đặt certbot
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx

# Lấy certificate (thay your-domain.com bằng domain của bạn)
sudo certbot certonly --standalone -d your-domain.com

# Cập nhật nginx.conf với đường dẫn certificate
# Thay đổi các dòng sau trong nginx.conf:
# ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
# ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
```

### 2. Cấu hình nginx.conf

File `nginx.conf` đã được cấu hình để hỗ trợ cả HTTP và HTTPS. Nếu bạn sử dụng Let's Encrypt hoặc certificate ở vị trí khác, cần cập nhật các dòng sau trong phần HTTPS server block:

```nginx
ssl_certificate /path/to/your/cert.pem;
ssl_certificate_key /path/to/your/key.pem;
```

### 3. Khởi động lại nginx

#### Nếu dùng Docker:
```bash
docker-compose restart nginx
```

#### Nếu cài đặt trực tiếp:
```bash
sudo nginx -t  # Kiểm tra cấu hình
sudo systemctl restart nginx
```

### 4. Kiểm tra

- **HTTP**: http://your-domain hoặc http://localhost
- **HTTPS**: https://your-domain hoặc https://localhost

## Tùy chọn: Chuyển hướng HTTP sang HTTPS

Nếu bạn muốn tự động chuyển hướng tất cả lưu lượng HTTP sang HTTPS, thêm đoạn code sau vào server block HTTP (port 80) trong `nginx.conf`:

```nginx
server {
    listen 80;
    server_name _;
    
    # Redirect HTTP to HTTPS
    return 301 https://$host$request_uri;
}
```

**Lưu ý**: Nếu thêm redirect này, HTTP sẽ không còn hoạt động độc lập nữa - tất cả sẽ được chuyển sang HTTPS.

## Cấu hình Flask

Flask đã được cấu hình để:
- Nhận diện HTTPS khi đứng sau nginx proxy
- Tự động sử dụng HTTPS cho các URL được tạo bởi `url_for()`
- Xử lý các proxy headers (`X-Forwarded-Proto`, `X-Forwarded-For`, etc.)

Cấu hình này được thiết lập tự động trong `config.py` khi `FLASK_ENV=production`.

## Troubleshooting

### Lỗi: "SSL certificate not found"
- Kiểm tra đường dẫn certificate trong `nginx.conf`
- Đảm bảo file certificate và key tồn tại
- Kiểm tra quyền truy cập file (nginx cần đọc được)

### Lỗi: "Connection refused" khi truy cập HTTPS
- Kiểm tra port 443 đã được mở trong firewall
- Kiểm tra nginx đã được khởi động lại sau khi cấu hình
- Xem log nginx: `sudo tail -f /var/log/nginx/error.log`

### Certificate hết hạn (Let's Encrypt)
Let's Encrypt certificates có thời hạn 90 ngày. Để tự động gia hạn:

```bash
# Thêm vào crontab
sudo crontab -e

# Thêm dòng sau để kiểm tra và gia hạn mỗi ngày
0 0 * * * certbot renew --quiet --deploy-hook "systemctl reload nginx"
```

## Bảo mật

- Luôn sử dụng HTTPS cho production
- Cấu hình HSTS (HTTP Strict Transport Security) - đã được bật trong nginx.conf
- Sử dụng Let's Encrypt cho production thay vì self-signed certificates
- Đảm bảo certificate được gia hạn tự động

