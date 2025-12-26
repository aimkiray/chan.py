import sys
import os
from typing import Dict, TypedDict
import xgboost as xgb
import lightgbm as lgb
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
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
    if code.startswith("5"):
        return f"sh.{code}"
    if code.startswith("1"):
        return f"sz.{code}"
    return code

def get_stock_name(code, data_src_type=DATA_SRC.BAO_STOCK):
    """
    Get stock name from Data Source
    """
    code = normalize_code(code)
    try:
        if data_src_type == DATA_SRC.CLICK_HOUSE:
            from DataAPI.ClickHouseAPI import CClickHouseAPI
            # Use ClickHouse to get basic info
            # We use K_DAY as a placeholder since SetBasciInfo doesn't depend on k_type
            api = CClickHouseAPI(code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ)
            api.SetBasciInfo()
            if api.name:
                return api.name
            else:
                return code
                
        # Fallback to Baostock if not ClickHouse
        # Suppress login success message
        import io
        import sys
        
        # Check if already logged in (CBaoStock maintains state but we can't easily check bs internal state without calling login)
        # But bs.login() is idempotent.
        
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            bs.login()
        except:
            pass
        finally:
            sys.stdout = old_stdout
        
        rs = bs.query_stock_basic(code=code)
        if rs.error_code == '0' and rs.next():
            # code, code_name, ipoDate, outDate, type, status
            row = rs.get_row_data()
            return row[1] # code_name
    except Exception as e:
        print(f"Error fetching stock name: {e}")
    return code

from DataAPI.CommonStockAPI import CCommonStockApi

class PreloadedStockAPI(CCommonStockApi):
    def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None, data_list=None):
        self.data_list = data_list or []
        super().__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        yield from self.data_list
    
    @classmethod
    def do_init(cls):
        pass
    
    @classmethod
    def do_close(cls):
        pass

class CChanCustom(CChan):
    def __init__(self, code, begin_time=None, end_time=None, data_src=None, lv_list=None, config=None, autype=None, preloaded_data=None):
        self.preloaded_data = preloaded_data
        super().__init__(code, begin_time, end_time, data_src, lv_list, config, autype)
    
    def GetStockAPI(self):
        if self.preloaded_data is not None:
            # We need to return a class that accepts arguments but uses our preloaded data
            # Since CChan instantiates the class, we can't easily pass the instance.
            # But we can monkey-patch the __init__ of our custom class to use the preloaded data?
            # Or better, just return a factory class.
            
            # CChan calls: stockapi_cls(code=..., ...)
            # So we define a class that ignores args and uses self.preloaded_data
            
            data = self.preloaded_data
            class CustomAPI(PreloadedStockAPI):
                def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None):
                    super().__init__(code, k_type, begin_date, end_date, autype, data_list=data)
            
            return CustomAPI
            
        return super().GetStockAPI()

def fetch_stock_data(code, level, begin_time, end_time, data_src_type, autype=AUTYPE.QFQ):
    """
    Helper to fetch raw K-line data without calculating Chan elements.
    Useful for checking data freshness for caching.
    """
    code = normalize_code(code)
    
    # Create a temporary CChan instance just to resolve the StockAPI class
    # This is a bit hacky but avoids duplicating the StockAPI resolution logic
    # Set trigger_step=True to avoid loading data in __init__
    temp_chan = CChan(
        code=code,
        begin_time=begin_time,
        end_time=end_time,
        data_src=data_src_type,
        lv_list=[level],
        config=CChanConfig({"trigger_step": True}),
        autype=autype
    )
    
    StockAPI = temp_chan.GetStockAPI()
    StockAPI.do_init()
    try:
        stock_api = StockAPI(code=code, k_type=level, begin_date=begin_time, end_date=end_time, autype=autype)
        data_list = list(stock_api.get_kl_data())
        return data_list
    finally:
        StockAPI.do_close()

def get_latest_data_time(code, level, data_src_type):
    """
    Optimized function to get ONLY the latest data time without fetching full history.
    """
    import datetime
    code = normalize_code(code)
    
    # Define a short lookback period
    if level == KL_TYPE.K_DAY:
        days = 30
    else:
        days = 60 # Increased from 5 to 60 to handle stale data better
        if data_src_type == DATA_SRC.CLICK_HOUSE:
            days = 365 # Even longer for ClickHouse which is fast
        
    begin_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    end_time = None
    
    # Reuse fetch_stock_data but with short range
    try:
        data_list = fetch_stock_data(code, level, begin_time, end_time, data_src_type)
        if data_list:
            return str(data_list[-1].time)
    except Exception as e:
        print(f"Error fetching latest time: {e}")
    return None


def get_chan_data(code, trigger_step=True, bi_strict=True, level=KL_TYPE.K_DAY, data_src_type=DATA_SRC.BAO_STOCK, preloaded_data=None, do_predict=False, begin_time=None):
    code = normalize_code(code)
    
    # Adjust begin_time based on frequency to avoid fetching too much data
    import datetime
    
    if begin_time is None:
        # Special handling for JQData restriction (2024-09-13 onwards)
        if level == KL_TYPE.K_DAY:
            begin_time = "2020-01-01"
        elif level == KL_TYPE.K_60M or level == KL_TYPE.K_30M:
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        else: # 5m, 15m, 1m
            # Increase lookback to 365 days for minute data to handle stale data
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        
    end_time = None
    data_src = data_src_type
    lv_list = [level]

    # If not predicting (training), we can disable trigger_step to speed up loading
    # unless trigger_step is explicitly required for some other reason.
    # But usually trigger_step is only for simulating history for BSP collection.
    if not do_predict:
        trigger_step = False

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
        if preloaded_data:
            chan = CChanCustom(
                code=code,
                begin_time=begin_time,
                end_time=end_time,
                data_src=data_src,
                lv_list=lv_list,
                config=config,
                autype=AUTYPE.QFQ,
                preloaded_data=preloaded_data
            )
        else:
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
    
    if trigger_step:
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
    else:
        # If not trigger_step, CChan has already processed everything in init
        # We just return the final state
        last_snapshot = chan
        # bsp_dict is empty because we didn't step through history to collect them
        # This is fine if do_predict is False
             
    return chan, bsp_dict, last_snapshot, config
             
    return chan, bsp_dict, last_snapshot, config

class SingleClassModel:
    def __init__(self, label):
        self.label = label
    
    def predict_proba(self, X):
        # Return probability 1.0 for the class 'label', 0.0 for others
        # Format: [[prob_0, prob_1]]
        if self.label == 1:
            return [[0.0, 1.0]] * len(X)
        else:
            return [[1.0, 0.0]] * len(X)

def get_or_train_model(_bsp_dict, _chan, model_type="xgboost"):
    # Prepare data for XGBoost
    X_train = []
    y_train = []
    
    if not _bsp_dict:
        return None, None

    # Extract features
    all_keys = set()
    for info in _bsp_dict.values():
        for k in info['feature'].keys():
            all_keys.add(k)
    feature_meta = list(all_keys)
    
    for idx, info in _bsp_dict.items():
        # Labeling
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
        
    print(f"Training data size: {len(X_train)}")
    print(f"Class distribution: {set(y_train)}")
    
    if not X_train:
        print("Skipping training: No data.")
        return None, feature_meta
        
    if len(set(y_train)) < 2:
        print(f"Skipping training: Single class detected. Classes: {set(y_train)}")
        # Return a dummy model that always predicts this class
        return SingleClassModel(list(set(y_train))[0]), feature_meta
        
    # Train Model based on type
    model = None
    print(f"Training model: {model_type}")
    
    try:
        if model_type == "xgboost":
            model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, eval_metric='logloss')
            model.fit(X_train, y_train)
        elif model_type == "lightgbm":
            model = lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, verbosity=-1)
            model.fit(X_train, y_train)
        elif model_type == "mlp":
            # MLP requires scaling and handling of missing values
            # Increase max_iter and use adaptive learning rate to help convergence
            model = Pipeline([
                ('imputer', SimpleImputer(missing_values=-9999999, strategy='mean')),
                ('scaler', StandardScaler()),
                ('mlp', MLPClassifier(
                    hidden_layer_sizes=(100, 50), 
                    max_iter=2000, 
                    learning_rate='adaptive',
                    early_stopping=True,
                    random_state=42
                ))
            ])
            model.fit(X_train, y_train)
        else:
            print(f"Unknown model type: {model_type}, falling back to XGBoost")
            model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, eval_metric='logloss')
            model.fit(X_train, y_train)
    except Exception as e:
        print(f"Model training failed: {e}")
        return None, feature_meta
    
    return model, feature_meta

def predict_bsp(model, bsp, feature_meta):
    if not model or not bsp:
        print("Prediction skipped: Model or BSP is None")
        return 0.0
        
    feat_vec = [bsp.features.get(k, -9999999) for k in feature_meta]
    # Predict prob
    try:
        probs = model.predict_proba([feat_vec])
        # probs is [[prob_0, prob_1]]
        # If model only has one class, probs might be [[1.0]] (not standard sklearn behavior but possible)
        # Sklearn classifiers usually return probability for all classes in model.classes_
        
        print(f"Model raw probabilities: {probs}")

        # Check shape
        if len(probs[0]) >= 2:
            return probs[0][1]
        elif len(probs[0]) == 1:
            # If only one probability returned, check model.classes_
            # But standard sklearn predict_proba returns columns for all classes.
            # If we trained with 2 classes, it returns 2 columns.
            
            # If using SingleClassModel, it handles it correctly.
            # If sklearn model somehow has 1 class (should be caught by get_or_train_model check), handle gracefully.
            val = probs[0][0]
            # If the only class is 1, return val. If 0, return 1-val? No, predict_proba usually corresponds to classes_.
            # Since we can't easily access classes_ from Pipeline easily without digging, let's assume get_or_train_model handles single class.
            return val 
            
        return 0.0
    except Exception as e:
        print(f"Prediction failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 0.0

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
