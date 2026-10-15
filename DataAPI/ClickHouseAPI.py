import os
import datetime
import logging
import threading
import time
from bisect import bisect_left, bisect_right
from typing import Iterable, Optional, List, Dict
from dotenv import load_dotenv
from clickhouse_driver import Client

from Common.CEnum import AUTYPE, KL_TYPE, DATA_FIELD
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

_logger = logging.getLogger(__name__)

# 负缓存 TTL（秒）：表不存在 / min(date) 兜底的负结果长期驻留会让新建表、新装数据
# 在进程内永久不可见，给负结果加 TTL 到期重查（正结果仍长期缓存）。
_NEG_CACHE_TTL_ENV = "MLCHAN_CLICKHOUSE_NEG_CACHE_TTL_SEC"
_NEG_CACHE_TTL_DEFAULT = 60.0
# 分钟级大跨度查询按自然日分段执行（默认约一个季度/段），单次结果集从"全量物化"
# 降为"分段物化"，限制单次服务端扫描与网络缓冲峰值；<=0 表示禁用分段。
_QUERY_SEGMENT_DAYS_ENV = "MLCHAN_CLICKHOUSE_QUERY_SEGMENT_DAYS"
_QUERY_SEGMENT_DAYS_DEFAULT = 92
# _resample_intraday_from_lower_data 中 _fill_session 合成缺失分钟平盘 K 线的开关。
# 默认开启（保持既有行为）；置 0/false 关闭，重采样只聚合真实分钟线。
_FILL_SESSION_ENV = "MLCHAN_CLICKHOUSE_FILL_SESSION"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except Exception:
        return int(default)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _split_query_segments(begin_query: str, end_query: str, is_intraday: bool, seg_days: Optional[int] = None):
    """把 [begin_query, end_query] 按自然日切成若干段（仅分钟级大跨度生效）。

    返回 [(seg_begin, seg_end), ...]：首段 begin / 末段 end 保留原始串（可能含
    时分秒，保证边界语义与单发查询完全一致），中间段为整日 00:00:00 ~ 23:59:59。
    段间按自然日互不重迭，合并结果与单发全量查询等价。
    非分钟级 / seg_days<=0 / 跨度不足一段时返回单段。
    """
    if not is_intraday:
        return [(begin_query, end_query)]
    if seg_days is None:
        seg_days = _env_int(_QUERY_SEGMENT_DAYS_ENV, _QUERY_SEGMENT_DAYS_DEFAULT)
    if seg_days <= 0:
        return [(begin_query, end_query)]

    def _to_date(s):
        try:
            return datetime.date.fromisoformat(str(s).strip()[:10])
        except Exception:
            return None

    b = _to_date(begin_query)
    e = _to_date(end_query)
    if b is None or e is None or e < b or (e - b).days <= seg_days:
        return [(begin_query, end_query)]

    segs = []
    cur = b
    while cur <= e:
        seg_end = min(cur + datetime.timedelta(days=seg_days - 1), e)
        seg_begin_s = begin_query if cur == b else f"{cur.isoformat()} 00:00:00"
        seg_end_s = end_query if seg_end == e else f"{seg_end.isoformat()} 23:59:59"
        segs.append((seg_begin_s, seg_end_s))
        cur = seg_end + datetime.timedelta(days=1)
    return segs


class _LockedClickHouseClient:
    def __init__(self, client: Client, lock: threading.Lock):
        self._client = client
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._client.execute(*args, **kwargs)

    def execute_iter(self, *args, **kwargs):
        # 逐块流式读取：锁只覆盖每次 next()（驱动在该调用内做 socket 读），
        # 不跨 yield 持锁，避免长时间阻塞其它线程。
        it = self._client.execute_iter(*args, **kwargs)
        while True:
            with self._lock:
                try:
                    row = next(it)
                except StopIteration:
                    return
            yield row

    def disconnect(self):
        with self._lock:
            return self._client.disconnect()

    def __getattr__(self, name: str):
        # 旁路锁修复：callable 属性（如 execute_iter 以外的方法）此前经由
        # __getattr__ 直接返回裸 client 方法，调用时不持锁。这里把 callable
        # 包一层锁；非 callable 属性（如 last_query 状态）原样返回。
        attr = getattr(self._client, name)
        if callable(attr):
            def _locked(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return _locked
        return attr

class CClickHouseAPI(CCommonStockApi):
    _client = None
    _client_lock = threading.Lock()
    _init_lock = threading.Lock()
    _qfq_factor_cache = {}
    _hfq_factor_cache = {}
    _table_exists_cache = {}
    _min_daily_date_cache = {}
    _last_init_error = ""
    _last_init_error_ts = 0.0
    _last_init_log_ts = 0.0

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
            try:
                port = int(os.getenv("DB_PORT", 9000))
            except Exception:
                port = 9000
            user = os.getenv("DB_USER", "default")
            password = os.getenv("DB_PASSWORD", "")
            database = os.getenv("DB_NAME", "stock_data")
            try:
                retry_sec = float(os.getenv("MLCHAN_CLICKHOUSE_INIT_RETRY_SEC", "15") or 15)
            except Exception:
                retry_sec = 15.0
            now_ts = time.monotonic()
            if cls._last_init_error_ts > 0 and now_ts - cls._last_init_error_ts < max(0.0, retry_sec):
                return

            raw_client = None
            probe_client = None
            # probe+正式双连接：先用短超时 probe_client 探活（MLCHAN_CLICKHOUSE_INIT_TIMEOUT，
            # 缺省 = connect_timeout），CH 不可达时快速失败并走 retry_sec 节流重试，
            # 避免正式连接的长 send_receive_timeout（默认 300s）卡在握手期；
            # 探活成功后再以完整超时参数建正式 raw_client。
            driver_loggers = [
                logging.getLogger("clickhouse_driver"),
                logging.getLogger("clickhouse_driver.connection"),
            ]
            logger_disabled = [lg.disabled for lg in driver_loggers]
            try:
                try:
                    connect_timeout = float(os.getenv("DB_CONNECT_TIMEOUT", "3") or 3)
                except Exception:
                    connect_timeout = 3.0
                try:
                    init_timeout = float(os.getenv("MLCHAN_CLICKHOUSE_INIT_TIMEOUT", str(connect_timeout)) or connect_timeout)
                except Exception:
                    init_timeout = connect_timeout
                try:
                    send_receive_timeout = float(os.getenv("DB_SEND_RECEIVE_TIMEOUT", "300") or 300)
                except Exception:
                    send_receive_timeout = 300.0
                for lg in driver_loggers:
                    lg.disabled = True
                probe_client = Client(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=connect_timeout,
                    send_receive_timeout=max(1.0, init_timeout),
                )
                probe_client.execute("SELECT 1")
                probe_client.disconnect()
                probe_client = None

                raw_client = Client(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=connect_timeout,
                    send_receive_timeout=send_receive_timeout,
                )
                locked_client = _LockedClickHouseClient(raw_client, cls._client_lock)
                cls._client = locked_client
                cls._last_init_error = ""
                cls._last_init_error_ts = 0.0
            except Exception as e:
                cls._last_init_error = f"{type(e).__name__}: {e}"
                cls._last_init_error_ts = time.monotonic()
                try:
                    log_interval = float(os.getenv("MLCHAN_CLICKHOUSE_INIT_LOG_INTERVAL_SEC", "60") or 60)
                except Exception:
                    log_interval = 60.0
                if cls._last_init_log_ts <= 0 or cls._last_init_error_ts - cls._last_init_log_ts >= max(0.0, log_interval):
                    _logger.warning(
                        f"ClickHouse unavailable at {host}:{port}/{database}: {cls._last_init_error}. "
                        "Backend will keep running and retry on later DB requests. "
                        "clickhouse_driver requires the native TCP port, usually 9000; HTTP port 8123 will not work here."
                    )
                    cls._last_init_log_ts = cls._last_init_error_ts
                try:
                    if probe_client is not None:
                        probe_client.disconnect()
                    if raw_client is not None:
                        raw_client.disconnect()
                except Exception:
                    pass
                cls._client = None
            finally:
                for lg, disabled in zip(driver_loggers, logger_disabled):
                    lg.disabled = disabled

    @classmethod
    def do_close(cls):
        # 与 do_init 共用 _init_lock：此前 do_close 无锁，可能与 do_init 并发
        # （close 刚清完又被 init 重连 / init 建连期间被 close 置 None 泄漏连接）。
        with cls._init_lock:
            client = cls._client
            cls._client = None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:
            pass

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
            # 返回 None（区别于 []）：[] 表示"表存在但无该列/表不存在"，
            # None 表示"元数据查询本身失败"。调用方如需区分 DB 故障可判 None。
            _logger.warning(f"ClickHouse columns query failed for {table}: {type(e).__name__}: {e}")
            return None

    @classmethod
    def _table_exists(cls, table: str, database: Optional[str] = None) -> bool:
        client = cls.get_client()
        if not client:
            return False

        cache_key = (database or "", table)
        cached = cls._table_exists_cache.get(cache_key)
        if cached is not None:
            # 负缓存 TTL：False 结果到期重查，避免建表后进程内长期判不存在；
            # True 结果不随 TTL 失效。
            exists_val, cached_ts = cached
            if exists_val or (time.monotonic() - cached_ts) < _env_float(_NEG_CACHE_TTL_ENV, _NEG_CACHE_TTL_DEFAULT):
                return bool(exists_val)

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
        except Exception as e:
            _logger.warning(f"ClickHouse table_exists query failed for {table}: {type(e).__name__}: {e}")
            exists = False

        if len(cls._table_exists_cache) > 1024:
            cls._table_exists_cache.clear()
        cls._table_exists_cache[cache_key] = (exists, time.monotonic())
        return exists

    @classmethod
    def _infer_asset_type_from_code(cls, normalized_code: str) -> str:
        code = str(normalized_code or "").strip().lower().replace(".", "")
        market = code[:2] if len(code) >= 8 and code[:2] in ("sh", "sz", "bj") else ""
        code6 = code[2:8] if market else code[-6:]
        if len(code6) != 6 or not code6.isdigit():
            return "STOCK"

        if market == "sh" and (code6.startswith("000") or code6 == "999999"):
            return "INDEX"
        if market == "sz" and code6.startswith("399"):
            return "INDEX"
        if market == "bj" and code6.startswith("899"):
            return "INDEX"
        if not market and code6.startswith("399"):
            return "INDEX"

        if market == "sh" and code6.startswith(("51", "52", "56", "58")):
            return "ETF"
        if market == "sz" and code6.startswith(("15", "16")):
            return "ETF"
        if not market and code6.startswith(("51", "52", "56", "58", "15", "16")):
            return "ETF"

        return "STOCK"

    @classmethod
    def _asset_route_from_type(cls, asset_type: str, normalized_code: str = "") -> Dict[str, object]:
        asset = str(asset_type or "STOCK").strip().upper()
        if asset in ("AUTO", "DETECT"):
            asset = cls._infer_asset_type_from_code(normalized_code)
        elif asset in ("ETF", "FUND", "FUNDS"):
            asset = "ETF"
        elif asset in ("INDEX", "IDX", "BENCHMARK"):
            asset = "INDEX"
        else:
            asset = "STOCK"

        if asset == "INDEX":
            return {
                "asset": "INDEX",
                "prefix": "index_",
                "info_table": "index_info",
                "daily_table": "index_daily",
                "code_candidates": ["code", "ts_code", "symbol"],
                "name_candidates": ["name", "index_name", "fullname", "full_name"],
                "allow_stock_adjust": False,
            }
        if asset == "ETF":
            return {
                "asset": "ETF",
                "prefix": "etf_",
                "info_table": "etf_info",
                "daily_table": "etf_daily",
                "code_candidates": ["code", "ts_code", "symbol", "sec_code", "fund_code"],
                "name_candidates": ["name", "fund_name", "sec_name", "short_name", "fullname", "full_name"],
                "allow_stock_adjust": False,
            }
        return {
            "asset": "STOCK",
            "prefix": "stock_",
            "info_table": "stock_info",
            "daily_table": "stock_daily",
            "code_candidates": ["code", "stock_code", "symbol", "ts_code", "sec_code"],
            "name_candidates": ["name", "stock_name", "sec_name", "short_name"],
            "allow_stock_adjust": True,
        }

    def _resolve_asset_route(self, normalized_code: str = "") -> Dict[str, object]:
        code = normalized_code or str(self.code or "").lower().replace(".", "")
        return self._asset_route_from_type(self.asset_type, code)

    @classmethod
    def _get_min_daily_date(cls, normalized_code: str) -> datetime.date:
        cached = cls._min_daily_date_cache.get(normalized_code)
        if isinstance(cached, tuple):
            dt, cached_ts, is_fallback = cached
            if (not is_fallback) or (time.monotonic() - cached_ts) < _env_float(_NEG_CACHE_TTL_ENV, _NEG_CACHE_TTL_DEFAULT):
                return dt
        elif isinstance(cached, datetime.date):
            return cached

        client = cls.get_client()
        if not client:
            _logger.warning("ClickHouse min(date) probe skipped: client unavailable")
            cls._min_daily_date_cache[normalized_code] = (datetime.date(1990, 1, 1), time.monotonic(), True)
            return datetime.date(1990, 1, 1)

        is_fallback = False
        try:
            rows = client.execute(
                "SELECT min(date) FROM stock_daily WHERE code = %(code)s",
                {"code": normalized_code},
            )
            raw = rows[0][0] if rows and rows[0] else None
            dt = cls._parse_ymd(raw) or (raw if isinstance(raw, datetime.date) else None)
            if dt is None:
                dt = datetime.date(1990, 1, 1)
                is_fallback = True
        except Exception as e:
            _logger.warning(f"ClickHouse min(date) query failed for {normalized_code}: {type(e).__name__}: {e}")
            dt = datetime.date(1990, 1, 1)
            is_fallback = True

        # 负缓存 TTL：仅兜底值（无连接/查询失败/表空）标 is_fallback 并到期重查；
        # 真实 min(date) 不随 TTL 失效。
        if len(cls._min_daily_date_cache) > 4096:
            cls._min_daily_date_cache.clear()
        cls._min_daily_date_cache[normalized_code] = (dt, time.monotonic(), is_fallback)
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
                _logger.warning(f"ClickHouse list pools failed: {e}")
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
            _logger.warning(f"ClickHouse list pools failed: {e}")
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
    def get_pool_members(
        cls,
        pool_id: str,
        table: str = "stock_constituent",
        limit: int = 5000,
        as_of_date: Optional[str] = None,
        snapshot_date: Optional[str] = None,
    ):
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

            dt = None
            if snapshot_date is not None and str(snapshot_date).strip():
                dt = str(snapshot_date).strip()
                params["target_dt"] = dt
                dt_expr = "%(target_dt)s"
            else:
                aod = str(as_of_date).strip() if as_of_date is not None else ""
                if aod:
                    params["as_of_dt"] = aod
                    dt_expr = f"maxIf({snapshot_date_col}, {snapshot_date_col} <= %(as_of_dt)s)"
                else:
                    dt_expr = f"max({snapshot_date_col})"

            query = f"""
                WITH (
                    SELECT {dt_expr}
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
                _logger.warning(f"ClickHouse get pool members failed: {e}")
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
            dt = None
            params = {"pool_id": pool_id, "limit": int(limit)}
            if snapshot_date is not None and str(snapshot_date).strip():
                dt = str(snapshot_date).strip()
                params["target_dt"] = dt
                dt_expr = "%(target_dt)s"
            else:
                aod = str(as_of_date).strip() if as_of_date is not None else ""
                if aod:
                    params["as_of_dt"] = aod
                    dt_expr = f"maxIf({date_col}, {date_col} <= %(as_of_dt)s)"
                else:
                    dt_expr = f"max({date_col})"
            query = f"""
                WITH (
                    SELECT {dt_expr}
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
            if date_col:
                rows = client.execute(query, params)
            else:
                rows = client.execute(query, {"pool_id": pool_id, "limit": int(limit)})
        except Exception as e:
            _logger.warning(f"ClickHouse get pool members failed: {e}")
            return []

        return [str(r[0]) for r in rows]

    @classmethod
    def get_pool_snapshot_members(
        cls,
        pool_id: str,
        start_date: str,
        end_date: str,
        table: str = "stock_constituent",
        limit: int = 200000,
    ) -> Dict[str, List[str]]:
        client = cls.get_client()
        if not client:
            return {}
        cols = cls._get_table_columns(table)
        if not cols:
            return {}

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
            params = {"group_code": group_code, "start": str(start_date), "end": str(end_date), "limit": int(limit)}
            if group_type_col and group_type is not None:
                where = where + f" AND {group_type_col} = %(group_type)s"
                params["group_type"] = group_type

            query = f"""
                WITH (
                    SELECT maxIf({snapshot_date_col}, {snapshot_date_col} <= %(start)s)
                    FROM {table}
                    WHERE {where}
                ) AS base_dt
                SELECT
                    {snapshot_date_col} AS snapshot_date,
                    {stock_code_col} AS code
                FROM {table}
                WHERE {where}
                  AND {snapshot_date_col} >= coalesce(base_dt, toDate('1970-01-01'))
                  AND {snapshot_date_col} <= %(end)s
                LIMIT %(limit)s
            """
            try:
                rows = client.execute(query, params)
            except Exception as e:
                _logger.warning(f"ClickHouse get pool snapshot members failed: {type(e).__name__}: {e}")
                return {}

            by_dt: Dict[str, List[str]] = {}
            for dt_value, code_value in rows:
                dt = cls._parse_ymd(dt_value)
                if dt is None:
                    continue
                code = str(code_value) if code_value is not None else ""
                code = code.strip()
                if not code:
                    continue
                k = dt.strftime("%Y-%m-%d")
                by_dt.setdefault(k, []).append(code)
            for k, v in list(by_dt.items()):
                by_dt[k] = list(dict.fromkeys([str(x) for x in v if str(x).strip()]))
            return by_dt

        def pick_first(candidates):
            for c in candidates:
                if c in col_set:
                    return orig_by_lower[c]
            return None

        code_col = pick_first(["code", "stock_code", "symbol", "sec_code", "ts_code"])
        pool_id_col = pick_first(["index_code", "pool_code", "sector_code", "concept_code", "board_code", "pool", "pool_id", "index", "index_id", "category", "group", "name"])
        date_col = pick_first(["trade_date", "date", "dt", "as_of", "update_date", "effective_date"])
        if not (code_col and pool_id_col and date_col):
            return {}

        params = {"pool_id": str(pool_id), "start": str(start_date), "end": str(end_date), "limit": int(limit)}
        query = f"""
            WITH (
                SELECT maxIf({date_col}, {date_col} <= %(start)s)
                FROM {table}
                WHERE {pool_id_col} = %(pool_id)s
            ) AS base_dt
            SELECT
                {date_col} AS snapshot_date,
                {code_col} AS code
            FROM {table}
            WHERE {pool_id_col} = %(pool_id)s
              AND {date_col} >= coalesce(base_dt, toDate('1970-01-01'))
              AND {date_col} <= %(end)s
            LIMIT %(limit)s
        """
        try:
            rows = client.execute(query, params)
        except Exception as e:
            _logger.warning(f"ClickHouse get pool snapshot members failed: {type(e).__name__}: {e}")
            return {}

        by_dt: Dict[str, List[str]] = {}
        for dt_value, code_value in rows:
            dt = cls._parse_ymd(dt_value)
            if dt is None:
                continue
            code = str(code_value) if code_value is not None else ""
            code = code.strip()
            if not code:
                continue
            k = dt.strftime("%Y-%m-%d")
            by_dt.setdefault(k, []).append(code)
        for k, v in list(by_dt.items()):
            by_dt[k] = list(dict.fromkeys([str(x) for x in v if str(x).strip()]))
        return by_dt

    def __setattr__(self, name, value):
        # _client 是进程级类属性单例（do_init/do_close/get_client 统一管理）。
        # 实例侧写 self._client 会遮蔽类属性：do_close 只清类属性，实例仍持失效
        # 客户端继续查询且异常被吞成空数据 —— 历史上 _ensure_connection 就犯过。
        # 这里直接禁掉实例写，保持"唯一存储在类属性"的一致性约定。
        if name == "_client":
            raise AttributeError(
                "CClickHouseAPI._client is a class-level singleton managed by "
                "do_init/do_close; do not assign on instances"
            )
        super().__setattr__(name, value)

    def _warn_once(self, msg: str):
        # 与 TdxAPI._warn_once 同约定：记录进 self.warnings（Chan.py 会汇入
        # data_warnings 透传到 API 响应），同时落日志；两者都按消息去重，
        # 避免同一告警在日志里每轮迭代重打一遍。
        if msg not in self.warnings:
            self.warnings.append(msg)
            _logger.warning(msg)

    def _ensure_connection(self):
        # 只触发类级惰性建连（do_init 内部有 retry 节流），不往实例写 _client。
        # 历史实现写 self._client 造成实例属性遮蔽类属性，do_close 后实例仍持
        # 失效 client —— 现统一为只读类属性（self._client 解析到 CClickHouseAPI._client）。
        self.get_client()

    @classmethod
    def _new_light_instance(cls, code, k_type, begin_date, end_date, autype, asset_type="STOCK"):
        """绕过 __init__ 构造的轻量实例，供 _resample_intraday_from_lower 复用查询逻辑。

        正规构造会经基类 _ensure_basic_info 调 SetBasciInfo → info 表查询（每次重采样
        一次 N+1）。重采样只需要 get_kl_data 的取数逻辑，name/is_stock 用不到，
        故直接跳过构造期 DB 访问（_basic_info_ready=True 防惰性触发）。
        """
        inst = object.__new__(cls)
        inst.code = code
        inst.name = None
        inst.is_stock = None
        inst.k_type = k_type
        inst.begin_date = begin_date
        inst.end_date = end_date
        inst.autype = autype
        inst.asset_type = asset_type
        inst.warnings = []
        inst._basic_info_ready = True  # SetBasciInfo 已跳过，勿再惰性触发
        return inst

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
            self._warn_once(f"clickhouse_qfq_unavailable:{normalized_code}:client not initialized, prices NOT adjusted")
            return ([], {})
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        begin_lookback_dt = begin_dt - datetime.timedelta(days=120)
        begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
        end_key = end_dt.strftime("%Y-%m-%d")
        cache_key = (normalized_code, begin_lookback, end_key)
        cached = self._qfq_factor_cache.get(cache_key)
        if cached is not None:
            return cached

        # _get_table_columns 返回 None 表示元数据查询失败（区别于表无该列的 []）
        div_cols = self._get_table_columns("stock_dividend") or []
        if not div_cols:
            _logger.warning("ClickHouse stock_dividend schema probe returned no columns")
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
            except Exception as e:
                # 失败≠无分红：静默置空会使复权因子缺失而返回未复权价
                self._warn_once(
                    f"clickhouse_dividend_query_failed:{normalized_code}:{type(e).__name__}:{e}; QFQ factors may be incomplete"
                )
                dividend_rows = []

        daily_query = """
            SELECT date, open, close
            FROM stock_daily
            WHERE code = %(code)s
              AND date >= %(begin)s
              AND date <= %(end)s
            ORDER BY date ASC
        """
        daily_failed = False
        try:
            daily_rows = self._client.execute(
                daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key}
            )
        except Exception as e:
            self._warn_once(
                f"clickhouse_factor_daily_query_failed:{normalized_code}:{type(e).__name__}:{e}; prices NOT adjusted"
            )
            daily_rows = []
            daily_failed = True

        if not daily_rows:
            result = ([], {})
            if daily_failed:
                # 查询失败的空结果不入缓存，避免瞬时故障被记成"长期无数据"
                return result
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
            self._warn_once(f"clickhouse_hfq_unavailable:{normalized_code}:client not initialized, prices NOT adjusted")
            return ([], {})
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        begin_lookback_dt = self._get_min_daily_date(normalized_code)
        begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
        end_key = end_dt.strftime("%Y-%m-%d")
        cache_key = (normalized_code, begin_lookback, end_key)
        cached = self._hfq_factor_cache.get(cache_key)
        if cached is not None:
            return cached

        # _get_table_columns 返回 None 表示元数据查询失败（区别于表无该列的 []）
        div_cols = self._get_table_columns("stock_dividend") or []
        if not div_cols:
            _logger.warning("ClickHouse stock_dividend schema probe returned no columns")
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
            except Exception as e:
                # 失败≠无分红：静默置空会使复权因子缺失而返回未复权价
                self._warn_once(
                    f"clickhouse_dividend_query_failed:{normalized_code}:{type(e).__name__}:{e}; HFQ factors may be incomplete"
                )
                dividend_rows = []

        daily_query = """
            SELECT date, open, close
            FROM stock_daily
            WHERE code = %(code)s
              AND date >= %(begin)s
              AND date <= %(end)s
            ORDER BY date ASC
        """
        daily_failed = False
        try:
            daily_rows = self._client.execute(
                daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key}
            )
        except Exception as e:
            self._warn_once(
                f"clickhouse_factor_daily_query_failed:{normalized_code}:{type(e).__name__}:{e}; prices NOT adjusted"
            )
            daily_rows = []
            daily_failed = True

        if not daily_rows:
            result = ([], {})
            if daily_failed:
                # 查询失败的空结果不入缓存，避免瞬时故障被记成"长期无数据"
                return result
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
                    # round(...,4) 而非 2：ETF/基金最小变动价为 0.001，round2 会丢末位精度；
                    # 复权价本身为浮点，保留 4 位仅去除浮点尾差。纯 2 位小数标的结果不变。
                    data_list.append(
                        CKLine_Unit(
                            {
                                DATA_FIELD.FIELD_TIME: CTime(
                                    agg_last_date.year, agg_last_date.month, agg_last_date.day, 0, 0, auto=False
                                ),
                                DATA_FIELD.FIELD_OPEN: float(round(agg_open, 4)),
                                DATA_FIELD.FIELD_HIGH: float(round(agg_high, 4)),
                                DATA_FIELD.FIELD_LOW: float(round(agg_low, 4)),
                                DATA_FIELD.FIELD_CLOSE: float(round(agg_close, 4)),
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
                        DATA_FIELD.FIELD_OPEN: float(round(agg_open, 4)),
                        DATA_FIELD.FIELD_HIGH: float(round(agg_high, 4)),
                        DATA_FIELD.FIELD_LOW: float(round(agg_low, 4)),
                        DATA_FIELD.FIELD_CLOSE: float(round(agg_close, 4)),
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
            self._warn_once(f"clickhouse_trading_dates_unavailable:{normalized_code}:client not initialized")
            return []
        begin_dt = self._parse_ymd(begin_date) or datetime.date(1990, 1, 1)
        end_dt = self._parse_ymd(end_date) or datetime.date.today()
        if end_dt < begin_dt:
            begin_dt, end_dt = end_dt, begin_dt
        route = self._resolve_asset_route(normalized_code)
        table = str(route.get("daily_table") or "stock_daily")

        cols = self._get_table_columns(table) or []
        cols_lower = {c.lower(): c for c in cols}
        date_col = self._pick_column(cols_lower, ["date", "trade_date", "dt"])
        code_col = self._pick_column(cols_lower, list(route.get("code_candidates") or ["code", "ts_code", "symbol"]))
        if not date_col or not code_col:
            return []
        try:
            rows = self._client.execute(
                f"""
                SELECT {date_col}
                FROM {table}
                WHERE {code_col} = %(code)s
                  AND {date_col} >= %(begin)s
                  AND {date_col} <= %(end)s
                ORDER BY {date_col} ASC
                """,
                {"code": normalized_code, "begin": begin_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            )
        except Exception as e:
            self._warn_once(
                f"clickhouse_trading_dates_query_failed:{normalized_code}:{type(e).__name__}:{e}"
            )
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

        fill_synth_count = 0  # _fill_session 凭空合成的分钟条数（仅统计，供打标/告警）

        if used_lower_k_type == KL_TYPE.K_1M:
            by_dt = {}
            for klu in lower_data:
                dt = _klu_dt(klu)
                if dt is None or not _is_trade_time(dt):
                    continue
                by_dt[dt] = klu

            def _fill_session(day: datetime.date, start_h: int, start_m: int, end_h: int, end_m: int):
                nonlocal fill_synth_count
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
                    synth_klu = CKLine_Unit(
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
                    # 合成条标记：该 KLU 非真实成交（0 成交量平盘补齐），下游可经
                    # getattr(klu, "is_synthetic", False) 或聚合产出条上的
                    # has_synthetic_minutes / trade_info.metric["synthetic_minutes"] 分辨。
                    synth_klu.is_synthetic = True
                    by_dt[cur] = synth_klu
                    fill_synth_count += 1
                    last_close = px
                    cur += datetime.timedelta(minutes=1)

            # 配置开关：MLCHAN_CLICKHOUSE_FILL_SESSION=0 时不再凭空补分钟，
            # 聚合桶只含真实分钟线（聚合逻辑对缺口天然兼容）。
            if _env_flag(_FILL_SESSION_ENV, True):
                days = sorted(list({dt.date() for dt in by_dt.keys()}))
                for day in days:
                    _fill_session(day, 9, 30, 11, 30)
                    _fill_session(day, 13, 0, 15, 0)
                if fill_synth_count:
                    self._warn_once(
                        f"clickhouse_fill_session:{self.code}:{target_k_type.name}:"
                        f"synthetic_1min_bars={fill_synth_count} (0-volume flat bars injected for missing minutes)"
                    )
            elif by_dt:
                self._warn_once(
                    f"clickhouse_fill_session_disabled:{self.code}:{target_k_type.name}:"
                    "missing minutes are NOT synthesized (MLCHAN_CLICKHOUSE_FILL_SESSION=0)"
                )
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
        route = self._resolve_asset_route(normalized_code)
        allow_stock_adjust = bool(route.get("allow_stock_adjust", False))
        factor_trading_dates = []
        factor_by_date = {}
        if allow_stock_adjust and self.autype == AUTYPE.QFQ:
            factor_trading_dates, factor_by_date = self._get_qfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )
        elif allow_stock_adjust and self.autype == AUTYPE.HFQ:
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
            synth_cnt = 0
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
                if getattr(klu, "is_synthetic", False):
                    synth_cnt += 1
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
            out_klu = CKLine_Unit(
                {
                    DATA_FIELD.FIELD_TIME: t,
                    DATA_FIELD.FIELD_OPEN: float(round(o, 4)),
                    DATA_FIELD.FIELD_HIGH: float(round(h, 4)),
                    DATA_FIELD.FIELD_LOW: float(round(l, 4)),
                    DATA_FIELD.FIELD_CLOSE: float(round(c, 4)),
                    DATA_FIELD.FIELD_VOLUME: float(vol_sum),
                    DATA_FIELD.FIELD_TURNOVER: float(amt_sum),
                }
            )
            if synth_cnt:
                # 桶内掺有 _fill_session 合成分钟：标记到产出条，下游可分辨真伪
                out_klu.has_synthetic_minutes = True
                out_klu.trade_info.metric["synthetic_minutes"] = float(synth_cnt)
            out.append(out_klu)
        return out

    def _resample_intraday_from_lower(self, target_k_type: KL_TYPE, begin_date: str, end_date: str):
        """返回 (resampled_rows, used_lower_k_type)；无产出/不支持级别返回 ([], None)。

        used_lower_k_type 记录实际取到数据的低级别（K_1M/K_5M 依次回退），供调用方
        的 clickhouse_resample 警告如实标注来源级别。
        """
        interval_map = {
            KL_TYPE.K_5M: 5,
            KL_TYPE.K_15M: 15,
            KL_TYPE.K_30M: 30,
            KL_TYPE.K_60M: 60,
        }
        interval_min = interval_map.get(target_k_type)
        if interval_min is None:
            return [], None

        candidates = [KL_TYPE.K_1M, KL_TYPE.K_5M]
        lower_data = []
        used_lower_k_type = None
        for lower_k_type in candidates:
            # 轻量实例：正规构造会触发 SetBasciInfo → info 表查询（每次重采样 N+1）；
            # 这里只需要 get_kl_data 的取数路径。连接是类级单例，无需也不允许
            # 再向实例写 _client（原 lower_api._client = self._client 为死赋值，
            # 会立刻被 _ensure_connection 的类属性覆盖，且现已被 __setattr__ 禁写）。
            lower_api = CClickHouseAPI._new_light_instance(
                code=self.code,
                k_type=lower_k_type,
                begin_date=begin_date,
                end_date=end_date,
                autype=AUTYPE.NONE,
                asset_type=self.asset_type,
            )
            try:
                tmp = list(lower_api.get_kl_data() or [])
            except Exception as e:
                # 某个低级别查询失败（如 1min 表故障）时尝试下一个候选级别，
                # 但把失败原因记入 warnings 而不是静默吞掉。
                self._warn_once(
                    f"clickhouse_resample_lower_failed:{self.code}:{lower_k_type.name}:{type(e).__name__}:{e}"
                )
                tmp = []
            if lower_api.warnings:
                self.warnings.extend(lower_api.warnings)
            if tmp:
                lower_data = tmp
                used_lower_k_type = lower_k_type
                break

        if not lower_data or used_lower_k_type is None:
            return [], None

        return self._resample_intraday_from_lower_data(
            target_k_type=target_k_type,
            lower_data=lower_data,
            used_lower_k_type=used_lower_k_type,
            begin_date=begin_date,
            end_date=end_date,
        ), used_lower_k_type

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        self._ensure_connection()
        if not self._client:
            # 不再静默 return []：调用方无法区分"无数据"与"DB 故障"。
            # 抛 SRC_DATA_NOT_FOUND，Chan.py 可据此 auto_skip_illegal_sub_lv 跳过子级别。
            err = self._last_init_error or "unknown init failure"
            raise CChanException(
                f"ClickHouse client not initialized: {err}",
                ErrCode.SRC_DATA_NOT_FOUND,
            )

        normalized_code = self.code.lower().replace(".", "")
        route = self._resolve_asset_route(normalized_code)
        asset = str(route.get("asset") or "STOCK")
        prefix = str(route.get("prefix") or "stock_")
        allow_stock_adjust = bool(route.get("allow_stock_adjust", False))

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

        if allow_stock_adjust and self.autype == AUTYPE.HFQ and self.k_type in (KL_TYPE.K_WEEK, KL_TYPE.K_MON):
            need_aggregate = True
            query_k_type = KL_TYPE.K_DAY
            table = table_map.get(query_k_type)
        elif allow_stock_adjust and self.autype == AUTYPE.QFQ and self.k_type in (KL_TYPE.K_WEEK, KL_TYPE.K_MON):
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
            if allow_stock_adjust and self.autype == AUTYPE.QFQ and table:
                qfq_table = f"{table}_qfq"
                if self._table_exists(qfq_table):
                    table = qfq_table
                    use_precomputed_qfq = True

        if asset in ("INDEX", "ETF") and query_k_type in (KL_TYPE.K_WEEK, KL_TYPE.K_MON) and table and (not self._table_exists(table)):
            need_aggregate = True
            query_k_type = KL_TYPE.K_DAY
            table = table_map.get(query_k_type)
            use_precomputed_qfq = False

        if not table:
            raise CChanException(
                f"Unsupported k_type for ClickHouse: {query_k_type}",
                ErrCode.PARA_ERROR,
            )

        base_table = table_map.get(query_k_type)

        is_intraday = query_k_type in [KL_TYPE.K_1M, KL_TYPE.K_5M, KL_TYPE.K_15M, KL_TYPE.K_30M, KL_TYPE.K_60M]
        
        begin_date = self.begin_date if self.begin_date else "1990-01-01"
        end_date = self.end_date if self.end_date else datetime.date.today().strftime("%Y-%m-%d")
        begin_query = begin_date
        end_query = end_date
        if is_intraday:
            begin_query = self._normalize_datetime_bound(begin_date, is_end=False)
            end_query = self._normalize_datetime_bound(end_date, is_end=True)

        params = {
            'code': normalized_code,
            'begin': begin_query,
            'end': end_query
        }

        def pick_cols_for_table(table_name: str):
            cols = self._get_table_columns(table_name)
            if cols is None:
                # _get_table_columns 的 None 表示元数据查询故障（区别于表缺失的 []），
                # 属 DB 故障，抛错而非静默空数据
                msg = f"ClickHouse schema probe failed for table {table_name} ({normalized_code})"
                self._warn_once(msg)
                raise CChanException(msg, ErrCode.SRC_DATA_NOT_FOUND)
            if not cols:
                return None
            cols_lower = {c.lower(): c for c in cols}
            time_col = self._pick_column(
                cols_lower,
                ["datetime", "time", "ts", "trade_time", "timestamp"]
                if is_intraday
                else ["date", "trade_date", "datetime", "dt"],
            )
            code_col = self._pick_column(cols_lower, ["code", "ts_code", "symbol", "sec_code", "fund_code"])
            open_col = self._pick_column(cols_lower, ["open", "open_price"])
            high_col = self._pick_column(cols_lower, ["high", "high_price"])
            low_col = self._pick_column(cols_lower, ["low", "low_price"])
            close_col = self._pick_column(cols_lower, ["close", "close_price"])
            vol_col = self._pick_column(cols_lower, ["volume", "vol", "trade_volume"])
            amt_col = self._pick_column(cols_lower, ["amount", "amt", "turnover", "trade_amount"])
            if not (time_col and code_col and open_col and high_col and low_col and close_col):
                return None
            return {
                "time_col": time_col,
                "code_col": code_col,
                "open_col": open_col,
                "high_col": high_col,
                "low_col": low_col,
                "close_col": close_col,
                "vol_expr": vol_col if vol_col else "0",
                "amt_expr": amt_col if amt_col else "0",
            }

        def run_query(table_name: str):
            picked = pick_cols_for_table(table_name)
            if not picked:
                return None
            time_col = picked["time_col"]
            code_col = picked["code_col"]
            open_col = picked["open_col"]
            high_col = picked["high_col"]
            low_col = picked["low_col"]
            close_col = picked["close_col"]
            vol_expr = picked["vol_expr"]
            amt_expr = picked["amt_expr"]
            q = f"""
                SELECT {time_col}, {open_col}, {high_col}, {low_col}, {close_col}, {vol_expr}, {amt_expr}
                FROM {table_name}
                WHERE {code_col} = %(code)s
                AND {time_col} >= %(begin)s
                AND {time_col} <= %(end)s
                ORDER BY {time_col} ASC
            """
            client = self.get_client()
            if not client:
                raise CChanException(
                    f"ClickHouse client not initialized: {self._last_init_error or 'unknown init failure'}",
                    ErrCode.SRC_DATA_NOT_FOUND,
                )
            # 分段查询：分钟级大跨度（1min×多年可达数十万行）一次 execute 全量物化
            # 服务端/网络缓冲峰值高；按自然日分段执行（默认 92 天/段，
            # MLCHAN_CLICKHOUSE_QUERY_SEGMENT_DAYS 可调，<=0 禁用），段间按天互不重迭，
            # 合并结果与单发等价 —— 下游仍需要 list（rows[0]/rows[-1] 与多趟遍历），
            # 故不能整体改生成器，只能限制单段规模。
            segments = _split_query_segments(begin_query, end_query, is_intraday)
            try:
                if len(segments) == 1:
                    return client.execute(q, params)
                all_rows = []
                for seg_begin, seg_end in segments:
                    seg_rows = client.execute(q, dict(params, begin=seg_begin, end=seg_end))
                    if seg_rows:
                        all_rows.extend(seg_rows)
                return all_rows
            except Exception as e:
                # 失败≠无数据：抛 CChanException（SRC_DATA_NOT_FOUND），
                # Chan.py 迭代期可据此 auto_skip_illegal_sub_lv
                msg = f"ClickHouse query failed on {table_name} for {normalized_code}: {type(e).__name__}: {e}"
                self._warn_once(msg)
                raise CChanException(msg, ErrCode.SRC_DATA_NOT_FOUND) from e

        rows = run_query(table)

        if rows is None:
            # 表不存在 / 缺必需列：合法空结果，但记录 warning 以便排查
            self._warn_once(f"clickhouse_table_unusable:{table}:{normalized_code}:{query_k_type.name}")
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
                        seg_rows, _seg_from = self._resample_intraday_from_lower(query_k_type, begin_date=seg_begin, end_date=seg_end)
                        patched_extra.extend(seg_rows)
                    if patched_extra:
                        try:
                            self.warnings.append(
                                f"clickhouse_patch_gap:{normalized_code}:{query_k_type.name}:missing_days:{len(missing)}:{begin_date}~{end_date}"
                            )
                        except Exception:
                            pass
            except Exception as e:
                self._warn_once(
                    f"clickhouse_patch_gap_failed:{normalized_code}:{query_k_type.name}:{type(e).__name__}:{e}"
                )
                patched_extra = []

        if (not rows) and query_k_type in (KL_TYPE.K_30M, KL_TYPE.K_60M):
            resampled, resample_from = self._resample_intraday_from_lower(query_k_type, begin_date=begin_date, end_date=end_date)
            if resampled:
                try:
                    self.warnings.append(
                        f"clickhouse_resample:{normalized_code}:{query_k_type.name}:"
                        f"from:{(resample_from or KL_TYPE.K_1M).name}:{begin_date}~{end_date}"
                    )
                except Exception:
                    pass
                return resampled

        factor_trading_dates = []
        factor_by_date = {}
        if allow_stock_adjust and self.autype == AUTYPE.QFQ and not use_precomputed_qfq:
            factor_trading_dates, factor_by_date = self._get_qfq_factors_by_date(
                normalized_code=normalized_code, begin_date=begin_date, end_date=end_date
            )
        elif allow_stock_adjust and self.autype == AUTYPE.HFQ:
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
                        DATA_FIELD.FIELD_OPEN: float(round(_open, 4)),
                        DATA_FIELD.FIELD_HIGH: float(round(_high, 4)),
                        DATA_FIELD.FIELD_LOW: float(round(_low, 4)),
                        DATA_FIELD.FIELD_CLOSE: float(round(_close, 4)),
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
        self._ensure_connection()
        if not self._client:
            _logger.debug("ClickHouse SetBasciInfo skipped: client not initialized")
            return

        normalized_code = self.code.lower().replace(".", "")
        route = self._resolve_asset_route(normalized_code)
        asset = str(route.get("asset") or "STOCK")

        def try_table(table: str, code_candidates: List[str], name_candidates: List[str]) -> Optional[str]:
            if not self._table_exists(table):
                return None
            cols = self._get_table_columns(table)
            if not cols:
                return None
            cols_lower = {c.lower(): c for c in cols}
            code_col = self._pick_column(cols_lower, code_candidates)
            name_col = self._pick_column(cols_lower, name_candidates)
            if not code_col or not name_col:
                return None
            try:
                rows = self._client.execute(
                    f"SELECT {name_col} FROM {table} WHERE {code_col} = %(code)s LIMIT 1",
                    {"code": normalized_code},
                )
                if rows and rows[0] and rows[0][0]:
                    return str(rows[0][0])
            except Exception as e:
                _logger.warning(f"ClickHouse basic info query failed on {table}: {type(e).__name__}: {e}")
                return None
            return None

        name = None
        info_table = str(route.get("info_table") or "")
        if info_table:
            name = try_table(
                info_table,
                list(route.get("code_candidates") or ["code", "ts_code", "symbol"]),
                list(route.get("name_candidates") or ["name"]),
            )

        self.name = name if name is not None else self.code
        self.is_stock = asset == "STOCK"

    @classmethod
    def _list_assets(cls, asset_type: str, limit: int = 2000, keyword: str = None):
        client = cls.get_client()
        if not client:
            return []

        route = cls._asset_route_from_type(asset_type)
        daily_table = str(route.get("daily_table") or "")
        if not cls._table_exists(daily_table):
            return []

        cols = cls._get_table_columns(daily_table) or []
        cols_lower = {c.lower(): c for c in cols}
        code_col_name = cls._pick_column(cols_lower, list(route.get("code_candidates") or ["code", "ts_code", "symbol"]))
        if not code_col_name:
            return []

        info_table = str(route.get("info_table") or "")
        if not cls._table_exists(info_table):
            info_table = None

        info_code_col = None
        info_name_col = None
        if info_table:
            info_cols = cls._get_table_columns(info_table) or []
            info_lower = {c.lower(): c for c in info_cols}
            info_code_col = cls._pick_column(info_lower, list(route.get("code_candidates") or ["code", "ts_code", "symbol"]))
            info_name_col = cls._pick_column(info_lower, list(route.get("name_candidates") or ["name"]))
            if not info_code_col or not info_name_col:
                info_table = None
        
        try:
            where_clause = ""
            params = {"limit": limit}
            if keyword:
                kw = f"%{keyword}%"
                params["kw"] = kw
                if info_table:
                    where_clause = f"WHERE a.{code_col_name} ILIKE %(kw)s OR b.{info_name_col} ILIKE %(kw)s"
                else:
                    where_clause = f"WHERE {code_col_name} ILIKE %(kw)s"

            if info_table:
                query = f"""
                    SELECT DISTINCT a.{code_col_name} as code, b.{info_name_col} 
                    FROM {daily_table} a
                    LEFT JOIN {info_table} b ON a.{code_col_name} = b.{info_code_col}
                    {where_clause}
                    LIMIT %(limit)s
                """
                rows = client.execute(query, params)
                result = []
                for r in rows:
                    c = str(r[0])
                    n = str(r[1]) if r[1] else c
                    result.append({"code": c, "name": n})
                return result
            else:
                query = f"""
                    SELECT DISTINCT {code_col_name} as code 
                    FROM {daily_table} 
                    {where_clause}
                    LIMIT %(limit)s
                """
                rows = client.execute(query, params)
                return [{"code": str(r[0]), "name": str(r[0])} for r in rows]
        except Exception as e:
            _logger.warning(f"List {route.get('asset', asset_type)} assets failed: {e}")
            return []

    @classmethod
    def list_indices(cls, limit: int = 2000, keyword: str = None):
        return cls._list_assets("INDEX", limit=limit, keyword=keyword)

    @classmethod
    def list_etfs(cls, limit: int = 2000, keyword: str = None):
        return cls._list_assets("ETF", limit=limit, keyword=keyword)
