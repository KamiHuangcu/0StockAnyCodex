# History

更新日期：2026-05-23

這份文件整理 `stock_scanner.py` 從舊版輸出格式改到新版架構之後，`db` / `csv` 的主要差異。

說明基準：

- 舊版：以 Git 目前上一版的 `stock_scanner.py` 為準
- 新版：以目前工作目錄中的 `stock_scanner.py` 為準
- 本文件比較的是「程式設計與輸出結構差異」，不是同一天實跑後的市場資料筆數差異

## 1. 輸出檔案差異

### 舊版

- DB：`stock_data.db`
- CSV：`stock_export.csv`

### 新版

- DB：`stock_scanner.db`
- CSV：`snapshot_aftermarket.csv`
- CSV：`snapshot_intraday.csv`
- CSV：`ai_training.csv`
- Cache：`chip_cache/`

### 差異摘要

- DB 檔案數量：`1 -> 1`
- CSV 檔案數量：`1 -> 3`
- 額外新增：`chip_cache/`，用來快取官方籌碼資料

## 2. DB 結構差異

### 舊版 DB

表 / View：

- `k_bars`
- `k_bar_indicators`
- `q_instruments`
- `q_timeframes`
- `q_price_bars`
- `q_indicator_features`
- `q_ai_labels`
- `v_quant_ai_dataset`

統計：

- Table：`7`
- View：`1`

### 新版 DB

表 / View：

- `symbols`
- `k_bars`
- `k_bar_features`
- `chip_daily`
- `ai_labels`
- `v_ai_features_intraday`
- `v_ai_features_aftermarket`

統計：

- Table：`5`
- View：`2`

### 結構上最大的改變

- 舊版是 `k_bar_indicators + q_*` 的雙軌結構。
- 新版改成比較直接的 5 張核心表，結構明顯簡化。
- 舊版 `k_bars` 內重複存 `base_code`、`name`、`item_type`。
- 新版把這些標的基本資料抽到 `symbols`，`k_bars` 只保留 K 線本體。
- 新版新增 `chip_daily`，正式把籌碼資料納入 DB。
- 新版新增兩個 AI view，明確區分盤中與盤後資料使用邏輯。

## 3. DB 內容增加了什麼

### 新增的核心資料

- `symbols.market`
- `symbols.first_seen`
- `chip_daily` 全表
- `k_bar_features.daily_score`
- `k_bar_features.reason`

### 新增的籌碼欄位

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

### 新增的資料使用規則

- `v_ai_features_intraday`：盤中只接前一交易日籌碼，避免 data leakage
- `v_ai_features_aftermarket`：盤後可接當日籌碼

## 4. DB 內容減少了什麼

### 舊版有、但新版不再正式存入 DB 的技術欄位

- `bias5`
- `bias10`
- `bias60`
- `bias120`
- `bias5_chg`
- `bias10_chg`
- `bias20_chg`
- `bias60_chg`
- `bias120_chg`
- `bb_mid20`
- `bb_upper20`
- `bb_lower20`
- `plus_di14`
- `minus_di14`
- `vzo`
- `vzo14`
- `money_flow_index`
- `volume_price_trend`
- `vpt`

### 舊版有、但新版簡化掉的 AI label 欄位

- `label_horizon_bars_1d`
- `label_horizon_bars_3d`
- `label_horizon_bars_5d`

### 結構上是「移動」不是「遺失」的資訊

- `base_code`
- `name`
- `item_type`

這些不是不見，而是從舊版 `k_bars` 移到新版 `symbols`。

## 5. CSV 結構差異

### 舊版 CSV

- 檔名：`stock_export.csv`
- 型態：單一總表
- 特性：所有週期、所有標的、所有歷史列混在同一個檔案
- 欄位數：`89`

### 新版 CSV

- `snapshot_aftermarket.csv`
  - 用途：盤後最新 `1d` 快照
  - 內容：每檔最新一列
  - 欄位數：`69`
- `snapshot_intraday.csv`
  - 用途：盤中最新 `5m` 快照
  - 內容：每檔最新一列
  - 欄位數：`69`
- `ai_training.csv`
  - 用途：AI 訓練資料
  - 內容：只匯出 `1d` 且 `label_ready = 1`
  - 欄位數：`77`

### CSV 設計差異

- 舊版是一份「全資料匯出」。
- 新版拆成三種用途不同的 CSV。
- 新版比較接近實際分析流程：
  - 快照給盤中 / 盤後判讀
  - 訓練集給 AI

## 6. CSV 增加了什麼資訊

### 新版 CSV 新增欄位

- `market`
- `daily_score`
- `reason`
- `foreign_buy_sell`
- `investment_trust_buy_sell`
- `dealer_buy_sell`
- `institutional_total_buy_sell`
- `foreign_buy_sell_3d`
- `investment_trust_buy_sell_3d`
- `institutional_total_buy_sell_3d`
- `margin_change`
- `short_change`
- `margin_balance`
- `short_balance`
- `margin_short_ratio`
- `bullish_chip_score`
- `bearish_chip_score`
- `chip_data_status`
- `chip_data_date`

### `ai_training.csv` 額外保留的 label 欄位

- `future_1d_return`
- `future_3d_return`
- `max_upside_5d`
- `drawdown_5d`
- `buy_signal`
- `entry_price`
- `ai_signal_score`
- `label_ready`

## 7. CSV 減少了什麼資訊

### 舊版 CSV 有、但新版快照 / 訓練 CSV 不再直接輸出的欄位

- `record_type`
- `item_type`
- `interval_type`
- `scan_time`
- `open`
- `high`
- `low`
- `close`
- `bias5`
- `bias10`
- `bias60`
- `bias120`
- `bias5_chg`
- `bias10_chg`
- `bias20_chg`
- `bias60_chg`
- `bias120_chg`
- `bb_mid20`
- `bb_upper20`
- `bb_lower20`
- `plus_di14`
- `minus_di14`
- `vzo`
- `vzo14`
- `money_flow_index`
- `volume_price_trend`
- `vpt`
- `tail_ratio`
- `corr20`
- `stock_index_ratio`
- `label_horizon_bars_1d`
- `label_horizon_bars_3d`
- `label_horizon_bars_5d`
- `note`

### 這些欄位裡有幾種情況

- 有些是真的拿掉：
  - 例如 `bias5_chg`、`vzo14`、`label_horizon_bars_*`
- 有些是欄位名稱改了：
  - `open` -> `open_price`
  - `high` -> `high_price`
  - `low` -> `low_price`
  - `close` -> `close_price`
- 有些是因為檔案用途固定，所以不需要再輸出：
  - `interval_type`
  - `record_type`
- 有些資料還在 DB，但不放進快照 CSV：
  - `item_type`

## 8. 週期保留時間差異

### 舊版

- `1m`：保留 `14` 天
- `5m`：保留 `30` 天
- `30m`：保留 `60` 天
- `1d`：保留 `180` 天
- `1wk`：保留 `1095` 天

### 新版

- `1m`：保留 `5` 天
- `5m`：保留 `20` 天
- `30m`：保留 `60` 天
- `1d`：保留 `1095` 天
- `1wk`：保留 `1825` 天

### 差異

- `1m`：`14 -> 5`，減少 `9` 天
- `5m`：`30 -> 20`，減少 `10` 天
- `30m`：`60 -> 60`，不變
- `1d`：`180 -> 1095`，增加 `915` 天
- `1wk`：`1095 -> 1825`，增加 `730` 天

## 9. 一句話總結

這次改版的方向不是單純多幾個欄位，而是把輸出從「技術指標總表」升級成「技術面 + 籌碼面 + AI 使用情境分流」：

- DB 變簡潔
- CSV 變分工明確
- 新增官方籌碼資訊
- 新增盤中 / 盤後分流 view
- 移除一部分舊版較雜或重複的技術欄位
