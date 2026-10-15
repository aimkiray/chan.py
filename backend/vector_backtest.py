import pandas as pd
import numpy as np
import datetime
import concurrent.futures
import os
import traceback
from bisect import bisect_right
from typing import List, Dict, Optional, Tuple, Any
from sqlalchemy.orm import Session

from backend.chan_service import (
    fetch_stock_data,
    CChanCustom,
    stragety_feature,
    extract_state_features_from_cur_lv,
    load_pretrained_model_bundle,
    load_pretrained_model_bundle_by_key,
    predict_proba_1,
    get_stock_name,
    normalize_code,
)
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from ChanConfig import CChanConfig
from backend.storage import StorageManager

def get_chinext50_stocks() -> List[str]:
    """
    Get ChiNext 50 stocks.
    """
    try:
        import akshare as ak
        # Try akshare first
        # 399673 is ChiNext 50 index code
        df = ak.index_stock_cons(symbol="399673")
        if df is not None and not df.empty:
            # assuming column '品种代码' or similar
            # akshare output columns change often, need to be careful
            # typical columns: variety, date, symbol, name...
            # symbol is usually the code.
            if 'stock_code' in df.columns:
                return [normalize_code(str(x)) for x in df['stock_code'].tolist()]
            if '品种代码' in df.columns:
                return [normalize_code(str(x)) for x in df['品种代码'].tolist()]
            # Fallback: check first column
            return [normalize_code(str(x)) for x in df.iloc[:, 0].tolist()]
    except Exception as e:
        print(f"Error getting ChiNext 50 from akshare: {e}")

    return []


def _board_limit_rate(code: str) -> float:
    """Daily price-limit ratio by board, mirroring wolfpack_backtest's rule:
    ChiNext (300) / STAR (688) -> 19.5%, BSE (8/4/92) -> 29.5%, else 9.5%.
    ST stocks' 5% cap can't be told from the code alone."""
    s = str(code or "")
    tail = s.split(".")[-1] if "." in s else s
    if tail.startswith("300") or tail.startswith("688"):
        return 0.195
    if tail.startswith("8") or tail.startswith("4") or tail.startswith("92"):
        return 0.295
    return 0.095


# Bars per year for Sharpe annualization, covering every supported
# `frequency` — previously weekly/monthly runs still used 250.
_ANNUAL_FACTOR_BY_KL_TYPE = {
    KL_TYPE.K_DAY: 250.0,
    KL_TYPE.K_WEEK: 52.0,
    KL_TYPE.K_MON: 12.0,
    KL_TYPE.K_30M: 250.0 * 8.0,
    KL_TYPE.K_60M: 250.0 * 4.0,
    KL_TYPE.K_15M: 250.0 * 16.0,
    KL_TYPE.K_5M: 250.0 * 48.0,
    KL_TYPE.K_1M: 250.0 * 240.0,
}


def _calc_stock_signals(
    code: str,
    begin_time: str,
    end_time: str,
    model_bundle: Any,
    kl_type=KL_TYPE.K_30M,
    data_src=DATA_SRC.CLICK_HOUSE,
    autype=AUTYPE.QFQ,
    enable_rolling_lookback: bool = True,
    score_feature: Optional[str] = None,
) -> pd.DataFrame:
    """
    Calculate signals for a single stock.
    Returns DataFrame with index=time, columns=[score, price].
    """
    try:
        code = normalize_code(str(code))
        kl_list = fetch_stock_data(
            code,
            kl_type,
            begin_time,
            end_time,
            data_src,
            autype=autype
        )
        
        if not kl_list or len(kl_list) < 100:
            return pd.DataFrame()

        # Config
        chan_config = CChanConfig({
            "trigger_step": True, 
            "bi_strict": True,
            "skip_step": 0,
            "divergence_rate": float("inf"),
            "bsp2_follow_1": True, 
            "bsp3_follow_1": True,
            "min_zs_cnt": 0, 
            "bs1_peak": True, 
            "macd_algo": "peak", 
            "bs_type": '1,2,3a,1p,2s,3b', 
            "print_warning": False, 
            "zs_algo": "normal"
        })

        chan = CChanCustom(
            code=code,
            begin_time=begin_time,
            end_time=end_time,
            data_src=data_src,
            lv_list=[kl_type],
            config=chan_config,
            autype=autype,
            preloaded_data=kl_list
        )

        used_score_feature = str(score_feature or "").strip()
        if used_score_feature:
            bst = None
            feature_meta = None
        else:
            bst = None
            if isinstance(model_bundle, dict):
                bst = model_bundle.get("bst")
                if bst is None:
                    bst = model_bundle.get("model")

            feature_meta = model_bundle.get("feature_meta") if isinstance(model_bundle, dict) else None
            if bst is None or not isinstance(feature_meta, list) or len(feature_meta) <= 0:
                return pd.DataFrame()
        
        results = []
        
        # We need to iterate and predict. 
        # Optimized: CChanCustom.step_load() yields snapshots.
        # But we need prediction at *every* bar or specific intervals?
        # User: "Signal: Calculate prediction score every 30 minutes".
        # So yes, every bar (since it is 30m bars).
        
        for chan_snapshot in chan.step_load():
            cur_lv_chan = chan_snapshot[0]
            if len(cur_lv_chan) < 2:
                continue
            
            last_klu = cur_lv_chan[-1][-1]
            # Time
            ts = last_klu.time.to_str()
            price = float(last_klu.close)
            try:
                bar_high = float(last_klu.high)
            except Exception:
                bar_high = price
            try:
                bar_low = float(last_klu.low)
            except Exception:
                bar_low = price
            
            # Feature extraction
            # We need features for the *current* state.
            # Usually features are associated with a BSP.
            # If we want a score at *every* bar, we need a model that accepts *every* bar's state.
            # Most MLChan models are trained on BSPs.
            # IF the user wants "prediction score", and we only have BSP models, we might only get scores at BSPs.
            # BUT, the user said "Calculate prediction score every 30 minutes".
            # If I use a BSP model on a non-BSP bar, the features might be weird or invalid.
            # However, `stragety_feature` calculates features based on current context.
            # Let's assume we can generate a score.
            
            # Actually, `predict_proba_1` needs a feature vector.
            # `stragety_feature` returns a dict.
            # We need to extract features.
            
            try:
                feat_dict = stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback)
                # Add state features
                state_feat = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
                if state_feat:
                    # If state_feat is a dict, update. If it's CFeatures, convert?
                    # Assuming dict based on usage in strategy_runner (passed to add_feat)
                    if isinstance(state_feat, dict):
                        feat_dict.update(state_feat)
                    else:
                        # Fallback if it's an object with to_dict or similar, or just try to merge
                        pass
                
                if used_score_feature:
                    raw = feat_dict.get(used_score_feature, np.nan)
                    try:
                        score = float(raw)
                    except Exception:
                        score = float("nan")
                    if not np.isfinite(score):
                        continue
                else:
                    feat_vec = [feat_dict.get(k, -9999999) for k in feature_meta]
                    score = float(predict_proba_1(bst, [feat_vec])[0])
                
                results.append({
                    "time": pd.to_datetime(ts),
                    "code": code,
                    "score": score,
                    "price": price,
                    "high": bar_high,
                    "low": bar_low,
                })
            except Exception:
                continue
                
        return pd.DataFrame(results)

    except Exception as e:
        print(f"Error processing {code}: {e}")
        return pd.DataFrame()

class VectorBacktester:
    def __init__(self):
        pass

    def run(self,
            pool_codes: List[str],
            start_date: str,
            end_date: str,
            model_key: Optional[str] = None,
            **kwargs
    ) -> Dict[str, Any]:
        """Top-N rotation backtest on model/factor scores.

        Execution semantics (no same-bar lookahead): scores computed from
        bar-t close data fill at bar t+`execution_delay_bars` close
        (default 1). `execution_delay_bars=0` restores the legacy optimistic
        close-auction assumption that the signal bar's own close is fillable.

        Cost model follows the wolfpack_backtest convention: `trade_cost`
        commission charged on both sides (slippage is folded into it;
        default 0.0002, same as WolfpackBacktester.run), `stamp_duty_sell`
        sell-side stamp duty (default 0.001, exempt for ETF-like codes),
        `lot_size` board-lot rounding on buys (default 100).

        Tradability: a bar with no traded price (suspension / data gap) is
        untradable for both sides — missing prices are NOT silently filled.
        Positions held through price-less bars are valued at their last
        traded price (entry price only as initial value) and each
        equity_curve row flags how many positions used a marked/estimated
        price via `estimated_positions`. Bars closed locked at the up limit
        cannot be bought, bars locked at the down limit cannot be sold
        (`check_price_limit`, default True).

        `holding_period` is counted in *bars* of the chosen `frequency`,
        not trading days.
        """
        pool_codes = [normalize_code(str(c)) for c in (pool_codes or []) if str(c or "").strip()]
        pool_codes = list(dict.fromkeys(pool_codes))
        if not pool_codes:
            return {"error": "No pool codes."}

        pool_id = str(kwargs.get("pool_id") or "").strip() or None
        pool_point_in_time = bool(kwargs.get("pool_point_in_time", kwargs.get("point_in_time_pool", False)))

        freq = str(kwargs.get("frequency", "1d") or "1d").strip().lower()
        freq_map = {
            "1d": KL_TYPE.K_DAY,
            "30m": KL_TYPE.K_30M,
            "60m": KL_TYPE.K_60M,
            "15m": KL_TYPE.K_15M,
            "5m": KL_TYPE.K_5M,
            "1m": KL_TYPE.K_1M,
            "1w": KL_TYPE.K_WEEK,
            "1mo": KL_TYPE.K_MON,
        }
        kl_type = freq_map.get(freq, KL_TYPE.K_DAY)

        data_src_raw = str(kwargs.get("data_src", "clickhouse") or "clickhouse").strip().lower()
        if data_src_raw == "akshare":
            return {"error": "akshare data_src is disabled"}
        src_map = {
            "baostock": DATA_SRC.BAO_STOCK,
            "ccxt": DATA_SRC.CCXT,
            "csv": DATA_SRC.CSV,
            "tdx": DATA_SRC.TDX,
            "tongdaxin": DATA_SRC.TDX,
            "clickhouse": DATA_SRC.CLICK_HOUSE,
        }
        data_src = src_map.get(data_src_raw, DATA_SRC.CLICK_HOUSE)

        raw_autype = str(kwargs.get("autype", "qfq") or "qfq").strip().lower()
        autype_map = {
            "qfq": AUTYPE.QFQ,
            "hfq": AUTYPE.HFQ,
            "none": AUTYPE.NONE,
        }
        autype = autype_map.get(raw_autype, AUTYPE.QFQ)
        enable_rolling_lookback = bool(kwargs.get("enable_rolling_lookback", True))

        score_feature = str(kwargs.get("score_feature") or "").strip()

        # 1. Load Model (unless factor backtest)
        model_bundle = None
        if not score_feature:
            if model_key:
                model_bundle = load_pretrained_model_bundle_by_key(model_key)
            else:
                pass
            if not model_bundle:
                return {"error": "No model found. Please train a model first."}

        # 2. Parallel Compute Signals
        # CChan feature extraction is CPU-bound — a ThreadPool would
        # serialize on the GIL. ProcessPool matches strategy_runner's pool
        # model. The worker is a module-level function and every argument is
        # picklable (model_bundle itself is loaded from a pickle file), so
        # Windows' spawn start method works from this worker thread.
        all_signals = []
        try:
            max_workers = int(kwargs.get("max_workers", 8))
        except Exception:
            max_workers = 8
        max_workers = max(1, min(int(max_workers), int(len(pool_codes))))
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _calc_stock_signals,
                    code,
                    start_date,
                    end_date,
                    model_bundle,
                    kl_type,
                    data_src,
                    autype,
                    enable_rolling_lookback,
                    score_feature,
                ): code
                for code in pool_codes
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    df = future.result()
                    if not df.empty:
                        all_signals.append(df)
                except Exception as e:
                    print(f"Stock failed: {futures[future]} {e}")
        
        if not all_signals:
            return {"error": "No signals generated."}
            
        # 3. Merge Signals
        full_df = pd.concat(all_signals)
        full_df = full_df.sort_values("time")

        # Duplicate (time, code) rows (re-listed code, overlapping fetch
        # ranges) make pivot() raise ValueError with no recovery; keep the
        # last occurrence per key — same dedupe intent as strategy_runner's
        # ts-dedupe on kl_list.
        full_df = full_df.drop_duplicates(subset=["time", "code"], keep="last")

        # Pivot to get Score Matrix and Price Matrix
        # Index: Time, Columns: Stocks
        score_df = full_df.pivot(index="time", columns="code", values="score")

        # Prices stay raw (NOT forward-filled): a NaN bar means the stock did
        # not trade — it is untradable that bar, and positions held through
        # it mark at their last traded price instead of a stale filled close.
        raw_price_df = full_df.pivot(index="time", columns="code", values="price")
        has_hl = ("high" in full_df.columns) and ("low" in full_df.columns)
        high_df = full_df.pivot(index="time", columns="code", values="high") if has_hl else None
        low_df = full_df.pivot(index="time", columns="code", values="low") if has_hl else None

        score_df = score_df.astype(float)

        score_mode = str(kwargs.get("score_mode", "raw") or "raw").strip().lower()
        score_higher_is_better = bool(kwargs.get("score_higher_is_better", True))
        if score_mode in ["rank", "cross_section_rank", "cross_sectional_rank", "cs_rank"]:
            # Downstream always picks max via sort_values(ascending=False), so rank the
            # preferred end highest: ascending=True -> highest raw score gets pct=1.0.
            score_df = score_df.rank(axis=1, pct=True, ascending=bool(score_higher_is_better))
        score_df = score_df.fillna(float("-inf"))
        
        # 4. Vector Simulation
        # Parameters
        HOLD_PERIODS = int(kwargs.get("holding_period", 5))
        TOP_N = int(kwargs.get("portfolio_top_n", 2))
        top_frac = kwargs.get("portfolio_top_frac", None)
        if (top_frac is not None) and (TOP_N <= 0):
            try:
                tf = float(top_frac)
            except Exception:
                tf = None
            if tf is not None and tf > 0:
                TOP_N = max(1, int(np.ceil(tf * float(len(pool_codes)))))
        min_signal_score = kwargs.get("min_signal_score", None)
        if min_signal_score in ["", "null", "None"]:
            min_signal_score = None
        try:
            SCORE_THR = float(min_signal_score) if min_signal_score is not None else float("-inf")
        except Exception:
            SCORE_THR = float("-inf")
        try:
            CAPITAL = float(kwargs.get("initial_capital", 100000.0))
        except Exception:
            CAPITAL = 100000.0

        enforce_t1 = bool(kwargs.get("enforce_t1", False))

        # --- Execution / cost model (aligned with wolfpack_backtest) ---
        try:
            EXEC_DELAY = int(kwargs.get("execution_delay_bars", 1))
        except Exception:
            EXEC_DELAY = 1
        EXEC_DELAY = max(0, EXEC_DELAY)
        try:
            # Default matches wolfpack_backtest.run() / WolfpackBacktestRequest
            # (0.0002 per-side commission); an explicit 0.0 still opts out.
            _tc_raw = kwargs.get("trade_cost", 0.0002)
            TRADE_COST = 0.0002 if _tc_raw is None else float(_tc_raw)
        except Exception:
            TRADE_COST = 0.0002
        TRADE_COST = max(0.0, min(1.0, TRADE_COST))
        try:
            _stamp_raw = kwargs.get("stamp_duty_sell", os.environ.get("MLCHAN_STAMP_DUTY_SELL", 0.001))
            STAMP_SELL = 0.001 if _stamp_raw is None else float(_stamp_raw)
        except Exception:
            STAMP_SELL = 0.001
        STAMP_SELL = max(0.0, min(1.0, STAMP_SELL))
        try:
            LOT = int(kwargs.get("lot_size", 100) or 100)
        except Exception:
            LOT = 100
        LOT = max(1, LOT)
        check_price_limit = bool(kwargs.get("check_price_limit", True))

        try:
            # Reuse wolfpack's ETF classification for stamp-duty exemption;
            # lazy import keeps this module light for spawned pool workers.
            from backend.wolfpack_backtest import _is_cn_etf_like
        except Exception:
            def _is_cn_etf_like(code):
                return False

        def _sell_fee_rate_for(code: str) -> float:
            stamp = 0.0 if _is_cn_etf_like(code) else STAMP_SELL
            return max(0.0, min(1.0, TRADE_COST + stamp))

        def _buy_blocked(code: str, px: float, prev_px, bar_high) -> bool:
            """Bar closed locked at the up limit -> no fill (same shape as
            wolfpack's buy-side limit check: pct >= board limit AND close
            pinned to the bar high)."""
            if not check_price_limit:
                return False
            try:
                if prev_px is None or pd.isna(prev_px) or float(prev_px) <= 0:
                    return False
                if bar_high is None or pd.isna(bar_high):
                    return False
                return float(px) >= float(prev_px) * (1.0 + _board_limit_rate(code)) and abs(float(px) - float(bar_high)) < 0.01
            except Exception:
                return False

        def _sell_blocked(code: str, px: float, prev_px, bar_low) -> bool:
            """Bar closed locked at the down limit -> cannot exit."""
            if not check_price_limit:
                return False
            try:
                if prev_px is None or pd.isna(prev_px) or float(prev_px) <= 0:
                    return False
                if bar_low is None or pd.isna(bar_low):
                    return False
                return float(px) <= float(prev_px) * (1.0 - _board_limit_rate(code)) and abs(float(px) - float(bar_low)) < 0.01
            except Exception:
                return False

        def _day_key(x) -> Optional[int]:
            try:
                dt = pd.to_datetime(x)
            except Exception:
                return None
            try:
                return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)
            except Exception:
                return None

        universe_allow_by_day = None
        pool_snapshot_count = 0
        first_pool_snapshot_day = None
        if pool_point_in_time and pool_id and (":" in pool_id):
            try:
                from DataAPI.ClickHouseAPI import CClickHouseAPI

                snap = CClickHouseAPI.get_pool_snapshot_members(pool_id, str(start_date), str(end_date), table="stock_constituent")
                if snap:
                    snap_items = []
                    for k, codes in (snap or {}).items():
                        try:
                            d = datetime.datetime.strptime(str(k), "%Y-%m-%d").date()
                        except Exception:
                            continue
                        dk = int(d.year) * 10000 + int(d.month) * 100 + int(d.day)
                        cc = [normalize_code(str(c)) for c in (codes or []) if str(c or "").strip()]
                        cc = list(dict.fromkeys([c for c in cc if c]))
                        if dk > 0 and cc:
                            snap_items.append((dk, cc))
                    snap_items.sort(key=lambda x: x[0])
                    if snap_items:
                        pool_snapshot_count = int(len(snap_items))
                        snap_days = [x[0] for x in snap_items]
                        snap_codes = [x[1] for x in snap_items]
                        first_pool_snapshot_day = int(snap_days[0]) if snap_days else None
                        universe_allow_by_day = {}
                        uniq_days = []
                        seen_days = set()
                        for t in score_df.index:
                            dk = _day_key(t)
                            if dk is None or dk in seen_days:
                                continue
                            seen_days.add(dk)
                            uniq_days.append(dk)
                        uniq_days.sort()
                        for dk in uniq_days:
                            idx = bisect_right(snap_days, dk) - 1
                            if idx >= 0:
                                universe_allow_by_day[int(dk)] = set(snap_codes[idx])
            except Exception:
                universe_allow_by_day = None
                pool_snapshot_count = 0
        
        # State
        positions = {} # code -> {entry_price, entry_time_idx, shares, entry_day, last_price}
        cash = CAPITAL
        equity_curve = []

        times = score_df.index

        # Previous trading-day close per code — the exchange's price-limit
        # reference for any bar frequency (for daily bars this is simply the
        # prior bar's close). Raw closes grouped by calendar day, ffilled
        # across days the stock didn't trade so a resuming stock still gets
        # its true last-traded reference.
        prev_close_df = None
        try:
            day_keys_arr = []
            for _t in times:
                _dk = _day_key(_t)
                day_keys_arr.append(int(_dk) if _dk is not None else -1)
            day_idx = pd.Index(day_keys_arr)
            day_last = raw_price_df.groupby(day_idx).last().ffill()
            prev_close_df = day_last.shift(1).reindex(day_idx)
            prev_close_df.index = times
        except Exception:
            prev_close_df = None

        for t_idx, t in enumerate(times):
            cur_day = _day_key(t)
            # Bar whose signals fill at this bar's close (see docstring —
            # EXEC_DELAY=1 removes the same-bar lookahead).
            sig_idx = t_idx - EXEC_DELAY
            raw_today = raw_price_df.iloc[t_idx]
            prev_today = prev_close_df.iloc[t_idx] if prev_close_df is not None else None
            high_today = high_df.iloc[t_idx] if high_df is not None else None
            low_today = low_df.iloc[t_idx] if low_df is not None else None

            # 1. Update last traded prices + portfolio value
            current_equity = cash
            to_exit = []
            for code, pos in positions.items():
                raw_px = raw_today.get(code)
                if not pd.isna(raw_px) and float(raw_px) > 0:
                    pos["last_price"] = float(raw_px)
                # Mark at last traded price, not entry price — a suspended /
                # delisted position no longer silently reverts to cost.
                mark = pos.get("last_price") or pos["entry_price"]

                # Check hold period (unit: bars)
                held_duration = t_idx - pos['entry_time_idx']

                entry_day = pos.get("entry_day")
                t1_ok = True
                if bool(enforce_t1) and (cur_day is not None) and (entry_day is not None):
                    t1_ok = int(cur_day) > int(entry_day)
                if held_duration >= HOLD_PERIODS and bool(t1_ok):
                    to_exit.append(code)

                current_equity += pos['shares'] * mark

            # Execute Exits — only at a price the stock actually traded at
            # (raw, not ffilled) and not while locked at the down limit; a
            # suspended or limit-locked position carries over to the next bar.
            for code in to_exit:
                pos = positions.get(code)
                if pos is None:
                    continue
                raw_px = raw_today.get(code)
                if pd.isna(raw_px) or float(raw_px) <= 0:
                    continue
                px = float(raw_px)
                if _sell_blocked(
                    code, px,
                    prev_today.get(code) if prev_today is not None else None,
                    low_today.get(code) if low_today is not None else None,
                ):
                    continue
                positions.pop(code)
                cash += pos['shares'] * px * (1.0 - _sell_fee_rate_for(code))

            # 2. Rank and Buy — decide from the signal bar's scores, fill at
            # this bar's close.
            # Available slots
            open_slots = TOP_N - len(positions)

            can_buy = True
            if first_pool_snapshot_day is not None and cur_day is not None and int(cur_day) < int(first_pool_snapshot_day):
                can_buy = False

            if open_slots > 0 and bool(can_buy) and sig_idx >= 0:
                # Get candidates from the signal bar
                scores = score_df.iloc[sig_idx]
                candidates = scores[scores > SCORE_THR]
                # Pool membership is evaluated at the execution day: what can
                # be bought today is today's constituent set.
                if universe_allow_by_day is not None and cur_day is not None:
                    allow = universe_allow_by_day.get(int(cur_day))
                    if allow is not None:
                        candidates = candidates[candidates.index.isin(allow)]
                candidates = candidates.sort_values(ascending=False)

                # Filter out already held
                candidates = candidates[~candidates.index.isin(positions.keys())]

                to_buy = candidates.head(open_slots).index.tolist()

                if to_buy:
                    # "均仓买入" => Target Weight = 1/N of current equity.
                    target_pos_value = current_equity / TOP_N

                    for code in to_buy:
                        raw_px = raw_today.get(code)
                        if pd.isna(raw_px) or float(raw_px) <= 0:
                            continue
                        price = float(raw_px)
                        if _buy_blocked(
                            code, price,
                            prev_today.get(code) if prev_today is not None else None,
                            high_today.get(code) if high_today is not None else None,
                        ):
                            continue

                        # Cost check
                        budget = target_pos_value
                        if budget > cash:
                            budget = cash
                        if budget <= 0:
                            break

                        # Round down to board lots and cap by fee-inclusive
                        # affordability (wolfpack's lot accounting — no more
                        # fractional zero-lot fills).
                        sh = int(int(budget / price // LOT) * LOT)
                        if sh <= 0:
                            continue
                        sh_max = int(int(cash / (price * (1.0 + TRADE_COST)) // LOT) * LOT)
                        if sh_max <= 0:
                            continue
                        if sh > sh_max:
                            sh = sh_max
                        cost = sh * price * (1.0 + TRADE_COST)
                        if cost <= 0.0 or cost > cash:
                            continue
                        cash -= cost
                        positions[code] = {
                            'entry_price': price,
                            'entry_time_idx': t_idx,
                            'shares': sh,
                            'entry_day': cur_day,
                            'last_price': price,
                        }

            # Recalculate Equity after trades; positions without a traded
            # price this bar mark at their last traded price and are counted
            # as estimated valuations.
            step_equity = cash
            estimated = 0
            for code, pos in positions.items():
                p = raw_today.get(code)
                if pd.isna(p) or float(p) <= 0:
                    estimated += 1
                    p = pos.get("last_price") or pos["entry_price"]
                step_equity += pos['shares'] * float(p)

            equity_curve.append({"time": t, "equity": step_equity, "estimated_positions": int(estimated)})

        # 5. Calculate Metrics
        eq_df = pd.DataFrame(equity_curve).set_index("time")
        eq_df['ret'] = eq_df['equity'].pct_change()

        total_ret = (eq_df['equity'].iloc[-1] / CAPITAL) - 1
        sharpe = 0
        if eq_df['ret'].std() > 0:
            annual = _ANNUAL_FACTOR_BY_KL_TYPE.get(kl_type, 250.0)
            sharpe = (eq_df['ret'].mean() / eq_df['ret'].std()) * np.sqrt(float(annual))

        # Max Drawdown
        eq_df['cummax'] = eq_df['equity'].cummax()
        eq_df['drawdown'] = (eq_df['equity'] - eq_df['cummax']) / eq_df['cummax']
        max_drawdown = eq_df['drawdown'].min()

        return {
            "metrics": {
                "total_return": total_ret,
                "sharpe_ratio": sharpe,
                "max_drawdown": max_drawdown,
                "final_equity": eq_df['equity'].iloc[-1],
                "holding_period_bars": HOLD_PERIODS,
                "execution_delay_bars": EXEC_DELAY,
                "trade_cost": TRADE_COST,
                "stamp_duty_sell": STAMP_SELL,
                "lot_size": LOT,
            },
            "equity_curve": equity_curve
        }
