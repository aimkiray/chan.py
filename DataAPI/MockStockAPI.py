import random
import datetime
from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from .CommonStockAPI import CCommonStockApi

class CMockStock(CCommonStockApi):
    def __init__(self, code, k_type=KL_TYPE.K_1S, begin_date=None, end_date=None, autype=AUTYPE.QFQ):
        super(CMockStock, self).__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        # Generate synthetic 1s data
        # We'll generate data for the last 1 hour, or within begin_date/end_date if specified
        
        now = datetime.datetime.now()
        if self.end_date:
            end_dt = datetime.datetime.strptime(self.end_date, "%Y-%m-%d")
        else:
            end_dt = now
            
        if self.begin_date:
             start_dt = datetime.datetime.strptime(self.begin_date, "%Y-%m-%d")
        else:
             start_dt = end_dt - datetime.timedelta(hours=1)
             
        # Limit to last 2 hours max to prevent explosion
        if (end_dt - start_dt).total_seconds() > 7200:
            start_dt = end_dt - datetime.timedelta(hours=2)
            
        # Base price
        price = 10.0
        
        current = start_dt
        while current <= end_dt:
            # Skip non-trading hours? For mock, let's just generate continuous
            # Or better, simple 9:30-11:30, 13:00-15:00 logic
            if not (9 <= current.hour < 15):
                current += datetime.timedelta(seconds=1)
                continue
                
            # Random walk
            change = random.uniform(-0.02, 0.02)
            open_p = price
            close_p = price + change
            high_p = max(open_p, close_p) + random.uniform(0, 0.01)
            low_p = min(open_p, close_p) - random.uniform(0, 0.01)
            price = close_p
            
            # Volume
            volume = random.randint(100, 10000)
            
            item_dict = {
                DATA_FIELD.FIELD_TIME: CTime(current.year, current.month, current.day, current.hour, current.minute, current.second),
                DATA_FIELD.FIELD_OPEN: round(open_p, 2),
                DATA_FIELD.FIELD_HIGH: round(high_p, 2),
                DATA_FIELD.FIELD_LOW: round(low_p, 2),
                DATA_FIELD.FIELD_CLOSE: round(close_p, 2),
                DATA_FIELD.FIELD_VOLUME: float(volume),
                DATA_FIELD.FIELD_TURNOVER: float(volume * price)
            }
            
            yield CKLine_Unit(item_dict)
            current += datetime.timedelta(seconds=1)

    def SetBasciInfo(self):
        self.name = f"Mock-{self.code}"
        self.is_stock = True

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass
