from Bi.BiList import CBiList
from Common.CEnum import SEG_TYPE

from .SegListComm import CSegListComm


def bi_is_new_high(bi, pre_bi):
    return bi._high() > pre_bi._high()


def bi_is_new_low(bi, pre_bi):
    return bi._low() < pre_bi._low()


class CSegListDef(CSegListComm):
    def __init__(self, seg_config=None, lv=SEG_TYPE.BI):
        super(CSegListDef, self).__init__(seg_config=seg_config, lv=lv)

    def update(self, bi_lst: CBiList):
        self.do_init()
        self.cal_bi_sure(bi_lst)
        self.collect_left_seg(bi_lst)

    # update_last_end 已随 sure_seg_update_end 死开关一并移除（唯一调用点消失，
    # 属死代码；历史实现见 git 记录）

    def cal_bi_sure(self, bi_lst):
        peak_bi = None
        if len(bi_lst) == 0:
            return
        for idx, bi in enumerate(bi_lst):
            if idx < 2:
                continue
            if peak_bi and ((bi.is_up() and peak_bi.is_up() and bi._high() >= peak_bi._high()) or (bi.is_down() and peak_bi.is_down() and bi._low() <= peak_bi._low())):
                peak_bi = bi
                continue
            pre_bi = bi_lst[idx-2]
            if (bi.is_up() and bi_is_new_high(bi, pre_bi)) or \
               (bi.is_down() and bi_is_new_low(bi, pre_bi)):
                if peak_bi is None:
                    if len(self) == 0 or bi.dir != self[-1].dir:
                        peak_bi = bi
                        continue
                elif peak_bi.dir != bi.dir:
                    if bi.idx - peak_bi.idx <= 2:
                        continue
                    self.add_new_seg(bi_lst, peak_bi.idx)
                    peak_bi = bi
                    continue
        if peak_bi is not None:
            self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False)
