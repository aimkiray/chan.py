from typing import List

from Common.ChanException import CChanException, ErrCode


class CMACD_item:
    def __init__(self, fast_ema, slow_ema, DIF, DEA):
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.DIF = DIF
        self.DEA = DEA
        self.macd = 2 * (DIF - DEA)


class CMACD:
    def __init__(self, fastperiod=12, slowperiod=26, signalperiod=9):
        for name, p in (("fastperiod", fastperiod), ("slowperiod", slowperiod), ("signalperiod", signalperiod)):
            if not isinstance(p, int) or p < 1:
                raise CChanException(f"CMACD {name} must be int >= 1, got {p!r}", ErrCode.PARA_ERROR)
        # macd_info 保留全量历史：add() 只读 [-1]，但序列留作回溯/调试用途；
        # 每根 K 线约 5 个 float，会话级数据量下 O(n) 内存可接受，故不裁剪
        self.macd_info: List[CMACD_item] = []
        self.fastperiod = fastperiod
        self.slowperiod = slowperiod
        self.signalperiod = signalperiod

    def add(self, value) -> CMACD_item:
        if not self.macd_info:
            self.macd_info.append(CMACD_item(fast_ema=value, slow_ema=value, DIF=0, DEA=0))
        else:
            _fast_ema = (2 * value + (self.fastperiod - 1) * self.macd_info[-1].fast_ema) / (self.fastperiod + 1)
            _slow_ema = (2 * value + (self.slowperiod - 1) * self.macd_info[-1].slow_ema) / (self.slowperiod + 1)
            _dif = _fast_ema - _slow_ema
            _dea = (2 * _dif + (self.signalperiod - 1) * self.macd_info[-1].DEA) / (self.signalperiod + 1)
            self.macd_info.append(CMACD_item(fast_ema=_fast_ema, slow_ema=_slow_ema, DIF=_dif, DEA=_dea))
        return self.macd_info[-1]
