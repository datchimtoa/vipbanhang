# 🚀 TRIỂN KHAI BOT LÊN VPS (headless — không cần màn hình)

Bản `bot_vps.py` là bản **chạy nền trên VPS**, không có GUI. Config 100% trong `.env`.

## ⚡ CÁCH NHANH NHẤT — 1 LỆNH DUY NHẤT

Trên VPS (Ubuntu/Debian), chạy:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/datchimtoa/vipbanhang/main/deploy.sh)"
```

Script tự làm hết: cài Python + venv → clone code → tạo `.env` từ mẫu → cài thư viện →
tạo systemd service → **chạy ngầm vĩnh viễn, tự restart khi crash / tự bật khi VPS reboot**.

Sau đó chỉ cần sửa cấu hình rồi chạy lại 1 lần:

```bash
nano /opt/telebot/.env        # điền BOT_TOKEN, ADMIN_IDS, bank/ví...
sudo bash /opt/telebot/deploy.sh   # chạy lại để start bot
```

## 1. Cài đặt thủ công (nếu muốn tự làm từng bước)

```bash
mkdir -p /opt/telebot && cd /opt/telebot
git clone https://github.com/datchimtoa/vipbanhang.git .
cp .env.example .env
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Sửa file `.env`

Mở `/opt/telebot/.env`, điền:
- `BOT_TOKEN` — từ @BotFather
- `ADMIN_IDS` — ID Telegram của bạn (gõ `/id` cho bot hoặc dùng @userinfobot)
- `ADMIN_USERNAME` — username admin (chỉ dùng khi bot báo lỗi/hết kho; **OTP lấy ngay tại bot**)
- `ADMIN_IDS` — ID Telegram admin nhận yêu cầu nạp tiền + yêu cầu OTP
- Thông tin ngân hàng / ví USDT BEP20 / ví TON (kênh nào để trống sẽ tự ẩn)
- `SHOW_ACC_INFO=1` — gửi kèm MK/2FA cho khách (đổi runtime bằng `/setaccinfo`)

## 3. Chạy thử

```bash
cd /opt/telebot && venv/bin/python bot_vps.py
```
Thấy dòng `🚀 Bot đang chạy...` là OK. Ctrl+C để dừng.

## 4. Chạy 24/7 bằng systemd (deploy.sh đã làm tự động)

```bash
sudo bash /opt/telebot/deploy.sh          # cài lại + start (idempotent)
sudo bash /opt/telebot/deploy.sh update   # pull code mới + restart
systemctl daemon-reload
systemctl enable --now telebot

# Xem log realtime:
journalctl -u telebot -f

# Khởi động lại sau khi sửa .env:
systemctl restart telebot
```

## 5. Cách dùng — TOÀN BỘ bằng NÚT TRÊN BÀN PHÍM (Reply Keyboard)

### Menu chính (bàn phím, không phải nút dưới chat)
`📱 Mua Acc Telegram` | `💳 Nạp tiền` | `📦 Chuyên mục` | `💰 Số dư` | `🧾 Lịch sử mua` | `🤝 Tiếp thị` | `🛠 Admin Panel` (admin) | `⬅️ Menu chính` (quay lại mọi lúc)

### 📱 Mua Acc Telegram
1. Bấm `📱 Mua Acc Telegram` → bot hỏi **mua gói hay mua lẻ**:
   - `🎟 Mua gói acc` → chọn gói 10/20/50/100... trên bàn phím → `✅ Xác nhận mua`
   - `📱 Mua acc lẻ` → 1 acc, giá do `/setprice` đặt
2. Nhận danh sách SĐT + **nút `🔑 Nhận OTP`** bên dưới (mỗi acc 1 nút).

### 🔑 Nhận OTP (user ↔ admin)
1. Ngay sau khi mua thành công, dưới đơn hàng **luôn hiện nút `🔑 Nhận OTP — <sđt>`**
   cho từng acc (tin nút riêng + fallback bàn phím nếu inline lỗi).
   **Sau khi mua acc, lấy OTP ngay tại bot này** — không cần liên hệ admin nữa.
2. User bấm nút (hoặc gõ `/layotp <sđt>`) → bot có thể gửi thông báo yêu cầu về admin
   với đầy đủ tham số: 👤 tên người mua (tên Telegram), 🆔 ID Telegram, 📞 SĐT acc đã mua.
3. Mua SLL nhiều acc → dùng **`/layotpsll <sdt1> <sdt2> ...`**: mỗi SĐT cách nhau
   1 dấu cách, **mỗi lần tối đa 10 SĐT**. Ví dụ:
   `/layotpsll 0912345678 0987654321 0933111222`
   → bot gửi **từng yêu cầu riêng lẻ** về admin (có SĐT trong từng tin nhắn);
   admin gửi từng OTP; bot **trả lại từng OTP theo đúng thứ tự, kèm SĐT của mã đó**.
4. Admin **chỉ cần reply (trả lời) chính tin nhắn yêu cầu bằng mã OTP**
   (hoặc dùng `/guiotp <id> <mã>`) → bot **tự động gửi OTP cho khách** ngay.
   👉 Bot **hiểu mọi ngôn ngữ**: `123456`, `OTP: 123456`, `mã otp là 123456`,
   `验证码 123456`, `رمز التحقق 123456`... đều gửi đúng mã.
5. **Gửi kèm MẬT KHẨU / 2FA (tu chọn — có hay không đều được):**
   - Admin reply: `123456 | matkhau | 2FAKEY` (thiếu phần nào cũng OK),
     hoặc `/guiotp 12 123456 | matkhau | 2FAKEY`.
   - Nếu admin chỉ gửi mỗi mã OTP, bot **tự lấy MK/2FA đã lưu của acc đó** gửi kèm
     (khi tính năng đang bật — xem `/setaccinfo`).
6. Bot chống spam/trùng: 1 acc chỉ có 1 yêu cầu chờ; mã không gửi 2 lần.

###  Nhập acc kèm MẬT KHẨU / 2FA (tuỳ chọn)
Gõ danh sách vào 1 tin nhắn rồi **reply** bằng `/addsll [vn|ngoai]`, mỗi dòng 1 acc:

```
0912345678 | matkhau123 | 2FAKEY
0987654321 | matkhau456
0913111222
```

- `mk` **và** `2fa` đều **tuỳ chọn**: có thể chỉ SĐT, SĐT + MK, hoặc SĐT + MK + 2FA.
- Khi giao acc, bot hiện `sđt | mk | 2fa`; với OTP bot gửi kèm `🔒 MK` và `🛡 2FA`.
- Bật/tắt việc gửi kèm cho khách: **`/setaccinfo on`** / **`/setaccinfo off`**
  (mặc định lấy từ `SHOW_ACC_INFO=1` trong `.env`).

### 💳 Nạp tiền
1. Bấm `💳 Nạp tiền` → chọn kênh trên bàn phím: `🏦 Ngân hàng (VietQR)` / `💵 USDT (BEP20)` / `💎 Gram (TON)`
2. Nhập số tiền → nhận QR + thông tin chuyển khoản.
3. **Nội dung chuyển khoản: `Napid <idtelegram>`** (ID Telegram của user — mỗi user cố định 1 nội dung, giống bản gốc; xem lại bằng lệnh `/id`).
4. Chuyển xong bấm `✅ Tôi đã chuyển tiền` → bot gửi yêu cầu tới **admin duyệt** (`/duyet <id>` / `/tuchoi <id>`) → user nhận thông báo.

### 🤝 Tiếp thị
Mỗi user có link `https://t.me/<bot>?start=ref<id>` — người được giới thiệu mua hàng thì người giới thiệu nhận % hoa hồng (mặc định 5%, đổi bằng `/setaff`).

### 📋 Lệnh dành cho user
| Lệnh | Chức năng |
|---|---|
| `/start` | Mở menu chính (kèm bàn phím) |
| `/id` | Xem ID Telegram + nội dung nạp `Napid <id>` |
| `/layotp <sđt>` | Yêu cầu OTP cho acc đã mua (dùng cho đơn nhiều acc) |
| `/layotpsll <sdt1> <sdt2> ...` | Lấy OTP SLL: mỗi SĐT cách 1 dấu cách, tối đa 10 SĐT/lần |


### Admin (lệnh slash + nút `🛠 Admin Panel`)
| Lệnh | Chức năng |
|---|---|
| `/addsll [vn\|ngoai] 09xx 09yy ...` | Thêm SĐT (cách / xuống dòng / phẩy; hoặc **reply** danh sách). Kèm MK/2FA tuỳ chọn: mỗi dòng `sdt \| mk \| 2fa` |
| `/setaccinfo on\|off` | Bật/tắt gửi kèm **MK + 2FA** cho khách khi giao acc / gửi OTP |
| `/delsll <sdt>` | Xoá 1 acc khả dụng |
| `/stock` hoặc nút `📦 Kho acc` | Xem kho |
| `/pending` hoặc nút `⏳ Nạp chờ duyệt` | Danh sách nạp chờ duyệt |
| `/duyet <id>` / `/tuchoi <id>` | Duyệt / từ chối nạp |
| **Reply tin nhắn YC OTP bằng mã OTP** | Gửi OTP cho khách (cách nhanh nhất) |
| `/otplist` hoặc nút `🔑 OTP chờ gửi` | Danh sách yêu cầu OTP đang chờ |
| `/guiotp <id> <mã>` | Gửi OTP cho khách theo số yêu cầu. Kèm MK/2FA: `/guiotp <id> <mã> \| mk \| 2fa` |
| `/setpack <size> <giá>` | Tạo/đổi giá gói |
| `/setprice <giá>` | Đổi giá acc lẻ |
| `/setrate <usdt\|ton> <vnd>` | Đổi tỉ giá USDT/TON |
| `/setaff <%>` | Đổi % hoa hồng tiếp thị |
| `/addbal <uid> <số>` | Cộng/trừ số dư user |
| `/stats` hoặc nút `📊 Thống kê` | Thống kê |

## 6. Ghi chú kỹ thuật
- Bot tự rep nhanh: `concurrent_updates` + connection pool 256 + SQLite WAL.
- Mua acc & duyệt nạp là transaction nguyên tử — không bao giờ bán trùng acc hay cộng tiền 2 lần.
- Mọi tiền tệ lưu theo VND; USDT/TON quy đổi theo tỉ giá trong `.env` (hoặc `/setrate`).
