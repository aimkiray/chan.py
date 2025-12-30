import os
import datetime
from typing import Iterable, Optional
from dotenv import load_dotenv
from clickhouse_driver import Client

from Common.CEnum import AUTYPE, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

class CClickHouseAPI(CCommonStockApi):
    _client = None

    def __init__(self, code, k_type, begin_date, end_date, autype):
        super(CClickHouseAPI, self).__init__(code, k_type, begin_date, end_date, autype)

    @classmethod
    def do_init(cls):
        if cls._client is None:
            load_dotenv()
            host = os.getenv("DB_HOST", "localhost")
            port = int(os.getenv("DB_PORT", 9000))
            user = os.getenv("DB_USER", "default")
            password = os.getenv("DB_PASSWORD", "")
            database = os.getenv("DB_NAME", "stock_data")
            
            try:
                cls._client = Client(host=host, port=port, user=user, password=password, database=database)
                # Test connection
                cls._client.execute("SELECT 1")
            except Exception as e:
                print(f"ClickHouse init failed: {e}")
                cls._client = None

    @classmethod
    def do_close(cls):
        if cls._client:
            cls._client.disconnect()
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
        if self._client is None:
            self.do_init()

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        self._ensure_connection()
        if not self._client:
            print("ClickHouse client not initialized")
            return []

        table_map = {
            KL_TYPE.K_1M: "stock_1min",
            KL_TYPE.K_5M: "stock_5min",
            KL_TYPE.K_15M: "stock_15min",
            KL_TYPE.K_30M: "stock_30min",
            KL_TYPE.K_60M: "stock_60min",
            KL_TYPE.K_DAY: "stock_daily",
            KL_TYPE.K_WEEK: "stock_weekly",
            KL_TYPE.K_MON: "stock_monthly",
        }
        
        if self.autype == AUTYPE.QFQ:
            if self.k_type == KL_TYPE.K_DAY:
                table_map[KL_TYPE.K_DAY] = "stock_daily_qfq"
            elif self.k_type == KL_TYPE.K_WEEK:
                table_map[KL_TYPE.K_WEEK] = "stock_weekly_qfq"
            elif self.k_type == KL_TYPE.K_MON:
                table_map[KL_TYPE.K_MON] = "stock_monthly_qfq"
        
        table = table_map.get(self.k_type)
        if not table:
            raise Exception(f"Unsupported k_type for ClickHouse: {self.k_type}")

        time_col = "datetime" if self.k_type in [KL_TYPE.K_1M, KL_TYPE.K_5M, KL_TYPE.K_15M, KL_TYPE.K_30M, KL_TYPE.K_60M] else "date"
        
        # Normalize code: remove dot for ClickHouse query
        normalized_code = self.code.lower().replace(".", "")
        
        begin_date = self.begin_date if self.begin_date else "1990-01-01"
        end_date = self.end_date if self.end_date else datetime.date.today().strftime("%Y-%m-%d")
        
        query = f"""
            SELECT {time_col}, open, high, low, close, volume, amount 
            FROM {table}
            WHERE code = %(code)s 
            AND {time_col} >= %(begin)s 
            AND {time_col} <= %(end)s
            ORDER BY {time_col} ASC
        """
        
        params = {
            'code': normalized_code,
            'begin': begin_date,
            'end': end_date
        }
        
        # print(f"DEBUG: Querying {table} with params: {params}")
        
        try:
            rows = self._client.execute(query, params)
            # print(f"DEBUG: Got {len(rows)} rows")
        except Exception as e:
            print(f"Query failed: {e}")
            return []
        
        data_list = []
        for row in rows:
            dt, _open, _high, _low, _close, _vol, _amt = row
            
            # Sanitize data: fix 0 values which cause validation errors
            if _close <= 0: continue # Skip invalid rows
            
            if _open <= 0: _open = _close
            if _high <= 0: _high = _close
            if _low <= 0: _low = _close
            
            # Ensure high is max and low is min
            _high = max(_high, _open, _close)
            _low = min(_low, _open, _close)
            
            if hasattr(dt, 'hour'):
                kl_time = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
            else:
                kl_time = CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)
            
            # Simple deduplication (assuming sorted input)
            if data_list and data_list[-1].time.ts == kl_time.ts:
                continue
                
            data_list.append(CKLine_Unit(
                {
                    DATA_FIELD.FIELD_TIME: kl_time,
                    DATA_FIELD.FIELD_OPEN: float(round(_open, 2)),
                    DATA_FIELD.FIELD_HIGH: float(round(_high, 2)),
                    DATA_FIELD.FIELD_LOW: float(round(_low, 2)),
                    DATA_FIELD.FIELD_CLOSE: float(round(_close, 2)),
                    DATA_FIELD.FIELD_VOLUME: float(_vol),
                    DATA_FIELD.FIELD_TURNOVER: float(_amt)
                }
            ))
            
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
