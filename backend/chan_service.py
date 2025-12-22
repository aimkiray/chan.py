import sys
import os
from typing import Dict, TypedDict
import xgboost as xgb
import baostock as bs

# Add parent directory to path to allow importing from root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Chan import CChan
from ChanConfig import CChanConfig
from ChanModel.Features import CFeatures
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Common.CTime import CTime
from BuySellPoint.BS_Point import CBS_Point
from Plot.PlotMeta import CChanPlotMeta

class T_SAMPLE_INFO(TypedDict):
    feature: CFeatures
    is_buy: bool
    open_time: CTime

def stragety_feature(last_klu):
    return {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
        "high_low_rate": (last_klu.high - last_klu.low)/last_klu.close,
        "close": last_klu.close,
        "volume": float(last_klu.qfq_volume) if hasattr(last_klu, 'qfq_volume') else 0.0
    }

def normalize_code(code):
    code = code.strip().lower()
    if code.startswith("sh.") or code.startswith("sz."):
        return code
    if code.startswith("sh") and len(code) == 8 and code[2:].isdigit():
        return f"sh.{code[2:]}"
    if code.startswith("sz") and len(code) == 8 and code[2:].isdigit():
        return f"sz.{code[2:]}"
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith("0") or code.startswith("3"):
        return f"sz.{code}"
    return code

def get_stock_name(code):
    """
    Get stock name from Baostock
    """
    code = normalize_code(code)
    try:
        # Ensure login
        bs.login()
        
        rs = bs.query_stock_basic(code=code)
        if rs.error_code == '0' and rs.next():
            # code, code_name, ipoDate, outDate, type, status
            row = rs.get_row_data()
            return row[1] # code_name
    except Exception as e:
        print(f"Error fetching stock name: {e}")
    return code

def get_chan_data(code, trigger_step=True, bi_strict=True, level=KL_TYPE.K_DAY, data_src_type=DATA_SRC.BAO_STOCK):
    code = normalize_code(code)
    
    # Adjust begin_time based on frequency to avoid fetching too much data
    import datetime
    
    # Special handling for JQData restriction (2024-09-13 onwards)
    if data_src_type == DATA_SRC.JQ_DATA:
         begin_time = "2024-09-13"
    elif level == KL_TYPE.K_DAY:
        begin_time = "2020-01-01"
    elif level == KL_TYPE.K_60M or level == KL_TYPE.K_30M:
        begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
    elif level == KL_TYPE.K_1S:
        begin_time = None # Mock/1s specific
    else: # 5m, 15m, 1m
        begin_time = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
        
    end_time = None
    data_src = data_src_type
    lv_list = [level]

    config = CChanConfig({
        "trigger_step": trigger_step, 
        "bi_strict": bi_strict,
        "bi_fx_check": "strict" if bi_strict else "half",
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
        "mean_metrics": [5, 20],
    })

    try:
        chan = CChan(
            code=code,
            begin_time=begin_time,
            end_time=end_time,
            data_src=data_src,
            lv_list=lv_list,
            config=config,
            autype=AUTYPE.QFQ,
        )
    except Exception as e:
        print(f"Error loading CChan: {e}")
        return None, None, None, None

    bsp_dict: Dict[int, T_SAMPLE_INFO] = {} 
    last_snapshot = None
    
    print(f"Start processing {code} from {begin_time}...")
    # Iterate through steps to collect training samples
    for i, chan_snapshot in enumerate(chan.step_load()):
        if i % 100 == 0:
            print(f"Processing step {i}...")
        last_snapshot = chan_snapshot
        
        last_klu = chan_snapshot[0][-1][-1]
        bsp_list = chan_snapshot.get_latest_bsp()
        
        if not bsp_list:
            continue
            
        last_bsp = bsp_list[0]
        cur_lv_chan = chan_snapshot[0]
        
        # Record BSP when it appears
        if last_bsp.klu.idx not in bsp_dict and cur_lv_chan[-2].idx == last_bsp.klu.klc.idx:
            bsp_dict[last_bsp.klu.idx] = {
                "feature": last_bsp.features,
                "is_buy": last_bsp.is_buy,
                "open_time": last_klu.time,
                "bsp_obj": last_bsp
            }
            # Add custom strategy features
            bsp_dict[last_bsp.klu.idx]['feature'].add_feat(stragety_feature(last_klu))

    # Re-calculate to ensure latest state is fully updated for plotting
    if last_snapshot:
        for lv in last_snapshot.lv_list:
             # Disable step calculation mode to force full update including virtual Bi
             last_snapshot.kl_datas[lv].step_calculation = False
             last_snapshot.kl_datas[lv].cal_seg_and_zs()
             
    return chan, bsp_dict, last_snapshot, config

def get_or_train_model(_bsp_dict, _chan):
    # Prepare data for XGBoost
    X_train = []
    y_train = []
    
    # Labeling logic: 
    # If a buy point is followed by a rise, it's valid (1).
    # If a sell point is followed by a drop, it's valid (1).
    # We use a simple heuristic: profit > threshold within N bars?
    # Or just use the standard Chan logic if available.
    
    # For now, let's assume we label based on future performance.
    # Since we are in simulation, we can peek future? No, step_load simulates real-time.
    # But for training, we need historical labels.
    
    # Wait, the original code used 'is_stock=True' which usually fetches all data.
    # Here we collected _bsp_dict during step_load.
    
    # Let's simplify: We return a dummy model or load a pre-trained one.
    # Or train on the fly with what we have.
    
    if not _bsp_dict:
        return None, None

    # Extract features
    # We need to know the feature names.
    # Collect all possible keys from all samples to handle different BSP types
    all_keys = set()
    for info in _bsp_dict.values():
        for k in info['feature'].keys():
            all_keys.add(k)
    feature_meta = list(all_keys)
    
    for idx, info in _bsp_dict.items():
        # Labeling
        # Check profit in next 5 bars
        lookahead = 5
        cur_klu = info['bsp_obj'].klu
        future_klu = cur_klu
        for _ in range(lookahead):
            if future_klu.next:
                future_klu = future_klu.next
            else:
                break
        
        profit = 0.0
        if info['is_buy']:
            profit = (future_klu.close - cur_klu.close) / cur_klu.close
        else:
            profit = (cur_klu.close - future_klu.close) / cur_klu.close
            
        label = 1 if profit > 0 else 0
        
        # Use -9999999 for missing values (standard for XGBoost)
        feat_vec = [info['feature'].get(k, -9999999) for k in feature_meta]
        X_train.append(feat_vec)
        y_train.append(label)
        
    if not X_train or len(set(y_train)) < 2:
        print(f"Skipping training: Not enough data or classes. Classes: {set(y_train)}")
        return None, feature_meta
        
    # Train XGBoost
    model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, eval_metric='logloss')
    model.fit(X_train, y_train)
    
    return model, feature_meta

def predict_bsp(model, bsp, feature_meta):
    if not model or not bsp:
        return 0.0
        
    feat_vec = [bsp.features.get(k, -9999999) for k in feature_meta]
    # Predict prob
    probs = model.predict_proba([feat_vec])
    # probs is [[prob_0, prob_1]]
    return probs[0][1]

def download_stock_history(code, frequency='1d'):
    import csv
    import io
    import datetime

    code = normalize_code(code)
    bs.login()
    
    freq_map = {
        "1d": "d",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "60m": "60",
        "1m": "1",
    }
    bs_freq = freq_map.get(frequency, "d")
    
    # Fields: standard OHLCV + others
    fields = "date,time,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg"
    
    rs = bs.query_history_k_data_plus(
        code=code,
        fields=fields,
        start_date='1990-01-01',
        end_date=datetime.datetime.now().strftime("%Y-%m-%d"),
        frequency=bs_freq,
        adjustflag="2" # QFQ
    )
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(fields.split(','))
    
    while (rs.error_code == '0') & rs.next():
        writer.writerow(rs.get_row_data())
        
    bs.logout()
    return output.getvalue()
