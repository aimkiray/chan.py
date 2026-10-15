from Bi.BiList import CBiList
from Common.CEnum import BI_DIR, SEG_TYPE

from .EigenFX import CEigenFX
from .SegListComm import CSegListComm


class CSegListChan(CSegListComm):
    def __init__(self, seg_config=None, lv=SEG_TYPE.BI):
        super(CSegListChan, self).__init__(seg_config=seg_config, lv=lv)

    def _pop_last_seg(self):
        # 弹出尾线段时必须同步清理其覆盖笔的 parent_seg 与前驱的 next，
        # 否则下游（BSPointList.treat_bsp3_after 等）会读到悬空引用
        _seg = self.lst.pop()
        for bi in _seg.bi_list:
            bi.parent_seg = None
        if _seg.pre:
            _seg.pre.next = None

    def do_init(self):
        # 删除末尾不确定的线段
        while len(self) and not self.lst[-1].is_sure:
            self._pop_last_seg()
        if len(self):
            eigen_fx = self.lst[-1].eigen_fx
            if eigen_fx and eigen_fx.ele[-1] and not eigen_fx.ele[-1].lst[-1].is_sure:
                # 如果确定线段的分形的第三元素包含不确定笔，也需要重新算，不然线段分形元素的高低点可能不对
                self._pop_last_seg()

    def update(self, bi_lst: CBiList):
        self.do_init()
        if len(self) == 0:
            self.cal_seg_sure(bi_lst, begin_idx=0)
        else:
            self.cal_seg_sure(bi_lst, begin_idx=self[-1].end_bi.idx+1)
        self.collect_left_seg(bi_lst)

    def cal_seg_sure(self, bi_lst: CBiList, begin_idx: int):
        cur_begin = begin_idx
        while True:
            up_eigen = CEigenFX(BI_DIR.UP, lv=self.lv)  # 上升线段下降笔
            down_eigen = CEigenFX(BI_DIR.DOWN, lv=self.lv)  # 下降线段上升笔
            last_seg_dir = None if len(self) == 0 else self[-1].dir

            fx_eigen = None
            for _idx in range(cur_begin, len(bi_lst)):  # 索引遍历避免每次尾扫整段切片拷贝
                bi = bi_lst[_idx]
                if bi.is_down() and last_seg_dir != BI_DIR.UP:
                    if up_eigen.add(bi):
                        fx_eigen = up_eigen
                elif bi.is_up() and last_seg_dir != BI_DIR.DOWN:
                    if down_eigen.add(bi):
                        fx_eigen = down_eigen
                if len(self) == 0:  # 尝试确定第一段方向，不要以谁先成为分形来决定，反例：US.EVRG
                    if up_eigen.ele[1] is not None and bi.is_down():
                        last_seg_dir = BI_DIR.DOWN
                        down_eigen.clear()
                    elif down_eigen.ele[1] is not None and bi.is_up():
                        up_eigen.clear()
                        last_seg_dir = BI_DIR.UP
                    if up_eigen.ele[1] is None and last_seg_dir == BI_DIR.DOWN and bi.dir == BI_DIR.DOWN:
                        last_seg_dir = None
                    elif down_eigen.ele[1] is None and last_seg_dir == BI_DIR.UP and bi.dir == BI_DIR.UP:
                        last_seg_dir = None

                if fx_eigen:
                    break

            if not fx_eigen:
                return

            next_begin = self.treat_fx_eigen(fx_eigen, bi_lst)
            if next_begin is None:
                return
            cur_begin = next_begin

    def treat_fx_eigen(self, fx_eigen, bi_lst: CBiList):
        _test = fx_eigen.can_be_end(bi_lst)
        end_bi_idx = fx_eigen.GetPeakBiIdx()
        if _test in [True, None]:  # None表示反向分型找到尾部也没找到
            is_true = _test is not None  # 如果是正常结束
            if not self.add_new_seg(bi_lst, end_bi_idx, is_sure=is_true and fx_eigen.all_bi_is_sure()):  # 防止第一根线段的方向与首尾值异常
                return end_bi_idx + 1
            self.lst[-1].eigen_fx = fx_eigen
            if is_true:
                return end_bi_idx + 1
            return None
        # can_be_end 在 #272 注释掉阈值判定后只返回 True/None，以下为
        # 防御性兜底（不可达）：若未来恢复 False 返回，从第二元素重找
        return fx_eigen.lst[1].idx
