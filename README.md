# Stock Scanner / NetworkPingTest

這是一個用 Python Tkinter 製作的台股資料掃描工具。程式會從 Yahoo Finance 抓取股票或指數的 K 線資料，存入 SQLite，計算常用技術指標，並匯出成 CSV，方便後續用 Excel、Power BI、Python 或其他工具分析。

> 注意：本專案僅供資料整理與研究用途，不構成任何投資建議。

## 功能

- 從 `stock_list.txt` 讀取股票、指數清單
- 自動解析台股 Yahoo Finance 代號，例如 `.TW`、`.TWO`
- 支援多週期資料：
  - `1m`
  - `5m`
  - `30m`
  - `1d`
  - `1wk`
- 將 K 線資料存入 SQLite：`stock_data.db`
- 匯出整合資料到 CSV：`stock_export.csv`
- 計算多種技術指標：
  - MA / BIAS
  - RSI
  - KD
  - MACD
  - Bollinger Bands
  - ATR / ADX / DI
  - VWAP
  - OBV
  - VZO
  - MFI
  - VPT
  - Williams %R
  - Beta / Correlation
  - Relative Strength

## 資料來源說明

- `stock_list.txt`：使用者自行提供的股票 / 指數清單，程式先從這個檔案決定要抓哪些標的。
- Yahoo Finance API：程式透過 `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}` 抓取各標的的 K 線資料。
- 從 Yahoo Finance 抓到的原始欄位包含：
  - `open_price`：開盤價
  - `high_price`：最高價
  - `low_price`：最低價
  - `close_price`：收盤價
  - `volume`：成交量
  - `bar_time`：K 線時間
- 週期資料來源同樣都是 Yahoo Finance，目前使用：
  - `1m`
  - `5m`
  - `30m`
  - `1d`
  - `1wk`
- `stock_data.db`：本機 SQLite 資料庫，保存從 Yahoo Finance 抓回來的原始 K 線資料與後續計算結果；不是外部來源。
- `stock_export.csv`：由本程式從 SQLite 整理後匯出的結果檔；不是外部來源。
- 技術指標欄位例如 MA、RSI、KD、MACD、布林通道、ATR、ADX、VWAP、OBV、MFI、VPT 等，都是根據 Yahoo Finance 回傳的 OHLCV 資料在本機計算，不是另外向其他網站抓取。
- `Beta`、`Correlation`、`Relative Strength` 這類相對強弱 / 市場比較欄位，基準資料來自清單中的市場指數，例如 `^TWII` 或 `^TWOII`；這些指數資料本身也同樣是從 Yahoo Finance 抓取。

## 資料輸出檔案說明

- `stock_data.db`：主資料庫檔，程式執行後會自動建立 / 更新。
- `stock_export.csv`：匯出檔，程式每次掃描後會重新整理輸出。

`stock_data.db` 主要內容：

- `k_bars`：保存原始 K 線資料，欄位重點包含 `symbol`、`base_code`、`name`、`item_type`、`interval_type`、`bar_time`、`open_price`、`high_price`、`low_price`、`close_price`、`volume`、`scan_time`。
- `k_bar_indicators`：保存每根 K 線對應的技術指標與衍生欄位，除了原始 OHLCV 外，也包含 MA、BIAS、RSI、KD、MACD、布林通道、ATR、ADX、VWAP、OBV、MFI、VPT、52 週高低點、Beta、Correlation、Relative Strength、AI label 欄位等。
- `q_instruments`：標的基本資料，例如代號、名稱、類型、市場別。
- `q_timeframes`：各週期設定資料，例如 `1m`、`5m`、`30m`、`1d`、`1wk` 的分鐘數、每日根數、保留天數、匯出天數。
- `q_price_bars`、`q_indicator_features`、`q_ai_labels`：整理給量化分析 / 特徵欄位 / 標籤欄位使用的結構化資料表。

`stock_export.csv` 主要內容：

- 每一列代表某個標的在某個週期、某個 `bar_time` 的整合資料。
- 內容包含標的資訊：`symbol`、`base_code`、`name`、`item_type`。
- 內容包含 K 線欄位：`open`、`high`、`low`、`close`、`volume`、`interval_type`、`bar_time`、`scan_time`。
- 內容包含技術指標欄位：MA、BIAS、RSI、KD、MACD、布林通道、ATR、ADX、VWAP、OBV、VZO、MFI、VPT、52 週高低點、Beta、Correlation、Relative Strength 等。
- 內容也包含衍生標記欄位：`future_1d_return`、`future_3d_return`、`max_upside_5d`、`drawdown_5d`、`buy_signal`、`entry_price`、`ai_signal_score`、`label_ready` 等。

## 往前抓取時間

程式目前對不同週期的抓取範圍如下，外部來源都是 Yahoo Finance：

- `1m`：向 Yahoo Finance 要最近 `7d`，寫入資料庫時保留最近 `14` 天，CSV 也匯出最近 `14` 天。
- `5m`：向 Yahoo Finance 要最近 `60d`，寫入資料庫時保留最近 `30` 天，CSV 也匯出最近 `30` 天。
- `30m`：向 Yahoo Finance 要最近 `60d`，寫入資料庫時保留最近 `60` 天，CSV 也匯出最近 `60` 天。
- `1d`：向 Yahoo Finance 要最近 `1y`，寫入資料庫時保留最近 `180` 天，CSV 也匯出最近 `180` 天。
- `1wk`：向 Yahoo Finance 要最近 `5y`，寫入資料庫時保留最近 `1095` 天，CSV 也匯出最近 `1095` 天。

補充說明：

- 「向 Yahoo Finance 要最近多久」是 API 請求範圍。
- 「保留最近多久」是寫進 `stock_data.db` 時實際留下來的資料區間。
- 「CSV 匯出最近多久」是輸出到 `stock_export.csv` 時會帶出的資料區間。

## 專案檔案

```text
stock_scanner.py     主程式
build_exe.bat        Windows 一鍵編譯腳本
build_exe.ps1        PowerShell 編譯流程
stock_list.txt       股票清單，需放在 exe 或 py 同資料夾
stock_data.db        執行後產生的 SQLite 資料庫
stock_export.csv     執行後產生的 CSV 匯出檔
```

## 股票清單格式

請建立 `stock_list.txt`，並放在 `stock_scanner.py` 或 `NetworkPingTest.exe` 同一個資料夾。

每行格式：

```text
代號,名稱,類型
```

範例：

```text
2330,台積電,stock
2317,鴻海,stock
^TWII,加權指數,index
^TWOII,櫃買指數,index
```

說明：

- `stock`：一般股票
- `index`：指數或基準資料
- 已經有 `.TW` 或 `.TWO` 的代號會直接使用
- 未帶後綴的台股代號會依序嘗試 `.TW`、`.TWO`
- 建議清單中包含 `^TWII` 和 `^TWOII`，這樣 Beta、Correlation、Relative Strength 等欄位才有基準資料可計算

## 直接執行 Python

需求：

- Python 3.12 或相近版本
- `requests`

安裝套件：

```powershell
python -m pip install requests
```

執行：

```powershell
python stock_scanner.py
```

## 編譯成 Windows 執行檔

本專案已附上一鍵編譯腳本，不需要每次手動切換 MSYS64 或重新設定 PowerShell PATH。

直接雙擊：

```text
build_exe.bat
```

或在 PowerShell 執行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

編譯腳本會自動：

- 建立本地編譯用虛擬環境 `.venv-build`
- 安裝或更新 `pip`
- 安裝 `pyinstaller` 和 `requests`
- 檢查 Python 語法
- 編譯出單檔 exe
- 將 `stock_list.txt` 複製到 `dist` 資料夾

編譯完成後，執行檔會產生在：

```text
dist\NetworkPingTest.exe
```

## 輸出資料

執行掃描後會產生：

```text
stock_data.db
stock_export.csv
```

`stock_data.db` 是 SQLite 資料庫，用來保存已抓取的 K 線資料。

`stock_export.csv` 是整合後的匯出檔，包含 OHLCV、週期、股票資訊、掃描時間與各種技術指標。

## 注意事項

- Yahoo Finance 可能限制短週期資料的可抓取範圍，例如 `1m` 通常只能抓近期資料。
- 掃描大量股票時需要等待一段時間，程式內有隨機延遲以降低請求頻率。
- 如果打包成 exe，`stock_list.txt` 必須放在 exe 同資料夾。
- `stock_data.db`、`stock_export.csv`、`dist`、`build`、`.venv-build` 通常不建議提交到 GitHub。

## 建議的 `.gitignore`

如果要將專案放到 GitHub，建議忽略執行與編譯產物：

```gitignore
__pycache__/
.venv-build/
build/
dist/
*.spec
stock_data.db
stock_export.csv
```
