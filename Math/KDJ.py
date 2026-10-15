from Common.ChanException import CChanException, ErrCode


class KDJ_Item:
    def __init__(self, k, d, j):
        self.k = k
        self.d = d
        self.j = j


class KDJ:
    def __init__(self, period: int = 9):
        if not isinstance(period, int) or period < 1:
            raise CChanException(f"KDJ period must be int >= 1, got {period!r}", ErrCode.PARA_ERROR)
        self.arr = []
        self.period = period
        self.pre_kdj = KDJ_Item(50, 50, 50)
        # 与 K/D 初值 50 对齐：一字板（hn==ln）时 RSV 未定义，沿用上期 RSV，
        # 使 K/D/J 保持原水平而非被 rsv=0 错误地持续拉向 0
        self.pre_rsv = 50.0

    def add(self, high, low, close) -> KDJ_Item:
        self.arr.append({
            'high': high,
            'low': low,
        })
        if len(self.arr) > self.period:
            self.arr.pop(0)

        hn = max([x['high'] for x in self.arr])
        ln = min([x['low'] for x in self.arr])
        cn = close
        rsv = 100 * (cn - ln) / (hn - ln) if hn != ln else self.pre_rsv
        self.pre_rsv = rsv

        cur_k = 2 / 3 * self.pre_kdj.k + 1 / 3 * rsv
        cur_d = 2 / 3 * self.pre_kdj.d + 1 / 3 * cur_k
        cur_j = 3 * cur_k - 2 * cur_d
        cur_kdj = KDJ_Item(cur_k, cur_d, cur_j)
        self.pre_kdj = cur_kdj

        return cur_kdj
