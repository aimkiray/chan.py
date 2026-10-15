from Common.ChanException import CChanException, ErrCode


class CZSConfig:
    def __init__(self, need_combine=True, zs_combine_mode="zs", one_bi_zs=False, zs_algo="normal"):
        if zs_combine_mode not in ("zs", "peak"):
            raise CChanException(f"unknown zs_combine_mode={zs_combine_mode}", ErrCode.PARA_ERROR)
        if zs_algo not in ("normal", "over_seg", "auto"):
            raise CChanException(f"unknown zs_algo={zs_algo}", ErrCode.PARA_ERROR)
        if zs_algo == "over_seg" and one_bi_zs:
            raise CChanException("zs_algo='over_seg' 不支持 one_bi_zs=True", ErrCode.PARA_ERROR)
        if not isinstance(need_combine, bool):
            raise CChanException(f"need_combine must be bool, got {need_combine!r}", ErrCode.PARA_ERROR)
        if not isinstance(one_bi_zs, bool):
            raise CChanException(f"one_bi_zs must be bool, got {one_bi_zs!r}", ErrCode.PARA_ERROR)
        self.need_combine = need_combine
        self.zs_combine_mode = zs_combine_mode
        self.one_bi_zs = one_bi_zs
        self.zs_algo = zs_algo
