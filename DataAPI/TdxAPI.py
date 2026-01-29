import datetime
import os
from typing import Iterable, Optional

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.CTime import CTime
from Common.func_util import kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi


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


def _tdx_market_from_code(code: str) -> Optional[int]:
    c = str(code or "").strip().lower()
    if c.startswith("sh.") or c.startswith("sh"):
        return 1
    if c.startswith("sz.") or c.startswith("sz"):
        return 0
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


class CTdxStockApi(CCommonStockApi):
    api = None
    api_pid = None

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ, asset_type="STOCK"):
        super(CTdxStockApi, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def SetBasciInfo(self):
        self.name = str(self.code)
        code6 = _code6_from_code(self.code) or ""
        self.is_stock = not _is_index_code(self.code, code6, self.asset_type)

    @classmethod
    def do_init(cls):
        pid = os.getpid()
        if cls.api is not None and cls.api_pid == pid:
            return
        try:
            from pytdx.hq import TdxHq_API
        except Exception as e:
            raise Exception(f"pytdx not available: {e}")

        servers = [
            ("183.60.224.178", 7709),
            ("183.60.224.178", 7721),
            ("101.227.73.20", 7709),
            ("119.147.212.81", 7709),
        ]

        api = TdxHq_API(raise_exception=False, auto_retry=True)
        ok = False
        for host, port in servers:
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

        cls.api = api
        cls.api_pid = pid

    @classmethod
    def do_close(cls):
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
            return []

        code6 = _code6_from_code(self.code)
        if code6 is None:
            return []

        market = _tdx_market_from_code(self.code)
        if market is None:
            if code6.startswith("6") or code6.startswith("9"):
                market = 1
            else:
                market = 0

        is_index = _is_index_code(self.code, code6, self.asset_type)
        beg_d = _parse_yyyymmdd_or_iso_to_date(self.begin_date) if self.begin_date else None
        end_d = _parse_yyyymmdd_or_iso_to_date(self.end_date) if self.end_date else None

        if self.__class__.api is None or self.__class__.api_pid != os.getpid():
            self.__class__.do_init()
        api = self.__class__.api
        if api is None:
            return []

        page_size = 800
        max_pages = 400
        offset = 0

        items = []
        for _ in range(max_pages):
            if is_index:
                bars = api.get_index_bars(category, market, code6, offset, page_size) or []
            else:
                bars = api.get_security_bars(category, market, code6, offset, page_size) or []
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
                    dt_str = str(b.get("datetime") or "").strip()
                    if not dt_str:
                        continue
                    try:
                        dt = datetime.datetime.strptime(dt_str + ":00", "%Y-%m-%d %H:%M:%S")
                    except Exception:
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

        if not items:
            return []

        items.sort(key=lambda x: x[0])
        seen = set()
        for dt, b in items:
            key = dt.strftime("%Y-%m-%d %H:%M:%S")
            if key in seen:
                continue
            seen.add(key)

            try:
                c = float(b.get("close", 0.0) or 0.0)
            except Exception:
                c = 0.0
            if c <= 0.0:
                continue

            def _f(name):
                try:
                    return float(b.get(name, 0.0) or 0.0)
                except Exception:
                    return 0.0

            o = _f("open")
            h = _f("high")
            l = _f("low")
            if o <= 0.0:
                o = c
            if h <= 0.0:
                h = c
            if l <= 0.0:
                l = c
            h = max(h, o, c)
            l = min(l, o, c)

            vol = _f("vol")
            if not is_index:
                vol *= 100.0
            amt = _f("amount")

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
