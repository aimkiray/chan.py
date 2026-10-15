from .CEnum import BI_DIR, KL_TYPE
from .ChanException import CChanException, ErrCode


def kltype_lt_day(_type: KL_TYPE):
    return _type.value < KL_TYPE.K_DAY.value


def kltype_lte_day(_type: KL_TYPE):
    return _type.value <= KL_TYPE.K_DAY.value


def check_kltype_order(type_list):
    if len(type_list) == 0:
        raise CChanException("lv_list不能为空", ErrCode.PARA_ERROR)
    last_lv = type_list[0].value
    for kl_type in type_list[1:]:
        if kl_type.value >= last_lv:
            raise CChanException("lv_list的顺序必须从大级别到小级别", ErrCode.PARA_ERROR)
        last_lv = kl_type.value


def revert_bi_dir(dir):
    return BI_DIR.DOWN if dir == BI_DIR.UP else BI_DIR.UP


def has_overlap(l1, h1, l2, h2, equal=False):
    return h2 >= l1 and h1 >= l2 if equal else h2 > l1 and h1 > l2


def str2float(s):
    # 不做前置类型白名单：np.int64/np.float32/Decimal 等 Number 类型
    # float() 本可正确解析，白名单会把它们静默归 0.0（回归）；
    # None/非法串仍走 except 归 0.0，语义与基线一致
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_inf(v):
    if isinstance(v, float):
        if v == float("inf"):
            v = 'float("inf")'
        if v == float("-inf"):
            v = 'float("-inf")'
    return v
