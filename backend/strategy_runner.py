import json
import datetime
import traceback
import asyncio
import baostock as bs
import concurrent.futures
import os
import copy
import hashlib
from typing import List, Dict, Any, Optional

from backend.chan_service import (
    fetch_stock_data,
    CChanCustom,
    train_time_split_backtest,
    stragety_feature,
    get_stock_name
)
from backend.storage import StorageManager, StrategyRun
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
    require_signal: bool,
    signal_lookback: int,
    signal_direction: str
):
    try:
        # Set thread limits for worker process
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        
        # Fetch Data
        kl_list = fetch_stock_data(
            code, 
            kl_type, 
            begin_time, 
            None, 
            data_src
        )
        
        if not kl_list or len(kl_list) < 100:
            return code, None, 0, 0.0, ""

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
                    "custom_chan_config": custom_chan_config,
                    "enable_rolling_lookback": enable_rolling_lookback,
                    "model_type": model_type,
                    "min_accuracy": float(min_accuracy),
                    "require_signal": require_signal,
                    "signal_lookback": signal_lookback,
                    "signal_direction": signal_direction,
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

        chan = CChanCustom(
            code=code,
            begin_time=begin_time,
            end_time=None,
            data_src=data_src,
            lv_list=[kl_type],
            config=chan_config,
            autype=AUTYPE.QFQ,
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
                    bsp_dict[last_bsp.klu.idx]["feature"].add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))
                except Exception:
                    pass
        
        if not last_snapshot:
            return code, None, len(bsp_dict), 0.0, ""

        last_klu = last_snapshot[0][-1][-1]
        latest_bsp_list = last_snapshot.get_latest_bsp(number=0)
        
        # Train with n_jobs=1 to avoid thread explosion
        bst, feature_meta, accuracy_info = train_time_split_backtest(
            bsp_dict, 
            model_type=model_type, 
            calibrate_method="isotonic",
            n_jobs=1
        )
        
        current_accuracy = 0.0
        method = ""
        if accuracy_info:
            current_accuracy = float(accuracy_info.get("accuracy", 0.0))
            method = (
                f"{accuracy_info.get('method', '')}"
                f" train={accuracy_info.get('train_count', 0)}"
                f" test={accuracy_info.get('test_count', 0)}"
                f" valid={accuracy_info.get('valid_count', 0)}"
                f" total={accuracy_info.get('total_count', 0)}"
            )
        
        result = None
        if current_accuracy >= min_accuracy:
            # Match Accuracy!
            # Check Signal if required
            has_signal = False
            latest_signal_date = ""
            latest_signal_type = ""

            if require_signal:
                last_idx = last_klu.idx
                
                for bsp in latest_bsp_list:
                    if signal_direction == 'buy' and not bsp.is_buy:
                        continue
                    if signal_direction == 'sell' and bsp.is_buy:
                        continue
                        
                    if abs(bsp.klu.idx - last_idx) < signal_lookback:
                        has_signal = True
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
            
            if has_signal:
                 stock_name = get_stock_name(code, data_src)
                 result = {
                     "code": code,
                     "name": stock_name,
                     "accuracy": current_accuracy,
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

        return code, result, len(bsp_dict), current_accuracy, method

    except Exception as e:
        print(f"Error processing {code}: {e}")
        return code, None, 0, 0.0, f"error: {str(e)}"

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
            data_length_years = float(params.get('data_length_years', 1.0))
            min_accuracy = float(params.get('min_accuracy', 0.8))
            custom_chan_config = params.get('chan_config') or {}
            require_signal = params.get('require_signal', False)
            signal_lookback = int(params.get('signal_lookback', 5))
            signal_direction = params.get('signal_direction', 'buy')
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

            self._update_progress(run_id, 0, len(stocks), "running")
            
            results = []
            processed = 0
            
            kl_type = KL_TYPE.K_DAY if frequency == '1d' else KL_TYPE.K_30M
            if frequency == '30m': kl_type = KL_TYPE.K_30M
            elif frequency == '5m': kl_type = KL_TYPE.K_5M
            elif frequency == '60m': kl_type = KL_TYPE.K_60M
            
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
                            require_signal,
                            signal_lookback,
                            signal_direction
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
                            r_code, r_result, r_samples, r_acc, r_method = future.result()
                            
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
            
            # Completed
            self._update_progress(run_id, processed, len(stocks), "completed", json.dumps(results), len(results))
            
        except Exception as e:
            traceback.print_exc()
            self._update_progress(run_id, 0, 0, "failed")
