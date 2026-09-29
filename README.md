# RalliSmart V2 — Tích hợp Home Assistant

Điều khiển nhà Rang Dong RalliSmart V2 trên Home Assistant (đèn, công tắc,
rèm/cửa cuốn, cảm biến, camera, scene).

---

## 1. Yêu cầu

- Home Assistant 2024.x trở lên (đã kiểm thử với HA 2026.2).
- Tài khoản app **RalliSmart V2** (Rang Dong).
- Một **License Key** (mua/đăng ký tại website bản quyền bên dưới).

Website bản quyền: **https://rallismart-license.vercel.app**

---

## 2. Cài đặt

### Cách A — thủ công
1. Giải nén file zip.
2. Copy thư mục `custom_components/rallismart` vào:
   ```
   <thư-mục-config-HA>/custom_components/rallismart
   ```
   (thường là `/config/custom_components/rallismart`).
3. Khởi động lại Home Assistant.

### Cách B — HACS
1. HACS → Integrations → ⋮ → **Custom repositories**.
2. Dán URL repo này, chọn loại **Integration**, Add.
3. Tìm **RalliSmart V2** → Download → khởi động lại HA.

---

## 3. Lấy License Key

1. Mở website bản quyền: **https://rallismart-license.vercel.app**
2. **Đăng ký** bằng email và xác minh email.
3. Vào **Dashboard**:
   - **Dùng thử miễn phí 1 ngày** (mỗi tài khoản 1 lần), hoặc
   - **Mua gói**: 1 tháng 50.000đ / Vĩnh viễn 200.000đ.
4. Quét QR PayOS để thanh toán. Hệ thống **tự động cấp License Key** ngay khi
   thanh toán thành công (không cần chờ duyệt).
5. Copy **License Key** (dạng `RDS-XXXX-XXXX-XXXX-XXXX`).

> Mỗi License Key **chỉ kích hoạt được trên một cài đặt Home Assistant**.
> Nếu đổi máy, liên hệ admin để **reset** license.

> Địa chỉ máy chủ bản quyền mặc định là `https://rallismart-license.vercel.app`.
> Nếu bạn triển khai server ở tên miền khác, sửa `DEFAULT_LICENSE_SERVER` và
> `WEBSITE_URL` trong `custom_components/rallismart/const.py`, hoặc nhập địa
> chỉ mới ở ô *License server URL* khi cấu hình.

---

## 4. Thêm vào Home Assistant

1. **Settings → Devices & Services → Add Integration**.
2. Tìm **RalliSmart V2**.
3. Nhập lần lượt:
   - **Tài khoản RalliSmart** (số điện thoại/email + mật khẩu app).
   - **Chọn nhà** (nếu tài khoản có nhiều nhà).
   - **License Key** (dán key đã lấy ở bước 3).
4. Hoàn tất. Các thiết bị sẽ xuất hiện theo từng khu vực/thiết bị.

---

## 5. Thiết bị được hỗ trợ

| Loại | Chức năng |
|------|-----------|
| Đèn | Bật/tắt, độ sáng; đổi màu nhiệt độ (CCT) hoặc màu RGB tuỳ thiết bị |
| Công tắc | Bật/tắt; panel nhiều nút hiện thành nhiều *Gang* trong cùng một thiết bị |
| Rèm / cửa cuốn | Mở / dừng / đóng |
| Cảm biến | Nhiệt độ, độ ẩm, bụi mịn PM2.5 |
| Nhị phân | Khói, cửa, chuyển động |
| Camera | Xem trực tiếp RTSP/Camera (nếu tài khoản đã cấu hình camera) |
| Scene | Gọi/ kích hoạt các cảnh của app |
| Button | *Identify* – nháy thiết bị để nhận biết |

---

## 6. Xử lý sự cố

- **Không thêm được / báo license sai**: kiểm tra key đã copy đủ chưa; key có
  thể đã hết hạn, bị khóa, hoặc đã dùng cho cài đặt khác (cần reset).
- **Không kết nối máy chủ bản quyền**: kiểm tra mạng; có thể nhập lại địa chỉ
  máy chủ bản quyền ở bước cấu hình.
- **Một số cảm biến/camera không thấy**: chỉ hiện khi tài khoản/nhà thực sự có
  thiết bị đó và (với camera) đã được cấu hình trong app.
- **Để gỡ license**: xoá integration khỏi HA, hoặc liên hệ admin thu hồi key.
