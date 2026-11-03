# 🤖 TodoBot Executive Assistant

Hệ thống Trợ Lý Cá Nhân Toàn Năng vận hành trên **Google Antigravity (CLI / Remote Control)**, tối ưu hóa đặc biệt cho **VPS Ubuntu 24.04 LTS (1GB RAM)** với kiến trúc **Zero Idle RAM**.

---

## 🌟 Tính Năng Nổi Bật

- **🧠 Trí Nhớ Dài Hạn Phân Tầng (`memory/*.md`)**:
  - `profile.md`: Hồ sơ cá nhân, thói quen, múi giờ, quy tắc xưng hô.
  - `tasks.md`: Danh sách công việc, hạn chót (deadlines), việc lặp lại.
  - `finances.md`: Quản lý thu chi theo chuẩn Sổ Cái (Ledger) + Tự động tính số dư khả dụng.
  - `debts.md`: Quản lý các khoản nợ (mình nợ ai / ai nợ mình), tiến độ trả nợ & lãi suất.
  - `roadmap.md`: Mục tiêu dài hạn, các cột mốc (milestones) và tiến độ %.
- **⚡ Kiến Trúc Zero Idle RAM (0 MB RAM lúc chờ)**:
  - Worker chạy bằng **Python 3 tích hợp sẵn** của Ubuntu (không tốn tài nguyên cài node_modules nặng nề).
  - Lập lịch thông qua **Linux Cron (`* * * * *`)**: Mỗi phút cron đánh thức worker chạy 0.5s để quét sự kiện rồi thoát ngay lập tức, dành trọn 100% RAM cho Antigravity CLI.
- **🛡️ Nguyên Tắc Độc Quyền Ghi (Single Writer)**:
  - Agent là thực thể duy nhất có quyền ghi/sửa dữ liệu Markdown.
  - Worker là **Read-Only đối với file Markdown**, trạng thái đã gửi được lưu độc lập trong `data/bot.sqlite` -> Loại bỏ 100% nguy cơ xung đột dữ liệu hay lỗi format.
- **📱 Thông Báo Qua Telegram Bot**:
  - Gửi tin nhắn đúng thời điểm hẹn qua Telegram Bot API, kèm loại sự kiện và thời gian nhắc.
- **📱 Điều Khiển Từ Xa Bằng Điện Thoại (Antigravity Remote Control)**:
  - Kết nối bảo mật qua Google Account, sử dụng giao diện Chat Canvas đầy đủ trên trình duyệt điện thoại hoặc app PWA.

---

## 📂 Cấu Trúc Dự Án

```
TodoBot/
├── AGENTS.md             # Bộ não quy tắc, persona và hướng dẫn hành vi của Agent
├── worker.py             # Script Python quét sự kiện và gửi tin nhắn Telegram
├── test_worker.py        # Bộ unit test & kiểm tra parser
├── setup.sh              # Script 1-click triển khai tự động lên VPS Ubuntu 24.04
├── .env.example          # Mẫu cấu hình Telegram bot token và chat ID
├── .gitignore            # Loại trừ dữ liệu nhạy cảm, database và cache
├── README.md             # Hướng dẫn chi tiết
│
├── memory/               # Trí nhớ dài hạn (Do Agent quản lý)
│   ├── profile.md        # Hồ sơ cá nhân người dùng
│   ├── tasks.md          # Công việc & lịch nhắc
│   ├── finances.md       # Thu chi & số dư khả dụng
│   ├── debts.md          # Quản lý nợ & tiến độ trả
│   └── roadmap.md        # Lộ trình & mục tiêu dài hạn
│
└── data/                 # Dữ liệu cục bộ (Tự động sinh khi chạy)
    ├── bot.sqlite        # Database SQLite theo dõi lịch sử gửi thông báo
    └── worker.log        # Nhật ký chạy của cron worker
```

---

## 🚀 Hướng Dẫn Cài Đặt Lên VPS Ubuntu 24.04 LTS (1-Click)

### Bước 1: Đưa mã nguồn lên VPS
```bash
git clone <URL_REPO_CỦA_BẠN> TodoBot
cd TodoBot
```

### Bước 2: Chạy script thiết lập tự động
```bash
sudo bash setup.sh
```
*Script sẽ tự động:*
1. Tạo **2GB Swap Memory** để bảo vệ VPS 1GB RAM không bao giờ bị OOM crash.
2. Cài đặt Python 3, Cron, Tmux.
3. Khởi tạo file `.env` và database SQLite `data/bot.sqlite`.
4. Đăng ký tiến trình chạy `worker.py` mỗi phút vào Crontab hệ thống.

### Bước 3: Cấu hình Telegram
1. Nhắn `@BotFather` trên Telegram để tạo bot và lấy token.
2. Mở chat với bot mới tạo và nhấn **Start** (đối với nhóm, thêm bot vào nhóm và gửi một tin nhắn).
3. Truy cập `https://api.telegram.org/bot<TOKEN>/getUpdates` trong trình duyệt, thay `<TOKEN>` bằng token của bot. Lấy giá trị `message.chat.id` trong kết quả; chat ID nhóm thường là số âm. Không chia sẻ token hoặc URL chứa token.

Mở file `.env`:
```bash
nano .env
```
Điền các giá trị:
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TIMEZONE=Asia/Ho_Chi_Minh
```

### Bước 4: Kiểm tra kết nối Telegram
Chạy lệnh gửi thử tin nhắn:
```bash
python3 worker.py --test-telegram
```
Kiểm tra chat Telegram để xác nhận đã nhận được tin nhắn. Nếu đã có file `.env` từ bản trước, thay các biến Resend bằng hai biến Telegram ở trên. Các sự kiện đã ghi trạng thái `sent` trong SQLite sẽ không gửi lại.

Lần đầu worker quét lịch, nó lưu mốc kích hoạt trong SQLite: các lịch trước mốc này không gửi dồn. Lịch mới được gửi bù nếu trễ tối đa 24 giờ. Thẻ `repeat: weekly` nhắc mỗi tuần cùng thứ và giờ ghi trong `notify`; mỗi tuần chỉ gửi một lần, không gửi dồn các tuần đã lỡ. Không xóa `data/bot.sqlite` nếu muốn giữ mốc kích hoạt và lịch sử chống gửi trùng.

---

## 📱 Cách Kết Nối Chat Từ Điện Thoại (Antigravity Remote Control)

1. **Trên VPS:** Khởi động chế độ Remote Control:
   ```bash
   agy remote-control
   ```
   *(Khuyên dùng bên trong phiên `tmux` để tiến trình luôn chạy: `tmux new -s agy` rồi chạy `agy remote-control`)*.
2. **Trên điện thoại:**
   - Mở trình duyệt truy cập: `https://antigravity.google/`
   - Đăng nhập cùng tài khoản Google bạn đã xác thực trên CLI.
   - Chọn VPS trong danh sách máy chủ.
   - Thêm trang web ra Màn hình chính (Add to Home Screen) để dùng như một ứng dụng di động mượt mà.

---

## 💬 Kịch Bản Chat Mẫu Với Trợ Lý

- **Nhắc việc**:
  > *"Nhắc anh 15h chiều mai gửi hợp đồng thuê nhà cho đối tác nhé"*
  > *(Agent sẽ tự động thêm vào `memory/tasks.md` kèm thẻ ẩn `<!-- id: ... | notify: ... -->`)*

- **Ghi nhận chi tiêu**:
  > *"Hôm nay anh vừa đổ xăng hết 80k và ăn trưa 45k"*
  > *(Agent ghi 2 dòng vào `memory/finances.md` và tự tính lại tổng chi tiêu cùng số dư)*

- **Ghi nhận trả nợ**:
  > *"Anh vừa chuyển khoản trả 2 triệu cho thẻ tín dụng Vietcombank"*
  > *(Agent giảm dư nợ, ghi log vào `memory/debts.md` và báo cáo số nợ còn lại)*

- **Mở rộng nhu cầu mới**:
  > *"Anh muốn theo dõi lịch uống thực phẩm chức năng và tập gym hàng ngày"*
  > *(Agent sẽ tự tạo `memory/habits.md` theo đúng chuẩn, worker sẽ tự động nhận diện và nhắn Telegram khi đến giờ)*.

---

## 🧪 Kiểm Tra Mã Nguồn Cục Bộ (Test Suite)
Để chạy unit test kiểm tra tính toàn vẹn của regex parser và SQLite idempotency:
```bash
python3 test_worker.py
```
Quét thử dữ liệu hiện có mà không gửi tin nhắn:
```bash
python3 worker.py --dry-run
```
