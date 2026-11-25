# RalliSmart V2 — Tích hợp Home Assistant

Website bản quyền: https://rallismart-license.vercel.app

Repository HACS: https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant

## Trạng thái dịch vụ

Website, database Neon và webhook PayOS đã được triển khai. Đăng ký tài khoản mới bằng email + mật khẩu là mở ngay, không cần xác minh email; chỉ cần đăng nhập là dùng được và nhận key dùng thử. Không chuyển tiền qua link ngoài website; chỉ thanh toán đơn do dashboard của tài khoản tạo.

## Yêu cầu

- Home Assistant: đã kiểm thử các lớp tích hợp trên phiên bản 2026.2.3; chưa xác nhận tương thích với các bản cũ hơn.
- Tài khoản app RalliSmart V2 của Rạng Đông.
- HC01 dùng firmware v1: đã xác minh SignalR trên firmware 1.2.45. HC firmware v2 dùng cloud MQTT: đã xác minh trên `RD_MH_F93C` firmware 2.2.3 với panel 2/3/4 nút. Công tắc WiFi Tuya và cửa cuốn WiFi Tuya loại `45001` được hỗ trợ qua cloud proxy (kể cả nhà có HC).
- Cài đặt mới cần License Key. Bản đã cấu hình trước đây chưa có key tiếp tục hoạt động để không làm gián đoạn thiết bị.

## Cài bằng HACS

1. Mở HACS → menu ba chấm → Custom repositories.
2. Thêm `https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant`, loại **Integration**.
3. Tải **RalliSmart V2**, sau đó khởi động lại Home Assistant.
4. Vào Settings → Devices & Services → Add Integration → RalliSmart V2.
5. Nhập tài khoản RalliSmart và License Key. Tất cả nhà thuộc tài khoản tự động được thêm vào cùng một tích hợp; không cần thêm lại cho từng nhà. Địa chỉ máy chủ bản quyền được tích hợp sẵn — không cần nhập; bấm link trong form để mở trang kích hoạt lấy key.

## Cài thủ công

1. Giải nén `rallismart-custom-0.6.8.zip`.
2. Copy thư mục `custom_components/rallismart` vào `/config/custom_components/rallismart`.
3. Khởi động lại Home Assistant, rồi thêm integration như trên.

Zip chỉ chứa thành phần cần cài và hướng dẫn, không chứa server, test, PoC, tài khoản hoặc khóa thanh toán. Mã Python là thành phần bắt buộc để Home Assistant nạp integration; không phải mã bị mã hóa.

## Nâng cấp bản đang chạy

1. Sao lưu cấu hình Home Assistant trước khi cập nhật.
2. Cập nhật bằng HACS hoặc ghi đè các file integration, rồi restart HA.
3. Không xóa integration, thiết bị, entity hoặc automation đang có.
4. Tài khoản, nhà, entity unique ID và các nút công tắc giữ nguyên.
   - Entry cũ chỉ có một nhà sẽ tự mở rộng ra mọi nhà của tài khoản.
   - Nếu trước đây đã thêm nhiều entry cho cùng tài khoản, mỗi entry tiếp tục phụ trách nhà của nó (không tạo entity trùng). Có thể xóa các entry thừa và giữ lại một entry duy nhất.
   - Bản 0.6.4 thêm HC firmware v2 qua cloud MQTT (panel 2/3/4 nút), giữ nguyên entity/unique ID; không cần xóa entry hay entity.
   - Bản 0.6.5 thêm camera độc lập của app RalliSmart 1.5.3 (không cần HC/Tuya), giữ nguyên entity/unique ID hiện có.
   - Bản 0.6.6 thêm cover cho cửa cuốn WiFi Tuya (`45001`): mở/dừng/đóng và đặt % mở qua cloud proxy.
   - Bản 0.6.7 sửa vị trí cover lấy từ `percent_control` (đúng DP app dùng cho `OPENED`) thay vì `percent_state` luôn bằng 0, khắc phục nút đóng bị mờ và trạng thái không đổi; lệnh mở/đóng cập nhật vị trí ngay trên HA.
   - Bản 0.6.8 thêm poll nhanh cho cửa cuốn Tuya tại các mốc 1/3/6/10 giây sau mỗi lệnh HA, sau đó trở lại chu kỳ 30 giây; thay đổi từ app vẫn được cập nhật theo chu kỳ 30 giây.
   - HC v2 được poll trạng thái mỗi 30 giây vì chưa xác nhận có push; HC v1 vẫn nhận thay đổi ngay qua SignalR.
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
- Thay đổi từ app RalliSmart được HA áp dụng ngay khi cloud phát bản tin `Command`, không chờ HC xác nhận. SignalR tự nối lại khi mất kết nối và đồng bộ lại trạng thái sau đó.
- Nhiều nhà trong cùng một entry: tên thiết bị, scene và camera được thêm tiền tố tên nhà (ví dụ `Nha · Đèn phòng khách`) để dễ nhận biết; entity ID và unique ID giữ nguyên.
- HC v2: panel 2/3/4 nút dùng MQTT; mỗi gang là entity riêng, điều khiển qua id panel cha và trường `bt`/`bt2`/`bt3`/`bt4`. Trạng thái được poll mỗi 30 giây.
- Công tắc WiFi Tuya (kể cả nhà có HC): mỗi gang là entity riêng, trạng thái cập nhật mỗi 30 giây và điều khiển qua cloud proxy.
- Cửa cuốn WiFi Tuya (`45001`): là một cover, hỗ trợ mở/dừng/đóng và đặt % mở; vị trí đọc từ `percent_control`; sau mỗi lệnh HA poll nhanh tại 1/3/6/10 giây rồi trở lại chu kỳ 30 giây. Đã xác minh đường dẫn lệnh bằng lệnh `stop`, chưa chạy thử đóng/mở thật.
- Chỉ nhà thuộc tài khoản đang đăng nhập mới hiện trong danh sách; nhà của tài khoản khác bị ẩn.
- Đèn: on/off; DIM/CCT/RGB có mapping hiện có nhưng chưa kiểm chứng đủ mọi model. Với HC v2, mapping JSON `onoff`/`dim`/`cct`/`h`/`s`/`l` mới chỉ kiểm thử bằng unit test, chưa có đèn v2 thực tế.
- Rèm/cửa cuốn: giữ chức năng hiện có; chiều và tính năng chưa được kiểm chứng đầy đủ, cần quan sát trực tiếp khi dùng lần đầu.
- Sensor/binary sensor: chỉ hiện cho model đã có mapping, không tạo sensor giả cho nhà không có thiết bị đó.
- Camera: hỗ trợ camera gắn HC và camera độc lập trong app RalliSmart 1.5.3 (type `61001`-`61007`). HA cần truy cập được mạng chứa camera và cloud phải trả URL RTSP/HTTP hợp lệ; URL không có scheme hoặc danh sách camera rỗng không chứng minh camera không có cấu hình trên app.
- Scene và Identify: có thể thay đổi nhiều tải điện. Kiểm tra thiết bị đích trước khi nhấn. Chưa xác nhận đầy đủ trên phần cứng.
- Trạng thái ban đầu được đồng bộ một lần từ cloud; thiết bị không có giá trị trên cloud vẫn có thể unknown cho tới khi nhận push hoặc lần poll kế tiếp. Không khẳng định mọi chức năng app gốc đã được hỗ trợ.
- Broker MQTT v2 dùng chứng chỉ tự ký; tích hợp hiện tắt kiểm tra chứng chỉ (`CERT_NONE`). Chưa hỗ trợ pinning.

## Báo lỗi

https://github.com/trankhanhduy2929-beep/rallismart-v2-homeassistant/issues
