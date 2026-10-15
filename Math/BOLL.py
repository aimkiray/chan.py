import math

from Common.ChanException import CChanException, ErrCode


def _truncate(x):
    # 只防护精确 0 的两处退化：
    # 1) theta==0（窗口内常数序列）→ 保持极小非零带宽使 UP>DOWN 严格成立，
    #    避免下游把 (UP-DOWN) 当分母时炸掉；
    # 2) DOWN==0 → 保护将 DOWN 当分母/基准的调用。
    # 价格本应为正（上游 KLU.check / PRICE_BELOW_ZERO 把关），
    # 对"价格极大导致 ±2e-7 被浮点精度吞掉"这类极端不做进一步处理。
    return x if x != 0 else 1e-7


class BOLL_Metric:
    def __init__(self, ma, theta):
        self.theta = _truncate(theta)
        self.UP = ma + 2*theta
        self.DOWN = _truncate(ma - 2*theta)
        self.MID = ma


class BollModel:
    def __init__(self, N=20):
        if not isinstance(N, int) or N <= 1:
            raise CChanException(f"BollModel window N must be int > 1, got {N!r}", ErrCode.PARA_ERROR)
        self.N = N
        self.arr = []

    def add(self, value) -> BOLL_Metric:
        self.arr.append(value)
        if len(self.arr) > self.N:
            self.arr = self.arr[-self.N:]
        ma = sum(self.arr)/len(self.arr)
        theta = math.sqrt(sum((x-ma)**2 for x in self.arr) / len(self.arr))
        return BOLL_Metric(ma, theta)
