# History

這份文件記錄 `stock_scanner.py` 各版本的架構與輸出格式演變。
每次有結構性變更時新增一個版本條目，版本號格式：`YYYYMMDD_NN`。

---

## 版本索引

| 版本 | 日期 | 主要變更 |
|---|---|---|
| [20260523_00](#版本-20260523_00) | 2026-05-23 | 舊版 → 新版重構（DB / CSV 基礎架構） |
| [20260523_01](#版本-20260523_01) | 2026-05-23 | 新增 FinMind 補強、美國市場情緒、防呆機制、外部設定檔 |

---

## 版本 20260523_00

> 紀錄基礎：`stock_scanner.py` 從舊版輸出格式改到新版架構之後，`db` / `csv` 的主要差異。
>
> 說明基準：
> - 舊版：以 Git 目前上一版的 `stock_scanner.py` 為準
> - 新版：以目前工作目錄中的 `stock_scanner.py` 為準
> - 本文件比較的是「程式設計與輸出結構差異」，不是同一天實跑後的市場資料筆數差異

### 1. 輸出檔案差異

#### 舊版

- DB：`stock_data.db`
- CSV：`stock_export.csv`

#### 新版

- DB：`stock_scanner.db`
- CSV：`snapshot_aftermarket.csv`
- CSV：`snapshot_intraday.csv`
- CSV：`ai_training.csv`
- Cache：`chip_cache/`

#### 差異摘要

- DB 檔案數量：`1 → 1`
- CSV 檔案數量：`1 → 3`
- 額外新增：`chip_cache/`，用來快取官方籌碼資料

---

### 2. DB 結構差異

#### 舊版 DB（表 / View）

- `k_bars`
- `k_bar_indicators`
- `q_instruments`
- `q_timeframes`
- `q_price_bars`
- `q_indicator_features`
- `q_ai_labels`
- `v_quant_ai_dataset`

統計：Table `7` + View `1`

#### 新版 DB（表 / View）

- `symbols`
- `k_bars`
- `k_bar_features`
- `chip_daily`
- `ai_labels`
- `v_ai_features_intraday`
- `v_ai_features_aftermarket`

統計：Table `5` + View `2`

#### 結構上最大的改變

- 舊版是 `k_bar_indicators + q_*` 的雙軌結構。
- 新版改成比較直接的 5 張核心表，結構明顯簡化。
- 舊版 `k_bars` 內重複存 `base_code`、`name`、`item_type`。
- 新版把這些標的基本資料抽到 `symbols`，`k_bars` 只保留 K 線本體。
- 新版新增 `chip_daily`，正式把籌碼資料納入 DB。
- 新版新增兩個 AI view，明確區分盤中與盤後資料使用邏輯。

---

### 3. DB 內容增加了什麼

#### 新增的核心資料

- `symbols.market`
- `symbols.first_seen`
- `chip_daily` 全表
- `k_bar_features.daily_score`
- `k_bar_features.reason`

#### 新增的籌碼欄位

- `foreign_buy_sell`
- `investment_trust_buy_sell`
- `dealer_buy_sell`
- `institutional_total_buy_sell`
- `foreign_buy_sell_3d`
- `investment_trust_buy_sell_3d`
- `dealer_buy_sell_3d`
- `institutional_total_buy_sell_3d`
- `margin_change`
- `short_change`
- `margin_balance`
- `short_balance`
- `margin_short_ratio`
- `bullish_chip_score`
- `bearish_chip_score`
- `bullish_chip_reason`
- `bearish_chip_reason`
- `chip_data_source`
- `chip_data_status`
- `chip_data_date`

#### 新增的資料使用規則

- `v_ai_features_intraday`：盤中只接前一交易日籌碼，避免 data leakage
- `v_ai_features_aftermarket`：盤後可接當日籌碼

---

### 4. DB 內容減少了什麼

#### 舊版有、但新版不再正式存入 DB 的技術欄位

- `bias5` / `bias10` / `bias60` / `bias120`
- `bias5_chg` / `bias10_chg` / `bias20_chg` / `bias60_chg` / `bias120_chg`
- `bb_mid20` / `bb_upper20` / `bb_lower20`
- `plus_di14` / `minus_di14`
- `vzo` / `vzo14`
- `money_flow_index` / `volume_price_trend` / `vpt`

#### 舊版有、但新版簡化掉的 AI label 欄位

- `label_horizon_bars_1d`
- `label_horizon_bars_3d`
- `label_horizon_bars_5d`

#### 結構上是「移動」不是「遺失」的資訊

- `base_code` / `name` / `item_type`：從舊版 `k_bars` 移到新版 `symbols`

---

### 5. CSV 結構差異

#### 舊版 CSV

- 檔名：`stock_export.csv`
- 型態：單一總表
- 特性：所有週期、所有標的、所有歷史列混在同一個檔案
- 欄位數：`89`

#### 新版 CSV

- `snapshot_aftermarket.csv`：盤後最新 `1d` 快照；每檔最新一列；欄位數 `69`
- `snapshot_intraday.csv`：盤中最新 `5m` 快照；每檔最新一列；欄位數 `69`
- `ai_training.csv`：AI 訓練資料；只匯出 `1d` 且 `label_ready = 1`；欄位數 `77`

#### CSV 設計差異

- 舊版是一份「全資料匯出」。
- 新版拆成三種用途不同的 CSV：快照給盤中/盤後判讀，訓練集給 AI。

---

### 6. CSV 增加了什麼資訊

#### 新版 CSV 新增欄位

- `market`
- `daily_score` / `reason`
- `foreign_buy_sell` / `investment_trust_buy_sell` / `dealer_buy_sell`
- `institutional_total_buy_sell`
- `foreign_buy_sell_3d` / `investment_trust_buy_sell_3d` / `institutional_total_buy_sell_3d`
- `margin_change` / `short_change` / `margin_balance` / `short_balance`
- `margin_short_ratio`
- `bullish_chip_score` / `bearish_chip_score`
- `chip_data_status` / `chip_data_date`

#### `ai_training.csv` 額外保留的 label 欄位

- `future_1d_return` / `future_3d_return`
- `max_upside_5d` / `drawdown_5d`
- `buy_signal` / `entry_price` / `ai_signal_score` / `label_ready`

---

### 7. CSV 減少了什麼資訊

#### 舊版有、但新版不再直接輸出的欄位

- `record_type` / `item_type` / `interval_type` / `scan_time`
- `open` / `high` / `low` / `close`（改名為 `open_price` 等）
- `bias5_chg` 系列 / `vzo14` / `money_flow_index` / `volume_price_trend`
- `label_horizon_bars_*` / `note`

#### 欄位名稱變更（非移除）

- `open` → `open_price`
- `high` → `high_price`
- `low` → `low_price`
- `close` → `close_price`

---

### 8. 週期保留時間（20260523_00 基準值）

| interval | 舊版 | 新版 |
|---|---|---|
| `1m` | 14 天 | 5 天（-9） |
| `5m` | 30 天 | 20 天（-10） |
| `30m` | 60 天 | 60 天（不變） |
| `1d` | 180 天 | 1095 天（+915） |
| `1wk` | 1095 天 | 1825 天（+730） |

---

### 9. 一句話總結（20260523_00）

這次改版的方向不是單純多幾個欄位，而是把輸出從「技術指標總表」升級成「技術面 + 籌碼面 + AI 使用情境分流」：DB 變簡潔、CSV 變分工明確、新增官方籌碼資訊、新增盤中/盤後分流 view、移除部分舊版較雜或重複的技術欄位。

---

---

## 版本 20260523_01

> 基準：在 20260523_00 的架構上，新增 FinMind 補強資料源、美國市場情緒、防呆更新機制、外部設定檔，並重新整理資料來源優先順序。

### 1. 新增檔案

| 檔案 | 用途 |
|---|---|
| `finmind_client.py` | FinMind API 存取層（查 quota、抓 dataset、熔斷處理） |
| `finmind_features.py` | FinMind DB 讀寫、缺口判斷、欄位 mapping |
| `scanner_config.ini` | 外部設定檔（保留天數、抓取範圍等，不存在時用預設值） |

---

### 2. DB 結構差異（相對 20260523_00）

#### 新增 4 張表

| 表名 | 用途 |
|---|---|
| `stock_fundamentals` | FinMind 補強欄位：PER/PBR、外資持股比率、月營收、借券、當沖 |
| `market_context` | 美國市場情緒：VIX、SPX、NASDAQ（來自 Yahoo ^VIX / ^GSPC / ^IXIC） |
| `finmind_fetch_log` | 記錄哪些 FinMind dataset 已抓過，避免重複消耗 API 次數 |
| `fetch_meta` | 追蹤每個 (symbol, interval_type) 的抓取狀態，驅動三種抓取模式 |

#### DB 表數變化

| 版本 | Table | View |
|---|---|---|
| 20260523_00 | 5 | 2 |
| **20260523_01** | **9** | **2** |

#### View 結構更新

兩個 View（`v_ai_features_intraday` / `v_ai_features_aftermarket`）均新增：

```sql
LEFT JOIN stock_fundamentals fm ON fm.symbol = f.symbol AND fm.trade_date = ...
LEFT JOIN market_context mc     ON mc.trade_date = ...
```

SELECT 新增欄位（兩個 View 相同）：

```
fm.per, fm.pbr, fm.dividend_yield,
fm.monthly_revenue, fm.revenue_mom, fm.revenue_yoy,
fm.foreign_holding_ratio,
fm.foreign_holding_change_5d, fm.foreign_holding_change_20d,
fm.securities_lending_volume, fm.securities_lending_fee_rate,
fm.day_trading_volume, fm.day_trading_ratio,
mc.vix_close, mc.vix_change_pct,
mc.spx_1d_return, mc.spx_5d_return, mc.spx_above_ma20,
mc.ndx_1d_return,
mc.us_sentiment_label, mc.us_sentiment_score
```

---

### 3. 資料來源優先順序（新增）

20260523_00 只有 Yahoo + TWSE/TPEX 官方兩條路徑，本版正式定義優先順序：

```
1. Yahoo Finance  → K 線（全週期）、技術指標、^VIX / ^GSPC / ^IXIC
2. TWSE 官方 API → 主板三大法人買賣超、融資融券
3. TPEX 官方 API → 上櫃股三大法人買賣超、融資融券
4. FinMind Free  → PER/PBR、外資持股比率；以上失敗時的籌碼 fallback
```

**FinMind fallback 保護規則：** 當 `chip_daily` 對應列的
`chip_data_source = 'official_twse_tpex'` AND `chip_data_status = 'ok'` 時，
FinMind 不覆蓋，跳過。

---

### 4. CSV 新增欄位（相對 20260523_00）

#### `snapshot_aftermarket.csv` / `snapshot_intraday.csv` 新增

| 欄位 | 來源 | 說明 |
|---|---|---|
| `per` | FinMind | 本益比 |
| `pbr` | FinMind | 股價淨值比 |
| `dividend_yield` | FinMind | 殖利率 |
| `monthly_revenue` | FinMind | 月營收 |
| `revenue_mom` | FinMind | 月增率 % |
| `revenue_yoy` | FinMind | 年增率 % |
| `foreign_holding_ratio` | FinMind | 外資持股比率 % |
| `foreign_holding_change_5d` | 計算 | 與前第 5 筆有效交易日比較的比率變化 |
| `foreign_holding_change_20d` | 計算 | 與前第 20 筆有效交易日比較的比率變化 |
| `securities_lending_volume` | FinMind | 借券成交量 |
| `securities_lending_fee_rate` | FinMind | 借券費率 |
| `day_trading_volume` | FinMind | 當沖成交量 |
| `day_trading_ratio` | FinMind | 當沖比率 |
| `vix_close` | Yahoo ^VIX | VIX 收盤（<15 貪婪；>30 恐慌） |
| `vix_change_pct` | Yahoo ^VIX | VIX 當日漲跌幅 % |
| `spx_1d_return` | Yahoo ^GSPC | S&P 500 當日報酬率 % |
| `spx_5d_return` | Yahoo ^GSPC | S&P 500 5 日累積報酬率 % |
| `spx_above_ma20` | Yahoo ^GSPC | 1=站上 MA20；0=跌破 |
| `ndx_1d_return` | Yahoo ^IXIC | NASDAQ 當日報酬率 % |
| `us_sentiment_label` | 計算 | `fear`/`elevated`/`neutral`/`greed` |
| `us_sentiment_score` | 計算 | 0-100，分數越高越樂觀 |
| `chip_data_source` | chip_daily | 本版補入（20260523_00 遺漏） |

預估總欄位數：`snapshot_aftermarket.csv` / `snapshot_intraday.csv` **約 90 欄**（+21）

---

### 5. 週期保留時間更新

| interval | 20260523_00 | **20260523_01** | 變化 |
|---|---|---|---|
| `1m` | 5 天 | 5 天 | 不變 |
| `5m` | 20 天 | 20 天 | 不變 |
| `30m` | 60 天 | 60 天 | 不變 |
| `1d` | 1095 天 | **730 天** | -365 天（改為 2 年） |
| `1wk` | 1825 天 | **1095 天** | -730 天（改為 3 年） |

**本版起可外部修改：** 編輯 `scanner_config.ini` 的 `[KEEP_DAYS]` 區段，
不需要修改程式碼，下次執行生效。

---

### 6. 新增機制：防呆更新（歷史資料凍結）

20260523_00 全部使用 `INSERT OR REPLACE`，導致每次執行都重算歷史資料。
本版改為：

| 資料 | 歷史（bar_time/trade_date < today） | 當日（== today） |
|---|---|---|
| `k_bars` | `INSERT OR IGNORE`（凍結） | `INSERT OR REPLACE`（刷新） |
| `k_bar_features` | `INSERT OR IGNORE`（凍結） | `INSERT OR REPLACE`（刷新） |
| `chip_daily` | status=ok 時跳過不重抓 | 每次嘗試（盤後才會有資料） |
| `stock_fundamentals` | row 存在時 `INSERT OR IGNORE` | `INSERT OR REPLACE` |
| `market_context` | row 存在時跳過 | `INSERT OR REPLACE` |

**「盤中掃 → 盤後更新」的解法：** 今日 1d bar 使用 `INSERT OR REPLACE`，
盤中不完整的收盤價在盤後重跑時會被完整值覆蓋。

---

### 7. 新增機制：三種抓取模式（fetch_meta 驅動）

| 模式 | 觸發條件 | 寫入方式 | 抓取範圍 |
|---|---|---|---|
| **A full_backfill** | 首次掃描 / DB 全新 / 新加股票 | `INSERT OR REPLACE` 全量 | 2y（1d）/ 3y（1wk） |
| **B incremental** | 正常情況 | 歷史 `IGNORE`；今日 `REPLACE` | 7d（1d）/ 21d（1wk） |
| **C adj_refetch** | Yahoo close 差異 > 2%（除權息調整） | 先 `DELETE` 舊資料，再 `REPLACE` 全量 | 同模式 A |

**新增股票自動回補：** `stock_list.txt` 加入新股票後，該股在 `fetch_meta` 無記錄
→ 自動觸發模式 A，現有股票不受影響，仍走增量模式。

**Yahoo 調整偵測：** 每次增量更新時比對最近 10 根 bar 的 `close_price`，
差異 > 2% 觸發模式 C（門檻可在 `scanner_config.ini` 修改）。

---

### 8. 一句話總結（20260523_01）

在 20260523_00 的基礎上，新增了三個維度的資料（FinMind基本面、美國市場情緒、外資持股比率），
並建立了可靠的歷史資料保護機制（防呆、調整偵測、自動回補），
讓每次執行更快、資料更完整、不浪費 API 次數。
