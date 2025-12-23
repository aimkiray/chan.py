import jqdatasdk as jq
import pandas as pd
import os
import datetime
from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.CTime import CTime
from Common.func_util import str2float, kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

def create_item_dict(data, column_name):
    for i in range(len(data)):
        data[i] = parse_time_column(data[i]) if column_name[i] == DATA_FIELD.FIELD_TIME else str2float(data[i])
    return dict(zip(column_name, data))

def parse_time_column(inp):
    # 2021-09-13 10:00:00
    inp = str(inp)
    if len(inp) == 19:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        hour = int(inp[11:13])
        minute = int(inp[14:16])
        return CTime(year, month, day, hour, minute)
    # 2021-09-13
    elif len(inp) == 10:
        year = int(inp[:4])
        month = int(inp[5:7])
        day = int(inp[8:10])
        return CTime(year, month, day, 0, 0, auto=False)
    else:
        # Try to parse pandas timestamp
        try:
            ts = pd.Timestamp(inp)
            return CTime(ts.year, ts.month, ts.day, ts.hour, ts.minute)
        except:
            raise Exception(f"unknown time column from jqdata:{inp}")

class CJQData(CCommonStockApi):
    _is_authenticated = False

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ):
        super(CJQData, self).__init__(code, k_type, begin_date, end_date, autype)
        self.authenticate()

    @classmethod
    def authenticate(cls):
        if cls._is_authenticated:
            return
        
        # Try to get credentials from env vars
        username = os.environ.get("JQDATA_USER", "")
        password = os.environ.get("JQDATA_PASSWORD", "")
        
        if not username or not password:
             print("Warning: JQDATA_USER or JQDATA_PASSWORD not found in env vars.")
             # For demo purposes, we might not be able to auth.
             # But if user has them, they should set them.
             return

        try:
            jq.auth(username, password)
            cls._is_authenticated = True
            print("JQData Authenticated successfully.")
        except Exception as e:
            print(f"JQData Authentication failed: {e}")

    def get_kl_data(self):
        # JQData code format: 000001.XSHE, 600000.XSHG
        symbol = self.code
        
        # Handle sz./sh. prefix (from normalize_code)
        if symbol.startswith("sz."):
            symbol = symbol[3:] + ".XSHE"
        elif symbol.startswith("sh."):
            symbol = symbol[3:] + ".XSHG"
        elif symbol.endswith(".sz"):
            symbol = symbol.replace(".sz", ".XSHE")
        elif symbol.endswith(".sh"):
            symbol = symbol.replace(".sh", ".XSHG")
        elif not symbol.endswith(".XSHE") and not symbol.endswith(".XSHG"):
             # Guess
             if symbol.startswith("6"):
                 symbol = f"{symbol}.XSHG"
             else:
                 symbol = f"{symbol}.XSHE"
        
        unit = self.__convert_type()
        fq = "pre" if self.autype == AUTYPE.QFQ else "post" if self.autype == AUTYPE.HFQ else None
        
        # JQData get_price
        # fields=['open', 'close', 'low', 'high', 'volume', 'money']
        # money is turnover
        
        # Hardcode date range limit for demo/restricted account
        # User reported limit: 2024-09-14 to 2025-09-21
        # We enforce start_date to be at least 2024-09-14 and end_date at most 2025-09-21
        
        start_date = self.begin_date if self.begin_date else "2024-09-14"
        
        # Ensure we compare dates properly (handle potential time strings)
        start_comp = str(start_date)[:10]
        if start_comp < "2024-09-14":
             print(f"Warning: JQData start_date {start_date} is before allowed limit 2024-09-14. Adjusting.")
             start_date = "2024-09-14"
             
        end_date = self.end_date if self.end_date else "2025-09-21"
        end_comp = str(end_date)[:10]
        # 2025-09-21 is the limit.
        if end_comp > "2025-09-21": 
             print(f"Warning: JQData end_date {end_date} is after allowed limit 2025-09-21. Adjusting to 2025-09-21.")
             end_date = "2025-09-21"
        
        print(f"JQData Request: {symbol}, start={start_date}, end={end_date}, freq={unit}")

        try:
            df = jq.get_price(symbol, start_date=start_date, end_date=end_date, frequency=unit, fields=['open', 'close', 'high', 'low', 'volume', 'money'], skip_paused=True, fq=fq)
            
            # Reset index to get date/time as column if it's in index
            df = df.reset_index()
            # Index column name is usually 'index' or 'time' depending on version
            # But usually index is DatetimeIndex
            
            columns = [
                DATA_FIELD.FIELD_TIME,
                DATA_FIELD.FIELD_OPEN,
                DATA_FIELD.FIELD_HIGH,
                DATA_FIELD.FIELD_LOW,
                DATA_FIELD.FIELD_CLOSE,
                DATA_FIELD.FIELD_VOLUME,
                DATA_FIELD.FIELD_TURNOVER
            ]
            
            # Map JQ columns
            # df columns: index/time, open, close, high, low, volume, money
            
            last_date = None
            for _, row in df.iterrows():
                # row[0] is time
                row_time = row.iloc[0]
                # Ensure row_time is comparable for last_date logic
                if isinstance(row_time, pd.Timestamp):
                     current_dt = row_time.to_pydatetime()
                else:
                     # try parse
                     try:
                         current_dt = pd.to_datetime(row_time).to_pydatetime()
                     except:
                         current_dt = None
                         
                if current_dt:
                    if last_date is None or current_dt > last_date:
                        last_date = current_dt

                data = [
                    str(row_time), # Time
                    str(row['open']),
                    str(row['high']),
                    str(row['low']),
                    str(row['close']),
                    str(row['volume']),
                    str(row['money'])
                ]
                yield CKLine_Unit(create_item_dict(data, columns))

            # Check for Real-time tick (Call Auction or Trading Session)
            # Logic: If today is a trading day and we haven't yielded today's data (or it's incomplete), fetch tick.
            # JQData's get_price might include today's data if end_date covers it.
            # But just in case, or for call auction, we check.
            
            now = datetime.datetime.now()
            is_trading_time = (now.weekday() < 5) and (
                (now.hour == 9 and now.minute >= 15) or 
                (now.hour > 9 and now.hour < 15) or 
                (now.hour == 15 and now.minute <= 30)
            )
            
            # If we are in trading time, OR if it's past trading time but today's data is missing from history
            # For simplicity, if it's a weekday and we don't have today's data in last_date, try to fetch tick.
            
            should_fetch_tick = False
            if now.weekday() < 5:
                if last_date is None:
                    should_fetch_tick = True
                else:
                    # Check if last_date is today
                    if last_date.date() < now.date():
                        should_fetch_tick = True
            
            if should_fetch_tick and self.k_type == KL_TYPE.K_DAY:
                try:
                    tick = jq.get_current_tick(symbol)
                    if isinstance(tick, list) and len(tick) > 0:
                        tick = tick[0]
                    
                    if hasattr(tick, 'current') and tick.current > 0:
                         tick_time = tick.datetime
                         # Double check time to avoid dup (though should be covered by last_date check)
                         tick_dt = datetime.datetime.strptime(str(tick_time)[:19], "%Y-%m-%d %H:%M:%S")
                         
                         if last_date is None or tick_dt.date() > last_date.date():
                             data = [
                                str(tick_time),
                                str(tick.current),
                                str(tick.high if tick.high > 0 else tick.current),
                                str(tick.low if tick.low > 0 and tick.low < float('inf') else tick.current),
                                str(tick.current),
                                str(tick.volume),
                                str(tick.money)
                             ]
                             yield CKLine_Unit(create_item_dict(data, columns))
                except Exception as ex:
                    print(f"Real-time Tick Error: {ex}")
                
        except Exception as e:
            print(f"JQData Error: {e}")
            raise e

    def SetBasciInfo(self):
        self.name = self.code
        self.is_stock = True

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass

    def __convert_type(self):
        # JQData frequencies: '1m', '5m', '15m', '30m', '60m', '120m', '1d', '1w', '1M'
        _dict = {
            KL_TYPE.K_DAY: '1d',
            KL_TYPE.K_WEEK: '1w',
            KL_TYPE.K_MON: '1M',
            KL_TYPE.K_1M: '1m',
            KL_TYPE.K_5M: '5m',
            KL_TYPE.K_15M: '15m',
            KL_TYPE.K_30M: '30m',
            KL_TYPE.K_60M: '60m',
        }
        return _dict.get(self.k_type, '1d')
