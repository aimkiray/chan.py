import os
import json
import hashlib
import datetime
import uuid
from typing import Optional
from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, Index, text, or_
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

def _normalize_sim_id(sim_id):
    sid = str(sim_id or "").strip()
    return sid if sid else "default"

def _is_default_sim(sim_id):
    return _normalize_sim_id(sim_id) == "default"

def _normalize_portfolio_id(portfolio_id):
    if portfolio_id is None:
        return None
    try:
        pid = int(portfolio_id)
    except Exception:
        return None
    return pid if pid > 0 else None

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

class BacktestPreset(Base):
    __tablename__ = 'backtest_preset'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, index=True)
    paper_config_json = Column(Text)
    paper_params_json = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class PositionStatus(Base):
    __tablename__ = 'position_status'

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), unique=True, index=True)
    entry_time = Column(Integer)
    entry_day = Column(Integer, index=True)
    entry_price = Column(Float)
    bars_held = Column(Integer)
    updated_at = Column(String(40), index=True)

class LiveOrder(Base):
    __tablename__ = 'live_order'

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_id = Column(String(40), index=True)
    portfolio_id = Column(Integer, index=True)
    stock_code = Column(String(20), index=True)
    side = Column(String(10), index=True)  # buy/sell
    status = Column(String(20), index=True)  # pending/executed/blocked/canceled
    requested_ts = Column(Integer, index=True)
    execute_after_ts = Column(Integer, index=True)
    min_day = Column(Integer, index=True)
    price = Column(Float)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class LiveTrade(Base):
    __tablename__ = 'live_trade'

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_id = Column(String(40), index=True)
    portfolio_id = Column(Integer, index=True)
    stock_code = Column(String(20), index=True)
    side = Column(String(10), index=True)  # buy/sell
    status = Column(String(20), index=True)  # executed/pending/canceled
    trade_ts = Column(Integer, index=True)
    price = Column(Float)
    meta_json = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class LiveSim(Base):
    __tablename__ = "live_sim"

    sim_id = Column(String(40), primary_key=True)
    name = Column(String(120), index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    initial_capital = Column(Float, default=100000.0)
    balance = Column(Float, default=100000.0)

class LiveRun(Base):
    __tablename__ = "live_run"

    run_id = Column(String(40), primary_key=True)
    name = Column(String(120), index=True)
    sim_id = Column(String(40), index=True)
    portfolio_id = Column(Integer, index=True)
    status = Column(String(20), index=True)
    steps = Column(Integer, default=0)
    last_step_ts = Column(Integer, index=True)
    next_exec_ts = Column(Integer, index=True)
    offset_seconds = Column(Integer, default=5)
    auto_resume = Column(Integer, default=1)
    notify_enabled = Column(Integer, default=0)
    notify_kind = Column(String(20))
    notify_webhook = Column(Text)
    notify_secret = Column(Text)
    sms_enabled = Column(Integer, default=0)
    sms_phone = Column(String(40))
    sms_token = Column(Text)
    params_json = Column(Text)
    last_result_json = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    stopped_at = Column(DateTime)

class LiveStepRecord(Base):
    __tablename__ = "live_step_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_id = Column(String(40), index=True)
    portfolio_id = Column(Integer, index=True)
    run_id = Column(String(40), index=True)
    step_ts = Column(Integer, index=True)
    day = Column(Integer, index=True)
    risk_json = Column(Text)
    targets_json = Column(Text)
    sell_json = Column(Text)
    buy_json = Column(Text)
    blocked_json = Column(Text)
    summary_json = Column(Text)
    result_json = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_live_step_sim_ts", "sim_id", "step_ts"),
        Index("idx_live_step_run_ts", "run_id", "step_ts"),
        Index("idx_live_step_sim_day", "sim_id", "day"),
    )

class LivePosition(Base):
    __tablename__ = "live_position"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_id = Column(String(40), index=True)
    portfolio_id = Column(Integer, index=True)
    stock_code = Column(String(20), index=True)
    stock_name = Column(String(50))
    entry_time = Column(Integer)
    entry_day = Column(Integer, index=True)
    entry_price = Column(Float)
    bars_held = Column(Integer)
    volume = Column(Integer, default=0)
    updated_at = Column(String(40), index=True)

    __table_args__ = (
        Index("idx_live_position_sim_pid_code_unique", "sim_id", "portfolio_id", "stock_code", unique=True),
        Index("idx_live_position_sim_pid_entry_day", "sim_id", "portfolio_id", "entry_day"),
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
                cols = conn.execute(text("PRAGMA table_info(position_status)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "entry_day" not in names:
                    try:
                        conn.execute(text("ALTER TABLE position_status ADD COLUMN entry_day INTEGER"))
                    except Exception:
                        pass
                conn.execute(text("CREATE TABLE IF NOT EXISTS position_status (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code VARCHAR(20) UNIQUE, entry_time INTEGER, entry_day INTEGER, entry_price FLOAT, bars_held INTEGER, updated_at VARCHAR(40))"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_position_status_code ON position_status(stock_code)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_position_status_updated ON position_status(updated_at)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_position_status_entry_day ON position_status(entry_day)"))

                cols = conn.execute(text("PRAGMA table_info(live_order)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "sim_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_order ADD COLUMN sim_id VARCHAR(40)"))
                    except Exception:
                        pass
                if "portfolio_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_order ADD COLUMN portfolio_id INTEGER"))
                    except Exception:
                        pass
                if "execute_after_ts" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_order ADD COLUMN execute_after_ts INTEGER"))
                    except Exception:
                        pass
                if "min_day" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_order ADD COLUMN min_day INTEGER"))
                    except Exception:
                        pass
                if "reason" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_order ADD COLUMN reason TEXT"))
                    except Exception:
                        pass
                conn.execute(text("CREATE TABLE IF NOT EXISTS live_order (id INTEGER PRIMARY KEY AUTOINCREMENT, sim_id VARCHAR(40), portfolio_id INTEGER, stock_code VARCHAR(20), side VARCHAR(10), status VARCHAR(20), requested_ts INTEGER, execute_after_ts INTEGER, min_day INTEGER, price FLOAT, reason TEXT, created_at DATETIME, updated_at DATETIME)"))
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_sim_id ON live_order(sim_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_portfolio_id ON live_order(portfolio_id)"))
                except Exception:
                    pass
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_code ON live_order(stock_code)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_status ON live_order(status)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_requested_ts ON live_order(requested_ts)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_execute_after_ts ON live_order(execute_after_ts)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_order_min_day ON live_order(min_day)"))

                cols = conn.execute(text("PRAGMA table_info(live_trade)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "sim_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_trade ADD COLUMN sim_id VARCHAR(40)"))
                    except Exception:
                        pass
                if "portfolio_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_trade ADD COLUMN portfolio_id INTEGER"))
                    except Exception:
                        pass
                conn.execute(text("CREATE TABLE IF NOT EXISTS live_trade (id INTEGER PRIMARY KEY AUTOINCREMENT, sim_id VARCHAR(40), portfolio_id INTEGER, stock_code VARCHAR(20), side VARCHAR(10), status VARCHAR(20), trade_ts INTEGER, price FLOAT, meta_json TEXT, created_at DATETIME)"))
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_trade_sim_id ON live_trade(sim_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_trade_portfolio_id ON live_trade(portfolio_id)"))
                except Exception:
                    pass
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_trade_code ON live_trade(stock_code)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_trade_status ON live_trade(status)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_trade_trade_ts ON live_trade(trade_ts)"))

                conn.execute(text("CREATE TABLE IF NOT EXISTS live_sim (sim_id VARCHAR(40) PRIMARY KEY, name VARCHAR(120), created_at DATETIME, updated_at DATETIME)"))
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_sim_name ON live_sim(name)"))
                except Exception:
                    pass

                cols = conn.execute(text("PRAGMA table_info(live_sim)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "initial_capital" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_sim ADD COLUMN initial_capital FLOAT DEFAULT 100000.0"))
                    except Exception:
                        pass
                if "balance" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_sim ADD COLUMN balance FLOAT DEFAULT 100000.0"))
                    except Exception:
                        pass

                cols = conn.execute(text("PRAGMA table_info(live_run)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "name" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN name VARCHAR(120)"))
                    except Exception:
                        pass
                if "portfolio_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN portfolio_id INTEGER"))
                    except Exception:
                        pass
                if "next_exec_ts" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN next_exec_ts INTEGER"))
                    except Exception:
                        pass
                if "offset_seconds" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN offset_seconds INTEGER"))
                    except Exception:
                        pass
                if "auto_resume" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN auto_resume INTEGER"))
                    except Exception:
                        pass
                if "notify_enabled" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN notify_enabled INTEGER"))
                    except Exception:
                        pass
                if "notify_kind" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN notify_kind VARCHAR(20)"))
                    except Exception:
                        pass
                if "notify_webhook" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN notify_webhook TEXT"))
                    except Exception:
                        pass
                if "notify_secret" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN notify_secret TEXT"))
                    except Exception:
                        pass
                if "sms_enabled" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN sms_enabled INTEGER"))
                    except Exception:
                        pass
                if "sms_phone" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN sms_phone VARCHAR(40)"))
                    except Exception:
                        pass
                if "sms_token" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_run ADD COLUMN sms_token TEXT"))
                    except Exception:
                        pass
                conn.execute(text("""CREATE TABLE IF NOT EXISTS live_run (
                    run_id VARCHAR(40) PRIMARY KEY,
                    name VARCHAR(120),
                    sim_id VARCHAR(40),
                    portfolio_id INTEGER,
                    status VARCHAR(20),
                    steps INTEGER,
                    last_step_ts INTEGER,
                    next_exec_ts INTEGER,
                    offset_seconds INTEGER,
                    auto_resume INTEGER,
                    notify_enabled INTEGER,
                    notify_kind VARCHAR(20),
                    notify_webhook TEXT,
                    notify_secret TEXT,
                    sms_enabled INTEGER,
                    sms_phone VARCHAR(40),
                    sms_token TEXT,
                    params_json TEXT,
                    last_result_json TEXT,
                    error TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    stopped_at DATETIME
                )"""))
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_run_sim_id ON live_run(sim_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_run_portfolio_id ON live_run(portfolio_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_run_status ON live_run(status)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_run_next_exec_ts ON live_run(next_exec_ts)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_run_updated_at ON live_run(updated_at)"))
                except Exception:
                    pass

                cols = conn.execute(text("PRAGMA table_info(live_step_record)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "portfolio_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_step_record ADD COLUMN portfolio_id INTEGER"))
                    except Exception:
                        pass
                conn.execute(text("""CREATE TABLE IF NOT EXISTS live_step_record (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sim_id VARCHAR(40),
                    portfolio_id INTEGER,
                    run_id VARCHAR(40),
                    step_ts INTEGER,
                    day INTEGER,
                    risk_json TEXT,
                    targets_json TEXT,
                    sell_json TEXT,
                    buy_json TEXT,
                    blocked_json TEXT,
                    summary_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at DATETIME
                )"""))
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_step_sim_id ON live_step_record(sim_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_step_portfolio_id ON live_step_record(portfolio_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_step_run_id ON live_step_record(run_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_step_step_ts ON live_step_record(step_ts)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_step_day ON live_step_record(day)"))
                except Exception:
                    pass

                cols = conn.execute(text("PRAGMA table_info(live_position)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "portfolio_id" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_position ADD COLUMN portfolio_id INTEGER"))
                    except Exception:
                        pass
                conn.execute(text("""CREATE TABLE IF NOT EXISTS live_position (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sim_id VARCHAR(40),
                    portfolio_id INTEGER,
                    stock_code VARCHAR(20),
                    stock_name VARCHAR(50),
                    entry_time INTEGER,
                    entry_price FLOAT,
                    bars_held INTEGER,
                    volume INTEGER,
                    updated_at VARCHAR(40)
                )"""))
                cols = conn.execute(text("PRAGMA table_info(live_position)")).fetchall()
                names = [c[1] for c in cols] if cols else []
                if "volume" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_position ADD COLUMN volume INTEGER DEFAULT 0"))
                    except Exception:
                        pass
                if "stock_name" not in names:
                    try:
                        conn.execute(text("ALTER TABLE live_position ADD COLUMN stock_name VARCHAR(50)"))
                    except Exception:
                        pass
                try:
                    conn.execute(text("DROP INDEX IF EXISTS idx_live_position_sim_code_unique"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_position_sim_id ON live_position(sim_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_position_portfolio_id ON live_position(portfolio_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_position_code ON live_position(stock_code)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_position_entry_day ON live_position(entry_day)"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_live_position_sim_pid_code_unique ON live_position(sim_id, portfolio_id, stock_code)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_live_position_sim_pid_entry_day ON live_position(sim_id, portfolio_id, entry_day)"))
                except Exception:
                    pass
                try:
                    conn.commit()
                except Exception:
                    try:
                        conn.connection.commit()
                    except Exception:
                        pass
        except Exception as e:
            print(f"Failed to ensure schema: {e}")

    def list_positions(self, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            out = []
            if _is_default_sim(sid) and pid is None:
                rows = session.query(PositionStatus).all()
                for r in rows or []:
                    out.append({
                        "stock_code": r.stock_code,
                        "entry_time": int(r.entry_time or 0),
                        "entry_day": int(r.entry_day or 0),
                        "entry_price": float(r.entry_price or 0.0),
                        "bars_held": int(r.bars_held or 0),
                        "updated_at": str(r.updated_at or ""),
                    })
                return out

            q = session.query(LivePosition).filter(LivePosition.sim_id == sid)
            if pid is not None:
                q = q.filter(LivePosition.portfolio_id == pid)
            rows = q.all()
            for r in rows or []:
                out.append({
                    "stock_code": r.stock_code,
                    "stock_name": r.stock_name,
                    "entry_time": int(r.entry_time or 0),
                    "entry_day": int(r.entry_day or 0),
                    "entry_price": float(r.entry_price or 0.0),
                    "bars_held": int(r.bars_held or 0),
                    "volume": int(r.volume or 0),
                    "updated_at": str(r.updated_at or ""),
                })
            return out

    def upsert_position(self, stock_code: str, entry_time: int, entry_price: float, bars_held: int = 0, sim_id: str = None, portfolio_id: int = None, volume: int = 0, stock_name: str = ""):
        now = datetime.datetime.utcnow().isoformat() + "Z"
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        stock_code = str(stock_code or "").strip()
        if not stock_code:
            return
        
        try:
            vol = int(volume or 0)
        except Exception:
            vol = 0
        try:
            entry_time = int(entry_time or 0)
        except Exception:
            entry_time = 0
        entry_day = None
        if entry_time > 0:
            try:
                dt = datetime.datetime.fromtimestamp(int(entry_time), tz=datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)
                entry_day = int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)
            except Exception:
                entry_day = None
        with self.Session() as session:
            if _is_default_sim(sid) and pid is None:
                row = session.query(PositionStatus).filter_by(stock_code=stock_code).first()
                if row is None:
                    row = PositionStatus(stock_code=stock_code)
                    session.add(row)
            else:
                row = session.query(LivePosition).filter_by(sim_id=sid, portfolio_id=pid, stock_code=stock_code).first()
                if row is None:
                    row = LivePosition(sim_id=sid, portfolio_id=pid, stock_code=stock_code)
                    session.add(row)

            row.entry_time = int(entry_time or 0)
            if entry_day is not None:
                row.entry_day = int(entry_day)
            row.entry_price = float(entry_price or 0.0)
            row.bars_held = int(bars_held or 0)
            if vol > 0:
                row.volume = int(vol)
            if stock_name:
                row.stock_name = str(stock_name)
            row.updated_at = now
            session.commit()

    def get_live_sim(self, sim_id: str) -> Optional[dict]:
        sid = _normalize_sim_id(sim_id)
        with self.Session() as session:
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if not row:
                return None
            return {
                "sim_id": row.sim_id,
                "name": row.name,
                "created_at": _dt_to_utc_iso(row.created_at),
                "updated_at": _dt_to_utc_iso(row.updated_at),
                "initial_capital": float(row.initial_capital or 0.0),
                "balance": float(row.balance or 0.0),
            }

    def update_live_sim_balance(self, sim_id: str, balance: float):
        sid = _normalize_sim_id(sim_id)
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row:
                row.balance = float(balance)
                row.updated_at = now
                session.commit()


    def increment_bars_held(self, inc: int = 1, sim_id: str = None, portfolio_id: int = None):
        now = datetime.datetime.utcnow().isoformat() + "Z"
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            inc = int(inc)
        except Exception:
            inc = 1
        if inc <= 0:
            inc = 1
        with self.Session() as session:
            if _is_default_sim(sid) and pid is None:
                rows = session.query(PositionStatus).all()
            else:
                q = session.query(LivePosition).filter(LivePosition.sim_id == sid)
                if pid is not None:
                    q = q.filter(LivePosition.portfolio_id == pid)
                rows = q.all()
            for r in rows or []:
                r.bars_held = int(r.bars_held or 0) + int(inc)
                r.updated_at = now
            session.commit()

    def delete_positions(self, stock_codes, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        codes = [str(c).strip() for c in (stock_codes or []) if str(c or "").strip()]
        if not codes:
            return
        with self.Session() as session:
            if _is_default_sim(sid) and pid is None:
                session.query(PositionStatus).filter(PositionStatus.stock_code.in_(codes)).delete(synchronize_session=False)
            else:
                q = session.query(LivePosition).filter(LivePosition.sim_id == sid, LivePosition.stock_code.in_(codes))
                if pid is not None:
                    q = q.filter(LivePosition.portfolio_id == pid)
                q.delete(synchronize_session=False)
            session.commit()

    def list_live_orders(self, status: str = None, limit: int = 200, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            q = session.query(LiveOrder)
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default"))
            else:
                q = q.filter(LiveOrder.sim_id == sid)
            if status:
                q = q.filter(LiveOrder.status == str(status))
            q = q.order_by(LiveOrder.created_at.desc(), LiveOrder.id.desc())
            try:
                limit = int(limit or 200)
            except Exception:
                limit = 200
            limit = max(1, min(limit, 1000))
            rows = q.limit(limit).all()
            out = []
            for r in rows or []:
                out.append({
                    "id": int(r.id),
                    "stock_code": str(r.stock_code or ""),
                    "side": str(r.side or ""),
                    "status": str(r.status or ""),
                    "requested_ts": int(r.requested_ts or 0),
                    "execute_after_ts": int(r.execute_after_ts or 0),
                    "min_day": int(r.min_day or 0),
                    "price": float(r.price or 0.0),
                    "reason": r.reason,
                    "created_at": _dt_to_utc_iso(r.created_at),
                    "updated_at": _dt_to_utc_iso(r.updated_at),
                })
            return out

    def replace_pending_live_orders(self, orders: list, requested_ts: int, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        requested_ts = int(requested_ts or 0)
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            q = session.query(LiveOrder).filter(LiveOrder.status == "pending")
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default"))
            else:
                q = q.filter(LiveOrder.sim_id == sid)
            q.delete(synchronize_session=False)
            for o in orders or []:
                if not isinstance(o, dict):
                    continue
                code = str(o.get("stock_code") or "").strip()
                side = str(o.get("side") or "").strip().lower()
                if not code or side not in ["buy", "sell"]:
                    continue
                try:
                    price = float(o.get("price")) if o.get("price") is not None else 0.0
                except Exception:
                    price = 0.0
                try:
                    execute_after_ts = int(o.get("execute_after_ts") or 0)
                except Exception:
                    execute_after_ts = 0
                try:
                    min_day = int(o.get("min_day") or 0)
                except Exception:
                    min_day = 0
                reason = o.get("reason")
                row = LiveOrder(
                    sim_id=sid,
                    portfolio_id=pid,
                    stock_code=code,
                    side=side,
                    status="pending",
                    requested_ts=requested_ts,
                    execute_after_ts=execute_after_ts,
                    min_day=min_day,
                    price=price,
                    reason=reason,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.commit()

    def mark_orders_executed(self, order_ids: list, executed_ts: int, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        now = datetime.datetime.utcnow()
        try:
            ids = [int(x) for x in (order_ids or [])]
        except Exception:
            ids = []
        ids = [x for x in ids if x > 0]
        if not ids:
            return
        with self.Session() as session:
            q = session.query(LiveOrder).filter(LiveOrder.id.in_(ids))
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default"))
            else:
                q = q.filter(LiveOrder.sim_id == sid)
            rows = q.all()
            for r in rows or []:
                r.status = "executed"
                r.updated_at = now
                if executed_ts and not r.requested_ts:
                    r.requested_ts = int(executed_ts)
            session.commit()

    def replace_blocked_live_orders(self, orders: list, requested_ts: int, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        requested_ts = int(requested_ts or 0)
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            q = session.query(LiveOrder).filter(LiveOrder.status == "blocked")
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default"))
            else:
                q = q.filter(LiveOrder.sim_id == sid)
            q.delete(synchronize_session=False)
            for o in orders or []:
                if not isinstance(o, dict):
                    continue
                code = str(o.get("stock_code") or "").strip()
                side = str(o.get("side") or "").strip().lower()
                if not code or side not in ["buy", "sell"]:
                    continue
                try:
                    price = float(o.get("price")) if o.get("price") is not None else 0.0
                except Exception:
                    price = 0.0
                try:
                    execute_after_ts = int(o.get("execute_after_ts") or 0)
                except Exception:
                    execute_after_ts = 0
                try:
                    min_day = int(o.get("min_day") or 0)
                except Exception:
                    min_day = 0
                reason = o.get("reason")
                row = LiveOrder(
                    sim_id=sid,
                    portfolio_id=pid,
                    stock_code=code,
                    side=side,
                    status="blocked",
                    requested_ts=requested_ts,
                    execute_after_ts=execute_after_ts,
                    min_day=min_day,
                    price=price,
                    reason=reason,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.commit()

    def clear_live_orders(self, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            q = session.query(LiveOrder)
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if sid is None:
                q.delete(synchronize_session=False)
            elif _is_default_sim(sid):
                q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default")).delete(synchronize_session=False)
            else:
                q.filter(LiveOrder.sim_id == sid).delete(synchronize_session=False)
            session.commit()

    def add_live_trades(self, trades: list, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        if not trades:
            return
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            for tr in trades or []:
                if not isinstance(tr, dict):
                    continue
                code = str(tr.get("stock_code") or "").strip()
                side = str(tr.get("side") or "").strip().lower()
                status = str(tr.get("status") or "").strip().lower() or "executed"
                if not code or side not in ["buy", "sell"]:
                    continue
                try:
                    price = float(tr.get("price")) if tr.get("price") is not None else 0.0
                except Exception:
                    price = 0.0
                try:
                    trade_ts = int(tr.get("trade_ts") or 0)
                except Exception:
                    trade_ts = 0
                meta = tr.get("meta")
                meta_json = None
                if meta is not None:
                    try:
                        meta_json = json.dumps(meta, ensure_ascii=False)
                    except Exception:
                        meta_json = None
                row = LiveTrade(
                    sim_id=sid,
                    portfolio_id=pid,
                    stock_code=code,
                    side=side,
                    status=status,
                    trade_ts=trade_ts,
                    price=price,
                    meta_json=meta_json,
                    created_at=now,
                )
                session.add(row)
            session.commit()

    def list_live_trades(self, limit: int = 200, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            q = session.query(LiveTrade)
            if pid is not None:
                q = q.filter(LiveTrade.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveTrade.sim_id == None, LiveTrade.sim_id == "", LiveTrade.sim_id == "default"))
            else:
                q = q.filter(LiveTrade.sim_id == sid)
            q = q.order_by(LiveTrade.created_at.desc(), LiveTrade.id.desc())
            try:
                limit = int(limit or 200)
            except Exception:
                limit = 200
            limit = max(1, min(limit, 2000))
            rows = q.limit(limit).all()
            out = []
            for r in rows or []:
                meta = None
                if r.meta_json:
                    try:
                        meta = json.loads(r.meta_json)
                    except Exception:
                        meta = None
                out.append({
                    "id": int(r.id),
                    "stock_code": str(r.stock_code or ""),
                    "side": str(r.side or ""),
                    "status": str(r.status or ""),
                    "trade_ts": int(r.trade_ts or 0),
                    "price": float(r.price or 0.0),
                    "meta": meta,
                    "created_at": _dt_to_utc_iso(r.created_at),
                })
            return out

    def list_live_trades_paged(self, page: int = 1, page_size: int = 50, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            try:
                p = int(page or 1)
            except Exception:
                p = 1
            if p < 1:
                p = 1
            try:
                ps = int(page_size or 50)
            except Exception:
                ps = 50
            if ps < 1:
                ps = 1
            if ps > 500:
                ps = 500

            q = session.query(LiveTrade)
            if pid is not None:
                q = q.filter(LiveTrade.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveTrade.sim_id == None, LiveTrade.sim_id == "", LiveTrade.sim_id == "default"))
            else:
                q = q.filter(LiveTrade.sim_id == sid)
            total = q.count()
            q = q.order_by(LiveTrade.created_at.desc(), LiveTrade.id.desc())
            q = q.offset((p - 1) * ps).limit(ps)
            rows = q.all()

            out = []
            for r in rows or []:
                meta = None
                if r.meta_json:
                    try:
                        meta = json.loads(r.meta_json)
                    except Exception:
                        meta = None
                out.append({
                    "id": int(r.id),
                    "stock_code": str(r.stock_code or ""),
                    "side": str(r.side or ""),
                    "status": str(r.status or ""),
                    "trade_ts": int(r.trade_ts or 0),
                    "price": float(r.price or 0.0),
                    "meta": meta,
                    "created_at": _dt_to_utc_iso(r.created_at),
                })

            return {"items": out, "total": int(total), "page": int(p), "page_size": int(ps)}

    def clear_live_trades(self, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            q = session.query(LiveTrade)
            if pid is not None:
                q = q.filter(LiveTrade.portfolio_id == pid)
            if sid is None:
                q.delete(synchronize_session=False)
            elif _is_default_sim(sid):
                q.filter(or_(LiveTrade.sim_id == None, LiveTrade.sim_id == "", LiveTrade.sim_id == "default")).delete(synchronize_session=False)
            else:
                q.filter(LiveTrade.sim_id == sid).delete(synchronize_session=False)
            session.commit()

    def clear_positions(self, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            if _is_default_sim(sid) and pid is None:
                session.query(PositionStatus).delete(synchronize_session=False)
            else:
                q = session.query(LivePosition).filter(LivePosition.sim_id == sid)
                if pid is not None:
                    q = q.filter(LivePosition.portfolio_id == pid)
                q.delete(synchronize_session=False)
            session.commit()

    def upsert_live_sim(self, sim_id: str, name: str = None) -> dict:
        sid = _normalize_sim_id(sim_id)
        nm = str(name or "").strip() or None
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row is None:
                row = LiveSim(sim_id=sid)
                session.add(row)
                row.created_at = now
            row.name = nm
            row.updated_at = now
            session.commit()
            return {"sim_id": sid, "name": nm, "created_at": _dt_to_utc_iso(row.created_at), "updated_at": _dt_to_utc_iso(row.updated_at)}

    def list_live_sims(self, limit: int = 200) -> list:
        try:
            lim = int(limit or 200)
        except Exception:
            lim = 200
        lim = max(1, min(lim, 2000))
        with self.Session() as session:
            q = session.query(LiveSim).order_by(LiveSim.updated_at.desc(), LiveSim.created_at.desc())
            rows = q.limit(lim).all()
            out = []
            for r in rows or []:
                out.append({"sim_id": str(r.sim_id or ""), "name": str(r.name or ""), "created_at": _dt_to_utc_iso(r.created_at), "updated_at": _dt_to_utc_iso(r.updated_at)})
            return out

    def upsert_live_run(
        self,
        run_id: str,
        sim_id: str,
        name: str,
        status: str,
        params: dict,
        portfolio_id: int = None,
        next_exec_ts: int = 0,
        offset_seconds: int = 5,
        auto_resume: bool = True,
        notify_enabled: bool = False,
        notify_kind: str = "",
        notify_webhook: str = "",
        notify_secret: str = "",
        sms_enabled: bool = False,
        sms_phone: str = "",
        sms_token: str = "",
        error: str = None,
        last_result: dict = None,
    ) -> dict:
        rid = str(run_id or "").strip()
        if not rid:
            rid = uuid.uuid4().hex[:32]
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        nm = str(name or "").strip() or None
        st = str(status or "").strip().lower() or "stopped"
        now = datetime.datetime.utcnow()
        try:
            nxt = int(next_exec_ts or 0)
        except Exception:
            nxt = 0
        try:
            off = int(offset_seconds or 5)
        except Exception:
            off = 5
        off = max(0, min(int(off), 120))
        ar = 1 if bool(auto_resume) else 0
        ne = 1 if bool(notify_enabled) else 0
        nk = str(notify_kind or "").strip().lower() or None
        nw = str(notify_webhook or "").strip() or None
        ns = str(notify_secret or "").strip() or None
        se = 1 if bool(sms_enabled) else 0
        sp = str(sms_phone or "").strip() or None
        stoken = str(sms_token or "").strip() or None
        params_json = json.dumps(params or {}, ensure_ascii=False)
        last_result_json = json.dumps(last_result or {}, ensure_ascii=False) if last_result is not None else None
        with self.Session() as session:
            row = session.query(LiveRun).filter(LiveRun.run_id == rid).first()
            if row is None:
                row = LiveRun(run_id=rid)
                session.add(row)
                row.created_at = now
                row.steps = 0
            row.name = nm
            row.sim_id = sid
            row.portfolio_id = pid
            row.status = st
            row.next_exec_ts = int(nxt or 0)
            row.offset_seconds = int(off)
            row.auto_resume = int(ar)
            row.notify_enabled = int(ne)
            row.notify_kind = nk
            row.notify_webhook = nw
            row.notify_secret = ns
            row.sms_enabled = int(se)
            row.sms_phone = sp
            row.sms_token = stoken
            row.params_json = params_json
            if error is not None:
                row.error = str(error)
            if last_result_json is not None:
                row.last_result_json = last_result_json
            row.updated_at = now
            if st != "running":
                row.stopped_at = now
            session.commit()
            return self.get_live_run(rid)

    def get_live_run(self, run_id: str) -> Optional[dict]:
        rid = str(run_id or "").strip()
        if not rid:
            return None
        with self.Session() as session:
            r = session.query(LiveRun).filter(LiveRun.run_id == rid).first()
            if not r:
                return None
            params = None
            if r.params_json:
                try:
                    params = json.loads(r.params_json)
                except Exception:
                    params = None
            last = None
            if r.last_result_json:
                try:
                    last = json.loads(r.last_result_json)
                except Exception:
                    last = None
            return {
                "run_id": str(r.run_id or ""),
                "name": str(r.name or ""),
                "sim_id": str(r.sim_id or ""),
                "portfolio_id": int(r.portfolio_id or 0) or None,
                "status": str(r.status or ""),
                "steps": int(r.steps or 0),
                "last_step_ts": int(r.last_step_ts or 0),
                "next_exec_ts": int(r.next_exec_ts or 0),
                "offset_seconds": int(r.offset_seconds or 0),
                "auto_resume": bool(int(r.auto_resume or 0)),
                "notify_enabled": bool(int(r.notify_enabled or 0)),
                "notify_kind": str(r.notify_kind or ""),
                "notify_webhook": str(r.notify_webhook or ""),
                "notify_secret": str(r.notify_secret or ""),
                "sms_enabled": bool(int(getattr(r, "sms_enabled", 0) or 0)),
                "sms_phone": str(getattr(r, "sms_phone", "") or ""),
                "sms_token": str(getattr(r, "sms_token", "") or ""),
                "params": params,
                "last_result": last,
                "error": str(r.error or ""),
                "created_at": _dt_to_utc_iso(r.created_at),
                "updated_at": _dt_to_utc_iso(r.updated_at),
                "stopped_at": _dt_to_utc_iso(r.stopped_at),
            }

    def list_live_runs(self, status: str = None, limit: int = 200, portfolio_id: int = None, sim_id: str = None) -> list:
        st = str(status or "").strip().lower() or None
        pid = _normalize_portfolio_id(portfolio_id)
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        try:
            lim = int(limit or 200)
        except Exception:
            lim = 200
        lim = max(1, min(lim, 2000))
        with self.Session() as session:
            q = session.query(LiveRun)
            if pid is not None:
                q = q.filter(LiveRun.portfolio_id == pid)
            if sid is not None:
                q = q.filter(LiveRun.sim_id == sid)
            if st:
                q = q.filter(LiveRun.status == st)
            q = q.order_by(LiveRun.updated_at.desc(), LiveRun.created_at.desc())
            rows = q.limit(lim).all()
            out = []
            for r in rows or []:
                out.append({
                    "run_id": str(r.run_id or ""),
                    "name": str(r.name or ""),
                    "sim_id": str(r.sim_id or ""),
                    "portfolio_id": int(r.portfolio_id or 0) or None,
                    "status": str(r.status or ""),
                    "steps": int(r.steps or 0),
                    "last_step_ts": int(r.last_step_ts or 0),
                    "next_exec_ts": int(r.next_exec_ts or 0),
                    "offset_seconds": int(r.offset_seconds or 0),
                    "auto_resume": bool(int(r.auto_resume or 0)),
                    "notify_enabled": bool(int(r.notify_enabled or 0)),
                    "notify_kind": str(r.notify_kind or ""),
                    "notify_webhook": str(r.notify_webhook or ""),
                    "notify_secret": str(r.notify_secret or ""),
                    "sms_enabled": bool(int(getattr(r, "sms_enabled", 0) or 0)),
                    "sms_phone": str(getattr(r, "sms_phone", "") or ""),
                    "sms_token": str(getattr(r, "sms_token", "") or ""),
                    "error": str(r.error or ""),
                    "created_at": _dt_to_utc_iso(r.created_at),
                    "updated_at": _dt_to_utc_iso(r.updated_at),
                })
            return out

    def update_live_run_fields(self, run_id: str, fields: dict) -> Optional[dict]:
        rid = str(run_id or "").strip()
        if not rid:
            return None
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            r = session.query(LiveRun).filter(LiveRun.run_id == rid).first()
            if not r:
                return None
            for k, v in (fields or {}).items():
                if k in ["name", "sim_id", "status", "error", "params_json", "last_result_json"]:
                    setattr(r, k, v)
                elif k in ["portfolio_id", "steps", "last_step_ts", "next_exec_ts", "offset_seconds", "auto_resume", "notify_enabled", "sms_enabled"]:
                    try:
                        setattr(r, k, int(v))
                    except Exception:
                        continue
                elif k in ["notify_kind", "notify_webhook", "notify_secret", "sms_phone", "sms_token"]:
                    setattr(r, k, v)
                elif k in ["stopped_at"]:
                    setattr(r, k, v)
            r.updated_at = now
            session.commit()
        return self.get_live_run(rid)

    def delete_live_run(self, run_id: str) -> bool:
        rid = str(run_id or "").strip()
        if not rid:
            return False
        with self.Session() as session:
            q = session.query(LiveRun).filter(LiveRun.run_id == rid)
            if q.first() is None:
                return False
            session.query(LiveStepRecord).filter(LiveStepRecord.run_id == rid).delete(synchronize_session=False)
            q.delete(synchronize_session=False)
            session.commit()
            return True

    def add_live_step_record(
        self,
        sim_id: str,
        run_id: str,
        step_ts: int,
        day: int,
        portfolio_id: int = None,
        risk: dict = None,
        targets: list = None,
        sell: list = None,
        buy: list = None,
        blocked: list = None,
        summary: dict = None,
        result: dict = None,
        error: str = None,
    ) -> bool:
        sid = _normalize_sim_id(sim_id)
        rid = str(run_id or "").strip() or None
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            st = int(step_ts or 0)
        except Exception:
            st = 0
        try:
            dk = int(day or 0)
        except Exception:
            dk = 0
        now = datetime.datetime.utcnow()
        with self.Session() as session:
            row = LiveStepRecord(
                sim_id=sid,
                portfolio_id=pid,
                run_id=rid,
                step_ts=int(st),
                day=int(dk),
                risk_json=json.dumps(risk or {}, ensure_ascii=False),
                targets_json=json.dumps(targets or [], ensure_ascii=False),
                sell_json=json.dumps(sell or [], ensure_ascii=False),
                buy_json=json.dumps(buy or [], ensure_ascii=False),
                blocked_json=json.dumps(blocked or [], ensure_ascii=False),
                summary_json=json.dumps(summary or {}, ensure_ascii=False),
                result_json=json.dumps(result or {}, ensure_ascii=False) if result is not None else None,
                error=str(error or "") or None,
                created_at=now,
            )
            session.add(row)
            session.commit()
            return True

    def list_live_steps_paged(
        self,
        sim_id: str = None,
        run_id: str = None,
        portfolio_id: int = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        rid = str(run_id or "").strip() if run_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            p = int(page or 1)
        except Exception:
            p = 1
        if p < 1:
            p = 1
        try:
            ps = int(page_size or 50)
        except Exception:
            ps = 50
        if ps < 1:
            ps = 1
        if ps > 500:
            ps = 500
        with self.Session() as session:
            q = session.query(LiveStepRecord)
            if pid is not None:
                q = q.filter(LiveStepRecord.portfolio_id == pid)
            if sid is not None:
                q = q.filter(LiveStepRecord.sim_id == sid)
            if rid:
                q = q.filter(LiveStepRecord.run_id == rid)
            total = q.count()
            q = q.order_by(LiveStepRecord.step_ts.desc(), LiveStepRecord.id.desc())
            q = q.offset((p - 1) * ps).limit(ps)
            rows = q.all()
            items = []
            for r in rows or []:
                def _loads(x):
                    if not x:
                        return None
                    try:
                        return json.loads(x)
                    except Exception:
                        return None
                items.append({
                    "id": int(r.id),
                    "sim_id": str(r.sim_id or ""),
                    "portfolio_id": int(r.portfolio_id or 0) or None,
                    "run_id": str(r.run_id or ""),
                    "step_ts": int(r.step_ts or 0),
                    "day": int(r.day or 0),
                    "risk": _loads(r.risk_json) or {},
                    "targets": _loads(r.targets_json) or [],
                    "sell": _loads(r.sell_json) or [],
                    "buy": _loads(r.buy_json) or [],
                    "blocked": _loads(r.blocked_json) or [],
                    "summary": _loads(r.summary_json) or {},
                    "error": str(r.error or ""),
                    "created_at": _dt_to_utc_iso(r.created_at),
                })
            return {"items": items, "total": int(total), "page": int(p), "page_size": int(ps)}

    def clear_live_steps(self, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        with self.Session() as session:
            q = session.query(LiveStepRecord)
            if pid is not None:
                q = q.filter(LiveStepRecord.portfolio_id == pid)
            if sid is None:
                q.delete(synchronize_session=False)
            else:
                q.filter(LiveStepRecord.sim_id == sid).delete(synchronize_session=False)
            session.commit()

    def list_live_step_stocks_paged(
        self,
        sim_id: str = None,
        run_id: str = None,
        portfolio_id: int = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        rid = str(run_id or "").strip() if run_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            p = int(page or 1)
        except Exception:
            p = 1
        if p < 1:
            p = 1
        try:
            ps = int(page_size or 10)
        except Exception:
            ps = 10
        if ps < 1:
            ps = 1
        if ps > 200:
            ps = 200

        def _loads(x):
            if not x:
                return None
            try:
                return json.loads(x)
            except Exception:
                return None

        with self.Session() as session:
            q = session.query(LiveStepRecord)
            if pid is not None:
                q = q.filter(LiveStepRecord.portfolio_id == pid)
            if sid is not None:
                q = q.filter(LiveStepRecord.sim_id == sid)
            if rid:
                q = q.filter(LiveStepRecord.run_id == rid)
            q = q.order_by(LiveStepRecord.step_ts.desc(), LiveStepRecord.id.desc())
            rows = q.all()

            all_items = []
            for r in rows or []:
                buy = _loads(r.buy_json) or []
                sell = _loads(r.sell_json) or []
                blocked = _loads(r.blocked_json) or []
                err = str(r.error or "")

                seq = []
                side_by_code = {}
                state_by_code = {}

                def _add_code(code: str, side: str):
                    c = str(code or "").strip()
                    if not c:
                        return
                    if c not in side_by_code:
                        seq.append(c)
                    if side in ["buy", "sell"]:
                        if c not in side_by_code:
                            side_by_code[c] = side
                        elif side_by_code[c] not in ["buy", "sell"]:
                            side_by_code[c] = side
                    if c not in state_by_code:
                        state_by_code[c] = {"status": "executed", "reason": ""}

                for c in sell:
                    _add_code(c, "sell")
                for c in buy:
                    _add_code(c, "buy")
                for b in blocked:
                    if not isinstance(b, dict):
                        continue
                    code = str(b.get("stock_code") or "").strip()
                    side = str(b.get("side") or "").strip().lower()
                    _add_code(code, side if side in ["buy", "sell"] else "-")

                for b in blocked:
                    if not isinstance(b, dict):
                        continue
                    code = str(b.get("stock_code") or "").strip()
                    if not code:
                        continue
                    if code not in state_by_code:
                        state_by_code[code] = {"status": "unexecuted", "reason": str(b.get("reason") or "")}
                        if code not in side_by_code:
                            side = str(b.get("side") or "").strip().lower()
                            side_by_code[code] = side if side in ["buy", "sell"] else "-"
                        if code not in seq:
                            seq.append(code)
                    else:
                        state_by_code[code]["status"] = "unexecuted"
                        state_by_code[code]["reason"] = str(b.get("reason") or "")

                if err:
                    if not seq:
                        seq = ["-"]
                        side_by_code["-"] = "-"
                        state_by_code["-"] = {"status": "failed", "reason": err}
                    else:
                        for code in list(state_by_code.keys()):
                            state_by_code[code]["status"] = "failed"
                            state_by_code[code]["reason"] = err

                for code in seq:
                    st = state_by_code.get(code) or {}
                    all_items.append({
                        "step_id": int(r.id),
                        "sim_id": str(r.sim_id or ""),
                        "portfolio_id": int(r.portfolio_id or 0) or None,
                        "run_id": str(r.run_id or ""),
                        "step_ts": int(r.step_ts or 0),
                        "day": int(r.day or 0),
                        "stock_code": str(code or ""),
                        "side": str(side_by_code.get(code) or ""),
                        "status": str(st.get("status") or ""),
                        "reason": str(st.get("reason") or ""),
                        "created_at": _dt_to_utc_iso(r.created_at),
                    })

            total = len(all_items)
            begin = (p - 1) * ps
            end = begin + ps
            items = all_items[begin:end] if begin < total else []
            return {"items": items, "total": int(total), "page": int(p), "page_size": int(ps)}

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
            if str(run.status) in ["running", "pending", "queued"]:
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
                if str(run.status) in ["running", "pending", "queued"]:
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

    def list_backtest_presets(self, limit: int = 200) -> list:
        try:
            lim = int(limit or 200)
        except Exception:
            lim = 200
        lim = max(1, min(int(lim), 2000))

        with self.Session() as session:
            q = session.query(BacktestPreset).order_by(BacktestPreset.updated_at.desc(), BacktestPreset.created_at.desc())
            rows = q.limit(lim).all()
            out = []
            for r in rows or []:
                out.append({
                    "id": int(r.id),
                    "name": str(r.name or ""),
                    "created_at": _dt_to_utc_iso(r.created_at),
                    "updated_at": _dt_to_utc_iso(r.updated_at),
                })
            return out

    def get_backtest_preset(self, preset_id: int) -> Optional[dict]:
        try:
            pid = int(preset_id)
        except Exception:
            pid = 0
        if pid <= 0:
            return None

        with self.Session() as session:
            r = session.query(BacktestPreset).filter(BacktestPreset.id == pid).first()
            if not r:
                return None
            cfg = {}
            prm = {}
            if r.paper_config_json:
                try:
                    cfg = json.loads(r.paper_config_json) or {}
                except Exception:
                    cfg = {}
            if r.paper_params_json:
                try:
                    prm = json.loads(r.paper_params_json) or {}
                except Exception:
                    prm = {}
            return {
                "id": int(r.id),
                "name": str(r.name or ""),
                "paper_config": cfg,
                "paper_params": prm,
                "created_at": _dt_to_utc_iso(r.created_at),
                "updated_at": _dt_to_utc_iso(r.updated_at),
            }

    def upsert_backtest_preset(self, name: str, paper_config: dict, paper_params: dict) -> dict:
        nm = str(name or "").strip()
        if not nm:
            raise ValueError("name required")
        cfg_json = json.dumps(paper_config or {}, ensure_ascii=False)
        prm_json = json.dumps(paper_params or {}, ensure_ascii=False)
        now = datetime.datetime.utcnow()

        with self.Session() as session:
            r = session.query(BacktestPreset).filter(BacktestPreset.name == nm).first()
            if r:
                r.paper_config_json = cfg_json
                r.paper_params_json = prm_json
                r.updated_at = now
                session.commit()
                pid = int(r.id)
            else:
                r = BacktestPreset(
                    name=nm,
                    paper_config_json=cfg_json,
                    paper_params_json=prm_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(r)
                session.commit()
                pid = int(r.id)

        out = self.get_backtest_preset(pid)
        return out or {"id": pid, "name": nm, "paper_config": paper_config or {}, "paper_params": paper_params or {}}

    def get_result_by_id(self, result_id):
        session = self.Session()
        try:
            r = session.query(AnalysisResult).filter(AnalysisResult.id == result_id).first()
            if r:
                return json.loads(r.result_json)
            return None
        finally:
            session.close()
