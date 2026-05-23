import sqlite3
from datetime import datetime, timedelta

from finmind_client import FinMindAbort


def is_missing(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def safe_text(value, default=""):
    if is_missing(value):
        return default
    return str(value).strip()


def safe_float(value, default=None):
    if is_missing(value):
        return default
    try:
        text = str(value).strip().replace(",", "").replace("%", "")
        if text in ("--", "-", "None", "nan", "NaN"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def normalize_stock_id(value):
    text = safe_text(value)
    if text.endswith(".TW"):
        return text[:-3]
    if text.endswith(".TWO"):
        return text[:-4]
    return text


def parse_trade_date(value, fallback=""):
    text = safe_text(value)
    if not text:
        return fallback
    text = text[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return fallback


def pick_value(row, *names):
    for name in names:
        if name in row and not is_missing(row[name]):
            return row[name]
    return None


class FinMindFeatureManager:
    def __init__(self, client, conn, logger=None):
        self.client = client
        self.conn = conn
        self.logger = logger or (lambda message: None)

    def log(self, message):
        if self.logger:
            self.logger(message)

    def ensure_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_fundamentals (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                per REAL,
                pbr REAL,
                dividend_yield REAL,
                foreign_holding_ratio REAL,
                foreign_holding_change_5d REAL,
                foreign_holding_change_20d REAL,
                monthly_revenue REAL,
                revenue_mom REAL,
                revenue_yoy REAL,
                securities_lending_volume REAL,
                securities_lending_fee_rate REAL,
                day_trading_volume REAL,
                day_trading_ratio REAL,
                source TEXT DEFAULT 'FinMind',
                updated_at TEXT,
                PRIMARY KEY (trade_date, symbol)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS finmind_fetch_log (
                dataset TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                data_id TEXT NOT NULL DEFAULT 'ALL',
                status TEXT NOT NULL,
                row_count INTEGER DEFAULT 0,
                api_used INTEGER DEFAULT 0,
                error_msg TEXT,
                fetched_at TEXT,
                PRIMARY KEY (dataset, trade_date, data_id)
            )
        """)

    def is_fetched(self, dataset, trade_date, data_id="ALL"):
        row = self.conn.execute("""
            SELECT status
            FROM finmind_fetch_log
            WHERE dataset = ? AND trade_date = ? AND data_id = ?
        """, (dataset, trade_date, data_id)).fetchone()
        return bool(row and row[0] in ("success", "empty"))

    def _write_log(self, dataset, trade_date, status, row_count=0, error_msg="", data_id="ALL"):
        self.conn.execute("""
            INSERT INTO finmind_fetch_log
            (dataset, trade_date, data_id, status, row_count, api_used, error_msg, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset, trade_date, data_id) DO UPDATE SET
                status = excluded.status,
                row_count = excluded.row_count,
                api_used = excluded.api_used,
                error_msg = excluded.error_msg,
                fetched_at = excluded.fetched_at
        """, (
            dataset,
            trade_date,
            data_id,
            status,
            row_count,
            1 if status in ("success", "empty", "failed") else 0,
            safe_text(error_msg),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    def _symbol_map(self):
        rows = self.conn.execute("""
            SELECT base_code, symbol
            FROM symbols
        """).fetchall()
        return {safe_text(base_code): safe_text(symbol) for base_code, symbol in rows if base_code and symbol}

    def _upsert_fundamentals(self, rows):
        if not rows:
            return 0
        columns = [
            "trade_date",
            "symbol",
            "per",
            "pbr",
            "dividend_yield",
            "foreign_holding_ratio",
            "foreign_holding_change_5d",
            "foreign_holding_change_20d",
            "monthly_revenue",
            "revenue_mom",
            "revenue_yoy",
            "securities_lending_volume",
            "securities_lending_fee_rate",
            "day_trading_volume",
            "day_trading_ratio",
            "source",
            "updated_at",
        ]
        placeholders = ", ".join(["?"] * len(columns))
        update_sql = ", ".join(
            f"{column} = COALESCE(excluded.{column}, stock_fundamentals.{column})"
            for column in columns[2:]
        )
        payload = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in rows:
            payload.append([
                row.get("trade_date"),
                row.get("symbol"),
                row.get("per"),
                row.get("pbr"),
                row.get("dividend_yield"),
                row.get("foreign_holding_ratio"),
                row.get("foreign_holding_change_5d"),
                row.get("foreign_holding_change_20d"),
                row.get("monthly_revenue"),
                row.get("revenue_mom"),
                row.get("revenue_yoy"),
                row.get("securities_lending_volume"),
                row.get("securities_lending_fee_rate"),
                row.get("day_trading_volume"),
                row.get("day_trading_ratio"),
                row.get("source", "FinMind"),
                now,
            ])
        self.conn.executemany(f"""
            INSERT INTO stock_fundamentals ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                {update_sql}
        """, payload)
        return len(payload)

    def _refresh_holding_changes(self, symbols):
        for symbol in symbols:
            current_row = self.conn.execute("""
                SELECT trade_date, foreign_holding_ratio
                FROM stock_fundamentals
                WHERE symbol = ? AND foreign_holding_ratio IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 1
            """, (symbol,)).fetchone()
            if not current_row:
                continue
            trade_date, current_ratio = current_row
            history = self.conn.execute("""
                SELECT trade_date, foreign_holding_ratio
                FROM stock_fundamentals
                WHERE symbol = ? AND trade_date < ? AND foreign_holding_ratio IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 20
            """, (symbol, trade_date)).fetchall()
            change_5d = None
            change_20d = None
            if len(history) >= 5:
                change_5d = current_ratio - history[4][1]
            elif history:
                change_5d = current_ratio - history[-1][1]
            if len(history) >= 20:
                change_20d = current_ratio - history[19][1]
            elif history:
                change_20d = current_ratio - history[-1][1]
            self.conn.execute("""
                UPDATE stock_fundamentals
                SET foreign_holding_change_5d = ?,
                    foreign_holding_change_20d = ?
                WHERE trade_date = ? AND symbol = ?
            """, (change_5d, change_20d, trade_date, symbol))

    def _merge_chip_row(self, trade_date, row):
        symbol = row["symbol"]
        base_code = normalize_stock_id(symbol)
        existing = self.conn.execute("""
            SELECT base_code, foreign_buy_sell, investment_trust_buy_sell, dealer_buy_sell,
                   institutional_total_buy_sell, foreign_buy_sell_3d, investment_trust_buy_sell_3d,
                   dealer_buy_sell_3d, institutional_total_buy_sell_3d, margin_change,
                   short_change, margin_balance, short_balance, margin_short_ratio,
                   chip_data_source, chip_data_status, chip_data_date
            FROM chip_daily
            WHERE symbol = ? AND trade_date = ?
        """, (symbol, trade_date)).fetchone()
        if existing and existing[14] == "official_twse_tpex" and existing[15] == "ok":
            return False

        fields = {
            "foreign_buy_sell": 0.0,
            "investment_trust_buy_sell": 0.0,
            "dealer_buy_sell": 0.0,
            "institutional_total_buy_sell": 0.0,
            "foreign_buy_sell_3d": 0.0,
            "investment_trust_buy_sell_3d": 0.0,
            "dealer_buy_sell_3d": 0.0,
            "institutional_total_buy_sell_3d": 0.0,
            "margin_change": 0.0,
            "short_change": 0.0,
            "margin_balance": 0.0,
            "short_balance": 0.0,
            "margin_short_ratio": 0.0,
        }
        source = "finmind"
        status = "partial_chip_data"
        chip_data_date = row.get("chip_data_date", trade_date)
        if existing:
            base_code = safe_text(existing[0]) or base_code
            keys = list(fields)
            for index, key in enumerate(keys, start=1):
                fields[key] = safe_float(existing[index], 0.0)
            source = safe_text(existing[14]) or source
            status = safe_text(existing[15]) or status
            chip_data_date = safe_text(existing[16]) or chip_data_date
        for key in fields:
            if key in row and row[key] is not None:
                fields[key] = safe_float(row[key], 0.0)
        has_institutional = any(
            fields[key] != 0
            for key in (
                "foreign_buy_sell",
                "investment_trust_buy_sell",
                "dealer_buy_sell",
                "institutional_total_buy_sell",
            )
        )
        has_margin = any(
            fields[key] != 0
            for key in (
                "margin_change",
                "short_change",
                "margin_balance",
                "short_balance",
            )
        )
        status = "ok" if has_institutional and has_margin else "partial_chip_data"
        source = "finmind"
        self.conn.execute("""
            INSERT INTO chip_daily (
                symbol, base_code, trade_date,
                foreign_buy_sell, investment_trust_buy_sell, dealer_buy_sell,
                institutional_total_buy_sell, foreign_buy_sell_3d,
                investment_trust_buy_sell_3d, dealer_buy_sell_3d,
                institutional_total_buy_sell_3d, margin_change, short_change,
                margin_balance, short_balance, margin_short_ratio,
                chip_data_source, chip_data_status, chip_data_date, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                chip_data_source = excluded.chip_data_source,
                chip_data_status = excluded.chip_data_status,
                chip_data_date = excluded.chip_data_date,
                updated_at = excluded.updated_at
        """, (
            symbol,
            base_code,
            trade_date,
            fields["foreign_buy_sell"],
            fields["investment_trust_buy_sell"],
            fields["dealer_buy_sell"],
            fields["institutional_total_buy_sell"],
            fields["foreign_buy_sell_3d"],
            fields["investment_trust_buy_sell_3d"],
            fields["dealer_buy_sell_3d"],
            fields["institutional_total_buy_sell_3d"],
            fields["margin_change"],
            fields["short_change"],
            fields["margin_balance"],
            fields["short_balance"],
            fields["margin_short_ratio"],
            source,
            status,
            chip_data_date,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        return True

    def _fetch_per(self, trade_date, symbol_map):
        dataset = "TaiwanStockPER"
        rows = self.client.fetch_dataset(dataset, trade_date, trade_date)
        output = []
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            output.append({
                "trade_date": parse_trade_date(pick_value(row, "date"), trade_date),
                "symbol": symbol,
                "per": safe_float(pick_value(row, "PER", "per")),
                "pbr": safe_float(pick_value(row, "PBR", "pbr")),
                "dividend_yield": safe_float(pick_value(row, "dividend_yield", "DividendYield", "殖利率")),
                "source": "FinMind",
            })
        return dataset, rows, output

    def _fetch_shareholding(self, trade_date, symbol_map):
        dataset = "TaiwanStockShareholding"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=40)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        latest_by_symbol = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id", "股票代號"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date", "資料日期"), trade_date)
            ratio = safe_float(pick_value(row, "HoldingRatio", "holding_ratio", "持股比率", "外資持股比率"))
            if ratio is None:
                continue
            current = latest_by_symbol.get(symbol)
            if not current or (row_date, ratio) >= (current["trade_date"], current["foreign_holding_ratio"] or -1):
                latest_by_symbol[symbol] = {
                    "trade_date": row_date,
                    "symbol": symbol,
                    "foreign_holding_ratio": ratio,
                    "source": "FinMind",
                }
        output = list(latest_by_symbol.values())
        return dataset, rows, output

    def _fetch_month_revenue(self, trade_date, symbol_map):
        dataset = "TaiwanStockMonthRevenue"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=120)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        latest_by_symbol = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date", "revenue_month", "month"), trade_date)
            revenue = safe_float(pick_value(row, "revenue", "Revenue", "當月營收"))
            if revenue is None:
                continue
            current = latest_by_symbol.get(symbol)
            if not current or row_date >= current["trade_date"]:
                latest_by_symbol[symbol] = {
                    "trade_date": row_date,
                    "symbol": symbol,
                    "monthly_revenue": revenue,
                    "revenue_mom": safe_float(pick_value(row, "revenue_mom", "RevenueMoM", "上月比較增減(%)")),
                    "revenue_yoy": safe_float(pick_value(row, "revenue_yoy", "RevenueYoY", "去年同月增減(%)")),
                    "source": "FinMind",
                }
        return dataset, rows, list(latest_by_symbol.values())

    def _fetch_lending(self, trade_date, symbol_map):
        dataset = "TaiwanStockSecuritiesLending"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=14)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        latest_by_symbol = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date"), trade_date)
            volume = safe_float(pick_value(
                row,
                "securities_lending_volume",
                "SecuritiesLendingVolume",
                "借券餘額",
                "借券張數",
            ))
            fee_rate = safe_float(pick_value(
                row,
                "securities_lending_fee_rate",
                "SecuritiesLendingFeeRate",
                "借券費率",
            ))
            if volume is None and fee_rate is None:
                continue
            current = latest_by_symbol.get(symbol)
            if not current or row_date >= current["trade_date"]:
                latest_by_symbol[symbol] = {
                    "trade_date": row_date,
                    "symbol": symbol,
                    "securities_lending_volume": volume,
                    "securities_lending_fee_rate": fee_rate,
                    "source": "FinMind",
                }
        return dataset, rows, list(latest_by_symbol.values())

    def _fetch_day_trading(self, trade_date, symbol_map):
        dataset = "TaiwanStockDayTrading"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=14)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        latest_by_symbol = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date"), trade_date)
            volume = safe_float(pick_value(
                row,
                "day_trading_volume",
                "DayTradingVolume",
                "當沖成交股數",
            ))
            ratio = safe_float(pick_value(
                row,
                "day_trading_ratio",
                "DayTradingRatio",
                "當沖比率",
            ))
            if volume is None and ratio is None:
                continue
            current = latest_by_symbol.get(symbol)
            if not current or row_date >= current["trade_date"]:
                latest_by_symbol[symbol] = {
                    "trade_date": row_date,
                    "symbol": symbol,
                    "day_trading_volume": volume,
                    "day_trading_ratio": ratio,
                    "source": "FinMind",
                }
        return dataset, rows, list(latest_by_symbol.values())

    def _fetch_institutional(self, trade_date, symbol_map):
        dataset = "TaiwanStockInstitutionalInvestorsBuySell"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=7)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        grouped = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date"), trade_date)
            item = grouped.setdefault(symbol, {}).setdefault(row_date, {
                "foreign_buy_sell": 0.0,
                "investment_trust_buy_sell": 0.0,
                "dealer_buy_sell": 0.0,
                "institutional_total_buy_sell": 0.0,
            })
            investor = safe_text(pick_value(row, "name", "investor", "investors", "buy_sell_type"))
            net = safe_float(pick_value(row, "buy_sell", "buy_sell_num", "買賣差額"))
            if net is None:
                buy = safe_float(pick_value(row, "buy", "buy_amount", "買進股數"), 0.0)
                sell = safe_float(pick_value(row, "sell", "sell_amount", "賣出股數"), 0.0)
                net = buy - sell
            if "外" in investor or "foreign" in investor.lower():
                item["foreign_buy_sell"] += net or 0.0
            elif "投信" in investor or "trust" in investor.lower():
                item["investment_trust_buy_sell"] += net or 0.0
            elif "自營" in investor or "dealer" in investor.lower():
                item["dealer_buy_sell"] += net or 0.0
            else:
                item["institutional_total_buy_sell"] += net or 0.0
            item["institutional_total_buy_sell"] = (
                item["foreign_buy_sell"]
                + item["investment_trust_buy_sell"]
                + item["dealer_buy_sell"]
            )

        output = []
        for symbol, by_date in grouped.items():
            latest_dates = sorted(by_date.keys(), reverse=True)
            if not latest_dates:
                continue
            latest_date = latest_dates[0]
            latest_row = dict(by_date[latest_date])
            latest_row["symbol"] = symbol
            latest_row["chip_data_date"] = latest_date
            sums_3d = {
                "foreign_buy_sell_3d": 0.0,
                "investment_trust_buy_sell_3d": 0.0,
                "dealer_buy_sell_3d": 0.0,
                "institutional_total_buy_sell_3d": 0.0,
            }
            for candidate_date in latest_dates[:3]:
                daily = by_date[candidate_date]
                sums_3d["foreign_buy_sell_3d"] += daily["foreign_buy_sell"]
                sums_3d["investment_trust_buy_sell_3d"] += daily["investment_trust_buy_sell"]
                sums_3d["dealer_buy_sell_3d"] += daily["dealer_buy_sell"]
                sums_3d["institutional_total_buy_sell_3d"] += daily["institutional_total_buy_sell"]
            latest_row.update(sums_3d)
            output.append(latest_row)
        return dataset, rows, output

    def _fetch_margin(self, trade_date, symbol_map):
        dataset = "TaiwanStockMarginPurchaseShortSale"
        start_date = (datetime.fromisoformat(trade_date) - timedelta(days=7)).date().isoformat()
        rows = self.client.fetch_dataset(dataset, start_date, trade_date)
        latest_by_symbol = {}
        for row in rows:
            stock_id = normalize_stock_id(pick_value(row, "stock_id", "data_id"))
            symbol = symbol_map.get(stock_id)
            if not symbol:
                continue
            row_date = parse_trade_date(pick_value(row, "date"), trade_date)
            margin_balance = safe_float(pick_value(
                row,
                "margin_balance",
                "MarginPurchaseTodayBalance",
                "融資餘額",
            ))
            short_balance = safe_float(pick_value(
                row,
                "short_balance",
                "ShortSaleTodayBalance",
                "融券餘額",
            ))
            margin_change = safe_float(pick_value(
                row,
                "margin_change",
                "MarginPurchaseBuy",
                "融資增減",
            ))
            short_change = safe_float(pick_value(
                row,
                "short_change",
                "ShortSaleSell",
                "融券增減",
            ))
            current = latest_by_symbol.get(symbol)
            if not current or row_date >= current["chip_data_date"]:
                latest_by_symbol[symbol] = {
                    "symbol": symbol,
                    "chip_data_date": row_date,
                    "margin_change": margin_change,
                    "short_change": short_change,
                    "margin_balance": margin_balance,
                    "short_balance": short_balance,
                    "margin_short_ratio": (
                        (short_balance / margin_balance)
                        if margin_balance not in (None, 0) and short_balance is not None
                        else None
                    ),
                }
        return dataset, rows, list(latest_by_symbol.values())

    def update_pipeline(self, trade_date):
        self.ensure_tables()
        if not self.client.is_enabled():
            self.log("FinMind 已停用，略過補強資料流程")
            return {}

        symbol_map = self._symbol_map()
        if not symbol_map:
            self.log("FinMind 略過：DB 尚無 symbols 可對應")
            return {}

        try:
            remaining = self.client.check_quota()
        except FinMindAbort as exc:
            self.log(f"FinMind 配額檢查失敗，略過本次補強：{exc}")
            self._write_log("FinMindPipeline", trade_date, "failed", 0, str(exc))
            return {}
        if remaining is not None and remaining < self.client.min_remaining:
            self.log(f"FinMind 剩餘配額 {remaining} 低於門檻 {self.client.min_remaining}，略過本次更新")
            return {"remaining": remaining, "skipped": True}

        summary = {}
        fundamental_fetchers = [
            self._fetch_per,
            self._fetch_shareholding,
            self._fetch_month_revenue,
            self._fetch_lending,
            self._fetch_day_trading,
        ]

        try:
            for fetcher in fundamental_fetchers:
                preview_name = fetcher.__name__.replace("_fetch_", "")
                dataset_name = {
                    "per": "TaiwanStockPER",
                    "shareholding": "TaiwanStockShareholding",
                    "month_revenue": "TaiwanStockMonthRevenue",
                    "lending": "TaiwanStockSecuritiesLending",
                    "day_trading": "TaiwanStockDayTrading",
                }.get(preview_name, preview_name)
                if self.is_fetched(dataset_name, trade_date):
                    continue
                real_dataset, raw_rows, output_rows = fetcher(trade_date, symbol_map)
                if self.is_fetched(real_dataset, trade_date):
                    continue
                if output_rows:
                    self._upsert_fundamentals(output_rows)
                    self._write_log(real_dataset, trade_date, "success", len(output_rows))
                else:
                    self._write_log(real_dataset, trade_date, "empty", 0)
                summary[real_dataset] = len(output_rows)

            institutional_dataset = "TaiwanStockInstitutionalInvestorsBuySell"
            if not self.is_fetched(institutional_dataset, trade_date):
                institutional_dataset, raw_rows, chip_rows = self._fetch_institutional(trade_date, symbol_map)
                merged_count = sum(1 for row in chip_rows if self._merge_chip_row(trade_date, row))
                self._write_log(
                    institutional_dataset,
                    trade_date,
                    "success" if chip_rows else "empty",
                    merged_count,
                )
                summary[institutional_dataset] = merged_count

            margin_dataset = "TaiwanStockMarginPurchaseShortSale"
            if not self.is_fetched(margin_dataset, trade_date):
                margin_dataset, raw_rows, chip_rows = self._fetch_margin(trade_date, symbol_map)
                merged_count = sum(1 for row in chip_rows if self._merge_chip_row(trade_date, row))
                self._write_log(
                    margin_dataset,
                    trade_date,
                    "success" if chip_rows else "empty",
                    merged_count,
                )
                summary[margin_dataset] = merged_count
        except FinMindAbort as exc:
            self.log(f"FinMind 中止：{exc}")
            self._write_log("FinMindPipeline", trade_date, "failed", 0, str(exc))
            return summary
        except Exception as exc:
            self.log(f"FinMind 補強流程失敗，但主流程繼續：{exc}")
            self._write_log("FinMindPipeline", trade_date, "failed", 0, str(exc))
            return summary

        self._refresh_holding_changes(list(symbol_map.values()))
        cutoff_fund = (datetime.now() - timedelta(days=1095)).date().isoformat()
        self.conn.execute(
            "DELETE FROM stock_fundamentals WHERE trade_date < ?",
            (cutoff_fund,),
        )
        return summary
