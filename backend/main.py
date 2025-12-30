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

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.chan_service import (
    get_chan_data,
    predict_bsp,
    stragety_feature,
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
)
from backend.serialization import serialize_chan_data
from backend.storage import StorageManager
from Plot.PlotMeta import CChanPlotMeta
from Common.CEnum import KL_TYPE, DATA_SRC

app = FastAPI()
storage = StorageManager()

_pretrain_jobs_lock = threading.Lock()
_pretrain_jobs = {}
_PRETRAIN_JOBS_MAX = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_MAX", "50") or 50)
_PRETRAIN_JOBS_TTL_SEC = int(os.environ.get("MLCHAN_PRETRAIN_JOBS_TTL_SEC", str(6 * 3600)) or (6 * 3600))

def _utc_now_str():
    return datetime.datetime.utcnow().isoformat() + "Z"

def _set_pretrain_job(job_id: str, **fields):
    with _pretrain_jobs_lock:
        job = _pretrain_jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = _utc_now_str()

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
    force_refresh: bool = False
    data_length_mode: str = "default" # "default" or "max"
    data_length_years: Optional[float] = None
    use_pretrained: Optional[bool] = None
    pretrained_model_key: Optional[str] = None
    blend_models: Optional[bool] = None

class PretrainRequest(BaseModel):
    codes: list[str]
    frequency: str = "1d"
    data_src: str = "clickhouse"
    model: str = "xgboost"
    data_length_mode: str = "max"
    data_length_years: Optional[float] = None
    force_refresh: bool = False
    pool_name: Optional[str] = None

class UpdatePretrainedModelNameRequest(BaseModel):
    name: Optional[str] = None

@app.post("/api/pretrain_jobs")
async def create_pretrain_job(req: PretrainRequest):
    params = {
        "codes": sorted([normalize_code(str(c).strip()) for c in (req.codes or []) if str(c).strip()]),
        "data_length_mode": req.data_length_mode,
        "data_length_years": req.data_length_years,
        "pool_name": req.pool_name
    }
    # Removed database duplicate check to allow re-training with new unique keys
    # if not req.force_refresh and storage.is_duplicate_pretrain(req.model, req.frequency, req.data_src, params):
    #    raise HTTPException(status_code=409, detail="duplicate pretrain")

    job_id = str(uuid.uuid4())
    now = _utc_now_str()
    with _pretrain_jobs_lock:
        _pretrain_jobs[job_id] = {
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
            },
            "created_at": now,
            "updated_at": now,
        }

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
                calibrate_method="isotonic",
                progress_cb=progress_cb,
                pool_name=req.pool_name,
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

    t = threading.Thread(target=run_job, daemon=True)
    t.start()
    return {"job_id": job_id}

@app.get("/api/pretrain_jobs/{job_id}")
async def get_pretrain_job(job_id: str):
    with _pretrain_jobs_lock:
        job = _pretrain_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job

@app.get("/api/pretrain_jobs")
async def list_pretrain_jobs(limit: int = 200):
    with _pretrain_jobs_lock:
        jobs = list(_pretrain_jobs.values())
    def sort_key(j):
        return j.get("updated_at", "") or ""
    jobs.sort(key=sort_key, reverse=True)
    return {"items": jobs[: int(limit)]}

@app.get("/api/pretrained_models")
async def list_pretrained_models(page: Optional[int] = None, page_size: int = 20, limit: int = 200):
    if page is None:
        items = list_pretrained_model_metas(limit=int(limit))
        keys = [str(m.get("key") or "").strip() for m in items if isinstance(m, dict)]
        name_map = storage.get_pretrained_model_names_by_keys(keys)
        items = [_attach_pretrained_display_name(m, name_map.get(str(m.get("key")))) for m in items]
        return {"items": items}

    p = int(page)
    if p < 1:
        p = 1
    ps = int(page_size)
    if ps < 1:
        ps = 1
    if ps > 200:
        ps = 200

    all_items = list_pretrained_model_metas(limit=None)
    total = len(all_items)
    start = (p - 1) * ps
    end = start + ps
    items = all_items[start:end]
    keys = [str(m.get("key") or "").strip() for m in items if isinstance(m, dict)]
    name_map = storage.get_pretrained_model_names_by_keys(keys)
    items = [_attach_pretrained_display_name(m, name_map.get(str(m.get("key")))) for m in items]
    return {"items": items, "total": total, "page": p, "page_size": ps}

@app.get("/api/pretrained_models/{key}")
async def get_pretrained_model_detail(key: str):
    meta = get_pretrained_model_detail_by_key(key)
    if not meta:
        raise HTTPException(status_code=404, detail="model not found")
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
    display_name = name or _auto_pretrained_model_name(meta.get("model_type"), meta.get("frequency"), meta.get("trained_at"))
    return {"status": "success", "key": key, "name": name, "display_name": display_name}

@app.get("/api/history")
async def get_history(code: Optional[str] = None, page: int = 1, page_size: int = 10):
    return storage.get_history(code, page, page_size)

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

        # 1. Calculate Time Range First
        import datetime
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
            latest_data_time = get_latest_data_time(req.code, level, data_src)
        except Exception as e:
             print(f"Error fetching latest time: {e}")

        params = {
            "trigger_step": req.trigger_step,
            "bi_strict": req.bi_strict,
            "model": req.model,
            "data_src": req.data_src,
            "do_predict": req.do_predict,
            "data_length_mode": req.data_length_mode,
            "data_length_years": req.data_length_years
        }
        if req.use_pretrained is not None:
            params["use_pretrained"] = req.use_pretrained
        if req.pretrained_model_key is not None:
            params["pretrained_model_key"] = req.pretrained_model_key
        if req.blend_models is not None:
            params["blend_models"] = req.blend_models

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
                None,
                data_src,
                use_online_latest=(data_src == DATA_SRC.CLICK_HOUSE),
            )
        except Exception as e:
            print(f"Fetch data error: {e}")
            kl_list = []
            
        if not kl_list:
             raise HTTPException(status_code=404, detail=f"Data not found for {req.code}")
             
        latest_data_time = str(kl_list[-1].time)
        
        # 4. Run Analysis with preloaded data
        chan, bsp_dict, last_snapshot, config = get_chan_data(
            req.code, 
            req.trigger_step, 
            req.bi_strict, 
            level, 
            data_src, 
            preloaded_data=kl_list, 
            do_predict=req.do_predict,
            begin_time=begin_time
        )
        
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
                        pretrained_bundle = load_pretrained_model_bundle(req.model, req.frequency, req.data_src)
                    except Exception:
                        pretrained_bundle = None

            if should_blend:
                online_bst, online_feature_meta, online_accuracy = train_time_split_backtest(bsp_dict, model_type=req.model, calibrate_method="isotonic")
                if online_accuracy is not None:
                    online_accuracy["pretrained"] = False
                    online_accuracy["mode"] = "online"

                pretrained_bst = None
                pretrained_feature_meta = None
                pretrained_accuracy = None
                if pretrained_bundle and pretrained_bundle.get("model") and pretrained_bundle.get("feature_meta"):
                    pretrained_bst = pretrained_bundle["model"]
                    pretrained_feature_meta = pretrained_bundle["feature_meta"]
                    pretrained_accuracy = evaluate_fixed_model_time_split(bsp_dict, pretrained_bst, pretrained_feature_meta, test_ratio=0.2, threshold=0.5)
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
                bst, feature_meta, accuracy_info = train_time_split_backtest(bsp_dict, model_type=req.model, calibrate_method="isotonic")
                if accuracy_info is not None:
                    accuracy_info["pretrained"] = False
                    accuracy_info["mode"] = "online"

            if bst:
                bsp_list = last_snapshot.get_latest_bsp()
                if bsp_list:
                    latest_bsp = bsp_list[0]
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
            calibrate_method="isotonic",
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
