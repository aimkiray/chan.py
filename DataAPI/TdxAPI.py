import datetime
import os
import threading
from typing import Iterable, Optional

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from Common.func_util import kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi


# 默认 TDX 行情服务器；可用环境变量 MLCHAN_TDX_SERVERS 覆盖，
# 格式 "host:port,host:port"（IP 不再只能改代码）。
_DEFAULT_TDX_SERVERS = (
    ("183.60.224.178", 7709),
    ("183.60.224.178", 7721),
    ("101.227.73.20", 7709),
    ("119.147.212.81", 7709),
)

# TDX hq 协议 market 编码：0=SZ, 1=SH。安装的 pytdx TDXParams 仅定义
# MARKET_SZ/MARKET_SH；北交所在通达信协议中的 de-facto 编码为 2
# （pytdx 只透传 market 字节，是否支持取决于服务器）。
_TDX_MARKET_BJ = 2


def _tdx_servers():
    raw = os.getenv("MLCHAN_TDX_SERVERS", "").strip()
    if not raw:
        return list(_DEFAULT_TDX_SERVERS)
    servers = []
    for part in raw.split(","):
        host, sep, port = part.strip().partition(":")
        host = host.strip()
        if not host or not sep:
            continue
        try:
            servers.append((host, int(port)))
        except ValueError:
            continue
    return servers or list(_DEFAULT_TDX_SERVERS)


def _parse_yyyymmdd_or_iso_to_date(s: Optional[str]) -> Optional[datetime.date]:
    if not s:
        return None
    ss = str(s).strip()
    if not ss:
        return None
    if len(ss) >= 8 and ss[:8].isdigit():
        try:
            y = int(ss[0:4])
            m = int(ss[4:6])
            d = int(ss[6:8])
            return datetime.date(y, m, d)
        except Exception:
            pass
    try:
        return datetime.date.fromisoformat(ss[:10])
    except Exception:
        return None


def _parse_bar_datetime(dt_str: str) -> Optional[datetime.datetime]:
    """Robust parse of pytdx bar ``datetime`` strings.

    Previously ``strptime(dt_str + ":00", "%Y-%m-%d %H:%M:%S")`` only worked for
    ``YYYY-MM-DD HH:MM`` inputs and silently dropped bars whose datetime already
    carried seconds. Accept the common variants instead.
    """
    s = str(dt_str or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M:%S",
                "%Y%m%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _tdx_market_from_code(code: str) -> Optional[int]:
    c = str(code or "").strip().lower()
    if c.startswith("sh"):
        return 1
    if c.startswith("sz"):
        return 0
    if c.startswith("bj"):
        # 北交所 de-facto market=2；旧代码落入 market=0(深市) 静默取空/错数据
        return _TDX_MARKET_BJ
    return None


def _code6_from_code(code: str) -> Optional[str]:
    c = str(code or "").strip()
    if "." in c:
        c = c.split(".")[-1]
    c = c[-6:]
    if len(c) != 6 or (not c.isdigit()):
        return None
    return c


def _is_index_code(code: str, code6: str, asset_type: str) -> bool:
    if str(asset_type or "").strip().upper() == "INDEX":
        return True
    c = str(code or "").strip().lower()
    s6 = str(code6 or "").strip()
    if c.startswith("sh") and s6.startswith("000"):
        return True
    if c.startswith("sh") and s6 == "999999":
        return True
    if c.startswith("sz") and s6.startswith("399"):
        return True
    return False


def _category_from_kl_type(k_type: KL_TYPE) -> Optional[int]:
    mapping = {
        KL_TYPE.K_1M: 8,
        KL_TYPE.K_5M: 0,
        KL_TYPE.K_15M: 1,
        KL_TYPE.K_30M: 2,
        KL_TYPE.K_60M: 3,
        KL_TYPE.K_DAY: 4,
        KL_TYPE.K_WEEK: 5,
        KL_TYPE.K_MON: 6,
    }
    return mapping.get(k_type)


def _bar_float(b, name):
    try:
        return float(b.get(name, 0.0) or 0.0)
    except Exception:
        return 0.0


class _LockedTdxHqClient:
    """Serialize calls on the shared TdxHq_API socket.

    Same pattern as ``_LockedClickHouseClient`` in ClickHouseAPI: the class-level
    ``cls.api`` singleton is shared by all instances/threads; without a lock,
    concurrent ``get_security_bars``/``get_index_bars`` calls interleave
    request/response bytes on the one TCP connection.

    Granularity: one lock per shared connection, held for a whole
    request/response cycle — identical to the ClickHouse wrapper. Deadlock risk
    is nil: the lock is never held while acquiring another of our locks, and
    pytdx's own per-call lock (enabled via ``multithread=True``) is always taken
    *inside* ours, giving a fixed lock order wrapper->pytdx.
    """

    def __init__(self, api, lock: threading.Lock):
        self._api = api
        self._lock = lock

    def get_security_bars(self, *args, **kwargs):
        with self._lock:
            return self._api.get_security_bars(*args, **kwargs)

    def get_index_bars(self, *args, **kwargs):
        with self._lock:
            return self._api.get_index_bars(*args, **kwargs)

    def connect(self, *args, **kwargs):
        with self._lock:
            return self._api.connect(*args, **kwargs)

    def disconnect(self, *args, **kwargs):
        with self._lock:
            return self._api.disconnect(*args, **kwargs)

    def __getattr__(self, name: str):
        # Passthrough for attributes/other methods. Any bypassed call is still
        # serialized by pytdx's internal lock (multithread=True).
        return getattr(self._api, name)


class CTdxStockApi(CCommonStockApi):
    api = None
    api_pid = None
    _api_lock = threading.Lock()
    _init_lock = threading.Lock()

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.NONE, asset_type="STOCK"):
        # autype default is NONE (was QFQ): TDX hq only serves raw unadjusted bars.
        super(CTdxStockApi, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def _warn_once(self, msg: str):
        if msg not in self.warnings:
            print(f"[WARNING] {msg}")
            self.warnings.append(msg)

    def SetBasciInfo(self):
        self.name = str(self.code)
        code6 = _code6_from_code(self.code) or ""
        self.is_stock = not _is_index_code(self.code, code6, self.asset_type)

    @classmethod
    def do_init(cls):
        pid = os.getpid()
        if cls.api is not None and cls.api_pid == pid:
            return
        # Double-checked locking: previously two threads could race past the
        # outer check and both connect, leaking a socket.
        with cls._init_lock:
            if cls.api is not None and cls.api_pid == pid:
                return
            try:
                from pytdx.hq import TdxHq_API
            except Exception as e:
                raise Exception(f"pytdx not available: {e}")

            # raise_exception=True: pytdx raises TdxFunctionCallError after
            # auto_retry instead of returning None — a None used to be
            # indistinguishable from "no more data" and silently truncated
            # history. multithread=True enables pytdx's own per-call lock so
            # even methods reached via __getattr__ stay serialized.
            api = TdxHq_API(raise_exception=True, auto_retry=True, multithread=True)
            ok = False
            for host, port in _tdx_servers():
                try:
                    if api.connect(host, port):
                        ok = True
                        break
                except Exception:
                    continue
            if not ok:
                try:
                    api.disconnect()
                except Exception:
                    pass
                raise Exception("tdx connect failed")

            cls.api = _LockedTdxHqClient(api, cls._api_lock)
            cls.api_pid = pid

    @classmethod
    def do_close(cls):
        with cls._init_lock:
            api = cls.api
            cls.api = None
            cls.api_pid = None
        if api is None:
            return
        try:
            api.disconnect()
        except Exception:
            pass

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        category = _category_from_kl_type(self.k_type)
        if category is None:
            # Previously returned [] silently for unsupported levels
            # (e.g. K_3M/K_QUARTER) — same "silent degrade" class as AkShare.
            raise CChanException(
                f"TDX does not support k_type={self.k_type}",
                ErrCode.SRC_DATA_TYPE_ERR,
            )

        code6 = _code6_from_code(self.code)
        if code6 is None:
            raise CChanException(
                f"TDX cannot parse code: {self.code!r} (expect 6 digits, optionally sh./sz./bj.-prefixed)",
                ErrCode.PARA_ERROR,
            )

        market = _tdx_market_from_code(self.code)
        if market is None:
            # Bare 6-digit code heuristic. BJ-style codes (43x/83x/87x/88x/920)
            # route to market=2; SZ/SH have no such segments.
            if code6.startswith("92") or code6[:1] in ("4", "8"):
                market = _TDX_MARKET_BJ
            elif code6.startswith("6") or code6.startswith("9"):
                market = 1
            else:
                market = 0
        if market == _TDX_MARKET_BJ:
            self._warn_once(
                f"TDX BJ market=2 for {self.code} is a de-facto convention not defined in "
                "installed pytdx TDXParams; whether a server serves BJ bars varies."
            )

        is_index = _is_index_code(self.code, code6, self.asset_type)

        # TDX hq get_security_bars/get_index_bars have no fq parameter — bars are
        # always raw/unadjusted, so autype used to be silently ignored. Client-side
        # adjustment via get_xdxr_info factors is out of scope and unverifiable
        # offline; explicitly refuse instead of returning wrong-semantics data.
        # (backend/chan_service already calls TDX with autype=NONE and adjusts the
        # tail via ClickHouse factors externally.) Index bars have no corporate
        # actions, so autype is meaningless for them and not rejected.
        if not is_index:
            au_name = str(getattr(self.autype, "name", self.autype) or "").strip().upper()
            if au_name and au_name != "NONE":
                raise CChanException(
                    f"TDX data source does not support autype={au_name} "
                    f"(returns raw unadjusted bars only); use autype=NONE for code {self.code}",
                    ErrCode.PARA_ERROR,
                )

        beg_d = _parse_yyyymmdd_or_iso_to_date(self.begin_date) if self.begin_date else None
        end_d = _parse_yyyymmdd_or_iso_to_date(self.end_date) if self.end_date else None

        if self.__class__.api is None or self.__class__.api_pid != os.getpid():
            self.__class__.do_init()
        api = self.__class__.api
        if api is None:
            return

        page_size = 800
        max_pages = 400
        offset = 0

        items = []
        for _ in range(max_pages):
            try:
                if is_index:
                    bars = api.get_index_bars(category, market, code6, offset, page_size)
                else:
                    bars = api.get_security_bars(category, market, code6, offset, page_size)
            except Exception as e:
                # raise_exception=True makes pytdx raise after auto_retry instead of
                # returning None. Convert to CChanException so mid-iteration failures
                # are explicit (and level-skippable via SRC_DATA_NOT_FOUND) rather
                # than silent truncation of the history.
                msg = (f"TDX fetch failed for {self.code} "
                       f"(market={market}, category={category}, offset={offset}): "
                       f"{type(e).__name__}: {e}")
                print(f"[WARNING] {msg}")
                self.warnings.append(msg)
                raise CChanException(msg, ErrCode.SRC_DATA_NOT_FOUND) from e
            if not bars:
                break
            offset += page_size

            oldest_dt_in_page = None
            for b in bars:
                if not b:
                    continue
                try:
                    y = int(b.get("year", 0))
                    m = int(b.get("month", 0))
                    d = int(b.get("day", 0))
                    hh = int(b.get("hour", 0))
                    mm = int(b.get("minute", 0))
                    dt = datetime.datetime(y, m, d, hh, mm)
                except Exception:
                    dt = _parse_bar_datetime(b.get("datetime"))
                    if dt is None:
                        continue

                if oldest_dt_in_page is None or dt < oldest_dt_in_page:
                    oldest_dt_in_page = dt

                dd = dt.date()
                if beg_d is not None and dd < beg_d:
                    continue
                if end_d is not None and dd > end_d:
                    continue

                items.append((dt, b))

            if beg_d is not None and oldest_dt_in_page is not None and oldest_dt_in_page.date() < beg_d:
                break
        else:
            # for-else: all max_pages pages consumed without an early break —
            # history likely truncated (~400*800 bars ≈ 5.5y of 1-minute bars).
            self._warn_once(
                f"TDX pagination reached max_pages={max_pages} for {self.code} "
                f"(category={category}); result may be truncated."
            )

        if not items:
            return

        items.sort(key=lambda x: x[0])
        seen = set()
        for dt, b in items:
            key = dt.strftime("%Y-%m-%d %H:%M:%S")
            if key in seen:
                continue
            seen.add(key)

            c = _bar_float(b, "close")
            if c <= 0.0:
                continue

            o = _bar_float(b, "open")
            h = _bar_float(b, "high")
            l = _bar_float(b, "low")
            if o <= 0.0:
                o = c
            if h <= 0.0:
                h = c
            if l <= 0.0:
                l = c
            h = max(h, o, c)
            l = min(l, o, c)

            vol = _bar_float(b, "vol")
            if not is_index:
                vol *= 100.0
            amt = _bar_float(b, "amount")

            is_minute = bool(kltype_lt_day(self.k_type))
            if is_minute:
                t = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute)
            else:
                t = CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)

            item = {
                DATA_FIELD.FIELD_TIME: t,
                DATA_FIELD.FIELD_OPEN: float(o),
                DATA_FIELD.FIELD_HIGH: float(h),
                DATA_FIELD.FIELD_LOW: float(l),
                DATA_FIELD.FIELD_CLOSE: float(c),
                DATA_FIELD.FIELD_VOLUME: float(vol),
                DATA_FIELD.FIELD_TURNOVER: float(amt),
            }
            yield CKLine_Unit(item)
