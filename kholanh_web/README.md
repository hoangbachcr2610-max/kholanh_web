# Kho lạnh — Service chạy nền + Web có đăng nhập

```
 SHT30 + Relay ──RS485── [PC kho lạnh]
                          ├─ controller.py  (luồng nền: đọc Modbus, báo động, tự động bật/tắt quạt, ghi SQLite)
                          └─ app.py (Flask + waitress, cổng 8000) ◄── LAN / Tailscale / Cloudflare Tunnel ◄── điện thoại
```

| File | Việc |
|---|---|
| `modbus.py` | Modbus RTU + bộ giả lập (giữ nguyên logic cũ) |
| `controller.py` | Toàn bộ logic của class `App` Tkinter cũ, bỏ giao diện |
| `remote.py` | Truy cập từ xa: LAN, Tailscale, Cloudflare Quick Tunnel (như cửa sổ "Điều khiển từ xa" cũ) |
| `db.py` | SQLite: tài khoản, số liệu, nhật ký, audit |
| `app.py` | Web, đăng nhập, phân quyền, API |
| `run_server.py` | Khởi động tất cả |
| `manage.py` | Tạo/khóa/đổi mật khẩu tài khoản, liệt kê cổng COM |
| `config.json` | Giá trị ban đầu + cổng web. Cổng COM, baud, ID… chỉnh trên web ở mục **Thiết bị** |

Phân quyền: **viewer** (xem, tải CSV) · **operator** (+ đổi Auto/Thủ công, bật/tắt quạt, đổi ngưỡng) · **admin** (+ failsafe, mục Thiết bị, mục Từ xa, quản lý tài khoản, nhật ký thao tác).

## Chạy (đã cài Python)
Trong thư mục chứa `run_server.py`:
```
python -m pip install -r requirements.txt
python manage.py add-user admin --role admin
python run_server.py --simulate      # thử bằng dữ liệu giả
python run_server.py                 # chạy thật
```
Mở http://127.0.0.1:8000 và đăng nhập. Không có `--simulate` thì chương trình chưa kết nối gì cho tới khi bạn vào **Thiết bị** bấm KẾT NỐI.
Chạy không cần VS Code: bấm đúp `start.bat`, hoặc xem phần "Tự chạy ngầm" bên dưới.

## Kết nối thiết bị thật (mục **Thiết bị**, giống bảng "Kết nối Modbus RTU" cũ)
1. **Tắt hẳn app Tkinter cũ** (một cổng COM chỉ một chương trình dùng được; Modbus Poll cũng vậy).
2. Đăng nhập bằng tài khoản admin → **Thiết bị**: chọn cổng COM (↻ để quét lại, hoặc gõ tay như `COM6`), baud, parity, chu kỳ đọc, ID cảm biến, ID relay, function đọc, thứ tự thanh ghi. Bấm **KẾT NỐI**.
3. Qua **Tổng quan** kiểm tra nhiệt độ/độ ẩm, rồi thử bật/tắt quạt ở chế độ Thủ công **khi đang đứng cạnh kho**.

Hành vi cần biết:
- Thông số được **lưu lại**. Sau khi đã bấm KẾT NỐI, nếu mất điện hoặc khởi động lại máy, chương trình **tự kết nối lại**; rút/cắm lại USB-RS485 cũng tự nối lại (thử mỗi 10 giây).
- Đổi thông số khi đang chạy: sửa rồi bấm **Áp dụng** (nối lại ngay).
- **NGẮT KẾT NỐI** là dừng hẳn, không báo động giả, không tự nối lại.
- Kết nối thủ công thất bại (sai cổng...) thì báo lỗi ngay, không tự thử lại âm thầm.
- Ngưỡng/chế độ/failsafe cũng lưu trong CSDL (đổi trên web), không nằm trong `config.json`.
- Mỗi lần kết nối, ở chế độ Tự động chương trình ghi trạng thái 2 quạt cho khớp số liệu hiện tại.

## Truy cập từ xa (mục **Từ xa**, giống cửa sổ "Điều khiển từ xa" cũ)
Cả 3 cách dùng chung **tài khoản đăng nhập** (không còn mã PIN).
1. **Cùng Wi-Fi/LAN**: hiện sẵn `http://IP-máy:8000`, bấm Sao chép. Cần `"host": "0.0.0.0"` (mặc định đã vậy). Lần đầu Windows có thể hỏi quyền Firewall: cho phép mạng riêng.
2. **Tailscale** (khuyên dùng): máy này và điện thoại cùng đăng nhập một tài khoản Tailscale. Trang tự phát hiện IP `100.x.x.x`; bấm "Kiểm tra lại" nếu mới cài.
3. **Cloudflare Tunnel (link Internet)**: bấm **Bật tunnel**, đợi hiện link `https://xxxx.trycloudflare.com` (đổi mỗi lần bật). Có ô "Tự bật tunnel khi chương trình khởi động". Nếu `cloudflared` không nằm trong PATH, ghi đường dẫn đầy đủ vào `"tunnel_bin"` trong `config.json` (chương trình cũng tự tìm trong `C:\Program Files (x86)\cloudflared\`).

Cookie đăng nhập tự gắn cờ Secure khi vào bằng HTTPS (Cloudflare), không gắn khi vào bằng http://IP-LAN hoặc Tailscale.

## Tự chạy ngầm khi bật máy (không cần VS Code)
Đăng nhập Windows rồi mới chạy: dùng Task Scheduler với `pythonw.exe` (bản không hiện cửa sổ). Đổi đường dẫn cho đúng máy bạn (`where python` để tìm):
```
schtasks /Create /TN "KhoLanh" /SC ONLOGON /F /TR "\"C:\...\Python312\pythonw.exe\" \"E:\...\kholanh_web\run_server.py\""
schtasks /Run /TN "KhoLanh"
schtasks /End /TN "KhoLanh"
```
Chạy kể cả khi chưa ai đăng nhập: dùng NSSM (nssm.cc), Command Prompt **Run as administrator**:
```
nssm install KhoLanh "C:\...\python.exe" "E:\...\kholanh_web\run_server.py"
nssm set KhoLanh AppDirectory E:\...\kholanh_web
nssm set KhoLanh Start SERVICE_AUTO_START
nssm start KhoLanh
```
Chỉ chạy MỘT bản tại một thời điểm (cổng 8000 và cổng COM chỉ một chương trình dùng được). Log: `data\kholanh.log`. Đặt Windows không tự Sleep.

## Nhập lịch sử CSV cũ (tuỳ chọn)
`python import_csv.py C:\duong_dan\kho_lanh_log.csv`

## Sao lưu
Sao lưu thư mục `data\` (chứa `kholanh.db`) định kỳ.

## Việc nên làm tiếp
- Cảnh báo Telegram/Zalo/email khi vượt ngưỡng (hiện chỉ hiện trên web; tiếng bíp app cũ đã bỏ).
- Thiết bị an toàn độc lập (PLC/ESP32/relay có watchdog) cho trường hợp PC treo: khi đó quạt giữ nguyên trạng thái cuối.
