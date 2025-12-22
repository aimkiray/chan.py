import sys
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.chan_service import get_chan_data, get_or_train_model, predict_bsp, stragety_feature, get_stock_name
from backend.serialization import serialize_chan_data
from Plot.PlotMeta import CChanPlotMeta

app = FastAPI()

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

@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest):
    try:
        chan, bsp_dict, last_snapshot, config = get_chan_data(req.code, req.trigger_step, req.bi_strict)
        
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
        bst, feature_meta = get_or_train_model(bsp_dict, chan)
        
        latest_signal = None
        accuracy_info = None
        
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
        
        return {
            "status": "success",
            "data": data,
            "signal": latest_signal,
            "accuracy": accuracy_info,
            "stock_name": stock_name
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
