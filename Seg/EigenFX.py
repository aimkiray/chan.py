from typing import List, Optional

from Bi.Bi import CBi
from Bi.BiList import CBiList
from Common.CEnum import BI_DIR, FX_TYPE, KLINE_DIR, SEG_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.func_util import revert_bi_dir

from .Eigen import CEigen


class CEigenFX:
    def __init__(self, _dir: BI_DIR, exclude_included=True, lv=SEG_TYPE.BI):
        self.lv = lv
        self.dir = _dir  # 线段方向
        self.ele: List[Optional[CEigen]] = [None, None, None]
        self.lst: List[CBi] = []
        self.exclude_included = exclude_included
        self.kl_dir = KLINE_DIR.UP if _dir == BI_DIR.UP else KLINE_DIR.DOWN
        self.last_evidence_bi: Optional[CBi] = None
        self.last_evidence_bi_is_sure: bool = False
        self.actual_break_flag = True

    def treat_first_ele(self, bi: CBi) -> bool:
        self.ele[0] = CEigen(bi, self.kl_dir)
        return False

    def treat_second_ele(self, bi: CBi) -> bool:
        assert self.ele[0] is not None
        combine_dir = self.ele[0].try_add(bi, exclude_included=self.exclude_included)
        if combine_dir != KLINE_DIR.COMBINE:  # 不能合并
            self.ele[1] = CEigen(bi, self.kl_dir)
            if (self.is_up() and self.ele[1].high < self.ele[0].high) or \
               (self.is_down() and self.ele[1].low > self.ele[0].low):  # 前两元素不可能成为分形
                return self.reset()
        return False

    def treat_third_ele(self, bi: CBi) -> bool:
        assert self.ele[0] is not None
        assert self.ele[1] is not None
        self.last_evidence_bi = bi
        self.last_evidence_bi_is_sure = bi.is_used_to_be_sure
        allow_top_equal = (1 if bi.is_down() else -1) if self.exclude_included else None
        combine_dir = self.ele[1].try_add(bi, allow_top_equal=allow_top_equal)
        if combine_dir == KLINE_DIR.COMBINE:
            return False
        # 与前两元素一致用 kl_dir 构造；combine_dir 可能是 INCLUDED，死路径中
        # ele[2] 移位为 ele[0]/ele[1] 后 INCLUDED 方向参与 try_add 会抛错
        self.ele[2] = CEigen(bi, self.kl_dir)
        if not self.actual_break():
            return self.reset()
        self.ele[1].update_fx(self.ele[0], self.ele[2], exclude_included=self.exclude_included, allow_top_equal=allow_top_equal)  # type: ignore
        fx = self.ele[1].fx
        is_fx = (self.is_up() and fx == FX_TYPE.TOP) or (self.is_down() and fx == FX_TYPE.BOTTOM)
        return True if is_fx else self.reset()

    def add(self, bi: CBi) -> bool:  # 返回是否出现分形
        # 业务不变量不用 assert（python -O 下会被剥离，同向笔会静默产出错误分型）
        if bi.dir == self.dir:
            raise CChanException(f"特征序列元素的方向必须和线段方向相反! 当前笔:{bi.idx}({bi.dir}) 线段方向:{self.dir}", ErrCode.SEG_EIGEN_ERR)
        self.lst.append(bi)
        if self.ele[0] is None:  # 第一元素
            return self.treat_first_ele(bi)
        elif self.ele[1] is None:  # 第二元素
            return self.treat_second_ele(bi)
        elif self.ele[2] is None:  # 第三元素
            return self.treat_third_ele(bi)
        else:
            raise CChanException(f"特征序列3个都找齐了还没处理!! 当前笔:{bi.idx},当前:{str(self)}", ErrCode.SEG_EIGEN_ERR)

    def reset(self):
        bi_tmp_list = list(self.lst[1:])
        if self.exclude_included:
            self.clear()
            for bi in bi_tmp_list:
                if self.add(bi):
                    return True
        else:
            assert self.ele[1] is not None
            ele2_begin_idx = self.ele[1].lst[0].idx
            self.ele[0], self.ele[1], self.ele[2] = self.ele[1], self.ele[2], None
            self.lst = [bi for bi in bi_tmp_list if bi.idx >= ele2_begin_idx]  # 从第二元素开始

        return False

    def can_be_end(self, bi_lst: CBiList):
        assert self.ele[1] is not None
        if self.ele[1].gap:
            assert self.ele[0] is not None
            end_bi_idx = self.GetPeakBiIdx()
            return self.find_revert_fx(bi_lst, end_bi_idx+2)
        else:
            if not self.actual_break_flag:
                return None
            return True

    def is_down(self):
        return self.dir == BI_DIR.DOWN

    def is_up(self):
        return self.dir == BI_DIR.UP

    def GetPeakBiIdx(self):
        assert self.ele[1] is not None
        return self.ele[1].GetPeakBiIdx()

    def all_bi_is_sure(self):
        assert self.last_evidence_bi is not None
        return next((False for bi in self.lst if not bi.is_used_to_be_sure), True) and self.last_evidence_bi_is_sure

    def clear(self):
        self.ele = [None, None, None]
        self.lst = []
        # actual_break_flag/last_evidence_* 描述的是"当前候选分型"的突破证据，
        # 清空重建后必须复位，否则上一轮的"未实际突破"结论会被锁存到新分型上
        self.actual_break_flag = True
        self.last_evidence_bi = None
        self.last_evidence_bi_is_sure = False

    def __str__(self):
        _t = [f"{[] if ele is None else ','.join([str(b.idx) for b in ele.lst])}" for ele in self.ele]
        return " | ".join(_t)

    def actual_break(self):
        if not self.exclude_included:
            return True
        assert self.ele[2] and self.ele[1]
        if (self.is_up() and self.ele[2].low < self.ele[1][-1]._low()) or \
           (self.is_down() and self.ele[2].high > self.ele[1][-1]._high()):  # 防止第二元素因为合并导致后面没有实际突破
            return True
        assert len(self.ele[2]) == 1
        ele2_bi = self.ele[2][0]
        if ele2_bi.next and ele2_bi.next.next:
            if ele2_bi.is_down():
                if ele2_bi.next.next._low() < ele2_bi._low():
                    self.last_evidence_bi = ele2_bi.next.next
                    self.last_evidence_bi_is_sure = ele2_bi.next.next.is_used_to_be_sure
                    return True
                else:
                    if not ele2_bi.next.next.is_used_to_be_sure or ele2_bi.next.next.next is None:
                        self.actual_break_flag = False
                        return True
            elif ele2_bi.is_up():
                if ele2_bi.next.next._high() > ele2_bi._high():
                    self.last_evidence_bi = ele2_bi.next.next
                    self.last_evidence_bi_is_sure = ele2_bi.next.next.is_used_to_be_sure
                    return True
                else:
                    if not ele2_bi.next.next.is_used_to_be_sure or ele2_bi.next.next.next is None:
                        self.actual_break_flag = False
                        return True
        else:
            if ele2_bi.next:
                if self.is_up() and ele2_bi.next._high() > self.ele[1].high:
                    # 疑似顶分型被后续笔证伪：非实际突破，落到末尾 return False -> reset()
                    return False
                elif self.is_down() and ele2_bi.next._low() < self.ele[1].low:
                    # 疑似底分型被后续笔证伪：同上
                    return False
                else:
                    self.actual_break_flag = False
                    return True
            else:
                self.actual_break_flag = False
                return True
        return False

    def find_revert_fx(self, bi_list: CBiList, begin_idx: int):
        # 如果返回None，表示找到最后了
        if begin_idx >= len(bi_list):
            return None  # 峰后尚无反向笔可供判定，视同"未找到"等待后续数据
        first_bi_dir = bi_list[begin_idx].dir  # down则是要找顶分型
        revert_eigen_fx = CEigenFX(revert_bi_dir(first_bi_dir), exclude_included=True, lv=self.lv)  # 顶分型的话要找上升线段
        for idx in range(begin_idx, len(bi_list), 2):  # 同向笔序列，步长2避免整段切片拷贝
            bi = bi_list[idx]
            if revert_eigen_fx.add(bi):
                while True:
                    _test = revert_eigen_fx.can_be_end(bi_list)
                    if not revert_eigen_fx.actual_break_flag:
                        _test = None
                    if _test in [True, None]:
                        if revert_eigen_fx.ele[2] is None:
                            raise CChanException(
                                f"find_revert_fx: 分型判定成立但第三元素缺失, 笔idx={bi.idx}, lv={self.lv}",
                                ErrCode.SEG_EIGEN_ERR)
                        self.last_evidence_bi = revert_eigen_fx.ele[2].lst[-1]
                        if _test is True:
                            self.last_evidence_bi_is_sure = self.last_evidence_bi.is_used_to_be_sure and revert_eigen_fx.last_evidence_bi_is_sure
                        return _test
                    elif not revert_eigen_fx.reset():
                        break
            # 早期曾用 thred_value/break_thred 提前判否定（参看#272），已移除
        return None
