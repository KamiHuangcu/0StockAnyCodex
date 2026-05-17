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
