import functools
from datetime import datetime


@functools.total_ordering
class CTime:
    """K线时间值对象。

    比较/相等/哈希均以内部时间戳 `self.ts` 为基准（与 __gt__/__ge__ 一致）。

    精度契约：
    - 比较与哈希精度 = 秒级时间戳（含 auto 规则下的 23:59 语义）；
    - `to_str()/__str__()` 输出：无时分秒 -> ``YYYY/MM/DD``；
      秒为 0 -> ``YYYY/MM/DD HH:MM``；秒非 0 -> ``YYYY/MM/DD HH:MM:SS``。
      即秒级K线（K_3S~K_30S）不同的秒值不会产生字符串碰撞。

    `auto=True` 约定（保留）：小时和分钟都为 0 时视为"当天结束"（23:59），
    用于让日线时间排在当日分钟线之后；比较时生效，但 to_str 仍显示 00:00。
    """
    def __init__(self, year, month, day, hour, minute, second=0, auto=True):
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self.minute = minute
        self.second = second
        self.auto = auto  # 自适应对天的理解
        self.set_timestamp()  # set self.ts

    def __str__(self):
        return self.to_str()

    def to_str(self):
        if self.hour == 0 and self.minute == 0 and self.second == 0:
            return f"{self.year:04}/{self.month:02}/{self.day:02}"
        elif self.second == 0:
            return f"{self.year:04}/{self.month:02}/{self.day:02} {self.hour:02}:{self.minute:02}"
        else:
            return f"{self.year:04}/{self.month:02}/{self.day:02} {self.hour:02}:{self.minute:02}:{self.second:02}"

    def toDateStr(self, splt=''):
        return f"{self.year:04}{splt}{self.month:02}{splt}{self.day:02}"

    def toDate(self):
        return CTime(self.year, self.month, self.day, 0, 0, auto=False)

    def set_timestamp(self):
        if self.hour == 0 and self.minute == 0 and self.auto:
            date = datetime(self.year, self.month, self.day, 23, 59, self.second)
        else:
            date = datetime(self.year, self.month, self.day, self.hour, self.minute, self.second)
        self.ts = date.timestamp()

    def __eq__(self, t2):
        if not isinstance(t2, CTime):
            return NotImplemented
        return self.ts == t2.ts

    def __gt__(self, t2):
        return self.ts > t2.ts

    def __ge__(self, t2):
        return self.ts >= t2.ts

    def __hash__(self):
        return hash(self.ts)
