from Common.CEnum import FX_CHECK_METHOD
from Common.ChanException import CChanException, ErrCode


class CBiConfig:
    def __init__(
        self,
        bi_algo="normal",
        is_strict=True,
        bi_fx_check="half",
        gap_as_kl=True,
        bi_end_is_peak=True,
        bi_allow_sub_peak=True,
    ):
        if bi_algo not in ("normal", "fx"):
            raise CChanException(f"unknown bi_algo={bi_algo}", ErrCode.PARA_ERROR)
        for _name, _val in (("is_strict", is_strict), ("gap_as_kl", gap_as_kl),
                            ("bi_end_is_peak", bi_end_is_peak),
                            ("bi_allow_sub_peak", bi_allow_sub_peak)):
            if not isinstance(_val, bool):
                raise CChanException(f"{_name} must be bool, got {_val!r}", ErrCode.PARA_ERROR)
        self.bi_algo = bi_algo
        self.is_strict = is_strict
        if bi_fx_check == "strict":
            self.bi_fx_check = FX_CHECK_METHOD.STRICT
        elif bi_fx_check == "loss":
            self.bi_fx_check = FX_CHECK_METHOD.LOSS
        elif bi_fx_check == "half":
            self.bi_fx_check = FX_CHECK_METHOD.HALF
        elif bi_fx_check == 'totally':
            self.bi_fx_check = FX_CHECK_METHOD.TOTALLY
        else:
            raise CChanException(f"unknown bi_fx_check={bi_fx_check}", ErrCode.PARA_ERROR)

        self.gap_as_kl = gap_as_kl
        self.bi_end_is_peak = bi_end_is_peak
        self.bi_allow_sub_peak = bi_allow_sub_peak
