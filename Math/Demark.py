from dataclasses import dataclass
from typing import List, Literal, Optional, TypedDict

from Common.CEnum import BI_DIR
from Common.ChanException import CChanException, ErrCode


@dataclass
class C_KL:
    idx: int
    close: float
    high: float
    low: float

    def v(self, is_close: bool, _dir: BI_DIR) -> float:
        if is_close:
            return self.close
        return self.high if _dir == BI_DIR.UP else self.low


T_DEMARK_TYPE = Literal['setup', 'countdown']


class T_DEMARK_INDEX(TypedDict):
    type: T_DEMARK_TYPE
    dir: BI_DIR
    idx: int
    series: 'CDemarkSetup'


class CDemarkIndex:
    def __init__(self):
        self.data: List[T_DEMARK_INDEX] = []

    def add(self, _dir: BI_DIR, _type: T_DEMARK_TYPE, idx: int, series: 'CDemarkSetup'):
        self.data.append({"dir": _dir, "idx": idx, "type": _type, "series": series})

    def get_setup(self) -> List[T_DEMARK_INDEX]:
        return [info for info in self.data if info['type'] == 'setup']

    def get_countdown(self) -> List[T_DEMARK_INDEX]:
        return [info for info in self.data if info['type'] == 'countdown']

    def update(self, demark_index: 'CDemarkIndex'):
        self.data.extend(demark_index.data)


class CDemarkCountdown:
    def __init__(self, _dir: BI_DIR, kl_list: List[C_KL], TDST_peak: float, config):
        self.dir = _dir
        # 入参是调用方新切的窗口且 C_KL 创建后不再修改，浅拷贝即可（原 deepcopy 冗余）
        self.kl_list: List[C_KL] = list(kl_list)
        self.idx = 0
        self.TDST_peak = TDST_peak
        self.finish = False
        self.COUNTDOWN_BIAS = config.COUNTDOWN_BIAS
        self.MAX_COUNTDOWN = config.MAX_COUNTDOWN
        self.COUNTDOWN_CMP2CLOSE = config.COUNTDOWN_CMP2CLOSE

    def update(self, kl: C_KL) -> bool:
        if self.finish:
            return False
        self.kl_list.append(kl)
        if len(self.kl_list) <= self.COUNTDOWN_BIAS:
            return False
        if self.idx == self.MAX_COUNTDOWN:
            self.finish = True
            return False
        if (self.dir == BI_DIR.DOWN and kl.high > self.TDST_peak) or (self.dir == BI_DIR.UP and kl.low < self.TDST_peak):
            self.finish = True
            return False
        if self.dir == BI_DIR.DOWN and self.kl_list[-1].close < self.kl_list[-1 - self.COUNTDOWN_BIAS].v(self.COUNTDOWN_CMP2CLOSE, self.dir):
            self.idx += 1
            return True
        if self.dir == BI_DIR.UP and self.kl_list[-1].close > self.kl_list[-1 - self.COUNTDOWN_BIAS].v(self.COUNTDOWN_CMP2CLOSE, self.dir):
            self.idx += 1
            return True
        return False


class CDemarkSetup:
    def __init__(self, _dir: BI_DIR, kl_list: List[C_KL], pre_kl: C_KL, config):
        self.dir = _dir
        # 入参是引擎新切的窗口（kl_lst[-B-1:-1]）且 C_KL 创建后不再修改，浅拷贝即可（原 deepcopy 冗余）
        self.kl_list: List[C_KL] = list(kl_list)
        self.pre_kl = pre_kl  # 跳空时用
        self.DEMARK_LEN = config.DEMARK_LEN
        self.SETUP_BIAS = config.SETUP_BIAS
        self.COUNTDOWN_BIAS = config.COUNTDOWN_BIAS
        self.MAX_COUNTDOWN = config.MAX_COUNTDOWN
        self.TIAOKONG_ST = config.TIAOKONG_ST
        self.SETUP_CMP2CLOSE = config.SETUP_CMP2CLOSE
        self.COUNTDOWN_CMP2CLOSE = config.COUNTDOWN_CMP2CLOSE
        assert len(self.kl_list) == self.SETUP_BIAS
        self.countdown: Optional[CDemarkCountdown] = None
        self.setup_finished = False
        self.idx = 0
        self.TDST_peak: Optional[float] = None

        self.last_demark_index = CDemarkIndex()  # 缓存用

    def update(self, kl: C_KL) -> CDemarkIndex:
        self.last_demark_index = CDemarkIndex()
        if not self.setup_finished:
            self.kl_list.append(kl)
            # setup 计数在条件连续满足时可越过 DEMARK_LEN 继续增长（不回收），
            # 直至条件中断才置 setup_finished；DEMARK_LEN 仅是触发 countdown 的门槛
            if self.dir == BI_DIR.DOWN:
                if self.kl_list[-1].close < self.kl_list[-1-self.SETUP_BIAS].v(self.SETUP_CMP2CLOSE, self.dir):
                    self.add_setup()
                else:
                    self.setup_finished = True
            elif self.kl_list[-1].close > self.kl_list[-1-self.SETUP_BIAS].v(self.SETUP_CMP2CLOSE, self.dir):
                self.add_setup()
            else:
                self.setup_finished = True
        if self.idx == self.DEMARK_LEN and not self.setup_finished and self.countdown is None:
            self.countdown = CDemarkCountdown(self.dir, self.kl_list[:-1], self.cal_TDST_peak(), self)
        if self.countdown is not None and self.countdown.update(kl):
            self.last_demark_index.add(self.dir, 'countdown', self.countdown.idx, self)
        return self.last_demark_index

    def add_setup(self):
        self.idx += 1
        self.last_demark_index.add(self.dir, 'setup', self.idx, self)

    def cal_TDST_peak(self) -> float:
        assert len(self.kl_list) == self.SETUP_BIAS+self.DEMARK_LEN
        arr = self.kl_list[self.SETUP_BIAS:self.SETUP_BIAS+self.DEMARK_LEN]
        assert len(arr) == self.DEMARK_LEN
        if self.dir == BI_DIR.DOWN:
            res = max(kl.high for kl in arr)
            if self.TIAOKONG_ST and arr[0].high < self.pre_kl.close:
                res = max(res, self.pre_kl.close)
        else:
            res = min(kl.low for kl in arr)
            if self.TIAOKONG_ST and arr[0].low > self.pre_kl.close:
                res = min(res, self.pre_kl.close)
        self.TDST_peak = res
        return res


class CDemarkEngine:
    def __init__(
        self,
        demark_len=9,
        setup_bias=4,
        countdown_bias=2,
        max_countdown=13,
        tiaokong_st=True,
        setup_cmp2close=True,
        countdown_cmp2close=True
    ):
        for name, p in (("demark_len", demark_len), ("setup_bias", setup_bias),
                        ("countdown_bias", countdown_bias), ("max_countdown", max_countdown)):
            if not isinstance(p, int) or p < 1:
                raise CChanException(f"CDemarkEngine {name} must be int >= 1, got {p!r}", ErrCode.PARA_ERROR)
        self.DEMARK_LEN = demark_len
        self.SETUP_BIAS = setup_bias
        self.COUNTDOWN_BIAS = countdown_bias
        self.MAX_COUNTDOWN = max_countdown
        self.TIAOKONG_ST = tiaokong_st  # 第一根跳空时是否跟前一根的close比
        self.SETUP_CMP2CLOSE = setup_cmp2close
        self.COUNTDOWN_CMP2CLOSE = countdown_cmp2close

        self.kl_lst: List[C_KL] = []
        self.series: List[CDemarkSetup] = []

    def update(self, idx: int, close: float, high: float, low: float) -> CDemarkIndex:
        self.kl_lst.append(C_KL(idx, close, high, low))
        if len(self.kl_lst) <= self.SETUP_BIAS+1:
            return CDemarkIndex()

        # SETUP_CMP2CLOSE 同时约束 setup 的触发与续计（CDemarkSetup.update 用
        # .v(SETUP_CMP2CLOSE, dir)）：True 与参照 bar 的 close 比，False 与其
        # low（DOWN）/high（UP）比；两侧规则须一致，否则会出现"触发后立刻断计"
        # 或"满足续计规则却永不触发"的割裂
        ref_kl = self.kl_lst[-1-self.SETUP_BIAS]
        if self.kl_lst[-1].close < ref_kl.v(self.SETUP_CMP2CLOSE, BI_DIR.DOWN):
            if not any(series.dir == BI_DIR.DOWN and not series.setup_finished for series in self.series):
                self.series.append(CDemarkSetup(BI_DIR.DOWN, self.kl_lst[-self.SETUP_BIAS-1:-1], self.kl_lst[-self.SETUP_BIAS-2], self))
            for series in self.series:
                if series.dir == BI_DIR.UP and series.countdown is None and not series.setup_finished:
                    series.setup_finished = True
        elif self.kl_lst[-1].close > ref_kl.v(self.SETUP_CMP2CLOSE, BI_DIR.UP):
            if not any(series.dir == BI_DIR.UP and not series.setup_finished for series in self.series):
                self.series.append(CDemarkSetup(BI_DIR.UP, self.kl_lst[-self.SETUP_BIAS-1:-1], self.kl_lst[-self.SETUP_BIAS-2], self))
            for series in self.series:
                if series.dir == BI_DIR.DOWN and series.countdown is None and not series.setup_finished:
                    series.setup_finished = True

        self.clear()
        self.update_all_series()

        result = self.cal_result()
        self.clear()
        return result

    def cal_result(self) -> CDemarkIndex:
        demark_index = CDemarkIndex()
        for series in self.series:
            demark_index.update(series.last_demark_index)
        return demark_index

    def clear(self):
        invalid_series = [series for series in self.series if series.setup_finished and series.countdown is None]
        for s in invalid_series:
            self.series.remove(s)
        invalid_series = [series for series in self.series if series.countdown is not None and series.countdown.finish]
        for s in invalid_series:
            self.series.remove(s)

    def update_all_series(self):
        """用最新一根 K 线推进所有存活 series。

        若某 series 的 setup 在本轮恰好数到 DEMARK_LEN（setup 完成、转入
        countdown 阶段），按实现约定只保留该 series、丢弃其余——一个时点
        只跟踪一个已完成 setup 的序列。
        """
        finished_setup: Optional[int] = None
        for series in self.series:
            demark_idx = series.update(self.kl_lst[-1])
            for setup_idx in demark_idx.get_setup():
                if setup_idx['idx'] == self.DEMARK_LEN:
                    # 不变量：同一根 K 线至多一个 series 完成 setup——同向未完成
                    # series 不会重复创建（见 update 的 any() 检查），反向不可能同
                    # 时延展；显式 raise 代替 assert，避免 -O 下静默保留错误序列
                    if finished_setup is not None:
                        raise CChanException("multiple demark setups finished on the same bar", ErrCode.COMMON_ERROR)
                    finished_setup = id(series)
        if finished_setup is not None:
            self.series = [series for series in self.series if id(series) == finished_setup]
