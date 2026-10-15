import abc
from typing import Iterable

from Common.CEnum import DATA_FIELD
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from Common.func_util import str2float
from KLine.KLine_Unit import CKLine_Unit


def parse_time_column(inp, src_name="data", auto=False):
    """将数据源的原始时间列解析为 CTime（原为 BaoStockAPI/csvAPI/AkShareAPI/ccxt
    四处重复实现的统一版本）。

    支持的输入格式（按长度区分）：
    - len=10  ``YYYY-MM-DD``          日级：恒按当日 00:00 计（auto=False）
    - len=17  ``YYYYMMDDHHMMSSmmm``   如 baostock 分钟线 ``20210902113000000``（取到分钟）
    - len=19  ``YYYY-MM-DD HH:MM:SS`` 标准时间戳

    ``auto`` 透传 ``CTime(auto=...)``，仅在 hour=minute=0 时影响内部 ts
    （auto=True 会把 0:00 视作当日 23:59，用于让日线排在当日分钟线之后）。
    本仓库全部数据源（BaoStock/TDX/ClickHouse/AkShare）的日级口径统一为
    00:00(auto=False)，故此处默认 auto=False；确需旧 23:59 语义的调用方
    显式传 ``auto=True``。

    解析失败抛 ``CChanException(SRC_DATA_FORMAT_ERROR)``（不再抛裸 Exception），
    ``src_name`` 用于在错误信息中标示数据来源。
    """
    s = str(inp).strip()
    try:
        if len(s) == 10:
            return CTime(int(s[:4]), int(s[5:7]), int(s[8:10]), 0, 0, auto=False)
        if len(s) == 17:
            return CTime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]), auto=auto)
        if len(s) == 19:
            return CTime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16]), auto=auto)
    except (ValueError, TypeError):
        pass
    raise CChanException(f"unknown time column from {src_name}: {inp!r}", ErrCode.SRC_DATA_FORMAT_ERROR)


def create_item_dict(data, column_name, src_name="data"):
    """把一行原始字符串数据按列名组装成 CKLine_Unit 所需的 dict：
    ``DATA_FIELD.FIELD_TIME`` 列解析为 CTime，其余列走 ``str2float``
    （非法值归 0.0，语义与原各源一致）。注意会原地改写 ``data``。

    ``column_name`` 为逐列的 DATA_FIELD 名；按列名（而非固定按下标0）识别时间列，
    兼容各源的时间列位置约定。
    """
    for i in range(len(data)):
        if column_name[i] == DATA_FIELD.FIELD_TIME:
            data[i] = parse_time_column(data[i], src_name=src_name)
        else:
            data[i] = str2float(data[i])
    return dict(zip(column_name, data))


class CCommonStockApi(abc.ABC):
    """数据源基类契约。

    - ``get_kl_data`` / ``do_init`` / ``do_close`` 为抽象方法，子类必须实现
      （此前类未继承 abc.ABC，装饰器形同虚设，缺实现也能实例化）。
    - ``SetBasciInfo`` 保留**默认空实现**而非抽象方法：基类 ``__init__`` 已把
      ``name``/``is_stock`` 置为 None，而 ``backend.chan_service.PreloadedStockAPI``
      这类"无基本信息可查"的预装载源依赖默认实现——若改抽象会使其实例化失败。
      需要名称/股票类型判定的源（BaoStock/AkShare/TDX/ClickHouse）应覆写本方法。
    - 构造期调用 ``SetBasciInfo`` 是既有对外契约（``backend get_stock_name`` 在
      构造后直接读 ``api.name``）。若某源的 ``SetBasciInfo`` 有重副作用（如
      ClickHouse 在构造期发起 DB 连接/查表），可置 ``DEFER_BASIC_INFO=True`` 把它
      推迟到首次 ``_ensure_basic_info()``/``get_kl_data()``；覆写 ``get_kl_data``
      时须在读取 ``name``/``is_stock`` 之前调用 ``self._ensure_basic_info()``。
    """

    DEFER_BASIC_INFO = False

    def __init__(self, code, k_type, begin_date, end_date, autype, asset_type="STOCK"):
        self.code = code
        self.name = None
        self.is_stock = None
        self.k_type = k_type
        self.begin_date = begin_date
        self.end_date = end_date
        self.autype = autype
        self.asset_type = asset_type
        self.warnings = []  # List to store warnings during data fetching
        self._basic_info_ready = False
        if not self.DEFER_BASIC_INFO:
            self._ensure_basic_info()

    def _ensure_basic_info(self):
        """惰性执行一次 SetBasciInfo；DEFER_BASIC_INFO=False 时构造期已完成。"""
        if not self._basic_info_ready:
            self.SetBasciInfo()
            self._basic_info_ready = True

    @abc.abstractmethod
    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        pass

    def SetBasciInfo(self):
        """填充 self.name / self.is_stock；默认空实现，契约见类 docstring。"""
        pass

    @classmethod
    @abc.abstractmethod
    def do_init(cls):
        pass

    @classmethod
    @abc.abstractmethod
    def do_close(cls):
        pass
