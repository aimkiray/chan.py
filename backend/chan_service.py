import sys
import os
import json
import datetime
import gc
import hashlib
import pickle
import gzip
import uuid
from typing import Dict, TypedDict, Optional
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo
import concurrent.futures
import xgboost as xgb
import lightgbm as lgb
import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
import baostock as bs

# Add parent directory to path to allow importing from root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Chan import CChan
from ChanConfig import CChanConfig
from ChanModel.Features import CFeatures
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE, BSP_TYPE
from Common.CTime import CTime
from BuySellPoint.BS_Point import CBS_Point
from Plot.PlotMeta import CChanPlotMeta

class T_SAMPLE_INFO(TypedDict):
    feature: CFeatures
    is_buy: bool
    open_time: CTime

def stragety_feature(last_klu, enable_rolling_lookback=True):
    # Improved feature engineering for better generalization
    # Avoid absolute price/volume values which lead to overfitting
    
    # Calculate simple moving averages (approximation)
    # Since we only have last_klu here, we rely on what's available or compute ratios
    # But ideally we should have access to history. 
    # For now, we add more normalized indicators.
    
    # Note: KLine Unit usually has MACD computed if CChan ran with it.
    
    feat = {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
        "high_low_rate": (last_klu.high - last_klu.low)/last_klu.close,
        # Normalized Close: Close relative to Open (intraday strength)
        "close_open_ratio": last_klu.close / last_klu.open,
        # Normalized High/Low position
        "close_pos_in_bar": (last_klu.close - last_klu.low) / (last_klu.high - last_klu.low + 1e-8),
    }

    if enable_rolling_lookback:
        # Add Long-Term Context (Rolling Lookback)
        # We traverse back 250 bars (approx 1 year) to find High/Low
        # This captures "Position in Range" which is critical for Zhuang Gu (e.g. low in 1 year range)
        try:
            high_250 = last_klu.high
            low_250 = last_klu.low
            curr = last_klu
            count = 0
            limit = 250
            while curr.pre and count < limit:
                curr = curr.pre
                if curr.high > high_250: high_250 = curr.high
                if curr.low < low_250: low_250 = curr.low
                count += 1
            
            if count > 20: # Only if we have enough history
                feat["price_rank_250"] = (last_klu.close - low_250) / (high_250 - low_250 + 1e-8)
                feat["volatility_250"] = (high_250 - low_250) / (low_250 + 1e-8)
        except Exception:
            pass
    
    if hasattr(last_klu, 'macd'):
        feat.update({
            "macd_dif": last_klu.macd.DIF,
            "macd_dea": last_klu.macd.DEA,
            "macd_bar": last_klu.macd.macd,
        })
        
    return feat

def build_chan_state_feature(kl_data, last_klu, last_bsp: Optional[CBS_Point] = None):
    feat = {}
    if kl_data is None or last_klu is None:
        return feat

    try:
        feat["chan_bi_cnt"] = float(len(getattr(kl_data, "bi_list", []) or []))
        feat["chan_seg_cnt"] = float(len(getattr(kl_data, "seg_list", []) or []))
        feat["chan_zs_cnt"] = float(len(getattr(kl_data, "zs_list", []) or []))
    except Exception:
        pass

    last_bi = None
    try:
        if getattr(kl_data, "bi_list", None) is not None and len(kl_data.bi_list) > 0:
            last_bi = kl_data.bi_list[-1]
    except Exception:
        last_bi = None

    if last_bi is not None:
        try:
            feat["chan_last_bi_is_up"] = 1.0 if bool(last_bi.is_up()) else 0.0
        except Exception:
            pass
        try:
            feat["chan_last_bi_is_sure"] = 1.0 if bool(getattr(last_bi, "is_sure", False)) else 0.0
        except Exception:
            pass
        try:
            bv = float(last_bi.get_begin_val() or 0.0)
            amp = float(last_bi.amp() or 0.0)
            feat["chan_last_bi_amp"] = amp
            feat["chan_last_bi_amp_rate"] = (amp / bv) if bv else 0.0
        except Exception:
            pass
        try:
            feat["chan_last_bi_klu_cnt"] = float(last_bi.get_klu_cnt() or 0.0)
        except Exception:
            pass
        try:
            end_idx = int(getattr(last_bi.get_end_klu(), "idx", 0) or 0)
            feat["chan_last_bi_age"] = float(int(getattr(last_klu, "idx", 0) or 0) - end_idx)
        except Exception:
            pass

    last_seg = None
    try:
        if getattr(kl_data, "seg_list", None) is not None and len(kl_data.seg_list) > 0:
            last_seg = kl_data.seg_list[-1]
    except Exception:
        last_seg = None

    if last_seg is not None:
        try:
            feat["chan_last_seg_is_down"] = 1.0 if bool(last_seg.is_down()) else 0.0
        except Exception:
            pass
        try:
            feat["chan_last_seg_is_sure"] = 1.0 if bool(getattr(last_seg, "is_sure", False)) else 0.0
        except Exception:
            pass
        try:
            bv = float(last_seg.get_begin_val() or 0.0)
            amp = float(last_seg.amp() or 0.0)
            feat["chan_last_seg_amp"] = amp
            feat["chan_last_seg_amp_rate"] = (amp / bv) if bv else 0.0
        except Exception:
            pass
        try:
            feat["chan_last_seg_bi_cnt"] = float(last_seg.cal_bi_cnt() or 0.0)
        except Exception:
            pass
        try:
            end_idx = int(getattr(last_seg.get_end_klu(), "idx", 0) or 0)
            feat["chan_last_seg_age"] = float(int(getattr(last_klu, "idx", 0) or 0) - end_idx)
        except Exception:
            pass

    last_zs = None
    try:
        if getattr(kl_data, "zs_list", None) is not None and len(kl_data.zs_list) > 0:
            last_zs = kl_data.zs_list[-1]
    except Exception:
        last_zs = None

    if last_zs is not None:
        try:
            low = float(getattr(last_zs, "low", 0.0) or 0.0)
            high = float(getattr(last_zs, "high", 0.0) or 0.0)
            mid = float(getattr(last_zs, "mid", 0.0) or 0.0)
            feat["chan_last_zs_height"] = float(max(0.0, high - low))
            feat["chan_last_zs_height_rate"] = ((high - low) / mid) if mid else 0.0
            close = float(getattr(last_klu, "close", 0.0) or 0.0)
            feat["chan_in_last_zs"] = 1.0 if (low and high and low <= close <= high) else 0.0
        except Exception:
            pass
        try:
            end_klu_idx = int(getattr(getattr(last_zs, "end", None), "idx", 0) or 0)
            feat["chan_last_zs_age"] = float(int(getattr(last_klu, "idx", 0) or 0) - end_klu_idx)
        except Exception:
            pass
        try:
            begin_bi_idx = int(getattr(getattr(last_zs, "begin_bi", None), "idx", 0) or 0)
            end_bi_idx = int(getattr(getattr(last_zs, "end_bi", None), "idx", 0) or 0)
            if end_bi_idx >= begin_bi_idx:
                feat["chan_last_zs_bi_span"] = float(end_bi_idx - begin_bi_idx + 1)
        except Exception:
            pass

    if last_bsp is not None:
        try:
            feat["chan_last_bsp_is_buy"] = 1.0 if bool(getattr(last_bsp, "is_buy", False)) else 0.0
        except Exception:
            pass
        try:
            feat["chan_last_bsp_age"] = float(int(getattr(last_klu, "idx", 0) or 0) - int(getattr(getattr(last_bsp, "klu", None), "idx", 0) or 0))
        except Exception:
            pass
        try:
            tp = set(getattr(last_bsp, "type", []) or [])
            feat["chan_last_bsp_t1"] = 1.0 if BSP_TYPE.T1 in tp else 0.0
            feat["chan_last_bsp_t1p"] = 1.0 if BSP_TYPE.T1P in tp else 0.0
            feat["chan_last_bsp_t2"] = 1.0 if BSP_TYPE.T2 in tp else 0.0
            feat["chan_last_bsp_t2s"] = 1.0 if BSP_TYPE.T2S in tp else 0.0
            feat["chan_last_bsp_t3a"] = 1.0 if BSP_TYPE.T3A in tp else 0.0
            feat["chan_last_bsp_t3b"] = 1.0 if BSP_TYPE.T3B in tp else 0.0
        except Exception:
            pass

    return feat

def normalize_code(code):
    code = code.strip().lower()
    if code.endswith(".sh"):
        return f"sh.{code[:-3]}"
    if code.endswith(".sz"):
        return f"sz.{code[:-3]}"
    if code.endswith(".bj"):
        return f"bj.{code[:-3]}"
    
    if code.startswith("sh.") or code.startswith("sz.") or code.startswith("bj."):
        return code
    if code.startswith("sh") and len(code) == 8 and code[2:].isdigit():
        return f"sh.{code[2:]}"
    if code.startswith("sz") and len(code) == 8 and code[2:].isdigit():
        return f"sz.{code[2:]}"
    if code.startswith("bj") and len(code) == 8 and code[2:].isdigit():
        return f"bj.{code[2:]}"
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith("0") or code.startswith("3"):
        return f"sz.{code}"
    if code.startswith("5"):
        return f"sh.{code}"
    if code.startswith("1"):
        return f"sz.{code}"
    return code

def _is_cn_stock_market_open(now: Optional[datetime.datetime] = None) -> bool:
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = None

    if now is None:
        now = datetime.datetime.now(tz) if tz else datetime.datetime.now()
    elif tz and now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    elif tz:
        now = now.astimezone(tz)

    if now.weekday() >= 5:
        return False

    hhmm = now.hour * 60 + now.minute
    open_am = 9 * 60 + 30
    close_am = 11 * 60 + 30
    open_pm = 13 * 60
    close_pm = 15 * 60
    return (open_am <= hhmm < close_am) or (open_pm <= hhmm < close_pm)

def _eastmoney_secid(code: str) -> str:
    code = normalize_code(code)
    if code.startswith("sh."):
        return f"1.{code.split('.', 1)[1]}"
    if code.startswith("sz."):
        return f"0.{code.split('.', 1)[1]}"
    if code.startswith("bj."):
        return f"0.{code.split('.', 1)[1]}"
    digits = "".join([ch for ch in code if ch.isdigit()])
    if digits.startswith("6"):
        return f"1.{digits}"
    return f"0.{digits}"

def _eastmoney_klt(level: KL_TYPE) -> Optional[int]:
    mp = {
        KL_TYPE.K_1M: 1,
        KL_TYPE.K_5M: 5,
        KL_TYPE.K_15M: 15,
        KL_TYPE.K_30M: 30,
        KL_TYPE.K_60M: 60,
        KL_TYPE.K_DAY: 101,
        KL_TYPE.K_WEEK: 102,
        KL_TYPE.K_MON: 103,
    }
    return mp.get(level)

def _parse_eastmoney_time(s: str) -> Optional[CTime]:
    raw = str(s or "").strip()
    if not raw:
        return None
    try:
        if " " in raw:
            try:
                dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M")
            except Exception:
                dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d")
        return CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)
    except Exception:
        return None

def _eastmoney_tail_limit(level: KL_TYPE) -> int:
    if level == KL_TYPE.K_1M:
        return 320
    if level == KL_TYPE.K_5M:
        return 120
    if level == KL_TYPE.K_15M:
        return 80
    if level == KL_TYPE.K_30M:
        return 60
    if level == KL_TYPE.K_60M:
        return 40
    if level in (KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON):
        return 16
    return 16

def _fetch_latest_klines_eastmoney(
    code: str,
    level: KL_TYPE,
    limit: int = 5,
    autype: AUTYPE = AUTYPE.QFQ,
    beg: Optional[str] = None,
    end: str = "20500000",
):
    from Common.CEnum import DATA_FIELD
    from KLine.KLine_Unit import CKLine_Unit

    klt = _eastmoney_klt(level)
    if klt is None:
        return []

    secid = _eastmoney_secid(code)
    fqt_map = {AUTYPE.NONE: 0, AUTYPE.QFQ: 1, AUTYPE.HFQ: 2}
    fqt = fqt_map.get(autype, 1)
    fields1 = "f1,f2,f3,f4,f5,f6,f7,f8"
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    beg_val = str(beg or "0").strip() or "0"
    lmt_val = min(100000, max(1, int(limit)))
    params = {
        "fields1": fields1,
        "fields2": fields2,
        "klt": str(klt),
        "fqt": str(fqt),
        "secid": secid,
        "beg": beg_val,
        "end": str(end or "20500000"),
        "lmt": str(lmt_val),
        "iscca": "1",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": "https://quote.eastmoney.com/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        payload = json.loads(raw) if raw else {}
    except Exception:
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(klines, list) or not klines:
        return []

    out = []
    for row in klines:
        if not isinstance(row, str):
            continue
        parts = row.split(",")
        if len(parts) < 6:
            continue
        t = _parse_eastmoney_time(parts[0])
        if t is None:
            continue

        def to_float(v):
            try:
                return float(v)
            except Exception:
                return None

        o = to_float(parts[1])
        c = to_float(parts[2])
        h = to_float(parts[3])
        l = to_float(parts[4])
        v = to_float(parts[5]) or 0.0
        if str(klt) in ("101", "102", "103", "1", "5", "15", "30", "60"):
            v *= 100.0
        amt = to_float(parts[6]) if len(parts) > 6 else 0.0
        amt = amt or 0.0

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

        out.append(
            CKLine_Unit(
                {
                    DATA_FIELD.FIELD_TIME: t,
                    DATA_FIELD.FIELD_OPEN: float(round(o, 2)),
                    DATA_FIELD.FIELD_HIGH: float(round(h, 2)),
                    DATA_FIELD.FIELD_LOW: float(round(l, 2)),
                    DATA_FIELD.FIELD_CLOSE: float(round(c, 2)),
                    DATA_FIELD.FIELD_VOLUME: float(v),
                    DATA_FIELD.FIELD_TURNOVER: float(amt),
                }
            )
        )

    if not out:
        return []

    out.sort(key=lambda x: x.time.ts)
    uniq = []
    last_ts = None
    for k in out:
        ts = k.time.ts
        if last_ts is not None and ts == last_ts:
            uniq[-1] = k
            continue
        uniq.append(k)
        last_ts = ts
    return uniq

def _fetch_recent_klines_eastmoney(code: str, level: KL_TYPE, days: int, autype: AUTYPE = AUTYPE.NONE):
    import datetime

    max_days = int(days or 0)
    if max_days <= 0:
        return []

    out = []
    seen_days = set()
    end_key = "20500101"
    last_end_key = None

    for _ in range(max_days * 2):
        if last_end_key == end_key:
            break
        last_end_key = end_key

        batch = _fetch_latest_klines_eastmoney(code, level, limit=6000, autype=autype, end=end_key)
        if not batch:
            break

        out.extend(batch)

        batch_dates = []
        for k in batch:
            t = k.time
            d = datetime.date(int(t.year), int(t.month), int(t.day))
            seen_days.add(d)
            batch_dates.append(d)

        if len(seen_days) >= max_days:
            break

        earliest = min(batch_dates) if batch_dates else None
        if earliest is None:
            break
        end_key = (earliest - datetime.timedelta(days=1)).strftime("%Y%m%d")

    if not out:
        return []

    out.sort(key=lambda x: x.time.ts)
    uniq = []
    last_ts = None
    for k in out:
        ts = k.time.ts
        if last_ts is not None and ts == last_ts:
            uniq[-1] = k
            continue
        uniq.append(k)
        last_ts = ts
    return uniq

def _fetch_recent_klines_akshare(code: str, level: KL_TYPE, days: int):
    import datetime

    max_days = int(days or 0)
    if max_days <= 0:
        return []

    try:
        from DataAPI.AkShareAPI import CAkShare
    except Exception:
        return []

    out = []
    seen_days = set()
    end_dt = datetime.date.today()

    for _ in range(8):
        begin_dt = end_dt - datetime.timedelta(days=30)
        api = CAkShare(code, k_type=level, begin_date=begin_dt.strftime("%Y-%m-%d"), end_date=end_dt.strftime("%Y-%m-%d"), autype=AUTYPE.NONE)
        try:
            batch = list(api.get_kl_data())
        except Exception:
            batch = []

        if not batch:
            break

        out.extend(batch)
        batch_dates = []
        for k in batch:
            t = k.time
            d = datetime.date(int(t.year), int(t.month), int(t.day))
            seen_days.add(d)
            batch_dates.append(d)

        if len(seen_days) >= max_days:
            break

        earliest = min(batch_dates) if batch_dates else None
        if earliest is None:
            break
        end_dt = earliest - datetime.timedelta(days=1)

    if not out:
        return []

    out.sort(key=lambda x: x.time.ts)
    uniq = []
    last_ts = None
    for k in out:
        ts = k.time.ts
        if last_ts is not None and ts == last_ts:
            uniq[-1] = k
            continue
        uniq.append(k)
        last_ts = ts
    return uniq

def _merge_klines_tail(base_list, tail_list):
    if not base_list:
        return list(tail_list or [])
    if not tail_list:
        return base_list

    for k in tail_list:
        ts = k.time.ts
        if base_list and ts > base_list[-1].time.ts:
            base_list.append(k)
            continue
        replaced = False
        for i in range(len(base_list) - 1, -1, -1):
            cur_ts = base_list[i].time.ts
            if cur_ts == ts:
                base_list[i] = k
                replaced = True
                break
            if cur_ts < ts:
                base_list.insert(i + 1, k)
                replaced = True
                break
        if not replaced:
            base_list.insert(0, k)

    dedup = []
    seen = set()
    for k in base_list:
        ts = k.time.ts
        if ts in seen:
            continue
        seen.add(ts)
        dedup.append(k)
    return dedup

def _append_klines_tail(base_list, tail_list):
    if not base_list:
        return list(tail_list or [])
    if not tail_list:
        return base_list
    last_ts = base_list[-1].time.ts if base_list else None
    if last_ts is None:
        return _merge_klines_tail(base_list, tail_list)
    for k in tail_list:
        if k.time.ts > last_ts:
            base_list.append(k)
    return base_list

def _adjust_tail_by_clickhouse_factors(code: str, tail_list, autype: AUTYPE):
    if not tail_list or autype == AUTYPE.NONE:
        return tail_list or []

    from bisect import bisect_right
    import datetime
    from DataAPI.ClickHouseAPI import CClickHouseAPI

    CClickHouseAPI.do_init()
    try:
        dates = []
        for k in tail_list:
            t = k.time
            dates.append(datetime.date(int(t.year), int(t.month), int(t.day)))
        if not dates:
            return tail_list

        begin_date = min(dates).strftime("%Y-%m-%d")
        end_date = max(dates).strftime("%Y-%m-%d")

        code = normalize_code(code)
        api = CClickHouseAPI(code=code, k_type=KL_TYPE.K_DAY, begin_date=begin_date, end_date=end_date, autype=autype)
        api._ensure_connection()

        normalized_code = str(code or "").lower().replace(".", "")
        factor_trading_dates = []
        factor_by_date = {}
        if autype == AUTYPE.QFQ:
            factor_trading_dates, factor_by_date = api._get_qfq_factors_by_date(
                normalized_code=normalized_code,
                begin_date=begin_date,
                end_date=end_date,
            )
        elif autype == AUTYPE.HFQ:
            factor_trading_dates, factor_by_date = api._get_hfq_factors_by_date(
                normalized_code=normalized_code,
                begin_date=begin_date,
                end_date=end_date,
            )

        if not factor_by_date:
            return tail_list

        for k in tail_list:
            t = k.time
            row_date = datetime.date(int(t.year), int(t.month), int(t.day))
            factor = factor_by_date.get(row_date)
            if factor is None and factor_trading_dates:
                idx = bisect_right(factor_trading_dates, row_date) - 1
                if idx >= 0:
                    factor = factor_by_date.get(factor_trading_dates[idx])
            if factor is None:
                continue
            o = float(k.open) * factor
            h = float(k.high) * factor
            l = float(k.low) * factor
            c = float(k.close) * factor
            hi = max(h, o, c)
            lo = min(l, o, c)
            k.open = o
            k.high = hi
            k.low = lo
            k.close = c

        return tail_list
    finally:
        CClickHouseAPI.do_close()

def get_stock_name(code, data_src_type=DATA_SRC.BAO_STOCK):
    """
    Get stock name from Data Source
    """
    code = normalize_code(code)
    try:
        if data_src_type == DATA_SRC.CLICK_HOUSE:
            from DataAPI.ClickHouseAPI import CClickHouseAPI
            # Use ClickHouse to get basic info
            # We use K_DAY as a placeholder since SetBasciInfo doesn't depend on k_type
            api = CClickHouseAPI(code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ)
            api.SetBasciInfo()
            if api.name:
                return api.name
            else:
                return code
                
        # Fallback to Baostock if not ClickHouse
        # Suppress login success message
        import io
        import sys
        
        # Check if already logged in (CBaoStock maintains state but we can't easily check bs internal state without calling login)
        # But bs.login() is idempotent.
        
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            bs.login()
        except:
            pass
        finally:
            sys.stdout = old_stdout
        
        rs = bs.query_stock_basic(code=code)
        if rs.error_code == '0' and rs.next():
            # code, code_name, ipoDate, outDate, type, status
            row = rs.get_row_data()
            return row[1] # code_name
    except Exception as e:
        print(f"Error fetching stock name: {e}")
    return code

from DataAPI.CommonStockAPI import CCommonStockApi

class PreloadedStockAPI(CCommonStockApi):
    def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None, data_list=None):
        self.data_list = data_list or []
        super().__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        yield from self.data_list
    
    @classmethod
    def do_init(cls):
        pass
    
    @classmethod
    def do_close(cls):
        pass

class CChanCustom(CChan):
    def __init__(self, code, begin_time=None, end_time=None, data_src=None, lv_list=None, config=None, autype=None, preloaded_data=None):
        self.preloaded_data = preloaded_data
        super().__init__(code, begin_time, end_time, data_src, lv_list, config, autype)
    
    def GetStockAPI(self):
        if self.preloaded_data is not None:
            # We need to return a class that accepts arguments but uses our preloaded data
            # Since CChan instantiates the class, we can't easily pass the instance.
            # But we can monkey-patch the __init__ of our custom class to use the preloaded data?
            # Or better, just return a factory class.
            
            # CChan calls: stockapi_cls(code=..., ...)
            # So we define a class that ignores args and uses self.preloaded_data
            
            data = self.preloaded_data
            class CustomAPI(PreloadedStockAPI):
                def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None):
                    super().__init__(code, k_type, begin_date, end_date, autype, data_list=data)
            
            return CustomAPI
            
        return super().GetStockAPI()

def _parse_time_bound_ts(raw: Optional[str], is_end: bool) -> Optional[int]:
    s = str(raw or "").strip()
    if not s:
        return None
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            if fmt in ("%Y-%m-%d", "%Y%m%d"):
                if is_end:
                    dt = dt.replace(hour=23, minute=59, second=59)
                else:
                    dt = dt.replace(hour=0, minute=0, second=0)
            ct = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
            return int(ct.ts)
        except Exception:
            continue
    try:
        dt = datetime.datetime.fromisoformat(s)
        if is_end:
            dt = dt.replace(second=59) if dt.second == 0 else dt
        ct = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
        return int(ct.ts)
    except Exception:
        return None

def fetch_stock_data(code, level, begin_time, end_time, data_src_type, autype=AUTYPE.QFQ, use_online_latest: bool = False):
    """
    Helper to fetch raw K-line data without calculating Chan elements.
    Useful for checking data freshness for caching.
    """
    code = normalize_code(code)
    
    # Create a temporary CChan instance just to resolve the StockAPI class
    # This is a bit hacky but avoids duplicating the StockAPI resolution logic
    # Set trigger_step=True to avoid loading data in __init__
    temp_chan = CChan(
        code=code,
        begin_time=begin_time,
        end_time=end_time,
        data_src=data_src_type,
        lv_list=[level],
        config=CChanConfig({"trigger_step": True}),
        autype=autype
    )
    
    StockAPI = temp_chan.GetStockAPI()
    StockAPI.do_init()
    try:
        stock_api = StockAPI(code=code, k_type=level, begin_date=begin_time, end_date=end_time, autype=autype)
        data_list = list(stock_api.get_kl_data())
        if (
            use_online_latest
            and data_src_type == DATA_SRC.CLICK_HOUSE
            and end_time is None
        ):
            if level == KL_TYPE.K_1M:
                try:
                    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
                except Exception:
                    now = datetime.datetime.now()
                today = now.date()
                beg = today.strftime("%Y%m%d")
                if data_list:
                    try:
                        t = data_list[-1].time
                        last_date = datetime.date(int(t.year), int(t.month), int(t.day))
                        if last_date >= today:
                            beg = last_date.strftime("%Y%m%d")
                    except Exception:
                        pass

                raw_tail = _fetch_latest_klines_eastmoney(
                    code,
                    level,
                    limit=100000,
                    autype=AUTYPE.NONE,
                    beg=beg,
                    end="20500000",
                )
                if raw_tail:
                    tail = _adjust_tail_by_clickhouse_factors(code=code, tail_list=raw_tail, autype=autype)
                    data_list = _merge_klines_tail(data_list, tail)
            else:
                base_limit = _eastmoney_tail_limit(level)
                cap_map = {
                    KL_TYPE.K_1M: 6000,
                    KL_TYPE.K_5M: 2400,
                    KL_TYPE.K_15M: 2000,
                    KL_TYPE.K_30M: 1200,
                    KL_TYPE.K_60M: 800,
                    KL_TYPE.K_DAY: 64,
                    KL_TYPE.K_WEEK: 128,
                    KL_TYPE.K_MON: 128,
                }
                cap = cap_map.get(level, max(512, base_limit))
                limit = base_limit
                last_ts = data_list[-1].time.ts if data_list else None
                raw_tail = None
                for _ in range(8):
                    raw_tail = _fetch_latest_klines_eastmoney(code, level, limit=limit, autype=AUTYPE.NONE)
                    if not raw_tail:
                        break
                    if last_ts is None:
                        break
                    first_ts = raw_tail[0].time.ts
                    if first_ts <= last_ts:
                        break
                    if limit >= cap:
                        break
                    limit = min(cap, int(limit * 2))

                if raw_tail:
                    tail = _adjust_tail_by_clickhouse_factors(code=code, tail_list=raw_tail, autype=autype)
                    data_list = _merge_klines_tail(data_list, tail)
        begin_ts = _parse_time_bound_ts(begin_time, is_end=False)
        end_ts = _parse_time_bound_ts(end_time, is_end=True)
        if begin_ts is not None or end_ts is not None:
            filtered = []
            for k in data_list:
                ts = getattr(getattr(k, "time", None), "ts", None)
                if ts is None:
                    continue
                if begin_ts is not None and int(ts) < int(begin_ts):
                    continue
                if end_ts is not None and int(ts) > int(end_ts):
                    continue
                filtered.append(k)
            data_list = filtered

        data_list.sort(key=lambda x: getattr(getattr(x, "time", None), "ts", 0))
        dedup = []
        last_ts = None
        for k in data_list:
            ts = getattr(getattr(k, "time", None), "ts", None)
            if ts is None:
                continue
            if last_ts is not None and ts == last_ts:
                dedup[-1] = k
                continue
            dedup.append(k)
            last_ts = ts
        return dedup
    finally:
        StockAPI.do_close()

def get_latest_data_time(code, level, data_src_type, autype: AUTYPE = AUTYPE.QFQ):
    """
    Optimized function to get ONLY the latest data time without fetching full history.
    """
    import datetime
    code = normalize_code(code)
    
    # Define a short lookback period
    if level == KL_TYPE.K_DAY:
        days = 30
    else:
        days = 60 # Increased from 5 to 60 to handle stale data better
        if data_src_type == DATA_SRC.CLICK_HOUSE:
            days = 365 # Even longer for ClickHouse which is fast
        
    begin_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    end_time = None
    
    # Reuse fetch_stock_data but with short range
    try:
        use_online_latest = bool(data_src_type == DATA_SRC.CLICK_HOUSE)
        data_list = fetch_stock_data(
            code,
            level,
            begin_time,
            end_time,
            data_src_type,
            autype=autype,
            use_online_latest=use_online_latest,
        )
        if data_list:
            return str(data_list[-1].time)
    except Exception as e:
        print(f"Error fetching latest time: {e}")
    return None


def extract_state_features_from_cur_lv(cur_lv_chan, last_klu):
    out = {}
    try:
        close = float(getattr(last_klu, "close", 0.0) or 0.0)
    except Exception:
        close = 0.0
    denom = close if abs(close) > 1e-12 else 1e-12

    bi_list = getattr(cur_lv_chan, "bi_list", None)
    try:
        bi_cnt = int(len(bi_list)) if bi_list is not None else 0
    except Exception:
        bi_cnt = 0
    out["bi_cnt"] = bi_cnt
    if bi_cnt >= 1:
        try:
            last_bi = bi_list[-1]
            last_bi_amp = float(last_bi.amp())
            out["bi_last_dir"] = 1 if bool(last_bi.is_up()) else (-1 if bool(last_bi.is_down()) else 0)
            out["bi_last_amp_r"] = float(last_bi_amp / denom)
            out["bi_last_klc_cnt"] = int(last_bi.get_klc_cnt())
            out["bi_last_klu_cnt"] = int(last_bi.get_klu_cnt())
            out["bi_last_is_sure"] = 1 if bool(getattr(last_bi, "is_sure", False)) else 0
        except Exception:
            pass
    if bi_cnt >= 2:
        try:
            prev_bi = bi_list[-2]
            prev_amp = float(prev_bi.amp())
            out["bi_prev_amp_r"] = float(prev_amp / denom)
            out["bi_last_over_prev_amp"] = float((out.get("bi_last_amp_r", 0.0) * denom) / (prev_amp + 1e-12))
            out["bi_dir_change"] = 1 if int(out.get("bi_last_dir", 0)) != (1 if bool(prev_bi.is_up()) else (-1 if bool(prev_bi.is_down()) else 0)) else 0
        except Exception:
            pass

    seg_list = getattr(cur_lv_chan, "seg_list", None)
    try:
        seg_cnt = int(len(seg_list)) if seg_list is not None else 0
    except Exception:
        seg_cnt = 0
    out["seg_cnt"] = seg_cnt
    if seg_cnt >= 1:
        try:
            last_seg = seg_list[-1]
            seg_amp = float(last_seg.amp())
            out["seg_last_dir"] = 1 if bool(last_seg.is_up()) else (-1 if bool(last_seg.is_down()) else 0)
            out["seg_last_amp_r"] = float(seg_amp / denom)
            out["seg_last_klu_cnt"] = int(last_seg.get_klu_cnt())
            out["seg_last_is_sure"] = 1 if bool(getattr(last_seg, "is_sure", False)) else 0
        except Exception:
            pass

    zs_list = getattr(cur_lv_chan, "zs_list", None)
    try:
        zs_cnt = int(len(zs_list)) if zs_list is not None else 0
    except Exception:
        zs_cnt = 0
    out["zs_cnt"] = zs_cnt
    if zs_cnt >= 1:
        try:
            last_zs = zs_list[-1]
            low = float(getattr(last_zs, "low", 0.0) or 0.0)
            high = float(getattr(last_zs, "high", 0.0) or 0.0)
            mid = float(getattr(last_zs, "mid", (low + high) / 2.0) or 0.0)
            width = float(high - low)
            out["zs_last_width_r"] = float(width / denom)
            out["zs_last_mid_r"] = float(mid / denom)
            out["zs_last_pos"] = float((close - low) / (width + 1e-12))
            out["zs_last_is_sure"] = 1 if bool(getattr(last_zs, "is_sure", False)) else 0
            try:
                end_klu = getattr(last_zs, "end", None)
                end_idx = int(getattr(end_klu, "idx", 0) or 0)
                cur_idx = int(getattr(last_klu, "idx", 0) or 0)
                out["zs_last_age"] = int(max(0, cur_idx - end_idx))
            except Exception:
                pass
        except Exception:
            pass

    return out


def extract_state_features(chan_snapshot):
    try:
        cur_lv_chan = chan_snapshot[0]
        last_klu = cur_lv_chan[-1][-1]
    except Exception:
        return {}
    return extract_state_features_from_cur_lv(cur_lv_chan, last_klu)


def get_chan_data(code, trigger_step=True, bi_strict=True, level=KL_TYPE.K_DAY, data_src_type=DATA_SRC.BAO_STOCK, preloaded_data=None, do_predict=False, begin_time=None, end_time=None, enable_rolling_lookback=True, autype: AUTYPE = AUTYPE.QFQ):
    code = normalize_code(code)
    
    # Adjust begin_time based on frequency to avoid fetching too much data
    import datetime
    
    if begin_time is None:
        # Special handling for JQData restriction (2024-09-13 onwards)
        if level == KL_TYPE.K_DAY:
            begin_time = "2020-01-01"
        elif level == KL_TYPE.K_60M or level == KL_TYPE.K_30M:
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        else: # 5m, 15m, 1m
            # Increase lookback to 365 days for minute data to handle stale data
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        
    data_src = data_src_type
    lv_list = [level]

    # If not predicting (training), we can disable trigger_step to speed up loading
    # unless trigger_step is explicitly required for some other reason.
    # But usually trigger_step is only for simulating history for BSP collection.
    if not do_predict:
        trigger_step = False

    config = CChanConfig({
        "trigger_step": trigger_step, 
        "bi_strict": bi_strict,
        "bi_fx_check": "strict" if bi_strict else "half",
        "skip_step": 0,
        "divergence_rate": float("inf"),
        "bsp2_follow_1": False,
        "bsp3_follow_1": False,
        "min_zs_cnt": 0,
        "bs1_peak": False,
        "macd_algo": "peak",
        "bs_type": '1,2,3a,1p,2s,3b',
        "print_warning": False,
        "zs_algo": "normal",
        "mean_metrics": [5, 20],
    })
    
    try:
        if preloaded_data:
            chan = CChanCustom(
                code=code,
                begin_time=begin_time,
                end_time=end_time,
                data_src=data_src,
                lv_list=lv_list,
                config=config,
                autype=autype,
                preloaded_data=preloaded_data
            )
        else:
            chan = CChan(
                code=code,
                begin_time=begin_time,
                end_time=end_time,
                data_src=data_src,
                lv_list=lv_list,
                config=config,
                autype=autype,
            )
    except Exception as e:
        print(f"Error loading CChan: {e}")
        return None, None, None, None

    bsp_dict: Dict[int, T_SAMPLE_INFO] = {} 
    last_snapshot = None
    
    if trigger_step:
        print(f"Start processing {code} from {begin_time}...")
        kl_count = 0
        bsp_count = 0
        rolling_count = 0
        raw_stride = os.environ.get("MLCHAN_ROLLING_SAMPLE_STRIDE")
        if raw_stride is None or str(raw_stride).strip() == "":
            default_stride_by_level = {
                KL_TYPE.K_1M: 20,
                KL_TYPE.K_5M: 8,
                KL_TYPE.K_15M: 4,
                KL_TYPE.K_30M: 4,
                KL_TYPE.K_60M: 2,
            }
            rolling_stride = int(default_stride_by_level.get(level, 1))
        else:
            try:
                rolling_stride = int(str(raw_stride).strip() or "1")
            except Exception:
                rolling_stride = 1
        rolling_stride = max(1, rolling_stride)
        rolling_enabled = str(os.environ.get("MLCHAN_ROLLING_SAMPLE_ENABLED", "1") or "1").strip().lower() not in ["0", "false", "no", "off"]
        rolling_key_base = 10_000_000_000
        
        # Iterate through steps to collect training samples
        for i, chan_snapshot in enumerate(chan.step_load()):
            kl_count += 1
            if i % 100 == 0:
                print(f"Processing step {i}...")
            last_snapshot = chan_snapshot
            
            last_klu = chan_snapshot[0][-1][-1]
            bsp_list = chan_snapshot.get_latest_bsp()
            
            if not bsp_list:
                continue
                
            last_bsp = bsp_list[0]
            cur_lv_chan = chan_snapshot[0]
            state_feat = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
            
            # Record BSP when it appears
            if last_bsp.klu.idx not in bsp_dict and cur_lv_chan[-2].idx == last_bsp.klu.klc.idx:
                bsp_count += 1
                feat_copy = CFeatures(dict(last_bsp.features.items()))
                if state_feat:
                    try:
                        feat_copy.add_feat(state_feat)
                    except Exception:
                        pass
                try:
                    feat_copy.add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                except Exception:
                    pass
                bsp_dict[last_bsp.klu.idx] = {
                    "feature": feat_copy,
                    "is_buy": last_bsp.is_buy,
                    "open_time": last_klu.time,
                    "bsp_obj": last_bsp,
                    "decision_klu": last_klu,
                }

            if rolling_enabled and (i % rolling_stride == 0):
                rk = rolling_key_base + int(last_klu.idx) * 2 + (1 if bool(last_bsp.is_buy) else 0)
                if rk not in bsp_dict:
                    rolling_count += 1
                    feat_copy = CFeatures(dict(last_bsp.features.items()))
                    if state_feat:
                        try:
                            feat_copy.add_feat(state_feat)
                        except Exception:
                            pass
                    try:
                        feat_copy.add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                    except Exception:
                        pass
                    bsp_dict[rk] = {
                        "feature": feat_copy,
                        "is_buy": last_bsp.is_buy,
                        "open_time": last_klu.time,
                        "bsp_obj": last_bsp,
                        "decision_klu": last_klu,
                    }

        print(f"[DEBUG] {code}: Processed {kl_count} K-lines, Found {bsp_count} event BSP samples, Added {rolling_count} rolling samples, Total {len(bsp_dict)}")

        # Re-calculate to ensure latest state is fully updated for plotting
        if last_snapshot:
            for lv in last_snapshot.lv_list:
                 # Disable step calculation mode to force full update including virtual Bi
                 last_snapshot.kl_datas[lv].step_calculation = False
                 last_snapshot.kl_datas[lv].cal_seg_and_zs()
    else:
        # If not trigger_step, CChan has already processed everything in init
        # We just return the final state
        last_snapshot = chan
        # bsp_dict is empty because we didn't step through history to collect them
        # This is fine if do_predict is False
             
    return chan, bsp_dict, last_snapshot, config
             
    return chan, bsp_dict, last_snapshot, config

class SingleClassModel:
    def __init__(self, label):
        self.label = label
    
    def predict_proba(self, X):
        # Return probability 1.0 for the class 'label', 0.0 for others
        # Format: [[prob_0, prob_1]]
        if self.label == 1:
            return [[0.0, 1.0]] * len(X)
        else:
            return [[1.0, 0.0]] * len(X)
    
    def predict(self, X):
        return [self.label] * len(X)

def _quantile(values, q: float):
    if not values:
        return 0.0
    q = float(q)
    if q <= 0.0:
        return float(min(values))
    if q >= 1.0:
        return float(max(values))
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if n == 1:
        return float(vals[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    w = pos - lo
    return float(vals[lo] * (1.0 - w) + vals[hi] * w)

def _profit_for_info(info, lookahead: int = 3):
    decision_klu = info.get('decision_klu')
    cur_klu = decision_klu if decision_klu else info['bsp_obj'].klu
    base_price = cur_klu.close
    future_klu = cur_klu
    for _ in range(int(lookahead or 0)):
        nxt = getattr(future_klu, 'next', None)
        if nxt:
            future_klu = nxt
        else:
            break
    if info['is_buy']:
        profit = (future_klu.close - base_price) / (base_price or 1e-12)
    else:
        profit = (base_price - future_klu.close) / (base_price or 1e-12)
    return float(profit)

def _auto_profit_threshold_from_infos(infos, q: float = 0.7, min_threshold: float = 0.0, profit_lookahead: int = 3):
    profits = []
    for info in infos or []:
        try:
            profits.append(_profit_for_info(info, lookahead=profit_lookahead))
        except Exception:
            continue
    if not profits:
        return float(min_threshold)
    return float(max(float(min_threshold), _quantile(profits, q)))

def build_training_data(bsp_dict, feature_meta=None, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3):
    X = []
    y = []
    if not bsp_dict:
        return X, y, [] if feature_meta is None else feature_meta

    if feature_meta is None:
        all_keys = set()
        for info in bsp_dict.values():
            for k in info['feature'].keys():
                all_keys.add(k)
        feature_meta = sorted(all_keys)

    infos = list(bsp_dict.values())
    if profit_threshold is None:
        used_profit_threshold = _auto_profit_threshold_from_infos(infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
    else:
        used_profit_threshold = float(profit_threshold)

    for _, info in bsp_dict.items():
        try:
            profit = _profit_for_info(info, lookahead=profit_lookahead)
        except Exception:
            profit = 0.0
        label = 1 if float(profit) > used_profit_threshold else 0
        feat_vec = [info['feature'].get(k, -9999999) for k in feature_meta]
        X.append(feat_vec)
        y.append(label)

    return X, y, feature_meta

def build_feature_meta(bsp_dict):
    if not bsp_dict:
        return []
    all_keys = set()
    for info in bsp_dict.values():
        for k in info['feature'].keys():
            all_keys.add(k)
    return sorted(all_keys)

def build_training_data_from_infos(infos, feature_meta, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3):
    X = []
    y = []
    if not infos:
        return X, y

    if profit_threshold is None:
        used_profit_threshold = _auto_profit_threshold_from_infos(infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
    else:
        used_profit_threshold = float(profit_threshold)

    for info in infos:
        try:
            profit = _profit_for_info(info, lookahead=profit_lookahead)
        except Exception:
            profit = 0.0
        label = 1 if float(profit) > used_profit_threshold else 0
        feat_vec = [info['feature'].get(k, -9999999) for k in feature_meta]
        X.append(feat_vec)
        y.append(label)

    return X, y

def _rankdata_average_ties(a: np.ndarray) -> np.ndarray:
    n = int(a.size)
    if n <= 0:
        return np.asarray([], dtype=float)

    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=float)

    i = 0
    r = 1.0
    while i < n:
        j = i + 1
        ai = a[order[i]]
        while j < n and a[order[j]] == ai:
            j += 1
        k = j - i
        avg = r + (float(k) - 1.0) * 0.5
        for t in range(i, j):
            ranks[order[t]] = avg
        r += float(k)
        i = j
    return ranks

def _spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    if x.size <= 1 or y.size <= 1:
        return 0.0
    rx = _rankdata_average_ties(x)
    ry = _rankdata_average_ties(y)
    mx = float(rx.mean())
    my = float(ry.mean())
    dx = rx - mx
    dy = ry - my
    denom = float(np.sqrt(np.sum(dx * dx) * np.sum(dy * dy)))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(dx * dy) / denom)

def compute_feature_ic_table(
    X,
    y,
    feature_meta,
    missing_value: float = -9999999.0,
    min_n: int = 50,
):
    if not X or not y or not feature_meta:
        return []

    try:
        arr = np.asarray(X, dtype=float)
    except Exception:
        return []
    if arr.ndim != 2 or arr.shape[0] <= 1:
        return []

    try:
        yy = np.asarray(y, dtype=float)
    except Exception:
        yy = np.asarray([float(v) for v in (y or [])], dtype=float)
    if yy.ndim != 1 or yy.size <= 1:
        return []

    n_rows = int(min(arr.shape[0], yy.size))
    arr = arr[:n_rows, :]
    yy = yy[:n_rows]

    out = []
    n_cols = int(arr.shape[1])
    for j in range(n_cols):
        name = None
        try:
            name = feature_meta[j]
        except Exception:
            name = f"f{j}"

        xj = arr[:, j]
        mask = np.isfinite(xj) & (xj != float(missing_value)) & np.isfinite(yy)
        n_valid = int(np.sum(mask))
        if n_valid < int(min_n):
            ic = 0.0
        else:
            ic = _spearman_corr(xj[mask], yy[mask])
        out.append({"feature": str(name), "ic": float(ic), "abs_ic": float(abs(ic)), "n": int(n_valid)})

    out.sort(key=lambda d: float(d.get("abs_ic", 0.0)), reverse=True)
    return out

def compute_feature_ic_report(
    X,
    y,
    feature_meta,
    keep_threshold: float = 0.02,
    drop_threshold: float = 0.01,
    top_n: int = 20,
    include_table: bool = True,
):
    table = compute_feature_ic_table(X, y, feature_meta)
    if not table:
        return {
            "keep_threshold": float(keep_threshold),
            "drop_threshold": float(drop_threshold),
            "feature_count": 0,
            "kept_count": 0,
            "dropped_count": 0,
            "mid_count": 0,
            "max_abs_ic": 0.0,
            "all_abs_ic_lt_drop_threshold": True,
            "top_abs": [],
            "top_positive": [],
            "top_negative": [],
            "suggest_keep_features": [],
            "suggest_drop_features": [],
            "table": [] if include_table else None,
        }

    keep_threshold = float(keep_threshold)
    drop_threshold = float(drop_threshold)
    kept = [r for r in table if float(r.get("abs_ic", 0.0)) >= keep_threshold]
    dropped = [r for r in table if float(r.get("abs_ic", 0.0)) <= drop_threshold]
    mid = [r for r in table if float(r.get("abs_ic", 0.0)) > drop_threshold and float(r.get("abs_ic", 0.0)) < keep_threshold]

    max_abs_ic = float(max((float(r.get("abs_ic", 0.0)) for r in table), default=0.0))

    pos = sorted(table, key=lambda r: float(r.get("ic", 0.0)), reverse=True)
    neg = sorted(table, key=lambda r: float(r.get("ic", 0.0)))

    def _pick(rows):
        return [{"feature": r.get("feature"), "ic": float(r.get("ic", 0.0)), "n": int(r.get("n", 0))} for r in (rows[: int(top_n)] if int(top_n) > 0 else [])]

    return {
        "keep_threshold": float(keep_threshold),
        "drop_threshold": float(drop_threshold),
        "feature_count": int(len(table)),
        "kept_count": int(len(kept)),
        "dropped_count": int(len(dropped)),
        "mid_count": int(len(mid)),
        "max_abs_ic": float(max_abs_ic),
        "all_abs_ic_lt_drop_threshold": bool(max_abs_ic < drop_threshold),
        "top_abs": _pick(table),
        "top_positive": _pick(pos),
        "top_negative": _pick(neg),
        "suggest_keep_features": [r.get("feature") for r in kept],
        "suggest_drop_features": [r.get("feature") for r in dropped],
        "table": table if include_table else None,
    }

def _logistic_regression_baseline_probs(X_train, y_train, X_test):
    if not X_train or not y_train or not X_test:
        return []
    if len(set(y_train)) < 2:
        return []
    try:
        pipe = Pipeline(
            [
                ("imputer", SimpleImputer(missing_values=-9999999, strategy="mean")),
                ("scaler", StandardScaler()),
                ("logreg", LogisticRegression(max_iter=1000, solver="liblinear")),
            ]
        )
        pipe.fit(X_train, y_train)
        probs = predict_proba_1(pipe, X_test)
        return [float(p) for p in (probs or [])]
    except Exception:
        return []

def _binary_class_counts(y):
    neg = 0
    pos = 0
    for v in (y or []):
        iv = None
        try:
            iv = int(v)
        except Exception:
            try:
                iv = int(float(v))
            except Exception:
                iv = None
        if iv == 1:
            pos += 1
        elif iv == 0:
            neg += 1
    return neg, pos

def _binary_balance_meta(y):
    neg, pos = _binary_class_counts(y)
    total = neg + pos
    pos_rate = (float(pos) / float(total)) if total > 0 else 0.0
    return {"neg": neg, "pos": pos, "total": total, "pos_rate": pos_rate}

def _scale_pos_weight_from_y(y):
    neg, pos = _binary_class_counts(y)
    if pos <= 0:
        return 1.0
    return float(neg) / float(pos)

def build_estimator(model_type="xgboost", n_jobs=-1, scale_pos_weight=1.0, use_scale_pos_weight: bool = False, xgb_max_depth: Optional[int] = None, xgb_reg_alpha: Optional[float] = None, xgb_reg_lambda: Optional[float] = None, xgb_subsample: Optional[float] = None, xgb_colsample_bytree: Optional[float] = None, lgb_max_depth: Optional[int] = None, lgb_reg_alpha: Optional[float] = None, lgb_reg_lambda: Optional[float] = None):
    if model_type == "xgboost":
        md = 5 if xgb_max_depth is None else int(xgb_max_depth)
        ra = 0.0 if xgb_reg_alpha is None else float(xgb_reg_alpha)
        rl = 1.0 if xgb_reg_lambda is None else float(xgb_reg_lambda)
        ss = 1.0 if xgb_subsample is None else float(xgb_subsample)
        cs = 1.0 if xgb_colsample_bytree is None else float(xgb_colsample_bytree)
        spw = float(scale_pos_weight) if bool(use_scale_pos_weight) else 1.0
        return xgb.XGBClassifier(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=md,
            eval_metric='logloss',
            n_jobs=n_jobs,
            missing=-9999999,
            random_state=42,
            scale_pos_weight=spw,
            reg_alpha=ra,
            reg_lambda=rl,
            subsample=ss,
            colsample_bytree=cs,
        )
    if model_type == "lightgbm":
        md = 5 if lgb_max_depth is None else int(lgb_max_depth)
        ra = 0.0 if lgb_reg_alpha is None else float(lgb_reg_alpha)
        rl = 0.0 if lgb_reg_lambda is None else float(lgb_reg_lambda)
        spw = float(scale_pos_weight) if bool(use_scale_pos_weight) else 1.0
        return lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, max_depth=md, verbosity=-1, n_jobs=n_jobs, scale_pos_weight=spw, reg_alpha=ra, reg_lambda=rl)
    if model_type == "mlp":
        return Pipeline([
            ('imputer', SimpleImputer(missing_values=-9999999, strategy='mean')),
            ('scaler', StandardScaler()),
            ('mlp', MLPClassifier(
                hidden_layer_sizes=(100, 50),
                max_iter=2000,
                learning_rate='adaptive',
                early_stopping=True,
                random_state=42
            ))
        ])
    spw = float(scale_pos_weight) if bool(use_scale_pos_weight) else 1.0
    md = 5 if xgb_max_depth is None else int(xgb_max_depth)
    ra = 0.0 if xgb_reg_alpha is None else float(xgb_reg_alpha)
    rl = 1.0 if xgb_reg_lambda is None else float(xgb_reg_lambda)
    ss = 1.0 if xgb_subsample is None else float(xgb_subsample)
    cs = 1.0 if xgb_colsample_bytree is None else float(xgb_colsample_bytree)
    return xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=md, eval_metric='logloss', scale_pos_weight=spw, reg_alpha=ra, reg_lambda=rl, subsample=ss, colsample_bytree=cs)

def _min_val_fraction() -> float:
    raw = os.environ.get("MLCHAN_VAL_FRACTION", "0.2")
    try:
        v = float(raw)
    except Exception:
        v = 0.2
    if v < 0.2:
        v = 0.2
    if v >= 0.5:
        v = 0.5
    return float(v)


def _feature_keep_fraction() -> float:
    raw = os.environ.get("MLCHAN_FEATURE_KEEP_FRACTION", "0.5")
    try:
        v = float(raw)
    except Exception:
        v = 0.5
    if v <= 0.0:
        v = 0.5
    if v > 1.0:
        v = 1.0
    if v > 0.5:
        v = 0.5
    return float(v)


def _feature_corr_threshold() -> float:
    raw = os.environ.get("MLCHAN_FEATURE_CORR_THRESHOLD", "0.9")
    try:
        v = float(raw)
    except Exception:
        v = 0.9
    if v <= 0.0:
        v = 0.9
    if v >= 0.999:
        v = 0.999
    return float(v)


def _split_train_val_time_order(X, y, val_frac: float):
    n = len(X or [])
    if n <= 2:
        return X, y, [], []
    try:
        vf = float(val_frac)
    except Exception:
        vf = 0.2
    if vf < 0.2:
        vf = 0.2
    if vf >= 0.5:
        vf = 0.5
    val_size = int(round(n * vf))
    val_size = max(1, min(val_size, n - 1))
    split = n - val_size
    return X[:split], y[:split], X[split:], y[split:]


def _importance_map_from_model(model, feature_names):
    m = {}
    imp = _get_feature_importance(model, feature_names)
    for row in imp or []:
        if not isinstance(row, dict):
            continue
        k = row.get("feature")
        if not k:
            continue
        try:
            m[str(k)] = float(row.get("importance", 0.0) or 0.0)
        except Exception:
            m[str(k)] = 0.0
    return m


def _select_top_by_importance(feature_names, importance_map, keep_frac: float):
    if not feature_names:
        return []
    try:
        kf = float(keep_frac)
    except Exception:
        kf = 0.5
    if kf <= 0.0:
        kf = 0.5
    if kf > 1.0:
        kf = 1.0
    if kf > 0.5:
        kf = 0.5
    n = len(feature_names)
    keep_n = max(2, int(round(n * kf)))
    keep_n = min(keep_n, n)
    ordered = list(feature_names)
    ordered.sort(key=lambda f: float(importance_map.get(str(f), 0.0)), reverse=True)
    return ordered[:keep_n]


def _prune_correlated_features(X, feature_names, importance_map, corr_threshold: float):
    if not X or not feature_names or len(feature_names) <= 2:
        return list(feature_names or [])
    try:
        th = float(corr_threshold)
    except Exception:
        th = 0.9
    if th <= 0.0:
        th = 0.9
    if th >= 0.999:
        th = 0.999

    try:
        arr = np.asarray(X, dtype=float)
    except Exception:
        return list(feature_names)
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] != len(feature_names):
        return list(feature_names)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    mean = arr.mean(axis=0)
    std = arr.std(axis=0)
    std[std < 1e-12] = 1.0
    z = (arr - mean) / std
    try:
        corr = np.corrcoef(z, rowvar=False)
    except Exception:
        return list(feature_names)

    order = list(range(len(feature_names)))
    order.sort(key=lambda i: float(importance_map.get(str(feature_names[i]), 0.0)), reverse=True)
    keep = [True] * len(feature_names)
    for a_pos, i in enumerate(order):
        if not keep[i]:
            continue
        for j in order[a_pos + 1:]:
            if not keep[j]:
                continue
            try:
                cv = float(corr[i, j])
            except Exception:
                continue
            if abs(cv) > th:
                keep[j] = False
    out = [feature_names[i] for i in range(len(feature_names)) if keep[i]]
    if len(out) < 2:
        return list(feature_names)
    return out


def train_model_from_xy(X_train, y_train, model_type="xgboost", n_jobs=-1, use_scale_pos_weight: bool = False, xgb_max_depth: Optional[int] = None, xgb_reg_alpha: Optional[float] = None, xgb_reg_lambda: Optional[float] = None, xgb_subsample: Optional[float] = None, xgb_colsample_bytree: Optional[float] = None, lgb_max_depth: Optional[int] = None, lgb_reg_alpha: Optional[float] = None, lgb_reg_lambda: Optional[float] = None, X_val=None, y_val=None):
    if not X_train:
        return None
    if len(set(y_train)) < 2:
        return SingleClassModel(list(set(y_train))[0])

    scale_pos_weight = 1.0
    if bool(use_scale_pos_weight) and model_type in ["xgboost", "lightgbm"]:
        neg_count, pos_count = _binary_class_counts(y_train)
        if pos_count > 0:
            scale_pos_weight = float(neg_count) / float(pos_count)

    model = build_estimator(
        model_type=model_type,
        n_jobs=n_jobs,
        scale_pos_weight=scale_pos_weight,
        use_scale_pos_weight=use_scale_pos_weight,
        xgb_max_depth=xgb_max_depth,
        xgb_reg_alpha=xgb_reg_alpha,
        xgb_reg_lambda=xgb_reg_lambda,
        xgb_subsample=xgb_subsample,
        xgb_colsample_bytree=xgb_colsample_bytree,
        lgb_max_depth=lgb_max_depth,
        lgb_reg_alpha=lgb_reg_alpha,
        lgb_reg_lambda=lgb_reg_lambda,
    )
    used_X_train = X_train
    used_y_train = y_train
    used_X_val = X_val
    used_y_val = y_val
    if not used_X_val or not used_y_val:
        used_X_train, used_y_train, used_X_val, used_y_val = _split_train_val_time_order(X_train, y_train, _min_val_fraction())

    fit_kwargs = {}
    if model_type in ["xgboost", "lightgbm"] and used_X_val and used_y_val:
        fit_kwargs["eval_set"] = [(used_X_val, used_y_val)]
        fit_kwargs["verbose"] = False
    model.fit(used_X_train, used_y_train, **fit_kwargs)
    return model


def train_model_with_feature_selection(
    X_train,
    y_train,
    feature_meta,
    model_type="xgboost",
    n_jobs=-1,
    use_scale_pos_weight: bool = False,
    xgb_max_depth: Optional[int] = None,
    xgb_reg_alpha: Optional[float] = None,
    xgb_reg_lambda: Optional[float] = None,
    xgb_subsample: Optional[float] = None,
    xgb_colsample_bytree: Optional[float] = None,
    lgb_max_depth: Optional[int] = None,
    lgb_reg_alpha: Optional[float] = None,
    lgb_reg_lambda: Optional[float] = None,
    X_val=None,
    y_val=None,
):
    if not feature_meta:
        m = train_model_from_xy(
            X_train,
            y_train,
            model_type=model_type,
            n_jobs=n_jobs,
            use_scale_pos_weight=use_scale_pos_weight,
            xgb_max_depth=xgb_max_depth,
            xgb_reg_alpha=xgb_reg_alpha,
            xgb_reg_lambda=xgb_reg_lambda,
            xgb_subsample=xgb_subsample,
            xgb_colsample_bytree=xgb_colsample_bytree,
            lgb_max_depth=lgb_max_depth,
            lgb_reg_alpha=lgb_reg_alpha,
            lgb_reg_lambda=lgb_reg_lambda,
            X_val=X_val,
            y_val=y_val,
        )
        return m, list(feature_meta or []), {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}

    model = train_model_from_xy(
        X_train,
        y_train,
        model_type=model_type,
        n_jobs=n_jobs,
        use_scale_pos_weight=use_scale_pos_weight,
        xgb_max_depth=xgb_max_depth,
        xgb_reg_alpha=xgb_reg_alpha,
        xgb_reg_lambda=xgb_reg_lambda,
        xgb_subsample=xgb_subsample,
        xgb_colsample_bytree=xgb_colsample_bytree,
        lgb_max_depth=lgb_max_depth,
        lgb_reg_alpha=lgb_reg_alpha,
        lgb_reg_lambda=lgb_reg_lambda,
        X_val=X_val,
        y_val=y_val,
    )
    if not model or isinstance(model, SingleClassModel):
        return model, list(feature_meta or []), {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}

    importance_map = _importance_map_from_model(model, feature_meta)
    keep_frac = _feature_keep_fraction()
    corr_th = _feature_corr_threshold()
    kept1 = _select_top_by_importance(feature_meta, importance_map, keep_frac=keep_frac)
    dropped_by_importance = max(0, len(feature_meta) - len(kept1))

    idx_map = {str(k): i for i, k in enumerate(feature_meta)}
    cols = [idx_map.get(str(k)) for k in kept1 if str(k) in idx_map]
    if len(cols) < 2:
        return model, list(feature_meta), {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
    X_tr_sel = [[row[c] for c in cols] for row in (X_train or [])]
    kept2 = _prune_correlated_features(X_tr_sel, kept1, importance_map, corr_threshold=corr_th)
    dropped_by_corr = max(0, len(kept1) - len(kept2))

    cols2 = [idx_map.get(str(k)) for k in kept2 if str(k) in idx_map]
    if len(cols2) < 2:
        kept2 = kept1
        cols2 = cols
        dropped_by_corr = 0

    X_train2 = [[row[c] for c in cols2] for row in (X_train or [])]
    X_val2 = [[row[c] for c in cols2] for row in (X_val or [])] if (X_val and y_val) else None
    y_val2 = list(y_val) if (X_val and y_val) else None

    model2 = train_model_from_xy(
        X_train2,
        y_train,
        model_type=model_type,
        n_jobs=n_jobs,
        use_scale_pos_weight=use_scale_pos_weight,
        xgb_max_depth=xgb_max_depth,
        xgb_reg_alpha=xgb_reg_alpha,
        xgb_reg_lambda=xgb_reg_lambda,
        xgb_subsample=xgb_subsample,
        xgb_colsample_bytree=xgb_colsample_bytree,
        lgb_max_depth=lgb_max_depth,
        lgb_reg_alpha=lgb_reg_alpha,
        lgb_reg_lambda=lgb_reg_lambda,
        X_val=X_val2,
        y_val=y_val2,
    )
    if model2:
        model = model2
    return model, list(kept2), {"selected_features": list(kept2), "dropped_by_importance": int(dropped_by_importance), "dropped_by_corr": int(dropped_by_corr)}

def predict_proba_1(model, X):
    probs = model.predict_proba(X)
    out = []
    for row in probs:
        if len(row) >= 2:
            out.append(float(row[1]))
        elif len(row) == 1:
            out.append(float(row[0]))
        else:
            out.append(0.0)
    return out

def brier_score(model, X, y):
    if not model or not X or not y:
        return 0.0
    p = predict_proba_1(model, X)
    total = min(len(p), len(y))
    if total <= 0:
        return 0.0
    s = 0.0
    for i in range(total):
        diff = float(p[i]) - float(y[i])
        s += diff * diff
    return s / total

def _clip01(p: float, eps: float = 1e-15) -> float:
    try:
        pv = float(p)
    except Exception:
        pv = 0.0
    if pv < eps:
        return eps
    if pv > 1.0 - eps:
        return 1.0 - eps
    return pv

def _brier_from_probs(y, probs) -> float:
    if not y or not probs:
        return 0.0
    total = min(len(y), len(probs))
    if total <= 0:
        return 0.0
    s = 0.0
    for i in range(total):
        diff = float(probs[i]) - float(y[i])
        s += diff * diff
    return float(s / total)

def _logloss_from_probs(y, probs) -> float:
    if not y or not probs:
        return 0.0
    total = min(len(y), len(probs))
    if total <= 0:
        return 0.0
    yt = []
    pt = []
    for i in range(total):
        try:
            yt.append(int(y[i]))
        except Exception:
            try:
                yt.append(int(float(y[i])))
            except Exception:
                yt.append(0)
        pt.append(_clip01(probs[i]))
    try:
        return float(log_loss(yt, pt, labels=[0, 1]))
    except Exception:
        return 0.0

def _roc_auc_from_probs(y, probs):
    if not y or not probs:
        return None
    total = min(len(y), len(probs))
    if total <= 0:
        return None
    yt = []
    pt = []
    for i in range(total):
        try:
            yt.append(int(y[i]))
        except Exception:
            try:
                yt.append(int(float(y[i])))
            except Exception:
                yt.append(0)
        pt.append(float(probs[i]))
    if len(set(yt)) < 2:
        return None
    try:
        return float(roc_auc_score(yt, pt))
    except Exception:
        return None

def _pr_auc_from_probs(y, probs):
    if not y or not probs:
        return None
    total = min(len(y), len(probs))
    if total <= 0:
        return None
    yt = []
    pt = []
    for i in range(total):
        try:
            yt.append(int(y[i]))
        except Exception:
            try:
                yt.append(int(float(y[i])))
            except Exception:
                yt.append(0)
        pt.append(float(probs[i]))
    if len(set(yt)) < 2:
        return None
    try:
        return float(average_precision_score(yt, pt))
    except Exception:
        return None

def _reversal_check_from_probs(y, probs, quantile_bin_count: int = 5):
    if not y or not probs:
        return {
            "suggest_invert": False,
            "roc_auc": None,
            "roc_auc_inverted": None,
            "pr_auc": None,
            "pr_auc_inverted": None,
            "monotonic_violations": 0,
            "monotonic_violations_inverted": 0,
        }
    total = min(len(y), len(probs))
    if total <= 0:
        return {
            "suggest_invert": False,
            "roc_auc": None,
            "roc_auc_inverted": None,
            "pr_auc": None,
            "pr_auc_inverted": None,
            "monotonic_violations": 0,
            "monotonic_violations_inverted": 0,
        }

    inv = []
    for i in range(total):
        try:
            inv.append(1.0 - float(probs[i]))
        except Exception:
            inv.append(1.0)

    auc = _roc_auc_from_probs(y, probs)
    auc_inv = _roc_auc_from_probs(y, inv)
    pr = _pr_auc_from_probs(y, probs)
    pr_inv = _pr_auc_from_probs(y, inv)

    qb = _quantile_bins_from_probs(y, probs, bin_count=quantile_bin_count)
    qb_inv = _quantile_bins_from_probs(y, inv, bin_count=quantile_bin_count)
    mv = _monotonic_violations(qb)
    mv_inv = _monotonic_violations(qb_inv)

    suggest = False
    if auc is None and auc_inv is not None:
        suggest = True
    elif auc is not None and auc_inv is not None:
        suggest = bool((auc_inv - auc) > 0.02 and (mv > 0 or mv_inv < mv))
    elif mv > 0 and mv_inv == 0:
        suggest = True

    return {
        "suggest_invert": bool(suggest),
        "roc_auc": auc,
        "roc_auc_inverted": auc_inv,
        "pr_auc": pr,
        "pr_auc_inverted": pr_inv,
        "monotonic_violations": int(mv),
        "monotonic_violations_inverted": int(mv_inv),
    }

def _calibration_bins_from_probs(y, probs, bin_count: int = 10):
    bins = []
    ece = 0.0
    if not y or not probs:
        return bins, ece
    total = min(len(y), len(probs))
    if total <= 0:
        return bins, ece
    try:
        bin_count = int(bin_count)
    except Exception:
        bin_count = 10
    if bin_count <= 0:
        bin_count = 10
    agg = [{"count": 0, "sum_p": 0.0, "sum_y": 0.0} for _ in range(bin_count)]
    for i in range(total):
        p = float(probs[i])
        yv = float(y[i])
        idx = int(p * bin_count)
        if idx >= bin_count:
            idx = bin_count - 1
        if idx < 0:
            idx = 0
        agg[idx]["count"] += 1
        agg[idx]["sum_p"] += p
        agg[idx]["sum_y"] += yv
    for i in range(bin_count):
        c = agg[i]["count"]
        low = i / bin_count
        high = (i + 1) / bin_count
        if c > 0:
            avg_p = agg[i]["sum_p"] / c
            win_rate = agg[i]["sum_y"] / c
            ece += abs(avg_p - win_rate) * (c / total)
        else:
            avg_p = 0.0
            win_rate = 0.0
        bins.append({"low": low, "high": high, "count": c, "avg_pred": avg_p, "win_rate": win_rate})
    return bins, float(ece)

def _precision_at_threshold_from_probs(y, probs, threshold: float = 0.7):
    if not y or not probs:
        return {"threshold": float(threshold), "count": 0, "precision": 0.0, "avg_pred": 0.0}
    total = min(len(y), len(probs))
    if total <= 0:
        return {"threshold": float(threshold), "count": 0, "precision": 0.0, "avg_pred": 0.0}
    thr = float(threshold)
    c = 0
    sum_p = 0.0
    sum_y = 0.0
    for i in range(total):
        try:
            p = float(probs[i])
        except Exception:
            p = 0.0
        if p < thr:
            continue
        c += 1
        sum_p += p
        try:
            sum_y += float(y[i])
        except Exception:
            sum_y += 0.0
    if c <= 0:
        return {"threshold": float(thr), "count": 0, "precision": 0.0, "avg_pred": 0.0}
    return {"threshold": float(thr), "count": int(c), "precision": float(sum_y / c), "avg_pred": float(sum_p / c)}

def _high_confidence_bins_from_probs(y, probs, ranges=None):
    if ranges is None:
        ranges = [(0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    out = []
    if not y or not probs:
        for low, high in (ranges or []):
            out.append({"low": float(low), "high": float(high), "count": 0, "avg_pred": 0.0, "win_rate": 0.0})
        return out
    total = min(len(y), len(probs))
    if total <= 0:
        for low, high in (ranges or []):
            out.append({"low": float(low), "high": float(high), "count": 0, "avg_pred": 0.0, "win_rate": 0.0})
        return out
    for low, high in (ranges or []):
        c = 0
        sum_p = 0.0
        sum_y = 0.0
        lo = float(low)
        hi = float(high)
        for i in range(total):
            try:
                p = float(probs[i])
            except Exception:
                p = 0.0
            if hi >= 1.0:
                if p < lo or p > hi:
                    continue
            else:
                if p < lo or p >= hi:
                    continue
            c += 1
            sum_p += p
            try:
                sum_y += float(y[i])
            except Exception:
                sum_y += 0.0
        if c <= 0:
            out.append({"low": float(lo), "high": float(hi), "count": 0, "avg_pred": 0.0, "win_rate": 0.0})
        else:
            out.append({"low": float(lo), "high": float(hi), "count": int(c), "avg_pred": float(sum_p / c), "win_rate": float(sum_y / c)})
    return out

def _prob_summary_from_probs(probs):
    if not probs:
        return {
            "count": 0,
            "min": 0.0,
            "p05": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
        }
    vals = []
    for p in probs:
        try:
            vals.append(float(p))
        except Exception:
            vals.append(0.0)
    vals.sort()
    n = len(vals)
    if n <= 0:
        return {
            "count": 0,
            "min": 0.0,
            "p05": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
        }
    s = 0.0
    for v in vals:
        s += float(v)
    mean = s / float(n)
    var = 0.0
    for v in vals:
        d = float(v) - float(mean)
        var += d * d
    std = (var / float(n)) ** 0.5 if n > 0 else 0.0

    def _q(frac: float):
        if n == 1:
            return float(vals[0])
        x = float(frac)
        if x < 0.0:
            x = 0.0
        if x > 1.0:
            x = 1.0
        idx = int(round(x * float(n - 1)))
        if idx < 0:
            idx = 0
        if idx >= n:
            idx = n - 1
        return float(vals[idx])

    return {
        "count": int(n),
        "min": float(vals[0]),
        "p05": _q(0.05),
        "p25": _q(0.25),
        "p50": _q(0.50),
        "p75": _q(0.75),
        "p95": _q(0.95),
        "max": float(vals[-1]),
        "mean": float(mean),
        "std": float(std),
    }

def _quantile_bins_from_probs(y, probs, bin_count: int = 5):
    bins = []
    if not y or not probs:
        return bins
    total = min(len(y), len(probs))
    if total <= 0:
        return bins
    try:
        bin_count = int(bin_count)
    except Exception:
        bin_count = 5
    if bin_count <= 0:
        bin_count = 5
    idxs = list(range(total))
    idxs.sort(key=lambda i: float(probs[i]))
    for bi in range(bin_count):
        start = int((bi * total) / bin_count)
        end = int(((bi + 1) * total) / bin_count)
        if end <= start:
            continue
        sub = idxs[start:end]
        s_p = 0.0
        s_y = 0.0
        p_min = None
        p_max = None
        for i in sub:
            pv = float(probs[i])
            yv = float(y[i])
            s_p += pv
            s_y += yv
            p_min = pv if p_min is None else min(p_min, pv)
            p_max = pv if p_max is None else max(p_max, pv)
        c = len(sub)
        avg_p = s_p / c if c > 0 else 0.0
        win_rate = s_y / c if c > 0 else 0.0
        bins.append({
            "low": float(p_min if p_min is not None else 0.0),
            "high": float(p_max if p_max is not None else 0.0),
            "count": c,
            "avg_pred": float(avg_p),
            "win_rate": float(win_rate),
        })
    return bins

def _monotonic_violations(bins) -> int:
    if not bins:
        return 0
    v = 0
    last_wr = None
    last_ap = None
    for b in bins:
        ap = float(b.get("avg_pred", 0.0) or 0.0)
        wr = float(b.get("win_rate", 0.0) or 0.0)
        if last_wr is not None and last_ap is not None and ap >= last_ap and wr < last_wr:
            v += 1
        last_wr = wr
        last_ap = ap
    return int(v)

def _accuracy_from_probs(y, probs, threshold: float = 0.5):
    if not y or not probs:
        return 0, 0, 0.0
    total = min(len(y), len(probs))
    if total <= 0:
        return 0, 0, 0.0
    correct = 0
    for i in range(total):
        pred = 1 if float(probs[i]) >= float(threshold) else 0
        try:
            yi = int(y[i])
        except Exception:
            try:
                yi = int(float(y[i]))
            except Exception:
                yi = 0
        if pred == yi:
            correct += 1
    return int(correct), int(total), (float(correct) / float(total) if total > 0 else 0.0)

def _max_drawdown_from_pnls(pnls):
    if not pnls:
        return 0.0
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for r in pnls:
        try:
            eq += float(r)
        except Exception:
            continue
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > mdd:
            mdd = dd
    return float(mdd)

def _feature_meta_from_infos(infos):
    if not infos:
        return []
    all_keys = set()
    for info in infos:
        try:
            feat = info.get("feature")
        except Exception:
            feat = None
        if not feat:
            continue
        try:
            for k in feat.keys():
                all_keys.add(k)
        except Exception:
            continue
    return sorted(all_keys)

def _normalize_calibrate_method(raw):
    s = str(raw or "").strip().lower()
    if s in ["", "none", "raw", "off", "disable", "disabled", "no", "false", "0"]:
        return None
    if s in ["sigmoid", "platt"]:
        return "sigmoid"
    if s == "isotonic":
        return "sigmoid"
    return None

def _walk_forward_eval_infos(
    infos,
    model_type="xgboost",
    test_ratio=0.2,
    val_ratio=0.2,
    threshold=0.5,
    calibrate_method="none",
    min_train=200,
    min_val=200,
    min_test=500,
    n_jobs=-1,
    profit_threshold: Optional[float] = 0.02,
    auto_profit_quantile: float = 0.7,
    profit_lookahead: int = 3,
    use_scale_pos_weight: bool = False,
    xgb_max_depth: Optional[int] = None,
    xgb_reg_alpha: Optional[float] = None,
    xgb_reg_lambda: Optional[float] = None,
    lgb_max_depth: Optional[int] = None,
    lgb_reg_alpha: Optional[float] = None,
    lgb_reg_lambda: Optional[float] = None,
    walk_forward_max_folds: int = 20,
    walk_forward_test_window: Optional[int] = None,
    walk_forward_val_window: Optional[int] = None,
    walk_forward_step: Optional[int] = None,
    trade_cost: float = 0.0,
    topk_frac: float = 0.2,
    quantile_bin_count: int = 5,
):
    n = len(infos or [])
    if n <= 0:
        return None, {
            "status": "error",
            "detail": "no infos"
        }

    if walk_forward_test_window is None:
        walk_forward_test_window = int(n * float(test_ratio))
    try:
        test_window = int(walk_forward_test_window)
    except Exception:
        test_window = int(n * float(test_ratio))
    test_window = max(int(min_test), test_window, 1)

    used_calibrate_method_req = _normalize_calibrate_method(calibrate_method)

    provided_val_window = walk_forward_val_window is not None
    if walk_forward_val_window is None:
        walk_forward_val_window = int(n * float(val_ratio))
    try:
        val_window = int(walk_forward_val_window)
    except Exception:
        val_window = int(n * float(val_ratio))
    if (not provided_val_window) and float(val_ratio) <= 0.0:
        val_window = int(min_val)
    val_window = max(int(min_val), int(val_window), 1)

    try:
        max_folds = int(walk_forward_max_folds)
    except Exception:
        max_folds = 20
    if max_folds <= 0:
        max_folds = 1

    if walk_forward_step is None:
        step = test_window
    else:
        try:
            step = int(walk_forward_step)
        except Exception:
            step = test_window
    step = max(step, test_window, 1)

    start_train_end = int(min_train)
    if start_train_end <= 0:
        start_train_end = 1

    desired_train_years = 6.0
    try:
        first_ts = int(getattr((infos[0] or {}).get("open_time"), "ts", 0) or 0)
    except Exception:
        first_ts = 0
    if first_ts > 0:
        target_ts = int(first_ts) + int(float(desired_train_years) * 365.0 * 24.0 * 3600.0)
        idx_years = 0
        for i in range(n):
            try:
                ts = int(getattr((infos[i] or {}).get("open_time"), "ts", 0) or 0)
            except Exception:
                ts = 0
            if ts and int(ts) >= int(target_ts):
                idx_years = int(i + 1)
                break
        if idx_years > 0:
            start_train_end = max(start_train_end, idx_years)
    start_train_end = max(start_train_end, 100)

    if start_train_end + val_window + test_window > n:
        return None, {
            "status": "error",
            "detail": f"insufficient_data_for_walk_forward: n={n} need>={start_train_end + val_window + test_window}",
        }

    p_all = []
    y_all = []
    profit_all = []
    p_prior_all = []
    p_logreg_all = []
    infos_all = []
    folds = []
    last_model = None
    last_meta = []
    last_fold_train_X = None
    last_fold_train_y = None
    last_fold_feature_meta = None

    train_end = int(start_train_end)
    used_folds = 0
    while used_folds < max_folds:
        val_start = train_end
        val_end = train_end + val_window
        test_start = val_end
        test_end = test_start + test_window
        if test_end > n:
            break

        train_infos = infos[:train_end]
        val_infos = infos[val_start:val_end] if val_window > 0 else []
        test_infos = infos[test_start:test_end]

        feature_meta = _feature_meta_from_infos(train_infos)
        if not feature_meta:
            train_end += step
            used_folds += 1
            continue

        if profit_threshold is None:
            used_profit_threshold = _auto_profit_threshold_from_infos(train_infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
        else:
            used_profit_threshold = float(profit_threshold)

        X_train, y_train = build_training_data_from_infos(train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
        X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
        X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)

        if not X_train or not X_test or len(set(y_train)) < 2:
            train_end += step
            used_folds += 1
            continue

        selected_features = []
        feature_sel_meta = {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
        model = None
        try:
            model, feature_meta2, feature_sel_meta = train_model_with_feature_selection(
                X_train,
                y_train,
                feature_meta,
                model_type=model_type,
                n_jobs=n_jobs,
                use_scale_pos_weight=use_scale_pos_weight,
                xgb_max_depth=xgb_max_depth,
                xgb_reg_alpha=xgb_reg_alpha,
                xgb_reg_lambda=xgb_reg_lambda,
                lgb_max_depth=lgb_max_depth,
                lgb_reg_alpha=lgb_reg_alpha,
                lgb_reg_lambda=lgb_reg_lambda,
                X_val=X_val,
                y_val=y_val,
            )
        except Exception:
            model = None
            feature_meta2 = None
            feature_sel_meta = {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
        if not model:
            train_end += step
            used_folds += 1
            continue
        if feature_meta2:
            feature_meta = list(feature_meta2)
        selected_features = list(feature_sel_meta.get("selected_features") or feature_meta or [])

        X_train, y_train = build_training_data_from_infos(train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
        X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
        X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
        if not X_train or not X_test or len(set(y_train)) < 2:
            train_end += step
            used_folds += 1
            continue

        score_model = model
        calibrated = False
        used_calibrate_method = None
        if used_calibrate_method_req is not None and val_window > 0 and X_val and y_val and len(set(y_val)) >= 2 and not isinstance(model, SingleClassModel):
            chosen_method = used_calibrate_method_req
            try:
                try:
                    cal = CalibratedClassifierCV(estimator=model, method=chosen_method, cv="prefit")
                except TypeError:
                    cal = CalibratedClassifierCV(base_estimator=model, method=chosen_method, cv="prefit")
                cal.fit(X_val, y_val)
                score_model = cal
                calibrated = True
                used_calibrate_method = chosen_method
            except Exception:
                score_model = model
                calibrated = False
                used_calibrate_method = None

        try:
            probs = predict_proba_1(score_model, X_test)
        except Exception:
            probs = []
        total = min(len(probs), len(y_test))
        if total <= 0:
            train_end += step
            used_folds += 1
            continue

        neg_c, pos_c = _binary_class_counts(y_train)
        train_pos_rate = (float(pos_c) / float(neg_c + pos_c)) if (neg_c + pos_c) > 0 else 0.0
        prior_probs = [float(train_pos_rate) for _ in range(total)]
        fold_ic = compute_feature_ic_report(X_train, y_train, feature_meta, include_table=False, top_n=10)
        fold_logreg_probs = _logistic_regression_baseline_probs(X_train, y_train, X_test)
        logreg_ok = bool(fold_logreg_probs) and len(fold_logreg_probs) >= total
        if not logreg_ok:
            fold_logreg_probs = list(prior_probs)
        last_fold_train_X = X_train
        last_fold_train_y = y_train
        last_fold_feature_meta = list(feature_meta or [])

        for i in range(total):
            p_all.append(float(probs[i]))
            y_all.append(int(y_test[i]))
            p_prior_all.append(float(prior_probs[i]))
            p_logreg_all.append(float(fold_logreg_probs[i]))
            infos_all.append(test_infos[i])
            try:
                profit_all.append(float(_profit_for_info(test_infos[i], lookahead=profit_lookahead)))
            except Exception:
                profit_all.append(0.0)

        last_model = model
        last_meta = feature_meta
        c1, t1, acc1 = _accuracy_from_probs(y_test, probs, threshold=threshold)
        fold_info = {
            "fold": int(used_folds),
            "train_count": len(X_train),
            "val_count": len(X_val),
            "test_count": len(X_test),
            "calibrated": bool(calibrated),
            "calibrate_method": used_calibrate_method,
            "selected_features": list(selected_features or []),
            "dropped_by_importance": int((feature_sel_meta or {}).get("dropped_by_importance", 0) or 0),
            "dropped_by_corr": int((feature_sel_meta or {}).get("dropped_by_corr", 0) or 0),
            "scale_pos_weight": (_scale_pos_weight_from_y(y_train) if bool(use_scale_pos_weight) and model_type in ["xgboost", "lightgbm"] else 1.0),
            "accuracy": float(acc1),
            "valid_count": int(c1),
            "total_count": int(t1),
            "brier_score": _brier_from_probs(y_test, probs),
            "logloss": _logloss_from_probs(y_test, probs),
            "roc_auc": _roc_auc_from_probs(y_test, probs),
            "pr_auc": _pr_auc_from_probs(y_test, probs),
            "train_pos_rate": float(train_pos_rate),
            "feature_ic": fold_ic,
            "baseline_logistic_regression": {
                "trained": bool(logreg_ok),
                "brier_score": _brier_from_probs(y_test, fold_logreg_probs),
                "logloss": _logloss_from_probs(y_test, fold_logreg_probs),
                "roc_auc": _roc_auc_from_probs(y_test, fold_logreg_probs),
                "pr_auc": _pr_auc_from_probs(y_test, fold_logreg_probs),
            },
        }
        folds.append(fold_info)

        train_end += step
        used_folds += 1

    if not y_all or not p_all:
        return None, {
            "status": "error",
            "detail": "insufficient_data"
        }

    total_correct, total_count, acc = _accuracy_from_probs(y_all, p_all, threshold=threshold)
    bins_fixed, ece = _calibration_bins_from_probs(y_all, p_all, bin_count=10)
    bins_quantile = _quantile_bins_from_probs(y_all, p_all, bin_count=quantile_bin_count)
    reversal = _reversal_check_from_probs(y_all, p_all, quantile_bin_count=quantile_bin_count)

    baseline_zero = [0.0 for _ in range(len(p_all))]
    bz_correct, bz_total, bz_acc = _accuracy_from_probs(y_all, baseline_zero, threshold=threshold)
    bp_correct, bp_total, bp_acc = _accuracy_from_probs(y_all, p_prior_all, threshold=threshold)
    lr_correct, lr_total, lr_acc = _accuracy_from_probs(y_all, p_logreg_all, threshold=threshold)

    last_fold_ic = None
    if last_fold_train_X and last_fold_train_y and last_fold_feature_meta:
        last_fold_ic = compute_feature_ic_report(last_fold_train_X, last_fold_train_y, last_fold_feature_meta, include_table=True, top_n=20)

    try:
        tk_frac = float(topk_frac)
    except Exception:
        tk_frac = 0.2
    if tk_frac <= 0:
        tk_frac = 0.2
    if tk_frac > 1:
        tk_frac = 1.0
    topk_n = int(max(1, round(len(p_all) * tk_frac)))
    idxs = list(range(len(p_all)))
    idxs.sort(key=lambda i: float(p_all[i]), reverse=True)
    top10_n = int(max(1, round(len(p_all) * 0.1)))
    top10_idxs = idxs[:top10_n]
    topk_idxs = idxs[:topk_n]
    topk_y = [y_all[i] for i in topk_idxs]
    topk_hit = float(sum(topk_y) / len(topk_y)) if topk_y else 0.0
    top10_y = [y_all[i] for i in top10_idxs]
    top10_hit = float(sum(top10_y) / len(top10_y)) if top10_y else 0.0

    trade_idxs = [i for i in range(len(p_all)) if float(p_all[i]) >= float(threshold)]
    trade_pnls = []
    for i in trade_idxs:
        trade_pnls.append(float(profit_all[i]) - float(trade_cost or 0.0))
    top10_pnls = []
    for i in top10_idxs:
        top10_pnls.append(float(profit_all[i]) - float(trade_cost or 0.0))
    topk_pnls = []
    for i in topk_idxs:
        topk_pnls.append(float(profit_all[i]) - float(trade_cost or 0.0))

    bad_cases = _find_bad_cases(infos_all, p_all, y_all, profit_all, prob_threshold=0.1)
    feat_imp = _get_feature_importance(last_model, last_meta)

    trading = {
        "threshold": {
            "threshold": float(threshold),
            "trade_count": int(len(trade_idxs)),
            "avg_net_profit": float(sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0.0,
            "total_net_profit": float(sum(trade_pnls)) if trade_pnls else 0.0,
            "max_drawdown": _max_drawdown_from_pnls(trade_pnls),
            "win_rate": float(sum(1 for r in trade_pnls if r > 0.0) / len(trade_pnls)) if trade_pnls else 0.0,
        },
        "topk": {
            "topk_frac": float(tk_frac),
            "topk_n": int(topk_n),
            "hit_rate": float(topk_hit),
            "avg_net_profit": float(sum(topk_pnls) / len(topk_pnls)) if topk_pnls else 0.0,
            "total_net_profit": float(sum(topk_pnls)) if topk_pnls else 0.0,
            "max_drawdown": _max_drawdown_from_pnls(topk_pnls),
            "win_rate": float(sum(1 for r in topk_pnls if r > 0.0) / len(topk_pnls)) if topk_pnls else 0.0,
        },
        "top10": {
            "topk_frac": 0.1,
            "topk_n": int(top10_n),
            "hit_rate": float(top10_hit),
            "avg_net_profit": float(sum(top10_pnls) / len(top10_pnls)) if top10_pnls else 0.0,
            "total_net_profit": float(sum(top10_pnls)) if top10_pnls else 0.0,
            "max_drawdown": _max_drawdown_from_pnls(top10_pnls),
            "win_rate": float(sum(1 for r in top10_pnls if r > 0.0) / len(top10_pnls)) if top10_pnls else 0.0,
        },
    }

    brier_val = _brier_from_probs(y_all, p_all)
    roc_auc_val = _roc_auc_from_probs(y_all, p_all)
    pr_auc_val = _pr_auc_from_probs(y_all, p_all)
    logloss_val = _logloss_from_probs(y_all, p_all)
    advice = []
    if roc_auc_val is None:
        advice.append("AUC 无法计算（测试集单一类别），先扩大样本量/拉长年份。")
    else:
        if float(roc_auc_val) < 0.55:
            advice.append("AUC < 0.55：排序能力接近噪声，建议更换特征/因子。")
        elif float(roc_auc_val) >= 0.60:
            advice.append("AUC >= 0.60：有排序能力，优先用 Top10%/TopK 相对排序。")
    if float(brier_val) > 0.25:
        advice.append("Brier > 0.25：概率不可用，避免阈值交易，关注排序。")
    else:
        advice.append("Brier <= 0.25：概率质量可用，可尝试阈值或分桶策略。")

    return {
        "valid_count": int(total_correct),
        "total_count": int(total_count),
        "accuracy": float(acc),
        "method": "walk_forward",
        "train_count": int(folds[-1]["train_count"]) if folds else 0,
        "val_count": int(folds[-1]["val_count"]) if folds else 0,
        "test_count": int(folds[-1]["test_count"]) if folds else 0,
        "oos_test_count": int(len(y_all)),
        "brier_score": brier_val,
        "logloss": logloss_val,
        "roc_auc": roc_auc_val,
        "pr_auc": pr_auc_val,
        "ece": float(ece),
        "prob_summary": _prob_summary_from_probs(p_all),
        "strategy_advice": advice,
        "calibration_bins": bins_fixed,
        "precision_at_0.7": _precision_at_threshold_from_probs(y_all, p_all, threshold=0.7),
        "high_confidence_bins": _high_confidence_bins_from_probs(y_all, p_all),
        "quantile_bins": bins_quantile,
        "monotonic_violations": _monotonic_violations(bins_quantile),
        "reversal_check": reversal,
        "bad_cases": bad_cases,
        "feature_importance": feat_imp,
        "feature_ic": last_fold_ic,
        "selected_features": (list((folds[-1] or {}).get("selected_features") or []) if folds else []),
        "folds": folds,
        "class_balance": {
            "test": _binary_balance_meta(y_all),
        },
        "baseline": {
            "always_negative": {
                "accuracy": float(bz_acc),
                "brier_score": _brier_from_probs(y_all, baseline_zero),
                "logloss": _logloss_from_probs(y_all, baseline_zero),
                "roc_auc": _roc_auc_from_probs(y_all, baseline_zero),
                "pr_auc": _pr_auc_from_probs(y_all, baseline_zero),
            },
            "prior_from_train": {
                "accuracy": float(bp_acc),
                "brier_score": _brier_from_probs(y_all, p_prior_all),
                "logloss": _logloss_from_probs(y_all, p_prior_all),
                "roc_auc": _roc_auc_from_probs(y_all, p_prior_all),
                "pr_auc": _pr_auc_from_probs(y_all, p_prior_all),
            },
            "logistic_regression": {
                "accuracy": float(lr_acc),
                "brier_score": _brier_from_probs(y_all, p_logreg_all),
                "logloss": _logloss_from_probs(y_all, p_logreg_all),
                "roc_auc": _roc_auc_from_probs(y_all, p_logreg_all),
                "pr_auc": _pr_auc_from_probs(y_all, p_logreg_all),
            },
        },
        "trading": trading,
    }, {
        "probs": p_all,
        "y": y_all,
    }

def evaluate_model_accuracy(model, X, y, threshold=0.5):
    if not model or not X or not y:
        return 0, 0, 0.0

    preds = None
    try:
        probs = predict_proba_1(model, X)
        preds = [1 if float(p) >= float(threshold) else 0 for p in probs]
    except Exception:
        try:
            preds = model.predict(X)
            preds = list(map(int, preds))
        except Exception:
            preds = [0 for _ in range(len(X))]

    correct = 0
    total = min(len(preds), len(y))
    for i in range(total):
        if int(preds[i]) == int(y[i]):
            correct += 1

    return correct, total, (correct / total if total > 0 else 0.0)

def train_time_split_backtest(bsp_dict, model_type="xgboost", test_ratio=0.3, val_ratio=0.2, threshold=0.5, calibrate_method="none", min_train=200, min_val=200, min_test=500, n_jobs=-1, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3, use_scale_pos_weight: bool = False, xgb_max_depth: Optional[int] = None, xgb_reg_alpha: Optional[float] = None, xgb_reg_lambda: Optional[float] = None, lgb_max_depth: Optional[int] = None, lgb_reg_alpha: Optional[float] = None, lgb_reg_lambda: Optional[float] = None, backtest_mode: str = "auto", walk_forward_max_folds: int = 20, walk_forward_test_window: Optional[int] = None, walk_forward_val_window: Optional[int] = None, walk_forward_step: Optional[int] = None, trade_cost: float = 0.0, topk_frac: float = 0.2, quantile_bin_count: int = 5):
    empty_model = None
    empty_meta = []
    if not bsp_dict:
        return empty_model, empty_meta, {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "time_split",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": [],
            "quantile_bins": [],
            "monotonic_violations": 0,
            "roc_auc": None,
            "pr_auc": None,
            "logloss": 0.0,
            "baseline": {},
            "trading": {},
            "reversal_check": {},
        }

    infos = list(bsp_dict.values())
    try:
        infos.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
    except Exception:
        pass

    n = len(infos)
    if str(model_type or "").strip().lower() in ["xgboost", "lightgbm"]:
        use_scale_pos_weight = True
    calibrate_method_req = _normalize_calibrate_method(calibrate_method)
    try:
        val_ratio = float(val_ratio)
    except Exception:
        val_ratio = _min_val_fraction()
    val_ratio = max(float(val_ratio), float(_min_val_fraction()))
    try:
        test_ratio = float(test_ratio)
    except Exception:
        test_ratio = 0.3
    if float(test_ratio) <= 0.0:
        test_ratio = 0.3
    if float(val_ratio) + float(test_ratio) >= 0.9:
        test_ratio = max(0.1, 0.9 - float(val_ratio))
    
    # CASE 1: Insufficient Data (< 10 samples)
    if n < 10:
        return empty_model, empty_meta, {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "insufficient_data",
            "train_count": n,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": [],
            "roc_auc": None,
            "pr_auc": None,
            "logloss": 0.0,
            "bad_cases": [],
            "feature_importance": [],
            "baseline": {},
            "trading": {}
        }

    mode = str(backtest_mode or "").strip().lower()
    if mode in ["", "auto"]:
        try:
            desired_test = max(int(min_test), int(n * float(test_ratio)))
        except Exception:
            desired_test = int(min_test)
        desired_test = max(int(desired_test), 1)
        try:
            desired_val = int(n * float(val_ratio)) if float(val_ratio) > 0.0 else 0
        except Exception:
            desired_val = 0
        desired_val = max(int(desired_val), 0)
        desired_train = max(int(min_train), 100)
        mode = "walk_forward" if int(n) >= int(desired_train + desired_val + 2 * desired_test) else "time_split"

    if mode and mode not in ["time_split", "timesplit", "single_split", "single"]:
        wf_info, wf_debug = _walk_forward_eval_infos(
            infos,
            model_type=model_type,
            test_ratio=test_ratio,
            val_ratio=val_ratio,
            threshold=threshold,
            calibrate_method=calibrate_method,
            min_train=min_train,
            min_val=min_val,
            min_test=min_test,
            n_jobs=n_jobs,
            profit_threshold=profit_threshold,
            auto_profit_quantile=auto_profit_quantile,
            profit_lookahead=profit_lookahead,
            use_scale_pos_weight=use_scale_pos_weight,
            xgb_max_depth=xgb_max_depth,
            xgb_reg_alpha=xgb_reg_alpha,
            xgb_reg_lambda=xgb_reg_lambda,
            lgb_max_depth=lgb_max_depth,
            lgb_reg_alpha=lgb_reg_alpha,
            lgb_reg_lambda=lgb_reg_lambda,
            walk_forward_max_folds=walk_forward_max_folds,
            walk_forward_test_window=walk_forward_test_window,
            walk_forward_val_window=walk_forward_val_window,
            walk_forward_step=walk_forward_step,
            trade_cost=trade_cost,
            topk_frac=topk_frac,
            quantile_bin_count=quantile_bin_count,
        )

        feature_meta = build_feature_meta(bsp_dict)
        if profit_threshold is None:
            used_profit_threshold = _auto_profit_threshold_from_infos(infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
        else:
            used_profit_threshold = float(profit_threshold)
        X_all, y_all = build_training_data_from_infos(infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)

        final_model = None
        if X_all and len(set(y_all)) >= 2:
            try:
                final_model = train_model_from_xy(
                    X_all,
                    y_all,
                    model_type=model_type,
                    n_jobs=n_jobs,
                    use_scale_pos_weight=use_scale_pos_weight,
                    xgb_max_depth=xgb_max_depth,
                    xgb_reg_alpha=xgb_reg_alpha,
                    xgb_reg_lambda=xgb_reg_lambda,
                    lgb_max_depth=lgb_max_depth,
                    lgb_reg_alpha=lgb_reg_alpha,
                    lgb_reg_lambda=lgb_reg_lambda,
                )
            except Exception:
                final_model = None

        selected_features = []
        feature_importance_full = []
        if final_model and feature_meta:
            feature_importance_full = _get_feature_importance(final_model, feature_meta)
            topk = 5
            if topk > 0 and len(feature_meta) > topk and feature_importance_full:
                selected_features = [m.get("feature") for m in (feature_importance_full[:topk] or []) if isinstance(m, dict) and m.get("feature")]
                if len(selected_features) >= 2:
                    feature_meta2 = list(selected_features)
                    X_all2, y_all2 = build_training_data_from_infos(infos, feature_meta2, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
                    if X_all2 and len(set(y_all2 or [])) >= 2:
                        try:
                            model2 = train_model_from_xy(
                                X_all2,
                                y_all2,
                                model_type=model_type,
                                n_jobs=n_jobs,
                                use_scale_pos_weight=use_scale_pos_weight,
                                xgb_max_depth=xgb_max_depth,
                                xgb_reg_alpha=xgb_reg_alpha,
                                xgb_reg_lambda=xgb_reg_lambda,
                                lgb_max_depth=lgb_max_depth,
                                lgb_reg_alpha=lgb_reg_alpha,
                                lgb_reg_lambda=lgb_reg_lambda,
                            )
                            if model2:
                                final_model = model2
                                feature_meta = feature_meta2
                                X_all, y_all = X_all2, y_all2
                        except Exception:
                            pass
                    else:
                        selected_features = []

        score_model = final_model
        final_calibrated = False
        final_calibrate_method = None
        if calibrate_method_req is not None and final_model and not isinstance(final_model, SingleClassModel) and X_all and y_all and len(set(y_all)) >= 2:
            n_splits = 3 if len(y_all) >= 120 else (2 if len(y_all) >= 60 else 0)
            if n_splits >= 2:
                chosen_method = calibrate_method_req
                try:
                    scale_pos_weight = 1.0
                    if bool(use_scale_pos_weight) and model_type in ["xgboost", "lightgbm"] and y_all:
                        neg_count, pos_count = _binary_class_counts(y_all)
                        if pos_count > 0:
                            scale_pos_weight = float(neg_count) / float(pos_count)
                    est = build_estimator(model_type=model_type, n_jobs=n_jobs, scale_pos_weight=scale_pos_weight, use_scale_pos_weight=use_scale_pos_weight, xgb_max_depth=xgb_max_depth, xgb_reg_alpha=xgb_reg_alpha, xgb_reg_lambda=xgb_reg_lambda, lgb_max_depth=lgb_max_depth, lgb_reg_alpha=lgb_reg_alpha, lgb_reg_lambda=lgb_reg_lambda)
                    tscv = TimeSeriesSplit(n_splits=n_splits)
                    try:
                        cal = CalibratedClassifierCV(estimator=est, method=chosen_method, cv=tscv)
                    except TypeError:
                        cal = CalibratedClassifierCV(base_estimator=est, method=chosen_method, cv=tscv)
                    cal.fit(X_all, y_all)
                    score_model = cal
                    final_calibrated = True
                    final_calibrate_method = f"{chosen_method}_cv_ts"
                except Exception:
                    score_model = final_model
                    final_calibrated = False
                    final_calibrate_method = None

        if wf_info is None:
            info = {
                "valid_count": 0,
                "total_count": 0,
                "accuracy": 0.0,
                "method": "walk_forward_insufficient_data",
                "train_count": len(X_all),
                "val_count": 0,
                "test_count": 0,
                "oos_test_count": 0,
                "brier_score": 0.0,
                "ece": 0.0,
                "roc_auc": None,
                "pr_auc": None,
                "logloss": 0.0,
                "calibration_bins": [],
                "quantile_bins": [],
                "monotonic_violations": 0,
                "folds": [],
                "baseline": {},
                "trading": {},
                "detail": wf_debug,
            }
        else:
            info = dict(wf_info or {})
            info["method"] = f"walk_forward_calibrated_{final_calibrate_method}" if final_calibrated and final_calibrate_method else ("walk_forward_calibrated" if final_calibrated else "walk_forward_uncalibrated")
            info["final_model_calibrated"] = bool(final_calibrated)
            info["final_model_calibrate_method"] = final_calibrate_method
            info["profit_threshold"] = float(used_profit_threshold)
            info["profit_lookahead"] = int(profit_lookahead or 0)
            info["selected_features"] = list(selected_features or [])
            if feature_importance_full:
                info["feature_importance_full"] = feature_importance_full
            
            if final_model:
                info["feature_importance"] = _get_feature_importance(final_model, feature_meta)

            if info.get("brier_score", 0.0) > 0.25:
                info["warning"] = f"High Brier Score ({info.get('brier_score', 0.0):.4f} > 0.25). Model has weak predictive power. Do not use for trading."

        return score_model, feature_meta, info

    if mode in ["time_split", "timesplit", "single_split", "single"]:
        test_ratio = 0.3

    if n < (min_train + min_val + min_test):
        feature_meta = build_feature_meta(bsp_dict)
        return None, feature_meta, {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "insufficient_data",
            "train_count": n,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": [],
            "detail": f"need>={int(min_train) + int(min_val) + int(min_test)} samples for train/val/test; got n={int(n)}",
        }

    train_end = int(n * (1 - val_ratio - test_ratio))
    train_end = max(min_train, min(train_end, n - (min_val + min_test)))
    val_end = int(n * (1 - test_ratio))
    val_end = max(train_end + min_val, min(val_end, n - min_test))

    train_infos = infos[:train_end]
    val_infos = infos[train_end:val_end]
    test_infos = infos[val_end:]

    feature_meta = build_feature_meta(bsp_dict)
    if profit_threshold is None:
        used_profit_threshold = _auto_profit_threshold_from_infos(train_infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
    else:
        used_profit_threshold = float(profit_threshold)
    X_train, y_train = build_training_data_from_infos(train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    feature_ic_full = compute_feature_ic_report(X_train, y_train, feature_meta, include_table=True, top_n=20)

    class_balance = {
        "train": _binary_balance_meta(y_train),
        "val": _binary_balance_meta(y_val),
        "test": _binary_balance_meta(y_test),
    }
    scale_pos_weight = 1.0
    if model_type in ["xgboost", "lightgbm"]:
        scale_pos_weight = _scale_pos_weight_from_y(y_train)

    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return None, feature_meta, {
            "valid_count": 0,
            "total_count": len(y_test),
            "accuracy": 0.0,
            "method": "single_class_split",
            "train_count": len(X_train),
            "val_count": len(X_val),
            "test_count": len(X_test),
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": [],
            "class_balance": class_balance,
            "scale_pos_weight": scale_pos_weight,
        }

    selected_features = []
    feature_importance_full = []
    feature_sel_meta = {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
    model = None
    try:
        model, feature_meta2, feature_sel_meta = train_model_with_feature_selection(
            X_train,
            y_train,
            feature_meta,
            model_type=model_type,
            n_jobs=n_jobs,
            use_scale_pos_weight=use_scale_pos_weight,
            xgb_max_depth=xgb_max_depth,
            xgb_reg_alpha=xgb_reg_alpha,
            xgb_reg_lambda=xgb_reg_lambda,
            lgb_max_depth=lgb_max_depth,
            lgb_reg_alpha=lgb_reg_alpha,
            lgb_reg_lambda=lgb_reg_lambda,
            X_val=X_val,
            y_val=y_val,
        )
        if feature_meta2:
            feature_meta = list(feature_meta2)
            selected_features = list(feature_meta2)
            X_train, y_train = build_training_data_from_infos(train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
            X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
            X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
            class_balance = {
                "train": _binary_balance_meta(y_train),
                "val": _binary_balance_meta(y_val),
                "test": _binary_balance_meta(y_test),
            }
            if model_type in ["xgboost", "lightgbm"]:
                scale_pos_weight = _scale_pos_weight_from_y(y_train)
    except Exception:
        model = None
        feature_sel_meta = {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
    if model and feature_meta:
        feature_importance_full = _get_feature_importance(model, feature_meta)
    feature_ic_selected = compute_feature_ic_report(X_train, y_train, feature_meta, include_table=False, top_n=20)

    calibrated = False
    score_model = model
    used_calibrate_method = None
    if model and not isinstance(model, SingleClassModel):
        X_cal = list(X_train or [])
        y_cal = list(y_train or [])
        if X_val and y_val:
            X_cal.extend(X_val)
            y_cal.extend(y_val)

        if calibrate_method_req is not None and X_val and y_val and len(set(y_val)) >= 2:
            try:
                chosen_method = calibrate_method_req
                try:
                    cal = CalibratedClassifierCV(estimator=model, method=chosen_method, cv="prefit")
                except TypeError:
                    cal = CalibratedClassifierCV(base_estimator=model, method=chosen_method, cv="prefit")
                cal.fit(X_val, y_val)
                score_model = cal
                calibrated = True
                used_calibrate_method = chosen_method
            except Exception:
                score_model = model
                calibrated = False
                used_calibrate_method = None

        if calibrate_method_req is not None and (not calibrated) and X_cal and y_cal and len(set(y_cal)) >= 2:
            n_splits = 3 if len(y_cal) >= 120 else (2 if len(y_cal) >= 60 else 0)
            if n_splits >= 2:
                chosen_method = calibrate_method_req
                try:
                    scale_pos_weight = 1.0
                    if bool(use_scale_pos_weight) and model_type in ["xgboost", "lightgbm"] and y_cal:
                        neg_count, pos_count = _binary_class_counts(y_cal)
                        if pos_count > 0:
                            scale_pos_weight = float(neg_count) / float(pos_count)

                    est = build_estimator(model_type=model_type, n_jobs=n_jobs, scale_pos_weight=scale_pos_weight, use_scale_pos_weight=use_scale_pos_weight, xgb_max_depth=xgb_max_depth, xgb_reg_alpha=xgb_reg_alpha, xgb_reg_lambda=xgb_reg_lambda, lgb_max_depth=lgb_max_depth, lgb_reg_alpha=lgb_reg_alpha, lgb_reg_lambda=lgb_reg_lambda)
                    tscv = TimeSeriesSplit(n_splits=n_splits)
                    try:
                        cal = CalibratedClassifierCV(estimator=est, method=chosen_method, cv=tscv)
                    except TypeError:
                        cal = CalibratedClassifierCV(base_estimator=est, method=chosen_method, cv=tscv)
                    cal.fit(X_cal, y_cal)
                    score_model = cal
                    calibrated = True
                    used_calibrate_method = f"{chosen_method}_cv_ts"
                except Exception:
                    score_model = model
                    calibrated = False
                    used_calibrate_method = None

    probs = []
    try:
        probs = predict_proba_1(score_model, X_test) if score_model and X_test and y_test else []
    except Exception:
        probs = []

    valid_count, total_count, acc = _accuracy_from_probs(y_test, probs, threshold=threshold)

    bins, ece = _calibration_bins_from_probs(y_test, probs, bin_count=10)
    qbins = _quantile_bins_from_probs(y_test, probs, bin_count=quantile_bin_count)
    reversal = _reversal_check_from_probs(y_test, probs, quantile_bin_count=quantile_bin_count)

    neg_c, pos_c = _binary_class_counts(y_train)
    train_pos_rate = (float(pos_c) / float(neg_c + pos_c)) if (neg_c + pos_c) > 0 else 0.0
    baseline_zero = [0.0 for _ in range(len(y_test or []))]
    baseline_prior = [float(train_pos_rate) for _ in range(len(y_test or []))]
    bz_correct, bz_total, bz_acc = _accuracy_from_probs(y_test, baseline_zero, threshold=threshold)
    bp_correct, bp_total, bp_acc = _accuracy_from_probs(y_test, baseline_prior, threshold=threshold)
    logreg_probs_raw = _logistic_regression_baseline_probs(X_train, y_train, X_test)
    logreg_trained = bool(logreg_probs_raw) and len(logreg_probs_raw) >= len(y_test or [])
    logreg_probs = logreg_probs_raw if logreg_trained else list(baseline_prior)
    lr_correct, lr_total, lr_acc = _accuracy_from_probs(y_test, logreg_probs, threshold=threshold)

    profit_test = []
    for info in test_infos:
        try:
            profit_test.append(float(_profit_for_info(info, lookahead=profit_lookahead)))
        except Exception:
            profit_test.append(0.0)

    bad_cases = _find_bad_cases(test_infos, probs, y_test, profit_test, prob_threshold=0.1)
    feat_imp = _get_feature_importance(score_model, feature_meta)


    try:
        tk_frac = float(topk_frac)
    except Exception:
        tk_frac = 0.2
    if tk_frac <= 0:
        tk_frac = 0.2
    if tk_frac > 1:
        tk_frac = 1.0
    topk_n = int(max(1, round(len(probs) * tk_frac))) if probs else 0
    idxs = list(range(len(probs)))
    idxs.sort(key=lambda i: float(probs[i]), reverse=True)
    top10_n = int(max(1, round(len(probs) * 0.1))) if probs else 0
    top10_idxs = idxs[:top10_n] if top10_n > 0 else []
    topk_idxs = idxs[:topk_n] if topk_n > 0 else []
    topk_y = [y_test[i] for i in topk_idxs] if topk_idxs else []
    topk_hit = float(sum(topk_y) / len(topk_y)) if topk_y else 0.0
    top10_y = [y_test[i] for i in top10_idxs] if top10_idxs else []
    top10_hit = float(sum(top10_y) / len(top10_y)) if top10_y else 0.0

    trade_idxs = [i for i in range(len(probs)) if float(probs[i]) >= float(threshold)] if probs else []
    trade_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in trade_idxs] if trade_idxs else []
    top10_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in top10_idxs] if top10_idxs else []
    topk_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in topk_idxs] if topk_idxs else []

    brier_val = _brier_from_probs(y_test, probs)
    warning_msg = None
    if brier_val > 0.25:
        warning_msg = f"High Brier Score ({brier_val:.4f} > 0.25). Model has weak predictive power. Do not use for trading."

    roc_auc_val = _roc_auc_from_probs(y_test, probs)
    pr_auc_val = _pr_auc_from_probs(y_test, probs)
    logloss_val = _logloss_from_probs(y_test, probs)
    advice = []
    if roc_auc_val is None:
        advice.append("AUC 无法计算（测试集单一类别），先扩大样本量/拉长年份。")
    else:
        if float(roc_auc_val) < 0.55:
            advice.append("AUC < 0.55：排序能力接近噪声，建议更换特征/因子。")
        elif float(roc_auc_val) >= 0.60:
            advice.append("AUC >= 0.60：有排序能力，优先用 Top10%/TopK 相对排序。")
    if float(brier_val) > 0.25:
        advice.append("Brier > 0.25：概率不可用，避免阈值交易，关注排序。")
    else:
        advice.append("Brier <= 0.25：概率质量可用，可尝试阈值或分桶策略。")

    return score_model, feature_meta, {
        "valid_count": valid_count,
        "total_count": total_count,
        "accuracy": acc,
        "method": f"time_split_calibrated_{used_calibrate_method}" if calibrated and used_calibrate_method else ("time_split_calibrated" if calibrated else "time_split_uncalibrated"),
        "train_count": len(X_train),
        "val_count": len(X_val),
        "test_count": len(X_test),
        "brier_score": brier_val,
        "warning": warning_msg,
        "logloss": logloss_val,
        "roc_auc": roc_auc_val,
        "pr_auc": pr_auc_val,
        "ece": ece,
        "prob_summary": _prob_summary_from_probs(probs),
        "strategy_advice": advice,
        "calibration_bins": bins,
        "precision_at_0.7": _precision_at_threshold_from_probs(y_test, probs, threshold=0.7),
        "high_confidence_bins": _high_confidence_bins_from_probs(y_test, probs),
        "quantile_bins": qbins,
        "monotonic_violations": _monotonic_violations(qbins),
        "reversal_check": reversal,
        "bad_cases": bad_cases,
        "feature_importance": feat_imp,
        "feature_importance_full": feature_importance_full,
        "feature_ic": feature_ic_full,
        "feature_ic_selected": feature_ic_selected,
        "selected_features": selected_features,
        "class_balance": class_balance,
        "scale_pos_weight": (scale_pos_weight if bool(use_scale_pos_weight) else 1.0),
        "baseline": {
            "always_negative": {
                "accuracy": float(bz_acc),
                "brier_score": _brier_from_probs(y_test, baseline_zero),
                "logloss": _logloss_from_probs(y_test, baseline_zero),
                "roc_auc": _roc_auc_from_probs(y_test, baseline_zero),
                "pr_auc": _pr_auc_from_probs(y_test, baseline_zero),
            },
            "prior_from_train": {
                "accuracy": float(bp_acc),
                "brier_score": _brier_from_probs(y_test, baseline_prior),
                "logloss": _logloss_from_probs(y_test, baseline_prior),
                "roc_auc": _roc_auc_from_probs(y_test, baseline_prior),
                "pr_auc": _pr_auc_from_probs(y_test, baseline_prior),
                "train_pos_rate": float(train_pos_rate),
            },
            "logistic_regression": {
                "trained": bool(logreg_trained),
                "accuracy": float(lr_acc),
                "brier_score": _brier_from_probs(y_test, logreg_probs),
                "logloss": _logloss_from_probs(y_test, logreg_probs),
                "roc_auc": _roc_auc_from_probs(y_test, logreg_probs),
                "pr_auc": _pr_auc_from_probs(y_test, logreg_probs),
            },
        },
        "trading": {
            "threshold": {
                "threshold": float(threshold),
                "trade_count": int(len(trade_idxs)),
                "avg_net_profit": float(sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0.0,
                "total_net_profit": float(sum(trade_pnls)) if trade_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(trade_pnls),
                "win_rate": float(sum(1 for r in trade_pnls if r > 0.0) / len(trade_pnls)) if trade_pnls else 0.0,
            },
            "topk": {
                "topk_frac": float(tk_frac),
                "topk_n": int(topk_n),
                "hit_rate": float(topk_hit),
                "avg_net_profit": float(sum(topk_pnls) / len(topk_pnls)) if topk_pnls else 0.0,
                "total_net_profit": float(sum(topk_pnls)) if topk_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(topk_pnls),
                "win_rate": float(sum(1 for r in topk_pnls if r > 0.0) / len(topk_pnls)) if topk_pnls else 0.0,
            },
            "top10": {
                "topk_frac": 0.1,
                "topk_n": int(top10_n),
                "hit_rate": float(top10_hit),
                "avg_net_profit": float(sum(top10_pnls) / len(top10_pnls)) if top10_pnls else 0.0,
                "total_net_profit": float(sum(top10_pnls)) if top10_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(top10_pnls),
                "win_rate": float(sum(1 for r in top10_pnls if r > 0.0) / len(top10_pnls)) if top10_pnls else 0.0,
            },
        },
    }

def compute_time_split_accuracy(bsp_dict, model_type="xgboost", test_ratio=0.3, threshold=0.5, min_train=200, min_test=500, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3):
    if not bsp_dict:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "time_split",
            "train_count": 0,
            "test_count": 0
        }

    _, _, info = train_time_split_backtest(
        bsp_dict,
        model_type=model_type,
        test_ratio=test_ratio,
        val_ratio=_min_val_fraction(),
        threshold=threshold,
        calibrate_method="none",
        min_train=min_train,
        min_val=1,
        min_test=min_test,
        profit_threshold=profit_threshold,
        auto_profit_quantile=auto_profit_quantile,
        profit_lookahead=profit_lookahead,
    )
    return info

def compute_recent_accuracy(bsp_dict, model_type="xgboost", recent_years: float = 1.0, threshold: float = 0.5, calibrate_method: str = "none", min_train: int = 200, min_val: int = 200, min_test: int = 500, n_jobs: int = -1, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3):
    if not bsp_dict:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "recent_empty",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0
        }

    infos = list(bsp_dict.values())
    try:
        infos.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
    except Exception:
        pass

    if not infos:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "recent_empty",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0
        }

    anchor_ts = getattr(infos[-1].get("open_time"), "ts", 0) or 0
    try:
        years = float(recent_years)
    except Exception:
        years = 1.0
    if years <= 0:
        years = 1.0
    cutoff_ts = int(anchor_ts) - int(years * 365 * 24 * 3600)

    train_infos = []
    test_infos = []
    for info in infos:
        ts = getattr(info.get("open_time"), "ts", 0) or 0
        if int(ts) >= int(cutoff_ts):
            test_infos.append(info)
        else:
            train_infos.append(info)

    if len(train_infos) < int(min_train) or len(test_infos) < int(min_test):
        return {
            "valid_count": 0,
            "total_count": len(test_infos),
            "accuracy": 0.0,
            "method": "recent_insufficient_data",
            "train_count": len(train_infos),
            "val_count": 0,
            "test_count": len(test_infos)
        }

    feature_meta = build_feature_meta(bsp_dict)
    if profit_threshold is None:
        used_profit_threshold = _auto_profit_threshold_from_infos(train_infos, q=auto_profit_quantile, min_threshold=0.0, profit_lookahead=profit_lookahead)
    else:
        used_profit_threshold = float(profit_threshold)

    val_size = max(1, int(round(len(train_infos) * float(_min_val_fraction()))))
    val_size = max(int(min_val), val_size)
    val_size = min(val_size, max(1, len(train_infos) - int(min_train)))

    base_train_infos = train_infos[:-val_size] if val_size > 0 else train_infos
    val_infos = train_infos[-val_size:] if val_size > 0 else []

    X_train, y_train = build_training_data_from_infos(base_train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)

    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return {
            "valid_count": 0,
            "total_count": len(y_test),
            "accuracy": 0.0,
            "method": "recent_single_class_split",
            "train_count": len(X_train),
            "val_count": len(X_val),
            "test_count": len(X_test)
        }

    use_scale_pos_weight = str(model_type or "").strip().lower() in ["xgboost", "lightgbm"]
    selected_features = []
    feature_sel_meta = {"selected_features": [], "dropped_by_importance": 0, "dropped_by_corr": 0}
    model = None
    try:
        model, feature_meta2, feature_sel_meta = train_model_with_feature_selection(
            X_train,
            y_train,
            feature_meta,
            model_type=model_type,
            n_jobs=n_jobs,
            use_scale_pos_weight=use_scale_pos_weight,
            X_val=X_val,
            y_val=y_val,
        )
        if feature_meta2:
            feature_meta = list(feature_meta2)
            selected_features = list(feature_meta2)
            X_train, y_train = build_training_data_from_infos(base_train_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
            X_val, y_val = build_training_data_from_infos(val_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
            X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=used_profit_threshold, profit_lookahead=profit_lookahead)
    except Exception:
        model = None

    if not model:
        return {
            "valid_count": 0,
            "total_count": len(y_test),
            "accuracy": 0.0,
            "method": "recent_train_failed",
            "train_count": len(X_train),
            "val_count": len(X_val),
            "test_count": len(X_test)
        }

    score_model = model
    calibrated = False
    used_calibrate_method = ""
    chosen_method = _normalize_calibrate_method(calibrate_method)

    if chosen_method is not None and X_val and y_val and len(set(y_val)) >= 2:
        try:
            try:
                cal = CalibratedClassifierCV(estimator=model, method=str(chosen_method), cv="prefit")
            except TypeError:
                cal = CalibratedClassifierCV(base_estimator=model, method=str(chosen_method), cv="prefit")
            cal.fit(X_val, y_val)
            score_model = cal
            calibrated = True
            used_calibrate_method = str(chosen_method)
        except Exception:
            score_model = model

    valid_count, total_count, acc = evaluate_model_accuracy(score_model, X_test, y_test, threshold=threshold)
    return {
        "valid_count": valid_count,
        "total_count": total_count,
        "accuracy": acc,
        "method": f"recent_cutoff_calibrated_{used_calibrate_method}" if calibrated and used_calibrate_method else ("recent_cutoff_calibrated" if calibrated else "recent_cutoff_uncalibrated"),
        "train_count": len(X_train),
        "val_count": len(X_val),
        "test_count": len(X_test),
        "brier_score": brier_score(score_model, X_test, y_test),
        "selected_features": list(selected_features or []),
        "dropped_by_importance": int((feature_sel_meta or {}).get("dropped_by_importance", 0) or 0),
        "dropped_by_corr": int((feature_sel_meta or {}).get("dropped_by_corr", 0) or 0),
    }

def get_or_train_model(_bsp_dict, _chan, model_type="xgboost", profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3):
    # Prepare data for XGBoost
    if not _bsp_dict:
        return None, None

    feature_meta = build_feature_meta(_bsp_dict)
    X_train, y_train, feature_meta = build_training_data(_bsp_dict, feature_meta, profit_threshold=profit_threshold, auto_profit_quantile=auto_profit_quantile, profit_lookahead=profit_lookahead)
        
    print(f"Training data size: {len(X_train)}")
    print(f"Class distribution: {set(y_train)}")
    
    if not X_train:
        print("Skipping training: No data.")
        return None, feature_meta
        
    if len(set(y_train)) < 2:
        print(f"Skipping training: Single class detected. Classes: {set(y_train)}")
        # Return a dummy model that always predicts this class
        return SingleClassModel(list(set(y_train))[0]), feature_meta
        
    print(f"Training model: {model_type}")
    
    try:
        use_scale_pos_weight = str(model_type or "").strip().lower() in ["xgboost", "lightgbm"]
        model, feature_meta2, _ = train_model_with_feature_selection(
            X_train,
            y_train,
            feature_meta,
            model_type=model_type,
            use_scale_pos_weight=use_scale_pos_weight,
        )
        if feature_meta2:
            feature_meta = list(feature_meta2)
    except Exception as e:
        print(f"Model training failed: {e}")
        return None, feature_meta
    
    return model, feature_meta

def predict_bsp(model, bsp, feature_meta):
    if not model or not bsp:
        print("Prediction skipped: Model or BSP is None")
        return 0.0
        
    feat_vec = [bsp.features.get(k, -9999999) for k in feature_meta]
    # Predict prob
    try:
        probs = model.predict_proba([feat_vec])
        # probs is [[prob_0, prob_1]]
        # If model only has one class, probs might be [[1.0]] (not standard sklearn behavior but possible)
        # Sklearn classifiers usually return probability for all classes in model.classes_
        
        # print(f"Model raw probabilities: {probs}")

        # Check shape
        if len(probs[0]) >= 2:
            return probs[0][1]
        elif len(probs[0]) == 1:
            # If only one probability returned, check model.classes_
            # But standard sklearn predict_proba returns columns for all classes.
            # If we trained with 2 classes, it returns 2 columns.
            
            # If using SingleClassModel, it handles it correctly.
            # If sklearn model somehow has 1 class (should be caught by get_or_train_model check), handle gracefully.
            val = probs[0][0]
            # If the only class is 1, return val. If 0, return 1-val? No, predict_proba usually corresponds to classes_.
            # Since we can't easily access classes_ from Pipeline easily without digging, let's assume get_or_train_model handles single class.
            return val 
            
        return 0.0
    except Exception as e:
        print(f"Prediction failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 0.0

def download_stock_history(code, frequency='1d'):
    import csv
    import io
    import datetime

    code = normalize_code(code)
    bs.login()
    
    freq_map = {
        "1d": "d",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "60m": "60",
        "1m": "1",
    }
    bs_freq = freq_map.get(frequency, "d")
    
    # Fields: standard OHLCV + others
    fields = "date,time,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg"
    
    rs = bs.query_history_k_data_plus(
        code=code,
        fields=fields,
        start_date='1990-01-01',
        end_date=datetime.datetime.now().strftime("%Y-%m-%d"),
        frequency=bs_freq,
        adjustflag="2" # QFQ
    )
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(fields.split(','))
    
    while (rs.error_code == '0') & rs.next():
        writer.writerow(rs.get_row_data())
        
    bs.logout()
    return output.getvalue()

MODEL_CACHE_VERSION = 1

def _get_model_cache_dir():
    cache_dir = os.environ.get("MLCHAN_MODEL_CACHE_DIR")
    if cache_dir:
        return cache_dir
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "model_cache")

def _sanitize_model_key_part(s: str) -> str:
    if s is None:
        return "none"
    out = str(s).strip().lower()
    out = out.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
    return out

def build_pretrained_model_key(model_type: str, frequency: str, data_src: str) -> str:
    # Generate a unique key every time to allow multiple versions
    parts = [
        _sanitize_model_key_part(model_type),
        _sanitize_model_key_part(frequency),
        _sanitize_model_key_part(data_src),
        f"v{MODEL_CACHE_VERSION}",
        uuid.uuid4().hex,  # Add uniqueness
    ]
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def _pretrained_model_paths_by_key(key: str):
    cache_dir = _get_model_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    bundle_path = os.path.join(cache_dir, f"{key}.pkl.gz")
    meta_path = os.path.join(cache_dir, f"{key}.json")
    return bundle_path, meta_path

def save_pretrained_model_bundle(model, feature_meta, model_type: str, frequency: str, data_src: str, meta: dict, model_key: Optional[str] = None):
    if model_key is None:
        # Use the unified key builder which is now unique
        model_key = build_pretrained_model_key(model_type, frequency, data_src)
    trained_at = datetime.datetime.utcnow().isoformat() + "Z"
    bundle_path, meta_path = _pretrained_model_paths_by_key(model_key)

    bundle = {
        "version": MODEL_CACHE_VERSION,
        "key": model_key,
        "model_type": model_type,
        "frequency": frequency,
        "data_src": data_src,
        "trained_at": trained_at,
        "feature_meta": feature_meta,
        "model": model,
        "meta": meta or {},
    }
    with gzip.open(bundle_path, "wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    safe_meta = {
        "version": MODEL_CACHE_VERSION,
        "key": model_key,
        "model_type": model_type,
        "frequency": frequency,
        "data_src": data_src,
        "bundle_path": bundle_path,
        "trained_at": trained_at,
        "meta": meta or {},
        "feature_count": len(feature_meta or []),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(safe_meta, f, ensure_ascii=False, sort_keys=True, indent=2)
    return bundle_path, model_key, trained_at

def list_pretrained_model_metas(limit: Optional[int] = 200):
    cache_dir = _get_model_cache_dir()
    if not os.path.isdir(cache_dir):
        return []
    items = []
    try:
        for name in os.listdir(cache_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(cache_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    if "key" not in meta:
                        meta["key"] = os.path.splitext(os.path.basename(path))[0]
                    items.append(meta)
            except Exception:
                continue
    except Exception:
        return []

    def sort_key(m):
        ts = m.get("trained_at", "") or ""
        return ts

    items.sort(key=sort_key, reverse=True)
    if limit is None:
        return items
    return items[: int(limit)]

def get_pretrained_model_detail_by_key(key: str):
    if not key:
        return None
    key = str(key).strip()
    if len(key) != 32:
        return None
    for ch in key:
        if ch not in "0123456789abcdef":
            return None
    cache_dir = _get_model_cache_dir()
    meta_path = os.path.join(cache_dir, f"{key}.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            return None
        meta.setdefault("key", key)
        return meta
    except Exception:
        return None

def delete_pretrained_model_bundle_by_key(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        return {"status": "not_found", "key": str(key or "")}

    bundle_path, meta_path = _pretrained_model_paths_by_key(str(key).strip())
    removed_meta = False
    removed_bundle = False
    missing = []

    try:
        if os.path.exists(meta_path):
            os.remove(meta_path)
            removed_meta = True
        else:
            missing.append(meta_path)
    except Exception:
        pass

    try:
        bp = str(meta.get("bundle_path") or bundle_path)
        if bp and os.path.exists(bp):
            os.remove(bp)
            removed_bundle = True
        else:
            if bp:
                missing.append(bp)
    except Exception:
        pass

    if not removed_bundle:
        try:
            if os.path.exists(bundle_path):
                os.remove(bundle_path)
                removed_bundle = True
        except Exception:
            pass

    return {
        "status": "success",
        "key": str(key).strip(),
        "removed_meta": removed_meta,
        "removed_bundle": removed_bundle,
        "missing": missing,
    }

def load_pretrained_model_bundle(model_type: str, frequency: str, data_src: str, autype: Optional[object] = None):
    items = list_pretrained_model_metas(limit=None)
    mt = str(model_type or "")
    freq = str(frequency or "")
    src = str(data_src or "")
    want_autype = None
    if autype is not None:
        try:
            if isinstance(autype, AUTYPE):
                want_autype = str(getattr(autype, "name", "") or "").strip().upper() or None
            else:
                raw = str(autype or "").strip().lower()
                if raw == "hfq":
                    want_autype = "HFQ"
                elif raw == "qfq":
                    want_autype = "QFQ"
                elif raw == "none":
                    want_autype = "NONE"
                else:
                    want_autype = str(autype or "").strip().upper() or None
        except Exception:
            want_autype = None

    candidates = []
    for m in items or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("model_type") or "") != mt:
            continue
        if str(m.get("frequency") or "") != freq:
            continue
        if str(m.get("data_src") or "") != src:
            continue
        if int(m.get("version") or 0) != int(MODEL_CACHE_VERSION):
            continue
        try:
            if int(m.get("feature_count") or 0) <= 0:
                continue
        except Exception:
            continue
        if want_autype:
            try:
                meta = m.get("meta", {}) if isinstance(m.get("meta", {}), dict) else {}
                got = str(meta.get("autype") or "").strip().upper()
                if got and got != want_autype:
                    continue
            except Exception:
                pass
        candidates.append(m)

    if not candidates and want_autype:
        for m in items or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("model_type") or "") != mt:
                continue
            if str(m.get("frequency") or "") != freq:
                continue
            if str(m.get("data_src") or "") != src:
                continue
            if int(m.get("version") or 0) != int(MODEL_CACHE_VERSION):
                continue
            try:
                if int(m.get("feature_count") or 0) <= 0:
                    continue
            except Exception:
                continue
            candidates.append(m)

    if not candidates:
        return None
    key = str(candidates[0].get("key") or "").strip()
    if not key:
        return None
    return load_pretrained_model_bundle_by_key(key)

def load_pretrained_model_bundle_by_key(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        return None
    bundle_path = meta.get("bundle_path")
    if not bundle_path or not os.path.exists(bundle_path):
        return None
    with gzip.open(bundle_path, "rb") as f:
        bundle = pickle.load(f)
    if not isinstance(bundle, dict):
        return None
    if bundle.get("version") != MODEL_CACHE_VERSION:
        return None
    if bundle.get("key") and str(bundle.get("key")) != str(key):
        return None
    fm = bundle.get("feature_meta")
    if not isinstance(fm, list) or len(fm) <= 0:
        return None
    return bundle

def _get_feature_importance(model, feature_names):
    importances = []
    if not model or not feature_names:
        return importances
    
    vals = None
    try:
        # XGBoost / LGBM / Sklearn
        if hasattr(model, "feature_importances_"):
            vals = model.feature_importances_
        elif hasattr(model, "coef_"):
            vals = model.coef_[0]
    except Exception:
        pass
        
    if vals is not None and len(vals) == len(feature_names):
        combined = []
        for i, name in enumerate(feature_names):
            combined.append({"feature": name, "importance": float(vals[i])})
        combined.sort(key=lambda x: abs(x["importance"]), reverse=True)
        importances = combined[:50] # Top 50
        
    return importances

def _find_bad_cases(infos, probs, y_true, profits, prob_threshold=0.1):
    bad_cases = []
    if not infos or not probs or not y_true:
        return bad_cases
    n = min(len(infos), len(probs), len(y_true))
    for i in range(n):
        p = float(probs[i])
        y = int(y_true[i])
        if p <= prob_threshold and y == 1:
            info = infos[i]
            profit = 0.0
            if profits and i < len(profits):
                profit = profits[i]
            else:
                try:
                    profit = float(info.get("profit", 0.0) or 0.0)
                except Exception:
                    profit = 0.0
            
            bad_cases.append({
                "code": str(info.get("code") or ""),
                "open_time": getattr(info.get("open_time"), "ts", 0),
                "open_date": str(getattr(info.get("open_time"), "date", "")),
                "prob": p,
                "label": y,
                "profit": profit
            })
    # Sort by profit descending (biggest misses)
    bad_cases.sort(key=lambda x: x["profit"], reverse=True)
    return bad_cases[:20] # Return top 20

def evaluate_fixed_model_time_split(bsp_dict, model, feature_meta, test_ratio=0.2, threshold=0.5, min_test=500, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3, trade_cost: float = 0.0, topk_frac: float = 0.2, quantile_bin_count: int = 5):
    if not bsp_dict or not model or not feature_meta:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "pretrained_fixed_empty",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": [],
            "roc_auc": None,
            "pr_auc": None,
            "logloss": 0.0,
            "baseline": {},
            "trading": {}
        }

    infos = list(bsp_dict.values())
    try:
        infos.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
    except Exception:
        pass

    n = len(infos)
    if n <= 1:
        X_all, y_all = build_training_data_from_infos(infos, feature_meta, profit_threshold=profit_threshold, auto_profit_quantile=auto_profit_quantile, profit_lookahead=profit_lookahead)
        probs = predict_proba_1(model, X_all) if X_all and y_all else []
        valid_count, total_count, acc = _accuracy_from_probs(y_all, probs, threshold=threshold)
        return {
            "valid_count": valid_count,
            "total_count": total_count,
            "accuracy": acc,
            "method": "pretrained_fixed_all_data",
            "train_count": 0,
            "val_count": 0,
            "test_count": len(X_all),
            "brier_score": _brier_from_probs(y_all, probs),
            "ece": (_calibration_bins_from_probs(y_all, probs, bin_count=10)[1] if probs else 0.0),
            "calibration_bins": (_calibration_bins_from_probs(y_all, probs, bin_count=10)[0] if probs else []),
            "roc_auc": _roc_auc_from_probs(y_all, probs),
            "pr_auc": _pr_auc_from_probs(y_all, probs),
            "logloss": _logloss_from_probs(y_all, probs),
            "baseline": {},
            "trading": {}
        }

    test_size = max(int(n * test_ratio), min_test)
    test_size = min(test_size, n - 1)
    split = n - test_size
    split = max(1, split)
    train_infos = infos[:split]
    test_infos = infos[split:]

    X_test, y_test = build_training_data_from_infos(test_infos, feature_meta, profit_threshold=profit_threshold, auto_profit_quantile=auto_profit_quantile, profit_lookahead=profit_lookahead)
    probs = predict_proba_1(model, X_test) if X_test and y_test else []
    valid_count, total_count, acc = _accuracy_from_probs(y_test, probs, threshold=threshold)

    profit_test = []
    for info in test_infos:
        try:
            profit_test.append(float(_profit_for_info(info, lookahead=profit_lookahead)))
        except Exception:
            profit_test.append(0.0)

    bins, ece = _calibration_bins_from_probs(y_test, probs, bin_count=10)
    qbins = _quantile_bins_from_probs(y_test, probs, bin_count=quantile_bin_count)
    reversal = _reversal_check_from_probs(y_test, probs, quantile_bin_count=quantile_bin_count)
    bad_cases = _find_bad_cases(test_infos, probs, y_test, profit_test, prob_threshold=0.1)
    feat_imp = _get_feature_importance(model, feature_meta)

    X_train, y_train = build_training_data_from_infos(train_infos, feature_meta, profit_threshold=profit_threshold, auto_profit_quantile=auto_profit_quantile, profit_lookahead=profit_lookahead)
    neg_c, pos_c = _binary_class_counts(y_train)
    train_pos_rate = (float(pos_c) / float(neg_c + pos_c)) if (neg_c + pos_c) > 0 else 0.0
    baseline_zero = [0.0 for _ in range(len(y_test or []))]
    baseline_prior = [float(train_pos_rate) for _ in range(len(y_test or []))]
    bz_correct, bz_total, bz_acc = _accuracy_from_probs(y_test, baseline_zero, threshold=threshold)
    bp_correct, bp_total, bp_acc = _accuracy_from_probs(y_test, baseline_prior, threshold=threshold)

    profit_test = []
    for info in test_infos:
        try:
            profit_test.append(float(_profit_for_info(info, lookahead=profit_lookahead)))
        except Exception:
            profit_test.append(0.0)

    try:
        tk_frac = float(topk_frac)
    except Exception:
        tk_frac = 0.2
    if tk_frac <= 0:
        tk_frac = 0.2
    if tk_frac > 1:
        tk_frac = 1.0
    topk_n = int(max(1, round(len(probs) * tk_frac))) if probs else 0
    idxs = list(range(len(probs)))
    idxs.sort(key=lambda i: float(probs[i]), reverse=True)
    top10_n = int(max(1, round(len(probs) * 0.1))) if probs else 0
    top10_idxs = idxs[:top10_n] if top10_n > 0 else []
    topk_idxs = idxs[:topk_n] if topk_n > 0 else []
    topk_y = [y_test[i] for i in topk_idxs] if topk_idxs else []
    topk_hit = float(sum(topk_y) / len(topk_y)) if topk_y else 0.0
    top10_y = [y_test[i] for i in top10_idxs] if top10_idxs else []
    top10_hit = float(sum(top10_y) / len(top10_y)) if top10_y else 0.0

    trade_idxs = [i for i in range(len(probs)) if float(probs[i]) >= float(threshold)] if probs else []
    trade_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in trade_idxs] if trade_idxs else []
    top10_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in top10_idxs] if top10_idxs else []
    topk_pnls = [(float(profit_test[i]) - float(trade_cost or 0.0)) for i in topk_idxs] if topk_idxs else []

    brier_val = _brier_from_probs(y_test, probs)
    roc_auc_val = _roc_auc_from_probs(y_test, probs)
    pr_auc_val = _pr_auc_from_probs(y_test, probs)
    logloss_val = _logloss_from_probs(y_test, probs)
    advice = []
    if roc_auc_val is None:
        advice.append("AUC 无法计算（测试集单一类别），先扩大样本量/拉长年份。")
    else:
        if float(roc_auc_val) < 0.55:
            advice.append("AUC < 0.55：排序能力接近噪声，建议更换特征/因子。")
        elif float(roc_auc_val) >= 0.60:
            advice.append("AUC >= 0.60：有排序能力，优先用 Top10%/TopK 相对排序。")
    if float(brier_val) > 0.25:
        advice.append("Brier > 0.25：概率不可用，避免阈值交易，关注排序。")
    else:
        advice.append("Brier <= 0.25：概率质量可用，可尝试阈值或分桶策略。")

    return {
        "valid_count": valid_count,
        "total_count": total_count,
        "accuracy": acc,
        "method": "pretrained_fixed_time_split",
        "train_count": 0,
        "val_count": 0,
        "test_count": len(X_test),
        "brier_score": brier_val,
        "ece": ece,
        "prob_summary": _prob_summary_from_probs(probs),
        "strategy_advice": advice,
        "calibration_bins": bins,
        "precision_at_0.7": _precision_at_threshold_from_probs(y_test, probs, threshold=0.7),
        "high_confidence_bins": _high_confidence_bins_from_probs(y_test, probs),
        "quantile_bins": qbins,
        "monotonic_violations": _monotonic_violations(qbins),
        "reversal_check": reversal,
        "bad_cases": bad_cases,
        "feature_importance": feat_imp,
        "roc_auc": roc_auc_val,
        "pr_auc": pr_auc_val,
        "logloss": logloss_val,
        "baseline": {
            "always_negative": {
                "accuracy": float(bz_acc),
                "brier_score": _brier_from_probs(y_test, baseline_zero),
                "logloss": _logloss_from_probs(y_test, baseline_zero),
                "roc_auc": _roc_auc_from_probs(y_test, baseline_zero),
                "pr_auc": _pr_auc_from_probs(y_test, baseline_zero),
            },
            "prior_from_train": {
                "accuracy": float(bp_acc),
                "brier_score": _brier_from_probs(y_test, baseline_prior),
                "logloss": _logloss_from_probs(y_test, baseline_prior),
                "roc_auc": _roc_auc_from_probs(y_test, baseline_prior),
                "pr_auc": _pr_auc_from_probs(y_test, baseline_prior),
                "train_pos_rate": float(train_pos_rate),
            },
        },
        "trading": {
            "threshold": {
                "threshold": float(threshold),
                "trade_count": int(len(trade_idxs)),
                "avg_net_profit": float(sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0.0,
                "total_net_profit": float(sum(trade_pnls)) if trade_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(trade_pnls),
                "win_rate": float(sum(1 for r in trade_pnls if r > 0.0) / len(trade_pnls)) if trade_pnls else 0.0,
            },
            "topk": {
                "topk_frac": float(tk_frac),
                "topk_n": int(topk_n),
                "hit_rate": float(topk_hit),
                "avg_net_profit": float(sum(topk_pnls) / len(topk_pnls)) if topk_pnls else 0.0,
                "total_net_profit": float(sum(topk_pnls)) if topk_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(topk_pnls),
                "win_rate": float(sum(1 for r in topk_pnls if r > 0.0) / len(topk_pnls)) if topk_pnls else 0.0,
            },
            "top10": {
                "topk_frac": 0.1,
                "topk_n": int(top10_n),
                "hit_rate": float(top10_hit),
                "avg_net_profit": float(sum(top10_pnls) / len(top10_pnls)) if top10_pnls else 0.0,
                "total_net_profit": float(sum(top10_pnls)) if top10_pnls else 0.0,
                "max_drawdown": _max_drawdown_from_pnls(top10_pnls),
                "win_rate": float(sum(1 for r in top10_pnls if r > 0.0) / len(top10_pnls)) if top10_pnls else 0.0,
            },
        }
    }

def _process_code_for_pretrain(args):
    profit_threshold = 0.01
    profit_lookahead = 5
    autype = AUTYPE.HFQ
    if isinstance(args, (list, tuple)) and len(args) >= 6:
        code, level, begin_time, data_src_type, profit_threshold, profit_lookahead = args[:6]
        if len(args) >= 7:
            autype = args[6]
    elif isinstance(args, (list, tuple)) and len(args) >= 5:
        code, level, begin_time, data_src_type, profit_threshold = args[:5]
        if len(args) >= 6:
            autype = args[5]
    else:
        code, level, begin_time, data_src_type = args
        if isinstance(args, (list, tuple)) and len(args) >= 5:
            autype = args[4]
    code = normalize_code(code)
    try:
        if not isinstance(autype, AUTYPE):
            raw = str(autype or "").strip()
            if raw:
                raw = raw.upper()
                if raw in AUTYPE.__members__:
                    autype = AUTYPE[raw]
                else:
                    autype = AUTYPE.HFQ
            else:
                autype = AUTYPE.HFQ
    except Exception:
        autype = AUTYPE.HFQ
    try:
        kl_list = fetch_stock_data(code, level, begin_time, None, data_src_type, autype=autype)
        if not kl_list:
            return code, None, [], set()
            
        latest_time = str(kl_list[-1].time)
        
        _, bsp_dict, _, _ = get_chan_data(
            code=code,
            trigger_step=True,
            bi_strict=True,
            level=level,
            data_src_type=data_src_type,
            preloaded_data=kl_list,
            do_predict=True,
            begin_time=begin_time,
            autype=autype,
        )
        
        local_samples = []
        feature_keys = set()
        
        if bsp_dict:
            for info in bsp_dict.values():
                bsp_obj = info.get("bsp_obj", None)
                if not bsp_obj or not getattr(bsp_obj, "klu", None):
                    continue
                decision_klu = info.get("decision_klu", None)
                cur_klu = decision_klu if decision_klu is not None else bsp_obj.klu
                future_klu = cur_klu
                for _ in range(int(profit_lookahead or 0)):
                    nxt = getattr(future_klu, "next", None)
                    if nxt:
                        future_klu = nxt
                    else:
                        break
                try:
                    cur_close = float(cur_klu.close)
                    future_close = float(future_klu.close)
                except Exception:
                    continue
                if not cur_close:
                    continue

                is_buy = bool(info.get("is_buy", False))
                profit = (future_close - cur_close) / cur_close if is_buy else (cur_close - future_close) / cur_close
                try:
                    used_profit_threshold = None if profit_threshold is None else float(profit_threshold)
                except Exception:
                    used_profit_threshold = 0.01
                label = None if used_profit_threshold is None else (1 if float(profit) > used_profit_threshold else 0)

                feat = info.get("feature", None)
                feat_dict = {}
                try:
                    if feat is None:
                        feat_dict = {}
                    elif isinstance(feat, dict):
                        feat_dict = dict(feat)
                    elif hasattr(feat, "items"):
                        feat_dict = dict(feat.items())
                    else:
                        feat_dict = dict(feat)
                except Exception:
                    feat_dict = {}
                
                if feat_dict:
                    for k in feat_dict.keys():
                        feature_keys.add(k)

                open_ts = 0
                try:
                    open_ts = int(getattr(info.get("open_time", None), "ts", 0) or 0)
                except Exception:
                    open_ts = 0
                local_samples.append({"open_ts": open_ts, "feature": feat_dict, "profit": float(profit), "label": label})
        
        return code, latest_time, local_samples, feature_keys
    except Exception as e:
        print(f"Error processing {code}: {e}")
        return code, None, [], set()

def pretrain_and_persist_model(codes, level, data_src_type, begin_time, model_type="xgboost", frequency="1d", data_src="clickhouse", calibrate_method="none", progress_cb=None, pool_name=None, profit_threshold: Optional[float] = 0.02, auto_profit_quantile: float = 0.7, profit_lookahead: int = 3, autype: AUTYPE = AUTYPE.HFQ):
    if not codes:
        return None, {
            "status": "error",
            "detail": "codes is required"
        }

    def report(stage: str, progress: float, message=None, extra=None):
        if not progress_cb:
            return
        payload = {"stage": stage, "progress": float(progress)}
        if message is not None:
            payload["message"] = str(message)
        if extra:
            payload.update(extra)
        try:
            progress_cb(payload)
        except Exception:
            pass

    per_code_latest_time = {}
    per_code_sample_count = {}
    samples = []
    feature_keys = set()

    total = max(len(codes), 1)
    report("collecting", 0.0, "start")

    try:
        # Optimized for server environment: Use ProcessPoolExecutor for parallel data processing
        # This utilizes the multiple cores (e.g. 64 cores) efficiently
        # Logic: Env Var -> min(60, CPU) -> bound by len(codes)
        
        default_workers = min(60, os.cpu_count() or 4)
        env_workers = os.environ.get("MLCHAN_PRETRAIN_WORKERS")
        if env_workers:
            try:
                max_workers = int(env_workers)
            except ValueError:
                max_workers = default_workers
        else:
            max_workers = default_workers
            
        # Ensure at least 1 and not more than tasks
        max_workers = max(1, min(max_workers, len(codes)))
        
        print(f"Starting pretrain data collection with {max_workers} workers for {len(codes)} codes.")
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            autype_name = None
            try:
                autype_name = autype.name if isinstance(autype, AUTYPE) else str(autype or "").strip().upper()
            except Exception:
                autype_name = "HFQ"
            if not autype_name:
                autype_name = "HFQ"

            tasks = [(code, level, begin_time, data_src_type, profit_threshold, profit_lookahead, autype_name) for code in codes]
            future_to_code = {executor.submit(_process_code_for_pretrain, t): t[0] for t in tasks}
            
            completed_count = 0
            for future in concurrent.futures.as_completed(future_to_code):
                completed_count += 1
                orig_code = future_to_code[future]
                report("collecting", min(0.8, 0.05 + 0.75 * (completed_count / total)), f"{completed_count}/{total} {orig_code}")
                
                try:
                    r_code, r_latest, r_samples, r_keys = future.result()
                    if r_latest:
                        per_code_latest_time[r_code] = r_latest
                    if r_samples:
                        samples.extend(r_samples)
                        per_code_sample_count[r_code] = len(r_samples)
                    if r_keys:
                        feature_keys.update(r_keys)
                except Exception as e:
                    print(f"Worker exception for {orig_code}: {e}")

        if not samples:
            return None, {
                "status": "error",
                "detail": "no training samples collected"
            }

        report("training", 0.85, "training")

        feature_meta = sorted(feature_keys)
        samples.sort(key=lambda s: int(s.get("open_ts", 0) or 0))

        def build_xy(rows, used_profit_threshold: float):
            X = []
            y = []
            for r in rows:
                feat = r.get("feature", {}) or {}
                X.append([feat.get(k, -9999999) for k in feature_meta])
                if "profit" in r and r.get("profit") is not None:
                    try:
                        y.append(1 if float(r.get("profit")) > float(used_profit_threshold) else 0)
                    except Exception:
                        y.append(int(r.get("label", 0) or 0))
                else:
                    y.append(int(r.get("label", 0) or 0))
            return X, y

        n = len(samples)
        test_ratio = 0.2
        val_ratio = 0.2
        threshold = 0.5
        min_train = 50
        min_val = 200
        min_test = 500
        used_profit_threshold = None

        if n < (min_train + min_val + min_test):
            if profit_threshold is None:
                profits = []
                for r in samples:
                    if r.get("profit") is None:
                        continue
                    try:
                        profits.append(float(r.get("profit")))
                    except Exception:
                        continue
                used_profit_threshold = max(0.0, _quantile(profits, auto_profit_quantile)) if profits else 0.0
            else:
                used_profit_threshold = float(profit_threshold)
            X_all, y_all = build_xy(samples, used_profit_threshold)
            model = None
            try:
                use_scale_pos_weight = str(model_type or "").strip().lower() in ["xgboost", "lightgbm"]
                model = train_model_from_xy(X_all, y_all, model_type=model_type, use_scale_pos_weight=use_scale_pos_weight)
            except Exception:
                model = None
            valid_count, total_count, acc = evaluate_model_accuracy(model, X_all, y_all, threshold=threshold)
            accuracy_info = {
                "valid_count": valid_count,
                "total_count": total_count,
                "accuracy": acc,
                "method": "fallback_all_data",
                "train_count": len(X_all),
                "val_count": 0,
                "test_count": 0,
                "brier_score": brier_score(model, X_all, y_all),
                "ece": 0.0,
                "calibration_bins": []
            }
        else:
            train_end = int(n * (1 - val_ratio - test_ratio))
            train_end = max(min_train, min(train_end, n - (min_val + min_test)))
            val_end = int(n * (1 - test_ratio))
            val_end = max(train_end + min_val, min(val_end, n - min_test))

            train_rows = samples[:train_end]
            val_rows = samples[train_end:val_end]
            test_rows = samples[val_end:]

            if profit_threshold is None:
                profits = []
                for r in train_rows:
                    if r.get("profit") is None:
                        continue
                    try:
                        profits.append(float(r.get("profit")))
                    except Exception:
                        continue
                used_profit_threshold = max(0.0, _quantile(profits, auto_profit_quantile)) if profits else 0.0
            else:
                used_profit_threshold = float(profit_threshold)

            X_train, y_train = build_xy(train_rows, used_profit_threshold)
            X_val, y_val = build_xy(val_rows, used_profit_threshold)
            X_test, y_test = build_xy(test_rows, used_profit_threshold)

            model = None
            try:
                use_scale_pos_weight = str(model_type or "").strip().lower() in ["xgboost", "lightgbm"]
                model = train_model_from_xy(X_train, y_train, model_type=model_type, use_scale_pos_weight=use_scale_pos_weight)
            except Exception:
                model = None

            calibrated = False
            score_model = model
            used_calibrate_method = None
            if model and not isinstance(model, SingleClassModel):
                calibrate_method_req = _normalize_calibrate_method(calibrate_method)
                X_cal = list(X_train or [])
                y_cal = list(y_train or [])
                if X_val and y_val:
                    X_cal.extend(X_val)
                    y_cal.extend(y_val)

                if calibrate_method_req is not None and X_val and y_val and len(set(y_val)) >= 2:
                    try:
                        chosen_method = calibrate_method_req
                        try:
                            cal = CalibratedClassifierCV(estimator=model, method=chosen_method, cv="prefit")
                        except TypeError:
                            cal = CalibratedClassifierCV(base_estimator=model, method=chosen_method, cv="prefit")
                        cal.fit(X_val, y_val)
                        score_model = cal
                        calibrated = True
                        used_calibrate_method = chosen_method
                    except Exception:
                        score_model = model
                        calibrated = False
                        used_calibrate_method = None

                if calibrate_method_req is not None and (not calibrated) and X_cal and y_cal and len(set(y_cal)) >= 2:
                    n_splits = 3 if len(y_cal) >= 120 else (2 if len(y_cal) >= 60 else 0)
                    if n_splits >= 2:
                        chosen_method = calibrate_method_req
                        try:
                            scale_pos_weight = 1.0
                            if model_type in ["xgboost", "lightgbm"] and y_cal:
                                neg_count, pos_count = _binary_class_counts(y_cal)
                                if pos_count > 0:
                                    scale_pos_weight = float(neg_count) / float(pos_count)
                            est = build_estimator(model_type=model_type, n_jobs=-1, scale_pos_weight=scale_pos_weight)
                            tscv = TimeSeriesSplit(n_splits=n_splits)
                            try:
                                cal = CalibratedClassifierCV(estimator=est, method=chosen_method, cv=tscv)
                            except TypeError:
                                cal = CalibratedClassifierCV(base_estimator=est, method=chosen_method, cv=tscv)
                            cal.fit(X_cal, y_cal)
                            score_model = cal
                            calibrated = True
                            used_calibrate_method = f"{chosen_method}_cv_ts"
                        except Exception:
                            score_model = model
                            calibrated = False
                            used_calibrate_method = None

            valid_count, total_count, acc = evaluate_model_accuracy(score_model, X_test, y_test, threshold=threshold)

            bins = []
            ece = 0.0
            if score_model and X_test and y_test:
                probs = predict_proba_1(score_model, X_test)
                total_p = min(len(probs), len(y_test))
                if total_p > 0:
                    bin_count = 10
                    agg = [{"count": 0, "sum_p": 0.0, "sum_y": 0.0} for _ in range(bin_count)]
                    for i in range(total_p):
                        p = float(probs[i])
                        yv = float(y_test[i])
                        bi = int(p * bin_count)
                        if bi >= bin_count:
                            bi = bin_count - 1
                        if bi < 0:
                            bi = 0
                        agg[bi]["count"] += 1
                        agg[bi]["sum_p"] += p
                        agg[bi]["sum_y"] += yv
                    for i in range(bin_count):
                        c = agg[i]["count"]
                        low = i / bin_count
                        high = (i + 1) / bin_count
                        if c > 0:
                            avg_p = agg[i]["sum_p"] / c
                            win_rate = agg[i]["sum_y"] / c
                            ece += abs(avg_p - win_rate) * (c / total_p)
                        else:
                            avg_p = 0.0
                            win_rate = 0.0
                        bins.append({
                            "low": low,
                            "high": high,
                            "count": c,
                            "avg_pred": avg_p,
                            "win_rate": win_rate
                        })

            accuracy_info = {
                "valid_count": valid_count,
                "total_count": total_count,
                "accuracy": acc,
                "method": f"time_split_calibrated_{used_calibrate_method}" if calibrated and used_calibrate_method else ("time_split_calibrated" if calibrated else "time_split_uncalibrated"),
                "train_count": len(X_train),
                "val_count": len(X_val),
                "test_count": len(X_test),
                "brier_score": brier_score(score_model, X_test, y_test),
                "ece": ece,
                "calibration_bins": bins,
                "class_balance": {
                    "train": _binary_balance_meta(y_train),
                    "val": _binary_balance_meta(y_val),
                    "test": _binary_balance_meta(y_test),
                },
                "scale_pos_weight": (_scale_pos_weight_from_y(y_train) if model_type in ["xgboost", "lightgbm"] else 1.0),
            }

            model = score_model

        if not model:
            return None, {
                "status": "error",
                "detail": "model training failed"
            }

        meta = {
            "accuracy": accuracy_info,
            "begin_time": begin_time,
            "autype": (autype.name if isinstance(autype, AUTYPE) else str(autype or "")),
            "codes": list(per_code_latest_time.keys()),
            "per_code_latest_time": per_code_latest_time,
            "per_code_sample_count": per_code_sample_count,
            "sample_count": len(samples),
            "pool_name": pool_name,
            "profit_threshold": used_profit_threshold,
            "profit_lookahead": int(profit_lookahead or 0),
        }
        report("saving", 0.95, "saving")
        path, model_key, trained_at = save_pretrained_model_bundle(
            model=model,
            feature_meta=feature_meta,
            model_type=model_type,
            frequency=frequency,
            data_src=data_src,
            meta=meta
        )
        report("done", 1.0, "done")
        return path, {
            "status": "success",
            "bundle_path": path,
            "model_key": model_key,
            "model_type": model_type,
            "frequency": frequency,
            "data_src": data_src,
            "feature_count": len(feature_meta),
            "sample_count": len(samples),
            "accuracy": accuracy_info,
            "codes": meta["codes"],
            "per_code_sample_count": per_code_sample_count,
            "trained_at": trained_at,
        }
    finally:
        try:
            samples.clear()
        except Exception:
            pass
        try:
            feature_keys.clear()
        except Exception:
            pass
        gc.collect()
