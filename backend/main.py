import sys
import os
import json
import datetime
import threading
import uuid
import gc
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
from backend.storage import StorageManager, StrategyRun
from backend.strategy_runner import StrategyRunner
from Plot.PlotMeta import CChanPlotMeta
from Common.CEnum import KL_TYPE, DATA_SRC, AUTYPE

app = FastAPI()
storage = StorageManager()
strategy_runner = StrategyRunner(storage)

_pretrain_jobs_lock = threading.Lock()
_pretrain_jobs = {}
_PRETRAIN_JOBS_MAX = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_MAX", "50") or 50)
_PRETRAIN_JOBS_TTL_SEC = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_TTL_SEC", str(6 * 3600)) or (6 * 3600))

_task_queue_cv = threading.Condition()
_task_queue = []
_task_current = None
_task_worker_started = False

def _ensure_task_worker_started():
    global _task_worker_started
    with _task_queue_cv:
        if _task_worker_started:
            return
        _task_worker_started = True

    def _worker_loop():
        global _task_current
        while True:
            task = None
            with _task_queue_cv:
                while not _task_queue:
                    _task_queue_cv.wait()
                task = _task_queue.pop(0)
                _task_current = task
            try:
                fn = task.get("fn")
                if callable(fn):
                    fn()
            except Exception:
                pass
            finally:
                with _task_queue_cv:
                    _task_current = None

    t = threading.Thread(target=_worker_loop, daemon=True)
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
        cur = _task_current
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
    profit_threshold: Optional[float] = 0.01
    auto_profit_quantile: float = 0.7
    profit_lookahead: int = 5
    backtest_mode: str = "auto"
    walk_forward_max_folds: int = 20
    walk_forward_test_window: Optional[int] = None
    walk_forward_val_window: Optional[int] = None
    walk_forward_step: Optional[int] = None
    trade_cost: float = 0.0
    topk_frac: float = 0.2
    quantile_bin_count: int = 5
    calibrate_method: str = "none"
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
    backtest_mode: str = "auto"
    walk_forward_max_folds: int = 20
    walk_forward_test_window: Optional[int] = None
    walk_forward_val_window: Optional[int] = None
    walk_forward_step: Optional[int] = None
    trade_cost: float = 0.0
    topk_frac: float = 0.2
    quantile_bin_count: int = 5
    calibrate_method: str = "none"
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
    if req.pool_id == "chinext50":
        from backend.vector_backtest import get_chinext50_stocks
        pool_codes = get_chinext50_stocks()
    else:
        # TODO: Support other pools
        pass
    
    if not pool_codes:
         # Fallback for testing
         pool_codes = ["sz.300059", "sz.300750"] # Sample ChiNext stocks
         
    # Run
    # Warning: This is blocking. In production, use background task.
    result = tester.run(pool_codes, start, end, req.model_key)
    return result

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

@app.post("/api/strategy/run")
async def run_strategy(req: StrategyRequest):
    try:
        params = req.dict()
        session = storage.Session()
        run = StrategyRun(
            strategy_name=req.strategy_name,
            params_json=json.dumps(params),
            status="pending",
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
            cur = _task_current
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

@app.delete("/api/strategy/runs/{run_id}")
async def delete_strategy_run(run_id: int):
    rid = int(run_id)
    session = storage.Session()
    try:
        run = session.query(StrategyRun).filter(StrategyRun.id == rid).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        st = str(run.status or "")
        if st in ["running", "pending"]:
            raise HTTPException(status_code=409, detail="run is running")
        if st == "queued":
            if not _cancel_task_if_queued("strategy_run", str(rid)):
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
