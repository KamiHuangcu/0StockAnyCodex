# FinMind Test

測試日期：2026-05-23

## 測試目的

- 驗證 `.env` 中的 `FINMIND_API_TOKEN` 是否可用
- 測試 FinMind 是否能讀到目前專案官方 TWSE / TPEX 流程沒有接進來的資料
- 區分哪些 dataset 在 `Free` 等級可用，哪些需要升級方案

## 測試結果總結

### 1. Token 狀態

目前 `.env` 中的 `FINMIND_API_TOKEN` 測試結果：

- 狀態：`不可用`
- FinMind 回應：`Token is illegal.`

代表目前 `.env` 內這組 token 需要重新到 FinMind 官網更新或重新複製。

## 2. 不帶 token 仍可讀取的資料

以下資料集測試可正常讀取，代表就算先不修 token，也可以先做 PoC：

- `TaiwanStockInfo`
  - 可取得：股票代號、名稱、產業別、類型

- `TaiwanStockPrice`
  - 可取得：日 OHLC、成交量、成交金額、漲跌價差、成交筆數

- `TaiwanStockPER`
  - 可取得：`PER`、`PBR`、`dividend_yield`

- `TaiwanStockTotalReturnIndex`
  - 可取得：加權 / 櫃買報酬指數

- `TaiwanStockMonthRevenue`
  - 可取得：月營收、年/月欄位、建立時間

- `TaiwanStockFinancialStatements`
  - 可取得：綜合損益表欄位

- `TaiwanStockBalanceSheet`
  - 可取得：資產負債表欄位

- `TaiwanStockCashFlowsStatement`
  - 可取得：現金流量表欄位

- `TaiwanStockDividend`
  - 可取得：股利政策、除息日、發放日、員工股利、董監酬勞等欄位

- `TaiwanStockShareholding`
  - 可取得：外資持股、剩餘可投資股數、持股比率、國際代碼等

- `TaiwanStockSecuritiesLending`
  - 可取得：借券成交量、費率、借券期間、還券日

- `TaiwanStockDayTrading`
  - 可取得：當沖成交量、買賣金額

- `TaiwanStockInstitutionalInvestorsBuySell`
  - 可取得：法人買賣明細原始列

- `TaiwanStockMarginPurchaseShortSale`
  - 可取得：融資融券原始欄位，比官方接口格式更一致

- `TaiwanDailyShortSaleBalances`
  - 可取得：融券與借券賣出餘額、額度、回補等欄位

- `TaiwanStockDelisting`
  - 可取得：下市 / 下櫃清單

- `TaiwanStockNews`
  - 可取得：新聞標題、來源、連結、日期

## 3. 目前測試需要升級方案的資料

以下資料集測試時被 FinMind 回覆：

`Your level is free. Please update your user level.`

代表目前帳號等級或匿名等級無法使用：

- `TaiwanStockPriceAdj`
- `TaiwanStockWeekPrice`
- `TaiwanStockMonthPrice`
- `TaiwanStockIndustryChain`
- `TaiwanStockHoldingSharesPer`
- `TaiwanStockPriceLimit`
- `TaiwanStockSuspended`
- `TaiwanStockTradingDailyReport`
- `TaiwanStockBlockTrade`
- `TaiwanStockDispositionSecuritiesPeriod`

## 4. 以目前專案官方流程來看，FinMind 額外補到什麼

目前 `stock_scanner.py` 的官方資料來源，主要只接：

- TWSE / TPEX 三大法人
- TWSE / TPEX 融資融券

所以從「目前專案已接的官方資料」角度來看，FinMind 額外能補到的重點是：

- `PER / PBR / dividend_yield`
- 月營收
- 綜合損益表
- 資產負債表
- 現金流量表
- 股利政策
- 外資持股比率
- 借券資料
- 當沖資料
- 下市 / 下櫃名單
- 新聞資料
- 報酬指數

## 5. 哪些資訊最值得優先接

如果目標是補強現在的 `stock_scanner.py`，優先順序建議：

1. `TaiwanStockPER`
   理由：可直接補 `PER`、`PBR`、`dividend_yield`

2. `TaiwanStockMonthRevenue`
   理由：月營收很常拿來做基本面過濾

3. `TaiwanStockShareholding`
   理由：可補外資持股比率，不用只看單日買賣超

4. `TaiwanStockSecuritiesLending`
   理由：能補空方籌碼 / 借券面

5. `TaiwanStockDividend`
   理由：可補殖利率、除息與配息節奏

6. `TaiwanStockNews`
   理由：可做事件面輔助標記

## 6. 注意事項

- 這次測試顯示目前 `.env` 的 token 已失效，若要測試需要會員等級的 dataset，必須先更新 token。
- 即使不修 token，很多 `Free` dataset 仍可匿名讀取。
- 有些資料不是「官方完全沒有」，而是「目前專案沒有接官方那一條資料來源」。
- 若後續真的要接 FinMind，建議先接 `.env` 自動讀取，只先使用 `FINMIND_API_TOKEN` 一個欄位即可。
