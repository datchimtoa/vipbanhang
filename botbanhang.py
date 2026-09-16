import os
import re
import json
import time
import html
import queue
import sqlite3
import threading
import urllib.parse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import requests
import customtkinter as ctk
from tkinter import filedialog, messagebox

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── Label nút bàn phím (dùng để detect trong on_message) ──
BTN_CATS    = "📦 Chuyên mục"
BTN_TELE    = "📱 Mua Acc Telegram"
BTN_DEPOSIT = "💳 Nạp tiền"
BTN_HISTORY = "🧾 Lịch sử mua"
BTN_AFF     = "🤝 Tiếp thị"
BTN_ADMIN   = "🛠 Admin Panel"
BTN_BALANCE = "💰 Số dư"

REPLY_KB_LABELS = {BTN_CATS, BTN_TELE, BTN_DEPOSIT, BTN_HISTORY, BTN_AFF, BTN_ADMIN, BTN_BALANCE}

# =========================================================
# CONFIG / CONSTANTS
# =========================================================
APP_TITLE = "TELE SHOP BOT - VietQR + AutoBank V3 (PC 24/7)"
DB_FILE = "tele_shop.sqlite3"
CONFIG_FILE = "tele_shop_config.json"

DEFAULT_VIETQR_TEMPLATE = "compact2"

VIETQR_BANKS = [
    ("Vietcombank", "vietcombank"),
    ("Techcombank", "techcombank"),
    ("MB Bank", "mbbank"),
    ("ACB", "acb"),
    ("VietinBank", "vietinbank"),
    ("BIDV", "bidv"),
    ("Sacombank", "sacombank"),
    ("TPBank", "tpbank"),
]


NAPID_RE = re.compile(r"\bNapid\s+(\d+)\b", re.IGNORECASE)
ADMIN_MODE: set = set()

# Buy cache TTL (giây) — tránh bộ nhớ rò rỉ
BUY_CACHE_TTL = 600

# =========================================================
# HELPERS
# =========================================================
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_int(x, default=0) -> int:
    try:
        return int(str(x).replace(",", "").strip())
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default


def vietqr_image_url(bank_id, account_no, template, amount, add_info, account_name) -> str:
    base = f"https://img.vietqr.io/image/{bank_id}-{account_no}-{template}.png"
    qs = {
        "amount": str(max(0, int(amount))),
        "addInfo": add_info,
        "accountName": account_name or "",
    }
    return base + "?" + urllib.parse.urlencode(qs, safe="")




def h(text: str) -> str:
    """Escape HTML an toàn cho deliver_text và các user-supplied content."""
    return html.escape(str(text or ""), quote=False)


# =========================================================
# DB — dùng 1 write-lock để tránh "database is locked"
# =========================================================
_DB_LOCK = threading.Lock()


def db_connect():
    con = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db():
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            ref_by INTEGER,
            created_at TEXT
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT 0,
            deliver_text TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            price INTEGER,
            discount INTEGER DEFAULT 0,
            final_price INTEGER,
            coupon_code TEXT,
            status TEXT,
            created_at TEXT
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            description TEXT,
            trans_id TEXT,
            status TEXT,
            created_at TEXT
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS coupons(
            code TEXT PRIMARY KEY,
            discount_amount INTEGER DEFAULT 0,
            discount_percent INTEGER DEFAULT 0,
            apply_product_id INTEGER DEFAULT NULL,
            min_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            max_uses INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS affiliate(
            user_id INTEGER PRIMARY KEY,
            percent INTEGER DEFAULT 5
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_earnings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_user_id INTEGER,
            order_id INTEGER,
            amount INTEGER,
            created_at TEXT
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_seen(
            transaction_id TEXT PRIMARY KEY,
            seen_at TEXT
        )""")

        # ---- BẢNG MỚI: Acc Telegram ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tele_accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            password TEXT DEFAULT '',
            twofa TEXT DEFAULT '',
            note TEXT DEFAULT '',
            price_vnd INTEGER DEFAULT 0,
            status TEXT DEFAULT 'available',
            sold_to INTEGER DEFAULT NULL,
            sold_order_id INTEGER DEFAULT NULL,
            created_at TEXT
        )""")

        # ---- BẢNG MỚI: Settings động ----
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        con.commit()
        con.close()


# =========================================================
# SETTINGS HELPERS
# =========================================================
def get_setting(key: str, default: str = "") -> str:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = cur.fetchone()
        con.close()
    return str(r["value"]) if r else default


def set_setting(key: str, value: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        con.commit()
        con.close()


def get_usd_rate() -> int:
    """Tỉ giá USD→VND hiện tại (lưu trong settings)."""
    return safe_int(get_setting("usd_rate", "25000"), 25000)


# =========================================================
# USER HELPERS
# =========================================================
def upsert_user(tg_user):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (tg_user.id,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users(user_id, username, first_name, balance, ref_by, created_at) VALUES(?,?,?,?,?,?)",
                (tg_user.id, tg_user.username or "", tg_user.first_name or "", 0, None, now_str()),
            )
        else:
            cur.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (tg_user.username or "", tg_user.first_name or "", tg_user.id),
            )
        con.commit()
        con.close()


def set_ref_if_empty(user_id: int, ref_by: int):
    if user_id == ref_by:
        return
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        if r and r["ref_by"] is None:
            cur.execute("UPDATE users SET ref_by=? WHERE user_id=?", (ref_by, user_id))
        con.commit()
        con.close()


def get_user(user_id: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        con.close()
    return r


def get_balance(user_id: int) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        con.close()
    return int(r["balance"] or 0) if r else 0


def add_balance(user_id: int, amount: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, balance, ref_by, created_at) VALUES(?, '', '', 0, NULL, ?)",
            (int(user_id), now_str()),
        )
        cur.execute("UPDATE users SET balance = COALESCE(balance,0) + ? WHERE user_id=?", (int(amount), int(user_id)))
        con.commit()
        con.close()


def subtract_balance(user_id: int, amount: int) -> bool:
    """Trả True nếu trừ thành công. Dùng lock để atomic."""
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        if not r or int(r["balance"] or 0) < amount:
            con.close()
            return False
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (int(amount), user_id))
        con.commit()
        con.close()
    return True


# =========================================================
# CATEGORIES / PRODUCTS
# =========================================================
def add_category(name: str) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("INSERT INTO categories(name, is_active) VALUES(?,1)", (name,))
        con.commit()
        cid = cur.lastrowid
        con.close()
    return cid


def edit_category(cid: int, name: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
        con.commit()
        con.close()


def delete_category(cid: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE categories SET is_active=0 WHERE id=?", (cid,))
        con.commit()
        con.close()


def list_categories():
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT id, name FROM categories WHERE is_active=1 ORDER BY id DESC")
        rows = cur.fetchall()
        con.close()
    return rows


def add_product(category_id, name, price, stock, deliver_text) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO products(category_id, name, price, stock, deliver_text, is_active) VALUES(?,?,?,?,?,1)",
            (category_id, name, int(price), int(stock), deliver_text or ""),
        )
        con.commit()
        pid = cur.lastrowid
        con.close()
    return pid


def edit_product(pid, name, price, stock, deliver_text):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "UPDATE products SET name=?, price=?, stock=?, deliver_text=? WHERE id=?",
            (name, int(price), int(stock), deliver_text or "", pid),
        )
        con.commit()
        con.close()


def delete_product(pid: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
        con.commit()
        con.close()


def list_products_by_category(cid: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT id, name, price, stock FROM products WHERE is_active=1 AND category_id=? ORDER BY id DESC", (cid,))
        rows = cur.fetchall()
        con.close()
    return rows


def get_product(pid: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT * FROM products WHERE id=? AND is_active=1", (pid,))
        r = cur.fetchone()
        con.close()
    return r


def atomic_buy_product(pid: int, user_id: int, final_price: int) -> bool:
    """Atomic: trừ tiền + giảm stock trong 1 transaction. Trả False nếu thất bại."""
    with _DB_LOCK:
        con = db_connect()
        try:
            cur = con.cursor()
            # Check balance
            cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
            u = cur.fetchone()
            if not u or int(u["balance"] or 0) < final_price:
                return False
            # Check stock
            cur.execute("SELECT stock FROM products WHERE id=? AND is_active=1", (pid,))
            p = cur.fetchone()
            if not p or int(p["stock"] or 0) <= 0:
                return False
            # Execute
            cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (final_price, user_id))
            cur.execute("UPDATE products SET stock = stock - 1 WHERE id=?", (pid,))
            con.commit()
            return True
        except Exception:
            con.rollback()
            return False
        finally:
            con.close()


# =========================================================
# TELE ACCOUNTS
# =========================================================
def add_tele_acc(phone: str, password: str, twofa: str, note: str, price_vnd: int) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO tele_accounts(phone, password, twofa, note, price_vnd, status, created_at) VALUES(?,?,?,?,?,'available',?)",
            (phone.strip(), password.strip(), twofa.strip(), note.strip(), int(price_vnd), now_str()),
        )
        con.commit()
        aid = cur.lastrowid
        con.close()
    return aid


def delete_tele_acc(acc_id: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("DELETE FROM tele_accounts WHERE id=?", (acc_id,))
        con.commit()
        con.close()


def list_tele_accs(status: str = "available"):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "SELECT id, phone, note, price_vnd, status FROM tele_accounts WHERE status=? ORDER BY id DESC",
            (status,),
        )
        rows = cur.fetchall()
        con.close()
    return rows


def get_tele_acc(acc_id: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT * FROM tele_accounts WHERE id=?", (acc_id,))
        r = cur.fetchone()
        con.close()
    return r


def count_available_tele_accs() -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM tele_accounts WHERE status='available'")
        r = cur.fetchone()
        con.close()
    return int(r["c"] or 0) if r else 0


def atomic_buy_tele_acc(acc_id: int, user_id: int, price_vnd: int, order_id: int) -> bool:
    """Atomic: trừ tiền + mark acc sold trong 1 transaction."""
    with _DB_LOCK:
        con = db_connect()
        try:
            cur = con.cursor()
            # Check balance
            cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
            u = cur.fetchone()
            if not u or int(u["balance"] or 0) < price_vnd:
                return False
            # Check acc vẫn available
            cur.execute("SELECT id FROM tele_accounts WHERE id=? AND status='available'", (acc_id,))
            a = cur.fetchone()
            if not a:
                return False
            # Execute
            cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price_vnd, user_id))
            cur.execute(
                "UPDATE tele_accounts SET status='sold', sold_to=?, sold_order_id=? WHERE id=?",
                (user_id, order_id, acc_id),
            )
            con.commit()
            return True
        except Exception:
            con.rollback()
            return False
        finally:
            con.close()


# =========================================================
# COUPONS
# =========================================================
def upsert_coupon(code, discount_amount, discount_percent, apply_product_id, min_order, max_uses):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO coupons(code, discount_amount, discount_percent, apply_product_id, min_order, is_active, max_uses, used_count)
            VALUES(?,?,?,?,?,1,?,0)
            ON CONFLICT(code) DO UPDATE SET
                discount_amount=excluded.discount_amount,
                discount_percent=excluded.discount_percent,
                apply_product_id=excluded.apply_product_id,
                min_order=excluded.min_order,
                max_uses=excluded.max_uses,
                is_active=1
        """, (code.upper(), int(discount_amount), int(discount_percent), apply_product_id, int(min_order), int(max_uses)))
        con.commit()
        con.close()


def disable_coupon(code: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE coupons SET is_active=0 WHERE code=?", (code.upper(),))
        con.commit()
        con.close()


def get_coupon(code: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT * FROM coupons WHERE code=? AND is_active=1", (code.upper(),))
        r = cur.fetchone()
        con.close()
    return r


def mark_coupon_used(code: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code=?", (code.upper(),))
        con.commit()
        con.close()


# =========================================================
# AFFILIATE
# =========================================================
def get_aff_percent(user_id: int) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT percent FROM affiliate WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        if not r:
            cur.execute("INSERT OR IGNORE INTO affiliate(user_id, percent) VALUES(?,5)", (user_id,))
            con.commit()
            con.close()
            return 5
        con.close()
    return int(r["percent"] or 5)


def set_aff_percent(user_id: int, percent: int):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO affiliate(user_id, percent) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET percent=excluded.percent",
            (user_id, int(percent)),
        )
        con.commit()
        con.close()


def add_aff_earning(referrer_id, referred_user_id, order_id, amount):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO affiliate_earnings(referrer_id, referred_user_id, order_id, amount, created_at) VALUES(?,?,?,?,?)",
            (referrer_id, referred_user_id, order_id, int(amount), now_str()),
        )
        con.commit()
        con.close()


# =========================================================
# ORDERS / DEPOSITS
# =========================================================
def create_order(user_id, product_id, price, discount, final_price, coupon_code, status) -> int:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO orders(user_id, product_id, price, discount, final_price, coupon_code, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, product_id, int(price), int(discount), int(final_price), coupon_code, status, now_str()),
        )
        con.commit()
        oid = cur.lastrowid
        con.close()
    return oid


def list_orders(user_id: int, limit: int = 20):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("""
            SELECT o.id, o.final_price, o.status, o.created_at, p.name AS product_name
            FROM orders o
            LEFT JOIN products p ON p.id=o.product_id
            WHERE o.user_id=?
            ORDER BY o.id DESC LIMIT ?
        """, (user_id, int(limit)))
        rows = cur.fetchall()
        con.close()
    return rows


def create_deposit(user_id, amount, description, trans_id, status):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO deposits(user_id, amount, description, trans_id, status, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, int(amount), description or "", trans_id or "", status, now_str()),
        )
        con.commit()
        con.close()


# =========================================================
# BANK SEEN
# =========================================================
def bank_tx_seen(transaction_id: str) -> bool:
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT transaction_id FROM bank_seen WHERE transaction_id=?", (transaction_id,))
        r = cur.fetchone()
        con.close()
    return r is not None


def mark_bank_tx_seen(transaction_id: str):
    with _DB_LOCK:
        con = db_connect()
        cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (transaction_id, now_str()))
        con.commit()
        con.close()


# =========================================================
# CONFIG
# =========================================================
@dataclass
class BotConfig:
    admin_id: int = 0
    bot_token: str = ""
    log_group_id: int = 0
    start_video_path: str = ""
    # VietQR (hiển thị QR cho user quét)
    vietqr_bank_name: str = "MB Bank"
    vietqr_bank_id: str = "mbbank"
    vietqr_stk: str = ""
    vietqr_ctk: str = ""
    vietqr_template: str = DEFAULT_VIETQR_TEMPLATE
    # MB Bank auto-login
    mb_username: str = ""
    mb_password: str = ""
    mb_account_no: str = ""   # STK muốn theo dõi (để trống = lấy STK đầu tiên)
    poll_interval: int = 10   # giây — MB thường đủ nhanh ở 10s


def load_config() -> BotConfig:
    if not os.path.exists(CONFIG_FILE):
        return BotConfig()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            j = json.load(f)
        cfg = BotConfig()
        for k, v in j.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg
    except Exception:
        return BotConfig()


def save_config(cfg: BotConfig):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, ensure_ascii=False, indent=2)


# =========================================================
# BOT RUNNER
# =========================================================
class BotRunner:
    def __init__(self, log_queue: queue.Queue):
        self.cfg: BotConfig = load_config()
        self.log_queue = log_queue
        self._app: Optional[Application] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._bank_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._bot_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_username: Optional[str] = None
        # buy cache: {user_id: {"product_id": int, "ts": float}} — có TTL
        self._buy_cache: Dict[int, Dict[str, Any]] = {}
        # tele buy cache: {user_id: {"acc_id": int, "ts": float}}
        self._tele_buy_cache: Dict[int, Dict[str, Any]] = {}

    def log(self, msg: str):
        try:
            self.log_queue.put(f"[{now_str()}] {msg}")
        except Exception:
            pass

    def _clean_caches(self):
        now = time.time()
        expired_buy = [u for u, v in self._buy_cache.items() if now - v.get("ts", 0) > BUY_CACHE_TTL]
        for u in expired_buy:
            del self._buy_cache[u]
        expired_tele = [u for u, v in self._tele_buy_cache.items() if now - v.get("ts", 0) > BUY_CACHE_TTL]
        for u in expired_tele:
            del self._tele_buy_cache[u]

    # ─── KEYBOARDS ─────────────────────────────────────────
    def user_reply_keyboard(self, user_id: int) -> ReplyKeyboardMarkup:
        """Bàn phím persistent bên dưới chat."""
        stock_tele = count_available_tele_accs()
        rows = [
            [KeyboardButton(BTN_CATS), KeyboardButton(f"{BTN_TELE} ({stock_tele})")],
            [KeyboardButton(BTN_DEPOSIT), KeyboardButton(BTN_HISTORY)],
            [KeyboardButton(BTN_AFF), KeyboardButton(BTN_BALANCE)],
        ]
        if user_id in ADMIN_MODE:
            rows.append([KeyboardButton(BTN_ADMIN)])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

    # Giữ lại alias để không cần đổi chỗ nào cần gửi kèm reply_markup
    def user_main_keyboard(self, user_id: int) -> ReplyKeyboardMarkup:
        return self.user_reply_keyboard(user_id)

    def admin_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("📱 Quản lý Acc Tele", callback_data="a:tele_panel")],
            [InlineKeyboardButton("💱 Tỉ giá USD/VND", callback_data="a:set_rate")],
            [InlineKeyboardButton("➕ Thêm chuyên mục", callback_data="a:addcat"),
             InlineKeyboardButton("✏️ Sửa chuyên mục", callback_data="a:editcat")],
            [InlineKeyboardButton("🗑 Xóa chuyên mục", callback_data="a:delcat")],
            [InlineKeyboardButton("➕ Thêm sản phẩm", callback_data="a:addprod"),
             InlineKeyboardButton("✏️ Sửa sản phẩm", callback_data="a:editprod")],
            [InlineKeyboardButton("🗑 Xóa sản phẩm", callback_data="a:delprod")],
            [InlineKeyboardButton("🏷 Coupon: Thêm/Update", callback_data="a:addcoupon"),
             InlineKeyboardButton("🚫 Coupon: Tắt", callback_data="a:delcoupon")],
            [InlineKeyboardButton("💸 % Hoa hồng (Affiliate)", callback_data="a:setcomm")],
            [InlineKeyboardButton("📣 Broadcast", callback_data="a:broadcast")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="u:home")],
        ]
        return InlineKeyboardMarkup(rows)

    def tele_admin_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("➕ Thêm acc", callback_data="a:tele_add")],
            [InlineKeyboardButton("📋 Xem acc available", callback_data="a:tele_view_avail")],
            [InlineKeyboardButton("📋 Xem acc đã bán", callback_data="a:tele_view_sold")],
            [InlineKeyboardButton("🗑 Xóa acc theo ID", callback_data="a:tele_del")],
            [InlineKeyboardButton("⬅️ Admin Panel", callback_data="a:panel")],
        ]
        return InlineKeyboardMarkup(rows)

    # ─── LOG GROUP ─────────────────────────────────────────
    async def _send_log_group(self, bot, text: str):
        gid = int(self.cfg.log_group_id or 0)
        if gid == 0:
            return
        try:
            await bot.send_message(chat_id=gid, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            self.log(f"Log group error: {e}")

    async def _notify_purchase_log(self, bot, user_id, order_id, prod_name, price, discount, final_price, coupon_code):
        u = get_user(user_id)
        uname = u["username"] if u else ""
        fname = u["first_name"] if u else ""
        user_line = f"<code>{user_id}</code>"
        if uname:
            user_line += f" • @{h(uname)}"
        elif fname:
            user_line += f" • {h(fname)}"
        text = (
            "🛒 <b>ĐƠN HÀNG MỚI</b>\n"
            f"🧾 Mã đơn: <b>#{order_id}</b>\n"
            f"👤 User: {user_line}\n"
            f"📦 Sản phẩm: <b>{h(prod_name)}</b>\n"
            f"💵 Giá gốc: <b>{price:,}đ</b>\n"
            f"🏷 Giảm giá: <b>{discount:,}đ</b>\n"
            f"✅ Thanh toán: <b>{final_price:,}đ</b>\n"
            f"🎟 Coupon: <code>{h(coupon_code or 'none')}</code>\n"
            f"⏰ {now_str()}"
        )
        await self._send_log_group(bot, text)

    async def _notify_tele_sale_log(self, bot, user_id, order_id, acc_id, phone, price_vnd):
        u = get_user(user_id)
        uname = u["username"] if u else ""
        user_line = f"<code>{user_id}</code>"
        if uname:
            user_line += f" • @{h(uname)}"
        text = (
            "📱 <b>BÁN ACC TELEGRAM</b>\n"
            f"🧾 Đơn: <b>#{order_id}</b>\n"
            f"👤 User: {user_line}\n"
            f"📲 Acc ID: <b>#{acc_id}</b> | <code>{h(phone)}</code>\n"
            f"💰 Giá: <b>{price_vnd:,}đ</b>\n"
            f"⏰ {now_str()}"
        )
        await self._send_log_group(bot, text)

    async def _notify_deposit_log(self, bot, user_id, amount, tid, desc):
        u = get_user(user_id)
        uname = u["username"] if u else ""
        fname = u["first_name"] if u else ""
        user_line = f"<code>{user_id}</code>"
        if uname:
            user_line += f" • @{h(uname)}"
        elif fname:
            user_line += f" • {h(fname)}"
        text = (
            "💳 <b>NẠP TIỀN THÀNH CÔNG</b>\n"
            f"👤 User: {user_line}\n"
            f"💰 Số tiền: <b>{amount:,}đ</b>\n"
            f"🧾 Mã GD: <code>{h(tid)}</code>\n"
            f"📝 ND: <code>{h((desc or '')[:300])}</code>\n"
            f"⏰ {now_str()}"
        )
        await self._send_log_group(bot, text)

    # ─── HANDLERS ──────────────────────────────────────────
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        upsert_user(user)

        if context.args:
            arg0 = context.args[0].strip()
            if arg0.lower().startswith("ref_"):
                ref_id = safe_int(arg0.split("_", 1)[1], 0)
                if ref_id > 0:
                    set_ref_if_empty(user.id, ref_id)

        try:
            if self.cfg.start_video_path and os.path.exists(self.cfg.start_video_path):
                with open(self.cfg.start_video_path, "rb") as vf:
                    await update.message.reply_video(video=vf, caption="🎉 Chào mừng!")
        except Exception as e:
            self.log(f"Send start video error: {e}")

        bal = get_balance(user.id)
        rate = get_usd_rate()
        txt = (
            f"👋 Xin chào <b>{h(user.first_name)}</b>\n"
            f"💰 Số dư: <b>{bal:,}đ</b>\n"
            f"💱 Tỉ giá: <b>1 USD = {rate:,}đ</b>\n\n"
            "Chọn chức năng bên dưới 👇"
        )
        await update.message.reply_text(
            txt,
            reply_markup=self.user_reply_keyboard(user.id),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_checkadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        upsert_user(user)
        if user.id == int(self.cfg.admin_id or 0):
            ADMIN_MODE.add(user.id)
            await update.message.reply_text(
                "✅ Admin mode ON. Nút <b>🛠 Admin Panel</b> đã hiện trên bàn phím.",
                reply_markup=self.user_reply_keyboard(user.id),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text("❌ Không phải admin.")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📌 Lệnh:\n/start\n/checkadmin (admin ẩn)\n/help\n/getchatid\n\n"
            "Admin dùng Admin Panel sau /checkadmin."
        )

    async def cmd_getchatid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        await update.message.reply_text(
            f"🆔 Chat ID: <code>{chat.id}</code>\n📝 Type: <b>{chat.type}</b>",
            parse_mode=ParseMode.HTML,
        )

    # ─── CALLBACK ROUTER ───────────────────────────────────
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        user = q.from_user
        upsert_user(user)
        self._clean_caches()

        data = q.data or ""

        # ── USER FLOW ──
        if data == "u:home":
            bal = get_balance(user.id)
            rate = get_usd_rate()
            await q.message.reply_text(
                f"🏠 Menu chính\n💰 Số dư: <b>{bal:,}đ</b>\n💱 Tỉ giá: <b>1 USD = {rate:,}đ</b>",
                reply_markup=self.user_reply_keyboard(user.id),
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "u:cats":
            await self._kb_cats(q.message, user)
            return

        if data.startswith("u:cat:"):
            cid = safe_int(data.split(":")[2], 0)
            prods = list_products_by_category(cid)
            if not prods:
                await q.edit_message_text(
                    "❌ Chưa có sản phẩm.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")]]),
                )
                return
            rows = [
                [InlineKeyboardButton(f"🛒 {p['name']} — {p['price']:,}đ (Kho: {p['stock']})", callback_data=f"u:prod:{p['id']}")]
                for p in prods
            ]
            rows.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")])
            await q.edit_message_text("🛍 Chọn sản phẩm:", reply_markup=InlineKeyboardMarkup(rows))
            return

        if data.startswith("u:prod:"):
            pid = safe_int(data.split(":")[2], 0)
            prod = get_product(pid)
            if not prod:
                await q.edit_message_text("❌ Sản phẩm không tồn tại.")
                return
            txt = (
                f"🛒 <b>{h(prod['name'])}</b>\n"
                f"💵 Giá: <b>{int(prod['price']):,}đ</b>\n"
                f"📦 Kho: <b>{int(prod['stock']):,}</b>\n\nBạn muốn mua?"
            )
            rows = [
                [InlineKeyboardButton("✅ Mua ngay", callback_data=f"u:buy:{pid}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")],
            ]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        if data.startswith("u:buy:"):
            pid = safe_int(data.split(":")[2], 0)
            prod = get_product(pid)
            if not prod:
                await q.edit_message_text("❌ Sản phẩm không tồn tại.")
                return
            if int(prod["stock"] or 0) <= 0:
                await q.edit_message_text("❌ Hết hàng.")
                return
            self._buy_cache[user.id] = {"product_id": pid, "ts": time.time()}
            rows = [
                [InlineKeyboardButton("✅ Có", callback_data="u:coupon_yes"),
                 InlineKeyboardButton("❌ Không", callback_data="u:coupon_no")],
                [InlineKeyboardButton("⬅️ Huỷ", callback_data="u:home")],
            ]
            await q.edit_message_text("🏷 Bạn có muốn nhập mã giảm giá không?", reply_markup=InlineKeyboardMarkup(rows))
            return

        if data == "u:coupon_no":
            await self._finalize_purchase(user.id, coupon_code=None, message=q.message, context=context)
            return

        if data == "u:coupon_yes":
            await q.edit_message_text("📩 Gửi <b>mã giảm giá</b>:", parse_mode=ParseMode.HTML)
            context.user_data["waiting_coupon"] = True
            return



        # ── TELE ACC FLOW ──
        if data == "u:tele_list":
            await self._kb_tele_list(q.message, user)
            return

        if data.startswith("u:tele_detail:"):
            acc_id = safe_int(data.split(":")[2], 0)
            acc = get_tele_acc(acc_id)
            if not acc or acc["status"] != "available":
                await q.edit_message_text("❌ Acc không còn available.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️", callback_data="u:tele_list")]]))
                return
            rate = get_usd_rate()
            price_usd = round(acc["price_vnd"] / rate, 2) if rate else 0
            txt = (
                f"📱 <b>Acc Telegram #{acc_id}</b>\n"
                f"💰 Giá: <b>{int(acc['price_vnd']):,}đ</b> (~${price_usd})\n"
                f"📝 Note: {h(acc['note'] or 'Không có')}\n\n"
                "✅ Sau khi mua, thông tin đăng nhập sẽ được gửi ngay."
            )
            rows = [
                [InlineKeyboardButton("✅ Mua ngay", callback_data=f"u:tele_buy:{acc_id}")],
                [InlineKeyboardButton("⬅️ Danh sách", callback_data="u:tele_list")],
            ]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        if data.startswith("u:tele_buy:"):
            acc_id = safe_int(data.split(":")[2], 0)
            acc = get_tele_acc(acc_id)
            if not acc or acc["status"] != "available":
                await q.edit_message_text("❌ Acc không còn available.")
                return
            price_vnd = int(acc["price_vnd"])
            bal = get_balance(user.id)
            if bal < price_vnd:
                await q.edit_message_text(
                    f"❌ Số dư không đủ.\n💰 Số dư: {bal:,}đ\n🧾 Cần: {price_vnd:,}đ\n👉 Bấm <b>💳 Nạp tiền</b> trên bàn phím.",
                    parse_mode=ParseMode.HTML,
                )
                return
            # Tạo order placeholder trước
            order_id = create_order(
                user_id=user.id,
                product_id=None,
                price=price_vnd,
                discount=0,
                final_price=price_vnd,
                coupon_code=None,
                status="PENDING_TELE",
            )
            ok = atomic_buy_tele_acc(acc_id, user.id, price_vnd, order_id)
            if not ok:
                # Huỷ order vừa tạo
                with _DB_LOCK:
                    con = db_connect()
                    cur = con.cursor()
                    cur.execute("UPDATE orders SET status='FAILED' WHERE id=?", (order_id,))
                    con.commit()
                    con.close()
                await q.edit_message_text("❌ Mua thất bại (hết hàng hoặc số dư không đủ). Thử lại.")
                return
            # Update order status
            with _DB_LOCK:
                con = db_connect()
                cur = con.cursor()
                cur.execute("UPDATE orders SET status='PAID', product_id=? WHERE id=?", (acc_id, order_id))
                con.commit()
                con.close()

            # Reload acc để lấy thông tin đầy đủ
            acc_full = get_tele_acc(acc_id)
            deliver = (
                f"📱 <b>Thông tin Acc Telegram</b>\n"
                f"📞 Phone: <code>{h(acc_full['phone'])}</code>\n"
                f"🔑 Password: <code>{h(acc_full['password'] or 'N/A')}</code>\n"
                f"🔐 2FA: <code>{h(acc_full['twofa'] or 'N/A')}</code>\n"
                f"📝 Note: {h(acc_full['note'] or 'Không có')}"
            )
            await q.edit_message_text(
                f"✅ <b>Mua acc thành công!</b>\n🧾 Đơn #{order_id}\n💰 Đã trừ: {price_vnd:,}đ\n\n{deliver}",
                parse_mode=ParseMode.HTML,
            )
            # Notify admin + log group
            try:
                if int(self.cfg.admin_id or 0) > 0:
                    await context.bot.send_message(
                        chat_id=int(self.cfg.admin_id),
                        text=f"📱 Bán acc #{acc_id} ({acc_full['phone']}) cho user {user.id}\nĐơn #{order_id} — {price_vnd:,}đ",
                    )
            except Exception:
                pass
            try:
                await self._notify_tele_sale_log(context.bot, user.id, order_id, acc_id, acc_full["phone"], price_vnd)
            except Exception as e:
                self.log(f"Tele sale log error: {e}")

            # Affiliate
            u_row = get_user(user.id)
            ref_by = u_row["ref_by"] if u_row else None
            if ref_by:
                percent = get_aff_percent(int(ref_by))
                earn = (price_vnd * percent) // 100
                if earn > 0:
                    add_balance(int(ref_by), earn)
                    add_aff_earning(int(ref_by), user.id, order_id, earn)
                    try:
                        await context.bot.send_message(chat_id=int(ref_by), text=f"🎉 Hoa hồng {earn:,}đ từ đơn #{order_id}")
                    except Exception:
                        pass
            return

        # ── ADMIN FLOW ──
        if data == "a:panel":
            if user.id not in ADMIN_MODE:
                await q.edit_message_text("❌ Chưa bật admin. /checkadmin")
                return
            await q.edit_message_text("🛠 <b>Admin Panel</b>", reply_markup=self.admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if data == "a:tele_panel":
            if user.id not in ADMIN_MODE:
                return
            avail = count_available_tele_accs()
            sold = len(list_tele_accs("sold"))
            rate = get_usd_rate()
            await q.edit_message_text(
                f"📱 <b>Quản lý Acc Telegram</b>\n"
                f"✅ Available: <b>{avail}</b>\n"
                f"💰 Đã bán: <b>{sold}</b>\n"
                f"💱 Tỉ giá: <b>1 USD = {rate:,}đ</b>",
                reply_markup=self.tele_admin_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "a:tele_add":
            if user.id not in ADMIN_MODE:
                return
            await q.edit_message_text(
                "➕ Gửi lệnh:\n"
                "<code>/addacc PHONE | PASSWORD | 2FA | NOTE | GIA_VND</code>\n\n"
                "Ví dụ:\n"
                "<code>/addacc +84912345678 | pass123 | abc123 | Acc VIP | 150000</code>\n\n"
                "PASSWORD / 2FA để trống gõ dấu - nếu không có.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️", callback_data="a:tele_panel")]]),
            )
            return

        if data == "a:tele_view_avail":
            if user.id not in ADMIN_MODE:
                return
            accs = list_tele_accs("available")
            if not accs:
                await q.edit_message_text("📋 Không có acc available.", reply_markup=self.tele_admin_keyboard())
                return
            lines = ["📋 <b>Acc Available:</b>\n"]
            for a in accs[:50]:
                lines.append(f"#{a['id']} | {h(a['phone'])} | {int(a['price_vnd']):,}đ | {h(a['note'] or '')}")
            await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=self.tele_admin_keyboard())
            return

        if data == "a:tele_view_sold":
            if user.id not in ADMIN_MODE:
                return
            accs = list_tele_accs("sold")
            if not accs:
                await q.edit_message_text("📋 Chưa bán acc nào.", reply_markup=self.tele_admin_keyboard())
                return
            lines = ["📋 <b>Acc Đã Bán:</b>\n"]
            for a in accs[:50]:
                lines.append(f"#{a['id']} | {h(a['phone'])} | {int(a['price_vnd']):,}đ")
            await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=self.tele_admin_keyboard())
            return

        if data == "a:tele_del":
            if user.id not in ADMIN_MODE:
                return
            await q.edit_message_text(
                "🗑 Gửi lệnh:\n<code>/delacc ACC_ID</code>\n\nVí dụ: <code>/delacc 5</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️", callback_data="a:tele_panel")]]),
            )
            return

        if data == "a:set_rate":
            if user.id not in ADMIN_MODE:
                return
            rate = get_usd_rate()
            await q.edit_message_text(
                f"💱 Tỉ giá hiện tại: <b>1 USD = {rate:,}đ</b>\n\nGửi lệnh:\n<code>/setrate TI_GIA</code>\nVí dụ: <code>/setrate 25500</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin", callback_data="a:panel")]]),
            )
            return

        if data.startswith("a:"):
            if user.id not in ADMIN_MODE:
                await q.edit_message_text("❌ Bạn không có quyền.")
                return
            action = data.split(":")[1]
            tips = {
                "addcat":    "➕ Gửi: <code>/addcat Tên chuyên mục</code>",
                "editcat":   "✏️ Gửi: <code>/editcat ID Tên mới</code>",
                "delcat":    "🗑 Gửi: <code>/delcat ID</code>",
                "addprod":   "➕ Gửi:\n<code>/addprod CAT_ID | Tên | Giá | Kho | Nội dung giao</code>",
                "editprod":  "✏️ Gửi:\n<code>/editprod PROD_ID | Tên | Giá | Kho | Nội dung giao</code>",
                "delprod":   "🗑 Gửi: <code>/delprod PROD_ID</code>",
                "addcoupon": "🏷 Gửi:\n<code>/coupon CODE | giam_tien | giam_% | all/PROD_ID | min_order | max_uses</code>",
                "delcoupon": "🚫 Gửi: <code>/couponoff CODE</code>",
                "setcomm":   "💸 Gửi: <code>/setcomm USER_ID PERCENT</code>",
                "broadcast": "📣 Gửi: <code>/broadcast Nội dung</code>",
            }
            tip = tips.get(action, "⚠️ Không hỗ trợ.")
            await q.edit_message_text(tip, parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())

    # ─── MESSAGE HANDLER ───────────────────────────────────
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        upsert_user(user)
        text = (update.message.text or "").strip()
        self._clean_caches()

        # ── Waiting states (ưu tiên cao nhất) ──
        if context.user_data.get("waiting_deposit_amount"):
            context.user_data["waiting_deposit_amount"] = False
            amt = safe_int(text, 0)
            if amt <= 0:
                await update.message.reply_text("❌ Số tiền không hợp lệ.")
                return
            if not self.cfg.vietqr_stk or not self.cfg.vietqr_ctk or not self.cfg.vietqr_bank_id:
                await update.message.reply_text("❌ Admin chưa cấu hình bank.")
                return
            add_info = f"Napid {user.id}"
            qr_url = vietqr_image_url(
                bank_id=self.cfg.vietqr_bank_id,
                account_no=self.cfg.vietqr_stk,
                template=self.cfg.vietqr_template or DEFAULT_VIETQR_TEMPLATE,
                amount=amt,
                add_info=add_info,
                account_name=self.cfg.vietqr_ctk,
            )
            msg = (
                "💳 <b>Nạp tiền</b>\n"
                f"🏦 Ngân hàng: <b>{h(self.cfg.vietqr_bank_name)}</b>\n"
                f"💳 STK: <code>{h(self.cfg.vietqr_stk)}</code>\n"
                f"👤 CTK: <b>{h(self.cfg.vietqr_ctk)}</b>\n"
                f"✅ Nội dung CK: <code>{add_info}</code>\n"
                f"💰 Số tiền: <b>{amt:,}đ</b>\n\n"
                f"📌 Quét QR:\n{qr_url}"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            return

        if context.user_data.get("waiting_coupon"):
            context.user_data["waiting_coupon"] = False
            code = text.upper()
            await self._finalize_purchase(user.id, coupon_code=code, message=update.message, context=context)
            return

        # ── Nút bàn phím — detect theo prefix vì text có thể có "(N acc)" ──
        tl = text.lower()

        if tl.startswith(BTN_CATS.lower()):
            await self._kb_cats(update.message, user)
            return

        if tl.startswith(BTN_TELE.lower()):
            await self._kb_tele_list(update.message, user)
            return

        if tl.startswith(BTN_DEPOSIT.lower()):
            await update.message.reply_text("💳 Nhập số tiền bạn muốn nạp (VD: 50000):")
            context.user_data["waiting_deposit_amount"] = True
            return

        if tl.startswith(BTN_HISTORY.lower()):
            await self._kb_history(update.message, user)
            return

        if tl.startswith(BTN_AFF.lower()):
            await self._kb_aff(update.message, user, context)
            return

        if tl.startswith(BTN_BALANCE.lower()):
            bal = get_balance(user.id)
            rate = get_usd_rate()
            await update.message.reply_text(
                f"💰 Số dư: <b>{bal:,}đ</b>\n💱 Tỉ giá: <b>1 USD = {rate:,}đ</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if tl.startswith(BTN_ADMIN.lower()):
            if user.id not in ADMIN_MODE:
                await update.message.reply_text("❌ Chưa bật admin. /checkadmin")
                return
            await update.message.reply_text(
                "🛠 <b>Admin Panel</b>",
                reply_markup=self.admin_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Fallback ──
        await update.message.reply_text("👇 Chọn chức năng trên bàn phím nhé.")

    # ─── KB HANDLERS (nút bàn phím gọi vào) ──────────────
    # ─── KB SUB-HANDLERS ──────────────────────────────────
    # Nhận message object (từ update.message hoặc q.message) để dùng được từ cả 2 nơi.

    async def _kb_cats(self, message, user):
        cats = list_categories()
        if not cats:
            await message.reply_text("❌ Chưa có chuyên mục nào.")
            return
        rows = [[InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"u:cat:{c['id']}")] for c in cats]
        await message.reply_text("📦 Chọn chuyên mục:", reply_markup=InlineKeyboardMarkup(rows))

    async def _kb_tele_list(self, message, user):
        accs = list_tele_accs("available")
        rate = get_usd_rate()
        if not accs:
            await message.reply_text("❌ Hiện chưa có acc Telegram nào.\nQuay lại sau nhé!")
            return
        rows = [
            [InlineKeyboardButton(
                f"📱 #{a['id']} | {a['price_vnd']:,}đ | {a['note'] or 'No note'}",
                callback_data=f"u:tele_detail:{a['id']}"
            )]
            for a in accs
        ]
        await message.reply_text(
            f"📱 <b>Acc Telegram có sẵn</b> ({len(accs)} acc)\n💱 Tỉ giá: 1 USD = {rate:,}đ",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
        )

    async def _kb_history(self, message, user):
        orders = list_orders(user.id, limit=15)
        if not orders:
            await message.reply_text("🧾 Chưa có đơn hàng nào.")
            return
        lines = ["🧾 <b>Lịch sử mua</b> (15 đơn gần nhất):\n"]
        for o in orders:
            lines.append(f"#{o['id']} • {h(o['product_name'] or 'Acc Tele')} • {int(o['final_price']):,}đ • {o['status']} • {o['created_at']}")
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _kb_aff(self, message, user, context):
        if not self._bot_username:
            try:
                me = await context.bot.get_me()
                self._bot_username = me.username
            except Exception:
                self._bot_username = "YourBot"
        link = f"https://t.me/{self._bot_username}?start=ref_{user.id}"
        percent = get_aff_percent(user.id)
        with _DB_LOCK:
            con = db_connect()
            cur = con.cursor()
            cur.execute("SELECT COALESCE(SUM(amount),0) AS s FROM affiliate_earnings WHERE referrer_id=?", (user.id,))
            total = int(cur.fetchone()["s"] or 0)
            con.close()
        txt = (
            "🤝 <b>Tiếp thị liên kết</b>\n"
            f"🔗 Link ref: <code>{link}</code>\n"
            f"💸 % hoa hồng: <b>{percent}%</b>\n"
            f"🏦 Tổng hoa hồng: <b>{total:,}đ</b>\n\n"
            "Gửi link, khi họ mua bạn nhận hoa hồng."
        )
        await message.reply_text(txt, parse_mode=ParseMode.HTML)

    # ─── PURCHASE FINALIZER ────────────────────────────────
    async def _finalize_purchase(self, user_id, coupon_code, message, context):
        cache = self._buy_cache.get(user_id)
        if not cache:
            await message.reply_text("❌ Không có giao dịch mua đang chờ.", reply_markup=self.user_main_keyboard(user_id))
            return
        pid = cache.get("product_id")
        prod = get_product(pid)
        if not prod:
            await message.reply_text("❌ Sản phẩm không tồn tại.", reply_markup=self.user_main_keyboard(user_id))
            return
        if int(prod["stock"] or 0) <= 0:
            await message.reply_text("❌ Hết hàng.", reply_markup=self.user_main_keyboard(user_id))
            return

        price = int(prod["price"])
        discount = 0
        applied_code = None

        if coupon_code:
            cp = get_coupon(coupon_code)
            if not cp:
                await message.reply_text("❌ Mã giảm giá không hợp lệ. Tiếp tục không giảm.", reply_markup=self.user_main_keyboard(user_id))
            else:
                max_uses = int(cp["max_uses"] or 0)
                used = int(cp["used_count"] or 0)
                min_order = int(cp["min_order"] or 0)
                apply_pid = cp["apply_product_id"]
                if max_uses > 0 and used >= max_uses:
                    await message.reply_text("❌ Mã hết lượt dùng. Tiếp tục không giảm.", reply_markup=self.user_main_keyboard(user_id))
                elif price < min_order:
                    await message.reply_text(f"❌ Đơn tối thiểu {min_order:,}đ. Tiếp tục không giảm.", reply_markup=self.user_main_keyboard(user_id))
                elif apply_pid is not None and int(apply_pid) != int(pid):
                    await message.reply_text("❌ Mã không áp dụng cho sản phẩm này. Tiếp tục không giảm.", reply_markup=self.user_main_keyboard(user_id))
                else:
                    dpct = int(cp["discount_percent"] or 0)
                    damt = int(cp["discount_amount"] or 0)
                    discount = (price * dpct) // 100 if dpct > 0 else damt
                    discount = max(0, min(discount, price))
                    applied_code = coupon_code

        final_price = max(0, price - discount)

        # ATOMIC: trừ tiền + giảm stock trong 1 lock
        ok = atomic_buy_product(pid, user_id, final_price)
        if not ok:
            bal = get_balance(user_id)
            if bal < final_price:
                await message.reply_text(
                    f"❌ Số dư không đủ.\n💰 Số dư: {bal:,}đ\n🧾 Cần: {final_price:,}đ",
                    reply_markup=self.user_main_keyboard(user_id),
                )
            else:
                await message.reply_text("❌ Hết hàng (vừa có người mua trước).", reply_markup=self.user_main_keyboard(user_id))
            return

        order_id = create_order(user_id, pid, price, discount, final_price, applied_code, "PAID")
        if applied_code:
            mark_coupon_used(applied_code)

        u_row = get_user(user_id)
        ref_by = u_row["ref_by"] if u_row else None
        if ref_by:
            percent = get_aff_percent(int(ref_by))
            earn = (final_price * percent) // 100
            if earn > 0:
                add_balance(int(ref_by), earn)
                add_aff_earning(int(ref_by), user_id, order_id, earn)
                try:
                    await context.bot.send_message(chat_id=int(ref_by), text=f"🎉 Hoa hồng {earn:,}đ từ đơn #{order_id}")
                except Exception:
                    pass

        deliver_text = (prod["deliver_text"] or "").strip()
        if not deliver_text:
            deliver_text = "✅ Admin sẽ giao nội dung sau."

        await message.reply_text(
            "✅ <b>Mua thành công</b>\n"
            f"🧾 Đơn: <b>#{order_id}</b>\n"
            f"🛒 Sản phẩm: <b>{h(prod['name'])}</b>\n"
            f"💵 Giá: <b>{price:,}đ</b>\n"
            f"🏷 Giảm: <b>{discount:,}đ</b>\n"
            f"✅ Thanh toán: <b>{final_price:,}đ</b>\n\n"
            f"📦 <b>Nội dung giao:</b>\n{h(deliver_text)}",
            parse_mode=ParseMode.HTML,
            reply_markup=self.user_main_keyboard(user_id),
        )
        try:
            if int(self.cfg.admin_id or 0) > 0:
                await context.bot.send_message(
                    chat_id=int(self.cfg.admin_id),
                    text=f"🛎 Đơn mới #{order_id}\nUser: {user_id}\nSP: {prod['name']}\nThanh toán: {final_price:,}đ",
                )
        except Exception:
            pass
        try:
            await self._notify_purchase_log(context.bot, user_id, order_id, prod["name"], price, discount, final_price, applied_code)
        except Exception as e:
            self.log(f"Purchase log error: {e}")
        self._buy_cache.pop(user_id, None)

    # ─── ADMIN COMMANDS ────────────────────────────────────
    def _is_admin(self, user_id: int) -> bool:
        return user_id == int(self.cfg.admin_id or 0)

    async def admin_guard(self, update: Update) -> bool:
        uid = update.effective_user.id
        if not self._is_admin(uid):
            await update.message.reply_text("❌ Không có quyền admin.")
            return False
        if uid not in ADMIN_MODE:
            await update.message.reply_text("⚠️ Bật admin trước: /checkadmin")
            return False
        return True

    def _parse_pipe(self, raw: str) -> List[str]:
        return [x.strip() for x in raw.split("|")]

    async def cmd_addcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        name = " ".join(context.args).strip()
        if not name:
            await update.message.reply_text("Dùng: /addcat Tên chuyên mục")
            return
        cid = add_category(name)
        await update.message.reply_text(f"✅ Đã thêm chuyên mục #{cid}: {name}")

    async def cmd_editcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Dùng: /editcat ID Tên mới")
            return
        cid = safe_int(context.args[0], 0)
        name = " ".join(context.args[1:]).strip()
        if cid <= 0 or not name:
            await update.message.reply_text("❌ Sai dữ liệu.")
            return
        edit_category(cid, name)
        await update.message.reply_text(f"✅ Đã sửa chuyên mục #{cid}")

    async def cmd_delcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        cid = safe_int(context.args[0] if context.args else "0", 0)
        if cid <= 0:
            await update.message.reply_text("Dùng: /delcat ID")
            return
        delete_category(cid)
        await update.message.reply_text(f"✅ Đã ẩn chuyên mục #{cid}")

    async def cmd_addprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/addprod", "", 1).strip()
        parts = self._parse_pipe(raw)
        if len(parts) < 5:
            await update.message.reply_text("Dùng: /addprod CAT_ID | Tên | Giá | Kho | Nội dung giao")
            return
        cid, name, price, stock, deliver = safe_int(parts[0]), parts[1], safe_int(parts[2]), safe_int(parts[3]), parts[4]
        if cid <= 0 or not name or price <= 0:
            await update.message.reply_text("❌ Sai dữ liệu.")
            return
        pid = add_product(cid, name, price, stock, deliver)
        await update.message.reply_text(f"✅ Đã thêm sản phẩm #{pid}: {name}")

    async def cmd_editprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/editprod", "", 1).strip()
        parts = self._parse_pipe(raw)
        if len(parts) < 5:
            await update.message.reply_text("Dùng: /editprod PROD_ID | Tên | Giá | Kho | Nội dung giao")
            return
        pid, name, price, stock, deliver = safe_int(parts[0]), parts[1], safe_int(parts[2]), safe_int(parts[3]), parts[4]
        if pid <= 0 or not name or price <= 0:
            await update.message.reply_text("❌ Sai dữ liệu.")
            return
        edit_product(pid, name, price, stock, deliver)
        await update.message.reply_text(f"✅ Đã sửa sản phẩm #{pid}")

    async def cmd_delprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        pid = safe_int(context.args[0] if context.args else "0", 0)
        if pid <= 0:
            await update.message.reply_text("Dùng: /delprod PROD_ID")
            return
        delete_product(pid)
        await update.message.reply_text(f"✅ Đã ẩn sản phẩm #{pid}")

    # ─── TELE ACC COMMANDS ─────────────────────────────────
    async def cmd_addacc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/addacc", "", 1).strip()
        parts = self._parse_pipe(raw)
        if len(parts) < 5:
            await update.message.reply_text(
                "Dùng: /addacc PHONE | PASSWORD | 2FA | NOTE | GIA_VND\n"
                "PASSWORD/2FA không có thì gõ -"
            )
            return
        phone = parts[0]
        password = "" if parts[1] == "-" else parts[1]
        twofa = "" if parts[2] == "-" else parts[2]
        note = parts[3]
        price_vnd = safe_int(parts[4], 0)
        if not phone or price_vnd <= 0:
            await update.message.reply_text("❌ Phone hoặc giá không hợp lệ.")
            return
        aid = add_tele_acc(phone, password, twofa, note, price_vnd)
        await update.message.reply_text(f"✅ Đã thêm acc #{aid}: {phone} — {price_vnd:,}đ")

    async def cmd_delacc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        acc_id = safe_int(context.args[0] if context.args else "0", 0)
        if acc_id <= 0:
            await update.message.reply_text("Dùng: /delacc ACC_ID")
            return
        acc = get_tele_acc(acc_id)
        if not acc:
            await update.message.reply_text(f"❌ Acc #{acc_id} không tồn tại.")
            return
        if acc["status"] == "sold":
            await update.message.reply_text(f"⚠️ Acc #{acc_id} đã bán rồi. Xác nhận xóa: /delacc_force {acc_id}")
            return
        delete_tele_acc(acc_id)
        await update.message.reply_text(f"✅ Đã xóa acc #{acc_id}")

    async def cmd_delacc_force(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        acc_id = safe_int(context.args[0] if context.args else "0", 0)
        if acc_id <= 0:
            await update.message.reply_text("Dùng: /delacc_force ACC_ID")
            return
        delete_tele_acc(acc_id)
        await update.message.reply_text(f"✅ Đã xóa acc #{acc_id} (force)")

    async def cmd_setrate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        rate = safe_int(context.args[0] if context.args else "0", 0)
        if rate <= 0:
            await update.message.reply_text("Dùng: /setrate TI_GIA\nVí dụ: /setrate 25500")
            return
        set_setting("usd_rate", str(rate))
        await update.message.reply_text(f"✅ Đã set tỉ giá: 1 USD = {rate:,}đ")

    async def cmd_listacc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        accs = list_tele_accs("available")
        if not accs:
            await update.message.reply_text("📋 Không có acc available.")
            return
        lines = ["📋 <b>Acc Available:</b>"]
        for a in accs[:80]:
            lines.append(f"#{a['id']} | {h(a['phone'])} | {int(a['price_vnd']):,}đ | {h(a['note'] or '')}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ─── OTHER ADMIN COMMANDS ──────────────────────────────
    async def cmd_coupon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/coupon", "", 1).strip()
        parts = self._parse_pipe(raw)
        if len(parts) < 6:
            await update.message.reply_text("Dùng: /coupon CODE | giam_tien | giam_% | all/PROD_ID | min_order | max_uses")
            return
        code = parts[0].upper()
        damt, dpct = safe_int(parts[1]), safe_int(parts[2])
        ap = parts[3].lower()
        min_order, max_uses = safe_int(parts[4]), safe_int(parts[5])
        apply_pid = None if ap == "all" else safe_int(ap, 0)
        if not code or dpct < 0 or dpct > 100:
            await update.message.reply_text("❌ Dữ liệu không hợp lệ.")
            return
        upsert_coupon(code, damt, dpct, apply_pid, min_order, max_uses)
        await update.message.reply_text(f"✅ Coupon {code} đã tạo/cập nhật.")

    async def cmd_couponoff(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if not context.args:
            await update.message.reply_text("Dùng: /couponoff CODE")
            return
        disable_coupon(context.args[0].upper())
        await update.message.reply_text(f"✅ Đã tắt coupon {context.args[0].upper()}")

    async def cmd_setcomm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Dùng: /setcomm USER_ID PERCENT")
            return
        uid, pct = safe_int(context.args[0]), safe_int(context.args[1])
        if uid <= 0 or pct < 0 or pct > 100:
            await update.message.reply_text("❌ Sai dữ liệu.")
            return
        set_aff_percent(uid, pct)
        await update.message.reply_text(f"✅ Đã set hoa hồng user {uid} = {pct}%")

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        msg = update.message.text.replace("/broadcast", "", 1).strip()
        if not msg:
            await update.message.reply_text("Dùng: /broadcast Nội dung")
            return
        with _DB_LOCK:
            con = db_connect()
            cur = con.cursor()
            cur.execute("SELECT user_id FROM users")
            users = [int(r["user_id"]) for r in cur.fetchall()]
            con.close()
        sent = fail = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=msg)
                sent += 1
            except Exception:
                fail += 1
        await update.message.reply_text(f"✅ Broadcast xong. Sent={sent} Fail={fail}")

    # ─── MB BANK POLL (mbbank-lib, miễn phí) ──────────────
    async def _notify_deposit(self, bot, user_id, amount, tid):
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ Nạp thành công <b>{amount:,}đ</b>\n🧾 Mã GD: <code>{tid}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    async def _notify_admin_deposit(self, bot, user_id, amount, tid, desc):
        if int(self.cfg.admin_id or 0) <= 0:
            return
        try:
            await bot.send_message(
                chat_id=int(self.cfg.admin_id),
                text=(
                    f"💳 Nạp mới\nUser: <code>{user_id}</code>\n"
                    f"Tiền: <b>{amount:,}đ</b>\nMã GD: <code>{tid}</code>\n"
                    f"ND: <code>{h((desc or '')[:200])}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    async def _mb_bank_poll_loop(self):
        """Chạy trong bot event loop — dùng MBBankAsync, poll mỗi poll_interval giây."""
        from mbbank import MBBankAsync
        from datetime import datetime, timedelta

        self.log("MB Bank poll loop starting...")
        mb: Optional[MBBankAsync] = None

        while not self._stop_event.is_set():
            try:
                # ── Login / re-login nếu chưa có session ──
                if mb is None:
                    if not self.cfg.mb_username or not self.cfg.mb_password:
                        self.log("MB Bank: chưa cấu hình username/password. Bỏ qua poll.")
                        await asyncio.sleep(30)
                        continue
                    self.log("MB Bank: đang login...")
                    mb = MBBankAsync(username=self.cfg.mb_username, password=self.cfg.mb_password)
                    await mb.login()
                    self.log("MB Bank: login OK.")

                # ── Lấy lịch sử 1 ngày gần nhất ──
                now_dt = datetime.now()
                from_dt = now_dt - timedelta(days=1)
                acc_no = self.cfg.mb_account_no or None

                result = await mb.getTransactionAccountHistory(
                    accountNo=acc_no,
                    from_date=from_dt,
                    to_date=now_dt,
                )

                txs = result.transactionHistoryList or []
                new_count = 0

                for tx in txs:
                    # Chỉ quan tâm giao dịch tiền vào (creditAmount > 0)
                    amount = safe_int(getattr(tx, "creditAmount", 0) or 0)
                    if amount <= 0:
                        continue

                    # Transaction ID
                    tid = str(
                        getattr(tx, "refNo", None)
                        or getattr(tx, "transactionId", None)
                        or ""
                    ).strip()
                    if not tid:
                        continue
                    if bank_tx_seen(tid):
                        continue

                    desc = str(getattr(tx, "description", "") or "").strip()

                    # Extract Napid <user_id> từ nội dung CK
                    m = re.search(r"napid\s*(\d+)", desc, re.IGNORECASE)
                    if not m:
                        mark_bank_tx_seen(tid)   # mark để không check lại
                        continue

                    user_id = safe_int(m.group(1))
                    if user_id <= 0:
                        mark_bank_tx_seen(tid)
                        continue

                    mark_bank_tx_seen(tid)
                    add_balance(user_id, amount)
                    create_deposit(user_id, amount, desc, tid, "SUCCESS")
                    new_count += 1
                    self.log(f"✅ Nạp: user={user_id} amount={amount:,} tid={tid}")

                    bot = self._app.bot
                    await self._notify_deposit(bot, user_id, amount, tid)
                    await self._notify_admin_deposit(bot, user_id, amount, tid, desc)
                    await self._notify_deposit_log(bot, user_id, amount, tid, desc)

                if new_count:
                    self.log(f"MB Bank poll: +{new_count} giao dịch mới.")

            except Exception as e:
                err = str(e)
                self.log(f"MB Bank poll error: {err}")
                # Session hết hạn → force re-login lần sau
                if any(k in err.lower() for k in ("login", "session", "token", "unauthorized", "401")):
                    self.log("MB Bank: session expired, sẽ re-login lần sau.")
                    mb = None
                await asyncio.sleep(5)
                continue

            # Đợi đến lần poll tiếp
            interval = max(5, int(self.cfg.poll_interval or 10))
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.get_event_loop().run_in_executor(None, self._stop_event.wait, interval)),
                    timeout=interval + 1,
                )
            except Exception:
                pass
            if self._stop_event.is_set():
                break

        self.log("MB Bank poll loop stopped.")

    # ─── START / STOP ──────────────────────────────────────
    def _build_application(self) -> Application:
        app = Application.builder().token(self.cfg.bot_token).build()
        cmds = [
            ("start", self.cmd_start),
            ("help", self.cmd_help),
            ("checkadmin", self.cmd_checkadmin),
            ("getchatid", self.cmd_getchatid),
            ("addcat", self.cmd_addcat),
            ("editcat", self.cmd_editcat),
            ("delcat", self.cmd_delcat),
            ("addprod", self.cmd_addprod),
            ("editprod", self.cmd_editprod),
            ("delprod", self.cmd_delprod),
            ("coupon", self.cmd_coupon),
            ("couponoff", self.cmd_couponoff),
            ("setcomm", self.cmd_setcomm),
            ("broadcast", self.cmd_broadcast),
            # tele acc
            ("addacc", self.cmd_addacc),
            ("delacc", self.cmd_delacc),
            ("delacc_force", self.cmd_delacc_force),
            ("listacc", self.cmd_listacc),
            ("setrate", self.cmd_setrate),
        ]
        for name, handler in cmds:
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        return app

    def _bot_thread_main(self):
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            self._bot_loop = loop
            self._app = self._build_application()
            self.log("Telegram bot starting...")

            async def runner():
                await self._app.initialize()
                await self._app.start()
                try:
                    me = await self._app.bot.get_me()
                    self._bot_username = me.username
                    self.log(f"Bot: @{self._bot_username}")
                except Exception:
                    pass
                await self._app.updater.start_polling(drop_pending_updates=True)
                # Chạy MB Bank poll trong cùng event loop
                asyncio.ensure_future(self._mb_bank_poll_loop())
                while not self._stop_event.is_set():
                    await asyncio.sleep(0.5)
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()

            loop.run_until_complete(runner())
            self.log("Bot stopped.")
        except Exception as e:
            self.log(f"Bot thread error: {e}")

    def start(self, cfg: BotConfig):
        self.cfg = cfg
        save_config(self.cfg)
        if not self.cfg.bot_token:
            raise RuntimeError("Chưa nhập BOT TOKEN")
        if int(self.cfg.admin_id or 0) <= 0:
            raise RuntimeError("Chưa nhập ADMIN ID")
        self._stop_event.clear()
        self._bot_thread = threading.Thread(target=self._bot_thread_main, daemon=True)
        self._bot_thread.start()
        self.log("✅ Started.")

    def stop(self):
        self._stop_event.set()
        self.log("⛔ Stopping...")


# =========================================================
# GUI
# =========================================================
COLOR_PALETTE = {
    "bg_main":  "#0a0e14",
    "card_bg":  "#151b23",
    "card_sec": "#1c242f",
    "primary":  "#00a3ff",
    "action":   "#ff5e23",
    "success":  "#26d07c",
    "danger":   "#ff3b3b",
    "text_p":   "#ffffff",
    "text_s":   "#8b949e",
    "border":   "#30363d",
}


class AppGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x900")
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=COLOR_PALETTE["bg_main"])
        init_db()
        self.log_queue: queue.Queue = queue.Queue()
        self.runner = BotRunner(self.log_queue)
        self.cfg = self.runner.cfg
        self._build_ui()
        self.after(200, self._poll_log_queue)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            self, corner_radius=24,
            label_text="⚡ Cấu hình Hệ thống",
            label_font=ctk.CTkFont(size=22, weight="bold"),
            fg_color=COLOR_PALETTE["card_bg"],
            label_fg_color=COLOR_PALETTE["primary"],
            label_text_color="#ffffff",
        )
        right = ctk.CTkFrame(self, corner_radius=24, fg_color=COLOR_PALETTE["card_bg"], border_width=1, border_color=COLOR_PALETTE["border"])
        left.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        right.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # ── Section 1: Bot Core ──
        sec1 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec1.grid(row=1, column=0, padx=15, pady=15, sticky="ew")
        sec1.grid_columnconfigure(1, weight=1)

        fields_sec1 = [
            ("👑 Admin ID", "ent_admin", str(self.cfg.admin_id or "")),
            ("🧾 Log Group", "ent_log_group", str(self.cfg.log_group_id or "")),
            ("🤖 Bot Token", "ent_token", self.cfg.bot_token or ""),
        ]
        for i, (label, attr, val) in enumerate(fields_sec1):
            ctk.CTkLabel(sec1, text=label, text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=i, column=0, padx=15, pady=12, sticky="w")
            ent = ctk.CTkEntry(sec1, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
            ent.grid(row=i, column=1, padx=15, pady=12, sticky="ew")
            ent.insert(0, val)
            setattr(self, attr, ent)

        # Video Start
        ctk.CTkLabel(sec1, text="🎬 Video Start", text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=15, pady=12, sticky="w")
        vid_row = ctk.CTkFrame(sec1, fg_color="transparent")
        vid_row.grid(row=3, column=1, padx=15, pady=12, sticky="ew")
        vid_row.grid_columnconfigure(0, weight=1)
        self.ent_video = ctk.CTkEntry(vid_row, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_video.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.ent_video.insert(0, self.cfg.start_video_path or "")
        ctk.CTkButton(vid_row, text="📁", width=50, corner_radius=14, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["primary"], command=self.pick_video).grid(row=0, column=1)

        # ── Section 2: VietQR / Bank Info ──
        sec2 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec2.grid(row=2, column=0, padx=15, pady=15, sticky="ew")
        sec2.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sec2, text="🏦 VietQR / Bank Info", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold", size=14)).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 5), sticky="w")

        ctk.CTkLabel(sec2, text="🏦 Ngân hàng", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=15, pady=10, sticky="w")
        self.cmb_vietqr_bank = ctk.CTkOptionMenu(
            sec2, values=[x[0] for x in VIETQR_BANKS],
            corner_radius=14, fg_color=COLOR_PALETTE["action"],
            button_color=COLOR_PALETTE["action"], button_hover_color="#e04a1b",
            command=self.on_vietqr_bank_changed,
        )
        self.cmb_vietqr_bank.grid(row=1, column=1, padx=15, pady=10, sticky="ew")
        self.cmb_vietqr_bank.set(self.cfg.vietqr_bank_name or "Vietcombank")

        bank_fields = [
            ("💳 Số tài khoản", "ent_vietqr_stk", self.cfg.vietqr_stk or ""),
            ("👤 Tên chủ TK", "ent_vietqr_ctk", self.cfg.vietqr_ctk or ""),
            ("🧩 Template QR", "ent_vietqr_tpl", self.cfg.vietqr_template or DEFAULT_VIETQR_TEMPLATE),
        ]
        for i, (label, attr, val) in enumerate(bank_fields, start=2):
            ctk.CTkLabel(sec2, text=label, text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=i, column=0, padx=15, pady=10, sticky="w")
            ent = ctk.CTkEntry(sec2, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
            ent.grid(row=i, column=1, padx=15, pady=10, sticky="ew")
            ent.insert(0, val)
            setattr(self, attr, ent)

        ctk.CTkLabel(
            sec2, text="ℹ️ Nội dung CK user sẽ là: Napid <user_id>",
            text_color=COLOR_PALETTE["text_s"], font=ctk.CTkFont(slant="italic", size=11)
        ).grid(row=5, column=0, columnspan=2, padx=15, pady=(0, 12), sticky="w")

        # ── Section 3: MB Bank AutoPay ──
        sec3 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec3.grid(row=3, column=0, padx=15, pady=15, sticky="ew")
        sec3.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sec3, text="🏦 MB Bank AutoPay (miễn phí)", text_color="#26d07c", font=ctk.CTkFont(weight="bold", size=14)).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 5), sticky="w")

        mb_fields = [
            ("👤 MB Username", "ent_mb_user", self.cfg.mb_username or "", False),
            ("🔑 MB Password", "ent_mb_pass", self.cfg.mb_password or "", True),
            ("💳 Số TK theo dõi", "ent_mb_acc", self.cfg.mb_account_no or "", False),
            ("⏱ Poll (giây)", "ent_poll", str(self.cfg.poll_interval or 10), False),
        ]
        for i, (label, attr, val, is_pass) in enumerate(mb_fields, start=1):
            ctk.CTkLabel(sec3, text=label, text_color="#26d07c", font=ctk.CTkFont(weight="bold")).grid(row=i, column=0, padx=15, pady=10, sticky="w")
            ent = ctk.CTkEntry(
                sec3,
                corner_radius=14,
                fg_color=COLOR_PALETTE["bg_main"],
                border_color=COLOR_PALETTE["border"],
                height=40,
                show="•" if is_pass else "",
            )
            ent.grid(row=i, column=1, padx=15, pady=10, sticky="ew")
            ent.insert(0, val)
            setattr(self, attr, ent)

        ctk.CTkLabel(
            sec3,
            text="ℹ️ Dùng tài khoản MB Bank của chủ bot. Số TK để trống = tự lấy STK đầu tiên.",
            text_color=COLOR_PALETTE["text_s"], font=ctk.CTkFont(slant="italic", size=11), wraplength=320,
        ).grid(row=5, column=0, columnspan=2, padx=15, pady=(0, 12), sticky="w")

        # ── Buttons ──
        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.grid(row=4, column=0, padx=25, pady=(20, 30), sticky="ew")
        btns.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(btns, text="💾 LƯU CONFIG", height=40, corner_radius=12, fg_color=COLOR_PALETTE["success"], hover_color="#1ea060", font=ctk.CTkFont(size=13, weight="bold"), command=self.save_cfg_ui).grid(row=0, column=0, padx=8, sticky="ew")
        ctk.CTkButton(btns, text="🚀 START BOT", height=40, corner_radius=12, fg_color=COLOR_PALETTE["primary"], hover_color="#008ce9", font=ctk.CTkFont(size=13, weight="bold"), command=self.start_clicked).grid(row=0, column=1, padx=8, sticky="ew")
        ctk.CTkButton(btns, text="🛑 STOP BOT", height=40, corner_radius=12, fg_color=COLOR_PALETTE["danger"], hover_color="#d62e2e", font=ctk.CTkFont(size=13, weight="bold"), command=self.stop_clicked).grid(row=0, column=2, padx=8, sticky="ew")

        # ── Right: Logs ──
        ctk.CTkLabel(right, text="📟 LOG HỆ THỐNG", font=ctk.CTkFont(size=20, weight="bold"), text_color=COLOR_PALETTE["primary"]).grid(row=0, column=0, padx=20, pady=(20, 5), sticky="w")
        ctk.CTkLabel(right, text="by trthaodev - coderent.one", font=ctk.CTkFont(size=12, slant="italic"), text_color=COLOR_PALETTE["text_s"]).grid(row=0, column=0, padx=20, pady=(20, 5), sticky="e")

        self.txt_log = ctk.CTkTextbox(right, corner_radius=22, border_width=1, border_color=COLOR_PALETTE["border"], fg_color=COLOR_PALETTE["card_sec"], text_color="#e6edf3", font=ctk.CTkFont(size=13))
        self.txt_log.grid(row=1, column=0, padx=20, pady=15, sticky="nsew")

        hint = (
            "✅ Bot bán hàng + Acc Telegram\n"
            "─────────────────────────────\n"
            "Admin (sau /checkadmin):\n"
            "• /addacc PHONE | PASS | 2FA | NOTE | GIA\n"
            "• /delacc ACC_ID  →  xóa acc\n"
            "• /listacc         →  xem acc available\n"
            "• /setrate 25500   →  tỉ giá USD\n"
            "• /addcat, /addprod, /coupon, /broadcast...\n"
            "• Admin Panel inline: nút 🛠 trong menu\n"
            "─────────────────────────────\n"
            "Nạp tiền: ND chuyển khoản = 'Napid <user_id>'\n"
            "Bank poll tự cộng tiền sau mỗi poll interval.\n"
        )
        self.txt_log.insert("1.0", hint)

    def pick_video(self):
        path = filedialog.askopenfilename(title="Chọn video", filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi"), ("All", "*.*")])
        if path:
            self.ent_video.delete(0, "end")
            self.ent_video.insert(0, path)

    def on_vietqr_bank_changed(self, bank_name: str):
        bank_id = dict(VIETQR_BANKS).get(bank_name, "vietcombank")
        self.cfg.vietqr_bank_name = bank_name
        self.cfg.vietqr_bank_id = bank_id

    def read_cfg_from_ui(self) -> BotConfig:
        cfg = BotConfig()
        cfg.admin_id = safe_int(self.ent_admin.get(), 0)
        cfg.log_group_id = safe_int(self.ent_log_group.get(), 0)
        cfg.bot_token = self.ent_token.get().strip()
        cfg.start_video_path = self.ent_video.get().strip()
        bank_name = self.cmb_vietqr_bank.get().strip()
        cfg.vietqr_bank_name = bank_name
        cfg.vietqr_bank_id = dict(VIETQR_BANKS).get(bank_name, "mbbank")
        cfg.vietqr_stk = self.ent_vietqr_stk.get().strip()
        cfg.vietqr_ctk = self.ent_vietqr_ctk.get().strip()
        cfg.vietqr_template = self.ent_vietqr_tpl.get().strip() or DEFAULT_VIETQR_TEMPLATE
        cfg.mb_username = self.ent_mb_user.get().strip()
        cfg.mb_password = self.ent_mb_pass.get().strip()
        cfg.mb_account_no = self.ent_mb_acc.get().strip()
        cfg.poll_interval = max(5, safe_int(self.ent_poll.get(), 10))
        return cfg

    def save_cfg_ui(self):
        self.cfg = self.read_cfg_from_ui()
        save_config(self.cfg)
        self.runner.cfg = self.cfg
        self._append_log("✅ Đã lưu cấu hình.")

    def start_clicked(self):
        try:
            self.cfg = self.read_cfg_from_ui()
            save_config(self.cfg)
            self.runner.start(self.cfg)
            self._append_log("▶️ START OK.")
        except Exception as e:
            messagebox.showerror("Lỗi Start", str(e))

    def stop_clicked(self):
        try:
            self.runner.stop()
            self._append_log("⛔ Đã gửi tín hiệu STOP.")
        except Exception as e:
            messagebox.showerror("Lỗi Stop", str(e))

    def _append_log(self, line: str):
        self.txt_log.insert("end", f"\n{line}")
        self.txt_log.see("end")

    def _poll_log_queue(self):
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._poll_log_queue)


# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    init_db()
    app = AppGUI()
    app.mainloop()
