"""Wolfpack multi-stock backtester.

Pipeline: per-code candidate signals are computed in a ProcessPoolExecutor
(_calc_code_candidates -> chan/BSP + model predictions), then a daily
portfolio simulation (_simulate_universe_daily_topn) selects the top-N
highest-probability candidates each trading day and executes at the next
available price (same-day open, or the configured decision-time price) while
honoring T+1, stop-loss (judged on close, executed next day), pool/index/risk
filters and sell-side limit-down / suspension tradability checks.

Timezone convention: all bar timestamps are epoch seconds; trading-day
bucketing uses the China (UTC+8) calendar via backend.live.common helpers.
Date-only parameters (start_date/end_date/filter_start_date) are interpreted
as UTC+8 calendar days, independent of the host's local timezone.

Hidden env knobs (all optional):
- MLCHAN_STAMP_DUTY_SELL (default 0.001): sell-side stamp duty rate applied to
  non-ETF-like A-share codes on every sell fill.
- MLCHAN_MIN_KL_SIZE (default 500): minimum kline bars required per code
  before chan/BSP analysis is attempted.
- MLCHAN_MIN_BSP_SIZE (default 200): minimum BSP sample size required before a
  code produces candidates.
- MLCHAN_DESIRED_TRAIN_YEARS (default 1.0): training window in years; when
  run()'s lookback_days is not given the fetch range is extended backwards by
  (train_years + 3) * 365 days.
- MLCHAN_WOLFPACK_MAX_WORKERS (default 32): hard cap on the ProcessPoolExecutor
  fan-out inside run(). The HTTP layer additionally applies its own stricter
  cap (main._resolve_backtest_max_workers / MLCHAN_BACKTEST_MAX_WORKERS=16)
  before calling run(); this cap protects direct/library callers.
"""

import datetime
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import os
from bisect import bisect_right

from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from backend.chan_service import (
    fetch_stock_data,
    get_chan_data,
    train_time_split_backtest,
    load_pretrained_model_bundle_by_key,
    normalize_code,
    predict_proba_1,
    _profit_for_info,
    _min_profit_for_info,
    _normalize_side_filter,
    _side_filter_allows,
)
from backend.live.common import (
    cn_day_key_from_ts as _cn_day_key_from_ts,
    cn_ts_from_day_key_minutes as _cn_ts_from_day_min_shared,
    daily_oc_from_kl_list as _daily_oc_shared,
    parse_hhmm_to_minutes as _hhmm_to_min_shared,
)

def _env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except:
        return int(default)

def _env_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except:
        return float(default)


# Hard cap for ProcessPoolExecutor fan-out (DoS guard): direct calls to
# WolfpackBacktester.run() bypass main.py's _resolve_backtest_max_workers
# (_BACKTEST_MAX_WORKERS), so enforce an in-module cap aligned with the
# live-scan hard cap (_LIVE_SCAN_MAX_WORKERS=32). Raisable via env only for
# trusted local runs.
_WOLFPACK_MAX_WORKERS_CAP = max(1, _env_int("MLCHAN_WOLFPACK_MAX_WORKERS", 32))


def _extract_6digits(code: str) -> Optional[str]:
    s = str(code or "").strip().lower()
    if not s:
        return None
    if "." in s:
        parts = [p for p in s.split(".") if p]
        for p in reversed(parts):
            if p.isdigit() and len(p) == 6:
                return p
    digits = "".join([c for c in s if c.isdigit()])
    if len(digits) == 6:
        return digits
    return None

def _is_cn_etf_like(code: str) -> bool:
    digits = _extract_6digits(code)
    if not digits:
        return False
    return bool(
        digits.startswith(
            (
                "11",
                "12",
                "13",
                "15",
                "16",
                "17",
                "18",
                "50",
                "51",
                "56",
                "58",
            )
        )
    )

def _sell_fee_rates(code: str, trade_cost: float, stamp_duty_sell: float) -> Tuple[float, float, float]:
    """Return (commission_rate, stamp_rate, total_rate) for a sell fill.

    Commission is the per-side trade_cost; sell stamp duty applies to
    non-ETF-like A-share codes only. Each component is clamped to [0, 1] and
    the summed rate to [0, 1] as well."""
    try:
        commission = float(trade_cost or 0.0)
    except Exception:
        commission = 0.0
    try:
        stamp = float(stamp_duty_sell or 0.0)
    except Exception:
        stamp = 0.0
    commission_rate = float(max(0.0, min(1.0, float(commission))))
    stamp_rate = 0.0 if _is_cn_etf_like(str(code)) else float(max(0.0, min(1.0, float(stamp))))
    total_rate = float(max(0.0, min(1.0, float(commission_rate) + float(stamp_rate))))
    return float(commission_rate), float(stamp_rate), float(total_rate)


def _limit_rate_for_code(code: str) -> float:
    """Approximate daily price-limit band by board: ~10% main board,
    ~20% ChiNext (300*) / STAR (688*), ~30% BSE (8*/4*/92*)."""
    s = str(code or "").split(".")[-1]
    if s.startswith("300") or s.startswith("688"):
        return 0.195
    if s.startswith("8") or s.startswith("4") or s.startswith("92"):
        return 0.295
    return 0.095


def _cn_ts_from_date_str(date_str: Optional[str]) -> Optional[int]:
    """Parse 'YYYY-MM-DD' as a China (UTC+8) calendar day and return the epoch
    ts of that day's 00:00 CN time — consistent with cn_day_key_from_ts
    bucketing and independent of the host's local timezone."""
    try:
        d = datetime.datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except Exception:
        return None
    dk = int(d.year) * 10000 + int(d.month) * 100 + int(d.day)
    return _cn_ts_from_day_min_shared(dk, 0)


def _candidate_sort_key(r) -> Tuple[float, str, int]:
    """Deterministic ranking for same-day candidates: probability desc, then
    code asc, then signal ts asc as stable tie-breaks so equal-prob
    selections are reproducible regardless of ProcessPoolExecutor
    completion order."""
    try:
        p = float((r or {}).get("prob"))
    except Exception:
        p = float("-inf")
    if not np.isfinite(p):
        p = float("-inf")
    try:
        ts = int((r or {}).get("ts") or 0)
    except Exception:
        ts = 0
    return (-p, str((r or {}).get("code") or ""), int(ts))


def _day_key_from_ts(ts: int) -> Optional[int]:
    return _cn_day_key_from_ts(int(ts))


def _date_range_days(start_date: str, end_date: str) -> List[int]:
    try:
        s = datetime.datetime.strptime(str(start_date), "%Y-%m-%d").date()
    except Exception:
        s = (datetime.datetime.now() - datetime.timedelta(days=365 * 5)).date()
    try:
        e = datetime.datetime.strptime(str(end_date), "%Y-%m-%d").date()
    except Exception:
        e = datetime.datetime.now().date()
    if e < s:
        s, e = e, s
    days = []
    cur = s
    while cur <= e:
        days.append(int(cur.year) * 10000 + int(cur.month) * 100 + int(cur.day))
        cur = cur + datetime.timedelta(days=1)
    return days


def _parse_hhmm_to_minutes(v: Optional[str], default_min: int = 10 * 60) -> int:
    return int(_hhmm_to_min_shared(v, default_min=int(default_min)))


def _prev_weekday_day_key(dk: int) -> Optional[int]:
    try:
        s = str(int(dk))
        d = datetime.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        return None
    pd = d - datetime.timedelta(days=1)
    while pd.weekday() >= 5:
        pd = pd - datetime.timedelta(days=1)
    return int(pd.year) * 10000 + int(pd.month) * 100 + int(pd.day)


def _daily_oc_from_kl_list(kl_list, decision_min: Optional[int] = None) -> Dict[int, Dict[str, float]]:
    return _daily_oc_shared(kl_list, decision_min=decision_min)


def _load_index_filter_day_set(
    index_code: str,
    start_date: str,
    end_date: str,
    data_src: DATA_SRC,
    autype: AUTYPE,
    ma_window: int = 20,
) -> Optional[Dict[int, bool]]:
    if data_src == DATA_SRC.AK_SHARE:
        data_src = DATA_SRC.BAO_STOCK
    try:
        mw = int(ma_window)
    except Exception:
        mw = 20
    mw = 20 if mw <= 1 else mw

    code = normalize_code(str(index_code or "399006"))
    if str(code).endswith("399006") and (not (code.startswith("sz.") or code.startswith("sh."))):
        code = "sz.399006"
    kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    if (not kl_list) or len(kl_list) < mw + 5:
        alt_src = DATA_SRC.BAO_STOCK if data_src != DATA_SRC.BAO_STOCK else None
        if alt_src is not None:
            try:
                kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, alt_src, autype=autype, use_online_latest=True, asset_type="INDEX")
            except Exception:
                kl_list = None
    if not kl_list or len(kl_list) < mw + 5:
        return None

    closes = []
    dks = []
    for k in kl_list:
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        dk = _day_key_from_ts(ts)
        if dk is None:
            continue
        try:
            c = float(getattr(k, "close", 0.0) or 0.0)
        except Exception:
            c = 0.0
        if c <= 0:
            continue
        dks.append(int(dk))
        closes.append(float(c))

    if len(closes) < mw + 5:
        return None

    allow = {}
    for i in range(len(closes)):
        if i < mw:
            continue
        ma = float(sum(closes[i - mw : i]) / float(mw))
        allow[int(dks[i])] = bool(closes[i - 1] > ma) if i - 1 >= 0 else False
    return allow


def _aggregate_daily_ohlc(kl_list) -> List[dict]:
    """Aggregate a (possibly intraday) kline list into per-CN-day OHLC records
    sorted by day key. Day bucketing uses the UTC+8 calendar
    (cn_day_key_from_ts); open/close come from the day's first/last bar, and
    missing/invalid o/h/l values fall back to the bar close."""
    by_day = {}
    for k in (kl_list or []):
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        dk = _day_key_from_ts(ts)
        if dk is None:
            continue
        try:
            o = float(getattr(k, "open", 0.0) or 0.0)
            hi = float(getattr(k, "high", 0.0) or 0.0)
            lo = float(getattr(k, "low", 0.0) or 0.0)
            c = float(getattr(k, "close", 0.0) or 0.0)
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
            by_day[int(dk)] = {
                "day": int(dk),
                "open": float(o),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "ts_first": int(ts),
                "ts_last": int(ts),
            }
        else:
            dd["high"] = float(max(float(dd["high"]), float(hi)))
            dd["low"] = float(min(float(dd["low"]), float(lo)))
            if int(ts) <= int(dd.get("ts_first") or 0):
                dd["ts_first"] = int(ts)
                dd["open"] = float(o)
            if int(ts) >= int(dd.get("ts_last") or 0):
                dd["ts_last"] = int(ts)
                dd["close"] = float(c)
    return [by_day[k] for k in sorted(by_day.keys())]


def _load_index_risk_day_map(
    index_code: str,
    start_date: str,
    end_date: str,
    data_src: DATA_SRC,
    autype: AUTYPE,
    atr_window: int = 14,
    drop_threshold: float = -0.03,
    atr_pct_threshold: float = 0.04,
    flat_drop_threshold: float = -0.05,
    flat_atr_pct_threshold: float = 0.06,
    gap_down_threshold: Optional[float] = None,
    mode: str = "both",
) -> Optional[Dict[int, str]]:
    raw_mode = str(mode or "").strip().lower()
    use_drop = raw_mode in ["both", "drop", "ret", "return"]
    use_atr = raw_mode in ["both", "atr", "vol", "volatility"]
    if not use_drop and not use_atr:
        return None

    try:
        win = int(atr_window)
    except Exception:
        win = 14
    win = max(2, win)

    code = normalize_code(str(index_code or "399006"))
    if str(code).endswith("399006") and (not (code.startswith("sz.") or code.startswith("sh."))):
        code = "sz.399006"
    kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    if not kl_list:
        kl_list = fetch_stock_data(code, KL_TYPE.K_30M, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    if not kl_list:
        return None

    daily = _aggregate_daily_ohlc(kl_list)
    if len(daily) < max(5, win + 2):
        return None

    dks = [int(d.get("day") or 0) for d in daily]
    opens = [float(d.get("open") or 0.0) for d in daily]
    highs = [float(d.get("high") or 0.0) for d in daily]
    lows = [float(d.get("low") or 0.0) for d in daily]
    closes = [float(d.get("close") or 0.0) for d in daily]

    if len(closes) < max(5, win + 2):
        return None

    def tr(h: float, l: float, pc: float) -> float:
        return float(max(h - l, abs(h - pc), abs(l - pc)))

    out = {}
    for i in range(1, len(closes)):
        dk_today = int(dks[i])
        c_prev = float(closes[i - 1])
        o_today = float(opens[i])
        gap_open_pct = float(o_today / c_prev - 1.0) if c_prev > 0.0 and o_today > 0.0 else None
        if gap_down_threshold is not None and gap_open_pct is not None:
            if float(gap_open_pct) <= float(gap_down_threshold):
                out[dk_today] = "flat"
                continue

        if i < 2:
            continue

        c_2 = float(closes[i - 2])
        c_y = float(closes[i - 1])
        ret1d_y = float(c_y / c_2 - 1.0) if c_2 > 0.0 else None

        trs = []
        start = max(1, (i - 1) - win + 1)
        for j in range(start, i):
            pc = float(closes[j - 1])
            trs.append(tr(float(highs[j]), float(lows[j]), pc))
        atr_pct_y = float((sum(trs) / float(len(trs))) / c_y) if trs and c_y > 0.0 else None

        flat = False
        reduce = False
        if use_drop and ret1d_y is not None:
            if float(ret1d_y) <= float(flat_drop_threshold):
                flat = True
            elif float(ret1d_y) <= float(drop_threshold):
                reduce = True
        if use_atr and atr_pct_y is not None:
            if float(atr_pct_y) >= float(flat_atr_pct_threshold):
                flat = True
            elif float(atr_pct_y) >= float(atr_pct_threshold):
                reduce = True if not flat else reduce

        if flat:
            out[dk_today] = "flat"
        elif reduce:
            out[dk_today] = "reduce"
    return out


def _load_index_daily_ohlc(
    index_code: str,
    start_date: str,
    end_date: str,
    data_src: DATA_SRC,
    autype: AUTYPE,
) -> List[dict]:
    code = normalize_code(str(index_code or "399006"))
    if str(code).endswith("399006") and (not (code.startswith("sz.") or code.startswith("sh."))):
        code = "sz.399006"
    kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    if not kl_list:
        kl_list = fetch_stock_data(code, KL_TYPE.K_30M, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    if not kl_list:
        return []

    return _aggregate_daily_ohlc(kl_list)


def _load_index_intraday_close_series(
    index_code: str,
    start_date: str,
    end_date: str,
    data_src: DATA_SRC,
    autype: AUTYPE,
) -> List[tuple]:
    code = normalize_code(str(index_code or "399006"))
    if str(code).endswith("399006") and (not (code.startswith("sz.") or code.startswith("sh."))):
        code = "sz.399006"
    kl_list = fetch_stock_data(code, KL_TYPE.K_30M, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE), asset_type="INDEX")
    out = []
    for k in (kl_list or []):
        try:
            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue
        try:
            c = float(getattr(k, "close", 0.0) or 0.0)
        except Exception:
            c = 0.0
        if c <= 0.0:
            continue
        out.append((int(ts), float(c)))
    out.sort(key=lambda x: int(x[0]))
    return out


def _simulate_universe_daily_topn(
    candidates: List[dict],
    start_date: str,
    end_date: str,
    trade_cost: float = 0.0,
    top_n: int = 3,
    initial_capital: float = 100000.0,
    lot_size: int = 100,
    universe_allow_by_day: Optional[Dict[int, Dict[str, bool]]] = None,
    force_sell_out_of_pool: bool = True,
    index_allow_by_day: Optional[Dict[int, bool]] = None,
    risk_by_day: Optional[Dict[int, str]] = None,
    intraday_cfg: Optional[dict] = None,
    daily_oc_by_code: Optional[Dict[str, Dict[int, Dict[str, float]]]] = None,
    sell_mode: str = "baseline",
    t1_decision_time: Optional[str] = None,
    t1_gap_strong_threshold: float = 0.03,
    t1_gap_weak_threshold: float = -0.02,
    t1_gap_very_strong_threshold: float = 0.05,
    enforce_t1: bool = True,
    stop_loss: float = -0.03,
) -> dict:
    try:
        tc = float(trade_cost or 0.0)
    except Exception:
        tc = 0.0
    try:
        base_capital = float(initial_capital)
    except Exception:
        base_capital = 100000.0
    base_capital = float(max(1.0, base_capital))
    try:
        lot = int(lot_size)
    except Exception:
        lot = 100
    lot = int(max(1, lot))
    stamp_sell = _env_float("MLCHAN_STAMP_DUTY_SELL", 0.001)
    try:
        tn = int(top_n)
    except Exception:
        tn = 3
    tn = 1 if tn <= 1 else tn


    sell_mode = str(sell_mode or "baseline").strip().lower() or "baseline"
    decision_min = _parse_hhmm_to_minutes(t1_decision_time, default_min=10 * 60)
    try:
        strong_th = float(t1_gap_strong_threshold)
    except Exception:
        strong_th = 0.03
    try:
        weak_th = float(t1_gap_weak_threshold)
    except Exception:
        weak_th = -0.02
    try:
        very_strong_th = float(t1_gap_very_strong_threshold)
    except Exception:
        very_strong_th = 0.05
    try:
        sl = float(stop_loss)
    except Exception:
        sl = -0.03

    by_day: Dict[int, List[dict]] = {}
    raw_candidate_count_by_day: Dict[int, int] = {}
    pool_filtered_count_by_day: Dict[int, int] = {}
    for r in (candidates or []):
        if not isinstance(r, dict):
            continue
        try:
            dk = int(r.get("day") or 0)
        except Exception:
            dk = 0
        if dk <= 0:
            continue
        raw_candidate_count_by_day[int(dk)] = int(raw_candidate_count_by_day.get(int(dk), 0)) + 1
        if universe_allow_by_day is not None:
            allow = universe_allow_by_day.get(int(dk))
            if allow is not None:
                code = str(r.get("code") or "").strip()
                if not code or (not bool(allow.get(code, False))):
                    pool_filtered_count_by_day[int(dk)] = int(pool_filtered_count_by_day.get(int(dk), 0)) + 1
                    continue
        by_day.setdefault(int(dk), []).append(r)

    daily_oc_by_code = daily_oc_by_code or {}
    last_close: Dict[str, float] = {}
    decision_px_enabled = False
    for _, m in (daily_oc_by_code or {}).items():
        if not isinstance(m, dict):
            continue
        for _, dd in (m or {}).items():
            if isinstance(dd, dict) and ("decision_px" in dd):
                decision_px_enabled = True
                break
        if decision_px_enabled:
            break

    buy_trade_min = None
    if decision_min and int(decision_min) > (9 * 60 + 30):
        for _, m in (daily_oc_by_code or {}).items():
            if not isinstance(m, dict):
                continue
            for _, dd in (m or {}).items():
                if isinstance(dd, dict) and ("decision_px" in dd):
                    buy_trade_min = int(decision_min)
                    break
            if buy_trade_min is not None:
                break

    # same minute _buy_at_px will use: sell_trade_min and buy_trade_min are both
    # decision_min whenever the decision-px path is enabled, else open (9:30)
    buy_exec_min = int(decision_min) if (bool(decision_px_enabled) and int(decision_min) > (9 * 60 + 30)) else None

    def _format_day_time(dk: int, use_close: bool) -> str:
        try:
            s = str(int(dk))
        except Exception:
            s = str(dk)
        if len(s) == 8 and s.isdigit():
            day = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        else:
            day = s
        hhmm = "15:00" if bool(use_close) else "09:30"
        return f"{day} {hhmm}"

    def _format_day_ts(dk: int, use_close: bool) -> Optional[int]:
        minute_of_day = (15 * 60) if bool(use_close) else (9 * 60 + 30)
        return _cn_ts_from_day_min_shared(int(dk), int(minute_of_day))

    def _format_day_time_at_min(dk: int, minute_of_day: int) -> str:
        try:
            s = str(int(dk))
        except Exception:
            s = str(dk)
        if len(s) == 8 and s.isdigit():
            day = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        else:
            day = s
        try:
            mm = int(minute_of_day)
        except Exception:
            mm = 10 * 60
        mm = max(0, min(23 * 60 + 59, int(mm)))
        hh = int(mm // 60)
        mi = int(mm % 60)
        return f"{day} {hh:02d}:{mi:02d}"

    def _format_day_ts_at_min(dk: int, minute_of_day: int) -> Optional[int]:
        return _cn_ts_from_day_min_shared(int(dk), int(minute_of_day))

    def _get_oc(code: str, dk: int) -> Tuple[Optional[float], Optional[float]]:
        m = daily_oc_by_code.get(str(code)) or {}
        dd = m.get(int(dk)) or {}
        o = dd.get("open")
        c = dd.get("close")
        try:
            o = float(o) if o is not None else None
        except Exception:
            o = None
        try:
            c = float(c) if c is not None else None
        except Exception:
            c = None
        if c is None or (isinstance(c, float) and (not np.isfinite(c))) or (c is not None and c <= 0.0):
            c = last_close.get(str(code))
        if o is None or (isinstance(o, float) and (not np.isfinite(o))) or (o is not None and o <= 0.0):
            o = c
        return o, c

    def _get_decision_px(code: str, dk: int) -> Optional[float]:
        m = daily_oc_by_code.get(str(code)) or {}
        dd = m.get(int(dk)) or {}
        px = dd.get("decision_px")
        try:
            px = float(px) if px is not None else None
        except Exception:
            px = None
        if px is None or (isinstance(px, float) and (not np.isfinite(px))) or (px is not None and px <= 0.0):
            o, c = _get_oc(code, dk)
            px = o if (o is not None and o > 0.0) else c
        return px

    def _market_data_ok(dk: int) -> bool:
        for code, m in daily_oc_by_code.items():
            dd = (m or {}).get(int(dk))
            if dd and float(dd.get("close") or 0.0) > 0.0:
                return True
        return False

    day_list = _date_range_days(start_date, end_date)
    eq = 1.0
    prev_eq = 1.0
    rets = []
    curve = []
    trade_days = 0

    cash = float(base_capital)
    positions: Dict[str, dict] = {}
    trade_logs: List[dict] = []
    daily_stats: List[dict] = []
    pending_rows: List[dict] = []
    pending_stop_sell: List[str] = []

    def _equity_at_px(dk: int, use_close: bool) -> float:
        tot = float(cash)
        for code, p in (positions or {}).items():
            px = None
            o, c = _get_oc(code, dk)
            px = c if use_close else o
            if px is None or px <= 0.0:
                continue
            try:
                sh = float(p.get("shares") or 0.0)
            except Exception:
                sh = 0.0
            tot += float(sh) * float(px)
        return float(tot)

    def _equity_norm_at_px(dk: int, use_close: bool) -> float:
        v = _equity_at_px(dk, use_close=use_close)
        if base_capital > 0:
            return float(v / float(base_capital))
        return float(v)

    def _resolve_trade_px(code: str, dk: int, trade_min: Optional[int]) -> Tuple[Optional[float], Optional[int]]:
        if trade_min is None:
            return None, None
        m = daily_oc_by_code.get(str(code)) or {}
        dd = m.get(int(dk)) or {}
        raw = dd.get("decision_px")
        try:
            px0 = float(raw) if raw is not None else None
        except Exception:
            px0 = None
        if px0 is not None and px0 > 0.0 and np.isfinite(px0):
            return float(px0), int(trade_min)
        return None, None

    def _bar_for(code: str, dk: int) -> Optional[dict]:
        dd = (daily_oc_by_code.get(str(code)) or {}).get(int(dk))
        return dd if isinstance(dd, dict) else None

    def _sell_tradeable(code: str, dk: int) -> bool:
        """Sell-side tradability, symmetric with the buy-side limit-up guard:
        a stock with no bar on dk (suspension / missing data) or whose day
        closed sealed at limit-down cannot be exited that day. daily_oc rows
        lacking low/prev_close keys degrade to 'tradeable' (cannot tell)."""
        dd = _bar_for(code, dk)
        if dd is None:
            return False
        try:
            lo = float(dd.get("low")) if dd.get("low") is not None else None
        except Exception:
            lo = None
        try:
            cl = float(dd.get("close")) if dd.get("close") is not None else None
        except Exception:
            cl = None
        try:
            pc = float(dd.get("prev_close")) if dd.get("prev_close") is not None else None
        except Exception:
            pc = None
        if cl is None or cl <= 0.0:
            return False
        if pc is not None and pc > 0.0 and lo is not None and lo > 0.0:
            rate = _limit_rate_for_code(code)
            if float(cl) <= float(pc) * (1.0 - float(rate)) and abs(float(cl) - float(lo)) < 0.01:
                return False
        return True

    def _sell_at_px(codes: List[str], dk: int, use_close: bool, reason_by_code: Optional[Dict[str, str]] = None, trade_min: Optional[int] = None, blocked_out: Optional[List[str]] = None) -> int:
        nonlocal cash
        sold = 0
        for code in (codes or []):
            p = positions.get(str(code))
            if not p:
                continue
            if not _sell_tradeable(str(code), int(dk)):
                if blocked_out is not None:
                    blocked_out.append(str(code))
                continue
            px, used_trade_min = _resolve_trade_px(str(code), int(dk), trade_min)
            if used_trade_min is not None:
                time_text = _format_day_time_at_min(int(dk), int(used_trade_min))
                trade_ts = _format_day_ts_at_min(int(dk), int(used_trade_min))
            else:
                o, c = _get_oc(code, dk)
                px = c if use_close else o
                time_text = _format_day_time(int(dk), bool(use_close))
                trade_ts = _format_day_ts(int(dk), bool(use_close))
            if px is None or px <= 0.0:
                continue
            raw_sh = p.get("shares")
            try:
                sh = int(raw_sh)
            except Exception:
                try:
                    sh = int(float(raw_sh or 0.0))
                except Exception:
                    sh = 0
            if sh <= 0:
                positions.pop(str(code), None)
                continue
            cash_before = float(cash)
            try:
                entry_price = float(p.get("entry_price") or 0.0)
            except Exception:
                entry_price = 0.0
            try:
                entry_day = int(p.get("entry_day") or 0)
            except Exception:
                entry_day = 0
            try:
                prob = float(p.get("prob") or 0.0)
            except Exception:
                prob = 0.0

            gross = float(sh) * float(px)
            commission_rate, stamp_rate, sell_fee_rate = _sell_fee_rates(str(code), tc, stamp_sell)
            sell_proceeds = float(gross) * float(max(0.0, 1.0 - float(sell_fee_rate)))
            buy_cost = float(sh) * float(entry_price) * float(1.0 + float(commission_rate)) if entry_price > 0.0 else None
            pnl = float(sell_proceeds - float(buy_cost)) if buy_cost is not None else None
            pnl_pct = float(pnl / float(buy_cost)) if pnl is not None and buy_cost and float(buy_cost) > 0.0 else None

            cash += float(sell_proceeds)
            reason = None
            if reason_by_code is not None:
                reason = reason_by_code.get(str(code))
            trade_logs.append(
                {
                    "day": int(dk),
                    "time": str(time_text),
                    "trade_ts": int(trade_ts) if trade_ts is not None else None,
                    "code": str(code),
                    "side": "sell",
                    "price": float(px),
                    "shares": int(sh),
                    "use_close": bool(use_close),
                    "reason": str(reason) if reason else None,
                    "entry_day": int(entry_day) if entry_day else None,
                    "entry_price": float(entry_price) if entry_price else None,
                    "prob": float(prob),
                    "gross": float(gross),
                    "fee": float(gross) * float(sell_fee_rate),
                    "fee_commission": float(gross) * float(commission_rate),
                    "fee_stamp_duty": float(gross) * float(stamp_rate),
                    "fee_rate_commission": float(commission_rate),
                    "fee_rate_stamp_duty": float(stamp_rate),
                    "cash_before": float(cash_before),
                    "cash_after": float(cash),
                    "pnl": float(pnl) if pnl is not None else None,
                    "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                }
            )
            positions.pop(str(code), None)
            sold += 1
        return int(sold)

    def _buy_at_px(rows: List[dict], dk: int, use_close: bool, target_slots: int, trade_min: Optional[int] = None) -> int:
        nonlocal cash
        if target_slots <= 0:
            return 0
        tot_eq = _equity_at_px(dk, use_close=use_close)
        target_value = float(tot_eq) / float(max(1, int(target_slots)))
        remaining = int(target_slots)
        bought = 0
        for r in (rows or []):
            if remaining <= 0:
                break
            code = str(r.get("code") or "").strip()
            if not code or code in positions:
                continue
            if _bar_for(code, int(dk)) is None:
                # no bar today (suspension / missing data): refuse a
                # stale-price buy filled from last_close fallback
                continue
            px, used_trade_min = _resolve_trade_px(str(code), int(dk), trade_min)
            if used_trade_min is not None:
                time_text = _format_day_time_at_min(int(dk), int(used_trade_min))
                trade_ts = _format_day_ts_at_min(int(dk), int(used_trade_min))
            else:
                o, c = _get_oc(code, dk)
                px = c if use_close else o
                time_text = _format_day_time(int(dk), bool(use_close))
                trade_ts = _format_day_ts(int(dk), bool(use_close))
            if px is None or px <= 0.0:
                continue
            budget = float(min(float(target_value), float(cash)))
            if budget <= 0.0:
                break
            buy_fee_rate = float(max(0.0, min(1.0, float(tc))))
            sh_raw = float(budget) / float(px)
            sh = int(int(float(sh_raw) // float(lot)) * int(lot))
            if sh <= 0:
                continue
            max_afford = float(cash) / float(float(px) * float(1.0 + float(buy_fee_rate)))
            sh_max = int(int(float(max_afford) // float(lot)) * int(lot))
            if sh_max <= 0:
                continue
            if sh > sh_max:
                sh = int(sh_max)
            cost = float(sh) * float(px) * float(1.0 + float(buy_fee_rate))
            if cost <= 0.0 or cost > cash:
                continue
            cash_before = float(cash)
            cash -= float(cost)
            positions[code] = {
                "shares": int(sh),
                "entry_day": int(dk),
                "entry_price": float(px),
                "prob": float(r.get("prob") or 0.0),
            }
            try:
                prob = float(r.get("prob") or 0.0)
            except Exception:
                prob = 0.0
            gross = float(sh) * float(px)
            trade_logs.append(
                {
                    "day": int(dk),
                    "time": str(time_text),
                    "trade_ts": int(trade_ts) if trade_ts is not None else None,
                    "code": str(code),
                    "side": "buy",
                    "price": float(px),
                    "shares": int(sh),
                    "use_close": bool(use_close),
                    "signal_ts": int(r.get("ts") or 0) or None,
                    "prob": float(prob),
                    "gross": float(gross),
                    "fee": float(gross) * float(buy_fee_rate),
                    "fee_commission": float(gross) * float(buy_fee_rate),
                    "fee_stamp_duty": 0.0,
                    "fee_rate_commission": float(buy_fee_rate),
                    "fee_rate_stamp_duty": 0.0,
                    "cash_before": float(cash_before),
                    "cash_after": float(cash),
                }
            )
            remaining -= 1
            bought += 1
        return int(bought)

    def _buy_decision_ts(code: str, dk: int) -> Optional[int]:
        _, used_min = _resolve_trade_px(str(code), int(dk), buy_exec_min)
        minute = int(used_min) if used_min is not None else (9 * 60 + 30)
        return _format_day_ts_at_min(int(dk), int(minute))

    for dk in day_list:
        market_ok = bool(_market_data_ok(int(dk)))
        index_allowed = True
        if index_allow_by_day is not None:
            index_allowed = bool(index_allow_by_day.get(int(dk), False))

        if not market_ok:
            eq = _equity_norm_at_px(int(dk), use_close=True)
            rr = float(eq / prev_eq - 1.0) if prev_eq > 0 else 0.0
            rets.append(float(rr))
            curve.append({"day": int(dk), "equity": float(eq), "r": float(rr), "codes": list(positions.keys()), "probs": [positions[c].get("prob") for c in positions.keys()]})
            daily_stats.append(
                {
                    "day": int(dk),
                    "market_data_ok": bool(market_ok),
                    "index_allowed": bool(index_allowed),
                    "risk_state": "na",
                    "day_top_n": 0,
                    "raw_candidates": int(raw_candidate_count_by_day.get(int(dk), 0)),
                    "pool_filtered": int(pool_filtered_count_by_day.get(int(dk), 0)),
                    "candidates_after_pool": int(len(by_day.get(int(dk)) or [])),
                    "candidates_before_intraday": 0,
                    "candidates_after_intraday": 0,
                    "selected": 0,
                    "has_targets": False,
                    "fallback_hold": False,
                    "positions_before": int(len(positions)),
                    "sell_stop_loss": 0,
                    "sell_out_of_pool": 0,
                    "sell_rebalance": 0,
                    "sell_planned": 0,
                    "sell_executed": 0,
                    "sell_blocked_untradeable": 0,
                    "buy_executed": 0,
                    "positions_after": int(len(positions)),
                    "pool_allow_size": None,
                    "pool_allow_missing": None,
                }
            )
            prev_eq = float(eq)
            continue

        if risk_by_day is not None:
            s = str(risk_by_day.get(int(dk)) or "").strip().lower()
            if s == "flat":
                day_tn = 0
                risk_flat = True
            else:
                day_tn = 1 if s == "reduce" else tn
                risk_flat = False
        else:
            day_tn = tn
            risk_flat = False

        rows = pending_rows + list(by_day.get(int(dk)) or [])
        pending_rows = []
        if rows:
            eligible_rows = []
            for r in rows:
                try:
                    sig_ts = int((r or {}).get("ts") or 0)
                except Exception:
                    sig_ts = 0
                dec_ts = _buy_decision_ts(str((r or {}).get("code") or ""), int(dk))
                if sig_ts > 0 and dec_ts is not None and int(sig_ts) > int(dec_ts):
                    pending_rows.append(r)  # signal is later than today's decision moment; defer to next trading day
                    continue
                eligible_rows.append(r)
            rows = eligible_rows
        intraday_before = int(len(rows))
        if intraday_cfg and bool(intraday_cfg.get("enabled")) and rows:
            try:
                bear_th = float(intraday_cfg.get("bear_threshold"))
            except Exception:
                bear_th = -0.015
            try:
                bull_th = float(intraday_cfg.get("bull_threshold"))
            except Exception:
                bull_th = -0.03
            fail_mode = str(intraday_cfg.get("data_fail_mode") or "reduce").strip().lower()
            prev_close = (intraday_cfg.get("prev_close_by_day") or {}).get(int(dk))
            ma20 = (intraday_cfg.get("ma20_by_day") or {}).get(int(dk))
            get_px = intraday_cfg.get("get_index_close_at_ts")
            missing = []
            if prev_close is None:
                missing.append("prev_close")
            if ma20 is None:
                missing.append("ma20")
            if not callable(get_px):
                missing.append("current_price")
            if missing:
                if fail_mode == "flat":
                    day_tn = 0
                elif fail_mode == "reduce":
                    day_tn = min(int(day_tn), 1) if int(day_tn) > 0 else 0

            next_rows = []
            px_eval = 0
            px_missing = 0
            for r in rows:
                try:
                    ts = int(r.get("ts") or 0)
                except Exception:
                    ts = 0
                cur_px = None
                if callable(get_px) and ts > 0:
                    try:
                        cur_px = get_px(ts)
                    except Exception:
                        cur_px = None
                if ts > 0:
                    px_eval += 1
                    if cur_px is None:
                        px_missing += 1
                if prev_close is None or ma20 is None or cur_px is None:
                    next_rows.append(r)
                    continue
                try:
                    pc = float(prev_close)
                    ma = float(ma20)
                    cp = float(cur_px)
                except Exception:
                    next_rows.append(r)
                    continue
                if pc <= 0.0 or cp <= 0.0 or ma <= 0.0:
                    next_rows.append(r)
                    continue
                change = float(cp / pc - 1.0)
                threshold = float(bear_th) if bool(cp < ma) else float(bull_th)
                if float(change) <= float(threshold):
                    continue
                next_rows.append(r)
            if "current_price" not in missing and px_eval > 0 and px_missing >= px_eval:
                # no signal-time index price available at all; same data-fail semantics
                if fail_mode == "flat":
                    day_tn = 0
                elif fail_mode == "reduce":
                    day_tn = min(int(day_tn), 1) if int(day_tn) > 0 else 0
            rows = next_rows
        intraday_after = int(len(rows))

        sel = []
        if bool(index_allowed) and int(day_tn) > 0:
            rows.sort(key=_candidate_sort_key)
            sel = rows[: int(day_tn)]

        target_rows = sel
        target_codes = [str(r.get("code") or "").strip() for r in (target_rows or []) if str(r.get("code") or "").strip()]
        target_codes = list(dict.fromkeys(target_codes))
        has_targets = bool(target_codes)

        forced_sell = []
        if pending_stop_sell:
            forced_sell = list(dict.fromkeys([str(c) for c in pending_stop_sell if str(c) in positions]))
        next_stop_sell = []
        for code, p in list((positions or {}).items()):
            o, c = _get_oc(code, dk)
            if c is None or c <= 0.0:
                continue
            last_close[str(code)] = float(c)
            try:
                ep = float(p.get("entry_price") or 0.0)
            except Exception:
                ep = 0.0
            if ep > 0.0:
                ret = float(c / ep - 1.0)
                if float(ret) <= float(sl):
                    next_stop_sell.append(str(code))  # stop is judged on today's close; earliest execution is next trading day
        pending_stop_sell = next_stop_sell

        out_of_pool_sell = []
        if bool(force_sell_out_of_pool) and (universe_allow_by_day is not None):
            allow = universe_allow_by_day.get(int(dk))
            if isinstance(allow, dict):
                for code in list((positions or {}).keys()):
                    if code and (not bool(allow.get(str(code), False))):
                        out_of_pool_sell.append(str(code))

        if (not has_targets) and (not risk_flat):
            target_codes = list(positions.keys())

        if risk_flat:
            target_codes = []

        current_codes = list(positions.keys())
        rebalance_out = [c for c in current_codes if c and c not in target_codes]
        sell_codes = list(dict.fromkeys([c for c in (forced_sell + out_of_pool_sell + rebalance_out) if c]))
        sell_codes_later = []
        reason_by_code = {}
        if risk_flat:
            for c in current_codes:
                if c:
                    reason_by_code[str(c)] = "risk_flat"
        else:
            for c in forced_sell:
                if c:
                    reason_by_code[str(c)] = "stop_loss"
            for c in out_of_pool_sell:
                if c and str(c) not in reason_by_code:
                    reason_by_code[str(c)] = "out_of_pool"
            for c in rebalance_out:
                if c and str(c) not in reason_by_code:
                    reason_by_code[str(c)] = "rebalance"

        if sell_mode in ("t1_gap_roll", "gap_roll", "advanced") and sell_codes:
            prev_day_key = _prev_weekday_day_key(int(dk))
            rebalance_set = set(rebalance_out or [])
            forced_set = set(forced_sell or [])
            filtered_sell = []
            filtered_sell_later = []
            for c in sell_codes:
                if c in forced_set:
                    filtered_sell.append(c)
                    continue
                if c not in rebalance_set:
                    filtered_sell.append(c)
                    continue
                p = positions.get(str(c))
                if not p:
                    filtered_sell.append(c)
                    continue
                try:
                    ed = int(p.get("entry_day") or 0)
                except Exception:
                    ed = 0
                if prev_day_key is None or int(ed) != int(prev_day_key):
                    filtered_sell.append(c)
                    continue
                o, _ = _get_oc(str(c), int(dk))
                _, pc = _get_oc(str(c), int(prev_day_key))
                op = None
                if o is not None and pc is not None and float(o) > 0.0 and float(pc) > 0.0:
                    op = float(o / pc - 1.0)
                if op is not None and float(op) > float(very_strong_th):
                    continue
                if op is not None and float(op) < float(weak_th):
                    filtered_sell.append(c)
                    continue
                if op is not None and float(op) >= float(strong_th):
                    filtered_sell_later.append(c)
                    continue
                if int(decision_min) <= 10 * 60:
                    continue
                filtered_sell.append(c)
            sell_codes = filtered_sell
            sell_codes_later = filtered_sell_later

        if bool(enforce_t1) and sell_codes:
            sell_ok = []
            for c in sell_codes:
                p = positions.get(str(c))
                if not p:
                    sell_ok.append(c)
                    continue
                try:
                    ed = int(p.get("entry_day") or 0)
                except Exception:
                    ed = 0
                if ed and int(ed) >= int(dk):
                    continue
                sell_ok.append(c)
            sell_codes = sell_ok
        if bool(enforce_t1) and sell_codes_later:
            sell_ok = []
            for c in sell_codes_later:
                p = positions.get(str(c))
                if not p:
                    sell_ok.append(c)
                    continue
                try:
                    ed = int(p.get("entry_day") or 0)
                except Exception:
                    ed = 0
                if ed and int(ed) >= int(dk):
                    continue
                sell_ok.append(c)
            sell_codes_later = sell_ok

        do_split_sell = sell_mode in ("t1_gap_roll", "gap_roll", "advanced")
        sell_trade_min = None
        if (not bool(do_split_sell)) and bool(decision_px_enabled) and int(decision_min) > (9 * 60 + 30):
            sell_trade_min = int(decision_min)
        buy_min = sell_trade_min if sell_trade_min is not None else buy_trade_min

        positions_before = int(len(positions))
        sell_executed = 0
        sell_blocked: List[str] = []
        if sell_trade_min is not None:
            sell_executed = _sell_at_px(sell_codes, int(dk), use_close=False, reason_by_code=reason_by_code, trade_min=sell_trade_min, blocked_out=sell_blocked)
        else:
            sell_executed += _sell_at_px(sell_codes, int(dk), use_close=False, reason_by_code=reason_by_code, trade_min=None, blocked_out=sell_blocked)
            if sell_codes_later:
                sell_executed += _sell_at_px(sell_codes_later, int(dk), use_close=False, reason_by_code=reason_by_code, trade_min=int(decision_min), blocked_out=sell_blocked)
        buy_executed = 0
        if has_targets and int(day_tn) > 0:
            open_slots = max(0, int(day_tn) - int(len(positions)))
            buy_rows = [r for r in (target_rows or []) if str(r.get("code") or "").strip() not in positions]
            buy_executed = _buy_at_px(buy_rows, int(dk), use_close=False, target_slots=int(open_slots), trade_min=buy_min)

        allow_size = None
        allow_missing = None
        if universe_allow_by_day is not None:
            allow = universe_allow_by_day.get(int(dk))
            allow_missing = allow is None
            allow_size = int(len(allow)) if isinstance(allow, dict) else None

        risk_state = "normal"
        if bool(risk_flat):
            risk_state = "flat"
        elif int(day_tn) < int(tn):
            risk_state = "reduce"

        daily_stats.append(
            {
                "day": int(dk),
                "market_data_ok": bool(market_ok),
                "index_allowed": bool(index_allowed),
                "risk_state": str(risk_state),
                "day_top_n": int(day_tn),
                "raw_candidates": int(raw_candidate_count_by_day.get(int(dk), 0)),
                "pool_filtered": int(pool_filtered_count_by_day.get(int(dk), 0)),
                "candidates_after_pool": int(len(by_day.get(int(dk)) or [])),
                "candidates_before_intraday": int(intraday_before),
                "candidates_after_intraday": int(intraday_after),
                "selected": int(len(sel)),
                "has_targets": bool(has_targets),
                "fallback_hold": bool((not has_targets) and (not risk_flat)),
                "positions_before": int(positions_before),
                "sell_stop_loss": int(len(forced_sell)),
                "sell_out_of_pool": int(len(out_of_pool_sell)),
                "sell_rebalance": int(len(rebalance_out)),
                "sell_planned": int(len(sell_codes) + len(sell_codes_later)),
                "sell_executed": int(sell_executed),
                "sell_blocked_untradeable": int(len(sell_blocked)),
                "buy_executed": int(buy_executed),
                "positions_after": int(len(positions)),
                "pool_allow_size": allow_size,
                "pool_allow_missing": allow_missing,
            }
        )

        eq = _equity_norm_at_px(int(dk), use_close=True)
        rr = float(eq / prev_eq - 1.0) if prev_eq > 0 else 0.0
        rets.append(float(rr))
        if positions:
            trade_days += 1
        curve.append({"day": int(dk), "equity": float(eq), "r": float(rr), "codes": list(positions.keys()), "probs": [positions[c].get("prob") for c in positions.keys()]})
        prev_eq = float(eq)

        for code in list(positions.keys()):
            _, c = _get_oc(str(code), int(dk))
            if c is not None and c > 0.0:
                last_close[str(code)] = float(c)

    win_rate = float(sum(1 for x in rets if float(x) > 0.0) / float(trade_days)) if trade_days > 0 else 0.0
    pos_sum = float(sum([x for x in rets if float(x) > 0.0])) if rets else 0.0
    neg_sum = float(sum([x for x in rets if float(x) < 0.0])) if rets else 0.0
    pf = None
    if neg_sum < 0.0:
        pf = float(pos_sum / abs(float(neg_sum))) if abs(float(neg_sum)) > 0 else None

    mean = float(sum(rets) / len(rets)) if rets else 0.0
    std = float(np.std(np.asarray(rets, dtype=float))) if rets else 0.0
    sharpe = float(mean / std * np.sqrt(250.0)) if std > 0.0 else 0.0

    ann = None
    if day_list and len(day_list) > 1:
        try:
            days = float(len(day_list))
            ann = float((float(eq) ** (365.0 / float(days))) - 1.0)
        except Exception:
            ann = None

    peak = 1.0
    mdd = 0.0
    for p in [c.get("equity") for c in curve]:
        try:
            v = float(p)
        except Exception:
            continue
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd

    return {
        "trade_cost": float(tc),
        "top_n": int(tn),
        "initial_capital": float(base_capital),
        "lot_size": int(lot),
        "stamp_duty_sell": float(stamp_sell),
        "trade_count": int(trade_days),
        "win_rate": float(win_rate),
        "profit_factor": pf,
        "total_return": float(eq - 1.0),
        "annualized_return": ann,
        "sharpe": float(sharpe),
        "max_drawdown": float(mdd),
        "equity_curve": curve,
        "trade_logs": trade_logs,
        "daily_stats": daily_stats,
    }


def _calc_code_candidates(
    code: str,
    start_date: str,
    end_date: str,
    level: KL_TYPE,
    data_src: DATA_SRC,
    autype: AUTYPE,
    rolling_sample_mode: Optional[str],
    rolling_stride: Optional[int],
    side_filter: str,
    enable_rolling_lookback: bool,
    min_prob: float,
    profit_threshold: Optional[float],
    holding_period: int,
    trade_cost: float,
    walk_forward_max_folds: int,
    model_type: Optional[str] = None,
    pretrained_model_key: Optional[str] = None,
    rank_transform_enabled: Optional[bool] = True,
    only_features: Optional[Any] = None,
    feature_preset: Optional[str] = None,
    decision_min: Optional[int] = None,
    stop_loss: float = -0.03,
    enforce_t1: bool = True,
    filter_start_date: Optional[str] = None,
) -> Tuple[str, List[dict], Optional[dict], Dict[int, Dict[str, float]]]:
    code = normalize_code(code)
    side_filter = _normalize_side_filter(side_filter, default="buy")
    use_online_latest = bool(data_src == DATA_SRC.CLICK_HOUSE)
    kl_list = fetch_stock_data(code, level, start_date, end_date, data_src, autype=autype, use_online_latest=use_online_latest)
    daily_oc = _daily_oc_from_kl_list(kl_list, decision_min=decision_min)
    # Annotate each day with low and prev traded-day close so the simulator
    # can apply sell-side limit-down / no-bar tradability checks. Keys are
    # additive: consumers that only know open/close/decision_px are unaffected.
    try:
        _agg_days = _aggregate_daily_ohlc(kl_list)
        _prev_c = None
        for _d in (_agg_days or []):
            _dk = int((_d or {}).get("day") or 0)
            _dd = daily_oc.get(_dk) if isinstance(daily_oc, dict) else None
            if isinstance(_dd, dict):
                _dd["low"] = float(_d.get("low") or 0.0)
                _dd["prev_close"] = float(_prev_c) if _prev_c else None
            try:
                _c = float((_d or {}).get("close") or 0.0)
            except Exception:
                _c = 0.0
            if _c > 0.0:
                _prev_c = float(_c)
    except Exception:
        pass

    min_kl = _env_int("MLCHAN_MIN_KL_SIZE", 500)
    if not kl_list or len(kl_list) < min_kl:
        return code, [], {"detail": "no_data", "kl": int(len(kl_list) if kl_list else 0)}, daily_oc

    _, bsp_dict, _, _ = get_chan_data(
        code,
        True,
        True,
        level,
        data_src,
        preloaded_data=kl_list,
        do_predict=True,
        begin_time=start_date,
        end_time=end_date,
        enable_rolling_lookback=enable_rolling_lookback,
        autype=autype,
        rolling_sample_mode=rolling_sample_mode,
        rolling_stride=rolling_stride,
        side_filter=side_filter,
    )

    min_bsp = _env_int("MLCHAN_MIN_BSP_SIZE", 200)
    if not bsp_dict or len(bsp_dict) < min_bsp:
        return code, [], {"detail": "no_bsp", "bsp": int(len(bsp_dict) if bsp_dict else 0)}, daily_oc

    fixed_ts = None
    if filter_start_date:
        # UTC+8 calendar-day boundary, consistent with cn_day_key_from_ts
        # (was local-tz datetime.timestamp() -> host-dependent)
        fixed_ts = _cn_ts_from_date_str(filter_start_date)

    used_model_type = str(model_type or "").strip().lower() or "lr"
    if used_model_type in ["logistic_regression", "logreg"]:
        used_model_type = "lr"

    preds = []
    bundle = None
    if str(pretrained_model_key or "").strip():
        try:
            bundle = load_pretrained_model_bundle_by_key(str(pretrained_model_key).strip())
        except Exception:
            bundle = None
    if isinstance(bundle, dict) and (bundle.get("model") is not None) and isinstance(bundle.get("feature_meta"), list) and bundle.get("feature_meta"):
        model = bundle.get("model")
        feature_meta = list(bundle.get("feature_meta") or [])
        infos_all = list((bsp_dict or {}).values())
        if side_filter != "both":
            infos_all = [info for info in infos_all if _side_filter_allows((info or {}).get("is_buy", False), side_filter)]
        try:
            infos_all.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
        except Exception:
            pass
        if fixed_ts:
            filtered_infos = []
            for info in infos_all:
                try:
                    ts = int(getattr((info or {}).get("open_time"), "ts", 0) or 0)
                except Exception:
                    ts = 0
                if ts and int(ts) >= int(fixed_ts):
                    filtered_infos.append(info)
            infos_all = filtered_infos

        feats = []
        info_ts = []
        for info in (infos_all or []):
            feat = None
            try:
                feat = (info or {}).get("feature")
            except Exception:
                feat = None
            feat_dict = {}
            if feat is None:
                feat_dict = {}
            elif isinstance(feat, dict):
                feat_dict = dict(feat)
            elif hasattr(feat, "items"):
                try:
                    feat_dict = dict(feat.items())
                except Exception:
                    feat_dict = {}
            feats.append([feat_dict.get(k, -9999999) for k in feature_meta])
            try:
                info_ts.append(int(getattr((info or {}).get("open_time"), "ts", 0) or 0))
            except Exception:
                info_ts.append(0)

        probs = []
        try:
            probs = list(predict_proba_1(model, feats) or [])
        except Exception:
            probs = []

        n = min(len(probs), len(infos_all), len(info_ts))
        for i in range(n):
            info = infos_all[i]
            ts = int(info_ts[i] or 0)
            if ts <= 0:
                continue
            try:
                p = float(probs[i])
            except Exception:
                continue
            preds.append(
                {
                    "ts": int(ts),
                    "prob": float(p),
                    "label": 0,
                    "is_buy": bool((info or {}).get("is_buy")),
                    "profit": float(_profit_for_info(info, lookahead=int(holding_period), enforce_t1=bool(enforce_t1))),
                    "min_profit": float(_min_profit_for_info(info, lookahead=int(holding_period), enforce_t1=bool(enforce_t1))),
                }
            )
    else:
        _, _, acc = train_time_split_backtest(
            bsp_dict,
            model_type=used_model_type,
            backtest_mode="walk_forward",
            only_features=only_features,
            rank_transform_enabled=rank_transform_enabled,
            walk_forward_max_folds=int(walk_forward_max_folds),
            profit_threshold=profit_threshold,
            profit_lookahead=int(holding_period),
            trade_cost=float(trade_cost or 0.0),
            enforce_t1=bool(enforce_t1),
            fixed_test_start_ts=fixed_ts,
            side_filter=side_filter,
            feature_preset=feature_preset,
        )
        if isinstance(acc, dict):
            preds = list(acc.get("predictions") or [])

    kl_idx_map = {}
    for i, k in enumerate(kl_list or []):
        try:
            kl_idx_map[int(getattr(getattr(k, "time", None), "ts", 0) or 0)] = i
        except Exception:
            pass

    best = {}
    for r in preds:
        if not isinstance(r, dict):
            continue
        if not bool(r.get("is_buy")):
            continue
        try:
            prob = float(r.get("prob"))
        except Exception:
            continue
        if not np.isfinite(prob) or float(prob) < float(min_prob):
            continue
        try:
            ts = int(r.get("ts") or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            continue

        # Check Limit Up
        idx = kl_idx_map.get(ts)
        if idx is not None and idx > 0:
            k = kl_list[idx]
            try:
                c_px = float(getattr(k, "close", 0.0) or 0.0)
                h_px = float(getattr(k, "high", 0.0) or 0.0)
                prev = kl_list[idx - 1]
                p_px = float(getattr(prev, "close", 0.0) or c_px)
                limit_rate = _limit_rate_for_code(code)
                if p_px > 0 and c_px >= p_px * (1.0 + limit_rate) and abs(c_px - h_px) < 0.01:
                    continue
            except Exception:
                pass


        dk = _day_key_from_ts(ts)
        if dk is None:
            continue
        cur = best.get(dk)
        if cur is None:
            best[dk] = r
            continue
        try:
            cur_prob = float(cur.get("prob"))
        except Exception:
            cur_prob = float("-inf")
        if float(prob) > float(cur_prob):
            best[dk] = r

    out = []
    for dk, r in best.items():
        try:
            pr = float(r.get("profit"))
        except Exception:
            pr = 0.0
        try:
            min_pr = float(r.get("min_profit"))
        except Exception:
            min_pr = 0.0
        realized = float(pr)
        if float(min_pr) <= float(stop_loss):
            realized = float(stop_loss)
        try:
            ts = int(r.get("ts") or 0)
        except Exception:
            ts = 0
        out.append({"day": int(dk), "ts": int(ts), "prob": float(r.get("prob")), "profit": float(realized), "code": code})
    
    if filter_start_date:
        try:
            # same UTC+8 boundary convention as fixed_ts above
            fs_ts = _cn_ts_from_date_str(filter_start_date)
            if fs_ts is not None:
                out = [r for r in out if int(r.get("ts") or 0) >= int(fs_ts)]
        except Exception:
            pass

    return code, out, {"detail": "ok", "candidates": int(len(out))}, daily_oc


class WolfpackBacktester:
    def run(
        self,
        pool_codes: List[str],
        start_date: str,
        end_date: str,
        frequency: str = "30m",
        data_src: str = "clickhouse",
        autype: str = "qfq",
        enable_rolling_lookback: bool = True,
        rolling_sample_mode: str = "every_bar",
        rolling_stride: int = 1,
        min_prob: float = 0.25,
        holding_period: int = 3,
        trade_cost: float = 0.0002,
        profit_threshold: Optional[float] = 0.01,
        walk_forward_max_folds: int = 25,
        max_workers: int = 8,
        portfolio_top_n: int = 3,
        index_filter_enabled: bool = True,
        index_code: str = "399006",
        index_ma_window: int = 20,
        stop_loss: float = -0.03,
        enforce_t1: bool = True,
        sell_mode: str = "baseline",
        t1_decision_time: str = "10:00",
        t1_gap_strong_threshold: float = 0.03,
        t1_gap_weak_threshold: float = -0.02,
        t1_gap_very_strong_threshold: float = 0.05,
        risk_filter_enabled: bool = False,
        risk_index_code: Optional[str] = None,
        risk_mode: str = "both",
        risk_drop_threshold: float = -0.03,
        risk_flat_drop_threshold: float = -0.05,
        risk_atr_window: int = 14,
        risk_atr_pct_threshold: float = 0.04,
        risk_flat_atr_pct_threshold: float = 0.06,
        risk_gap_down_threshold: float = -0.015,
        risk_intraday_enabled: bool = True,
        risk_intraday_bear_threshold: float = -0.015,
        risk_intraday_bull_threshold: float = -0.03,
        risk_trend_ma_window: int = 20,
        risk_data_fail_mode: str = "reduce",
        risk_intraday_data_fail_mode: str = "reduce",
        pool_id: Optional[str] = None,
        pool_point_in_time: bool = False,
        model: Optional[str] = None,
        pretrained_model_key: Optional[str] = None,
        rank_mode: Optional[str] = None,
        lookback_days: Optional[int] = None,
        only_features: Optional[str] = None,
        feature_preset: Optional[str] = None,
        side_filter: str = "buy",
    ) -> Dict[str, Any]:
        """Run the wolfpack backtest.

        Parameter groups:
        - pool/data: pool_codes, start_date/end_date, frequency
          (30m/60m/15m/5m/1m/1d), data_src (clickhouse/baostock/csv/tdx/ccxt;
          akshare disabled), autype (qfq/hfq/none), pool_id +
          pool_point_in_time (point-in-time constituent snapshots).
        - signal/model: enable_rolling_lookback, rolling_sample_mode,
          rolling_stride, min_prob, holding_period, profit_threshold,
          walk_forward_max_folds, model, pretrained_model_key, rank_mode,
          lookback_days, only_features, feature_preset, side_filter.
        - portfolio/exec: portfolio_top_n, trade_cost, stop_loss, enforce_t1,
          max_workers (clamped to [1, min(pool_size,
          _WOLFPACK_MAX_WORKERS_CAP)]), sell_mode, t1_decision_time,
          t1_gap_*_threshold.
        - filters: index_filter_enabled/index_code/index_ma_window,
          risk_filter_enabled + risk_* (risk_mode, thresholds, atr window,
          intraday thresholds, fail modes). See module docstring for the
          MLCHAN_* env knobs.
        """
        pool_id = str(pool_id or "").strip() or None
        pool_point_in_time = bool(pool_point_in_time)
        side_filter = _normalize_side_filter(side_filter, default="buy")

        pool_codes = [normalize_code(str(c)) for c in (pool_codes or []) if str(c or "").strip()]
        pool_codes = list(dict.fromkeys(pool_codes))

        universe_allow_by_day = None
        pool_snapshot_count = 0
        first_pool_snapshot_day = None
        pool_snapshots: List[dict] = []
        pool_snapshot_members: List[dict] = []
        if pool_point_in_time and pool_id and (":" in pool_id):
            try:
                from DataAPI.ClickHouseAPI import CClickHouseAPI

                snap = CClickHouseAPI.get_pool_snapshot_members(pool_id, start_date, end_date, table="stock_constituent")
                if snap:
                    def _normalize_member_code(v) -> Optional[str]:
                        s = str(v or "").strip()
                        if not s:
                            return None
                        try:
                            c = normalize_code(s)
                        except Exception:
                            c = s.strip().lower()
                        c = str(c or "").strip().lower()
                        if c.count(".") >= 2:
                            parts = [p for p in c.split(".") if p]
                            prefix = None
                            digits = None
                            for p in parts:
                                if p in ("sh", "sz", "bj"):
                                    prefix = p
                                    break
                            for p in reversed(parts):
                                if p.isdigit() and len(p) == 6:
                                    digits = p
                                    break
                            if prefix and digits:
                                c = f"{prefix}.{digits}"
                            elif prefix and len(parts) >= 2:
                                c = f"{prefix}.{parts[1]}"
                        c = str(c or "").strip()
                        if not c:
                            return None
                        return c

                    parsed = []
                    for k, codes in (snap or {}).items():
                        try:
                            d = datetime.datetime.strptime(str(k), "%Y-%m-%d").date()
                        except Exception:
                            continue
                        dk = int(d.year) * 10000 + int(d.month) * 100 + int(d.day)
                        cc = []
                        for c in (codes or []):
                            nc = _normalize_member_code(c)
                            if nc:
                                cc.append(nc)
                        cc = list(dict.fromkeys(cc))
                        if dk > 0 and cc:
                            parsed.append((dk, cc))
                    parsed.sort(key=lambda x: int(x[0]))
                    if parsed:
                        pool_snapshot_count = int(len(parsed))
                        pool_snapshots = [
                            {
                                "day": int(dk),
                                "snapshot_date": f"{str(int(dk))[0:4]}-{str(int(dk))[4:6]}-{str(int(dk))[6:8]}",
                                "stock_count": int(len(cc)),
                            }
                            for dk, cc in parsed
                        ]
                        pool_snapshot_members = [
                            {
                                "day": int(dk),
                                "snapshot_date": f"{str(int(dk))[0:4]}-{str(int(dk))[4:6]}-{str(int(dk))[6:8]}",
                                "codes": list(cc),
                            }
                            for dk, cc in parsed
                        ]
                        snap_days = [int(x[0]) for x in parsed]
                        snap_sets = [set(x[1]) for x in parsed]
                        first_pool_snapshot_day = int(snap_days[0]) if snap_days else None
                        all_codes = []
                        for ss in snap_sets:
                            all_codes.extend(list(ss))
                        all_codes = list(dict.fromkeys([c for c in all_codes if c]))
                        if all_codes:
                            pool_codes = all_codes
                            day_list = _date_range_days(start_date, end_date)
                            allow_map: Dict[int, Dict[str, bool]] = {}
                            for dk in (day_list or []):
                                idx = bisect_right(snap_days, int(dk)) - 1
                                if idx < 0:
                                    continue
                                allow_set = snap_sets[int(idx)]
                                allow_map[int(dk)] = {c: True for c in allow_set}
                            universe_allow_by_day = allow_map
            except Exception:
                universe_allow_by_day = None

        if not pool_codes:
            return {"status": "error", "detail": "no_pool_codes"}

        freq = str(frequency or "30m").strip().lower()
        freq_map = {"30m": KL_TYPE.K_30M, "60m": KL_TYPE.K_60M, "15m": KL_TYPE.K_15M, "5m": KL_TYPE.K_5M, "1m": KL_TYPE.K_1M, "1d": KL_TYPE.K_DAY}
        level = freq_map.get(freq, KL_TYPE.K_30M)

        src_raw = str(data_src or "clickhouse").strip().lower()
        if src_raw == "akshare":
            return {"status": "error", "detail": "akshare data_src is disabled"}
        src_map = {"baostock": DATA_SRC.BAO_STOCK, "ccxt": DATA_SRC.CCXT, "csv": DATA_SRC.CSV, "tdx": DATA_SRC.TDX, "tongdaxin": DATA_SRC.TDX, "clickhouse": DATA_SRC.CLICK_HOUSE}
        src = src_map.get(src_raw, DATA_SRC.CLICK_HOUSE)

        raw_autype = str(autype or "qfq").strip().lower()
        autype_map = {"qfq": AUTYPE.QFQ, "hfq": AUTYPE.HFQ, "none": AUTYPE.NONE}
        au = autype_map.get(raw_autype, AUTYPE.QFQ)

        try:
            mp = float(min_prob)
        except Exception:
            mp = 0.25
        mp = 0.0 if mp < 0.0 else (1.0 if mp > 1.0 else mp)

        decision_min = _parse_hhmm_to_minutes(t1_decision_time, default_min=10 * 60)
        decision_min_for_oc = int(decision_min) if level != KL_TYPE.K_DAY else None

        try:
            mw = int(max_workers)
        except Exception:
            mw = 8
        mw = max(1, min(int(mw), int(len(pool_codes)), int(_WOLFPACK_MAX_WORKERS_CAP)))

        all_candidates = []
        per_code = {}
        daily_oc_by_code: Dict[str, Dict[int, Dict[str, float]]] = {}
        allow_by_day = None
        if bool(index_filter_enabled):
            allow_by_day = _load_index_filter_day_set(index_code, start_date, end_date, src, au, ma_window=int(index_ma_window))
        if first_pool_snapshot_day is not None and int(first_pool_snapshot_day) > 0:
            day_list = _date_range_days(start_date, end_date)
            trade_allow_by_day: Dict[int, bool] = {}
            for dk in (day_list or []):
                if int(dk) < int(first_pool_snapshot_day):
                    trade_allow_by_day[int(dk)] = False
                    continue
                if bool(index_filter_enabled) and allow_by_day is not None:
                    trade_allow_by_day[int(dk)] = bool(allow_by_day.get(int(dk), False))
                else:
                    trade_allow_by_day[int(dk)] = True
            allow_by_day = trade_allow_by_day
        risk_by_day = None
        if bool(risk_filter_enabled):
            risk_idx = str(risk_index_code or index_code or "399006")
            risk_by_day = _load_index_risk_day_map(
                risk_idx,
                start_date,
                end_date,
                src,
                au,
                atr_window=int(risk_atr_window),
                drop_threshold=float(risk_drop_threshold),
                atr_pct_threshold=float(risk_atr_pct_threshold),
                flat_drop_threshold=float(risk_flat_drop_threshold),
                flat_atr_pct_threshold=float(risk_flat_atr_pct_threshold),
                gap_down_threshold=float(risk_gap_down_threshold),
                mode=str(risk_mode or "both"),
            )
            if not risk_by_day:
                raw_fail = str(risk_data_fail_mode or "reduce").strip().lower()
                if raw_fail in ("reduce", "flat"):
                    risk_by_day = {int(dk): str(raw_fail) for dk in _date_range_days(start_date, end_date)}

        intraday_cfg = None
        if bool(risk_intraday_enabled):
            daily = _load_index_daily_ohlc(index_code, start_date, end_date, src, au)
            if daily:
                try:
                    w = int(risk_trend_ma_window or 20)
                except Exception:
                    w = 20
                w = max(2, int(w))
                closes = [float(d.get("close") or 0.0) for d in daily]
                days = [int(d.get("day") or 0) for d in daily]
                prev_close_by_day = {}
                ma20_by_day = {}
                for i in range(len(days)):
                    dk = int(days[i])
                    if dk <= 0:
                        continue
                    if i - 1 >= 0 and closes[i - 1] and closes[i - 1] > 0.0:
                        prev_close_by_day[dk] = float(closes[i - 1])
                    if i >= w:
                        seg = closes[i - w : i]
                        if seg and all((x and x > 0.0) for x in seg):
                            ma20_by_day[dk] = float(sum(seg) / float(w))

                ts_close = _load_index_intraday_close_series(index_code, start_date, end_date, src, au)
                ts_list = [int(x[0]) for x in ts_close]
                close_list = [float(x[1]) for x in ts_close]

                def _get_index_close_at_ts(ts: int):
                    if not ts_list:
                        return None
                    try:
                        t = int(ts)
                    except Exception:
                        return None
                    if t <= 0:
                        return None
                    idx = bisect_right(ts_list, int(t)) - 1
                    if idx < 0:
                        return None
                    return float(close_list[idx])

                intraday_cfg = {
                    "enabled": True,
                    "bear_threshold": float(risk_intraday_bear_threshold),
                    "bull_threshold": float(risk_intraday_bull_threshold),
                    "data_fail_mode": str(risk_intraday_data_fail_mode or "reduce"),
                    "ma20_by_day": ma20_by_day,
                    "prev_close_by_day": prev_close_by_day,
                    "get_index_close_at_ts": _get_index_close_at_ts,
                }
            else:
                intraday_cfg = {
                    "enabled": True,
                    "bear_threshold": float(risk_intraday_bear_threshold),
                    "bull_threshold": float(risk_intraday_bull_threshold),
                    "data_fail_mode": str(risk_intraday_data_fail_mode or "reduce"),
                    "ma20_by_day": {},
                    "prev_close_by_day": {},
                    "get_index_close_at_ts": None,
                }

        used_rank_mode = str(rank_mode or "").strip().lower() or None
        if used_rank_mode in ("raw", "none", "off", "disabled"):
            used_rank_transform_enabled = False
        else:
            used_rank_transform_enabled = True

        used_model_type = str(model or "").strip().lower() or "lr"
        if used_model_type in ["logistic_regression", "logreg"]:
            used_model_type = "lr"

        used_lookback_days = None
        try:
            used_lookback_days = int(lookback_days) if lookback_days is not None else None
        except Exception:
            used_lookback_days = None
        if used_lookback_days is None or used_lookback_days <= 0:
            try:
                train_years = _env_float("MLCHAN_DESIRED_TRAIN_YEARS", 1.0)
                used_lookback_days = int((train_years + 3.0) * 365)
            except Exception:
                used_lookback_days = 0
        try:
            s_dt = datetime.datetime.strptime(str(start_date), "%Y-%m-%d")
            ext_s_dt = s_dt - datetime.timedelta(days=int(used_lookback_days or 0))
            extended_start_date = ext_s_dt.strftime("%Y-%m-%d")
        except Exception:
            extended_start_date = str(start_date)

        with concurrent.futures.ProcessPoolExecutor(max_workers=mw) as executor:
            futures = {
                executor.submit(
                    _calc_code_candidates,
                    code,
                    extended_start_date,
                    end_date,
                    level,
                    src,
                    au,
                    str(rolling_sample_mode or "").strip().lower() or "every_bar",
                    int(rolling_stride) if rolling_stride is not None else 1,
                    side_filter,
                    bool(enable_rolling_lookback),
                    float(mp),
                    profit_threshold,
                    int(holding_period),
                    float(trade_cost or 0.0),
                    int(walk_forward_max_folds),
                    used_model_type,
                    str(pretrained_model_key or "").strip() or None,
                    bool(used_rank_transform_enabled),
                    only_features,
                    feature_preset,
                    decision_min_for_oc,
                    float(stop_loss),
                    bool(enforce_t1),
                    str(start_date),
                ): code
                for code in pool_codes
            }
            for fut in concurrent.futures.as_completed(futures):
                code = futures.get(fut)
                try:
                    cc, rows, meta, daily_oc = fut.result()
                except Exception as e:
                    per_code[str(code)] = {"detail": str(e)}
                    continue
                if meta:
                    per_code[str(cc)] = dict(meta)
                if rows:
                    all_candidates.extend(rows)
                if daily_oc:
                    try:
                        daily_oc_by_code[str(cc)] = dict(daily_oc)
                    except Exception:
                        pass

        # as_completed() yield order is nondeterministic; re-sort the merged
        # list by (day, -prob, code, ts) so every downstream consumer sees a
        # reproducible candidate order (the per-day top-N selection re-sorts
        # with the same ranking via _candidate_sort_key).
        try:
            all_candidates.sort(key=lambda r: (int((r or {}).get("day") or 0),) + _candidate_sort_key(r))
        except Exception:
            pass

        portfolio = _simulate_universe_daily_topn(
            all_candidates,
            start_date,
            end_date,
            trade_cost=float(trade_cost or 0.0),
            top_n=int(portfolio_top_n),
            universe_allow_by_day=universe_allow_by_day,
            index_allow_by_day=allow_by_day,
            risk_by_day=risk_by_day,
            intraday_cfg=intraday_cfg,
            daily_oc_by_code=daily_oc_by_code,
            sell_mode=str(sell_mode or "baseline").strip().lower() or "baseline",
            t1_decision_time=str(t1_decision_time or "10:00"),
            t1_gap_strong_threshold=float(t1_gap_strong_threshold),
            t1_gap_weak_threshold=float(t1_gap_weak_threshold),
            t1_gap_very_strong_threshold=float(t1_gap_very_strong_threshold),
            enforce_t1=bool(enforce_t1),
            stop_loss=float(stop_loss),
        )
        return {
            "status": "success",
            "pool_count": int(len(pool_codes)),
            "candidates": int(len(all_candidates)),
            "params": {
                "frequency": freq,
                "data_src": src_raw,
                "autype": raw_autype,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "pool_id": str(pool_id) if pool_id else None,
                "pool_point_in_time": bool(pool_point_in_time),
                "pool_snapshot_count": int(pool_snapshot_count),
                "pool_snapshots": pool_snapshots,
                "pool_snapshot_members": pool_snapshot_members,
                "rolling_sample_mode": str(rolling_sample_mode or "").strip().lower() or "every_bar",
                "rolling_stride": int(rolling_stride) if rolling_stride is not None else 1,
                "min_prob": float(mp),
                "holding_period": int(holding_period),
                "max_workers": int(mw),
                "trade_cost": float(trade_cost or 0.0),
                "profit_threshold": profit_threshold,
                "walk_forward_max_folds": int(walk_forward_max_folds),
                "portfolio_top_n": int(portfolio_top_n),
                "index_filter_enabled": bool(index_filter_enabled),
                "index_code": str(index_code),
                "index_ma_window": int(index_ma_window),
                "stop_loss": float(stop_loss),
                "enforce_t1": bool(enforce_t1),
                "sell_mode": str(sell_mode or "baseline").strip().lower() or "baseline",
                "t1_decision_time": str(t1_decision_time or "10:00"),
                "t1_gap_strong_threshold": float(t1_gap_strong_threshold),
                "t1_gap_weak_threshold": float(t1_gap_weak_threshold),
                "t1_gap_very_strong_threshold": float(t1_gap_very_strong_threshold),
                "risk_filter_enabled": bool(risk_filter_enabled),
                "risk_mode": str(risk_mode or "both"),
                "risk_drop_threshold": float(risk_drop_threshold),
                "risk_flat_drop_threshold": float(risk_flat_drop_threshold),
                "risk_atr_window": int(risk_atr_window),
                "risk_atr_pct_threshold": float(risk_atr_pct_threshold),
                "risk_flat_atr_pct_threshold": float(risk_flat_atr_pct_threshold),
                "risk_gap_down_threshold": float(risk_gap_down_threshold),
                "risk_intraday_enabled": bool(risk_intraday_enabled),
                "risk_intraday_bear_threshold": float(risk_intraday_bear_threshold),
                "risk_intraday_bull_threshold": float(risk_intraday_bull_threshold),
                "risk_trend_ma_window": int(risk_trend_ma_window),
                "risk_data_fail_mode": str(risk_data_fail_mode or "reduce"),
                "risk_intraday_data_fail_mode": str(risk_intraday_data_fail_mode or "reduce"),
                "model": str(used_model_type),
                "pretrained_model_key": str(pretrained_model_key) if str(pretrained_model_key or "").strip() else None,
                "rank_mode": str(used_rank_mode) if used_rank_mode else None,
                "rank_transform_enabled": bool(used_rank_transform_enabled),
                "lookback_days": int(used_lookback_days or 0),
                "only_features": only_features,
                "feature_preset": feature_preset,
                "side_filter": side_filter,
            },
            "portfolio": portfolio,
            "per_code": per_code,
        }
