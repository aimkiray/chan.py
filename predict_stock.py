"""
Standalone demo: 在内存中完成"买卖点特征采样 -> XGBoost训练 -> 最新信号打分"全流程。

- 不依赖任何外部产物（不读写 model.json/feature.meta 等文件）
- label 为"该BSP终态仍在买卖点列表中"，属事后标注，仅作流程演示，不构成真实预测

用法:
    python predict_stock.py                        # 默认分析 sz.002701 (ORG Technology)
    python predict_stock.py 601888                 # 裸代码自动补市场前缀 -> sh.601888
    python predict_stock.py sh.601888              # 显式指定带市场前缀的代码
    python predict_stock.py sh.601888 "中国中免"    # 附带显示名称
"""
import sys
from typing import Dict, TypedDict

import xgboost as xgb

from Chan import CChan
from ChanConfig import CChanConfig
from ChanModel.Features import CFeatures
from Common.CEnum import AUTYPE, DATA_FIELD, DATA_SRC, KL_TYPE
from Common.CTime import CTime
from BuySellPoint.BS_Point import CBS_Point


class T_SAMPLE_INFO(TypedDict):
    feature: CFeatures
    is_buy: bool
    open_time: CTime
    bsp_obj: CBS_Point


def stragety_feature(last_klu):
    # Simple features for demonstration
    # In a real scenario, you would add more technical indicators here
    volume = last_klu.trade_info.metric.get(DATA_FIELD.FIELD_VOLUME)
    return {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
        "high_low_rate": (last_klu.high - last_klu.low)/last_klu.close,
        "close": last_klu.close,
        # CKLine_Unit没有volume属性，成交量在trade_info.metric里；
        # 数据源不提供成交量时（如部分指数）退化为0.0
        "volume": float(volume) if volume is not None else 0.0,
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


def normalize_code(code: str) -> str:
    """裸6位代码自动补市场前缀；已带sh./sz./bj.前缀的输入原样返回"""
    code = code.strip().lower()
    if code.startswith(("sh.", "sz.", "bj.")):
        return code
    if code.startswith("6") or code.startswith("9"):
        return "sh." + code
    if code.startswith("4") or code.startswith("8"):
        # 北交所代码（数据源无bj数据时会明确报错，好过静默错挂sz）
        return "bj." + code
    return "sz." + code


def get_confirm_klu(latest_bsp: CBS_Point, fallback_klu):
    """取BSP分型确认时刻的开仓K线，与训练阶段口径一致：
    训练时在"BSP所在KLC成为倒数第二根"那一帧取最新KLU，即确认KLC的首根KLU。
    若BSP所在KLC还没有下一根KLC（信号太新未经确认），退化为最新KLU。
    """
    confirm_klc = latest_bsp.klu.klc.next
    if confirm_klc is not None and len(confirm_klc) > 0:
        return confirm_klc[0]
    return fallback_klu


def run_analysis(code="sz.002701", name="ORG Technology"):
    print(f"Initializing Chan Theory Analysis for {name} ({code})...")

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

    # Store the very last snapshot to analyze current status
    last_snapshot = None

    for chan_snapshot in chan.step_load():
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
                "bsp_obj": last_bsp,  # Keep object for later reference if needed
            }
            # Add custom strategy features（开仓K线=确认KLC首根KLU，见get_confirm_klu）
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
            print(f"Last Signal Date: {latest_bsp.klu.time} ({days_diff:.1f} days ago)")
            print(f"Signal: {'BUY' if latest_bsp.is_buy else 'SELL'} (Type: {latest_bsp.type2str()})")

            # Predict validity
            # 特征口径必须与训练阶段一致：若该BSP已在循环中入样，其features已含
            # 确认时刻的策略特征，add_feat会覆写成错误时刻的值，必须跳过；
            # 否则按确认KLC首根KLU重算（见get_confirm_klu）。
            if latest_bsp.klu.idx not in bsp_dict:
                confirm_klu = get_confirm_klu(latest_bsp, last_klu)
                if confirm_klu is last_klu:
                    print("(信号尚未被后续K线确认，开仓K线特征以最新K线近似)")
                latest_bsp.features.add_feat(stragety_feature(confirm_klu))

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
    if len(sys.argv) > 3:
        sys.exit(__doc__)
    if len(sys.argv) > 1:
        code_arg = normalize_code(sys.argv[1])
        name_arg = sys.argv[2] if len(sys.argv) > 2 else "Stock " + code_arg
    else:
        code_arg, name_arg = "sz.002701", "ORG Technology"
    run_analysis(code=code_arg, name=name_arg)
