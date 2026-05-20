import tkinter as tk
from tkinter import messagebox
import requests
import sqlite3
import time
import random
import csv
import math
import os
import sys
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

DB_NAME = os.path.join(APP_DIR, "stock_data.db")
LIST_FILE = os.path.join(APP_DIR, "stock_list.txt")
EXPORT_FILE = os.path.join(APP_DIR, "stock_export.csv")

REQUEST_TIMEOUT = 15
MAX_RETRY = 3

KEEP_DAYS = {
    "1m": 14,
    "5m": 30,
    "30m": 60,
    "1d": 180,
    "1wk": 365 * 3,
}

EXPORT_DAYS = {
    "1m": 14,
    "5m": 30,
    "30m": 60,
    "1d": 180,
    "1wk": 365 * 3,
}

YAHOO_CONFIG = {
    # Yahoo usually limits 1m data range. 7d is safer than trying 14d.
    "1m": {"range": "7d", "interval": "1m"},
    "5m": {"range": "60d", "interval": "5m"},
    "30m": {"range": "60d", "interval": "30m"},
    "1d": {"range": "1y", "interval": "1d"},
    "1wk": {"range": "5y", "interval": "1wk"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# 不再寫死自動加入，全部交給 stock_list.txt 控制。
MARKET_INDEXES = []

INTERVALS = ["1m", "5m", "30m", "1d", "1wk"]

INDICATOR_DB_COLUMNS = [
    ("record_type", "TEXT"),
    ("symbol", "TEXT"),
    ("base_code", "TEXT"),
    ("name", "TEXT"),
    ("item_type", "TEXT"),
    ("interval_type", "TEXT"),
    ("bar_time", "TEXT"),
    ("open_price", "REAL"),
    ("high_price", "REAL"),
    ("low_price", "REAL"),
    ("close_price", "REAL"),
    ("volume", "REAL"),
    ("scan_time", "TEXT"),
    ("change_pct", "REAL"),
    ("ma5", "REAL"),
    ("ma10", "REAL"),
    ("ma20", "REAL"),
    ("ma60", "REAL"),
    ("ma120", "REAL"),
    ("ma_slope_20", "REAL"),
    ("bias5", "REAL"),
    ("bias10", "REAL"),
    ("bias20", "REAL"),
    ("bias60", "REAL"),
    ("bias120", "REAL"),
    ("bias5_chg", "REAL"),
    ("bias10_chg", "REAL"),
    ("bias20_chg", "REAL"),
    ("bias60_chg", "REAL"),
    ("bias120_chg", "REAL"),
    ("rsi14", "REAL"),
    ("k9", "REAL"),
    ("d9", "REAL"),
    ("j9", "REAL"),
    ("williams_r14", "REAL"),
    ("dif", "REAL"),
    ("macd", "REAL"),
    ("osc", "REAL"),
    ("bb_upper", "REAL"),
    ("bb_middle", "REAL"),
    ("bb_lower", "REAL"),
    ("bb_mid20", "REAL"),
    ("bb_upper20", "REAL"),
    ("bb_lower20", "REAL"),
    ("bb_width", "REAL"),
    ("price_loc_bb", "REAL"),
    ("atr14", "REAL"),
    ("vol_std_score", "REAL"),
    ("adx14", "REAL"),
    ("plus_di", "REAL"),
    ("minus_di", "REAL"),
    ("plus_di14", "REAL"),
    ("minus_di14", "REAL"),
    ("volume_ma5", "REAL"),
    ("volume_ratio", "REAL"),
    ("vwap", "REAL"),
    ("obv", "REAL"),
    ("vzo", "REAL"),
    ("vzo14", "REAL"),
    ("mfi14", "REAL"),
    ("money_flow_index", "REAL"),
    ("vpt", "REAL"),
    ("volume_price_trend", "REAL"),
    ("day_range_pos", "REAL"),
    ("high_52w", "REAL"),
    ("low_52w", "REAL"),
    ("dist_high_52w_pct", "REAL"),
    ("dist_low_52w_pct", "REAL"),
    ("beta20", "REAL"),
    ("corr20", "REAL"),
    ("relative_strength_pct", "REAL"),
    ("stock_index_ratio", "REAL"),
    ("gap_pct", "REAL"),
    ("tail_ratio", "REAL"),
    ("upper_tail_ratio", "REAL"),
    ("lower_tail_ratio", "REAL"),
    ("short_term_score", "REAL"),
    ("note", "TEXT"),
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
]

stop_scan = False


def ensure_indicator_table(cur):
    column_defs = ",\n            ".join(
        f"{name} {column_type}" for name, column_type in INDICATOR_DB_COLUMNS
    )

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS k_bar_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {column_defs},
            UNIQUE(symbol, interval_type, bar_time)
        )
    """)

    cur.execute("PRAGMA table_info(k_bar_indicators)")
    existing_columns = {row[1] for row in cur.fetchall()}

    for name, column_type in INDICATOR_DB_COLUMNS:
        if name not in existing_columns:
            cur.execute(f"ALTER TABLE k_bar_indicators ADD COLUMN {name} {column_type}")

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_k_bar_indicators_unique
        ON k_bar_indicators (symbol, interval_type, bar_time)
    """)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS k_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            base_code TEXT,
            name TEXT,
            item_type TEXT,
            interval_type TEXT,
            bar_time TEXT,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            volume REAL,
            scan_time TEXT,
            UNIQUE(symbol, interval_type, bar_time)
        )
    """)

    ensure_indicator_table(cur)

    conn.commit()
    conn.close()


def log(message):
    result_text.insert(tk.END, message + "\n")
    result_text.see(tk.END)
    root.update()


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
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "range": range_value,
        "interval": interval,
    }

    last_error = None

    for attempt in range(1, MAX_RETRY + 1):
        try:
            res = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            res.raise_for_status()

            data = res.json()

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

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS[interval_type])).strftime("%Y-%m-%d %H:%M:%S")
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved_count = 0

    for i, ts in enumerate(timestamps):
        close_price = closes[i] if i < len(closes) else None

        if close_price is None:
            continue

        bar_dt = datetime.fromtimestamp(ts)
        bar_time = bar_dt.strftime("%Y-%m-%d %H:%M:%S")

        if bar_time < cutoff:
            continue

        cur.execute("""
            INSERT OR REPLACE INTO k_bars
            (symbol, base_code, name, item_type, interval_type, bar_time,
             open_price, high_price, low_price, close_price, volume, scan_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            base_code,
            name,
            item_type,
            interval_type,
            bar_time,
            opens[i] if i < len(opens) else None,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            close_price,
            volumes[i] if i < len(volumes) else None,
            scan_time,
        ))

        saved_count += 1

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

    typical_prices = []
    raw_money_flow = []

    for i in range(len(rows)):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        volume = volumes[i] or 0

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

    return rows


def round_value(value):
    if value is None:
        return ""

    if isinstance(value, float):
        return round(value, 4)

    return value


def load_rows(symbol, interval_type):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT symbol, base_code, name, item_type, interval_type, bar_time,
               open_price, high_price, low_price, close_price, volume, scan_time
        FROM k_bars
        WHERE symbol = ? AND interval_type = ?
        ORDER BY bar_time ASC
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
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT symbol, item_type FROM k_bars ORDER BY symbol")
    symbols = cur.fetchall()

    conn.close()

    all_rows = []

    for interval_type in INTERVALS:
        twii_returns, twii_closes = build_index_maps("^TWII", interval_type)
        twoii_returns, twoii_closes = build_index_maps("^TWOII", interval_type)

        export_cutoff = (datetime.now() - timedelta(days=EXPORT_DAYS[interval_type])).strftime("%Y-%m-%d %H:%M:%S")

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

            for row in rows:
                if row["bar_time"] >= export_cutoff:
                    all_rows.append(row)

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


def save_indicator_rows(rows):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    ensure_indicator_table(cur)

    cur.execute("""
        DELETE FROM k_bar_indicators
        WHERE NOT EXISTS (
            SELECT 1
            FROM k_bars
            WHERE k_bars.symbol = k_bar_indicators.symbol
              AND k_bars.interval_type = k_bar_indicators.interval_type
              AND k_bars.bar_time = k_bar_indicators.bar_time
        )
    """)

    if rows:
        columns = [name for name, _ in INDICATOR_DB_COLUMNS]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ", ".join(["?"] * len(columns))
        column_names = ", ".join(columns)
        update_columns = [
            name for name in columns
            if name not in ("symbol", "interval_type", "bar_time", "created_at")
        ]
        update_sql = ", ".join(f"{name} = excluded.{name}" for name in update_columns)

        cur.executemany(
            f"""
            INSERT INTO k_bar_indicators ({column_names})
            VALUES ({placeholders})
            ON CONFLICT(symbol, interval_type, bar_time)
            DO UPDATE SET {update_sql}
            """,
            [
                [indicator_db_value(row, column_name, now) for column_name in columns]
                for row in rows
            ],
        )

    conn.commit()
    conn.close()

    return len(rows)


def export_one_csv():
    rows = get_all_rows_with_indicators()
    indicator_count = save_indicator_rows(rows)

    headers = [
        "record_type",
        "symbol",
        "base_code",
        "name",
        "item_type",
        "interval_type",
        "bar_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "scan_time",
        "change_pct",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "ma120",
        "bias5",
        "bias10",
        "bias20",
        "bias60",
        "bias120",
        "bias5_chg",
        "bias10_chg",
        "bias20_chg",
        "bias60_chg",
        "bias120_chg",
        "rsi14",
        "k9",
        "d9",
        "j9",
        "williams_r14",
        "dif",
        "macd",
        "osc",
        "volume_ma5",
        "volume_ratio",
        "vol_std_score",
        "vwap",
        "obv",
        "vzo14",
        "money_flow_index",
        "volume_price_trend",
        "vzo",
        "mfi14",
        "vpt",
        "day_range_pos",
        "bb_mid20",
        "bb_upper20",
        "bb_lower20",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "bb_width",
        "price_loc_bb",
        "atr14",
        "plus_di14",
        "minus_di14",
        "plus_di",
        "minus_di",
        "adx14",
        "high_52w",
        "low_52w",
        "dist_high_52w_pct",
        "dist_low_52w_pct",
        "beta20",
        "corr20",
        "relative_strength_pct",
        "stock_index_ratio",
        "ma_slope_20",
        "gap_pct",
        "tail_ratio",
        "upper_tail_ratio",
        "lower_tail_ratio",
        "short_term_score",
        "note",
    ]

    with open(EXPORT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for row in rows:
            writer.writerow([
                row["interval_type"],
                row["symbol"],
                row["base_code"],
                row["name"],
                row["item_type"],
                row["interval_type"],
                row["bar_time"],
                round_value(row["open"]),
                round_value(row["high"]),
                round_value(row["low"]),
                round_value(row["close"]),
                round_value(row["volume"]),
                row["scan_time"],
                round_value(row.get("change_pct")),
                round_value(row.get("ma5")),
                round_value(row.get("ma10")),
                round_value(row.get("ma20")),
                round_value(row.get("ma60")),
                round_value(row.get("ma120")),
                round_value(row.get("bias5")),
                round_value(row.get("bias10")),
                round_value(row.get("bias20")),
                round_value(row.get("bias60")),
                round_value(row.get("bias120")),
                round_value(row.get("bias5_chg")),
                round_value(row.get("bias10_chg")),
                round_value(row.get("bias20_chg")),
                round_value(row.get("bias60_chg")),
                round_value(row.get("bias120_chg")),
                round_value(row.get("rsi14")),
                round_value(row.get("k9")),
                round_value(row.get("d9")),
                round_value(row.get("j9")),
                round_value(row.get("williams_r14")),
                round_value(row.get("dif")),
                round_value(row.get("macd")),
                round_value(row.get("osc")),
                round_value(row.get("volume_ma5")),
                round_value(row.get("volume_ratio")),
                round_value(row.get("vol_std_score")),
                round_value(row.get("vwap")),
                round_value(row.get("obv")),
                round_value(row.get("vzo14")),
                round_value(row.get("money_flow_index")),
                round_value(row.get("volume_price_trend")),
                round_value(row.get("vzo")),
                round_value(row.get("mfi14")),
                round_value(row.get("vpt")),
                round_value(row.get("day_range_pos")),
                round_value(row.get("bb_mid20")),
                round_value(row.get("bb_upper20")),
                round_value(row.get("bb_lower20")),
                round_value(row.get("bb_upper")),
                round_value(row.get("bb_middle")),
                round_value(row.get("bb_lower")),
                round_value(row.get("bb_width")),
                round_value(row.get("price_loc_bb")),
                round_value(row.get("atr14")),
                round_value(row.get("plus_di14")),
                round_value(row.get("minus_di14")),
                round_value(row.get("plus_di")),
                round_value(row.get("minus_di")),
                round_value(row.get("adx14")),
                round_value(row.get("high_52w")),
                round_value(row.get("low_52w")),
                round_value(row.get("dist_high_52w_pct")),
                round_value(row.get("dist_low_52w_pct")),
                round_value(row.get("beta20")),
                round_value(row.get("corr20")),
                round_value(row.get("relative_strength_pct")),
                round_value(row.get("stock_index_ratio")),
                round_value(row.get("ma_slope_20")),
                round_value(row.get("gap_pct")),
                round_value(row.get("tail_ratio")),
                round_value(row.get("upper_tail_ratio")),
                round_value(row.get("lower_tail_ratio")),
                round_value(row.get("short_term_score")),
                "Network Ping Test 資料",
            ])

    return EXPORT_FILE, len(rows), indicator_count


def display_item_type(item_type):
    mapping = {
        "stock": "Network Speed",
        "index": "Benchmark Ping",
    }
    return mapping.get(item_type, item_type)

def get_latest_bar_time(symbol, interval_type):
    conn = sqlite3.connect(DB_NAME)
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
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT symbol, name, interval_type, bar_time, close_price, scan_time
        FROM k_bars
        WHERE id IN (
            SELECT MAX(id)
            FROM k_bars
            GROUP BY symbol, interval_type
        )
        ORDER BY symbol, interval_type
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
    log("保存期間：1m 14天、5m 30天、30m 60天、1d 180天、1wk 3年")
    log("匯出：單一 Network Ping Test CSV")
    log("分析欄位：MA/RSI/KD/MACD/BB/ATR/ADX/BIAS/VWAP/OBV/VZO/MFI/VPT/Williams%R")
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

    export_file, row_count, indicator_count = export_one_csv()

    log("")
    log(f"SQLite k_bar_indicators updated: {indicator_count} rows")
    log(f"CSV 已輸出：{export_file}，共 {row_count} 筆 Network Ping Test 資料")
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

result_text = tk.Text(root, width=150, height=40)
result_text.pack(padx=10, pady=10)

root.mainloop()
