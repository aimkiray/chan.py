import json
from typing import Dict, TypedDict

import xgboost as xgb

from Chan import CChan
from ChanConfig import CChanConfig
from ChanModel.Features import CFeatures
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Common.CTime import CTime
from BuySellPoint.BS_Point import CBS_Point

class T_SAMPLE_INFO(TypedDict):
    feature: CFeatures
    is_buy: bool
    open_time: CTime

def stragety_feature(last_klu):
    # Simple features for demonstration
    # In a real scenario, you would add more technical indicators here
    return {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
        "high_low_rate": (last_klu.high - last_klu.low)/last_klu.close,
        "close": last_klu.close,
        "volume": float(last_klu.qfq_volume) if hasattr(last_klu, 'qfq_volume') else 0.0
    }

def predict_bsp(model: xgb.Booster, last_bsp: CBS_Point, meta: Dict[str, int]):
    missing = -9999999
    feature_arr = [missing] * len(meta)
    for feat_name, feat_value in last_bsp.features.items():
        if feat_name in meta:
            feature_arr[meta[feat_name]] = feat_value
    feature_arr = [feature_arr]
    dtest = xgb.DMatrix(feature_arr, missing=missing)
    return model.predict(dtest)[0]

def run_analysis():
    print("Initializing Chan Theory Analysis for China Tourism Group Duty Free (sh.601888)...")
    
    code = "sh.601888"
    begin_time = "2020-01-01"
    end_time = None
    data_src = DATA_SRC.BAO_STOCK
    lv_list = [KL_TYPE.K_DAY]

    config = CChanConfig({
        "trigger_step": True, 
        "bi_strict": True,
        "skip_step": 0,
        "divergence_rate": float("inf"),
        "bsp2_follow_1": False,
        "bsp3_follow_1": False,
        "min_zs_cnt": 0,
        "bs1_peak": False,
        "macd_algo": "peak",
        "bs_type": '1,2,3a,1p,2s,3b',
        "print_warning": False,
        "zs_algo": "normal",
    })

    chan = CChan(
        code=code,
        begin_time=begin_time,
        end_time=end_time,
        data_src=data_src,
        lv_list=lv_list,
        config=config,
        autype=AUTYPE.QFQ,
    )

    bsp_dict: Dict[int, T_SAMPLE_INFO] = {} 
    
    print("Processing data and collecting features...")
    step_cnt = 0
    
    # Store the very last snapshot to analyze current status
    last_snapshot = None
    
    for chan_snapshot in chan.step_load():
        step_cnt += 1
        last_snapshot = chan_snapshot
        
        last_klu = chan_snapshot[0][-1][-1]
        bsp_list = chan_snapshot.get_latest_bsp()
        
        if not bsp_list:
            continue
            
        last_bsp = bsp_list[0]
        cur_lv_chan = chan_snapshot[0]
        
        # Record BSP when it appears (check if we already recorded this idx to avoid duplicates)
        if last_bsp.klu.idx not in bsp_dict and cur_lv_chan[-2].idx == last_bsp.klu.klc.idx:
            bsp_dict[last_bsp.klu.idx] = {
                "feature": last_bsp.features,
                "is_buy": last_bsp.is_buy,
                "open_time": last_klu.time,
                "bsp_obj": last_bsp # Keep object for later reference if needed
            }
            # Add custom strategy features
            bsp_dict[last_bsp.klu.idx]['feature'].add_feat(stragety_feature(last_klu))

    if not bsp_dict:
        print("No Buy/Sell points found in the specified period.")
        return

    print(f"Collected {len(bsp_dict)} potential Buy/Sell points.")

    # 1. Label generation (Training Phase)
    # Get the final valid BSPs from the completed Chan object
    bsp_academy = [bsp.klu.idx for bsp in chan.get_latest_bsp(number=0)]
    
    feature_meta = {}
    cur_feature_idx = 0
    samples = []
    labels = []
    
    for bsp_klu_idx, feature_info in bsp_dict.items():
        label = 1 if bsp_klu_idx in bsp_academy else 0
        labels.append(label)
        
        # Build feature vector
        sample_feats = {}
        for feature_name, value in feature_info['feature'].items():
            if feature_name not in feature_meta:
                feature_meta[feature_name] = cur_feature_idx
                cur_feature_idx += 1
            sample_feats[feature_meta[feature_name]] = value
        samples.append(sample_feats)

    # Convert to DMatrix
    # We need to construct a sparse matrix or dense matrix
    # For simplicity, let's use a list of lists (dense)
    X = []
    for s in samples:
        row = [-9999999] * len(feature_meta)
        for feat_idx, val in s.items():
            row[feat_idx] = val
        X.append(row)
    
    print("Training XGBoost model on historical validity of signals...")
    dtrain = xgb.DMatrix(X, label=labels, missing=-9999999)
    param = {'max_depth': 3, 'eta': 0.1, 'objective': 'binary:logistic', 'eval_metric': 'auc'}
    bst = xgb.train(param, dtrain, num_boost_round=20)
    
    print("Training complete.")
    
    # 2. Analyze the CURRENT situation (Prediction Phase)
    # Check the latest BSP from the last snapshot
    if last_snapshot:
        latest_bsp_list = last_snapshot.get_latest_bsp()
        if latest_bsp_list:
            latest_bsp = latest_bsp_list[0]
            # Check if this BSP is recent (e.g. within last few bars)
            last_klu = last_snapshot[0][-1][-1]
            days_diff = (last_klu.time.ts - latest_bsp.klu.time.ts) / (24 * 3600)
            
            print("\n" + "="*50)
            print("LATEST SIGNAL ANALYSIS")
            print("="*50)
            print(f"Current Date: {last_klu.time}")
            print(f"Last Signal Date: {latest_bsp.klu.time} ({days_diff} days ago)")
            print(f"Signal: {'BUY' if latest_bsp.is_buy else 'SELL'} (Type: {latest_bsp.type2str()})")
            
            # Predict validity
            # We need to reconstruct features for this latest BSP
            # Note: The features in latest_bsp.features should already be populated by the Chan calculation
            # But we added 'stragety_feature' manually in the loop. 
            # We need to add it here too for the prediction to be consistent.
            # However, latest_bsp might be one we already processed in the loop?
            # If it's the very last one, maybe.
            
            latest_bsp.features.add_feat(stragety_feature(last_klu))
            
            score = predict_bsp(bst, latest_bsp, feature_meta)
            print(f"Model Confidence (Probability of Signal Validity): {score:.4f}")
            
            if score > 0.5:
                print(">> Model suggests this signal is likely VALID.")
            else:
                print(">> Model suggests this signal might be INVALID/WEAK.")
                
            print(f"Last Close Price: {last_klu.close}")
            
        else:
            print("\n" + "="*50)
            print("CURRENT STATUS")
            print("="*50)
            last_klu = last_snapshot[0][-1][-1]
            print(f"Current Date: {last_klu.time}")
            print(f"Last Close Price: {last_klu.close}")
            print("No recent Buy/Sell point detected.")
            
    # Also print some general stats
    valid_count = sum(labels)
    total_count = len(labels)
    print("\n" + "-"*30)
    print(f"Historical Stats (2020-Now):")
    print(f"Total Signals Detected: {total_count}")
    print(f"Valid Signals: {valid_count} ({valid_count/total_count*100:.1f}%)")

if __name__ == "__main__":
    run_analysis()
