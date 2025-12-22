import os
from sqlalchemy import create_engine, Column, String, Float, Index
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

class StockKLine(Base):
    __tablename__ = 'stock_kline'
    
    code = Column(String(20), primary_key=True)
    date = Column(String(20), primary_key=True) # Format: YYYY-MM-DD or YYYYMMDDHHMMSS
    frequency = Column(String(10), primary_key=True)
    adjust_flag = Column(String(1), primary_key=True) # '2' for QFQ, '1' for HFQ, '3' for None
    
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    turn = Column(Float, nullable=True)

    # Index for fast retrieval of latest date
    __table_args__ = (
        Index('idx_code_freq_date', 'code', 'frequency', 'adjust_flag', 'date'),
    )

class DBManager:
    def __init__(self, db_path='stock_data.db'):
        # Use absolute path to ensure DB is created in project root
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.getcwd(), db_path)
            
        self.engine = create_engine(f'sqlite:///{db_path}')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_latest_date(self, code, frequency, adjust_flag):
        session = self.Session()
        try:
            result = session.query(StockKLine.date).filter(
                StockKLine.code == code,
                StockKLine.frequency == frequency,
                StockKLine.adjust_flag == adjust_flag
            ).order_by(StockKLine.date.desc()).first()
            return result[0] if result else None
        finally:
            session.close()

    def get_data(self, code, frequency, adjust_flag, start_date=None, end_date=None):
        session = self.Session()
        try:
            query = session.query(StockKLine).filter(
                StockKLine.code == code,
                StockKLine.frequency == frequency,
                StockKLine.adjust_flag == adjust_flag
            )
            if start_date:
                query = query.filter(StockKLine.date >= start_date)
            if end_date:
                query = query.filter(StockKLine.date <= end_date)
            
            # Return list of dicts/objects that can be detached from session
            # Or just return the objects, but we must be careful accessing them after session close if using lazy load.
            # Since we fetch all columns eagerly by default (no relationships), it should be fine if we query .all()
            return query.order_by(StockKLine.date).all()
        finally:
            session.close()

    def save_data(self, data_list):
        if not data_list:
            return
        session = self.Session()
        try:
            for item in data_list:
                session.merge(item)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def close(self):
        self.engine.dispose()
