#!/bin/bash

# Script deploy tự động lên Render.com
# Sử dụng: ./deploy_render.sh

set -e

echo "🚀 Deploying to Render.com..."
echo "================================"

# Kiểm tra git status
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠️  Có thay đổi chưa commit. Committing changes..."
    git add .
    git commit -m "Deploy to Render.com - $(date)"
fi

# Push lên GitHub
echo "📤 Pushing to GitHub..."
git push origin main

echo "✅ Code đã được push lên GitHub"
echo ""
echo "📋 Bước tiếp theo:"
echo "1. Truy cập https://render.com"
echo "2. Đăng nhập và kết nối GitHub"
echo "3. Tạo Web Service mới:"
echo "   - Repository: chọn repo này"
echo "   - Build Command: pip install -r requirements.txt && python setup_render.py"
echo "   - Start Command: gunicorn app:app"
echo "   - Plan: Free"
echo ""
echo "4. Tạo PostgreSQL Database:"
echo "   - Name: inventory-db"
echo "   - Plan: Free"
echo ""
echo "5. Cấu hình Environment Variables:"
echo "   - FLASK_ENV=production"
echo "   - DATABASE_URL=[từ database service]"
echo "   - SECRET_KEY=[Render sẽ tự tạo]"
echo ""
echo "6. Deploy và test ứng dụng"
echo ""
echo "🌐 URL sẽ có dạng: https://your-app-name.onrender.com"
echo "🔍 Health check: https://your-app-name.onrender.com/health"