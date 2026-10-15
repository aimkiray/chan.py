from typing import Self

from Bi.Bi import CBi
from Combiner.KLine_Combiner import CKLine_Combiner
from Common.CEnum import BI_DIR, FX_TYPE
from Common.ChanException import CChanException, ErrCode


class CEigen(CKLine_Combiner[CBi]):
    def __init__(self, bi, _dir):
        super(CEigen, self).__init__(bi, _dir)
        self.gap = False

    def update_fx(self, _pre: Self, _next: Self, exclude_included=False, allow_top_equal=None):
        super(CEigen, self).update_fx(_pre, _next, exclude_included, allow_top_equal)
        if (self.fx == FX_TYPE.TOP and _pre.high < self.low) or \
           (self.fx == FX_TYPE.BOTTOM and _pre.low > self.high):
            self.gap = True

    def __str__(self):
        return f"{self.lst[0].idx}~{self.lst[-1].idx} gap={self.gap} fx={self.fx}"

    def GetPeakBiIdx(self):
        # 业务不变量不用 assert（python -O 下会被剥离，UNKNOWN 分型会静默返回错误峰值）
        if self.fx == FX_TYPE.UNKNOWN:
            raise CChanException("特征元素分型未知，无法取峰值笔", ErrCode.SEG_EIGEN_ERR)
        bi_dir = self.lst[0].dir
        if bi_dir == BI_DIR.UP:  # 下降线段
            return self.get_peak_klu(is_high=False).idx-1
        else:
            return self.get_peak_klu(is_high=True).idx-1
