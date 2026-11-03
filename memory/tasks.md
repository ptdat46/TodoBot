# 📋 Quản Lý Công Việc & Nhắc Việc (Tasks & Reminders)

Nơi lưu trữ danh sách các đầu việc cần làm, hạn chót (deadlines), và các sự kiện cần gửi thông báo qua Telegram.

> **Quy ước thẻ thông báo ẩn:**
> `- [ ] Tên công việc <!-- id: task-YYYYMMDD-XX | notify: YYYY-MM-DD HH:MM | priority: high/normal -->`
> Với việc lặp lại hằng tuần, thêm `| repeat: weekly` cuối thẻ; ngày và giờ `notify` là mốc đầu tiên.
> Worker sẽ tự động quét thẻ này và gửi tin nhắn Telegram đúng giờ hẹn.

---

## 📌 Việc Khẩn Cấp & Có Hạn Chót (Deadlines)
- [ ] Hoàn tất kiểm tra kết nối VPS và cấu hình email Resend <!-- id: task-20261015-01 | notify: 2026-10-15 10:00 | priority: high -->
- [ ] Review lại kế hoạch tài chính và mục tiêu tháng này <!-- id: task-20261015-02 | notify: 2026-10-16 08:30 | priority: normal -->

## 📝 Việc Đang Thực Hiện (In Progress)
- [ ] Cài đặt Antigravity CLI và thiết lập Remote Control trên điện thoại

## 🔁 Việc Định Kỳ Hàng Tuần / Hàng Tháng (Recurring)
- [ ] Sao lưu (backup) thư mục `memory/` lên kho lưu trữ an toàn (Chủ nhật hàng tuần) <!-- id: task-recurring-backup | notify: 2026-10-18 20:00 | priority: normal | repeat: weekly -->

## ✅ Việc Đã Hoàn Thành (Archive)
- [x] Khởi tạo kiến trúc dự án TodoBot Assistant (2026-10-15)
