import threading

import baostock as bs
import baostock.common.contants as bs_cons

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.func_util import kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi, create_item_dict


def GetColumnNameFromFieldList(fields: str):
    _dict = {
        "time": DATA_FIELD.FIELD_TIME,
        "date": DATA_FIELD.FIELD_TIME,
        "open": DATA_FIELD.FIELD_OPEN,
        "high": DATA_FIELD.FIELD_HIGH,
        "low": DATA_FIELD.FIELD_LOW,
        "close": DATA_FIELD.FIELD_CLOSE,
        "volume": DATA_FIELD.FIELD_VOLUME,
        "amount": DATA_FIELD.FIELD_TURNOVER,
        "turn": DATA_FIELD.FIELD_TURNRATE,
    }
    return [_dict[x] for x in fields.split(",")]


# 登录态失效类错误码：命中即认为全局 socket 会话已坏，允许复位后重登重试一次
_BS_SESSION_ERR_CODES = frozenset({
    bs_cons.BSERR_NO_LOGIN,               # 未登录/被服务端踢下线
    bs_cons.BSERR_RECVCONNECTION_CLOSED,  # 接收时连接断开
    bs_cons.BSERR_RECVSOCK_FAIL,          # 网络接收错误
    bs_cons.BSERR_SENDSOCK_FAIL,          # 网络发送失败
})


class CBaoStock(CCommonStockApi):
    is_connect = False

    # SetBasciInfo 需要 query_stock_basic 网络往返，推迟到首次取数，
    # 避免每个级别构造实例时都先打一次基本信息查询
    DEFER_BASIC_INFO = True

    # baostock SDK 全局共享单条 socket（socketutil.send_msg 直接读写
    # context.default_socket，login() 每次重建并顶替它），同进程并发查询必须
    # 串行化。用 RLock 而非 Lock：Chan.load_iterator 会挂起上级生成器转而消费
    # 下级生成器（同一根 socket 上的多级加载嵌套获取同一把锁），普通 Lock 在
    # 该模式下必然死锁；RLock 对同线程重入放行、跨线程串行。
    _conn_lock = threading.RLock()

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ, asset_type="STOCK"):
        super(CBaoStock, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def get_kl_data(self):
        self._ensure_basic_info()  # name/is_stock 惰性填充，内含网络查询
        # 天级别以上才有详细交易信息
        if kltype_lt_day(self.k_type):
            if not self.is_stock:
                raise CChanException("没有获取到数据，注意指数是没有分钟级别数据的！", ErrCode.SRC_DATA_NOT_FOUND)
            fields = "time,open,high,low,close,volume,amount"
        else:
            fields = "date,open,high,low,close,volume,amount,turn"

        autype_dict = {AUTYPE.QFQ: "2", AUTYPE.HFQ: "1", AUTYPE.NONE: "3"}
        adjust_flag = autype_dict[self.autype]
        frequency = self.__convert_type()
        field_list = GetColumnNameFromFieldList(fields)
        cls = self.__class__

        # 初始查询 + 全部翻页迭代都在锁内：ResultData.next() 翻页直接读写全局 socket
        with cls._conn_lock:
            rs = cls._query_with_relogin(
                lambda: bs.query_history_k_data_plus(
                    code=self.code,
                    fields=fields,
                    start_date=self.begin_date,
                    end_date=self.end_date,
                    frequency=frequency,
                    adjustflag=adjust_flag,
                )
            )
            if rs is None:
                # baostock 对非法日期格式 print 后裸 return（security/history.py:65-67），
                # 返回值是 None 而非 ResultData，取 .error_code 会 AttributeError
                raise CChanException(
                    f"baostock query_history_k_data_plus 返回 None（参数非法，常见为日期格式），"
                    f"code={self.code} begin={self.begin_date} end={self.end_date}",
                    ErrCode.PARA_ERROR)
            if rs.error_code != '0':
                raise CChanException(f"baostock 查询失败: {rs.error_msg}", ErrCode.COMMON_ERROR)

            while rs.error_code == '0' and rs.next():
                yield CKLine_Unit(create_item_dict(rs.get_row_data(), field_list, src_name="baostock"))

            if rs.error_code != '0':
                # 翻页途中收到服务端错误：next() 先更新 error_code 再返回 False
                if rs.error_code in _BS_SESSION_ERR_CODES:
                    cls.is_connect = False  # 会话已坏，下次 do_init 重登
                raise CChanException(
                    f"baostock 翻页中途失败(error_code={rs.error_code}, {rs.error_msg})，K线数据被截断",
                    ErrCode.COMMON_ERROR)
            if len(rs.data) > 0 and rs.cur_row_num >= len(rs.data):
                # rs.next() 翻页网络失败时静默 return False 且不更新 error_code
                # （data/resultset.py:84-85），此时 rs.data 停留在已耗尽的最后一页；
                # 而正常取尽时迭代会再取回一页空数据（setData→data==[]、cur_row_num=0）。
                # 因此“循环退出后 data 仍非空”即翻页静默截断的证据，必须报错而非
                # 把部分数据当全量交给下游。
                cls.is_connect = False
                raise CChanException(
                    "baostock 翻页网络失败（next() 静默返回 False），K线数据被截断",
                    ErrCode.COMMON_ERROR)

    def SetBasciInfo(self):
        rs = self.__class__._query_with_relogin(lambda: bs.query_stock_basic(code=self.code))
        if rs is None or rs.error_code != '0':
            raise CChanException(
                f"baostock query_stock_basic 失败: {rs.error_msg if rs is not None else '返回 None'}",
                ErrCode.COMMON_ERROR)
        if not rs.next():
            # 空结果集时 get_row_data() 返回 []，直接解包会 ValueError；先 next() 判空
            raise CChanException(f"baostock query_stock_basic 无记录: {self.code}", ErrCode.SRC_DATA_NOT_FOUND)
        row = rs.get_row_data()
        if len(row) < 6:
            raise CChanException(
                f"baostock query_stock_basic 行字段不足({len(row)}<6): {self.code}",
                ErrCode.SRC_DATA_FORMAT_ERROR)
        _code, code_name, _ipo_date, _out_date, stock_type, _status = row[:6]
        self.name = code_name
        self.is_stock = (stock_type == '1')

    @classmethod
    def _query_with_relogin(cls, query):
        """执行一次 baostock 查询；命中会话失效类错误码时复位登录态、重登并重试一次。"""
        with cls._conn_lock:
            rs = query()
            if rs is not None and getattr(rs, "error_code", None) in _BS_SESSION_ERR_CODES:
                cls.is_connect = False
                cls.do_init()  # 重新 login；某些触发路径并不保证 do_init 刚跑过
                rs = query()
            return rs

    @classmethod
    def do_init(cls):
        with cls._conn_lock:
            if not cls.is_connect:
                lg = bs.login()
                # login() 恒返回 ResultData 对象（未定义 __bool__，恒为真），
                # 必须检查 error_code=='0' 才算登录成功
                cls.is_connect = bool(lg is not None and getattr(lg, "error_code", None) == '0')
                if not cls.is_connect:
                    raise CChanException(
                        f"baostock login failed: {getattr(lg, 'error_msg', lg)}",
                        ErrCode.COMMON_ERROR)

    @classmethod
    def do_close(cls):
        # 保持登录态（延续原实现 "Keep connection alive" 的意图，并补足失效自愈）：
        # baostock 会话是进程级全局 socket，Chan.load()/fetch_stock_data 的 finally
        # 每趟都会调本方法；若在此登出，每趟加载都要重付一次 TCP+认证往返，且
        # 多线程/嵌套加载时会踢掉其他在途迭代的全局会话。
        # 服务端踢会话/断连的失效场景由 _query_with_relogin 的 BSERR_NO_LOGIN 等
        # 错误码自愈重登覆盖；翻页网络错误时也会在出错分支复位 is_connect。
        pass

    def __convert_type(self):
        _dict = {
            KL_TYPE.K_DAY: 'd',
            KL_TYPE.K_WEEK: 'w',
            KL_TYPE.K_MON: 'm',
            KL_TYPE.K_5M: '5',
            KL_TYPE.K_15M: '15',
            KL_TYPE.K_30M: '30',
            KL_TYPE.K_60M: '60',
            KL_TYPE.K_1M: '1',  # Attempt to support 1 minute
        }
        if self.k_type not in _dict:
            raise CChanException(f"baostock 数据源不支持级别 {self.k_type}", ErrCode.SRC_DATA_TYPE_ERR)
        return _dict[self.k_type]
