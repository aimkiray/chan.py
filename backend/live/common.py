import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE


def parse_autype(s: str) -> AUTYPE:
    raw = str(s or "qfq").strip().lower()
    if raw == "qfq":
        return AUTYPE.QFQ
    if raw == "hfq":
        return AUTYPE.HFQ
    if raw in ["none", "no", "off"]:
        return AUTYPE.NONE
    return AUTYPE.QFQ


def parse_data_src(s: str) -> DATA_SRC:
    raw = str(s or "clickhouse").strip().lower()
    if raw == "clickhouse":
        return DATA_SRC.CLICK_HOUSE
    if raw == "baostock":
        return DATA_SRC.BAO_STOCK
    if raw == "akshare":
        return DATA_SRC.AK_SHARE
    if raw == "csv":
        return DATA_SRC.CSV
    if raw == "ccxt":
        return DATA_SRC.CCXT
    return DATA_SRC.CLICK_HOUSE


def parse_frequency(s: str) -> Tuple[str, KL_TYPE]:
    raw = str(s or "30m").strip().lower()
    mp = {
        "1d": KL_TYPE.K_DAY,
        "30m": KL_TYPE.K_30M,
        "60m": KL_TYPE.K_60M,
        "15m": KL_TYPE.K_15M,
        "5m": KL_TYPE.K_5M,
        "1m": KL_TYPE.K_1M,
        "1w": KL_TYPE.K_WEEK,
        "1mo": KL_TYPE.K_MON,
    }
    return raw, mp.get(raw, KL_TYPE.K_30M)


def day_key_from_ts(ts: int) -> Optional[int]:
    try:
        dt = datetime.datetime.fromtimestamp(int(ts))
    except Exception:
        return None
    return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)


def compute_volatility_250(kl_list) -> float:
    if not kl_list:
        return 0.0
    last = list(kl_list)[-250:]
    lows = []
    highs = []
    for k in last:
        try:
            lo = float(getattr(k, "low", 0.0) or 0.0)
            hi = float(getattr(k, "high", 0.0) or 0.0)
        except Exception:
            lo, hi = 0.0, 0.0
        if lo > 0 and hi > 0:
            lows.append(lo)
            highs.append(hi)
    if not lows or not highs:
        return 0.0
    low_250 = float(min(lows))
    high_250 = float(max(highs))
    if low_250 <= 0:
        return 0.0
    return float((high_250 - low_250) / low_250)


_INDEX_RISK_CACHE: Dict[tuple, dict] = {}


def _tr(high: float, low: float, prev_close: float) -> float:
    return float(max(high - low, abs(high - prev_close), abs(low - prev_close)))


def _cn_day_key_from_ts(ts: int) -> Optional[int]:
    try:
        dt = datetime.datetime.fromtimestamp(int(ts))
    except Exception:
        return None
    return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)


def intraday_plunge_risk(
    current_price: Optional[float],
    prev_close: Optional[float],
    ma_value: Optional[float],
    bear_threshold: float = -0.015,
    bull_threshold: float = -0.03,
) -> dict:
    try:
        cp = float(current_price) if current_price is not None else None
    except Exception:
        cp = None
    try:
        pc = float(prev_close) if prev_close is not None else None
    except Exception:
        pc = None
    try:
        ma = float(ma_value) if ma_value is not None else None
    except Exception:
        ma = None

    if cp is None or pc is None or ma is None or pc <= 0.0 or cp <= 0.0:
        return {
            "flat": False,
            "trend": None,
            "change": None,
            "threshold": None,
            "current_price": cp,
            "prev_close": pc,
            "ma": ma,
        }

    change = float(cp / pc - 1.0)
    bear = bool(cp < ma)
    threshold = float(bear_threshold) if bear else float(bull_threshold)
    flat = bool(change <= threshold)
    return {
        "flat": bool(flat),
        "trend": "bear" if bear else "bull",
        "change": float(change),
        "threshold": float(threshold),
        "current_price": float(cp),
        "prev_close": float(pc),
        "ma": float(ma),
    }


def index_risk_from_daily_kl(
    kl_list,
    now_ts: int,
    atr_window: int = 14,
    drop_threshold: float = -0.03,
    atr_pct_threshold: float = 0.04,
    flat_drop_threshold: float = -0.05,
    flat_atr_pct_threshold: float = 0.06,
    gap_down_threshold: Optional[float] = None,
    ma_window: int = 20,
    mode: str = "both",
) -> dict:
    raw_items = list(kl_list or [])
    if len(raw_items) < 3:
        return {
            "level": "normal",
            "day": 0,
            "ref_day": 0,
            "ret1d": None,
            "atr_pct": None,
            "gap_open_pct": None,
            "today_open": None,
            "prev_close": None,
            "ma_window": int(ma_window or 20),
            "ma20": None,
            "mode": str(mode or ""),
        }

    by_day: Dict[int, dict] = {}
    for k in raw_items:
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        dk = _cn_day_key_from_ts(ts)
        if dk is None:
            continue
        try:
            hi = float(getattr(k, "high", 0.0) or 0.0)
            lo = float(getattr(k, "low", 0.0) or 0.0)
            c = float(getattr(k, "close", 0.0) or 0.0)
            o = float(getattr(k, "open", 0.0) or 0.0)
        except Exception:
            continue
        if c <= 0.0:
            continue
        if o <= 0.0:
            o = c
        if hi <= 0.0:
            hi = c
        if lo <= 0.0:
            lo = c
        dd = by_day.get(int(dk))
        if dd is None:
            dd = {
                "day": int(dk),
                "open": float(o),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "ts_first": int(ts),
                "ts_last": int(ts),
            }
            by_day[int(dk)] = dd
        else:
            dd["high"] = float(max(float(dd["high"]), float(hi)))
            dd["low"] = float(min(float(dd["low"]), float(lo)))
            if int(ts) <= int(dd.get("ts_first") or 0):
                dd["ts_first"] = int(ts)
                dd["open"] = float(o)
            if int(ts) >= int(dd.get("ts_last") or 0):
                dd["ts_last"] = int(ts)
                dd["close"] = float(c)

    items = [by_day[k] for k in sorted(by_day.keys())]
    if len(items) < 3:
        return {
            "level": "normal",
            "day": 0,
            "ref_day": 0,
            "ret1d": None,
            "atr_pct": None,
            "gap_open_pct": None,
            "today_open": None,
            "prev_close": None,
            "mode": str(mode or ""),
        }

    now_dk = _cn_day_key_from_ts(int(now_ts or 0)) or 0
    today_rec = None
    if now_dk:
        for it in reversed(items):
            if int(it.get("day") or 0) == int(now_dk):
                today_rec = it
                break
    hist = [it for it in items if int(it.get("day") or 0) < int(now_dk)] if now_dk else list(items)
    if len(hist) < 3:
        return {
            "level": "normal",
            "day": int(now_dk or 0),
            "ref_day": 0,
            "ret1d": None,
            "atr_pct": None,
            "gap_open_pct": None,
            "today_open": None,
            "prev_close": None,
            "ma_window": int(ma_window or 20),
            "ma20": None,
            "mode": str(mode or ""),
        }

    last = hist[-1]
    prev = hist[-2]
    day = int(now_dk or last.get("day") or 0)
    ref_day = int(last.get("day") or 0)

    try:
        c0 = float(prev.get("close") or 0.0)
        c1 = float(last.get("close") or 0.0)
    except Exception:
        c0, c1 = 0.0, 0.0
    ret1d = None
    if c0 > 0.0 and c1 > 0.0:
        ret1d = float(c1 / c0 - 1.0)

    try:
        win = max(2, int(atr_window or 14))
    except Exception:
        win = 14
    trs = []
    start = max(1, len(hist) - win)
    for i in range(start, len(hist)):
        k = hist[i]
        kp = hist[i - 1]
        try:
            hi = float(k.get("high") or 0.0)
            lo = float(k.get("low") or 0.0)
            pc = float(kp.get("close") or 0.0)
            cc = float(k.get("close") or 0.0)
        except Exception:
            continue
        if pc <= 0.0:
            continue
        if hi > 0.0 and lo > 0.0:
            trs.append(_tr(hi, lo, pc))
        elif cc > 0.0:
            trs.append(abs(float(cc) - float(pc)))
    atr_pct = None
    if trs and c1 > 0.0:
        atr_pct = float((sum(trs) / float(len(trs))) / c1)

    raw_mode = str(mode or "").strip().lower()
    use_drop = raw_mode in ["both", "drop", "ret", "return"]
    use_atr = raw_mode in ["both", "atr", "vol", "volatility"]

    gap_open_pct = None
    today_open = None
    prev_close = float(c1) if c1 and c1 > 0.0 else None
    if today_rec is not None:
        try:
            today_open = float(today_rec.get("open") or 0.0)
        except Exception:
            today_open = None
    if (
        gap_down_threshold is not None
        and prev_close is not None
        and today_open is not None
        and float(prev_close) > 0.0
        and float(today_open) > 0.0
    ):
        gap_open_pct = float(float(today_open) / float(prev_close) - 1.0)

    ma20 = None
    try:
        w = int(ma_window or 20)
    except Exception:
        w = 20
    if len(hist) >= max(2, w):
        try:
            tail = [float(it.get("close") or 0.0) for it in hist[-w:]]
            tail = [x for x in tail if x and x > 0.0]
            if len(tail) == w:
                ma20 = float(sum(tail) / float(w))
        except Exception:
            ma20 = None

    flat = False
    reduce = False
    if use_drop and ret1d is not None:
        if float(ret1d) <= float(flat_drop_threshold):
            flat = True
        elif float(ret1d) <= float(drop_threshold):
            reduce = True
    if use_atr and atr_pct is not None:
        if float(atr_pct) >= float(flat_atr_pct_threshold):
            flat = True
        elif float(atr_pct) >= float(atr_pct_threshold):
            reduce = True if not flat else reduce

    level = "normal"
    if gap_open_pct is not None and gap_down_threshold is not None:
        if float(gap_open_pct) <= float(gap_down_threshold):
            level = "flat"
            return {
                "level": level,
                "day": int(day or 0),
                "ref_day": int(ref_day or 0),
                "ret1d": ret1d,
                "atr_pct": atr_pct,
                "gap_open_pct": gap_open_pct,
                "today_open": today_open,
                "prev_close": prev_close,
                "ma_window": int(w),
                "ma20": ma20,
                "gap_down_threshold": float(gap_down_threshold),
                "mode": str(raw_mode),
            }
    if flat:
        level = "flat"
    elif reduce:
        level = "reduce"
    return {
        "level": level,
        "day": int(day or 0),
        "ref_day": int(ref_day or 0),
        "ret1d": ret1d,
        "atr_pct": atr_pct,
        "gap_open_pct": gap_open_pct,
        "today_open": today_open,
        "prev_close": prev_close,
        "ma_window": int(w),
        "ma20": ma20,
        "gap_down_threshold": float(gap_down_threshold) if gap_down_threshold is not None else None,
        "mode": str(raw_mode),
    }


def get_cached_index_risk(
    cache_key: tuple,
    loader: Callable[[], Any],
    now_ts: int,
    atr_window: int = 14,
    drop_threshold: float = -0.03,
    atr_pct_threshold: float = 0.04,
    flat_drop_threshold: float = -0.05,
    flat_atr_pct_threshold: float = 0.06,
    gap_down_threshold: Optional[float] = None,
    ma_window: int = 20,
    mode: str = "both",
) -> dict:
    ck = tuple(cache_key or ())
    cached = _INDEX_RISK_CACHE.get(ck)
    if isinstance(cached, dict):
        return cached
    kl_list = loader() if loader is not None else None
    out = index_risk_from_daily_kl(
        kl_list,
        now_ts=now_ts,
        atr_window=atr_window,
        drop_threshold=drop_threshold,
        atr_pct_threshold=atr_pct_threshold,
        flat_drop_threshold=flat_drop_threshold,
        flat_atr_pct_threshold=flat_atr_pct_threshold,
        gap_down_threshold=gap_down_threshold,
        ma_window=int(ma_window or 20),
        mode=mode,
    )
    if len(_INDEX_RISK_CACHE) > 64:
        _INDEX_RISK_CACHE.clear()
    _INDEX_RISK_CACHE[ck] = dict(out or {})
    return out
