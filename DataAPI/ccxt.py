"""CCXT 数字货币数据源。

模块名注意：本文件与三方库 ``ccxt`` 同名。模块内 ``import ccxt`` 为绝对导入，
取 site-packages 中的三方库而非本文件（Python3 无隐式相对导入）；但请勿把
``DataAPI/`` 目录加入 sys.path 或以顶层模块方式 ``import ccxt``/``python ccxt.py``，
否则本文件会遮蔽真正的三方库。

ccxt 为可选依赖，未安装时本模块仍可被导入/编译；只在实际取数时抛
CChanException(SRC_DATA_TYPE_ERR)，不会拖垮 ``import DataAPI.ccxt``。
"""
import datetime
import threading

try:
    import ccxt
    _CCXT_IMPORT_ERR = None
except ImportError as _e:  # 三方库未安装
    ccxt = None
    _CCXT_IMPORT_ERR = _e

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi, create_item_dict


def GetColumnNameFromFieldList(fields: str):
    _dict = {
        "time": DATA_FIELD.FIELD_TIME,
        "open": DATA_FIELD.FIELD_OPEN,
        "high": DATA_FIELD.FIELD_HIGH,
        "low": DATA_FIELD.FIELD_LOW,
        "close": DATA_FIELD.FIELD_CLOSE,
        "volume": DATA_FIELD.FIELD_VOLUME,
    }
    return [_dict[x] for x in fields.split(",")]


# fetch_ohlcv 分页参数；(fmt, 是否纯日期粒度)
_MS_BOUND_FORMATS = (
    ("%Y-%m-%d %H:%M:%S", False),
    ("%Y-%m-%d %H:%M", False),
    ("%Y-%m-%d", True),
    ("%Y%m%d", True),
)


def _bound_to_ms(raw, is_end):
    """把 begin/end_date 解析为 UTC 毫秒时间戳；None/空串 -> None。

    纯日期粒度：begin 取当日 00:00 UTC；end 顺延一天（配合 ``ts > end_ms``
    过滤，等价于 end_date 当天闭区间）。显式带时刻的边界按字面时刻。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt, day_only in _MS_BOUND_FORMATS:
        try:
            dt = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if is_end and day_only:
            dt += datetime.timedelta(days=1)
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    raise CChanException(f"ccxt 无法解析{'end' if is_end else 'begin'}_date: {raw!r}", ErrCode.PARA_ERROR)


class CCXT(CCommonStockApi):
    _exchange = None

    # ccxt 同步 exchange 底层是 requests.Session，非线程安全；
    # 用 RLock 而非 Lock：多级加载时上级生成器被挂起、同线程嵌套消费下级
    # 生成器会重入同一把锁，普通 Lock 必死锁。
    _exchange_lock = threading.RLock()

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ, asset_type="STOCK"):
        super(CCXT, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def get_kl_data(self):
        self._ensure_basic_info()
        fields = "time,open,high,low,close,volume"
        field_list = GetColumnNameFromFieldList(fields)
        timeframe = self.__convert_type()
        since_ms = _bound_to_ms(self.begin_date, is_end=False)
        end_ms = _bound_to_ms(self.end_date, is_end=True)

        cls = self.__class__
        with cls._exchange_lock:
            exchange = cls._get_exchange()
            if not getattr(exchange, "has", {}).get("fetchOHLCV"):
                raise CChanException(
                    f"exchange {getattr(exchange, 'id', '?')} 不支持 fetch_ohlcv",
                    ErrCode.SRC_DATA_TYPE_ERR)

            page_limit = 1000
            # begin_date 为空时从 epoch 起拉（多数交易所对 since=0 会钳到最早可用K线），
            # 否则 get_kl_data 只会拿到默认 ~500 根最近数据 —— 静默截断
            cursor = since_ms if since_ms is not None else 0
            done = False
            prev_page_last_ts = None
            emitted_ts = None
            while not done:
                batch = exchange.fetch_ohlcv(self.code, timeframe, since=cursor, limit=page_limit)
                if not batch:
                    break
                for item in batch:
                    ts = int(item[0])
                    if end_ms is not None and ts > end_ms:
                        done = True
                        break
                    if emitted_ts is not None and ts <= emitted_ts:
                        continue  # 交易所返回重叠页时去重
                    # ccxt 毫秒时间戳是 UTC：显式 tzinfo=utc 的 fromtimestamp，
                    # 而非本地时区裸 fromtimestamp（utcfromtimestamp 自 py3.12 起 deprecated）
                    time_str = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    item_data = [time_str, item[1], item[2], item[3], item[4], item[5]]
                    emitted_ts = ts
                    yield CKLine_Unit(create_item_dict(item_data, field_list, src_name="ccxt"), autofix=True)
                if done or len(batch) < page_limit:
                    break
                page_last_ts = int(batch[-1][0])
                if prev_page_last_ts is not None and page_last_ts <= prev_page_last_ts:
                    break  # 交易所忽略 since 等无推进场景，防死循环
                prev_page_last_ts = page_last_ts
                cursor = page_last_ts + 1

    def SetBasciInfo(self):
        self.name = str(self.code)
        self.is_stock = False  # 数字货币对，非股票

    @classmethod
    def _get_exchange(cls):
        if ccxt is None:
            raise CChanException(
                f"未安装三方库 ccxt（{_CCXT_IMPORT_ERR}），data_src=ccxt 不可用；pip install ccxt",
                ErrCode.SRC_DATA_TYPE_ERR)
        with cls._exchange_lock:
            if cls._exchange is None:
                cls._exchange = ccxt.binance()
            return cls._exchange

    @classmethod
    def do_init(cls):
        if ccxt is not None:
            cls._get_exchange()  # 预热复用同一 exchange 实例（load_markets 等惰性资源只建一次）

    @classmethod
    def do_close(cls):
        exchange, cls._exchange = cls._exchange, None
        if exchange is None:
            return
        try:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def __convert_type(self):
        _dict = {
            KL_TYPE.K_DAY: '1d',
            KL_TYPE.K_WEEK: '1w',
            KL_TYPE.K_MON: '1M',
            KL_TYPE.K_5M: '5m',
            KL_TYPE.K_15M: '15m',
            KL_TYPE.K_30M: '30m',
            KL_TYPE.K_60M: '1h',
        }
        if self.k_type not in _dict:
            raise CChanException(f"ccxt 数据源不支持级别 {self.k_type}", ErrCode.SRC_DATA_TYPE_ERR)
        return _dict[self.k_type]
