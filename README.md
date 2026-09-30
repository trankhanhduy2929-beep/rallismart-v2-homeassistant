# RalliSmart V2 — Tích hợp Home Assistant

Website bản quyền: https://rallismart-license.vercel.app

Repository HACS: https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant

## Trạng thái dịch vụ

Website, database Neon và webhook PayOS đã được triển khai. Đăng ký tài khoản mới bằng email + mật khẩu là mở ngay, không cần xác minh email; chỉ cần đăng nhập là dùng được và nhận key dùng thử. Không chuyển tiền qua link ngoài website; chỉ thanh toán đơn do dashboard của tài khoản tạo.

## Yêu cầu

- Home Assistant: đã kiểm thử các lớp tích hợp trên phiên bản 2026.2.3; chưa xác nhận tương thích với các bản cũ hơn.
- Tài khoản app RalliSmart V2 của Rạng Đông.
- HC01 dùng firmware v1; đã xác minh đường điều khiển SignalR trên firmware 1.2.45. Không hỗ trợ đường MQTT v2 trong bản này.
- Cài đặt mới cần License Key. Bản đã cấu hình trước đây chưa có key tiếp tục hoạt động để không làm gián đoạn thiết bị.

## Cài bằng HACS

1. Mở HACS → menu ba chấm → Custom repositories.
2. Thêm `https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant`, loại **Integration**.
3. Tải **RalliSmart V2**, sau đó khởi động lại Home Assistant.
4. Vào Settings → Devices & Services → Add Integration → RalliSmart V2.
5. Nhập tài khoản RalliSmart, chọn nhà, nhập License Key. Địa chỉ máy chủ bản quyền được tích hợp sẵn — không cần nhập; bấm link trong form để mở trang kích hoạt lấy key.

## Cài thủ công

1. Giải nén `rallismart-custom-0.5.3.zip`.
2. Copy thư mục `custom_components/rallismart` vào `/config/custom_components/rallismart`.
3. Khởi động lại Home Assistant, rồi thêm integration như trên.

Zip chỉ chứa thành phần cần cài và hướng dẫn, không chứa server, test, PoC, tài khoản hoặc khóa thanh toán. Mã Python là thành phần bắt buộc để Home Assistant nạp integration; không phải mã bị mã hóa.

## Nâng cấp bản đang chạy

1. Sao lưu cấu hình Home Assistant trước khi cập nhật.
2. Cập nhật bằng HACS hoặc ghi đè các file integration, rồi restart HA.
3. Không xóa integration, thiết bị, entity hoặc automation đang có.
4. Tài khoản, nhà, entity unique ID và các nút công tắc giữ nguyên.
5. Vào Settings → Devices & Services → RalliSmart V2 → **Configure / Cấu hình** để nhập hoặc thay License Key. Việc lưu key sẽ nạp lại entry đó.

Nếu entry cũ chưa có key, thông báo tương thích sẽ hiện link website; thiết bị không bị chặn chỉ vì nâng cấp. Sau khi nhập key, entry chuyển sang kiểm tra license. Cài đặt mới luôn yêu cầu key hợp lệ.

## License

1. Đăng ký tại https://rallismart-license.vercel.app/register bằng email + mật khẩu; tài khoản dùng được ngay, không cần xác minh email.
2. Vào Dashboard, chọn gói:
   - Dùng thử: miễn phí 1 ngày, một lần cho tài khoản và một lần cho ID cài đặt.
   - 1 tháng: 30 ngày, 50.000đ.
   - Vĩnh viễn: 200.000đ.
3. Thanh toán QR hoặc mở trang PayOS từ đơn trong dashboard.
4. Server kiểm tra chữ ký webhook và trạng thái thanh toán trên PayOS, sau đó tự cấp key. Không cần admin duyệt.
5. Xem lại key và lịch sử đơn trên dashboard; dán key vào cấu hình integration.

Mỗi key gắn với một ID cài đặt HA lưu trong `.storage/rallismart_install`. Nhiều nhà trong cùng cài đặt có thể dùng cùng key. Dùng trial rồi mua key mới không cần xóa integration. Đổi máy cần admin reset liên kết; xóa integration không tự giải phóng key.

Không chia sẻ key, thư mục `.storage` hay bản sao cấu hình HA. Cơ chế này hạn chế chia sẻ thông thường; không thể chống tuyệt đối việc sao chép toàn bộ cài đặt hoặc sửa mã Python.

## Khi license hoặc mạng gặp sự cố

- License bị khóa, thu hồi hoặc hết hạn: các entity chuyển unavailable; lệnh điều khiển bị chặn. Đổi key trong **Cấu hình**, không xóa entry.
- Máy chủ license tạm mất kết nối khi integration đang chạy: giữ trạng thái hợp lệ đã biết, không vượt thời điểm hết hạn của key có thời hạn.
- Khi HA khởi động lại, key đã cấu hình cần được xác thực online; lỗi mạng sẽ khiến HA thử lại.
- Máy chủ chính thức: `https://rallismart-license.vercel.app`. Chỉ thay URL khi quản trị viên cung cấp địa chỉ HTTPS tin cậy, vì key sẽ được gửi tới máy chủ đó.
- Không đưa tài khoản RalliSmart, License Key, URL RTSP chứa mật khẩu hoặc toàn bộ log bí mật vào issue công khai.

## Chức năng và giới hạn

- Công tắc nhiều nút: từng gang là entity riêng, gom vào thiết bị panel; giữ riêng địa chỉ điều khiển từng gang.
- Đèn: on/off; DIM/CCT/RGB có mapping hiện có nhưng chưa kiểm chứng đủ mọi model.
- Rèm/cửa cuốn: giữ chức năng hiện có; chiều và tính năng chưa được kiểm chứng đầy đủ, cần quan sát trực tiếp khi dùng lần đầu.
- Sensor/binary sensor: chỉ hiện cho model đã có mapping, không tạo sensor giả cho nhà không có thiết bị đó.
- Camera: HA cần truy cập được mạng chứa camera và URL RTSP hợp lệ; danh sách camera rỗng không chứng minh camera không có cấu hình trên app.
- Scene và Identify: có thể thay đổi nhiều tải điện. Kiểm tra thiết bị đích trước khi nhấn. Chưa xác nhận đầy đủ trên phần cứng.
- Trạng thái ban đầu có thể unknown cho tới khi nhận push. Không khẳng định mọi chức năng app gốc đã được hỗ trợ.

## Báo lỗi

https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant/issues
