import math
from typing import List

from Bi.Bi import CBi
from BuySellPoint.BS_Point import CBS_Point
from Common.CEnum import FX_TYPE
from Common.ChanException import CChanException, ErrCode
from KLine.KLine import CKLine
from KLine.KLine_List import CKLine_List
from Seg.Eigen import CEigen
from Seg.EigenFX import CEigenFX
from Seg.Seg import CSeg
from ZS.ZS import CZS


def _finite_or_none(v):
    """将 NaN/±inf 归一为 None。

    meta 字段会被 backend/serialization.py 逐字段摘取后 json.dumps，
    float() 不拦截 NaN/inf，会产生非法 JSON 字面量；归一为 None 后
    序列化为 null，前端可安全 JSON.parse。绘图侧 matplotlib 将 None
    视作缺失值（与 NaN 等价的空档语义），语义不回归。
    """
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


class Cklc_meta:
    def __init__(self, klc: CKLine):
        self.high = _finite_or_none(klc.high)
        self.low = _finite_or_none(klc.low)
        self.begin_idx = klc.lst[0].idx
        self.end_idx = klc.lst[-1].idx
        self.type = klc.fx if klc.fx != FX_TYPE.UNKNOWN else klc.dir

        self.klu_list = list(klc.lst)


class CBi_meta:
    def __init__(self, bi: CBi):
        self.idx = bi.idx
        self.dir = bi.dir
        self.type = bi.type
        self.begin_x = bi.get_begin_klu().idx
        self.end_x = bi.get_end_klu().idx
        self.begin_y = _finite_or_none(bi.get_begin_val())
        self.end_y = _finite_or_none(bi.get_end_val())
        self.is_sure = bi.is_sure


class CSeg_meta:
    def __init__(self, seg: CSeg):
        # CBi/CSeg 均实现了 get_begin_klu/get_begin_val/get_end_klu/get_end_val
        # （CSeg 版本会向 start_bi/end_bi 递归委托），多态调用即可同时覆盖
        # start_bi 为笔（普通线段）与为线段（段段）两种形态，无需类型名分派
        try:
            self.begin_x = seg.start_bi.get_begin_klu().idx
            self.begin_y = _finite_or_none(seg.start_bi.get_begin_val())
            self.end_x = seg.end_bi.get_end_klu().idx
            self.end_y = _finite_or_none(seg.end_bi.get_end_val())
        except AttributeError as e:
            raise CChanException(
                f"Unknown start_bi/end_bi type: {type(seg.start_bi)} {seg.start_bi}",
                ErrCode.PLOT_ERR) from e
        self.dir = seg.dir
        self.is_sure = seg.is_sure
        self.idx = seg.idx

        # tl 存预计算好的 (x0,y0,x1,y1) 坐标元组而非活 CTrendLine 对象：
        # 保证 meta 是纯数据，序列化/缓存路径不会意外钉住 Chan 内部对象
        self.tl = {}
        if seg.support_trend_line and seg.support_trend_line.line:
            self.tl["support"] = self.format_tl(seg.support_trend_line)
        if seg.resistance_trend_line and seg.resistance_trend_line.line:
            self.tl["resistance"] = self.format_tl(seg.resistance_trend_line)

    def format_tl(self, tl):
        if tl.line is None:
            raise CChanException("format_tl requires trend line with valid line", ErrCode.PLOT_ERR)
        tl_slope = tl.line.slope + 1e-7
        tl_x = tl.line.p.x
        tl_y = tl.line.p.y
        tl_y0 = self.begin_y
        tl_y1 = self.end_y
        if tl_y0 is None or tl_y1 is None or tl_slope == 0:
            # 端点 y 含非有限值（已归一为 None）或斜率退化时坐标无意义
            return (None, tl_y0, None, tl_y1)
        tl_x0 = (tl_y0-tl_y)/tl_slope + tl_x
        tl_x1 = (tl_y1-tl_y)/tl_slope + tl_x
        return tuple(_finite_or_none(v) for v in (tl_x0, tl_y0, tl_x1, tl_y1))


class CEigen_meta:
    def __init__(self, eigen: CEigen):
        self.begin_x = eigen.lst[0].get_begin_klu().idx
        self.end_x = eigen.lst[-1].get_end_klu().idx
        self.begin_y = _finite_or_none(eigen.low)
        self.end_y = _finite_or_none(eigen.high)
        self.w = self.end_x - self.begin_x
        self.h = None if self.begin_y is None or self.end_y is None else self.end_y - self.begin_y


class CEigenFX_meta:
    def __init__(self, eigenFX: CEigenFX):
        self.ele = [CEigen_meta(ele) for ele in eigenFX.ele if ele is not None]
        if len(self.ele) != 3 or len(eigenFX.ele) < 2 or eigenFX.ele[1] is None:
            raise CChanException(
                f"eigenFX must contain exactly 3 eigen with non-None middle one, got ele_cnt={len(self.ele)}",
                ErrCode.PLOT_ERR)
        self.gap = eigenFX.ele[1].gap
        self.fx = eigenFX.ele[1].fx


class CZS_meta:
    def __init__(self, zs: CZS):
        self.low = _finite_or_none(zs.low)
        self.high = _finite_or_none(zs.high)
        self.begin = zs.begin.idx
        self.end = zs.end.idx
        self.w = self.end - self.begin
        self.h = None if self.low is None or self.high is None else self.high - self.low
        self.is_sure = zs.is_sure
        self.sub_zs_lst = [CZS_meta(t) for t in zs.sub_zs_lst]
        self.is_onebi_zs = zs.is_one_bi_zs()


class CBS_Point_meta:
    def __init__(self, bsp: CBS_Point, is_seg):
        self.is_buy = bsp.is_buy
        self.type = bsp.type2str()
        self.is_seg = is_seg

        self.x = bsp.klu.idx
        self.y = _finite_or_none(bsp.klu.low if self.is_buy else bsp.klu.high)

    def desc(self):
        is_seg_flag = "※" if self.is_seg else ""
        return f'{is_seg_flag}b{self.type}' if self.is_buy else f'{is_seg_flag}s{self.type}'


class CChanPlotMeta:
    def __init__(self, kl_list: CKLine_List):
        self.data = kl_list

        self.klc_list: List[Cklc_meta] = [Cklc_meta(klc) for klc in kl_list.lst]
        self.datetick = [klu.time.to_str() for klu in self.klu_iter()]
        self.klu_len = sum(len(klc.klu_list) for klc in self.klc_list)

        self.bi_list = [CBi_meta(bi) for bi in kl_list.bi_list]

        self.seg_list: List[CSeg_meta] = []
        self.eigenfx_lst: List[CEigenFX_meta] = []
        for seg in kl_list.seg_list:
            self.seg_list.append(CSeg_meta(seg))
            if seg.eigen_fx:
                self.eigenfx_lst.append(CEigenFX_meta(seg.eigen_fx))

        self.seg_eigenfx_lst: List[CEigenFX_meta] = []
        self.segseg_list: List[CSeg_meta] = []
        for segseg in kl_list.segseg_list:
            self.segseg_list.append(CSeg_meta(segseg))
            if segseg.eigen_fx:
                self.seg_eigenfx_lst.append(CEigenFX_meta(segseg.eigen_fx))

        self.zs_lst: List[CZS_meta] = [CZS_meta(zs) for zs in kl_list.zs_list]
        self.segzs_lst: List[CZS_meta] = [CZS_meta(segzs) for segzs in kl_list.segzs_list]

        self.bs_point_lst: List[CBS_Point_meta] = [CBS_Point_meta(bs_point, is_seg=False) for bs_point in kl_list.bs_point_lst.bsp_iter()]
        self.seg_bsp_lst: List[CBS_Point_meta] = [CBS_Point_meta(seg_bsp, is_seg=True) for seg_bsp in kl_list.seg_bs_point_lst.bsp_iter()]

    def klu_iter(self):
        for klc in self.klc_list:
            yield from klc.klu_list

    def sub_last_kseg_start_idx(self, seg_cnt):
        # seg_cnt=0 时 [-0] 会误取首元素，必须显式短路；cnt 超界同按"无偏移"返回 0
        if not seg_cnt or len(self.data.seg_list) <= seg_cnt:
            return 0
        begin_klu = self.data.seg_list[-seg_cnt].get_begin_klu()
        if not begin_klu.sub_kl_list:
            return 0
        return begin_klu.sub_kl_list[0].idx

    def sub_last_kbi_start_idx(self, bi_cnt):
        if not bi_cnt or len(self.data.bi_list) <= bi_cnt:
            return 0
        begin_klc = self.data.bi_list[-bi_cnt].begin_klc
        if not begin_klc.lst or not begin_klc.lst[0].sub_kl_list:
            return 0
        return begin_klc.lst[0].sub_kl_list[0].idx

    def sub_range_start_idx(self, x_range):
        for klc in self.data[::-1]:
            for klu in klc[::-1]:
                x_range -= 1
                if x_range == 0:
                    return klu.sub_kl_list[0].idx if klu.sub_kl_list else 0
        return 0
