# stock_scanner 重構架構規格
> 給 Claude Code 執行用。請依照此規格從頭建立新版 `stock_scanner.py`。

---

## 資料來源優先順序

每個欄位的資料依以下順序嘗試，取第一個成功的來源：

```
1. Yahoo Finance   → K 線（全部週期）、技術指標、美國指數（^VIX / ^GSPC / ^IXIC）
2. TWSE 官方 API  → 台股主板三大法人買賣超、融資融券
3. TPEX 官方 API  → 上櫃股三大法人買賣超、融資融券
4. FinMind Free   → PER/PBR、外資持股比率（非買賣超）、以上來源失敗時的籌碼 fallback
```

**原則：**
- Yahoo / TWSE / TPEX 任何一個失敗，不影響其他來源
- FinMind 失敗時主流程繼續，只跳過 FinMind 補強
- `chip_data_source` 欄位記錄每檔股票當日籌碼實際來自哪個來源

---

## 專案背景

將兩個現有程式合併重構：
- `stock_scanner.py`：多週期掃描（1m/5m/30m/1d/1wk），有 tkinter GUI，有指標計算
- `scan_all_1d.py`：日K掃描，有官方 TWSE/TPEX 籌碼模組，架構較乾淨

目標：合併成一個新版 `stock_scanner.py`，保留多週期能力，加入籌碼，優化 DB 和 CSV。

---

## 檔案結構

```
stock_scanner.py           ← 主程式（重構目標）
finmind_client.py          ← FinMind API 存取層
finmind_features.py        ← FinMind DB 讀寫、缺口判斷
scanner_config.ini         ← 外部設定檔（保留天數等，不存在時用預設值）
stock_list.txt             ← 股票清單（已有，不動）
chip_cache/                ← 籌碼 cache 目錄（自動建立）
stock_scanner.db           ← SQLite DB（自動建立）
snapshot_aftermarket.csv   ← 盤後 AI 用 CSV（自動產生）
snapshot_intraday.csv      ← 盤中 AI 用 CSV（自動產生）
ai_training.csv            ← AI 訓練用 CSV（自動產生）
```

---

## DB Schema

### 核心原則
- 廢棄舊版 `k_bars` + `k_bar_indicators` + `q_*` 系列
- 改用以下 9 張核心資料表 + 2 個 View

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

### Table: `market_context`

每日美國市場情緒，由 Yahoo Finance 抓取美國指數後計算。
每天一筆，所有台股共用同一筆（不分個股）。

```sql
CREATE TABLE IF NOT EXISTS market_context (
    trade_date      TEXT PRIMARY KEY,   -- 對應台灣交易日，格式 YYYY-MM-DD

    -- VIX 恐慌指數（來自 Yahoo ^VIX）
    vix_close           REAL,           -- 收盤值
    vix_change_pct      REAL,           -- 當日漲跌幅 %
    vix_ma10            REAL,           -- 10日均線

    -- S&P 500（來自 Yahoo ^GSPC）
    spx_close           REAL,
    spx_1d_return       REAL,           -- 當日報酬率 %
    spx_5d_return       REAL,           -- 5日累積報酬率 %
    spx_above_ma20      INTEGER,        -- 1 = 站上 MA20，0 = 跌破

    -- NASDAQ（來自 Yahoo ^IXIC）
    ndx_close           REAL,
    ndx_1d_return       REAL,
    ndx_5d_return       REAL,

    -- 合成情緒標籤（程式計算）
    -- 規則：vix < 15 → 'greed'；15-20 → 'neutral'；20-30 → 'elevated'；> 30 → 'fear'
    us_sentiment_label  TEXT,           -- 'fear' | 'elevated' | 'neutral' | 'greed'
    us_sentiment_score  REAL,           -- 0-100，由 VIX + SPX 趨勢合成

    updated_at          TEXT
);
```

**美國市場情緒分數計算（`us_sentiment_score`）：**

```
base_score = 100 - min(vix_close * 2, 100)          # VIX 低 → 分高
spx_bonus  = spx_5d_return * 2                        # SPX 上漲加分
us_sentiment_score = clamp(base_score + spx_bonus, 0, 100)
```

**資料抓取時機：** 在 `update_all()` 開始時先抓一次，寫入 `market_context`，
台灣市場尚未開盤時使用前一個美國交易日的收盤資料（Yahoo 自動返回最近交易日）。

---

### Table: `fetch_meta`

追蹤每個 (symbol, interval_type) 的抓取狀態。
決定每次執行要做「首次完整回補」、「調整後重抓」或「一般增量更新」。

```sql
CREATE TABLE IF NOT EXISTS fetch_meta (
    symbol          TEXT NOT NULL,
    interval_type   TEXT NOT NULL,   -- '1m'|'5m'|'30m'|'1d'|'1wk'

    first_seen_date TEXT,            -- 第一次出現在 stock_list.txt 的日期
    last_full_fetch TEXT,            -- 最後一次完整回補的日期（NULL = 尚未回補）
    last_update     TEXT,            -- 最後一次增量更新的日期時間（ISO 格式）
    bar_count       INTEGER DEFAULT 0, -- 目前 k_bars 中此 symbol/interval 的筆數（寫入後更新）

    -- Yahoo 調整偵測
    last_adj_check  TEXT,            -- 最後一次執行調整比對的時間
    adj_detected_at TEXT,            -- 最後一次偵測到 Yahoo 調整的時間（NULL = 從未偵測到）
    adj_count       INTEGER DEFAULT 0, -- 歷史上累計偵測到的調整次數

    PRIMARY KEY (symbol, interval_type)
);
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
    -- foreign_buy_sell 正數 = 外資買超；負數 = 外資賣超
    c.foreign_buy_sell, c.investment_trust_buy_sell,
    c.dealer_buy_sell, c.institutional_total_buy_sell,
    c.foreign_buy_sell_3d, c.investment_trust_buy_sell_3d,
    c.dealer_buy_sell_3d, c.institutional_total_buy_sell_3d,
    c.margin_change, c.short_change, c.margin_balance, c.short_balance,
    c.margin_short_ratio, c.bullish_chip_score, c.bearish_chip_score,
    c.chip_data_source, c.chip_data_status, c.chip_data_date,
    -- FinMind 補強欄位（前一交易日，防 data leakage）
    fm.per, fm.pbr, fm.dividend_yield,
    fm.monthly_revenue, fm.revenue_mom, fm.revenue_yoy,
    fm.foreign_holding_ratio,
    fm.foreign_holding_change_5d,
    fm.foreign_holding_change_20d,
    fm.securities_lending_volume, fm.securities_lending_fee_rate,
    fm.day_trading_volume, fm.day_trading_ratio,
    -- 美國市場情緒（前一美國交易日，台灣盤中時美股已收盤）
    mc.vix_close, mc.vix_change_pct,
    mc.spx_1d_return, mc.spx_5d_return, mc.spx_above_ma20,
    mc.ndx_1d_return,
    mc.us_sentiment_label, mc.us_sentiment_score,
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
LEFT JOIN stock_fundamentals fm
    ON fm.symbol = f.symbol
   AND fm.trade_date = (
       SELECT MAX(trade_date) FROM stock_fundamentals fm2
       WHERE fm2.symbol = f.symbol
         AND fm2.trade_date < date(f.bar_time)
   )
LEFT JOIN market_context mc
    ON mc.trade_date = (
       SELECT MAX(trade_date) FROM market_context mc2
       WHERE mc2.trade_date < date(f.bar_time)
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
    -- foreign_buy_sell 正數 = 外資買超；負數 = 外資賣超
    c.foreign_buy_sell, c.investment_trust_buy_sell,
    c.dealer_buy_sell, c.institutional_total_buy_sell,
    c.foreign_buy_sell_3d, c.investment_trust_buy_sell_3d,
    c.dealer_buy_sell_3d, c.institutional_total_buy_sell_3d,
    c.margin_change, c.short_change, c.margin_balance, c.short_balance,
    c.margin_short_ratio, c.bullish_chip_score, c.bearish_chip_score,
    c.chip_data_source, c.chip_data_status, c.chip_data_date,
    -- FinMind 補強欄位（當日；盤後公布，合法使用）
    fm.per, fm.pbr, fm.dividend_yield,
    fm.monthly_revenue, fm.revenue_mom, fm.revenue_yoy,
    fm.foreign_holding_ratio,
    fm.foreign_holding_change_5d,
    fm.foreign_holding_change_20d,
    fm.securities_lending_volume, fm.securities_lending_fee_rate,
    fm.day_trading_volume, fm.day_trading_ratio,
    -- 美國市場情緒（當日；台灣盤後時美股已收盤或盤中，取最近有資料的一天）
    mc.vix_close, mc.vix_change_pct,
    mc.spx_1d_return, mc.spx_5d_return, mc.spx_above_ma20,
    mc.ndx_1d_return,
    mc.us_sentiment_label, mc.us_sentiment_score,
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
LEFT JOIN stock_fundamentals fm
    ON fm.symbol = f.symbol
   AND fm.trade_date = date(f.bar_time)
LEFT JOIN market_context mc
    ON mc.trade_date = (
       SELECT MAX(trade_date) FROM market_context mc2
       WHERE mc2.trade_date <= date(f.bar_time)
   )
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

**欄位順序（約 80 欄）：**
```
-- 識別
symbol, base_code, name, market, bar_time,

-- K 線
open_price, high_price, low_price, close_price, volume,

-- 技術指標：價格衍生
change_pct, gap_pct, upper_tail_ratio, lower_tail_ratio, day_range_pos,

-- 技術指標：均線
ma5, ma10, ma20, ma60, ma120, ma_slope_20, bias20,

-- 技術指標：動能
rsi14, k9, d9, j9, dif, macd, osc, williams_r14,

-- 技術指標：量能
volume_ratio, vol_std_score, vwap, obv, mfi14,

-- 技術指標：波動/趨勢
atr14, adx14, plus_di, minus_di,
bb_upper, bb_middle, bb_lower, bb_width, price_loc_bb,

-- 技術指標：52週
high_52w, low_52w, dist_high_52w_pct, dist_low_52w_pct,

-- 技術指標：相對大盤
relative_strength_pct, beta20, corr20, stock_index_ratio,

-- 籌碼：三大法人（正 = 買超，負 = 賣超）
foreign_buy_sell,               -- 外資單日買賣超（股）
investment_trust_buy_sell,      -- 投信單日買賣超（股）
dealer_buy_sell,                -- 自營商單日買賣超（股）
institutional_total_buy_sell,   -- 三大法人合計（股）

-- 籌碼：三大法人 3 日累積
foreign_buy_sell_3d,
investment_trust_buy_sell_3d,
dealer_buy_sell_3d,
institutional_total_buy_sell_3d,

-- 籌碼：融資融券
margin_change, short_change, margin_balance, short_balance, margin_short_ratio,

-- 籌碼：評分
bullish_chip_score, bearish_chip_score,

-- 籌碼：資料狀態
chip_data_source, chip_data_status, chip_data_date,

-- FinMind 基本面（TaiwanStockPER / MonthRevenue）
per, pbr, dividend_yield,
monthly_revenue, revenue_mom, revenue_yoy,

-- 外資持股比率（FinMind TaiwanStockShareholding）
foreign_holding_ratio,          -- 外資持股占發行股數比率（%）
foreign_holding_change_5d,      -- 當日 ratio - 往前第 5 筆有效交易日 ratio（+加碼、-減碼）
foreign_holding_change_20d,     -- 當日 ratio - 往前第 20 筆有效交易日 ratio

-- 借券 / 當沖（FinMind Phase 2）
securities_lending_volume, securities_lending_fee_rate,
day_trading_volume, day_trading_ratio,

-- 美國市場情緒（Yahoo Finance ^VIX / ^GSPC / ^IXIC）
vix_close,                      -- VIX 收盤（< 15 貪婪；> 30 恐慌）
vix_change_pct,                 -- VIX 當日漲跌幅（正 = 恐慌升溫）
spx_1d_return,                  -- S&P 500 當日報酬率 %
spx_5d_return,                  -- S&P 500 5 日累積報酬率 %
spx_above_ma20,                 -- 1 = SPX 站上 MA20；0 = 跌破
ndx_1d_return,                  -- NASDAQ 當日報酬率 %
us_sentiment_label,             -- 'fear' | 'elevated' | 'neutral' | 'greed'
us_sentiment_score,             -- 0-100，分數越高越樂觀

-- 綜合評分
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
│   ├── load_config()            ← 讀 scanner_config.ini；檔案不存在時用預設值
│   ├── KEEP_DAYS                ← 由 load_config() 產生，可外部修改
│   ├── YAHOO_CONFIG             ← 由 load_config() 產生
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
│   ├── init_db(conn)         ← 建立 9 張表 + 2 個 View + 索引
│   └── ensure_columns()      ← migrate 舊欄位用
│
├── [股票清單模組]
│   └── read_stock_list()     ← 讀 stock_list.txt，格式同現在
│
├── [Yahoo 模組]
│   ├── fetch_yahoo_chart(symbol, range_value, interval)
│   ├── resolve_symbol(base_code)   ← 自動試 .TW / .TWO
│   ├── determine_fetch_mode(conn, symbol, interval_type) → (mode, range)
│   │   ├── 查 fetch_meta；若不存在 → full_backfill（新股 / 首次）
│   │   ├── 若存在 → incremental（短窗口）
│   │   └── 增量抓完後呼叫 detect_price_adjustment；差異 > threshold → adj_refetch
│   └── save_bars(conn, symbol, base_code, name, item_type, interval_type)
│       ├── 呼叫 determine_fetch_mode() → mode
│       ├── mode=full_backfill/adj_refetch → INSERT OR REPLACE（全量覆蓋；adj 時先 DELETE）
│       ├── mode=incremental, bar < today → INSERT OR IGNORE（歷史凍結）
│       ├── mode=incremental, bar == today → INSERT OR REPLACE（當日刷新）
│       ├── DELETE 超過 KEEP_DAYS 的過期資料
│       └── UPDATE fetch_meta（last_full_fetch / last_update / adj_detected_at）
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
│       ├── bar_time.date() < today → INSERT OR IGNORE（歷史凍結）
│       └── bar_time.date() == today → INSERT OR REPLACE（當日刷新）
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
├── [美國市場情緒模組]
│   └── fetch_market_context(conn)
│       ├── 用 fetch_yahoo_chart 抓 ^VIX、^GSPC、^IXIC（range='5d', interval='1d'）
│       ├── 計算 vix_change_pct、spx_1d_return、spx_5d_return、spx_above_ma20、ndx_1d_return
│       ├── 計算 us_sentiment_score 與 us_sentiment_label
│       └── UPSERT → market_context（只在 update_all 開始時呼叫一次）
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
│       ├── trade_date < today AND existing status='ok' → 跳過（已確認資料，不覆蓋）
│       └── trade_date == today OR status != 'ok' → INSERT OR REPLACE
│           (一次把所有股票的籌碼寫入；盤中執行可能寫 'api_failed'，盤後重跑更新為 'ok')
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

## 完整資料欄位對應表（資料來源 × 欄位）

| 欄位群組 | 欄位 | 來源 | 必要性 |
|---|---|---|---|
| K 線 | open/high/low/close/volume | Yahoo | 必要 |
| 技術指標 | MA/RSI/KD/MACD/BB/ATR/ADX等 | Yahoo 計算 | 必要 |
| 相對大盤 | beta20, relative_strength_pct | Yahoo 計算 | 必要 |
| 外資買超/賣超 | foreign_buy_sell（正=買超，負=賣超） | TWSE/TPEX → FinMind fallback | 必要 |
| 投信買賣超 | investment_trust_buy_sell | TWSE/TPEX → FinMind fallback | 必要 |
| 自營商買賣超 | dealer_buy_sell | TWSE/TPEX → FinMind fallback | 必要 |
| 3日累積買賣超 | *_buy_sell_3d | 計算 | 重要 |
| 融資融券 | margin_balance, short_balance, margin_short_ratio | TWSE/TPEX → FinMind fallback | 必要 |
| 籌碼評分 | bullish/bearish_chip_score | 計算 | 必要 |
| **外資持股比率** | **foreign_holding_ratio** | **FinMind TaiwanStockShareholding** | 重要 |
| **持股比率變化** | **foreign_holding_change_5d/20d** | **計算（FinMind 歷史）** | 重要 |
| **VIX / 美國情緒** | **vix_close, us_sentiment_label** | **Yahoo ^VIX** | 重要 |
| **SPX / NASDAQ** | **spx_1d_return, ndx_1d_return** | **Yahoo ^GSPC / ^IXIC** | 重要 |
| PER/PBR | per, pbr, dividend_yield | FinMind TaiwanStockPER | 次要 |

**粗體欄位 = 此次新增，原架構未包含。**

---

## `update_all()` 完整執行順序

```text
1. fetch_market_context()          ← Yahoo 抓 ^VIX / ^GSPC / ^IXIC → market_context
2. for each stock:
     mode = determine_fetch_mode() ← 查 fetch_meta：full_backfill / adj_refetch / incremental
     save_bars()                   ← Yahoo 拉 K 線 → k_bars
                                      依 mode 決定 INSERT OR REPLACE / IGNORE
                                      mode=incremental 時先做調整偵測；若觸發改為 adj_refetch
     calculate_indicators()        ← 計算技術指標 → k_bar_features
     save_ai_labels()              ← 計算 AI 標籤 → ai_labels
     update fetch_meta             ← 更新 last_update / last_full_fetch / adj 欄位
3. fetch_official_chip_data()      ← TWSE + TPEX → chip_daily（chip_data_source='official'）
4. save_chip_daily()               ← 批次寫入 chip_daily
5. fm_manager.update_pipeline()    ← FinMind（ENABLE_FINMIND=1 才執行）
     5a. 檢查 API 剩餘次數
     5b. TaiwanStockPER           → stock_fundamentals
     5c. TaiwanStockShareholding  → stock_fundamentals（foreign_holding_ratio）
     5d. TaiwanStockInstitutionalInvestorsBuySell
         → 僅當 chip_daily 當筆的 chip_data_source != 'official_twse_tpex'
           OR chip_data_status != 'ok' 時，才 fallback 寫入
         → 官方資料已存在且 status='ok' 時跳過，不覆蓋
     5e. TaiwanStockMarginPurchaseShortSale
         → 同 5d 原則，不覆蓋官方成功資料
6. export_csvs()                   ← 從 View 查詢，輸出 CSV
```

---

## 關鍵實作細節

### 1. 外部設定檔 `scanner_config.ini`

程式啟動時以 `configparser` 讀取，若檔案不存在或某個 key 缺失，使用以下預設值。

```ini
# scanner_config.ini
# K 線與資料保留天數（單位：天）。修改後下次執行生效。
# 縮短天數不會主動刪除舊資料；舊資料只在下次寫入時清除過期筆數。

[KEEP_DAYS]
1m  = 5
5m  = 20
30m = 60
1d  = 730
1wk = 1095

[YAHOO]
# 各 interval 向 Yahoo 請求的歷史範圍。通常不需要修改。
range_1m  = 5d
range_5m  = 60d
range_30m = 60d
range_1d  = 2y
range_1wk = 3y

[RECENT_FETCH_RANGE]
# 增量更新時，向 Yahoo 請求的短窗口範圍（新股 / 調整偵測不受此限制）
1m  = 5d
5m  = 7d
30m = 7d
1d  = 7d
1wk = 21d

[ADJUSTMENT_DETECT]
# 每次增量更新時，用最近幾根 bar 做調整比對
recent_bars = 10
# close_price 差異超過此百分比（小數）視為 Yahoo 已調整歷史資料
threshold_pct = 0.02

[FINMIND]
# 最低 API 剩餘次數門檻，低於此值時跳過 FinMind
min_remaining = 80
# 每次 API 請求後等待秒數
sleep_seconds = 0.7
```

**讀取方式（程式碼）：**
```python
import configparser

def load_config():
    cfg = configparser.ConfigParser()
    cfg.read('scanner_config.ini', encoding='utf-8')
    keep_days = {
        '1m':  cfg.getint('KEEP_DAYS', '1m',  fallback=5),
        '5m':  cfg.getint('KEEP_DAYS', '5m',  fallback=20),
        '30m': cfg.getint('KEEP_DAYS', '30m', fallback=60),
        '1d':  cfg.getint('KEEP_DAYS', '1d',  fallback=730),
        '1wk': cfg.getint('KEEP_DAYS', '1wk', fallback=1095),
    }
    yahoo_config = {
        '1m':  {'range': cfg.get('YAHOO', 'range_1m',  fallback='5d'),  'interval': '1m'},
        '5m':  {'range': cfg.get('YAHOO', 'range_5m',  fallback='60d'), 'interval': '5m'},
        '30m': {'range': cfg.get('YAHOO', 'range_30m', fallback='60d'), 'interval': '30m'},
        '1d':  {'range': cfg.get('YAHOO', 'range_1d',  fallback='2y'),  'interval': '1d'},
        '1wk': {'range': cfg.get('YAHOO', 'range_1wk', fallback='3y'),  'interval': '1wk'},
    }
    return keep_days, yahoo_config

KEEP_DAYS, YAHOO_CONFIG = load_config()
```

---

### 2. 防呆更新機制（歷史資料凍結）

**核心原則：** 歷史已收盤的 bar / 已確認的籌碼 → 不重複抓取、不覆蓋。
當日仍在變動中的資料（盤中 bar、盤後待發布的籌碼）→ 每次執行都刷新。

#### K 線與技術指標（`k_bars` / `k_bar_features`）

```
bar_time.date() < today  →  INSERT OR IGNORE（歷史已收盤，凍結）
bar_time.date() == today →  INSERT OR REPLACE（當日可能仍在盤中，刷新）
```

**注意：** 盤中掃描時今日 1d bar 是不完整的（未收盤），
盤後再次執行時 `INSERT OR REPLACE` 會以完整收盤價覆蓋，這是預期行為。

#### 籌碼（`chip_daily`）

```
trade_date < today AND chip_data_status='ok'
  →  跳過（官方已確認，不重打 API，不覆蓋）

trade_date == today OR chip_data_status != 'ok'
  →  重新嘗試（TWSE/TPEX 盤後約 17:30 才發布；
     盤中掃描時會得到空資料，狀態寫 'api_failed'；
     盤後執行時會成功取得並更新為 'ok'）
```

#### FinMind 補強欄位（`stock_fundamentals`）

```
trade_date < today AND row 已存在
  →  INSERT OR IGNORE（基本面資料不會回溯修正）

trade_date == today OR row 不存在
  →  INSERT OR REPLACE
```

`finmind_fetch_log` 的 `status='success'` / `'empty'` 已保護不重複打 API（見 FinMind 設計章節）。

#### 美國市場情緒（`market_context`）

```
trade_date < today AND row 已存在
  →  跳過（美股已收盤，資料不再變動）

trade_date == today
  →  INSERT OR REPLACE（美股盤中仍在波動）
```

#### 過期資料清除時機

清除邏輯在 `save_bars()` 寫入後立即執行，不在啟動時全掃：

```python
conn.execute(
    "DELETE FROM k_bars WHERE symbol=? AND interval_type=? AND bar_time < ?",
    (symbol, interval_type, cutoff_date)
)
```

`cutoff_date = (today - timedelta(days=KEEP_DAYS[interval_type])).isoformat()`

縮短 `KEEP_DAYS` 後不會立即刪除；舊資料只在下次 `save_bars()` 時才被清除。

---

### 3. 歷史資料補足與 Yahoo 調整偵測

#### 抓取模式決策（`determine_fetch_mode()`）

每次 `save_bars()` 前先查 `fetch_meta`，決定這次要全量回補、調整後重抓，還是一般增量：

```
fetch_meta 中查不到此 (symbol, interval_type)
OR last_full_fetch IS NULL
  → 模式 A：full_backfill（首次掃描 / 新加股票）
    range = YAHOO_CONFIG[interval_type]['range']（2y / 3y）
    寫入：INSERT OR REPLACE 全部 bar（包含歷史）
    完成後：更新 fetch_meta.last_full_fetch = today

模式 A 完成後，或 last_full_fetch IS NOT NULL
  → 先做增量抓取（模式 B 短窗口），同時執行調整偵測：
      fetched_bars 最後 ADJ_DETECT_BARS 根 vs. DB 中相同 bar_time 的 close_price
      若任一根差異 > ADJ_DETECT_THRESHOLD → 觸發模式 C

差異不超過門檻（正常情況）
  → 模式 B：incremental（一般增量更新）
    range = RECENT_FETCH_RANGE[interval_type]（5-21 天）
    寫入：bar_time < today → INSERT OR IGNORE；bar_time == today → INSERT OR REPLACE
    完成後：更新 fetch_meta.last_update

偵測到調整（close_price 差異 > threshold）
  → 模式 C：adj_refetch（Yahoo 歷史資料被修正）
    range = YAHOO_CONFIG[interval_type]['range']（重抓全量）
    清除此 symbol/interval 在 k_bars 和 k_bar_features 中的所有舊資料
    寫入：INSERT OR REPLACE 全部 bar
    重新計算所有技術指標（save_features）
    更新 fetch_meta.adj_detected_at / adj_count / last_full_fetch
    log: "[ADJUST] {symbol} {interval}: 偵測到 Yahoo 調整，已重抓全量資料"
```

#### 「首次掃描」與「新增股票」的處理（模式 A）

兩種情況都走相同路徑：
- **DB/CSV 全新**：所有 symbol 的 `fetch_meta` 都不存在 → 全部執行模式 A
- **stock_list.txt 加入新股票**：新 symbol 的 `fetch_meta` 不存在 → 只有新股執行模式 A，現有股票正常增量

模式 A 觸發條件：`fetch_meta` 中找不到該 (symbol, interval_type)，或 `last_full_fetch IS NULL`。

#### Yahoo 調整偵測細節

```python
def detect_price_adjustment(conn, symbol, interval_type, fetched_bars):
    recent = fetched_bars[-ADJ_DETECT_BARS:]     # 取最後 N 根
    for bar in recent:
        row = conn.execute(
            "SELECT close_price FROM k_bars "
            "WHERE symbol=? AND interval_type=? AND bar_time=?",
            (symbol, interval_type, bar['bar_time'])
        ).fetchone()
        if row and row[0]:                        # DB 有此 bar
            diff = abs(bar['close'] - row[0]) / row[0]
            if diff > ADJ_DETECT_THRESHOLD:
                return True, bar['bar_time']      # 回傳 True 與第一個差異 bar
    return False, None
```

常見觸發時機：
- 除權息調整（Yahoo 回溯修正所有歷史 close_price）
- 股票分割（例如 1:2，歷史全部除以 2）

#### `save_bars()` 完整寫入流程

```
1. determine_fetch_mode(symbol, interval_type) → mode, range
2. fetch_yahoo_chart(symbol, range, interval)  → fetched_bars
3. if mode == 'incremental':
       adj, adj_bar = detect_price_adjustment(conn, ...)
       if adj:
           mode = 'adj_refetch'
           fetched_bars = fetch_yahoo_chart(full range)
           DELETE FROM k_bars         WHERE symbol=? AND interval_type=?
           DELETE FROM k_bar_features WHERE symbol=? AND interval_type=?
4. for bar in fetched_bars:
       if mode in ('full_backfill', 'adj_refetch'):
           INSERT OR REPLACE INTO k_bars   ← 全部覆蓋
       elif bar.date < today:
           INSERT OR IGNORE INTO k_bars    ← 歷史凍結
       else:
           INSERT OR REPLACE INTO k_bars   ← 當日刷新
5. DELETE 超過 KEEP_DAYS 的過期資料
6. UPDATE fetch_meta（last_full_fetch / last_update / adj 欄位）
```

---

### 4. 籌碼寫入時機
```
每次 update_all() 結束後，統一呼叫一次 fetch_official_chip_data()
用最新的 1d bar_time 當作 latest_trade_date
將結果寫入 chip_daily（所有股票一次批次寫入）
```

### 4. 籌碼評分函數
完整搬用 `scan_all_1d.py` 的：
- `score_bullish_chip(row)` → `bullish_chip_score`, `bullish_chip_reason`
- `score_bearish_chip(row)` → `bearish_chip_score`, `bearish_chip_reason`

這兩個函數用到的欄位：
`foreign_buy_sell`, `investment_trust_buy_sell`, `dealer_buy_sell`,
`institutional_total_buy_sell`, `foreign_buy_sell_3d`, `investment_trust_buy_sell_3d`,
`institutional_total_buy_sell_3d`, `margin_change`, `short_change`,
`change_pct`, `upper_tail_ratio`, `close_position`, `close`, `open_price`, `ma20`

### 5. AI labels 計算
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

### 6. VWAP 計算方式
- `1m`, `5m`, `30m`：**盤內累積 VWAP**（每天重置）
  - 依 `bar_time` 判斷是否同一天，是則累積，否則重置
- `1d`, `1wk`：**歷史累積 VWAP**（全部累積不重置）

### 7. 大盤指數處理
```python
# stock_list.txt 中需包含大盤指數
# 例如：^TWII, 台股加權指數, index
# 例如：^TWOII, 上櫃指數, index
# 計算 beta20, corr20, relative_strength_pct 時使用
# 讀取方式：build_index_maps(conn, '^TWII', interval_type)
```

### 8. CSV 數值格式
```python
# 所有 REAL 欄位：round(value, 4)
# None → 空字串 ''
# 編碼：utf-8-sig（Excel 開啟相容）
```

### 9. DB 連線設定
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
- `candidate_200.csv`, `top_stocks.csv`, `after_market_top30.csv` 等 → 不在此版本實作

---

## FinMind 整合設計（Optional 補強資料源）

> FinMind 只作補強，不影響 Yahoo / TWSE / TPEX / KGI 主流程。
> FinMind 失敗、token 無效、API 次數不足時，主流程繼續正常執行。

---

### 設計原則

**不建立 `stock_finmind_features`（避免與 `chip_daily` 欄位重疊）**

`chip_daily` 已有 `foreign_buy_sell`、`investment_trust_buy_sell`、`margin_balance` 等欄位，
且 `chip_data_source` 欄位已設計為支援 `'finmind'`。

正確分法：
- **三大法人 / 融資融券**：FinMind 作為 `chip_daily` 的 fallback，寫入後標記 `chip_data_source = 'finmind'`
- **FinMind 獨有欄位**（PER/PBR/外資持股比率/月營收）：寫入新表 `stock_fundamentals`

---

### .env 新增設定

```
ENABLE_FINMIND=1
FINMIND_API_TOKEN=your_token_here
FINMIND_MIN_REMAINING=80
FINMIND_SLEEP_SECONDS=0.7
```

- Token 不加引號
- 沒有 token 仍可使用 Free 等級資料（`TaiwanStockPER` 等已驗證可匿名讀取）
- `ENABLE_FINMIND=0` 時完全跳過 FinMind，主流程不受影響

---

### 新增 Table: `stock_fundamentals`

存放 FinMind 獨有的基本面 + 籌碼結構欄位（不與 `chip_daily` 重疊）。

```sql
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    trade_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL,   -- Yahoo 格式，如 2330.TW

    -- 估值（來自 TaiwanStockPER）
    per                         REAL,
    pbr                         REAL,
    dividend_yield              REAL,

    -- 外資持股比率（來自 TaiwanStockShareholding）
    -- 注意：這是累積持股「比率」，不是當日買賣超，與 chip_daily.foreign_buy_sell 不重疊
    foreign_holding_ratio       REAL,
    -- 變化量 = 當筆 ratio - 該 symbol 在 stock_fundamentals 中
    --   往前數第 N 筆有效資料的 ratio（以實際交易日順序計算，非 calendar day）
    foreign_holding_change_5d   REAL,   -- 與第 5 筆前有效交易日比較
    foreign_holding_change_20d  REAL,   -- 與第 20 筆前有效交易日比較

    -- 月營收（來自 TaiwanStockMonthRevenue）
    monthly_revenue             REAL,
    revenue_mom                 REAL,   -- 月增率 %
    revenue_yoy                 REAL,   -- 年增率 %

    -- 借券（來自 TaiwanStockSecuritiesLending）
    securities_lending_volume   REAL,
    securities_lending_fee_rate REAL,

    -- 當沖（來自 TaiwanStockDayTrading）
    day_trading_volume          REAL,
    day_trading_ratio           REAL,

    source      TEXT DEFAULT 'FinMind',
    updated_at  TEXT,

    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_stock_fundamentals_symbol_date
    ON stock_fundamentals (symbol, trade_date DESC);
```

---

### 新增 Table: `finmind_fetch_log`

追蹤哪些 dataset / 日期已抓過，避免重複消耗 API 次數。

```sql
CREATE TABLE IF NOT EXISTS finmind_fetch_log (
    dataset     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    data_id     TEXT NOT NULL DEFAULT 'ALL',   -- 'ALL' 表全市場批次；個股則填 base_code

    status      TEXT NOT NULL,   -- 'success' | 'empty' | 'failed'
    row_count   INTEGER DEFAULT 0,
    api_used    INTEGER DEFAULT 0,
    error_msg   TEXT,
    fetched_at  TEXT,

    PRIMARY KEY (dataset, trade_date, data_id)
);
```

**status 說明：**

| status | 意義 | 下次是否重抓 |
|---|---|---|
| `success` | 成功取得資料 | 跳過 |
| `empty` | API 有查，當天無資料（例如休市） | 跳過 |
| `failed` | 連線錯誤 / 解析失敗 | 重試 |

---

### FinMind 執行流程（每次 update_all() 結束後觸發）

```text
1. 讀 .env → 檢查 ENABLE_FINMIND
2. 若關閉 → 直接跳過
3. 呼叫 FinMind user_info API → 取得 remaining = api_request_limit - user_count
4. 若 remaining < FINMIND_MIN_REMAINING → 跳過並 log
5. 查 finmind_fetch_log → 找出今日尚未 success/empty 的 dataset
6. 依序抓取（每個 dataset 前再查一次 remaining）：
   Phase 1（優先）：
     a. TaiwanStockPER        → stock_fundamentals.per/pbr/dividend_yield
     b. TaiwanStockShareholding → stock_fundamentals.foreign_holding_ratio
     c. TaiwanStockInstitutionalInvestorsBuySell
        → 僅當 chip_daily 對應列不存在，或
          chip_data_source != 'official_twse_tpex' OR chip_data_status != 'ok' 時寫入
        → 寫入時 chip_data_source = 'finmind'
        → 官方已成功（source='official_twse_tpex' AND status='ok'）時跳過，不覆蓋
     d. TaiwanStockMarginPurchaseShortSale
        → 同 c 原則
   Phase 2（次要，日後再接）：
     e. TaiwanStockSecuritiesLending → stock_fundamentals
     f. TaiwanStockDayTrading        → stock_fundamentals
     g. TaiwanStockMonthRevenue      → stock_fundamentals
     h. TaiwanDailyShortSaleBalances → stock_fundamentals
7. 每個 dataset 結束後寫入 finmind_fetch_log
8. 計算 foreign_holding_change_5d / change_20d（讀歷史資料推算）
9. 主流程 view 已 LEFT JOIN stock_fundamentals → CSV 自動帶出新欄位
```

---

### FinMind API 次數檢查

```python
# GET https://api.web.finmindtrade.com/v2/user_info
# Header: Authorization: Bearer {token}
# 回傳：user_count, api_request_limit
remaining = api_request_limit - user_count
if remaining < FINMIND_MIN_REMAINING:
    skip_finmind()
```

- 程式開始先查一次
- 每個 dataset 抓取前再查一次
- 遇到 HTTP 402 或 "Token is illegal" → 立即熔斷，不繼續消耗

---

### View 更新：納入 `stock_fundamentals`

在 `v_ai_features_aftermarket` 和 `v_ai_features_intraday` 兩個 View 中，
各加一個 `LEFT JOIN stock_fundamentals`，讓 CSV 匯出時自動帶出基本面欄位。

```sql
-- 在兩個 View 的 FROM 子句末尾加入：
LEFT JOIN stock_fundamentals fm
    ON fm.symbol = f.symbol
   AND fm.trade_date = date(f.bar_time)

-- SELECT 新增欄位：
fm.per, fm.pbr, fm.dividend_yield,
fm.foreign_holding_ratio,
fm.foreign_holding_change_5d,
fm.foreign_holding_change_20d,
fm.monthly_revenue, fm.revenue_mom, fm.revenue_yoy,
fm.securities_lending_volume, fm.securities_lending_fee_rate,
fm.day_trading_volume, fm.day_trading_ratio
```

**為何選擇更新 View 而非在主程式 merge：**
- CSV 欄位由 View 決定，不需在 `stock_scanner.py` 主流程另外寫 merge 邏輯
- FinMind 沒有資料時，LEFT JOIN 回傳 NULL，CSV 欄位留空，主流程不受影響
- 將來新增更多基本面欄位，只需更新 View，不動主程式邏輯

---

### 新增程式檔案

```
finmind_client.py     ← FinMind API 存取層
finmind_features.py   ← DB 讀寫、缺口判斷、欄位 mapping
```

#### `finmind_client.py` 負責

```python
class FinMindClient:
    def __init__(self)           # 讀 .env，載入 token
    def check_quota(self)        # → remaining: int；失敗回傳 0
    def fetch_dataset(           # 全市場批次抓（不帶 stock_id）
        self, dataset, start_date, end_date
    )                            # → pd.DataFrame or None
    def is_enabled(self)         # 讀 ENABLE_FINMIND
```

- 使用 `urllib.request`（與主程式一致，不引入 requests）
- 遇到 402 / Token illegal / 連線逾時 → raise `FinMindAbort`，呼叫端 catch 後 skip

#### `finmind_features.py` 負責

```python
class FinMindFeatureManager:
    def __init__(self, client: FinMindClient, conn)
    def ensure_tables(self)      # 建立 stock_fundamentals + finmind_fetch_log
    def is_fetched(self, dataset, trade_date)  # 查 log，True = 跳過
    def update_pipeline(self, trade_date)      # 主流程入口
    def _fetch_per(self, trade_date)
    def _fetch_shareholding(self, trade_date)
    def _fetch_institutional(self, trade_date) # → fallback chip_daily
    def _fetch_margin(self, trade_date)        # → fallback chip_daily
    def _write_log(self, dataset, trade_date, status, row_count, error_msg)
    def _upsert_fundamentals(self, rows)       # UPSERT → stock_fundamentals
    def _fallback_chip_daily(self, rows, source_dataset)
        # 寫入 chip_daily 前先查：若該 (symbol, trade_date) 的
        # chip_data_source = 'official_twse_tpex' AND chip_data_status = 'ok'
        # 則跳過，不覆蓋官方成功資料
```

---

### FinMind Dataset → 欄位 Mapping（第一版）

抓取前務必先 `print(dataset, df.columns.tolist())` 確認實際欄位名稱，
以下為預期對應（需與實際回傳比對後修正）：

| Dataset | FinMind 欄位 | 寫入位置 |
|---|---|---|
| TaiwanStockPER | `PER`, `PBR`, `dividend_yield` | `stock_fundamentals` |
| TaiwanStockShareholding | `HoldingRatio`（或 `持股比率`，需確認） | `stock_fundamentals.foreign_holding_ratio` |
| TaiwanStockInstitutionalInvestorsBuySell | 法人買賣明細 | `chip_daily`（fallback） |
| TaiwanStockMarginPurchaseShortSale | 融資融券原始欄位 | `chip_daily`（fallback） |

**特別注意：`TaiwanStockShareholding` 的欄位名稱**
FINMIND_TEST.md 顯示回傳為「持股比率、國際代碼、剩餘可投資股數」等中文欄位，
務必先 print 確認，再寫 mapping，否則資料會靜默寫入 NULL。

---

### `stock_fundamentals` 資料保留策略

| 欄位類型 | 保留天數 |
|---|---|
| 估值（PER/PBR）、外資持股比率 | 1095 天（3年，與 1d k_bars 對齊） |
| 月營收 | 1095 天 |
| 借券 / 當沖 | 365 天 |

清除邏輯：在 `ensure_tables()` 後，`DELETE WHERE trade_date < date('now', '-1095 days')`。

---

## 實作順序建議（給 Claude Code）

1. 設定區、常數、路徑
2. 工具函數（全從 scan_all_1d.py 搬）
3. DB 模組（`init_db` 建立 9 張表 + 2 個 View + 索引）
   - 9 張表：`symbols`, `k_bars`, `k_bar_features`, `chip_daily`, `ai_labels`,
     `stock_fundamentals`, `market_context`, `finmind_fetch_log`, `fetch_meta`
   - 2 個 View：`v_ai_features_intraday`, `v_ai_features_aftermarket`
     （均已包含 LEFT JOIN `stock_fundamentals` 與 LEFT JOIN `market_context`）
4. 股票清單模組
5. Yahoo 模組（fetch + save_bars）
6. 指標計算模組（合併兩個程式最佳版本）
7. AI Labels 模組
8. 籌碼模組（完整從 scan_all_1d.py 搬，只改 save_chip_daily）
9. 美國市場情緒模組（`fetch_market_context`，放在主程式內，`update_all()` 最前面呼叫）
10. 主掃描流程
11. CSV 匯出模組
12. GUI 模組
13. FinMind 模組（`finmind_client.py` + `finmind_features.py`）
    - 在 `update_all()` 末尾加入 `fm_manager.update_pipeline(trade_date)`
    - FinMind 整個 block 包在 try/except，失敗只 log，不拋出

---

## 給 Claude Code 的額外指示

- 使用 Python 3.8+ 語法
- 只用標準函式庫 + `tkinter`（不引入新的第三方套件；FinMind SDK 也用 `urllib.request` 實作）
- requests 換成 `urllib.request`（與 scan_all_1d.py 一致）
- 所有 DB 操作使用 `conn.executemany()` 批次寫入
- 籌碼模組函數名稱、邏輯、欄位名稱完全對應 `scan_all_1d.py`，方便日後維護
- GUI 保留現有外觀：tkinter Text widget 顯示 log，兩個按鈕（開始/停止）
- 新增第三個按鈕：「匯出 CSV」（單獨觸發 export_csvs）
- stop_scan 全域旗標控制停止邏輯不變
- FinMind 整個流程包在獨立 try/except，任何錯誤只 log，不影響主流程
