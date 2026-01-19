import datetime
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from backend.chan_service import (
    fetch_stock_data,
    get_chan_data,
    train_time_split_backtest,
    normalize_code,
)


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
    kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE))
    if (not kl_list) or len(kl_list) < mw + 5:
        alt_src = None
        if data_src != DATA_SRC.AK_SHARE:
            alt_src = DATA_SRC.AK_SHARE
        elif data_src != DATA_SRC.BAO_STOCK:
            alt_src = DATA_SRC.BAO_STOCK
        if alt_src is not None:
            kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, start_date, end_date, alt_src, autype=autype, use_online_latest=True)
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


def _simulate_universe_daily_topn(
    candidates: List[dict],
    start_date: str,
    end_date: str,
    trade_cost: float = 0.0,
    top_n: int = 3,
    index_allow_by_day: Optional[Dict[int, bool]] = None,
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
        rows = by_day.get(int(dk)) or []
        rows.sort(key=lambda x: float(x.get("prob", float("-inf"))), reverse=True)
        sel = rows[:tn]
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
) -> Tuple[str, List[dict], Optional[dict]]:
    code = normalize_code(code)
    use_online_latest = bool(data_src == DATA_SRC.CLICK_HOUSE)
    kl_list = fetch_stock_data(code, level, start_date, end_date, data_src, autype=autype, use_online_latest=use_online_latest)
    if not kl_list or len(kl_list) < 500:
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
    if not bsp_dict or len(bsp_dict) < 200:
        return code, [], {"detail": "no_bsp", "bsp": int(len(bsp_dict) if bsp_dict else 0)}

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
    )
    preds = []
    if isinstance(acc, dict):
        preds = list(acc.get("predictions") or [])

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
        out.append({"day": int(dk), "prob": float(r.get("prob")), "profit": float(realized), "code": code})
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=mw) as executor:
            futures = {
                executor.submit(
                    _calc_code_candidates,
                    code,
                    start_date,
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
                "rank_transform_enabled": True,
                "only_features": ["volatility_250", "bsp3_zs_height"],
                "model": "lr",
            },
            "portfolio": portfolio,
            "per_code": per_code,
        }
