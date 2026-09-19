#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TELE SHOP BOT — VPS EDITION v2 (Reply Keyboard)
===============================================
- Toàn bộ chức năng thao tác bằng NÚT TRÊN BÀN PHÍM (Reply Keyboard),
  không dùng nút inline dưới chat.
- Menu chính: 📦 Chuyên mục | 📱 Mua Acc Telegram | 💳 Nạp tiền |
  🧾 Lịch sử mua | 🤝 Tiếp thị | 💰 Số dư | 🛠 Admin Panel (admin)
- Nội dung chuyển khoản: "Napid <idtelegram>" của user (giống bản gốc).
- Mua Acc Telegram: chọn "🎟 Mua gói acc" (10/20/50/100...) hoặc "📱 Mua acc lẻ".
- Nạp tiền: 🏦 Bank (VietQR) / 💵 USDT (BEP20) / 💎 Gram (TON) -> user bấm
  "✅ Tôi đã chuyển tiền" -> bot gửi yêu cầu vào chat ADMIN để duyệt.
- Sau khi mua acc: bot hiện nút "🔑 Nhận OTP" dưới mỗi acc (100%).
  User bấm nút (hoặc /layotp <sdt>, hoặc /layotpsll <sdt1> <sdt2> ... cho đơn SLL,
  tối đa 10 SĐT/lần) -> bot gửi yêu cầu OTP tới chat ADMIN kèm
  tên người mua (tên Telegram) + ID Telegram + SĐT acc đã mua.
  Admin chỉ cần REPLY tin nhắn yêu cầu bằng mã OTP (mọi ngôn ngữ đều hiểu,
  hoặc /guiotp <id> <ma>) -> bot tự động gửi mã OTP cho khách.
- Nhập acc kèm MẬT KHẨU / 2FA (tuỳ chọn) — reply danh sách bằng /addsll:
     0912345678 | matkhau123 | 2FAKEY
     0987654321 | matkhau456
  Bật/tắt gửi kèm MK + 2FA cho khách: /setaccinfo on|off
  Admin gửi OTP kèm MK/2FA: reply "123456 | mk | 2fa" hoặc /guiotp 12 123456 | mk | 2fa
- Phân loại kho: Acc Việt 🇻🇳 / Acc Ngoại 🌍 (giá + gói riêng từng loại).
- Toàn bộ việc lấy OTP diễn ra NGAY TẠI BOT (không cần liên hệ admin thủ công).

Chạy: python3 bot_vps.py
"""

# Tương thích Python 3.9 (cú pháp X | Y trong type hints)
from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import threading
import unicodedata
import urllib.parse
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
    CallbackQueryHandler,
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

# ── Nạp tiền / bán hàng: giá riêng Acc Việt / Acc Ngoại ──
MIN_DEPOSIT_VND = _float("MIN_DEPOSIT_VND", 20000)
DEFAULT_USDT_RATE = _float("USDT_RATE_VND", 26000)   # 1 USDT = ? VND
DEFAULT_TON_RATE = _float("TON_RATE_VND", 80000)     # 1 TON (Gram) = ? VND
PACK_PRICE_VN = _float("PACK_PRICE_VN", 0) or None        # giá lẻ 1 acc VN
PACK_PRICE_NGOAI = _float("PACK_PRICE_NGOAI", 0) or None  # giá lẻ 1 acc Ngoại
DEFAULT_PACK_PRICE = _float("PACK_PRICE_PER_ACC", 50000)  # fallback cũ
PACK_SIZES_VN = [
    int(x)
    for x in re.split(r"[,\s]+", os.getenv("PACK_SIZES_VN", os.getenv("PACK_SIZES", "10,20,50,100")).strip())
    if x.strip().isdigit()
] or [10, 20, 50, 100]
PACK_SIZES_NGOAI = [
    int(x)
    for x in re.split(r"[,\s]+", os.getenv("PACK_SIZES_NGOAI", os.getenv("PACK_SIZES", "10,20,50,100")).strip())
    if x.strip().isdigit()
] or [10, 20, 50, 100]
PACK_SIZES = [{"size": s, "kind": k} for k in ("vn", "ngoai")
              for s in (PACK_SIZES_VN if k == "vn" else PACK_SIZES_NGOAI)]
BASE_PRICE = {
    "vn": PACK_PRICE_VN if PACK_PRICE_VN else DEFAULT_PACK_PRICE,
    "ngoai": PACK_PRICE_NGOAI if PACK_PRICE_NGOAI else DEFAULT_PACK_PRICE,
}
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
            CREATE INDEX IF NOT EXISTS idx_sll_phone ON sll_accs(phone);
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
                size  INTEGER NOT NULL,
                kind  TEXT NOT NULL DEFAULT 'vn',
                price REAL NOT NULL,
                PRIMARY KEY(size, kind)
            );
            CREATE INDEX IF NOT EXISTS idx_packages_size ON packages(size);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS otp_requests(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                phone      TEXT NOT NULL,
                order_id   INTEGER,
                status     TEXT NOT NULL DEFAULT 'pending', -- pending|sent
                otp        TEXT,
                created_at TEXT,
                sent_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_otp_status ON otp_requests(status);
            CREATE TABLE IF NOT EXISTS otp_msg_map(
                admin_id   INTEGER NOT NULL,
                msg_id     INTEGER NOT NULL,
                req_id     INTEGER NOT NULL,
                created_at TEXT,
                PRIMARY KEY(admin_id, msg_id)
            );
            """
        )
        # ─ 1) Migrate: thêm cột còn thiếu (chạy TRƯỚC mọi index trên cột mới) ──
        def _has_col(table: str, col: str) -> bool:
            try:
                return col in [
                    r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()
                ]
            except sqlite3.OperationalError:
                return False

        for table, col, ddl in (
            ("users", "referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER"),
            ("sll_accs", "password", "ALTER TABLE sll_accs ADD COLUMN password TEXT"),
            ("sll_accs", "twofa", "ALTER TABLE sll_accs ADD COLUMN twofa TEXT"),
            ("sll_accs", "kind", "ALTER TABLE sll_accs ADD COLUMN kind TEXT NOT NULL DEFAULT 'vn'"),
            ("orders", "acc_kind", "ALTER TABLE orders ADD COLUMN acc_kind TEXT"),
        ):
            if _has_col(table, col):
                continue
            try:
                con.execute(ddl)
                log.info("Migrate: thêm cột %s.%s", table, col)
            except sqlite3.OperationalError as e:
                log.warning("Migrate %s.%s lỗi: %s", table, col, e)

        # ── 2) packages: DB cũ chỉ có (size, price) -> rebuild (size, kind, price) ──
        if not _has_col("packages", "kind"):
            try:
                con.execute("DROP TABLE IF EXISTS packages_new")
                con.execute(
                    "CREATE TABLE packages_new("
                    "size INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'vn', "
                    "price REAL NOT NULL, PRIMARY KEY(size, kind))"
                )
                con.execute(
                    "INSERT OR IGNORE INTO packages_new(size, kind, price) "
                    "SELECT size, 'vn', price FROM packages"
                )
                con.execute("DROP TABLE packages")
                con.execute("ALTER TABLE packages_new RENAME TO packages")
                log.info("Migrate: packages -> (size, kind) xong")
            except sqlite3.OperationalError as e:
                log.warning("Migrate packages lỗi: %s", e)

        # ── 3) Index tạo SAU migrate (DB cũ không vỡ vì thiếu cột) ──
        for idx in (
            "CREATE INDEX IF NOT EXISTS idx_sll_status ON sll_accs(status)",
            "CREATE INDEX IF NOT EXISTS idx_sll_phone ON sll_accs(phone)",
            "CREATE INDEX IF NOT EXISTS idx_sll_kind_status ON sll_accs(kind, status)",
            "CREATE INDEX IF NOT EXISTS idx_packages_size ON packages(size)",
            "CREATE INDEX IF NOT EXISTS idx_packages_kind ON packages(kind)",
        ):
            try:
                con.execute(idx)
            except sqlite3.OperationalError as e:
                log.warning("Tạo index lỗi (%s): %s", idx, e)

        # ─ 4) Backfill 'kind' cho acc cũ theo đầu số SĐT (chỉ chạy 1 lần) ──
        if not con.execute(
            "SELECT 1 FROM settings WHERE key='migrated_kind_v1'"
        ).fetchone():
            rows = con.execute("SELECT phone FROM sll_accs").fetchall()
            fixes = [(detect_kind(r["phone"]), r["phone"]) for r in rows]
            fixes = [(k, p) for k, p in fixes if k != "vn"]
            if fixes:
                con.executemany("UPDATE sll_accs SET kind=? WHERE phone=?", fixes)
            con.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES('migrated_kind_v1','1')"
            )
            log.info("Migrate: backfill kind cho %d acc", len(fixes))

        _seed_default_packages(con)
        con.close()


def _seed_default_packages(con: sqlite3.Connection) -> None:
    """Seed gói mặc định cho cả 2 loại: giá lẻ từng loại × số lượng."""
    for k in ("vn", "ngoai"):
        base = BASE_PRICE[k]
        sizes = PACK_SIZES_VN if k == "vn" else PACK_SIZES_NGOAI
        for s in sizes:
            con.execute(
                "INSERT OR IGNORE INTO packages(size, kind, price) VALUES(?,?,?)",
                (s, k, base * s),
            )


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


def get_packages(kind: str | None = None) -> list:
    with _DB_LOCK:
        con = db()
        if kind:
            rows = con.execute(
                "SELECT size, kind, price FROM packages WHERE kind=? ORDER BY size",
                (kind,),
            ).fetchall()
        else:
            rows = con.execute("SELECT size, kind, price FROM packages ORDER BY kind, size").fetchall()
        con.close()
        return [(int(r["size"]), str(r["kind"]), float(r["price"])) for r in rows]


def get_package_price(size: int, kind: str):
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT price FROM packages WHERE size=? AND kind=?", (size, kind)
        ).fetchone()
        con.close()
        return float(row["price"]) if row else None


def set_package(size: int, price: float, kind: str = "vn") -> None:
    kind = norm_kind(kind) or "vn"
    with _DB_LOCK:
        con = db()
        con.execute(
            "INSERT INTO packages(size, kind, price) VALUES(?,?,?) "
            "ON CONFLICT(size, kind) DO UPDATE SET price=excluded.price",
            (size, kind, price),
        )
        con.close()


def stock_count(kind: str | None = None) -> int:
    with _DB_LOCK:
        con = db()
        if kind:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM sll_accs WHERE status='available' AND kind=?",
                (kind,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM sll_accs WHERE status='available'"
            ).fetchone()
        con.close()
        return int(row["c"])


KINDS = ("vn", "ngoai")
KIND_NAME = {"vn": "🇻🇳 Acc Việt", "ngoai": "🌍 Acc Ngoại"}
# Alias để admin gõ cho nhanh: /addsll vn ... | /addsll ngoai|nn|qt ...
KIND_ALIAS = {
    "vn": "vn", "viet": "vn", "vietnam": "vn", "việt": "vn",
    "ngoai": "ngoai", "ngoại": "ngoai", "nn": "ngoai",
    "qt": "ngoai", "quocte": "ngoai", "quốc": "ngoai", "us": "ngoai",
}
VN_PREFIXES = (
    "84", "03", "05", "07", "08", "09", "01",
    "243", "242", "244", "245", "246", "247", "248", "249",
    "282", "283", "284", "285", "286", "287", "288", "289",
    "203", "204", "205", "206", "207", "208", "209",
    "213", "214", "215", "216", "217", "218", "219",
    "223", "224", "225", "226", "227", "228", "229",
    "233", "234", "235", "236", "237", "238", "239",
    "253", "254", "255", "256", "257", "258", "259",
    "263", "264", "265", "266", "267", "268", "269",
    "273", "274", "275", "276", "277", "278", "279",
    "293", "294", "295", "296", "297", "298", "299",
    "343", "342", "344", "345", "346", "347", "348", "349",
    "352", "353", "354", "355", "356", "357", "358", "359",
    "362", "363", "364", "365", "366", "367", "368", "369",
    "372", "373", "374", "375", "376", "377", "378", "379",
    "382", "383", "384", "385", "386", "387", "388", "389",
    "392", "393", "394", "395", "396", "397", "398", "399",
    "523", "522", "524", "525", "526", "527", "528", "529",
    "532", "533", "534", "535", "536", "537", "538", "539",
    "562", "563", "564", "565", "566", "567", "568", "569",
    "582", "583", "584", "585", "586", "587", "588", "589",
    "592", "593", "594", "595", "596", "597", "598", "599",
    "702", "703", "704", "705", "706", "707", "708", "709",
    "712", "713", "714", "715", "716", "717", "718", "719",
    "722", "723", "724", "725", "726", "727", "728", "729",
    "732", "733", "734", "735", "736", "737", "738", "739",
    "762", "763", "764", "765", "766", "767", "768", "769",
    "772", "773", "774", "775", "776", "777", "778", "779",
    "782", "783", "784", "785", "786", "787", "788", "789",
    "792", "793", "794", "795", "796", "797", "798", "799",
    "812", "813", "814", "815", "816", "817", "818", "819",
    "822", "823", "824", "825", "826", "827", "828", "829",
    "832", "833", "834", "835", "836", "837", "838", "839",
    "852", "853", "854", "855", "856", "857", "858", "859",
    "862", "863", "864", "865", "866", "867", "868", "869",
    "882", "883", "884", "885", "886", "887", "888", "889",
    "902", "903", "904", "905", "906", "907", "908", "909",
    "912", "913", "914", "915", "916", "917", "918", "919",
    "922", "923", "924", "925", "926", "927", "928", "929",
    "932", "933", "934", "935", "936", "937", "938", "939",
    "941", "942", "944", "945", "946", "947", "948", "949",
    "962", "963", "964", "965", "966", "967", "968", "969",
)


def norm_kind(x) -> str | None:
    if x is None:
        return None
    return KIND_ALIAS.get(str(x).strip().lower())


def detect_kind(phone: str) -> str:
    """Tự đoán loại acc từ SĐT: số VN (+84 / 0xxx...) -> 'vn', còn lại -> 'ngoai'."""
    p = (phone or "").strip().lstrip("+")
    for pre in VN_PREFIXES:
        if p.startswith(pre):
            return "vn"
    return "ngoai"


def kind_of_order(order_row) -> str:
    k = norm_kind(order_row["kind"] if order_row else None)
    if k:
        return k
    return "vn"


def parse_phones(text: str) -> list[str]:
    out, seen = [], set()
    for tok in re.split(r"[\s,;]+", (text or "").strip()):
        tok = tok.strip().lstrip("+")
        if re.fullmatch(r"\d{8,15}", tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def parse_acc_lines(text: str) -> list[dict]:
    """Parse danh sách acc — chấp nhận CẢ 2 định dạng:

    1) Chỉ SĐT (cách nhau bằng dấu cách / phẩy / xuống dòng):
           /addsll vn 0912345678 0987654321
    2) Có kèm MK và 2FA — TUỲ CHỌN (mỗi acc 1 dòng, ngăn bằng '|'):
           0912345678 | matkhau123 | 2FAKEY
           0987654321 | matkhau456

    Trả về [{'phone','password','twofa'}]; bỏ dòng không có SĐT hợp lệ / trùng.
    """
    out, seen = [], set()
    normalized = normalize_text(text or "")
    for raw in normalized.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            phone = (parts[0] if parts else "").lstrip("+")
            pw = parts[1] if len(parts) > 1 else ""
            t2 = parts[2] if len(parts) > 2 and parts[2] else None
            if not re.fullmatch(r"\d{8,15}", phone) or phone in seen:
                continue
            seen.add(phone)
            out.append({"phone": phone, "password": pw or "", "twofa": t2})
        else:
            for p in parse_phones(line):
                if p in seen:
                    continue
                seen.add(p)
                out.append({"phone": p, "password": "", "twofa": None})
    return out


def add_sll(phones: list[str], kind: str | None = None) -> tuple[int, int]:
    """Thêm acc theo loại. Mỗi phần tử có thể là SĐT (str) hoặc dict
    {'phone','password','twofa'}. Nếu kind=None -> tự đoán từng SĐT (VN/ngoại)."""
    added = dup = 0
    with _DB_LOCK:
        con = db()
        for item in phones:
            if isinstance(item, dict):
                p = item.get("phone", "")
                pw = item.get("password") or ""
                t2 = item.get("twofa")
            else:
                p, pw, t2 = item, "", None
            k = kind or detect_kind(p)
            try:
                con.execute(
                    "INSERT INTO sll_accs(phone, kind, password, twofa) VALUES(?,?,?,?)",
                    (p, k, pw, t2),
                )
                added += 1
            except sqlite3.IntegrityError:
                dup += 1
        con.close()
    return added, dup


def show_acc_info() -> bool:
    """Có gửi kèm MK + 2FA cho khách không (setting /setaccinfo > .env SHOW_ACC_INFO)."""
    v = get_setting("show_acc_info")
    if v is None:
        return str(os.getenv("SHOW_ACC_INFO", "1")).strip() not in (
            "0", "off", "false", "no", "",
        )
    return str(v).strip() not in ("0", "off", "false", "no", "")


def acc_info_map(phones: list) -> dict:
    """Lấy MK/2FA của nhiều SĐT trong 1 query (nhanh cho đơn gói lớn)."""
    if not phones:
        return {}
    marks = ",".join("?" * len(phones))
    with _DB_LOCK:
        con = db()
        rows = con.execute(
            f"SELECT phone, password, twofa FROM sll_accs WHERE phone IN ({marks})",
            list(phones),
        ).fetchall()
        con.close()
    return {r["phone"]: r for r in rows}


def set_acc_info(phone: str, password: str | None = None, twofa: str | None = None) -> None:
    """Lưu MK / 2FA mà admin gửi kèm (chỉ ghi khi có giá trị)."""
    sets, args = [], []
    if password:
        sets.append("password=?")
        args.append(password)
    if twofa:
        sets.append("twofa=?")
        args.append(twofa)
    if not sets:
        return
    args.append(phone)
    with _DB_LOCK:
        con = db()
        con.execute(f"UPDATE sll_accs SET {', '.join(sets)} WHERE phone=?", args)
        con.close()


def format_acc(phone: str, info_map: dict | None = None) -> str:
    """Dòng giao acc:  sdt | mk | 2fa  (mk/2fa chỉ thêm khi có + tính năng đang bật)."""
    if info_map is None:
        info_map = acc_info_map([phone])
    row = info_map.get(phone)
    pw = (row["password"] if row and row["password"] else "") if show_acc_info() else ""
    t2 = (row["twofa"] if row and row["twofa"] else "") if show_acc_info() else ""
    out = f"<code>{h(phone)}</code>"
    if pw:
        out += f" | <code>{h(pw)}</code>"
    if t2:
        out += f" | <code>{h(t2)}</code>"
    return out


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


def buy_pack(user_id: int, size: int, price: float, aff_percent: float = 0.0,
             acc_kind: str = "vn"):
    """Mua gói/acc lẻ theo loại: trừ tiền + lock N acc + hoa hồng trong 1 transaction.
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
                    "SELECT phone FROM sll_accs WHERE status='available' AND kind=? "
                    "ORDER BY id LIMIT ?",
                    (acc_kind, size),
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
                "INSERT INTO orders(user_id, kind, acc_kind, qty, price, status, phones, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (user_id, "PACK" if size > 1 else "SINGLE", acc_kind, size, price, "PAID",
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
# OTP — user ấn nút xin OTP, admin gửi OTP, bot chuyển cho khách
# ─────────────────────────────────────────────
def user_owns_phone(user_id: int, phone: str) -> bool:
    """Acc này có thuộc user không (đã mua)."""
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT 1 FROM sll_accs WHERE phone=? AND status='sold' AND sold_to=?",
            (phone, user_id),
        ).fetchone()
        con.close()
        return row is not None


def get_open_otp_request(user_id: int, phone: str):
    """Yêu cầu OTP đang chờ xử lý (tránh user spam)."""
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT * FROM otp_requests WHERE user_id=? AND phone=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id, phone),
        ).fetchone()
        con.close()
        return row


def get_latest_pending_by_user(user_id: int):
    """Yêu cầu OTP đang chờ mới nhất của 1 user.

    Dùng khi admin gửi /guiotp với ID Telegram của khách
    thay vì ID yêu cầu -> bot tự tìm yêu cầu mới nhất, không báo lỗi.
    """
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT * FROM otp_requests WHERE user_id=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        con.close()
        return row


def create_otp_request(user_id: int, phone: str, order_id: int | None) -> int:
    with _DB_LOCK:
        con = db()
        cur = con.execute(
            "INSERT INTO otp_requests(user_id, phone, order_id, status, created_at) "
            "VALUES(?,?,?,?,?)",
            (user_id, phone, order_id, "pending", now_str()),
        )
        rid = int(cur.lastrowid)
        con.close()
    return rid


def get_otp_request(rid: int):
    with _DB_LOCK:
        con = db()
        row = con.execute("SELECT * FROM otp_requests WHERE id=?", (rid,)).fetchone()
        con.close()
        return row


def map_otp_msg(admin_id: int, msg_id: int, req_id: int) -> None:
    with _DB_LOCK:
        con = db()
        con.execute(
            "INSERT INTO otp_msg_map(admin_id, msg_id, req_id, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(admin_id, msg_id) DO UPDATE SET req_id=excluded.req_id",
            (admin_id, msg_id, req_id, now_str()),
        )
        con.close()


def lookup_otp_msg(admin_id: int, msg_id: int):
    """Tìm yêu cầu OTP từ tin nhắn admin đang reply."""
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT r.* FROM otp_msg_map m JOIN otp_requests r ON r.id=m.req_id "
            "WHERE m.admin_id=? AND m.msg_id=?",
            (admin_id, msg_id),
        ).fetchone()
        con.close()
        return row


def fulfill_otp_request(rid: int, otp: str):
    """Đánh dấu đã gửi OTP. Trả row nếu lần đầu (tránh gửi trùng)."""
    with _DB_LOCK:
        con = db()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM otp_requests WHERE id=? AND status='pending'", (rid,)
            ).fetchone()
            if not row:
                con.execute("ROLLBACK")
                return None
            con.execute(
                "UPDATE otp_requests SET status='sent', otp=?, sent_at=? WHERE id=?",
                (otp, now_str(), rid),
            )
            con.execute("COMMIT")
            return row
        finally:
            con.close()


def pending_otp_requests(limit: int = 20) -> list:
    with _DB_LOCK:
        con = db()
        rows = con.execute(
            "SELECT * FROM otp_requests WHERE status='pending' ORDER BY id DESC LIMIT ?",
            (limit,),
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
LBL_KIND_VN = "🇻🇳 Acc Việt"
LBL_KIND_NGOAI = "🌍 Acc Ngoại"

# Nạp tiền
LBL_BANK    = "🏦 Ngân hàng (VietQR)"
LBL_USDT    = "💵 USDT (BEP20)"
LBL_TON     = "💎 Gram (TON)"
LBL_DEP_OK  = "✅ Tôi đã chuyển tiền"
LBL_DEP_NO  = "❌ Huỷ nạp"

# Admin panel
LBL_STOCK   = "📦 Kho acc"
LBL_PENDING = "⏳ Nạp chờ duyệt"
LBL_STATS   = "📊 Thống kê"
LBL_OTP     = "🔑 OTP chờ gửi"

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


def packs_kb(kind: str) -> ReplyKeyboardMarkup:
    """Bàn phím gói của 1 loại acc."""
    rows = [
        [KeyboardButton(f"🎟 Gói {KIND_NAME[kind]} {size} acc — {vnd(price)}")]
        for size, kind, price in get_packages(kind)
    ]
    rows.append([KeyboardButton(BTN_BACK)])
    return ReplyKeyboardMarkup(rows, **RP)


def kinds_kb() -> ReplyKeyboardMarkup:
    """Bước 1 khi mua: chọn loại Acc Việt / Acc Ngoại."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(LBL_KIND_VN), KeyboardButton(LBL_KIND_NGOAI)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


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
         [KeyboardButton(LBL_OTP), KeyboardButton(LBL_STATS)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def _otp_reply_kb(phones: list) -> ReplyKeyboardMarkup:
    """Bàn phím LẤY OTP dự phòng (fallback khi InlineKeyboard bị lỗi).

    Mỗi nút mã hoá order_id + phone -> callback otp:req để admin gửi từng OTP,
    bot trả lại từng mã theo đúng thứ tự kèm SĐT.
    """
    rows = [
        [KeyboardButton(f"🔑 LẤY OTP {p}")]
        for p in phones[:20]
    ]
    rows.append([KeyboardButton(BTN_BACK)])
    return ReplyKeyboardMarkup(rows, **RP)


def cats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_TELE), KeyboardButton(BTN_DEPOSIT)],
         [KeyboardButton(BTN_BACK)]],
        **RP,
    )


def clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("state", "dep_method", "dep_id", "buy_kind", "buy_size", "buy_price"):
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
        "🔑 Sau khi mua acc, lấy mã OTP ngay tại bot này (có nút lấy OTP dưới đơn).\n\n"
        "👇 Chọn chức năng trên bàn phím bên dưới:",
        reply_markup=main_kb(uid),
        disable_web_page_preview=True,
    )


async def flow_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for k in ("vn", "ngoai"):
        packs = get_packages(k)
        lines.append(
            f"{KIND_NAME[k]}\n"
            f"   • Kho hiện có: <b>{stock_count(k)}</b> acc\n"
            f"   • Acc lẻ: <b>{vnd(single_price(k))}</b>/acc\n"
            f"   • Gói: " + (", ".join(f"{s} acc ({vnd(p)})" for s, _, p in packs) or "chưa có") + "\n"
        )
    await update.effective_message.reply_html(
        "📦 <b>CHUYÊN MỤC SẢN PHẨM</b>\n\n"
        "📱 <b>Acc Telegram SLL</b>\n"
        + "\n".join(lines)
        + "\n👇 Chọn nút bên dưới để xem tiếp:",
        reply_markup=cats_kb(),
    )


def single_price(kind: str = "vn") -> float:
    """Giá acc lẻ riêng từng loại (setting > .env > fallback)."""
    v = get_setting(f"price_single_{kind}") or get_setting("price_single")
    try:
        if v:
            return float(v)
    except Exception:
        pass
    return BASE_PRICE.get(kind, DEFAULT_PACK_PRICE)


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
    context.user_data["state"] = "buy_kind"
    await update.effective_message.reply_html(
        f"📱 <b>MUA ACC TELEGRAM</b>\n\n"
        f"{KIND_NAME['vn']}: <b>{stock_count('vn')}</b> acc — từ {vnd(single_price('vn'))}/acc\n"
        f"{KIND_NAME['ngoai']}: <b>{stock_count('ngoai')}</b> acc — từ {vnd(single_price('ngoai'))}/acc\n\n"
        "🔑 Mua xong lấy mã OTP ngay tại bot này (nút lấy OTP hiện dưới đơn).\n\n"
        "👇 <b>Chọn loại acc muốn mua:</b>",
        reply_markup=kinds_kb(),
    )


async def flow_buy_packs(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    context.user_data["state"] = "buy_pack_select"
    context.user_data["buy_kind"] = kind
    packs = get_packages(kind)
    if not packs:
        await update.effective_message.reply_html(
            f"😔 {KIND_NAME[kind]} hiện chưa có gói nào.",
            reply_markup=kinds_kb(),
        )
        return
    await update.effective_message.reply_html(
        f"🎟 <b>CHỌN GÓI {KIND_NAME[kind].upper()}</b>\n\n👇 Chọn gói muốn mua trên bàn phím:",
        reply_markup=packs_kb(kind),
    )


async def flow_buy_pack_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, size: int, kind: str):
    price = get_package_price(size, kind)
    if price is None:
        await update.effective_message.reply_text("❌ Gói không tồn tại.")
        return
    uid = update.effective_user.id
    bal = get_balance(uid)
    context.user_data["state"] = "buy_confirm"
    context.user_data["buy_kind"] = kind
    context.user_data["buy_size"] = size
    context.user_data["buy_price"] = price
    ok = bal >= price and stock_count(kind) >= size
    txt = (
        f"🧾 <b>ĐƠN MUA — GÓI {size} {KIND_NAME[kind].upper()}</b>\n\n"
        f"💸 Giá: <b>{vnd(price)}</b>\n"
        f"💰 Số dư: {vnd(bal)}\n"
        f"📦 Kho {KIND_NAME[kind]}: {stock_count(kind)} acc\n\n"
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


async def flow_buy_single(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    uid = update.effective_user.id
    price = single_price(kind)
    bal = get_balance(uid)
    context.user_data["state"] = "buy_confirm"
    context.user_data["buy_kind"] = kind
    context.user_data["buy_size"] = 1
    context.user_data["buy_price"] = price
    txt = (
        f"🧾 <b>ĐƠN MUA — 1 {KIND_NAME[kind].upper()} LẺ</b>\n\n"
        f"💸 Giá: <b>{vnd(price)}</b>/acc\n"
        f"💰 Số dư: {vnd(bal)}\n"
        f"📦 Kho {KIND_NAME[kind]}: {stock_count(kind)} acc\n\n"
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
    if stock_count(kind) < 1:
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
    kind = norm_kind(context.user_data.get("buy_kind")) or "vn"
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
    order_id, result = buy_pack(uid, size, price, percent, acc_kind=kind)
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
    info_map = acc_info_map(phones)
    listing = "\n".join(
        f"{i}. {format_acc(p, info_map)}" for i, p in enumerate(phones, 1)
    )
    has_info = any((r["password"] or r["twofa"]) for r in info_map.values())
    title = (
        "📞 <b>DANH SÁCH ACC (SĐT | MK | 2FA):</b>"
        if has_info else "📞 <b>DANH SÁCH ACC:</b>"
    )
    txt = (
        f"✅ <b>MUA THÀNH CÔNG</b>\n\n"
        f"🧾 Đơn: <b>#{order_id}</b> — {size} {KIND_NAME[kind]}\n"
        f"💸 Đã trừ: {vnd(price)}\n"
        f"💰 Số dư còn lại: {vnd(get_balance(uid))}\n\n"
        f"{title}\n{listing}\n\n"
        f"🔑 <b>Lấy OTP ngay tại bot này:</b> bấm nút <b>🔑 Nhận OTP</b> "
        f"ở tin nhắn bên dưới — bot tự xử lý và gửi mã về cho bạn."
    )
    otp_rows = [
        [InlineKeyboardButton(f"🔑 Nhận OTP — {p}", callback_data=f"otp:req:{order_id}:{p}")]
        for p in phones[:20]
    ]
    if len(phones) > 20:
        txt += (
            "\n\n💡 Đơn có nhiều hơn 20 acc: dùng <code>/layotpsll &lt;sdt1&gt; &lt;sdt2&gt; ...</code> "
            "(cách nhau bằng dấu cách, tối đa 10 SĐT/lần) để lấy OTP cho từng acc."
        )
    await update.effective_message.reply_html(txt, disable_web_page_preview=True)
    # Gửi nút OTP ở tin nhắn riêng + try/except: đảm bảo nút hiện 100%
    if otp_rows:
        try:
            btn_msg = await update.effective_message.reply_html(
                "👇 <b>Bấm nút bên dưới để lấy OTP cho từng acc</b> "
                "(bot gửi yêu cầu ngay, mã OTP được gửi lại tự động):",
                reply_markup=InlineKeyboardMarkup(otp_rows),
                disable_web_page_preview=True,
            )
            log.info(
                "Đã gửi %d nút OTP cho user %s (đơn #%s, msg=%s)",
                len(otp_rows), uid, order_id,
                getattr(btn_msg, "message_id", "?"),
            )
        except TelegramError as e:
            log.error("Không gửi được nút OTP inline cho user %s: %s — dùng fallback bàn phím", uid, e)
            await update.effective_message.reply_html(
                "👇 <b>Bấm nút LẤY OTP bên dưới</b> "
                "(bot gửi yêu cầu ngay, mã OTP được gửi lại tự động):",
                reply_markup=_otp_reply_kb(phones[:20]),
                disable_web_page_preview=True,
            )
    # Trả lại bàn phím menu chính
    await update.effective_message.reply_html(
        "🏠 <b>Menu chính</b>", reply_markup=main_kb(uid)
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
            "❌ Không có yêu cầu nạp nào đang chờ. Tạo lại bằng 💳 Nạp tiền.",
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
        f"💳 Kênh: {METHOD_NAME.get(dep['method'], dep['method'])}\n"
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


def get_full_name(user_id: int) -> str:
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT full_name FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        con.close()
        return (row["full_name"] if row and row["full_name"] else "") or ""


# ─────────────────────────────────────────────
# LUỒNG OTP: user xin OTP -> admin reply mã -> bot gửi cho khách
# ─────────────────────────────────────────────
async def notify_admin_otp(bot, rid: int, requester=None):
    """Gửi yêu cầu OTP tới tất cả admin (kèm @nguoimua, ID, SĐT acc).

    - Nếu là mua 1 acc (bấm nút ngay sau khi mua): gửi đủ tham số
      tên người mua (tên Telegram), ID Telegram, SĐT acc đã mua.
    - Nếu là mua SLL (lệnh /layotpsll): mỗi SĐT là 1 yêu cầu riêng —
      admin nhận từng cái lẻ (có SĐT trong tin nhắn), admin gửi từng OTP;
      bot trả lại từng OTP theo đúng thứ tự, kèm SĐT của mã đó.
    """
    req = get_otp_request(rid)
    if not req:
        return
    uname = get_username(req["user_id"])
    full = get_full_name(req["user_id"])
    if requester is not None:
        full = requester.full_name or full
        uname = requester.username or uname
    buyer = f"@{h(uname)}" if uname else "Không có username"
    info = acc_info_map([req["phone"]]).get(req["phone"])
    extra = ""
    if info:
        if info["password"]:
            extra += f"\n🔒 MK đã lưu: <code>{h(info['password'])}</code>"
        if info["twofa"]:
            extra += f"\n🛡 2FA đã lưu: <code>{h(info['twofa'])}</code>"
    txt = (
        f"🔑 <b>YÊU CẦU OTP #{rid}</b>\n\n"
        f"👤 Tên người mua (tên Telegram): <b>{h(full or 'user')}</b> — {buyer}\n"
        f"🆔 ID Telegram: <code>{req['user_id']}</code>\n"
        f"📞 SĐT acc đã mua: <code>{h(req['phone'])}</code>\n"
        f"🧾 Đơn hàng: <b>#{req['order_id']}</b>\n"
        f"🕐 {req['created_at']}{extra}\n\n"
        "👉 <b>Reply (trả lời) chính tin nhắn này bằng mã OTP</b> — bot sẽ tự gửi cho khách.\n"
        f"Hoặc dùng: <code>/guiotp {rid} &lt;ma_otp&gt;</code>\n"
        "💡 Gửi kèm MK/2FA (tuỳ chọn): <code>123456 | mk | 2fa</code>"
    )
    for aid in ADMIN_IDS:
        try:
            m = await bot.send_message(aid, txt, parse_mode=ParseMode.HTML)
            if m:
                map_otp_msg(aid, m.message_id, rid)
        except TelegramError as e:
            log.warning("Không gửi được yêu cầu OTP cho admin %s: %s", aid, e)


async def flow_otp_request(update: Update, context: ContextTypes.DEFAULT_TYPE, phone: str):
    """User (hoặc callback) yêu cầu OTP cho 1 SĐT đã mua."""
    uid = update.effective_user.id
    phone = (phone or "").strip().lstrip("+")
    if not re.fullmatch(r"\d{8,15}", phone):
        await update.effective_message.reply_html(
            "❌ SĐT không hợp lệ. Dùng: <code>/layotp 0912345678</code>\n"
            "Mua SLL nhiều acc: <code>/layotpsll 0912.. 0987.. ...</code>"
        )
        return
    if not user_owns_phone(uid, phone):
        await update.effective_message.reply_html(
            "❌ Bạn chưa mua acc có SĐT này. Kiểm tra lại trong 🧾 Lịch sử mua."
        )
        return
    open_req = get_open_otp_request(uid, phone)
    if open_req:
        await update.effective_message.reply_html(
            f"⏳ Bạn đã gửi yêu cầu OTP cho acc <code>{h(phone)}</code> (yêu cầu #{open_req['id']}).\n"
            "Bot sẽ tự động gửi lại mã OTP cho bạn ngay khi có."
        )
        return
    rid = create_otp_request(uid, phone, None)
    await update.effective_message.reply_html(
        f"✅ <b>Đã gửi yêu cầu OTP #{rid}</b> cho acc <code>{h(phone)}</code>.\n"
        "⏳ Bot sẽ tự động gửi lại mã OTP cho bạn ngay khi có."
    )
    await notify_admin_otp(context.bot, rid, update.effective_user)


async def cmd_layotpsll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/layotpsll <sdt1> <sdt2> ... — lấy OTP hàng loạt (tối đa 10 SĐT/lần).

    Đúng định dạng yêu cầu: mỗi SĐT cách nhau 1 dấu cách, mỗi lần tối đa 10 số.
    Admin nhận từng yêu cầu riêng, gửi SĐT vào chat admin;
    bot trả lại từng OTP cho user theo đúng thứ tự kèm SĐT của mã đó.
    """
    msg = update.effective_message
    uid = update.effective_user.id
    args = list(context.args or [])
    if not args:
        await msg.reply_html(
            "📝 <b>Cách dùng lấy OTP SLL:</b>\n"
            "<code>/layotpsll 0912345678 0987654321 0933111222</code>\n"
            "• Mỗi SĐT cách nhau 1 dấu cách, mỗi lần tối đa <b>10 SĐT</b>.\n"
            "• Chỉ lấy được cho các acc <b>bạn đã mua</b>.\n"
            "• Mua lẻ 1 acc: bấm nút <b>🔑 Nhận OTP</b> dưới đơn mua hoặc dùng <code>/layotp</code>."
        )
        return
    parsed = parse_phones(" ".join(args))
    if not parsed:
        await msg.reply_html(
            "❌ Không tìm thấy SĐT hợp lệ.\n"
            "Ví dụ đúng: <code>/layotpsll 0912345678 0987654321</code>"
        )
        return
    if len(parsed) > 10:
        await msg.reply_html(
            f"❌ Mỗi lần chỉ được tối đa <b>10 SĐT</b> (bạn gửi {len(parsed)} SĐT).\n"
            f"👉 Chia nhỏ ra và gửi lại, ví dụ 10 số/lần."
        )
        return
    ok_rids: list[tuple[int, str]] = []     # (req_id, phone) — giữ đúng thứ tự
    skip_dup: list[str] = []
    skip_own: list[str] = []
    for p in parsed:
        if not user_owns_phone(uid, p):
            skip_own.append(p)
            continue
        if get_open_otp_request(uid, p):
            skip_dup.append(p)
            continue
        rid = create_otp_request(uid, p, None)
        ok_rids.append((rid, p))
    if not ok_rids:
        parts = ["⚠️ Không tạo được yêu cầu OTP nào."]
        if skip_dup:
            parts.append("• Đã gửi từ trước: " + ", ".join(f"<code>{h(x)}</code>" for x in skip_dup))
        if skip_own:
            parts.append("• Bạn chưa mua: " + ", ".join(f"<code>{h(x)}</code>" for x in skip_own))
        await msg.reply_html("\n".join(parts))
        return
    lst = "\n".join(
        f"{i}. <code>{h(p)}</code> (yêu cầu #{rid})" for i, (rid, p) in enumerate(ok_rids, 1)
    )
    await msg.reply_html(
        f"✅ <b>Đã gửi {len(ok_rids)} yêu cầu OTP SLL:</b>\n{lst}\n"
        f"⏳ Bot sẽ trả lại từng OTP cho bạn theo đúng thứ tự kèm SĐT ngay khi có mã."
        + (f"\n\n⚠️ Bỏ qua (đã gửi từ trước): " + ", ".join(f"<code>{h(x)}</code>" for x in skip_dup) if skip_dup else "")
        + (f"\n⚠️ Bỏ qua (bạn chưa mua): " + ", ".join(f"<code>{h(x)}</code>" for x in skip_own) if skip_own else "")
    )
    # Gửi từng yêu cầu tới admin để admin gửi từng OTP
    for rid, _p in ok_rids:
        await notify_admin_otp(context.bot, rid, update.effective_user)


async def deliver_otp_to_client(
    context: ContextTypes.DEFAULT_TYPE,
    req,
    otp: str,
    password: str | None = None,
    twofa: str | None = None,
):
    """Gửi OTP cho khách. Trả True nếu gửi thành công lần đầu.

    - password / twofa: admin gửi KÈM mã OTP (tuỳ chọn) -> bot lưu lại vào acc
      và gửi luôn cho khách.
    - Nếu admin không gửi kèm, bot tự lấy MK/2FA đã lưu của acc đó
      (khi tính năng gửi kèm đang bật bằng /setaccinfo on).
    """
    info = acc_info_map([req["phone"]]).get(req["phone"])
    pw = password or ((info["password"] or "") if info and show_acc_info() else "")
    t2 = twofa or ((info["twofa"] or "") if info and show_acc_info() else "")
    if password or twofa:
        set_acc_info(req["phone"], password, twofa)   # ghi nhớ cho lần sau
    done = fulfill_otp_request(req["id"], otp)
    if not done:
        return False
    body = (
        f"🔑 <b>MÃ OTP CHO ACC</b> <code>{h(req['phone'])}</code>\n\n"
        f"<code>{h(otp)}</code>\n"
    )
    if pw:
        body += f"\n🔒 <b>MK:</b> <code>{h(pw)}</code>"
    if t2:
        body += f"\n🛡 <b>2FA:</b> <code>{h(t2)}</code>"
    body += "\n\n⏳ Mã có hiệu lực ngắn — nhập ngay để đăng nhập nhé!"
    ok = True
    try:
        await context.bot.send_message(req["user_id"], body, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        ok = False
        log.warning("Không gửi được OTP cho user %s: %s", req["user_id"], e)
    return ok


def normalize_text(text: str) -> str:
    """Chuẩn hoá full-width + mọi hệ chữ số (Rập/Thái/Miến/Trung...) về ASCII."""
    t = (text or "").replace("｜", "|")
    out = []
    for ch in t:
        if ch.isdigit() and not ch.isascii():
            try:
                out.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _extract_otp(text: str) -> str:
    """Đọc mã OTP từ tin nhắn admin — KHÔNG giới hạn ngôn ngữ.

    Nhận mọi cách viết: '123456', 'ma otp la 123456', 'OTP: 123456',
    '验证码 123456', 'رمز التحقق 123456', 'mã xác minh là 123456'...
    """
    t = normalize_text(text).strip()
    if not t:
        return ""
    if re.fullmatch(r"[A-Za-z0-9\-]{3,16}", t):
        return t
    tokens = re.findall(r"[A-Za-z0-9\-]{3,16}", t)
    digits = [x for x in tokens if x.isdigit()]
    if digits:
        return digits[-1]
    return tokens[-1] if tokens else t[:16]


def parse_otp_payload(text: str) -> tuple[str, str | None, str | None]:
    """Admin gửi mã OTP, CÓ THỂ kèm MK / 2FA (tuỳ chọn). Trả (otp, mk, 2fa).

    - '123456'                    -> ('123456', None, None)
    - '123456 | mk123 | 2faXYZ'   -> ('123456', 'mk123', '2faXYZ')
    - '123456 | mk123'            -> ('123456', 'mk123', None)
    - 'mã otp là 123456'          -> ('123456', None, None)  (mọi ngôn ngữ)
    """
    t = normalize_text(text).strip()
    if "|" in t:
        parts = [p.strip() for p in t.split("|")]
        otp = _extract_otp(parts[0])
        pw = parts[1] if len(parts) > 1 and parts[1] else None
        t2 = parts[2] if len(parts) > 2 and parts[2] else None
        return otp, pw, t2
    return _extract_otp(t), None, None


async def cb_otp_by_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, phone: str):
    """Chung 1 đường cho nút INLINE (otp:req) và nút ReplyKeyboard (🔑 LẤY OTP).

    Tạo yêu cầu OTP duy nhất, gửi notify đầy đủ tham số về admin.
    """
    uid = update.effective_user.id
    phone = (phone or "").strip().lstrip("+")
    if not user_owns_phone(uid, phone):
        await update.effective_message.reply_html(
            "❌ Acc này không thuộc bạn. Kiểm tra lại trong 🧾 Lịch sử mua."
        )
        return
    open_req = get_open_otp_request(uid, phone)
    if open_req:
        await update.effective_message.reply_html(
            f"⏳ Đã gửi yêu cầu OTP #{open_req['id']} cho acc <code>{h(phone)}</code> — bot sẽ gửi mã lại ngay khi có nhé!"
        )
        return
    order_id = None
    with _DB_LOCK:
        con = db()
        row = con.execute(
            "SELECT order_id FROM sll_accs WHERE phone=? AND sold_to=? ORDER BY order_id DESC LIMIT 1",
            (phone, uid),
        ).fetchone()
        con.close()
        if row:
            order_id = row["order_id"]
    rid = create_otp_request(uid, phone, order_id)
    await update.effective_message.reply_html(
        f"✅ <b>Đã gửi yêu cầu OTP #{rid}</b> cho acc <code>{h(phone)}</code>.\n"
        "⏳ Bot sẽ gửi lại mã OTP cho bạn ngay khi có."
    )
    await notify_admin_otp(context.bot, rid, update.effective_user)


async def cb_otp_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User bấm nút  Nhận OTP dưới tin nhắn mua thành công."""
    q = update.callback_query
    uid = q.from_user.id
    parts = q.data.split(":")          # otp:req:<order_id>:<phone>
    try:
        phone = parts[3]
    except (IndexError, ValueError):
        await q.answer("❌ Yêu cầu không hợp lệ.", show_alert=True)
        return
    phone = (phone or "").strip().lstrip("+")
    if not user_owns_phone(uid, phone):
        await q.answer("❌ Acc này không thuộc bạn.", show_alert=True)
        return
    open_req = get_open_otp_request(uid, phone)
    if open_req:
        await q.answer(
            f"⏳ Đã gửi yêu cầu OTP #{open_req['id']} — bot sẽ gửi mã lại ngay khi có nhé!",
            show_alert=True,
        )
        return
    order_id = None
    try:
        order_id = int(parts[2])
    except (IndexError, ValueError):
        pass
    rid = create_otp_request(uid, phone, order_id)
    await q.answer("✅ Đã gửi yêu cầu OTP!", show_alert=True)
    await notify_admin_otp(context.bot, rid, q.from_user)
    try:
        await q.message.reply_html(
            f"🔑 <b>YÊU CẦU OTP #{rid}</b>\n"
            f"📞 Acc: <code>{h(phone)}</code>\n"
            "✅ Bot sẽ tự động gửi mã OTP cho bạn ngay khi có."
        )
    except TelegramError:
        pass


async def cmd_layotp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/layotp <sdt> — user yêu cầu OTP cho acc đã mua."""
    if not context.args:
        await update.effective_message.reply_html(
            "📝 Dùng: <code>/layotp 0912345678</code>\n"
            "hoặc bấm nút <b>🔑 Nhận OTP</b> dưới tin nhắn mua thành công."
        )
        return
    await flow_otp_request(update, context, context.args[0])


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
        f"📦 Kho acc: {stock_count('vn')} Việt / {stock_count('ngoai')} Ngoại\n\n"
        "📋 <b>Lệnh nhanh:</b>\n"
        "• <code>/addsll [vn|ngoai] &lt;sdt...&gt;</code> — nhập acc (tự đoán nếu thiếu loại)\n"
        "• <code>/addsll [vn|ngoai]</code> khi <b>reply</b> danh sách — nhập kèm MK/2FA,\n"
        "   mỗi dòng: <code>sdt | mk | 2fa</code> (mk, 2fa <b>tuỳ chọn</b>)\n"
        "• <code>/setaccinfo on|off</code> — bật/tắt gửi kèm MK + 2FA cho khách\n"
        "• <code>/delsll &lt;sdt&gt;</code> — xoá acc\n"
        "• <code>/stock [vn|ngoai]</code> — xem kho từng loại\n"
        "• <code>/duyet &lt;id&gt;</code> / <code>/tuchoi &lt;id&gt;</code> — duyệt nạp\n"
        "• <code>/otplist</code> — YC OTP đang chờ (hoặc nút 🔑 OTP chờ gửi)\n"
        "• <code>/guiotp &lt;id&gt; &lt;ma&gt;</code> — gửi OTP cho khách\n"
        "   kèm MK/2FA: <code>/guiotp &lt;id&gt; &lt;ma&gt; | mk | 2fa</code>\n"
        "   (hoặc chỉ cần <b>reply</b> tin nhắn YC OTP — không giới hạn ngôn ngữ)\n"
        "• <code>/setpack &lt;vn|ngoai&gt; &lt;size&gt; &lt;giá&gt;</code> — giá gói từng loại\n"
        "• <code>/setprice &lt;vn|ngoai&gt; &lt;giá&gt;</code> — giá acc lẻ từng loại\n"
        "• <code>/setrate &lt;usdt|ton&gt; &lt;vnd&gt;</code> — tỉ giá\n"
        "• <code>/setaff &lt;%&gt;</code> — % hoa hồng tiếp thị\n"
        "• <code>/addbal &lt;uid&gt; &lt;tiền&gt;</code> — cộng tiền user",
        reply_markup=admin_kb(),
    )


async def flow_admin_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(
        f"📦 <b>Kho acc khả dụng:</b> <b>{stock_count('vn')}</b> Việt / "
        f"<b>{stock_count('ngoai')}</b> Ngoại",
        reply_markup=admin_kb(),
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
    """Thêm SĐT theo loại: /addsll [vn|ngoai] <sdt...>.

    - /addsll vn 0912... 0987...      -> ép toàn bộ là Acc Việt
    - /addsll ngoai 1415... 4477...   -> ép toàn bộ là Acc Ngoại (alias: nn, qt)
    - /addsll 0912... 1415...         -> tự đoán từng SĐT (VN/ngoại)
    SĐT: xuống dòng / cách / phẩy đều được; hoặc reply tin nhắn chứa danh sách.
    """
    msg = update.effective_message
    args = list(context.args or [])
    kind = norm_kind(args[0]) if args else None
    if kind and len(args) > 1:
        args = args[1:]   # bỏ token loại ở đầu
    elif kind and len(args) == 1:
        args = []
    text = " ".join(args)
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or ""
    accs = parse_acc_lines(text)
    if not accs:
        await msg.reply_html(
            "📝 <b>Cách dùng /addsll:</b>\n"
            "🔹 <b>Chỉ SĐT</b> (tự đoán Việt/Ngoại):\n"
            "<code>/addsll vn 0912345678 0987654321</code> — ép Acc Việt\n"
            "<code>/addsll ngoai 14155551234 4477009001</code> — ép Acc Ngoại\n"
            "<code>/addsll 0912345678 14155551234</code> — tự đoán từng SĐT\n"
            "🔹 <b>Kèm MK / 2FA (tu chọn)</b> — mỗi acc 1 dòng, ngăn bằng <code>|</code>:\n"
            "<code>0912345678 | matkhau123 | 2FAKEY</code>\n"
            "<code>0987654321 | matkhau456</code>\n"
            "→ gõ danh sách vào 1 tin nhắn rồi <b>reply</b> tin đó bằng "
            "<code>/addsll [vn|ngoai]</code>\n"
            " SĐT cách nhau bằng dấu cách, xuống dòng hoặc phẩy."
        )
        return
    added, dup = add_sll(accs, kind)
    with_info = sum(1 for a in accs if a["password"] or a["twofa"])
    if kind:
        detail = f" ({KIND_NAME[kind]})"
    else:
        c_vn = sum(1 for a in accs if detect_kind(a["phone"]) == "vn")
        c_nn = len(accs) - c_vn
        detail = f" (tự đoán: {c_vn} Việt / {c_nn} Ngoại)"
    await msg.reply_html(
        f"✅ <b>Đã thêm {added} acc SLL</b>{detail}"
        + (f" (bỏ qua {dup} trùng)" if dup else "")
        + (f"\n🔐 Acc có kèm MK/2FA: <b>{with_info}</b>" if with_info else "")
        + f"\n📦 Kho hiện tại: <b>{stock_count('vn')}</b> Việt / <b>{stock_count('ngoai')}</b> Ngoại"
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
    k = norm_kind(context.args[0]) if context.args else None
    if k:
        await update.effective_message.reply_html(
            f"📦 Kho <b>{KIND_NAME[k]}</b> khả dụng: <b>{stock_count(k)}</b>"
        )
    else:
        await update.effective_message.reply_html(
            f"📦 Kho khả dụng: <b>{stock_count('vn')}</b> Việt / "
            f"<b>{stock_count('ngoai')}</b> Ngoại\n"
            "Xem riêng: <code>/stock vn</code> hoặc <code>/stock ngoai</code>"
        )


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
    """/setpack <vn|ngoai> <size> <gia_vnd> — giá gói riêng từng loại.
    vd: /setpack vn 10 400000 | /setpack ngoai 10 700000
    (Tương thích cũ: /setpack <size> <gia> -> mặc định Acc Việt)"""
    if len(context.args) == 3 and norm_kind(context.args[0]):
        kind = norm_kind(context.args[0])
        size_s, price_s = context.args[1], context.args[2]
    elif len(context.args) == 2 and context.args[0].isdigit():
        kind, size_s, price_s = "vn", context.args[0], context.args[1]
    else:
        await update.effective_message.reply_text(
            "Dùng: /setpack <vn|ngoai> <size> <gia_vnd>\n"
            "vd: /setpack vn 10 400000 | /setpack ngoai 10 700000"
        )
        return
    if not size_s.isdigit():
        await update.effective_message.reply_text("❌ Size không hợp lệ.")
        return
    price = _parse_amount("bank", price_s)
    if price is None or price <= 0:
        await update.effective_message.reply_text("❌ Giá không hợp lệ.")
        return
    set_package(int(size_s), price, kind)
    await update.effective_message.reply_html(
        f"✅ Đã đặt gói <b>{size_s} {KIND_NAME[kind]} = {vnd(price)}</b>"
    )


@admin_only
async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setprice <vn|ngoai> <gia_1_acc> — giá acc lẻ riêng từng loại.
    vd: /setprice vn 50000 | /setprice ngoai 80000
    (Tương thích cũ: /setprice <gia> -> mặc định Acc Việt)"""
    if len(context.args) == 2 and norm_kind(context.args[0]):
        kind, price_s = norm_kind(context.args[0]), context.args[1]
    elif len(context.args) == 1:
        kind, price_s = "vn", context.args[0]
    else:
        await update.effective_message.reply_text(
            "Dùng: /setprice <vn|ngoai> <gia_1_acc>\n"
            "vd: /setprice vn 50000 | /setprice ngoai 80000"
        )
        return
    price = _parse_amount("bank", price_s)
    if price is None or price <= 0:
        await update.effective_message.reply_text("❌ Giá không hợp lệ.")
        return
    set_setting(f"price_single_{kind}", price)
    await update.effective_message.reply_html(
        f"✅ Giá acc lẻ <b>{KIND_NAME[kind]}: {vnd(price)}</b>/acc"
    )


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
async def cmd_setaccinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setaccinfo on|off — có gửi kèm MK + 2FA cho khách không (tuỳ chọn).

    • ON : khi giao acc / gửi OTP, bot gửi kèm MK + 2FA của acc (nếu có).
    • OFF: chỉ gửi SĐT (và mã OTP).
    """
    if not context.args:
        await update.effective_message.reply_html(
            "🔐 <b>Gửi kèm MK + 2FA cho khách</b>\n\n"
            f"Trạng thái hiện tại: <b>{'ON' if show_acc_info() else 'OFF'}</b>\n\n"
            "Dùng: <code>/setaccinfo on</code> hoặc <code>/setaccinfo off</code>\n"
            "• <b>on</b>: giao acc + OTP kèm <b>MK</b> và <b>2FA</b> (nếu acc có).\n"
            "• <b>off</b>: chỉ gửi SĐT và mã OTP.\n"
            "👉 Nhập acc kèm MK/2FA: reply danh sách bằng <code>/addsll [vn|ngoai]</code>\n"
            "   định dạng mỗi dòng: <code>sdt | mk | 2fa</code> (2fa tuỳ chọn)."
        )
        return
    val = context.args[0].strip().lower()
    if val in ("1", "on", "true", "yes", "bat", "bật"):
        on = True
    elif val in ("0", "off", "false", "no", "tat", "tắt"):
        on = False
    else:
        await update.effective_message.reply_text("❌ Dùng: /setaccinfo on hoặc /setaccinfo off")
        return
    set_setting("show_acc_info", "1" if on else "0")
    await update.effective_message.reply_html(
        f"✅ Đã {'BẬT' if on else 'TẮT'} gửi kèm MK/2FA cho khách."
    )


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


@admin_only
async def cmd_guiotp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/guiotp <id_yeu_cau> <ma_otp> — admin gửi OTP cho khách.

    Có thể gửi kèm MK / 2FA (tuỳ chọn): /guiotp 12 123456 | mk | 2fa
    """
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.effective_message.reply_html(
            "📝 Dùng: <code>/guiotp &lt;id_yeu_cau&gt; &lt;ma_otp&gt;</code>\n"
            "vd: <code>/guiotp 12 123456</code>\n"
            "💡 Gửi kèm MK/2FA (tuỳ chọn): <code>/guiotp 12 123456 | mk | 2fa</code>\n"
            "👉 Hoặc chỉ cần <b>reply</b> tin nhắn yêu cầu OTP bằng mã OTP."
        )
        return
    key = int(context.args[0])
    otp, pw, t2 = parse_otp_payload(" ".join(context.args[1:]))
    req = get_otp_request(key)
    if not req:
        # Admin hay nhầm: gửi ID Telegram của khách thay vì ID yêu cầu.
        # Tự tìm yêu cầu đang chờ mới nhất của user đó.
        req = get_latest_pending_by_user(key)
        if not req:
            await update.effective_message.reply_text(
                f"❌ Không tìm thấy yêu cầu OTP #{key}.\n"
                f"👉 Xem danh sách đang chờ: /otplist"
            )
            return
        log.info("Admin gửi /guiotp với user_id=%s -> tự tìm yêu cầu mới nhất #%s", key, req["id"])
    if req["status"] != "pending":
        await update.effective_message.reply_html(
            f"⚠️ Yêu cầu #{req['id']} đã xử lý trước đó"
            + (f" (mã: <code>{h(req['otp'])}</code>)" if req["otp"] else "") + "."
        )
        return
    if not otp:
        await update.effective_message.reply_text(
            "❌ Không đọc được mã OTP. Dùng: /guiotp <id> <ma_otp>"
        )
        return
    ok = await deliver_otp_to_client(context, req, otp, pw, t2)
    if ok:
        await update.effective_message.reply_html(
            f"✅ Đã gửi OTP cho khách <b>@{h(get_username(req['user_id']))}</b> "
            f"(user {req['user_id']}, acc <code>{h(req['phone'])}</code>)."
            + (f"\n🔒 Kèm MK{'+ 2FA' if t2 else ''}." if (pw or t2) else "")
        )
    else:
        await update.effective_message.reply_html(
            f"⚠️ Không gửi được cho user {req['user_id']} (có thể đã chặn bot)."
        )


@admin_only
async def cmd_otplist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/otplist — danh sách yêu cầu OTP đang chờ."""
    rows = pending_otp_requests()
    if not rows:
        await update.effective_message.reply_text("✅ Không có yêu cầu OTP nào đang chờ.")
        return
    lines = []
    for r in rows:
        uname = get_username(r["user_id"]) or "no_username"
        lines.append(
            f"#{r['id']} — @{h(uname)} (id {r['user_id']}) — "
            f"acc <code>{h(r['phone'])}</code> — đơn #{r['order_id']} — {r['created_at']}"
        )
    await update.effective_message.reply_html(
        "🔑 <b>YÊU CẦU OTP ĐANG CHỜ</b>\n\n" + "\n".join(lines)
        + "\n\n👉 Gửi mã: <code>/guiotp &lt;id&gt; &lt;ma_otp&gt;</code>"
    )


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

    # ── 0) ADMIN REPLY tin nhắn yêu cầu OTP = gửi OTP cho khách ──
    if is_admin(uid) and msg.reply_to_message:
        req = lookup_otp_msg(uid, msg.reply_to_message.message_id)
        if req:
            if req["status"] != "pending":
                await msg.reply_html(
                    f"⚠️ Yêu cầu OTP #{req['id']} đã được gửi trước đó rồi."
                )
                return
            code, pw, t2 = parse_otp_payload(text)
            if not code:
                await msg.reply_html(
                    "❌ Không đọc được mã OTP. Reply lại với mã (vd: <code>123456</code>).\n"
                    "💡 Gửi kèm MK/2FA (tu chọn): <code>123456 | mk | 2fa</code>"
                )
                return
            ok = await deliver_otp_to_client(context, req, code, pw, t2)
            if ok:
                extra = ""
                if pw or t2:
                    extra = f"\n🔒 Kèm: {'MK ' if pw else ''}{'+ 2FA' if t2 else ''}".strip()
                await msg.reply_html(
                    f"✅ Đã gửi OTP <code>{h(code)}</code> cho khách "
                    f"<b>@{h(get_username(req['user_id']))}</b> "
                    f"(user {req['user_id']}, acc <code>{h(req['phone'])}</code>).{extra}"
                )
            else:
                await msg.reply_html(
                    f"⚠️ Không gửi được cho user {req['user_id']} (có thể đã chặn bot)."
                )
            return

    # ── 0) Nút LẤY OTP dự phòng (ReplyKeyboard): mỗi nút = 1 SĐT đã mua ──
    m_otp = re.fullmatch(r"🔑 LẤY OTP (\+?\d{8,15})", text)
    if m_otp:
        await cb_otp_by_phone(update, context, m_otp.group(1))
        return
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

    # ── 2) Luồng MUA ACC: chọn loại -> chọn gói/lẻ -> xác nhận ──
    if state == "buy_kind":
        if text == LBL_KIND_VN:
            context.user_data["buy_kind"] = "vn"
            await msg.reply_html(
                f"✅ Đã chọn <b>{KIND_NAME['vn']}</b> — kho: <b>{stock_count('vn')}</b> acc.\n"
                "👇 Bạn muốn mua gói hay mua lẻ?",
                reply_markup=buy_kb(),
            )
            context.user_data["state"] = "buy_menu"
            return
        if text == LBL_KIND_NGOAI:
            context.user_data["buy_kind"] = "ngoai"
            await msg.reply_html(
                f"✅ Đã chọn <b>{KIND_NAME['ngoai']}</b> — kho: <b>{stock_count('ngoai')}</b> acc.\n"
                "👇 Bạn muốn mua gói hay mua lẻ?",
                reply_markup=buy_kb(),
            )
            context.user_data["state"] = "buy_menu"
            return
        await msg.reply_html("👇 Chọn loại acc trên bàn phím nhé:")
        return

    if state == "buy_menu":
        kind = norm_kind(context.user_data.get("buy_kind")) or "vn"
        context.user_data["buy_kind"] = kind
        if text == LBL_PACKS:
            await flow_buy_packs(update, context, kind)
            return
        if text == LBL_SINGLE:
            await flow_buy_single(update, context, kind)
            return
        await msg.reply_html("👇 Chọn <b>🎟 Mua gói acc</b> hoặc <b>📱 Mua acc lẻ</b>:")
        return

    if state == "buy_pack_select":
        kind = norm_kind(context.user_data.get("buy_kind")) or "vn"
        m = re.fullmatch(r"🎟 Gói (.+?) (\d+) acc — ([\d.,]+)đ", text)
        if m:
            await flow_buy_pack_detail(update, context, int(m.group(2)), kind)
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
        if text == LBL_OTP:
            await cmd_otplist(update, context)
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
def build_app() -> Application:
    """Tạo Application + đăng ký toàn bộ handler (tách ra để test được)."""
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
    app.add_handler(CommandHandler("setaccinfo", cmd_setaccinfo), group=0)
    app.add_handler(CommandHandler("addbal", cmd_addbal), group=0)
    app.add_handler(CommandHandler("stats", cmd_stats), group=0)
    app.add_handler(CommandHandler("layotp", cmd_layotp), group=0)
    app.add_handler(CommandHandler("layotpsll", cmd_layotpsll), group=0)
    app.add_handler(CommandHandler("guiotp", cmd_guiotp), group=0)
    app.add_handler(CommandHandler("otplist", cmd_otplist), group=0)

    # Callback: user bấm nút 🔑 Nhận OTP dưới tin nhắn mua thành công
    app.add_handler(
        CallbackQueryHandler(cb_otp_request, pattern=r"^otp:req:\d+:\d+$"), group=0
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message), group=1)
    app.add_error_handler(on_error)
    return app


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Thiếu BOT_TOKEN trong file .env")
    if not ADMIN_IDS:
        raise SystemExit("❌ Thiếu ADMIN_IDS trong file .env")

    init_db()
    app = build_app()

    log.info("🚀 Bot đang chạy (Reply Keyboard mode)... Admin: %s", sorted(ADMIN_IDS))
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
        poll_interval=0.5,
    )


if __name__ == "__main__":
    main()
