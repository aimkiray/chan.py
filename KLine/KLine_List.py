import copy
import itertools
from typing import List, Union, overload

from Bi.Bi import CBi
from Bi.BiList import CBiList
from BuySellPoint.BSPointList import CBSPointList
from ChanConfig import CChanConfig
from Common.CEnum import KLINE_DIR, SEG_TYPE
from Common.ChanException import CChanException, ErrCode
from Seg.Seg import CSeg
from Seg.SegConfig import CSegConfig
from Seg.SegListComm import CSegListComm
from ZS.ZSList import CZSList

from .KLine import CKLine
from .KLine_Unit import CKLine_Unit


def get_seglist_instance(seg_config: CSegConfig, lv) -> CSegListComm:
    if seg_config.seg_algo == "chan":
        from Seg.SegListChan import CSegListChan
        return CSegListChan(seg_config, lv)
    elif seg_config.seg_algo == "1+1":
        print(f'Please avoid using seg_algo={seg_config.seg_algo} as it is deprecated and no longer maintained.')
        from Seg.SegListDYH import CSegListDYH
        return CSegListDYH(seg_config, lv)
    elif seg_config.seg_algo == "break":
        print(f'Please avoid using seg_algo={seg_config.seg_algo} as it is deprecated and no longer maintained.')
        from Seg.SegListDef import CSegListDef
        return CSegListDef(seg_config, lv)
    else:
        raise CChanException(f"unsupport seg algoright:{seg_config.seg_algo}", ErrCode.PARA_ERROR)


class CKLine_List:
    def __init__(self, kl_type, conf: CChanConfig):
        self.kl_type = kl_type
        self.config = conf
        self.lst: List[CKLine] = []  # K线列表，可递归  元素KLine类型
        self.bi_list = CBiList(bi_conf=conf.bi_conf)
        self.seg_list: CSegListComm[CBi] = get_seglist_instance(seg_config=conf.seg_conf, lv=SEG_TYPE.BI)
        self.segseg_list: CSegListComm[CSeg[CBi]] = get_seglist_instance(seg_config=conf.seg_conf, lv=SEG_TYPE.SEG)

        self.zs_list = CZSList(zs_config=conf.zs_conf)
        self.segzs_list = CZSList(zs_config=conf.zs_conf)

        self.bs_point_lst = CBSPointList[CBi, CBiList](bs_point_config=conf.bs_point_conf)
        self.seg_bs_point_lst = CBSPointList[CSeg, CSegListComm](bs_point_config=conf.seg_bs_point_conf)

        self.metric_model_lst = conf.GetMetricModel()

        self.step_calculation = self.need_cal_step_by_step()

        self.last_sure_seg_start_bi_idx = -1
        self.last_sure_segseg_start_bi_idx = -1

    def __deepcopy__(self, memo):
        # 用 __new__ 起步而非走 __init__：bi/seg/zs/bsp/metric 等成员随后全部
        # 被深拷贝覆盖，__init__ 里重建一遍纯属白构造（且 deprecated seg_algo 会重复打 warning）
        cls = self.__class__
        new_obj: CKLine_List = cls.__new__(cls)
        memo[id(self)] = new_obj
        new_obj.kl_type = self.kl_type
        # config 与 CChan.__deepcopy__ 对齐：CChan 先 copy.deepcopy(self.conf, memo)，
        # 这里取 memo 中那份副本，使拷贝侧各层共享同一个新 conf，
        # 消除“kl_datas 仍持原 conf / chan.conf 已是新 conf”的双向污染；
        # 独立深拷贝 CKLine_List 时 memo 里没有，则自行深拷贝保证隔离
        conf = memo.get(id(self.config))
        if conf is None:
            conf = copy.deepcopy(self.config, memo)
        new_obj.config = conf
        new_obj.lst = []
        for klc in self.lst:
            klus_new = []
            for klu in klc.lst:
                new_klu = copy.deepcopy(klu, memo)
                memo[id(klu)] = new_klu
                if klu.pre is not None:
                    new_klu.set_pre_klu(memo[id(klu.pre)])
                klus_new.append(new_klu)

            new_klc = CKLine(klus_new[0], idx=klc.idx, _dir=klc.dir)
            new_klc.set_fx(klc.fx)
            new_klc.kl_type = klc.kl_type
            for idx, klu in enumerate(klus_new):
                klu.set_klc(new_klc)
                if idx != 0:
                    new_klc.try_add(klu, skip_update_input=True)
            memo[id(klc)] = new_klc
            if new_obj.lst:
                new_obj.lst[-1].set_next(new_klc)
                new_klc.set_pre(new_obj.lst[-1])
            new_obj.lst.append(new_klc)
        new_obj.bi_list = copy.deepcopy(self.bi_list, memo)
        new_obj.seg_list = copy.deepcopy(self.seg_list, memo)
        new_obj.segseg_list = copy.deepcopy(self.segseg_list, memo)
        new_obj.zs_list = copy.deepcopy(self.zs_list, memo)
        new_obj.segzs_list = copy.deepcopy(self.segzs_list, memo)
        new_obj.bs_point_lst = copy.deepcopy(self.bs_point_lst, memo)
        new_obj.metric_model_lst = copy.deepcopy(self.metric_model_lst, memo)
        new_obj.step_calculation = copy.deepcopy(self.step_calculation, memo)
        new_obj.seg_bs_point_lst = copy.deepcopy(self.seg_bs_point_lst, memo)
        # 增量计算游标一并拷贝，否则拷贝侧首次 cal_seg 会全量回扫
        new_obj.last_sure_seg_start_bi_idx = self.last_sure_seg_start_bi_idx
        new_obj.last_sure_segseg_start_bi_idx = self.last_sure_segseg_start_bi_idx
        return new_obj

    @overload
    def __getitem__(self, index: int) -> CKLine: ...

    @overload
    def __getitem__(self, index: slice) -> List[CKLine]: ...

    def __getitem__(self, index: Union[slice, int]) -> Union[List[CKLine], CKLine]:
        return self.lst[index]

    def __len__(self):
        return len(self.lst)

    def cal_seg_and_zs(self):
        if len(self.lst) == 0 or len(self.bi_list) == 0:
            # 无K线/无笔时无可计算内容，安全返回；
            # 最高级别空数据由 CChan.load()/trigger_load() 统一抛 ErrCode.NO_DATA
            return
        if not self.step_calculation:
            self.bi_list.try_add_virtual_bi(self.lst[-1])
        self.last_sure_seg_start_bi_idx = cal_seg(self.bi_list, self.seg_list, self.last_sure_seg_start_bi_idx)
        self.zs_list.cal_bi_zs(self.bi_list, self.seg_list)
        update_zs_in_seg(self.bi_list, self.seg_list, self.zs_list)  # 计算seg的zs_lst，以及中枢的bi_in, bi_out

        self.last_sure_segseg_start_bi_idx = cal_seg(self.seg_list, self.segseg_list, self.last_sure_segseg_start_bi_idx)
        self.segzs_list.cal_bi_zs(self.seg_list, self.segseg_list)
        update_zs_in_seg(self.seg_list, self.segseg_list, self.segzs_list)  # 计算segseg的zs_lst，以及中枢的bi_in, bi_out

        # 计算买卖点
        self.seg_bs_point_lst.cal(self.seg_list, self.segseg_list)  # 线段线段买卖点
        self.bs_point_lst.cal(self.bi_list, self.seg_list)  # 再算笔买卖点

    def need_cal_step_by_step(self):
        return self.config.trigger_step

    def add_single_klu(self, klu: CKLine_Unit):
        klu.set_metric(self.metric_model_lst)
        if len(self.lst) == 0:
            self.lst.append(CKLine(klu, idx=0))
        else:
            last_klc = self.lst[-1]
            if len(self.lst) == 1 and len(last_klc.lst) == 1:
                # 首根KLC没有先验方向（原实现恒为UP）：用次根相对首根的位置初始化。
                # 次根高点更高，或高点持平且低点不更低 -> 向上合并；否则向下。
                # 对非包含情形该推断与 test_combine 的几何结论一致；对包含情形
                # 决定首个合并KLC取高高(UP)还是低低(DOWN)，避免与真实走势相反。
                first_klu = last_klc.lst[0]
                last_klc.set_dir(KLINE_DIR.UP if klu.high > first_klu.high or
                                 (klu.high == first_klu.high and klu.low >= first_klu.low)
                                 else KLINE_DIR.DOWN)
            _dir = last_klc.try_add(klu)
            if _dir != KLINE_DIR.COMBINE:  # 不需要合并K线
                self.lst.append(CKLine(klu, idx=len(self.lst), _dir=_dir))
                if len(self.lst) >= 3:
                    self.lst[-2].update_fx(self.lst[-3], self.lst[-1])
                if self.bi_list.update_bi(self.lst[-2], self.lst[-1], self.step_calculation) and self.step_calculation:
                    self.cal_seg_and_zs()
            elif self.step_calculation and self.bi_list.try_add_virtual_bi(self.lst[-1], need_del_end=True):  # 这里的必要性参见issue#175
                self.cal_seg_and_zs()

    def klu_iter(self, klc_begin_idx=0):
        if klc_begin_idx < 0:
            # islice 不支持负索引，换算成等价正索引（与原切片语义一致）
            klc_begin_idx = max(len(self.lst) + klc_begin_idx, 0)
        for klc in itertools.islice(self.lst, klc_begin_idx, None):
            yield from klc.lst


def cal_seg(bi_list, seg_list: CSegListComm, last_sure_seg_start_bi_idx):
    seg_list.update(bi_list)

    if len(seg_list) == 0:
        for bi in bi_list:
            bi.set_seg_idx(0)
        return -1

    cur_seg: CSeg = seg_list[-1]

    bi_idx = len(bi_list) - 1
    while bi_idx >= 0:
        bi = bi_list[bi_idx]
        if bi.seg_idx is not None and bi.idx < last_sure_seg_start_bi_idx:
            break
        if bi.idx > cur_seg.end_bi.idx:
            bi.set_seg_idx(cur_seg.idx+1)
            bi_idx -= 1
            continue
        while bi.idx < cur_seg.start_bi.idx:
            # bi 早于当前 seg 覆盖范围：持续回退直到被某个 seg 覆盖。
            # 正常数据下首 seg 恒从 bi0 开始（含 split_first_seg），因此回退到底属于异常，显式抛错
            cur_seg = cur_seg.pre
            if cur_seg is None:
                raise CChanException(
                    f"bi idx={bi.idx} 未被任何线段覆盖（首线段start_bi.idx={seg_list[0].start_bi.idx}）",
                    ErrCode.COMMON_ERROR)
        bi.set_seg_idx(cur_seg.idx)
        bi_idx -= 1

    last_sure_seg_start_bi_idx = -1
    seg = seg_list[-1]
    while seg:
        if seg.is_sure:
            last_sure_seg_start_bi_idx = seg.start_bi.idx
            break
        seg = seg.pre
    return last_sure_seg_start_bi_idx


def update_zs_in_seg(bi_list, seg_list, zs_list):
    sure_seg_cnt = 0
    seg_idx = len(seg_list) - 1
    while seg_idx >= 0:
        seg = seg_list[seg_idx]
        if seg.ele_inside_is_sure:
            break
        if seg.is_sure:
            sure_seg_cnt += 1
        seg.clear_zs_lst()
        _zs_idx = len(zs_list) - 1
        while _zs_idx >= 0:
            zs = zs_list[_zs_idx]
            if zs.end.idx < seg.start_bi.get_begin_klu().idx:
                break
            if zs.is_inside(seg):
                seg.add_zs(zs)
            if zs.begin_bi.idx <= 0:
                # 首笔起点没有“进中枢的笔”；正常构造路径（CZSList.add_to_free_lst）已禁止
                # 此类中枢，此处防御显式抛错——不能用 assert（-O 下 bi_list[-1] 静默污染为最新笔）
                raise CChanException(
                    f"中枢起点为首个bi(begin_bi.idx={zs.begin_bi.idx})，无前笔可作为bi_in",
                    ErrCode.COMMON_ERROR)
            zs.set_bi_in(bi_list[zs.begin_bi.idx-1])
            if zs.end_bi.idx+1 < len(bi_list):
                zs.set_bi_out(bi_list[zs.end_bi.idx+1])
            zs.set_bi_lst(list(bi_list[zs.begin_bi.idx:zs.end_bi.idx+1]))
            _zs_idx -= 1

        if sure_seg_cnt > 2:
            if not seg.ele_inside_is_sure:
                seg.ele_inside_is_sure = True
        seg_idx -= 1
