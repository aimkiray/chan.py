import sys
import os
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
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
    data_src: str = "baostock"
    model: str = "xgboost"
    do_predict: bool = False
    force_refresh: bool = False

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
            "1s": KL_TYPE.K_1S,
            "1w": KL_TYPE.K_WEEK,
            "1mo": KL_TYPE.K_MON,
        }
        level = freq_map.get(req.frequency, KL_TYPE.K_DAY)
        
        src_map = {
            "baostock": DATA_SRC.BAO_STOCK,
            "akshare": DATA_SRC.AK_SHARE,
            "mock": DATA_SRC.MOCK,
            "ccxt": DATA_SRC.CCXT,
            "csv": DATA_SRC.CSV,
            "jqdata": DATA_SRC.JQ_DATA,
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
            # Replicating logic from get_chan_data:
            import datetime
            if data_src == DATA_SRC.JQ_DATA:
                 begin_time = "2024-10-01"
            elif level == KL_TYPE.K_DAY:
                begin_time = "2020-01-01"
            elif level == KL_TYPE.K_WEEK or level == KL_TYPE.K_MON:
                begin_time = "2015-01-01"
            elif level == KL_TYPE.K_60M or level == KL_TYPE.K_30M:
                begin_time = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
            elif level == KL_TYPE.K_1S:
                begin_time = None 
            else: 
                begin_time = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
            
            kl_list = fetch_stock_data(req.code, level, begin_time, None, data_src)
        except Exception as e:
            print(f"Fetch data error: {e}")
            kl_list = []
            
        if not kl_list:
             raise HTTPException(status_code=404, detail=f"Data not found for {req.code}")
             
        latest_data_time = str(kl_list[-1].time)
        
        # 4. Run Analysis with preloaded data
        chan, bsp_dict, last_snapshot, config = get_chan_data(req.code, req.trigger_step, req.bi_strict, level, data_src, preloaded_data=kl_list)
        
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
        stock_name = get_stock_name(req.code)
        
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
        
        result_data = {
            "status": "success",
            "data": data,
            "signal": latest_signal,
            "accuracy": accuracy_info,
            "stock_name": stock_name
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
