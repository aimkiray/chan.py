import baostock as bs

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.CTime import CTime
from Common.func_util import kltype_lt_day, str2float
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi
from .SqliteCache import DBManager, StockKLine


def create_item_dict(data, column_name):
    for i in range(len(data)):
        data[i] = parse_time_column(data[i]) if i == 0 else str2float(data[i])
    return dict(zip(column_name, data))


def parse_time_column(inp):
    # 20210902113000000
    # 2021-09-13
    if len(inp) == 10:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        hour = minute = 0
    elif len(inp) == 17:
        year = int(inp[:4])
        month = int(inp[4:6])
        day = int(inp[6:8])
        hour = int(inp[8:10])
        minute = int(inp[10:12])
    elif len(inp) == 19:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        hour = int(inp[11:13])
        minute = int(inp[14:16])
    else:
        raise Exception(f"unknown time column from baostock:{inp}")
    return CTime(year, month, day, hour, minute)


def GetColumnNameFromFieldList(fileds: str):
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
    return [_dict[x] for x in fileds.split(",")]


class CBaoStock(CCommonStockApi):
    is_connect = None

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ):
        super(CBaoStock, self).__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        # 天级别以上才有详细交易信息
        if kltype_lt_day(self.k_type):
            if not self.is_stock:
                raise Exception("没有获取到数据，注意指数是没有分钟级别数据的！")
            fields = "time,open,high,low,close"
        else:
            fields = "date,open,high,low,close,volume,amount,turn"
        
        autype_dict = {AUTYPE.QFQ: "2", AUTYPE.HFQ: "1", AUTYPE.NONE: "3"}
        adjust_flag = autype_dict[self.autype]
        frequency = self.__convert_type()
        
        # --- Cache Logic ---
        db_mgr = DBManager()
        try:
            # 1. Yield from DB
            db_data = db_mgr.get_data(self.code, frequency, adjust_flag, start_date=self.begin_date, end_date=self.end_date)
            last_db_date = None
            
            for item in db_data:
                yield CKLine_Unit(self._db_item_to_dict(item))
                last_db_date = item.date
                
            # 2. Check if we need to fetch from Baostock
            if self.end_date and last_db_date and last_db_date >= self.end_date:
                return

            bs_start_date = self.begin_date
            if last_db_date:
                # Baostock start_date format: YYYY-MM-DD
                if len(last_db_date) > 10:
                    bs_start_date = f"{last_db_date[:4]}-{last_db_date[4:6]}-{last_db_date[6:8]}"
                else:
                    bs_start_date = last_db_date

            rs = bs.query_history_k_data_plus(
                code=self.code,
                fields=fields,
                start_date=bs_start_date,
                end_date=self.end_date,
                frequency=frequency,
                adjustflag=adjust_flag,
            )
            if rs.error_code != '0':
                raise Exception(rs.error_msg)
                
            new_data_list = []
            field_list = GetColumnNameFromFieldList(fields)
            
            while rs.error_code == '0' and rs.next():
                row_data = rs.get_row_data()
                row_date = row_data[0] # date or time is always first
                
                # Skip if already in DB
                if last_db_date and row_date <= last_db_date:
                    continue
                    
                # Create DB object
                kline_obj = self._create_db_obj(row_data, fields, self.code, frequency, adjust_flag)
                new_data_list.append(kline_obj)
                
                yield CKLine_Unit(create_item_dict(row_data, field_list))
                
            # Save new data
            if new_data_list:
                db_mgr.save_data(new_data_list)
        finally:
            db_mgr.close()

    def _db_item_to_dict(self, item):
        d = {
            DATA_FIELD.FIELD_TIME: parse_time_column(item.date),
            DATA_FIELD.FIELD_OPEN: item.open,
            DATA_FIELD.FIELD_HIGH: item.high,
            DATA_FIELD.FIELD_LOW: item.low,
            DATA_FIELD.FIELD_CLOSE: item.close,
        }
        if item.volume is not None: d[DATA_FIELD.FIELD_VOLUME] = item.volume
        if item.amount is not None: d[DATA_FIELD.FIELD_TURNOVER] = item.amount
        if item.turn is not None: d[DATA_FIELD.FIELD_TURNRATE] = item.turn
        return d

    def _create_db_obj(self, row_data, fields_str, code, frequency, adjust_flag):
        field_names = fields_str.split(',')
        data_map = dict(zip(field_names, row_data))
        
        obj = StockKLine(
            code=code,
            date=data_map.get('date', data_map.get('time')),
            frequency=frequency,
            adjust_flag=adjust_flag,
            open=str2float(data_map['open']),
            high=str2float(data_map['high']),
            low=str2float(data_map['low']),
            close=str2float(data_map['close']),
        )
        
        if 'volume' in data_map and data_map['volume']:
            obj.volume = str2float(data_map['volume'])
        if 'amount' in data_map and data_map['amount']:
            obj.amount = str2float(data_map['amount'])
        if 'turn' in data_map and data_map['turn']:
            obj.turn = str2float(data_map['turn'])
            
        return obj

    def SetBasciInfo(self):
        rs = bs.query_stock_basic(code=self.code)
        if rs.error_code != '0':
            raise Exception(rs.error_msg)
        code, code_name, ipoDate, outDate, stock_type, status = rs.get_row_data()
        self.name = code_name
        self.is_stock = (stock_type == '1')

    @classmethod
    def do_init(cls):
        if not cls.is_connect:
            cls.is_connect = bs.login()

    @classmethod
    def do_close(cls):
        if cls.is_connect:
            bs.logout()
            cls.is_connect = None

    def __convert_type(self):
        _dict = {
            KL_TYPE.K_DAY: 'd',
            KL_TYPE.K_WEEK: 'w',
            KL_TYPE.K_MON: 'm',
            KL_TYPE.K_5M: '5',
            KL_TYPE.K_15M: '15',
            KL_TYPE.K_30M: '30',
            KL_TYPE.K_60M: '60',
        }
        return _dict[self.k_type]
