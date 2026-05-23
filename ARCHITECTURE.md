# stock_scanner 重構架構規格
> 給 Claude Code 執行用。請依照此規格從頭建立新版 `stock_scanner.py`。

---

## 專案背景

將兩個現有程式合併重構：
- `stock_scanner.py`：多週期掃描（1m/5m/30m/1d/1wk），有 tkinter GUI，有指標計算
- `scan_all_1d.py`：日K掃描，有官方 TWSE/TPEX 籌碼模組，架構較乾淨

目標：合併成一個新版 `stock_scanner.py`，保留多週期能力，加入籌碼，優化 DB 和 CSV。

---

## 檔案結構

```
stock_scanner.py      ← 主程式（重構目標）
stock_list.txt        ← 股票清單（已有，不動）
chip_cache/           ← 籌碼 cache 目錄（自動建立）
stock_scanner.db      ← SQLite DB（自動建立）
snapshot_aftermarket.csv   ← 盤後 AI 用 CSV（自動產生）
snapshot_intraday.csv      ← 盤中 AI 用 CSV（自動產生）
ai_training.csv            ← AI 訓練用 CSV（自動產生）
```

---

## DB Schema

### 核心原則
- 廢棄舊版 `k_bars` + `k_bar_indicators` + `q_*` 系列
- 改用以下 6 張表

---

### Table: `symbols`

```sql
CREATE TABLE IF NOT EXISTS symbols (
    symbol      TEXT PRIMARY KEY,   -- Yahoo 代號，如 2330.TW
    base_code   TEXT NOT NULL,       -- 純代號，如 2330
    name        TEXT,
    item_type   TEXT,                -- 'stock' | 'index' | 'etf'
    market      TEXT,                -- 'TW' | 'TWO' | 'index'
    first_seen  TEXT,
    updated_at  TEXT
);
```

---

### Table: `k_bars`

```sql
CREATE TABLE IF NOT EXISTS k_bars (
    symbol          TEXT NOT NULL,
    interval_type   TEXT NOT NULL,   -- '1m'|'5m'|'30m'|'1d'|'1wk'
    bar_time        TEXT NOT NULL,   -- ISO 格式，如 2025-05-21 09:01:00
    open_price      REAL,
    high_price      REAL,
    low_price       REAL,
    close_price     REAL,
    volume          REAL,
    fetch_time      TEXT,
    PRIMARY KEY (symbol, interval_type, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_k_bars_symbol_interval_time
    ON k_bars (symbol, interval_type, bar_time DESC);
CREATE INDEX IF NOT EXISTS idx_k_bars_interval_time
    ON k_bars (interval_type, bar_time DESC);
```

**保留期限（寫入時清除過期資料）：**

| interval_type | 保留天數 |
|---|---|
| 1m  | 5 天 |
| 5m  | 20 天 |
| 30m | 60 天 |
| 1d  | 1095 天（3年） |
| 1wk | 1825 天（5年） |

---

### Table: `k_bar_features`

技術指標，隨 `k_bars` 寫入同步計算並儲存。
PK 與 `k_bars` 相同：`(symbol, interval_type, bar_time)`。

```sql
CREATE TABLE IF NOT EXISTS k_bar_features (
    symbol          TEXT NOT NULL,
    interval_type   TEXT NOT NULL,
    bar_time        TEXT NOT NULL,

    -- 價格衍生
    change_pct      REAL,
    gap_pct         REAL,
    upper_tail_ratio REAL,
    lower_tail_ratio REAL,
    tail_ratio      REAL,
    day_range_pos   REAL,

    -- 均線
    ma5             REAL,
    ma10            REAL,
    ma20            REAL,
    ma60            REAL,
    ma120           REAL,
    ma_slope_20     REAL,
    bias20          REAL,

    -- 動能
    rsi14           REAL,
    k9              REAL,
    d9              REAL,
    j9              REAL,
    dif             REAL,
    macd            REAL,
    osc             REAL,
    williams_r14    REAL,

    -- 量能
    volume_ma5      REAL,
    volume_ratio    REAL,
    vol_std_score   REAL,
    vwap            REAL,
    obv             REAL,
    mfi14           REAL,

    -- 波動/趨勢
    atr14           REAL,
    adx14           REAL,
    plus_di         REAL,
    minus_di        REAL,
    bb_upper        REAL,
    bb_middle       REAL,
    bb_lower        REAL,
    bb_width        REAL,
    price_loc_bb    REAL,

    -- 52週
    high_52w        REAL,
    low_52w         REAL,
    dist_high_52w_pct REAL,
    dist_low_52w_pct  REAL,

    -- 相對強弱（對大盤）
    beta20          REAL,
    corr20          REAL,
    relative_strength_pct REAL,
    stock_index_ratio     REAL,

    -- 綜合評分
    daily_score     REAL,
    short_term_score REAL,
    reason          TEXT,

    updated_at      TEXT,
    PRIMARY KEY (symbol, interval_type, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_k_bar_features_interval_time
    ON k_bar_features (interval_type, bar_time DESC);
CREATE INDEX IF NOT EXISTS idx_k_bar_features_score
    ON k_bar_features (interval_type, daily_score DESC);
```

---

### Table: `chip_daily`

每日籌碼，**只有日頻資料**。一檔股票每天一筆。

```sql
CREATE TABLE IF NOT EXISTS chip_daily (
    symbol              TEXT NOT NULL,
    base_code           TEXT NOT NULL,
    trade_date          TEXT NOT NULL,   -- 格式 YYYY-MM-DD

    -- 三大法人（單位：股）
    foreign_buy_sell            REAL DEFAULT 0,
    investment_trust_buy_sell   REAL DEFAULT 0,
    dealer_buy_sell             REAL DEFAULT 0,
    institutional_total_buy_sell REAL DEFAULT 0,

    -- 三大法人 3 日累積
    foreign_buy_sell_3d             REAL DEFAULT 0,
    investment_trust_buy_sell_3d    REAL DEFAULT 0,
    dealer_buy_sell_3d              REAL DEFAULT 0,
    institutional_total_buy_sell_3d REAL DEFAULT 0,

    -- 融資融券
    margin_change       REAL DEFAULT 0,
    short_change        REAL DEFAULT 0,
    margin_balance      REAL DEFAULT 0,
    short_balance       REAL DEFAULT 0,
    margin_short_ratio  REAL DEFAULT 0,

    -- 籌碼評分（由 score_bullish_chip / score_bearish_chip 計算）
    bullish_chip_score  REAL DEFAULT 0,
    bearish_chip_score  REAL DEFAULT 0,
    bullish_chip_reason TEXT,
    bearish_chip_reason TEXT,

    -- 資料狀態
    chip_data_source    TEXT,   -- 'official_twse_tpex' | 'finmind' | 'cache'
    chip_data_status    TEXT,   -- 'ok' | 'partial_chip_data' | 'missing_chip_data' | 'api_failed'
    chip_data_date      TEXT,   -- 實際籌碼資料日期（可能落後 1-2 天）

    updated_at          TEXT,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_chip_daily_date
    ON chip_daily (trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_chip_daily_symbol_date
    ON chip_daily (symbol, trade_date DESC);
```

---

### Table: `ai_labels`

未來報酬標籤，供 AI 訓練用。

```sql
CREATE TABLE IF NOT EXISTS ai_labels (
    symbol          TEXT NOT NULL,
    interval_type   TEXT NOT NULL,
    bar_time        TEXT NOT NULL,
    future_1d_return  REAL,
    future_3d_return  REAL,
    max_upside_5d     REAL,
    drawdown_5d       REAL,
    buy_signal        INTEGER,
    entry_price       REAL,
    ai_signal_score   REAL,
    label_ready       INTEGER DEFAULT 0,
    created_at        TEXT,
    updated_at        TEXT,
    PRIMARY KEY (symbol, interval_type, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_ai_labels_buy_signal
    ON ai_labels (buy_signal, interval_type, bar_time);
```

---

### View: `v_ai_features_intraday`

給**盤中/當沖 AI** 使用。JOIN **前一交易日**籌碼（避免 data leakage）。

```sql
CREATE VIEW IF NOT EXISTS v_ai_features_intraday AS
SELECT
    f.symbol,
    f.interval_type,
    f.bar_time,
    s.base_code,
    s.name,
    s.item_type,
    s.market,
    b.open_price,
    b.high_price,
    b.low_price,
    b.close_price,
    b.volume,
    -- 所有技術指標
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
    -- 前一交易日籌碼（盤中不能用當日，防 data leakage）
    c.foreign_buy_sell, c.investment_trust_buy_sell,
    c.dealer_buy_sell, c.institutional_total_buy_sell,
    c.foreign_buy_sell_3d, c.investment_trust_buy_sell_3d,
    c.dealer_buy_sell_3d, c.institutional_total_buy_sell_3d,
    c.margin_change, c.short_change, c.margin_balance, c.short_balance,
    c.margin_short_ratio, c.bullish_chip_score, c.bearish_chip_score,
    c.chip_data_status, c.chip_data_date,
    -- AI labels
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
   AND l.bar_time = f.bar_time;
```

---

### View: `v_ai_features_aftermarket`

給**盤後選股/明日預測 AI** 使用。JOIN **當日**籌碼（盤後才公布，合法使用）。
只包含 `1d` 和 `1wk` 資料。

```sql
CREATE VIEW IF NOT EXISTS v_ai_features_aftermarket AS
SELECT
    f.symbol,
    f.interval_type,
    f.bar_time,
    s.base_code,
    s.name,
    s.item_type,
    s.market,
    b.open_price,
    b.high_price,
    b.low_price,
    b.close_price,
    b.volume,
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
    -- 當日籌碼（盤後才公布，此處合法）
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
WHERE f.interval_type IN ('1d', '1wk');
```

---

## CSV 設計

### 1. `snapshot_aftermarket.csv`
**用途：** 盤後丟給 AI 分析，每檔股票一行，只有最新日K。

**查詢邏輯：**
```sql
SELECT * FROM v_ai_features_aftermarket
WHERE interval_type = '1d'
  AND bar_time = (
      SELECT MAX(bar_time) FROM k_bar_features
      WHERE interval_type = '1d'
  )
ORDER BY daily_score DESC;
```

**欄位順序（約 60 欄）：**
```
symbol, base_code, name, market, bar_time,
open_price, high_price, low_price, close_price, volume,
change_pct, gap_pct, ma5, ma10, ma20, ma60, ma120,
ma_slope_20, bias20, rsi14, k9, d9, j9,
dif, macd, osc, williams_r14,
volume_ratio, vol_std_score, vwap, obv, mfi14,
atr14, adx14, plus_di, minus_di,
bb_upper, bb_middle, bb_lower, bb_width, price_loc_bb,
upper_tail_ratio, lower_tail_ratio, day_range_pos,
high_52w, low_52w, dist_high_52w_pct, dist_low_52w_pct,
relative_strength_pct, beta20,
foreign_buy_sell, investment_trust_buy_sell, dealer_buy_sell,
institutional_total_buy_sell,
foreign_buy_sell_3d, investment_trust_buy_sell_3d,
institutional_total_buy_sell_3d,
margin_change, short_change, margin_balance, short_balance,
margin_short_ratio, bullish_chip_score, bearish_chip_score,
chip_data_status, chip_data_date,
daily_score, short_term_score, reason
```

---

### 2. `snapshot_intraday.csv`
**用途：** 盤中丟給 AI 分析，每檔股票最新 5m K 棒狀態。

**查詢邏輯：**
```sql
SELECT * FROM v_ai_features_intraday
WHERE interval_type = '5m'
  AND bar_time = (
      SELECT MAX(bar_time) FROM k_bar_features f2
      WHERE f2.symbol = k_bar_features.symbol
        AND f2.interval_type = '5m'
  )
ORDER BY daily_score DESC;
```

**欄位：** 同 `snapshot_aftermarket.csv`，但包含 5m 的 `vwap`（盤中 VWAP）。

---

### 3. `ai_training.csv`
**用途：** AI 訓練用，包含歷史所有 bar + features + labels。
只匯出 `label_ready = 1` 的資料，只匯出 `1d` 資料（訓練主力）。

**查詢邏輯：**
```sql
SELECT * FROM v_ai_features_aftermarket
WHERE interval_type = '1d'
  AND label_ready = 1
ORDER BY symbol, bar_time;
```

**額外欄位（在此 CSV 才有）：**
```
future_1d_return, future_3d_return, max_upside_5d, drawdown_5d,
buy_signal, entry_price, ai_signal_score, label_ready
```

---

## 程式模組結構

```
stock_scanner.py
├── [設定區]
│   ├── 路徑常數（DB_NAME, LIST_FILE, CSV paths, CHIP_CACHE_DIR）
│   ├── KEEP_DAYS（各 interval 保留天數）
│   ├── YAHOO_CONFIG（各 interval 對應 Yahoo range/interval 參數）
│   └── INTERVALS = ['1m', '5m', '30m', '1d', '1wk']
│
├── [工具函數] 從 scan_all_1d.py 直接搬
│   ├── log(), warn()
│   ├── safe_float(), safe_text(), is_missing()
│   ├── round_value()
│   ├── gt(), ge(), le(), lt(), between()
│   └── parse_trade_date()
│
├── [DB 模組]
│   ├── connect_db()          ← WAL mode, NORMAL sync
│   ├── init_db(conn)         ← 建立 6 張表 + 2 個 View + 索引
│   └── ensure_columns()      ← migrate 舊欄位用
│
├── [股票清單模組]
│   └── read_stock_list()     ← 讀 stock_list.txt，格式同現在
│
├── [Yahoo 模組]
│   ├── fetch_yahoo_chart(symbol, range_value, interval)
│   ├── resolve_symbol(base_code)   ← 自動試 .TW / .TWO
│   └── save_bars(conn, symbol, base_code, name, item_type, interval_type)
│       ├── 呼叫 fetch_yahoo_chart
│       ├── INSERT OR REPLACE INTO k_bars
│       └── DELETE 過期資料
│
├── [指標計算模組] 合併兩個程式的最佳版本
│   ├── sma(), ema_series(), stddev()
│   ├── calculate_indicators(rows, index_return_map, index_close_map)
│   │   ├── MA5/10/20/60/120, slope, bias20
│   │   ├── RSI14
│   │   ├── KD9 (K, D, J)
│   │   ├── MACD (DIF, MACD, OSC)
│   │   ├── Williams %R 14
│   │   ├── BB (upper/middle/lower/width/price_loc_bb)
│   │   ├── ATR14, ADX14, +DI, -DI
│   │   ├── Volume MA5, Volume Ratio, Vol Std Score
│   │   ├── VWAP (當根 K 棒累積，1m/5m/30m 有意義)
│   │   ├── OBV, MFI14
│   │   ├── 52週高低, dist_high/low_52w_pct
│   │   ├── Beta20, Corr20 (對大盤)
│   │   ├── change_pct, gap_pct
│   │   ├── upper/lower/tail_ratio, day_range_pos
│   │   ├── daily_score, short_term_score, reason (從 scan_all_1d.py 的 score_row 搬)
│   │   └── relative_strength_pct, stock_index_ratio
│   └── save_features(conn, symbol, interval_type, rows)
│       └── INSERT OR REPLACE INTO k_bar_features
│
├── [AI Labels 模組]
│   ├── add_ai_labels(rows)
│   │   ├── future_1d_return, future_3d_return
│   │   ├── max_upside_5d, drawdown_5d
│   │   ├── buy_signal (ai_signal_score >= 5)
│   │   ├── entry_price
│   │   └── label_ready (未來5d資料足夠才為1)
│   └── save_ai_labels(conn, symbol, interval_type, rows)
│
├── [籌碼模組] 完整從 scan_all_1d.py 搬
│   ├── === 工具 ===
│   │   ├── chip_cache_path(kind, trade_date)
│   │   ├── compact_number()
│   │   ├── normalize_stock_id()
│   │   ├── read_csv_dicts(), write_csv_dicts()
│   │   ├── twse_date(), roc_date()
│   │   └── fetch_json_url()
│   │
│   ├── === 官方 API ===
│   │   ├── fetch_official_rows(kind, trade_date, stats)
│   │   │   ├── kind: 'twse_institutional' | 'twse_margin'
│   │   │   │         'tpex_institutional' | 'tpex_margin'
│   │   │   └── 有 cache 先讀 cache
│   │   ├── normalize_twse_institutional_payload()
│   │   ├── normalize_tpex_institutional_payload()
│   │   ├── normalize_twse_margin_payload()
│   │   └── normalize_tpex_margin_payload()
│   │
│   ├── === 資料整合 ===
│   │   ├── group_official_institutional_rows()
│   │   ├── group_official_margin_rows()
│   │   └── fetch_official_chip_data(latest_trade_date)
│   │       └── 抓最近 5 個交易日，取第一個有資料的日期
│   │
│   ├── === 評分 ===
│   │   ├── score_bullish_chip(row)   ← 從 scan_all_1d.py 搬
│   │   └── score_bearish_chip(row)   ← 從 scan_all_1d.py 搬
│   │
│   └── save_chip_daily(conn, chip_result, latest_trade_date)
│       └── INSERT OR REPLACE INTO chip_daily
│           (一次把所有股票的籌碼寫入)
│
├── [主掃描流程]
│   ├── build_index_maps(conn, index_symbol, interval_type)
│   │   └── 讀大盤 K 線，建立 {bar_time: return} 和 {bar_time: close} map
│   ├── load_bars_for_calc(conn, symbol, interval_type)
│   │   └── 從 k_bars 讀出，格式化為 rows dict list
│   ├── process_symbol(conn, symbol, base_code, name, item_type)
│   │   ├── for interval in INTERVALS:
│   │   │   ├── save_bars()           ← 拉 Yahoo，寫 k_bars
│   │   │   ├── rows = load_bars_for_calc()
│   │   │   ├── rows = calculate_indicators(rows, index_maps)
│   │   │   ├── save_features()       ← 寫 k_bar_features
│   │   │   └── save_ai_labels()      ← 寫 ai_labels
│   │   └── random_sleep()
│   └── update_all()                  ← GUI 按鈕觸發
│       ├── 讀 stock_list.txt
│       ├── for each stock: process_symbol()
│       ├── 取最新 1d bar_time → fetch_official_chip_data()
│       ├── save_chip_daily()
│       └── export_csvs()
│
├── [CSV 匯出模組]
│   ├── export_snapshot_aftermarket(conn)
│   │   └── 查 v_ai_features_aftermarket，只取最新日K，每股一行
│   ├── export_snapshot_intraday(conn)
│   │   └── 查 v_ai_features_intraday，只取最新 5m K，每股一行
│   ├── export_ai_training(conn)
│   │   └── 查 v_ai_features_aftermarket，只取 label_ready=1 的 1d 資料
│   └── export_csvs(conn)
│       └── 呼叫以上三個
│
└── [GUI 模組] tkinter，保留現有外觀
    ├── log(message)         ← 寫入 result_text
    ├── 按鈕：開始掃描 / 停止掃描 / 匯出CSV
    └── root.mainloop()
```

---

## 關鍵實作細節

### 1. Yahoo Config
```python
KEEP_DAYS = {
    '1m':  5,
    '5m':  20,
    '30m': 60,
    '1d':  1095,
    '1wk': 1825,
}

YAHOO_CONFIG = {
    '1m':  {'range': '5d',  'interval': '1m'},
    '5m':  {'range': '60d', 'interval': '5m'},
    '30m': {'range': '60d', 'interval': '30m'},
    '1d':  {'range': '3y',  'interval': '1d'},
    '1wk': {'range': '5y',  'interval': '1wk'},
}
```

### 2. 籌碼寫入時機
```
每次 update_all() 結束後，統一呼叫一次 fetch_official_chip_data()
用最新的 1d bar_time 當作 latest_trade_date
將結果寫入 chip_daily（所有股票一次批次寫入）
```

### 3. 籌碼評分函數
完整搬用 `scan_all_1d.py` 的：
- `score_bullish_chip(row)` → `bullish_chip_score`, `bullish_chip_reason`
- `score_bearish_chip(row)` → `bearish_chip_score`, `bearish_chip_reason`

這兩個函數用到的欄位：
`foreign_buy_sell`, `investment_trust_buy_sell`, `dealer_buy_sell`,
`institutional_total_buy_sell`, `foreign_buy_sell_3d`, `investment_trust_buy_sell_3d`,
`institutional_total_buy_sell_3d`, `margin_change`, `short_change`,
`change_pct`, `upper_tail_ratio`, `close_position`, `close`, `open_price`, `ma20`

### 4. AI labels 計算
```python
bars_per_day = {'1m': 270, '5m': 54, '30m': 9, '1d': 1, '1wk': 1}
horizon_1d = bars_per_day[interval_type]       # 1天後
horizon_3d = horizon_1d * 3                     # 3天後
horizon_5d = horizon_1d * 5                     # 5天後
future_1d_return  = (close[i+horizon_1d] - close[i]) / close[i] * 100
future_3d_return  = (close[i+horizon_3d] - close[i]) / close[i] * 100
max_upside_5d     = (max(high[i+1..i+horizon_5d]) - close[i]) / close[i] * 100
drawdown_5d       = (min(low[i+1..i+horizon_5d]) - close[i]) / close[i] * 100
label_ready       = 1 if 未來5d資料完整 else 0
```

### 5. VWAP 計算方式
- `1m`, `5m`, `30m`：**盤內累積 VWAP**（每天重置）
  - 依 `bar_time` 判斷是否同一天，是則累積，否則重置
- `1d`, `1wk`：**歷史累積 VWAP**（全部累積不重置）

### 6. 大盤指數處理
```python
# stock_list.txt 中需包含大盤指數
# 例如：^TWII, 台股加權指數, index
# 例如：^TWOII, 上櫃指數, index
# 計算 beta20, corr20, relative_strength_pct 時使用
# 讀取方式：build_index_maps(conn, '^TWII', interval_type)
```

### 7. CSV 數值格式
```python
# 所有 REAL 欄位：round(value, 4)
# None → 空字串 ''
# 編碼：utf-8-sig（Excel 開啟相容）
```

### 8. DB 連線設定
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA temp_store=MEMORY")
conn.execute("PRAGMA cache_size=-32000")  # 32MB
```

---

## 不需要實作的部分

- `q_price_bars`, `q_instruments`, `q_timeframes`, `q_indicator_features`, `q_ai_labels` → 全部刪除
- `k_bars`（舊版）, `k_bar_indicators` → 全部刪除
- FinMind 主流程 → 只保留 `fetch_finmind_chip_data_optional()` 作為 fallback，不在主流程呼叫
- `candidate_200.csv`, `top_stocks.csv`, `after_market_top30.csv` 等 → 不在此版本實作

---

## 實作順序建議（給 Claude Code）

1. 設定區、常數、路徑
2. 工具函數（全從 scan_all_1d.py 搬）
3. DB 模組（init_db 建立 6 張表 + 2 個 View）
4. 股票清單模組
5. Yahoo 模組（fetch + save_bars）
6. 指標計算模組（合併兩個程式最佳版本）
7. AI Labels 模組
8. 籌碼模組（完整從 scan_all_1d.py 搬，只改 save_chip_daily）
9. 主掃描流程
10. CSV 匯出模組
11. GUI 模組

---

## 給 Claude Code 的額外指示

- 使用 Python 3.8+ 語法
- 只用標準函式庫 + `tkinter`（不引入新的第三方套件）
- requests 換成 `urllib.request`（與 scan_all_1d.py 一致）
- 所有 DB 操作使用 `conn.executemany()` 批次寫入
- 籌碼模組函數名稱、邏輯、欄位名稱完全對應 `scan_all_1d.py`，方便日後維護
- GUI 保留現有外觀：tkinter Text widget 顯示 log，兩個按鈕（開始/停止）
- 新增第三個按鈕：「匯出 CSV」（單獨觸發 export_csvs）
- stop_scan 全域旗標控制停止邏輯不變
