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
    echo "    👉 Hãy điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID vào file .env!"
else
    echo "    [+] File .env đã tồn tại."
fi

# 5. Khởi tạo thư mục dữ liệu và Database SQLite
echo "[4/6] Khởi tạo cơ sở dữ liệu SQLite..."
mkdir -p "$PROJECT_DIR/data"
python3 -c "import bot; bot.init_chat_db()"
echo "    [+] Database tại data/bot.sqlite đã sẵn sàng."

# 6. Đăng ký Linux Cron Job mỗi phút (Zero Idle RAM cho Notification Worker)
echo "[5/6] Cấu hình Linux Cron Job..."
CRON_JOB="* * * * * cd $PROJECT_DIR && /usr/bin/python3 $PROJECT_DIR/worker.py >> $PROJECT_DIR/data/worker.log 2>&1"

# Thêm cron job nếu chưa tồn tại
(crontab -l 2>/dev/null | grep -F -v "$PROJECT_DIR/worker.py" ; echo "$CRON_JOB") | crontab -
echo "    [+] Đã đăng ký Cron Job quét mỗi phút: * * * * *"

# 7. Đăng ký systemd service cho Telegram AI Agent (bot.py)
echo "[6/6] Cấu hình dịch vụ nền TodoBot Telegram Agent..."
if [ "$IS_ROOT" = true ]; then
    SERVICE_FILE="/etc/systemd/system/todobot-agent.service"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=TodoBot Telegram AI Agent (2-Way Chat)
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/python3 $PROJECT_DIR/bot.py
Restart=always
RestartSec=5
StandardOutput=append:$PROJECT_DIR/data/bot.log
StandardError=append:$PROJECT_DIR/data/bot.log

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable todobot-agent.service > /dev/null 2>&1 || true
    echo "    [+] Đã tạo systemd service: todobot-agent.service (~15MB RAM)."
else
    echo "    [i] Bạn có thể chạy bot bằng lệnh: python3 bot.py"
fi

echo ""
echo "=========================================================="
echo "🎉 CÀI ĐẶT HOÀN TẤT!"
echo "=========================================================="
echo "Các bước tiếp theo:"
echo "1. Mở file .env và điền TELEGRAM_BOT_TOKEN, AI_BASE_URL, AI_API_KEY:"
echo "   nano .env"
echo "2. Chạy thử kiểm tra kết nối Telegram:"
echo "   python3 worker.py --test-telegram"
echo "3. Khởi động Agent Chat 2 chiều trên Telegram:"
echo "   sudo systemctl restart todobot-agent"
echo "   (Hoặc chạy trực tiếp: python3 bot.py)"
echo "=========================================================="
