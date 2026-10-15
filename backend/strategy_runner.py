import json
import datetime
import traceback
import asyncio
import baostock as bs
import concurrent.futures
import os
import copy
import hashlib
import numpy as np
from typing import List, Dict, Any, Optional

from backend.chan_service import (
    fetch_stock_data,
    CChanCustom,
    train_time_split_backtest,
    compute_recent_accuracy,
    _atr_pct_from_klu,
    predict_proba_1,
    stragety_feature,
    extract_state_features_from_cur_lv,
    build_chan_state_feature,
    get_stock_name,
    normalize_code,
    _add_sample_semantic_features,
    _normalize_side_filter,
    _side_filter_allows,
    json_safe_value,
)
from backend.storage import StorageManager, StrategyRun
from backend.vector_backtest import VectorBacktester
from backend.wolfpack_backtest import _day_key_from_ts, _daily_oc_from_kl_list, _simulate_universe_daily_topn
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from ChanConfig import CChanConfig
from DataAPI.ClickHouseAPI import CClickHouseAPI

_worker_storage = None

def get_worker_storage():
    global _worker_storage
    if _worker_storage is None:
        _worker_storage = StorageManager()
    return _worker_storage

def _none_like(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return str(value).strip().lower() in ("", "null", "none", "nan")
    return False

def _finite_float_or_none(value):
    if _none_like(value):
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if not np.isfinite(out):
        return None
    return float(out)

def _optional_float_param(params: Dict[str, Any], key: str, min_value: Optional[float] = None, max_value: Optional[float] = None):
    out = _finite_float_or_none((params or {}).get(key))
    if out is None:
        return None
    if min_value is not None and out < float(min_value):
        out = float(min_value)
    if max_value is not None and out > float(max_value):
        out = float(max_value)
    return out

def _optional_int_param(params: Dict[str, Any], key: str, default: int = 0, min_value: int = 0, max_value: Optional[int] = None) -> int:
    raw = (params or {}).get(key, default)
    if _none_like(raw):
        out = int(default)
    else:
        try:
            out = int(float(raw))
        except Exception:
            out = int(default)
    if out < int(min_value):
        out = int(min_value)
    if max_value is not None and out > int(max_value):
        out = int(max_value)
    return int(out)

def _strategy_trading_metrics(accuracy_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    trading = (accuracy_info or {}).get("trading") if isinstance(accuracy_info, dict) else None
    if not isinstance(trading, dict):
        return {}
    source = None
    raw = None
    for name in ("topk_vector", "topk", "top10", "threshold"):
        cand = trading.get(name)
        if isinstance(cand, dict):
            source = name
            raw = cand
            break
    if not isinstance(raw, dict):
        return {}
    pf = None
    try:
        pf_raw = raw.get("profit_factor")
        if bool(raw.get("profit_factor_is_infinite", False)):
            pf = float("inf")
        elif pf_raw is not None and np.isinf(float(pf_raw)):
            pf = float("inf") if float(pf_raw) > 0 else float("-inf")
        else:
            pf = _finite_float_or_none(pf_raw)
    except Exception:
        pf = _finite_float_or_none(raw.get("profit_factor"))
    avg_return = _finite_float_or_none(raw.get("avg_bar_return"))
    if avg_return is None:
        avg_return = _finite_float_or_none(raw.get("avg_net_profit"))
    if avg_return is None:
        avg_return = _finite_float_or_none(raw.get("expectancy"))
    trade_count = _optional_int_param(raw, "trade_count", default=0, min_value=0)
    if trade_count <= 0:
        trade_count = _optional_int_param(raw, "topk_n", default=0, min_value=0)
    max_drawdown = _finite_float_or_none(raw.get("max_drawdown"))
    return {
        "source": source,
        "profit_factor": pf,
        "profit_factor_is_infinite": bool(raw.get("profit_factor_is_infinite", False)),
        "topk_avg_return": avg_return,
        "topk_trade_count": int(trade_count),
        "topk_max_drawdown": max_drawdown,
        "win_rate": _finite_float_or_none(raw.get("win_rate")),
        "expectancy": _finite_float_or_none(raw.get("expectancy")),
        "avg_win": _finite_float_or_none(raw.get("avg_win")),
        "avg_loss": _finite_float_or_none(raw.get("avg_loss")),
    }

def _profit_filter_check(
    accuracy_info: Optional[Dict[str, Any]],
    min_profit_factor: Optional[float],
    min_topk_avg_return: Optional[float],
    min_topk_trade_count: int,
    max_topk_drawdown: Optional[float],
):
    metrics = _strategy_trading_metrics(accuracy_info)
    failures = []
    if min_profit_factor is not None:
        pf = metrics.get("profit_factor")
        if pf is None or float(pf) < float(min_profit_factor):
            failures.append(f"profit_factor<{float(min_profit_factor):.4g}")
    if min_topk_avg_return is not None:
        ar = metrics.get("topk_avg_return")
        if ar is None or float(ar) < float(min_topk_avg_return):
            failures.append(f"topk_avg_return<{float(min_topk_avg_return):.4g}")
    if int(min_topk_trade_count or 0) > 0:
        tc = int(metrics.get("topk_trade_count") or 0)
        if tc < int(min_topk_trade_count):
            failures.append(f"topk_trades<{int(min_topk_trade_count)}")
    if max_topk_drawdown is not None:
        dd = metrics.get("topk_max_drawdown")
        if dd is None or float(dd) > float(max_topk_drawdown):
            failures.append(f"topk_drawdown>{float(max_topk_drawdown):.4g}")
    return len(failures) == 0, metrics, failures

def get_hs300_stocks() -> List[str]:
    try:
        lg = bs.login()
        if lg.error_code != '0':
            print(f"login respond error_code:{lg.error_code}")
            return []
        rs = bs.query_hs300_stocks()
        hs300_stocks = []
        while (rs.error_code == '0') & rs.next():
            # [updateDate, code, code_name]
            # code format: sh.600000
            hs300_stocks.append(rs.get_row_data()[1])
        bs.logout()
        return hs300_stocks
    except Exception as e:
        print(f"Error getting HS300 stocks: {e}")
        return []

def _process_single_stock_strategy(
    code: str,
    kl_type,
    begin_time: str,
    data_src,
    custom_chan_config: Dict,
    enable_rolling_lookback: bool,
    model_type: str,
    min_accuracy: float,
    min_recent_accuracy: Optional[float],
    recent_accuracy_years: float,
    min_profit_factor: Optional[float],
    min_topk_avg_return: Optional[float],
    min_topk_trade_count: int,
    max_topk_drawdown: Optional[float],
    require_signal: bool,
    signal_lookback: int,
    signal_direction: str,
    min_signal_score: Optional[float],
    min_bsp_count: int,
    min_test_count: int,
    high_vol_atr_pct_min: Optional[float],
    use_atr_label: bool,
    atr_period: int,
    atr_mult: float,
    profit_threshold: Optional[float],
    auto_profit_quantile: float,
    profit_lookahead: int,
    trade_cost: float,
    enforce_t1: bool,
    only_features: Optional[Any],
    feature_preset: Optional[str],
    side_filter: str,
    autype,
    need_predictions: bool = False,
):
    try:
        # Set thread limits for worker process
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        os.environ["OPENBLAS_NUM_THREADS"] = "1"

        used_autype = AUTYPE.HFQ
        try:
            if isinstance(autype, AUTYPE):
                used_autype = autype
            else:
                raw = str(autype or "").strip().lower()
                if raw == "qfq":
                    used_autype = AUTYPE.QFQ
                elif raw == "none":
                    used_autype = AUTYPE.NONE
                else:
                    used_autype = AUTYPE.HFQ
        except Exception:
            used_autype = AUTYPE.HFQ
        side_filter = _normalize_side_filter(side_filter, default="buy")
        
        # Fetch Data
        kl_list = fetch_stock_data(
            code, 
            kl_type, 
            begin_time, 
            None, 
            data_src,
            autype=used_autype,
        )
        
        if not kl_list or len(kl_list) < 100:
            return code, None, 0, 0.0, "", None

        # Chan Calc
        base_chan_config = {
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
            "zs_algo": "normal",
        }
        if custom_chan_config:
            base_chan_config.update(custom_chan_config)

        # Force trigger_step to True
        base_chan_config["trigger_step"] = True

        chan_config = CChanConfig(base_chan_config)

        kl_list.sort(key=lambda x: getattr(getattr(x, "time", None), "ts", 0))
        deduped = []
        last_ts = None
        for klu in kl_list:
            ts = getattr(getattr(klu, "time", None), "ts", None)
            if ts is None:
                continue
            if last_ts is not None and ts == last_ts:
                continue
            deduped.append(klu)
            last_ts = ts
        kl_list = deduped
        daily_oc = _daily_oc_from_kl_list(kl_list)

        # --- Cache Logic Start ---
        data_start_time = None
        data_end_time = None
        key_hash = None
        cache_params = None
        storage = None
        
        try:
            if kl_list:
                data_start_time = kl_list[0].time.to_str()
                data_end_time = kl_list[-1].time.to_str()
                
                cache_params = {
                    "code": code,
                    "kl_type": str(kl_type),
                    "begin_time": begin_time,
                    "data_src": str(data_src),
                    "autype": str(getattr(used_autype, "name", used_autype)),
                    "custom_chan_config": custom_chan_config,
                    "enable_rolling_lookback": enable_rolling_lookback,
                    "model_type": model_type,
                    "min_accuracy": float(min_accuracy),
                    "min_recent_accuracy": float(min_recent_accuracy) if min_recent_accuracy is not None else None,
                    "recent_accuracy_years": float(recent_accuracy_years),
                    "min_profit_factor": float(min_profit_factor) if min_profit_factor is not None else None,
                    "min_topk_avg_return": float(min_topk_avg_return) if min_topk_avg_return is not None else None,
                    "min_topk_trade_count": int(min_topk_trade_count or 0),
                    "max_topk_drawdown": float(max_topk_drawdown) if max_topk_drawdown is not None else None,
                    "require_signal": require_signal,
                    "signal_lookback": signal_lookback,
                    "signal_direction": signal_direction,
                    "min_signal_score": float(min_signal_score) if min_signal_score is not None else None,
                    "min_bsp_count": int(min_bsp_count),
                    "min_test_count": int(min_test_count),
                    "high_vol_atr_pct_min": float(high_vol_atr_pct_min) if high_vol_atr_pct_min is not None else None,
                    "use_atr_label": bool(use_atr_label),
                    "atr_period": int(atr_period),
                    "atr_mult": float(atr_mult),
                    "profit_threshold": float(profit_threshold) if profit_threshold is not None else None,
                    "auto_profit_quantile": float(auto_profit_quantile),
                    "profit_lookahead": int(profit_lookahead),
                    "trade_cost": float(trade_cost or 0.0),
                    "enforce_t1": bool(enforce_t1),
                    "only_features": only_features,
                    "feature_preset": feature_preset,
                    "side_filter": side_filter,
                    "data_start_time": data_start_time,
                    "data_end_time": data_end_time
                }
                
                params_json = json.dumps(cache_params, sort_keys=True, ensure_ascii=False)
                key_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()
                
                storage = get_worker_storage()
                cached = storage.get_strategy_cache(key_hash)
                if cached and (not bool(need_predictions)):
                    return code, cached["result"], cached["bsp_count"], cached["accuracy"], cached["method"] + " (cached)", {"daily_oc": daily_oc}
        except Exception:
            pass
        # --- Cache Logic End ---

        try:
            hv = None
            if high_vol_atr_pct_min not in ["", "null", "None", None]:
                hv = float(high_vol_atr_pct_min)
            if hv is not None:
                atr_pct = _atr_pct_from_klu(kl_list[-1], period=atr_period)
                if atr_pct is None or float(atr_pct) < float(hv):
                    method = f"high_vol_filtered atr_pct={atr_pct}"
                    try:
                        if key_hash and storage:
                            storage.save_strategy_cache(
                                key_hash,
                                code,
                                0.0,
                                0,
                                method,
                                None,
                                data_start_time,
                                data_end_time,
                                cache_params,
                            )
                    except Exception:
                        pass
                    return code, None, 0, 0.0, method, None
        except Exception:
            pass

        chan = CChanCustom(
            code=code,
            begin_time=begin_time,
            end_time=None,
            data_src=data_src,
            lv_list=[kl_type],
            config=chan_config,
            autype=used_autype,
            preloaded_data=kl_list,
        )

        bsp_dict = {}
        last_snapshot = None
        kl_count = 0
        bsp_count = 0
        
        for chan_snapshot in chan.step_load():
            kl_count += 1
            last_snapshot = chan_snapshot
            cur_lv_chan = chan_snapshot[0]
            if len(cur_lv_chan) < 2:
                continue
            last_klu = cur_lv_chan[-1][-1]
            bsp_list = chan_snapshot.get_latest_bsp()
            if not bsp_list:
                continue
            last_bsp = bsp_list[0]
            state_feat = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
            chan_state_feat = build_chan_state_feature(cur_lv_chan, last_klu, last_bsp)
            if _side_filter_allows(last_bsp.is_buy, side_filter) and last_bsp.klu.idx not in bsp_dict and cur_lv_chan[-2].idx == last_bsp.klu.klc.idx:
                bsp_count += 1
                feat_copy = copy.deepcopy(last_bsp.features)
                try:
                    _add_sample_semantic_features(feat_copy, bool(last_bsp.is_buy), last_bsp, "event_bsp")
                except Exception:
                    pass
                try:
                    if chan_state_feat:
                        feat_copy.add_feat(chan_state_feat)
                except Exception:
                    pass
                bsp_dict[last_bsp.klu.idx] = {
                    "feature": feat_copy,
                    "is_buy": last_bsp.is_buy,
                    "open_time": last_klu.time,
                    "bsp_obj": last_bsp,
                    "decision_klu": last_klu,
                    "sample_kind": "event_bsp",
                }
                try:
                    if state_feat:
                        bsp_dict[last_bsp.klu.idx]["feature"].add_feat(state_feat)
                    bsp_dict[last_bsp.klu.idx]["feature"].add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                except Exception:
                    pass
        
        if not last_snapshot:
            return code, None, len(bsp_dict), 0.0, "", {"daily_oc": daily_oc}

        last_klu = last_snapshot[0][-1][-1]
        latest_bsp_list = last_snapshot.get_latest_bsp(number=0)

        if int(min_bsp_count) > 0 and len(bsp_dict) < int(min_bsp_count):
            method = f"insufficient_samples<{int(min_bsp_count)} total={len(bsp_dict)}"
            try:
                if key_hash and storage:
                    storage.save_strategy_cache(
                        key_hash,
                        code,
                        0.0,
                        len(bsp_dict),
                        method,
                        None,
                        data_start_time,
                        data_end_time,
                        cache_params,
                    )
            except Exception:
                pass
            return code, None, len(bsp_dict), 0.0, method, {"daily_oc": daily_oc}
        
        # Train with n_jobs=1 to avoid thread explosion
        backtest_kwargs = {
            "model_type": model_type,
            "calibrate_method": "none",
            "n_jobs": 1,
            "profit_threshold": profit_threshold,
            "auto_profit_quantile": auto_profit_quantile,
            "profit_lookahead": profit_lookahead,
            "trade_cost": float(trade_cost or 0.0),
            "enforce_t1": bool(enforce_t1),
            "use_atr_label": bool(use_atr_label),
            "atr_period": int(atr_period),
            "atr_mult": float(atr_mult),
            "only_features": only_features,
            "feature_preset": feature_preset,
            "side_filter": side_filter,
        }
        if int(min_test_count) > 0:
            backtest_kwargs["min_test"] = int(min_test_count)
        bst, feature_meta, accuracy_info = train_time_split_backtest(
            bsp_dict, 
            **backtest_kwargs,
        )
        
        current_accuracy = 0.0
        method = ""
        test_count = 0
        if accuracy_info:
            current_accuracy = float(accuracy_info.get("accuracy", 0.0))
            try:
                test_count = int(float(accuracy_info.get("test_count", 0) or 0))
            except Exception:
                test_count = 0
            method = (
                f"{accuracy_info.get('method', '')}"
                f" train={accuracy_info.get('train_count', 0)}"
                f" test={accuracy_info.get('test_count', 0)}"
                f" valid={accuracy_info.get('valid_count', 0)}"
                f" total={accuracy_info.get('total_count', 0)}"
            )
        if int(min_test_count) > 0 and test_count < int(min_test_count):
            method = (method + f" insufficient_test<{int(min_test_count)}") if method else f"insufficient_test<{int(min_test_count)}"
            current_accuracy = 0.0

        result = None
        recent_accuracy = None
        try:
            if min_recent_accuracy is not None:
                recent_kwargs = {
                    "model_type": model_type,
                    "recent_years": recent_accuracy_years,
                    "calibrate_method": "none",
                    "n_jobs": 1,
                    "profit_threshold": profit_threshold,
                    "auto_profit_quantile": auto_profit_quantile,
                    "profit_lookahead": profit_lookahead,
                    "trade_cost": float(trade_cost or 0.0),
                    "enforce_t1": bool(enforce_t1),
                    "use_atr_label": bool(use_atr_label),
                    "atr_period": int(atr_period),
                    "atr_mult": float(atr_mult),
                    "only_features": only_features,
                    "feature_preset": feature_preset,
                    "side_filter": side_filter,
                }
                if int(min_test_count) > 0:
                    recent_kwargs["min_test"] = int(min_test_count)
                recent_info = compute_recent_accuracy(
                    bsp_dict,
                    **recent_kwargs,
                )
                recent_accuracy = float((recent_info or {}).get("accuracy", 0.0))
        except Exception:
            recent_accuracy = None

        profit_ok, trading_metrics, profit_failures = _profit_filter_check(
            accuracy_info,
            min_profit_factor=min_profit_factor,
            min_topk_avg_return=min_topk_avg_return,
            min_topk_trade_count=min_topk_trade_count,
            max_topk_drawdown=max_topk_drawdown,
        )
        if trading_metrics:
            try:
                method += (
                    f" {trading_metrics.get('source')}"
                    f" pf={trading_metrics.get('profit_factor')}"
                    f" avg={trading_metrics.get('topk_avg_return')}"
                    f" trades={trading_metrics.get('topk_trade_count')}"
                )
            except Exception:
                pass
        if not profit_ok:
            method = (method + f" profit_filtered={','.join(profit_failures)}") if method else f"profit_filtered={','.join(profit_failures)}"

        if current_accuracy >= float(min_accuracy) and profit_ok:
            has_signal = False
            latest_signal_date = ""
            latest_signal_type = ""
            selected_bsp = None

            if require_signal:
                last_idx = last_klu.idx
                for bsp in latest_bsp_list:
                    if not _side_filter_allows(bool(bsp.is_buy), side_filter):
                        continue
                    if signal_direction == 'buy' and not bsp.is_buy:
                        continue
                    if signal_direction == 'sell' and bsp.is_buy:
                        continue
                    if abs(bsp.klu.idx - last_idx) < signal_lookback:
                        has_signal = True
                        selected_bsp = bsp
                        latest_signal_date = bsp.klu.time.to_str()
                        latest_signal_type = bsp.type2str()
                        if bsp.is_buy:
                            latest_signal_type += " (Buy)"
                        else:
                            latest_signal_type += " (Sell)"
                        break
                    if (last_idx - bsp.klu.idx) >= signal_lookback:
                        break
            else:
                has_signal = True
                if latest_bsp_list:
                    for bsp in latest_bsp_list:
                        if not _side_filter_allows(bool(bsp.is_buy), side_filter):
                            continue
                        if signal_direction == 'buy' and not bsp.is_buy:
                            continue
                        if signal_direction == 'sell' and bsp.is_buy:
                            continue
                        selected_bsp = bsp
                        latest_signal_date = selected_bsp.klu.time.to_str()
                        latest_signal_type = selected_bsp.type2str()
                        if selected_bsp.is_buy:
                            latest_signal_type += " (Buy)"
                        else:
                            latest_signal_type += " (Sell)"
                        break

            signal_score = None
            if has_signal and selected_bsp is not None and bst and feature_meta:
                try:
                    feat_obj = None
                    try:
                        feat_obj = (bsp_dict.get(selected_bsp.klu.idx) or {}).get("feature")
                    except Exception:
                        feat_obj = None
                    if feat_obj is None:
                        feat_obj = selected_bsp.features
                    try:
                        try:
                            cur_lv_chan = last_snapshot[0]
                            state_feat2 = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
                            if state_feat2:
                                feat_obj.add_feat(state_feat2)
                            chan_state_feat2 = build_chan_state_feature(cur_lv_chan, last_klu, selected_bsp)
                            if chan_state_feat2:
                                feat_obj.add_feat(chan_state_feat2)
                            _add_sample_semantic_features(feat_obj, bool(selected_bsp.is_buy), selected_bsp, "event_bsp")
                        except Exception:
                            pass
                        feat_obj.add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                    except Exception:
                        pass
                    feat_vec = [feat_obj.get(k, -9999999) for k in feature_meta]
                    signal_score = float(predict_proba_1(bst, [feat_vec])[0])
                except Exception:
                    signal_score = None

            recent_ok = True
            if min_recent_accuracy is not None:
                recent_ok = recent_accuracy is not None and float(recent_accuracy) >= float(min_recent_accuracy)

            score_ok = True
            if min_signal_score is not None:
                score_ok = signal_score is not None and float(signal_score) >= float(min_signal_score)

            if has_signal and recent_ok and score_ok:
                stock_name = get_stock_name(code, data_src)
                result = {
                    "code": code,
                    "name": stock_name,
                    "accuracy": current_accuracy,
                    "recent_accuracy": recent_accuracy,
                    "signal_score": signal_score,
                    "profit_factor": trading_metrics.get("profit_factor") if trading_metrics else None,
                    "profit_factor_is_infinite": trading_metrics.get("profit_factor_is_infinite") if trading_metrics else False,
                    "topk_avg_return": trading_metrics.get("topk_avg_return") if trading_metrics else None,
                    "topk_trade_count": trading_metrics.get("topk_trade_count") if trading_metrics else None,
                    "topk_max_drawdown": trading_metrics.get("topk_max_drawdown") if trading_metrics else None,
                    "trading_metrics": trading_metrics,
                    "latest_date": latest_signal_date if require_signal else last_klu.time.to_str(),
                    "signal_type": latest_signal_type
                }
        
        # Save to cache
        result = json_safe_value(result) if result is not None else None
        try:
            if key_hash and storage:
                storage.save_strategy_cache(
                    key_hash, 
                    code, 
                    current_accuracy, 
                    len(bsp_dict), 
                    method, 
                    result, 
                    data_start_time, 
                    data_end_time, 
                    cache_params
                )
        except Exception:
            pass

        info = accuracy_info if isinstance(accuracy_info, dict) else {}
        info = json_safe_value(info) if isinstance(info, dict) else {}
        info["daily_oc"] = daily_oc
        return code, result, len(bsp_dict), current_accuracy, method, info

    except Exception as e:
        print(f"Error processing {code}: {e}")
        return code, None, 0, 0.0, f"error: {str(e)}", None

class StrategyRunner:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def _is_run_canceled(self, run_id: int) -> bool:
        session = self.storage.Session()
        try:
            run = session.query(StrategyRun).filter(StrategyRun.id == int(run_id)).first()
            if not run:
                return True
            return str(run.status or "") == "canceled"
        except Exception:
            return False
        finally:
            session.close()

    def _update_progress(self, run_id: int, processed: int, total: int, status: str = "running", result_json: str = None, result_count: int = None):
        session = self.storage.Session()
        try:
            run = session.query(StrategyRun).filter(StrategyRun.id == run_id).first()
            if run:
                run.processed_stocks = processed
                run.total_stocks = total
                if str(run.status or "") != "canceled":
                    run.status = status
                run.progress = int((processed / total) * 100) if total > 0 else 0
                if result_json:
                    run.result_json = result_json
                if result_count is not None:
                    try:
                        run.result_count = int(result_count)
                    except Exception:
                        run.result_count = 0
                if status in ["completed", "failed", "canceled"]:
                    run.completed_at = datetime.datetime.utcnow()
                session.commit()
        except Exception as e:
            print(f"Error updating progress: {e}")
        finally:
            session.close()

    def run_strategy_sync(self, run_id: int, params: Dict[str, Any]):
        try:
            if self._is_run_canceled(run_id):
                return
            self._update_progress(run_id, 0, 0, "pending")
            scope = params.get('scope', 'hs300')
            model_type = params.get('model', 'xgboost')
            frequency = params.get('frequency', '1d')
            raw_autype = str(params.get("autype", "hfq") or "hfq").strip().lower()
            if raw_autype not in ("qfq", "hfq", "none"):
                raw_autype = "hfq"
            amp_whitelist_days = params.get("amp_whitelist_days", 0)
            try:
                if amp_whitelist_days in ["", "null", "None", None]:
                    amp_whitelist_days = 0
                amp_whitelist_days = int(float(amp_whitelist_days))
            except Exception:
                amp_whitelist_days = 0
            if amp_whitelist_days < 0:
                amp_whitelist_days = 0
            if amp_whitelist_days > 365:
                amp_whitelist_days = 365
            amp_whitelist_top_frac = params.get("amp_whitelist_top_frac", 0.3)
            try:
                if amp_whitelist_top_frac in ["", "null", "None", None]:
                    amp_whitelist_top_frac = 0.3
                amp_whitelist_top_frac = float(amp_whitelist_top_frac)
            except Exception:
                amp_whitelist_top_frac = 0.3
            if amp_whitelist_top_frac <= 0:
                amp_whitelist_top_frac = 0.3
            if amp_whitelist_top_frac > 1:
                amp_whitelist_top_frac = 1.0
            data_length_years = float(params.get('data_length_years', 1.0))
            min_accuracy = float(params.get('min_accuracy', 0.8))
            if min_accuracy < 0:
                min_accuracy = 0.0
            if min_accuracy > 1:
                min_accuracy = 1.0
            min_recent_accuracy = params.get('min_recent_accuracy', None)
            try:
                if min_recent_accuracy is not None:
                    min_recent_accuracy = float(min_recent_accuracy)
            except Exception:
                min_recent_accuracy = None
            if min_recent_accuracy is not None:
                if min_recent_accuracy < 0:
                    min_recent_accuracy = 0.0
                if min_recent_accuracy > 1:
                    min_recent_accuracy = 1.0
            recent_accuracy_years = params.get('recent_accuracy_years', 1.0)
            try:
                recent_accuracy_years = float(recent_accuracy_years)
            except Exception:
                recent_accuracy_years = 1.0
            if recent_accuracy_years <= 0:
                recent_accuracy_years = 1.0
            min_profit_factor = _optional_float_param(params, "min_profit_factor", min_value=0.0)
            min_topk_avg_return = _optional_float_param(params, "min_topk_avg_return")
            min_topk_trade_count = _optional_int_param(params, "min_topk_trade_count", default=0, min_value=0, max_value=1000000)
            max_topk_drawdown = _optional_float_param(params, "max_topk_drawdown", min_value=0.0)
            custom_chan_config = params.get('chan_config') or {}
            require_signal = params.get('require_signal', False)
            signal_lookback = int(params.get('signal_lookback', 5))
            signal_direction = params.get('signal_direction', 'buy')
            side_filter = _normalize_side_filter(params.get("side_filter", "buy"), default="buy")
            only_features = params.get("only_features", None)
            if isinstance(only_features, str) and not str(only_features).strip():
                only_features = None
            feature_preset = params.get("feature_preset", None)
            if isinstance(feature_preset, str) and not str(feature_preset).strip():
                feature_preset = None
            if str(model_type or "").strip().lower() in ("lr", "logreg", "logistic", "logistic_regression") and only_features is None and feature_preset is None:
                feature_preset = "lr_default"
            min_signal_score = params.get('min_signal_score', None)
            try:
                if min_signal_score is not None:
                    min_signal_score = float(min_signal_score)
            except Exception:
                min_signal_score = None
            if min_signal_score is not None:
                if min_signal_score < 0:
                    min_signal_score = 0.0
                if min_signal_score > 1:
                    min_signal_score = 1.0
                require_signal = True
            if signal_lookback < 1:
                signal_lookback = 5

            min_bsp_count = params.get('min_bsp_count', 0)
            if min_bsp_count in ["", "null", "None", None]:
                min_bsp_count = 0
            try:
                min_bsp_count = int(float(min_bsp_count))
            except Exception:
                min_bsp_count = 0
            if min_bsp_count < 0:
                min_bsp_count = 0
            if min_bsp_count > 1000000:
                min_bsp_count = 1000000

            min_test_count = params.get('min_test_count', 0)
            if min_test_count in ["", "null", "None", None]:
                min_test_count = 0
            try:
                min_test_count = int(float(min_test_count))
            except Exception:
                min_test_count = 0
            if min_test_count < 0:
                min_test_count = 0
            if min_test_count > 1000000:
                min_test_count = 1000000

            high_vol_atr_pct_min = params.get("high_vol_atr_pct_min", None)
            try:
                if high_vol_atr_pct_min in ["", "null", "None", None]:
                    high_vol_atr_pct_min = None
                if high_vol_atr_pct_min is not None:
                    high_vol_atr_pct_min = float(high_vol_atr_pct_min)
                    if high_vol_atr_pct_min < 0:
                        high_vol_atr_pct_min = 0.0
            except Exception:
                high_vol_atr_pct_min = None

            use_atr_label = bool(params.get("use_atr_label", False))
            atr_period = params.get("atr_period", 14)
            try:
                atr_period = int(float(atr_period))
            except Exception:
                atr_period = 14
            if atr_period <= 0:
                atr_period = 14

            atr_mult = params.get("atr_mult", 1.0)
            try:
                atr_mult = float(atr_mult)
            except Exception:
                atr_mult = 1.0
            if atr_mult < 0:
                atr_mult = 0.0

            profit_threshold = params.get('profit_threshold', 0.01)
            if profit_threshold in ["", "null", "None"]:
                profit_threshold = None
            try:
                if profit_threshold is not None:
                    profit_threshold = float(profit_threshold)
            except Exception:
                profit_threshold = 0.01
            if profit_threshold is not None and profit_threshold < 0:
                profit_threshold = 0.0
            auto_profit_quantile = params.get('auto_profit_quantile', 0.7)
            try:
                auto_profit_quantile = float(auto_profit_quantile)
            except Exception:
                auto_profit_quantile = 0.7
            if auto_profit_quantile < 0:
                auto_profit_quantile = 0.0
            if auto_profit_quantile > 1:
                auto_profit_quantile = 1.0
            profit_lookahead = params.get('profit_lookahead', 5)
            try:
                profit_lookahead = int(float(profit_lookahead))
            except Exception:
                profit_lookahead = 5
            if profit_lookahead < 0:
                profit_lookahead = 0
            if profit_lookahead > 250:
                profit_lookahead = 250
            trade_cost = params.get('trade_cost', 0.0)
            try:
                trade_cost = float(trade_cost)
            except Exception:
                trade_cost = 0.0
            if trade_cost < 0:
                trade_cost = 0.0
            enforce_t1_raw = params.get('enforce_t1', False)
            if isinstance(enforce_t1_raw, str):
                enforce_t1 = str(enforce_t1_raw).strip().lower() in ("1", "true", "yes", "y", "on")
            else:
                enforce_t1 = bool(enforce_t1_raw)
            pool_id = params.get('pool_id')
            enable_rolling_lookback = params.get('enable_rolling_lookback', True)
            # 0 disables the portfolio block below; default matches StrategyRequest.portfolio_top_n
            portfolio_top_n = _optional_int_param(params, "portfolio_top_n", default=2, min_value=0)
            
            # 1. Get Stocks
            stocks = []
            raw_codes = params.get("codes")
            manual_codes = params.get("manual_codes")
            if raw_codes and isinstance(raw_codes, (list, tuple)):
                for c in raw_codes:
                    cc = str(c or "").strip()
                    if not cc:
                        continue
                    try:
                        stocks.append(normalize_code(cc))
                    except Exception:
                        stocks.append(cc)
            elif manual_codes:
                text = str(manual_codes or "")
                parts = []
                cur = ""
                for ch in text:
                    if ch.isalnum() or ch in [".", "_"]:
                        cur += ch
                        continue
                    if cur:
                        parts.append(cur)
                        cur = ""
                if cur:
                    parts.append(cur)
                for c in parts:
                    cc = str(c or "").strip()
                    if not cc:
                        continue
                    try:
                        stocks.append(normalize_code(cc))
                    except Exception:
                        stocks.append(cc)

            seen = set()
            uniq = []
            for c in stocks:
                cc = str(c or "").strip()
                if not cc or cc in seen:
                    continue
                seen.add(cc)
                uniq.append(cc)
            stocks = uniq

            if pool_id:
                print(f"Fetching stocks for pool_id: {pool_id}")
                if not stocks:
                    stocks = CClickHouseAPI.get_pool_members(pool_id)
                    print(f"Got {len(stocks)} stocks from pool {pool_id}")
            
            if not stocks:
                if scope == 'hs300':
                    stocks = get_hs300_stocks()
                else:
                    # Fallback or other scopes
                    stocks = get_hs300_stocks()
            
            if not stocks:
                self._update_progress(run_id, 0, 0, "failed")
                return

            if amp_whitelist_days > 0 and len(stocks) > 1:
                try:
                    autype_map = {"qfq": AUTYPE.QFQ, "hfq": AUTYPE.HFQ, "none": AUTYPE.NONE}
                    used_autype = autype_map.get(raw_autype, AUTYPE.HFQ)
                    lookback_days = max(30, int(amp_whitelist_days * 4))
                    amp_begin_time = (datetime.datetime.now() - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")

                    def _avg_amp(code: str):
                        try:
                            kl_list = fetch_stock_data(code, KL_TYPE.K_DAY, amp_begin_time, None, DATA_SRC.CLICK_HOUSE, autype=used_autype)
                            if not kl_list:
                                return code, None
                            tail = kl_list[-int(amp_whitelist_days):] if amp_whitelist_days > 0 else list(kl_list)
                            if not tail:
                                return code, None
                            amps = []
                            for k in tail:
                                try:
                                    c = float(getattr(k, "close", 0.0) or 0.0)
                                    h = float(getattr(k, "high", 0.0) or 0.0)
                                    l = float(getattr(k, "low", 0.0) or 0.0)
                                    if c > 0:
                                        amps.append((h - l) / c)
                                except Exception:
                                    continue
                            if not amps:
                                return code, None
                            return code, float(sum(amps) / float(len(amps)))
                        except Exception:
                            return code, None

                    amp_workers = max(1, min(16, len(stocks)))
                    scored = []
                    with concurrent.futures.ThreadPoolExecutor(max_workers=amp_workers) as ex:
                        futs = [ex.submit(_avg_amp, c) for c in stocks]
                        for fut in concurrent.futures.as_completed(futs):
                            try:
                                c, a = fut.result()
                                if a is not None:
                                    scored.append((c, float(a)))
                            except Exception:
                                continue

                    if scored:
                        scored.sort(key=lambda x: float(x[1]), reverse=True)
                        keep_n = int(max(1, round(len(scored) * float(amp_whitelist_top_frac))))
                        keep = set([c for c, _ in scored[:keep_n]])
                        stocks = [c for c in stocks if c in keep]
                except Exception:
                    pass

            self._update_progress(run_id, 0, len(stocks), "running")
            
            results = []
            processed = 0
            all_predictions = {}
            daily_oc_by_code = {}
            
            freq = str(frequency or "").strip().lower() or "1d"
            kl_type = KL_TYPE.K_DAY
            if freq == "30m":
                kl_type = KL_TYPE.K_30M
            elif freq == "5m":
                kl_type = KL_TYPE.K_5M
            elif freq == "60m":
                kl_type = KL_TYPE.K_60M
            elif freq == "15m":
                kl_type = KL_TYPE.K_15M
            elif freq == "1m":
                kl_type = KL_TYPE.K_1M
            elif freq == "1w":
                kl_type = KL_TYPE.K_WEEK
            elif freq == "1mo":
                kl_type = KL_TYPE.K_MON
            
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=int(data_length_years * 365))).strftime("%Y-%m-%d")

            # Use max workers based on CPU count, but leave some for system/db
            # User has 64 cores/128 threads.
            # Using 60 workers should be safe and efficient.
            
            default_workers = min(60, os.cpu_count() or 4)
            env_workers = os.environ.get("MLCHAN_STRATEGY_WORKERS")
            if env_workers:
                try:
                    max_workers = int(env_workers)
                except ValueError:
                    max_workers = default_workers
            else:
                max_workers = default_workers
            
            # Ensure at least 1 and not more than tasks
            max_workers = max(1, min(max_workers, len(stocks)))
            
            print(f"Starting strategy execution with {max_workers} workers for {len(stocks)} stocks.")

            executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
            try:
                active_futures = set()
                stock_iter = iter(stocks)
                future_to_code = {}
                target_active = max_workers * 2
                cancelled = False

                def submit_next():
                    try:
                        code = next(stock_iter)
                        fut = executor.submit(
                            _process_single_stock_strategy,
                            code,
                            kl_type,
                            begin_time,
                            DATA_SRC.CLICK_HOUSE,
                            custom_chan_config,
                            enable_rolling_lookback,
                            model_type,
                            min_accuracy,
                            min_recent_accuracy,
                            recent_accuracy_years,
                            min_profit_factor,
                            min_topk_avg_return,
                            min_topk_trade_count,
                            max_topk_drawdown,
                            require_signal,
                            signal_lookback,
                            signal_direction,
                            min_signal_score,
                            min_bsp_count,
                            min_test_count,
                            high_vol_atr_pct_min,
                            use_atr_label,
                            atr_period,
                            atr_mult,
                            profit_threshold,
                            auto_profit_quantile,
                            profit_lookahead,
                            trade_cost,
                            enforce_t1,
                            only_features,
                            feature_preset,
                            side_filter,
                            raw_autype,
                            bool(portfolio_top_n),
                        )
                        active_futures.add(fut)
                        future_to_code[fut] = code
                        return True
                    except StopIteration:
                        return False

                for _ in range(target_active):
                    if not submit_next():
                        break

                while active_futures:
                    if processed % 5 == 0 and self._is_run_canceled(run_id):
                        cancelled = True
                        break

                    done, _ = concurrent.futures.wait(active_futures, return_when=concurrent.futures.FIRST_COMPLETED)

                    for future in done:
                        active_futures.remove(future)
                        orig_code = future_to_code.pop(future, None)
                        processed += 1

                        try:
                            r_code, r_result, r_samples, r_acc, r_method, r_info = future.result()

                            if r_info and isinstance(r_info, dict) and "predictions" in r_info:
                                all_predictions[r_code] = r_info["predictions"]
                            if r_info and isinstance(r_info, dict) and isinstance(r_info.get("daily_oc"), dict):
                                daily_oc_by_code[r_code] = r_info["daily_oc"]

                            if processed % 10 == 0:
                                print(f"[Strategy] {r_code} samples={r_samples} accuracy={r_acc:.3f} {r_method}")
                                self._update_progress(run_id, processed, len(stocks), "running")

                            if r_result:
                                print(f"[Match] {r_code} samples={r_samples} acc={r_acc:.3f}")
                                results.append(r_result)

                        except Exception as e:
                            if orig_code:
                                print(f"Exception for {orig_code}: {e}")
                            else:
                                print(f"Exception in worker: {e}")
                            traceback.print_exc()

                        if processed % 5 == 0 and self._is_run_canceled(run_id):
                            cancelled = True
                            break

                        submit_next()

                    if cancelled:
                        break
            finally:
                if self._is_run_canceled(run_id):
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                else:
                    executor.shutdown(wait=True, cancel_futures=False)

            if self._is_run_canceled(run_id):
                try:
                    self._update_progress(run_id, processed, len(stocks), "canceled", json.dumps(json_safe_value(results), ensure_ascii=False, allow_nan=False), len(results))
                except Exception:
                    pass
                return
            
            # Portfolio Backtest
            if self._is_run_canceled(run_id):
                try:
                    self._update_progress(run_id, processed, len(stocks), "canceled", json.dumps(json_safe_value(results), ensure_ascii=False, allow_nan=False), len(results))
                except Exception:
                    pass
                return
            if portfolio_top_n and all_predictions:
                try:
                    min_score = float(min_signal_score) if min_signal_score is not None else 0.6
                    best = {}
                    for code, preds in all_predictions.items():
                        for p in (preds or []):
                            if not isinstance(p, dict):
                                continue
                            if not bool(p.get("is_buy")):
                                continue
                            try:
                                prob = float(p.get("prob"))
                            except Exception:
                                continue
                            if (not np.isfinite(prob)) or float(prob) < float(min_score):
                                continue
                            try:
                                ts = int(p.get("ts") or 0)
                            except Exception:
                                ts = 0
                            if ts <= 0:
                                continue
                            dk = _day_key_from_ts(ts)
                            if dk is None:
                                continue
                            k = (str(code), int(dk))
                            cur = best.get(k)
                            if cur is None or float(prob) > float(cur.get("prob") or float("-inf")):
                                best[k] = {"code": str(code), "day": int(dk), "ts": int(ts), "prob": float(prob)}

                    candidates = list(best.values())
                    end_date = datetime.datetime.now().strftime("%Y-%m-%d")
                    sim = _simulate_universe_daily_topn(
                        candidates,
                        begin_time,
                        end_date,
                        trade_cost=float(trade_cost or 0.0),
                        top_n=int(portfolio_top_n),
                        daily_oc_by_code=daily_oc_by_code,
                        sell_mode="baseline",
                        enforce_t1=True,
                        stop_loss=-1.0,
                    )

                    sharpe = float(sim.get("sharpe") or 0.0) if isinstance(sim, dict) else 0.0
                    max_dd = float(sim.get("max_drawdown") or 0.0) if isinstance(sim, dict) else 0.0
                    total_return = float(sim.get("total_return") or 0.0) if isinstance(sim, dict) else 0.0
                    trade_count = int(sim.get("trade_count") or 0) if isinstance(sim, dict) else 0

                    portfolio_res = {
                        "code": "PORTFOLIO",
                        "name": f"Portfolio (Top {portfolio_top_n})",
                        "accuracy": 0.0,
                        "recent_accuracy": 0.0,
                        "signal_score": 0.0,
                        "signal_type": f"Sharpe: {sharpe:.2f}, MaxDD: {max_dd:.2%}, Ret: {total_return:.2%}",
                        "latest_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "metrics": {
                            "sharpe": sharpe,
                            "max_dd": max_dd,
                            "total_return": total_return,
                            "trade_count": trade_count
                        }
                    }
                    results.insert(0, portfolio_res)
                except Exception as e:
                    print(f"Portfolio calculation failed: {e}")
                    traceback.print_exc()

            # Completed
            self._update_progress(run_id, processed, len(stocks), "completed", json.dumps(json_safe_value(results), ensure_ascii=False, allow_nan=False), len(results))
            
        except Exception as e:
            traceback.print_exc()
            self._update_progress(run_id, 0, 0, "failed")
