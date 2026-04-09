@echo off
echo ==============================================
echo Khởi động Hệ thống Quản lý Thiết bị (Test)
echo ==============================================

:: Kiểm tra và kích hoạt môi trường ảo
if not exist "venv\Scripts\activate.bat" (
    echo [Loi] Khong tim thay virtual environment 'venv'.
    echo Vui long chay: python -m venv venv
    pause
    exit /b 1
)

echo Dang kich hoat virtual environment...
call venv\Scripts\activate.bat

:: Chạy ứng dụng
echo Dang khoi dong server Flask...
python app.py

pause
