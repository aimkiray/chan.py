import os
import datetime
import threading
from bisect import bisect_left, bisect_right
from typing import Iterable, Optional, List
from dotenv import load_dotenv
from clickhouse_driver import Client

from Common.CEnum import AUTYPE, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

class _LockedClickHouseClient:
    def __init__(self, client: Client, lock: threading.Lock):
        self._client = client
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._client.execute(*args, **kwargs)

    def disconnect(self):
        with self._lock:
            return self._client.disconnect()

    def __getattr__(self, name: str):
        return getattr(self._client, name)

class CClickHouseAPI(CCommonStockApi):
    _client = None
    _client_lock = threading.Lock()
    _init_lock = threading.Lock()
    _qfq_factor_cache = {}
    _hfq_factor_cache = {}
    _table_exists_cache = {}
    _min_daily_date_cache = {}

    def __init__(self, code, k_type, begin_date, end_date, autype, asset_type="STOCK"):
        super(CClickHouseAPI, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    @classmethod
    def do_init(cls):
        if cls._client is not None:
            return
        with cls._init_lock:
            if cls._client is not None:
                return
            load_dotenv()
            host = os.getenv("DB_HOST", "localhost")
            port = int(os.getenv("DB_PORT", 9000))
            user = os.getenv("DB_USER", "default")
            password = os.getenv("DB_PASSWORD", "")
            database = os.getenv("DB_NAME", "stock_data")

            raw_client = None
            try:
                raw_client = Client(host=host, port=port, user=user, password=password, database=database)
                locked_client = _LockedClickHouseClient(raw_client, cls._client_lock)
                locked_client.execute("SELECT 1")
                cls._client = locked_client
            except Exception as e:
                print(f"ClickHouse init failed: {e}")
                try:
                    if raw_client is not None:
                        raw_client.disconnect()
                except Exception:
                    pass
                cls._client = None

    @classmethod
    def do_close(cls):
        if cls._client:
            try:
                cls._client.disconnect()
            except Exception:
                pass
            cls._client = None

    @classmethod
    def get_client(cls) -> Optional[Client]:
        cls.do_init()
        return cls._client

    @classmethod
    def _get_table_columns(cls, table: str, database: Optional[str] = None):
        client = cls.get_client()
        if not client:
            return []
        db_expr = "currentDatabase()" if database is None else "%(db)s"
        query = f"""
            SELECT name
            FROM system.columns
            WHERE database = {db_expr}
              AND table = %(table)s
            ORDER BY position ASC
        """
        params = {"table": table}
        if database is not None:
            params["db"] = database
        try:
            rows = client.execute(query, params)
            return [r[0] for r in rows]
        except Exception as e:
            print(f"ClickHouse columns query failed: {e}")
            return []

    @classmethod
    def _table_exists(cls, table: str, database: Optional[str] = None) -> bool:
        client = cls.get_client()
        if not client:
            return False

        cache_key = (database or "", table)
        cached = cls._table_exists_cache.get(cache_key)
        if cached is not None:
            return bool(cached)

        db_expr = "currentDatabase()" if database is None else "%(db)s"
        query = f"""
            SELECT count()
            FROM system.tables
            WHERE database = {db_expr}
              AND name = %(table)s
        """
        params = {"table": table}
        if database is not None:
            params["db"] = database
        try:
            rows = client.execute(query, params)
            exists = bool(rows and rows[0] and int(rows[0][0]) > 0)
        except Exception:
            exists = False

        if len(cls._table_exists_cache) > 1024:
            cls._table_exists_cache.clear()
        cls._table_exists_cache[cache_key] = exists
        return exists

    @classmethod
    def _get_min_daily_date(cls, normalized_code: str) -> datetime.date:
        cached = cls._min_daily_date_cache.get(normalized_code)
        if isinstance(cached, datetime.date):
            return cached

        client = cls.get_client()
        if not client:
            dt = datetime.date(1990, 1, 1)
            cls._min_daily_date_cache[normalized_code] = dt
            return dt

        try:
            rows = client.execute(
                "SELECT min(date) FROM stock_daily WHERE code = %(code)s",
                {"code": normalized_code},
            )
            raw = rows[0][0] if rows and rows[0] else None
            dt = cls._parse_ymd(raw) or (raw if isinstance(raw, datetime.date) else None)
            if dt is None:
                dt = datetime.date(1990, 1, 1)
        except Exception:
            dt = datetime.date(1990, 1, 1)

        if len(cls._min_daily_date_cache) > 4096:
            cls._min_daily_date_cache.clear()
        cls._min_daily_date_cache[normalized_code] = dt
        return dt

    @classmethod
    def list_stock_pools(cls, table: str = "stock_constituent"):
        client = cls.get_client()
        if not client:
            return []
        cols = cls._get_table_columns(table)
        if not cols:
            return []

        col_set = set([c.lower() for c in cols])
        orig_by_lower = {c.lower(): c for c in cols}

        def has(name: str) -> bool:
            return name.lower() in col_set

        def col(name: str) -> Optional[str]:
            return orig_by_lower.get(name.lower())

        if has("group_type") and has("group_code") and has("group_name") and has("snapshot_date") and has("stock_code"):
            group_type_col = col("group_type")
            group_code_col = col("group_code")
            group_name_col = col("group_name")
            snapshot_date_col = col("snapshot_date")
            stock_code_col = col("stock_code")

            query = f"""
                WITH latest AS (
                    SELECT
                        {group_type_col} AS group_type,
                        {group_code_col} AS group_code,
                        max({snapshot_date_col}) AS snapshot_date
                    FROM {table}
                    GROUP BY group_type, group_code
                )
                SELECT
                    l.group_type AS group_type,
                    l.group_code AS group_code,
                    any(s.{group_name_col}) AS group_name,
                    l.snapshot_date AS snapshot_date,
                    countDistinct(s.{stock_code_col}) AS stock_count
                FROM latest l
                INNER JOIN {table} s
                  ON s.{group_type_col} = l.group_type
                 AND s.{group_code_col} = l.group_code
                 AND s.{snapshot_date_col} = l.snapshot_date
                GROUP BY group_type, group_code, snapshot_date
                ORDER BY stock_count DESC
                LIMIT 2000
            """
            try:
                rows = client.execute(query)
            except Exception as e:
                print(f"ClickHouse list pools failed: {e}")
                return []

            items = []
            for group_type, group_code, group_name, snapshot_date, stock_count in rows:
                pool_id = f"{group_type}:{group_code}"
                items.append({
                    "pool_id": str(pool_id),
                    "pool_type": str(group_type),
                    "pool_code": str(group_code),
                    "pool_name": str(group_name) if group_name is not None else str(group_code),
                    "snapshot_date": str(snapshot_date) if snapshot_date is not None else None,
                    "stock_count": int(stock_count) if stock_count is not None else 0
                })
            return items

        def pick_first(candidates):
            for c in candidates:
                if c in col_set:
                    return orig_by_lower[c]
            return None

        code_col = pick_first(["code", "stock_code", "symbol", "sec_code", "ts_code"])
        if not code_col:
            return []

        pool_id_col = None
        pool_name_col = None
        pairs = [
            ("index_code", "index_name"),
            ("pool_code", "pool_name"),
            ("sector_code", "sector_name"),
            ("concept_code", "concept_name"),
            ("board_code", "board_name"),
        ]
        for pid, pname in pairs:
            pid_col = pick_first([pid])
            pname_col = pick_first([pname])
            if pid_col and pname_col:
                pool_id_col = pid_col
                pool_name_col = pname_col
                break

        if not pool_id_col:
            pool_id_col = pick_first(["pool", "pool_id", "index", "index_id", "category", "group", "name"])
        if not pool_name_col:
            pool_name_col = pick_first(["pool_name", "index_name", "name", "category_name", "group_name"])

        if not pool_id_col:
            return []

        pool_name_expr = pool_id_col if not pool_name_col else f"any({pool_name_col})"

        query = f"""
            SELECT
                {pool_id_col} AS pool_id,
                {pool_name_expr} AS pool_name,
                countDistinct({code_col}) AS stock_count
            FROM {table}
            GROUP BY pool_id
            ORDER BY stock_count DESC
            LIMIT 1000
        """
        try:
            rows = client.execute(query)
        except Exception as e:
            print(f"ClickHouse list pools failed: {e}")
            return []

        items = []
        for pool_id, pool_name, stock_count in rows:
            items.append({
                "pool_id": str(pool_id),
                "pool_name": str(pool_name) if pool_name is not None else str(pool_id),
                "stock_count": int(stock_count) if stock_count is not None else 0
            })
        return items

    @classmethod
    def get_pool_members(cls, pool_id: str, table: str = "stock_constituent", limit: int = 5000):
        client = cls.get_client()
        if not client:
            return []
        cols = cls._get_table_columns(table)
        if not cols:
            return []

        col_set = set([c.lower() for c in cols])
        orig_by_lower = {c.lower(): c for c in cols}

        def has(name: str) -> bool:
            return name.lower() in col_set

        def col(name: str) -> Optional[str]:
            return orig_by_lower.get(name.lower())

        group_type = None
        group_code = pool_id
        if ":" in pool_id:
            parts = pool_id.split(":", 1)
            group_type = parts[0]
            group_code = parts[1]

        if has("group_code") and has("snapshot_date") and has("stock_code"):
            group_code_col = col("group_code")
            snapshot_date_col = col("snapshot_date")
            stock_code_col = col("stock_code")
            group_type_col = col("group_type") if has("group_type") else None

            where = f"{group_code_col} = %(group_code)s"
            params = {"group_code": group_code, "limit": int(limit)}
            if group_type_col and group_type is not None:
                where = where + f" AND {group_type_col} = %(group_type)s"
                params["group_type"] = group_type

            query = f"""
                WITH (
                    SELECT max({snapshot_date_col})
                    FROM {table}
                    WHERE {where}
                ) AS latest_dt
                SELECT DISTINCT {stock_code_col} AS code
                FROM {table}
                WHERE {where}
                  AND {snapshot_date_col} = latest_dt
                ORDER BY code
                LIMIT %(limit)s
            """

            try:
                rows = client.execute(query, params)
            except Exception as e:
                print(f"ClickHouse get pool members failed: {e}")
                return []
            return [str(r[0]) for r in rows]

        def pick_first(candidates):
            for c in candidates:
                if c in col_set:
                    return orig_by_lower[c]
            return None

        code_col = pick_first(["code", "stock_code", "symbol", "sec_code", "ts_code"])
        if not code_col:
            return []

        pool_id_col = pick_first(["index_code", "pool_code", "sector_code", "concept_code", "board_code", "pool", "pool_id", "index", "index_id", "category", "group", "name"])
        if not pool_id_col:
            return []

        date_col = pick_first(["trade_date", "date", "dt", "as_of", "update_date", "effective_date"])

        if date_col:
            query = f"""
                WITH (
                    SELECT max({date_col})
                    FROM {table}
                    WHERE {pool_id_col} = %(pool_id)s
                ) AS latest_dt
                SELECT DISTINCT {code_col} AS code
                FROM {table}
                WHERE {pool_id_col} = %(pool_id)s
                  AND {date_col} = latest_dt
                ORDER BY code
                LIMIT %(limit)s
            """
        else:
            query = f"""
                SELECT DISTINCT {code_col} AS code
                FROM {table}
                WHERE {pool_id_col} = %(pool_id)s
                ORDER BY code
                LIMIT %(limit)s
            """

        try:
            rows = client.execute(query, {"pool_id": pool_id, "limit": int(limit)})
        except Exception as e:
            print(f"ClickHouse get pool members failed: {e}")
            return []

        return [str(r[0]) for r in rows]

    def _ensure_connection(self):
        client = self.get_client()
        self._client = client

    @classmethod
    def _parse_ymd(cls, value) -> Optional[datetime.date]:
        if value is None:
            return None
        if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                return datetime.date.fromisoformat(s[:10])
            except Exception:
                return None
        return None

    @classmethod
    def _pick_column(cls, cols_lower_map, candidates):
        for name in candidates:
            col = cols_lower_map.get(name.lower())
            if col:
                return col
        return None

    def _get_qfq_factors_by_date(self, normalized_code: str, begin_date: str, end_date: str):
        self._ensure_connection()
        if not self._client:
            result = ([], {})
            return result
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        begin_lookback_dt = begin_dt - datetime.timedelta(days=120)
        begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
        end_key = end_dt.strftime("%Y-%m-%d")
        cache_key = (normalized_code, begin_lookback, end_key)
        cached = self._qfq_factor_cache.get(cache_key)
        if cached is not None:
            return cached

        div_cols = self._get_table_columns("stock_dividend")
        div_cols_lower = {c.lower(): c for c in div_cols}
        div_code_col = self._pick_column(div_cols_lower, ["code", "stock_code", "symbol", "ts_code", "sec_code"])
        div_date_col = self._pick_column(div_cols_lower, ["ex_dividend_date", "ex_date", "exdate", "ex_dt", "exdividend_date"])
        div_bonus_col = self._pick_column(div_cols_lower, ["bonus_share_ratio", "bonus_ratio", "bonus_share_rate", "bonus_share"])
        div_transfer_col = self._pick_column(div_cols_lower, ["transfer_ratio", "transfer_share_ratio", "transfer_rate", "transfer_share"])
        div_cash_col = self._pick_column(div_cols_lower, ["cash_dividend", "cash_dividend_per_share", "cash_div", "cash"])
        div_rights_ratio_col = self._pick_column(div_cols_lower, ["rightsissue_ratio", "rights_issue_ratio", "rights_ratio", "allotment_ratio"])
        div_rights_price_col = self._pick_column(div_cols_lower, ["rightsissue_price", "rights_issue_price", "rights_price", "allotment_price"])

        dividend_rows = []
        if div_code_col and div_date_col:
            bonus_expr = div_bonus_col if div_bonus_col else "0"
            transfer_expr = div_transfer_col if div_transfer_col else "0"
            cash_expr = div_cash_col if div_cash_col else "0"
            rights_ratio_expr = div_rights_ratio_col if div_rights_ratio_col else "0"
            rights_price_expr = div_rights_price_col if div_rights_price_col else "0"
            div_query = f"""
                SELECT {div_date_col}, {bonus_expr}, {transfer_expr}, {cash_expr}, {rights_ratio_expr}, {rights_price_expr}
                FROM stock_dividend
                WHERE {div_code_col} = %(code)s
                  AND {div_date_col} >= %(begin)s
                  AND {div_date_col} <= %(end)s
                ORDER BY {div_date_col} ASC
            """
            try:
                dividend_rows = self._client.execute(
                    div_query,
                    {"code": normalized_code, "begin": begin_lookback, "end": end_key},
                )
            except Exception:
                dividend_rows = []

        daily_query = """
            SELECT date, open, close
            FROM stock_daily
            WHERE code = %(code)s
              AND date >= %(begin)s
              AND date <= %(end)s
            ORDER BY date ASC
        """
        try:
            daily_rows = self._client.execute(
                daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key}
            )
        except Exception:
            daily_rows = []

        if not daily_rows:
            result = ([], {})
            if len(self._qfq_factor_cache) > 256:
                self._qfq_factor_cache.clear()
            self._qfq_factor_cache[cache_key] = result
            return result

        trading_dates = []
        daily_open_close = {}
        closes = []
        for dt_value, open_value, close_value in daily_rows:
            dt = self._parse_ymd(dt_value)
            if dt is None:
                continue
            open_f = float(open_value) if open_value is not None else 0.0
            close_f = float(close_value) if close_value is not None else 0.0
            if close_f <= 0:
                continue
            trading_dates.append(dt)
            closes.append(close_f)
            daily_open_close[dt] = (open_f, close_f)

        if not trading_dates:
            result = ([], {})
            if len(self._qfq_factor_cache) > 256:
                self._qfq_factor_cache.clear()
            self._qfq_factor_cache[cache_key] = result
            return result

        events = {}
        for ex_dt_value, bonus_value, transfer_value, cash_value, rights_ratio_value, rights_price_value in dividend_rows:
            ex_dt = self._parse_ymd(ex_dt_value)
            if ex_dt is None:
                continue
            bonus = float(bonus_value) if bonus_value is not None else 0.0
            transfer = float(transfer_value) if transfer_value is not None else 0.0
            cash = float(cash_value) if cash_value is not None else 0.0
            rights_ratio = float(rights_ratio_value) if rights_ratio_value is not None else 0.0
            rights_price = float(rights_price_value) if rights_price_value is not None else 0.0
            prev = events.get(ex_dt)
            if prev is None:
                rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
                events[ex_dt] = (bonus + transfer, cash, rights_ratio, rights_amount)
            else:
                prev_rights_ratio = float(prev[2])
                prev_rights_amount = float(prev[3])
                add_rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
                events[ex_dt] = (
                    prev[0] + bonus + transfer,
                    prev[1] + cash,
                    prev_rights_ratio + rights_ratio,
                    prev_rights_amount + add_rights_amount,
                )

        unit_divisors = [1.0, 10.0, 100.0]
        baseline_err = None
        best_divisor = 1.0
        best_err = None
        best_n = 0

        if events:
            for divisor in unit_divisors:
                errs = []
                for ex_dt, (split_raw, cash_raw, rights_ratio_raw, rights_amount_raw) in events.items():
                    ex_index = bisect_left(trading_dates, ex_dt)
                    if ex_index <= 0 or ex_index >= len(trading_dates):
                        continue
                    prev_close = closes[ex_index - 1]
                    if prev_close <= 0:
                        continue

                    obs_dt = trading_dates[ex_index]
                    obs_open, obs_close = daily_open_close.get(obs_dt, (0.0, 0.0))
                    obs_candidates = []
                    if obs_open and obs_open > 0:
                        obs_candidates.append(obs_open)
                    if obs_close and obs_close > 0:
                        obs_candidates.append(obs_close)
                    if not obs_candidates:
                        continue

                    split_ratio = float(split_raw) / divisor
                    cash = float(cash_raw) / divisor
                    rights_ratio = float(rights_ratio_raw) / divisor
                    rights_amount = float(rights_amount_raw) / divisor

                    theo = (prev_close - cash + rights_amount) / (1.0 + split_ratio + rights_ratio)
                    err = min(abs(theo - obs) / max(obs, 1e-6) for obs in obs_candidates)
                    errs.append(err)

                if not errs:
                    continue

                errs.sort()
                med = errs[len(errs) // 2]

                if divisor == 1.0:
                    baseline_err = med

                if best_err is None or med < best_err:
                    best_err = med
                    best_divisor = divisor
                    best_n = len(errs)

        chosen_divisor = 1.0
        if best_err is not None and best_divisor != 1.0 and baseline_err is not None:
            if best_n >= 1 and best_err <= 0.03 and (baseline_err - best_err) >= 0.01:
                chosen_divisor = best_divisor
            elif baseline_err is not None and baseline_err > 0.08:
                self.warnings.append(
                    f"ClickHouse stock_dividend unit ambiguous for {normalized_code} (median_err={baseline_err:.3f})"
                )
        if best_err is not None and best_err > 0.2:
            self.warnings.append(
                f"ClickHouse stock_dividend check failed for {normalized_code} (median_err={best_err:.3f}), skip QFQ"
            )
            events = {}

        ratio_by_ex_index = {}
        if events:
            for ex_dt, (split_ratio_raw, cash_dividend_raw, rights_ratio_raw, rights_amount_raw) in sorted(events.items(), key=lambda x: x[0]):
                ex_index = bisect_left(trading_dates, ex_dt)
                if ex_index >= len(trading_dates):
                    continue
                if ex_index == 0:
                    continue
                prev_close = closes[ex_index - 1]
                split_ratio = float(split_ratio_raw) / chosen_divisor
                cash_dividend = float(cash_dividend_raw) / chosen_divisor
                rights_ratio = float(rights_ratio_raw) / chosen_divisor
                rights_amount = float(rights_amount_raw) / chosen_divisor

                denom = (1.0 + split_ratio + rights_ratio) * prev_close
                if denom <= 0:
                    continue
                ratio = (prev_close - cash_dividend + rights_amount) / denom
                if ratio <= 0:
                    continue
                prior = ratio_by_ex_index.get(ex_index)
                ratio_by_ex_index[ex_index] = ratio if prior is None else (prior * ratio)

        factor = 1.0
        factor_by_date = {}
        for i in range(len(trading_dates) - 1, -1, -1):
            factor_by_date[trading_dates[i]] = factor
            ratio = ratio_by_ex_index.get(i)
            if ratio is not None:
                factor *= ratio

        result = (trading_dates, factor_by_date)
        if len(self._qfq_factor_cache) > 256:
            self._qfq_factor_cache.clear()
        self._qfq_factor_cache[cache_key] = result
        return result

    def _get_hfq_factors_by_date(self, normalized_code: str, begin_date: str, end_date: str):
        self._ensure_connection()
        if not self._client:
            result = ([], {})
            return result
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        begin_lookback_dt = self._get_min_daily_date(normalized_code)
        begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
        end_key = end_dt.strftime("%Y-%m-%d")
        cache_key = (normalized_code, begin_lookback, end_key)
        cached = self._hfq_factor_cache.get(cache_key)
        if cached is not None:
            return cached

        div_cols = self._get_table_columns("stock_dividend")
        div_cols_lower = {c.lower(): c for c in div_cols}
        div_code_col = self._pick_column(div_cols_lower, ["code", "stock_code", "symbol", "ts_code", "sec_code"])
        div_date_col = self._pick_column(div_cols_lower, ["ex_dividend_date", "ex_date", "exdate", "ex_dt", "exdividend_date"])
        div_bonus_col = self._pick_column(div_cols_lower, ["bonus_share_ratio", "bonus_ratio", "bonus_share_rate", "bonus_share"])
        div_transfer_col = self._pick_column(div_cols_lower, ["transfer_ratio", "transfer_share_ratio", "transfer_rate", "transfer_share"])
        div_cash_col = self._pick_column(div_cols_lower, ["cash_dividend", "cash_dividend_per_share", "cash_div", "cash"])
        div_rights_ratio_col = self._pick_column(div_cols_lower, ["rightsissue_ratio", "rights_issue_ratio", "rights_ratio", "allotment_ratio"])
        div_rights_price_col = self._pick_column(div_cols_lower, ["rightsissue_price", "rights_issue_price", "rights_price", "allotment_price"])

        dividend_rows = []
        if div_code_col and div_date_col:
            bonus_expr = div_bonus_col if div_bonus_col else "0"
            transfer_expr = div_transfer_col if div_transfer_col else "0"
            cash_expr = div_cash_col if div_cash_col else "0"
            rights_ratio_expr = div_rights_ratio_col if div_rights_ratio_col else "0"
            rights_price_expr = div_rights_price_col if div_rights_price_col else "0"
            div_query = f"""
                SELECT {div_date_col}, {bonus_expr}, {transfer_expr}, {cash_expr}, {rights_ratio_expr}, {rights_price_expr}
                FROM stock_dividend
                WHERE {div_code_col} = %(code)s
                  AND {div_date_col} >= %(begin)s
                  AND {div_date_col} <= %(end)s
                ORDER BY {div_date_col} ASC
            """
            try:
                dividend_rows = self._client.execute(
                    div_query,
                    {"code": normalized_code, "begin": begin_lookback, "end": end_key},
                )
            except Exception:
                dividend_rows = []

        daily_query = """
            SELECT date, open, close
            FROM stock_daily
            WHERE code = %(code)s
              AND date >= %(begin)s
              AND date <= %(end)s
            ORDER BY date ASC
        """
        try:
            daily_rows = self._client.execute(
                daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key}
            )
        except Exception:
            daily_rows = []

        if not daily_rows:
            result = ([], {})
            if len(self._hfq_factor_cache) > 256:
                self._hfq_factor_cache.clear()
            self._hfq_factor_cache[cache_key] = result
            return result

        trading_dates = []
        daily_open_close = {}
        closes = []
        for dt_value, open_value, close_value in daily_rows:
            dt = self._parse_ymd(dt_value)
            if dt is None:
                continue
            open_f = float(open_value) if open_value is not None else 0.0
            close_f = float(close_value) if close_value is not None else 0.0
            if close_f <= 0:
                continue
            trading_dates.append(dt)
            closes.append(close_f)
            daily_open_close[dt] = (open_f, close_f)

        if not trading_dates:
            result = ([], {})
            if len(self._hfq_factor_cache) > 256:
                self._hfq_factor_cache.clear()
            self._hfq_factor_cache[cache_key] = result
            return result

        events = {}
        for ex_dt_value, bonus_value, transfer_value, cash_value, rights_ratio_value, rights_price_value in dividend_rows:
            ex_dt = self._parse_ymd(ex_dt_value)
            if ex_dt is None:
                continue
            bonus = float(bonus_value) if bonus_value is not None else 0.0
            transfer = float(transfer_value) if transfer_value is not None else 0.0
            cash = float(cash_value) if cash_value is not None else 0.0
            rights_ratio = float(rights_ratio_value) if rights_ratio_value is not None else 0.0
            rights_price = float(rights_price_value) if rights_price_value is not None else 0.0
            prev = events.get(ex_dt)
            if prev is None:
                rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
                events[ex_dt] = (bonus + transfer, cash, rights_ratio, rights_amount)
            else:
                prev_rights_ratio = float(prev[2])
                prev_rights_amount = float(prev[3])
                add_rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
                events[ex_dt] = (
                    prev[0] + bonus + transfer,
                    prev[1] + cash,
                    prev_rights_ratio + rights_ratio,
                    prev_rights_amount + add_rights_amount,
                )

        unit_divisors = [1.0, 10.0, 100.0]
        baseline_err = None
        best_divisor = 1.0
        best_err = None
        best_n = 0

        if events:
            for divisor in unit_divisors:
                errs = []
                for ex_dt, (split_raw, cash_raw, rights_ratio_raw, rights_amount_raw) in events.items():
                    ex_index = bisect_left(trading_dates, ex_dt)
                    if ex_index <= 0 or ex_index >= len(trading_dates):
                        continue
                    prev_close = closes[ex_index - 1]
                    if prev_close <= 0:
                        continue

                    obs_dt = trading_dates[ex_index]
                    obs_open, obs_close = daily_open_close.get(obs_dt, (0.0, 0.0))
                    obs_candidates = []
                    if obs_open and obs_open > 0:
                        obs_candidates.append(obs_open)
                    if obs_close and obs_close > 0:
                        obs_candidates.append(obs_close)
                    if not obs_candidates:
                        continue

                    split_ratio = float(split_raw) / divisor
                    cash = float(cash_raw) / divisor
                    rights_ratio = float(rights_ratio_raw) / divisor
                    rights_amount = float(rights_amount_raw) / divisor

                    theo = (prev_close - cash + rights_amount) / (1.0 + split_ratio + rights_ratio)
                    err = min(abs(theo - obs) / max(obs, 1e-6) for obs in obs_candidates)
                    errs.append(err)

                if not errs:
                    continue

                errs.sort()
                med = errs[len(errs) // 2]

                if divisor == 1.0:
                    baseline_err = med

                if best_err is None or med < best_err:
                    best_err = med
                    best_divisor = divisor
                    best_n = len(errs)

        chosen_divisor = 1.0
        if best_err is not None and best_divisor != 1.0 and baseline_err is not None:
            if best_n >= 1 and best_err <= 0.03 and (baseline_err - best_err) >= 0.01:
                chosen_divisor = best_divisor
            elif baseline_err is not None and baseline_err > 0.08:
                self.warnings.append(
                    f"ClickHouse stock_dividend unit ambiguous for {normalized_code} (median_err={baseline_err:.3f})"
                )
        if best_err is not None and best_err > 0.2:
            self.warnings.append(
                f"ClickHouse stock_dividend check failed for {normalized_code} (median_err={best_err:.3f}), skip HFQ"
            )
            events = {}

        ratio_by_ex_index = {}
        if events:
            for ex_dt, (split_ratio_raw, cash_dividend_raw, rights_ratio_raw, rights_amount_raw) in sorted(events.items(), key=lambda x: x[0]):
                ex_index = bisect_left(trading_dates, ex_dt)
                if ex_index >= len(trading_dates):
                    continue
                if ex_index == 0:
                    continue
                prev_close = closes[ex_index - 1]
                split_ratio = float(split_ratio_raw) / chosen_divisor
                cash_dividend = float(cash_dividend_raw) / chosen_divisor
                rights_ratio = float(rights_ratio_raw) / chosen_divisor
                rights_amount = float(rights_amount_raw) / chosen_divisor

                denom = (1.0 + split_ratio + rights_ratio) * prev_close
                if denom <= 0:
                    continue
                ratio = (prev_close - cash_dividend + rights_amount) / denom
                if ratio <= 0:
                    continue
                prior = ratio_by_ex_index.get(ex_index)
                ratio_by_ex_index[ex_index] = ratio if prior is None else (prior * ratio)

        factor = 1.0
        factor_by_date = {}
        factor_by_date[trading_dates[0]] = factor
        for i in range(1, len(trading_dates)):
            ratio = ratio_by_ex_index.get(i)
            if ratio is not None:
                factor = factor / ratio
            factor_by_date[trading_dates[i]] = factor

        result = (trading_dates, factor_by_date)
        if len(self._hfq_factor_cache) > 256:
            self._hfq_factor_cache.clear()
        self._hfq_factor_cache[cache_key] = result
        return result

    def _aggregate_daily_items(self, daily_items, target_k_type: KL_TYPE):
        data_list = []
        cur_key = None
        agg_open = None
        agg_high = None
        agg_low = None
        agg_close = None
        agg_vol = 0.0
        agg_amt = 0.0
        agg_last_date = None

        for item in daily_items:
            d = item["date"]
            if target_k_type == KL_TYPE.K_WEEK:
                iso = d.isocalendar()
                key = (iso.year, iso.week)
            else:
                key = (d.year, d.month)

            if cur_key is None or key != cur_key:
                if cur_key is not None and agg_last_date is not None:
                    data_list.append(
                        CKLine_Unit(
                            {
                                DATA_FIELD.FIELD_TIME: CTime(
                                    agg_last_date.year, agg_last_date.month, agg_last_date.day, 0, 0, auto=False
                                ),
                                DATA_FIELD.FIELD_OPEN: float(round(agg_open, 2)),
                                DATA_FIELD.FIELD_HIGH: float(round(agg_high, 2)),
                                DATA_FIELD.FIELD_LOW: float(round(agg_low, 2)),
                                DATA_FIELD.FIELD_CLOSE: float(round(agg_close, 2)),
                                DATA_FIELD.FIELD_VOLUME: float(agg_vol),
                                DATA_FIELD.FIELD_TURNOVER: float(agg_amt),
                            }
                        )
                    )

                cur_key = key
                agg_open = float(item["open"])
                agg_high = float(item["high"])
                agg_low = float(item["low"])
                agg_close = float(item["close"])
                agg_vol = float(item["volume"])
                agg_amt = float(item["amount"])
                agg_last_date = d
                continue

            agg_high = max(agg_high, float(item["high"]))
            agg_low = min(agg_low, float(item["low"]))
            agg_close = float(item["close"])
            agg_vol += float(item["volume"])
            agg_amt += float(item["amount"])
            agg_last_date = d

        if cur_key is not None and agg_last_date is not None:
            data_list.append(
                CKLine_Unit(
                    {
                        DATA_FIELD.FIELD_TIME: CTime(
                            agg_last_date.year, agg_last_date.month, agg_last_date.day, 0, 0, auto=False
                        ),
                        DATA_FIELD.FIELD_OPEN: float(round(agg_open, 2)),
                        DATA_FIELD.FIELD_HIGH: float(round(agg_high, 2)),
                        DATA_FIELD.FIELD_LOW: float(round(agg_low, 2)),
                        DATA_FIELD.FIELD_CLOSE: float(round(agg_close, 2)),
                        DATA_FIELD.FIELD_VOLUME: float(agg_vol),
                        DATA_FIELD.FIELD_TURNOVER: float(agg_amt),
                    }
                )
            )

        return data_list

    def _normalize_datetime_bound(self, value: str, is_end: bool) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        if " " in raw or "T" in raw:
            return raw.replace("T", " ")
        if len(raw) == 10:
            try:
                datetime.datetime.strptime(raw, "%Y-%m-%d")
                return f"{raw} 23:59:59" if is_end else f"{raw} 00:00:00"
            except Exception:
                return raw
        if len(raw) == 8 and raw.isdigit():
            try:
                dt = datetime.datetime.strptime(raw, "%Y%m%d")
                s = dt.strftime("%Y-%m-%d")
                return f"{s} 23:59:59" if is_end else f"{s} 00:00:00"
            except Exception:
                return raw
        return raw

    def _get_trading_dates(self, normalized_code: str, begin_date: str, end_date: str):
        self._ensure_connection()
        if not self._client:
            return []
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        if end_dt < begin_dt:
            begin_dt, end_dt = end_dt, begin_dt
        table = "index_daily" if str(self.asset_type or "").upper() == "INDEX" else "stock_daily"
        try:
            rows = self._client.execute(
                f"""
                SELECT date
                FROM {table}
                WHERE code = %(code)s
                  AND date >= %(begin)s
                  AND date <= %(end)s
                ORDER BY date ASC
                """,
                {"code": normalized_code, "begin": begin_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            )
        except Exception:
            rows = []
        out = []
        for (d,) in rows:
            dt = self._parse_ymd(d)
            if dt is None:
                continue
            out.append(dt)
        return out

    def _group_date_segments(self, dates: List[datetime.date]):
        if not dates:
            return []
        dates = sorted(list(dict.fromkeys(dates)))
        segs = []
        s = dates[0]
        prev = dates[0]
        for d in dates[1:]:
            if (d - prev).days == 1:
                prev = d
                continue
            segs.append((s, prev))
            s = d
            prev = d
        segs.append((s, prev))
        return segs

    def _resample_intraday_from_lower_data(
        self,
        target_k_type: KL_TYPE,
        lower_data: List[CKLine_Unit],
        used_lower_k_type: KL_TYPE,
        begin_date: str,
        end_date: str,
    ):
        interval_map = {
            KL_TYPE.K_5M: 5,
            KL_TYPE.K_15M: 15,
            KL_TYPE.K_30M: 30,
            KL_TYPE.K_60M: 60,
        }
        interval_min = interval_map.get(target_k_type)
        if interval_min is None:
            return []

        def _klu_dt(klu: CKLine_Unit) -> Optional[datetime.datetime]:
            t = getattr(klu, "time", None)
            if t is None:
                return None
            try:
                return datetime.datetime(int(t.year), int(t.month), int(t.day), int(t.hour), int(t.minute))
            except Exception:
                return None

        def _trade_metric(klu: CKLine_Unit, key: DATA_FIELD) -> float:
            try:
                return float(getattr(getattr(klu, "trade_info", None), "metric", {}).get(key, 0.0) or 0.0)
            except Exception:
                return 0.0

        def _is_trade_time(dt: datetime.datetime) -> bool:
            hhmm = int(dt.hour) * 60 + int(dt.minute)
            if 9 * 60 + 25 <= hhmm < 9 * 60 + 30:
                return True
            if 9 * 60 + 30 <= hhmm < 11 * 60 + 30:
                return True
            if 13 * 60 <= hhmm < 15 * 60:
                return True
            return False

        def _bucket_start(dt: datetime.datetime) -> Optional[datetime.datetime]:
            hhmm = int(dt.hour) * 60 + int(dt.minute)
            d = dt.date()
            if 9 * 60 + 25 <= hhmm < 9 * 60 + 30:
                dt = datetime.datetime(d.year, d.month, d.day, 9, 30)
                hhmm = 9 * 60 + 30
            if 9 * 60 + 30 <= hhmm < 11 * 60 + 30:
                anchor = datetime.datetime(d.year, d.month, d.day, 9, 30)
                offset = int((dt - anchor).total_seconds() // 60)
                b = int(offset // int(interval_min)) * int(interval_min)
                return anchor + datetime.timedelta(minutes=b)
            if 13 * 60 <= hhmm < 15 * 60:
                anchor = datetime.datetime(d.year, d.month, d.day, 13, 0)
                offset = int((dt - anchor).total_seconds() // 60)
                b = int(offset // int(interval_min)) * int(interval_min)
                return anchor + datetime.timedelta(minutes=b)
            return None

        if used_lower_k_type == KL_TYPE.K_1M:
            by_dt = {}
            for klu in lower_data:
                dt = _klu_dt(klu)
                if dt is None or not _is_trade_time(dt):
                    continue
                by_dt[dt] = klu

            def _fill_session(day: datetime.date, start_h: int, start_m: int, end_h: int, end_m: int):
                start = datetime.datetime(day.year, day.month, day.day, start_h, start_m)
                end = datetime.datetime(day.year, day.month, day.day, end_h, end_m)
                first = None
                for dt in sorted([k for k in by_dt.keys() if start <= k < end]):
                    first = by_dt.get(dt)
                    if first is not None:
                        break
                base_px = None
                if first is not None:
                    try:
                        base_px = float(getattr(first, "open", 0.0) or 0.0) or float(getattr(first, "close", 0.0) or 0.0)
                    except Exception:
                        base_px = None
                last_close = None
                cur = start
                while cur < end:
                    if cur in by_dt:
                        klu = by_dt[cur]
                        try:
                            c = float(getattr(klu, "close", 0.0) or 0.0)
                        except Exception:
                            c = 0.0
                        if c > 0:
                            last_close = c
                        cur += datetime.timedelta(minutes=1)
                        continue
                    px = last_close
                    if px is None:
                        px = base_px
                    if px is None:
                        cur += datetime.timedelta(minutes=1)
                        continue
                    t = CTime(cur.year, cur.month, cur.day, cur.hour, cur.minute, auto=False)
                    by_dt[cur] = CKLine_Unit(
                        {
                            DATA_FIELD.FIELD_TIME: t,
                            DATA_FIELD.FIELD_OPEN: float(px),
                            DATA_FIELD.FIELD_HIGH: float(px),
                            DATA_FIELD.FIELD_LOW: float(px),
                            DATA_FIELD.FIELD_CLOSE: float(px),
                            DATA_FIELD.FIELD_VOLUME: 0.0,
                            DATA_FIELD.FIELD_TURNOVER: 0.0,
                        }
                    )
                    last_close = px
                    cur += datetime.timedelta(minutes=1)

            days = sorted(list({dt.date() for dt in by_dt.keys()}))
            for day in days:
                _fill_session(day, 9, 30, 11, 30)
                _fill_session(day, 13, 0, 15, 0)
            lower_data = [by_dt[dt] for dt in sorted(by_dt.keys())]

        grouped = {}
        for klu in lower_data:
            dt = _klu_dt(klu)
            if dt is None or not _is_trade_time(dt):
                continue
            b = _bucket_start(dt)
            if b is None:
                continue
            grouped.setdefault(b, []).append(klu)

        if not grouped:
            return []

        normalized_code = self.code.lower().replace(".", "")
        factor_trading_dates = []
        factor_by_date = {}
        if self.autype == AUTYPE.QFQ:
            factor_trading_dates, factor_by_date = self._get_qfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )
        elif self.autype == AUTYPE.HFQ:
            factor_trading_dates, factor_by_date = self._get_hfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )

        out = []
        for bdt in sorted(grouped.keys()):
            rows = sorted(grouped[bdt], key=lambda x: int(getattr(getattr(x, "time", None), "ts", 0) or 0))
            if not rows:
                continue

            first = rows[0]
            last = rows[-1]
            try:
                o = float(getattr(first, "open", 0.0) or 0.0)
                c = float(getattr(last, "close", 0.0) or 0.0)
            except Exception:
                continue
            if c <= 0:
                continue
            if o <= 0:
                o = c

            highs = []
            lows = []
            vol_sum = 0.0
            amt_sum = 0.0
            for klu in rows:
                try:
                    h = float(getattr(klu, "high", 0.0) or 0.0)
                    l = float(getattr(klu, "low", 0.0) or 0.0)
                    cc = float(getattr(klu, "close", 0.0) or 0.0)
                except Exception:
                    continue
                if cc <= 0:
                    continue
                if h <= 0:
                    h = cc
                if l <= 0:
                    l = cc
                highs.append(max(h, cc))
                lows.append(min(l, cc))
                vol_sum += _trade_metric(klu, DATA_FIELD.FIELD_VOLUME)
                amt_sum += _trade_metric(klu, DATA_FIELD.FIELD_TURNOVER)
            if not highs or not lows:
                continue
            h = float(max(highs + [o, c]))
            l = float(min(lows + [o, c]))

            factor = None
            if factor_by_date:
                d = bdt.date()
                factor = factor_by_date.get(d)
                if factor is None and factor_trading_dates:
                    idx = bisect_right(factor_trading_dates, d) - 1
                    if idx >= 0:
                        factor = factor_by_date.get(factor_trading_dates[idx])
            if factor is not None:
                o = float(o) * factor
                h = float(h) * factor
                l = float(l) * factor
                c = float(c) * factor

            t = CTime(bdt.year, bdt.month, bdt.day, bdt.hour, bdt.minute, auto=False)
            out.append(
                CKLine_Unit(
                    {
                        DATA_FIELD.FIELD_TIME: t,
                        DATA_FIELD.FIELD_OPEN: float(round(o, 2)),
                        DATA_FIELD.FIELD_HIGH: float(round(h, 2)),
                        DATA_FIELD.FIELD_LOW: float(round(l, 2)),
                        DATA_FIELD.FIELD_CLOSE: float(round(c, 2)),
                        DATA_FIELD.FIELD_VOLUME: float(vol_sum),
                        DATA_FIELD.FIELD_TURNOVER: float(amt_sum),
                    }
                )
            )
        return out

    def _resample_intraday_from_lower(self, target_k_type: KL_TYPE, begin_date: str, end_date: str):
        interval_map = {
            KL_TYPE.K_5M: 5,
            KL_TYPE.K_15M: 15,
            KL_TYPE.K_30M: 30,
            KL_TYPE.K_60M: 60,
        }
        interval_min = interval_map.get(target_k_type)
        if interval_min is None:
            return []

        candidates = [KL_TYPE.K_1M, KL_TYPE.K_5M]
        lower_data = []
        used_lower_k_type = None
        for lower_k_type in candidates:
            lower_api = CClickHouseAPI(
                code=self.code,
                k_type=lower_k_type,
                begin_date=begin_date,
                end_date=end_date,
                autype=AUTYPE.NONE,
                asset_type=self.asset_type,
            )
            try:
                lower_api._client = self._client
            except Exception:
                pass
            tmp = list(lower_api.get_kl_data() or [])
            if tmp:
                lower_data = tmp
                used_lower_k_type = lower_k_type
                break

        if not lower_data or used_lower_k_type is None:
            return []

        return self._resample_intraday_from_lower_data(
            target_k_type=target_k_type,
            lower_data=lower_data,
            used_lower_k_type=used_lower_k_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        self._ensure_connection()
        if not self._client:
            print("ClickHouse client not initialized")
            return []

        prefix = "index_" if self.asset_type == "INDEX" else "stock_"

        table_map = {
            KL_TYPE.K_1M: f"{prefix}1min",
            KL_TYPE.K_5M: f"{prefix}5min",
            KL_TYPE.K_15M: f"{prefix}15min",
            KL_TYPE.K_30M: f"{prefix}30min",
            KL_TYPE.K_60M: f"{prefix}60min",
            KL_TYPE.K_DAY: f"{prefix}daily",
            KL_TYPE.K_WEEK: f"{prefix}weekly",
            KL_TYPE.K_MON: f"{prefix}monthly",
        }

        need_aggregate = False
        use_precomputed_qfq = False

        if self.autype == AUTYPE.HFQ and self.k_type in (KL_TYPE.K_WEEK, KL_TYPE.K_MON):
            need_aggregate = True
            query_k_type = KL_TYPE.K_DAY
            table = table_map.get(query_k_type)
        elif self.autype == AUTYPE.QFQ and self.k_type in (KL_TYPE.K_WEEK, KL_TYPE.K_MON):
            direct_table = table_map.get(self.k_type)
            direct_qfq_table = f"{direct_table}_qfq" if direct_table else None
            if direct_qfq_table and self._table_exists(direct_qfq_table):
                query_k_type = self.k_type
                table = direct_qfq_table
                use_precomputed_qfq = True
            else:
                need_aggregate = True
                query_k_type = KL_TYPE.K_DAY
                table = table_map.get(query_k_type)
                if table:
                    qfq_table = f"{table}_qfq"
                    if self._table_exists(qfq_table):
                        table = qfq_table
                        use_precomputed_qfq = True
        else:
            query_k_type = self.k_type
            table = table_map.get(query_k_type)
            if self.autype == AUTYPE.QFQ and table:
                qfq_table = f"{table}_qfq"
                if self._table_exists(qfq_table):
                    table = qfq_table
                    use_precomputed_qfq = True

        if not table:
            raise Exception(f"Unsupported k_type for ClickHouse: {query_k_type}")

        base_table = table_map.get(query_k_type)

        time_col = (
            "datetime"
            if query_k_type in [KL_TYPE.K_1M, KL_TYPE.K_5M, KL_TYPE.K_15M, KL_TYPE.K_30M, KL_TYPE.K_60M]
            else "date"
        )
        
        # Normalize code: remove dot for ClickHouse query
        # For indices, we might need to be careful. 
        # But previous code used normalized_code for index too (sz.399006 -> sz399006)
        normalized_code = self.code.lower().replace(".", "")
        
        begin_date = self.begin_date if self.begin_date else "1990-01-01"
        end_date = self.end_date if self.end_date else datetime.date.today().strftime("%Y-%m-%d")
        begin_query = begin_date
        end_query = end_date
        if time_col == "datetime":
            begin_query = self._normalize_datetime_bound(begin_date, is_end=False)
            end_query = self._normalize_datetime_bound(end_date, is_end=True)

        params = {
            'code': normalized_code,
            'begin': begin_query,
            'end': end_query
        }
        
        # print(f"DEBUG: Querying {table} with params: {params}")

        def run_query(table_name: str):
            q = f"""
                SELECT {time_col}, open, high, low, close, volume, amount 
                FROM {table_name}
                WHERE code = %(code)s 
                AND {time_col} >= %(begin)s 
                AND {time_col} <= %(end)s
                ORDER BY {time_col} ASC
            """
            try:
                client = self._client or self.get_client()
                if not client:
                    return None
                self._client = client
                return client.execute(q, params)
            except Exception as e:
                print(f"Query failed: {e}")
                return None

        rows = run_query(table)
        
        if rows is None:
            return []

        if use_precomputed_qfq and base_table and base_table != table:
            fallback = False
            if not rows:
                fallback = True
            else:
                begin_dt = self._parse_ymd(begin_date)
                end_dt = self._parse_ymd(end_date)
                first_dt = rows[0][0]
                last_dt = rows[-1][0]
                first_day = first_dt.date() if isinstance(first_dt, datetime.datetime) else first_dt
                last_day = last_dt.date() if isinstance(last_dt, datetime.datetime) else last_dt
                if begin_dt and isinstance(first_day, datetime.date):
                    if (first_day - begin_dt).days > 14:
                        fallback = True
                if not fallback and end_dt and isinstance(last_day, datetime.date):
                    if (end_dt - last_day).days > 14:
                        fallback = True

            if fallback:
                rows2 = run_query(base_table)
                if rows2 is not None:
                    rows = rows2
                    use_precomputed_qfq = False

        patched_extra = []
        if rows and query_k_type in (KL_TYPE.K_30M, KL_TYPE.K_60M):
            try:
                trading_dates = self._get_trading_dates(normalized_code, begin_date=begin_date, end_date=end_date)
                present = set()
                for r in rows:
                    dt0 = r[0]
                    d0 = dt0.date() if isinstance(dt0, datetime.datetime) else dt0
                    if isinstance(d0, datetime.date):
                        present.add(d0)
                missing = [d for d in trading_dates if d not in present]
                if len(missing) > 0:
                    cap_days = 120
                    if len(missing) > cap_days:
                        missing = sorted(missing)[-cap_days:]
                    segs = self._group_date_segments(missing)
                    for a, b in segs:
                        seg_begin = a.strftime("%Y-%m-%d")
                        seg_end = b.strftime("%Y-%m-%d")
                        patched_extra.extend(self._resample_intraday_from_lower(query_k_type, begin_date=seg_begin, end_date=seg_end))
                    if patched_extra:
                        try:
                            self.warnings.append(
                                f"clickhouse_patch_gap:{normalized_code}:{query_k_type.name}:missing_days:{len(missing)}:{begin_date}~{end_date}"
                            )
                        except Exception:
                            pass
            except Exception:
                patched_extra = []

        if (not rows) and query_k_type in (KL_TYPE.K_30M, KL_TYPE.K_60M):
            resampled = self._resample_intraday_from_lower(query_k_type, begin_date=begin_date, end_date=end_date)
            if resampled:
                try:
                    self.warnings.append(
                        f"clickhouse_resample:{normalized_code}:{query_k_type.name}:from:{KL_TYPE.K_5M.name}:{begin_date}~{end_date}"
                    )
                except Exception:
                    pass
                return resampled

        factor_trading_dates = []
        factor_by_date = {}
        if self.autype == AUTYPE.QFQ and not use_precomputed_qfq:
            factor_trading_dates, factor_by_date = self._get_qfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )
        elif self.autype == AUTYPE.HFQ:
            factor_trading_dates, factor_by_date = self._get_hfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )

        if need_aggregate:
            daily_items = []
            for row in rows:
                dt, _open, _high, _low, _close, _vol, _amt = row
                if _close <= 0:
                    continue

                if _open <= 0:
                    _open = _close
                if _high <= 0:
                    _high = _close
                if _low <= 0:
                    _low = _close

                row_date = dt.date() if isinstance(dt, datetime.datetime) else dt

                if factor_by_date:
                    factor = factor_by_date.get(row_date)
                    if factor is None and factor_trading_dates:
                        idx = bisect_right(factor_trading_dates, row_date) - 1
                        if idx >= 0:
                            factor = factor_by_date.get(factor_trading_dates[idx])
                    if factor is not None:
                        _open = float(_open) * factor
                        _high = float(_high) * factor
                        _low = float(_low) * factor
                        _close = float(_close) * factor

                _high = max(_high, _open, _close)
                _low = min(_low, _open, _close)

                if daily_items and daily_items[-1]["date"] == row_date:
                    continue

                daily_items.append(
                    {
                        "date": row_date,
                        "open": float(_open),
                        "high": float(_high),
                        "low": float(_low),
                        "close": float(_close),
                        "volume": float(_vol),
                        "amount": float(_amt),
                    }
                )

            return self._aggregate_daily_items(daily_items, self.k_type)

        data_list = []
        for row in rows:
            dt, _open, _high, _low, _close, _vol, _amt = row

            if _close <= 0:
                continue

            if _open <= 0:
                _open = _close
            if _high <= 0:
                _high = _close
            if _low <= 0:
                _low = _close

            if factor_by_date:
                row_date = dt.date() if isinstance(dt, datetime.datetime) else dt
                factor = factor_by_date.get(row_date)
                if factor is None and factor_trading_dates:
                    idx = bisect_right(factor_trading_dates, row_date) - 1
                    if idx >= 0:
                        factor = factor_by_date.get(factor_trading_dates[idx])
                if factor is not None:
                    _open = float(_open) * factor
                    _high = float(_high) * factor
                    _low = float(_low) * factor
                    _close = float(_close) * factor

            _high = max(_high, _open, _close)
            _low = min(_low, _open, _close)

            if hasattr(dt, "hour"):
                kl_time = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
            else:
                kl_time = CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)

            if data_list and data_list[-1].time.ts == kl_time.ts:
                continue

            data_list.append(
                CKLine_Unit(
                    {
                        DATA_FIELD.FIELD_TIME: kl_time,
                        DATA_FIELD.FIELD_OPEN: float(round(_open, 2)),
                        DATA_FIELD.FIELD_HIGH: float(round(_high, 2)),
                        DATA_FIELD.FIELD_LOW: float(round(_low, 2)),
                        DATA_FIELD.FIELD_CLOSE: float(round(_close, 2)),
                        DATA_FIELD.FIELD_VOLUME: float(_vol),
                        DATA_FIELD.FIELD_TURNOVER: float(_amt),
                    }
                )
            )

        if patched_extra:
            data_list = list(data_list) + list(patched_extra)
            data_list.sort(key=lambda x: int(getattr(getattr(x, "time", None), "ts", 0) or 0))
            dedup = []
            last_ts = None
            for k in data_list:
                ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
                if ts <= 0:
                    continue
                if last_ts is not None and ts == last_ts:
                    dedup[-1] = k
                    continue
                dedup.append(k)
                last_ts = ts
            data_list = dedup

        return data_list

    def SetBasciInfo(self):
        # Query stock_info table
        self._ensure_connection()
        if not self._client:
            return
            
        normalized_code = self.code.lower().replace(".", "")
        query = "SELECT name FROM stock_info WHERE code = %(code)s LIMIT 1"
        
        try:
            rows = self._client.execute(query, {'code': normalized_code})
            if rows:
                self.name = rows[0][0]
            else:
                self.name = self.code
        except Exception as e:
            print(f"SetBasciInfo failed: {e}")
            self.name = self.code

    @classmethod
    def list_indices(cls, limit: int = 2000):
        client = cls.get_client()
        if not client:
            return []
        
        if not cls._table_exists("index_daily"):
            return []
            
        cols = cls._get_table_columns("index_daily")
        col_set = set([c.lower() for c in cols])
        
        code_col_name = "code"
        if "ts_code" in col_set: code_col_name = "ts_code"
        elif "symbol" in col_set: code_col_name = "symbol"
        elif "code" in col_set: code_col_name = "code"
        
        # We need to get distinct codes from index_daily, then try to get names from index_info
        # If index_info exists, join or separate query. 
        # For simplicity and ClickHouse optimization:
        # Get codes first, then names? Or LEFT JOIN. 
        # Let's try LEFT JOIN if index_info exists.
        
        has_info = cls._table_exists("index_info")
        
        try:
            if has_info:
                query = f"""
                    SELECT DISTINCT a.{code_col_name} as code, b.name 
                    FROM index_daily a
                    LEFT JOIN index_info b ON a.{code_col_name} = b.code
                    LIMIT %(limit)s
                """
                rows = client.execute(query, {"limit": limit})
                result = []
                for r in rows:
                    c = str(r[0])
                    n = str(r[1]) if r[1] else c
                    result.append({"code": c, "name": n})
                return result
            else:
                query = f"""
                    SELECT DISTINCT {code_col_name} as code 
                    FROM index_daily 
                    LIMIT %(limit)s
                """
                rows = client.execute(query, {"limit": limit})
                return [{"code": str(r[0]), "name": str(r[0])} for r in rows]
        except Exception as e:
            print(f"List indices failed: {e}")
            return []
