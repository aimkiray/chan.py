from __future__ import annotations

from typing import TYPE_CHECKING, Generic, List, Optional, Self, TypeVar

from Bi.Bi import CBi
from Common.CEnum import BI_DIR, MACD_ALGO, TREND_LINE_SIDE
from Common.ChanException import CChanException, ErrCode
from KLine.KLine_Unit import CKLine_Unit
from Math.TrendLine import CTrendLine

from .EigenFX import CEigenFX

if TYPE_CHECKING:
    # Bi/Seg 与 ZS、BuySellPoint 存在循环依赖（ZS 顶层 import CSeg、
    # BS_Point 顶层 import CSeg），类型注解一律走 TYPE_CHECKING
    from BuySellPoint.BS_Point import CBS_Point
    from ZS.ZS import CZS

LINE_TYPE = TypeVar('LINE_TYPE', CBi, "CSeg")


class CSeg(Generic[LINE_TYPE]):
    def __init__(self, idx: int, start_bi: LINE_TYPE, end_bi: LINE_TYPE, is_sure=True, seg_dir=None, reason="normal"):
        # 业务不变量不用 assert（python -O 下会被剥离）；
        # 不能用 SEG_END_VALUE_ERR：add_new_seg 会把该错误码吞掉并返回 False
        if is_sure and start_bi.idx != 0 and start_bi.dir != end_bi.dir:
            raise CChanException(
                f"确定线段的首尾笔方向必须一致! {start_bi.idx}({start_bi.dir}) {end_bi.idx}({end_bi.dir}) {is_sure}",
                ErrCode.COMMON_ERROR)
        self.idx = idx
        self.start_bi = start_bi
        self.end_bi = end_bi
        self.is_sure = is_sure
        self.used_to_be_sure = is_sure
        self.dir = end_bi.dir if seg_dir is None else seg_dir

        self.zs_lst: List[CZS[LINE_TYPE]] = []

        self.eigen_fx: Optional[CEigenFX] = None
        self.seg_idx = None  # 线段的线段用
        self.parent_seg: Optional[CSeg] = None  # 在哪个线段里面
        self.pre: Optional[Self] = None
        self.next: Optional[Self] = None

        self.bsp: Optional[CBS_Point] = None  # 尾部是不是买卖点

        self.bi_list: List[LINE_TYPE] = []  # 仅通过self.update_bi_list来更新
        self.reason = reason
        self.support_trend_line = None
        self.resistance_trend_line = None
        if end_bi.idx - start_bi.idx < 2:
            self.is_sure = False
            # 降级为不确定的短线段从未真正"确定"过，证据链（segseg 的
            # all_bi_is_sure）里不应再把它算作曾经的确定段
            self.used_to_be_sure = False
        self.check()

        self.ele_inside_is_sure = False

    def set_seg_idx(self, idx):
        self.seg_idx = idx

    def check(self):
        if not self.is_sure:
            return
        if self.is_down():
            if self.start_bi.get_begin_val() < self.end_bi.get_end_val():
                raise CChanException(f"下降线段起始点应该高于结束点! idx={self.idx}", ErrCode.SEG_END_VALUE_ERR)
        elif self.start_bi.get_begin_val() > self.end_bi.get_end_val():
            raise CChanException(f"上升线段起始点应该低于结束点! idx={self.idx}", ErrCode.SEG_END_VALUE_ERR)
        # 注：构造期已将 end_bi.idx - start_bi.idx < 2 的线段降级为不确定，
        # 走到这里的 is_sure=True 线段跨度必然 >=2，原 SEG_LEN_ERR 分支不可达故移除

    def __str__(self):
        return f"{self.start_bi.idx}->{self.end_bi.idx}: {self.dir}  {self.is_sure}"

    def add_zs(self, zs):
        self.zs_lst = [zs] + self.zs_lst  # 因为中枢是反序加入的

    def cal_klu_slope(self):
        assert self.end_bi.idx >= self.start_bi.idx
        klu_span = self.get_end_klu().idx - self.get_begin_klu().idx
        begin_val = self.get_begin_val()
        if klu_span == 0 or begin_val == 0:
            return 0.0  # 退化输入（单klu段/零价），斜率无意义时返回0而非抛除零
        return (self.get_end_val()-begin_val)/klu_span/begin_val

    def cal_amp(self):
        begin_val = self.get_begin_val()
        if begin_val == 0:
            return 0.0
        return (self.get_end_val()-begin_val)/begin_val

    def cal_bi_cnt(self):
        return self.end_bi.idx-self.start_bi.idx+1

    def clear_zs_lst(self):
        self.zs_lst = []

    def _low(self):
        return self.end_bi.get_end_klu().low if self.is_down() else self.start_bi.get_begin_klu().low

    def _high(self):
        return self.end_bi.get_end_klu().high if self.is_up() else self.start_bi.get_begin_klu().high

    def is_down(self):
        return self.dir == BI_DIR.DOWN

    def is_up(self):
        return self.dir == BI_DIR.UP

    def get_end_val(self):
        return self.end_bi.get_end_val()

    def get_begin_val(self):
        return self.start_bi.get_begin_val()

    @property
    def is_used_to_be_sure(self) -> bool:
        return self.is_sure or self.used_to_be_sure

    def amp(self):
        return abs(self.get_end_val() - self.get_begin_val())

    def get_end_klu(self) -> CKLine_Unit:
        return self.end_bi.get_end_klu()

    def get_begin_klu(self) -> CKLine_Unit:
        return self.start_bi.get_begin_klu()

    def get_klu_cnt(self):
        return self.get_end_klu().idx - self.get_begin_klu().idx + 1

    def cal_macd_metric(self, macd_algo, is_reverse):
        if macd_algo == MACD_ALGO.SLOPE:
            return self.Cal_MACD_slope()
        elif macd_algo == MACD_ALGO.AMP:
            return self.Cal_MACD_amp()
        else:
            raise CChanException(f"unsupport macd_algo={macd_algo} of Seg, should be one of slope/amp", ErrCode.PARA_ERROR)

    def Cal_MACD_slope(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        klu_cnt = end_klu.idx - begin_klu.idx + 1
        if self.is_up():
            if end_klu.high == 0 or klu_cnt == 0:
                return 0.0
            return (end_klu.high - begin_klu.low)/end_klu.high/klu_cnt
        else:
            if begin_klu.high == 0 or klu_cnt == 0:
                return 0.0
            return (begin_klu.high - end_klu.low)/begin_klu.high/klu_cnt

    def Cal_MACD_amp(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        if self.is_down():
            if begin_klu.high == 0:
                return 0.0
            return (begin_klu.high-end_klu.low)/begin_klu.high
        else:
            if begin_klu.low == 0:
                return 0.0
            return (end_klu.high-begin_klu.low)/begin_klu.low

    def update_bi_list(self, bi_lst, idx1, idx2):
        for bi_idx in range(idx1, idx2+1):
            bi_lst[bi_idx].parent_seg = self
            self.bi_list.append(bi_lst[bi_idx])
        if len(self.bi_list) >= 3:
            self.support_trend_line = CTrendLine(self.bi_list, TREND_LINE_SIDE.INSIDE)
            self.resistance_trend_line = CTrendLine(self.bi_list, TREND_LINE_SIDE.OUTSIDE)

    def get_first_multi_bi_zs(self):
        return next((zs for zs in self.zs_lst if not zs.is_one_bi_zs()), None)

    def get_multi_bi_zs_lst(self):
        return [zs for zs in self.zs_lst if not zs.is_one_bi_zs()]

    def get_final_multi_bi_zs(self):
        zs_idx = len(self.zs_lst) - 1
        while zs_idx >= 0:
            zs = self.zs_lst[zs_idx]
            if not zs.is_one_bi_zs():
                return zs
            zs_idx -= 1
        return None

    def get_multi_bi_zs_cnt(self):
        return sum(not zs.is_one_bi_zs() for zs in self.zs_lst)
