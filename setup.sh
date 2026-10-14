#!/usr/bin/env bash
# ==============================================================================
# TodoBot VPS Setup Script
# Target: Ubuntu Server 24.04 LTS (Optimized for 1GB RAM)
# ==============================================================================

set -e

echo "=========================================================="
echo "🚀 Khởi tạo TodoBot Assistant trên Ubuntu 24.04 LTS"
echo "=========================================================="

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 1. Kiểm tra quyền root để cấu hình Swap & Crontab hệ thống
IS_ROOT=false
if [ "$EUID" -eq 0 ]; then
    IS_ROOT=true
fi

# 2. Tạo 2GB Swap Memory (Bảo vệ OOM cho VPS 1GB RAM)
if [ "$IS_ROOT" = true ]; then
    echo "[1/5] Kiểm tra bộ nhớ Swap..."
    if ! swapon --show | grep -q "swapfile"; then
        echo "    -> Đang tạo 2GB Swapfile để bảo vệ RAM..."
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
        fi
        echo "    [+] Đã kích hoạt 2GB Swap thành công!"
    else
        echo "    [+] Swapfile đã tồn tại. Bỏ qua."
    fi
else
    echo "[!] Chú ý: Bạn không chạy bằng sudo/root. Vui lòng đảm bảo VPS đã có Swap."
fi

# 3. Cài đặt các gói tối thiểu (Python3, Cron, Tmux)
if [ "$IS_ROOT" = true ]; then
    echo "[2/5] Đảm bảo các tiện ích cơ bản (Python3, Cron, Tmux)..."
    apt-get update -y > /dev/null
    apt-get install -y python3 cron tmux > /dev/null
    systemctl enable cron > /dev/null 2>&1 || true
    systemctl start cron > /dev/null 2>&1 || true
    echo "    [+] Tiện ích hệ thống đã sẵn sàng."
fi

# 4. Tạo file cấu hình .env nếu chưa có
echo "[3/5] Khởi tạo tệp cấu hình .env..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "    [+] Đã tạo .env từ mẫu .env.example."
    echo "    👉 Hãy điền RESEND_API_KEY và NOTIFY_TO_EMAIL vào file .env!"
else
    echo "    [+] File .env đã tồn tại."
fi

# 5. Khởi tạo thư mục dữ liệu và Database SQLite
echo "[4/5] Khởi tạo cơ sở dữ liệu SQLite..."
mkdir -p "$PROJECT_DIR/data"
python3 -c "import worker; worker.init_db()"
echo "    [+] Database tại data/bot.sqlite đã sẵn sàng."

# 6. Đăng ký Linux Cron Job mỗi phút (Zero Idle RAM)
echo "[5/5] Cấu hình Linux Cron Job..."
CRON_JOB="* * * * * cd $PROJECT_DIR && /usr/bin/python3 $PROJECT_DIR/worker.py >> $PROJECT_DIR/data/worker.log 2>&1"

# Thêm cron job nếu chưa tồn tại
(crontab -l 2>/dev/null | grep -F -v "$PROJECT_DIR/worker.py" ; echo "$CRON_JOB") | crontab -
echo "    [+] Đã đăng ký Cron Job quét mỗi phút: * * * * *"

echo ""
echo "=========================================================="
echo "🎉 CÀI ĐẶT HOÀN TẤT!"
echo "=========================================================="
echo "Các bước tiếp theo:"
echo "1. Mở file .env và điền thông tin:"
echo "   nano .env"
echo "2. Chạy thử kiểm tra kết nối email:"
echo "   python3 worker.py --test-email"
echo "3. Bật Antigravity Remote Control để điều khiển từ điện thoại:"
echo "   agy remote-control"
echo "=========================================================="
