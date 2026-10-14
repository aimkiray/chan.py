import argparse
import contextlib
import datetime as dt
import io
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DataAPI.ClickHouseAPI import CClickHouseAPI  # noqa: E402
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE  # noqa: E402
from backend.chan_service import (  # noqa: E402
    _add_sample_semantic_features,
    build_chan_state_feature,
    extract_state_features_from_cur_lv,
    fetch_stock_data,
    get_chan_data,
    json_safe_value,
    predict_bsp,
    stragety_feature,
    train_time_split_backtest,
)


MAINBOARD_PATTERNS = (
    "sh600",
    "sh601",
    "sh603",
    "sh605",
    "sz000",
    "sz001",
    "sz002",
    "sz003",
)

FEATURE_COLUMNS = [
    "ret_1",
    "ret_2",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_20",
    "intraday_ret",
    "amplitude_pct",
    "close_pos",
    "turnover",
    "amount_log",
    "volume_log",
    "ma_gap_5",
    "ma_gap_10",
    "ma_gap_20",
    "ma_gap_60",
    "volatility_5",
    "volatility_10",
    "volatility_20",
    "volume_ratio_5",
    "volume_ratio_20",
    "turnover_ratio_20",
    "drawdown_20",
    "range_pos_20",
    "market_ret_1",
    "market_up_ratio",
    "rel_ret_1",
    "rel_ret_5",
    "rel_ret_20",
]


def _json_default(value):
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def _parse_date(raw: Optional[str]) -> Optional[dt.date]:
    if raw is None or str(raw).strip() == "":
        return None
    return dt.date.fromisoformat(str(raw).strip()[:10])


def _next_weekday(day: dt.date) -> dt.date:
    out = day + dt.timedelta(days=1)
    while out.weekday() >= 5:
        out += dt.timedelta(days=1)
    return out


def _nth_weekday_after(day: dt.date, n: int) -> dt.date:
    n = max(1, int(n or 1))
    out = day
    for _ in range(n):
        out = _next_weekday(out)
    return out


def _mainboard_where(alias: str = "i") -> str:
    parts = [f"startsWith({alias}.code, '{p}')" for p in MAINBOARD_PATTERNS]
    return "(" + " OR ".join(parts) + ")"


def _code_board(code: str) -> str:
    c = str(code or "").lower()
    if c.startswith("sh"):
        return "sh_main"
    if c.startswith("sz"):
        return "sz_main"
    return "unknown"


def _freq_to_level(freq: str):
    raw = str(freq or "").strip().lower()
    return {
        "1d": KL_TYPE.K_DAY,
        "d": KL_TYPE.K_DAY,
        "day": KL_TYPE.K_DAY,
        "5m": KL_TYPE.K_5M,
        "15m": KL_TYPE.K_15M,
        "30m": KL_TYPE.K_30M,
        "60m": KL_TYPE.K_60M,
        "1m": KL_TYPE.K_1M,
        "1w": KL_TYPE.K_WEEK,
        "1mo": KL_TYPE.K_MON,
    }.get(raw, KL_TYPE.K_DAY)


def _data_src_enum(raw: str):
    key = str(raw or "").strip().lower()
    return {
        "clickhouse": DATA_SRC.CLICK_HOUSE,
        "baostock": DATA_SRC.BAO_STOCK,
        "tdx": DATA_SRC.TDX,
        "tongdaxin": DATA_SRC.TDX,
        "csv": DATA_SRC.CSV,
    }.get(key, DATA_SRC.CLICK_HOUSE)


def _autype_enum(raw: str):
    key = str(raw or "").strip().lower()
    return {"qfq": AUTYPE.QFQ, "hfq": AUTYPE.HFQ, "none": AUTYPE.NONE}.get(key, AUTYPE.QFQ)


def _get_latest_daily_date(client) -> dt.date:
    rows = client.execute("SELECT max(date) FROM stock_daily")
    value = rows[0][0] if rows and rows[0] else None
    if not isinstance(value, dt.date):
        raise RuntimeError("stock_daily has no max(date)")
    return value


def _get_next_trading_date_or_weekday(client, as_of: dt.date) -> dt.date:
    rows = client.execute(
        "SELECT min(date) FROM stock_daily WHERE date > %(as_of)s",
        {"as_of": as_of},
    )
    value = rows[0][0] if rows and rows[0] else None
    if isinstance(value, dt.date) and value > as_of:
        return value
    return _next_weekday(as_of)


def _get_nth_trading_date_after(client, as_of: dt.date, n: int) -> Optional[dt.date]:
    n = max(1, int(n or 1))
    rows = client.execute(
        """
        SELECT DISTINCT date
        FROM stock_daily
        WHERE date > %(as_of)s
        ORDER BY date ASC
        LIMIT %(limit)s
        """,
        {"as_of": as_of, "limit": int(n)},
    )
    dates = [r[0] for r in rows or [] if isinstance(r[0], dt.date)]
    if len(dates) >= n:
        return dates[n - 1]
    return None


def _metadata_eval_lookahead(metadata: Dict, override: Optional[int] = None) -> int:
    if override is not None and int(override) > 0:
        return int(override)
    try:
        if str(metadata.get("method") or "") == "chan_bsp_filtered":
            return max(1, int((metadata.get("filter") or {}).get("profit_lookahead") or 1))
    except Exception:
        pass
    try:
        return max(1, int((metadata.get("model") or {}).get("holding_period") or 1))
    except Exception:
        return 1


def _load_active_mainboard(client, as_of: dt.date, limit: Optional[int] = None) -> pd.DataFrame:
    where = _mainboard_where("i")
    limit_sql = f"LIMIT {int(limit)}" if limit and int(limit) > 0 else ""
    query = f"""
        SELECT i.code, any(i.name) AS name
        FROM stock_info i
        INNER JOIN (
            SELECT DISTINCT code
            FROM stock_daily
            WHERE date = %(as_of)s
        ) d ON d.code = i.code
        WHERE {where}
        GROUP BY i.code
        ORDER BY i.code
        {limit_sql}
    """
    rows = client.execute(query, {"as_of": as_of})
    return pd.DataFrame(rows, columns=["code", "name"])


def _load_history(client, codes: Sequence[str], begin: dt.date, as_of: dt.date) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    query = """
        SELECT
            date,
            code,
            open,
            close,
            high,
            low,
            volume,
            amount,
            pct_chg,
            turnover
        FROM stock_daily
        WHERE date >= %(begin)s
          AND date <= %(as_of)s
          AND code IN %(codes)s
        ORDER BY code, date
    """
    rows = client.execute(
        query,
        {
            "begin": begin,
            "as_of": as_of,
            "codes": tuple(str(c) for c in codes),
        },
    )
    df = pd.DataFrame(
        rows,
        columns=["date", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"],
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    numeric_cols = ["open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _safe_div(num, den):
    return np.where(np.abs(den) > 1e-12, num / den, np.nan)


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.sort_values(["code", "date"]).copy()
    g = out.groupby("code", group_keys=False)

    close = out["close"].astype(float)
    open_ = out["open"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)
    turnover = out["turnover"].astype(float)

    out["ret_1"] = out["pct_chg"].astype(float) / 100.0
    for window in (2, 3, 5, 10, 20):
        shifted = g["close"].shift(window)
        out[f"ret_{window}"] = _safe_div(close, shifted) - 1.0

    out["intraday_ret"] = _safe_div(close, open_) - 1.0
    out["amplitude_pct"] = _safe_div(high - low, close)
    out["close_pos"] = _safe_div(close - low, high - low)
    out["amount_log"] = np.log1p(out["amount"].clip(lower=0.0))
    out["volume_log"] = np.log1p(volume.clip(lower=0.0))

    for window in (5, 10, 20, 60):
        ma = g["close"].transform(lambda s, w=window: s.rolling(w, min_periods=max(3, w // 2)).mean())
        out[f"ma_gap_{window}"] = _safe_div(close, ma) - 1.0

    for window in (5, 10, 20):
        vol = g["ret_1"].transform(lambda s, w=window: s.rolling(w, min_periods=max(3, w // 2)).std())
        out[f"volatility_{window}"] = vol

    for window in (5, 20):
        vma = g["volume"].transform(lambda s, w=window: s.rolling(w, min_periods=max(3, w // 2)).mean())
        out[f"volume_ratio_{window}"] = _safe_div(volume, vma) - 1.0
    tma = g["turnover"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    out["turnover_ratio_20"] = _safe_div(turnover, tma) - 1.0

    rolling_high_20 = g["high"].transform(lambda s: s.rolling(20, min_periods=10).max())
    rolling_low_20 = g["low"].transform(lambda s: s.rolling(20, min_periods=10).min())
    out["drawdown_20"] = _safe_div(close, rolling_high_20) - 1.0
    out["range_pos_20"] = _safe_div(close - rolling_low_20, rolling_high_20 - rolling_low_20)

    market = out.groupby("date").agg(
        market_ret_1=("ret_1", "mean"),
        market_up_ratio=("ret_1", lambda s: float((s > 0.0).mean()) if len(s) else np.nan),
    )
    out = out.merge(market, left_on="date", right_index=True, how="left")
    out["rel_ret_1"] = out["ret_1"] - out["market_ret_1"]
    for window in (5, 20):
        market_col = out.groupby("date")[f"ret_{window}"].transform("mean")
        out[f"rel_ret_{window}"] = out[f"ret_{window}"] - market_col

    next_close = g["close"].shift(-1)
    out["next_return"] = _safe_div(next_close, close) - 1.0
    out["label_up"] = np.where(out["next_return"].notna(), (out["next_return"] > 0.0).astype(float), np.nan)
    return out


def _fit_model(train: pd.DataFrame, val: pd.DataFrame, n_jobs: int, n_estimators: int):
    import xgboost as xgb

    model = xgb.XGBClassifier(
        n_estimators=int(n_estimators),
        max_depth=3,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=int(n_jobs),
        random_state=42,
    )
    eval_set = None
    if val is not None and not val.empty:
        eval_set = [(val[FEATURE_COLUMNS].to_numpy(dtype=np.float32), val["label_up"].astype(int).to_numpy())]
    model.fit(
        train[FEATURE_COLUMNS].to_numpy(dtype=np.float32),
        train["label_up"].astype(int).to_numpy(),
        eval_set=eval_set,
        verbose=False,
    )
    return model


def _score_metrics(y_true, prob, threshold: float) -> Dict[str, float]:
    y_true = np.asarray(y_true).astype(int)
    prob = np.asarray(prob).astype(float)
    pred = (prob >= float(threshold)).astype(int)
    out = {
        "count": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, pred)) if len(y_true) else None,
        "brier_score": float(brier_score_loss(y_true, prob)) if len(y_true) else None,
        "actual_up_rate": float(y_true.mean()) if len(y_true) else None,
        "predicted_up_rate": float(pred.mean()) if len(y_true) else None,
        "majority_baseline_accuracy": float(max(y_true.mean(), 1.0 - y_true.mean())) if len(y_true) else None,
        "threshold": float(threshold),
    }
    try:
        out["roc_auc"] = float(roc_auc_score(y_true, prob))
    except Exception:
        out["roc_auc"] = None
    return out


def _profit_factor(pos_sum: float, loss_sum: float) -> Tuple[Optional[float], bool]:
    pos_sum = float(pos_sum or 0.0)
    loss_sum = float(loss_sum or 0.0)
    if loss_sum <= 0:
        return (None, bool(pos_sum > 0))
    return (pos_sum / loss_sum, False)


def _max_drawdown_from_returns(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in values or []:
        try:
            ret = float(value)
        except Exception:
            continue
        if not math.isfinite(ret):
            continue
        equity += ret
        if equity > peak:
            peak = equity
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return float(max_dd)


def _return_metric_summary(net_returns: Sequence[float]) -> Dict[str, Optional[float]]:
    vals = []
    for value in net_returns or []:
        try:
            v = float(value)
        except Exception:
            continue
        if math.isfinite(v):
            vals.append(v)
    if not vals:
        return {
            "avg_net_return": None,
            "median_net_return": None,
            "total_net_return": None,
            "win_rate_return": None,
            "gross_profit_sum": 0.0,
            "gross_loss_sum": 0.0,
            "profit_factor": None,
            "profit_factor_is_infinite": False,
            "max_drawdown": None,
        }
    pos_sum = float(sum(v for v in vals if v > 0))
    loss_sum = float(-sum(v for v in vals if v < 0))
    pf, pf_inf = _profit_factor(pos_sum, loss_sum)
    arr = np.asarray(vals, dtype=float)
    return {
        "avg_net_return": float(arr.mean()),
        "median_net_return": float(np.median(arr)),
        "total_net_return": float(arr.sum()),
        "win_rate_return": float((arr > 0).mean()),
        "gross_profit_sum": pos_sum,
        "gross_loss_sum": loss_sum,
        "profit_factor": pf,
        "profit_factor_is_infinite": bool(pf_inf),
        "max_drawdown": _max_drawdown_from_returns(vals),
    }


def _best_accuracy_threshold(y_true, prob) -> Tuple[float, Dict[str, float]]:
    y_true = np.asarray(y_true).astype(int)
    prob = np.asarray(prob).astype(float)
    best_threshold = 0.5
    best_acc = -1.0
    for threshold in np.linspace(0.40, 0.60, 81):
        acc = float(accuracy_score(y_true, (prob >= threshold).astype(int)))
        if acc > best_acc or (math.isclose(acc, best_acc) and abs(threshold - 0.5) < abs(best_threshold - 0.5)):
            best_threshold = float(threshold)
            best_acc = acc
    return best_threshold, _score_metrics(y_true, prob, best_threshold)


def _slim_accuracy_info(info) -> Dict:
    if not isinstance(info, dict):
        return {}
    keep = [
        "method",
        "valid_count",
        "total_count",
        "train_count",
        "val_count",
        "test_count",
        "accuracy",
        "roc_auc",
        "pr_auc",
        "brier_score",
        "ece",
        "logloss",
        "side_filter",
        "sample_summary",
        "feature_selection",
        "feature_identity",
        "feature_hash",
        "rank_transformed",
        "warning",
    ]
    out = {k: info.get(k) for k in keep if k in info}
    trading = info.get("trading")
    if isinstance(trading, dict):
        out["trading"] = {}
        for k in ("topk", "top10", "threshold", "topk_vector"):
            if k in trading:
                out["trading"][k] = trading.get(k)
    baseline = info.get("baseline")
    if isinstance(baseline, dict):
        out["baseline"] = {}
        for k in ("always_negative", "prior_from_train", "logistic_regression"):
            if k in baseline:
                out["baseline"][k] = baseline.get(k)
    return json_safe_value(out)


def _latest_run_dir(base_dir: Path) -> Optional[Path]:
    if not base_dir.exists():
        return None
    dirs = [p for p in base_dir.iterdir() if p.is_dir() and (p / "metadata.json").exists()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def predict(args) -> int:
    load_dotenv(ROOT / ".env")
    client = CClickHouseAPI.get_client()
    if client is None:
        raise RuntimeError("ClickHouse is not available")

    as_of = _parse_date(args.as_of) or _get_latest_daily_date(client)
    target_date = _parse_date(args.target_date) or _get_next_trading_date_or_weekday(client, as_of)
    begin = as_of - dt.timedelta(days=int(round(float(args.train_years) * 365)))

    pool = _load_active_mainboard(client, as_of, limit=args.limit)
    if pool.empty:
        raise RuntimeError(f"no active mainboard stocks found for {as_of}")
    codes = pool["code"].astype(str).tolist()

    print(f"as_of={as_of} target_date={target_date} active_mainboard={len(codes)} begin={begin}")
    t0 = time.time()
    hist = _load_history(client, codes, begin, as_of)
    print(f"loaded rows={len(hist)} elapsed={time.time() - t0:.1f}s")
    if hist.empty:
        raise RuntimeError("no history rows loaded")

    feat = _build_features(hist)
    feat = feat.merge(pool, on="code", how="left")
    feat["board"] = feat["code"].map(_code_board)

    as_of_rows = feat[feat["date"] == as_of].copy()
    if as_of_rows.empty:
        raise RuntimeError(f"no prediction rows for {as_of}")

    model_rows = feat[(feat["date"] < as_of) & feat["label_up"].notna()].copy()
    model_rows = model_rows.dropna(subset=["label_up"])
    if model_rows.empty or as_of_rows.empty:
        raise RuntimeError("not enough feature rows for training or prediction")

    unique_dates = sorted(model_rows["date"].unique())
    val_n = max(1, min(int(args.validation_days), max(1, len(unique_dates) // 4)))
    val_dates = set(unique_dates[-val_n:])
    train = model_rows[~model_rows["date"].isin(val_dates)].copy()
    val = model_rows[model_rows["date"].isin(val_dates)].copy()

    if args.max_train_rows and len(train) > int(args.max_train_rows):
        train = train.sample(n=int(args.max_train_rows), random_state=42).sort_values(["date", "code"])
    if args.max_val_rows and len(val) > int(args.max_val_rows):
        val = val.sample(n=int(args.max_val_rows), random_state=42).sort_values(["date", "code"])

    print(f"train_rows={len(train)} val_rows={len(val)} predict_rows={len(as_of_rows)}")
    t1 = time.time()
    model = _fit_model(train, val, n_jobs=args.n_jobs, n_estimators=args.n_estimators)
    print(f"trained elapsed={time.time() - t1:.1f}s")

    val_prob = model.predict_proba(val[FEATURE_COLUMNS].to_numpy(dtype=np.float32))[:, 1] if not val.empty else np.array([])
    best_threshold, best_val_metrics = _best_accuracy_threshold(val["label_up"].astype(int).to_numpy(), val_prob) if len(val_prob) else (0.5, {})
    fixed_threshold = float(args.threshold)
    threshold = best_threshold if str(args.threshold_mode).strip().lower() == "validation_accuracy" else fixed_threshold
    val_metrics = _score_metrics(val["label_up"].astype(int).to_numpy(), val_prob, threshold) if len(val_prob) else {}
    default_metrics = _score_metrics(val["label_up"].astype(int).to_numpy(), val_prob, 0.5) if len(val_prob) else {}
    print(
        f"validation_acc={val_metrics.get('accuracy')} auc={val_metrics.get('roc_auc')} "
        f"threshold={threshold:.3f} best_threshold={best_threshold:.3f}"
    )

    prob = model.predict_proba(as_of_rows[FEATURE_COLUMNS].to_numpy(dtype=np.float32))[:, 1]
    pred_up = prob >= threshold
    predictions = as_of_rows[["code", "name", "board", "date", "close", "open", "high", "low", "volume", "amount", "turnover"]].copy()
    predictions = predictions.rename(columns={"date": "as_of_date", "close": "as_of_close"})
    predictions["target_date"] = target_date.isoformat()
    predictions["prob_up"] = prob.astype(float)
    predictions["threshold"] = float(threshold)
    predictions["pred_up"] = pred_up.astype(int)
    predictions["pred_direction"] = np.where(pred_up, "up", "down")
    predictions["confidence"] = np.abs(predictions["prob_up"] - threshold)
    predictions["rank_by_confidence"] = predictions["confidence"].rank(method="first", ascending=False).astype(int)
    predictions["rank_by_prob_up"] = predictions["prob_up"].rank(method="first", ascending=False).astype(int)
    predictions = predictions.sort_values(["rank_by_confidence", "code"])

    generated_at = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"mainboard_nextday_{as_of.isoformat()}_to_{target_date.isoformat()}_{generated_at}"
    base_dir = Path(args.output_dir)
    if not base_dir.is_absolute():
        base_dir = ROOT / base_dir
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pred_path = run_dir / "predictions.csv"
    meta_path = run_dir / "metadata.json"
    predictions.to_csv(pred_path, index=False, encoding="utf-8-sig")

    try:
        importance = dict(zip(FEATURE_COLUMNS, [float(x) for x in model.feature_importances_]))
    except Exception:
        importance = {}

    metadata = {
        "run_id": run_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_date": as_of.isoformat(),
        "target_date": target_date.isoformat(),
        "pool": {
            "name": "active_hs_mainboard_a_share",
            "patterns": list(MAINBOARD_PATTERNS),
            "stock_count": int(len(codes)),
            "prediction_count": int(len(predictions)),
        },
        "data": {
            "begin_date": begin.isoformat(),
            "history_rows": int(len(hist)),
            "feature_rows": int(len(model_rows)),
            "train_rows": int(len(train)),
            "validation_rows": int(len(val)),
        },
        "model": {
            "type": "xgboost.XGBClassifier",
            "n_estimators": int(args.n_estimators),
            "n_jobs": int(args.n_jobs),
            "features": FEATURE_COLUMNS,
            "threshold": float(threshold),
            "threshold_mode": str(args.threshold_mode),
            "validation_best_threshold": float(best_threshold),
            "validation_metrics": val_metrics,
            "validation_best_threshold_metrics": best_val_metrics,
            "validation_metrics_threshold_0_5": default_metrics,
            "feature_importance": importance,
        },
        "files": {
            "predictions_csv": str(pred_path),
            "metadata_json": str(meta_path),
        },
        "notes": [
            "Prediction uses only stock_daily rows up to as_of_date.",
            "Label is next trading row close / current close - 1 > 0.",
            "Universe is active Shanghai/Shenzhen mainboard A-shares by code prefix and as_of daily availability.",
        ],
    }
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2, default=_json_default)

    print(f"saved: {pred_path}")
    print(f"metadata: {meta_path}")
    return 0


def _latest_bsp_features(last_snapshot, latest_bsp):
    if last_snapshot is None or latest_bsp is None:
        return None
    try:
        cur_lv_chan = last_snapshot[0]
        last_klu = cur_lv_chan[-1][-1]
    except Exception:
        return latest_bsp
    try:
        state_feat = extract_state_features_from_cur_lv(cur_lv_chan, last_klu)
        if state_feat:
            latest_bsp.features.add_feat(state_feat)
    except Exception:
        pass
    try:
        chan_state_feat = build_chan_state_feature(cur_lv_chan, last_klu, latest_bsp)
        if chan_state_feat:
            latest_bsp.features.add_feat(chan_state_feat)
    except Exception:
        pass
    try:
        sample_kind = "event_bsp"
        age = int(max(0, int(getattr(last_klu, "idx", 0) or 0) - int(getattr(latest_bsp.klu, "idx", 0) or 0)))
        if age > 0:
            sample_kind = "rolling_bsp"
        _add_sample_semantic_features(latest_bsp.features, bool(latest_bsp.is_buy), latest_bsp, sample_kind)
    except Exception:
        pass
    try:
        latest_bsp.features.add_feat(stragety_feature(last_klu))
    except Exception:
        pass
    return latest_bsp


def _bsp_type_str(bsp) -> str:
    try:
        return str(bsp.type2str())
    except Exception:
        return ""


def _time_to_str(value) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "to_str"):
            return str(value.to_str())
        return str(value)
    except Exception:
        return None


def _market_trend_filter_state(args, as_of: dt.date) -> Dict:
    enabled = bool(getattr(args, "market_filter_enabled", False))
    out = {
        "enabled": enabled,
        "passed": True,
        "index_code": str(getattr(args, "market_filter_index", "sh000001") or "sh000001"),
        "ma_window": int(getattr(args, "market_filter_ma", 20) or 20),
        "close": None,
        "ma": None,
        "reason": None,
    }
    if not enabled:
        return out
    ma_window = max(2, int(out["ma_window"]))
    begin = as_of - dt.timedelta(days=max(60, ma_window * 4))
    try:
        kl = fetch_stock_data(
            out["index_code"],
            KL_TYPE.K_DAY,
            begin.isoformat(),
            as_of.isoformat(),
            DATA_SRC.CLICK_HOUSE,
            autype=AUTYPE.NONE,
            use_online_latest=False,
            asset_type="INDEX",
        )
    except Exception as e:
        out["passed"] = str(getattr(args, "market_filter_data_fail_mode", "allow")).lower() != "block"
        out["reason"] = f"index_fetch_error: {e}"
        return out
    closes = []
    for item in kl or []:
        try:
            closes.append(float(getattr(item, "close", 0.0) or 0.0))
        except Exception:
            continue
    closes = [x for x in closes if x > 0.0]
    if len(closes) < ma_window:
        out["passed"] = str(getattr(args, "market_filter_data_fail_mode", "allow")).lower() != "block"
        out["reason"] = f"insufficient_index_rows: {len(closes)}"
        return out
    last_close = float(closes[-1])
    ma = float(sum(closes[-ma_window:]) / ma_window)
    out["close"] = last_close
    out["ma"] = ma
    out["passed"] = bool(last_close >= ma)
    out["reason"] = "close>=ma" if out["passed"] else "close<ma"
    return out


def _collect_one_chan_bsp(code: str, name: str, args, as_of: dt.date, begin: dt.date) -> Tuple[Dict, List[Dict]]:
    level = _freq_to_level(args.frequency)
    data_src = _data_src_enum(args.data_src)
    autype = _autype_enum(args.autype)
    row = {
        "code": str(code),
        "name": str(name or ""),
        "board": _code_board(str(code)),
        "status": "init",
        "as_of_date": as_of.isoformat(),
        "data_latest_time": None,
        "as_of_close": None,
        "raw_signal_is_buy": None,
        "raw_signal_direction": None,
        "raw_signal_type": None,
        "raw_signal_time": None,
        "signal_age_bars": None,
        "sample_count": 0,
        "filter_score": None,
        "pred_up": None,
        "pred_direction": None,
        "confidence": None,
        "_latest_bsp": None,
    }
    try:
        kl_list = fetch_stock_data(
            str(code),
            level,
            begin.isoformat(),
            as_of.isoformat(),
            data_src,
            autype=autype,
            use_online_latest=bool(data_src == DATA_SRC.CLICK_HOUSE),
        )
        if not kl_list:
            row["status"] = "no_data"
            return row, []
        try:
            row["data_latest_time"] = _time_to_str(kl_list[-1].time)
            row["as_of_close"] = float(kl_list[-1].close)
        except Exception:
            pass

        stdout_ctx = contextlib.nullcontext() if bool(getattr(args, "verbose_chan", False)) else contextlib.redirect_stdout(io.StringIO())
        with stdout_ctx:
            _, bsp_dict, last_snapshot, _ = get_chan_data(
                str(code),
                True,
                True,
                level,
                data_src,
                preloaded_data=kl_list,
                do_predict=True,
                begin_time=begin.isoformat(),
                end_time=as_of.isoformat(),
                enable_rolling_lookback=True,
                autype=autype,
                rolling_sample_mode=str(args.rolling_sample_mode or "every_bar"),
                rolling_stride=int(args.rolling_stride or 1),
                side_filter=str(args.side_filter or "both"),
            )
        samples = list((bsp_dict or {}).values())
        row["sample_count"] = int(len(samples))
        if not last_snapshot:
            row["status"] = "no_chan_snapshot"
            return row, samples

        latest_list = []
        try:
            latest_list = list(last_snapshot.get_latest_bsp() or [])
        except Exception:
            latest_list = []
        if not latest_list:
            row["status"] = "no_bsp"
            return row, samples

        latest_bsp = latest_list[0]
        try:
            cur_lv_chan = last_snapshot[0]
            last_klu = cur_lv_chan[-1][-1]
            row["signal_age_bars"] = int(max(0, int(getattr(last_klu, "idx", 0) or 0) - int(getattr(latest_bsp.klu, "idx", 0) or 0)))
        except Exception:
            row["signal_age_bars"] = None
        row["raw_signal_is_buy"] = bool(getattr(latest_bsp, "is_buy", False))
        row["raw_signal_direction"] = "buy" if bool(getattr(latest_bsp, "is_buy", False)) else "sell"
        row["raw_signal_type"] = _bsp_type_str(latest_bsp)
        row["raw_signal_time"] = _time_to_str(getattr(getattr(latest_bsp, "klu", None), "time", None))
        row["_latest_bsp"] = _latest_bsp_features(last_snapshot, latest_bsp)
        row["status"] = "raw_bsp"
        return row, samples
    except Exception as e:
        row["status"] = "error"
        row["error"] = str(e)
        return row, []


def predict_chan_bsp(args) -> int:
    load_dotenv(ROOT / ".env")
    client = CClickHouseAPI.get_client()
    if client is None:
        raise RuntimeError("ClickHouse is not available")

    as_of = _parse_date(args.as_of) or _get_latest_daily_date(client)
    eval_lookahead = max(1, int(args.profit_lookahead or 1))
    target_date = (
        _parse_date(args.target_date)
        or _get_nth_trading_date_after(client, as_of, eval_lookahead)
        or _nth_weekday_after(as_of, eval_lookahead)
    )
    begin = as_of - dt.timedelta(days=int(args.data_length_days))
    pool = _load_active_mainboard(client, as_of, limit=args.limit)
    if pool.empty:
        raise RuntimeError(f"no active mainboard stocks found for {as_of}")
    market_state = _market_trend_filter_state(args, as_of)

    print(
        f"method=chan_bsp as_of={as_of} target_date={target_date} "
        f"frequency={args.frequency} active_mainboard={len(pool)} begin={begin}"
    )
    if market_state.get("enabled"):
        print(
            "market_filter "
            f"{market_state.get('index_code')} close={market_state.get('close')} "
            f"ma{market_state.get('ma_window')}={market_state.get('ma')} "
            f"passed={market_state.get('passed')} reason={market_state.get('reason')}"
        )

    rows = []
    merged_bsp = {}
    merged_idx = 0
    t0 = time.time()
    for i, item in enumerate(pool.to_dict("records"), start=1):
        row, samples = _collect_one_chan_bsp(str(item["code"]), str(item.get("name") or ""), args, as_of, begin)
        rows.append(row)
        if samples:
            try:
                samples.sort(key=lambda x: getattr(x.get("open_time"), "ts", 0))
            except Exception:
                pass
            cap = int(args.per_code_sample_cap or 0)
            if cap > 0 and len(samples) > cap:
                samples = samples[-cap:]
            for info in samples:
                merged_idx += 1
                merged_bsp[merged_idx] = info
        if i == 1 or i % int(args.progress_every or 100) == 0 or i == len(pool):
            accepted_raw = sum(1 for r in rows if r.get("status") == "raw_bsp")
            print(
                f"collected {i}/{len(pool)} raw_bsp={accepted_raw} "
                f"samples={len(merged_bsp)} elapsed={time.time() - t0:.1f}s"
            )

    model = None
    feature_meta = []
    accuracy_info = {
        "method": "chan_bsp_filter_untrained",
        "detail": "not enough merged BSP samples",
        "sample_count": int(len(merged_bsp)),
    }
    if len(merged_bsp) >= int(args.min_total_samples):
        model, feature_meta, accuracy_info = train_time_split_backtest(
            merged_bsp,
            model_type=str(args.model),
            calibrate_method=str(args.calibrate_method),
            profit_threshold=args.profit_threshold,
            auto_profit_quantile=float(args.auto_profit_quantile),
            profit_lookahead=int(args.profit_lookahead),
            backtest_mode=str(args.backtest_mode),
            min_train=int(args.min_train),
            min_val=int(args.min_val),
            min_test=int(args.min_test),
            n_jobs=int(args.n_jobs),
            trade_cost=float(args.trade_cost),
            topk_frac=float(args.topk_frac),
            feature_preset=args.feature_preset,
            side_filter=str(args.side_filter or "both"),
            enforce_t1=bool(args.enforce_t1),
            rank_transform_enabled=bool(args.rank_transform_enabled),
        )

    min_score = float(args.min_score)
    max_age_bars = int(args.max_signal_age_bars)
    accepted_count = 0
    for row in rows:
        latest_bsp = row.pop("_latest_bsp", None)
        if row.get("status") != "raw_bsp":
            continue
        age = row.get("signal_age_bars")
        if age is None or int(age) > max_age_bars:
            row["status"] = "stale_signal"
            continue
        if model is None or not feature_meta or latest_bsp is None:
            row["status"] = "model_unavailable"
            continue
        try:
            score = float(predict_bsp(model, latest_bsp, feature_meta))
        except Exception as e:
            row["status"] = "predict_error"
            row["error"] = str(e)
            continue
        row["filter_score"] = score
        row["confidence"] = abs(score - min_score)
        is_buy = bool(row.get("raw_signal_is_buy"))
        row["prob_up"] = score if is_buy else (1.0 - score)
        if score < min_score:
            row["status"] = "filtered_low_score"
            continue
        if is_buy and bool(market_state.get("enabled")) and not bool(market_state.get("passed")):
            row["status"] = "filtered_market_trend"
            row["market_filter_reason"] = market_state.get("reason")
            continue
        row["pred_up"] = 1 if is_buy else 0
        row["pred_direction"] = "up" if is_buy else "down"
        row["status"] = "accepted"
        accepted_count += 1

    out_rows = [json_safe_value(r) for r in rows]
    predictions = pd.DataFrame(out_rows)
    if not predictions.empty:
        predictions["target_date"] = target_date.isoformat()
        predictions["threshold"] = min_score
        predictions["rank_by_confidence"] = None
        accepted_mask = predictions["status"].eq("accepted")
        if accepted_mask.any():
            predictions.loc[accepted_mask, "rank_by_confidence"] = (
                predictions.loc[accepted_mask, "confidence"].astype(float).rank(method="first", ascending=False).astype(int)
            )
        predictions = predictions.sort_values(["status", "rank_by_confidence", "code"], na_position="last")

    generated_at = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"mainboard_chan_bsp_{args.frequency}_{as_of.isoformat()}_to_{target_date.isoformat()}_{generated_at}"
    base_dir = Path(args.output_dir)
    if not base_dir.is_absolute():
        base_dir = ROOT / base_dir
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / "predictions.csv"
    meta_path = run_dir / "metadata.json"
    predictions.to_csv(pred_path, index=False, encoding="utf-8-sig")

    status_counts = predictions["status"].value_counts(dropna=False).to_dict() if not predictions.empty else {}
    slim_accuracy = _slim_accuracy_info(accuracy_info)
    metadata = {
        "run_id": run_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "method": "chan_bsp_filtered",
        "as_of_date": as_of.isoformat(),
        "target_date": target_date.isoformat(),
        "eval_lookahead": int(eval_lookahead),
        "pool": {
            "name": "active_hs_mainboard_a_share",
            "patterns": list(MAINBOARD_PATTERNS),
            "stock_count": int(len(pool)),
            "prediction_count": int(len(predictions)),
            "accepted_count": int(accepted_count),
            "status_counts": {str(k): int(v) for k, v in status_counts.items()},
        },
        "chan": {
            "frequency": str(args.frequency),
            "begin_date": begin.isoformat(),
            "data_length_days": int(args.data_length_days),
            "rolling_sample_mode": str(args.rolling_sample_mode),
            "rolling_stride": int(args.rolling_stride),
            "side_filter": str(args.side_filter),
            "max_signal_age_bars": int(max_age_bars),
        },
        "filter": {
            "model": str(args.model),
            "feature_preset": args.feature_preset,
            "min_score": float(min_score),
            "profit_lookahead": int(args.profit_lookahead),
            "profit_threshold": args.profit_threshold,
            "auto_profit_quantile": float(args.auto_profit_quantile),
            "trade_cost": float(args.trade_cost),
            "enforce_t1": bool(args.enforce_t1),
            "merged_sample_count": int(len(merged_bsp)),
            "accuracy": slim_accuracy,
        },
        "market_filter": json_safe_value(market_state),
        "files": {
            "predictions_csv": str(pred_path),
            "metadata_json": str(meta_path),
        },
        "notes": [
            "Only rows with status=accepted are evaluated as predictions.",
            "Accepted buy BSP maps to next-day up; accepted sell BSP maps to next-day down.",
            "No BSP, stale BSP, low filter score, and model failures are saved but skipped in accuracy.",
        ],
    }
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2, default=_json_default)

    print(f"accepted={accepted_count}/{len(predictions)} status_counts={metadata['pool']['status_counts']}")
    print(f"filter_accuracy={slim_accuracy.get('accuracy') if isinstance(slim_accuracy, dict) else None}")
    print(f"saved: {pred_path}")
    print(f"metadata: {meta_path}")
    return 0


def evaluate(args) -> int:
    load_dotenv(ROOT / ".env")
    client = CClickHouseAPI.get_client()
    if client is None:
        raise RuntimeError("ClickHouse is not available")

    base_dir = Path(args.output_dir)
    if not base_dir.is_absolute():
        base_dir = ROOT / base_dir
    run_dir = Path(args.run_dir) if args.run_dir else _latest_run_dir(base_dir)
    if run_dir is None:
        raise RuntimeError(f"no run directory found under {base_dir}")
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    meta_path = run_dir / "metadata.json"
    pred_path = run_dir / "predictions.csv"
    if not meta_path.exists() or not pred_path.exists():
        raise RuntimeError(f"run directory is incomplete: {run_dir}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    predictions = pd.read_csv(pred_path)
    as_of = _parse_date(metadata.get("as_of_date"))
    metadata_target_date = _parse_date(metadata.get("target_date"))
    if as_of is None:
        raise RuntimeError("metadata is missing as_of_date")
    eval_lookahead = _metadata_eval_lookahead(metadata, args.eval_lookahead)
    target_date = _parse_date(args.target_date)
    if target_date is None:
        target_date = _get_nth_trading_date_after(client, as_of, eval_lookahead)
        if target_date is None and eval_lookahead == 1:
            target_date = metadata_target_date
    if target_date is None:
        expected = metadata_target_date or _nth_weekday_after(as_of, eval_lookahead)
        print(
            f"no enough actual trading dates after as_of={as_of} for eval_lookahead={eval_lookahead}; "
            f"expected around {expected}"
        )
        return 2

    codes = predictions["code"].astype(str).tolist()
    rows = client.execute(
        """
        SELECT date, code, close, pct_chg
        FROM stock_daily
        WHERE date = %(target_date)s
          AND code IN %(codes)s
        """,
        {"target_date": target_date, "codes": tuple(codes)},
    )
    actual = pd.DataFrame(rows, columns=["actual_date", "code", "target_close", "target_pct_chg"])
    if actual.empty:
        print(f"no actual rows for target_date={target_date}; run evaluate after target close data is loaded")
        return 2

    out = predictions.merge(actual, on="code", how="left")
    out["actual_return"] = (pd.to_numeric(out["target_close"], errors="coerce") / pd.to_numeric(out["as_of_close"], errors="coerce")) - 1.0
    out["actual_up"] = (out["actual_return"] > 0.0).astype("float")
    if "prob_up" not in out.columns:
        out["prob_up"] = np.nan
    if "filter_score" in out.columns and "raw_signal_is_buy" in out.columns:
        missing_prob = out["prob_up"].isna() & out["filter_score"].notna()
        if missing_prob.any():
            raw_buy = out["raw_signal_is_buy"].astype(str).str.strip().str.lower().isin(["true", "1", "buy", "up"])
            score = pd.to_numeric(out["filter_score"], errors="coerce")
            out.loc[missing_prob & raw_buy, "prob_up"] = score[missing_prob & raw_buy]
            out.loc[missing_prob & (~raw_buy), "prob_up"] = 1.0 - score[missing_prob & (~raw_buy)]
    out["pred_up"] = pd.to_numeric(out.get("pred_up"), errors="coerce")
    out["is_evaluated_prediction"] = out["target_close"].notna() & out["pred_up"].notna()
    out["hit"] = np.where(
        out["is_evaluated_prediction"],
        (out["pred_up"].astype("float") == out["actual_up"]).astype("float"),
        np.nan,
    )
    filt_meta = metadata.get("filter") if isinstance(metadata.get("filter"), dict) else {}
    try:
        trade_cost = float(filt_meta.get("trade_cost") or 0.0)
    except Exception:
        trade_cost = 0.0
    pred_up_num = pd.to_numeric(out["pred_up"], errors="coerce")
    actual_ret_num = pd.to_numeric(out["actual_return"], errors="coerce")
    out["directional_return"] = np.where(
        pred_up_num == 1,
        actual_ret_num,
        np.where(pred_up_num == 0, -actual_ret_num, np.nan),
    )
    out["net_return"] = pd.to_numeric(out["directional_return"], errors="coerce") - float(trade_cost)
    valid = out[out["is_evaluated_prediction"]].copy()
    if valid.empty:
        print(f"no evaluable prediction rows for target_date={target_date}")
        return 2

    y_true = valid["actual_up"].astype(int).to_numpy()
    prob = valid["prob_up"].astype(float).to_numpy()
    threshold = float(metadata.get("model", {}).get("threshold", 0.5))
    summary = _score_metrics(y_true, prob, threshold)
    summary.update(
        {
            "run_id": metadata.get("run_id"),
            "method": metadata.get("method", "daily_features"),
            "as_of_date": as_of.isoformat(),
            "target_date": target_date.isoformat(),
            "metadata_target_date": metadata_target_date.isoformat() if metadata_target_date else None,
            "eval_lookahead": int(eval_lookahead),
            "matched_count": int(len(valid)),
            "snapshot_count": int(len(out)),
            "missing_actual_count": int(out["target_close"].isna().sum()),
            "skipped_prediction_count": int((out["target_close"].notna() & out["pred_up"].isna()).sum()),
            "up_prediction_accuracy": float(valid.loc[valid["pred_up"] == 1, "hit"].mean()) if (valid["pred_up"] == 1).any() else None,
            "down_prediction_accuracy": float(valid.loc[valid["pred_up"] == 0, "hit"].mean()) if (valid["pred_up"] == 0).any() else None,
            "trade_cost": float(trade_cost),
        }
    )
    try:
        return_order = valid.sort_values(["rank_by_confidence", "code"], na_position="last")
    except Exception:
        return_order = valid.sort_values(["code"])
    summary.update(_return_metric_summary(pd.to_numeric(return_order["net_return"], errors="coerce").tolist()))
    if "status" in out.columns:
        summary["status_counts"] = {str(k): int(v) for k, v in out["status"].value_counts(dropna=False).to_dict().items()}

    by_board = []
    for board, grp in valid.groupby("board"):
        by_board.append(
            {
                "board": board,
                "count": int(len(grp)),
                "accuracy": float(grp["hit"].mean()),
                "actual_up_rate": float(grp["actual_up"].mean()),
                "predicted_up_rate": float(grp["pred_up"].mean()),
            }
        )
    summary["by_board"] = by_board

    confidence_slices = []
    for frac in (0.05, 0.10, 0.20, 0.50, 1.0):
        n = max(1, int(round(len(valid) * frac)))
        grp = valid.sort_values("confidence", ascending=False).head(n)
        confidence_slices.append(
            {
                "top_confidence_frac": float(frac),
                "count": int(len(grp)),
                "accuracy": float(grp["hit"].mean()),
                "actual_up_rate": float(grp["actual_up"].mean()),
                "predicted_up_rate": float(grp["pred_up"].mean()),
                **_return_metric_summary(pd.to_numeric(grp["net_return"], errors="coerce").tolist()),
            }
        )
    summary["confidence_slices"] = confidence_slices

    eval_csv = run_dir / f"evaluation_{target_date.isoformat()}.csv"
    eval_json = run_dir / f"evaluation_summary_{target_date.isoformat()}.json"
    out.to_csv(eval_csv, index=False, encoding="utf-8-sig")
    with eval_json.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=_json_default)

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    print(f"saved: {eval_csv}")
    print(f"summary: {eval_json}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create and evaluate daily next-day direction snapshots.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pred = sub.add_parser("predict", help="train a daily direction model and save a prediction snapshot")
    pred.add_argument("--as-of", default=None, help="as-of date, defaults to max stock_daily date")
    pred.add_argument("--target-date", default=None, help="target date, defaults to next trading date if known, else next weekday")
    pred.add_argument("--train-years", type=float, default=2.0)
    pred.add_argument("--validation-days", type=int, default=60)
    pred.add_argument("--limit", type=int, default=0, help="optional stock limit for smoke tests")
    pred.add_argument("--max-train-rows", type=int, default=900000)
    pred.add_argument("--max-val-rows", type=int, default=180000)
    pred.add_argument("--n-estimators", type=int, default=120)
    pred.add_argument("--n-jobs", type=int, default=8)
    pred.add_argument("--threshold-mode", choices=["fixed", "validation_accuracy"], default="fixed")
    pred.add_argument("--threshold", type=float, default=0.5)
    pred.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions")
    pred.set_defaults(func=predict)

    chan = sub.add_parser("predict-chan-bsp", help="filter Chan buy/sell points and save a next-day direction snapshot")
    chan.add_argument("--as-of", default=None, help="as-of date, defaults to max stock_daily date")
    chan.add_argument("--target-date", default=None, help="target date, defaults to next trading date if known, else next weekday")
    chan.add_argument("--frequency", default="1d", help="Chan level, e.g. 1d or 5m")
    chan.add_argument("--data-src", default="clickhouse")
    chan.add_argument("--autype", default="qfq")
    chan.add_argument("--data-length-days", type=int, default=730)
    chan.add_argument("--limit", type=int, default=0, help="optional stock limit for smoke tests")
    chan.add_argument("--model", default="xgboost")
    chan.add_argument("--feature-preset", default="purify_compact")
    chan.add_argument("--side-filter", default="both", choices=["both", "buy", "sell"])
    chan.add_argument("--rolling-sample-mode", choices=["none", "bsp", "every_bar", "all"], default="every_bar")
    chan.add_argument("--rolling-stride", type=int, default=1)
    chan.add_argument("--per-code-sample-cap", type=int, default=500)
    chan.add_argument("--min-total-samples", type=int, default=2000)
    chan.add_argument("--min-train", type=int, default=1000)
    chan.add_argument("--min-val", type=int, default=300)
    chan.add_argument("--min-test", type=int, default=1000)
    chan.add_argument("--backtest-mode", default="time_split")
    chan.add_argument("--calibrate-method", default="none")
    chan.add_argument("--profit-threshold", type=float, default=0.0)
    chan.add_argument("--auto-profit-quantile", type=float, default=0.7)
    chan.add_argument("--profit-lookahead", type=int, default=1)
    chan.add_argument("--trade-cost", type=float, default=0.001)
    chan.add_argument("--topk-frac", type=float, default=0.2)
    chan.add_argument("--enforce-t1", action="store_true", default=True)
    chan.add_argument("--no-enforce-t1", action="store_false", dest="enforce_t1")
    chan.add_argument("--rank-transform-enabled", action="store_true", default=True)
    chan.add_argument("--no-rank-transform", action="store_false", dest="rank_transform_enabled")
    chan.add_argument("--min-score", type=float, default=0.55)
    chan.add_argument("--max-signal-age-bars", type=int, default=3)
    chan.add_argument("--market-filter-enabled", action="store_true")
    chan.add_argument("--no-market-filter", action="store_false", dest="market_filter_enabled")
    chan.add_argument("--market-filter-index", default="sh000001")
    chan.add_argument("--market-filter-ma", type=int, default=20)
    chan.add_argument("--market-filter-data-fail-mode", choices=["allow", "block"], default="allow")
    chan.add_argument("--n-jobs", type=int, default=8)
    chan.add_argument("--progress-every", type=int, default=100)
    chan.add_argument("--verbose-chan", action="store_true")
    chan.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions")
    chan.set_defaults(func=predict_chan_bsp)

    ev = sub.add_parser("evaluate", help="evaluate a saved snapshot after target-day data is loaded")
    ev.add_argument("--run-dir", default=None, help="run directory; defaults to latest under output-dir")
    ev.add_argument("--target-date", default=None, help="override evaluation exit date")
    ev.add_argument("--eval-lookahead", type=int, default=None, help="override holding period used to find the evaluation exit date")
    ev.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions")
    ev.set_defaults(func=evaluate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
