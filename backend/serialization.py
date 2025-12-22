from typing import List, Dict, Any
from Plot.PlotMeta import CChanPlotMeta, CBi_meta, CSeg_meta, CZS_meta, CBS_Point_meta, Cklc_meta
from Common.CEnum import KL_TYPE

def serialize_chan_data(meta: CChanPlotMeta, lv: KL_TYPE) -> Dict[str, Any]:
    """
    Serialize CChanPlotMeta to JSON-friendly dictionary for a specific level.
    """
    
    # K-Lines
    # ECharts candlestick: [date, open, close, low, high, volume]
    # We might need to split this for different chart libraries, but standard is array of arrays or objects.
    # Here we return struct for easy consumption.
    
    klines = []
    dates = []
    volumes = []
    
    # klu_iter yields CKLine_Unit. We need to handle time.
    for klu in meta.klu_iter():
        date_str = klu.time.to_str()
        dates.append(date_str)
        # open, close, low, high
        klines.append([klu.open, klu.close, klu.low, klu.high])
        volumes.append(float(klu.qfq_volume) if hasattr(klu, 'qfq_volume') else 0.0)
        
    # Bi
    bi_list = []
    for bi in meta.bi_list:
        bi_list.append({
            "idx": bi.idx,
            "type": bi.type.value if hasattr(bi.type, 'value') else str(bi.type),
            "is_sure": bi.is_sure,
            "start_coord": [bi.begin_x, bi.begin_y],
            "end_coord": [bi.end_x, bi.end_y]
        })
        
    # Seg
    seg_list = []
    for seg in meta.seg_list:
        seg_list.append({
            "idx": seg.idx,
            "is_sure": seg.is_sure,
            "start_coord": [seg.begin_x, seg.begin_y],
            "end_coord": [seg.end_x, seg.end_y]
        })
        
    # ZS (Center)
    zs_list = []
    for zs in meta.zs_lst:
        zs_list.append({
            "start_coord": [zs.begin, zs.low], # Bottom-Left (but actually x is index)
            "end_coord": [zs.end, zs.high],     # Top-Right
            "is_sure": zs.is_sure,
            "is_onebi_zs": zs.is_onebi_zs
        })
        
    # BSP (Buy/Sell Points)
    bsp_list = []
    for bsp in meta.bs_point_lst:
        bsp_list.append({
            "is_buy": bsp.is_buy,
            "type": bsp.type, # String like "1,2"
            "desc": bsp.desc(),
            "coord": [bsp.x, bsp.y],
            "is_seg": bsp.is_seg
        })

    # Moving Averages (Calculated in PlotDriver usually, but we can do it here if needed)
    # The current PlotDriver calculates means on the fly. 
    # We should calculate them here or rely on backend to provide them.
    # Let's extract means if available or calculate them.
    # CChanPlotMeta doesn't seem to store means directly.
    # PlotDriver does: mean_arr = [mean_dict.get(T, float("nan")) for mean_dict in mean_lst]
    # mean_lst comes from [klu.trend[TREND_TYPE.MEAN] for klu in meta.klu_iter()]
    
    means = {}
    from Common.CEnum import TREND_TYPE
    
    # Check if we have mean data
    first_klu = next(meta.klu_iter(), None)
    if first_klu and TREND_TYPE.MEAN in first_klu.trend:
        # Get all keys (5, 20 etc)
        mean_keys = first_klu.trend[TREND_TYPE.MEAN].keys()
        for k in mean_keys:
            means[k] = []
            
        for klu in meta.klu_iter():
            mean_dict = klu.trend.get(TREND_TYPE.MEAN, {})
            for k in mean_keys:
                means[k].append(mean_dict.get(k, None))
                
    return {
        "dates": dates,
        "klines": klines, # [open, close, low, high]
        "volumes": volumes,
        "bi": bi_list,
        "seg": seg_list,
        "zs": zs_list,
        "bsp": bsp_list,
        "means": means
    }
