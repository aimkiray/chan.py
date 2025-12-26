import sys
import os
import json
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

from backend.chan_service import get_chan_data, get_or_train_model, predict_bsp, stragety_feature, get_stock_name, download_stock_history, fetch_stock_data, get_latest_data_time, normalize_code
from backend.serialization import serialize_chan_data
from backend.storage import StorageManager
from Plot.PlotMeta import CChanPlotMeta
from Common.CEnum import KL_TYPE, DATA_SRC

app = FastAPI()
storage = StorageManager()

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

@app.get("/api/history")
async def get_history(code: Optional[str] = None):
    return storage.get_history(code)

@app.get("/api/history/{result_id}")
async def get_history_detail(result_id: int):
    result = storage.get_result_by_id(result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result

@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest):
    print(f"Received analyze request: code={req.code}, freq={req.frequency}, model={req.model}, force_refresh={req.force_refresh}")
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

        # 1. Check if we have a valid cache by comparing latest time
        # This avoids fetching full data if cache is valid
        latest_data_time = None
        kl_list = None
        
        try:
            # Only fetch the latest time (lightweight)
            latest_data_time = get_latest_data_time(req.code, level, data_src)
        except Exception as e:
             print(f"Error fetching latest time: {e}")

        params = {
            "trigger_step": req.trigger_step,
            "bi_strict": req.bi_strict,
            "model": req.model,
            "data_src": req.data_src,
            "do_predict": req.do_predict
        }

        if latest_data_time and not req.force_refresh:
            # 2. Check Cache
            cached_result = storage.get_latest_result(req.code, req.frequency, params, latest_data_time)
            
            if cached_result:
                print(f"Cache hit for {req.code} {req.frequency} {latest_data_time}")
                return json.loads(cached_result.result_json)
        
        # 3. Cache Miss or No Time info -> Fetch Full Data
        try:
            # Replicating logic from get_chan_data but with data_length_mode support:
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
            
            kl_list = fetch_stock_data(req.code, level, begin_time, None, data_src)
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
            bst, feature_meta = get_or_train_model(bsp_dict, chan, req.model)
            
            if bst:
                bsp_list = last_snapshot.get_latest_bsp()
                if bsp_list:
                    latest_bsp = bsp_list[0]
                    latest_bsp.features.add_feat(stragety_feature(last_klu))
                    score = predict_bsp(bst, latest_bsp, feature_meta)
                    days_diff = (last_klu.time.ts - latest_bsp.klu.time.ts) / (24 * 3600)
                    
                    latest_signal = {
                        "is_buy": latest_bsp.is_buy,
                        "type": latest_bsp.type2str(),
                        "date": latest_bsp.klu.time.to_str(),
                        "score": float(score),
                        "days_diff": float(days_diff)
                    }
                
                # Accuracy calculation
                # valid_count = sum([1 for idx in bsp_dict if idx in [b.klu.idx for b in chan.get_latest_bsp(number=0)]])
                # Note: chan.get_latest_bsp(number=0) returns ALL historical BSPs in current list? 
                # Actually number=0 usually means all.
                # But we need to check how get_or_train_model calculates labels.
                # It uses: bsp_academy = [bsp.klu.idx for bsp in _chan.get_latest_bsp(number=0)]
                
                all_bsps = [bsp.klu.idx for bsp in chan.get_latest_bsp(number=0)]
                valid_count = sum([1 for idx in bsp_dict if idx in all_bsps])
                total_count = len(bsp_dict)
                
                accuracy_info = {
                    "valid_count": valid_count,
                    "total_count": total_count,
                    "accuracy": valid_count / total_count if total_count > 0 else 0
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
                
            storage.save_result(
                code=req.code,
                freq=req.frequency,
                params=params,
                data_latest_time=latest_data_time,
                result_dict=result_data,
                model=req.model,
                signal_info=storage_signal_info
            )
        except Exception as e:
            print(f"Failed to save cache: {e}")
            
        return result_data
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

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
