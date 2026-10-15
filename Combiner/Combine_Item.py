from Common.ChanException import CChanException, ErrCode

# Bi/Seg 与本模块存在循环依赖（Bi -> KLine.KLine -> KLine_Combiner -> Combine_Item），
# 故类型在首次使用时延迟导入并缓存到 _ITEM_TYPES，避免热路径上每次实例化都走三次 import。
_ITEM_TYPES = None


def _get_item_types():
    global _ITEM_TYPES
    if _ITEM_TYPES is None:
        from Bi.Bi import CBi
        from KLine.KLine_Unit import CKLine_Unit
        from Seg.Seg import CSeg
        _ITEM_TYPES = (CBi, CKLine_Unit, CSeg)
    return _ITEM_TYPES


class CCombine_Item:
    def __init__(self, item):
        CBi, CKLine_Unit, CSeg = _get_item_types()
        if isinstance(item, CBi):
            self.time_begin = item.begin_klc.idx
            self.time_end = item.end_klc.idx
            self.high = item._high()
            self.low = item._low()
        elif isinstance(item, CKLine_Unit):
            self.time_begin = item.time
            self.time_end = item.time
            self.high = item.high
            self.low = item.low
        elif isinstance(item, CSeg):
            self.time_begin = item.start_bi.begin_klc.idx
            self.time_end = item.end_bi.end_klc.idx
            self.high = item._high()
            self.low = item._low()
        else:
            raise CChanException(f"{type(item)} is unsupport sub class of CCombine_Item", ErrCode.COMMON_ERROR)
