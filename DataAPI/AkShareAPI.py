import akshare as ak
import pandas as pd
import datetime
import requests
from typing import Optional
from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from Common.func_util import str2float, kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

# SH funds/ETF/LOF: 50x,51x,52x,56x,58x; SZ funds/ETF/LOF: 15x,16x,18x.
# (Previously only 51/52/56/58/15 — missed 50xxxx SH funds and 16xxxx SZ LOFs.)
_ETF_PREFIXES = ('15', '16', '18', '50', '51', '52', '56', '58')

# eastmoney push2 single-symbol quote timeout (seconds)
_REALTIME_QUOTE_TIMEOUT = 8

# timeout passed to akshare endpoints that accept one (stock_zh_a_hist)
_AK_HTTP_TIMEOUT = 30


def _is_etf_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().startswith(_ETF_PREFIXES)


def _to_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if pd.isna(v):
            return None
        return float(v)
    s = str(v)
    if not s or s == "nan" or s == "None":
        return None
    try:
        f = float(s)
        if pd.isna(f):
            return None
        return float(f)
    except Exception:
        return None


def create_item_dict(data, column_name):
    for i in range(len(data)):
        data[i] = parse_time_column(data[i]) if column_name[i] == DATA_FIELD.FIELD_TIME else str2float(data[i])
    return dict(zip(column_name, data))

def parse_time_column(inp):
    # 2021-09-13 10:00:00
    if len(inp) == 19:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        hour = int(inp[11:13])
        minute = int(inp[14:16])
        return CTime(year, month, day, hour, minute)
    # 2021-09-13
    elif len(inp) == 10:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        return CTime(year, month, day, 0, 0, auto=False)
    else:
        raise Exception(f"unknown time column from akshare:{inp}")

class CAkShare(CCommonStockApi):
    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ, asset_type="STOCK"):
        super(CAkShare, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def _is_index(self, symbol: str) -> bool:
        # Index detection aligned with TdxAPI._is_index_code:
        # SH indices: sh.000xxx / sh.880xxx / sh.999999; SZ indices: sz.399xxx.
        # Bare codes keep the legacy 399xxx-only heuristic (sh/sz/bj prefix decides market first).
        c = str(self.code or "").strip().lower()
        s6 = str(symbol or "").strip()
        heuristic = (
            (c.startswith("sh") and (s6.startswith("000") or s6.startswith("880") or s6 == "999999"))
            or (c.startswith("sz") and s6.startswith("399"))
            or (not c.startswith(("sh", "sz", "bj")) and s6.startswith("399"))
        )
        if str(self.asset_type or "").strip().upper() == "INDEX":
            if not heuristic:
                msg = f"asset_type=INDEX but code '{self.code}' does not match known index patterns; routing to index API anyway"
                if msg not in self.warnings:
                    print(f"[WARNING] {msg}")
                    self.warnings.append(msg)
            return True
        return heuristic

    def get_kl_data(self):
        # AkShare uses 6-digit code. Need to strip sh/sz prefix if present
        symbol = self.code.split('.')[-1]
        is_index = self._is_index(symbol)
        is_etf = _is_etf_symbol(symbol)
        
        period = self.__convert_type()
        adjust = "qfq" if self.autype == AUTYPE.QFQ else "hfq" if self.autype == AUTYPE.HFQ else ""
        
        start_date = self.begin_date.replace("-", "") if self.begin_date else "19900101"
        end_date = self.end_date.replace("-", "") if self.end_date else "20500101"
        start_dt = str(self.begin_date or "").strip()
        end_dt = str(self.end_date or "").strip()
        if start_dt and len(start_dt) == 10:
            start_dt = f"{start_dt} 09:30:00"
        if end_dt and len(end_dt) == 10:
            end_dt = f"{end_dt} 15:00:00"
        
        try:
            if kltype_lt_day(self.k_type):
                # Minute data
                # period: '1', '5', '15', '30', '60'
                if is_index:
                    if not hasattr(ak, "index_zh_a_hist_min_em"):
                        raise CChanException(
                            f"akshare lacks index_zh_a_hist_min_em; cannot fetch minute index data for {self.code}",
                            ErrCode.SRC_DATA_TYPE_ERR,
                        )
                    df = ak.index_zh_a_hist_min_em(
                        symbol=symbol,
                        period=period,
                        start_date=start_dt or f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]} 09:30:00",
                        end_date=end_dt or f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]} 15:00:00",
                    )
                    # index_zh_a_hist_min_em has no adjust param (index bars are never adjusted)
                elif is_etf:
                    # start/end were previously omitted → akshare pulled FULL minute history
                    # (defaults '1979-09-01'..'2222-01-01') with no local date filtering.
                    df = ak.fund_etf_hist_min_em(
                        symbol=symbol,
                        start_date=start_dt or f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]} 09:30:00",
                        end_date=end_dt or f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]} 15:00:00",
                        period=period,
                        adjust=adjust,
                    )
                else:
                    df = ak.stock_zh_a_hist_min_em(
                        symbol=symbol,
                        start_date=start_dt or f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]} 09:30:00",
                        end_date=end_dt or f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]} 15:00:00",
                        period=period,
                        adjust=adjust,
                    )
                
                # Columns: 时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额, ...
                # Standardize to: time, open, close, high, low, volume, amount
                rename_map = {
                    "时间": DATA_FIELD.FIELD_TIME,
                    "开盘": DATA_FIELD.FIELD_OPEN,
                    "收盘": DATA_FIELD.FIELD_CLOSE,
                    "最高": DATA_FIELD.FIELD_HIGH,
                    "最低": DATA_FIELD.FIELD_LOW,
                    "成交量": DATA_FIELD.FIELD_VOLUME,
                    "成交额": DATA_FIELD.FIELD_TURNOVER,
                }
            else:
                # Daily data
                # period: 'daily', 'weekly', 'monthly'
                p_map = {'d': 'daily', 'w': 'weekly', 'm': 'monthly'}
                p = p_map.get(period, 'daily')
                
                if is_index:
                    # index_kline has no fq adjustment; index_zh_a_hist resolves sh/sz market internally
                    df = ak.index_zh_a_hist(symbol=symbol, period=p, start_date=start_date, end_date=end_date)
                elif is_etf:
                    df = ak.fund_etf_hist_em(symbol=symbol, period=p, start_date=start_date, end_date=end_date, adjust=adjust)
                else:
                    # stock_zh_a_hist is the only akshare endpoint used here that accepts
                    # `timeout`; the others (fund_etf_hist_em/index_zh_a_hist/*_min_em)
                    # have no timeout parameter in akshare 1.17.98.
                    df = ak.stock_zh_a_hist(
                        symbol=symbol, period=p, start_date=start_date, end_date=end_date,
                        adjust=adjust, timeout=_AK_HTTP_TIMEOUT,
                    )
                
                rename_map = {
                    "日期": DATA_FIELD.FIELD_TIME,
                    "开盘": DATA_FIELD.FIELD_OPEN,
                    "收盘": DATA_FIELD.FIELD_CLOSE,
                    "最高": DATA_FIELD.FIELD_HIGH,
                    "最低": DATA_FIELD.FIELD_LOW,
                    "成交量": DATA_FIELD.FIELD_VOLUME,
                    "成交额": DATA_FIELD.FIELD_TURNOVER,
                    "换手率": DATA_FIELD.FIELD_TURNRATE,
                }
            
            df = df.rename(columns=rename_map)
            
            # Check for data truncation (common issue with AkShare minute data)
            if not df.empty and self.begin_date:
                try:
                    first_date_val = df.iloc[0][DATA_FIELD.FIELD_TIME]
                    first_date_str = str(first_date_val)
                    if len(first_date_str) >= 10:
                        req_start = datetime.datetime.strptime(self.begin_date, "%Y-%m-%d")
                        actual_start = datetime.datetime.strptime(first_date_str[:10], "%Y-%m-%d")
                        
                        if (actual_start - req_start).days > 5:
                            msg = f"AkShare data truncated for {self.code}. Requested from {self.begin_date}, got from {first_date_str[:10]}. This is likely an API limitation. Consider using BaoStock for longer history."
                            print(f"[WARNING] {msg}")
                            self.warnings.append(msg)
                except Exception as e:
                    # Ignore date parsing errors
                    pass
            
            columns = [
                DATA_FIELD.FIELD_TIME,
                DATA_FIELD.FIELD_OPEN,
                DATA_FIELD.FIELD_HIGH,
                DATA_FIELD.FIELD_LOW,
                DATA_FIELD.FIELD_CLOSE,
                DATA_FIELD.FIELD_VOLUME,
                DATA_FIELD.FIELD_TURNOVER
            ]
            if not kltype_lt_day(self.k_type):
                columns.append(DATA_FIELD.FIELD_TURNRATE)
                
            last_date = None
            for _, row in df.iterrows():
                time_raw = row.get(DATA_FIELD.FIELD_TIME)
                time_str = str(time_raw)

                try:
                    if len(time_str) >= 10:
                        current_dt = datetime.datetime.strptime(time_str[:10], "%Y-%m-%d")
                        if last_date is None or current_dt > last_date:
                            last_date = current_dt
                except Exception:
                    pass

                try:
                    t = parse_time_column(time_str[:19] if len(time_str) >= 19 else time_str[:10])
                except Exception:
                    continue

                o = _to_float(row.get(DATA_FIELD.FIELD_OPEN))
                h = _to_float(row.get(DATA_FIELD.FIELD_HIGH))
                l = _to_float(row.get(DATA_FIELD.FIELD_LOW))
                c = _to_float(row.get(DATA_FIELD.FIELD_CLOSE))
                if c is None or c <= 0:
                    continue
                if o is None or o <= 0:
                    o = c
                if h is None or h <= 0:
                    h = c
                if l is None or l <= 0:
                    l = c
                h = max(h, o, c)
                l = min(l, o, c)

                vol = _to_float(row.get(DATA_FIELD.FIELD_VOLUME)) or 0.0
                amt = _to_float(row.get(DATA_FIELD.FIELD_TURNOVER)) or 0.0

                item = {
                    DATA_FIELD.FIELD_TIME: t,
                    DATA_FIELD.FIELD_OPEN: float(o),
                    DATA_FIELD.FIELD_HIGH: float(h),
                    DATA_FIELD.FIELD_LOW: float(l),
                    DATA_FIELD.FIELD_CLOSE: float(c),
                    DATA_FIELD.FIELD_VOLUME: float(vol),
                    DATA_FIELD.FIELD_TURNOVER: float(amt),
                }
                if not kltype_lt_day(self.k_type):
                    tr = _to_float(row.get(DATA_FIELD.FIELD_TURNRATE))
                    if tr is not None:
                        item[DATA_FIELD.FIELD_TURNRATE] = float(tr)

                yield CKLine_Unit(item)
                
            # Check for Real-time tick (Call Auction or Trading Session or missing today)
            now = datetime.datetime.now()
            should_fetch_tick = False
            
            if now.weekday() < 5:
                if last_date is None:
                    should_fetch_tick = True
                else:
                     # Check if last_date is today
                    if last_date.date() < now.date():
                        should_fetch_tick = True
            
            if should_fetch_tick and self.k_type == KL_TYPE.K_DAY:
                try:
                    # Single-symbol quote via eastmoney push2. Replaces
                    # ak.stock_zh_a_spot_em(), which (a) pulls a ~5k-row full-market
                    # snapshot just to patch one daily bar, (b) exposes no timeout in
                    # akshare 1.17.98, and (c) silently maps e.g. sh.000001 to
                    # 平安银行 (spot_df['代码']=='000001' is a stock, not the index).
                    quote = self._fetch_realtime_quote(symbol)
                    if quote is not None:
                        current_price = quote["close"]

                        if current_price and str(current_price) != 'nan':
                            tick_time = now.strftime("%Y-%m-%d %H:%M:%S")

                            # Double check time to avoid dup
                            tick_dt = datetime.datetime.strptime(tick_time, "%Y-%m-%d %H:%M:%S")

                            if last_date is None or tick_dt.date() > last_date.date():
                                data = [
                                    tick_time,
                                    str(quote["open"]),    # Open
                                    str(quote["high"]),    # High
                                    str(quote["low"]),     # Low
                                    str(current_price),    # Close
                                    str(quote["volume"]),  # Volume
                                    str(quote["amount"]),  # Turnover
                                ]

                                # If values are nan/None, use current_price
                                for i in range(1, 5):
                                    if data[i] == 'nan' or data[i] == 'None':
                                        data[i] = str(current_price)

                                yield CKLine_Unit(create_item_dict(data, columns))

                except Exception as ex:
                    print(f"AkShare Real-time Tick Error: {ex}")

        except Exception:
            raise

    def _fetch_realtime_quote(self, symbol: str) -> Optional[dict]:
        """Single-symbol realtime quote from eastmoney push2 `qt/stock/get`.

        Returns dict(open/high/low/close/volume/amount) or None when the endpoint
        resolves a different symbol (f57 echo check) — refuses silently-wrong data.
        Index quotes are served under the same secid scheme (sh.000001 -> 1.000001).
        """
        c = str(self.code or "").strip().lower()
        if c.startswith("sh"):
            market_id = 1
        elif c.startswith(("sz", "bj")):
            # eastmoney push2 serves SZ and (best-effort) BJ quotes under market 0
            market_id = 0
        else:
            market_id = 1 if symbol[:1] in ("5", "6", "9") else 0
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f43,f44,f45,f46,f47,f48,f57",
            "secid": f"{market_id}.{symbol}",
        }
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params=params,
            timeout=_REALTIME_QUOTE_TIMEOUT,
        )
        payload = (r.json() or {}).get("data") or {}
        if str(payload.get("f57") or "") != symbol:
            return None
        return {
            "open": payload.get("f46"),
            "high": payload.get("f44"),
            "low": payload.get("f45"),
            "close": payload.get("f43"),
            "volume": payload.get("f47"),
            "amount": payload.get("f48"),
        }

    def SetBasciInfo(self):
        # AkShare has no dedicated basic-info call here; infer asset kind locally so
        # is_stock stays consistent with _is_index (and ETFs are not stocks either,
        # matching TDX/BaoStock semantics where only equities are is_stock=True).
        self.name = self.code
        symbol = self.code.split('.')[-1]
        self.is_stock = not (self._is_index(symbol) or _is_etf_symbol(symbol))

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass

    def __convert_type(self):
        _dict = {
            KL_TYPE.K_DAY: 'd',
            KL_TYPE.K_WEEK: 'w',
            KL_TYPE.K_MON: 'm',
            KL_TYPE.K_1M: '1',
            KL_TYPE.K_5M: '5',
            KL_TYPE.K_15M: '15',
            KL_TYPE.K_30M: '30',
            KL_TYPE.K_60M: '60',
        }
        period = _dict.get(self.k_type)
        if period is None:
            # Previously defaulted to 'd': e.g. K_QUARTER/K_3M silently degraded to
            # daily bars. Fail loudly instead (consistent with other sources).
            raise CChanException(
                f"AkShare does not support k_type={self.k_type}; "
                f"supported: {sorted(k.name for k in _dict)}",
                ErrCode.SRC_DATA_TYPE_ERR,
            )
        return period
