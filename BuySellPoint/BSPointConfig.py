from typing import Dict, List, Optional

from Common.CEnum import BSP_TYPE, MACD_ALGO
from Common.ChanException import CChanException, ErrCode


class CBSPointConfig:
    def __init__(self, **args):
        self.b_conf = CPointConfig(**args)
        self.s_conf = CPointConfig(**args)

    def GetBSConfig(self, is_buy):
        return self.b_conf if is_buy else self.s_conf


class CPointConfig:
    # attribute names declared in __init__; only these may be overridden via set()
    ALLOWED_FIELDS = frozenset([
        "divergence_rate",
        "multi_bi_divergence",
        "min_zs_cnt",
        "bsp1_only_multibi_zs",
        "max_bs2_rate",
        "macd_algo",
        "bs1_peak",
        "tmp_target_types",
        "target_types",
        "bsp2_follow_1",
        "bsp3_follow_1",
        "bsp3_peak",
        "bsp2s_follow_2",
        "max_bsp2s_lv",
        "strict_bsp3",
        "bsp3a_max_zs_cnt",
    ])

    def __init__(self,
                 divergence_rate,
                 multi_bi_divergence,
                 min_zs_cnt,
                 bsp1_only_multibi_zs,
                 max_bs2_rate,
                 macd_algo,
                 bs1_peak,
                 bs_type,
                 bsp2_follow_1,
                 bsp3_follow_1,
                 bsp3_peak,
                 bsp2s_follow_2,
                 max_bsp2s_lv,
                 strict_bsp3,
                 bsp3a_max_zs_cnt,
                 ):
        self.divergence_rate = divergence_rate
        self.multi_bi_divergence = multi_bi_divergence
        self.min_zs_cnt = min_zs_cnt
        self.bsp1_only_multibi_zs = bsp1_only_multibi_zs
        self.max_bs2_rate = max_bs2_rate
        self.check_max_bs2_rate()
        self.SetMacdAlgo(macd_algo)
        self.bs1_peak = bs1_peak
        self.tmp_target_types = bs_type
        self.target_types: List[BSP_TYPE] = []
        self.bsp2_follow_1 = bsp2_follow_1
        self.bsp3_follow_1 = bsp3_follow_1
        self.bsp3_peak = bsp3_peak
        self.bsp2s_follow_2 = bsp2s_follow_2
        self.max_bsp2s_lv: Optional[int] = max_bsp2s_lv
        self.strict_bsp3 = strict_bsp3
        self.bsp3a_max_zs_cnt = bsp3a_max_zs_cnt
        self.check_bsp3a_max_zs_cnt()

    def check_max_bs2_rate(self):
        # 配置校验不用 assert（python -O 下会被剥离）
        if not isinstance(self.max_bs2_rate, (int, float)) or self.max_bs2_rate > 1:
            raise CChanException(f"invalid max_bs2_rate={self.max_bs2_rate}, should be <= 1", ErrCode.PARA_ERROR)

    def check_bsp3a_max_zs_cnt(self):
        if not isinstance(self.bsp3a_max_zs_cnt, int) or isinstance(self.bsp3a_max_zs_cnt, bool) or self.bsp3a_max_zs_cnt < 1:
            raise CChanException(f"invalid bsp3a_max_zs_cnt={self.bsp3a_max_zs_cnt}, should be int >= 1", ErrCode.PARA_ERROR)

    def parse_target_type(self):
        _d: Dict[str, BSP_TYPE] = {x.value: x for x in BSP_TYPE}
        if isinstance(self.tmp_target_types, str):
            self.tmp_target_types = [t.strip() for t in self.tmp_target_types.split(",") if t.strip()]
        for target_t in self.tmp_target_types:
            if target_t not in _d:
                raise CChanException(
                    f"invalid bs target type={target_t}, should be one of {sorted(_d)}",
                    ErrCode.PARA_ERROR)
        self.target_types = [_d[_type] for _type in self.tmp_target_types]

    def SetMacdAlgo(self, macd_algo):
        _d = {
            "area": MACD_ALGO.AREA,
            "peak": MACD_ALGO.PEAK,
            "full_area": MACD_ALGO.FULL_AREA,
            "diff": MACD_ALGO.DIFF,
            "slope": MACD_ALGO.SLOPE,
            "amp": MACD_ALGO.AMP,
            "amount": MACD_ALGO.AMOUNT,
            "volumn": MACD_ALGO.VOLUMN,
            "amount_avg": MACD_ALGO.AMOUNT_AVG,
            "volumn_avg": MACD_ALGO.VOLUMN_AVG,
            "turnrate_avg": MACD_ALGO.TURNRATE_AVG,
            "rsi": MACD_ALGO.RSI,
        }
        # 也接受 MACD_ALGO 枚举本身；非法值显式报 PARA_ERROR 而非裸 KeyError
        if isinstance(macd_algo, MACD_ALGO):
            self.macd_algo = macd_algo
            return
        if macd_algo not in _d:
            raise CChanException(f"unknown macd_algo={macd_algo}, should be one of {sorted(_d)}", ErrCode.PARA_ERROR)
        self.macd_algo = _d[macd_algo]

    def set_target_types(self, v):
        # "bs_type"（构造参数名）/"tmp_target_types"/"target_types" 三种写法统一落到
        # tmp_target_types 上并即时刷新 target_types，避免直接赋 target_types 被
        # 后续 parse_target_type 覆盖而静默失效；接受 "1,2s" 逗号串、str 列表或枚举列表
        _d: Dict[str, BSP_TYPE] = {x.value: x for x in BSP_TYPE}
        if isinstance(v, str):
            v = [t.strip() for t in v.split(",") if t.strip()]
        if not isinstance(v, (list, tuple)):
            raise CChanException(f"invalid bs_type value: {v!r}", ErrCode.PARA_ERROR)
        norm = []
        for t in v:
            key = t.value if isinstance(t, BSP_TYPE) else t
            if key not in _d:
                raise CChanException(f"invalid bs_type item={t!r}, should be one of {sorted(_d)}", ErrCode.PARA_ERROR)
            norm.append(key)
        self.tmp_target_types = norm
        self.target_types = [_d[t] for t in norm]

    def set(self, k, v):
        if k == "macd_algo":
            self.SetMacdAlgo(v)
            return
        if k in ("bs_type", "tmp_target_types", "target_types"):
            self.set_target_types(v)
            return
        if k not in self.ALLOWED_FIELDS:
            raise CChanException(f"invalid bsp config para = {k}", ErrCode.PARA_ERROR)
        if not isinstance(v, (int, float, str, bool, list, tuple, type(None))):
            raise CChanException(f"invalid bsp config value for {k}: {type(v)}", ErrCode.PARA_ERROR)
        # set() 与构造校验口径一致，不允许绕过范围检查（先校验再赋值，拒绝值不留存）
        if k == "max_bs2_rate" and (not isinstance(v, (int, float)) or v > 1):
            raise CChanException(f"invalid max_bs2_rate={v}, should be <= 1", ErrCode.PARA_ERROR)
        if k == "bsp3a_max_zs_cnt" and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
            raise CChanException(f"invalid bsp3a_max_zs_cnt={v}, should be int >= 1", ErrCode.PARA_ERROR)
        setattr(self, k, v)
