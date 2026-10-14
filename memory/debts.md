# 💳 Quản Lý Nợ & Tiến Độ Trả Nợ (Debts & Loans)

Quản lý chi tiết các khoản cần trả (Nợ phải trả) và các khoản người khác nợ mình (Khoản phải thu), hỗ trợ nhắc lịch thanh toán qua Email.

---

## 📊 Bảng Tổng Hợp Tình Trạng Nợ (Debt Overview)

*(Trợ lý tự động tính toán và cập nhật khi có biến động)*

| Chỉ Số | Giá Trị (VND) | Ghi Chú |
| :--- | :--- | :--- |
| **Tổng Khoản Phải Trả (Mình nợ)** | `0 ₫` | Dư nợ gốc hiện tại cần trả |
| **Đã Thanh Toán Tích Lũy** | `0 ₫` | Tổng số tiền đã thanh toán từ trước đến nay |
| **Tổng Khoản Phải Thu (Ai nợ mình)**| `0 ₫` | Tiền người khác đang mượn |
| **Khoản Sắp Đến Hạn Gần Nhất** | *Chưa có* | Ngày đến hạn & số tiền |

---

## 🔴 Danh Sách Các Khoản Cần Trả (Liabilities)
*Quy ước thẻ nhắc nhở: Gắn thẻ `<!-- id: debt-pay-XX | notify: YYYY-MM-DD HH:MM | type: debt -->` trước ngày thanh toán.*

### 1. Thẻ tín dụng / Khoản vay mẫu
- **Bên cho vay / Ngân hàng**: Ngân hàng A
- **Số tiền gốc ban đầu**: `10,000,000 ₫`
- **Dư nợ hiện tại**: `10,000,000 ₫`
- **Ngày đến hạn hàng tháng**: Ngày 20 hàng tháng
- **Nhắc nhở tự động**: 
  - [ ] Thanh toán tối thiểu / dứt điểm thẻ tín dụng <!-- id: debt-pay-20261020 | notify: 2026-10-18 09:00 | type: debt -->
- **Lịch sử trả nợ khoản này**:
  | Ngày Trả | Số Tiền | Số Dư Còn Lại | Ghi Chú |
  | :--- | :--- | :--- | :--- |
  | *(Chưa có giao dịch)* | - | - | - |

---

## 🟢 Danh Sách Các Khoản Cần Thu (Receivables - Ai nợ mình)
*Quy ước: Gắn thẻ nhắc hẹn đòi tiền nếu cần.*

| Người Mượn | Số Tiền (VND) | Ngày Mượn | Hẹn Ngày Trả | Trạng Thái |
| :--- | :--- | :--- | :--- | :--- |
| *(Chưa có khoản phải thu)* | - | - | - | - |

---

## 🎯 Chiến Lược Trả Nợ (Debt Snowball / Avalanche)
- Ưu tiên 1: Thanh toán toàn bộ các khoản thẻ tín dụng hoặc lãi suất cao nhất trước để tránh phát sinh chi phí phạt.
- Ưu tiên 2: Trích lập ngân sách cố định hàng tháng vào quỹ trả nợ.
