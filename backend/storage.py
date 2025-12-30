import os
import json
import hashlib
import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Index, text
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
    
    # Data Range
    begin_time = Column(String(30))
    end_time = Column(String(30))

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

class PretrainedModel(Base):
    __tablename__ = 'pretrained_model'

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_key = Column(String(64), unique=True, index=True)
    model_type = Column(String(50), index=True)
    frequency = Column(String(10), index=True)
    data_src = Column(String(50), index=True)
    name = Column(String(120), index=True)

    params_hash = Column(String(64), index=True)
    params_json = Column(Text)

    bundle_path = Column(String(255))
    feature_count = Column(Integer)
    sample_count = Column(Integer)
    trained_at = Column(String(40), index=True)
    accuracy_json = Column(Text)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index('idx_pretrained_lookup', 'model_type', 'frequency', 'data_src', 'params_hash'),
    )

class StorageManager:
    def __init__(self, db_path='analysis.db'):
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.getcwd(), db_path)
            
        self.engine = create_engine(f'sqlite:///{db_path}', connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_schema(self):
        try:
            with self.engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(pretrained_model)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "name" not in names:
                    conn.execute(text("ALTER TABLE pretrained_model ADD COLUMN name VARCHAR(120)"))
                    try:
                        conn.commit()
                    except Exception:
                        try:
                            conn.connection.commit()
                        except Exception:
                            pass
        except Exception as e:
            print(f"Failed to ensure schema: {e}")

    def is_duplicate_pretrain(self, model_type, frequency, data_src, params):
        session = self.Session()
        try:
            params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
            params_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()
            q = session.query(PretrainedModel).filter(
                PretrainedModel.model_type == str(model_type or "").strip(),
                PretrainedModel.frequency == str(frequency or "").strip(),
                PretrainedModel.data_src == str(data_src or "").strip(),
                PretrainedModel.params_hash == params_hash,
            )
            return q.first() is not None
        finally:
            session.close()

    def upsert_pretrained_model(self, model_key, model_type, frequency, data_src, params, meta):
        session = self.Session()
        try:
            now = datetime.datetime.utcnow()
            params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
            params_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()

            record = session.query(PretrainedModel).filter(PretrainedModel.model_key == model_key).first()
            if record is None:
                record = PretrainedModel(model_key=model_key)
                session.add(record)

            record.model_type = model_type
            record.frequency = frequency
            record.data_src = data_src
            record.params_hash = params_hash
            record.params_json = params_json

            record.bundle_path = meta.get("bundle_path") if isinstance(meta, dict) else None
            record.feature_count = meta.get("feature_count") if isinstance(meta, dict) else None
            record.sample_count = meta.get("sample_count") if isinstance(meta, dict) else None
            record.trained_at = meta.get("trained_at") if isinstance(meta, dict) else None
            acc = meta.get("accuracy") if isinstance(meta, dict) else None
            record.accuracy_json = json.dumps(acc, ensure_ascii=False) if acc is not None else None

            record.updated_at = now
            if record.created_at is None:
                record.created_at = now

            session.commit()
            return record.id
        except Exception as e:
            print(f"Failed to upsert pretrained model: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def get_pretrained_model_names_by_keys(self, model_keys):
        if not model_keys:
            return {}
        keys = [str(k).strip() for k in model_keys if str(k).strip()]
        if not keys:
            return {}
        session = self.Session()
        try:
            rows = session.query(PretrainedModel.model_key, PretrainedModel.name).filter(PretrainedModel.model_key.in_(keys)).all()
            out = {}
            for mk, nm in rows:
                if mk:
                    out[str(mk)] = nm
            return out
        finally:
            session.close()

    def set_pretrained_model_name(self, model_key, name):
        session = self.Session()
        try:
            mk = str(model_key or "").strip()
            if not mk:
                return False
            record = session.query(PretrainedModel).filter(PretrainedModel.model_key == mk).first()
            if record is None:
                record = PretrainedModel(model_key=mk)
                session.add(record)
            record.name = name
            record.updated_at = datetime.datetime.utcnow()
            if record.created_at is None:
                record.created_at = record.updated_at
            session.commit()
            return True
        except Exception as e:
            print(f"Failed to set pretrained model name: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def save_result(self, code, freq, params, data_latest_time, result_dict, model, signal_info, begin_time=None, end_time=None):
        session = self.Session()
        try:
            params_json = json.dumps(params, sort_keys=True)
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
                accuracy=signal_info.get('accuracy') if signal_info else None,
                begin_time=begin_time,
                end_time=end_time
            )
            session.add(result)
            session.commit()
            return result.id
        except Exception as e:
            print(f"Failed to save result: {e}")
            session.rollback()
        finally:
            session.close()

    def get_latest_result(self, code, freq, params, data_latest_time, begin_time=None):
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
            )
            
            if begin_time:
                q = q.filter(AnalysisResult.begin_time == begin_time)
                
            q = q.order_by(AnalysisResult.created_at.desc())
            
            return q.first()
        finally:
            session.close()

    def get_history(self, code=None, page=1, page_size=10):
        session = self.Session()
        try:
            q = session.query(AnalysisResult)
            
            # Filter: Only show records with AI analysis (indicated by accuracy not being None)
            q = q.filter(AnalysisResult.accuracy != None)
            
            if code:
                q = q.filter(AnalysisResult.code == code)
            
            total = q.count()
            
            q = q.order_by(AnalysisResult.created_at.desc())
            q = q.offset((page - 1) * page_size).limit(page_size)
            
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
            return {"items": results, "total": total, "page": page, "page_size": page_size}
        finally:
            session.close()

    def delete_result(self, result_id):
        session = self.Session()
        try:
            q = session.query(AnalysisResult).filter(AnalysisResult.id == result_id)
            if q.first():
                q.delete()
                session.commit()
                return True
            return False
        except Exception as e:
            print(f"Failed to delete result: {e}")
            session.rollback()
            return False
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
