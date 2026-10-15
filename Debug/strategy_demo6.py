import json
import os
import sys
from pathlib import Path
from typing import Dict, TypedDict

import xgboost as xgb

# Debug/没有__init__.py，直接 python Debug/strategy_demo6.py 运行时
# sys.path只含本目录，需显式把仓库根加入才能导入Chan等核心模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from BuySellPoint.BS_Point import CBS_Point
from Chan import CChan
from ChanConfig import CChanConfig
from ChanModel.Features import CFeatures
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Common.CTime import CTime


# 训练产物目录，与strategy_demo5.OUTPUT_DIR保持一致（Debug/没有__init__.py，
# 不能包内导入；复制此约定避免依赖sys.path/脚本所在目录）
OUTPUT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp_data_cache", "strategy_demo5"))


class T_SAMPLE_INFO(TypedDict):
    feature: CFeatures
    is_buy: bool
    open_time: CTime


def stragety_feature(last_klu):
    # 与strategy_demo5.stragety_feature保持一致，改动需两边同步
    return {
        "open_klu_rate": (last_klu.close - last_klu.open)/last_klu.open,
    }


def predict_bsp(model: xgb.Booster, last_bsp: CBS_Point, meta: Dict[str, int]):
    missing = -9999999
    feature_arr = [missing] * len(meta)
    for feat_name, feat_value in last_bsp.features.items():
        if feat_name in meta:
            feature_arr[meta[feat_name]] = feat_value
    feature_arr = [feature_arr]
    dtest = xgb.DMatrix(feature_arr, missing=missing)
    return model.predict(dtest)


if __name__ == "__main__":
    """
    本demo主要演示如何在实盘中把策略产出的买卖点，对接到demo5中训练好的离线模型上
    """
    code = "sz.000001"
    begin_time = "2018-01-01"
    end_time = None
    data_src = DATA_SRC.BAO_STOCK
    lv_list = [KL_TYPE.K_DAY]

    # 配置必须与strategy_demo5完全一致，否则BSP集合不同、打分口径漂移，
    # "与demo5最后的predict结果完全一致"将不再成立
    config = CChanConfig({
        "trigger_step": True,  # 打开开关！
        "bi_strict": True,
        "skip_step": 0,
        "divergence_rate": float("inf"),
        "bsp2_follow_1": False,
        "bsp3_follow_1": False,
        "min_zs_cnt": 0,
        "bs1_peak": False,
        "macd_algo": "peak",
        "bs_type": '1,2,3a,1p,2s,3b',
        "print_warning": True,
        "zs_algo": "normal",
    })

    # demo5训练产物前置检查：缺失时给出明确引导，而非裸的XGBoostError/FileNotFoundError
    model_path = os.path.join(OUTPUT_DIR, "model.json")
    meta_path = os.path.join(OUTPUT_DIR, "feature.meta")
    missing_files = [p for p in (model_path, meta_path) if not os.path.exists(p)]
    if missing_files:
        sys.exit(f"缺少demo5训练产物: {missing_files}\n请先运行 Debug/strategy_demo5.py 生成后再运行本脚本")

    model = xgb.Booster()
    model.load_model(model_path)
    with open(meta_path, "r") as fid:
        meta = json.load(fid)

    chan = CChan(
        code=code,
        begin_time=begin_time,
        end_time=end_time,
        data_src=data_src,
        lv_list=lv_list,
        config=config,
        autype=AUTYPE.QFQ,
    )

    treated_bsp_idx = set()
    for chan_snapshot in chan.step_load():
        # 策略逻辑要对齐demo5
        last_klu = chan_snapshot[0][-1][-1]
        bsp_list = chan_snapshot.get_latest_bsp()
        if not bsp_list:
            continue
        last_bsp = bsp_list[0]

        cur_lv_chan = chan_snapshot[0]
        if last_bsp.klu.idx in treated_bsp_idx or cur_lv_chan[-2].idx != last_bsp.klu.klc.idx:
            continue

        last_bsp.features.add_feat(stragety_feature(last_klu))  # 开仓K线特征
        # 买卖点打分，应该和demo5最后的predict结果完全一致才对
        print(last_bsp.klu.time, predict_bsp(model, last_bsp, meta))
        treated_bsp_idx.add(last_bsp.klu.idx)
