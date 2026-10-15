import datetime
import math
import sys
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE


def _warn_fallback(kind: str, raw: str, fallback: str) -> None:
    # Silent fallback used to swallow parameter typos (e.g. --data_src clikhouse
    # ran against ClickHouse anyway); warn so the wrong value stays observable.
    print(f"[live.common] unknown {kind} {raw!r}; falling back to {fallback!r}", file=sys.stderr)


def parse_autype(s: str) -> AUTYPE:
    raw = str(s or "qfq").strip().lower()
    if raw == "qfq":
        return AUTYPE.QFQ
    if raw == "hfq":
        return AUTYPE.HFQ
    if raw in ["none", "no", "off"]:
        return AUTYPE.NONE
    if raw:
        _warn_fallback("autype", raw, "qfq")
    return AUTYPE.QFQ


def parse_data_src(s: str) -> DATA_SRC:
    raw = str(s or "clickhouse").strip().lower()
    if raw == "clickhouse":
        return DATA_SRC.CLICK_HOUSE
    if raw == "baostock":
        return DATA_SRC.BAO_STOCK
    if raw == "akshare":
        raise ValueError("akshare data_src is disabled")
    if raw in ["tdx", "tongdaxin"]:
        return DATA_SRC.TDX
    if raw == "csv":
        return DATA_SRC.CSV
    if raw == "ccxt":
        return DATA_SRC.CCXT
    if raw:
        _warn_fallback("data_src", raw, "clickhouse")
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
    if raw and raw not in mp:
        _warn_fallback("frequency", raw, "30m")
    return raw, mp.get(raw, KL_TYPE.K_30M)


def cn_dt_from_ts(ts: int) -> Optional[datetime.datetime]:
    """CN civil (UTC+8) datetime for a unix ts; None when ts is invalid.

    Never fabricates "now": an unreadable timestamp must stay visibly absent
    rather than silently re-anchor downstream day aggregation to today.
    """
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)
    except Exception:
        return None


def cn_day_key_from_ts(ts: int) -> Optional[int]:
    # int() coercion lives inside cn_dt_from_ts's try — don't pre-convert here
    # or a non-numeric ts escapes before the guard.
    dt = cn_dt_from_ts(ts)
    if dt is None:
        return None
    return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)


def day_key_from_ts(ts: int) -> Optional[int]:
    """Trading-day key on the CN (UTC+8) calendar.

    Same convention as cn_day_key_from_ts — kept under this shorter name for
    existing callers (paper_trade.py, backend.main) so cur_day and the index
    risk aggregation share one timezone convention instead of splitting
    local-tz vs UTC+8 on non-CST hosts.
    """
    return cn_day_key_from_ts(ts)


def parse_hhmm_to_minutes(v: Optional[str], default_min: int = 10 * 60) -> int:
    raw = str(v or "").strip()
    if not raw:
        return int(default_min)
    try:
        parts = raw.split(":")
        hh = int(parts[0]) if parts and parts[0].strip() else 10
        mm = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
        hh = max(0, min(23, int(hh)))
        mm = max(0, min(59, int(mm)))
        return int(hh * 60 + mm)
    except Exception:
        return int(default_min)


def cn_ts_from_day_key_minutes(day_key: int, minute_of_day: int) -> Optional[int]:
    try:
        s = str(int(day_key))
    except Exception:
        return None
    if len(s) != 8 or (not s.isdigit()):
        return None
    try:
        y = int(s[0:4])
        m = int(s[4:6])
        d = int(s[6:8])
    except Exception:
        return None
    try:
        mm = int(minute_of_day)
    except Exception:
        mm = 10 * 60
    mm = max(0, min(23 * 60 + 59, int(mm)))
    hh = int(mm // 60)
    mi = int(mm % 60)
    try:
        dt_utc = datetime.datetime(y, m, d, hh, mi, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=8)
        return int(dt_utc.timestamp())
    except Exception:
        return None


def pick_kl_bar_asof(kl_list, asof_ts: int):
    """Latest bar whose start ts <= asof_ts; None when asof_ts is earlier than
    every bar. Falling back to the last bar would inject future data into the
    decision (all callers degrade via getattr defaults / falsy checks).
    asof_ts=0 keeps the historical "latest bar" behaviour."""
    if not kl_list:
        return None
    try:
        t = int(asof_ts or 0)
    except Exception:
        t = 0
    picked = None
    for k in kl_list:
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        if t and int(ts) > int(t):
            continue
        picked = k
    return picked


def pick_kl_bar_asof_with_prev(kl_list, asof_ts: int):
    """(bar, prev_bar) variant of pick_kl_bar_asof — (None, None) when asof_ts
    predates every bar instead of returning a future bar."""
    if not kl_list:
        return None, None
    try:
        t = int(asof_ts or 0)
    except Exception:
        t = 0
    picked = None
    picked_i = -1
    for i, k in enumerate(kl_list):
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        if t and int(ts) > int(t):
            continue
        picked = k
        picked_i = i
    if picked is None:
        return None, None
    prev = kl_list[picked_i - 1] if picked_i > 0 else None
    return picked, prev


def daily_oc_from_kl_list(kl_list, decision_min: Optional[int] = None) -> Dict[int, Dict[str, float]]:
    by_day: Dict[int, Dict[str, Any]] = {}
    for k in (kl_list or []):
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        dk = cn_day_key_from_ts(ts)
        if dk is None:
            continue
        bar_min = None
        if decision_min is not None:
            try:
                dt = cn_dt_from_ts(int(ts))
                bar_min = int(dt.hour) * 60 + int(dt.minute)
            except Exception:
                bar_min = None
        try:
            o = float(getattr(k, "open", 0.0) or 0.0)
        except Exception:
            o = 0.0
        try:
            c = float(getattr(k, "close", 0.0) or 0.0)
        except Exception:
            c = 0.0
        if c <= 0.0:
            continue
        if o <= 0.0:
            o = c
        # Bar ts is the bar START (ClickHouseAPI._bucket_start): a 30m bar
        # stamped 10:00 closes ~10:29. "bar_min < decision_min" keeps only bars
        # fully completed before the decision minute — "<=" would pick the bar
        # starting AT decision time and leak ~one bar of future close into
        # decision_px (e.g. 10:00 decision priced with ~10:30 close).
        dd = by_day.get(int(dk))
        if dd is None:
            by_day[int(dk)] = {
                "open": float(o),
                "close": float(c),
                "ts_first": int(ts),
                "ts_last": int(ts),
                "decision_ts": int(ts) if (decision_min is not None and bar_min is not None and int(bar_min) < int(decision_min)) else 0,
                "decision_px": float(c) if (decision_min is not None and bar_min is not None and int(bar_min) < int(decision_min)) else None,
            }
            continue
        if int(ts) <= int(dd.get("ts_first") or 0):
            dd["ts_first"] = int(ts)
            dd["open"] = float(o)
        if int(ts) >= int(dd.get("ts_last") or 0):
            dd["ts_last"] = int(ts)
            dd["close"] = float(c)
        if decision_min is not None and bar_min is not None and int(bar_min) < int(decision_min):
            if int(ts) >= int(dd.get("decision_ts") or 0):
                dd["decision_ts"] = int(ts)
                dd["decision_px"] = float(c)
    out: Dict[int, Dict[str, float]] = {}
    for dk, dd in by_day.items():
        try:
            row: Dict[str, float] = {"open": float(dd.get("open") or 0.0), "close": float(dd.get("close") or 0.0)}
            if decision_min is not None:
                dp = dd.get("decision_px")
                try:
                    dp = float(dp) if dp is not None else None
                except Exception:
                    dp = None
                if dp is None or (isinstance(dp, float) and (not math.isfinite(dp))) or (dp is not None and dp <= 0.0):
                    dp = float(row.get("open") or 0.0)
                row["decision_px"] = float(dp) if dp and dp > 0.0 else float(row.get("open") or 0.0)
            out[int(dk)] = row
        except Exception:
            continue
    return out


def range_ratio_250(kl_list) -> float:
    """(max(high) - min(low)) / min(low) over the last <=250 bars.

    A range ratio, not a statistical volatility — on 30m bars 250 bars cover
    ~31 trading days. Renamed from compute_volatility_250 (which misdescribed
    the metric); the alias below keeps existing callers working."""
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


def compute_volatility_250(kl_list) -> float:
    """Deprecated alias of range_ratio_250 (backend.main still imports this
    name); the metric is a range ratio, not a volatility."""
    return range_ratio_250(kl_list)


# Bounded LRU: insertion-ordered, hits refresh position, evictions drop the
# oldest single key instead of clearing the whole cache (which caused a
# thundering-herd reload every 65th distinct key).
_INDEX_RISK_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_INDEX_RISK_CACHE_MAX = 64


def _tr(high: float, low: float, prev_close: float) -> float:
    return float(max(high - low, abs(high - prev_close), abs(low - prev_close)))


def _cn_expect_today_bar(now_ts: int) -> bool:
    """True when the CN calendar at now_ts is inside/after a regular weekday
    session (>= 09:30 CN) — i.e. today's daily bar ought to exist already, so a
    cached index-risk row lacking today_open is stale rather than legitimately
    empty."""
    dt = cn_dt_from_ts(now_ts or 0)
    if dt is None or dt.weekday() >= 5:
        return False
    return int(dt.hour) * 60 + int(dt.minute) >= 9 * 60 + 30


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
        dk = cn_day_key_from_ts(ts)
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

    now_dk = cn_day_key_from_ts(now_ts or 0) or 0
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
        # Staleness guard: an entry computed before today's first bar exists has
        # today_open=None, which disables gap_open_pct protection for the rest
        # of the day if served blindly (the caller's cache key is day-grained).
        # Once the CN session is actually underway, drop through and reload.
        stale = (
            gap_down_threshold is not None
            and cached.get("today_open") is None
            and _cn_expect_today_bar(now_ts or 0)
        )
        if not stale:
            _INDEX_RISK_CACHE.move_to_end(ck)
            # Return a copy so callers mutating the dict don't poison the cache.
            return dict(cached)
    kl_list = None
    load_error = None
    if loader is not None:
        try:
            kl_list = loader()
        except Exception as exc:
            # Explicit failure path: without this the exception propagated as a
            # 500 for the whole step, bypassing risk_data_fail_mode entirely.
            load_error = exc
            print(f"[live.common] index risk loader failed: {exc}", file=sys.stderr)
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
    if load_error is not None:
        # Surface the failure in the risk payload (data_missing still fires via
        # the caller's prev_close/ma20 check) and don't cache it, so a transient
        # loader error recovers on the next call instead of poisoning the day.
        out = dict(out or {})
        out["load_error"] = str(load_error)
        out.setdefault("reason", "data_load_error")
        return out
    while len(_INDEX_RISK_CACHE) >= _INDEX_RISK_CACHE_MAX:
        _INDEX_RISK_CACHE.popitem(last=False)
    _INDEX_RISK_CACHE[ck] = dict(out or {})
    return out
