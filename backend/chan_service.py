import sys
import os
import json
import datetime
import gc
import hashlib
import pickle
import gzip
import uuid
from typing import Dict, TypedDict, Optional
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo
import concurrent.futures
import xgboost as xgb
import lightgbm as lgb
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
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

def stragety_feature(last_klu, enable_rolling_lookback=True):
    # Improved feature engineering for better generalization
    # Avoid absolute price/volume values which lead to overfitting
    
    # Calculate simple moving averages (approximation)
    # Since we only have last_klu here, we rely on what's available or compute ratios
    # But ideally we should have access to history. 
    # For now, we add more normalized indicators.
    
    # Note: KLine Unit usually has MACD computed if CChan ran with it.
    
    feat = {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
        "high_low_rate": (last_klu.high - last_klu.low)/last_klu.close,
        # Normalized Close: Close relative to Open (intraday strength)
        "close_open_ratio": last_klu.close / last_klu.open,
        # Normalized High/Low position
        "close_pos_in_bar": (last_klu.close - last_klu.low) / (last_klu.high - last_klu.low + 1e-8),
    }

    if enable_rolling_lookback:
        # Add Long-Term Context (Rolling Lookback)
        # We traverse back 250 bars (approx 1 year) to find High/Low
        # This captures "Position in Range" which is critical for Zhuang Gu (e.g. low in 1 year range)
        try:
            high_250 = last_klu.high
            low_250 = last_klu.low
            curr = last_klu
            count = 0
            limit = 250
            while curr.pre and count < limit:
                curr = curr.pre
                if curr.high > high_250: high_250 = curr.high
                if curr.low < low_250: low_250 = curr.low
                count += 1
            
            if count > 20: # Only if we have enough history
                feat["price_rank_250"] = (last_klu.close - low_250) / (high_250 - low_250 + 1e-8)
                feat["volatility_250"] = (high_250 - low_250) / (low_250 + 1e-8)
        except Exception:
            pass
    
    if hasattr(last_klu, 'macd'):
        feat.update({
            "macd_dif": last_klu.macd.DIF,
            "macd_dea": last_klu.macd.DEA,
            "macd_bar": last_klu.macd.macd,
        })
        
    return feat

def normalize_code(code):
    code = code.strip().lower()
    if code.endswith(".sh"):
        return f"sh.{code[:-3]}"
    if code.endswith(".sz"):
        return f"sz.{code[:-3]}"
    if code.endswith(".bj"):
        return f"bj.{code[:-3]}"
    
    if code.startswith("sh.") or code.startswith("sz.") or code.startswith("bj."):
        return code
    if code.startswith("sh") and len(code) == 8 and code[2:].isdigit():
        return f"sh.{code[2:]}"
    if code.startswith("sz") and len(code) == 8 and code[2:].isdigit():
        return f"sz.{code[2:]}"
    if code.startswith("bj") and len(code) == 8 and code[2:].isdigit():
        return f"bj.{code[2:]}"
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith("0") or code.startswith("3"):
        return f"sz.{code}"
    if code.startswith("5"):
        return f"sh.{code}"
    if code.startswith("1"):
        return f"sz.{code}"
    return code

def _is_cn_stock_market_open(now: Optional[datetime.datetime] = None) -> bool:
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = None

    if now is None:
        now = datetime.datetime.now(tz) if tz else datetime.datetime.now()
    elif tz and now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    elif tz:
        now = now.astimezone(tz)

    if now.weekday() >= 5:
        return False

    hhmm = now.hour * 60 + now.minute
    open_am = 9 * 60 + 30
    close_am = 11 * 60 + 30
    open_pm = 13 * 60
    close_pm = 15 * 60
    return (open_am <= hhmm < close_am) or (open_pm <= hhmm < close_pm)

def _eastmoney_secid(code: str) -> str:
    code = normalize_code(code)
    if code.startswith("sh."):
        return f"1.{code.split('.', 1)[1]}"
    if code.startswith("sz."):
        return f"0.{code.split('.', 1)[1]}"
    if code.startswith("bj."):
        return f"0.{code.split('.', 1)[1]}"
    digits = "".join([ch for ch in code if ch.isdigit()])
    if digits.startswith("6"):
        return f"1.{digits}"
    return f"0.{digits}"

def _eastmoney_klt(level: KL_TYPE) -> Optional[int]:
    mp = {
        KL_TYPE.K_1M: 1,
        KL_TYPE.K_5M: 5,
        KL_TYPE.K_15M: 15,
        KL_TYPE.K_30M: 30,
        KL_TYPE.K_60M: 60,
        KL_TYPE.K_DAY: 101,
        KL_TYPE.K_WEEK: 102,
        KL_TYPE.K_MON: 103,
    }
    return mp.get(level)

def _parse_eastmoney_time(s: str) -> Optional[CTime]:
    raw = str(s or "").strip()
    if not raw:
        return None
    try:
        if " " in raw:
            try:
                dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M")
            except Exception:
                dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d")
        return CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)
    except Exception:
        return None

def _eastmoney_tail_limit(level: KL_TYPE) -> int:
    if level == KL_TYPE.K_1M:
        return 320
    if level == KL_TYPE.K_5M:
        return 120
    if level == KL_TYPE.K_15M:
        return 80
    if level == KL_TYPE.K_30M:
        return 60
    if level == KL_TYPE.K_60M:
        return 40
    if level in (KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON):
        return 16
    return 16

def _fetch_latest_klines_eastmoney(code: str, level: KL_TYPE, limit: int = 5, autype: AUTYPE = AUTYPE.QFQ):
    from Common.CEnum import DATA_FIELD
    from KLine.KLine_Unit import CKLine_Unit

    klt = _eastmoney_klt(level)
    if klt is None:
        return []

    secid = _eastmoney_secid(code)
    fqt = 1 if autype == AUTYPE.QFQ else 0
    fields1 = "f1,f2,f3,f4,f5,f6"
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    params = {
        "fields1": fields1,
        "fields2": fields2,
        "klt": str(klt),
        "fqt": str(fqt),
        "secid": secid,
        "end": "20500101",
        "lmt": str(int(limit)),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": "https://quote.eastmoney.com/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        payload = json.loads(raw) if raw else {}
    except Exception:
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(klines, list) or not klines:
        return []

    out = []
    for row in klines:
        if not isinstance(row, str):
            continue
        parts = row.split(",")
        if len(parts) < 6:
            continue
        t = _parse_eastmoney_time(parts[0])
        if t is None:
            continue

        def to_float(v):
            try:
                return float(v)
            except Exception:
                return None

        o = to_float(parts[1])
        c = to_float(parts[2])
        h = to_float(parts[3])
        l = to_float(parts[4])
        v = to_float(parts[5]) or 0.0
        amt = to_float(parts[6]) if len(parts) > 6 else 0.0
        amt = amt or 0.0

        if c is None or c <= 0:
            continue
        if o is None or o <= 0:
            o = c
        if h is None or h <= 0:
            h = c
        if l is None or l <= 0:
            l = c
        h = max(h, o, c)
        l = min(l, o, c)

        out.append(
            CKLine_Unit(
                {
                    DATA_FIELD.FIELD_TIME: t,
                    DATA_FIELD.FIELD_OPEN: float(round(o, 2)),
                    DATA_FIELD.FIELD_HIGH: float(round(h, 2)),
                    DATA_FIELD.FIELD_LOW: float(round(l, 2)),
                    DATA_FIELD.FIELD_CLOSE: float(round(c, 2)),
                    DATA_FIELD.FIELD_VOLUME: float(v),
                    DATA_FIELD.FIELD_TURNOVER: float(amt),
                }
            )
        )

    if not out:
        return []

    out.sort(key=lambda x: x.time.ts)
    uniq = []
    last_ts = None
    for k in out:
        ts = k.time.ts
        if last_ts is not None and ts == last_ts:
            uniq[-1] = k
            continue
        uniq.append(k)
        last_ts = ts
    return uniq

def _merge_klines_tail(base_list, tail_list):
    if not base_list:
        return list(tail_list or [])
    if not tail_list:
        return base_list

    for k in tail_list:
        ts = k.time.ts
        if base_list and ts > base_list[-1].time.ts:
            base_list.append(k)
            continue
        replaced = False
        for i in range(len(base_list) - 1, -1, -1):
            cur_ts = base_list[i].time.ts
            if cur_ts == ts:
                base_list[i] = k
                replaced = True
                break
            if cur_ts < ts:
                base_list.insert(i + 1, k)
                replaced = True
                break
        if not replaced:
            base_list.insert(0, k)

    dedup = []
    seen = set()
    for k in base_list:
        ts = k.time.ts
        if ts in seen:
            continue
        seen.add(ts)
        dedup.append(k)
    return dedup

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

def fetch_stock_data(code, level, begin_time, end_time, data_src_type, autype=AUTYPE.QFQ, use_online_latest: bool = False):
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
        if (
            use_online_latest
            and data_src_type == DATA_SRC.CLICK_HOUSE
            and end_time is None
        ):
            tail = _fetch_latest_klines_eastmoney(code, level, limit=_eastmoney_tail_limit(level), autype=autype)
            if tail:
                data_list = _merge_klines_tail(data_list, tail)
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
        data_list = fetch_stock_data(
            code,
            level,
            begin_time,
            end_time,
            data_src_type,
            use_online_latest=(data_src_type == DATA_SRC.CLICK_HOUSE),
        )
        if data_list:
            return str(data_list[-1].time)
    except Exception as e:
        print(f"Error fetching latest time: {e}")
    return None


def get_chan_data(code, trigger_step=True, bi_strict=True, level=KL_TYPE.K_DAY, data_src_type=DATA_SRC.BAO_STOCK, preloaded_data=None, do_predict=False, begin_time=None, enable_rolling_lookback=True):
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
        kl_count = 0
        bsp_count = 0
        
        # Iterate through steps to collect training samples
        for i, chan_snapshot in enumerate(chan.step_load()):
            kl_count += 1
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
                bsp_count += 1
                bsp_dict[last_bsp.klu.idx] = {
                    "feature": last_bsp.features,
                    "is_buy": last_bsp.is_buy,
                    "open_time": last_klu.time,
                    "bsp_obj": last_bsp
                }
                # Add custom strategy features
                bsp_dict[last_bsp.klu.idx]['feature'].add_feat(stragety_feature(last_klu, enable_rolling_lookback=enable_rolling_lookback))

        print(f"[DEBUG] {code}: Processed {kl_count} K-lines, Found {bsp_count} valid Buy/Sell Points (Training Samples)")

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
    
    def predict(self, X):
        return [self.label] * len(X)

def build_training_data(bsp_dict, feature_meta=None):
    X = []
    y = []
    if not bsp_dict:
        return X, y, [] if feature_meta is None else feature_meta

    if feature_meta is None:
        all_keys = set()
        for info in bsp_dict.values():
            for k in info['feature'].keys():
                all_keys.add(k)
        feature_meta = sorted(all_keys)

    for _, info in bsp_dict.items():
        lookahead = 5
        cur_klu = info['bsp_obj'].klu
        base_price = cur_klu.close
        future_klu = cur_klu
        for _ in range(lookahead):
            if future_klu.next:
                future_klu = future_klu.next
            else:
                break
        if info['is_buy']:
            profit = (future_klu.close - base_price) / (base_price or 1e-12)
        else:
            profit = (base_price - future_klu.close) / (base_price or 1e-12)
            
        # Threshold: Must be at least 1% profit to count as valid signal
        # This filters out noise where profit is 0.001%
        label = 1 if profit > 0.01 else 0
        feat_vec = [info['feature'].get(k, -9999999) for k in feature_meta]
        X.append(feat_vec)
        y.append(label)

    return X, y, feature_meta

def build_feature_meta(bsp_dict):
    if not bsp_dict:
        return []
    all_keys = set()
    for info in bsp_dict.values():
        for k in info['feature'].keys():
            all_keys.add(k)
    return sorted(all_keys)

def build_training_data_from_infos(infos, feature_meta):
    X = []
    y = []
    if not infos:
        return X, y

    for info in infos:
        lookahead = 5
        
        decision_klu = info.get('decision_klu')
        
        if decision_klu:
            cur_klu = decision_klu
            base_price = cur_klu.close
        else:
            cur_klu = info['bsp_obj'].klu
            base_price = cur_klu.close
            
        future_klu = cur_klu
        for _ in range(lookahead):
            if getattr(future_klu, 'next', None):
                future_klu = future_klu.next
            else:
                break
        
        if info['is_buy']:
            profit = (future_klu.close - base_price) / (base_price or 1e-12)
        else:
            profit = (base_price - future_klu.close) / (base_price or 1e-12)
            
        # Threshold: Must be at least 1% profit to count as valid signal
        # This filters out noise where profit is 0.001%
        label = 1 if profit > 0.01 else 0
        feat_vec = [info['feature'].get(k, -9999999) for k in feature_meta]
        X.append(feat_vec)
        y.append(label)

    return X, y

def train_model_from_xy(X_train, y_train, model_type="xgboost", n_jobs=-1):
    if not X_train:
        return None
    if len(set(y_train)) < 2:
        return SingleClassModel(list(set(y_train))[0])

    model = None
    if model_type == "xgboost":
        model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, eval_metric='logloss', n_jobs=n_jobs)
        model.fit(X_train, y_train)
    elif model_type == "lightgbm":
        model = lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, verbosity=-1, n_jobs=n_jobs)
        model.fit(X_train, y_train)
    elif model_type == "mlp":
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
        model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, eval_metric='logloss')
        model.fit(X_train, y_train)

    return model

def predict_proba_1(model, X):
    probs = model.predict_proba(X)
    out = []
    for row in probs:
        if len(row) >= 2:
            out.append(float(row[1]))
        elif len(row) == 1:
            out.append(float(row[0]))
        else:
            out.append(0.0)
    return out

def brier_score(model, X, y):
    if not model or not X or not y:
        return 0.0
    p = predict_proba_1(model, X)
    total = min(len(p), len(y))
    if total <= 0:
        return 0.0
    s = 0.0
    for i in range(total):
        diff = float(p[i]) - float(y[i])
        s += diff * diff
    return s / total

def evaluate_model_accuracy(model, X, y, threshold=0.5):
    if not model or not X or not y:
        return 0, 0, 0.0

    try:
        preds = model.predict(X)
        preds = list(map(int, preds))
    except Exception:
        probs = model.predict_proba(X)
        preds = []
        for row in probs:
            if len(row) >= 2:
                p1 = row[1]
            elif len(row) == 1:
                p1 = row[0]
            else:
                p1 = 0.0
            preds.append(1 if p1 >= threshold else 0)

    correct = 0
    total = min(len(preds), len(y))
    for i in range(total):
        if int(preds[i]) == int(y[i]):
            correct += 1

    return correct, total, (correct / total if total > 0 else 0.0)

def train_time_split_backtest(bsp_dict, model_type="xgboost", test_ratio=0.2, val_ratio=0.2, threshold=0.5, calibrate_method="sigmoid", min_train=50, min_val=20, min_test=20, n_jobs=-1):
    empty_model = None
    empty_meta = []
    if not bsp_dict:
        return empty_model, empty_meta, {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "time_split",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": []
        }

    infos = list(bsp_dict.values())
    try:
        infos.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
    except Exception:
        pass

    n = len(infos)
    
    # CASE 1: Insufficient Data (< 10 samples)
    if n < 10:
        return empty_model, empty_meta, {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "insufficient_data",
            "train_count": n,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": []
        }

    # CASE 2: Small Data (10 <= n < 90) - Simple Train/Test Split
    if n < (min_train + min_val + min_test):
        feature_meta = build_feature_meta(bsp_dict)
        test_window = max(3, int(n * 0.2))
        fold_count = 3
        first_train_end = max(5, n - fold_count * test_window)

        total_correct = 0
        total_test = 0
        used_folds = 0
        for i in range(fold_count):
            train_end = first_train_end + i * test_window
            test_start = train_end
            test_end = min(train_end + test_window, n)
            if train_end <= 0 or test_start >= n or test_start >= test_end:
                continue

            train_infos = infos[:train_end]
            test_infos = infos[test_start:test_end]
            X_train, y_train = build_training_data_from_infos(train_infos, feature_meta)
            X_test, y_test = build_training_data_from_infos(test_infos, feature_meta)
            if len(set(y_train)) < 2 or len(set(y_test)) < 2:
                continue

            try:
                model = train_model_from_xy(X_train, y_train, model_type=model_type, n_jobs=n_jobs)
            except Exception:
                model = None

            valid_count, total_count, _ = evaluate_model_accuracy(model, X_test, y_test, threshold=threshold)
            total_correct += int(valid_count)
            total_test += int(total_count)
            used_folds += 1

        min_total_test = max(8, test_window * 2)
        if total_test < min_total_test or used_folds <= 0:
            return None, feature_meta, {
                "valid_count": 0,
                "total_count": total_test,
                "accuracy": 0.0,
                "method": "insufficient_data",
                "train_count": n,
                "val_count": 0,
                "test_count": total_test,
                "brier_score": 0.0,
                "ece": 0.0,
                "calibration_bins": []
            }

        try:
            X_all, y_all = build_training_data_from_infos(infos, feature_meta)
        except Exception:
            X_all, y_all = [], []
        final_model = None
        if X_all and len(set(y_all)) >= 2:
            try:
                final_model = train_model_from_xy(X_all, y_all, model_type=model_type, n_jobs=n_jobs)
            except Exception:
                final_model = None

        acc = (total_correct / total_test) if total_test > 0 else 0.0
        return final_model, feature_meta, {
            "valid_count": total_correct,
            "total_count": total_test,
            "accuracy": acc,
            "method": "walk_forward_small",
            "train_count": n,
            "val_count": 0,
            "test_count": total_test,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": []
        }

    train_end = int(n * (1 - val_ratio - test_ratio))
    train_end = max(min_train, min(train_end, n - (min_val + min_test)))
    val_end = int(n * (1 - test_ratio))
    val_end = max(train_end + min_val, min(val_end, n - min_test))

    train_infos = infos[:train_end]
    val_infos = infos[train_end:val_end]
    test_infos = infos[val_end:]

    feature_meta = build_feature_meta(bsp_dict)
    X_train, y_train = build_training_data_from_infos(train_infos, feature_meta)
    X_val, y_val = build_training_data_from_infos(val_infos, feature_meta)
    X_test, y_test = build_training_data_from_infos(test_infos, feature_meta)

    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return None, feature_meta, {
            "valid_count": 0,
            "total_count": len(y_test),
            "accuracy": 0.0,
            "method": "single_class_split",
            "train_count": len(X_train),
            "val_count": len(X_val),
            "test_count": len(X_test),
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": []
        }

    model = None
    try:
        model = train_model_from_xy(X_train, y_train, model_type=model_type, n_jobs=n_jobs)
    except Exception:
        model = None

    calibrated = False
    score_model = model
    used_calibrate_method = None
    if model and X_val and y_val and len(set(y_val)) >= 2:
        try:
            chosen_method = calibrate_method
            if chosen_method == "isotonic" and len(y_val) < 200:
                chosen_method = "sigmoid"
            try:
                cal = CalibratedClassifierCV(estimator=model, method=chosen_method, cv='prefit')
            except TypeError:
                cal = CalibratedClassifierCV(base_estimator=model, method=chosen_method, cv='prefit')
            cal.fit(X_val, y_val)
            score_model = cal
            calibrated = True
            used_calibrate_method = chosen_method
        except Exception:
            score_model = model
            calibrated = False
            used_calibrate_method = None

    valid_count, total_count, acc = evaluate_model_accuracy(score_model, X_test, y_test, threshold=threshold)

    bins = []
    ece = 0.0
    if score_model and X_test and y_test:
        probs = predict_proba_1(score_model, X_test)
        total = min(len(probs), len(y_test))
        if total > 0:
            bin_count = 10
            agg = [{"count": 0, "sum_p": 0.0, "sum_y": 0.0} for _ in range(bin_count)]
            for i in range(total):
                p = float(probs[i])
                yv = float(y_test[i])
                idx = int(p * bin_count)
                if idx >= bin_count:
                    idx = bin_count - 1
                if idx < 0:
                    idx = 0
                agg[idx]["count"] += 1
                agg[idx]["sum_p"] += p
                agg[idx]["sum_y"] += yv

            for i in range(bin_count):
                c = agg[i]["count"]
                low = i / bin_count
                high = (i + 1) / bin_count
                if c > 0:
                    avg_p = agg[i]["sum_p"] / c
                    win_rate = agg[i]["sum_y"] / c
                    ece += abs(avg_p - win_rate) * (c / total)
                else:
                    avg_p = 0.0
                    win_rate = 0.0
                bins.append({
                    "low": low,
                    "high": high,
                    "count": c,
                    "avg_pred": avg_p,
                    "win_rate": win_rate
                })

    return score_model, feature_meta, {
        "valid_count": valid_count,
        "total_count": total_count,
        "accuracy": acc,
        "method": f"time_split_calibrated_{used_calibrate_method}" if calibrated and used_calibrate_method else ("time_split_calibrated" if calibrated else "time_split_uncalibrated"),
        "train_count": len(X_train),
        "val_count": len(X_val),
        "test_count": len(X_test),
        "brier_score": brier_score(score_model, X_test, y_test),
        "ece": ece,
        "calibration_bins": bins
    }

def compute_time_split_accuracy(bsp_dict, model_type="xgboost", test_ratio=0.2, threshold=0.5, min_train=50, min_test=20):
    if not bsp_dict:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "time_split",
            "train_count": 0,
            "test_count": 0
        }

    _, _, info = train_time_split_backtest(
        bsp_dict,
        model_type=model_type,
        test_ratio=test_ratio,
        val_ratio=0.0,
        threshold=threshold,
        calibrate_method="isotonic",
        min_train=min_train,
        min_val=0,
        min_test=min_test
    )
    return info

def get_or_train_model(_bsp_dict, _chan, model_type="xgboost"):
    # Prepare data for XGBoost
    if not _bsp_dict:
        return None, None

    feature_meta = build_feature_meta(_bsp_dict)
    X_train, y_train, feature_meta = build_training_data(_bsp_dict, feature_meta)
        
    print(f"Training data size: {len(X_train)}")
    print(f"Class distribution: {set(y_train)}")
    
    if not X_train:
        print("Skipping training: No data.")
        return None, feature_meta
        
    if len(set(y_train)) < 2:
        print(f"Skipping training: Single class detected. Classes: {set(y_train)}")
        # Return a dummy model that always predicts this class
        return SingleClassModel(list(set(y_train))[0]), feature_meta
        
    print(f"Training model: {model_type}")
    
    try:
        model = train_model_from_xy(X_train, y_train, model_type=model_type)
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
        
        # print(f"Model raw probabilities: {probs}")

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

MODEL_CACHE_VERSION = 1

def _get_model_cache_dir():
    cache_dir = os.environ.get("MLCHAN_MODEL_CACHE_DIR")
    if cache_dir:
        return cache_dir
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "model_cache")

def _sanitize_model_key_part(s: str) -> str:
    if s is None:
        return "none"
    out = str(s).strip().lower()
    out = out.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
    return out

def build_pretrained_model_key(model_type: str, frequency: str, data_src: str) -> str:
    # Generate a unique key every time to allow multiple versions
    parts = [
        _sanitize_model_key_part(model_type),
        _sanitize_model_key_part(frequency),
        _sanitize_model_key_part(data_src),
        f"v{MODEL_CACHE_VERSION}",
        uuid.uuid4().hex,  # Add uniqueness
    ]
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def _pretrained_model_paths_by_key(key: str):
    cache_dir = _get_model_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    bundle_path = os.path.join(cache_dir, f"{key}.pkl.gz")
    meta_path = os.path.join(cache_dir, f"{key}.json")
    return bundle_path, meta_path

def save_pretrained_model_bundle(model, feature_meta, model_type: str, frequency: str, data_src: str, meta: dict, model_key: Optional[str] = None):
    if model_key is None:
        # Use the unified key builder which is now unique
        model_key = build_pretrained_model_key(model_type, frequency, data_src)
    trained_at = datetime.datetime.utcnow().isoformat() + "Z"
    bundle_path, meta_path = _pretrained_model_paths_by_key(model_key)

    bundle = {
        "version": MODEL_CACHE_VERSION,
        "key": model_key,
        "model_type": model_type,
        "frequency": frequency,
        "data_src": data_src,
        "trained_at": trained_at,
        "feature_meta": feature_meta,
        "model": model,
        "meta": meta or {},
    }
    with gzip.open(bundle_path, "wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    safe_meta = {
        "version": MODEL_CACHE_VERSION,
        "key": model_key,
        "model_type": model_type,
        "frequency": frequency,
        "data_src": data_src,
        "bundle_path": bundle_path,
        "trained_at": trained_at,
        "meta": meta or {},
        "feature_count": len(feature_meta or []),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(safe_meta, f, ensure_ascii=False, sort_keys=True, indent=2)
    return bundle_path, model_key, trained_at

def list_pretrained_model_metas(limit: Optional[int] = 200):
    cache_dir = _get_model_cache_dir()
    if not os.path.isdir(cache_dir):
        return []
    items = []
    try:
        for name in os.listdir(cache_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(cache_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    if "key" not in meta:
                        meta["key"] = os.path.splitext(os.path.basename(path))[0]
                    items.append(meta)
            except Exception:
                continue
    except Exception:
        return []

    def sort_key(m):
        ts = m.get("trained_at", "") or ""
        return ts

    items.sort(key=sort_key, reverse=True)
    if limit is None:
        return items
    return items[: int(limit)]

def get_pretrained_model_detail_by_key(key: str):
    if not key:
        return None
    key = str(key).strip()
    if len(key) != 32:
        return None
    for ch in key:
        if ch not in "0123456789abcdef":
            return None
    cache_dir = _get_model_cache_dir()
    meta_path = os.path.join(cache_dir, f"{key}.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            return None
        meta.setdefault("key", key)
        return meta
    except Exception:
        return None

def delete_pretrained_model_bundle_by_key(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        return {"status": "not_found", "key": str(key or "")}

    bundle_path, meta_path = _pretrained_model_paths_by_key(str(key).strip())
    removed_meta = False
    removed_bundle = False
    missing = []

    try:
        if os.path.exists(meta_path):
            os.remove(meta_path)
            removed_meta = True
        else:
            missing.append(meta_path)
    except Exception:
        pass

    try:
        bp = str(meta.get("bundle_path") or bundle_path)
        if bp and os.path.exists(bp):
            os.remove(bp)
            removed_bundle = True
        else:
            if bp:
                missing.append(bp)
    except Exception:
        pass

    if not removed_bundle:
        try:
            if os.path.exists(bundle_path):
                os.remove(bundle_path)
                removed_bundle = True
        except Exception:
            pass

    return {
        "status": "success",
        "key": str(key).strip(),
        "removed_meta": removed_meta,
        "removed_bundle": removed_bundle,
        "missing": missing,
    }

def load_pretrained_model_bundle(model_type: str, frequency: str, data_src: str):
    items = list_pretrained_model_metas(limit=None)
    mt = str(model_type or "")
    freq = str(frequency or "")
    src = str(data_src or "")
    candidates = []
    for m in items or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("model_type") or "") != mt:
            continue
        if str(m.get("frequency") or "") != freq:
            continue
        if str(m.get("data_src") or "") != src:
            continue
        if int(m.get("version") or 0) != int(MODEL_CACHE_VERSION):
            continue
        candidates.append(m)
    if not candidates:
        return None
    key = str(candidates[0].get("key") or "").strip()
    if not key:
        return None
    return load_pretrained_model_bundle_by_key(key)

def load_pretrained_model_bundle_by_key(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        return None
    bundle_path = meta.get("bundle_path")
    if not bundle_path or not os.path.exists(bundle_path):
        return None
    with gzip.open(bundle_path, "rb") as f:
        bundle = pickle.load(f)
    if not isinstance(bundle, dict):
        return None
    if bundle.get("version") != MODEL_CACHE_VERSION:
        return None
    if bundle.get("key") and str(bundle.get("key")) != str(key):
        return None
    return bundle

def evaluate_fixed_model_time_split(bsp_dict, model, feature_meta, test_ratio=0.2, threshold=0.5):
    if not bsp_dict or not model or not feature_meta:
        return {
            "valid_count": 0,
            "total_count": 0,
            "accuracy": 0.0,
            "method": "pretrained_fixed_empty",
            "train_count": 0,
            "val_count": 0,
            "test_count": 0,
            "brier_score": 0.0,
            "ece": 0.0,
            "calibration_bins": []
        }

    infos = list(bsp_dict.values())
    try:
        infos.sort(key=lambda i: getattr(i.get("open_time"), "ts", 0))
    except Exception:
        pass

    n = len(infos)
    if n <= 1:
        X_all, y_all = build_training_data_from_infos(infos, feature_meta)
        valid_count, total_count, acc = evaluate_model_accuracy(model, X_all, y_all, threshold=threshold)
        return {
            "valid_count": valid_count,
            "total_count": total_count,
            "accuracy": acc,
            "method": "pretrained_fixed_all_data",
            "train_count": 0,
            "val_count": 0,
            "test_count": len(X_all),
            "brier_score": brier_score(model, X_all, y_all),
            "ece": 0.0,
            "calibration_bins": []
        }

    split = int(n * (1 - test_ratio))
    split = max(1, min(split, n - 1))
    test_infos = infos[split:]

    X_test, y_test = build_training_data_from_infos(test_infos, feature_meta)
    valid_count, total_count, acc = evaluate_model_accuracy(model, X_test, y_test, threshold=threshold)

    bins = []
    ece = 0.0
    if X_test and y_test:
        probs = predict_proba_1(model, X_test)
        total = min(len(probs), len(y_test))
        if total > 0:
            bin_count = 10
            agg = [{"count": 0, "sum_p": 0.0, "sum_y": 0.0} for _ in range(bin_count)]
            for i in range(total):
                p = float(probs[i])
                yv = float(y_test[i])
                idx = int(p * bin_count)
                if idx >= bin_count:
                    idx = bin_count - 1
                if idx < 0:
                    idx = 0
                agg[idx]["count"] += 1
                agg[idx]["sum_p"] += p
                agg[idx]["sum_y"] += yv

            for i in range(bin_count):
                c = agg[i]["count"]
                low = i / bin_count
                high = (i + 1) / bin_count
                if c > 0:
                    avg_p = agg[i]["sum_p"] / c
                    win_rate = agg[i]["sum_y"] / c
                    ece += abs(avg_p - win_rate) * (c / total)
                else:
                    avg_p = 0.0
                    win_rate = 0.0
                bins.append({
                    "low": low,
                    "high": high,
                    "count": c,
                    "avg_pred": avg_p,
                    "win_rate": win_rate
                })

    return {
        "valid_count": valid_count,
        "total_count": total_count,
        "accuracy": acc,
        "method": "pretrained_fixed_time_split",
        "train_count": 0,
        "val_count": 0,
        "test_count": len(X_test),
        "brier_score": brier_score(model, X_test, y_test),
        "ece": ece,
        "calibration_bins": bins
    }

def _process_code_for_pretrain(args):
    code, level, begin_time, data_src_type = args
    code = normalize_code(code)
    try:
        kl_list = fetch_stock_data(code, level, begin_time, None, data_src_type)
        if not kl_list:
            return code, None, [], set()
            
        latest_time = str(kl_list[-1].time)
        
        _, bsp_dict, _, _ = get_chan_data(
            code=code,
            trigger_step=True,
            bi_strict=True,
            level=level,
            data_src_type=data_src_type,
            preloaded_data=kl_list,
            do_predict=True,
            begin_time=begin_time
        )
        
        local_samples = []
        feature_keys = set()
        
        if bsp_dict:
            for info in bsp_dict.values():
                bsp_obj = info.get("bsp_obj", None)
                if not bsp_obj or not getattr(bsp_obj, "klu", None):
                    continue
                cur_klu = bsp_obj.klu
                future_klu = cur_klu
                for _ in range(5):
                    nxt = getattr(future_klu, "next", None)
                    if nxt:
                        future_klu = nxt
                    else:
                        break
                try:
                    cur_close = float(cur_klu.close)
                    future_close = float(future_klu.close)
                except Exception:
                    continue
                if not cur_close:
                    continue

                is_buy = bool(info.get("is_buy", False))
                profit = (future_close - cur_close) / cur_close if is_buy else (cur_close - future_close) / cur_close
                label = 1 if profit > 0 else 0

                feat = info.get("feature", None)
                try:
                    feat_dict = dict(feat) if feat is not None else {}
                except Exception:
                    feat_dict = {}
                
                if feat_dict:
                    for k in feat_dict.keys():
                        feature_keys.add(k)

                open_ts = 0
                try:
                    open_ts = int(getattr(info.get("open_time", None), "ts", 0) or 0)
                except Exception:
                    open_ts = 0
                local_samples.append({"open_ts": open_ts, "feature": feat_dict, "label": label})
        
        return code, latest_time, local_samples, feature_keys
    except Exception as e:
        print(f"Error processing {code}: {e}")
        return code, None, [], set()

def pretrain_and_persist_model(codes, level, data_src_type, begin_time, model_type="xgboost", frequency="1d", data_src="clickhouse", calibrate_method="isotonic", progress_cb=None, pool_name=None):
    if not codes:
        return None, {
            "status": "error",
            "detail": "codes is required"
        }

    def report(stage: str, progress: float, message=None, extra=None):
        if not progress_cb:
            return
        payload = {"stage": stage, "progress": float(progress)}
        if message is not None:
            payload["message"] = str(message)
        if extra:
            payload.update(extra)
        try:
            progress_cb(payload)
        except Exception:
            pass

    per_code_latest_time = {}
    per_code_sample_count = {}
    samples = []
    feature_keys = set()

    total = max(len(codes), 1)
    report("collecting", 0.0, "start")

    try:
        # Optimized for server environment: Use ProcessPoolExecutor for parallel data processing
        # This utilizes the multiple cores (e.g. 64 cores) efficiently
        # Logic: Env Var -> min(60, CPU) -> bound by len(codes)
        
        default_workers = min(60, os.cpu_count() or 4)
        env_workers = os.environ.get("MLCHAN_PRETRAIN_WORKERS")
        if env_workers:
            try:
                max_workers = int(env_workers)
            except ValueError:
                max_workers = default_workers
        else:
            max_workers = default_workers
            
        # Ensure at least 1 and not more than tasks
        max_workers = max(1, min(max_workers, len(codes)))
        
        print(f"Starting pretrain data collection with {max_workers} workers for {len(codes)} codes.")
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            tasks = [(code, level, begin_time, data_src_type) for code in codes]
            future_to_code = {executor.submit(_process_code_for_pretrain, t): t[0] for t in tasks}
            
            completed_count = 0
            for future in concurrent.futures.as_completed(future_to_code):
                completed_count += 1
                orig_code = future_to_code[future]
                report("collecting", min(0.8, 0.05 + 0.75 * (completed_count / total)), f"{completed_count}/{total} {orig_code}")
                
                try:
                    r_code, r_latest, r_samples, r_keys = future.result()
                    if r_latest:
                        per_code_latest_time[r_code] = r_latest
                    if r_samples:
                        samples.extend(r_samples)
                        per_code_sample_count[r_code] = len(r_samples)
                    if r_keys:
                        feature_keys.update(r_keys)
                except Exception as e:
                    print(f"Worker exception for {orig_code}: {e}")

        if not samples:
            return None, {
                "status": "error",
                "detail": "no training samples collected"
            }

        report("training", 0.85, "training")

        feature_meta = sorted(feature_keys)
        samples.sort(key=lambda s: int(s.get("open_ts", 0) or 0))

        def build_xy(rows):
            X = []
            y = []
            for r in rows:
                feat = r.get("feature", {}) or {}
                X.append([feat.get(k, -9999999) for k in feature_meta])
                y.append(int(r.get("label", 0) or 0))
            return X, y

        n = len(samples)
        test_ratio = 0.2
        val_ratio = 0.2
        threshold = 0.5
        min_train = 50
        min_val = 20
        min_test = 20

        if n < (min_train + min_val + min_test):
            X_all, y_all = build_xy(samples)
            model = None
            try:
                model = train_model_from_xy(X_all, y_all, model_type=model_type)
            except Exception:
                model = None
            valid_count, total_count, acc = evaluate_model_accuracy(model, X_all, y_all, threshold=threshold)
            accuracy_info = {
                "valid_count": valid_count,
                "total_count": total_count,
                "accuracy": acc,
                "method": "fallback_all_data",
                "train_count": len(X_all),
                "val_count": 0,
                "test_count": 0,
                "brier_score": brier_score(model, X_all, y_all),
                "ece": 0.0,
                "calibration_bins": []
            }
        else:
            train_end = int(n * (1 - val_ratio - test_ratio))
            train_end = max(min_train, min(train_end, n - (min_val + min_test)))
            val_end = int(n * (1 - test_ratio))
            val_end = max(train_end + min_val, min(val_end, n - min_test))

            train_rows = samples[:train_end]
            val_rows = samples[train_end:val_end]
            test_rows = samples[val_end:]

            X_train, y_train = build_xy(train_rows)
            X_val, y_val = build_xy(val_rows)
            X_test, y_test = build_xy(test_rows)

            model = None
            try:
                model = train_model_from_xy(X_train, y_train, model_type=model_type)
            except Exception:
                model = None

            calibrated = False
            score_model = model
            used_calibrate_method = None
            if model and not isinstance(model, SingleClassModel) and X_val and y_val and len(set(y_val)) >= 2:
                try:
                    chosen_method = calibrate_method
                    if chosen_method == "isotonic" and len(y_val) < 200:
                        chosen_method = "sigmoid"
                    try:
                        cal = CalibratedClassifierCV(estimator=model, method=chosen_method, cv='prefit')
                    except TypeError:
                        cal = CalibratedClassifierCV(base_estimator=model, method=chosen_method, cv='prefit')
                    cal.fit(X_val, y_val)
                    score_model = cal
                    calibrated = True
                    used_calibrate_method = chosen_method
                except Exception:
                    score_model = model
                    calibrated = False
                    used_calibrate_method = None

            valid_count, total_count, acc = evaluate_model_accuracy(score_model, X_test, y_test, threshold=threshold)

            bins = []
            ece = 0.0
            if score_model and X_test and y_test:
                probs = predict_proba_1(score_model, X_test)
                total_p = min(len(probs), len(y_test))
                if total_p > 0:
                    bin_count = 10
                    agg = [{"count": 0, "sum_p": 0.0, "sum_y": 0.0} for _ in range(bin_count)]
                    for i in range(total_p):
                        p = float(probs[i])
                        yv = float(y_test[i])
                        bi = int(p * bin_count)
                        if bi >= bin_count:
                            bi = bin_count - 1
                        if bi < 0:
                            bi = 0
                        agg[bi]["count"] += 1
                        agg[bi]["sum_p"] += p
                        agg[bi]["sum_y"] += yv
                    for i in range(bin_count):
                        c = agg[i]["count"]
                        low = i / bin_count
                        high = (i + 1) / bin_count
                        if c > 0:
                            avg_p = agg[i]["sum_p"] / c
                            win_rate = agg[i]["sum_y"] / c
                            ece += abs(avg_p - win_rate) * (c / total_p)
                        else:
                            avg_p = 0.0
                            win_rate = 0.0
                        bins.append({
                            "low": low,
                            "high": high,
                            "count": c,
                            "avg_pred": avg_p,
                            "win_rate": win_rate
                        })

            accuracy_info = {
                "valid_count": valid_count,
                "total_count": total_count,
                "accuracy": acc,
                "method": f"time_split_calibrated_{used_calibrate_method}" if calibrated and used_calibrate_method else ("time_split_calibrated" if calibrated else "time_split_uncalibrated"),
                "train_count": len(X_train),
                "val_count": len(X_val),
                "test_count": len(X_test),
                "brier_score": brier_score(score_model, X_test, y_test),
                "ece": ece,
                "calibration_bins": bins
            }

            model = score_model

        if not model:
            return None, {
                "status": "error",
                "detail": "model training failed"
            }

        meta = {
            "accuracy": accuracy_info,
            "begin_time": begin_time,
            "codes": list(per_code_latest_time.keys()),
            "per_code_latest_time": per_code_latest_time,
            "per_code_sample_count": per_code_sample_count,
            "sample_count": len(samples),
            "pool_name": pool_name
        }
        report("saving", 0.95, "saving")
        path, model_key, trained_at = save_pretrained_model_bundle(
            model=model,
            feature_meta=feature_meta,
            model_type=model_type,
            frequency=frequency,
            data_src=data_src,
            meta=meta
        )
        report("done", 1.0, "done")
        return path, {
            "status": "success",
            "bundle_path": path,
            "model_key": model_key,
            "model_type": model_type,
            "frequency": frequency,
            "data_src": data_src,
            "feature_count": len(feature_meta),
            "sample_count": len(samples),
            "accuracy": accuracy_info,
            "codes": meta["codes"],
            "per_code_sample_count": per_code_sample_count,
            "trained_at": trained_at,
        }
    finally:
        try:
            samples.clear()
        except Exception:
            pass
        try:
            feature_keys.clear()
        except Exception:
            pass
        gc.collect()
