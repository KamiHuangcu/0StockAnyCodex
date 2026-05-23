import tkinter as tk
from tkinter import messagebox
import sqlite3
import time
import random
import csv
import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


def get_app_dir():
    """
    讓程式不管是用 python 執行，或是 PyInstaller 打包成 exe，
    都固定讀取/輸出在程式所在資料夾。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()

DB_NAME = os.path.join(APP_DIR, "stock_scanner.db")
LIST_FILE = os.path.join(APP_DIR, "stock_list.txt")
SNAPSHOT_AFTERMARKET_FILE = os.path.join(APP_DIR, "snapshot_aftermarket.csv")
SNAPSHOT_INTRADAY_FILE = os.path.join(APP_DIR, "snapshot_intraday.csv")
AI_TRAINING_FILE = os.path.join(APP_DIR, "ai_training.csv")
CHIP_CACHE_DIR = os.path.join(APP_DIR, "chip_cache")

REQUEST_TIMEOUT = 15
MAX_RETRY = 3

KEEP_DAYS = {
    "1m": 5,
    "5m": 20,
    "30m": 60,
    "1d": 1095,
    "1wk": 1825,
}

YAHOO_CONFIG = {
    "1m": {"range": "5d", "interval": "1m"},
    "5m": {"range": "60d", "interval": "5m"},
    "30m": {"range": "60d", "interval": "30m"},
    "1d": {"range": "3y", "interval": "1d"},
    "1wk": {"range": "5y", "interval": "1wk"},
}

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TWSE_INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_INSTITUTIONAL_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
TPEX_MARGIN_URL = "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StockScanner/2.0"
SSL_CONTEXT = ssl._create_unverified_context()

# 不再寫死自動加入，全部交給 stock_list.txt 控制。
MARKET_INDEXES = []

INTERVALS = ["1m", "5m", "30m", "1d", "1wk"]

TIMEFRAME_METADATA = {
    "1m": {"interval_minutes": 1, "bars_per_day": 270, "sort_order": 1},
    "5m": {"interval_minutes": 5, "bars_per_day": 54, "sort_order": 2},
    "30m": {"interval_minutes": 30, "bars_per_day": 9, "sort_order": 3},
    "1d": {"interval_minutes": 1440, "bars_per_day": 1, "sort_order": 4},
    "1wk": {"interval_minutes": 10080, "bars_per_day": 1, "sort_order": 5},
}

FEATURE_COLUMNS = [
    ("change_pct", "REAL"),
    ("gap_pct", "REAL"),
    ("upper_tail_ratio", "REAL"),
    ("lower_tail_ratio", "REAL"),
    ("tail_ratio", "REAL"),
    ("day_range_pos", "REAL"),
    ("ma5", "REAL"),
    ("ma10", "REAL"),
    ("ma20", "REAL"),
    ("ma60", "REAL"),
    ("ma120", "REAL"),
    ("ma_slope_20", "REAL"),
    ("bias20", "REAL"),
    ("rsi14", "REAL"),
    ("k9", "REAL"),
    ("d9", "REAL"),
    ("j9", "REAL"),
    ("dif", "REAL"),
    ("macd", "REAL"),
    ("osc", "REAL"),
    ("williams_r14", "REAL"),
    ("volume_ma5", "REAL"),
    ("volume_ratio", "REAL"),
    ("vol_std_score", "REAL"),
    ("vwap", "REAL"),
    ("obv", "REAL"),
    ("mfi14", "REAL"),
    ("atr14", "REAL"),
    ("adx14", "REAL"),
    ("plus_di", "REAL"),
    ("minus_di", "REAL"),
    ("bb_upper", "REAL"),
    ("bb_middle", "REAL"),
    ("bb_lower", "REAL"),
    ("bb_width", "REAL"),
    ("price_loc_bb", "REAL"),
    ("high_52w", "REAL"),
    ("low_52w", "REAL"),
    ("dist_high_52w_pct", "REAL"),
    ("dist_low_52w_pct", "REAL"),
    ("beta20", "REAL"),
    ("corr20", "REAL"),
    ("relative_strength_pct", "REAL"),
    ("stock_index_ratio", "REAL"),
    ("daily_score", "REAL"),
    ("short_term_score", "REAL"),
    ("reason", "TEXT"),
]

AI_LABEL_COLUMNS = [
    ("future_1d_return", "REAL"),
    ("future_3d_return", "REAL"),
    ("max_upside_5d", "REAL"),
    ("drawdown_5d", "REAL"),
    ("buy_signal", "INTEGER"),
    ("entry_price", "REAL"),
    ("ai_signal_score", "REAL"),
    ("label_ready", "INTEGER"),
]

stop_scan = False


def ensure_columns(cur, table_name, columns):
    cur.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cur.fetchall()}

    for name, column_type in columns:
        if name not in existing_columns:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {column_type}")


def connect_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-32000")
    return conn


def init_db(conn=None):
    own_conn = conn is None
    conn = conn or connect_db()
    cur = conn.cursor()

    feature_defs = ",\n            ".join(
        f"{name} {column_type}" for name, column_type in FEATURE_COLUMNS
    )
    label_defs = ",\n            ".join(
        f"{name} {column_type}" for name, column_type in AI_LABEL_COLUMNS
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY,
            base_code TEXT NOT NULL,
            name TEXT,
            item_type TEXT,
            market TEXT,
            first_seen TEXT,
            updated_at TEXT
        )
    """)
    ensure_columns(cur, "symbols", [
        ("base_code", "TEXT"),
        ("name", "TEXT"),
        ("item_type", "TEXT"),
        ("market", "TEXT"),
        ("first_seen", "TEXT"),
        ("updated_at", "TEXT"),
    ])

    cur.execute("""
        CREATE TABLE IF NOT EXISTS k_bars (
            symbol TEXT NOT NULL,
            interval_type TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            volume REAL,
            fetch_time TEXT,
            PRIMARY KEY (symbol, interval_type, bar_time)
        )
    """)
    ensure_columns(cur, "k_bars", [
        ("open_price", "REAL"),
        ("high_price", "REAL"),
        ("low_price", "REAL"),
        ("close_price", "REAL"),
        ("volume", "REAL"),
        ("fetch_time", "TEXT"),
    ])
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_k_bars_symbol_interval_time
        ON k_bars (symbol, interval_type, bar_time DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_k_bars_interval_time
        ON k_bars (interval_type, bar_time DESC)
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS k_bar_features (
            symbol TEXT NOT NULL,
            interval_type TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            {feature_defs},
            updated_at TEXT,
            PRIMARY KEY (symbol, interval_type, bar_time)
        )
    """)
    ensure_columns(cur, "k_bar_features", FEATURE_COLUMNS + [("updated_at", "TEXT")])
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_k_bar_features_interval_time
        ON k_bar_features (interval_type, bar_time DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_k_bar_features_score
        ON k_bar_features (interval_type, daily_score DESC)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chip_daily (
            symbol TEXT NOT NULL,
            base_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            foreign_buy_sell REAL DEFAULT 0,
            investment_trust_buy_sell REAL DEFAULT 0,
            dealer_buy_sell REAL DEFAULT 0,
            institutional_total_buy_sell REAL DEFAULT 0,
            foreign_buy_sell_3d REAL DEFAULT 0,
            investment_trust_buy_sell_3d REAL DEFAULT 0,
            dealer_buy_sell_3d REAL DEFAULT 0,
            institutional_total_buy_sell_3d REAL DEFAULT 0,
            margin_change REAL DEFAULT 0,
            short_change REAL DEFAULT 0,
            margin_balance REAL DEFAULT 0,
            short_balance REAL DEFAULT 0,
            margin_short_ratio REAL DEFAULT 0,
            bullish_chip_score REAL DEFAULT 0,
            bearish_chip_score REAL DEFAULT 0,
            bullish_chip_reason TEXT,
            bearish_chip_reason TEXT,
            chip_data_source TEXT,
            chip_data_status TEXT,
            chip_data_date TEXT,
            updated_at TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    ensure_columns(cur, "chip_daily", [
        ("base_code", "TEXT"),
        ("foreign_buy_sell", "REAL DEFAULT 0"),
        ("investment_trust_buy_sell", "REAL DEFAULT 0"),
        ("dealer_buy_sell", "REAL DEFAULT 0"),
        ("institutional_total_buy_sell", "REAL DEFAULT 0"),
        ("foreign_buy_sell_3d", "REAL DEFAULT 0"),
        ("investment_trust_buy_sell_3d", "REAL DEFAULT 0"),
        ("dealer_buy_sell_3d", "REAL DEFAULT 0"),
        ("institutional_total_buy_sell_3d", "REAL DEFAULT 0"),
        ("margin_change", "REAL DEFAULT 0"),
        ("short_change", "REAL DEFAULT 0"),
        ("margin_balance", "REAL DEFAULT 0"),
        ("short_balance", "REAL DEFAULT 0"),
        ("margin_short_ratio", "REAL DEFAULT 0"),
        ("bullish_chip_score", "REAL DEFAULT 0"),
        ("bearish_chip_score", "REAL DEFAULT 0"),
        ("bullish_chip_reason", "TEXT"),
        ("bearish_chip_reason", "TEXT"),
        ("chip_data_source", "TEXT"),
        ("chip_data_status", "TEXT"),
        ("chip_data_date", "TEXT"),
        ("updated_at", "TEXT"),
    ])
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chip_daily_date
        ON chip_daily (trade_date DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chip_daily_symbol_date
        ON chip_daily (symbol, trade_date DESC)
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS ai_labels (
            symbol TEXT NOT NULL,
            interval_type TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            {label_defs},
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (symbol, interval_type, bar_time)
        )
    """)
    ensure_columns(cur, "ai_labels", AI_LABEL_COLUMNS + [
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ])
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_labels_buy_signal
        ON ai_labels (buy_signal, interval_type, bar_time)
    """)

    cur.execute("DROP VIEW IF EXISTS v_ai_features_intraday")
    cur.execute("DROP VIEW IF EXISTS v_ai_features_aftermarket")
    cur.execute("""
        CREATE VIEW v_ai_features_intraday AS
        SELECT
            f.symbol, f.interval_type, f.bar_time,
            s.base_code, s.name, s.item_type, s.market,
            b.open_price, b.high_price, b.low_price, b.close_price, b.volume,
            f.change_pct, f.gap_pct, f.upper_tail_ratio, f.lower_tail_ratio,
            f.tail_ratio, f.day_range_pos,
            f.ma5, f.ma10, f.ma20, f.ma60, f.ma120, f.ma_slope_20, f.bias20,
            f.rsi14, f.k9, f.d9, f.j9, f.dif, f.macd, f.osc, f.williams_r14,
            f.volume_ma5, f.volume_ratio, f.vol_std_score, f.vwap, f.obv, f.mfi14,
            f.atr14, f.adx14, f.plus_di, f.minus_di,
            f.bb_upper, f.bb_middle, f.bb_lower, f.bb_width, f.price_loc_bb,
            f.high_52w, f.low_52w, f.dist_high_52w_pct, f.dist_low_52w_pct,
            f.beta20, f.corr20, f.relative_strength_pct, f.stock_index_ratio,
            f.daily_score, f.short_term_score, f.reason,
            c.foreign_buy_sell, c.investment_trust_buy_sell,
            c.dealer_buy_sell, c.institutional_total_buy_sell,
            c.foreign_buy_sell_3d, c.investment_trust_buy_sell_3d,
            c.dealer_buy_sell_3d, c.institutional_total_buy_sell_3d,
            c.margin_change, c.short_change, c.margin_balance, c.short_balance,
            c.margin_short_ratio, c.bullish_chip_score, c.bearish_chip_score,
            c.chip_data_status, c.chip_data_date,
            l.future_1d_return, l.future_3d_return,
            l.max_upside_5d, l.drawdown_5d,
            l.buy_signal, l.entry_price, l.ai_signal_score, l.label_ready
        FROM k_bar_features f
        JOIN k_bars b
          ON b.symbol = f.symbol
         AND b.interval_type = f.interval_type
         AND b.bar_time = f.bar_time
        JOIN symbols s ON s.symbol = f.symbol
        LEFT JOIN chip_daily c
          ON c.symbol = f.symbol
         AND c.trade_date = (
             SELECT MAX(trade_date) FROM chip_daily c2
             WHERE c2.symbol = f.symbol
               AND c2.trade_date < date(f.bar_time)
         )
        LEFT JOIN ai_labels l
          ON l.symbol = f.symbol
         AND l.interval_type = f.interval_type
         AND l.bar_time = f.bar_time
    """)
    cur.execute("""
        CREATE VIEW v_ai_features_aftermarket AS
        SELECT
            f.symbol, f.interval_type, f.bar_time,
            s.base_code, s.name, s.item_type, s.market,
            b.open_price, b.high_price, b.low_price, b.close_price, b.volume,
            f.change_pct, f.gap_pct, f.upper_tail_ratio, f.lower_tail_ratio,
            f.tail_ratio, f.day_range_pos,
            f.ma5, f.ma10, f.ma20, f.ma60, f.ma120, f.ma_slope_20, f.bias20,
            f.rsi14, f.k9, f.d9, f.j9, f.dif, f.macd, f.osc, f.williams_r14,
            f.volume_ma5, f.volume_ratio, f.vol_std_score, f.vwap, f.obv, f.mfi14,
            f.atr14, f.adx14, f.plus_di, f.minus_di,
            f.bb_upper, f.bb_middle, f.bb_lower, f.bb_width, f.price_loc_bb,
            f.high_52w, f.low_52w, f.dist_high_52w_pct, f.dist_low_52w_pct,
            f.beta20, f.corr20, f.relative_strength_pct, f.stock_index_ratio,
            f.daily_score, f.short_term_score, f.reason,
            c.foreign_buy_sell, c.investment_trust_buy_sell,
            c.dealer_buy_sell, c.institutional_total_buy_sell,
            c.foreign_buy_sell_3d, c.investment_trust_buy_sell_3d,
            c.dealer_buy_sell_3d, c.institutional_total_buy_sell_3d,
            c.margin_change, c.short_change, c.margin_balance, c.short_balance,
            c.margin_short_ratio, c.bullish_chip_score, c.bearish_chip_score,
            c.chip_data_status, c.chip_data_date,
            l.future_1d_return, l.future_3d_return,
            l.max_upside_5d, l.drawdown_5d,
            l.buy_signal, l.entry_price, l.ai_signal_score, l.label_ready
        FROM k_bar_features f
        JOIN k_bars b
          ON b.symbol = f.symbol
         AND b.interval_type = f.interval_type
         AND b.bar_time = f.bar_time
        JOIN symbols s ON s.symbol = f.symbol
        LEFT JOIN chip_daily c
          ON c.symbol = f.symbol
         AND c.trade_date = date(f.bar_time)
        LEFT JOIN ai_labels l
          ON l.symbol = f.symbol
         AND l.interval_type = f.interval_type
         AND l.bar_time = f.bar_time
        WHERE f.interval_type IN ('1d', '1wk')
    """)

    conn.commit()

    if own_conn:
        conn.close()


def log(message):
    result_text.insert(tk.END, message + "\n")
    result_text.see(tk.END)
    root.update()


def warn(message):
    log(f"WARNING: {message}")


def is_missing(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return True
    return False


def safe_float(value, default=None):
    if is_missing(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def safe_text(value, default=""):
    if is_missing(value):
        return default
    return str(value).strip()


def gt(value, threshold):
    return value is not None and value > threshold


def ge(value, threshold):
    return value is not None and value >= threshold


def le(value, threshold):
    return value is not None and value <= threshold


def lt(value, threshold):
    return value is not None and value < threshold


def between(value, low, high):
    return value is not None and low <= value <= high


def parse_trade_date(value):
    text = safe_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def normalize_code(code):
    code = code.strip().upper()

    if code.startswith("^"):
        return code

    # 已經是 Yahoo 完整代號，例如 SEC.TW、2330.TW、8299.TWO
    if code.endswith(".TW") or code.endswith(".TWO"):
        return code

    return code


def read_stock_list():
    stocks = []

    with open(LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split(",")]

            if not parts or not parts[0]:
                continue

            base_code = normalize_code(parts[0])
            name = parts[1] if len(parts) > 1 else ""
            item_type = parts[2] if len(parts) > 2 else "stock"

            stocks.append((base_code, name, item_type))

    for symbol, name, item_type in MARKET_INDEXES:
        stocks.append((symbol, name, item_type))

    return stocks


def fetch_yahoo_chart(symbol, range_value, interval):
    url = YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=".^"))
    params = {
        "range": range_value,
        "interval": interval,
    }
    query_url = f"{url}?{urllib.parse.urlencode(params)}"

    last_error = None

    for attempt in range(1, MAX_RETRY + 1):
        try:
            request = urllib.request.Request(
                query_url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=SSL_CONTEXT) as response:
                data = json.loads(response.read().decode("utf-8"))

            if data.get("chart", {}).get("error"):
                raise Exception(data["chart"]["error"])

            result = data["chart"]["result"]

            if not result:
                raise Exception("遠端服務回傳 result 為空")

            return result[0]

        except Exception as e:
            last_error = e

            if attempt < MAX_RETRY:
                wait_sec = attempt * 2
                log(f"  Retry {attempt}/{MAX_RETRY}, waiting {wait_sec} sec...")
                time.sleep(wait_sec)

    raise Exception(last_error)


def resolve_symbol(base_code):
    # 指數或已指定 Yahoo 完整代號，直接驗證
    if base_code.startswith("^") or base_code.endswith(".TW") or base_code.endswith(".TWO"):
        fetch_yahoo_chart(base_code, "1d", "1d")
        return base_code

    candidates = [
        base_code + ".TW",
        base_code + ".TWO",
    ]

    last_error = None

    for symbol in candidates:
        try:
            fetch_yahoo_chart(symbol, "1d", "1d")
            return symbol
        except Exception as e:
            last_error = e
            log(f"  {symbol} 無法取得，改試下一個節點類型...")

    raise Exception(f"無法判斷節點類型：{base_code}，最後錯誤：{last_error}")


def market_index_for_symbol(symbol):
    if symbol.endswith(".TWO"):
        return "^TWOII"
    return "^TWII"


def market_for_symbol(symbol):
    if symbol.startswith("^"):
        return "index"
    if symbol.endswith(".TWO"):
        return "TWO"
    if symbol.endswith(".TW"):
        return "TW"
    return ""


def strip_yahoo_suffix(symbol):
    text = safe_text(symbol)
    if text.endswith(".TWO"):
        return text[:-4]
    if text.endswith(".TW"):
        return text[:-3]
    return text


def upsert_symbol(cur, symbol, base_code, name, item_type):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO symbols (symbol, base_code, name, item_type, market, first_seen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            base_code = excluded.base_code,
            name = excluded.name,
            item_type = excluded.item_type,
            market = excluded.market,
            updated_at = excluded.updated_at
    """, (
        symbol,
        strip_yahoo_suffix(base_code),
        name,
        item_type,
        market_for_symbol(symbol),
        now,
        now,
    ))


def save_bars(symbol, base_code, name, item_type, interval_type):
    config = YAHOO_CONFIG[interval_type]
    result = fetch_yahoo_chart(symbol, config["range"], config["interval"])

    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    conn = connect_db()
    cur = conn.cursor()
    upsert_symbol(cur, symbol, base_code, name, item_type)

    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS[interval_type])).strftime("%Y-%m-%d %H:%M:%S")
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved_count = 0
    price_rows = []

    for i, ts in enumerate(timestamps):
        close_price = closes[i] if i < len(closes) else None

        if close_price is None:
            continue

        bar_dt = datetime.fromtimestamp(ts)
        bar_time = bar_dt.strftime("%Y-%m-%d %H:%M:%S")

        if bar_time < cutoff:
            continue

        price_rows.append((
            symbol,
            interval_type,
            bar_time,
            opens[i] if i < len(opens) else None,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            close_price,
            volumes[i] if i < len(volumes) else None,
            fetch_time,
        ))

    if price_rows:
        cur.executemany("""
            INSERT INTO k_bars
            (symbol, interval_type, bar_time, open_price, high_price, low_price,
             close_price, volume, fetch_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval_type, bar_time) DO UPDATE SET
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                close_price = excluded.close_price,
                volume = excluded.volume,
                fetch_time = excluded.fetch_time
        """, price_rows)
        saved_count = len(price_rows)

    cur.execute(
        "DELETE FROM k_bars WHERE interval_type = ? AND bar_time < ?",
        (interval_type, cutoff),
    )

    conn.commit()
    conn.close()

    return saved_count


def sma(values, period, index):
    if index + 1 < period:
        return None

    data = values[index - period + 1:index + 1]

    if any(v is None for v in data):
        return None

    return sum(data) / period


def stddev(values):
    if not values or any(v is None for v in values):
        return None

    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)

    return math.sqrt(variance)


def ema_series(values, period):
    result = []
    alpha = 2 / (period + 1)
    ema = None

    for value in values:
        if value is None:
            result.append(None)
            continue

        if ema is None:
            ema = value
        else:
            ema = value * alpha + ema * (1 - alpha)

        result.append(ema)

    return result


def score_row(row):
    score = 0.0
    reasons = []
    close = row.get("close")

    def add(points, reason):
        nonlocal score
        score += points
        reasons.append(reason)

    if close is not None and row.get("ma20") is not None and close > row["ma20"]:
        add(2, "close>ma20")
    if row.get("ma5") and row.get("ma10") and row.get("ma20") and row["ma5"] > row["ma10"] > row["ma20"]:
        add(3, "ma5>ma10>ma20")
    if row.get("ma20") and row.get("ma60") and row["ma20"] > row["ma60"]:
        add(2, "ma20>ma60")
    if row.get("ma60") and row.get("ma120") and row["ma60"] > row["ma120"]:
        add(2, "ma60>ma120")
    if row.get("ma_slope_20") is not None and row["ma_slope_20"] > 0:
        add(2, "ma20 slope up")

    if row.get("rsi14") is not None:
        if 50 <= row["rsi14"] <= 70:
            add(2, "rsi healthy")
        elif 70 < row["rsi14"] <= 80:
            add(1, "rsi strong")
        elif row["rsi14"] > 85:
            add(-2, "rsi overheated")
        elif row["rsi14"] < 40:
            add(-1, "rsi weak")

    if row.get("k9") is not None and row.get("d9") is not None and row["k9"] > row["d9"]:
        add(1, "kd bullish")
    if row.get("osc") is not None and row["osc"] > 0:
        add(2, "macd osc positive")

    if row.get("volume_ratio") is not None:
        if row["volume_ratio"] >= 1.5:
            add(2, "volume expansion")
        elif row["volume_ratio"] >= 1.2:
            add(1, "volume above avg")

    if row.get("adx14") is not None:
        if row["adx14"] >= 25 and (row.get("plus_di") or 0) > (row.get("minus_di") or 0):
            add(3, "strong adx uptrend")
        elif row["adx14"] >= 20 and (row.get("plus_di") or 0) > (row.get("minus_di") or 0):
            add(2, "adx uptrend")

    if row.get("dist_high_52w_pct") is not None:
        if row["dist_high_52w_pct"] >= -3:
            add(2, "near 52w high")
        elif row["dist_high_52w_pct"] >= -10:
            add(1, "within 10pct high")

    if row.get("dist_low_52w_pct") is not None and row["dist_low_52w_pct"] >= 20:
        add(1, "above 52w low")

    if row.get("atr14") is not None and close not in (None, 0):
        atr_pct = row["atr14"] / close * 100
        if atr_pct <= 8:
            add(1, "atr controlled")
        elif atr_pct > 12:
            add(-1, "atr too wide")

    if row.get("gap_pct") is not None and row["gap_pct"] > 7:
        add(-2, "large gap")
    if row.get("upper_tail_ratio") is not None and row["upper_tail_ratio"] > 55:
        add(-1, "large upper tail")

    return score, "; ".join(reasons)


def calculate_beta_corr(stock_returns, index_returns, period, index):
    if index + 1 < period:
        return None, None

    s = stock_returns[index - period + 1:index + 1]
    m = index_returns[index - period + 1:index + 1]

    paired = [(a, b) for a, b in zip(s, m) if a is not None and b is not None]

    if len(paired) < period * 0.7:
        return None, None

    s_vals = [p[0] for p in paired]
    m_vals = [p[1] for p in paired]

    avg_s = sum(s_vals) / len(s_vals)
    avg_m = sum(m_vals) / len(m_vals)

    cov = sum((a - avg_s) * (b - avg_m) for a, b in paired) / len(paired)
    var_m = sum((b - avg_m) ** 2 for b in m_vals) / len(m_vals)
    var_s = sum((a - avg_s) ** 2 for a in s_vals) / len(s_vals)

    beta = None if var_m == 0 else cov / var_m
    corr = None if var_m == 0 or var_s == 0 else cov / math.sqrt(var_s * var_m)

    return beta, corr


def add_indicator_aliases(row):
    k9 = row.get("k9")
    d9 = row.get("d9")
    row["j9"] = None if k9 is None or d9 is None else (3 * k9) - (2 * d9)

    row["bb_upper"] = row.get("bb_upper20")
    row["bb_middle"] = row.get("bb_mid20")
    row["bb_lower"] = row.get("bb_lower20")
    row["plus_di"] = row.get("plus_di14")
    row["minus_di"] = row.get("minus_di14")
    row["vzo"] = row.get("vzo14")
    row["mfi14"] = row.get("money_flow_index")
    row["vpt"] = row.get("volume_price_trend")


def pct_from_close(close, future_value):
    if close in (None, 0) or future_value is None:
        return None

    return ((future_value - close) / close) * 100


def calculate_ai_signal_score(row):
    close = row.get("close")
    score = 0

    if close is not None and row.get("ma20") is not None and close > row["ma20"]:
        score += 1

    if row.get("ma5") is not None and row.get("ma20") is not None and row["ma5"] > row["ma20"]:
        score += 1

    if row.get("ma_slope_20") is not None and row["ma_slope_20"] > 0:
        score += 1

    if row.get("rsi14") is not None:
        if 45 <= row["rsi14"] <= 72:
            score += 1
        elif row["rsi14"] > 78 or row["rsi14"] < 30:
            score -= 1

    if row.get("k9") is not None and row.get("d9") is not None and row["k9"] > row["d9"]:
        score += 1

    if row.get("osc") is not None and row["osc"] > 0:
        score += 1

    if row.get("volume_ratio") is not None and row["volume_ratio"] >= 1.2:
        score += 1

    if (
        row.get("adx14") is not None
        and row["adx14"] >= 20
        and (row.get("plus_di") or 0) > (row.get("minus_di") or 0)
    ):
        score += 1

    if row.get("relative_strength_pct") is not None and row["relative_strength_pct"] >= 0:
        score += 1

    if row.get("price_loc_bb") is not None:
        if 0.2 <= row["price_loc_bb"] <= 0.9:
            score += 1
        elif row["price_loc_bb"] > 1:
            score -= 1

    if row.get("gap_pct") is not None and row["gap_pct"] > 5:
        score -= 1

    return score


def calculate_entry_price(row, buy_signal):
    if not buy_signal:
        return None

    close = row.get("close")
    ma5 = row.get("ma5")
    atr14 = row.get("atr14")

    if close is None:
        return None

    if ma5 is None:
        return close

    if atr14 is not None and atr14 > 0:
        entry_price = max(ma5, close - (atr14 * 0.35))
    else:
        entry_price = (close + ma5) / 2

    return min(close, max(entry_price, close * 0.97))


def add_ai_labels(rows):
    if not rows:
        return

    interval_type = rows[0].get("interval_type")
    bars_per_day = TIMEFRAME_METADATA.get(interval_type, {}).get("bars_per_day", 1)
    horizon_1d = max(1, bars_per_day)
    horizon_3d = horizon_1d * 3
    horizon_5d = horizon_1d * 5

    for i, row in enumerate(rows):
        close = row.get("close")
        future_1d_close = rows[i + horizon_1d].get("close") if i + horizon_1d < len(rows) else None
        future_3d_close = rows[i + horizon_3d].get("close") if i + horizon_3d < len(rows) else None
        future_window = rows[i + 1:i + horizon_5d + 1]
        future_highs = [r.get("high") for r in future_window if r.get("high") is not None]
        future_lows = [r.get("low") for r in future_window if r.get("low") is not None]

        row["future_1d_return"] = pct_from_close(close, future_1d_close)
        row["future_3d_return"] = pct_from_close(close, future_3d_close)
        row["max_upside_5d"] = pct_from_close(close, max(future_highs) if future_highs else None)
        row["drawdown_5d"] = pct_from_close(close, min(future_lows) if future_lows else None)

        ai_signal_score = calculate_ai_signal_score(row)
        buy_signal = 1 if close is not None and ai_signal_score >= 5 else 0

        row["ai_signal_score"] = ai_signal_score
        row["buy_signal"] = buy_signal
        row["entry_price"] = calculate_entry_price(row, buy_signal)
        row["label_ready"] = 1 if len(future_window) >= horizon_5d and close not in (None, 0) else 0
        row["label_horizon_bars_1d"] = horizon_1d
        row["label_horizon_bars_3d"] = horizon_3d
        row["label_horizon_bars_5d"] = horizon_5d


def calculate_indicators(rows, index_return_map=None, index_close_map=None):
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    opens = [r["open"] for r in rows]
    volumes = [r["volume"] for r in rows]

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)

    dif_list = []
    for e12, e26 in zip(ema12, ema26):
        dif_list.append(None if e12 is None or e26 is None else e12 - e26)

    macd_list = ema_series(dif_list, 9)

    gains = [0]
    losses = [0]
    returns = [None]

    obv = 0
    obv_list = []
    vpt = 0
    vpt_list = []
    signed_volume_list = []

    cumulative_pv = 0
    cumulative_volume = 0
    vwap_list = []
    current_vwap_date = None

    typical_prices = []
    raw_money_flow = []

    for i in range(len(rows)):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        volume = volumes[i] or 0
        interval_type = rows[i].get("interval_type")
        bar_date = rows[i].get("bar_time", "")[:10]

        if interval_type in ("1m", "5m", "30m") and bar_date != current_vwap_date:
            cumulative_pv = 0
            cumulative_volume = 0
            current_vwap_date = bar_date

        if close is not None:
            cumulative_pv += close * volume
            cumulative_volume += volume

        vwap_list.append(None if cumulative_volume == 0 else cumulative_pv / cumulative_volume)

        if None in (high, low, close):
            typical_prices.append(None)
            raw_money_flow.append(None)
        else:
            tp = (high + low + close) / 3
            typical_prices.append(tp)
            raw_money_flow.append(tp * volume)

        if i == 0 or closes[i - 1] is None or close is None:
            obv_list.append(obv)
            vpt_list.append(vpt)
            signed_volume_list.append(0)
            continue

        if close > closes[i - 1]:
            obv += volume
            signed_volume_list.append(volume)
        elif close < closes[i - 1]:
            obv -= volume
            signed_volume_list.append(-volume)
        else:
            signed_volume_list.append(0)

        if closes[i - 1] != 0:
            vpt += volume * (close - closes[i - 1]) / closes[i - 1]

        obv_list.append(obv)
        vpt_list.append(vpt)

    vzo_num = ema_series(signed_volume_list, 14)
    vzo_den = ema_series(volumes, 14)
    vzo_list = []

    for num, den in zip(vzo_num, vzo_den):
        vzo_list.append(None if not den else 100 * num / den)

    for i in range(1, len(closes)):
        if closes[i] is None or closes[i - 1] is None or closes[i - 1] == 0:
            gains.append(0)
            losses.append(0)
            returns.append(None)
        else:
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(abs(min(diff, 0)))
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

    k_prev = 50
    d_prev = 50

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(len(rows)):
        if i == 0:
            tr_list.append(None)
            plus_dm_list.append(None)
            minus_dm_list.append(None)
            continue

        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        prev_high = highs[i - 1]
        prev_low = lows[i - 1]

        if None in (high, low, prev_close, prev_high, prev_low):
            tr_list.append(None)
            plus_dm_list.append(None)
            minus_dm_list.append(None)
            continue

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    dx_list = []

    for i, row in enumerate(rows):
        close = closes[i]
        open_price = opens[i]
        high = highs[i]
        low = lows[i]
        volume = volumes[i]
        prev_close = closes[i - 1] if i > 0 else None

        row["change_pct"] = None if not prev_close else ((close - prev_close) / prev_close) * 100
        row["gap_pct"] = None if not prev_close or open_price is None else ((open_price - prev_close) / prev_close) * 100

        if None not in (open_price, high, low, close) and high != low:
            upper_tail = high - max(open_price, close)
            lower_tail = min(open_price, close) - low
            total_range = high - low
            row["upper_tail_ratio"] = upper_tail / total_range * 100
            row["lower_tail_ratio"] = lower_tail / total_range * 100
            row["tail_ratio"] = (upper_tail + lower_tail) / total_range * 100
            row["day_range_pos"] = (close - low) / (high - low)
        else:
            row["upper_tail_ratio"] = None
            row["lower_tail_ratio"] = None
            row["tail_ratio"] = None
            row["day_range_pos"] = None

        for period in [5, 10, 20, 60, 120]:
            row[f"ma{period}"] = sma(closes, period, i)
            ma = row[f"ma{period}"]
            bias_key = f"bias{period}"
            row[bias_key] = None if not ma else ((close - ma) / ma) * 100

            prev_bias = rows[i - 1].get(bias_key) if i > 0 else None
            row[f"{bias_key}_chg"] = None if prev_bias is None or row[bias_key] is None else row[bias_key] - prev_bias

        if i >= 5 and row.get("ma20") is not None and rows[i - 5].get("ma20") not in (None, 0):
            row["ma_slope_20"] = ((row["ma20"] - rows[i - 5]["ma20"]) / rows[i - 5]["ma20"]) * 100
        else:
            row["ma_slope_20"] = None

        if i + 1 >= 14:
            avg_gain = sum(gains[i - 13:i + 1]) / 14
            avg_loss = sum(losses[i - 13:i + 1]) / 14

            if avg_loss == 0:
                row["rsi14"] = 100
            else:
                rs = avg_gain / avg_loss
                row["rsi14"] = 100 - (100 / (1 + rs))
        else:
            row["rsi14"] = None

        if i + 1 >= 9:
            high9 = max(highs[i - 8:i + 1])
            low9 = min(lows[i - 8:i + 1])
            rsv = 50 if high9 == low9 else ((close - low9) / (high9 - low9)) * 100
            k = (2 / 3) * k_prev + (1 / 3) * rsv
            d = (2 / 3) * d_prev + (1 / 3) * k
            k_prev = k
            d_prev = d
            row["k9"] = k
            row["d9"] = d
        else:
            row["k9"] = None
            row["d9"] = None

        if i + 1 >= 14:
            highest14 = max(highs[i - 13:i + 1])
            lowest14 = min(lows[i - 13:i + 1])
            row["williams_r14"] = None if highest14 == lowest14 else -100 * (highest14 - close) / (highest14 - lowest14)
        else:
            row["williams_r14"] = None

        if i + 1 >= 14:
            pos_flow = 0
            neg_flow = 0

            for j in range(i - 13, i + 1):
                if j == 0:
                    continue

                tp = typical_prices[j]
                prev_tp = typical_prices[j - 1]
                mf = raw_money_flow[j]

                if None in (tp, prev_tp, mf):
                    continue

                if tp > prev_tp:
                    pos_flow += mf
                elif tp < prev_tp:
                    neg_flow += mf

            if neg_flow == 0:
                row["money_flow_index"] = 100
            else:
                money_ratio = pos_flow / neg_flow
                row["money_flow_index"] = 100 - (100 / (1 + money_ratio))
        else:
            row["money_flow_index"] = None

        row["volume_price_trend"] = vpt_list[i] if i < len(vpt_list) else None

        row["dif"] = dif_list[i]
        row["macd"] = macd_list[i]
        row["osc"] = None if row["dif"] is None or row["macd"] is None else row["dif"] - row["macd"]

        row["volume_ma5"] = sma(volumes, 5, i)
        row["volume_ratio"] = None if not row["volume_ma5"] or not volume else volume / row["volume_ma5"]

        if i + 1 >= 20:
            recent_volume20 = volumes[i - 19:i + 1]
            vol_mid = sum(recent_volume20) / 20 if not any(v is None for v in recent_volume20) else None
            vol_sd = stddev(recent_volume20)
            row["vol_std_score"] = None if not vol_mid or not vol_sd else (volume - vol_mid) / vol_sd
        else:
            row["vol_std_score"] = None

        if i + 1 >= 20:
            recent20 = closes[i - 19:i + 1]
            mid = sum(recent20) / 20
            sd = stddev(recent20)

            row["bb_mid20"] = mid
            row["bb_upper20"] = mid + 2 * sd
            row["bb_lower20"] = mid - 2 * sd
            row["bb_width"] = None if mid == 0 else ((row["bb_upper20"] - row["bb_lower20"]) / mid) * 100
            row["price_loc_bb"] = None if row["bb_upper20"] == row["bb_lower20"] else (close - row["bb_lower20"]) / (row["bb_upper20"] - row["bb_lower20"])
        else:
            row["bb_mid20"] = None
            row["bb_upper20"] = None
            row["bb_lower20"] = None
            row["bb_width"] = None
            row["price_loc_bb"] = None

        row["vwap"] = vwap_list[i]
        row["obv"] = obv_list[i]
        row["vzo14"] = vzo_list[i]

        row["atr14"] = sma(tr_list, 14, i)

        tr14 = sma(tr_list, 14, i)
        plus_dm14 = sma(plus_dm_list, 14, i)
        minus_dm14 = sma(minus_dm_list, 14, i)

        if tr14 and tr14 != 0:
            plus_di = 100 * plus_dm14 / tr14 if plus_dm14 is not None else None
            minus_di = 100 * minus_dm14 / tr14 if minus_dm14 is not None else None

            row["plus_di14"] = plus_di
            row["minus_di14"] = minus_di

            if plus_di is not None and minus_di is not None and plus_di + minus_di != 0:
                dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            else:
                dx = None
        else:
            row["plus_di14"] = None
            row["minus_di14"] = None
            dx = None

        dx_list.append(dx)
        row["adx14"] = sma(dx_list, 14, i)

        if i + 1 >= 252:
            high_52w = max(highs[i - 251:i + 1])
            low_52w = min(lows[i - 251:i + 1])
        else:
            high_52w = max(highs[:i + 1]) if highs[:i + 1] else None
            low_52w = min(lows[:i + 1]) if lows[:i + 1] else None

        row["high_52w"] = high_52w
        row["low_52w"] = low_52w
        row["dist_high_52w_pct"] = None if not high_52w else ((close - high_52w) / high_52w) * 100
        row["dist_low_52w_pct"] = None if not low_52w else ((close - low_52w) / low_52w) * 100

        if index_return_map:
            idx_ret = index_return_map.get(row["bar_time"])
            row["relative_strength_pct"] = None if idx_ret is None or row["change_pct"] is None else row["change_pct"] - (idx_ret * 100)
        else:
            row["relative_strength_pct"] = None

        if index_close_map:
            idx_close = index_close_map.get(row["bar_time"])
            row["stock_index_ratio"] = None if not idx_close else close / idx_close
        else:
            row["stock_index_ratio"] = None

        if index_return_map:
            aligned_index_returns = [index_return_map.get(r["bar_time"]) for r in rows]
            beta, corr = calculate_beta_corr(returns, aligned_index_returns, 20, i)
            row["beta20"] = beta
            row["corr20"] = corr
        else:
            row["beta20"] = None
            row["corr20"] = None

        score = 0

        if row.get("ma5") and row.get("ma20") and close > row["ma5"] > row["ma20"]:
            score += 2

        if row.get("rsi14") is not None and 50 <= row["rsi14"] <= 70:
            score += 1

        if row.get("k9") is not None and row.get("d9") is not None and row["k9"] > row["d9"]:
            score += 1

        if row.get("osc") is not None and row["osc"] > 0:
            score += 1

        if row.get("volume_ratio") is not None and row["volume_ratio"] >= 1.2:
            score += 1

        if row.get("adx14") is not None and row["adx14"] >= 20 and (row.get("plus_di14") or 0) > (row.get("minus_di14") or 0):
            score += 1

        if row.get("price_loc_bb") is not None and row["price_loc_bb"] > 1:
            score -= 1

        if row.get("rsi14") is not None and row["rsi14"] > 75:
            score -= 1

        row["short_term_score"] = score
        add_indicator_aliases(row)
        row["close_position"] = row.get("day_range_pos")
        row["daily_score"], row["reason"] = score_row(row)

    add_ai_labels(rows)

    return rows


def round_value(value):
    if value is None:
        return ""

    if isinstance(value, float):
        return round(value, 4)

    return value


def load_rows(symbol, interval_type):
    conn = connect_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT b.symbol, s.base_code, s.name, s.item_type, b.interval_type, b.bar_time,
               b.open_price, b.high_price, b.low_price, b.close_price, b.volume, b.fetch_time
        FROM k_bars b
        JOIN symbols s ON s.symbol = b.symbol
        WHERE b.symbol = ? AND b.interval_type = ?
        ORDER BY b.bar_time ASC
    """, (symbol, interval_type))

    rows = []

    for r in cur.fetchall():
        rows.append({
            "symbol": r[0],
            "base_code": r[1],
            "name": r[2],
            "item_type": r[3],
            "interval_type": r[4],
            "bar_time": r[5],
            "open": r[6],
            "high": r[7],
            "low": r[8],
            "close": r[9],
            "volume": r[10],
            "fetch_time": r[11],
            "scan_time": r[11],
        })

    conn.close()
    return rows


def build_index_maps(index_symbol, interval_type):
    rows = load_rows(index_symbol, interval_type)
    close_map = {}
    return_map = {}
    prev_close = None

    for row in rows:
        close = row["close"]
        close_map[row["bar_time"]] = close

        if prev_close and prev_close != 0:
            return_map[row["bar_time"]] = (close - prev_close) / prev_close
        else:
            return_map[row["bar_time"]] = None

        prev_close = close

    return return_map, close_map


def get_all_rows_with_indicators():
    conn = connect_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT s.symbol, s.item_type
        FROM symbols s
        JOIN k_bars b ON b.symbol = s.symbol
        ORDER BY s.symbol
    """)
    symbols = cur.fetchall()

    conn.close()

    all_rows = []

    for interval_type in INTERVALS:
        twii_returns, twii_closes = build_index_maps("^TWII", interval_type)
        twoii_returns, twoii_closes = build_index_maps("^TWOII", interval_type)

        for symbol, item_type in symbols:
            rows = load_rows(symbol, interval_type)

            if not rows:
                continue

            if item_type == "stock":
                index_symbol = market_index_for_symbol(symbol)

                if index_symbol == "^TWOII":
                    index_returns, index_closes = twoii_returns, twoii_closes
                else:
                    index_returns, index_closes = twii_returns, twii_closes
            else:
                index_returns, index_closes = None, None

            rows = calculate_indicators(rows, index_returns, index_closes)
            all_rows.extend(rows)

    return all_rows


def indicator_db_value(row, column_name, timestamp):
    source_map = {
        "record_type": "interval_type",
        "open_price": "open",
        "high_price": "high",
        "low_price": "low",
        "close_price": "close",
    }

    if column_name == "note":
        return "Network Ping Test data"

    if column_name in ("created_at", "updated_at"):
        return timestamp

    return row.get(source_map.get(column_name, column_name))


def save_features(conn, rows):
    if not rows:
        return 0

    columns = [name for name, _ in FEATURE_COLUMNS]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ", ".join(["?"] * (3 + len(columns) + 1))
    column_sql = ", ".join(["symbol", "interval_type", "bar_time"] + columns + ["updated_at"])
    update_sql = ", ".join(f"{name} = excluded.{name}" for name in columns + ["updated_at"])

    conn.executemany(
        f"""
        INSERT INTO k_bar_features ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT(symbol, interval_type, bar_time)
        DO UPDATE SET {update_sql}
        """,
        [
            [row["symbol"], row["interval_type"], row["bar_time"]]
            + [row.get(column_name) for column_name in columns]
            + [now]
            for row in rows
        ],
    )
    return len(rows)


def save_ai_labels(conn, rows):
    if not rows:
        return 0

    columns = [name for name, _ in AI_LABEL_COLUMNS]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ", ".join(["?"] * (3 + len(columns) + 2))
    column_sql = ", ".join(["symbol", "interval_type", "bar_time"] + columns + ["created_at", "updated_at"])
    update_sql = ", ".join(f"{name} = excluded.{name}" for name in columns + ["updated_at"])

    conn.executemany(
        f"""
        INSERT INTO ai_labels ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT(symbol, interval_type, bar_time)
        DO UPDATE SET {update_sql}
        """,
        [
            [row["symbol"], row["interval_type"], row["bar_time"]]
            + [row.get(column_name) for column_name in columns]
            + [now, now]
            for row in rows
        ],
    )
    return len(rows)


def read_csv_dicts(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        warn(f"讀取 cache 失敗 {os.path.basename(path)}: {exc}")
        return []


def write_csv_dicts(path, rows):
    if not rows:
        return
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def chip_cache_path(kind, trade_date):
    return os.path.join(CHIP_CACHE_DIR, f"{kind}_{trade_date}.csv")


def compact_number(value):
    if is_missing(value):
        return 0.0
    text = str(value).strip()
    text = text.replace(",", "").replace("--", "0").replace("X", "0").replace("+", "")
    if text in ("", "-", "N/A", "None"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_stock_id(value):
    text = safe_text(value)
    if "." in text:
        text = strip_yahoo_suffix(text)
    return text


def records_from_fields(fields, data_rows):
    records = []
    for values in data_rows or []:
        record = {}
        for index, field in enumerate(fields or []):
            key = safe_text(field) or f"field_{index}"
            if key in record:
                key = f"{key}_{index}"
            record[key] = values[index] if index < len(values) else ""
        records.append(record)
    return records


def twse_date(trade_date):
    return safe_text(trade_date).replace("-", "")


def roc_date(trade_date):
    parsed = parse_trade_date(trade_date)
    if parsed is None:
        return ""
    return f"{parsed.year - 1911:03d}/{parsed.month:02d}/{parsed.day:02d}"


def fetch_json_url(url, params, stats, source_name):
    query_url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    request = urllib.request.Request(query_url, headers=headers)
    stats["api_request_count"] += 1
    try:
        with urllib.request.urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8-sig")), "api"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            stats["rate_limited"] = True
            warn(f"{source_name} HTTP {exc.code}，籌碼資料改用 cache 或 fallback。")
            return None, "rate_limited"
        stats["api_error"] = True
        warn(f"{source_name} HTTP error: {exc}")
        return None, "api_failed"
    except Exception as exc:
        stats["api_error"] = True
        warn(f"{source_name} API 失敗: {exc}")
        return None, "api_failed"


def fetch_official_rows(kind, trade_date, stats):
    os.makedirs(CHIP_CACHE_DIR, exist_ok=True)
    cache_path = chip_cache_path(kind, trade_date)
    if os.path.exists(cache_path):
        stats["cache_hit_count"] += 1
        return read_csv_dicts(cache_path), "cache"

    try:
        if kind == "twse_institutional":
            payload, status = fetch_json_url(
                TWSE_INSTITUTIONAL_URL,
                {"date": twse_date(trade_date), "selectType": "ALLBUT0999", "response": "json"},
                stats,
                kind,
            )
            rows = normalize_twse_institutional_payload(payload) if payload else []
        elif kind == "twse_margin":
            payload, status = fetch_json_url(
                TWSE_MARGIN_URL,
                {"date": twse_date(trade_date), "selectType": "ALL", "response": "json"},
                stats,
                kind,
            )
            rows = normalize_twse_margin_payload(payload) if payload else []
        elif kind == "tpex_institutional":
            payload, status = fetch_json_url(
                TPEX_INSTITUTIONAL_URL,
                {"l": "zh-tw", "se": "EW", "t": "D", "d": roc_date(trade_date), "s": "0,asc,0"},
                stats,
                kind,
            )
            rows = normalize_tpex_institutional_payload(payload) if payload else []
        elif kind == "tpex_margin":
            payload, status = fetch_json_url(
                TPEX_MARGIN_URL,
                {"l": "zh-tw", "d": roc_date(trade_date), "s": "0,asc,0"},
                stats,
                kind,
            )
            rows = normalize_tpex_margin_payload(payload) if payload else []
        else:
            rows = []
            status = "api_failed"
    except Exception as exc:
        stats["api_error"] = True
        warn(f"{kind} 解析失敗: {exc}")
        rows = []
        status = "api_failed"

    if rows:
        write_csv_dicts(cache_path, rows)
    return rows, status


def normalize_twse_institutional_payload(payload):
    if not payload or payload.get("stat") != "OK":
        return []
    records = records_from_fields(payload.get("fields"), payload.get("data"))
    normalized = []
    for row in records:
        stock_id = normalize_stock_id(row.get("證券代號"))
        if not stock_id:
            continue
        foreign = compact_number(row.get("外陸資買賣超股數(不含外資自營商)")) + compact_number(row.get("外資自營商買賣超股數"))
        trust = compact_number(row.get("投信買賣超股數"))
        dealer = compact_number(row.get("自營商買賣超股數"))
        total = compact_number(row.get("三大法人買賣超股數"))
        normalized.append({
            "stock_id": stock_id,
            "foreign_buy_sell": foreign,
            "investment_trust_buy_sell": trust,
            "dealer_buy_sell": dealer,
            "institutional_total_buy_sell": total if total else foreign + trust + dealer,
        })
    return normalized


def normalize_tpex_institutional_payload(payload):
    tables = payload.get("tables") if payload else []
    if not tables:
        return []
    data_rows = tables[0].get("data") or []
    normalized = []
    for values in data_rows:
        if len(values) < 24:
            continue
        stock_id = normalize_stock_id(values[0])
        if not stock_id:
            continue
        foreign = compact_number(values[10])
        trust = compact_number(values[13])
        dealer = compact_number(values[22])
        total = compact_number(values[23])
        normalized.append({
            "stock_id": stock_id,
            "foreign_buy_sell": foreign,
            "investment_trust_buy_sell": trust,
            "dealer_buy_sell": dealer,
            "institutional_total_buy_sell": total if total else foreign + trust + dealer,
        })
    return normalized


def normalize_twse_margin_payload(payload):
    if not payload or payload.get("stat") != "OK":
        return []
    tables = payload.get("tables") or []
    if len(tables) < 2:
        return []
    data_rows = tables[1].get("data") or []
    normalized = []
    for values in data_rows:
        if len(values) < 13:
            continue
        stock_id = normalize_stock_id(values[0])
        margin_prev = compact_number(values[5])
        margin_balance = compact_number(values[6])
        short_prev = compact_number(values[11])
        short_balance = compact_number(values[12])
        normalized.append({
            "stock_id": stock_id,
            "margin_change": margin_balance - margin_prev,
            "short_change": short_balance - short_prev,
            "margin_balance": margin_balance,
            "short_balance": short_balance,
            "margin_short_ratio": 0 if margin_balance == 0 else short_balance / margin_balance,
        })
    return normalized


def normalize_tpex_margin_payload(payload):
    tables = payload.get("tables") if payload else []
    if not tables:
        return []
    data_rows = tables[0].get("data") or []
    normalized = []
    for values in data_rows:
        if len(values) < 15:
            continue
        stock_id = normalize_stock_id(values[0])
        margin_prev = compact_number(values[2])
        margin_balance = compact_number(values[6])
        short_prev = compact_number(values[10])
        short_balance = compact_number(values[14])
        normalized.append({
            "stock_id": stock_id,
            "margin_change": margin_balance - margin_prev,
            "short_change": short_balance - short_prev,
            "margin_balance": margin_balance,
            "short_balance": short_balance,
            "margin_short_ratio": 0 if margin_balance == 0 else short_balance / margin_balance,
        })
    return normalized


def has_enough_chip_rows(rows):
    return len(rows) >= 100


def group_official_institutional_rows(rows, stats):
    grouped = {}
    foreign_rows = 0
    trust_rows = 0
    dealer_rows = 0
    for row in rows:
        stock_id = normalize_stock_id(row.get("stock_id"))
        if not stock_id:
            continue
        item = grouped.setdefault(stock_id, {
            "foreign_buy_sell": 0.0,
            "investment_trust_buy_sell": 0.0,
            "dealer_buy_sell": 0.0,
            "institutional_total_buy_sell": 0.0,
        })
        foreign = compact_number(row.get("foreign_buy_sell"))
        trust = compact_number(row.get("investment_trust_buy_sell"))
        dealer = compact_number(row.get("dealer_buy_sell"))
        total = compact_number(row.get("institutional_total_buy_sell"))
        item["foreign_buy_sell"] += foreign
        item["investment_trust_buy_sell"] += trust
        item["dealer_buy_sell"] += dealer
        item["institutional_total_buy_sell"] += total if total else foreign + trust + dealer
        if foreign:
            foreign_rows += 1
        if trust:
            trust_rows += 1
        if dealer:
            dealer_rows += 1

    stats["foreign_rows_count"] += foreign_rows
    stats["investment_trust_rows_count"] += trust_rows
    stats["dealer_rows_count"] += dealer_rows
    stats["institutional_groupby_stock_count"] = max(
        stats.get("institutional_groupby_stock_count", 0),
        len(grouped),
    )
    return grouped


def group_official_margin_rows(rows):
    grouped = {}
    for row in rows:
        stock_id = normalize_stock_id(row.get("stock_id"))
        if not stock_id:
            continue
        grouped[stock_id] = {
            "margin_change": compact_number(row.get("margin_change")),
            "short_change": compact_number(row.get("short_change")),
            "margin_balance": compact_number(row.get("margin_balance")),
            "short_balance": compact_number(row.get("short_balance")),
            "margin_short_ratio": compact_number(row.get("margin_short_ratio")),
        }
    return grouped


def fetch_official_chip_data(latest_trade_date):
    stats = {
        "api_request_count": 0,
        "cache_hit_count": 0,
        "chip_date_list": [],
        "chip_data_date": "",
        "chip_data_source": "official_twse_tpex",
        "rate_limited": False,
        "api_error": False,
        "institutional_name_column": "official_column_mapping",
        "foreign_rows_count": 0,
        "investment_trust_rows_count": 0,
        "dealer_rows_count": 0,
        "institutional_groupby_stock_count": 0,
        "institutional_stock_count": 0,
        "margin_stock_count": 0,
    }
    empty_result = {
        "by_stock": {},
        "stats": stats,
        "global_status": "missing_chip_data",
    }
    if latest_trade_date is None:
        warn("找不到 latest_trade_date，籌碼資料 fallback 成純技術面版本。")
        return empty_result

    fetched_by_date = {}
    candidate_dates = []
    for offset in range(10):
        candidate = latest_trade_date - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        candidate_dates.append(candidate.isoformat())

    for chip_date in candidate_dates:
        twse_institutional_rows, _ = fetch_official_rows("twse_institutional", chip_date, stats)
        twse_margin_rows, _ = fetch_official_rows("twse_margin", chip_date, stats)
        tpex_institutional_rows, _ = fetch_official_rows("tpex_institutional", chip_date, stats)
        tpex_margin_rows, _ = fetch_official_rows("tpex_margin", chip_date, stats)
        institutional_rows = twse_institutional_rows + tpex_institutional_rows
        margin_rows = twse_margin_rows + tpex_margin_rows
        fetched_by_date[chip_date] = {
            "institutional_rows": institutional_rows,
            "margin_rows": margin_rows,
        }
        if has_enough_chip_rows(institutional_rows) or has_enough_chip_rows(margin_rows):
            stats["chip_date_list"].append(chip_date)
        if len(stats["chip_date_list"]) >= 5:
            break

    if not stats["chip_date_list"]:
        if stats["rate_limited"]:
            empty_result["global_status"] = "rate_limited"
        elif stats["api_error"]:
            empty_result["global_status"] = "api_failed"
        return empty_result

    chip_data_date = stats["chip_date_list"][0]
    stats["chip_data_date"] = chip_data_date
    latest_payload = fetched_by_date.get(chip_data_date, {})
    latest_institutional = group_official_institutional_rows(
        latest_payload.get("institutional_rows", []),
        stats,
    )
    latest_margin = group_official_margin_rows(latest_payload.get("margin_rows", []))

    institutional_3d = {}
    for chip_date in stats["chip_date_list"][:3]:
        payload = fetched_by_date.get(chip_date, {})
        daily = group_official_institutional_rows(payload.get("institutional_rows", []), stats)
        for stock_id, item in daily.items():
            target = institutional_3d.setdefault(stock_id, {
                "foreign_buy_sell_3d": 0.0,
                "investment_trust_buy_sell_3d": 0.0,
                "dealer_buy_sell_3d": 0.0,
                "institutional_total_buy_sell_3d": 0.0,
            })
            target["foreign_buy_sell_3d"] += item.get("foreign_buy_sell", 0)
            target["investment_trust_buy_sell_3d"] += item.get("investment_trust_buy_sell", 0)
            target["dealer_buy_sell_3d"] += item.get("dealer_buy_sell", 0)
            target["institutional_total_buy_sell_3d"] += item.get("institutional_total_buy_sell", 0)

    by_stock = {}
    all_stock_ids = set(latest_institutional) | set(latest_margin) | set(institutional_3d)
    for stock_id in all_stock_ids:
        by_stock[stock_id] = {}
        by_stock[stock_id].update(latest_institutional.get(stock_id, {}))
        by_stock[stock_id].update(institutional_3d.get(stock_id, {}))
        by_stock[stock_id].update(latest_margin.get(stock_id, {}))

    stats["institutional_stock_count"] = len(latest_institutional)
    stats["margin_stock_count"] = len(latest_margin)
    return {
        "by_stock": by_stock,
        "stats": stats,
        "global_status": "ok",
    }


def add_reason(reasons, text):
    if text:
        reasons.append(text)


def chip_status_for_row(chip_result, has_institutional, has_margin):
    global_status = chip_result.get("global_status", "missing_chip_data")
    if global_status in ("api_failed", "rate_limited"):
        return global_status
    if has_institutional and has_margin:
        return "ok"
    if has_institutional or has_margin:
        return "partial_chip_data"
    return "missing_chip_data"


def score_bullish_chip(row):
    score = 0.0
    reasons = []
    foreign = safe_float(row.get("foreign_buy_sell"), 0)
    trust = safe_float(row.get("investment_trust_buy_sell"), 0)
    dealer = safe_float(row.get("dealer_buy_sell"), 0)
    total = safe_float(row.get("institutional_total_buy_sell"), 0)
    foreign_3d = safe_float(row.get("foreign_buy_sell_3d"), 0)
    trust_3d = safe_float(row.get("investment_trust_buy_sell_3d"), 0)
    total_3d = safe_float(row.get("institutional_total_buy_sell_3d"), 0)
    margin_change = safe_float(row.get("margin_change"), 0)
    short_change = safe_float(row.get("short_change"), 0)
    change_pct = safe_float(row.get("change_pct"), 0)

    if foreign > 0:
        score += 2
        add_reason(reasons, "外資買超")
    if trust > 0:
        score += 3
        add_reason(reasons, "投信買超")
    if dealer > 0:
        score += 1
        add_reason(reasons, "自營商買超")
    if total > 0:
        score += 2
        add_reason(reasons, "三大法人合計買超")
    if foreign_3d > 0:
        score += 2
        add_reason(reasons, "外資3日買超")
    if trust_3d > 0:
        score += 3
        add_reason(reasons, "投信3日買超")
    if total_3d > 0:
        score += 2
        add_reason(reasons, "法人3日合計買超")
    if margin_change < 0 and change_pct > 0:
        score += 3
        add_reason(reasons, "融資減少且股價上漲")
    if margin_change > 0 and change_pct > 0:
        score -= 1
        add_reason(reasons, "融資增加追價疑慮")
    if short_change > 0 and change_pct > 0:
        score += 2
        add_reason(reasons, "融券增加具軋空潛力")
    if total < 0:
        score -= 3
        add_reason(reasons, "法人合計賣超")
    if trust < 0:
        score -= 3
        add_reason(reasons, "投信賣超")

    return score, "; ".join(reasons)


def score_bearish_chip(row):
    score = 0.0
    reasons = []
    foreign = safe_float(row.get("foreign_buy_sell"), 0)
    trust = safe_float(row.get("investment_trust_buy_sell"), 0)
    dealer = safe_float(row.get("dealer_buy_sell"), 0)
    total = safe_float(row.get("institutional_total_buy_sell"), 0)
    foreign_3d = safe_float(row.get("foreign_buy_sell_3d"), 0)
    trust_3d = safe_float(row.get("investment_trust_buy_sell_3d"), 0)
    total_3d = safe_float(row.get("institutional_total_buy_sell_3d"), 0)
    margin_change = safe_float(row.get("margin_change"), 0)
    short_change = safe_float(row.get("short_change"), 0)
    upper_tail_ratio = safe_float(row.get("upper_tail_ratio"), 0)
    change_pct = safe_float(row.get("change_pct"), 0)
    close = safe_float(row.get("close"), 0)
    open_price = safe_float(row.get("open"), 0)
    ma20 = safe_float(row.get("ma20"), 0)
    close_position = safe_float(row.get("close_position"), 0)

    if foreign < 0:
        score += 2
        add_reason(reasons, "外資賣超")
    if trust < 0:
        score += 3
        add_reason(reasons, "投信賣超")
    if dealer < 0:
        score += 1
        add_reason(reasons, "自營商賣超")
    if total < 0:
        score += 2
        add_reason(reasons, "三大法人合計賣超")
    if foreign_3d < 0:
        score += 2
        add_reason(reasons, "外資3日賣超")
    if trust_3d < 0:
        score += 3
        add_reason(reasons, "投信3日賣超")
    if total_3d < 0:
        score += 2
        add_reason(reasons, "法人3日合計賣超")
    if margin_change > 0 and upper_tail_ratio > 40:
        score += 3
        add_reason(reasons, "融資增加且長上影")
    if margin_change > 0 and close < open_price:
        score += 3
        add_reason(reasons, "融資增加且收黑K")
    if margin_change > 0 and ma20 and close < ma20:
        score += 2
        add_reason(reasons, "融資增加且跌破MA20")
    if short_change > 0 and change_pct > 0:
        score -= 2
        add_reason(reasons, "融券增加但股價強，防軋空")
    if short_change > 0 and close_position > 0.7:
        score -= 3
        add_reason(reasons, "融券增加且收盤強，防軋空")
    if trust > 0:
        score -= 3
        add_reason(reasons, "投信買超防下跌誤判")
    if total > 0:
        score -= 2
        add_reason(reasons, "法人合計買超防下跌誤判")

    return score, "; ".join(reasons)


def get_latest_trade_date(conn):
    row = conn.execute("""
        SELECT MAX(date(bar_time))
        FROM k_bars
        WHERE interval_type = '1d'
    """).fetchone()
    return parse_trade_date(row[0]) if row and row[0] else None


def load_latest_daily_feature_rows(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT
            s.symbol, s.base_code, b.bar_time, b.open_price, b.high_price,
            b.low_price, b.close_price, b.volume,
            f.change_pct, f.upper_tail_ratio, f.day_range_pos, f.ma20
        FROM symbols s
        LEFT JOIN k_bars b
          ON b.symbol = s.symbol
         AND b.interval_type = '1d'
         AND b.bar_time = (
             SELECT MAX(b2.bar_time)
             FROM k_bars b2
             WHERE b2.symbol = s.symbol
               AND b2.interval_type = '1d'
         )
        LEFT JOIN k_bar_features f
          ON f.symbol = b.symbol
         AND f.interval_type = b.interval_type
         AND f.bar_time = b.bar_time
        WHERE s.item_type != 'index'
    """)

    rows = {}
    for r in cur.fetchall():
        rows[r[0]] = {
            "symbol": r[0],
            "base_code": r[1],
            "bar_time": r[2],
            "open": r[3],
            "high": r[4],
            "low": r[5],
            "close": r[6],
            "volume": r[7],
            "change_pct": r[8],
            "upper_tail_ratio": r[9],
            "close_position": r[10],
            "ma20": r[11],
        }
    return rows


def save_chip_daily(conn, chip_result, latest_trade_date):
    if latest_trade_date is None:
        return 0

    chip_by_stock = chip_result.get("by_stock", {})
    stats = chip_result.get("stats", {})
    feature_rows = load_latest_daily_feature_rows(conn)
    trade_date = latest_trade_date.isoformat()
    chip_data_date = stats.get("chip_data_date", "")
    chip_data_source = stats.get("chip_data_source", "official_twse_tpex")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    output_rows = []
    numeric_chip_fields = [
        "foreign_buy_sell",
        "investment_trust_buy_sell",
        "dealer_buy_sell",
        "institutional_total_buy_sell",
        "foreign_buy_sell_3d",
        "investment_trust_buy_sell_3d",
        "dealer_buy_sell_3d",
        "institutional_total_buy_sell_3d",
        "margin_change",
        "short_change",
        "margin_balance",
        "short_balance",
        "margin_short_ratio",
    ]

    for symbol, row in feature_rows.items():
        base_code = normalize_stock_id(row.get("base_code"))
        chip = chip_by_stock.get(base_code, {})
        merged = dict(row)

        for field in numeric_chip_fields:
            merged[field] = safe_float(chip.get(field), 0)

        has_institutional = any(
            merged.get(field) != 0
            for field in (
                "foreign_buy_sell",
                "investment_trust_buy_sell",
                "dealer_buy_sell",
                "institutional_total_buy_sell",
            )
        )
        has_margin = any(
            merged.get(field) != 0
            for field in ("margin_change", "short_change", "margin_balance", "short_balance")
        )
        status = chip_status_for_row(chip_result, has_institutional, has_margin)
        bullish_score, bullish_reason = score_bullish_chip(merged)
        bearish_score, bearish_reason = score_bearish_chip(merged)

        output_rows.append([
            symbol,
            base_code,
            trade_date,
            merged["foreign_buy_sell"],
            merged["investment_trust_buy_sell"],
            merged["dealer_buy_sell"],
            merged["institutional_total_buy_sell"],
            merged["foreign_buy_sell_3d"],
            merged["investment_trust_buy_sell_3d"],
            merged["dealer_buy_sell_3d"],
            merged["institutional_total_buy_sell_3d"],
            merged["margin_change"],
            merged["short_change"],
            merged["margin_balance"],
            merged["short_balance"],
            merged["margin_short_ratio"],
            bullish_score,
            bearish_score,
            bullish_reason,
            bearish_reason,
            chip_data_source,
            status,
            chip_data_date,
            now,
        ])

    conn.executemany("""
        INSERT INTO chip_daily
        (symbol, base_code, trade_date,
         foreign_buy_sell, investment_trust_buy_sell, dealer_buy_sell,
         institutional_total_buy_sell,
         foreign_buy_sell_3d, investment_trust_buy_sell_3d, dealer_buy_sell_3d,
         institutional_total_buy_sell_3d,
         margin_change, short_change, margin_balance, short_balance, margin_short_ratio,
         bullish_chip_score, bearish_chip_score,
         bullish_chip_reason, bearish_chip_reason,
         chip_data_source, chip_data_status, chip_data_date, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, trade_date) DO UPDATE SET
            base_code = excluded.base_code,
            foreign_buy_sell = excluded.foreign_buy_sell,
            investment_trust_buy_sell = excluded.investment_trust_buy_sell,
            dealer_buy_sell = excluded.dealer_buy_sell,
            institutional_total_buy_sell = excluded.institutional_total_buy_sell,
            foreign_buy_sell_3d = excluded.foreign_buy_sell_3d,
            investment_trust_buy_sell_3d = excluded.investment_trust_buy_sell_3d,
            dealer_buy_sell_3d = excluded.dealer_buy_sell_3d,
            institutional_total_buy_sell_3d = excluded.institutional_total_buy_sell_3d,
            margin_change = excluded.margin_change,
            short_change = excluded.short_change,
            margin_balance = excluded.margin_balance,
            short_balance = excluded.short_balance,
            margin_short_ratio = excluded.margin_short_ratio,
            bullish_chip_score = excluded.bullish_chip_score,
            bearish_chip_score = excluded.bearish_chip_score,
            bullish_chip_reason = excluded.bullish_chip_reason,
            bearish_chip_reason = excluded.bearish_chip_reason,
            chip_data_source = excluded.chip_data_source,
            chip_data_status = excluded.chip_data_status,
            chip_data_date = excluded.chip_data_date,
            updated_at = excluded.updated_at
    """, output_rows)
    return len(output_rows)


CSV_EXPORT_COLUMNS = [
    "symbol",
    "base_code",
    "name",
    "market",
    "bar_time",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "change_pct",
    "gap_pct",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ma_slope_20",
    "bias20",
    "rsi14",
    "k9",
    "d9",
    "j9",
    "dif",
    "macd",
    "osc",
    "williams_r14",
    "volume_ratio",
    "vol_std_score",
    "vwap",
    "obv",
    "mfi14",
    "atr14",
    "adx14",
    "plus_di",
    "minus_di",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    "price_loc_bb",
    "upper_tail_ratio",
    "lower_tail_ratio",
    "day_range_pos",
    "high_52w",
    "low_52w",
    "dist_high_52w_pct",
    "dist_low_52w_pct",
    "relative_strength_pct",
    "beta20",
    "foreign_buy_sell",
    "investment_trust_buy_sell",
    "dealer_buy_sell",
    "institutional_total_buy_sell",
    "foreign_buy_sell_3d",
    "investment_trust_buy_sell_3d",
    "institutional_total_buy_sell_3d",
    "margin_change",
    "short_change",
    "margin_balance",
    "short_balance",
    "margin_short_ratio",
    "bullish_chip_score",
    "bearish_chip_score",
    "chip_data_status",
    "chip_data_date",
    "daily_score",
    "short_term_score",
    "reason",
]


AI_TRAINING_EXTRA_COLUMNS = [
    "future_1d_return",
    "future_3d_return",
    "max_upside_5d",
    "drawdown_5d",
    "buy_signal",
    "entry_price",
    "ai_signal_score",
    "label_ready",
]


def write_query_csv(conn, path, query, columns):
    cur = conn.execute(query)
    rows = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = {}
            for column in columns:
                output[column] = round_value(row.get(column))
            writer.writerow(output)
    return len(rows)


def export_snapshot_aftermarket(conn):
    columns_sql = ", ".join(CSV_EXPORT_COLUMNS)
    query = f"""
        SELECT {columns_sql}
        FROM v_ai_features_aftermarket
        WHERE interval_type = '1d'
          AND bar_time = (
              SELECT MAX(bar_time)
              FROM k_bar_features
              WHERE interval_type = '1d'
          )
        ORDER BY daily_score DESC
    """
    return write_query_csv(conn, SNAPSHOT_AFTERMARKET_FILE, query, CSV_EXPORT_COLUMNS)


def export_snapshot_intraday(conn):
    columns_sql = ", ".join(CSV_EXPORT_COLUMNS)
    query = f"""
        SELECT {columns_sql}
        FROM v_ai_features_intraday v
        WHERE interval_type = '5m'
          AND bar_time = (
              SELECT MAX(f2.bar_time)
              FROM k_bar_features f2
              WHERE f2.symbol = v.symbol
                AND f2.interval_type = '5m'
          )
        ORDER BY daily_score DESC
    """
    return write_query_csv(conn, SNAPSHOT_INTRADAY_FILE, query, CSV_EXPORT_COLUMNS)


def export_ai_training(conn):
    columns = CSV_EXPORT_COLUMNS + AI_TRAINING_EXTRA_COLUMNS
    columns_sql = ", ".join(columns)
    query = f"""
        SELECT {columns_sql}
        FROM v_ai_features_aftermarket
        WHERE interval_type = '1d'
          AND label_ready = 1
        ORDER BY symbol, bar_time
    """
    return write_query_csv(conn, AI_TRAINING_FILE, query, columns)


def export_csvs(conn=None):
    own_conn = conn is None
    conn = conn or connect_db()
    try:
        counts = {
            SNAPSHOT_AFTERMARKET_FILE: export_snapshot_aftermarket(conn),
            SNAPSHOT_INTRADAY_FILE: export_snapshot_intraday(conn),
            AI_TRAINING_FILE: export_ai_training(conn),
        }
        return counts
    finally:
        if own_conn:
            conn.close()


def display_item_type(item_type):
    mapping = {
        "stock": "Network Speed",
        "index": "Benchmark Ping",
    }
    return mapping.get(item_type, item_type)

def get_latest_bar_time(symbol, interval_type):
    conn = connect_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT MAX(bar_time)
        FROM k_bars
        WHERE symbol = ? AND interval_type = ?
    """, (symbol, interval_type))

    result = cur.fetchone()[0]

    conn.close()

    return result

def latest_summary():
    conn = connect_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT b.symbol, s.name, b.interval_type, b.bar_time, b.close_price, b.fetch_time
        FROM k_bars b
        JOIN symbols s ON s.symbol = b.symbol
        WHERE b.bar_time = (
            SELECT MAX(b2.bar_time)
            FROM k_bars b2
            WHERE b2.symbol = b.symbol
              AND b2.interval_type = b.interval_type
        )
        ORDER BY b.symbol, b.interval_type
    """)

    rows = cur.fetchall()
    conn.close()

    return rows


def random_sleep():
    delay = random.uniform(0.8, 1.4)
    log(f"  Random sleep {delay:.2f} sec...")
    time.sleep(delay)


def stop_update():
    global stop_scan
    stop_scan = True
    log("收到停止指令，這一筆完成後會停止。")


def export_csv_button():
    result_text.delete("1.0", tk.END)
    conn = connect_db()
    init_db(conn)
    try:
        all_rows = get_all_rows_with_indicators()
        feature_count = save_features(conn, all_rows)
        label_count = save_ai_labels(conn, all_rows)
        conn.commit()
        csv_counts = export_csvs(conn)
    finally:
        conn.close()

    log(f"SQLite k_bar_features updated: {feature_count} rows")
    log(f"SQLite ai_labels updated: {label_count} rows")
    for path, count in csv_counts.items():
        log(f"CSV 已輸出：{path}，共 {count} 筆資料")


def update_all():
    global stop_scan
    stop_scan = False
    result_text.delete("1.0", tk.END)

    try:
        stocks = read_stock_list()
    except FileNotFoundError:
        messagebox.showerror("錯誤", f"找不到 {LIST_FILE}")
        return

    if not stocks:
        messagebox.showwarning("提醒", "設定清單沒有測試項目")
        return

    log(f"APP 目錄：{APP_DIR}")
    log(f"讀取設定清單：{LIST_FILE}")
    log(f"開始 Network Ping Test，共 {len(stocks)} 筆，含測試節點與基準節點")
    log("模式：慢速單線程 Network Ping Test")
    log("代號：測試節點自動判斷 .TW / .TWO；基準節點與完整遠端代號直接使用")
    log("測試週期：1m / 5m / 30m / 1d / 1wk")
    log("保存期間：1m 5天、5m 20天、30m 60天、1d 3年、1wk 5年")
    log("匯出：snapshot_aftermarket.csv / snapshot_intraday.csv / ai_training.csv")
    log("分析欄位：MA/RSI/KD/MACD/BB/ATR/ADX/BIAS/VWAP/OBV/MFI/Williams%R/籌碼")
    log("Delay：random.uniform(0.8, 1.4)")
    log("=" * 70)

    success = 0
    fail = 0

    for index, (base_code, name, item_type) in enumerate(stocks, start=1):
        if stop_scan:
            log("測試已停止。")
            break

        log(f"[{index}/{len(stocks)}] Network Ping Test：{base_code} {name} ({display_item_type(item_type)})")

        try:
            symbol = resolve_symbol(base_code)
            interval_counts = []

            for interval_type in INTERVALS:
                count = save_bars(symbol, base_code, name, item_type, interval_type)
                interval_counts.append(f"{interval_type}:{count}")

            log(f"  成功：{symbol} | " + " | ".join(interval_counts))
            success += 1

        except Exception as e:
            log(f"  失敗：{e}")
            fail += 1

        if index < len(stocks) and not stop_scan:
            random_sleep()

    log("")
    log("=" * 70)
    log("最新 Network Ping Test 摘要")

    for row in latest_summary():
        symbol, name, interval_type, bar_time, close_price, scan_time = row
        log(f"{symbol} {name} | {interval_type} | {bar_time} | value {round_value(close_price)}")

    conn = connect_db()
    init_db(conn)
    try:
        all_rows = get_all_rows_with_indicators()
        feature_count = save_features(conn, all_rows)
        label_count = save_ai_labels(conn, all_rows)
        latest_trade_date = get_latest_trade_date(conn)

        log("")
        log("開始抓取 TWSE/TPEX 官方籌碼資料...")
        chip_result = fetch_official_chip_data(latest_trade_date)
        chip_count = save_chip_daily(conn, chip_result, latest_trade_date)
        conn.commit()

        csv_counts = export_csvs(conn)
    finally:
        conn.close()

    log("")
    log(f"SQLite k_bar_features updated: {feature_count} rows")
    log(f"SQLite ai_labels updated: {label_count} rows")
    log(f"SQLite chip_daily updated: {chip_count} rows")
    for path, count in csv_counts.items():
        log(f"CSV 已輸出：{path}，共 {count} 筆資料")
    log(f"完成：成功 {success} 筆，失敗 {fail} 筆")


init_db()

root = tk.Tk()
root.title("Network Ping Test 工程測試工具 Pro")
root.geometry("1180x760")

title_label = tk.Label(
    root,
    text="Network Ping Test 工程測試工具 Pro：多週期資料測試 + 設定清單控制"
)
title_label.pack(pady=10)

button_frame = tk.Frame(root)
button_frame.pack(pady=5)

update_button = tk.Button(
    button_frame,
    text="開始全部測試",
    command=update_all,
    width=20,
    height=2,
)
update_button.pack(side=tk.LEFT, padx=8)

stop_button = tk.Button(
    button_frame,
    text="停止測試",
    command=stop_update,
    width=20,
    height=2,
)
stop_button.pack(side=tk.LEFT, padx=8)

export_button = tk.Button(
    button_frame,
    text="匯出 CSV",
    command=export_csv_button,
    width=20,
    height=2,
)
export_button.pack(side=tk.LEFT, padx=8)

result_text = tk.Text(root, width=150, height=40)
result_text.pack(padx=10, pady=10)

root.mainloop()
