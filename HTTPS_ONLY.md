# Hệ thống chỉ chạy trên HTTPS

## Tổng quan

Hệ thống Inventory Management hiện **CHỈ chạy trên HTTPS** để đảm bảo bảo mật tối đa. Tất cả lưu lượng HTTP sẽ tự động được chuyển hướng sang HTTPS.

## Cơ chế bảo vệ 2 tầng

### 1. Nginx Level (Tầng Reverse Proxy)
- Tất cả request HTTP (port 80) được redirect 301 sang HTTPS
- Redirect được thực hiện trước khi request đến Flask application
- Hiệu quả và nhanh chóng

### 2. Flask Level (Tầng Application)
- Middleware kiểm tra `X-Forwarded-Proto` header
- Nếu phát hiện HTTP, tự động redirect sang HTTPS
- Đảm bảo không có request HTTP nào có thể bypass nginx

## Yêu cầu

### Bắt buộc có SSL Certificate

Hệ thống **KHÔNG THỂ** hoạt động nếu không có SSL certificate. Nginx sẽ không khởi động được nếu thiếu certificate.

### Tự động tạo Certificate (Docker)

Khi sử dụng Docker Compose, hệ thống sẽ tự động tạo self-signed certificate nếu chưa có:

```bash
docker-compose up -d
```

Script `init-ssl.sh` sẽ tự động chạy và tạo certificate trong container nginx.

### Tạo Certificate thủ công

Nếu không dùng Docker hoặc muốn tạo certificate thủ công:

```bash
# Tạo thư mục
mkdir -p ssl

# Tạo private key
openssl genrsa -out ssl/key.pem 2048

# Tạo certificate
openssl req -new -x509 -key ssl/key.pem -out ssl/cert.pem -days 365 \
    -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"
```

## Kiểm tra

### 1. Kiểm tra Certificate

```bash
# Trong Docker
docker-compose exec nginx ls -la /etc/nginx/ssl/

# Hoặc trên host
ls -la ssl/
```

Phải có 2 file:
- `cert.pem` - Certificate
- `key.pem` - Private key

### 2. Kiểm tra Redirect

```bash
# Test HTTP redirect
curl -I http://localhost

# Kết quả mong đợi:
# HTTP/1.1 301 Moved Permanently
# Location: https://localhost/...
```

### 3. Kiểm tra HTTPS

```bash
# Test HTTPS (bỏ qua certificate verification cho self-signed)
curl -k https://localhost/health
```

## Troubleshooting

### Lỗi: "nginx: [emerg] SSL_CTX_use_certificate_file() failed"

**Nguyên nhân**: Certificate không tồn tại hoặc đường dẫn sai.

**Giải pháp**:
1. Kiểm tra certificate có tồn tại:
   ```bash
   ls -la ssl/cert.pem ssl/key.pem
   ```

2. Nếu chưa có, tạo certificate (xem phần trên)

3. Khởi động lại nginx:
   ```bash
   docker-compose restart nginx
   ```

### Lỗi: "Connection refused" khi truy cập HTTPS

**Nguyên nhân**: Port 443 chưa được mở hoặc nginx chưa khởi động.

**Giải pháp**:
1. Kiểm tra nginx đang chạy:
   ```bash
   docker-compose ps nginx
   ```

2. Kiểm tra logs:
   ```bash
   docker-compose logs nginx
   ```

3. Kiểm tra firewall:
   ```bash
   sudo ufw status
   sudo ufw allow 443/tcp
   ```

### Lỗi: "NET::ERR_CERT_AUTHORITY_INVALID" trên trình duyệt

**Nguyên nhân**: Đang sử dụng self-signed certificate.

**Giải pháp**: Đây là bình thường với self-signed certificate. Để bỏ cảnh báo:
1. Click "Advanced" hoặc "Nâng cao"
2. Click "Proceed to localhost" hoặc "Tiếp tục đến localhost"

**Lưu ý**: Đối với production, nên sử dụng Let's Encrypt certificate thay vì self-signed.

## Production Deployment

### Sử dụng Let's Encrypt

1. Cài đặt certbot:
   ```bash
   sudo apt-get update
   sudo apt-get install -y certbot python3-certbot-nginx
   ```

2. Lấy certificate:
   ```bash
   sudo certbot certonly --standalone -d your-domain.com
   ```

3. Cập nhật nginx.conf:
   ```nginx
   ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
   ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
   ```

4. Khởi động lại nginx:
   ```bash
   docker-compose restart nginx
   ```

### Tự động gia hạn Certificate

Let's Encrypt certificates có thời hạn 90 ngày. Để tự động gia hạn:

```bash
# Thêm vào crontab
sudo crontab -e

# Thêm dòng sau
0 0 * * * certbot renew --quiet --deploy-hook "docker-compose restart nginx"
```

## Bảo mật

- ✅ Tất cả traffic được mã hóa qua HTTPS
- ✅ HTTP Strict Transport Security (HSTS) được bật
- ✅ Secure cookies và session
- ✅ Không có mixed content (HTTP/HTTPS)
- ✅ Certificate validation

## Tắt HTTPS (Không khuyến nghị)

⚠️ **CẢNH BÁO**: Chỉ làm điều này trong môi trường development/testing.

Nếu muốn tắt HTTPS (không khuyến nghị):

1. Sửa `nginx.conf`: Thay thế HTTP server block để không redirect
2. Sửa `config.py`: Comment out `force_https()` middleware
3. Khởi động lại services

**Lưu ý**: Điều này sẽ làm giảm đáng kể tính bảo mật của hệ thống.

