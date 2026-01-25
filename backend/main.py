import sys
import os
import json
import datetime
import threading
import uuid
import gc
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, HTTPException, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Callable

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.chan_service import (
    get_chan_data,
    predict_bsp,
    stragety_feature,
    extract_state_features_from_cur_lv,
    cross_section_rank_transform_rows,
    get_stock_name,
    download_stock_history,
    fetch_stock_data,
    get_latest_data_time,
    normalize_code,
    train_time_split_backtest,
    load_pretrained_model_bundle,
    load_pretrained_model_bundle_by_key,
    evaluate_fixed_model_time_split,
    pretrain_and_persist_model,
    list_pretrained_model_metas,
    get_pretrained_model_detail_by_key,
    delete_pretrained_model_bundle_by_key,
)
from backend.portfolio_service import get_portfolio_service
from backend.vector_backtest import VectorBacktester
from backend.serialization import serialize_chan_data
from backend.storage import StorageManager, StrategyRun, PositionStatus
from backend.strategy_runner import StrategyRunner
from Plot.PlotMeta import CChanPlotMeta
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE
from backend.live.common import day_key_from_ts

app = FastAPI()
storage = StorageManager()
strategy_runner = StrategyRunner(storage)

_live_run_scheduler_started = False
_live_run_scheduler_lock = threading.Lock()
_live_run_running = set()
_live_run_running_lock = threading.Lock()

_pretrain_jobs_lock = threading.Lock()
_pretrain_jobs = {}
_PRETRAIN_JOBS_MAX = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_MAX", "50") or 50)
_PRETRAIN_JOBS_TTL_SEC = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_TTL_SEC", str(6 * 3600)) or (6 * 3600))

_task_queue_cv = threading.Condition()
_task_queue = []
_task_current = {}
_task_worker_started = False

_live_task_queue_cv = threading.Condition()
_live_task_queue = []
_live_task_current = {}
_live_task_worker_started = False

def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return int(default)
        return int(raw)
    except Exception:
        return int(default)

_BACKTEST_MAX_WORKERS = max(1, _env_int("MLCHAN_BACKTEST_MAX_WORKERS", 16))

def _resolve_backtest_max_workers(req_max_workers: int, pool_size: int) -> int:
    try:
        req_mw = int(req_max_workers or 0)
    except Exception:
        req_mw = 0
    base = _BACKTEST_MAX_WORKERS if (req_mw <= 0 or req_mw == 8) else req_mw
    mw = min(int(base), int(_BACKTEST_MAX_WORKERS)) if _BACKTEST_MAX_WORKERS > 0 else int(base)
    ps = int(pool_size or 0)
    if ps <= 0:
        ps = 1
    return max(1, min(int(mw), int(ps)))

def _cn_dt_from_ts(ts: int) -> datetime.datetime:
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)


def _cn_ts_from_dt(dt_cn: datetime.datetime) -> int:
    try:
        return int((dt_cn - datetime.timedelta(hours=8)).timestamp())
    except Exception:
        return int(time.time())


def _next_weekday(d: datetime.date) -> datetime.date:
    while d.weekday() >= 5:
        d = d + datetime.timedelta(days=1)
    return d


def _next_cn_30m_exec_ts(now_ts: int, offset_seconds: int = 5) -> int:
    try:
        off = int(offset_seconds or 0)
    except Exception:
        off = 0
    off = max(0, min(int(off), 120))
    now = _cn_dt_from_ts(int(now_ts))
    d = _next_weekday(now.date())
    times = [(9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (13, 0), (13, 30), (14, 0), (14, 30), (15, 0)]
    if d != now.date():
        dt = datetime.datetime(d.year, d.month, d.day, times[0][0], times[0][1], 0)
        return int(_cn_ts_from_dt(dt + datetime.timedelta(seconds=int(off))))
    for hh, mm in times:
        dt = datetime.datetime(d.year, d.month, d.day, hh, mm, 0) + datetime.timedelta(seconds=int(off))
        if dt > now:
            return int(_cn_ts_from_dt(dt))
    nd = _next_weekday(d + datetime.timedelta(days=1))
    dt = datetime.datetime(nd.year, nd.month, nd.day, times[0][0], times[0][1], 0) + datetime.timedelta(seconds=int(off))
    return int(_cn_ts_from_dt(dt))

def _ensure_live_task_worker_started():
    global _live_task_worker_started
    with _live_task_queue_cv:
        if _live_task_worker_started:
            return
        _live_task_worker_started = True

    try:
        workers = int(os.environ.get("MLCHAN_LIVE_TASK_WORKERS", "1") or 1)
    except Exception:
        workers = 1
    workers = 1

    def _worker_loop(worker_id: int):
        global _live_task_current
        while True:
            task = None
            with _live_task_queue_cv:
                while not _live_task_queue:
                    _live_task_queue_cv.wait()
                task = _live_task_queue.pop(0)
                _live_task_current[worker_id] = task
            try:
                fn = task.get("fn")
                if callable(fn):
                    fn()
            except Exception:
                pass
            finally:
                with _live_task_queue_cv:
                    _live_task_current.pop(worker_id, None)

    for i in range(int(workers)):
        t = threading.Thread(target=lambda wid=i: _worker_loop(wid), daemon=True)
        t.start()

def _enqueue_live_task(kind: str, ref_id: str, fn: Callable[[], None]):
    _ensure_live_task_worker_started()
    with _live_task_queue_cv:
        _live_task_queue.append({
            "kind": str(kind),
            "ref_id": str(ref_id),
            "enqueued_at": _utc_now_str(),
            "fn": fn,
        })
        _live_task_queue_cv.notify()

def _enqueue_live_task_dedup(kind: str, ref_id: str, fn: Callable[[], None]) -> bool:
    _ensure_live_task_worker_started()
    k = str(kind)
    rid = str(ref_id)
    with _live_task_queue_cv:
        for t in _live_task_queue:
            try:
                if str(t.get("kind")) == k and str(t.get("ref_id")) == rid:
                    return False
            except Exception:
                continue
        for cur in (_live_task_current or {}).values():
            try:
                if str((cur or {}).get("kind")) == k and str((cur or {}).get("ref_id")) == rid:
                    return False
            except Exception:
                continue
        _live_task_queue.append({
            "kind": k,
            "ref_id": rid,
            "enqueued_at": _utc_now_str(),
            "fn": fn,
        })
        _live_task_queue_cv.notify()
    return True

def _live_tasks_busy() -> bool:
    with _live_task_queue_cv:
        if _live_task_queue:
            return True
        for cur in (_live_task_current or {}).values():
            if cur:
                return True
        return False

def _run_live_step_task(run_id: str, sim_id: str, notify_enabled: bool, offset_seconds: int):
    from backend.live.notifier import send_spug_sms, send_text, send_text_env

    try:
        run = storage.get_live_run(run_id) or {}
        params = run.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        params = dict(params)
        params["run_id"] = str(run_id)
        params["sim_id"] = str(sim_id or params.get("sim_id") or "").strip() or "default"
        if params.get("portfolio_id") is None:
            params["portfolio_id"] = run.get("portfolio_id")
        if "enable_rolling_lookback" not in params:
            params["enable_rolling_lookback"] = True

        req = LivePaperTradeStepRequest(**params)
        out = _live_paper_trade_step_impl(req)
        if isinstance(out, dict):
            step_ts = int(out.get("ts") or 0)
            steps = int(run.get("steps") or 0) + 1
            next_ts = _next_cn_30m_exec_ts(int(time.time()), int(offset_seconds or 5))
            storage.update_live_run_fields(run_id, {
                "steps": int(steps),
                "last_step_ts": int(step_ts),
                "next_exec_ts": int(next_ts),
                "last_result_json": json.dumps(out, ensure_ascii=False),
                "error": "",
            })
            if notify_enabled:
                try:
                    risk = out.get("risk") or {}
                    lvl = str(risk.get("level") or "")
                    reason = str(risk.get("reason") or "")
                    targets = out.get("targets") or []
                    codes = [str(t.get("code")) for t in targets if isinstance(t, dict) and t.get("code")]
                    msg = f"sim={params['sim_id']} pid={params.get('portfolio_id')} risk={lvl} reason={reason} top={codes} sell={(out.get('sell') or [])} buy={(out.get('buy') or [])}"
                    ok_any = False
                    nk = str(run.get("notify_kind") or "").strip()
                    nw = str(run.get("notify_webhook") or "").strip()
                    ns_raw = str(run.get("notify_secret") or "").strip()
                    ns = ns_raw if ns_raw else None
                    if nk and nw:
                        ok_any = bool(send_text(kind=nk, webhook=nw, text=msg, timeout_sec=8.0, secret=ns)) or ok_any
                    if bool(run.get("sms_enabled")):
                        sms_phone = str(run.get("sms_phone") or "").strip()
                        sms_token = str(run.get("sms_token") or "").strip()
                        if sms_phone and sms_token:
                            ok_any = bool(send_spug_sms(token_or_url=sms_token, phone=sms_phone, text=msg, timeout_sec=8.0)) or ok_any
                    if not ok_any:
                        send_text_env(msg)
                except Exception:
                    pass
    except Exception as e:
        next_ts = _next_cn_30m_exec_ts(int(time.time()), int(offset_seconds or 5))
        storage.update_live_run_fields(run_id, {"next_exec_ts": int(next_ts), "error": str(e)})
    finally:
        try:
            with _live_run_running_lock:
                _live_run_running.discard(str(run_id))
        except Exception:
            pass


def _start_live_run_scheduler():
    global _live_run_scheduler_started
    with _live_run_scheduler_lock:
        if _live_run_scheduler_started:
            return
        _live_run_scheduler_started = True

    def _loop():
        while True:
            try:
                now_ts = int(time.time())
                runs = storage.list_live_runs(status="running", limit=500) or []
                for r in runs:
                    rid = str(r.get("run_id") or "").strip()
                    if not rid:
                        continue
                    with _live_run_running_lock:
                        if rid in _live_run_running:
                            continue
                    try:
                        nxt = int(r.get("next_exec_ts") or 0)
                    except Exception:
                        nxt = 0
                    try:
                        off = int(r.get("offset_seconds") or 5)
                    except Exception:
                        off = 5
                    if nxt <= 0:
                        nxt = _next_cn_30m_exec_ts(now_ts, off)
                        storage.update_live_run_fields(rid, {"next_exec_ts": int(nxt)})
                    if int(now_ts) < int(nxt):
                        continue

                    sim_id = str(r.get("sim_id") or "").strip() or "default"
                    notify_enabled = bool(r.get("notify_enabled"))
                    enqueued = _enqueue_live_task_dedup(
                        "live_step",
                        str(rid),
                        lambda rid=rid, sid=sim_id, ne=notify_enabled, off=int(off): _run_live_step_task(str(rid), str(sid), bool(ne), int(off)),
                    )
                    if enqueued:
                        with _live_run_running_lock:
                            _live_run_running.add(rid)
            except Exception:
                pass
            time.sleep(1.0)

    th = threading.Thread(target=_loop, daemon=True)
    th.start()


@app.on_event("startup")
async def _on_startup_live_runs():
    try:
        _start_live_run_scheduler()
        runs = storage.list_live_runs(status="running", limit=500) or []
        now_ts = int(time.time())
        for r in runs:
            rid = str(r.get("run_id") or "").strip()
            if not rid:
                continue
            if not bool(r.get("auto_resume", True)):
                storage.update_live_run_fields(rid, {"status": "stopped", "next_exec_ts": 0})
                continue
            try:
                nxt = int(r.get("next_exec_ts") or 0)
            except Exception:
                nxt = 0
            try:
                off = int(r.get("offset_seconds") or 5)
            except Exception:
                off = 5
            if nxt <= 0:
                storage.update_live_run_fields(rid, {"next_exec_ts": int(_next_cn_30m_exec_ts(now_ts, off))})
    except Exception:
        return

def _ensure_task_worker_started():
    global _task_worker_started
    with _task_queue_cv:
        if _task_worker_started:
            return
        _task_worker_started = True

    try:
        workers = int(os.environ.get("MLCHAN_TASK_WORKERS", "2") or 2)
    except Exception:
        workers = 2
    workers = max(1, min(int(workers), 8))

    def _worker_loop(worker_id: int):
        global _task_current
        while True:
            task = None
            with _task_queue_cv:
                while not _task_queue:
                    _task_queue_cv.wait()
                task = _task_queue.pop(0)
                _task_current[worker_id] = task
            try:
                fn = task.get("fn")
                if callable(fn):
                    fn()
            except Exception:
                pass
            finally:
                with _task_queue_cv:
                    _task_current.pop(worker_id, None)

    for i in range(int(workers)):
        t = threading.Thread(target=lambda wid=i: _worker_loop(wid), daemon=True)
        t.start()

def _enqueue_task(kind: str, ref_id: str, fn: Callable[[], None]):
    _ensure_task_worker_started()
    with _task_queue_cv:
        _task_queue.append({
            "kind": str(kind),
            "ref_id": str(ref_id),
            "enqueued_at": _utc_now_str(),
            "fn": fn,
        })
        _task_queue_cv.notify()

def _cancel_task_if_queued(kind: str, ref_id: str) -> bool:
    with _task_queue_cv:
        for cur in (_task_current or {}).values():
            if cur and cur.get("kind") == kind and str(cur.get("ref_id")) == str(ref_id):
                return False
        kept = []
        removed = False
        for t in _task_queue:
            if t.get("kind") == kind and str(t.get("ref_id")) == str(ref_id):
                removed = True
                continue
            kept.append(t)
        _task_queue[:] = kept
        return removed

def _utc_now_str():
    return datetime.datetime.utcnow().isoformat() + "Z"

def _set_pretrain_job(job_id: str, **fields):
    updated = None
    with _pretrain_jobs_lock:
        job = _pretrain_jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = _utc_now_str()
        updated = dict(job)
    try:
        if updated is not None:
            storage.upsert_pretrain_job(updated)
    except Exception:
        pass

def _parse_utc(s: str):
    if not s:
        return None
    try:
        ss = str(s)
        if ss.endswith("Z"):
            ss = ss[:-1]
        return datetime.datetime.fromisoformat(ss)
    except Exception:
        return None

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

def _model_type_label(model_type: str) -> str:
    mt = str(model_type or "").strip()
    key = mt.lower()
    if key in ["logistic_regression", "logreg", "lr"]:
        return "LogisticRegression"
    if key == "xgboost":
        return "XGBoost"
    if key == "lightgbm":
        return "LightGBM"
    if key == "mlp":
        return "MLP"
    return mt or "Model"

def _auto_pretrained_model_name(model_type: str, frequency: str, trained_at: str, pool_name: Optional[str] = None) -> str:
    dt = _parse_utc(trained_at) if trained_at else None
    if dt is None:
        dt = datetime.datetime.utcnow()
    date = dt.strftime("%Y%m%d")
    mt = _model_type_label(model_type)
    freq = str(frequency or "").strip() or "-"
    
    pn = str(pool_name or "").strip()
    if pn:
        raw = f"{mt}_{freq}_{pn}_{date}"
    else:
        raw = f"{mt}_{freq}_{date}"
        
    out = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-", "."):
            out.append(ch)
    return "".join(out) or f"Model_{date}"

def _attach_pretrained_display_name(meta: dict, custom_name: Optional[str]):
    if not isinstance(meta, dict):
        return meta
    name = None
    if custom_name is not None:
        s = str(custom_name).strip()
        if s:
            name = s
            
    pool_name = None
    if isinstance(meta.get("meta"), dict):
        pool_name = meta["meta"].get("pool_name")
        
    display_name = name or _auto_pretrained_model_name(
        meta.get("model_type"), 
        meta.get("frequency"), 
        meta.get("trained_at"),
        pool_name=pool_name
    )
    out = dict(meta)
    out["name"] = name
    out["display_name"] = display_name
    return out

def _purge_pretrain_jobs():
    now = datetime.datetime.utcnow()
    with _pretrain_jobs_lock:
        items = list(_pretrain_jobs.values())
        terminal = [j for j in items if j.get("status") in ("success", "error")]

        for j in terminal:
            dt = _parse_utc(j.get("updated_at") or j.get("created_at") or "")
            if dt and (now - dt).total_seconds() > _PRETRAIN_JOBS_TTL_SEC:
                _pretrain_jobs.pop(j.get("job_id"), None)

        if len(_pretrain_jobs) <= _PRETRAIN_JOBS_MAX:
            return

        items = list(_pretrain_jobs.values())
        terminal = [j for j in items if j.get("status") in ("success", "error")]
        terminal.sort(key=lambda j: (j.get("updated_at") or j.get("created_at") or ""))
        while len(_pretrain_jobs) > _PRETRAIN_JOBS_MAX and terminal:
            j = terminal.pop(0)
            _pretrain_jobs.pop(j.get("job_id"), None)
    try:
        storage.prune_pretrain_jobs(_PRETRAIN_JOBS_MAX, _PRETRAIN_JOBS_TTL_SEC)
    except Exception:
        pass

def _shrink_pretrain_result(result):
    if not isinstance(result, dict):
        return result
    keep = [
        "status",
        "bundle_path",
        "model_key",
        "model_type",
        "frequency",
        "data_src",
        "feature_count",
        "sample_count",
        "accuracy",
    ]
    out = {k: result.get(k) for k in keep if k in result}
    if result.get("status") != "success":
        for k in ("detail", "message"):
            if k in result:
                out[k] = result.get(k)
    return out

def _shrink_accuracy_result(result):
    if not isinstance(result, dict):
        return result
    keep = [
        "method",
        "train_count",
        "val_count",
        "test_count",
        "valid_count",
        "total_count",
        "accuracy",
        "roc_auc",
        "pr_auc",
        "yearly_auc",
        "brier_score",
        "ece",
        "logloss",
        "selected_features",
        "warning",
    ]
    out = {k: result.get(k) for k in keep if k in result}
    trading = result.get("trading")
    if isinstance(trading, dict):
        tv = None
        try:
            tv = (trading.get("topk_vector") or None)
        except Exception:
            tv = None
        if tv is not None:
            out["trading"] = {"topk_vector": tv}
        else:
            thr = trading.get("threshold") if isinstance(trading.get("threshold"), dict) else None
            if thr is not None:
                out["trading"] = {"threshold": thr}
    return out

def _calc_pretrain_begin_time(req, level: KL_TYPE):
    days = 365
    if req.data_length_years is not None:
        days = int(365 * req.data_length_years)
    elif req.data_length_mode == "max":
        if level == KL_TYPE.K_1M:
            days = 365
        elif level == KL_TYPE.K_5M:
            days = 365 * 2
        elif level == KL_TYPE.K_15M:
            days = 365 * 5
        elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
            days = 365 * 10
    else:
        if level == KL_TYPE.K_1M:
            days = 180
        elif level == KL_TYPE.K_5M:
            days = 540
        elif level == KL_TYPE.K_15M:
            days = 900
        elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
            days = 1800
    days = max(days, 30)
    return (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev, allow all. In prod, specify.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    code: str
    trigger_step: bool = True
    bi_strict: bool = True
    frequency: str = "1d"
    data_src: str = "clickhouse"
    model: str = "xgboost"
    do_predict: bool = False
    autype: str = "qfq"
    force_refresh: bool = False
    data_length_mode: str = "default" # "default" or "max"
    data_length_years: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    use_pretrained: Optional[bool] = None
    pretrained_model_key: Optional[str] = None
    blend_models: Optional[bool] = None
    enable_rolling_lookback: bool = True # Enable/Disable long-term context features
    rank_transform_enabled: Optional[bool] = None
    only_features: Optional[Any] = None
    profit_threshold: Optional[float] = 0.01
    auto_profit_quantile: float = 0.7
    profit_lookahead: int = 5
    use_atr_label: bool = False
    atr_period: int = 14
    atr_mult: float = 1.0
    backtest_mode: str = "auto"
    walk_forward_max_folds: int = 20
    walk_forward_test_window: Optional[int] = None
    walk_forward_val_window: Optional[int] = None
    walk_forward_step: Optional[int] = None
    trade_cost: float = 0.0
    topk_frac: float = 0.2
    quantile_bin_count: int = 5
    calibrate_method: str = "none"
    use_scale_pos_weight: bool = False
    xgb_max_depth: Optional[int] = None
    xgb_reg_alpha: Optional[float] = None
    xgb_reg_lambda: Optional[float] = None
    lgb_max_depth: Optional[int] = None
    lgb_reg_alpha: Optional[float] = None
    lgb_reg_lambda: Optional[float] = None

class ValidateRequest(BaseModel):
    codes: List[str]
    frequency: str = "1d"
    data_src: str = "clickhouse"
    model: str = "xgboost"
    autype: str = "qfq"
    data_length_mode: str = "default"
    data_length_years: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enable_rolling_lookback: bool = True
    profit_threshold: Optional[float] = 0.01
    auto_profit_quantile: float = 0.7
    profit_lookahead: int = 5
    use_atr_label: bool = False
    atr_period: int = 14
    atr_mult: float = 1.0
    backtest_mode: str = "auto"
    walk_forward_max_folds: int = 20
    walk_forward_test_window: Optional[int] = None
    walk_forward_val_window: Optional[int] = None
    walk_forward_step: Optional[int] = None
    trade_cost: float = 0.0
    topk_frac: float = 0.2
    quantile_bin_count: int = 5
    calibrate_method: str = "none"
    use_scale_pos_weight: bool = False
    xgb_max_depth: Optional[int] = None
    xgb_reg_alpha: Optional[float] = None
    xgb_reg_lambda: Optional[float] = None
    lgb_max_depth: Optional[int] = None
    lgb_reg_alpha: Optional[float] = None
    lgb_reg_lambda: Optional[float] = None

class PretrainRequest(BaseModel):
    codes: list[str]
    frequency: str = "1d"
    data_src: str = "clickhouse"
    model: str = "xgboost"
    data_length_mode: str = "max"
    data_length_years: Optional[float] = None
    force_refresh: bool = False
    autype: str = "hfq"
    pool_name: Optional[str] = None
    profit_threshold: Optional[float] = 0.01
    auto_profit_quantile: float = 0.7
    profit_lookahead: Optional[int] = None
    high_vol_atr_pct_min: Optional[float] = None
    use_atr_label: bool = False
    atr_period: int = 14
    atr_mult: float = 1.0
    trade_cost: float = 0.0
    topk_frac: float = 0.2
    portfolio_min_score: float = 0.6
    portfolio_top_n: int = 2
    rank_mode: str = "sample"
    rolling_sample_mode: Optional[str] = None
    rolling_stride: Optional[int] = None
    only_features: Optional[List[str]] = None

def _default_profit_lookahead_for_frequency(freq: str) -> int:
    f = str(freq or "").strip().lower()
    if f == "30m":
        return 3
    return 5

class UpdatePretrainedModelNameRequest(BaseModel):
    name: Optional[str] = None

@app.post("/api/pretrain_jobs")
async def create_pretrain_job(req: PretrainRequest):
    profit_lookahead = req.profit_lookahead
    try:
        profit_lookahead = int(float(profit_lookahead)) if profit_lookahead not in ["", "null", "None", None] else None
    except Exception:
        profit_lookahead = None
    if profit_lookahead is None or profit_lookahead <= 0:
        profit_lookahead = _default_profit_lookahead_for_frequency(req.frequency)
    params = {
        "codes": sorted([normalize_code(str(c).strip()) for c in (req.codes or []) if str(c).strip()]),
        "data_length_mode": req.data_length_mode,
        "data_length_years": req.data_length_years,
        "autype": str(req.autype or "").strip().lower() or "hfq",
        "pool_name": req.pool_name,
        "profit_threshold": req.profit_threshold,
        "auto_profit_quantile": req.auto_profit_quantile,
        "profit_lookahead": profit_lookahead,
        "high_vol_atr_pct_min": req.high_vol_atr_pct_min,
        "use_atr_label": bool(req.use_atr_label),
        "atr_period": int(req.atr_period),
        "atr_mult": float(req.atr_mult),
    }
    # Removed database duplicate check to allow re-training with new unique keys
    # if not req.force_refresh and storage.is_duplicate_pretrain(req.model, req.frequency, req.data_src, params):
    #    raise HTTPException(status_code=409, detail="duplicate pretrain")

    job_id = str(uuid.uuid4())
    now = _utc_now_str()
    job_obj = None
    with _pretrain_jobs_lock:
        job_obj = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0.0,
            "stage": "queued",
            "message": "",
            "detail": None,
            "result": None,
            "request": {
                "codes_count": len(req.codes or []),
                "frequency": req.frequency,
                "data_src": req.data_src,
                "model": req.model,
                "data_length_mode": req.data_length_mode,
                "data_length_years": req.data_length_years,
                "force_refresh": req.force_refresh,
                "autype": str(req.autype or "").strip().lower() or "hfq",
            },
            "created_at": now,
            "updated_at": now,
        }
        _pretrain_jobs[job_id] = job_obj

    try:
        storage.upsert_pretrain_job(dict(job_obj))
    except Exception:
        pass

    def run_job():
        try:
            _set_pretrain_job(job_id, status="running", stage="collecting", progress=0.0, message="")

            freq_map = {
                "1d": KL_TYPE.K_DAY,
                "30m": KL_TYPE.K_30M,
                "5m": KL_TYPE.K_5M,
                "15m": KL_TYPE.K_15M,
                "60m": KL_TYPE.K_60M,
                "1m": KL_TYPE.K_1M,
                "1w": KL_TYPE.K_WEEK,
                "1mo": KL_TYPE.K_MON,
            }
            level = freq_map.get(req.frequency, KL_TYPE.K_DAY)

            src_map = {
                "baostock": DATA_SRC.BAO_STOCK,
                "akshare": DATA_SRC.AK_SHARE,
                "ccxt": DATA_SRC.CCXT,
                "csv": DATA_SRC.CSV,
                "clickhouse": DATA_SRC.CLICK_HOUSE,
            }
            data_src_type = src_map.get(req.data_src, DATA_SRC.BAO_STOCK)

            begin_time = _calc_pretrain_begin_time(req, level)
            raw_autype = str(getattr(req, "autype", "qfq") or "qfq").strip().lower()
            autype_map = {
                "qfq": AUTYPE.QFQ,
                "hfq": AUTYPE.HFQ,
                "none": AUTYPE.NONE,
            }
            autype = autype_map.get(raw_autype)
            if autype is None:
                autype = AUTYPE.HFQ
            profit_lookahead = req.profit_lookahead
            try:
                profit_lookahead = int(float(profit_lookahead)) if profit_lookahead not in ["", "null", "None", None] else None
            except Exception:
                profit_lookahead = None
            if profit_lookahead is None or profit_lookahead <= 0:
                profit_lookahead = _default_profit_lookahead_for_frequency(req.frequency)

            def progress_cb(p):
                if not isinstance(p, dict):
                    return
                progress = p.get("progress", None)
                if progress is not None:
                    try:
                        progress = float(progress)
                    except Exception:
                        progress = None
                fields = {
                    "stage": p.get("stage", None),
                    "message": p.get("message", None),
                }
                if progress is not None:
                    fields["progress"] = max(0.0, min(1.0, progress))
                _set_pretrain_job(job_id, **{k: v for k, v in fields.items() if v is not None})

            path, result = pretrain_and_persist_model(
                codes=req.codes,
                level=level,
                data_src_type=data_src_type,
                begin_time=begin_time,
                model_type=req.model,
                frequency=req.frequency,
                data_src=req.data_src,
                calibrate_method="none",
                progress_cb=progress_cb,
                pool_name=req.pool_name,
                profit_threshold=req.profit_threshold,
                auto_profit_quantile=req.auto_profit_quantile,
                profit_lookahead=profit_lookahead,
                autype=autype,
                high_vol_atr_pct_min=req.high_vol_atr_pct_min,
                use_atr_label=bool(req.use_atr_label),
                atr_period=int(req.atr_period),
                atr_mult=float(req.atr_mult),
                rank_mode=str(req.rank_mode or "sample"),
                rolling_sample_mode=req.rolling_sample_mode,
                rolling_stride=req.rolling_stride,
                only_features=req.only_features,
            )
            if not path:
                detail = None
                if isinstance(result, dict):
                    detail = result.get("detail", None)
                _set_pretrain_job(job_id, status="error", stage="error", progress=1.0, detail=detail or "pretrain failed", result=None)
                return

            mk = None
            if isinstance(result, dict):
                mk = result.get("model_key", None)
            trained_meta = get_pretrained_model_detail_by_key(mk) if mk else None
            if not trained_meta and path:
                try:
                    for m in list_pretrained_model_metas(limit=None) or []:
                        if isinstance(m, dict) and str(m.get("bundle_path") or "") == str(path):
                            trained_meta = m
                            mk = str(m.get("key") or "").strip() or mk
                            break
                except Exception:
                    trained_meta = None
            trained_meta = trained_meta or {}
            inner = trained_meta.get("meta", {}) or {}
            per_code = inner.get("per_code_sample_count", {}) or {}
            sample_count = inner.get("sample_count", None)
            if sample_count is None:
                try:
                    sample_count = int(sum([int(v) for v in per_code.values()]))
                except Exception:
                    sample_count = 0

            storage.upsert_pretrained_model(
                model_key=mk,
                model_type=req.model,
                frequency=req.frequency,
                data_src=req.data_src,
                params=params,
                meta={
                    "bundle_path": trained_meta.get("bundle_path"),
                    "feature_count": trained_meta.get("feature_count", 0),
                    "sample_count": sample_count,
                    "trained_at": trained_meta.get("trained_at"),
                    "accuracy": inner.get("accuracy"),
                },
            )

            _set_pretrain_job(job_id, status="success", stage="done", progress=1.0, result=_shrink_pretrain_result(result), detail=None)
        except Exception as e:
            _set_pretrain_job(job_id, status="error", stage="error", progress=1.0, detail=str(e), result=None)
        finally:
            _purge_pretrain_jobs()
            gc.collect()

    _enqueue_task("pretrain_job", job_id, run_job)
    return {"job_id": job_id}

@app.get("/api/pretrain_jobs/{job_id}")
async def get_pretrain_job(job_id: str):
    with _pretrain_jobs_lock:
        job = _pretrain_jobs.get(job_id)
        if job:
            return job
    job = None
    try:
        job = storage.get_pretrain_job(job_id)
    except Exception:
        job = None
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job

@app.get("/api/pretrain_jobs")
async def list_pretrain_jobs(page: Optional[int] = None, page_size: int = 50, limit: int = 200):
    if page is None:
        try:
            return storage.list_pretrain_jobs(limit=int(limit), include_success=False)
        except Exception:
            return {"items": []}
    try:
        return storage.list_pretrain_jobs(page=int(page), page_size=int(page_size), include_success=False)
    except Exception:
        return {"items": [], "total": 0, "page": int(page or 1), "page_size": int(page_size or 50)}

@app.delete("/api/pretrain_jobs/{job_id}")
async def delete_pretrain_job(job_id: str):
    removed_mem = False
    job = None
    with _pretrain_jobs_lock:
        job = _pretrain_jobs.get(job_id)
    st = str(job.get("status")) if isinstance(job, dict) else ""
    if st in ("running",):
        raise HTTPException(status_code=409, detail="job is running")
    if st in ("queued",):
        if not _cancel_task_if_queued("pretrain_job", job_id):
            raise HTTPException(status_code=409, detail="job is running")
    else:
        _cancel_task_if_queued("pretrain_job", job_id)

    with _pretrain_jobs_lock:
        if job_id in _pretrain_jobs:
            _pretrain_jobs.pop(job_id, None)
            removed_mem = True

    deleted_db = False
    try:
        deleted_db = bool(storage.delete_pretrain_job(job_id))
    except Exception:
        deleted_db = False
    if not removed_mem and not deleted_db:
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "success", "job_id": job_id, "deleted_db": deleted_db}

class BatchDeletePretrainJobsRequest(BaseModel):
    job_ids: List[str] = []

@app.post("/api/pretrain_jobs/batch_delete")
async def batch_delete_pretrain_jobs(req: BatchDeletePretrainJobsRequest):
    raw = [] if req is None else (req.job_ids or [])
    job_ids = []
    seen = set()
    for x in raw:
        jid = str(x or "").strip()
        if not jid or jid in seen:
            continue
        seen.add(jid)
        job_ids.append(jid)

    deleted = []
    failed = []
    for jid in job_ids:
        j = None
        with _pretrain_jobs_lock:
            j = _pretrain_jobs.get(jid)
        st = str(j.get("status")) if isinstance(j, dict) else ""
        if st in ("running",):
            failed.append({"job_id": jid, "reason": "running"})
            continue
        if st in ("queued",):
            if not _cancel_task_if_queued("pretrain_job", jid):
                failed.append({"job_id": jid, "reason": "running"})
                continue
        else:
            _cancel_task_if_queued("pretrain_job", jid)

        with _pretrain_jobs_lock:
            if jid in _pretrain_jobs:
                _pretrain_jobs.pop(jid, None)

        ok = False
        try:
            ok = bool(storage.delete_pretrain_job(jid))
        except Exception:
            ok = False
        if ok:
            deleted.append(jid)
        else:
            failed.append({"job_id": jid, "reason": "not_found"})
    return {"status": "success", "deleted": deleted, "failed": failed}

@app.get("/api/pretrained_models")
async def list_pretrained_models(page: Optional[int] = None, page_size: int = 20, limit: int = 200):
    if page is None:
        lim = int(limit)
        if lim < 1:
            lim = 1
        if lim > 200:
            lim = 200
        data = storage.get_pretrained_models(page=1, page_size=lim)
        items = []
        for it in data.get("items") or []:
            meta = dict(it)
            meta["meta"] = {}
            items.append(_attach_pretrained_display_name(meta, it.get("name")))
        return {"items": items}

    p = int(page)
    if p < 1:
        p = 1
    ps = int(page_size)
    if ps < 1:
        ps = 1
    if ps > 200:
        ps = 200

    data = storage.get_pretrained_models(page=p, page_size=ps)
    items = []
    for it in data.get("items") or []:
        meta = dict(it)
        meta["meta"] = {}
        items.append(_attach_pretrained_display_name(meta, it.get("name")))
    return {"items": items, "total": int(data.get("total") or 0), "page": p, "page_size": ps}

class BatchDeletePretrainedModelsRequest(BaseModel):
    keys: List[str] = []

@app.post("/api/pretrained_models/batch_delete")
async def batch_delete_pretrained_models(req: BatchDeletePretrainedModelsRequest):
    raw = [] if req is None else (req.keys or [])
    keys = []
    seen = set()
    for x in raw:
        k = str(x or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        keys.append(k)

    deleted = []
    failed = []
    for k in keys:
        deleted_files = None
        try:
            deleted_files = delete_pretrained_model_bundle_by_key(k)
        except Exception:
            deleted_files = {"status": "error", "key": str(k or "")}

        deleted_db = False
        try:
            deleted_db = bool(storage.delete_pretrained_model(k))
        except Exception:
            deleted_db = False

        if (deleted_files or {}).get("status") == "not_found" and not deleted_db:
            failed.append({"key": k, "reason": "not_found"})
        else:
            deleted.append({"key": k, "deleted_files": deleted_files, "deleted_db": deleted_db})
    return {"status": "success", "deleted": deleted, "failed": failed}

@app.get("/api/pretrained_models/{key}")
async def get_pretrained_model_detail(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if meta:
        m = meta.get("meta", {}) or {}
        per_code = m.get("per_code_sample_count", {}) or {}
        sample_count = m.get("sample_count", None)
        if sample_count is None:
            try:
                sample_count = int(sum([int(v) for v in per_code.values()]))
            except Exception:
                sample_count = 0
        result = {
            "status": "success",
            "bundle_path": meta.get("bundle_path"),
            "model_type": meta.get("model_type"),
            "frequency": meta.get("frequency"),
            "data_src": meta.get("data_src"),
            "feature_count": meta.get("feature_count", 0),
            "sample_count": sample_count,
            "accuracy": m.get("accuracy"),
            "codes": m.get("codes", []),
            "per_code_sample_count": per_code,
            "trained_at": meta.get("trained_at"),
            "begin_time": m.get("begin_time"),
        }
    else:
        db = storage.get_pretrained_model_by_key(key)
        if not db:
            raise HTTPException(status_code=404, detail="model not found")
        result = {
            "status": "success",
            "bundle_path": db.get("bundle_path"),
            "model_type": db.get("model_type"),
            "frequency": db.get("frequency"),
            "data_src": db.get("data_src"),
            "feature_count": db.get("feature_count") or 0,
            "sample_count": db.get("sample_count") or 0,
            "accuracy": db.get("accuracy"),
            "codes": [],
            "per_code_sample_count": {},
            "trained_at": db.get("trained_at"),
            "begin_time": None,
        }

    name_map = storage.get_pretrained_model_names_by_keys([key])
    result["name"] = name_map.get(key)
    result["display_name"] = (str(result["name"]).strip() if result.get("name") else "") or _auto_pretrained_model_name(
        result.get("model_type"),
        result.get("frequency"),
        result.get("trained_at"),
    )
    return result

@app.post("/api/pretrained_models/{key}/name")
async def update_pretrained_model_name(key: str, req: UpdatePretrainedModelNameRequest):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        db = storage.get_pretrained_model_by_key(key)
        if not db:
            raise HTTPException(status_code=404, detail="model not found")
    raw = None if req is None else req.name
    name = None
    if raw is not None:
        s = str(raw).strip()
        if s:
            if len(s) > 120:
                raise HTTPException(status_code=400, detail="name too long")
            name = s
    ok = storage.set_pretrained_model_name(key, name)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update name")
    base = meta or storage.get_pretrained_model_by_key(key) or {}
    display_name = name or _auto_pretrained_model_name(base.get("model_type"), base.get("frequency"), base.get("trained_at"))
    return {"status": "success", "key": key, "name": name, "display_name": display_name}

@app.delete("/api/pretrained_models/{key}")
async def delete_pretrained_model(key: str):
    deleted_files = None
    try:
        deleted_files = delete_pretrained_model_bundle_by_key(key)
    except Exception:
        deleted_files = {"status": "error", "key": str(key or "")}

    deleted_db = False
    try:
        deleted_db = bool(storage.delete_pretrained_model(key))
    except Exception:
        deleted_db = False

    if (deleted_files or {}).get("status") == "not_found" and not deleted_db:
        raise HTTPException(status_code=404, detail="model not found")

    return {"status": "success", "key": str(key or ""), "deleted_files": deleted_files, "deleted_db": deleted_db}

@app.get("/api/history")
async def get_history(code: Optional[str] = None, page: int = 1, page_size: int = 10):
    return storage.get_history(code, page, page_size)

class BatchDeleteHistoryRequest(BaseModel):
    result_ids: List[int] = []

@app.post("/api/history/batch_delete")
async def batch_delete_history(req: BatchDeleteHistoryRequest):
    ids = []
    for x in ((req.result_ids or []) if req is not None else []):
        try:
            ids.append(int(x))
        except Exception:
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {"status": "success", "deleted": [], "failed": []}

    res = storage.batch_delete_results(ids)
    return {"status": "success", "deleted": res.get("deleted") or [], "failed": res.get("failed") or []}

@app.delete("/api/history/{result_id}")
async def delete_history_item(result_id: int):
    success = storage.delete_result(result_id)
    if not success:
        raise HTTPException(status_code=404, detail="Result not found or failed to delete")
    return {"status": "success"}

@app.get("/api/history/{result_id}")
async def get_history_detail(result_id: int):
    result = storage.get_result_by_id(result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result

class CreatePortfolioRequest(BaseModel):
    name: str
    description: str = ""

class AddPositionRequest(BaseModel):
    code: str
    volume: int
    price: float
    name: str = ""

@app.get("/api/portfolios")
async def list_portfolios():
    return get_portfolio_service().list_portfolios()

@app.post("/api/portfolios")
async def create_portfolio(req: CreatePortfolioRequest):
    return get_portfolio_service().create_portfolio(req.name, req.description)

@app.delete("/api/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: int):
    get_portfolio_service().delete_portfolio(portfolio_id)
    return {"status": "success"}

@app.get("/api/portfolios/{portfolio_id}")
async def get_portfolio_detail(portfolio_id: int):
    p = get_portfolio_service().get_portfolio_detail(portfolio_id)
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return p

@app.post("/api/portfolios/{portfolio_id}/positions")
async def add_position(portfolio_id: int, req: AddPositionRequest):
    get_portfolio_service().add_position(portfolio_id, req.code, req.volume, req.price, req.name)
    return {"status": "success"}

class VectorBacktestRequest(BaseModel):
    pool_id: str = "chinext50"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    model_key: Optional[str] = None
    frequency: str = "1d"
    data_src: str = "clickhouse"
    autype: str = "qfq"
    enable_rolling_lookback: bool = True
    portfolio_top_n: int = 2
    portfolio_top_frac: Optional[float] = None
    holding_period: int = 5
    min_signal_score: Optional[float] = None
    initial_capital: float = 100000.0
    max_workers: int = _BACKTEST_MAX_WORKERS
    score_feature: Optional[str] = None
    score_mode: str = "raw"
    score_higher_is_better: bool = True

class WolfpackBacktestRequest(BaseModel):
    pool_id: str = "index:IDX399673"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    frequency: str = "30m"
    data_src: str = "clickhouse"
    autype: str = "qfq"
    enable_rolling_lookback: bool = True
    holding_period: int = 3
    min_prob: float = 0.25
    trade_cost: float = 0.0002
    walk_forward_max_folds: int = 25
    rolling_sample_mode: str = "every_bar"
    rolling_stride: int = 1
    portfolio_top_n: int = 2
    index_filter_enabled: bool = True
    index_code: str = "399006"
    index_ma_window: int = 20
    stop_loss: float = -0.03
    enforce_t1: bool = True
    sell_mode: str = "baseline"
    t1_decision_time: str = "10:00"
    t1_gap_strong_threshold: float = 0.03
    t1_gap_weak_threshold: float = -0.02
    t1_gap_very_strong_threshold: float = 0.05
    risk_filter_enabled: bool = True
    risk_mode: str = "both"
    risk_drop_threshold: float = -0.03
    risk_flat_drop_threshold: float = -0.05
    risk_atr_window: int = 14
    risk_atr_pct_threshold: float = 0.04
    risk_flat_atr_pct_threshold: float = 0.06
    risk_gap_down_threshold: float = -0.015
    risk_intraday_enabled: bool = True
    risk_intraday_bear_threshold: float = -0.015
    risk_intraday_bull_threshold: float = -0.03
    risk_trend_ma_window: int = 20
    risk_data_fail_mode: str = "reduce"
    risk_intraday_data_fail_mode: str = "reduce"
    max_workers: int = _BACKTEST_MAX_WORKERS

@app.post("/api/backtest/vector")
async def run_vector_backtest(req: VectorBacktestRequest):
    # Default to last 3 months if not provided
    import datetime
    end = req.end_date
    if not end:
        end = datetime.datetime.now().strftime("%Y-%m-%d")
    start = req.start_date
    if not start:
        start = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    
    # Run async? It might take time.
    # For simplicity, run in thread pool or background task.
    # But user wants results.
    # If it takes too long, we should return a job ID.
    # But for now, let's try synchronous with increased timeout or background task that stores result?
    # Given the user wants a "Simulated Profit and Loss page", maybe we should store it.
    
    # Let's run it and return results directly if it's fast enough, 
    # or better, start a background task and return a job ID (reusing pretrain_jobs logic or new one).
    # Since "Simple Vector Backtest" is requested, maybe I can just run it.
    # But 50 stocks * 3 months is manageable.
    
    # We can use the existing StrategyRunner structure but this is a specific logic.
    # I'll instantiate VectorBacktester.
    
    tester = VectorBacktester()
    
    # Determine pool
    pool_codes = []
    if req.pool_id in ["chinext50", "chinext10"]:
        from backend.vector_backtest import get_chinext50_stocks
        pool_codes = get_chinext50_stocks()
        if req.pool_id == "chinext10":
            pool_codes = list(pool_codes or [])[:10]
    else:
        # TODO: Support other pools
        pass
    
    if not pool_codes:
         # Fallback for testing
         pool_codes = ["sz.300059", "sz.300750"] # Sample ChiNext stocks
    
    mw = _resolve_backtest_max_workers(getattr(req, "max_workers", 0), len(pool_codes))
         
    # Run
    # Warning: This is blocking. In production, use background task.
    result = tester.run(
        pool_codes,
        start,
        end,
        req.model_key,
        frequency=req.frequency,
        data_src=req.data_src,
        autype=req.autype,
        enable_rolling_lookback=req.enable_rolling_lookback,
        portfolio_top_n=req.portfolio_top_n,
        portfolio_top_frac=req.portfolio_top_frac,
        holding_period=req.holding_period,
        min_signal_score=req.min_signal_score,
        initial_capital=req.initial_capital,
        max_workers=mw,
        score_feature=req.score_feature,
        score_mode=req.score_mode,
        score_higher_is_better=req.score_higher_is_better,
    )
    return result

@app.post("/api/backtest/wolfpack")
async def run_wolfpack_backtest(req: WolfpackBacktestRequest):
    import datetime
    end = req.end_date
    if not end:
        end = datetime.datetime.now().strftime("%Y-%m-%d")
    start = req.start_date
    if not start:
        start = (datetime.datetime.now() - datetime.timedelta(days=365 * 5)).strftime("%Y-%m-%d")

    from DataAPI.ClickHouseAPI import CClickHouseAPI
    pool_codes = CClickHouseAPI.get_pool_members(req.pool_id)
    if not pool_codes:
        try:
            pools = CClickHouseAPI.list_stock_pools(table="stock_constituent")
        except Exception:
            pools = []
        picked = None
        for p in (pools or []):
            pid = str((p or {}).get("pool_id") or "")
            nm = str((p or {}).get("pool_name") or "")
            if "399673" in pid or "399673" in nm or "创业板50" in nm:
                picked = pid
                break
        if picked:
            pool_codes = CClickHouseAPI.get_pool_members(picked)
        if not pool_codes:
            if str(req.data_src or "").strip().lower() != "clickhouse":
                from backend.vector_backtest import get_chinext50_stocks
                pool_codes = get_chinext50_stocks()

    mw = _resolve_backtest_max_workers(getattr(req, "max_workers", 0), len(pool_codes))

    from backend.wolfpack_backtest import WolfpackBacktester
    tester = WolfpackBacktester()
    return tester.run(
        pool_codes,
        start,
        end,
        frequency=req.frequency,
        data_src=req.data_src,
        autype=req.autype,
        enable_rolling_lookback=req.enable_rolling_lookback,
        min_prob=req.min_prob,
        holding_period=req.holding_period,
        trade_cost=req.trade_cost,
        walk_forward_max_folds=req.walk_forward_max_folds,
        rolling_sample_mode=req.rolling_sample_mode,
        rolling_stride=req.rolling_stride,
        portfolio_top_n=req.portfolio_top_n,
        index_filter_enabled=req.index_filter_enabled,
        index_code=req.index_code,
        index_ma_window=req.index_ma_window,
        stop_loss=req.stop_loss,
        enforce_t1=req.enforce_t1,
        sell_mode=req.sell_mode,
        t1_decision_time=req.t1_decision_time,
        t1_gap_strong_threshold=req.t1_gap_strong_threshold,
        t1_gap_weak_threshold=req.t1_gap_weak_threshold,
        t1_gap_very_strong_threshold=req.t1_gap_very_strong_threshold,
        risk_filter_enabled=req.risk_filter_enabled,
        risk_mode=req.risk_mode,
        risk_drop_threshold=req.risk_drop_threshold,
        risk_flat_drop_threshold=req.risk_flat_drop_threshold,
        risk_atr_window=req.risk_atr_window,
        risk_atr_pct_threshold=req.risk_atr_pct_threshold,
        risk_flat_atr_pct_threshold=req.risk_flat_atr_pct_threshold,
        risk_gap_down_threshold=req.risk_gap_down_threshold,
        risk_intraday_enabled=req.risk_intraday_enabled,
        risk_intraday_bear_threshold=req.risk_intraday_bear_threshold,
        risk_intraday_bull_threshold=req.risk_intraday_bull_threshold,
        risk_trend_ma_window=req.risk_trend_ma_window,
        risk_data_fail_mode=req.risk_data_fail_mode,
        risk_intraday_data_fail_mode=req.risk_intraday_data_fail_mode,
        max_workers=mw,
    )

class BacktestPresetUpsertRequest(BaseModel):
    name: str = ""
    paper_config: dict = {}
    paper_params: dict = {}

@app.get("/api/backtest/presets")
async def list_backtest_presets(limit: int = 200):
    try:
        items = storage.list_backtest_presets(limit=limit) or []
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/backtest/presets")
async def upsert_backtest_preset(req: BacktestPresetUpsertRequest):
    try:
        nm = str(req.name or "").strip()
        if not nm:
            raise HTTPException(status_code=400, detail="name required")
        item = storage.upsert_backtest_preset(
            name=nm,
            paper_config=req.paper_config or {},
            paper_params=req.paper_params or {},
        )
        return {"item": item}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/backtest/presets/{preset_id}")
async def get_backtest_preset(preset_id: int):
    try:
        item = storage.get_backtest_preset(preset_id)
        if not item:
            raise HTTPException(status_code=404, detail="preset not found")
        return {"item": item}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest):
    print(f"Received analyze request: code={req.code}, freq={req.frequency}, model={req.model}, force_refresh={req.force_refresh}, data_length_years={req.data_length_years}")
    try:
        req.code = normalize_code(req.code)
        
        freq_map = {
            "1d": KL_TYPE.K_DAY,
            "30m": KL_TYPE.K_30M,
            "5m": KL_TYPE.K_5M,
            "15m": KL_TYPE.K_15M,
            "60m": KL_TYPE.K_60M,
            "1m": KL_TYPE.K_1M,
            "1w": KL_TYPE.K_WEEK,
            "1mo": KL_TYPE.K_MON,
        }
        level = freq_map.get(req.frequency, KL_TYPE.K_DAY)
        
        src_map = {
            "baostock": DATA_SRC.BAO_STOCK,
            "akshare": DATA_SRC.AK_SHARE,
            "ccxt": DATA_SRC.CCXT,
            "csv": DATA_SRC.CSV,
            "clickhouse": DATA_SRC.CLICK_HOUSE,
        }
        data_src = src_map.get(req.data_src, DATA_SRC.BAO_STOCK)
        raw_autype = str(getattr(req, "autype", "qfq") or "qfq").strip().lower()
        autype_map = {
            "qfq": AUTYPE.QFQ,
            "hfq": AUTYPE.HFQ,
            "none": AUTYPE.NONE,
        }
        autype = autype_map.get(raw_autype)
        if autype is None:
            raise HTTPException(status_code=400, detail="invalid autype, expect qfq/hfq/none")

        try:
            if isinstance(req.only_features, str) and not str(req.only_features).strip():
                req.only_features = None
        except Exception:
            pass

        model_key = str(req.model or "").strip().lower()
        if model_key in ["logistic_regression", "logreg", "lr"]:
            if req.rank_transform_enabled is None:
                req.rank_transform_enabled = True
            try:
                is_empty_list = isinstance(req.only_features, list) and len(req.only_features) == 0
            except Exception:
                is_empty_list = False
            if req.only_features is None or is_empty_list:
                req.only_features = ["volatility_250", "bsp3_zs_height"]
            raw_mode = str(getattr(req, "backtest_mode", "") or "").strip().lower()
            if raw_mode in ["", "auto"]:
                req.backtest_mode = "walk_forward"

        # 1. Calculate Time Range First
        import datetime
        
        begin_time = None
        end_time = None
        
        if req.start_date:
            begin_time = req.start_date
        if req.end_date:
            end_time = req.end_date

        if not begin_time:
            days = 365 # Default safe fallback
            
            if req.data_length_years is not None:
                days = int(365 * req.data_length_years)
            elif req.data_length_mode == "max":
                 # Max Ranges: 1m: 1 year, 5m: 2 years, 15m: 5 years, 30m+: 10 years
                if level == KL_TYPE.K_1M:
                    days = 365 # 1 year
                elif level == KL_TYPE.K_5M:
                    days = 365 * 2 # 2 years
                elif level == KL_TYPE.K_15M:
                    days = 365 * 5 # 5 years
                elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                     days = 365 * 10 # 10 years
            else:
                # Default Ranges: 1m: 0.5yr, 5m: 1.5yr, 15m: 2.5yr, 30m+: 5yr
                if level == KL_TYPE.K_1M:
                    days = 180 # 0.5 year
                elif level == KL_TYPE.K_5M:
                    days = 540 # 1.5 years
                elif level == KL_TYPE.K_15M:
                    days = 900 # 2.5 years
                elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                    days = 1800 # 5 years

            # Ensure minimal days
            days = max(days, 30)
            
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        # 2. Check Cache with latest data time
        # This avoids fetching full data if cache is valid
        latest_data_time = None
        kl_list = None
        
        try:
            # Only fetch the latest time (lightweight)
            # Use short lookback for efficiency
            latest_data_time = get_latest_data_time(req.code, level, data_src, autype=autype)
        except Exception as e:
             print(f"Error fetching latest time: {e}")

        params = {
            "trigger_step": req.trigger_step,
            "bi_strict": req.bi_strict,
            "model": req.model,
            "data_src": req.data_src,
            "do_predict": req.do_predict,
            "data_length_mode": req.data_length_mode,
            "data_length_years": req.data_length_years,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "rank_transform_enabled": req.rank_transform_enabled,
            "only_features": req.only_features,
            "profit_threshold": req.profit_threshold,
            "auto_profit_quantile": req.auto_profit_quantile,
            "profit_lookahead": req.profit_lookahead,
            "autype": autype.name,
            "backtest_mode": req.backtest_mode,
            "walk_forward_max_folds": req.walk_forward_max_folds,
            "walk_forward_test_window": req.walk_forward_test_window,
            "walk_forward_val_window": req.walk_forward_val_window,
            "walk_forward_step": req.walk_forward_step,
            "trade_cost": req.trade_cost,
            "topk_frac": req.topk_frac,
            "quantile_bin_count": req.quantile_bin_count,
        }
        env_keys = [
            "MLCHAN_RANK_TRANSFORM",
            "MLCHAN_RANK_MIN_N",
            "MLCHAN_RANK_FEATURES",
            "MLCHAN_WINSORIZE",
            "MLCHAN_WINSORIZE_LO",
            "MLCHAN_WINSORIZE_HI",
            "MLCHAN_WINSORIZE_MIN_N",
            "MLCHAN_MONOTONE_MODE",
            "MLCHAN_MONOTONE_OVERRIDES",
            "MLCHAN_ONLY_FEATURES",
            "MLCHAN_FORCE_FEATURES",
            "MLCHAN_DROP_FEATURES",
            "MLCHAN_IC_ABS_THRESHOLD",
            "MLCHAN_IC_TOP_N",
            "MLCHAN_IC_MIN_N",
        ]
        params["mlchan_env"] = {k: os.environ.get(k) for k in env_keys if os.environ.get(k) is not None}
        use_online_latest = bool(data_src == DATA_SRC.CLICK_HOUSE)
        params["use_online_latest"] = use_online_latest
        if req.use_pretrained is not None:
            params["use_pretrained"] = req.use_pretrained
        if req.pretrained_model_key is not None:
            params["pretrained_model_key"] = req.pretrained_model_key
        if req.blend_models is not None:
            params["blend_models"] = req.blend_models
        
        params["enable_rolling_lookback"] = req.enable_rolling_lookback

        if not bool(req.blend_models):
            params["use_pretrained"] = False
            params.pop("pretrained_model_key", None)
            params.pop("blend_models", None)

        if latest_data_time and not req.force_refresh:
            # Check Cache
            # 1. Try to find a result with prediction (do_predict=True) first, even if not requested
            # This allows displaying cached AI results automatically without re-running
            if not req.do_predict:
                better_params = params.copy()
                better_params['do_predict'] = True
                better_result = storage.get_latest_result(req.code, req.frequency, better_params, latest_data_time, begin_time)
                if better_result:
                    print(f"Cache hit (upgraded with prediction) for {req.code} {req.frequency} {latest_data_time}")
                    return json.loads(better_result.result_json)

            # 2. Normal cache check
            cached_result = storage.get_latest_result(req.code, req.frequency, params, latest_data_time, begin_time)
            
            if cached_result:
                print(f"Cache hit for {req.code} {req.frequency} {latest_data_time} begin={begin_time}")
                return json.loads(cached_result.result_json)
        
        # 3. Cache Miss or No Time info -> Fetch Full Data
        try:
            # Use the pre-calculated begin_time
            kl_list = fetch_stock_data(
                req.code,
                level,
                begin_time,
                end_time,
                data_src,
                autype=autype,
                use_online_latest=use_online_latest,
            )
        except Exception as e:
            print(f"Fetch data error: {e}")
            kl_list = []
            
        if not kl_list:
             raise HTTPException(status_code=404, detail=f"Data not found for {req.code}")
             
        latest_data_time = str(kl_list[-1].time)
        
        # 4. Run Analysis with preloaded data
        try:
            chan, bsp_dict, last_snapshot, config = get_chan_data(
                req.code, 
                req.trigger_step, 
                req.bi_strict, 
                level, 
                data_src, 
                preloaded_data=kl_list, 
                do_predict=req.do_predict,
                begin_time=begin_time,
                end_time=end_time,
                enable_rolling_lookback=req.enable_rolling_lookback,
                autype=autype,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error loading CChan: {e}")
        
        if not chan or not last_snapshot:
            raise HTTPException(status_code=404, detail=f"Data not found for {req.code}")
            
        # We assume single level (Day) for now, as per get_chan_data defaults
        lv = last_snapshot.lv_list[0]
        
        # Create PlotMeta
        # Note: GetChanData ensures last_snapshot is updated with virtual bi
        meta = CChanPlotMeta(last_snapshot.kl_datas[lv])
        
        # Serialize
        data = serialize_chan_data(meta, lv)
        
        # Add extra info
        last_klu = last_snapshot[0][-1][-1]
        data['latest_close'] = last_klu.close
        data['latest_date'] = last_klu.time.to_str()
        
        # Get Stock Name
        stock_name = get_stock_name(req.code, data_src)
        
        # Prediction Logic (simplified for API)
        # We can run the training here and return the prediction for the latest BSP
        
        latest_signal = None
        accuracy_info = None

        if req.do_predict:
            pretrained_bundle = None
            should_blend = bool(req.blend_models)
            if should_blend and req.use_pretrained is not False:
                if req.pretrained_model_key:
                    try:
                        pretrained_bundle = load_pretrained_model_bundle_by_key(req.pretrained_model_key)
                    except Exception:
                        pretrained_bundle = None

                if not pretrained_bundle:
                    try:
                        pretrained_bundle = load_pretrained_model_bundle(req.model, req.frequency, req.data_src, autype=autype)
                    except Exception:
                        pretrained_bundle = None

            if should_blend:
                online_bst, online_feature_meta, online_accuracy = train_time_split_backtest(
                    bsp_dict,
                    model_type=req.model,
                    calibrate_method=req.calibrate_method,
                    profit_threshold=req.profit_threshold,
                    auto_profit_quantile=req.auto_profit_quantile,
                    profit_lookahead=req.profit_lookahead,
                    use_atr_label=req.use_atr_label,
                    atr_period=req.atr_period,
                    atr_mult=req.atr_mult,
                    use_scale_pos_weight=req.use_scale_pos_weight,
                    backtest_mode=req.backtest_mode,
                    walk_forward_max_folds=req.walk_forward_max_folds,
                    walk_forward_test_window=req.walk_forward_test_window,
                    walk_forward_val_window=req.walk_forward_val_window,
                    walk_forward_step=req.walk_forward_step,
                    trade_cost=req.trade_cost,
                    topk_frac=req.topk_frac,
                    quantile_bin_count=req.quantile_bin_count,
                    xgb_max_depth=req.xgb_max_depth,
                    xgb_reg_alpha=req.xgb_reg_alpha,
                    xgb_reg_lambda=req.xgb_reg_lambda,
                    lgb_max_depth=req.lgb_max_depth,
                    lgb_reg_alpha=req.lgb_reg_alpha,
                    lgb_reg_lambda=req.lgb_reg_lambda,
                    rank_transform_enabled=req.rank_transform_enabled,
                    only_features=req.only_features,
                )
                if online_accuracy is not None:
                    online_accuracy["pretrained"] = False
                    online_accuracy["mode"] = "online"

                pretrained_bst = None
                pretrained_feature_meta = None
                pretrained_accuracy = None
                if pretrained_bundle and pretrained_bundle.get("model") and pretrained_bundle.get("feature_meta"):
                    pretrained_bst = pretrained_bundle["model"]
                    pretrained_feature_meta = pretrained_bundle["feature_meta"]
                    pt = req.profit_threshold
                    try:
                        if pt is None and isinstance(pretrained_bundle.get("meta"), dict):
                            pt = pretrained_bundle["meta"].get("profit_threshold", None)
                    except Exception:
                        pt = req.profit_threshold
                    pretrained_accuracy = evaluate_fixed_model_time_split(
                        bsp_dict,
                        pretrained_bst,
                        pretrained_feature_meta,
                        test_ratio=0.2,
                        threshold=0.5,
                        profit_threshold=pt,
                        auto_profit_quantile=req.auto_profit_quantile,
                        profit_lookahead=req.profit_lookahead,
                        trade_cost=req.trade_cost,
                        topk_frac=req.topk_frac,
                        quantile_bin_count=req.quantile_bin_count,
                    )
                    pretrained_accuracy["pretrained"] = True
                    pretrained_accuracy["mode"] = "pretrained"
                    pretrained_accuracy["pretrained_key"] = pretrained_bundle.get("key")
                    pretrained_accuracy["pretrained_trained_at"] = pretrained_bundle.get("trained_at")

                if pretrained_bst and online_bst:
                    w_pre = 0.0
                    w_on = 0.0
                    try:
                        w_pre = float(pretrained_accuracy.get("accuracy", 0.0)) if isinstance(pretrained_accuracy, dict) else 0.0
                    except Exception:
                        w_pre = 0.0
                    try:
                        w_on = float(online_accuracy.get("accuracy", 0.0)) if isinstance(online_accuracy, dict) else 0.0
                    except Exception:
                        w_on = 0.0
                    w_pre = max(0.01, w_pre)
                    w_on = max(0.01, w_on)

                    bst = online_bst
                    feature_meta = online_feature_meta
                    accuracy_info = dict(online_accuracy or {})
                    accuracy_info["mode"] = "ensemble"
                    accuracy_info["ensemble_weight_pretrained"] = w_pre
                    accuracy_info["ensemble_weight_online"] = w_on
                    accuracy_info["pretrained_key"] = pretrained_bundle.get("key") if pretrained_bundle else None
                    accuracy_info["pretrained_trained_at"] = pretrained_bundle.get("trained_at") if pretrained_bundle else None
                    accuracy_info["pretrained_accuracy"] = dict(pretrained_accuracy or {}) if isinstance(pretrained_accuracy, dict) else pretrained_accuracy
                    accuracy_info["online_accuracy"] = dict(online_accuracy or {}) if isinstance(online_accuracy, dict) else online_accuracy
                elif pretrained_bst:
                    bst = pretrained_bst
                    feature_meta = pretrained_feature_meta
                    accuracy_info = pretrained_accuracy
                else:
                    bst = online_bst
                    feature_meta = online_feature_meta
                    accuracy_info = online_accuracy
            else:
                bst, feature_meta, accuracy_info = train_time_split_backtest(
                    bsp_dict,
                    model_type=req.model,
                    calibrate_method=req.calibrate_method,
                    profit_threshold=req.profit_threshold,
                    auto_profit_quantile=req.auto_profit_quantile,
                    profit_lookahead=req.profit_lookahead,
                    use_atr_label=req.use_atr_label,
                    atr_period=req.atr_period,
                    atr_mult=req.atr_mult,
                    use_scale_pos_weight=req.use_scale_pos_weight,
                    backtest_mode=req.backtest_mode,
                    walk_forward_max_folds=req.walk_forward_max_folds,
                    walk_forward_test_window=req.walk_forward_test_window,
                    walk_forward_val_window=req.walk_forward_val_window,
                    walk_forward_step=req.walk_forward_step,
                    trade_cost=req.trade_cost,
                    topk_frac=req.topk_frac,
                    quantile_bin_count=req.quantile_bin_count,
                    xgb_max_depth=req.xgb_max_depth,
                    xgb_reg_alpha=req.xgb_reg_alpha,
                    xgb_reg_lambda=req.xgb_reg_lambda,
                    lgb_max_depth=req.lgb_max_depth,
                    lgb_reg_alpha=req.lgb_reg_alpha,
                    lgb_reg_lambda=req.lgb_reg_lambda,
                    rank_transform_enabled=req.rank_transform_enabled,
                    only_features=req.only_features,
                )
                if accuracy_info is not None:
                    accuracy_info["pretrained"] = False
                    accuracy_info["mode"] = "online"

            if bst:
                bsp_list = last_snapshot.get_latest_bsp()
                if bsp_list:
                    latest_bsp = bsp_list[0]
                    try:
                        cur_lv_chan = last_snapshot[0]
                        state_feat = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
                        if state_feat:
                            latest_bsp.features.add_feat(state_feat)
                    except Exception:
                        pass
                    latest_bsp.features.add_feat(stragety_feature(last_klu))
                    score = predict_bsp(bst, latest_bsp, feature_meta)
                    if req.blend_models and accuracy_info and accuracy_info.get("mode") == "ensemble":
                        try:
                            pretrained_bst = None
                            pretrained_feature_meta = None
                            if pretrained_bundle and pretrained_bundle.get("model") and pretrained_bundle.get("feature_meta"):
                                pretrained_bst = pretrained_bundle["model"]
                                pretrained_feature_meta = pretrained_bundle["feature_meta"]
                            online_bst = bst
                            online_feature_meta = feature_meta
                            if pretrained_bst and online_bst and pretrained_feature_meta and online_feature_meta:
                                score_pre = predict_bsp(pretrained_bst, latest_bsp, pretrained_feature_meta)
                                score_on = predict_bsp(online_bst, latest_bsp, online_feature_meta)
                                w_pre = float(accuracy_info.get("ensemble_weight_pretrained", 0.5) or 0.5)
                                w_on = float(accuracy_info.get("ensemble_weight_online", 0.5) or 0.5)
                                denom = (w_pre + w_on) if (w_pre + w_on) > 0 else 1.0
                                score = (w_pre * float(score_pre) + w_on * float(score_on)) / denom
                                accuracy_info["ensemble_score"] = float(score)
                                accuracy_info["pretrained_score"] = float(score_pre)
                                accuracy_info["online_score"] = float(score_on)
                        except Exception:
                            pass
                    days_diff = (last_klu.time.ts - latest_bsp.klu.time.ts) / (24 * 3600)
                    
                    latest_signal = {
                        "is_buy": latest_bsp.is_buy,
                        "type": latest_bsp.type2str(),
                        "date": latest_bsp.klu.time.to_str(),
                        "score": float(score),
                        "days_diff": float(days_diff)
                    }
        
        # Check for data warnings from Chan instance
        data_warnings = getattr(chan, 'data_warnings', [])

        result_data = {
            "status": "success",
            "data": data,
            "signal": latest_signal,
            "accuracy": accuracy_info,
            "stock_name": stock_name,
            "data_warnings": data_warnings
        }
        
        # Save to Storage
        try:
            # Combine signal info
            storage_signal_info = {}
            if latest_signal:
                storage_signal_info.update(latest_signal)
            if accuracy_info:
                storage_signal_info['accuracy'] = f"{accuracy_info['accuracy']:.2f}"
                
            # Only save if it's an AI analysis (do_predict=True)
            if req.do_predict:
                storage.save_result(
                    code=req.code,
                    freq=req.frequency,
                    params=params,
                    data_latest_time=latest_data_time,
                    result_dict=result_data,
                    model=req.model,
                    signal_info=storage_signal_info,
                    begin_time=begin_time,
                    end_time=None # End time is implicitly 'now' or latest_data_time
                )
        except Exception as e:
            print(f"Failed to save cache: {e}")
            
        return result_data
        
    except HTTPException as e:
        raise e
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/validate")
async def validate_codes(req: ValidateRequest):
    try:
        codes = []
        for c in (req.codes or []):
            s = str(c or "").strip()
            if not s:
                continue
            try:
                codes.append(normalize_code(s))
            except Exception:
                codes.append(s)
        codes = sorted(list(dict.fromkeys(codes)))
        if not codes:
            raise HTTPException(status_code=400, detail="codes is empty")

        freq_map = {
            "1d": KL_TYPE.K_DAY,
            "30m": KL_TYPE.K_30M,
            "5m": KL_TYPE.K_5M,
            "15m": KL_TYPE.K_15M,
            "60m": KL_TYPE.K_60M,
            "1m": KL_TYPE.K_1M,
            "1w": KL_TYPE.K_WEEK,
            "1mo": KL_TYPE.K_MON,
        }
        level = freq_map.get(req.frequency, KL_TYPE.K_DAY)

        src_map = {
            "baostock": DATA_SRC.BAO_STOCK,
            "akshare": DATA_SRC.AK_SHARE,
            "ccxt": DATA_SRC.CCXT,
            "csv": DATA_SRC.CSV,
            "clickhouse": DATA_SRC.CLICK_HOUSE,
        }
        data_src = src_map.get(req.data_src, DATA_SRC.BAO_STOCK)

        raw_autype = str(getattr(req, "autype", "qfq") or "qfq").strip().lower()
        autype_map = {
            "qfq": AUTYPE.QFQ,
            "hfq": AUTYPE.HFQ,
            "none": AUTYPE.NONE,
        }
        autype = autype_map.get(raw_autype)
        if autype is None:
            raise HTTPException(status_code=400, detail="invalid autype, expect qfq/hfq/none")

        import datetime
        begin_time = None
        end_time = None
        if req.start_date:
            begin_time = req.start_date
        if req.end_date:
            end_time = req.end_date
        if not begin_time:
            days = 365
            if req.data_length_years is not None:
                days = int(365 * req.data_length_years)
            elif req.data_length_mode == "max":
                if level == KL_TYPE.K_1M:
                    days = 365
                elif level == KL_TYPE.K_5M:
                    days = 365 * 2
                elif level == KL_TYPE.K_15M:
                    days = 365 * 5
                elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                    days = 365 * 10
            else:
                if level == KL_TYPE.K_1M:
                    days = 180
                elif level == KL_TYPE.K_5M:
                    days = 540
                elif level == KL_TYPE.K_15M:
                    days = 900
                elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                    days = 1800
            days = max(days, 30)
            begin_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        use_online_latest = bool(data_src == DATA_SRC.CLICK_HOUSE)
        results = []
        for code in codes:
            item = {"code": code, "status": "error"}
            try:
                kl_list = fetch_stock_data(
                    code,
                    level,
                    begin_time,
                    end_time,
                    data_src,
                    autype=autype,
                    use_online_latest=use_online_latest,
                )
                if not kl_list:
                    item["detail"] = "no_data"
                    results.append(item)
                    continue

                _, bsp_dict, _, _ = get_chan_data(
                    code,
                    True,
                    True,
                    level,
                    data_src,
                    preloaded_data=kl_list,
                    do_predict=True,
                    begin_time=begin_time,
                    end_time=end_time,
                    enable_rolling_lookback=req.enable_rolling_lookback,
                    autype=autype,
                )
                if not bsp_dict:
                    item["detail"] = "no_bsp"
                    results.append(item)
                    continue

                _, _, accuracy_info = train_time_split_backtest(
                    bsp_dict,
                    model_type=req.model,
                    calibrate_method=req.calibrate_method,
                    profit_threshold=req.profit_threshold,
                    auto_profit_quantile=req.auto_profit_quantile,
                    profit_lookahead=req.profit_lookahead,
                    use_atr_label=req.use_atr_label,
                    atr_period=req.atr_period,
                    atr_mult=req.atr_mult,
                    use_scale_pos_weight=req.use_scale_pos_weight,
                    backtest_mode=req.backtest_mode,
                    walk_forward_max_folds=req.walk_forward_max_folds,
                    walk_forward_test_window=req.walk_forward_test_window,
                    walk_forward_val_window=req.walk_forward_val_window,
                    walk_forward_step=req.walk_forward_step,
                    trade_cost=req.trade_cost,
                    topk_frac=req.topk_frac,
                    quantile_bin_count=req.quantile_bin_count,
                    xgb_max_depth=req.xgb_max_depth,
                    xgb_reg_alpha=req.xgb_reg_alpha,
                    xgb_reg_lambda=req.xgb_reg_lambda,
                    lgb_max_depth=req.lgb_max_depth,
                    lgb_reg_alpha=req.lgb_reg_alpha,
                    lgb_reg_lambda=req.lgb_reg_lambda,
                )
                keep = [
                    "method",
                    "train_count",
                    "val_count",
                    "test_count",
                    "valid_count",
                    "total_count",
                    "accuracy",
                    "roc_auc",
                    "pr_auc",
                    "brier_score",
                    "ece",
                    "logloss",
                    "selected_features",
                    "feature_importance_full",
                    "feature_importance",
                    "warning",
                ]
                if isinstance(accuracy_info, dict):
                    slim = _shrink_accuracy_result(accuracy_info)
                    for k in ("feature_importance_full", "feature_importance"):
                        if k in accuracy_info:
                            slim[k] = accuracy_info.get(k)
                else:
                    slim = {}
                item["status"] = "success"
                item["accuracy"] = slim
            except Exception as e:
                item["detail"] = str(e)
            results.append(item)

        return {
            "status": "success",
            "begin_time": begin_time,
            "end_time": end_time,
            "frequency": req.frequency,
            "data_src": req.data_src,
            "model": req.model,
            "autype": autype.name,
            "results": results,
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/pretrain")
async def pretrain_model(req: PretrainRequest):
    try:
        freq_map = {
            "1d": KL_TYPE.K_DAY,
            "30m": KL_TYPE.K_30M,
            "5m": KL_TYPE.K_5M,
            "15m": KL_TYPE.K_15M,
            "60m": KL_TYPE.K_60M,
            "1m": KL_TYPE.K_1M,
            "1w": KL_TYPE.K_WEEK,
            "1mo": KL_TYPE.K_MON,
        }
        level = freq_map.get(req.frequency, KL_TYPE.K_DAY)

        src_map = {
            "baostock": DATA_SRC.BAO_STOCK,
            "akshare": DATA_SRC.AK_SHARE,
            "ccxt": DATA_SRC.CCXT,
            "csv": DATA_SRC.CSV,
            "clickhouse": DATA_SRC.CLICK_HOUSE,
        }
        data_src_type = src_map.get(req.data_src, DATA_SRC.BAO_STOCK)

        raw_autype = str(getattr(req, "autype", "qfq") or "qfq").strip().lower()
        autype_map = {
            "qfq": AUTYPE.QFQ,
            "hfq": AUTYPE.HFQ,
            "none": AUTYPE.NONE,
        }
        autype = autype_map.get(raw_autype)
        if autype is None:
            autype = AUTYPE.HFQ
        profit_lookahead = req.profit_lookahead
        try:
            profit_lookahead = int(float(profit_lookahead)) if profit_lookahead not in ["", "null", "None", None] else None
        except Exception:
            profit_lookahead = None
        if profit_lookahead is None or profit_lookahead <= 0:
            profit_lookahead = _default_profit_lookahead_for_frequency(req.frequency)

        import datetime
        days = 365
        if req.data_length_years is not None:
            days = int(365 * req.data_length_years)
        elif req.data_length_mode == "max":
            if level == KL_TYPE.K_1M:
                days = 365
            elif level == KL_TYPE.K_5M:
                days = 365 * 2
            elif level == KL_TYPE.K_15M:
                days = 365 * 5
            elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                days = 365 * 10
        else:
            if level == KL_TYPE.K_1M:
                days = 180
            elif level == KL_TYPE.K_5M:
                days = 540
            elif level == KL_TYPE.K_15M:
                days = 900
            elif level in [KL_TYPE.K_30M, KL_TYPE.K_60M, KL_TYPE.K_DAY, KL_TYPE.K_WEEK, KL_TYPE.K_MON]:
                days = 1800
        days = max(days, 30)
        begin_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        path, result = pretrain_and_persist_model(
            codes=req.codes,
            level=level,
            data_src_type=data_src_type,
            begin_time=begin_time,
            model_type=req.model,
            frequency=req.frequency,
            data_src=req.data_src,
            calibrate_method="none",
            profit_threshold=req.profit_threshold,
            auto_profit_quantile=req.auto_profit_quantile,
            profit_lookahead=profit_lookahead,
            autype=autype,
            high_vol_atr_pct_min=req.high_vol_atr_pct_min,
            use_atr_label=bool(req.use_atr_label),
            atr_period=int(req.atr_period),
            atr_mult=float(req.atr_mult),
            trade_cost=float(req.trade_cost or 0.0),
            topk_frac=float(req.topk_frac or 0.2),
            portfolio_min_score=float(req.portfolio_min_score or 0.6),
            portfolio_top_n=int(req.portfolio_top_n or 2),
        )
        if not path:
            raise HTTPException(status_code=400, detail=result.get("detail", "pretrain failed"))
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/indices")
async def list_indices(limit: int = 2000, keyword: Optional[str] = None):
    try:
        from DataAPI.ClickHouseAPI import CClickHouseAPI
        items = CClickHouseAPI.list_indices(limit=limit, keyword=keyword)
        return {"items": items}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock_pools")
async def list_stock_pools():
    try:
        from DataAPI.ClickHouseAPI import CClickHouseAPI
        items = CClickHouseAPI.list_stock_pools(table="stock_constituent")
        return {"items": items}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock_pools/{pool_id}/members")
async def get_stock_pool_members(pool_id: str, limit: int = 5000):
    try:
        from DataAPI.ClickHouseAPI import CClickHouseAPI
        raw_codes = CClickHouseAPI.get_pool_members(pool_id=pool_id, table="stock_constituent", limit=limit)
        codes = []
        for c in raw_codes:
            try:
                codes.append(normalize_code(c))
            except Exception:
                codes.append(str(c))
        codes = sorted(list(dict.fromkeys(codes)))
        return {"pool_id": pool_id, "codes": codes}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download/{code}")
async def download_data(code: str, frequency: str = "1d"):
    try:
        csv_content = download_stock_history(code, frequency)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={code}_{frequency}.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class StrategyRequest(BaseModel):
    strategy_name: str
    scope: str = "hs300"
    pool_id: Optional[str] = None
    index_code: str = "399006"
    model: str = "xgboost"
    autype: str = "hfq"
    min_accuracy: float = 0.8
    min_recent_accuracy: Optional[float] = None
    recent_accuracy_years: float = 1.0
    min_signal_score: Optional[float] = None
    min_bsp_count: int = 0
    min_test_count: int = 0
    high_vol_atr_pct_min: Optional[float] = None
    use_atr_label: bool = False
    atr_period: int = 14
    atr_mult: float = 1.0
    profit_threshold: Optional[float] = 0.01
    auto_profit_quantile: float = 0.7
    profit_lookahead: int = 5
    frequency: str = "1d"
    data_length_years: float = 1.0
    chan_config: Optional[Dict[str, Any]] = None
    require_signal: bool = False
    signal_lookback: int = 5
    signal_direction: str = "buy"  # buy, sell, both
    enable_rolling_lookback: bool = True # Enable/Disable long-term context features (250-bar lookback)
    amp_whitelist_days: int = 0
    amp_whitelist_top_frac: float = 0.3
    portfolio_top_n: int = 2
    holding_period: int = 3

class StrategyRunsBatchDeleteRequest(BaseModel):
    run_ids: List[int] = []

class LiveScanFeaturesRequest(BaseModel):
    pool_id: str = "index:IDX399673"
    frequency: str = "30m"
    data_src: str = "clickhouse"
    autype: str = "qfq"
    lookback_days: int = 120
    enable_rolling_lookback: bool = True
    bsp_feature_mode: str = "latest_bsp"
    rank_mode: str = "cross_section"
    rolling_sample_mode: str = "every_bar"
    rolling_stride: int = 1
    max_workers: int = 0
    limit: int = 0

class LivePaperTradeStepRequest(BaseModel):
    sim_id: Optional[str] = None
    run_id: Optional[str] = None
    portfolio_id: Optional[int] = None
    pool_id: str = "index:IDX399673"
    frequency: str = "30m"
    data_src: str = "clickhouse"
    autype: str = "qfq"
    lookback_days: int = 365 * 5
    enable_rolling_lookback: bool = True
    top_n: int = 2
    min_prob: float = 0.25
    holding_period: int = 3
    trade_cost: float = 0.0002
    walk_forward_max_folds: int = 25
    max_workers: int = 8
    limit: int = 0
    use_latest_available_day: bool = True
    pretrained_model_key: Optional[str] = None
    rank_mode: str = "cross_section"
    rolling_sample_mode: str = "every_bar"
    rolling_stride: int = 1
    risk_filter_enabled: bool = True
    risk_mode: str = "both"
    risk_index_code: str = "399006"
    risk_drop_threshold: float = -0.03
    risk_flat_drop_threshold: float = -0.05
    risk_atr_window: int = 14
    risk_atr_pct_threshold: float = 0.04
    risk_flat_atr_pct_threshold: float = 0.06
    risk_gap_down_threshold: float = -0.015
    risk_intraday_enabled: bool = True
    risk_intraday_bear_threshold: float = -0.015
    risk_intraday_bull_threshold: float = -0.03
    risk_trend_ma_window: int = 20
    risk_data_fail_mode: str = "reduce"
    risk_intraday_data_fail_mode: str = "reduce"
    sell_mode: str = "baseline"
    t1_decision_time: str = "10:00"
    t1_gap_strong_threshold: float = 0.03
    t1_gap_weak_threshold: float = -0.02
    t1_gap_very_strong_threshold: float = 0.05

class LiveSimUpsertRequest(BaseModel):
    sim_id: str
    name: Optional[str] = None


class LiveRunCreateRequest(BaseModel):
    run_id: Optional[str] = None
    name: str = ""
    sim_id: str = "default"
    portfolio_id: Optional[int] = None
    params: dict = {}
    offset_seconds: int = 5
    auto_resume: bool = True
    notify_enabled: bool = False
    notify_kind: str = ""
    notify_webhook: str = ""
    notify_secret: str = ""
    sms_enabled: bool = False
    sms_phone: str = ""
    sms_token: str = ""
    start: bool = True

@app.post("/api/live/scan_features")
async def live_scan_features(req: LiveScanFeaturesRequest):
    try:
        import concurrent.futures
        import os
        import time
        from DataAPI.ClickHouseAPI import CClickHouseAPI
        from backend.live.common import compute_volatility_250, parse_autype, parse_data_src, parse_frequency
        from backend.chan_service import cross_section_rank_transform_rows, extract_state_features

        pool_id = str(req.pool_id or "").strip() or "index:IDX399673"
        raw_codes = CClickHouseAPI.get_pool_members(pool_id=pool_id, table="stock_constituent", limit=5000)
        codes = []
        for c in raw_codes or []:
            try:
                codes.append(normalize_code(str(c)))
            except Exception:
                continue
        codes = list(dict.fromkeys([c for c in codes if c]))
        if int(req.limit or 0) > 0:
            codes = codes[: int(req.limit)]

        freq_raw, level = parse_frequency(req.frequency)
        data_src = parse_data_src(req.data_src)
        autype = parse_autype("qfq")

        end = datetime.datetime.now().strftime("%Y-%m-%d")
        begin = (datetime.datetime.now() - datetime.timedelta(days=int(req.lookback_days or 120))).strftime("%Y-%m-%d")
        want_bsp = str(req.bsp_feature_mode or "").strip().lower() == "latest_bsp"
        want_rank = str(req.rank_mode or "").strip().lower() == "cross_section"
        rolling_sample_mode = str(req.rolling_sample_mode or "").strip() or "every_bar"
        rolling_stride = int(req.rolling_stride) if req.rolling_stride is not None else 1
        try:
            req_mw = int(req.max_workers or 0)
        except Exception:
            req_mw = 0
        try:
            env_mw_raw = os.environ.get("MLCHAN_LIVE_MAX_WORKERS", "").strip()
            env_mw = int(env_mw_raw) if env_mw_raw else 0
        except Exception:
            env_mw = 0
        if env_mw and env_mw > 0:
            base_mw = req_mw if req_mw and req_mw > 0 else env_mw
            mw = min(int(base_mw), int(env_mw))
        else:
            mw = req_mw if req_mw and req_mw > 0 else 1
        mw = max(1, min(int(mw), int(len(codes) if codes else 1)))

        def scan_one(code: str):
            kl_list = fetch_stock_data(code, level, begin, end, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE))
            if not kl_list:
                return {
                    "code": code,
                    "ok": False,
                    "reason": "no_data",
                    "volatility_250": 0.0,
                    "volatility_250_rank": None,
                    "bsp3_zs_height": None,
                    "zs_last_height": None,
                    "zs_last_height_rank": None,
                    "sample_ts": None,
                }
            vol = compute_volatility_250(kl_list)
            zs = None
            zs_last_height = None
            sample_ts = 0
            try:
                if want_bsp:
                    chan_snapshot, bsp_dict, _, _ = get_chan_data(
                        code,
                        True,
                        True,
                        level,
                        data_src,
                        preloaded_data=kl_list,
                        do_predict=True,
                        begin_time=begin,
                        end_time=end,
                        enable_rolling_lookback=bool(req.enable_rolling_lookback),
                        autype=autype,
                        rolling_sample_mode=rolling_sample_mode,
                        rolling_stride=rolling_stride,
                    )
                else:
                    chan_snapshot, _, _, _ = get_chan_data(
                        code,
                        True,
                        True,
                        level,
                        data_src,
                        preloaded_data=kl_list,
                        do_predict=False,
                        begin_time=begin,
                        end_time=end,
                        enable_rolling_lookback=bool(req.enable_rolling_lookback),
                        autype=autype,
                        rolling_sample_mode=rolling_sample_mode,
                        rolling_stride=rolling_stride,
                    )
                    bsp_dict = None

                try:
                    cur_lv_chan = chan_snapshot[0]
                    last_klu = cur_lv_chan[-1][-1]
                    sample_ts = int(getattr(getattr(last_klu, "time", None), "ts", 0) or 0)
                except Exception:
                    sample_ts = 0
                try:
                    state_feat = extract_state_features(chan_snapshot) or {}
                    v = state_feat.get("zs_last_height")
                    zs_last_height = float(v) if v is not None else None
                except Exception:
                    zs_last_height = None

                if want_bsp and bsp_dict:
                    last_ts = 0
                    last_v = None
                    for _, info in (bsp_dict or {}).items():
                        try:
                            ot = info.get("open_time")
                            ts = int(getattr(ot, "ts", 0) or 0)
                        except Exception:
                            ts = 0
                        if ts <= 0 or ts < last_ts:
                            continue
                        try:
                            feat = info.get("feature")
                        except Exception:
                            feat = None
                        if not feat:
                            continue
                        try:
                            v = feat.get("bsp3_zs_height")
                        except Exception:
                            v = None
                        if v is None:
                            continue
                        try:
                            last_v = float(v)
                            last_ts = ts
                        except Exception:
                            continue
                    zs = last_v
            except Exception:
                zs = None
                zs_last_height = None
                sample_ts = 0

            return {
                "code": code,
                "ok": True,
                "reason": None,
                "volatility_250": float(vol),
                "volatility_250_rank": None,
                "bsp3_zs_height": (float(zs) if zs is not None else None),
                "zs_last_height": (float(zs_last_height) if zs_last_height is not None else None),
                "zs_last_height_rank": None,
                "sample_ts": (int(sample_ts) if int(sample_ts or 0) > 0 else None),
            }

        items = []
        if mw <= 1 or not codes:
            for code in codes:
                items.append(scan_one(code))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=int(mw)) as ex:
                futs = {ex.submit(scan_one, code): code for code in codes}
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        r = fut.result()
                    except Exception:
                        r = None
                    if r:
                        items.append(r)

        if want_rank and items:
            rows = []
            item_idxs = []
            for i, it in enumerate(items):
                if not isinstance(it, dict) or not bool(it.get("ok")):
                    continue
                rows.append(
                    {
                        "feature": {
                            "volatility_250": it.get("volatility_250", -9999999.0),
                            "zs_last_height": it.get("zs_last_height", -9999999.0),
                        }
                    }
                )
                item_idxs.append(i)
            if rows:
                cross_section_rank_transform_rows(rows, ["volatility_250", "zs_last_height"], missing_value=-9999999.0)
                for local_i, item_i in enumerate(item_idxs):
                    try:
                        it = items[item_i]
                        feat = rows[local_i].get("feature") or {}
                        v_rank = feat.get("volatility_250")
                        z_rank = feat.get("zs_last_height")
                        it["volatility_250_rank"] = float(v_rank) if isinstance(v_rank, (int, float)) else None
                        it["zs_last_height_rank"] = float(z_rank) if isinstance(z_rank, (int, float)) else None
                    except Exception:
                        continue

        items.sort(key=lambda x: float(x.get("volatility_250") or 0.0), reverse=True)
        return {
            "ts": int(time.time()),
            "pool_id": pool_id,
            "frequency": freq_raw,
            "rank_mode": (str(req.rank_mode or "").strip() or "cross_section"),
            "rolling_sample_mode": rolling_sample_mode,
            "rolling_stride": rolling_stride,
            "max_workers": int(mw),
            "items": items,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live/positions")
async def live_list_positions(sim_id: Optional[str] = None, portfolio_id: Optional[int] = None):
    try:
        return {"items": storage.list_positions(sim_id=sim_id, portfolio_id=portfolio_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live/orders")
async def live_list_orders(status: Optional[str] = "pending", limit: int = 200, sim_id: Optional[str] = None, portfolio_id: Optional[int] = None):
    try:
        st = str(status or "").strip()
        if not st:
            st = None
        return {"items": storage.list_live_orders(status=st, limit=limit, sim_id=sim_id, portfolio_id=portfolio_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live/trades")
async def live_list_trades(
    limit: int = 200,
    page: Optional[int] = None,
    page_size: int = 50,
    sim_id: Optional[str] = None,
    portfolio_id: Optional[int] = None,
):
    try:
        if page is not None:
            return storage.list_live_trades_paged(page=page, page_size=page_size, sim_id=sim_id, portfolio_id=portfolio_id)
        return {"items": storage.list_live_trades(limit=limit, sim_id=sim_id, portfolio_id=portfolio_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/live/sims")
async def live_upsert_sim(req: LiveSimUpsertRequest):
    try:
        return {"item": storage.upsert_live_sim(sim_id=req.sim_id, name=req.name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live/sims")
async def live_list_sims(limit: int = 200):
    try:
        return {"items": storage.list_live_sims(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/live/runs")
async def live_create_run(req: LiveRunCreateRequest):
    try:
        rid = str(req.run_id or "").strip() or uuid.uuid4().hex[:32]
        nm = str(req.name or "").strip()
        params = dict(req.params or {})
        sim_id = str(req.sim_id or params.get("sim_id") or "").strip() or "default"
        params["sim_id"] = sim_id
        pid = req.portfolio_id
        if pid is None:
            pid = params.get("portfolio_id")
        params["portfolio_id"] = pid
        st = "running" if bool(req.start) else "stopped"
        nxt = int(time.time()) if st == "running" else 0
        out = storage.upsert_live_run(
            run_id=rid,
            sim_id=sim_id,
            name=nm,
            status=st,
            params=params,
            portfolio_id=pid,
            next_exec_ts=int(nxt),
            offset_seconds=int(req.offset_seconds or 5),
            auto_resume=bool(req.auto_resume),
            notify_enabled=bool(req.notify_enabled),
            notify_kind=str(req.notify_kind or "").strip(),
            notify_webhook=str(req.notify_webhook or "").strip(),
            notify_secret=str(req.notify_secret or "").strip(),
            sms_enabled=bool(req.sms_enabled),
            sms_phone=str(req.sms_phone or "").strip(),
            sms_token=str(req.sms_token or "").strip(),
        )
        if st == "running":
            try:
                off = int(req.offset_seconds or 5)
            except Exception:
                off = 5
            notify_enabled = bool(req.notify_enabled)
            enqueued = _enqueue_live_task_dedup(
                "live_step",
                str(rid),
                lambda rid=rid, sid=sim_id, ne=notify_enabled, off=int(off): _run_live_step_task(str(rid), str(sid), bool(ne), int(off)),
            )
            if enqueued:
                with _live_run_running_lock:
                    _live_run_running.add(str(rid))
        return {"item": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live/runs")
async def live_list_runs(status: Optional[str] = None, limit: int = 200, portfolio_id: Optional[int] = None):
    try:
        st = str(status or "").strip().lower() or None
        return {"items": storage.list_live_runs(status=st, limit=limit, portfolio_id=portfolio_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live/steps")
async def live_list_steps(sim_id: Optional[str] = None, run_id: Optional[str] = None, portfolio_id: Optional[int] = None, page: int = 1, page_size: int = 50):
    try:
        return storage.list_live_steps_paged(sim_id=sim_id, run_id=run_id, portfolio_id=portfolio_id, page=page, page_size=page_size)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live/steps/stocks")
async def live_list_step_stocks(sim_id: Optional[str] = None, run_id: Optional[str] = None, portfolio_id: Optional[int] = None, page: int = 1, page_size: int = 10):
    try:
        return storage.list_live_step_stocks_paged(sim_id=sim_id, run_id=run_id, portfolio_id=portfolio_id, page=page, page_size=page_size)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live/equity_curve")
async def live_equity_curve(sim_id: Optional[str] = None, run_id: Optional[str] = None, portfolio_id: Optional[int] = None, limit: int = 2000):
    try:
        sid = str(sim_id or "").strip() or "default"
        tn = 2
        if run_id:
            r = storage.get_live_run(str(run_id))
            if r and isinstance(r.get("params"), dict):
                try:
                    tn = int((r.get("params") or {}).get("top_n") or 2)
                except Exception:
                    tn = 2
        tn = max(1, int(tn))
        trades = storage.list_live_trades(limit=limit, sim_id=sid, portfolio_id=portfolio_id) or []
        trades = sorted(trades, key=lambda x: int(x.get("trade_ts") or 0))
        last_buy = {}
        eq = 1.0
        pts = []
        for tr in trades:
            code = str(tr.get("stock_code") or "").strip()
            side = str(tr.get("side") or "").strip().lower()
            try:
                px = float(tr.get("price") or 0.0)
            except Exception:
                px = 0.0
            ts = int(tr.get("trade_ts") or 0)
            if not code or px <= 0.0 or ts <= 0:
                continue
            if side == "buy":
                last_buy[code] = float(px)
                continue
            if side == "sell":
                bp = last_buy.get(code)
                if bp and bp > 0.0:
                    rret = float(px / bp - 1.0)
                    eq = float(eq) * float(1.0 + rret / float(tn))
                pts.append({"ts": int(ts), "equity": float(eq), "code": code, "side": side, "price": float(px)})
        return {"sim_id": sid, "run_id": str(run_id or ""), "portfolio_id": portfolio_id, "top_n": int(tn), "points": pts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live/runs/{run_id}")
async def live_get_run(run_id: str):
    try:
        out = storage.get_live_run(run_id)
        if not out:
            raise HTTPException(status_code=404, detail="run not found")
        return {"item": out}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/live/runs/{run_id}/start")
async def live_start_run(run_id: str):
    try:
        r = storage.get_live_run(run_id)
        if not r:
            raise HTTPException(status_code=404, detail="run not found")
        nxt = int(time.time())
        out = storage.update_live_run_fields(run_id, {"status": "running", "next_exec_ts": int(nxt), "error": ""})
        try:
            sim_id = str((r or {}).get("sim_id") or "").strip() or "default"
        except Exception:
            sim_id = "default"
        try:
            notify_enabled = bool((r or {}).get("notify_enabled"))
        except Exception:
            notify_enabled = False
        try:
            off = int((r or {}).get("offset_seconds") or 5)
        except Exception:
            off = 5
        enqueued = _enqueue_live_task_dedup(
            "live_step",
            str(run_id),
            lambda rid=str(run_id), sid=sim_id, ne=notify_enabled, off=int(off): _run_live_step_task(str(rid), str(sid), bool(ne), int(off)),
        )
        if enqueued:
            with _live_run_running_lock:
                _live_run_running.add(str(run_id))
        return {"item": out}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/live/runs/{run_id}/stop")
async def live_stop_run(run_id: str):
    try:
        out = storage.update_live_run_fields(run_id, {"status": "stopped", "next_exec_ts": 0, "stopped_at": datetime.datetime.utcnow()})
        if not out:
            raise HTTPException(status_code=404, detail="run not found")
        return {"item": out}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/live/runs/{run_id}")
async def live_delete_run(run_id: str):
    try:
        ok = storage.delete_live_run(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail="run not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/live/positions_report")
async def live_positions_report(
    frequency: str = "30m",
    data_src: str = "clickhouse",
    autype: str = "qfq",
    lookback_days: int = 30,
    sim_id: Optional[str] = None,
    portfolio_id: Optional[int] = None,
):
    try:
        from backend.live.common import parse_autype, parse_data_src, parse_frequency

        freq_raw, level = parse_frequency(frequency)
        ds = parse_data_src(data_src)
        au = parse_autype("qfq")

        positions_raw = storage.list_positions(sim_id=sim_id, portfolio_id=portfolio_id) or []
        now_dt = datetime.datetime.now()
        end = now_dt.strftime("%Y-%m-%d")
        begin_dt = now_dt - datetime.timedelta(days=int(lookback_days or 30))
        try:
            entry_ts_list = [int(p.get("entry_time") or 0) for p in positions_raw]
            entry_ts_list = [ts for ts in entry_ts_list if ts > 0]
        except Exception:
            entry_ts_list = []
        if entry_ts_list:
            try:
                earliest_dt = datetime.datetime.fromtimestamp(int(min(entry_ts_list))) - datetime.timedelta(days=7)
                if earliest_dt < begin_dt:
                    begin_dt = earliest_dt
            except Exception:
                pass
        begin = begin_dt.strftime("%Y-%m-%d")

        def _k_ts(k) -> int:
            try:
                return int(getattr(getattr(k, "time", None), "ts", 0) or 0)
            except Exception:
                return 0

        def _find_entry_close(kl_list, entry_ts: int) -> float:
            if not kl_list:
                return 0.0
            ts = int(entry_ts or 0)
            if ts <= 0:
                return 0.0
            best = None
            best_ts = 0
            first_ts = 0
            last_ts = 0
            for k in kl_list:
                kts = _k_ts(k)
                if kts <= 0:
                    continue
                if first_ts <= 0:
                    first_ts = kts
                last_ts = kts
                if kts <= ts and kts >= best_ts:
                    best_ts = kts
                    best = k
            if best is not None:
                try:
                    return float(getattr(best, "close", 0.0) or 0.0)
                except Exception:
                    return 0.0
            if first_ts > 0 and ts < first_ts:
                try:
                    return float(getattr(kl_list[0], "close", 0.0) or 0.0)
                except Exception:
                    return 0.0
            if last_ts > 0 and ts > last_ts:
                try:
                    return float(getattr(kl_list[-1], "close", 0.0) or 0.0)
                except Exception:
                    return 0.0
            return 0.0

        items = []
        total_cost = 0.0
        total_mv = 0.0
        for p in positions_raw:
            code = str(p.get("stock_code") or "").strip()
            if not code:
                continue
            last_price = 0.0
            entry_price_qfq = 0.0
            try:
                kl_list = fetch_stock_data(
                    code,
                    level,
                    begin,
                    end,
                    ds,
                    autype=au,
                    use_online_latest=bool(ds == DATA_SRC.CLICK_HOUSE),
                )
                if kl_list:
                    last_price = float(getattr(kl_list[-1], "close", 0.0) or 0.0)
                    entry_price_qfq = float(_find_entry_close(kl_list, int(p.get("entry_time") or 0)) or 0.0)
            except Exception:
                last_price = 0.0

            entry_price = entry_price_qfq if entry_price_qfq > 0 else float(p.get("entry_price") or 0.0)
            pnl = (last_price - entry_price) if (entry_price and last_price) else 0.0
            pnl_pct = (pnl / entry_price) if entry_price else 0.0
            total_cost += entry_price
            total_mv += last_price
            items.append({
                **p,
                "frequency": str(freq_raw or ""),
                "entry_price": float(entry_price),
                "current_price": float(last_price),
                "pnl": float(pnl),
                "pnl_pct": float(pnl_pct),
            })

        total_pnl = total_mv - total_cost
        total_pnl_pct = (total_pnl / total_cost) if total_cost else 0.0
        return {
            "items": items,
            "summary": {
                "total_cost": float(total_cost),
                "total_market_value": float(total_mv),
                "total_pnl": float(total_pnl),
                "total_pnl_pct": float(total_pnl_pct),
            },
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/live/positions/reset")
async def live_reset_positions(sim_id: Optional[str] = None, portfolio_id: Optional[int] = None):
    try:
        try:
            storage.clear_positions(sim_id=sim_id, portfolio_id=portfolio_id)
        except Exception:
            pass
        try:
            storage.clear_live_orders(sim_id=sim_id, portfolio_id=portfolio_id)
        except Exception:
            pass
        try:
            storage.clear_live_trades(sim_id=sim_id, portfolio_id=portfolio_id)
        except Exception:
            pass
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/live/steps/reset")
async def live_reset_steps(sim_id: Optional[str] = None, portfolio_id: Optional[int] = None):
    try:
        storage.clear_live_steps(sim_id=sim_id, portfolio_id=portfolio_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class StrategyStopRequest(BaseModel):
    run_id: str

@app.post("/api/strategy/stop")
async def stop_strategy(req: StrategyStopRequest):
    try:
        r = storage.get_live_run(req.run_id)
        if not r:
            return {"status": "error", "message": "Run not found"}
        # Mark as stopped
        # ... implementation ...
        # For now, we rely on the client checking "active_runs" in live_paper_trade_step
        # But we should update status in DB
        storage.update_live_run_status(req.run_id, "stopped")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _scan_feature_worker(code, level, begin, end, data_src, autype, enable_rolling_lookback, rolling_sample_mode, rolling_stride):
    try:
        kl_list = fetch_stock_data(code, level, begin, end, data_src, autype=autype, use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE))
        if not kl_list or len(kl_list) < 600:
            return None
        _, bsp_dict, _, _ = get_chan_data(
            code,
            True,
            True,
            level,
            data_src,
            preloaded_data=kl_list,
            do_predict=True,
            begin_time=begin,
            end_time=end,
            enable_rolling_lookback=enable_rolling_lookback,
            autype=autype,
            rolling_sample_mode=rolling_sample_mode,
            rolling_stride=rolling_stride,
        )
        if not bsp_dict:
            return None
        best = None
        best_ts = 0
        for _, info in (bsp_dict or {}).items():
            if not isinstance(info, dict):
                continue
            if not bool(info.get("is_buy", True)):
                continue
            try:
                ot = info.get("open_time")
                ts = int(getattr(ot, "ts", 0) or 0)
            except Exception:
                ts = 0
            if ts <= 0 or ts < best_ts:
                continue
            best = info
            best_ts = ts
        if best is None or best_ts <= 0:
            return None
        dk = day_key_from_ts(best_ts)
        if dk is None:
            return None
        feat = best.get("feature")
        feat_dict = {}
        try:
            if feat is None:
                feat_dict = {}
            elif isinstance(feat, dict):
                feat_dict = dict(feat)
            elif hasattr(feat, "items"):
                feat_dict = dict(feat.items())
            else:
                feat_dict = dict(feat)
        except Exception:
            feat_dict = {}
        try:
            entry_price = float(getattr(kl_list[-1], "close", 0.0) or 0.0)
        except Exception:
            entry_price = 0.0
        return {"code": code, "day": int(dk), "ts": int(best_ts), "feature": feat_dict, "entry_price": float(entry_price)}
    except Exception:
        return None

@app.post("/api/live/paper_trade_step")
async def live_paper_trade_step(req: LivePaperTradeStepRequest):
    import asyncio
    import os as _os

    sim_id = str(req.sim_id or "").strip() or "default"
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _fn():
        try:
            out = _live_paper_trade_step_impl(req)
            loop.call_soon_threadsafe(fut.set_result, out)
        except Exception as e:
            loop.call_soon_threadsafe(fut.set_exception, e)

    if not _enqueue_live_task_dedup("live_step_direct", sim_id, _fn):
        raise HTTPException(status_code=409, detail="live step busy")

    try:
        timeout_sec = float(_os.environ.get("MLCHAN_LIVE_STEP_TIMEOUT_SEC", "900") or 900)
    except Exception:
        timeout_sec = 900.0

    try:
        return await asyncio.wait_for(fut, timeout=timeout_sec)
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="live step timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _live_paper_trade_step_impl(req: LivePaperTradeStepRequest):
    try:
        import concurrent.futures
        import os
        import time
        from DataAPI.ClickHouseAPI import CClickHouseAPI
        from backend.live.common import cn_ts_from_day_key_minutes, day_key_from_ts, get_cached_index_risk, intraday_plunge_risk, parse_autype, parse_data_src, parse_frequency, parse_hhmm_to_minutes, pick_kl_bar_asof, pick_kl_bar_asof_with_prev

        sim_id = str(req.sim_id or "").strip() or "default"
        run_id = str(getattr(req, "run_id", "") or "").strip()
        if run_id:
            r = storage.get_live_run(run_id)
            if not r or str(r.get("status") or "").strip().lower() != "running":
                return {"status": "stopped", "cmd": "stop", "reason": "run stopped"}

        pid = req.portfolio_id
        if pid is None and getattr(req, "run_id", None):
            try:
                r = storage.get_live_run(str(req.run_id))
                if r:
                    pid = r.get("portfolio_id")
            except Exception:
                pid = None
        pool_id = str(req.pool_id or "").strip() or "index:IDX399673"
        raw_codes = CClickHouseAPI.get_pool_members(pool_id=pool_id, table="stock_constituent", limit=5000)
        codes = []
        for c in raw_codes or []:
            try:
                codes.append(normalize_code(str(c)))
            except Exception:
                continue
        codes = list(dict.fromkeys([c for c in codes if c]))
        if int(req.limit or 0) > 0:
            codes = codes[: int(req.limit)]

        freq_raw, level = parse_frequency(req.frequency)
        data_src = parse_data_src(req.data_src)
        autype = parse_autype("qfq")

        end = datetime.datetime.now().strftime("%Y-%m-%d")
        begin = (datetime.datetime.now() - datetime.timedelta(days=int(req.lookback_days or 365 * 5))).strftime("%Y-%m-%d")

        top_n = max(1, int(req.top_n or 2))
        holding_period = max(1, int(req.holding_period or 3))
        min_prob = float(req.min_prob) if req.min_prob is not None else 0.25
        trade_cost = float(req.trade_cost) if req.trade_cost is not None else 0.0

        now_ts = int(time.time())
        cur_day = None

        bundle = None
        if req.pretrained_model_key:
            bundle = load_pretrained_model_bundle_by_key(str(req.pretrained_model_key).strip())
        if bundle is None:
            bundle = load_pretrained_model_bundle(
                "logistic_regression",
                str(req.frequency or "30m"),
                str(req.data_src or "clickhouse"),
                autype=autype,
                rank_mode=str(req.rank_mode or "cross_section"),
            )
        if not bundle:
            raise HTTPException(status_code=404, detail="no pretrained model for live paper trade (need rank_mode=cross_section logistic_regression)")
        model = bundle.get("model")
        feature_meta = bundle.get("feature_meta") or []
        if not model or not feature_meta:
            raise HTTPException(status_code=404, detail="pretrained model bundle is invalid")

        def _cn_dt(ts: int) -> datetime.datetime:
            try:
                return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)
            except Exception:
                return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)

        def _cn_ts(dt_cn: datetime.datetime) -> int:
            try:
                return int((dt_cn - datetime.timedelta(hours=8)).timestamp())
            except Exception:
                return int(time.time())

        def _next_trading_day(d: datetime.date) -> datetime.date:
            nd = d + datetime.timedelta(days=1)
            while nd.weekday() >= 5:
                nd = nd + datetime.timedelta(days=1)
            return nd

        def _cn_market_session(ts: int):
            dt = _cn_dt(ts)
            d = dt.date()
            if dt.weekday() >= 5:
                nd = _next_trading_day(d)
                return {
                    "session": "closed",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(nd.year, nd.month, nd.day, 9, 25)),
                    "next_exec_day": int(nd.strftime("%Y%m%d")),
                }
            hm = dt.hour * 60 + dt.minute
            if hm < (9 * 60 + 15):
                return {
                    "session": "closed",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(d.year, d.month, d.day, 9, 25)),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (9 * 60 + 15) <= hm < (9 * 60 + 25):
                return {
                    "session": "pre_auction",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(d.year, d.month, d.day, 9, 25)),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (9 * 60 + 25) <= hm < (9 * 60 + 30):
                return {
                    "session": "auction_pause",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(d.year, d.month, d.day, 9, 30)),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (9 * 60 + 30) <= hm < (11 * 60 + 30):
                return {
                    "session": "continuous_am",
                    "market_open": True,
                    "next_exec_ts": int(ts),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (11 * 60 + 30) <= hm < (13 * 60):
                return {
                    "session": "lunch_break",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(d.year, d.month, d.day, 13, 0)),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (13 * 60) <= hm < (14 * 60 + 57):
                return {
                    "session": "continuous_pm",
                    "market_open": True,
                    "next_exec_ts": int(ts),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            if (14 * 60 + 57) <= hm <= (15 * 60):
                return {
                    "session": "close_auction",
                    "market_open": False,
                    "next_exec_ts": _cn_ts(datetime.datetime(d.year, d.month, d.day, 15, 0)),
                    "next_exec_day": int(d.strftime("%Y%m%d")),
                }
            nd = _next_trading_day(d)
            return {
                "session": "after_close",
                "market_open": False,
                "next_exec_ts": _cn_ts(datetime.datetime(nd.year, nd.month, nd.day, 9, 25)),
                "next_exec_day": int(nd.strftime("%Y%m%d")),
            }

        sess = _cn_market_session(now_ts)
        market_open = bool(sess.get("market_open"))
        market_session = str(sess.get("session") or "")
        next_exec_ts = int(sess.get("next_exec_ts") or 0)
        next_exec_day = int(sess.get("next_exec_day") or 0)

        risk = None
        risk_level = "normal"
        effective_top_n = int(top_n)
        if bool(getattr(req, "risk_filter_enabled", True)):
            raw_idx = str(getattr(req, "risk_index_code", None) or "399006").strip()
            idx_code = raw_idx
            if idx_code.endswith("399006") and (not (idx_code.startswith("sz.") or idx_code.startswith("sh."))):
                idx_code = "sz.399006"
            try:
                atr_window = int(getattr(req, "risk_atr_window", 14) or 14)
            except Exception:
                atr_window = 14
            atr_window = max(2, atr_window)
            cache_key = (
                str(idx_code),
                int(day_key_from_ts(int(now_ts)) or 0),
                str(getattr(req, "risk_mode", "both") or "both"),
                float(getattr(req, "risk_drop_threshold", -0.03) or -0.03),
                float(getattr(req, "risk_flat_drop_threshold", -0.05) or -0.05),
                int(atr_window),
                float(getattr(req, "risk_atr_pct_threshold", 0.04) or 0.04),
                float(getattr(req, "risk_flat_atr_pct_threshold", 0.06) or 0.06),
                float(getattr(req, "risk_gap_down_threshold", -0.015) or -0.015),
                int(getattr(req, "risk_trend_ma_window", 20) or 20),
                str(data_src),
                str(autype),
            )

            def _load_idx_daily():
                try:
                    days = max(90, int(atr_window) * 6)
                except Exception:
                    days = 120
                end_d = _cn_dt(int(now_ts)).date()
                begin_d = end_d - datetime.timedelta(days=int(days))
                out = fetch_stock_data(
                    idx_code,
                    KL_TYPE.K_DAY,
                    begin_d.strftime("%Y-%m-%d"),
                    end_d.strftime("%Y-%m-%d"),
                    data_src,
                    autype=autype,
                    use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                )
                if out:
                    return out
                out = fetch_stock_data(
                    idx_code,
                    KL_TYPE.K_30M,
                    begin_d.strftime("%Y-%m-%d"),
                    end_d.strftime("%Y-%m-%d"),
                    data_src,
                    autype=autype,
                    use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                )
                if out:
                    return out
                if data_src == DATA_SRC.CLICK_HOUSE:
                    return fetch_stock_data(
                        idx_code,
                        KL_TYPE.K_DAY,
                        begin_d.strftime("%Y-%m-%d"),
                        end_d.strftime("%Y-%m-%d"),
                        DATA_SRC.AK_SHARE,
                        autype=AUTYPE.NONE,
                        use_online_latest=True,
                    )
                return out

            risk = get_cached_index_risk(
                cache_key,
                _load_idx_daily,
                now_ts=int(now_ts),
                atr_window=int(atr_window),
                drop_threshold=float(getattr(req, "risk_drop_threshold", -0.03) or -0.03),
                atr_pct_threshold=float(getattr(req, "risk_atr_pct_threshold", 0.04) or 0.04),
                flat_drop_threshold=float(getattr(req, "risk_flat_drop_threshold", -0.05) or -0.05),
                flat_atr_pct_threshold=float(getattr(req, "risk_flat_atr_pct_threshold", 0.06) or 0.06),
                gap_down_threshold=float(getattr(req, "risk_gap_down_threshold", -0.015) or -0.015),
                ma_window=int(getattr(req, "risk_trend_ma_window", 20) or 20),
                mode=str(getattr(req, "risk_mode", "both") or "both"),
            )
            try:
                risk_level = str((risk or {}).get("level") or "normal").strip().lower()
            except Exception:
                risk_level = "normal"
            if risk_level == "flat":
                effective_top_n = 0
            elif risk_level == "reduce":
                effective_top_n = min(int(effective_top_n), 1)
            if isinstance(risk, dict):
                missing = []
                if (risk.get("prev_close") is None) or (float(risk.get("prev_close") or 0.0) <= 0.0):
                    missing.append("prev_close")
                if risk.get("ma20") is None:
                    missing.append("ma20")
                if missing:
                    mode_raw = str(getattr(req, "risk_data_fail_mode", "reduce") or "reduce").strip().lower()
                    risk = dict(risk)
                    risk["data_missing"] = list(missing)
                    risk["data_fail_mode"] = str(mode_raw)
                    if not risk.get("reason"):
                        risk["reason"] = "data_missing"
                    if mode_raw == "flat":
                        risk_level = "flat"
                        effective_top_n = 0
                        risk["level"] = "flat"
                    elif mode_raw == "reduce":
                        effective_top_n = min(int(effective_top_n), 1)

            intraday = None
            if bool(getattr(req, "risk_intraday_enabled", True)):
                try:
                    w = int(getattr(req, "risk_trend_ma_window", 20) or 20)
                except Exception:
                    w = 20
                begin_d = _cn_dt(int(now_ts)).date() - datetime.timedelta(days=7)
                end_d = _cn_dt(int(now_ts)).date()
                cur_px = None

                def _pick_intraday_close(kl):
                    if not kl:
                        return None
                    picked = None
                    for k in kl:
                        try:
                            ts = int(getattr(getattr(k, "time", None), "ts", 0) or 0)
                        except Exception:
                            ts = 0
                        if ts <= 0 or ts > int(now_ts):
                            continue
                        try:
                            c = float(getattr(k, "close", 0.0) or 0.0)
                        except Exception:
                            c = 0.0
                        if c <= 0.0:
                            continue
                        picked = float(c)
                    return picked

                try:
                    kl = fetch_stock_data(
                        idx_code,
                        KL_TYPE.K_30M,
                        begin_d.strftime("%Y-%m-%d"),
                        end_d.strftime("%Y-%m-%d"),
                        data_src,
                        autype=autype,
                        use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                    )
                    cur_px = _pick_intraday_close(kl)
                except Exception:
                    cur_px = None
                if cur_px is None and data_src == DATA_SRC.CLICK_HOUSE:
                    try:
                        kl = fetch_stock_data(
                            idx_code,
                            KL_TYPE.K_30M,
                            begin_d.strftime("%Y-%m-%d"),
                            end_d.strftime("%Y-%m-%d"),
                            DATA_SRC.AK_SHARE,
                            autype=AUTYPE.NONE,
                            use_online_latest=True,
                        )
                        cur_px = _pick_intraday_close(kl)
                    except Exception:
                        cur_px = None

                intraday = intraday_plunge_risk(
                    current_price=cur_px,
                    prev_close=(risk or {}).get("prev_close") if isinstance(risk, dict) else None,
                    ma_value=(risk or {}).get("ma20") if isinstance(risk, dict) else None,
                    bear_threshold=float(getattr(req, "risk_intraday_bear_threshold", -0.015) or -0.015),
                    bull_threshold=float(getattr(req, "risk_intraday_bull_threshold", -0.03) or -0.03),
                )
                if isinstance(risk, dict):
                    risk = dict(risk)
                    risk["intraday"] = dict(intraday) if isinstance(intraday, dict) else None
                    risk["trend_ma_window"] = int(w)

                intraday_missing = []
                if isinstance(intraday, dict):
                    if intraday.get("current_price") is None:
                        intraday_missing.append("current_price")
                    if intraday.get("prev_close") is None:
                        intraday_missing.append("prev_close")
                    if intraday.get("ma") is None:
                        intraday_missing.append("ma20")
                    if intraday.get("change") is None:
                        intraday_missing.append("intraday_change")
                else:
                    intraday_missing = ["intraday_calc_failed"]

                if intraday_missing:
                    mode_raw = str(getattr(req, "risk_intraday_data_fail_mode", "reduce") or "reduce").strip().lower()
                    if isinstance(risk, dict):
                        risk["intraday_missing"] = list(intraday_missing)
                        risk["intraday_data_fail_mode"] = str(mode_raw)
                        if not risk.get("reason"):
                            risk["reason"] = "intraday_data_missing"
                    if mode_raw == "flat":
                        risk_level = "flat"
                        effective_top_n = 0
                        if isinstance(risk, dict):
                            risk["level"] = "flat"
                    elif mode_raw == "reduce":
                        effective_top_n = min(int(effective_top_n), 1)

                if isinstance(intraday, dict) and bool(intraday.get("flat")):
                    risk_level = "flat"
                    effective_top_n = 0
                    if isinstance(risk, dict):
                        risk["level"] = "flat"
                        risk["reason"] = "intraday_plunge"
        try:
            cur_day = int(_cn_dt(now_ts).strftime("%Y%m%d"))
        except Exception:
            cur_day = day_key_from_ts(now_ts)
        if cur_day is None:
            cur_day = int(next_exec_day or 0)

        pending_orders = storage.list_live_orders(status=None, limit=1000, sim_id=sim_id, portfolio_id=pid)
        can_execute_session = bool(market_open)
        executed_pending = False
        if can_execute_session and pending_orders:
            quote_cache = {}

            def _get_quote(code: str, asof_ts: Optional[int] = None):
                code = str(code or "").strip()
                if not code:
                    return None
                try:
                    ts_key = int(asof_ts or now_ts)
                except Exception:
                    ts_key = int(now_ts)
                cache_key = (code, int(ts_key // 60))
                if cache_key in quote_cache:
                    return quote_cache.get(cache_key)
                out = None
                try:
                    kl_list = fetch_stock_data(
                        code,
                        level,
                        begin,
                        end,
                        data_src,
                        autype=autype,
                        use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                    )
                    if kl_list:
                        k = pick_kl_bar_asof(kl_list, int(ts_key))
                        o = float(getattr(k, "open", 0.0) or 0.0)
                        c = float(getattr(k, "close", 0.0) or 0.0)
                        out = {"open": o, "close": c}
                except Exception:
                    out = None
                quote_cache[cache_key] = out
                return out

            def _pick_px(code: str, session_name: str, asof_ts: Optional[int] = None):
                q = _get_quote(code, asof_ts=asof_ts)
                if not q:
                    return 0.0
                o = float(q.get("open") or 0.0)
                c = float(q.get("close") or 0.0)
                if session_name == "auction_pause":
                    if o > 0:
                        return float(o)
                    return float(c or 0.0)
                if session_name == "close_auction":
                    if c > 0:
                        return float(c)
                    return float(o or 0.0)
                if c > 0:
                    return float(c)
                return float(o or 0.0)

            executable = []
            for o in pending_orders:
                st = str(o.get("status") or "").strip().lower()
                if st not in ["pending", "blocked"]:
                    continue
                try:
                    o_execute_after = int(o.get("execute_after_ts") or 0)
                except Exception:
                    o_execute_after = 0
                try:
                    o_min_day = int(o.get("min_day") or 0)
                except Exception:
                    o_min_day = 0
                if o_execute_after and int(now_ts) < int(o_execute_after):
                    continue
                if o_min_day and int(cur_day or 0) < int(o_min_day):
                    continue
                exec_sess = None
                if o_execute_after and int(o_execute_after) > 0:
                    try:
                        exec_sess = str((_cn_market_session(int(o_execute_after)) or {}).get("session") or "").strip()
                    except Exception:
                        exec_sess = None
                executable.append({**o, "_exec_session": exec_sess})

            if executable:
                sell_codes = []
                buy_items = []
                executed_ids = []
                for o in executable:
                    executed_ids.append(int(o.get("id") or 0))
                    code = str(o.get("stock_code") or "").strip()
                    side = str(o.get("side") or "").strip().lower()
                    if not code or side not in ["buy", "sell"]:
                        continue
                    px_session = str(o.get("_exec_session") or "").strip() or market_session
                    try:
                        px_asof = int(o.get("execute_after_ts") or 0)
                    except Exception:
                        px_asof = 0
                    if px_asof <= 0:
                        px_asof = int(now_ts)
                    if side == "sell":
                        sell_codes.append((code, px_session, int(px_asof)))
                    else:
                        buy_items.append({"code": code, "px_session": px_session, "px_asof": int(px_asof)})

                if market_open:
                    storage.increment_bars_held(1, sim_id=sim_id, portfolio_id=pid)
                sell_px = {}
                sell_only = []
                for c, sess_name, px_asof in sell_codes:
                    sell_only.append(c)
                    sell_px[c] = float(_pick_px(c, sess_name, asof_ts=int(px_asof)))
                if sell_only:
                    storage.delete_positions(sell_only, sim_id=sim_id, portfolio_id=pid)
                for bi in buy_items:
                    if bi.get("code"):
                        buy_px = float(_pick_px(str(bi["code"]), str(bi.get("px_session") or market_session), asof_ts=int(bi.get("px_asof") or now_ts)))
                        storage.upsert_position(str(bi["code"]), int(now_ts), float(buy_px), 0, sim_id=sim_id, portfolio_id=pid)

                trades = []
                for c, sess_name, _px_asof in sell_codes:
                    trades.append({"stock_code": c, "side": "sell", "status": "executed", "trade_ts": int(now_ts), "price": float(sell_px.get(c) or 0.0), "meta": {"source": "order", "session": sess_name or market_session}})
                for bi in buy_items:
                    code = str(bi.get("code") or "")
                    sess_name = str(bi.get("px_session") or "").strip() or market_session
                    trades.append({"stock_code": code, "side": "buy", "status": "executed", "trade_ts": int(now_ts), "price": float(_pick_px(code, sess_name, asof_ts=int(bi.get("px_asof") or now_ts))), "meta": {"source": "order", "session": sess_name}})
                storage.add_live_trades(trades, sim_id=sim_id, portfolio_id=pid)
                storage.mark_orders_executed([x for x in executed_ids if x], executed_ts=int(now_ts), sim_id=sim_id, portfolio_id=pid)
                executed_pending = True

        positions = storage.list_positions(sim_id=sim_id, portfolio_id=pid)
        pos_map = {p.get("stock_code"): p for p in (positions or []) if p.get("stock_code")}
        to_force_sell = [p["stock_code"] for p in (positions or []) if int(p.get("bars_held") or 0) >= int(holding_period)]

        results = []
        try:
            req_mw = int(req.max_workers or 0)
        except Exception:
            req_mw = 0
        try:
            env_mw_raw = os.environ.get("MLCHAN_LIVE_MAX_WORKERS", "").strip()
            env_mw = int(env_mw_raw) if env_mw_raw else 0
        except Exception:
            env_mw = 0
        if env_mw and env_mw > 0:
            base_mw = req_mw if req_mw and req_mw > 0 else env_mw
            mw = min(int(base_mw), int(env_mw))
        else:
            mw = req_mw if req_mw and req_mw > 0 else 8
        mw = max(1, min(int(mw), int(len(codes) if codes else 1)))

        if int(mw) <= 1 or not codes:
            for code in codes:
                try:
                    r = _scan_feature_worker(
                        code,
                        level,
                        begin,
                        end,
                        data_src,
                        autype,
                        bool(req.enable_rolling_lookback),
                        str(req.rolling_sample_mode or "every_bar"),
                        int(req.rolling_stride or 1),
                    )
                except Exception:
                    r = None
                if r:
                    results.append(r)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=int(mw)) as ex:
                futs = {
                    ex.submit(
                        _scan_feature_worker,
                        code,
                        level,
                        begin,
                        end,
                        data_src,
                        autype,
                        bool(req.enable_rolling_lookback),
                        str(req.rolling_sample_mode or "every_bar"),
                        int(req.rolling_stride or 1)
                    ): code
                    for code in codes
                }
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        r = fut.result()
                    except Exception:
                        r = None
                    if r:
                        results.append(r)

        try:
            cross_section_rank_transform_rows(results, feature_meta, missing_value=-9999999.0)
        except TypeError:
            cross_section_rank_transform_rows(results)
        X = []
        for r in results:
            feat = r.get("feature") if isinstance(r.get("feature"), dict) else {}
            row = []
            for k in feature_meta:
                v = feat.get(k, -9999999.0)
                try:
                    fv = float(v)
                except Exception:
                    fv = -9999999.0
                try:
                    import numpy as _np
                    if not _np.isfinite(fv):
                        fv = -9999999.0
                except Exception:
                    pass
                row.append(float(fv))
            X.append(row)
        probs = []
        try:
            probs = model.predict_proba(X)
        except Exception:
            probs = []
        for i, r in enumerate(results):
            p = 0.0
            try:
                if probs is not None and i < len(probs) and len(probs[i]) >= 2:
                    p = float(probs[i][1])
            except Exception:
                p = 0.0
            r["prob"] = float(p)

        results.sort(key=lambda x: (int(x.get("day") or 0), float(x.get("prob") or 0.0)), reverse=True)
        signal_day = int(cur_day or 0) if cur_day is not None else None
        todays = []
        if signal_day is not None:
            todays = [r for r in results if int(r.get("day") or 0) == int(signal_day)]
        if (not todays) and (bool(req.use_latest_available_day) or (not market_open)) and results:
            try:
                signal_day = int(max(int(r.get("day") or 0) for r in results))
            except Exception:
                signal_day = signal_day
            if signal_day is not None:
                todays = [r for r in results if int(r.get("day") or 0) == int(signal_day)]

        todays = [r for r in todays if float(r.get("prob") or 0.0) >= float(min_prob)]
        has_targets = bool(todays)
        todays.sort(key=lambda x: float(x.get("prob") or 0.0), reverse=True)
        targets = todays[: int(effective_top_n)] if has_targets and int(effective_top_n) > 0 else []
        target_codes = [t["code"] for t in targets] if targets else list(pos_map.keys())

        current_codes = list(pos_map.keys())
        rebalance_out = [c for c in current_codes if c and c not in target_codes] if targets else []
        if risk_level == "flat":
            rebalance_out = list(current_codes)
            target_codes = []
        sell_codes = list(dict.fromkeys([c for c in (to_force_sell + rebalance_out) if c]))
        buy_codes = [c for c in target_codes if c not in current_codes] if targets else []

        def _pos_entry_day(p: dict) -> int:
            try:
                ed = int(p.get("entry_day") or 0)
            except Exception:
                ed = 0
            if ed > 0:
                return ed
            try:
                return int(day_key_from_ts(int(p.get("entry_time") or 0)) or 0)
            except Exception:
                return 0

        sell_mode = str(getattr(req, "sell_mode", None) or "baseline").strip().lower() or "baseline"

        market = str(getattr(req, "market", None) or "cn_a").strip().lower()
        enforce_t1 = market in ["cn", "cn_a", "a", "a_share", "ashare"]
        blocked = []
        if enforce_t1 and sell_codes:
            t1_next_day = _next_trading_day(_cn_dt(now_ts).date())
            t1_min_day = int(t1_next_day.strftime("%Y%m%d"))
            t1_exec_ts = _cn_ts(datetime.datetime(t1_next_day.year, t1_next_day.month, t1_next_day.day, 9, 25))
            if sell_mode in ("delayed", "delay", "scheme1", "t1_delay"):
                decision_min = int(parse_hhmm_to_minutes(getattr(req, "t1_decision_time", None), default_min=10 * 60))
                decision_hh = int(decision_min // 60)
                decision_mm = int(decision_min % 60)
                t1_exec_ts = _cn_ts(datetime.datetime(t1_next_day.year, t1_next_day.month, t1_next_day.day, int(decision_hh), int(decision_mm)))
            sell_ok = []
            for c in sell_codes:
                p = pos_map.get(c)
                if not p:
                    sell_ok.append(c)
                    continue
                ed = _pos_entry_day(p)
                if ed and int(cur_day or 0) and int(ed) >= int(cur_day):
                    blocked.append({"stock_code": str(c), "side": "sell", "price": float(p.get("entry_price") or 0.0), "min_day": int(t1_min_day or 0), "execute_after_ts": int(t1_exec_ts or 0), "reason": "T+1"})
                    continue
                sell_ok.append(c)
            sell_codes = sell_ok

        storage.replace_blocked_live_orders(blocked, requested_ts=int(now_ts), sim_id=sim_id, portfolio_id=pid)
        if sell_mode in ("t1_gap_roll", "gap_roll", "advanced"):
            try:
                t1_gap_strong_th = float(getattr(req, "t1_gap_strong_threshold", 0.03) or 0.03)
            except Exception:
                t1_gap_strong_th = 0.03
            try:
                t1_gap_weak_th = float(getattr(req, "t1_gap_weak_threshold", -0.02) or -0.02)
            except Exception:
                t1_gap_weak_th = -0.02
            try:
                t1_gap_very_strong_th = float(getattr(req, "t1_gap_very_strong_threshold", 0.05) or 0.05)
            except Exception:
                t1_gap_very_strong_th = 0.05

            decision_min = int(parse_hhmm_to_minutes(getattr(req, "t1_decision_time", None), default_min=10 * 60))

            close_guard_min = 14 * 60 + 57

            def _prev_trading_day(d: datetime.date) -> datetime.date:
                pd = d - datetime.timedelta(days=1)
                while pd.weekday() >= 5:
                    pd = pd - datetime.timedelta(days=1)
                return pd

            def _t1_open_pct(code: str) -> Optional[float]:
                code = str(code or "").strip()
                if not code:
                    return None
                try:
                    kl_list = fetch_stock_data(
                        code,
                        level,
                        begin,
                        end,
                        data_src,
                        autype=autype,
                        use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                    )
                except Exception:
                    kl_list = None
                if not kl_list or len(kl_list) < 1:
                    return None
                k = kl_list[-1]
                prev = kl_list[-2] if len(kl_list) > 1 else None
                try:
                    o = float(getattr(k, "open", 0.0) or 0.0)
                except Exception:
                    o = 0.0
                try:
                    pc = float(getattr(prev, "close", 0.0) or 0.0) if prev is not None else 0.0
                except Exception:
                    pc = 0.0
                if o > 0.0 and pc > 0.0:
                    return float(o / pc - 1.0)
                return None

            dt_cn = _cn_dt(now_ts)
            now_min = int(dt_cn.hour) * 60 + int(dt_cn.minute)
            prev_day_key = int(_prev_trading_day(dt_cn.date()).strftime("%Y%m%d"))
            rebalance_set = set(rebalance_out or [])
            forced_set = set(to_force_sell or [])
            filtered_sell = []
            for c in sell_codes:
                if c in forced_set:
                    filtered_sell.append(c)
                    continue
                if c not in rebalance_set:
                    filtered_sell.append(c)
                    continue
                p = pos_map.get(c)
                if not p:
                    filtered_sell.append(c)
                    continue
                ed = _pos_entry_day(p)
                if int(ed or 0) != int(prev_day_key or 0):
                    filtered_sell.append(c)
                    continue
                op = _t1_open_pct(c)
                if now_min < int(decision_min):
                    if op is not None and float(op) < float(t1_gap_weak_th):
                        filtered_sell.append(c)
                    else:
                        continue
                else:
                    if op is not None and float(op) > float(t1_gap_very_strong_th) and now_min < int(close_guard_min):
                        continue
                    filtered_sell.append(c)
            sell_codes = filtered_sell

        if market_open and sell_mode in ("delayed", "delay", "scheme1", "t1_delay"):
            raw_decision = str(getattr(req, "t1_decision_time", None) or "10:00").strip()
            decision_min = 10 * 60
            try:
                parts = raw_decision.split(":")
                hh = int(parts[0]) if parts and parts[0].strip() else 10
                mm = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
                hh = max(0, min(23, hh))
                mm = max(0, min(59, mm))
                decision_min = hh * 60 + mm
            except Exception:
                decision_min = 10 * 60

            dt_cn = _cn_dt(now_ts)
            now_min = int(dt_cn.hour) * 60 + int(dt_cn.minute)
            if now_min < int(decision_min):
                delayed_pos = storage.list_positions(sim_id=sim_id, portfolio_id=pid)
                for p in delayed_pos or []:
                    if not p.get("stock_name"):
                        p["stock_name"] = str(get_stock_name(p.get("stock_code")) or "")
                resp = {
                    "ts": int(now_ts),
                    "day": int(cur_day or 0),
                    "pool_id": pool_id,
                    "frequency": freq_raw,
                    "model_key": str(bundle.get("key") or ""),
                    "rank_mode": str((bundle.get("meta") or {}).get("rank_mode") or ""),
                    "max_workers": int(mw),
                    "sell_mode": str(sell_mode),
                    "market_open": True,
                    "market_session": market_session,
                    "next_exec_ts": int(next_exec_ts or 0),
                    "executed_pending": bool(executed_pending),
                    "risk": {
                        "enabled": bool(getattr(req, "risk_filter_enabled", True)),
                        "level": str(risk_level),
                        "effective_top_n": int(effective_top_n),
                        "index_code": str(getattr(req, "risk_index_code", "399006") or "399006"),
                        "ret1d": (risk or {}).get("ret1d") if isinstance(risk, dict) else None,
                        "atr_pct": (risk or {}).get("atr_pct") if isinstance(risk, dict) else None,
                        "gap_open_pct": (risk or {}).get("gap_open_pct") if isinstance(risk, dict) else None,
                        "today_open": (risk or {}).get("today_open") if isinstance(risk, dict) else None,
                        "prev_close": (risk or {}).get("prev_close") if isinstance(risk, dict) else None,
                        "ma20": (risk or {}).get("ma20") if isinstance(risk, dict) else None,
                        "trend_ma_window": int(getattr(req, "risk_trend_ma_window", 20) or 20),
                        "intraday_enabled": bool(getattr(req, "risk_intraday_enabled", True)),
                        "intraday_bear_threshold": float(getattr(req, "risk_intraday_bear_threshold", -0.015) or -0.015),
                        "intraday_bull_threshold": float(getattr(req, "risk_intraday_bull_threshold", -0.03) or -0.03),
                        "intraday": (risk or {}).get("intraday") if isinstance(risk, dict) else None,
                        "reason": (risk or {}).get("reason") if isinstance(risk, dict) else None,
                        "data_fail_mode": str(getattr(req, "risk_data_fail_mode", "reduce") or "reduce"),
                        "data_missing": (risk or {}).get("data_missing") if isinstance(risk, dict) else None,
                        "intraday_data_fail_mode": str(getattr(req, "risk_intraday_data_fail_mode", "reduce") or "reduce"),
                        "intraday_missing": (risk or {}).get("intraday_missing") if isinstance(risk, dict) else None,
                        "gap_down_threshold": float(getattr(req, "risk_gap_down_threshold", -0.015) or -0.015),
                        "mode": str(getattr(req, "risk_mode", "both") or "both"),
                    },
                    "targets": targets,
                    "sell": [],
                    "buy": [],
                    "pending_orders": storage.list_live_orders(status=None, limit=1000, sim_id=sim_id, portfolio_id=pid),
                    "trades": storage.list_live_trades(limit=200, sim_id=sim_id, portfolio_id=pid),
                    "positions": delayed_pos,
                }
                try:
                    storage.add_live_step_record(
                        sim_id=sim_id,
                        run_id=str(getattr(req, "run_id", None) or ""),
                        step_ts=int(resp.get("ts") or 0),
                        day=int(resp.get("day") or 0),
                        portfolio_id=pid,
                        risk=resp.get("risk") if isinstance(resp.get("risk"), dict) else {},
                        targets=targets,
                        sell=[],
                        buy=[],
                        blocked=blocked,
                        summary={},
                        result=resp,
                        error=None,
                    )
                except Exception:
                    pass
                return resp

        if not market_open:
            pending = []
            for t in targets:
                if t.get("code") in buy_codes:
                    pending.append({"stock_code": str(t.get("code")), "side": "buy", "price": float(t.get("entry_price") or 0.0), "execute_after_ts": int(next_exec_ts or 0), "min_day": int(next_exec_day or 0), "reason": market_session})
            for c in sell_codes:
                pending.append({"stock_code": str(c), "side": "sell", "price": float((pos_map.get(c) or {}).get("entry_price") or 0.0), "execute_after_ts": int(next_exec_ts or 0), "min_day": int(next_exec_day or 0), "reason": market_session})
            storage.replace_pending_live_orders(pending, requested_ts=int(now_ts), sim_id=sim_id, portfolio_id=pid)
            resp = {
                "ts": int(now_ts),
                "day": int(cur_day or 0),
                "pool_id": pool_id,
                "frequency": freq_raw,
                "model_key": str(bundle.get("key") or ""),
                "rank_mode": str((bundle.get("meta") or {}).get("rank_mode") or ""),
                "max_workers": int(mw),
                "sell_mode": str(sell_mode),
                "market_open": False,
                "market_session": market_session,
                "next_exec_ts": int(next_exec_ts or 0),
                "executed_pending": bool(executed_pending),
                "risk": {
                    "enabled": bool(getattr(req, "risk_filter_enabled", True)),
                    "level": str(risk_level),
                    "effective_top_n": int(effective_top_n),
                    "index_code": str(getattr(req, "risk_index_code", "399006") or "399006"),
                    "ret1d": (risk or {}).get("ret1d") if isinstance(risk, dict) else None,
                    "atr_pct": (risk or {}).get("atr_pct") if isinstance(risk, dict) else None,
                    "gap_open_pct": (risk or {}).get("gap_open_pct") if isinstance(risk, dict) else None,
                    "today_open": (risk or {}).get("today_open") if isinstance(risk, dict) else None,
                    "prev_close": (risk or {}).get("prev_close") if isinstance(risk, dict) else None,
                    "ma20": (risk or {}).get("ma20") if isinstance(risk, dict) else None,
                    "trend_ma_window": int(getattr(req, "risk_trend_ma_window", 20) or 20),
                    "intraday_enabled": bool(getattr(req, "risk_intraday_enabled", True)),
                    "intraday_bear_threshold": float(getattr(req, "risk_intraday_bear_threshold", -0.015) or -0.015),
                    "intraday_bull_threshold": float(getattr(req, "risk_intraday_bull_threshold", -0.03) or -0.03),
                    "intraday": (risk or {}).get("intraday") if isinstance(risk, dict) else None,
                    "reason": (risk or {}).get("reason") if isinstance(risk, dict) else None,
                    "data_fail_mode": str(getattr(req, "risk_data_fail_mode", "reduce") or "reduce"),
                    "data_missing": (risk or {}).get("data_missing") if isinstance(risk, dict) else None,
                    "intraday_data_fail_mode": str(getattr(req, "risk_intraday_data_fail_mode", "reduce") or "reduce"),
                    "intraday_missing": (risk or {}).get("intraday_missing") if isinstance(risk, dict) else None,
                    "gap_down_threshold": float(getattr(req, "risk_gap_down_threshold", -0.015) or -0.015),
                    "mode": str(getattr(req, "risk_mode", "both") or "both"),
                },
                "targets": targets,
                "sell": sell_codes,
                "buy": buy_codes,
                "pending_orders": storage.list_live_orders(status=None, limit=1000, sim_id=sim_id, portfolio_id=pid),
                "trades": storage.list_live_trades(limit=200, sim_id=sim_id, portfolio_id=pid),
                "positions": storage.list_positions(sim_id=sim_id, portfolio_id=pid),
            }
            try:
                storage.add_live_step_record(
                    sim_id=sim_id,
                    run_id=str(getattr(req, "run_id", None) or ""),
                    step_ts=int(resp.get("ts") or 0),
                    day=int(resp.get("day") or 0),
                    portfolio_id=pid,
                    risk=resp.get("risk") if isinstance(resp.get("risk"), dict) else {},
                    targets=targets,
                    sell=sell_codes,
                    buy=buy_codes,
                    blocked=blocked,
                    summary={},
                    result=resp,
                    error=None,
                )
            except Exception:
                pass
            return resp

        quote_cache = {}

        def _get_quote(code: str, asof_ts: Optional[int] = None):
            code = str(code or "").strip()
            if not code:
                return None
            try:
                ts_key = int(asof_ts or now_ts)
            except Exception:
                ts_key = int(now_ts)
            cache_key = (code, int(ts_key // 60))
            if cache_key in quote_cache:
                return quote_cache.get(cache_key)
            out = None
            try:
                kl_list = fetch_stock_data(
                    code,
                    level,
                    begin,
                    end,
                    data_src,
                    autype=autype,
                    use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
                )
                if kl_list:
                    k, prev = pick_kl_bar_asof_with_prev(kl_list, int(ts_key))
                    o = float(getattr(k, "open", 0.0) or 0.0)
                    c = float(getattr(k, "close", 0.0) or 0.0)
                    h = float(getattr(k, "high", 0.0) or 0.0)
                    l = float(getattr(k, "low", 0.0) or 0.0)
                    pc = float(getattr(prev, "close", 0.0) or o) if prev else o
                    out = {"open": o, "close": c, "high": h, "low": l, "prev_close": pc}
            except Exception:
                out = None
            quote_cache[cache_key] = out
            return out

        def _pick_px(code: str, session_name: str, asof_ts: Optional[int] = None):
            q = _get_quote(code, asof_ts=asof_ts)
            if not q:
                return 0.0
            o = float(q.get("open") or 0.0)
            c = float(q.get("close") or 0.0)
            if session_name == "auction_pause":
                if o > 0:
                    return float(o)
                return float(c or 0.0)
            if session_name == "close_auction":
                if c > 0:
                    return float(c)
                return float(o or 0.0)
            if c > 0:
                return float(c)
            return float(o or 0.0)

        def _get_board_rules(c: str):
            c = str(c or "")
            if c.startswith("688"): # STAR Market
                return {"min_qty": 200, "inc_qty": 1, "limit_rate": 0.195}
            if c.startswith("8") or c.startswith("43") or c.startswith("92") or c.startswith("83"): # BSE (Approx codes)
                 return {"min_qty": 100, "inc_qty": 1, "limit_rate": 0.295}
            if c.startswith("300"): # ChiNext
                 return {"min_qty": 100, "inc_qty": 100, "limit_rate": 0.195}
            # Main Board
            return {"min_qty": 100, "inc_qty": 100, "limit_rate": 0.095}

        # Execution with Cash & Rules
        sim_info = storage.get_live_sim(sim_id)
        current_balance = float(sim_info.get("balance", 100000.0)) if sim_info else 100000.0
        trades = []

        px_now_ts = int(now_ts)
        if market_open and sell_mode in ("delayed", "delay", "scheme1", "t1_delay"):
            raw_decision = str(getattr(req, "t1_decision_time", None) or "10:00").strip()
            decision_hh = 10
            decision_mm = 0
            try:
                parts = raw_decision.split(":")
                decision_hh = int(parts[0]) if parts and parts[0].strip() else 10
                decision_mm = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
                decision_hh = max(0, min(23, decision_hh))
                decision_mm = max(0, min(59, decision_mm))
            except Exception:
                decision_hh = 10
                decision_mm = 0
            dt_cn = _cn_dt(int(now_ts))
            try:
                decision_ts_today = int(
                    (
                        datetime.datetime(dt_cn.year, dt_cn.month, dt_cn.day, int(decision_hh), int(decision_mm), 0, tzinfo=datetime.timezone.utc)
                        - datetime.timedelta(hours=8)
                    ).timestamp()
                )
            except Exception:
                decision_ts_today = 0
            if decision_ts_today and int(decision_ts_today) <= int(now_ts):
                px_now_ts = int(decision_ts_today)
        
        # 1. Process Sells
        sold_codes = []
        for c in sell_codes:
            p = pos_map.get(c)
            if not p: continue
            px = float(_pick_px(c, market_session, asof_ts=int(px_now_ts)))
            if px <= 0.01: continue # Suspension or error
            
            # Check Limit Down
            rules = _get_board_rules(c)
            q = _get_quote(c, asof_ts=int(px_now_ts))
            if q:
                pc = float(q.get("prev_close") or px)
                if pc > 0 and px <= pc * (1.0 - rules["limit_rate"]) and abs(px - float(q.get("low") or 0.0)) < 0.01:
                    blocked.append({"stock_code": c, "side": "sell", "price": px, "reason": "Limit Down", "min_day": 0, "execute_after_ts": 0})
                    continue

            # Simple assumption: If Volume=0 (legacy), assume 100.
            # Sell logic: Sell ALL available volume? Usually yes.
            # But we must respect lot size? 
            # Rule: selling has no lot size limit for "Odd Lots" (不足100股应一次性卖出).
            # So just sell all.
            vol = int(p.get("volume", 0) or 100)
            revenue = px * vol * (1.0 - 0.001) # 0.1% Tax+Fee
            
            current_balance += revenue
            sold_codes.append(c)
            trades.append({"stock_code": c, "side": "sell", "status": "executed", "trade_ts": int(now_ts), "price": px, "volume": vol, "meta": {"source": "step", "session": market_session}})

        if sold_codes:
            storage.delete_positions(sold_codes, sim_id=sim_id, portfolio_id=pid)

        # 2. Process Buys
        real_buy_codes = []
        if buy_codes:
            alloc_per_stock = current_balance / float(len(buy_codes))
            for t in targets:
                code = str(t.get("code") or "")
                if code not in buy_codes: continue
                
                px = float(_pick_px(code, market_session, asof_ts=int(px_now_ts)))
                if px <= 0.01: continue
                
                rules = _get_board_rules(code)
                
                # Check Limit Up
                q = _get_quote(code)
                if q:
                    pc = float(q.get("prev_close") or px)
                    if pc > 0 and px >= pc * (1.0 + rules["limit_rate"]) and abs(px - float(q.get("high") or 0.0)) < 0.01:
                        blocked.append({"stock_code": code, "side": "buy", "price": px, "reason": "Limit Up", "min_day": 0, "execute_after_ts": 0})
                        continue

                # Check Cash & Lot Size
                # Cost factor: 1 + 0.0002 (Fee)
                cost_factor = 1.0002
                try:
                    max_total_vol = int(alloc_per_stock / (px * cost_factor))
                except:
                    max_total_vol = 0
                
                if max_total_vol < rules["min_qty"]:
                    blocked.append({"stock_code": code, "side": "buy", "price": px, "reason": "Insufficient Cash", "min_day": 0, "execute_after_ts": 0})
                    continue

                # Quantize volume
                remainder = max_total_vol - rules["min_qty"]
                mid_units = remainder // rules["inc_qty"]
                vol = rules["min_qty"] + mid_units * rules["inc_qty"]
                
                cost = px * vol * cost_factor
                current_balance -= cost
                
                s_name = str(get_stock_name(code) or "")
                storage.upsert_position(code, int(t.get("ts") or now_ts), px, 0, sim_id=sim_id, portfolio_id=pid, volume=vol, stock_name=s_name)
                real_buy_codes.append(code)
                trades.append({"stock_code": code, "side": "buy", "status": "executed", "trade_ts": int(now_ts), "price": px, "volume": vol, "meta": {"source": "step", "prob": float(t.get("prob") or 0.0), "session": market_session}})

        
        buy_codes = real_buy_codes # Update for reporting
        storage.update_live_sim_balance(sim_id, current_balance)
        storage.add_live_trades(trades, sim_id=sim_id, portfolio_id=pid)

        after_pos = storage.list_positions(sim_id=sim_id, portfolio_id=pid)
        for p in after_pos:
            if not p.get("stock_name"):
                p["stock_name"] = str(get_stock_name(p.get("stock_code")) or "")
        resp = {
            "ts": int(now_ts),
            "day": int(cur_day or 0),
            "pool_id": pool_id,
            "frequency": freq_raw,
            "model_key": str(bundle.get("key") or ""),
            "rank_mode": str((bundle.get("meta") or {}).get("rank_mode") or ""),
            "max_workers": int(mw),
            "sell_mode": str(sell_mode),
            "market_open": True,
            "market_session": market_session,
            "next_exec_ts": int(next_exec_ts or 0),
            "executed_pending": bool(executed_pending),
            "risk": {
                "enabled": bool(getattr(req, "risk_filter_enabled", True)),
                "level": str(risk_level),
                "effective_top_n": int(effective_top_n),
                "index_code": str(getattr(req, "risk_index_code", "399006") or "399006"),
                "ret1d": (risk or {}).get("ret1d") if isinstance(risk, dict) else None,
                "atr_pct": (risk or {}).get("atr_pct") if isinstance(risk, dict) else None,
                "gap_open_pct": (risk or {}).get("gap_open_pct") if isinstance(risk, dict) else None,
                "today_open": (risk or {}).get("today_open") if isinstance(risk, dict) else None,
                "prev_close": (risk or {}).get("prev_close") if isinstance(risk, dict) else None,
                "ma20": (risk or {}).get("ma20") if isinstance(risk, dict) else None,
                "trend_ma_window": int(getattr(req, "risk_trend_ma_window", 20) or 20),
                "intraday_enabled": bool(getattr(req, "risk_intraday_enabled", True)),
                "intraday_bear_threshold": float(getattr(req, "risk_intraday_bear_threshold", -0.015) or -0.015),
                "intraday_bull_threshold": float(getattr(req, "risk_intraday_bull_threshold", -0.03) or -0.03),
                "intraday": (risk or {}).get("intraday") if isinstance(risk, dict) else None,
                "reason": (risk or {}).get("reason") if isinstance(risk, dict) else None,
                "data_fail_mode": str(getattr(req, "risk_data_fail_mode", "reduce") or "reduce"),
                "data_missing": (risk or {}).get("data_missing") if isinstance(risk, dict) else None,
                "intraday_data_fail_mode": str(getattr(req, "risk_intraday_data_fail_mode", "reduce") or "reduce"),
                "intraday_missing": (risk or {}).get("intraday_missing") if isinstance(risk, dict) else None,
                "gap_down_threshold": float(getattr(req, "risk_gap_down_threshold", -0.015) or -0.015),
                "mode": str(getattr(req, "risk_mode", "both") or "both"),
            },
            "targets": targets,
            "sell": sell_codes,
            "buy": buy_codes,
            "pending_orders": storage.list_live_orders(status=None, limit=1000, sim_id=sim_id, portfolio_id=pid),
            "trades": storage.list_live_trades(limit=200, sim_id=sim_id, portfolio_id=pid),
            "positions": after_pos,
        }
        try:
            storage.add_live_step_record(
                sim_id=sim_id,
                run_id=str(getattr(req, "run_id", None) or ""),
                step_ts=int(resp.get("ts") or 0),
                day=int(resp.get("day") or 0),
                portfolio_id=pid,
                risk=resp.get("risk") if isinstance(resp.get("risk"), dict) else {},
                targets=targets,
                sell=sell_codes,
                buy=buy_codes,
                blocked=blocked,
                summary={},
                result=resp,
                error=None,
            )
        except Exception:
            pass
        return resp
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/strategy/run")
async def run_strategy(req: StrategyRequest):
    try:
        params = req.dict()
        session = storage.Session()
        run = StrategyRun(
            strategy_name=req.strategy_name,
            params_json=json.dumps(params),
            status="queued",
            progress=0
        )
        session.add(run)
        session.commit()
        run_id = run.id
        session.close()

        _enqueue_task("strategy_run", str(run_id), lambda: strategy_runner.run_strategy_sync(run_id, params))
        
        return {"status": "success", "run_id": run_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/strategy/runs")
async def list_strategy_runs(page: int = 1, page_size: int = 20, limit: Optional[int] = None):
    if limit is not None:
        session = storage.Session()
        try:
            runs = session.query(StrategyRun).order_by(StrategyRun.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "strategy_name": r.strategy_name,
                    "status": r.status,
                    "progress": r.progress,
                    "created_at": _dt_to_utc_iso(r.created_at),
                    "completed_at": _dt_to_utc_iso(r.completed_at),
                    "total": r.total_stocks,
                    "processed": r.processed_stocks,
                    "result_count": int(getattr(r, "result_count", 0) or 0),
                }
                for r in runs
            ]
        finally:
            session.close()

    return storage.list_strategy_runs(page=page, page_size=page_size)

@app.post("/api/strategy/runs/batch_delete")
async def batch_delete_strategy_runs(req: StrategyRunsBatchDeleteRequest):
    ids = []
    for x in (req.run_ids or []):
        try:
            ids.append(int(x))
        except Exception:
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {"deleted": [], "failed": []}

    failed = []
    cancellable = []
    session = storage.Session()
    try:
        for rid in ids:
            run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
            if not run:
                failed.append({"run_id": rid, "reason": "not_found"})
                continue
            st = str(run.status or "")
            if st in ["running", "pending"]:
                failed.append({"run_id": rid, "reason": "running"})
                continue
            if st == "queued":
                if not _cancel_task_if_queued("strategy_run", str(rid)):
                    failed.append({"run_id": rid, "reason": "running"})
                    continue
            cancellable.append(rid)
    finally:
        session.close()

    res = storage.batch_delete_strategy_runs(cancellable)
    merged_failed = (res.get("failed") or []) + failed
    return {"deleted": res.get("deleted") or [], "failed": merged_failed}

@app.post("/api/strategy/runs/{run_id}/cancel")
async def cancel_strategy_run(run_id: int):
    rid = int(run_id)
    session = storage.Session()
    try:
        run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        st = str(run.status or "")
        if st in ["completed", "failed", "canceled"]:
            return {"status": "success", "run_id": rid, "already_terminal": True}
        if st in ["running"]:
            raise HTTPException(status_code=409, detail="run is running")

        removed_from_queue = False
        with _task_queue_cv:
            for cur in (_task_current or {}).values():
                if cur and cur.get("kind") == "strategy_run" and str(cur.get("ref_id")) == str(rid):
                    raise HTTPException(status_code=409, detail="run is running")
            kept = []
            for t in _task_queue:
                if t.get("kind") == "strategy_run" and str(t.get("ref_id")) == str(rid):
                    removed_from_queue = True
                    continue
                kept.append(t)
            _task_queue[:] = kept

        run.status = "canceled"
        run.progress = 0
        run.completed_at = datetime.datetime.utcnow()
        session.commit()
        return {"status": "success", "run_id": rid, "removed_from_queue": removed_from_queue}
    finally:
        session.close()

@app.post("/api/strategy/runs/{run_id}/stop")
async def stop_strategy_run(run_id: int):
    rid = int(run_id)
    session = storage.Session()
    try:
        run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        st = str(run.status or "")
        if st in ["completed", "failed", "canceled"]:
            return {"status": "success", "run_id": rid, "already_terminal": True}

        removed_from_queue = False
        with _task_queue_cv:
            kept = []
            for t in _task_queue:
                if t.get("kind") == "strategy_run" and str(t.get("ref_id")) == str(rid):
                    removed_from_queue = True
                    continue
                kept.append(t)
            _task_queue[:] = kept

        run.status = "canceled"
        run.completed_at = datetime.datetime.utcnow()
        session.commit()
        return {"status": "success", "run_id": rid, "removed_from_queue": removed_from_queue}
    finally:
        session.close()

@app.delete("/api/strategy/runs/{run_id}")
async def delete_strategy_run(run_id: int):
    rid = int(run_id)
    session = storage.Session()
    try:
        run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        st = str(run.status or "")
        if st in ["running", "pending", "queued"]:
            raise HTTPException(status_code=409, detail="run is running")
    finally:
        session.close()
    return storage.delete_strategy_run(rid)

@app.get("/api/strategy/runs/{run_id}")
async def get_strategy_run(run_id: int):
    session = storage.Session()
    try:
        run = session.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        result = []
        if run.result_json:
            try:
                result = json.loads(run.result_json)
            except:
                pass
                
        return {
            "id": run.id,
            "strategy_name": run.strategy_name,
            "status": run.status,
            "progress": run.progress,
            "params": json.loads(run.params_json) if run.params_json else {},
            "results": result,
            "result_count": int(getattr(run, "result_count", 0) or 0),
            "created_at": _dt_to_utc_iso(run.created_at),
            "completed_at": _dt_to_utc_iso(run.completed_at)
        }
    finally:
        session.close()

# Mount static files if "static" directory exists (For Production/Deployment)
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
    
    @app.get("/")
    async def read_index():
        return FileResponse(os.path.join(static_dir, "index.html"))
        
    @app.get("/{catchall:path}")
    async def read_catchall(catchall: str):
        # Check if file exists in static
        file_path = os.path.join(static_dir, catchall)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        # Otherwise return index.html for SPA routing
        return FileResponse(os.path.join(static_dir, "index.html"))

if __name__ == "__main__":
    import uvicorn
    import argparse
    
    parser = argparse.ArgumentParser(description='MLChan Backend Server')
    parser.add_argument('--port', type=int, default=8001, help='Port to run the server on')
    args = parser.parse_args()
    
    # Use specified port or default 8001
    uvicorn.run(app, host="0.0.0.0", port=args.port)
