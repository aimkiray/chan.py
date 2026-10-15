import os
import json
import math
import hashlib
import logging
import datetime
import uuid
from typing import Optional
from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, Index, text, or_, event, literal_column
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()

# Repo root = parent of the backend/ dir. Used to anchor the default db
# path so every entry point opens the same file regardless of CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_UNSET = object()  # sentinel: "value present but unusable" (vs None = clear)

_np = None
_np_loaded = False

def _numpy():
    """Lazy numpy handle.

    storage.py must stay importable without pulling the heavy service
    dependencies; numpy is probed on first _json_safe_value call only.
    """
    global _np, _np_loaded
    if not _np_loaded:
        try:
            import numpy
            _np = numpy
        except Exception:
            _np = None
        _np_loaded = True
    return _np

def _json_safe_value(value, inf_replacement: float = 999.0, _depth: int = 0):
    """Local equivalent of backend.chan_service.json_safe_value.

    storage must NOT import backend.chan_service at runtime (it pulls
    xgboost/lightgbm/sklearn/baostock); the previous lazy import both
    inverted the layering and swallowed every failure, silently disabling
    NaN/numpy cleaning. Keep semantics in sync with that implementation.
    """
    if _depth > 40:
        return None
    if value is None or isinstance(value, (str, bool)):
        return value
    np = _numpy()
    if np is not None:
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            value = float(value)
        elif isinstance(value, np.ndarray):
            return _json_safe_value(value.tolist(), inf_replacement=inf_replacement, _depth=_depth + 1)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        try:
            if math.isnan(value):
                return None
            if math.isinf(value):
                return float(inf_replacement if value > 0 else -inf_replacement)
        except Exception:
            return None
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v, inf_replacement=inf_replacement, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(v, inf_replacement=inf_replacement, _depth=_depth + 1) for v in value]
    # Unknown objects (Decimal, PathLike, ...) are not JSON-serializable;
    # stringify rather than letting them explode in a later json.dumps.
    try:
        return str(value)
    except Exception:
        return repr(value)

def _utc_now():
    """Naive datetime in UTC.

    DateTime columns here are timezone-naive: SQLite DATETIME cannot read
    back an aware value's '+00:00' suffix, and all stored timestamps are
    naive UTC. datetime.utcnow() is deprecated since py3.12; this yields
    the identical naive-UTC value and keeps naive/aware mixing out.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

def _coerce_naive_utc_dt(v):
    """Normalize a caller-supplied datetime-ish value to naive UTC.

    Returns _UNSET for present-but-unparseable input (caller should skip
    the field), None for None (explicit clear), else a naive datetime.
    """
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        if v.tzinfo is not None:
            return v.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return v
    if isinstance(v, bool):
        return _UNSET
    if isinstance(v, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(v), tz=datetime.timezone.utc).replace(tzinfo=None)
        except Exception:
            return _UNSET
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return _UNSET
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return _coerce_naive_utc_dt(datetime.datetime.fromisoformat(s))
        except Exception:
            return _UNSET
    return _UNSET

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

def _json_loads(x, default=None):
    if not x:
        return default
    try:
        return json.loads(x)
    except Exception:
        return default

class _AttrDict(dict):
    """dict that also exposes keys as read-only attributes.

    get_latest_result() used to leak a detached ORM row whose fields were
    accessed as attributes (`.result_json`). Returning a real dict keeps
    the storage contract uniform (every other getter returns dicts)
    without breaking those callers.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

def _live_order_to_dict(r):
    return {
        "id": int(r.id),
        "stock_code": str(r.stock_code or ""),
        "side": str(r.side or ""),
        "status": str(r.status or ""),
        "requested_ts": int(r.requested_ts or 0),
        "executed_ts": int(r.executed_ts or 0),
        "execute_after_ts": int(r.execute_after_ts or 0),
        "min_day": int(r.min_day or 0),
        "price": float(r.price or 0.0),
        "reason": r.reason,
        "created_at": _dt_to_utc_iso(r.created_at),
        "updated_at": _dt_to_utc_iso(r.updated_at),
    }

def _live_trade_to_dict(r):
    return {
        "id": int(r.id),
        "stock_code": str(r.stock_code or ""),
        "side": str(r.side or ""),
        "status": str(r.status or ""),
        "trade_ts": int(r.trade_ts or 0),
        "price": float(r.price or 0.0),
        "meta": _json_loads(r.meta_json),
        "created_at": _dt_to_utc_iso(r.created_at),
    }

def _live_run_to_dict(r, include_payloads=False):
    out = {
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
    }
    if include_payloads:
        out["params"] = _json_loads(r.params_json)
        out["last_result"] = _json_loads(r.last_result_json)
        out["stopped_at"] = _dt_to_utc_iso(r.stopped_at)
    return out

def _live_step_stock_items(r):
    """Fan one LiveStepRecord row out into per-stock status dicts.

    Dedup/order rules here are mirrored by _STEP_STOCKS_COUNT_SQL (which
    paginates at SQL level): a row expands to the distinct non-empty
    stripped stock codes across sell[] + buy[] + blocked[].stock_code —
    sell first, then new buy codes, then new blocked codes — or to a
    single "-" row when the step failed before emitting any code. Keep
    the two in sync when changing this function.
    """
    buy = _json_loads(r.buy_json)
    sell = _json_loads(r.sell_json)
    blocked = _json_loads(r.blocked_json)
    # Only list payloads fan out; corrupt/non-array payloads count as
    # empty (the SQL count expression treats them the same way).
    buy = buy if isinstance(buy, list) else []
    sell = sell if isinstance(sell, list) else []
    blocked = blocked if isinstance(blocked, list) else []
    err = str(r.error or "")

    seq = []
    side_by_code = {}
    state_by_code = {}

    def _add_code(code: str, side: str):
        c = str(code or "").strip()
        if not c:
            return
        # Dedup on state_by_code (set for every added code): keying this
        # off side_by_code used to re-append a code that was first seen
        # via a blocked entry without a buy/sell side.
        if c not in state_by_code:
            seq.append(c)
            state_by_code[c] = {"status": "executed", "reason": ""}
        if side in ["buy", "sell"] and c not in side_by_code:
            side_by_code[c] = side

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

    items = []
    for code in seq:
        st = state_by_code.get(code) or {}
        items.append({
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
    return items

# Per-step emitted-item count for list_live_step_stocks_paged, computed
# in SQL so pagination does not need to materialize/JSON-decode every
# step row. Mirrors _live_step_stock_items: COUNT(DISTINCT trimmed
# non-empty code) across sell[] + buy[] + blocked[].stock_code, and a
# synthetic "-" row when a failed step emitted no code. json_valid +
# json_type guards make corrupt payloads count as empty instead of
# raising "malformed JSON".
_STEP_STOCKS_COUNT_SQL = """(
    SELECT CASE WHEN c.cnt = 0 AND COALESCE(TRIM(live_step_record.error), '') <> '' THEN 1 ELSE c.cnt END
    FROM (
        SELECT COUNT(DISTINCT TRIM(x.code)) AS cnt FROM (
            SELECT e.value AS code FROM json_each(
                CASE WHEN json_valid(live_step_record.sell_json) AND json_type(live_step_record.sell_json) = 'array'
                     THEN live_step_record.sell_json ELSE '[]' END) e
            UNION ALL
            SELECT e.value AS code FROM json_each(
                CASE WHEN json_valid(live_step_record.buy_json) AND json_type(live_step_record.buy_json) = 'array'
                     THEN live_step_record.buy_json ELSE '[]' END) e
            UNION ALL
            SELECT json_extract(e.value, '$.stock_code') AS code FROM json_each(
                CASE WHEN json_valid(live_step_record.blocked_json) AND json_type(live_step_record.blocked_json) = 'array'
                     THEN live_step_record.blocked_json ELSE '[]' END) e
        ) x
        WHERE x.code IS NOT NULL AND TRIM(x.code) <> ''
    ) c
)"""

def _build_live_order(o, sid, pid, status, requested_ts, now):
    """Validate a raw order dict and build a LiveOrder row (None = skip)."""
    if not isinstance(o, dict):
        return None
    code = str(o.get("stock_code") or "").strip()
    side = str(o.get("side") or "").strip().lower()
    if not code or side not in ["buy", "sell"]:
        return None
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
    return LiveOrder(
        sim_id=sid,
        portfolio_id=pid,
        stock_code=code,
        side=side,
        status=status,
        requested_ts=requested_ts,
        execute_after_ts=execute_after_ts,
        min_day=min_day,
        price=price,
        reason=o.get("reason"),
        created_at=now,
        updated_at=now,
    )

def _pretrained_model_to_dict(r):
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
        "accuracy": _json_loads(r.accuracy_json),
    }

def _pretrain_job_to_dict(r):
    if not r:
        return None
    return {
        "job_id": r.job_id,
        "status": r.status,
        "progress": r.progress,
        "stage": r.stage,
        "message": r.message,
        "detail": r.detail,
        "result": _json_loads(r.result_json),
        "request": _json_loads(r.request_json),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }

def _column_default_sql(col):
    """Literal for a DEFAULT clause in ALTER TABLE ADD COLUMN, or None.

    Only scalar / server defaults are usable in a SQLite ALTER; callable
    defaults (e.g. _utc_now) are skipped on purpose.
    """
    arg = None
    if col.server_default is not None:
        arg = getattr(col.server_default, "arg", None)
    elif col.default is not None and getattr(col.default, "is_scalar", False):
        arg = col.default.arg
    if arg is None:
        return None
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return str(arg)

def _sqlite_connect(dbapi_conn, _connection_record):
    """Per-connection pragmas for this SQLite engine.

    journal_mode=WAL persists in the db file header, so the first
    connection flips it for every other StorageManager instance/process
    too (main.py global, strategy_runner worker, paper_trade, tools all
    open the same file). Under WAL readers are not blocked by the writer;
    concurrent writers still serialize on SQLite's single write lock, but
    busy_timeout makes them wait up to 15s instead of raising
    'database is locked' immediately. synchronous=NORMAL stays crash-safe
    under WAL while avoiding an fsync per commit.
    """
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        logger.debug("sqlite pragma setup failed: %s", e)
    finally:
        cur.close()

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
    created_at = Column(DateTime, default=_utc_now)
    
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

    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now)

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

    created_at_dt = Column(DateTime, default=_utc_now)
    updated_at_dt = Column(DateTime, default=_utc_now)

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
    created_at = Column(DateTime, default=_utc_now)
    completed_at = Column(DateTime)

class BacktestPreset(Base):
    __tablename__ = 'backtest_preset'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, index=True)
    paper_config_json = Column(Text)
    paper_params_json = Column(Text)
    created_at = Column(DateTime, default=_utc_now, index=True)
    updated_at = Column(DateTime, default=_utc_now, index=True)

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
    # Fill timestamp for executed orders — mark_orders_executed() used to
    # stuff it into requested_ts when that was empty; kept separate so
    # request time and fill time are not conflated.
    executed_ts = Column(Integer)
    execute_after_ts = Column(Integer, index=True)
    min_day = Column(Integer, index=True)
    price = Column(Float)
    reason = Column(Text)
    created_at = Column(DateTime, default=_utc_now, index=True)
    updated_at = Column(DateTime, default=_utc_now, index=True)

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
    created_at = Column(DateTime, default=_utc_now, index=True)

class LiveSim(Base):
    __tablename__ = "live_sim"

    sim_id = Column(String(40), primary_key=True)
    name = Column(String(120), index=True)
    created_at = Column(DateTime, default=_utc_now, index=True)
    updated_at = Column(DateTime, default=_utc_now, index=True)
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
    created_at = Column(DateTime, default=_utc_now, index=True)
    updated_at = Column(DateTime, default=_utc_now, index=True)
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
    created_at = Column(DateTime, default=_utc_now, index=True)

    __table_args__ = (
        Index("idx_live_step_sim_ts", "sim_id", "step_ts"),
        Index("idx_live_step_run_ts", "run_id", "step_ts"),
        Index("idx_live_step_sim_day", "sim_id", "day"),
    )

class LivePosition(Base):
    __tablename__ = "live_position"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_id = Column(String(40), index=True)
    # "no portfolio" is stored as the 0 sentinel (never NULL): SQLite
    # treats NULLs as distinct inside UNIQUE indexes, which used to let
    # duplicate (sim_id, NULL, stock_code) rows in. NOT NULL only applies
    # to fresh/ancient DBs (SQLite cannot tighten an existing column
    # without a table rebuild); on current DBs the write path and the
    # NULL->0 data migration in _ensure_schema enforce the same invariant.
    portfolio_id = Column(Integer, index=True, nullable=False, default=0, server_default="0")
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
    def __init__(self, db_path=None):
        # Relative paths resolve against the repo root (not os.getcwd()) so
        # the service, strategy_runner workers, paper_trade and tools all
        # open the same file regardless of the launch directory. Absolute
        # paths and the env override are honored as-is.
        if db_path is None:
            db_path = os.environ.get("MLCHAN_ANALYSIS_DB") or "analysis.db"
        if not os.path.isabs(db_path):
            db_path = os.path.join(_REPO_ROOT, db_path)
        self.db_path = db_path

        # timeout=15 is the sqlite3-level busy timeout; the connect hook
        # additionally switches the file to WAL + sets busy_timeout (see
        # _sqlite_connect) so concurrent writers across the multiple
        # StorageManager instances/processes wait instead of erroring.
        self.engine = create_engine(
            f'sqlite:///{db_path}',
            connect_args={'check_same_thread': False, 'timeout': 15},
        )
        event.listen(self.engine, "connect", _sqlite_connect)
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self.Session = sessionmaker(bind=self.engine)

    def _begin_immediate(self, session):
        """Open the session's transaction as BEGIN IMMEDIATE.

        pysqlite only emits BEGIN before the first DML, so a leading SELECT
        runs in autocommit — outside any transaction. A read-modify-write
        ("SELECT the row, then UPDATE/DELETE what we read") is therefore not
        snapshot-protected: a second writer can commit between the SELECT
        and our write and be silently overwritten (lost update), and a
        check-then-delete gate can act on a row that already changed.
        BEGIN IMMEDIATE takes SQLite's RESERVED (write) lock up front, so
        the whole read-modify-write is serialized; contenders wait on
        busy_timeout(15s) instead of racing. Under WAL, readers are not
        affected. (Guarding each UPDATE with a CAS predicate on the value
        we read — portfolio_service's _StaleRow approach — is the
        alternative; IMMEDIATE also protects the branch-on-read decisions
        a CAS cannot cover, and is one line per path.)
        """
        session.execute(text("BEGIN IMMEDIATE"))

    def _upsert_retry(self, work, attempts: int = 3):
        """Run work(session) in a BEGIN IMMEDIATE transaction, retrying on
        retryable write conflicts.

        The write paths below read before they write (upserts SELECT the
        row then INSERT or UPDATE it; deletes SELECT a gate then DELETE).
        BEGIN IMMEDIATE serializes each read-modify-write so the SELECT is
        never stale by the time we write. Two conflicts can still surface:

        * IntegrityError: a second writer inserted the same key between
          our SELECT and our commit. Rare now (the write lock serializes
          the contenders), but still reachable — e.g. on a DB whose
          UNIQUE index predates _migrate_indexes. The retry re-reads and
          updates the winner's row (last-writer-wins).
        * OperationalError 'database is locked': BEGIN IMMEDIATE or a
          mid-transaction write contended past busy_timeout. Transient —
          retry on a fresh transaction. Other OperationalErrors
          (disk full, no such table, ...) propagate immediately.

        work() must commit() the passed session itself so the failure
        point is inside the retried block, and must be idempotent.
        """
        last_exc = None
        for _ in range(max(1, int(attempts))):
            with self.Session() as session:
                try:
                    self._begin_immediate(session)
                    return work(session)
                except IntegrityError as e:
                    session.rollback()
                    last_exc = e
                except OperationalError as e:
                    session.rollback()
                    msg = str(e).lower()
                    if "locked" not in msg and "busy" not in msg:
                        raise
                    last_exc = e
        raise last_exc

    def _ensure_schema(self):
        """Idempotent migration pass for DBs created before the current ORM
        schema.

        Base.metadata.create_all() above already creates every ORM table —
        and, for tables it creates, their indexes — on fresh DBs, so the old
        ~300 lines of CREATE TABLE IF NOT EXISTS DDL here were dead code
        (and had drifted: e.g. its live_position DDL lacked entry_day). What
        remains is only what CREATE TABLE cannot fix on *existing* tables:

        1. ADD COLUMN for columns an older DB is missing — driven from the
           ORM metadata so every table is covered, each ALTER individually
           guarded (the old code had bare ALTERs that aborted all remaining
           migrations on first failure, and silently swallowed index
           failures sharing a try block).
        2. Data fixups required before constraints can be built:
           live_position.portfolio_id NULL -> 0 sentinel plus dedupe of the
           duplicate rows that may already exist, so the unique index can
           actually be created.
        3. CREATE INDEX IF NOT EXISTS for ORM indexes an old DB predates
           (create_all does not add indexes to pre-existing tables); each
           guarded so one failure does not skip the rest. This includes the
           UNIQUE indexes backing the key columns the upsert retry path
           relies on.

        The whole pass is a no-op on current schemas, so it is cheap to
        re-run on every boot without a version ladder; PRAGMA
        user_version = 1 marks the migrated epoch should a future schema
        change need versioning.
        """
        try:
            with self.engine.connect() as conn:
                self._migrate_missing_columns(conn)
                self._migrate_live_position_portfolio_id(conn)
                self._migrate_indexes(conn)
                conn.execute(text("PRAGMA user_version = 1"))
                conn.commit()
        except Exception as e:
            logger.warning("Failed to ensure schema: %s", e)

    def _migrate_missing_columns(self, conn):
        for table in Base.metadata.sorted_tables:
            try:
                existing = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table.name}")')).fetchall()}
            except Exception as e:
                logger.warning("schema migration: cannot inspect %s: %s", table.name, e)
                continue
            for col in table.columns:
                if col.name in existing:
                    continue
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=self.engine.dialect)}'
                default_sql = _column_default_sql(col)
                if default_sql is not None:
                    stmt += f" DEFAULT {default_sql}"
                # SQLite rejects ADD COLUMN ... NOT NULL without a constant
                # default; degrade to a nullable column rather than fail.
                if not col.nullable and default_sql is not None:
                    stmt += " NOT NULL"
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    logger.warning("schema migration: ADD COLUMN %s.%s failed: %s", table.name, col.name, e)

    def _migrate_live_position_portfolio_id(self, conn):
        # NULL portfolio_id defeats the (sim_id, portfolio_id, stock_code)
        # UNIQUE index — SQLite treats NULLs as distinct. Normalize to the
        # 0 sentinel first, then drop the duplicate rows that may already
        # exist (keeping the lowest id) so the index can be built.
        try:
            conn.execute(text("UPDATE live_position SET portfolio_id = 0 WHERE portfolio_id IS NULL"))
            conn.execute(text(
                "DELETE FROM live_position WHERE id NOT IN ("
                "SELECT MIN(id) FROM live_position GROUP BY sim_id, portfolio_id, stock_code)"
            ))
        except Exception as e:
            logger.warning("schema migration: live_position portfolio_id normalization failed: %s", e)

    def _migrate_indexes(self, conn):
        # Legacy index superseded by idx_live_position_sim_pid_code_unique.
        try:
            conn.execute(text("DROP INDEX IF EXISTS idx_live_position_sim_code_unique"))
        except Exception:
            pass
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                unique = "UNIQUE " if index.unique else ""
                cols = ", ".join(f'"{c.name}"' for c in index.columns)
                name = index.name or f'ix_{table.name}_{"_".join(c.name for c in index.columns)}'
                try:
                    conn.execute(text(f'CREATE {unique}INDEX IF NOT EXISTS "{name}" ON "{table.name}" ({cols})'))
                except Exception as e:
                    logger.warning("schema migration: CREATE INDEX %s failed: %s", name, e)

    # _ensure_schema helpers live above; dead CREATE TABLE DDL removed —
    # create_all owns table creation.

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
        now = _utc_now().isoformat() + "Z"
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

        use_position_status = _is_default_sim(sid) and pid is None
        # live_position's UNIQUE(sim_id, portfolio_id, stock_code) cannot
        # index NULL (SQLite treats NULLs as distinct), so "no portfolio"
        # is stored as the 0 sentinel — never NULL — for the constraint
        # to be effective. PositionStatus lookups stay keyed on code only.
        stored_pid = pid if pid is not None else 0

        def work(session):
            if use_position_status:
                row = session.query(PositionStatus).filter_by(stock_code=stock_code).first()
                if row is None:
                    row = PositionStatus(stock_code=stock_code)
                    session.add(row)
            else:
                row = session.query(LivePosition).filter_by(sim_id=sid, portfolio_id=stored_pid, stock_code=stock_code).first()
                if row is None:
                    row = LivePosition(sim_id=sid, portfolio_id=stored_pid, stock_code=stock_code)
                    session.add(row)

            row.entry_time = int(entry_time or 0)
            if entry_day is not None:
                row.entry_day = int(entry_day)
            row.entry_price = float(entry_price or 0.0)
            row.bars_held = int(bars_held or 0)
            if not use_position_status:
                # PositionStatus (the legacy default-sim table) has no
                # volume/stock_name columns — explicitly skipped here
                # instead of silently assigned to unmapped ORM attributes
                # and dropped at commit.
                if vol > 0:
                    row.volume = int(vol)
                if stock_name:
                    row.stock_name = str(stock_name)
            row.updated_at = now
            session.commit()

        self._upsert_retry(work)

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
        now = _utc_now()

        def work(session):
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row:
                row.balance = float(balance)
                row.updated_at = now
                session.commit()

        # Read-modify-write — route through _upsert_retry so the SELECT is
        # covered by BEGIN IMMEDIATE instead of running in autocommit.
        self._upsert_retry(work)

    def update_live_sim_initial_capital(self, sim_id: str, initial_capital: float, reset_balance: bool = True):
        sid = _normalize_sim_id(sim_id)
        now = _utc_now()
        try:
            cap = float(initial_capital)
        except Exception:
            cap = None
        if cap is None or cap <= 0:
            return

        def work(session):
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row is None:
                row = LiveSim(sim_id=sid)
                session.add(row)
                row.created_at = now
            row.initial_capital = float(cap)
            if reset_balance:
                row.balance = float(cap)
            row.updated_at = now
            session.commit()

        self._upsert_retry(work)


    def increment_bars_held(self, inc: int = 1, sim_id: str = None, portfolio_id: int = None):
        now = _utc_now().isoformat() + "Z"
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            inc = int(inc)
        except Exception:
            inc = 1
        if inc <= 0:
            inc = 1

        def work(session):
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

        # bars_held = read value + inc — a genuine read-modify-write where
        # a concurrent commit between SELECT and UPDATE silently drops an
        # increment. _upsert_retry's BEGIN IMMEDIATE serializes it.
        self._upsert_retry(work)

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
            return [_live_order_to_dict(r) for r in rows or []]

    def _replace_live_orders(self, orders: list, requested_ts: int, status: str, sid, pid):
        requested_ts = int(requested_ts or 0)
        now = _utc_now()
        with self.Session() as session:
            q = session.query(LiveOrder).filter(LiveOrder.status == status)
            if pid is not None:
                q = q.filter(LiveOrder.portfolio_id == pid)
            if _is_default_sim(sid):
                q = q.filter(or_(LiveOrder.sim_id == None, LiveOrder.sim_id == "", LiveOrder.sim_id == "default"))
            else:
                q = q.filter(LiveOrder.sim_id == sid)
            q.delete(synchronize_session=False)
            for o in orders or []:
                row = _build_live_order(o, sid, pid, status, requested_ts, now)
                if row is not None:
                    session.add(row)
            session.commit()

    def replace_pending_live_orders(self, orders: list, requested_ts: int, sim_id: str = None, portfolio_id: int = None):
        self._replace_live_orders(
            orders, requested_ts, "pending",
            _normalize_sim_id(sim_id), _normalize_portfolio_id(portfolio_id),
        )

    def mark_orders_executed(self, order_ids: list, executed_ts: int, sim_id: str = None, portfolio_id: int = None):
        sid = _normalize_sim_id(sim_id)
        pid = _normalize_portfolio_id(portfolio_id)
        now = _utc_now()
        try:
            ids = [int(x) for x in (order_ids or [])]
        except Exception:
            ids = []
        ids = [x for x in ids if x > 0]
        if not ids:
            return

        def work(session):
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
                # Fill time goes to the dedicated executed_ts column;
                # requested_ts keeps the request time (the old code stuffed
                # the fill time into requested_ts when it was empty, which
                # conflated the two fields).
                if executed_ts:
                    r.executed_ts = int(executed_ts)
            session.commit()

        # The id set was computed from a read — keep read+update inside one
        # IMMEDIATE transaction so the rows can't change under us.
        self._upsert_retry(work)

    def replace_blocked_live_orders(self, orders: list, requested_ts: int, sim_id: str = None, portfolio_id: int = None):
        self._replace_live_orders(
            orders, requested_ts, "blocked",
            _normalize_sim_id(sim_id), _normalize_portfolio_id(portfolio_id),
        )

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
        now = _utc_now()
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
                        meta_json = json.dumps(_json_safe_value(meta), ensure_ascii=False, allow_nan=False)
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
            return [_live_trade_to_dict(r) for r in rows or []]

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

            return {"items": [_live_trade_to_dict(r) for r in rows or []], "total": int(total), "page": int(p), "page_size": int(ps)}

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
        now = _utc_now()

        def work(session):
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row is None:
                row = LiveSim(sim_id=sid)
                session.add(row)
                row.created_at = now
            row.name = nm
            row.updated_at = now
            session.commit()
            return {"sim_id": sid, "name": nm, "created_at": _dt_to_utc_iso(row.created_at), "updated_at": _dt_to_utc_iso(row.updated_at)}

        return self._upsert_retry(work)

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
        now = _utc_now()
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
        params_json = json.dumps(_json_safe_value(params or {}), ensure_ascii=False, allow_nan=False)
        last_result_json = json.dumps(_json_safe_value(last_result), ensure_ascii=False, allow_nan=False) if last_result is not None else None

        def work(session):
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

        self._upsert_retry(work)
        return self.get_live_run(rid)

    def get_live_run(self, run_id: str) -> Optional[dict]:
        rid = str(run_id or "").strip()
        if not rid:
            return None
        with self.Session() as session:
            r = session.query(LiveRun).filter(LiveRun.run_id == rid).first()
            if not r:
                return None
            return _live_run_to_dict(r, include_payloads=True)

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
            return [_live_run_to_dict(r) for r in rows or []]

    def update_live_run_fields(self, run_id: str, fields: dict) -> Optional[dict]:
        rid = str(run_id or "").strip()
        if not rid:
            return None
        now = _utc_now()

        def work(session):
            r = session.query(LiveRun).filter(LiveRun.run_id == rid).first()
            if not r:
                return False
            # Whitelisted fields go through the same normalization as
            # upsert_live_run instead of bare setattr.
            for k, v in (fields or {}).items():
                if k == "name":
                    r.name = str(v or "").strip() or None
                elif k == "sim_id":
                    r.sim_id = _normalize_sim_id(v)
                elif k == "status":
                    st = str(v or "").strip().lower()
                    if st:
                        r.status = st
                elif k == "error":
                    r.error = str(v or "") or None
                elif k in ("params", "params_json", "last_result_json"):
                    # "params" is the dict form callers pass (it used to
                    # miss the whitelist and be silently dropped); *_json
                    # keys accept a valid JSON string verbatim or serialize
                    # any other value through _json_safe_value.
                    target = "params_json" if k == "params" else k
                    if v is None:
                        setattr(r, target, None)
                    elif isinstance(v, str):
                        try:
                            json.loads(v)
                            setattr(r, target, v)
                        except Exception:
                            logger.warning("update_live_run_fields: dropping invalid %s for run %s", k, rid)
                    else:
                        setattr(r, target, json.dumps(_json_safe_value(v), ensure_ascii=False, allow_nan=False))
                elif k == "portfolio_id":
                    r.portfolio_id = _normalize_portfolio_id(v)
                elif k in ["steps", "last_step_ts", "next_exec_ts", "offset_seconds", "auto_resume", "notify_enabled", "sms_enabled"]:
                    try:
                        setattr(r, k, int(v))
                    except Exception:
                        continue
                elif k in ["notify_kind", "notify_webhook", "notify_secret", "sms_phone", "sms_token"]:
                    setattr(r, k, str(v or "").strip() or None)
                elif k == "stopped_at":
                    dt = _coerce_naive_utc_dt(v)
                    if dt is not _UNSET:
                        r.stopped_at = dt
            r.updated_at = now
            session.commit()
            return True

        # Read-modify-write of a whitelisted field set — BEGIN IMMEDIATE via
        # _upsert_retry keeps the row stable between SELECT and commit.
        if not self._upsert_retry(work):
            return None
        return self.get_live_run(rid)

    def delete_live_run(self, run_id: str) -> bool:
        rid = str(run_id or "").strip()
        if not rid:
            return False

        def work(session):
            q = session.query(LiveRun).filter(LiveRun.run_id == rid)
            if q.first() is None:
                return False
            session.query(LiveStepRecord).filter(LiveStepRecord.run_id == rid).delete(synchronize_session=False)
            q.delete(synchronize_session=False)
            session.commit()
            return True

        # Check-then-delete: BEGIN IMMEDIATE keeps the existence check and
        # the delete inside one serialized transaction.
        return self._upsert_retry(work)

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
        now = _utc_now()
        with self.Session() as session:
            row = LiveStepRecord(
                sim_id=sid,
                portfolio_id=pid,
                run_id=rid,
                step_ts=int(st),
                day=int(dk),
                # All payloads go through _json_safe_value so NaN/numpy
                # values can't explode json.dumps at write time.
                risk_json=json.dumps(_json_safe_value(risk or {}), ensure_ascii=False, allow_nan=False),
                targets_json=json.dumps(_json_safe_value(targets or []), ensure_ascii=False, allow_nan=False),
                sell_json=json.dumps(_json_safe_value(sell or []), ensure_ascii=False, allow_nan=False),
                buy_json=json.dumps(_json_safe_value(buy or []), ensure_ascii=False, allow_nan=False),
                blocked_json=json.dumps(_json_safe_value(blocked or []), ensure_ascii=False, allow_nan=False),
                summary_json=json.dumps(_json_safe_value(summary or {}), ensure_ascii=False, allow_nan=False),
                result_json=json.dumps(_json_safe_value(result), ensure_ascii=False, allow_nan=False) if result is not None else None,
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
                _loads = _json_loads
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

    def list_live_step_results(
        self,
        sim_id: str = None,
        run_id: str = None,
        portfolio_id: int = None,
        limit: int = 2000,
    ) -> list:
        sid = _normalize_sim_id(sim_id) if sim_id is not None else None
        rid = str(run_id or "").strip() if run_id is not None else None
        pid = _normalize_portfolio_id(portfolio_id)
        try:
            lim = int(limit or 2000)
        except Exception:
            lim = 2000
        lim = max(1, min(lim, 2000))
        with self.Session() as session:
            q = session.query(LiveStepRecord)
            if pid is not None:
                q = q.filter(LiveStepRecord.portfolio_id == pid)
            if sid is not None:
                q = q.filter(LiveStepRecord.sim_id == sid)
            if rid:
                q = q.filter(LiveStepRecord.run_id == rid)
            q = q.order_by(LiveStepRecord.step_ts.desc(), LiveStepRecord.id.desc())
            rows = q.limit(lim).all()
            items = []
            for r in rows or []:
                result = None
                if r.result_json:
                    try:
                        result = json.loads(r.result_json)
                    except Exception:
                        result = None
                items.append({
                    "id": int(r.id),
                    "sim_id": str(r.sim_id or ""),
                    "portfolio_id": int(r.portfolio_id or 0) or None,
                    "run_id": str(r.run_id or ""),
                    "step_ts": int(r.step_ts or 0),
                    "day": int(r.day or 0),
                    "result": result if isinstance(result, dict) else None,
                })
            return items

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
        begin = (p - 1) * ps
        end = begin + ps

        with self.Session() as session:
            # Two-phase paging: each step row fans out to N stock items in
            # Python, so a plain LIMIT can't slice the result. Phase 1 asks
            # SQLite for just (id, emitted-item count) — json_each runs in C
            # over the small buy/sell/blocked columns and big payloads like
            # result_json are never read. Phase 2 materializes + decodes
            # only the step rows overlapping this page (O(page) rows, not
            # O(table) per request as before).
            q = session.query(
                LiveStepRecord.id,
                literal_column(_STEP_STOCKS_COUNT_SQL).label("item_cnt"),
            )
            if pid is not None:
                q = q.filter(LiveStepRecord.portfolio_id == pid)
            if sid is not None:
                q = q.filter(LiveStepRecord.sim_id == sid)
            if rid:
                q = q.filter(LiveStepRecord.run_id == rid)
            q = q.order_by(LiveStepRecord.step_ts.desc(), LiveStepRecord.id.desc())

            total = 0
            start_by_id = {}
            order = []
            for step_id, cnt in q.all():
                cnt = int(cnt or 0)
                # step's items occupy the global window [total, total+cnt)
                if cnt > 0 and total < end and total + cnt > begin:
                    sid_i = int(step_id)
                    start_by_id[sid_i] = total
                    order.append(sid_i)
                total += cnt

            items = []
            if order:
                rows = (
                    session.query(LiveStepRecord)
                    .filter(LiveStepRecord.id.in_(order))
                    .order_by(LiveStepRecord.step_ts.desc(), LiveStepRecord.id.desc())
                    .all()
                )
                for r in rows or []:
                    pos = start_by_id.get(int(r.id))
                    if pos is None:
                        continue
                    row_items = _live_step_stock_items(r)
                    items.extend(row_items[max(0, begin - pos):max(0, end - pos)])

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
        try:
            now = _utc_now()
            params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
            params_hash = hashlib.md5(params_json.encode('utf-8')).hexdigest()

            def work(session):
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
                record.accuracy_json = json.dumps(_json_safe_value(acc), ensure_ascii=False, allow_nan=False) if acc is not None else None

                record.updated_at = now
                if record.created_at is None:
                    record.created_at = now

                session.flush()
                record_id = record.id
                session.commit()
                return record_id

            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to upsert pretrained model: %s", e)
            return None

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
        mk = str(model_key or "").strip()
        if not mk:
            return False
        try:
            def work(session):
                record = session.query(PretrainedModel).filter(PretrainedModel.model_key == mk).first()
                if record is None:
                    record = PretrainedModel(model_key=mk)
                    session.add(record)
                record.name = name
                record.updated_at = _utc_now()
                if record.created_at is None:
                    record.created_at = record.updated_at
                session.commit()
                return True

            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to set pretrained model name: %s", e)
            return False

    def delete_pretrained_model(self, model_key):
        mk = str(model_key or "").strip()
        if not mk:
            return False

        def work(session):
            q = session.query(PretrainedModel).filter(PretrainedModel.model_key == mk)
            if q.first() is None:
                return False
            q.delete()
            session.commit()
            return True

        try:
            # Check-then-delete — serialized via BEGIN IMMEDIATE in
            # _upsert_retry (see its docstring).
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to delete pretrained model: %s", e)
            return False

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
            items = [_pretrained_model_to_dict(r) for r in q.all()]
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
            return _pretrained_model_to_dict(r)
        finally:
            session.close()

    def upsert_pretrain_job(self, job: dict):
        if not isinstance(job, dict):
            return False
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return False

        def work(session):
            now = _utc_now()
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

            record.request_json = json.dumps(_json_safe_value(job.get("request", None)), ensure_ascii=False, allow_nan=False) if job.get("request", None) is not None else None
            record.result_json = json.dumps(_json_safe_value(job.get("result", None)), ensure_ascii=False, allow_nan=False) if job.get("result", None) is not None else None

            record.created_at = str(job.get("created_at") or "").strip() or record.created_at
            record.updated_at = str(job.get("updated_at") or "").strip() or record.updated_at

            if record.created_at_dt is None:
                record.created_at_dt = now
            record.updated_at_dt = now

            session.commit()
            return True

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to upsert pretrain job: %s", e)
            return False

    def get_pretrain_job(self, job_id: str):
        session = self.Session()
        try:
            jid = str(job_id or "").strip()
            if not jid:
                return None
            r = session.query(PretrainJob).filter(PretrainJob.job_id == jid).first()
            if not r:
                return None
            return _pretrain_job_to_dict(r)
        finally:
            session.close()

    def list_pretrain_jobs(self, page=1, page_size=50, limit=None, include_success=True):
        session = self.Session()
        try:
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
                items = [_pretrain_job_to_dict(r) for r in q.all()]
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
            items = [_pretrain_job_to_dict(r) for r in q.all()]
            items = [i for i in items if i is not None]
            return {"items": items, "total": total, "page": p, "page_size": ps}
        finally:
            session.close()

    def delete_pretrain_job(self, job_id: str):
        jid = str(job_id or "").strip()
        if not jid:
            return False

        def work(session):
            q = session.query(PretrainJob).filter(PretrainJob.job_id == jid)
            if q.first() is None:
                return False
            q.delete()
            session.commit()
            return True

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to delete pretrain job: %s", e)
            return False

    def prune_pretrain_jobs(self, max_jobs: int, ttl_sec: int):
        try:
            max_jobs = int(max_jobs or 0)
            ttl_sec = int(ttl_sec or 0)
        except Exception:
            max_jobs = 0
            ttl_sec = 0
        if max_jobs < 1 and ttl_sec < 1:
            return {"deleted_by_ttl": 0, "deleted_by_cap": 0}

        def work(session):
            deleted_by_ttl = 0
            deleted_by_cap = 0

            if ttl_sec > 0:
                now = _utc_now()
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

        try:
            # The cap branch selects rows then deletes them — BEGIN
            # IMMEDIATE keeps that read stable inside the write txn.
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to prune pretrain jobs: %s", e)
            return {"deleted_by_ttl": 0, "deleted_by_cap": 0}

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
        try:
            rid = int(run_id)
        except Exception:
            return {"deleted": False, "reason": "invalid run_id"}

        def work(session):
            run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
            if not run:
                return {"deleted": False, "reason": "not_found"}
            # Status gate: refuse to delete an active run. Without a
            # serialized txn a concurrent status flip could land between
            # this SELECT and the delete; BEGIN IMMEDIATE closes that gap.
            if str(run.status) in ["running", "pending", "queued"]:
                return {"deleted": False, "reason": "running"}
            session.delete(run)
            session.commit()
            return {"deleted": True}

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to delete strategy run: %s", e)
            return {"deleted": False, "reason": str(e)}

    def batch_delete_strategy_runs(self, run_ids):
        ids = []
        for x in (run_ids or []):
            try:
                ids.append(int(x))
            except Exception:
                continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {"deleted": [], "failed": []}

        def work(session):
            deleted = []
            failed = []
            for rid in ids:
                run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
                if not run:
                    failed.append({"run_id": rid, "reason": "not_found"})
                    continue
                # Same status gate as delete_strategy_run — BEGIN
                # IMMEDIATE serializes the check against the delete.
                if str(run.status) in ["running", "pending", "queued"]:
                    failed.append({"run_id": rid, "reason": "running"})
                    continue
                session.delete(run)
                deleted.append(rid)

            session.commit()
            return {"deleted": deleted, "failed": failed}

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to batch delete strategy runs: %s", e)
            return {"deleted": [], "failed": [{"run_id": None, "reason": str(e)}]}

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
        def work(session):
            cache = session.query(StrategyCache).filter(StrategyCache.key_hash == key_hash).first()
            if not cache:
                cache = StrategyCache(key_hash=key_hash)
                session.add(cache)

            cache.code = code
            cache.accuracy = float(accuracy)
            cache.bsp_count = int(bsp_count)
            cache.method = str(method)
            cache.result_json = json.dumps(_json_safe_value(result), allow_nan=False) if result else None
            cache.updated_at = _dt_to_utc_iso(_utc_now())
            cache.start_time = str(start_time)
            cache.end_time = str(end_time)
            cache.params_json = json.dumps(params)

            session.commit()

        try:
            self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to save strategy cache: %s", e)

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
                result_json=json.dumps(_json_safe_value(result_dict), allow_nan=False),
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
            logger.exception("Failed to save result: %s", e)
            session.rollback()
        finally:
            session.close()

    def get_latest_result(self, code, freq, params, data_latest_time, begin_time=None):
        session = self.Session()
        try:
            params_json = json.dumps(params, sort_keys=True)
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

            r = q.order_by(AnalysisResult.created_at.desc()).first()
            if not r:
                return None
            # Return a plain dict like every other getter (this used to leak
            # a detached ORM instance). _AttrDict keeps `.result_json`
            # attribute access working for existing callers.
            return _AttrDict({
                "id": r.id,
                "code": r.code,
                "frequency": r.frequency,
                "params_hash": r.params_hash,
                "params_json": r.params_json,
                "data_latest_time": r.data_latest_time,
                "begin_time": r.begin_time,
                "end_time": r.end_time,
                "created_at": _dt_to_utc_iso(r.created_at),
                "result_json": r.result_json,
                "model": r.model,
                "signal_type": r.signal_type,
                "is_buy": r.is_buy,
                "accuracy": r.accuracy,
            })
        finally:
            session.close()

    def get_history(self, code=None, page=1, page_size=10):
        session = self.Session()
        try:
            # Converge page/page_size to bounded ints like the other pagers.
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
            ps = max(1, min(ps, 500))

            q = session.query(AnalysisResult)

            # Filter: Only show records with AI analysis (indicated by accuracy not being None)
            q = q.filter(AnalysisResult.accuracy != None)

            if code:
                q = q.filter(AnalysisResult.code == code)

            total = q.count()

            q = q.order_by(AnalysisResult.created_at.desc())
            q = q.offset((p - 1) * ps).limit(ps)

            results = []
            for r in q.all():
                ca = r.created_at
                results.append({
                    "id": r.id,
                    "code": r.code,
                    "frequency": r.frequency,
                    "created_at": ca.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ca, datetime.datetime) else str(ca or ""),
                    "model": r.model,
                    "signal_type": r.signal_type,
                    "is_buy": r.is_buy,
                    "accuracy": r.accuracy,
                    "data_latest_time": r.data_latest_time,
                    # We don't send full result_json in list to save bandwidth
                })
            return {"items": results, "total": total, "page": p, "page_size": ps}
        finally:
            session.close()

    def delete_result(self, result_id):
        try:
            rid = int(result_id)
        except Exception:
            return False

        def work(session):
            q = session.query(AnalysisResult).filter(AnalysisResult.id == rid)
            if q.first():
                q.delete()
                session.commit()
                return True
            return False

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to delete result: %s", e)
            return False

    def batch_delete_results(self, result_ids):
        ids = []
        for x in (result_ids or []):
            try:
                ids.append(int(x))
            except Exception:
                continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {"deleted": [], "failed": []}

        def work(session):
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

        try:
            return self._upsert_retry(work)
        except Exception as e:
            logger.exception("Failed to batch delete results: %s", e)
            return {"deleted": [], "failed": [{"result_id": None, "reason": str(e)}]}

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
            cfg = _json_loads(r.paper_config_json, {}) or {}
            prm = _json_loads(r.paper_params_json, {}) or {}
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
        now = _utc_now()

        def work(session):
            r = session.query(BacktestPreset).filter(BacktestPreset.name == nm).first()
            if r:
                r.paper_config_json = cfg_json
                r.paper_params_json = prm_json
                r.updated_at = now
            else:
                r = BacktestPreset(
                    name=nm,
                    paper_config_json=cfg_json,
                    paper_params_json=prm_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(r)
            session.flush()
            pid = int(r.id)
            session.commit()
            return pid

        pid = self._upsert_retry(work)
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
