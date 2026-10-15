from dataclasses import dataclass
from math import sqrt
from typing import Optional

from Common.CEnum import BI_DIR, TREND_LINE_SIDE


@dataclass
class Point:
    x: int
    y: float

    def cal_slope(self, p):
        # 同 x 时斜率无定义：按 dy 方向返回 ±inf（dy==0 约定 +inf），
        # 保证 cal_tl 的方向过滤（UP 剔除 slope<0 / DOWN 剔除 slope>0）语义正确；
        # 旧实现恒返 +inf，会把"同 x 更低"的候选点误当正斜率
        if self.x == p.x:
            return float("inf") if self.y >= p.y else float("-inf")
        return (self.y-p.y)/(self.x-p.x)


@dataclass
class Line:
    p: Point
    slope: float

    def cal_dis(self, p):
        return abs(self.slope*p.x - p.y + self.p.y - self.slope*self.p.x) / sqrt(self.slope**2 + 1)


class CTrendLine:
    """对 bi 序列每隔一笔取端点拟合趋势线。

    `self.line` 契约：Optional[Line]。
    当全部候选切片的距离均为 NaN（例如候选线斜率仅剩 ±inf）时保持 None，
    表示"无合法趋势线"——下游 PlotMeta(L57-60) 已对该状态判空兜底，
    调用方使用前应自行判断 line 是否为 None。
    """

    def __init__(self, lst, side=TREND_LINE_SIDE.OUTSIDE):
        self.line: Optional[Line] = None
        self.side = side
        self.cal(lst)

    def cal(self, lst):
        bench = float('inf')
        if self.side == TREND_LINE_SIDE.INSIDE:
            all_p = [Point(bi.get_begin_klu().idx, bi.get_begin_val()) for bi in lst[-1::-2]]
        else:
            all_p = [Point(bi.get_end_klu().idx, bi.get_end_val()) for bi in lst[-1::-2]]
        c_p = all_p  # cal_tl 只切片不原地修改，无需 copy
        while True:
            line, idx = cal_tl(c_p, lst[-1].dir, self.side)
            dis = sum(line.cal_dis(p) for p in all_p)
            if dis < bench:  # dis 为 nan 时恒 False，自动跳过斜率 ±inf 的候选
                bench = dis
                self.line = line
            c_p = c_p[idx:]
            if len(c_p) == 1:
                break


def init_peak_slope(_dir, side):
    if side == TREND_LINE_SIDE.INSIDE:
        return 0
    elif _dir == BI_DIR.UP:
        return float("inf")
    else:
        return -float("inf")


def cal_tl(c_p, _dir, side):
    p = c_p[0]
    peak_slope = init_peak_slope(_dir, side)
    idx = 1
    for point_idx, p2 in enumerate(c_p[1:]):
        slope = p.cal_slope(p2)
        if (_dir == BI_DIR.UP and slope < 0) or (_dir == BI_DIR.DOWN and slope > 0):
            continue
        if side == TREND_LINE_SIDE.INSIDE:
            if (_dir == BI_DIR.UP and slope > peak_slope) or (_dir == BI_DIR.DOWN and slope < peak_slope):
                peak_slope = slope
                idx = point_idx+1
        else:
            if (_dir == BI_DIR.UP and slope < peak_slope) or (_dir == BI_DIR.DOWN and slope > peak_slope):
                peak_slope = slope
                idx = point_idx+1
    return Line(p, peak_slope), idx
