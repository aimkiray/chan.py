from Common.CEnum import LEFT_SEG_METHOD
from Common.ChanException import CChanException, ErrCode

SEG_ALGO_LST = ("chan", "1+1", "break")


class CSegConfig:
    def __init__(self, seg_algo="chan", left_method="peak"):
        # 构造期白名单校验：非法 seg_algo 不再延迟到 KLine_List 工厂才报错
        if seg_algo not in SEG_ALGO_LST:
            raise CChanException(f"unknown seg_algo={seg_algo}, should be one of {SEG_ALGO_LST}", ErrCode.PARA_ERROR)
        self.seg_algo = seg_algo
        if left_method == "all":
            self.left_method = LEFT_SEG_METHOD.ALL
        elif left_method == "peak":
            self.left_method = LEFT_SEG_METHOD.PEAK
        else:
            raise CChanException(f"unknown left_seg_method={left_method}", ErrCode.PARA_ERROR)
