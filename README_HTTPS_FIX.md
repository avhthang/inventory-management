# Hướng dẫn sửa lỗi HTTPS

## Vấn đề
Sau khi chạy `./setup_ssl.sh` và chọn self-signed certificate, HTTPS vẫn không hoạt động.

## Nguyên nhân
Nginx không thể khởi động HTTPS server block nếu không tìm thấy SSL certificate files.

## Giải pháp

### Cách 1: Tự động tạo certificate khi khởi động Docker (Khuyến nghị)

Docker Compose đã được cấu hình để tự động tạo self-signed certificate nếu chưa có khi khởi động nginx container.

Chỉ cần khởi động lại nginx:
```bash
docker-compose restart nginx
```

Hoặc khởi động lại toàn bộ:
```bash
docker-compose down
docker-compose up -d
```

### Cách 2: Tạo certificate thủ công

Nếu không dùng Docker hoặc muốn tạo certificate thủ công:

```bash
# Tạo thư mục SSL
mkdir -p ssl

# Tạo private key
openssl genrsa -out ssl/key.pem 2048

# Tạo certificate
openssl req -new -x509 -key ssl/key.pem -out ssl/cert.pem -days 365 \
    -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"

# Khởi động lại nginx
docker-compose restart nginx
```

### Cách 3: Sử dụng script setup_ssl.sh

```bash
./setup_ssl.sh
# Chọn option 1 (Generate self-signed certificate)
# Sau đó khởi động lại nginx
docker-compose restart nginx
```

## Kiểm tra

1. Kiểm tra certificate đã được tạo:
```bash
ls -la ssl/
```

Bạn sẽ thấy:
- `cert.pem` - Certificate file
- `key.pem` - Private key file

2. Kiểm tra nginx logs:
```bash
docker-compose logs nginx
```

3. Truy cập HTTPS:
- https://localhost (sẽ có cảnh báo bảo mật vì là self-signed certificate)
- http://localhost (vẫn hoạt động bình thường)

## Lưu ý

- Self-signed certificate sẽ hiển thị cảnh báo bảo mật trên trình duyệt - đây là bình thường
- Để bỏ cảnh báo, bạn cần:
  1. Click "Advanced" hoặc "Nâng cao"
  2. Click "Proceed to localhost" hoặc "Tiếp tục đến localhost"

- Đối với production, nên sử dụng Let's Encrypt certificate thay vì self-signed:
```bash
./setup_ssl.sh
# Chọn option 2 (Setup Let's Encrypt certificate)
```

