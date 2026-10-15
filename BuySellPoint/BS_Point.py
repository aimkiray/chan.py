from typing import Dict, Generic, List, Optional, TypeVar, Union

from Bi.Bi import CBi
from ChanModel.Features import CFeatures
from Common.CEnum import BSP_TYPE
from Seg.Seg import CSeg

LINE_TYPE = TypeVar('LINE_TYPE', CBi, CSeg)


class CBS_Point(Generic[LINE_TYPE]):
    def __init__(self, bi: LINE_TYPE, is_buy, bs_type: BSP_TYPE, relate_bsp1: Optional['CBS_Point'], feature_dict=None):
        self.bi: LINE_TYPE = bi
        self.klu = bi.get_end_klu()
        self.is_buy = is_buy
        self.type: List[BSP_TYPE] = [bs_type]
        self.relate_bsp1 = relate_bsp1

        # 构造即把自身挂到 bi.bsp 上（副作用）：非 target 的买卖点由调用方
        # BSPointList.add_bs 在判定后负责回退置 None，bi.bsp 不变量靠该约定维持
        self.bi.bsp = self  # type: ignore
        self.features = CFeatures(feature_dict)

        self.init_common_feature()

    def add_type(self, bs_type: BSP_TYPE):
        self.type.append(bs_type)

    def type2str(self):
        return ",".join([x.value for x in self.type])

    def add_another_bsp_prop(self, bs_type: BSP_TYPE, relate_bsp1):
        self.add_type(bs_type)
        if self.relate_bsp1 is None:
            self.relate_bsp1 = relate_bsp1
        elif relate_bsp1 is not None and self.relate_bsp1.klu.idx != relate_bsp1.klu.idx:
            # 同一笔可能同时是 seg_k 的 T2 和 seg_{k-1} 的 T3A/T3B，各自跟随
            # 不同的一买：relate 不一致属合法场景，保留最早登记的一买为准，
            # 不再 assert 崩溃（python -O 下也避免静默接受不一致）
            pass

    def add_feat(self, inp1: Union[str, Dict[str, float], Dict[str, Optional[float]], 'CFeatures'], inp2: Optional[float] = None):
        self.features.add_feat(inp1, inp2)

    def init_common_feature(self):
        # 用于配置适用所有买卖点的特征
        self.add_feat({
            'bsp_bi_amp': self.bi.amp(),
        })
