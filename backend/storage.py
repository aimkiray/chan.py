import os
import json
import hashlib
import datetime
from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, Index, text
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

def _dt_to_utc_iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime.datetime):
        if dt.tzinfo is None:
            return dt.isoformat() + "Z"
        return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    return str(dt)

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

class StrategyCache(Base):
    __tablename__ = 'strategy_cache'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), unique=True, index=True)
    code = Column(String(20), index=True)
    accuracy = Column(Float)
    bsp_count = Column(Integer)
    method = Column(String(100))
    result_json = Column(Text)
    updated_at = Column(String(30))
    start_time = Column(String(30))
    end_time = Column(String(30))
    params_json = Column(Text)

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

class PretrainJob(Base):
    __tablename__ = 'pretrain_job'

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, index=True)

    status = Column(String(20), index=True)
    progress = Column(Float)
    stage = Column(String(50), index=True)
    message = Column(Text)
    detail = Column(Text)

    request_json = Column(Text)
    result_json = Column(Text)

    created_at = Column(String(40), index=True)
    updated_at = Column(String(40), index=True)

    created_at_dt = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at_dt = Column(DateTime, default=datetime.datetime.utcnow)

class StrategyRun(Base):
    __tablename__ = 'strategy_run'

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(100))
    params_json = Column(Text) # Scope, Filters, etc.
    status = Column(String(20)) # "running", "completed", "failed"
    progress = Column(Integer, default=0) # 0-100
    total_stocks = Column(Integer, default=0)
    processed_stocks = Column(Integer, default=0)
    result_count = Column(Integer, default=0)
    result_json = Column(Text) # List of matching stocks
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime)

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
                cols = conn.execute(text("PRAGMA table_info(strategy_run)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "result_count" not in names:
                    conn.execute(text("ALTER TABLE strategy_run ADD COLUMN result_count INTEGER DEFAULT 0"))
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

    def delete_pretrained_model(self, model_key):
        session = self.Session()
        try:
            mk = str(model_key or "").strip()
            if not mk:
                return False
            q = session.query(PretrainedModel).filter(PretrainedModel.model_key == mk)
            if q.first() is None:
                return False
            q.delete()
            session.commit()
            return True
        except Exception as e:
            print(f"Failed to delete pretrained model: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def get_pretrained_models(self, page=1, page_size=20):
        session = self.Session()
        try:
            p = int(page or 1)
            if p < 1:
                p = 1
            ps = int(page_size or 20)
            if ps < 1:
                ps = 1
            if ps > 200:
                ps = 200

            q = session.query(PretrainedModel)
            total = q.count()
            q = q.order_by(PretrainedModel.trained_at.desc(), PretrainedModel.updated_at.desc())
            q = q.offset((p - 1) * ps).limit(ps)
            items = []
            for r in q.all():
                acc = None
                if r.accuracy_json:
                    try:
                        acc = json.loads(r.accuracy_json)
                    except Exception:
                        acc = None
                items.append({
                    "key": r.model_key,
                    "model_type": r.model_type,
                    "frequency": r.frequency,
                    "data_src": r.data_src,
                    "name": r.name,
                    "bundle_path": r.bundle_path,
                    "feature_count": r.feature_count,
                    "sample_count": r.sample_count,
                    "trained_at": r.trained_at,
                    "accuracy": acc,
                })
            return {"items": items, "total": total, "page": p, "page_size": ps}
        finally:
            session.close()

    def get_pretrained_model_by_key(self, model_key):
        session = self.Session()
        try:
            mk = str(model_key or "").strip()
            if not mk:
                return None
            r = session.query(PretrainedModel).filter(PretrainedModel.model_key == mk).first()
            if not r:
                return None
            acc = None
            if r.accuracy_json:
                try:
                    acc = json.loads(r.accuracy_json)
                except Exception:
                    acc = None
            return {
                "key": r.model_key,
                "model_type": r.model_type,
                "frequency": r.frequency,
                "data_src": r.data_src,
                "name": r.name,
                "bundle_path": r.bundle_path,
                "feature_count": r.feature_count,
                "sample_count": r.sample_count,
                "trained_at": r.trained_at,
                "accuracy": acc,
            }
        finally:
            session.close()

    def upsert_pretrain_job(self, job: dict):
        if not isinstance(job, dict):
            return False
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return False

        session = self.Session()
        try:
            now = datetime.datetime.utcnow()
            record = session.query(PretrainJob).filter(PretrainJob.job_id == job_id).first()
            if record is None:
                record = PretrainJob(job_id=job_id)
                session.add(record)

            record.status = str(job.get("status") or "").strip() or None
            try:
                record.progress = float(job.get("progress")) if job.get("progress") is not None else None
            except Exception:
                record.progress = None
            record.stage = str(job.get("stage") or "").strip() or None
            record.message = job.get("message", None)
            record.detail = job.get("detail", None)

            record.request_json = json.dumps(job.get("request", None), ensure_ascii=False) if job.get("request", None) is not None else None
            record.result_json = json.dumps(job.get("result", None), ensure_ascii=False) if job.get("result", None) is not None else None

            record.created_at = str(job.get("created_at") or "").strip() or record.created_at
            record.updated_at = str(job.get("updated_at") or "").strip() or record.updated_at

            if record.created_at_dt is None:
                record.created_at_dt = now
            record.updated_at_dt = now

            session.commit()
            return True
        except Exception as e:
            print(f"Failed to upsert pretrain job: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def get_pretrain_job(self, job_id: str):
        session = self.Session()
        try:
            jid = str(job_id or "").strip()
            if not jid:
                return None
            r = session.query(PretrainJob).filter(PretrainJob.job_id == jid).first()
            if not r:
                return None

            req = None
            if r.request_json:
                try:
                    req = json.loads(r.request_json)
                except Exception:
                    req = None
            res = None
            if r.result_json:
                try:
                    res = json.loads(r.result_json)
                except Exception:
                    res = None

            return {
                "job_id": r.job_id,
                "status": r.status,
                "progress": r.progress,
                "stage": r.stage,
                "message": r.message,
                "detail": r.detail,
                "result": res,
                "request": req,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
        finally:
            session.close()

    def list_pretrain_jobs(self, page=1, page_size=50, limit=None, include_success=True):
        session = self.Session()
        try:
            def record_to_dict(r: PretrainJob):
                if not r:
                    return None
                req = None
                if r.request_json:
                    try:
                        req = json.loads(r.request_json)
                    except Exception:
                        req = None
                res = None
                if r.result_json:
                    try:
                        res = json.loads(r.result_json)
                    except Exception:
                        res = None
                return {
                    "job_id": r.job_id,
                    "status": r.status,
                    "progress": r.progress,
                    "stage": r.stage,
                    "message": r.message,
                    "detail": r.detail,
                    "result": res,
                    "request": req,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }

            if limit is not None:
                lim = int(limit)
                if lim < 1:
                    lim = 1
                if lim > 500:
                    lim = 500
                q = session.query(PretrainJob)
                if not include_success:
                    q = q.filter(PretrainJob.status != "success")
                q = q.order_by(PretrainJob.updated_at.desc(), PretrainJob.updated_at_dt.desc())
                q = q.limit(lim)
                items = [record_to_dict(r) for r in q.all()]
                items = [i for i in items if i is not None]
                return {"items": items}

            p = int(page or 1)
            if p < 1:
                p = 1
            ps = int(page_size or 50)
            if ps < 1:
                ps = 1
            if ps > 200:
                ps = 200

            q = session.query(PretrainJob)
            if not include_success:
                q = q.filter(PretrainJob.status != "success")
            total = q.count()
            q = q.order_by(PretrainJob.updated_at.desc(), PretrainJob.updated_at_dt.desc())
            q = q.offset((p - 1) * ps).limit(ps)
            items = [record_to_dict(r) for r in q.all()]
            items = [i for i in items if i is not None]
            return {"items": items, "total": total, "page": p, "page_size": ps}
        finally:
            session.close()

    def delete_pretrain_job(self, job_id: str):
        session = self.Session()
        try:
            jid = str(job_id or "").strip()
            if not jid:
                return False
            q = session.query(PretrainJob).filter(PretrainJob.job_id == jid)
            if q.first() is None:
                return False
            q.delete()
            session.commit()
            return True
        except Exception as e:
            print(f"Failed to delete pretrain job: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def prune_pretrain_jobs(self, max_jobs: int, ttl_sec: int):
        session = self.Session()
        try:
            max_jobs = int(max_jobs or 0)
            ttl_sec = int(ttl_sec or 0)
            if max_jobs < 1 and ttl_sec < 1:
                return {"deleted_by_ttl": 0, "deleted_by_cap": 0}

            deleted_by_ttl = 0
            deleted_by_cap = 0

            if ttl_sec > 0:
                now = datetime.datetime.utcnow()
                cutoff = now - datetime.timedelta(seconds=ttl_sec)
                cutoff_str = cutoff.isoformat() + "Z"
                q = session.query(PretrainJob).filter(
                    PretrainJob.status.in_(["success", "error"]),
                    PretrainJob.updated_at < cutoff_str
                )
                deleted_by_ttl = q.delete(synchronize_session=False)

            if max_jobs > 0:
                terminal_q = session.query(PretrainJob).filter(PretrainJob.status.in_(["success", "error"]))
                terminal_count = terminal_q.count()
                if terminal_count > max_jobs:
                    over = terminal_count - max_jobs
                    ids = terminal_q.order_by(PretrainJob.updated_at.asc(), PretrainJob.updated_at_dt.asc()).limit(over).all()
                    for r in ids:
                        session.delete(r)
                        deleted_by_cap += 1

            session.commit()
            return {"deleted_by_ttl": deleted_by_ttl, "deleted_by_cap": deleted_by_cap}
        except Exception as e:
            print(f"Failed to prune pretrain jobs: {e}")
            session.rollback()
            return {"deleted_by_ttl": 0, "deleted_by_cap": 0}
        finally:
            session.close()

    def list_strategy_runs(self, page=1, page_size=20):
        session = self.Session()
        try:
            p = int(page or 1)
            if p < 1:
                p = 1
            ps = int(page_size or 20)
            if ps < 1:
                ps = 1
            if ps > 200:
                ps = 200

            q = session.query(StrategyRun)
            total = q.count()
            q = q.order_by(StrategyRun.created_at.desc())
            q = q.offset((p - 1) * ps).limit(ps)

            items = []
            for r in q.all():
                items.append({
                    "id": r.id,
                    "strategy_name": r.strategy_name,
                    "status": r.status,
                    "progress": r.progress,
                    "created_at": _dt_to_utc_iso(r.created_at),
                    "completed_at": _dt_to_utc_iso(r.completed_at),
                    "total": r.total_stocks,
                    "processed": r.processed_stocks,
                    "result_count": int(r.result_count or 0),
                })
            return {"items": items, "total": total, "page": p, "page_size": ps}
        finally:
            session.close()

    def delete_strategy_run(self, run_id: int):
        session = self.Session()
        try:
            rid = int(run_id)
            run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
            if not run:
                return {"deleted": False, "reason": "not_found"}
            if str(run.status) in ["running", "pending"]:
                return {"deleted": False, "reason": "running"}
            session.delete(run)
            session.commit()
            return {"deleted": True}
        except Exception as e:
            print(f"Failed to delete strategy run: {e}")
            session.rollback()
            return {"deleted": False, "reason": str(e)}
        finally:
            session.close()

    def batch_delete_strategy_runs(self, run_ids):
        session = self.Session()
        try:
            ids = []
            for x in (run_ids or []):
                try:
                    ids.append(int(x))
                except Exception:
                    continue
            ids = list(dict.fromkeys(ids))
            if not ids:
                return {"deleted": [], "failed": []}

            deleted = []
            failed = []
            for rid in ids:
                run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
                if not run:
                    failed.append({"run_id": rid, "reason": "not_found"})
                    continue
                if str(run.status) in ["running", "pending"]:
                    failed.append({"run_id": rid, "reason": "running"})
                    continue
                session.delete(run)
                deleted.append(rid)

            session.commit()
            return {"deleted": deleted, "failed": failed}
        except Exception as e:
            print(f"Failed to batch delete strategy runs: {e}")
            session.rollback()
            return {"deleted": [], "failed": [{"run_id": None, "reason": str(e)}]}
        finally:
            session.close()

    def get_strategy_cache(self, key_hash):
        session = self.Session()
        try:
            cache = session.query(StrategyCache).filter(StrategyCache.key_hash == key_hash).first()
            if cache:
                return {
                    "accuracy": cache.accuracy,
                    "bsp_count": cache.bsp_count,
                    "method": cache.method,
                    "result": json.loads(cache.result_json) if cache.result_json else None
                }
            return None
        finally:
            session.close()

    def save_strategy_cache(self, key_hash, code, accuracy, bsp_count, method, result, start_time, end_time, params):
        session = self.Session()
        try:
            cache = session.query(StrategyCache).filter(StrategyCache.key_hash == key_hash).first()
            if not cache:
                cache = StrategyCache(key_hash=key_hash)
                session.add(cache)
            
            cache.code = code
            cache.accuracy = float(accuracy)
            cache.bsp_count = int(bsp_count)
            cache.method = str(method)
            cache.result_json = json.dumps(result) if result else None
            cache.updated_at = _dt_to_utc_iso(datetime.datetime.utcnow())
            cache.start_time = str(start_time)
            cache.end_time = str(end_time)
            cache.params_json = json.dumps(params)
            
            session.commit()
        except Exception as e:
            print(f"Failed to save strategy cache: {e}")
            session.rollback()
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

    def batch_delete_results(self, result_ids):
        session = self.Session()
        try:
            ids = []
            for x in (result_ids or []):
                try:
                    ids.append(int(x))
                except Exception:
                    continue
            ids = list(dict.fromkeys(ids))
            if not ids:
                return {"deleted": [], "failed": []}

            deleted = []
            failed = []
            for rid in ids:
                r = session.query(AnalysisResult).filter(AnalysisResult.id == rid).first()
                if not r:
                    failed.append({"result_id": rid, "reason": "not_found"})
                    continue
                session.delete(r)
                deleted.append(rid)

            session.commit()
            return {"deleted": deleted, "failed": failed}
        except Exception as e:
            print(f"Failed to batch delete results: {e}")
            session.rollback()
            return {"deleted": [], "failed": [{"result_id": None, "reason": str(e)}]}
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
