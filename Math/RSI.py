from Common.ChanException import CChanException, ErrCode


class RSI:
    """Wilder RSI。

    输出口径（对齐 TA-Lib/pandas 主流实现）：
    - 前 period 根为预热期：返回 NaN（指标尚未定义），但内部仍累计
      up/down 均值，作为第 period+1 根起 Wilder 平滑的种子；
    - 窗口内全程无涨跌（up==down==0）时 RS=0/0 未定义，按中性返回 50.0，
      而不是把平盘读作极度超卖的 0.0；
    - down==0 且 up>0（只涨不跌）返回 100.0。
    """

    def __init__(self, period: int = 14):
        if not isinstance(period, int) or period < 2:
            raise CChanException(f"RSI period must be int >= 2, got {period!r}", ErrCode.PARA_ERROR)
        self.close_arr = []
        self.period = period
        self.diff = []
        self.up = []
        self.down = []

    def add(self, close):
        self.close_arr.append(close)
        if len(self.close_arr) == 1:
            return float("nan")  # 无 diff，预热期

        self.diff.append(self.close_arr[-1] - self.close_arr[-2])

        if len(self.diff) < self.period:
            # 预热期：以简单均值累计 Wilder 平滑种子，不输出指标值
            up_sum = sum(x for x in self.diff if x > 0)
            down_sum = sum(-x for x in self.diff if x < 0)
            self.up.append(up_sum / len(self.diff))
            self.down.append(down_sum / len(self.diff))
            return float("nan")

        if self.diff[-1] > 0:
            upval = self.diff[-1]
            downval = 0.0
        else:
            upval = 0.0
            downval = -self.diff[-1]

        self.up.append((self.up[-1] * (self.period - 1) + upval) / self.period)
        self.down.append((self.down[-1] * (self.period - 1) + downval) / self.period)

        if self.down[-1] == 0:
            return 100.0 if self.up[-1] > 0 else 50.0

        rs = self.up[-1] / self.down[-1]
        rsi = 100.0 - 100.0 / (1.0 + rs)
        return rsi
