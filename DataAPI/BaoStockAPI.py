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
        return CTime(year, month, day, hour, minute, auto=False)
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
            # 1. Get from DB
            db_data = db_mgr.get_data(self.code, frequency, adjust_flag, start_date=self.begin_date, end_date=self.end_date)
            
            first_db_date = db_data[0].date if db_data else None
            last_db_date = db_data[-1].date if db_data else None
            
            # Check if DB data covers the start of our request
            # If DB starts significantly later than request, we should refetch all to ensure continuity.
            # e.g. Request 2020-01-01, DB has 2025-01-01.
            need_reload_all = False
            
            if self.begin_date:
                req_start = self.begin_date.replace("-", "").replace("/", "")[:8]
                if first_db_date:
                    db_start = first_db_date.replace("-", "").replace("/", "")[:8]
                    # Allow small gap (e.g. holidays) - but here we just check strictly or with simple logic
                    # If db_start is significantly larger than req_start, reload.
                    # Simple string comparison: if db_start > req_start, we might be missing data.
                    # To avoid reloading on simple holidays (req=Jan 1st, db=Jan 2nd), we could add logic,
                    # but reloading once is safer than missing data.
                    if db_start > req_start:
                        # Check if the gap is large (e.g. > 7 days) to avoid reloading for small holiday gaps
                        # Converting to datetime for diff
                        try:
                            import datetime
                            d1 = datetime.datetime.strptime(req_start, "%Y%m%d")
                            d2 = datetime.datetime.strptime(db_start, "%Y%m%d")
                            if (d2 - d1).days > 10:
                                need_reload_all = True
                        except:
                            need_reload_all = True
            else:
                # If no begin_date specified, and we have some data, we assume we want all history?
                # But usually begin_date is set. If not, and we have data, maybe we are fine?
                pass
                
            if need_reload_all:
                db_data = []
                last_db_date = None
                first_db_date = None
            
            for item in db_data:
                yield CKLine_Unit(self._db_item_to_dict(item))
                
            # 2. Check if we need to fetch from Baostock
            import datetime
            # Use Beijing Time (UTC+8) for today calculation to avoid timezone issues
            today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
            
            if self.end_date and last_db_date and last_db_date >= self.end_date:
                return
            
            # Optimization: If we already have data up to today, and since Baostock updates daily data EOD, we can skip
            # ONLY for daily/weekly/monthly data. Minute data updates intraday.
            if frequency in ['d', 'w', 'm']:
                last_db_date_str = last_db_date
                if last_db_date and len(last_db_date) > 10 and '-' not in last_db_date:
                    last_db_date_str = f"{last_db_date[:4]}-{last_db_date[4:6]}-{last_db_date[6:8]}"
                
                if last_db_date_str and last_db_date_str >= today:
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
            
            # Store the last date from DB for duplicate checking
            db_last_date = last_db_date
            
            # Track the latest date we have seen (DB or BS)
            final_last_date_str = last_db_date

            while rs.error_code == '0' and rs.next():
                row_data = rs.get_row_data()
                row_date = row_data[0] # date or time is always first
                
                # Filter duplicates from DB
                if db_last_date and row_date <= db_last_date:
                    continue

                # Update tracker
                final_last_date_str = row_date

                # Create DB object and add to list for saving
                db_obj = self._create_db_obj(row_data, fields, self.code, frequency, adjust_flag)
                new_data_list.append(db_obj)

                yield CKLine_Unit(create_item_dict(row_data, field_list))
            
            # Recalculate last date from all sources (DB + Baostock) to decide on Real-time
            
            # Parse final_last_date_str to datetime object
            final_last_date = None
            if final_last_date_str:
                 try:
                     # Baostock date format is usually YYYY-MM-DD or YYYYMMDD...
                     # But our parse_time_column handles it.
                     # Let's reuse parse_time_column to get CTime, then to datetime
                     ctime_obj = parse_time_column(final_last_date_str)
                     final_last_date = datetime.datetime(ctime_obj.year, ctime_obj.month, ctime_obj.day)
                 except:
                     pass

            # Save new data
            if new_data_list:
                db_mgr.save_data(new_data_list)

            # Check for Real-time tick (Call Auction or Trading Session or missing today)
            # Use AkShare as fallback since Baostock doesn't support real-time
            import datetime
            now = datetime.datetime.now()
            
            should_fetch_tick = False
            if now.weekday() < 5:
                if final_last_date is None:
                    should_fetch_tick = True
                else:
                    if final_last_date.date() < now.date():
                        should_fetch_tick = True
            
            if should_fetch_tick:
                try:
                    import akshare as ak
                    symbol = self.code.split('.')[-1]
                    
                    if self.k_type == KL_TYPE.K_DAY:
                        # Use stock_zh_a_hist for single stock which is much faster than spot_em (full market)
                        today_str = now.strftime("%Y%m%d")
                        spot_df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=today_str, end_date=today_str, adjust="qfq")
                        
                        if not spot_df.empty:
                            row_data = spot_df.iloc[0]
                            current_price = row_data['收盘']
                            
                            if current_price and str(current_price) != 'nan':
                                tick_time = now.strftime("%Y-%m-%d %H:%M:%S")
                                # Map AKShare columns to our internal names
                                data_map = {
                                    'time': tick_time,
                                    'date': tick_time,
                                    'open': str(row_data['开盘']),
                                    'high': str(row_data['最高']),
                                    'low': str(row_data['最低']),
                                    'close': str(current_price),
                                    'volume': str(row_data['成交量']),
                                    'amount': str(row_data['成交额']),
                                    'turn': str(row_data['换手率']) if '换手率' in row_data else '0'
                                }
                                
                                # Fill nan
                                for k in ['open', 'high', 'low']:
                                    if data_map[k] == 'nan' or data_map[k] == 'None': data_map[k] = str(current_price)
                                
                                # Double check time to avoid dup
                                tick_dt = datetime.datetime.strptime(tick_time, "%Y-%m-%d %H:%M:%S")
                                
                                if final_last_date is None or tick_dt.date() > final_last_date.date():
                                    # Construct list based on fields
                                    data_list = []
                                    for f in fields.split(','):
                                        data_list.append(data_map.get(f, '0'))
                                        
                                    yield CKLine_Unit(create_item_dict(data_list, field_list))
                    
                    elif kltype_lt_day(self.k_type):
                        # Minute data fallback
                        period = frequency
                        adjust = "qfq" if self.autype == AUTYPE.QFQ else "hfq" if self.autype == AUTYPE.HFQ else ""
                        today_str = now.strftime("%Y%m%d")
                        
                        df = ak.stock_zh_a_hist_min_em(symbol=symbol, start_date=today_str, end_date=today_str, period=period, adjust=adjust)
                        
                        if not df.empty:
                            rename_map = {
                                "时间": "time",
                                "开盘": "open",
                                "收盘": "close",
                                "最高": "high",
                                "最低": "low",
                                "成交量": "volume",
                                "成交额": "amount",
                            }
                            df = df.rename(columns=rename_map)
                            
                            for _, row in df.iterrows():
                                time_str = str(row['time'])
                                # AkShare time: "2023-10-27 09:35:00"
                                dt = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                                
                                if final_last_date is None or dt > final_last_date:
                                    data = {
                                        DATA_FIELD.FIELD_TIME: CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute),
                                        DATA_FIELD.FIELD_OPEN: float(row['open']),
                                        DATA_FIELD.FIELD_HIGH: float(row['high']),
                                        DATA_FIELD.FIELD_LOW: float(row['low']),
                                        DATA_FIELD.FIELD_CLOSE: float(row['close']),
                                        DATA_FIELD.FIELD_VOLUME: float(row['volume']),
                                        DATA_FIELD.FIELD_TURNOVER: float(row['amount']),
                                    }
                                    yield CKLine_Unit(data)

                except Exception as ex:
                    print(f"Baostock Real-time Tick (via AkShare) Error: {ex}")

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
        # Keep connection alive for performance and stability
        pass
        # if cls.is_connect:
        #     bs.logout()
        #     cls.is_connect = None

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
        return _dict[self.k_type]
