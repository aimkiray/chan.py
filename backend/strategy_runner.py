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
    get_stock_name
)
from backend.storage import StorageManager, StrategyRun
from backend.vector_backtest import VectorBacktester
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from ChanConfig import CChanConfig
from DataAPI.ClickHouseAPI import CClickHouseAPI

_worker_storage = None

def get_worker_storage():
    global _worker_storage
    if _worker_storage is None:
        _worker_storage = StorageManager()
    return _worker_storage

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
    autype,
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
                    "data_start_time": data_start_time,
                    "data_end_time": data_end_time
                }
                
                params_json = json.dumps(cache_params, sort_keys=True, ensure_ascii=False)
                key_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()
                
                storage = get_worker_storage()
                cached = storage.get_strategy_cache(key_hash)
                if cached:
                    return code, cached['result'], cached['bsp_count'], cached['accuracy'], cached['method'] + " (cached)"
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
            if last_bsp.klu.idx not in bsp_dict and cur_lv_chan[-2].idx == last_bsp.klu.klc.idx:
                bsp_count += 1
                bsp_dict[last_bsp.klu.idx] = {
                    "feature": copy.deepcopy(last_bsp.features),
                    "is_buy": last_bsp.is_buy,
                    "open_time": last_klu.time,
                    "bsp_obj": last_bsp,
                    "decision_klu": last_klu,
                }
                try:
                    if state_feat:
                        bsp_dict[last_bsp.klu.idx]["feature"].add_feat(state_feat)
                    bsp_dict[last_bsp.klu.idx]["feature"].add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                except Exception:
                    pass
        
        if not last_snapshot:
            return code, None, len(bsp_dict), 0.0, "", None

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
            return code, None, len(bsp_dict), 0.0, method
        
        # Train with n_jobs=1 to avoid thread explosion
        backtest_kwargs = {
            "model_type": model_type,
            "calibrate_method": "none",
            "n_jobs": 1,
            "profit_threshold": profit_threshold,
            "auto_profit_quantile": auto_profit_quantile,
            "profit_lookahead": profit_lookahead,
            "use_atr_label": bool(use_atr_label),
            "atr_period": int(atr_period),
            "atr_mult": float(atr_mult),
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
                    "use_atr_label": bool(use_atr_label),
                    "atr_period": int(atr_period),
                    "atr_mult": float(atr_mult),
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

        if current_accuracy >= float(min_accuracy):
            has_signal = False
            latest_signal_date = ""
            latest_signal_type = ""
            selected_bsp = None

            if require_signal:
                last_idx = last_klu.idx
                for bsp in latest_bsp_list:
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
                    selected_bsp = latest_bsp_list[0]
                    latest_signal_date = selected_bsp.klu.time.to_str()
                    latest_signal_type = selected_bsp.type2str()
                    if selected_bsp.is_buy:
                        latest_signal_type += " (Buy)"
                    else:
                        latest_signal_type += " (Sell)"

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
                    "latest_date": latest_signal_date if require_signal else last_klu.time.to_str(),
                    "signal_type": latest_signal_type
                }
        
        # Save to cache
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

        return code, result, len(bsp_dict), current_accuracy, method, accuracy_info

    except Exception as e:
        print(f"Error processing {code}: {e}")
        return code, None, 0, 0.0, f"error: {str(e)}", None

class StrategyRunner:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def _update_progress(self, run_id: int, processed: int, total: int, status: str = "running", result_json: str = None, result_count: int = None):
        session = self.storage.Session()
        try:
            run = session.query(StrategyRun).filter(StrategyRun.id == run_id).first()
            if run:
                run.processed_stocks = processed
                run.total_stocks = total
                run.status = status
                run.progress = int((processed / total) * 100) if total > 0 else 0
                if result_json:
                    run.result_json = result_json
                if result_count is not None:
                    try:
                        run.result_count = int(result_count)
                    except Exception:
                        run.result_count = 0
                if status in ["completed", "failed"]:
                    run.completed_at = datetime.datetime.utcnow()
                session.commit()
        except Exception as e:
            print(f"Error updating progress: {e}")
        finally:
            session.close()

    def run_strategy_sync(self, run_id: int, params: Dict[str, Any]):
        try:
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
            custom_chan_config = params.get('chan_config') or {}
            require_signal = params.get('require_signal', False)
            signal_lookback = int(params.get('signal_lookback', 5))
            signal_direction = params.get('signal_direction', 'buy')
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
            pool_id = params.get('pool_id')
            enable_rolling_lookback = params.get('enable_rolling_lookback', True)
            
            # 1. Get Stocks
            stocks = []
            if pool_id:
                print(f"Fetching stocks for pool_id: {pool_id}")
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

            with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
                # Sliding window submission to avoid overloading DB with thousands of pending tasks
                # Use a set to keep track of active futures
                active_futures = set()
                # Iterator for stocks
                stock_iter = iter(stocks)
                future_to_code = {}
                
                # Keep a buffer of e.g. 2 * max_workers to ensure CPU is always busy
                target_active = max_workers * 2
                
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
                            raw_autype,
                        )
                        active_futures.add(fut)
                        future_to_code[fut] = code
                        return True
                    except StopIteration:
                        return False

                # Initial fill
                for _ in range(target_active):
                    if not submit_next():
                        break
                
                # Process as they complete
                while active_futures:
                    # Wait for at least one future to complete
                    done, _ = concurrent.futures.wait(active_futures, return_when=concurrent.futures.FIRST_COMPLETED)
                    
                    for future in done:
                        active_futures.remove(future)
                        orig_code = future_to_code.pop(future)
                        processed += 1
                        
                        try:
                            r_code, r_result, r_samples, r_acc, r_method, r_info = future.result()
                            
                            if r_info and isinstance(r_info, dict) and "predictions" in r_info:
                                all_predictions[r_code] = r_info["predictions"]

                            if processed % 10 == 0:
                                print(f"[Strategy] {r_code} samples={r_samples} accuracy={r_acc:.3f} {r_method}")
                                self._update_progress(run_id, processed, len(stocks), "running")
                                
                            if r_result:
                                print(f"[Match] {r_code} samples={r_samples} acc={r_acc:.3f}")
                                results.append(r_result)
                                
                        except Exception as e:
                            print(f"Exception for {orig_code}: {e}")
                            traceback.print_exc()
                        
                        # Submit a new task to replace the completed one
                        submit_next()
            
            # Portfolio Backtest
            if portfolio_top_n and all_predictions:
                try:
                    # 1. Prepare Data
                    events_by_ts = {}
                    for code, preds in all_predictions.items():
                        for p in preds:
                            ts = int(p.get('ts', 0))
                            if ts > 0:
                                events_by_ts.setdefault(ts, []).append({
                                    'code': code,
                                    'prob': float(p.get('prob', 0)),
                                    'profit': float(p.get('profit', 0))
                                })
                    
                    sorted_ts = sorted(events_by_ts.keys())
                    min_score = float(min_signal_score) if min_signal_score is not None else 0.6
                    holding_period = int(params.get('holding_period', 3))
                    if holding_period < 1: holding_period = 1
                    
                    # 2. Simulate Trades
                    trades = [] # {entry_idx, exit_idx, return, code}
                    
                    for i, ts in enumerate(sorted_ts):
                        # Identify candidates
                        candidates = events_by_ts[ts]
                        candidates = [c for c in candidates if c['prob'] >= min_score]
                        candidates.sort(key=lambda x: x['prob'], reverse=True)
                        
                        # Check available slots
                        # Active trade: entry_idx <= i < exit_idx
                        active_trades = [t for t in trades if t['entry_idx'] <= i < t['exit_idx']]
                        slots_available = portfolio_top_n - len(active_trades)
                        
                        if slots_available > 0:
                            held_codes = {t['code'] for t in active_trades}
                            to_buy = []
                            for c in candidates:
                                if len(to_buy) >= slots_available:
                                    break
                                if c['code'] not in held_codes:
                                    to_buy.append(c)
                            
                            for c in to_buy:
                                trades.append({
                                    'code': c['code'],
                                    'entry_idx': i,
                                    'exit_idx': i + holding_period,
                                    'return': c['profit']
                                })
                    
                    # 3. Calculate Curve
                    step_returns = []
                    for i in range(len(sorted_ts)):
                        step_ret = 0.0
                        # Active trades at this step
                        active_trades = [t for t in trades if t['entry_idx'] <= i < t['exit_idx']]
                        
                        for t in active_trades:
                            # Simple linear attribution of return
                            step_ret += t['return'] / holding_period
                        
                        # Portfolio return = Sum(Position Returns) / TopN
                        # (Assuming equal capital allocation to N slots)
                        avg_step_ret = step_ret / portfolio_top_n
                        step_returns.append(avg_step_ret)
                    
                    # 4. Metrics
                    eq = 1.0
                    equity_curve = [1.0]
                    for r in step_returns:
                        eq *= (1.0 + r)
                        equity_curve.append(eq)
                        
                    total_return = eq - 1.0
                    sharpe = 0.0
                    max_dd = 0.0
                    
                    if step_returns:
                        arr = np.array(step_returns)
                        if np.std(arr) > 0:
                            # Simple Sharpe (not annualized)
                            sharpe = np.mean(arr) / np.std(arr)
                            # To annualize, we'd need frequency. 
                            # Assuming 30m (8 bars/day * 250 days = 2000)
                            if frequency == '30m':
                                sharpe *= np.sqrt(2000)
                            elif frequency == '1d':
                                sharpe *= np.sqrt(250)
                            elif frequency == '60m':
                                sharpe *= np.sqrt(1000)
                        
                        peak = -99999.0
                        for v in equity_curve:
                            if v > peak:
                                peak = v
                            dd = (peak - v) / peak if peak > 0 else 0
                            max_dd = max(max_dd, dd)
                            
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
                            "trade_count": len(trades)
                        }
                    }
                    results.insert(0, portfolio_res)
                except Exception as e:
                    print(f"Portfolio calculation failed: {e}")
                    traceback.print_exc()

            # Completed
            self._update_progress(run_id, processed, len(stocks), "completed", json.dumps(results), len(results))
            
        except Exception as e:
            traceback.print_exc()
            self._update_progress(run_id, 0, 0, "failed")
