#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TELE SHOP BOT — VPS EDITION v2 (Reply Keyboard)
===============================================
- Toàn bộ chức năng thao tác bằng NÚT TRÊN BÀN PHÍM (Reply Keyboard),
  không dùng nút inline dưới chat.
- Menu chính: 📦 Chuyên mục | 📱 Mua Acc Telegram | 💳 Nạp tiền |
  🧾 Lịch sử mua | �� Tiếp thị | 💰 Số dư | 🛠 Admin Panel (admin)
- Nội dung chuyển khoản: "Napid <idtelegram>" của user (giống bản gốc).
- Mua Acc Telegram: chọn "🎟 Mua gói acc" (10/20/50/100...) hoặc "📱 Mua acc lẻ".
- Nạp tiền: 🏦 Bank (VietQR) / 💵 USDT (BEP20) / 💎 Gram (TON) -> user bấm
  "✅ Tôi đã chuyển tiền" -> bot gửi yêu cầu vào chat ADMIN để duyệt.
- Sau khi mua, bot thông báo liên hệ @admin để nhận mã OTP.

Chạy: python3 bot_vps.py
"""

import html
import logging
import os
import re
import sqlite3
import threading
import urllib.parse
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ─────────────────────────────────────────────
# CONFIG — 100% từ file .env
# ─────────────────────────────────────────────
load_dotenv()


def _int(name: str, default) -> int:
    try:
        return int(str(os.getenv(name, default)).replace(",", "").strip())
    except Exception:
        return int(default)


def _float(name: str, default) -> float:
    try:
        return float(str(os.getenv(name, default)).replace(",", ".").strip())
    except Exception:
        return float(default)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = frozenset(
    int(x)
    for x in re.split(r"[,\s;]+", os.getenv("ADMIN_IDS", "").strip())
    if x.strip().isdigit()
)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip().lstrip("@")
DB_FILE = os.getenv("DB_FILE", "tele_shop_vps.sqlite3")

# ── Bank (VietQR: sinh QR cho user tự chuyển khoản — KHÔNG cần API autopay) ──
BANK_ID = os.getenv("BANK_ID", "vietcombank").strip()
BANK_ACCOUNT_NO = os.getenv("BANK_ACCOUNT_NO", "").strip()
BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "").strip()
VIETQR_TEMPLATE = os.getenv("VIETQR_TEMPLATE", "compact2").strip()

# ── Crypto ──
USDT_BEP20_ADDRESS = os.getenv("USDT_BEP20_ADDRESS", "").strip()
TON_ADDRESS = os.getenv("TON_ADDRESS", "").strip()

# ── Nạp tiền / bán hàng ──
MIN_DEPOSIT_VND = _float("MIN_DEPOSIT_VND", 20000)
DEFAULT_USDT_RATE = _float("USDT_RATE_VND", 26000)   # 1 USDT = ? VND
DEFAULT_TON_RATE = _float("TON_RATE_VND", 80000)     # 1 TON (Gram) = ? VND
DEFAULT_PACK_PRICE = _float("PACK_PRICE_PER_ACC", 50000)  # giá mặc định / acc
PACK_SIZES = [
    int(x)
    for x in re.split(r"[,\s]+", os.getenv("PACK_SIZES", "10,20,50,100").strip())
    if x.strip().isdigit()
] or [10, 20, 50, 100]
AFF_PERCENT_DEFAULT = _float("AFF_PERCENT", 5)  # % hoa hồng tiếp thị

VIETQR_URL = "https://img.vietqr.io/image"
QR_API = "https://api.qrserver.com/v1/create-qr-code"

# ─────────────────────────────────────────────
# DB — SQLite WAL + 1 write-lock (chống "database is locked")
# ─────────────────────────────────────────────
_DB_LOCK = threading.RLock()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.isolation_level = None
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    with _DB_LOCK:
        con = db()
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                balance    REAL NOT NULL DEFAULT 0,
                aff_code   TEXT UNIQUE,
                aff_earned REAL NOT NULL DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sll_accs(
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                phone    TEXT UNIQUE NOT NULL,
                status   TEXT NOT NULL DEFAULT 'available',
                sold_to  INTEGER,
                sold_at  TEXT,
                order_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_sll_status ON sll_accs(status);
            CREATE TABLE IF NOT EXISTS orders(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                kind       TEXT NOT NULL,
                qty        INTEGER NOT NULL,
                price      REAL NOT NULL,
                status     TEXT NOT NULL DEFAULT 'PAID',
                phones     TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS deposits(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount      REAL NOT NULL,
                amount_unit REAL NOT NULL DEFAULT 0,
                method      TEXT NOT NULL,
                code        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'draft',
                created_at  TEXT,
                handled_by  INTEGER,
                handled_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS packages(
                size  INTEGER PRIMARY KEY,
                price REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        for size in PACK_SIZES:
            con.execute(
                "INSERT OR IGNORE INTO packages(size, price) VALUES(?,?)",
                (size, DEFAULT_PACK_PRICE * size),
            )
        try:
            con.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        except sqlite3.OperationalError:
            pass  # cột đã tồn tại
        con.close()


def get_setting(key: str, default=None):
    with _DB_LOCK:
        con = db()
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        con.close()
        return row["value"] if row else default


def set_setting(key: str, value) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.close()


def get_rate(method: str) -> float:
    v = get_setting(f"rate_{method}")
    if v:
        try:
            return float(v)
        except Exception:
            pass
    return DEFAULT_USDT_RATE if method == "usdt" else DEFAULT_TON_RATE


# ─────────────────────────────────────────────
# DATA ACCESS
# ─────────────────────────────────────────────
def ensure_user(user_id: int, username: str | None, full_name: str | None) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "INSERT INTO users(user_id, username, full_name, created_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, full_name=excluded.full_name",
            (user_id, username or "", full_name or "", now_str()),
        )
        con.close()


def get_balance(user_id: int) -> float:
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT balance FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        con.close()
        return float(row["balance"]) if row else 0.0


def add_balance(user_id: int, amount: float) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id)
        )
        con.close()


def get_aff_earned(user_id: int) -> float:
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT aff_earned FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        con.close()
        return float(row["aff_earned"]) if row else 0.0


def credit_aff(referrer_id: int, amount: float) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "UPDATE users SET aff_earned=aff_earned+? WHERE user_id=?",
            (amount, referrer_id),
        )
        con.close()


def get_packages() -> list:
    with _DB_LOCK:
        con = db()
        rows = con.execute("SELECT size, price FROM packages ORDER BY size").fetchall()
        con.close()
        return [(int(r["size"]), float(r["price"])) for r in rows]


def set_package(size: int, price: float) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "INSERT INTO packages(size, price) VALUES(?,?) "
            "ON CONFLICT(size) DO UPDATE SET price=excluded.price",
            (size, price),
        )
        con.close()


def stock_count() -> int:
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT COUNT(*) AS c FROM sll_accs WHERE status='available'"
        ).fetchone()
        con.close()
        return int(row["c"])


def parse_phones(text: str) -> list[str]:
    out, seen = [], set()
    for tok in re.split(r"[\s,;]+", (text or "").strip()):
        tok = tok.strip().lstrip("+")
        if re.fullmatch(r"\d{8,15}", tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def add_sll(phones: list[str]) -> tuple[int, int]:
    added = dup = 0
    with _DB_LOCK:
        con = db()
        for p in phones:
            try:
                con.execute("INSERT INTO sll_accs(phone) VALUES(?)", (p,))
                added += 1
            except sqlite3.IntegrityError:
                dup += 1
        con.close()
    return added, dup


def del_sll(phone: str) -> int:
    with _DB_LOCK:
        con = db()
        cur = con.execute(
            "DELETE FROM sll_accs WHERE phone=? AND status='available'", (phone,)
        )
        n = cur.rowcount
        con.close()
        return n


def create_draft_deposit(
    user_id: int, method: str, amount_vnd: float, amount_unit: float
) -> int:
    with _DB_LOCK:
        con = db()
        cur = con.execute(
            "INSERT INTO deposits(user_id, amount, amount_unit, method, code, status, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, amount_vnd, amount_unit, method, f"Napid {user_id}", "draft", now_str()),
        )
        did = int(cur.lastrowid)
        con.close()
    return did


def get_deposit(did: int):
    with _DB_LOCK:
        con = db()
        row = con.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
        con.close()
        return row


def set_deposit_status(did: int, status: str) -> None:
    with _DB_LOCK:
        con = db()
        con.execute("UPDATE deposits SET status=? WHERE id=?", (status, did))
        con.close()


def decide_deposit(did: int, admin_id: int, approve: bool):
    with _DB_LOCK:
        con = db()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM deposits WHERE id=? AND status='pending'", (did,)
            ).fetchone()
            if not row:
                con.execute("ROLLBACK")
                return None
            con.execute(
                "UPDATE deposits SET status=?, handled_by=?, handled_at=? WHERE id=?",
                ("approved" if approve else "rejected", admin_id, now_str(), did),
            )
            if approve:
                con.execute(
                    "UPDATE users SET balance=balance+? WHERE user_id=?",
                    (row["amount"], row["user_id"]),
                )
            con.execute("COMMIT")
            return row
        finally:
            con.close()


def buy_pack(user_id: int, size: int, price: float, aff_percent: float = 0.0):
    """Mua gói/acc lẻ: trừ tiền + lock N acc + hoa hồng tiếp thị trong 1 transaction.
    Trả (order_id, phones) hoặc (None, 'balance'|'stock')."""
    with _DB_LOCK:
        con = db()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT balance, referred_by FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            bal = float(row["balance"]) if row else 0.0
            referrer = int(row["referred_by"]) if row and row["referred_by"] else None
            if bal < price:
                con.execute("ROLLBACK")
                return None, "balance"
            phones = [
                r["phone"]
                for r in con.execute(
                    "SELECT phone FROM sll_accs WHERE status='available' "
                    "ORDER BY id LIMIT ?",
                    (size,),
                ).fetchall()
            ]
            if len(phones) < size:
                con.execute("ROLLBACK")
                return None, "stock"
            con.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=?",
                (price, user_id),
            )
            cur = con.execute(
                "INSERT INTO orders(user_id, kind, qty, price, status, phones, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, "PACK" if size > 1 else "SINGLE", size, price, "PAID",
                 "\n".join(phones), now_str()),
            )
            order_id = int(cur.lastrowid)
            marks = ",".join("?" * len(phones))
            con.execute(
                f"UPDATE sll_accs SET status='sold', sold_to=?, sold_at=?, order_id=? "
                f"WHERE phone IN ({marks})",
                [user_id, now_str(), order_id] + phones,
            )
            if referrer and aff_percent > 0:
                bonus = round(price * aff_percent / 100.0, 2)
                con.execute(
                    "UPDATE users SET balance=balance+?, aff_earned=aff_earned+? "
                    "WHERE user_id=?",
                    (bonus, bonus, referrer),
                )
            else:
                bonus = 0.0
            con.execute("COMMIT")
            return order_id, (phones, referrer, bonus)
        finally:
            con.close()


def recent_orders(user_id: int, limit: int = 10) -> list:
    with _DB_LOCK:
        con = db()
        rows = con.execute(
            "SELECT id, kind, qty, price, status, created_at FROM orders "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        con.close()
        return rows


# ─────────────────────────────────────────────
# NHÃN NÚT BÀN PHÍM (Reply Keyboard — giống bản gốc)
# ─────────────────────────────────────────────
BTN_CATS    = "📦 Chuyên mục"
BTN_TELE    = "📱 Mua Acc Telegram"
BTN_DEPOSIT = "💳 Nạp tiền"
BTN_HISTORY = "🧾 Lịch sử mua"
BTN_AFF     = "🤝 Tiếp thị"
BTN_ADMIN   = "🛠 Admin Panel"
BTN_BALANCE = "💰 Số dư"
BTN_BACK    = "⬅️ Menu chính"

# Mua acc
LBL_PACKS   = "🎟 Mua gói acc"
LBL_SINGLE  = "📱 Mua acc lẻ"
LBL_BUY_OK  = "✅ Xác nhận mua"
LBL_BUY_NO  = "❌ Huỷ mua"

# Nạp tiền
LBL_BANK    = "🏦 Ngân hàng (VietQR)"
LBL_USDT    = "💵 USDT (BEP20)"
LBL_TON     = "💎 Gram (TON)"
LBL_DEP_OK  = "✅ Tôi đã chuyển tiền"
LBL_DEP_NO  = "❌ Huỷ nạp"

# Admin panel
LBL_STOCK   = "📦 Kho acc"
LBL_PENDING = "⏳ Nạp chờ duyệt"
LBL_STATS   = "�� Thống kê"

METHOD_NAME = {
    "bank": "🏦 Ngân hàng (VietQR)",
    "usdt": "💵 USDT (BEP20)",
    "ton": "💎 Gram (TON)",
}


def h(text) -> str:
    return html.escape(str(text or ""), quote=False)


def vnd(v) -> str:
    return f"{int(round(float(v))):,}".replace(",", ".") + "đ"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


RP = dict(resize_keyboard=True, input_field_placeholder="Chọn chức năng...")


def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(BTN_TELE), KeyboardButton(BTN_DEPOSIT)],
        [KeyboardButton(BTN_CATS), KeyboardButton(BTN_BALANCE)],
        [KeyboardButton(BTN_HISTORY), KeyboardButton(BTN_AFF)],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(BTN_ADMIN)])
    return ReplyKeyboardMarkup(rows, **RP)


def buy_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(LBL_PACKS), KeyboardButton(LBL_SINGLE)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def packs_kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(f"🎟 Gói {size} acc — {vnd(price)}")]
        for size, price in get_packages()
    ]
    rows.append([KeyboardButton(BTN_BACK)])
    return ReplyKeyboardMarkup(rows, **RP)


def confirm_buy_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(LBL_BUY_OK), KeyboardButton(LBL_BUY_NO)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def dep_method_kb() -> ReplyKeyboardMarkup:
    rows = []
    if BANK_ACCOUNT_NO and BANK_ID:
        rows.append([KeyboardButton(LBL_BANK)])
    if USDT_BEP20_ADDRESS:
        rows.append([KeyboardButton(LBL_USDT)])
    if TON_ADDRESS:
        rows.append([KeyboardButton(LBL_TON)])
    rows.append([KeyboardButton(BTN_BACK)])
    return ReplyKeyboardMarkup(rows, **RP)


def dep_confirm_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(LBL_DEP_OK), KeyboardButton(LBL_DEP_NO)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def admin_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(LBL_STOCK), KeyboardButton(LBL_PENDING)],
         [KeyboardButton(LBL_STATS), KeyboardButton(BTN_BACK)]],
        **RP,
    )


def cats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_TELE), KeyboardButton(BTN_DEPOSIT)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("state", "dep_method", "dep_id", "buy_size", "buy_price"):
        context.user_data.pop(k, None)


ASK_AMOUNT = {
    "bank": "🏦 <b>Nạp qua ngân hàng</b>\n\n"
            "👉 Nhập <b>số tiền VND</b> muốn nạp (tối thiểu {minv}):\n"
            "Ví dụ: <code>100000</code>",
    "usdt": "💵 <b>Nạp USDT (BEP20)</b>\n\n"
            "⚖️ Tỉ giá: <b>1 USDT = {rate}</b>\n"
            "👉 Nhập <b>số USDT</b> muốn nạp, ví dụ: <code>10</code>",
    "ton": "💎 <b>Nạp Gram (TON)</b>\n\n"
           "⚖️ Tỉ giá: <b>1 TON = {rate}</b>\n"
           "👉 Nhập <b>số TON</b> muốn nạp, ví dụ: <code>5</code>",
}


# ─────────────────────────────────────────────
# CÁC LUỒNG CHỨC NĂNG (gọi từ on_message)
# ─────────────────────────────────────────────
async def flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    # deep-link tiếp thị: /start ref<id>
    is_new = True
    with _DB_LOCK:
        con = db()
        is_new = con.execute(
            "SELECT 1 FROM users WHERE user_id=?", (uid,)
        ).fetchone() is None
        con.close()
    ensure_user(uid, u.username, u.full_name)
    if is_new and context.args:
        m = re.fullmatch(r"ref(\d+)", (context.args[0] or "").strip())
        if m:
            ref_id = int(m.group(1))
            if ref_id != uid:
                with _DB_LOCK:
                    con = db()
                    con.execute(
                        "UPDATE users SET referred_by=? WHERE user_id=?",
                        (ref_id, uid),
                    )
                    con.close()
    clear_state(context)
    await update.effective_message.reply_html(
        f"👋 Xin chào <b>{h(u.full_name or u.first_name or 'bạn')}</b>!\n\n"
        "📱 Bot bán <b>Acc Telegram SLL</b> (mua gói hoặc acc lẻ).\n"
        "💳 Nạp: 🏦 Ngân hàng (VietQR) • 💵 USDT (BEP20) • 💎 Gram (TON)\n"
        "🔑 Sau khi mua acc, liên hệ <b>@" + ADMIN_USERNAME + "</b> để nhận mã OTP.\n\n"
        "👇 Chọn chức năng trên bàn phím bên dưới:",
        reply_markup=main_kb(uid),
        disable_web_page_preview=True,
    )


async def flow_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    packs = get_packages()
    min_price = min((p for _, p in packs), default=DEFAULT_PACK_PRICE)
    await update.effective_message.reply_html(
        "📦 <b>CHUYÊN MỤC SẢN PHẨM</b>\n\n"
        f"📱 <b>Acc Telegram SLL</b>\n"
        f"   • Kho hiện có: <b>{stock_count()}</b> acc\n"
        f"   • Acc lẻ: <b>{vnd(single_price())}</b>/acc\n"
        f"   • Gói: " + ", ".join(f"{s} acc ({vnd(p)})" for s, p in packs) + "\n\n"
        "🎁 Sắp ra mắt: Acc khác đang được cập nhật...\n\n"
        "👇 Chọn nút bên dưới để xem tiếp:",
        reply_markup=cats_kb(),
    )


def single_price() -> float:
    v = get_setting("price_single")
    try:
        return float(v) if v else DEFAULT_PACK_PRICE
    except Exception:
        return DEFAULT_PACK_PRICE


async def flow_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.effective_message.reply_html(
        f"💰 <b>Số dư của bạn:</b> {vnd(get_balance(uid))}\n\n"
        f"👉 Bấm <b>💳 Nạp tiền</b> để nạp thêm.",
    )


async def flow_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = recent_orders(update.effective_user.id)
    if not rows:
        txt = "🧾 <b>Lịch sử mua</b>\n\nBạn chưa có đơn hàng nào."
    else:
        lines = []
        for r in rows:
            kind = "gói" if r["kind"] == "PACK" else "lẻ"
            lines.append(
                f"• Đơn #{r['id']} — {r['qty']} acc ({kind}) — {vnd(r['price'])} "
                f"— {r['status']} — {r['created_at']}"
            )
        txt = "🧾 <b>Lịch sử mua (10 đơn gần nhất)</b>\n\n" + "\n".join(lines)
    await update.effective_message.reply_html(txt)


async def flow_aff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    percent = _float("AFF_PERCENT", AFF_PERCENT_DEFAULT)
    v = get_setting("aff_percent")
    if v:
        try:
            percent = float(v)
        except Exception:
            pass
    bot_username = (await context.bot.get_me()).username
    earned = get_aff_earned(uid)
    await update.effective_message.reply_html(
        "🤝 <b>TIẾP THỊ - KIẾM TIỀN</b>\n\n"
        f"💎 Hoa hồng: <b>{percent:g}%</b> trên mỗi đơn hàng của người bạn giới thiệu.\n"
        f"💰 Hoa hồng đã nhận: <b>{vnd(earned)}</b>\n\n"
        "🔗 <b>Link giới thiệu của bạn:</b>\n"
        f"<code>https://t.me/{bot_username}?start=ref{uid}</code>\n\n"
        "👉 Chia sẻ link cho bạn bè — họ bấm /start qua link thì bạn được hưởng hoa hồng "
        "vĩnh viễn trên mọi đơn hàng của họ!",
        disable_web_page_preview=True,
    )


async def flow_buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context)
    context.user_data["state"] = "buy_menu"
    await update.effective_message.reply_html(
        f"📱 <b>MUA ACC TELEGRAM</b>\n\n"
        f"📦 Kho hiện có: <b>{stock_count()}</b> acc\n"
        "🎟 Gói: " + ", ".join(str(s) for s, _ in get_packages()) + " acc\n"
        f"📱 Acc lẻ: {vnd(single_price())}/acc\n\n"
        "🔑 Mua xong liên hệ <b>@" + ADMIN_USERNAME + "</b> để nhận mã OTP.\n\n"
        "👇 Bạn muốn mua gói hay mua lẻ?",
        reply_markup=buy_kb(),
    )


async def flow_buy_packs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "buy_pack_select"
    await update.effective_message.reply_html(
        "🎟 <b>CHỌN GÓI ACC</b>\n\n👇 Chọn gói muốn mua trên bàn phím:",
        reply_markup=packs_kb(),
    )


async def flow_buy_pack_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, size: int):
    packs = dict(get_packages())
    price = packs.get(size)
    if price is None:
        await update.effective_message.reply_text("❌ Gói không tồn tại.")
        return
    uid = update.effective_user.id
    bal = get_balance(uid)
    context.user_data["state"] = "buy_confirm"
    context.user_data["buy_size"] = size
    context.user_data["buy_price"] = price
    ok = bal >= price and stock_count() >= size
    txt = (
        f"🧾 <b>ĐƠN MUA — GÓI {size} ACC</b>\n\n"
        f"💸 Giá: <b>{vnd(price)}</b>\n"
        f"💰 Số dư: {vnd(bal)}\n"
        f"📦 Kho: {stock_count()} acc\n\n"
    )
    if not ok:
        context.user_data["state"] = "buy_menu"
        if bal < price:
            txt += f"❌ Số dư không đủ — bấm <b>💳 Nạp tiền</b> để nạp."
            await update.effective_message.reply_html(
                txt, reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton(BTN_DEPOSIT)], [KeyboardButton(BTN_BACK)]], **RP
                )
            )
        else:
            txt += "❌ Kho không đủ acc — liên hệ @" + ADMIN_USERNAME + "."
            await update.effective_message.reply_html(txt, reply_markup=buy_kb())
        return
    txt += "✅ Xác nhận mua bên dưới nhé:"
    await update.effective_message.reply_html(txt, reply_markup=confirm_buy_kb())


async def flow_buy_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    price = single_price()
    bal = get_balance(uid)
    context.user_data["state"] = "buy_confirm"
    context.user_data["buy_size"] = 1
    context.user_data["buy_price"] = price
    txt = (
        f"🧾 <b>ĐƠN MUA — 1 ACC LẺ</b>\n\n"
        f"💸 Giá: <b>{vnd(price)}</b>/acc\n"
        f"💰 Số dư: {vnd(bal)}\n"
        f"📦 Kho: {stock_count()} acc\n\n"
    )
    if bal < price:
        context.user_data["state"] = "buy_menu"
        await update.effective_message.reply_html(
            txt + "❌ Số dư không đủ — bấm <b>💳 Nạp tiền</b> để nạp.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton(BTN_DEPOSIT)], [KeyboardButton(BTN_BACK)]], **RP
            ),
        )
        return
    if stock_count() < 1:
        context.user_data["state"] = "buy_menu"
        await update.effective_message.reply_html(
            txt + "❌ Kho hết acc — liên hệ @" + ADMIN_USERNAME + ".",
            reply_markup=buy_kb(),
        )
        return
    await update.effective_message.reply_html(
        txt + "✅ Xác nhận mua bên dưới nhé:", reply_markup=confirm_buy_kb()
    )


async def flow_buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    size = int(context.user_data.get("buy_size") or 0)
    price = float(context.user_data.get("buy_price") or 0)
    if not size or price <= 0:
        await update.effective_message.reply_text("❌ Không có đơn hàng nào. Chọn lại nhé!", reply_markup=buy_kb())
        context.user_data["state"] = "buy_menu"
        return
    await update.effective_chat.send_action("typing")
    percent = _float("AFF_PERCENT", AFF_PERCENT_DEFAULT)
    v = get_setting("aff_percent")
    if v:
        try:
            percent = float(v)
        except Exception:
            pass
    order_id, result = buy_pack(uid, size, price, percent)
    if order_id is None:
        context.user_data["state"] = "buy_menu"
        if result == "balance":
            await update.effective_message.reply_html(
                f"❌ Số dư không đủ.\n💰 Số dư: {vnd(get_balance(uid))}\n🧾 Cần: {vnd(price)}",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton(BTN_DEPOSIT)], [KeyboardButton(BTN_BACK)]], **RP
                ),
            )
        else:
            await update.effective_message.reply_html(
                "❌ Kho không đủ acc. Vui lòng liên hệ @" + ADMIN_USERNAME + ".",
                reply_markup=buy_kb(),
            )
        return
    phones, referrer, bonus = result
    clear_state(context)
    listing = "\n".join(f"{i}. <code>{h(p)}</code>" for i, p in enumerate(phones, 1))
    txt = (
        f"✅ <b>MUA THÀNH CÔNG</b>\n\n"
        f"🧾 Đơn: <b>#{order_id}</b> — {size} acc\n"
        f"💸 Đã trừ: {vnd(price)}\n"
        f"💰 Số dư còn lại: {vnd(get_balance(uid))}\n\n"
        f"📞 <b>DANH SÁCH ACC:</b>\n{listing}\n\n"
        f"🔑 <b>Quan trọng:</b> Khi đăng nhập cần mã OTP —\n"
        f"👉 Liên hệ <b>@{ADMIN_USERNAME}</b> để nhận OTP (kèm mã đơn #{order_id})."
    )
    await update.effective_message.reply_html(
        txt, reply_markup=main_kb(uid), disable_web_page_preview=True
    )
    # Thông báo hoa hồng cho người giới thiệu
    if referrer and bonus > 0:
        try:
            await context.bot.send_message(
                referrer,
                f"🤝 <b>Hoa hồng tiếp thị +{vnd(bonus)}</b>\n"
                f"👤 Từ đơn #{order_id} của user {uid}\n"
                f"💰 Số dư mới: {vnd(get_balance(referrer))}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass


# ─────────────────────────────────────────────
# LUỒNG NẠP TIỀN (Bank / USDT / TON — nội dung: Napid <idtelegram>)
# ─────────────────────────────────────────────
async def flow_deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "dep_method"
    uid = update.effective_user.id
    await update.effective_message.reply_html(
        f"💳 <b>NẠP TIỀN</b>\n\n"
        f"💰 Số dư: {vnd(get_balance(uid))}\n"
        f"🔽 Số tiền tối thiểu: {vnd(MIN_DEPOSIT_VND)}\n\n"
        "👇 Chọn kênh nạp trên bàn phím:",
        reply_markup=dep_method_kb(),
    )


async def flow_deposit_method(update: Update, context: ContextTypes.DEFAULT_TYPE, method: str):
    context.user_data["state"] = "dep_amount"
    context.user_data["dep_method"] = method
    await update.effective_message.reply_html(
        ASK_AMOUNT[method].format(minv=vnd(MIN_DEPOSIT_VND), rate=vnd(get_rate(method)))
    )


def _parse_amount(method: str, raw: str) -> float | None:
    raw = (raw or "").strip()
    if method == "bank":
        raw = raw.replace(".", "").replace(",", "")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


async def flow_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str):
    method = context.user_data.get("dep_method") or "bank"
    amount = _parse_amount(method, raw)
    if amount is None or amount <= 0:
        await update.effective_message.reply_html(
            "❌ Số tiền không hợp lệ. Nhập lại nhé (ví dụ: <code>100000</code>)"
        )
        return
    rate = get_rate(method)
    if method == "bank":
        amount_vnd, amount_unit = amount, 0.0
        if amount_vnd < MIN_DEPOSIT_VND:
            await update.effective_message.reply_html(f"❌ Tối thiểu {vnd(MIN_DEPOSIT_VND)} nhé!")
            return
    else:
        amount_unit = amount
        amount_vnd = amount * rate
        if amount_vnd < MIN_DEPOSIT_VND:
            await update.effective_message.reply_html(
                f"❌ Tối thiểu tương đương {vnd(MIN_DEPOSIT_VND)} "
                f"(≈ {round(MIN_DEPOSIT_VND / rate, 2)} {method.upper()})."
            )
            return

    uid = update.effective_user.id
    did = create_draft_deposit(uid, method, amount_vnd, amount_unit)
    context.user_data["state"] = "dep_wait"
    context.user_data["dep_id"] = did
    note = f"Napid {uid}"

    kb = dep_confirm_kb()
    if method == "bank":
        qr = (
            f"{VIETQR_URL}/{BANK_ID}-{BANK_ACCOUNT_NO}-{VIETQR_TEMPLATE}.png?"
            + urllib.parse.urlencode(
                {
                    "amount": str(int(amount_vnd)),
                    "addInfo": note,
                    "accountName": BANK_ACCOUNT_NAME,
                }
            )
        )
        caption = (
            f"🧾 <b>YÊU CẦU NẠP TIỀN #{did}</b>\n\n"
            f"🏦 Ngân hàng: <b>{h(BANK_ID)}</b>\n"
            f"💳 STK: <code>{h(BANK_ACCOUNT_NO)}</code>\n"
            f"👤 Chủ TK: {h(BANK_ACCOUNT_NAME)}\n"
            f"💸 Số tiền: <b>{vnd(amount_vnd)}</b>\n"
            f"📝 Nội dung CK: <b>{note}</b> (BẮT BUỘC)\n\n"
            "⚠️ Chuyển xong bấm <b>✅ Tôi đã chuyển tiền</b> để admin duyệt."
        )
        await update.effective_message.reply_photo(
            qr, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
        )
    else:
        addr = USDT_BEP20_ADDRESS if method == "usdt" else TON_ADDRESS
        qr = QR_API + "?size=320x320&data=" + urllib.parse.quote(addr)
        net = "BEP20 (BNB Smart Chain)" if method == "usdt" else "TON (The Open Network)"
        unit = "USDT" if method == "usdt" else "TON (Gram)"
        caption = (
            f"🧾 <b>YÊU CẦU NẠP TIỀN #{did}</b>\n\n"
            f"💵 Mạng: <b>{net}</b>\n"
            f"📍 Địa chỉ ví: <code>{h(addr)}</code>\n"
            f"💸 Số lượng: <b>{amount_unit:g} {unit}</b> (≈ {vnd(amount_vnd)})\n"
            f"📝 Mã đối chiếu: <b>{note}</b>\n\n"
            f"⚠️ CHỈ gửi đúng mạng <b>{net}</b> — sai mạng mất tiền!\n"
            "Chuyển xong bấm <b>✅ Tôi đã chuyển tiền</b> để admin duyệt."
        )
        await update.effective_message.reply_photo(
            qr, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
        )


async def flow_deposit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    did = int(context.user_data.get("dep_id") or 0)
    dep = get_deposit(did) if did else None
    if not dep or dep["user_id"] != uid or dep["status"] != "draft":
        await update.effective_message.reply_text(
            "❌ Không có yêu cầu nạp nào đang chờ. Tạo lại bằng �� Nạp tiền.",
            reply_markup=main_kb(uid),
        )
        clear_state(context)
        return
    set_deposit_status(did, "pending")
    clear_state(context)
    await notify_admin_deposit(context, did)
    await update.effective_message.reply_html(
        f"✅ <b>Đã ghi nhận yêu cầu nạp #{did}!</b>\n\n"
        "⏳ Bot đã gửi thông báo cho admin — vui lòng chờ duyệt.\n"
        "Bạn sẽ nhận thông báo khi yêu cầu được xử lý.",
        reply_markup=main_kb(uid),
    )


async def flow_deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    did = int(context.user_data.get("dep_id") or 0)
    if did:
        dep = get_deposit(did)
        if dep and dep["status"] == "draft":
            set_deposit_status(did, "cancelled")
    clear_state(context)
    await update.effective_message.reply_html(
        "❌ Đã huỷ yêu cầu nạp tiền.", reply_markup=main_kb(update.effective_user.id)
    )


async def notify_admin_deposit(context: ContextTypes.DEFAULT_TYPE, did: int):
    dep = get_deposit(did)
    if not dep:
        return
    unit = ""
    if dep["method"] != "bank" and dep["amount_unit"]:
        unit = f" ({dep['amount_unit']:g} {dep['method'].upper()})"
    txt = (
        f"🔔 <b>YÊU CẦU NẠP TIỀN #{dep['id']}</b>\n\n"
        f"👤 User: {dep['user_id']} (@{h(get_username(dep['user_id']))})\n"
        f"�� Kênh: {METHOD_NAME.get(dep['method'], dep['method'])}\n"
        f"💸 Số tiền: <b>{vnd(dep['amount'])}</b>{unit}\n"
        f"📝 Nội dung: <code>{h(dep['code'])}</code>\n"
        f"🕐 {dep['created_at']}\n\n"
        f"👉 Kiểm tra giao dịch rồi dùng:\n"
        f"✅ <code>/duyet {dep['id']}</code>\n"
        f"❌ <code>/tuchoi {dep['id']}</code>"
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, txt, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("Không gửi được cho admin %s: %s", aid, e)


def get_username(user_id: int) -> str:
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT username FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        con.close()
        return (row["username"] if row and row["username"] else "") or ""


# ─────────────────────────────────────────────
# ADMIN PANEL (bàn phím)
# ─────────────────────────────────────────────
async def flow_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ Chỉ dành cho admin.")
        return
    await update.effective_message.reply_html(
        "🛠 <b>ADMIN PANEL</b>\n\n"
        f"📦 Kho acc: {stock_count()}\n\n"
        "📋 <b>Lệnh nhanh:</b>\n"
        "• <code>/addsll</code> — thêm SĐT (xuống dòng/cách/phẩy)\n"
        "• <code>/delsll &lt;sdt&gt;</code> — xoá acc\n"
        "• <code>/duyet &lt;id&gt;</code> / <code>/tuchoi &lt;id&gt;</code> — duyệt nạp\n"
        "• <code>/setpack &lt;size&gt; &lt;giá&gt;</code> — giá gói\n"
        "• <code>/setprice &lt;giá&gt;</code> — giá acc lẻ\n"
        "• <code>/setrate &lt;usdt|ton&gt; &lt;vnd&gt;</code> — tỉ giá\n"
        "• <code>/setaff &lt;%&gt;</code> — % hoa hồng tiếp thị\n"
        "• <code>/addbal &lt;uid&gt; &lt;tiền&gt;</code> — cộng tiền user",
        reply_markup=admin_kb(),
    )


async def flow_admin_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(
        f"📦 <b>Kho acc khả dụng:</b> {stock_count()}", reply_markup=admin_kb()
    )


async def flow_admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _DB_LOCK:
        con = db()
        rows = con.execute(
            "SELECT * FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 10"
        ).fetchall()
        con.close()
    if not rows:
        txt = "⏳ Không có yêu cầu nạp nào đang chờ duyệt."
    else:
        txt = "⏳ <b>Nạp chờ duyệt:</b>\n\n" + "\n".join(
            f"#{r['id']} — user {r['user_id']} — {vnd(r['amount'])} — "
            f"{r['method']} — {r['code']}"
            for r in rows
        ) + "\n\nDùng: /duyet &lt;id&gt; hoặc /tuchoi &lt;id&gt;"
    await update.effective_message.reply_html(txt, reply_markup=admin_kb())


async def flow_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _DB_LOCK:
        con = db()
        n_users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        o = con.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(price),0) s FROM orders WHERE status='PAID'"
        ).fetchone()
        con.close()
    await update.effective_message.reply_html(
        f"📊 <b>THỐNG KÊ</b>\n"
        f"👥 Users: {n_users}\n"
        f"📦 Kho acc: {stock_count()}\n"
        f"🧾 Đơn đã bán: {o['c']} ({vnd(o['s'])})",
        reply_markup=admin_kb(),
    )


# ─────────────────────────────────────────────
# ADMIN COMMANDS (slash)
# ─────────────────────────────────────────────
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.effective_message.reply_text("⛔ Chỉ dành cho admin.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def cmd_addsll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thêm SĐT: xuống dòng / cách / phẩy đều được; hoặc reply tin nhắn chứa danh sách."""
    msg = update.effective_message
    text = " ".join(context.args) if context.args else ""
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or ""
    phones = parse_phones(text)
    if not phones:
        await msg.reply_html(
            "📝 <b>Cách dùng /addsll:</b>\n"
            "<code>/addsll 0912345678 0987654321</code>\n"
            "hoặc mỗi SĐT 1 dòng:\n<code>/addsll 0912345678\n0987654321</code>\n"
            "hoặc reply tin nhắn chứa danh sách SĐT bằng /addsll"
        )
        return
    added, dup = add_sll(phones)
    await msg.reply_html(
        f"✅ <b>Đã thêm {added} acc SLL</b>" + (f" (bỏ qua {dup} trùng)" if dup else "")
        + f"\n📦 Kho hiện tại: <b>{stock_count()}</b> acc"
    )


@admin_only
async def cmd_delsll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Dùng: /delsll <sdt>")
        return
    n = del_sll(context.args[0].lstrip("+"))
    await update.effective_message.reply_text(
        f"🗑 Đã xoá {n} acc." if n else "❌ Không tìm thấy acc khả dụng với SĐT đó."
    )


@admin_only
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(f"📦 Kho acc khả dụng: <b>{stock_count()}</b>")


@admin_only
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await flow_admin_pending(update, context)


@admin_only
async def cmd_duyet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Dùng: /duyet <id_yeu_cau_nap>")
        return
    row = decide_deposit(int(context.args[0]), update.effective_user.id, True)
    if not row:
        await update.effective_message.reply_text("⚠️ Không tìm thấy yêu cầu pending này.")
        return
    await update.effective_message.reply_html(
        f"✅ Đã duyệt nạp #{row['id']}: +{vnd(row['amount'])} cho user {row['user_id']}"
    )
    try:
        await context.bot.send_message(
            row["user_id"],
            f"🎉 <b>Nạp tiền #{row['id']} đã được DUYỆT!</b>\n"
            f"💰 Số dư mới: {vnd(get_balance(row['user_id']))}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


@admin_only
async def cmd_tuchoi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Dùng: /tuchoi <id_yeu_cau_nap>")
        return
    row = decide_deposit(int(context.args[0]), update.effective_user.id, False)
    if not row:
        await update.effective_message.reply_text("⚠️ Không tìm thấy yêu cầu pending này.")
        return
    await update.effective_message.reply_html(f"❌ Đã từ chối nạp #{row['id']}")
    try:
        await context.bot.send_message(
            row["user_id"],
            f"❌ <b>Yêu cầu nạp #{row['id']} bị TỪ CHỐI.</b>\n"
            f"Nếu đã chuyển tiền, liên hệ @{ADMIN_USERNAME} để được hỗ trợ.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


@admin_only
async def cmd_setpack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Dùng: /setpack <size> <gia_vnd> (vd: /setpack 10 400000)")
        return
    price = _parse_amount("bank", context.args[1])
    if price is None or price <= 0:
        await update.effective_message.reply_text("❌ Giá không hợp lệ.")
        return
    set_package(int(context.args[0]), price)
    await update.effective_message.reply_html(
        f"✅ Đã đặt gói <b>{context.args[0]} acc = {vnd(price)}</b>"
    )


@admin_only
async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Dùng: /setprice <gia_1_acc>")
        return
    price = _parse_amount("bank", context.args[0])
    if price is None or price <= 0:
        await update.effective_message.reply_text("❌ Giá không hợp lệ.")
        return
    set_setting("price_single", price)
    await update.effective_message.reply_html(f"✅ Giá acc lẻ: <b>{vnd(price)}</b>/acc")


@admin_only
async def cmd_setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2 or context.args[0] not in ("usdt", "ton"):
        await update.effective_message.reply_text("Dùng: /setrate <usdt|ton> <vnd> (vd: /setrate usdt 26000)")
        return
    price = _parse_amount("bank", context.args[1])
    if price is None or price <= 0:
        await update.effective_message.reply_text("❌ Tỉ giá không hợp lệ.")
        return
    set_setting(f"rate_{context.args[0]}", price)
    await update.effective_message.reply_html(f"✅ 1 {context.args[0].upper()} = <b>{vnd(price)}</b>")


@admin_only
async def cmd_setaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Dùng: /setaff <phan_tram> (vd: /setaff 5)")
        return
    try:
        percent = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.effective_message.reply_text("❌ Không hợp lệ.")
        return
    if not 0 <= percent <= 50:
        await update.effective_message.reply_text("❌ % hoa hồng trong khoảng 0–50.")
        return
    set_setting("aff_percent", percent)
    await update.effective_message.reply_html(f"✅ Hoa hồng tiếp thị: <b>{percent:g}%</b>")


@admin_only
async def cmd_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2 or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text("Dùng: /addbal <user_id> <so_tien>")
        return
    uid = int(context.args[0])
    amount = _parse_amount("bank", context.args[1])
    if amount is None:
        await update.effective_message.reply_text("❌ Số tiền không hợp lệ.")
        return
    ensure_user(uid, "", "")
    add_balance(uid, amount)
    await update.effective_message.reply_html(
        f"✅ Đã {'+' if amount >= 0 else ''}{vnd(amount)} cho user {uid}. Số dư: {vnd(get_balance(uid))}"
    )


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await flow_admin_stats(update, context)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(
        f"🆔 ID của bạn: <code>{update.effective_user.id}</code>\n"
        f"📝 Nội dung nạp tiền: <code>Napid {update.effective_user.id}</code>"
    )


# ─────────────────────────────────────────────
# ON_MESSAGE — điều hướng bằng nút bàn phím (state machine)
# ─────────────────────────────────────────────
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    text = (msg.text or "").strip()
    ensure_user(uid, update.effective_user.username, update.effective_user.full_name)

    # ── 1) Nút menu chính — luôn ưu tiên (để user luôn thoát được luồng) ──
    if text == BTN_BACK:
        clear_state(context)
        await msg.reply_html("🏠 <b>Menu chính</b>", reply_markup=main_kb(uid))
        return
    if text == BTN_TELE:
        await flow_buy_menu(update, context)
        return
    if text == BTN_DEPOSIT:
        await flow_deposit_menu(update, context)
        return
    if text == BTN_CATS:
        await flow_cats(update, context)
        return
    if text == BTN_BALANCE:
        await flow_balance(update, context)
        return
    if text == BTN_HISTORY:
        await flow_history(update, context)
        return
    if text == BTN_AFF:
        await flow_aff(update, context)
        return
    if text == BTN_ADMIN:
        await flow_admin_panel(update, context)
        return

    state = context.user_data.get("state")

    # ── 2) Luồng MUA ACC ──
    if state == "buy_menu":
        if text == LBL_PACKS:
            await flow_buy_packs(update, context)
            return
        if text == LBL_SINGLE:
            await flow_buy_single(update, context)
            return
        await msg.reply_html("👇 Chọn <b>🎟 Mua gói acc</b> hoặc <b>📱 Mua acc lẻ</b>:")
        return

    if state == "buy_pack_select":
        m = re.fullmatch(r"🎟 Gói (\d+) acc — ([\d.,]+)đ", text)
        if m:
            await flow_buy_pack_detail(update, context, int(m.group(1)))
            return
        await msg.reply_html("👇 Chọn gói trên bàn phím nhé:")
        return

    if state == "buy_confirm":
        if text == LBL_BUY_OK:
            await flow_buy_confirm(update, context)
            return
        if text == LBL_BUY_NO:
            clear_state(context)
            await msg.reply_html("❌ Đã huỷ đơn.", reply_markup=buy_kb())
            context.user_data["state"] = "buy_menu"
            return
        await msg.reply_html("👉 Bấm <b>✅ Xác nhận mua</b> hoặc <b>❌ Huỷ mua</b>.")
        return

    # ── 3) Luồng NẠP TIỀN ──
    if state == "dep_method":
        if text == LBL_BANK and BANK_ACCOUNT_NO:
            await flow_deposit_method(update, context, "bank")
            return
        if text == LBL_USDT and USDT_BEP20_ADDRESS:
            await flow_deposit_method(update, context, "usdt")
            return
        if text == LBL_TON and TON_ADDRESS:
            await flow_deposit_method(update, context, "ton")
            return
        await msg.reply_html("👇 Chọn kênh nạp trên bàn phím nhé:")
        return

    if state == "dep_amount":
        await flow_deposit_amount(update, context, text)
        return

    if state == "dep_wait":
        if text == LBL_DEP_OK:
            await flow_deposit_confirm(update, context)
            return
        if text == LBL_DEP_NO:
            await flow_deposit_cancel(update, context)
            return
        await msg.reply_html(
            "👉 Bấm <b>✅ Tôi đã chuyển tiền</b> sau khi chuyển khoản, "
            "hoặc <b>❌ Huỷ nạp</b> để huỷ."
        )
        return

    # ── 4) Admin panel phím ──
    if is_admin(uid):
        if text == LBL_STOCK:
            await flow_admin_stock(update, context)
            return
        if text == LBL_PENDING:
            await flow_admin_pending(update, context)
            return
        if text == LBL_STATS:
            await flow_admin_stats(update, context)
            return

    # ── 5) Fallback ──
    await msg.reply_html(
        "🤖 Mình không hiểu tin nhắn này.\n"
        "👇 Dùng các nút trên bàn phím bên dưới nhé!",
        reply_markup=main_kb(uid),
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Lỗi khi xử lý update: %s", context.error, exc_info=context.error)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Thiếu BOT_TOKEN trong file .env")
    if not ADMIN_IDS:
        raise SystemExit("❌ Thiếu ADMIN_IDS trong file .env")

    init_db()
    req = HTTPXRequest(connection_pool_size=256)
    get_req = HTTPXRequest(connection_pool_size=256)
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(get_req)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler(["start", "menu"], flow_start), group=0)
    app.add_handler(CommandHandler("id", cmd_id), group=0)
    app.add_handler(CommandHandler("addsll", cmd_addsll), group=0)
    app.add_handler(CommandHandler("delsll", cmd_delsll), group=0)
    app.add_handler(CommandHandler("stock", cmd_stock), group=0)
    app.add_handler(CommandHandler("pending", cmd_pending), group=0)
    app.add_handler(CommandHandler("duyet", cmd_duyet), group=0)
    app.add_handler(CommandHandler("tuchoi", cmd_tuchoi), group=0)
    app.add_handler(CommandHandler("setpack", cmd_setpack), group=0)
    app.add_handler(CommandHandler("setprice", cmd_setprice), group=0)
    app.add_handler(CommandHandler("setrate", cmd_setrate), group=0)
    app.add_handler(CommandHandler("setaff", cmd_setaff), group=0)
    app.add_handler(CommandHandler("addbal", cmd_addbal), group=0)
    app.add_handler(CommandHandler("stats", cmd_stats), group=0)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message), group=1)
    app.add_error_handler(on_error)

    log.info("🚀 Bot đang chạy (Reply Keyboard mode)... Admin: %s", sorted(ADMIN_IDS))
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
        poll_interval=0.5,
    )


if __name__ == "__main__":
    main()
