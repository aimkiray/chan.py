import abc
from typing import Generic, List, TypeVar, Union, overload

from Bi.Bi import CBi
from Bi.BiList import CBiList
from Common.CEnum import BI_DIR, LEFT_SEG_METHOD, SEG_TYPE
from Common.ChanException import CChanException, ErrCode

from .Seg import CSeg
from .SegConfig import CSegConfig

SUB_LINE_TYPE = TypeVar('SUB_LINE_TYPE', CBi, "CSeg")


class CSegListComm(Generic[SUB_LINE_TYPE]):
    def __init__(self, seg_config=None, lv=SEG_TYPE.BI):
        # 不使用 CSegConfig() 作为默认参数，避免跨实例共享可变配置
        self.lst: List[CSeg[SUB_LINE_TYPE]] = []
        self.lv = lv
        # config 须在 do_init() 前赋值：do_init 为子类可覆写钩子，可能读配置
        self.config = seg_config if seg_config is not None else CSegConfig()
        self.do_init()

    def do_init(self):
        # 整表重建前必须断开旧线段的反向引用：未被新线段重新覆盖的笔
        # 否则会持有指向已废弃线段对象的 parent_seg（seg.next 同理）
        for seg in self.lst:
            for bi in seg.bi_list:
                bi.parent_seg = None
            if seg.pre:
                seg.pre.next = None
        self.lst = []

    def __iter__(self):
        yield from self.lst

    @overload
    def __getitem__(self, index: int) -> CSeg[SUB_LINE_TYPE]: ...

    @overload
    def __getitem__(self, index: slice) -> List[CSeg[SUB_LINE_TYPE]]: ...

    def __getitem__(self, index: Union[slice, int]) -> Union[List[CSeg[SUB_LINE_TYPE]], CSeg[SUB_LINE_TYPE]]:
        return self.lst[index]

    def __len__(self):
        return len(self.lst)

    def left_bi_break(self, bi_lst: CBiList):
        # 最后一个确定线段之后的笔有突破该线段最后一笔的
        if len(self) == 0:
            return False
        last_seg_end_bi = self[-1].end_bi
        for bi in bi_lst[last_seg_end_bi.idx+1:]:
            if last_seg_end_bi.is_up() and bi._high() > last_seg_end_bi._high():
                return True
            elif last_seg_end_bi.is_down() and bi._low() < last_seg_end_bi._low():
                return True
        return False

    def collect_first_seg(self, bi_lst: CBiList):
        if len(bi_lst) < 3:
            return
        if self.config.left_method == LEFT_SEG_METHOD.PEAK:
            _high = max(bi._high() for bi in bi_lst)
            _low = min(bi._low() for bi in bi_lst)
            if abs(_high-bi_lst[0].get_begin_val()) >= abs(_low-bi_lst[0].get_begin_val()):
                peak_bi = FindPeakBi(bi_lst, is_high=True)
                if peak_bi is None:
                    raise CChanException("首线段归集失败：笔序列中找不到向上峰值笔", ErrCode.COMMON_ERROR)
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=BI_DIR.UP, split_first_seg=False, reason="0seg_find_high")
            else:
                peak_bi = FindPeakBi(bi_lst, is_high=False)
                if peak_bi is None:
                    raise CChanException("首线段归集失败：笔序列中找不到向下峰值笔", ErrCode.COMMON_ERROR)
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=BI_DIR.DOWN, split_first_seg=False, reason="0seg_find_low")
            self.collect_left_as_seg(bi_lst)
        elif self.config.left_method == LEFT_SEG_METHOD.ALL:
            _dir = BI_DIR.UP if bi_lst[-1].get_end_val() >= bi_lst[0].get_begin_val() else BI_DIR.DOWN
            self.add_new_seg(bi_lst, bi_lst[-1].idx, is_sure=False, seg_dir=_dir, split_first_seg=False, reason="0seg_collect_all")
        else:
            raise CChanException(f"unknown seg left_method = {self.config.left_method}", ErrCode.PARA_ERROR)

    def collect_left_seg_peak_method(self, last_seg_end_bi, bi_lst):
        while True:
            find_new_seg = False
            if last_seg_end_bi.is_down():
                peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx + 3 :], is_high=True)
                if peak_bi and peak_bi.idx - last_seg_end_bi.idx >= 3:
                    self.add_new_seg(
                        bi_lst,
                        peak_bi.idx,
                        is_sure=False,
                        seg_dir=BI_DIR.UP,
                        reason="collectleft_find_high",
                    )
                    find_new_seg = True
            else:
                peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx + 3 :], is_high=False)
                if peak_bi and peak_bi.idx - last_seg_end_bi.idx >= 3:
                    self.add_new_seg(
                        bi_lst,
                        peak_bi.idx,
                        is_sure=False,
                        seg_dir=BI_DIR.DOWN,
                        reason="collectleft_find_low",
                    )
                    find_new_seg = True

            last_seg_end_bi = self[-1].end_bi
            if not find_new_seg:
                self.collect_left_as_seg(bi_lst)
                return

    def collect_segs(self, bi_lst):
        # 迭代代替原递归（add_new_seg 后尾调 collect_left_seg）：尾部笔数
        # 决定原递归深度（≈n/3），极端长输入可触 RecursionError
        while True:
            last_bi = bi_lst[-1]
            last_seg_end_bi = self[-1].end_bi
            if last_bi.idx-last_seg_end_bi.idx < 3:
                return
            if last_seg_end_bi.is_down() and last_bi.get_end_val() <= last_seg_end_bi.get_end_val():
                peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx+3:], is_high=True)
                if not peak_bi:
                    return
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=BI_DIR.UP, reason="collectleft_find_high_force")
                continue
            if last_seg_end_bi.is_up() and last_bi.get_end_val() >= last_seg_end_bi.get_end_val():
                peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx+3:], is_high=False)
                if not peak_bi:
                    return
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=BI_DIR.DOWN, reason="collectleft_find_low_force")
                continue
            # 剩下线段的尾部相比于最后一个线段的尾部，高低关系和最后一个虚线段的方向一致
            if self.config.left_method == LEFT_SEG_METHOD.ALL:  # 容易找不到二类买卖点！！
                self.collect_left_as_seg(bi_lst)
            elif self.config.left_method == LEFT_SEG_METHOD.PEAK:
                self.collect_left_seg_peak_method(last_seg_end_bi, bi_lst)
            else:
                raise CChanException(f"unknown seg left_method = {self.config.left_method}", ErrCode.PARA_ERROR)
            return

    def collect_left_seg(self, bi_lst: CBiList):
        if len(bi_lst) == 0:
            return  # 存量线段+空笔表无可归集（否则下方取 bi_lst[-1] 会 IndexError）
        if len(self) == 0:
            self.collect_first_seg(bi_lst)
        else:
            self.collect_segs(bi_lst)

    def collect_left_as_seg(self, bi_lst: CBiList):
        if len(bi_lst) == 0:
            return
        last_bi = bi_lst[-1]
        last_seg_end_bi = self[-1].end_bi
        if last_seg_end_bi.idx+1 >= len(bi_lst):
            return
        if last_seg_end_bi.dir == last_bi.dir:
            self.add_new_seg(bi_lst, last_bi.idx-1, is_sure=False, reason="collect_left_1")
        else:
            self.add_new_seg(bi_lst, last_bi.idx, is_sure=False, reason="collect_left_0")

    def try_add_new_seg(self, bi_lst, end_bi_idx: int, is_sure=True, seg_dir=None, split_first_seg=True, reason="normal"):
        if len(self) == 0 and split_first_seg and end_bi_idx >= 3:
            if peak_bi := FindPeakBi(bi_lst[end_bi_idx-3::-1], bi_lst[end_bi_idx].is_down()):
                if (peak_bi.is_down() and (peak_bi._low() < bi_lst[0]._low() or peak_bi.idx == 0)) or \
                   (peak_bi.is_up() and (peak_bi._high() > bi_lst[0]._high() or peak_bi.idx == 0)):  # 要比第一笔开头还高/低（因为没有比较到）
                    self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=peak_bi.dir, reason="split_first_1st")
                    self.add_new_seg(bi_lst, end_bi_idx, is_sure=False, reason="split_first_2nd")
                    return
        bi1_idx = 0 if len(self) == 0 else self[-1].end_bi.idx+1
        bi1 = bi_lst[bi1_idx]
        bi2 = bi_lst[end_bi_idx]
        self.lst.append(CSeg(len(self.lst), bi1, bi2, is_sure=is_sure, seg_dir=seg_dir, reason=reason))

        if len(self.lst) >= 2:
            self.lst[-2].next = self.lst[-1]
            self.lst[-1].pre = self.lst[-2]
        self.lst[-1].update_bi_list(bi_lst, bi1_idx, end_bi_idx)

    def add_new_seg(self, bi_lst: CBiList, end_bi_idx: int, is_sure=True, seg_dir=None, split_first_seg=True, reason="normal"):
        try:
            self.try_add_new_seg(bi_lst, end_bi_idx, is_sure, seg_dir, split_first_seg, reason)
        except CChanException as e:
            if e.errcode == ErrCode.SEG_END_VALUE_ERR and len(self.lst) == 0:
                return False
            raise  # 裸抛保留原回溯起点；非 CChanException 自然向上传播
        return True

    @abc.abstractmethod
    def update(self, bi_lst: CBiList):
        ...

    def exist_sure_seg(self):
        return any(seg.is_sure for seg in self.lst)


def FindPeakBi(bi_lst: Union[CBiList, List[CBi]], is_high):
    peak_val = float("-inf") if is_high else float("inf")
    peak_bi = None
    for bi in bi_lst:
        if (is_high and bi.get_end_val() >= peak_val and bi.is_up()) or (not is_high and bi.get_end_val() <= peak_val and bi.is_down()):
            if bi.pre and bi.pre.pre and ((is_high and bi.pre.pre.get_end_val() > bi.get_end_val()) or (not is_high and bi.pre.pre.get_end_val() < bi.get_end_val())):
                continue
            peak_val = bi.get_end_val()
            peak_bi = bi
    return peak_bi
