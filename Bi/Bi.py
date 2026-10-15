from typing import List, Optional

from Common.cache import make_cache
from Common.CEnum import BI_DIR, BI_TYPE, DATA_FIELD, FX_TYPE, MACD_ALGO
from Common.ChanException import CChanException, ErrCode
from KLine.KLine import CKLine
from KLine.KLine_Unit import CKLine_Unit


class CBi:
    def __init__(self, begin_klc: CKLine, end_klc: CKLine, idx: int, is_sure: bool):
        self.__dir = None
        self.__idx = idx
        self.__type = BI_TYPE.STRICT

        self.set(begin_klc, end_klc)

        self.__is_sure = is_sure
        self.__used_to_be_sure = is_sure
        self.__sure_end: List[CKLine] = []

        self.__seg_idx: Optional[int] = None

        from Seg.Seg import CSeg
        self.parent_seg: Optional[CSeg[CBi]] = None  # 在哪个线段里面

        from BuySellPoint.BS_Point import CBS_Point
        self.bsp: Optional[CBS_Point] = None  # 尾部是不是买卖点

        self.next: Optional[CBi] = None
        self.pre: Optional[CBi] = None

    def clean_cache(self):
        self._memoize_cache = {}

    @property
    def begin_klc(self): return self.__begin_klc

    @property
    def end_klc(self): return self.__end_klc

    @property
    def dir(self): return self.__dir

    @property
    def idx(self): return self.__idx

    @property
    def type(self): return self.__type

    @property
    def is_sure(self): return self.__is_sure

    @property
    def used_to_be_sure(self): return self.__used_to_be_sure

    @property
    def is_used_to_be_sure(self): return self.is_sure or self.used_to_be_sure

    @property
    def sure_end(self): return self.__sure_end

    @property
    def klc_lst(self):
        klc = self.begin_klc
        while True:
            yield klc
            klc = klc.next
            if not klc or klc.idx > self.end_klc.idx:
                break

    @property
    def klc_lst_re(self):
        klc = self.end_klc
        while True:
            yield klc
            klc = klc.pre
            if not klc or klc.idx < self.begin_klc.idx:
                break

    @property
    def seg_idx(self): return self.__seg_idx

    def set_seg_idx(self, idx):
        self.__seg_idx = idx

    def __str__(self):
        return f"{self.dir}|{self.begin_klc} ~ {self.end_klc}"

    def check(self):
        # 业务不变量校验不使用 assert（-O 下会被剥离），非法笔必须显式失败
        _ok = self.begin_klc.high > self.end_klc.low if self.is_down() \
            else self.begin_klc.low < self.end_klc.high
        if not _ok:
            raise CChanException(f"{self.idx}:{self.begin_klc[0].time}~{self.end_klc[-1].time}笔的方向和收尾位置不一致!", ErrCode.BI_ERR)

    def set(self, begin_klc: CKLine, end_klc: CKLine):
        self.__begin_klc: CKLine = begin_klc
        self.__end_klc: CKLine = end_klc
        if begin_klc.fx == FX_TYPE.BOTTOM:
            self.__dir = BI_DIR.UP
        elif begin_klc.fx == FX_TYPE.TOP:
            self.__dir = BI_DIR.DOWN
        else:
            raise CChanException("ERROR DIRECTION when creating bi", ErrCode.BI_ERR)
        self.check()
        self.clean_cache()

    @make_cache
    def get_begin_val(self):
        return self.begin_klc.low if self.is_up() else self.begin_klc.high

    @make_cache
    def get_end_val(self):
        return self.end_klc.high if self.is_up() else self.end_klc.low

    @make_cache
    def get_begin_klu(self) -> CKLine_Unit:
        if self.is_up():
            return self.begin_klc.get_peak_klu(is_high=False)
        else:
            return self.begin_klc.get_peak_klu(is_high=True)

    @make_cache
    def get_end_klu(self) -> CKLine_Unit:
        if self.is_up():
            return self.end_klc.get_peak_klu(is_high=True)
        else:
            return self.end_klc.get_peak_klu(is_high=False)

    @make_cache
    def amp(self):
        return abs(self.get_end_val() - self.get_begin_val())

    @make_cache
    def get_klu_cnt(self):
        return self.get_end_klu().idx - self.get_begin_klu().idx + 1

    @make_cache
    def get_klc_cnt(self):
        if self.end_klc.idx != self.get_end_klu().klc.idx or \
           self.begin_klc.idx != self.get_begin_klu().klc.idx:
            raise CChanException(f"bi idx={self.idx} 端点klc与峰元klu所属klc不一致", ErrCode.BI_ERR)
        return self.end_klc.idx - self.begin_klc.idx + 1

    @make_cache
    def _high(self):
        return self.end_klc.high if self.is_up() else self.begin_klc.high

    @make_cache
    def _low(self):
        return self.begin_klc.low if self.is_up() else self.end_klc.low

    @make_cache
    def _mid(self):
        return (self._high() + self._low()) / 2  # 笔的中位价

    @make_cache
    def is_down(self):
        return self.dir == BI_DIR.DOWN

    @make_cache
    def is_up(self):
        return self.dir == BI_DIR.UP

    def update_virtual_end(self, new_klc: CKLine):
        if self.__is_sure:
            self.append_sure_end(self.end_klc)
        self.update_new_end(new_klc)
        self.__used_to_be_sure = self.__is_sure or self.__used_to_be_sure
        self.__is_sure = False

    def restore_from_virtual_end(self, sure_end: CKLine):
        self.__is_sure = True
        self.__used_to_be_sure = True
        self.update_new_end(new_klc=sure_end)
        self.__sure_end = []

    def append_sure_end(self, klc: CKLine):
        self.__sure_end.append(klc)

    def update_new_end(self, new_klc: CKLine):
        self.__end_klc = new_klc
        self.check()
        self.clean_cache()

    def cal_macd_metric(self, macd_algo, is_reverse):
        if macd_algo == MACD_ALGO.AREA:
            return self.Cal_MACD_half(is_reverse)
        elif macd_algo == MACD_ALGO.PEAK:
            return self.Cal_MACD_peak()
        elif macd_algo == MACD_ALGO.FULL_AREA:
            return self.Cal_MACD_area()
        elif macd_algo == MACD_ALGO.DIFF:
            return self.Cal_MACD_diff()
        elif macd_algo == MACD_ALGO.SLOPE:
            return self.Cal_MACD_slope()
        elif macd_algo == MACD_ALGO.AMP:
            return self.Cal_MACD_amp()
        elif macd_algo == MACD_ALGO.AMOUNT:
            return self.Cal_MACD_trade_metric(DATA_FIELD.FIELD_TURNOVER, cal_avg=False)
        elif macd_algo == MACD_ALGO.VOLUMN:
            return self.Cal_MACD_trade_metric(DATA_FIELD.FIELD_VOLUME, cal_avg=False)
        elif macd_algo == MACD_ALGO.VOLUMN_AVG:
            return self.Cal_MACD_trade_metric(DATA_FIELD.FIELD_VOLUME, cal_avg=True)
        elif macd_algo == MACD_ALGO.AMOUNT_AVG:
            return self.Cal_MACD_trade_metric(DATA_FIELD.FIELD_TURNOVER, cal_avg=True)
        elif macd_algo == MACD_ALGO.TURNRATE_AVG:
            return self.Cal_MACD_trade_metric(DATA_FIELD.FIELD_TURNRATE, cal_avg=True)
        elif macd_algo == MACD_ALGO.RSI:
            return self.Cal_Rsi()
        else:
            raise CChanException(f"unsupport macd_algo={macd_algo}, should be one of area/full_area/peak/diff/slope/amp", ErrCode.PARA_ERROR)

    @make_cache
    def Cal_Rsi(self):
        # klu.rsi 是可选属性（仅在 RSI 指标模型启用时由 set_metric 写入）
        rsi_lst: List[float] = []
        for klc in self.klc_lst:
            rsi_lst.extend(klu.rsi for klu in klc.lst if hasattr(klu, "rsi"))
        if len(rsi_lst) == 0:
            raise CChanException("Cal_Rsi 需要配置 RSI 指标模型（klu.rsi 不存在）", ErrCode.CONFIG_ERROR)
        return 10000.0/(min(rsi_lst)+1e-7) if self.is_down() else max(rsi_lst)

    @make_cache
    def Cal_MACD_area(self):
        _s = 1e-7
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        for klc in self.klc_lst:
            for klu in klc.lst:
                if klu.idx < begin_klu.idx or klu.idx > end_klu.idx:
                    continue
                if (self.is_down() and klu.macd.macd < 0) or (self.is_up() and klu.macd.macd > 0):
                    _s += abs(klu.macd.macd)
        return _s

    @make_cache
    def Cal_MACD_peak(self):
        peak = 1e-7
        # 与 Cal_MACD_area 口径一致：只统计 [begin_klu, end_klu] 区间内的 klu，
        # 端点合并K线内越界的 klu 不计入
        begin_klu_idx = self.get_begin_klu().idx
        end_klu_idx = self.get_end_klu().idx
        for klc in self.klc_lst:
            for klu in klc.lst:
                if klu.idx < begin_klu_idx or klu.idx > end_klu_idx:
                    continue
                if abs(klu.macd.macd) > peak:
                    if self.is_down() and klu.macd.macd < 0:
                        peak = abs(klu.macd.macd)
                    elif self.is_up() and klu.macd.macd > 0:
                        peak = abs(klu.macd.macd)
        return peak

    def Cal_MACD_half(self, is_reverse):
        if is_reverse:
            return self.Cal_MACD_half_reverse()
        else:
            return self.Cal_MACD_half_obverse()

    @make_cache
    def Cal_MACD_half_obverse(self):
        _s = 1e-7
        begin_klu = self.get_begin_klu()
        end_klu_idx = self.get_end_klu().idx
        peak_macd = begin_klu.macd.macd
        for klc in self.klc_lst:
            for klu in klc.lst:
                if klu.idx < begin_klu.idx:
                    continue
                if klu.idx > end_klu_idx:
                    break  # 越过笔终点峰元，终止（与 area 口径一致）
                if klu.macd.macd*peak_macd > 0:
                    _s += abs(klu.macd.macd)
                else:
                    break
            else:  # 没有被break，继续找写一个KLC
                continue
            break
        return _s

    @make_cache
    def Cal_MACD_half_reverse(self):
        _s = 1e-7
        begin_klu = self.get_end_klu()
        start_klu_idx = self.get_begin_klu().idx
        peak_macd = begin_klu.macd.macd
        for klc in self.klc_lst_re:
            for klu in klc[::-1]:
                if klu.idx > begin_klu.idx:
                    continue
                if klu.idx < start_klu_idx:
                    break  # 越过笔起点峰元，终止（与 area 口径一致）
                if klu.macd.macd*peak_macd > 0:
                    _s += abs(klu.macd.macd)
                else:
                    break
            else:  # 没有被break，继续找写一个KLC
                continue
            break
        return _s

    @make_cache
    def Cal_MACD_diff(self):
        """
        macd红绿柱最大值最小值之差
        """
        _max, _min = float("-inf"), float("inf")
        begin_klu_idx = self.get_begin_klu().idx
        end_klu_idx = self.get_end_klu().idx
        for klc in self.klc_lst:
            for klu in klc.lst:
                if klu.idx < begin_klu_idx or klu.idx > end_klu_idx:
                    continue
                macd = klu.macd.macd
                if macd > _max:
                    _max = macd
                if macd < _min:
                    _min = macd
        return _max-_min

    @make_cache
    def Cal_MACD_slope(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        if self.is_up():
            return (end_klu.high - begin_klu.low)/end_klu.high/(end_klu.idx - begin_klu.idx + 1)
        else:
            return (begin_klu.high - end_klu.low)/begin_klu.high/(end_klu.idx - begin_klu.idx + 1)

    @make_cache
    def Cal_MACD_amp(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        if self.is_down():
            return (begin_klu.high-end_klu.low)/begin_klu.high
        else:
            return (end_klu.high-begin_klu.low)/begin_klu.low

    def Cal_MACD_trade_metric(self, metric: str, cal_avg=False) -> float:
        # make_cache 只支持零参方法，这里按 (metric, cal_avg) 手工缓存到同一约定缓存槽
        cache_key = f"Cal_MACD_trade_metric:{metric}:{cal_avg}"
        cache = getattr(self, "_memoize_cache", None)
        if cache is None:
            cache = self._memoize_cache = {}
        if cache_key in cache:
            return cache[cache_key]
        _s = 0.0
        _cnt = 0
        for klc in self.klc_lst:
            for klu in klc.lst:
                metric_res = klu.trade_info.metric[metric]
                if metric_res is None:
                    continue  # 缺失该指标的 klu 跳过，而不是整笔静默归零
                _s += metric_res
                _cnt += 1
        res = (_s / _cnt if _cnt else 0.0) if cal_avg else _s
        cache[cache_key] = res
        return res
