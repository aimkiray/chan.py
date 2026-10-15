from Common.CEnum import TREND_TYPE
from Common.ChanException import CChanException, ErrCode


class CTrendModel:
    def __init__(self, trend_type: TREND_TYPE, T: int):
        # T<=0 时 arr[-0:]==arr[0:]，窗口永不裁剪且失去滚动语义
        if not isinstance(T, int) or T <= 0:
            raise CChanException(f"CTrendModel window T must be positive int, got {T!r}", ErrCode.PARA_ERROR)
        self.T = T
        self.arr = []
        self.type = trend_type

    def add(self, value):  # 返回 float（MEAN）或入参元素类型（MAX/MIN）
        self.arr.append(value)
        if len(self.arr) > self.T:
            self.arr = self.arr[-self.T:]
        if self.type == TREND_TYPE.MEAN:
            return sum(self.arr)/len(self.arr)
        elif self.type == TREND_TYPE.MAX:
            return max(self.arr)
        elif self.type == TREND_TYPE.MIN:
            return min(self.arr)
        else:
            raise CChanException(f"Unknown trendModel Type = {self.type}", ErrCode.PARA_ERROR)
