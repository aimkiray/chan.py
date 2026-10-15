import math
from typing import Dict, Any
from Plot.PlotMeta import CChanPlotMeta
from Common.CEnum import KL_TYPE, DATA_FIELD, TREND_TYPE


def _finite_or_none(x):
    """float() that maps NaN / ±inf / non-numeric input to None.

    The returned dict is fed straight into JSONResponse; raw NaN/inf would
    either crash json.dumps(allow_nan=False) or emit non-standard literals.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _bsp_type_str(t):
    """Normalize CBS_Point_meta.type to a JSON-safe string.

    Normally already a "1,2"-style str (from CBS_Point.type2str); tolerate a
    raw set/list of enum members or an enum slipping through.
    """
    if isinstance(t, str):
        return t
    try:
        if isinstance(t, (set, frozenset, list, tuple)):
            return ",".join(str(getattr(x, "value", x)) for x in t)
        return str(getattr(t, "value", t))
    except Exception:
        return str(t)


def serialize_chan_data(meta: CChanPlotMeta, lv: KL_TYPE) -> Dict[str, Any]:
    """
    Serialize CChanPlotMeta to JSON-friendly dictionary for a specific level.

    `lv` identifies the level the meta was built for. CChanPlotMeta is
    already level-scoped (constructed from a single CKLine_List), so there is
    nothing to filter by; the level is echoed back as an informational field.

    All numeric leaves are sanitized here (NaN/±inf -> None), so the output
    is JSON-safe on its own and does not rely on callers wrapping it in
    ``json_safe_value``.
    """

    # K-Lines
    # ECharts candlestick: [date, open, close, low, high, volume]
    # We might need to split this for different chart libraries, but standard is array of arrays or objects.
    # Here we return struct for easy consumption.

    klines = []
    dates = []
    volumes = []
    adj_factors = []

    # Moving averages live on each klu's trend dict. A single pass over
    # klu_iter() collects both OHLCV rows and means (previously two full
    # iterations); mean keys are discovered from the first klu whose
    # mean_dict is non-empty (earlier bars may lack trend data).
    means = {}
    mean_keys = None

    # klu_iter yields CKLine_Unit. We need to handle time.
    for klu in meta.klu_iter():
        dates.append(klu.time.to_str())
        # open, close, low, high
        klines.append([
            _finite_or_none(klu.open),
            _finite_or_none(klu.close),
            _finite_or_none(klu.low),
            _finite_or_none(klu.high),
        ])

        # Get volume from trade_info
        vol = 0.0
        if hasattr(klu, 'trade_info') and klu.trade_info:
            vol = klu.trade_info.metric.get(DATA_FIELD.FIELD_VOLUME, 0.0)
        v = _finite_or_none(vol)
        volumes.append(v if v is not None else 0.0)

        af = _finite_or_none(getattr(klu, "adj_factor", 1.0))
        adj_factors.append(af if af is not None else 1.0)

        mean_dict = (getattr(klu, "trend", None) or {}).get(TREND_TYPE.MEAN) or {}
        if mean_keys is None:
            if mean_dict:
                # First klu with a non-empty mean dict decides which MA
                # periods (5, 20, ...) are reported; bars already emitted
                # get None backfills so each series stays aligned with dates.
                mean_keys = list(mean_dict.keys())
                for k in mean_keys:
                    means[k] = [None] * (len(dates) - 1)
        for k in (mean_keys or []):
            means[k].append(_finite_or_none(mean_dict.get(k)))

    # Bi
    bi_list = []
    for bi in meta.bi_list:
        bi_list.append({
            "idx": bi.idx,
            "type": bi.type.value if hasattr(bi.type, 'value') else str(bi.type),
            "is_sure": bool(bi.is_sure),
            "start_coord": [bi.begin_x, _finite_or_none(bi.begin_y)],
            "end_coord": [bi.end_x, _finite_or_none(bi.end_y)]
        })

    # Seg
    seg_list = []
    for seg in meta.seg_list:
        seg_list.append({
            "idx": seg.idx,
            "is_sure": bool(seg.is_sure),
            "start_coord": [seg.begin_x, _finite_or_none(seg.begin_y)],
            "end_coord": [seg.end_x, _finite_or_none(seg.end_y)]
        })

    # ZS (Center)
    zs_list = []
    for zs in meta.zs_lst:
        zs_list.append({
            "start_coord": [zs.begin, _finite_or_none(zs.low)], # Bottom-Left (but actually x is index)
            "end_coord": [zs.end, _finite_or_none(zs.high)],     # Top-Right
            "is_sure": bool(zs.is_sure),
            "is_onebi_zs": bool(zs.is_onebi_zs)
        })

    # BSP (Buy/Sell Points)
    bsp_list = []
    for bsp in meta.bs_point_lst:
        try:
            desc = bsp.desc()
        except Exception:
            desc = None
        bsp_list.append({
            "is_buy": bool(bsp.is_buy),
            "type": _bsp_type_str(bsp.type), # String like "1,2"
            "desc": desc,
            "coord": [bsp.x, _finite_or_none(bsp.y)],
            "is_seg": bool(bsp.is_seg)
        })

    return {
        "level": getattr(lv, "name", None) or (str(lv) if lv is not None else None),
        "dates": dates,
        "klines": klines, # [open, close, low, high]
        "volumes": volumes,
        "adj_factors": adj_factors,
        "bi": bi_list,
        "seg": seg_list,
        "zs": zs_list,
        "bsp": bsp_list,
        "means": means
    }
