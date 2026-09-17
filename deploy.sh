#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  TELE SHOP BOT — AUTO DEPLOY & CHẠY NGẦM VĨNH VIỄN (systemd)
# ═══════════════════════════════════════════════════════════════
#  Cài lần đầu (trên VPS, chạy bằng root/sudo):
#      sudo bash deploy.sh
#  Cập nhật code mới + khởi động lại:
#      sudo bash deploy.sh update
#  Xem log:
#      journalctl -u telebot -f
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_URL="https://github.com/datchimtoa/vipbanhang.git"
INSTALL_DIR="/opt/telebot"
SERVICE_NAME="telebot"
BRANCH="main"
VENV="$INSTALL_DIR/venv"

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Phải chạy bằng root. Dùng: sudo bash deploy.sh"
    exit 1
fi

RUN_USER="${SUDO_USER:-root}"

log()  { echo -e "\033[1;32m[✓]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
die()  { echo -e "\033[1;31m[✗]\033[0m $*"; exit 1; }

# ── Chọn interpreter Python tốt nhất có sẵn ────────────────────
# Ưu tiên >=3.10 (chạy PTB 22.x). Nếu chỉ có 3.9 thì dùng markers
# trong requirements.txt (tự cài PTB 21.x tương thích).
pick_python() {
    local c
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1; then
            if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                echo "$c"
                return 0
            fi
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"   # fallback <3.10
        return 0
    fi
    return 1
}

# ── 1. Cài Python + Git nếu thiếu (Ubuntu/Debian) ──────────────
if ! command -v python3 >/dev/null || ! command -v git >/dev/null; then
    log "Cài python3, python3-venv, git..."
    if command -v apt-get >/dev/null; then
        apt-get update -y && apt-get install -y python3 python3-venv python3-pip git
    elif command -v dnf >/dev/null; then
        dnf install -y python3 python3-pip git
        # thử cài bản mới hơn nếu có
        dnf install -y python3.11 python3.11-pip 2>/dev/null || true
        dnf install -y python3.12 python3.12-pip 2>/dev/null || true
    elif command -v yum >/dev/null; then
        yum install -y python3 python3-pip git
        yum install -y python3.11 python3.11-pip 2>/dev/null || true
    else
        die "Không tìm thấy trình quản lý gói. Hãy tự cài python3 + git."
    fi
fi

PY_BIN="$(pick_python)" || die "Không tìm thấy Python."
PY_VER="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Dùng Python: $PY_BIN ($PY_VER)"

# ── 2. Lấy / cập nhật code từ GitHub ───────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Cập nhật code mới nhất từ GitHub..."
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
    log "Clone repo từ GitHub..."
    rm -rf "$INSTALL_DIR.tmp"
    git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR.tmp"
    mv "$INSTALL_DIR.tmp" "$INSTALL_DIR"
fi
chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR" 2>/dev/null || true

# ── 3. Tạo file .env nếu chưa có ───────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    warn "Đã tạo $INSTALL_DIR/.env từ mẫu."
    warn "👉 BẠN PHẢI SỬA file .env (điền BOT_TOKEN, ADMIN_IDS, bank/ví...) rồi chạy lại:"
    warn "   nano $INSTALL_DIR/.env && sudo bash deploy.sh"
    # vẫn tiếp tục để cài hết, nhưng không start service nếu chưa có token thật
    NEED_ENV=1
fi

if grep -qE "^BOT_TOKEN=(123456:|$)" "$INSTALL_DIR/.env" 2>/dev/null; then
    NEED_ENV=1
    warn "BOT_TOKEN trong .env vẫn là placeholder."
fi

# ── 4. Tạo môi trường ảo + cài thư viện ────────────────────────
log "Tạo venv + cài thư viện..."
# Dọn venv cũ nếu nó được tạo bằng bản Python khác (tránh lẫn lib)
if [ -x "$VENV/bin/python" ]; then
    OLD_VER="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
    if [ "$OLD_VER" != "$PY_VER" ]; then
        warn "Venv cũ dùng Python $OLD_VER khác $PY_VER — tạo lại venv."
        rm -rf "$VENV"
    fi
fi
"$PY_BIN" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
log "Cài thư viện xong ($( "$VENV/bin/python" -c 'import telegram; print("PTB", telegram.__version__)' 2>/dev/null || echo '?'))."

# ── 5. Tạo systemd service (chạy ngầm vĩnh viễn, tự restart) ───
log "Tạo systemd service '$SERVICE_NAME'..."
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<UNIT
[Unit]
Description=Tele Shop Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV/bin/python $INSTALL_DIR/bot_vps.py
Restart=always
RestartSec=3
User=$RUN_USER

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

# ── 6. Khởi động ───────────────────────────────────────────────
if [ "${NEED_ENV:-0}" = "1" ]; then
    warn "Chưa start bot vì .env chưa cấu hình xong."
    warn "1) nano $INSTALL_DIR/.env"
    warn "2) sudo bash deploy.sh     <- chạy lại để start"
    exit 0
fi

log "Khởi động bot (chạy ngầm vĩnh viễn, tự bật lại khi VPS restart)..."
systemctl enable --now "$SERVICE_NAME"
sleep 2
systemctl --no-pager --full status "$SERVICE_NAME" | head -12 || true

echo
log "HOÀN TẤT! Bot đang chạy nền vĩnh viễn."
echo "   • Xem log        : journalctl -u $SERVICE_NAME -f"
echo "   • Khởi động lại  : sudo systemctl restart $SERVICE_NAME"
echo "   • Dừng bot       : sudo systemctl stop $SERVICE_NAME"
echo "   • Cập nhật code  : sudo bash $INSTALL_DIR/deploy.sh update"
