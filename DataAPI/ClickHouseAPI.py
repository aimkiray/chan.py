import os
import datetime
from typing import Iterable
from dotenv import load_dotenv
from clickhouse_driver import Client

from Common.CEnum import AUTYPE, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

class CClickHouseAPI(CCommonStockApi):
    _client = None

    def __init__(self, code, k_type, begin_date, end_date, autype):
        super(CClickHouseAPI, self).__init__(code, k_type, begin_date, end_date, autype)

    @classmethod
    def do_init(cls):
        if cls._client is None:
            load_dotenv()
            host = os.getenv("DB_HOST", "localhost")
            port = int(os.getenv("DB_PORT", 9000))
            user = os.getenv("DB_USER", "default")
            password = os.getenv("DB_PASSWORD", "")
            database = os.getenv("DB_NAME", "stock_data")
            
            try:
                cls._client = Client(host=host, port=port, user=user, password=password, database=database)
                # Test connection
                cls._client.execute("SELECT 1")
            except Exception as e:
                print(f"ClickHouse init failed: {e}")
                cls._client = None

    @classmethod
    def do_close(cls):
        if cls._client:
            cls._client.disconnect()
            cls._client = None

    def _ensure_connection(self):
        if self._client is None:
            self.do_init()

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        self._ensure_connection()
        if not self._client:
            print("ClickHouse client not initialized")
            return []

        table_map = {
            KL_TYPE.K_1M: "stock_1min",
            KL_TYPE.K_5M: "stock_5min",
            KL_TYPE.K_15M: "stock_15min",
            KL_TYPE.K_30M: "stock_30min",
            KL_TYPE.K_60M: "stock_60min",
            KL_TYPE.K_DAY: "stock_daily",
            KL_TYPE.K_WEEK: "stock_weekly",
            KL_TYPE.K_MON: "stock_monthly",
        }
        
        if self.autype == AUTYPE.QFQ:
            if self.k_type == KL_TYPE.K_DAY:
                table_map[KL_TYPE.K_DAY] = "stock_daily_qfq"
            elif self.k_type == KL_TYPE.K_WEEK:
                table_map[KL_TYPE.K_WEEK] = "stock_weekly_qfq"
            elif self.k_type == KL_TYPE.K_MON:
                table_map[KL_TYPE.K_MON] = "stock_monthly_qfq"
        
        table = table_map.get(self.k_type)
        if not table:
            raise Exception(f"Unsupported k_type for ClickHouse: {self.k_type}")

        time_col = "datetime" if self.k_type in [KL_TYPE.K_1M, KL_TYPE.K_5M, KL_TYPE.K_15M, KL_TYPE.K_30M, KL_TYPE.K_60M] else "date"
        
        # Normalize code: remove dot for ClickHouse query
        normalized_code = self.code.lower().replace(".", "")
        
        begin_date = self.begin_date if self.begin_date else "1990-01-01"
        end_date = self.end_date if self.end_date else datetime.date.today().strftime("%Y-%m-%d")
        
        query = f"""
            SELECT {time_col}, open, high, low, close, volume, amount 
            FROM {table}
            WHERE code = %(code)s 
            AND {time_col} >= %(begin)s 
            AND {time_col} <= %(end)s
            ORDER BY {time_col} ASC
        """
        
        params = {
            'code': normalized_code,
            'begin': begin_date,
            'end': end_date
        }
        
        # print(f"DEBUG: Querying {table} with params: {params}")
        
        try:
            rows = self._client.execute(query, params)
            # print(f"DEBUG: Got {len(rows)} rows")
        except Exception as e:
            print(f"Query failed: {e}")
            return []
        
        data_list = []
        for row in rows:
            dt, _open, _high, _low, _close, _vol, _amt = row
            
            # Sanitize data: fix 0 values which cause validation errors
            if _close <= 0: continue # Skip invalid rows
            
            if _open <= 0: _open = _close
            if _high <= 0: _high = _close
            if _low <= 0: _low = _close
            
            # Ensure high is max and low is min
            _high = max(_high, _open, _close)
            _low = min(_low, _open, _close)
            
            if hasattr(dt, 'hour'):
                kl_time = CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False)
            else:
                kl_time = CTime(dt.year, dt.month, dt.day, 0, 0, auto=False)
            
            # Simple deduplication (assuming sorted input)
            if data_list and data_list[-1].time.ts == kl_time.ts:
                continue
                
            data_list.append(CKLine_Unit(
                {
                    DATA_FIELD.FIELD_TIME: kl_time,
                    DATA_FIELD.FIELD_OPEN: float(round(_open, 2)),
                    DATA_FIELD.FIELD_HIGH: float(round(_high, 2)),
                    DATA_FIELD.FIELD_LOW: float(round(_low, 2)),
                    DATA_FIELD.FIELD_CLOSE: float(round(_close, 2)),
                    DATA_FIELD.FIELD_VOLUME: float(_vol),
                    DATA_FIELD.FIELD_TURNOVER: float(_amt)
                }
            ))
            
        return data_list

    def SetBasciInfo(self):
        # Query stock_info table
        self._ensure_connection()
        if not self._client:
            return
            
        normalized_code = self.code.lower().replace(".", "")
        query = "SELECT name FROM stock_info WHERE code = %(code)s LIMIT 1"
        
        try:
            rows = self._client.execute(query, {'code': normalized_code})
            if rows:
                self.name = rows[0][0]
            else:
                self.name = self.code
        except Exception as e:
            print(f"SetBasciInfo failed: {e}")
            self.name = self.code
