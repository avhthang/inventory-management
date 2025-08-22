# Sử dụng một image Python gọn nhẹ làm nền
FROM python:3.12-slim

# Thiết lập thư mục làm việc bên trong container
WORKDIR /app

# Sao chép file requirements.txt vào trước để tận dụng cache của Docker
COPY requirements.txt .

# Cài đặt các thư viện cần thiết
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép toàn bộ mã nguồn còn lại vào container
COPY . .

# Lệnh để chạy ứng dụng khi container khởi động
# Gunicorn sẽ lắng nghe trên tất cả các địa chỉ IP bên trong container ở cổng 8000
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "app:app"]
