# Environment

這份文件說明本專案 `.env` 檔案的建議格式與欄位用途。

## 目前狀態

- 專案根目錄已有 `.env` 慣例。
- 目前 `stock_scanner.py` 還沒有自動讀取 `.env`。
- 現在這份說明主要用途是統一格式，方便後續接 FinMind 或其他需要 token 的流程。

## 檔案格式

基本規則：

- 檔名：`.env`
- 編碼：`UTF-8`
- 每行一個設定：`KEY=VALUE`
- 不要在 `=` 前後加空白
- 註解請用 `#`
- 敏感資訊不要提交到 Git

## 建議內容

最小格式：

```env
FINMIND_API_TOKEN=your_finmind_api_token
```

完整格式：

```env
FINMIND_API_TOKEN=your_finmind_api_token
FINMIND_USER_ID=your_finmind_user_id
FINMIND_EMAIL=your_email@example.com
FINMIND_IS_VERIFIED=true
FINMIND_API_LIMIT_PER_HOUR=600
FINMIND_API_USED_COUNT=0
```

## 欄位說明

- `FINMIND_API_TOKEN`
  用途：FinMind API token。這是最重要的敏感欄位。

- `FINMIND_USER_ID`
  用途：FinMind 帳號 ID。偏向帳號識別資訊，可選填。

- `FINMIND_EMAIL`
  用途：FinMind 帳號 email。偏向帳號識別資訊，可選填。

- `FINMIND_IS_VERIFIED`
  用途：帳號是否完成驗證，建議填 `true` 或 `false`。

- `FINMIND_API_LIMIT_PER_HOUR`
  用途：每小時 API 額度紀錄，屬於參考資訊，可選填。

- `FINMIND_API_USED_COUNT`
  用途：目前已使用次數紀錄，屬於參考資訊，可選填。

## 建議做法

- 實際執行時，至少保留 `FINMIND_API_TOKEN`。
- 其餘欄位可以當作帳號資訊備註，不一定要被程式讀取。
- 若之後要讓 `stock_scanner.py` 自動讀 `.env`，建議優先只接 `FINMIND_API_TOKEN`，其他欄位保持 optional。

## 相關檔案

- 格式範例：[.env.example](</d:/Stock_any/0StockAnyCodex/.env.example:1>)
- 實際私密設定：`.env`（已在 `.gitignore` 中忽略）
