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
    normalize_code,
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


def _day_key_from_ts(ts: int) -> Optional[int]:
    try:
        dt = datetime.datetime.fromtimestamp(int(ts))
    except Exception:
        return None
    return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)


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


def _simulate_universe_daily_top1(
    candidates: List[dict],
    start_date: str,
    end_date: str,
    trade_cost: float = 0.0,
) -> dict:
    try:
        tc = float(trade_cost or 0.0)
    except Exception:
        tc = 0.0

    best_by_day: Dict[int, dict] = {}
    for r in (candidates or []):
        if not isinstance(r, dict):
            continue
        try:
            dk = int(r.get("day") or 0)
        except Exception:
            dk = 0
        if dk <= 0:
            continue
        cur = best_by_day.get(dk)
        if cur is None:
            best_by_day[dk] = r
            continue
        try:
            p0 = float(cur.get("prob"))
        except Exception:
            p0 = float("-inf")
        try:
            p1 = float(r.get("prob"))
        except Exception:
            p1 = float("-inf")
        if float(p1) > float(p0):
            best_by_day[dk] = r

    day_list = _date_range_days(start_date, end_date)
    eq = 1.0
    rets = []
    curve = []
    trade_count = 0
    for dk in day_list:
        r = best_by_day.get(dk)
        rr = 0.0
        code = None
        prob = None
        if r is not None:
            try:
                pr = float(r.get("profit"))
            except Exception:
                pr = 0.0
            if not np.isfinite(pr):
                pr = 0.0
            rr = float(pr) - float(tc)
            trade_count += 1
            code = r.get("code")
            try:
                prob = float(r.get("prob"))
            except Exception:
                prob = None
        rets.append(float(rr))
        eq = float(eq) * float(1.0 + float(rr))
        curve.append({"day": int(dk), "equity": float(eq), "r": float(rr), "code": code, "prob": prob})

    win_rate = float(sum(1 for x in rets if float(x) > 0.0) / float(trade_count)) if trade_count > 0 else 0.0
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
        "trade_count": int(trade_count),
        "win_rate": float(win_rate),
        "profit_factor": pf,
        "total_return": float(eq - 1.0),
        "annualized_return": ann,
        "sharpe": float(sharpe),
        "max_drawdown": float(mdd),
        "equity_curve": curve,
    }


def _load_index_filter_day_set(
    index_code: str,
    start_date: str,
    end_date: str,
    data_src: DATA_SRC,
    autype: AUTYPE,
    ma_window: int = 20,
) -> Optional[Dict[int, bool]]:
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
        alt_src = None
        if data_src in (DATA_SRC.AK_SHARE, DATA_SRC.BAO_STOCK):
            if data_src == DATA_SRC.AK_SHARE:
                alt_src = DATA_SRC.BAO_STOCK
            else:
                alt_src = DATA_SRC.AK_SHARE
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

    by_day = {}
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

    daily = [by_day[k] for k in sorted(by_day.keys())]
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

    by_day = {}
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
    index_allow_by_day: Optional[Dict[int, bool]] = None,
    risk_by_day: Optional[Dict[int, str]] = None,
    intraday_cfg: Optional[dict] = None,
) -> dict:
    try:
        tc = float(trade_cost or 0.0)
    except Exception:
        tc = 0.0
    try:
        tn = int(top_n)
    except Exception:
        tn = 3
    tn = 1 if tn <= 1 else tn

    by_day: Dict[int, List[dict]] = {}
    for r in (candidates or []):
        if not isinstance(r, dict):
            continue
        try:
            dk = int(r.get("day") or 0)
        except Exception:
            dk = 0
        if dk <= 0:
            continue
        by_day.setdefault(dk, []).append(r)

    day_list = _date_range_days(start_date, end_date)
    eq = 1.0
    rets = []
    curve = []
    trade_days = 0

    for dk in day_list:
        if index_allow_by_day is not None:
            if not bool(index_allow_by_day.get(int(dk), False)):
                rets.append(0.0)
                eq = float(eq) * 1.0
                curve.append({"day": int(dk), "equity": float(eq), "r": 0.0, "codes": [], "probs": []})
                continue
        if risk_by_day is not None:
            s = str(risk_by_day.get(int(dk)) or "").strip().lower()
            if s == "flat":
                rets.append(0.0)
                eq = float(eq) * 1.0
                curve.append({"day": int(dk), "equity": float(eq), "r": 0.0, "codes": [], "probs": []})
                continue
            day_tn = 1 if s == "reduce" else tn
        else:
            day_tn = tn
        rows = by_day.get(int(dk)) or []
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
            daily_low = (intraday_cfg.get("daily_low_by_day") or {}).get(int(dk))
            missing = []
            if prev_close is None:
                missing.append("prev_close")
            if ma20 is None:
                missing.append("ma20")
            if (not callable(get_px)) and daily_low is None:
                missing.append("current_price")
            if missing:
                if fail_mode == "flat":
                    rets.append(0.0)
                    eq = float(eq) * 1.0
                    curve.append({"day": int(dk), "equity": float(eq), "r": 0.0, "codes": [], "probs": []})
                    continue
                if fail_mode == "reduce":
                    day_tn = 1

            next_rows = []
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
                if cur_px is None:
                    cur_px = daily_low
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
            rows = next_rows
        rows.sort(key=lambda x: float(x.get("prob", float("-inf"))), reverse=True)
        sel = rows[: int(day_tn)]
        if not sel:
            rets.append(0.0)
            curve.append({"day": int(dk), "equity": float(eq), "r": 0.0, "codes": [], "probs": []})
            continue
        pnls = []
        codes = []
        probs = []
        for r in sel:
            try:
                pr = float(r.get("profit"))
            except Exception:
                pr = 0.0
            if not np.isfinite(pr):
                pr = 0.0
            pnls.append(float(pr) - float(tc))
            codes.append(r.get("code"))
            try:
                probs.append(float(r.get("prob")))
            except Exception:
                probs.append(None)
        rr = float(sum(pnls) / float(len(pnls))) if pnls else 0.0
        trade_days += 1
        rets.append(float(rr))
        eq = float(eq) * float(1.0 + float(rr))
        curve.append({"day": int(dk), "equity": float(eq), "r": float(rr), "codes": codes, "probs": probs})

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
        "trade_count": int(trade_days),
        "win_rate": float(win_rate),
        "profit_factor": pf,
        "total_return": float(eq - 1.0),
        "annualized_return": ann,
        "sharpe": float(sharpe),
        "max_drawdown": float(mdd),
        "equity_curve": curve,
    }


def _calc_code_candidates(
    code: str,
    start_date: str,
    end_date: str,
    level: KL_TYPE,
    data_src: DATA_SRC,
    autype: AUTYPE,
    enable_rolling_lookback: bool,
    min_prob: float,
    profit_threshold: Optional[float],
    holding_period: int,
    trade_cost: float,
    walk_forward_max_folds: int,
    stop_loss: float = -0.03,
    enforce_t1: bool = True,
    filter_start_date: Optional[str] = None,
) -> Tuple[str, List[dict], Optional[dict]]:
    code = normalize_code(code)
    use_online_latest = bool(data_src == DATA_SRC.CLICK_HOUSE)
    kl_list = fetch_stock_data(code, level, start_date, end_date, data_src, autype=autype, use_online_latest=use_online_latest)

    min_kl = _env_int("MLCHAN_MIN_KL_SIZE", 500)
    if not kl_list or len(kl_list) < min_kl:
        return code, [], {"detail": "no_data", "kl": int(len(kl_list) if kl_list else 0)}

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
    )

    min_bsp = _env_int("MLCHAN_MIN_BSP_SIZE", 200)
    if not bsp_dict or len(bsp_dict) < min_bsp:
        return code, [], {"detail": "no_bsp", "bsp": int(len(bsp_dict) if bsp_dict else 0)}

    fixed_ts = None
    if filter_start_date:
        try:
            fixed_ts = int(datetime.datetime.strptime(str(filter_start_date), "%Y-%m-%d").timestamp())
        except Exception:
            fixed_ts = None

    _, _, acc = train_time_split_backtest(
        bsp_dict,
        model_type="lr",
        backtest_mode="walk_forward",
        only_features=["volatility_250", "bsp3_zs_height"],
        rank_transform_enabled=True,
        walk_forward_max_folds=int(walk_forward_max_folds),
        profit_threshold=profit_threshold,
        profit_lookahead=int(holding_period),
        trade_cost=float(trade_cost or 0.0),
        enforce_t1=bool(enforce_t1),
        fixed_test_start_ts=fixed_ts,
    )
    preds = []
    if isinstance(acc, dict):
        preds = list(acc.get("predictions") or [])

    kl_idx_map = {}
    for i, k in enumerate(kl_list or []):
        try:
             kl_idx_map[int(getattr(getattr(k, "time", None), "ts", 0) or 0)] = i
        except:
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
                 prev = kl_list[idx-1]
                 p_px = float(getattr(prev, "close", 0.0) or c_px)
                 limit_rate = 0.095
                 s_code = code.split(".")[-1] if "." in code else code
                 if s_code.startswith("300") or s_code.startswith("688"):
                     limit_rate = 0.195
                 elif s_code.startswith("8") or s_code.startswith("4") or s_code.startswith("92"):
                     limit_rate = 0.295
                     
                 if p_px > 0 and c_px >= p_px * (1.0 + limit_rate) and abs(c_px - h_px) < 0.01:
                     # Block Limit Up Buy
                     continue
             except:
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
            fs_ts = int(datetime.datetime.strptime(filter_start_date, "%Y-%m-%d").timestamp())
            out = [r for r in out if r.get("ts", 0) >= fs_ts]
        except Exception:
            pass

    return code, out, {"detail": "ok", "candidates": int(len(out))}


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
        risk_filter_enabled: bool = False,
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
    ) -> Dict[str, Any]:
        pool_codes = [normalize_code(str(c)) for c in (pool_codes or []) if str(c or "").strip()]
        pool_codes = list(dict.fromkeys(pool_codes))
        if not pool_codes:
            return {"status": "error", "detail": "no_pool_codes"}

        freq = str(frequency or "30m").strip().lower()
        freq_map = {"30m": KL_TYPE.K_30M, "60m": KL_TYPE.K_60M, "15m": KL_TYPE.K_15M, "5m": KL_TYPE.K_5M, "1m": KL_TYPE.K_1M, "1d": KL_TYPE.K_DAY}
        level = freq_map.get(freq, KL_TYPE.K_30M)

        src_raw = str(data_src or "clickhouse").strip().lower()
        src_map = {"baostock": DATA_SRC.BAO_STOCK, "akshare": DATA_SRC.AK_SHARE, "ccxt": DATA_SRC.CCXT, "csv": DATA_SRC.CSV, "clickhouse": DATA_SRC.CLICK_HOUSE}
        src = src_map.get(src_raw, DATA_SRC.CLICK_HOUSE)

        raw_autype = str(autype or "qfq").strip().lower()
        autype_map = {"qfq": AUTYPE.QFQ, "hfq": AUTYPE.HFQ, "none": AUTYPE.NONE}
        au = autype_map.get(raw_autype, AUTYPE.QFQ)

        try:
            mp = float(min_prob)
        except Exception:
            mp = 0.25
        mp = 0.0 if mp < 0.0 else (1.0 if mp > 1.0 else mp)

        try:
            mw = int(max_workers)
        except Exception:
            mw = 8
        mw = max(1, min(int(mw), int(len(pool_codes))))

        all_candidates = []
        per_code = {}
        allow_by_day = None
        if bool(index_filter_enabled):
            allow_by_day = _load_index_filter_day_set(index_code, start_date, end_date, src, au, ma_window=int(index_ma_window))
        risk_by_day = None
        if bool(risk_filter_enabled):
            risk_by_day = _load_index_risk_day_map(
                index_code,
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
                lows = [float(d.get("low") or 0.0) for d in daily]
                prev_close_by_day = {}
                ma20_by_day = {}
                daily_low_by_day = {}
                for i in range(len(days)):
                    dk = int(days[i])
                    if dk <= 0:
                        continue
                    daily_low_by_day[dk] = float(lows[i]) if lows[i] and lows[i] > 0.0 else None
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
                    "daily_low_by_day": daily_low_by_day,
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
                    "daily_low_by_day": {},
                    "get_index_close_at_ts": None,
                }
        try:
            train_years = _env_float("MLCHAN_DESIRED_TRAIN_YEARS", 1.0)
            lookback_days = int((train_years + 3.0) * 365)
            s_dt = datetime.datetime.strptime(str(start_date), "%Y-%m-%d")
            ext_s_dt = s_dt - datetime.timedelta(days=lookback_days)
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
                    bool(enable_rolling_lookback),
                    float(mp),
                    profit_threshold,
                    int(holding_period),
                    float(trade_cost or 0.0),
                    int(walk_forward_max_folds),
                    float(stop_loss),
                    bool(enforce_t1),
                    str(start_date),
                ): code
                for code in pool_codes
            }
            for fut in concurrent.futures.as_completed(futures):
                code = futures.get(fut)
                try:
                    cc, rows, meta = fut.result()
                except Exception as e:
                    per_code[str(code)] = {"detail": str(e)}
                    continue
                if meta:
                    per_code[str(cc)] = dict(meta)
                if rows:
                    all_candidates.extend(rows)

        portfolio = _simulate_universe_daily_topn(
            all_candidates,
            start_date,
            end_date,
            trade_cost=float(trade_cost or 0.0),
            top_n=int(portfolio_top_n),
            index_allow_by_day=allow_by_day,
            risk_by_day=risk_by_day,
            intraday_cfg=intraday_cfg,
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
                "min_prob": float(mp),
                "holding_period": int(holding_period),
                "trade_cost": float(trade_cost or 0.0),
                "profit_threshold": profit_threshold,
                "walk_forward_max_folds": int(walk_forward_max_folds),
                "portfolio_top_n": int(portfolio_top_n),
                "index_filter_enabled": bool(index_filter_enabled),
                "index_code": str(index_code),
                "index_ma_window": int(index_ma_window),
                "stop_loss": float(stop_loss),
                "enforce_t1": bool(enforce_t1),
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
                "rank_transform_enabled": True,
                "only_features": ["volatility_250", "bsp3_zs_height"],
                "model": "lr",
            },
            "portfolio": portfolio,
            "per_code": per_code,
        }
