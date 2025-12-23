import os
import json
import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Index
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

class AnalysisResult(Base):
    __tablename__ = 'analysis_result'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), index=True)
    frequency = Column(String(10))
    # Signature of parameters: bi_strict, trigger_step, data_src, model
    params_hash = Column(String(64), index=True) 
    # Full params json for reference
    params_json = Column(Text)
    
    # Time of the latest K-line used in this analysis (to detect data updates)
    data_latest_time = Column(String(30))
    
    # When this analysis was run
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # The full JSON response to return to frontend
    result_json = Column(Text)
    
    # Extra info for list view
    model = Column(String(50))
    signal_type = Column(String(50)) # e.g. "1买"
    is_buy = Column(Integer) # 1 or 0
    accuracy = Column(String(20)) # e.g. "85.5%"

    __table_args__ = (
        Index('idx_lookup', 'code', 'frequency', 'params_hash', 'data_latest_time'),
    )

class StorageManager:
    def __init__(self, db_path='analysis.db'):
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.getcwd(), db_path)
            
        self.engine = create_engine(f'sqlite:///{db_path}', connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def save_result(self, code, freq, params, data_latest_time, result_dict, model, signal_info):
        session = self.Session()
        try:
            params_json = json.dumps(params, sort_keys=True)
            import hashlib
            params_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()
            
            # Check if exists to update or insert new? 
            # User wants "History", so maybe keep all?
            # But for caching, we just need the latest compatible one.
            # Let's just insert new ones. We can clean up old ones later if needed.
            
            result = AnalysisResult(
                code=code,
                frequency=freq,
                params_hash=params_hash,
                params_json=params_json,
                data_latest_time=data_latest_time,
                result_json=json.dumps(result_dict),
                model=model,
                signal_type=signal_info.get('type') if signal_info else None,
                is_buy=1 if signal_info and signal_info.get('is_buy') else 0,
                accuracy=signal_info.get('accuracy') if signal_info else None
            )
            session.add(result)
            session.commit()
            return result.id
        except Exception as e:
            print(f"Failed to save result: {e}")
            session.rollback()
        finally:
            session.close()

    def get_latest_result(self, code, freq, params, data_latest_time):
        session = self.Session()
        try:
            params_json = json.dumps(params, sort_keys=True)
            import hashlib
            params_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()
            
            # Find the most recent result that matches parameters AND data time
            # If data_latest_time is provided, we strictly match it.
            # This ensures we don't return stale analysis if new data arrived.
            
            q = session.query(AnalysisResult).filter(
                AnalysisResult.code == code,
                AnalysisResult.frequency == freq,
                AnalysisResult.params_hash == params_hash,
                AnalysisResult.data_latest_time == data_latest_time
            ).order_by(AnalysisResult.created_at.desc())
            
            return q.first()
        finally:
            session.close()

    def get_history(self, code=None, limit=50):
        session = self.Session()
        try:
            q = session.query(AnalysisResult)
            if code:
                q = q.filter(AnalysisResult.code == code)
            q = q.order_by(AnalysisResult.created_at.desc()).limit(limit)
            
            results = []
            for r in q.all():
                results.append({
                    "id": r.id,
                    "code": r.code,
                    "frequency": r.frequency,
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "model": r.model,
                    "signal_type": r.signal_type,
                    "is_buy": r.is_buy,
                    "accuracy": r.accuracy,
                    "data_latest_time": r.data_latest_time,
                    # We don't send full result_json in list to save bandwidth
                })
            return results
        finally:
            session.close()

    def get_result_by_id(self, result_id):
        session = self.Session()
        try:
            r = session.query(AnalysisResult).filter(AnalysisResult.id == result_id).first()
            if r:
                return json.loads(r.result_json)
            return None
        finally:
            session.close()
