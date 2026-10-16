from __future__ import annotations

from typing import TYPE_CHECKING, Generic, List, Optional, TypeVar

from Bi.Bi import CBi
from Common.ChanException import CChanException, ErrCode
from Common.func_util import has_overlap
from KLine.KLine_Unit import CKLine_Unit
from Seg.Seg import CSeg

if TYPE_CHECKING:
    # 仅用于类型注解；运行期不引入，避免低层 ZS 反向依赖高层 BuySellPoint
    from BuySellPoint.BSPointConfig import CPointConfig

LINE_TYPE = TypeVar('LINE_TYPE', CBi, "CSeg")


class CZS(Generic[LINE_TYPE]):
    def __init__(self, lst: Optional[List[LINE_TYPE]], is_sure=True):
        # begin/end：永远指向 klu
        # low/high: 中枢的范围
        # peak_low/peak_high: 中枢所涉及到的笔的最大值，最小值
        self.__is_sure = is_sure
        self.__sub_zs_lst: List[CZS] = []

        if lst is None:
            return

        self.__begin: CKLine_Unit = lst[0].get_begin_klu()
        self.__begin_bi: LINE_TYPE = lst[0]  # 中枢内部的笔

        # self.__low = None
        # self.__high = None
        # self.__mid = None
        self.update_zs_range(lst)

        # self.__end: CKLine_Unit = None
        # self.__end_bi: CBi = None  # 中枢内部的笔
        self.__peak_high = float("-inf")
        self.__peak_low = float("inf")
        for item in lst:
            self.update_zs_end(item)

        self.__bi_in: Optional[LINE_TYPE] = None  # 进中枢那一笔
        self.__bi_out: Optional[LINE_TYPE] = None  # 出中枢那一笔

        self.__bi_lst: List[LINE_TYPE] = []  # begin_bi~end_bi之间的笔，在update_zs_in_seg函数中更新

    def clean_cache(self):
        self._memoize_cache = {}

    @property
    def is_sure(self): return self.__is_sure

    @property
    def sub_zs_lst(self): return self.__sub_zs_lst

    @property
    def begin(self): return self.__begin

    @property
    def begin_bi(self): return self.__begin_bi

    @property
    def low(self): return self.__low

    @property
    def high(self): return self.__high

    @property
    def mid(self): return self.__mid

    @property
    def end(self): return self.__end

    @property
    def end_bi(self): return self.__end_bi

    @property
    def peak_high(self): return self.__peak_high

    @property
    def peak_low(self): return self.__peak_low

    @property
    def bi_in(self): return self.__bi_in

    @property
    def bi_out(self): return self.__bi_out

    @property
    def bi_lst(self): return self.__bi_lst

    def update_zs_range(self, lst):
        self.__low: float = max(bi._low() for bi in lst)
        self.__high: float = min(bi._high() for bi in lst)
        self.__mid: float = (self.__low + self.__high) / 2  # 中枢的中点
        self.clean_cache()

    def is_one_bi_zs(self):
        if self.end_bi is None:
            raise CChanException("zs.end_bi is None", ErrCode.COMMON_ERROR)
        return self.begin_bi.idx == self.end_bi.idx

    def update_zs_end(self, item):
        self.__end: CKLine_Unit = item.get_end_klu()
        self.__end_bi: CBi = item
        if item._low() < self.peak_low:
            self.__peak_low = item._low()
        if item._high() > self.peak_high:
            self.__peak_high = item._high()
        self.clean_cache()

    def __str__(self):
        _str = f"{self.begin_bi.idx}->{self.end_bi.idx}"
        if _str2 := ",".join([str(sub_zs) for sub_zs in self.sub_zs_lst]):
            return f"{_str}({_str2})"
        else:
            return _str

    def combine(self, zs2: 'CZS', combine_mode) -> bool:
        if zs2.is_one_bi_zs():
            return False
        if self.begin_bi.seg_idx != zs2.begin_bi.seg_idx:
            return False
        if combine_mode == 'zs':
            if not has_overlap(self.low, self.high, zs2.low, zs2.high, equal=True):
                return False
            self.do_combine(zs2)
            return True
        elif combine_mode == 'peak':
            # 注意与 'zs' 模式的口径差异：peak 模式刻意要求严格重叠（equal=False），
            # 两中枢峰区间仅边缘相触不合并；'zs' 模式边缘相触即合并
            if has_overlap(self.peak_low, self.peak_high, zs2.peak_low, zs2.peak_high):
                self.do_combine(zs2)
                return True
            else:
                return False
        else:
            raise CChanException(f"{combine_mode} is unsupport zs conbine mode", ErrCode.PARA_ERROR)

    def do_combine(self, zs2: 'CZS'):
        if len(self.sub_zs_lst) == 0:
            self.__sub_zs_lst.append(self.make_copy())
        self.__sub_zs_lst.append(zs2)

        self.__low = min([self.low, zs2.low])
        self.__high = max([self.high, zs2.high])
        self.__mid = (self.__low + self.__high) / 2  # 合并后中枢中点需随区间重算
        self.__peak_low = min([self.peak_low, zs2.peak_low])
        self.__peak_high = max([self.peak_high, zs2.peak_high])
        self.__end = zs2.end
        self.__bi_out = zs2.bi_out
        self.__end_bi = zs2.end_bi
        self.clean_cache()

    def try_add_to_end(self, item):
        if not self.in_range(item):
            return False
        if self.is_one_bi_zs():
            self.update_zs_range([self.begin_bi, item])
        self.update_zs_end(item)
        return True

    def in_range(self, item):
        return has_overlap(self.low, self.high, item._low(), item._high())

    def is_inside(self, seg: CSeg):
        return seg.start_bi.idx <= self.begin_bi.idx <= seg.end_bi.idx

    def is_divergence(self, config: CPointConfig, in_bi: list, out_bi: list):
        if len(out_bi) == 0:
            raise CChanException("is_divergence: out_bi 为空", ErrCode.COMMON_ERROR)
        if not self.end_bi_break(out_bi[-1]):  # 最后一笔必须突破中枢
            return False, None
        in_metric = construct_virtual_bi(in_bi).cal_macd_metric(config.macd_algo, is_reverse=False)
        out_metric = construct_virtual_bi(out_bi).cal_macd_metric(config.macd_algo, is_reverse=True)

        # +1e-7 防除零（与 BSPointList 中 divergence_rate 的计算口径一致）
        if config.divergence_rate > 100:  # 保送
            return True, out_metric/(in_metric+1e-7)
        else:
            return out_metric <= config.divergence_rate*in_metric, out_metric/(in_metric+1e-7)

    def init_from_zs(self, zs: 'CZS'):
        self.__begin = zs.begin
        self.__end = zs.end
        self.__low = zs.low
        self.__high = zs.high
        self.__mid = zs.mid
        self.__peak_high = zs.peak_high
        self.__peak_low = zs.peak_low
        self.__begin_bi = zs.begin_bi
        self.__end_bi = zs.end_bi
        self.__bi_in = zs.bi_in
        self.__bi_out = zs.bi_out
        self.__bi_lst = list(zs.bi_lst)
        self.__sub_zs_lst = list(zs.sub_zs_lst)

    def make_copy(self) -> 'CZS':
        copy = CZS(lst=None, is_sure=self.is_sure)
        copy.init_from_zs(zs=self)
        return copy

    def end_bi_break(self, end_bi=None) -> bool:
        if end_bi is None:
            end_bi = self.get_bi_out()
        if end_bi is None:
            raise CChanException("end_bi_break: 出中枢笔不存在", ErrCode.COMMON_ERROR)
        return (end_bi.is_down() and end_bi._low() < self.low) or \
            (end_bi.is_up() and end_bi._high() > self.high)

    def out_bi_is_peak(self, end_bi_idx: int):
        # 返回 (是否最低点，bi_out与中枢里面尾部最接近它的差距比例)
        if len(self.bi_lst) == 0:
            raise CChanException("out_bi_is_peak: bi_lst 为空", ErrCode.COMMON_ERROR)
        if self.bi_out is None:
            return False, None
        peak_rate = float("inf")
        for bi in self.bi_lst:
            if bi.idx > end_bi_idx:
                break
            if (self.bi_out.is_down() and bi._low() < self.bi_out._low()) or (self.bi_out.is_up() and bi._high() > self.bi_out._high()):
                return False, None
            r = abs(bi.get_end_val()-self.bi_out.get_end_val())/self.bi_out.get_end_val()
            if r < peak_rate:
                peak_rate = r
        return True, peak_rate

    def get_bi_in(self) -> LINE_TYPE:
        if self.bi_in is None:
            raise CChanException("zs.bi_in is None", ErrCode.COMMON_ERROR)
        return self.bi_in

    def get_bi_out(self) -> LINE_TYPE:
        if self.__bi_out is None:
            raise CChanException("zs.bi_out is None", ErrCode.COMMON_ERROR)
        return self.__bi_out

    def set_bi_in(self, bi):
        self.__bi_in = bi
        self.clean_cache()

    def set_bi_out(self, bi):
        self.__bi_out = bi
        self.clean_cache()

    def set_bi_lst(self, bi_lst):
        self.__bi_lst = bi_lst
        self.clean_cache()


def construct_virtual_bi(bi_lst: List[LINE_TYPE]) -> LINE_TYPE:
    # 把相邻多笔虚拟合成一笔（首begin~末end），供 cal_macd_metric 取整体幅度区间；
    # 虚拟对象 idx=-1/is_sure=False，不参与索引定位
    if len(bi_lst) == 0:
        raise CChanException("construct_virtual_bi: 输入列表为空", ErrCode.COMMON_ERROR)
    if len(bi_lst) == 1:
        return bi_lst[0]
    if isinstance(bi_lst[0], CBi):
        if not isinstance(bi_lst[-1], CBi):
            raise CChanException("construct_virtual_bi: 首尾元素类型不一致", ErrCode.COMMON_ERROR)
        return CBi(bi_lst[0].begin_klc, bi_lst[-1].end_klc, idx=-1, is_sure=False)
    if not (isinstance(bi_lst[0], CSeg) and isinstance(bi_lst[-1], CSeg)):
        raise CChanException("construct_virtual_bi: 元素必须是清一色 CBi 或 CSeg", ErrCode.COMMON_ERROR)
    return CSeg(idx=-1, start_bi=bi_lst[0].start_bi, end_bi=bi_lst[-1].end_bi, is_sure=False)
