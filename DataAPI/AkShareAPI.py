import akshare as ak
import pandas as pd
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
        raise Exception(f"unknown time column from akshare:{inp}")

class CAkShare(CCommonStockApi):
    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ):
        super(CAkShare, self).__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        # AkShare uses 6-digit code. Need to strip sh/sz prefix if present
        symbol = self.code.split('.')[-1]
        
        period = self.__convert_type()
        adjust = "qfq" if self.autype == AUTYPE.QFQ else "hfq" if self.autype == AUTYPE.HFQ else ""
        
        start_date = self.begin_date.replace("-", "") if self.begin_date else "19900101"
        end_date = self.end_date.replace("-", "") if self.end_date else "20500101"
        
        try:
            if kltype_lt_day(self.k_type):
                # Minute data
                # period: '1', '5', '15', '30', '60'
                df = ak.stock_zh_a_hist_min_em(symbol=symbol, start_date=start_date, end_date=end_date, period=period, adjust=adjust)
                # Columns: 时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额, ...
                # Standardize to: time, open, close, high, low, volume, amount
                rename_map = {
                    "时间": DATA_FIELD.FIELD_TIME,
                    "开盘": DATA_FIELD.FIELD_OPEN,
                    "收盘": DATA_FIELD.FIELD_CLOSE,
                    "最高": DATA_FIELD.FIELD_HIGH,
                    "最低": DATA_FIELD.FIELD_LOW,
                    "成交量": DATA_FIELD.FIELD_VOLUME,
                    "成交额": DATA_FIELD.FIELD_TURNOVER,
                }
            else:
                # Daily data
                # period: 'daily', 'weekly', 'monthly'
                p_map = {'d': 'daily', 'w': 'weekly', 'm': 'monthly'}
                p = p_map.get(period, 'daily')
                df = ak.stock_zh_a_hist(symbol=symbol, period=p, start_date=start_date, end_date=end_date, adjust=adjust)
                rename_map = {
                    "日期": DATA_FIELD.FIELD_TIME,
                    "开盘": DATA_FIELD.FIELD_OPEN,
                    "收盘": DATA_FIELD.FIELD_CLOSE,
                    "最高": DATA_FIELD.FIELD_HIGH,
                    "最低": DATA_FIELD.FIELD_LOW,
                    "成交量": DATA_FIELD.FIELD_VOLUME,
                    "成交额": DATA_FIELD.FIELD_TURNOVER,
                    "换手率": DATA_FIELD.FIELD_TURNRATE,
                }
            
            df = df.rename(columns=rename_map)
            
            columns = [
                DATA_FIELD.FIELD_TIME,
                DATA_FIELD.FIELD_OPEN,
                DATA_FIELD.FIELD_HIGH,
                DATA_FIELD.FIELD_LOW,
                DATA_FIELD.FIELD_CLOSE,
                DATA_FIELD.FIELD_VOLUME,
                DATA_FIELD.FIELD_TURNOVER
            ]
            if not kltype_lt_day(self.k_type):
                columns.append(DATA_FIELD.FIELD_TURNRATE)
                
            last_date = None
            for _, row in df.iterrows():
                data = [str(row[c]) for c in columns]
                # Try to capture last date
                # data[0] is time
                try:
                    dt_str = str(data[0])
                    if len(dt_str) >= 10:
                        current_dt = datetime.datetime.strptime(dt_str[:10], "%Y-%m-%d")
                        if last_date is None or current_dt > last_date:
                            last_date = current_dt
                except:
                    pass
                    
                yield CKLine_Unit(create_item_dict(data, columns))
                
            # Check for Real-time tick (Call Auction or Trading Session or missing today)
            now = datetime.datetime.now()
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
                    # Use stock_zh_a_spot_em for real-time data
                    # Note: This fetches all stocks, which might be slow but reliable
                    spot_df = ak.stock_zh_a_spot_em()
                    # Filter by code
                    # AkShare code in spot is 6 digits
                    spot_row = spot_df[spot_df['代码'] == symbol]
                    
                    if not spot_row.empty:
                        # Columns: 序号, 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, 成交额, 振幅, 最高, 最低, 今开, 昨收, 量比, 换手率, 市盈率-动态, 市净率
                        # Map to our columns
                        # Time is not in spot_df, use now
                        row_data = spot_row.iloc[0]
                        current_price = row_data['最新价']
                        
                        if current_price and str(current_price) != 'nan':
                            tick_time = now.strftime("%Y-%m-%d %H:%M:%S")
                            
                            # Double check time to avoid dup
                            tick_dt = datetime.datetime.strptime(tick_time, "%Y-%m-%d %H:%M:%S")
                            
                            if last_date is None or tick_dt.date() > last_date.date():
                                data = [
                                    tick_time,
                                    str(row_data['今开']), # Open
                                    str(row_data['最高']), # High
                                    str(row_data['最低']), # Low
                                    str(current_price),  # Close
                                    str(row_data['成交量']), # Volume
                                    str(row_data['成交额'])  # Turnover
                                ]
                                
                                # If values are nan/None, use current_price
                                for i in range(1, 5):
                                    if data[i] == 'nan' or data[i] == 'None':
                                        data[i] = str(current_price)
                                        
                                yield CKLine_Unit(create_item_dict(data, columns))
                             
                except Exception as ex:
                    print(f"AkShare Real-time Tick Error: {ex}")

        except Exception as e:
            print(f"AkShare Error: {e}")
            raise e

    def SetBasciInfo(self):
        # AkShare basic info is separate, we might skip or implement if needed
        self.name = self.code
        self.is_stock = True

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass

    def __convert_type(self):
        _dict = {
            KL_TYPE.K_DAY: 'd',
            KL_TYPE.K_WEEK: 'w',
            KL_TYPE.K_MON: 'm',
            KL_TYPE.K_1M: '1',
            KL_TYPE.K_5M: '5',
            KL_TYPE.K_15M: '15',
            KL_TYPE.K_30M: '30',
            KL_TYPE.K_60M: '60',
        }
        return _dict.get(self.k_type, 'd')
