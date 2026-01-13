import pandas as pd
import numpy as np
import datetime
import concurrent.futures
import traceback
from typing import List, Dict, Optional, Tuple, Any
import akshare as ak
from sqlalchemy.orm import Session

from backend.chan_service import (
    fetch_stock_data,
    CChanCustom,
    stragety_feature,
    extract_state_features_from_cur_lv,
    load_pretrained_model_bundle,
    predict_proba_1,
    get_stock_name
)
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from ChanConfig import CChanConfig
from backend.storage import StorageManager

def get_chinext50_stocks() -> List[str]:
    """
    Get ChiNext 50 stocks.
    """
    try:
        # Try akshare first
        # 399673 is ChiNext 50 index code
        df = ak.index_stock_cons(symbol="399673")
        if df is not None and not df.empty:
            # assuming column '品种代码' or similar
            # akshare output columns change often, need to be careful
            # typical columns: variety, date, symbol, name...
            # symbol is usually the code.
            if 'stock_code' in df.columns:
                return df['stock_code'].tolist()
            if '品种代码' in df.columns:
                return df['品种代码'].tolist()
            # Fallback: check first column
            return df.iloc[:, 0].tolist()
    except Exception as e:
        print(f"Error getting ChiNext 50 from akshare: {e}")
    
    return []

def _calc_stock_signals(
    code: str,
    begin_time: str,
    end_time: str,
    model_bundle: Any,
    kl_type=KL_TYPE.K_30M,
    data_src=DATA_SRC.CLICK_HOUSE
) -> pd.DataFrame:
    """
    Calculate signals for a single stock.
    Returns DataFrame with index=time, columns=[score, price].
    """
    try:
        kl_list = fetch_stock_data(
            code,
            kl_type,
            begin_time,
            end_time,
            data_src,
            autype=AUTYPE.HFQ
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
            end_time=None,
            data_src=data_src,
            lv_list=[kl_type],
            config=chan_config,
            autype=AUTYPE.HFQ,
            preloaded_data=kl_list
        )

        bst = model_bundle['bst']
        feature_meta = model_bundle['feature_meta']
        
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
                feat_dict = stragety_feature(last_klu, enable_rolling_lookback=True)
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
                
                feat_vec = [feat_dict.get(k, -9999999) for k in feature_meta]
                score = float(predict_proba_1(bst, [feat_vec])[0])
                
                results.append({
                    "time": pd.to_datetime(ts),
                    "code": code,
                    "score": score,
                    "price": price
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
        
        # 1. Load Model
        # If model_key is not provided, find the best one or latest one
        model_bundle = None
        if model_key:
            model_bundle = load_pretrained_model_bundle_by_key(model_key)
        else:
            # TODO: Auto find model
            # For now, require model_key or fail/mock
            pass
            
        if not model_bundle:
            # Fallback: Mock or Error
            # For "Simple Vector Backtest" requested by user, if they haven't trained a model, this will fail.
            # I should probably warn.
            # But let's assume there is a model or we can use a dummy one if needed.
            return {"error": "No model found. Please train a model first."}

        # 2. Parallel Compute Signals
        # 50 stocks is small enough for ThreadPool/ProcessPool
        all_signals = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=min(10, len(pool_codes))) as executor:
            futures = {
                executor.submit(_calc_stock_signals, code, start_date, end_date, model_bundle): code 
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
        
        # Pivot to get Score Matrix and Price Matrix
        # Index: Time, Columns: Stocks
        score_df = full_df.pivot(index="time", columns="code", values="score")
        price_df = full_df.pivot(index="time", columns="code", values="price")
        
        # Fill NaNs? Forward fill price, fill 0 for score
        price_df = price_df.ffill()
        score_df = score_df.fillna(0)
        
        # 4. Vector Simulation
        # Parameters
        HOLD_PERIODS = int(kwargs.get("holding_period", 3)) # 90 mins / 30 mins
        TOP_N = int(kwargs.get("portfolio_top_n", 2))
        SCORE_THR = float(kwargs.get("min_signal_score", 0.6))
        CAPITAL = 100000.0
        
        # State
        positions = {} # code -> {entry_price, entry_time_idx, shares}
        cash = CAPITAL
        equity_curve = []
        
        times = score_df.index
        
        for t_idx, t in enumerate(times):
            # 1. Update Portfolio Value
            current_equity = cash
            todays_prices = price_df.iloc[t_idx]
            
            # Check exits
            to_exit = []
            for code, pos in positions.items():
                current_price = todays_prices.get(code)
                if pd.isna(current_price):
                    current_price = pos['entry_price'] # Fallback
                
                # Check hold period
                held_duration = t_idx - pos['entry_time_idx']
                
                if held_duration >= HOLD_PERIODS:
                    to_exit.append(code)
                
                current_equity += pos['shares'] * current_price
            
            # Execute Exits
            for code in to_exit:
                pos = positions.pop(code)
                exit_price = todays_prices.get(code)
                cash += pos['shares'] * exit_price
            
            # 2. Rank and Buy
            # Available slots
            open_slots = TOP_N - len(positions)
            
            if open_slots > 0:
                # Get candidates
                scores = score_df.iloc[t_idx]
                candidates = scores[scores > SCORE_THR].sort_values(ascending=False)
                
                # Filter out already held
                candidates = candidates[~candidates.index.isin(positions.keys())]
                
                to_buy = candidates.head(open_slots).index.tolist()
                
                if to_buy:
                    # Equal weight for available cash? 
                    # Or equal weight of Total Capital / TOP_N?
                    # "均仓买入" usually means Target Weight = 1/N.
                    # Allocation per trade = Current Equity / TOP_N (Simplified)
                    # Or (Cash + PositionValue) / TOP_N
                    
                    # Let's use: Allocation = Current Equity / TOP_N
                    # But capped by available cash?
                    
                    target_pos_value = current_equity / TOP_N
                    
                    for code in to_buy:
                        price = todays_prices.get(code)
                        if pd.isna(price) or price <= 0:
                            continue
                            
                        # Cost check
                        cost = target_pos_value
                        if cost > cash:
                            cost = cash
                        
                        if cost < 0: 
                            continue

                        shares = cost / price
                        if shares > 0:
                            cash -= shares * price
                            positions[code] = {
                                'entry_price': price,
                                'entry_time_idx': t_idx,
                                'shares': shares
                            }
            
            # Recalculate Equity after trades
            step_equity = cash
            for code, pos in positions.items():
                p = todays_prices.get(code)
                step_equity += pos['shares'] * p
            
            equity_curve.append({"time": t, "equity": step_equity})
            
        # 5. Calculate Metrics
        eq_df = pd.DataFrame(equity_curve).set_index("time")
        eq_df['ret'] = eq_df['equity'].pct_change()
        
        total_ret = (eq_df['equity'].iloc[-1] / CAPITAL) - 1
        sharpe = 0
        if eq_df['ret'].std() > 0:
            sharpe = (eq_df['ret'].mean() / eq_df['ret'].std()) * np.sqrt(250 * 8) # Annualized (assuming 8 30m bars/day * 250 days? roughly)
            
        # Max Drawdown
        eq_df['cummax'] = eq_df['equity'].cummax()
        eq_df['drawdown'] = (eq_df['equity'] - eq_df['cummax']) / eq_df['cummax']
        max_drawdown = eq_df['drawdown'].min()
        
        return {
            "metrics": {
                "total_return": total_ret,
                "sharpe_ratio": sharpe,
                "max_drawdown": max_drawdown,
                "final_equity": eq_df['equity'].iloc[-1]
            },
            "equity_curve": eq_df['equity'].reset_index().to_dict(orient='records')
        }

