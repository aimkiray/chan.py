import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DataAPI.ClickHouseAPI import CClickHouseAPI  # noqa: E402
from backend.chan_service import (  # noqa: E402
    get_pretrained_model_detail_by_key,
    json_safe_value,
    list_pretrained_model_metas,
    normalize_code,
    pretrain_and_persist_model,
)
from backend.live.common import parse_autype, parse_data_src, parse_frequency  # noqa: E402
from backend.storage import StorageManager  # noqa: E402
from backend.tools.daily_direction_snapshot import _load_active_mainboard  # noqa: E402
from backend.tools.run_experiment_matrix import build_result_rows, report_matrix  # noqa: E402


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _latest_matrix_dir(output_dir: str) -> Optional[Path]:
    base = _resolve_path(output_dir)
    if not base.exists():
        return None
    items = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("matrix_")]
    if not items:
        return None
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return items[0]


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _weighted_avg(rows: List[Dict], key: str, weight_key: str = "matched_count") -> Optional[float]:
    num = 0.0
    den = 0.0
    for row in rows or []:
        value = _safe_float(row.get(key))
        if value is None:
            continue
        try:
            weight = float(row.get(weight_key) or 0.0)
        except Exception:
            weight = 0.0
        if weight <= 0:
            weight = 1.0
        num += float(value) * float(weight)
        den += float(weight)
    if den <= 0:
        return None
    return num / den


def _profit_factor_from_rows(rows: List[Dict]) -> Tuple[Optional[float], bool]:
    pos = 0.0
    loss = 0.0
    saw_inf = False
    for row in rows or []:
        pos += float(_safe_float(row.get("gross_profit_sum")) or 0.0)
        loss += float(_safe_float(row.get("gross_loss_sum")) or 0.0)
        if bool(row.get("profit_factor_is_infinite")):
            saw_inf = True
    if loss <= 0:
        return (None, bool(pos > 0 or saw_inf))
    return (pos / loss, False)


def _profit_factor_gate_value(group: Dict) -> Optional[float]:
    if bool(group.get("profit_factor_is_infinite")):
        return float("inf")
    return _safe_float(group.get("profit_factor"))


def _max_drawdown_from_rows(rows: List[Dict]) -> Optional[float]:
    dated = []
    for row in rows or []:
        value = _safe_float(row.get("avg_net_return"))
        if value is None:
            continue
        dated.append((str(row.get("as_of_date") or ""), float(value)))
    if not dated:
        return None
    dated.sort(key=lambda x: x[0])
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for _, ret in dated:
        equity += float(ret)
        if equity > peak:
            peak = equity
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return float(max_dd)


def _group_rows(rows: List[Dict]) -> List[Dict]:
    groups: Dict[Tuple, List[Dict]] = {}
    for row in rows or []:
        key = (
            str(row.get("side_filter") or ""),
            str(row.get("rolling_sample_mode") or ""),
            int(float(row.get("profit_lookahead") or 0)),
            bool(row.get("market_filter_enabled")),
            int(float(row.get("market_filter_ma") or 0)) if bool(row.get("market_filter_enabled")) else 0,
        )
        groups.setdefault(key, []).append(row)

    out = []
    for key, items in groups.items():
        evaluated = [r for r in items if _safe_float(r.get("eval_accuracy")) is not None and int(float(r.get("matched_count") or 0)) > 0]
        total_matched = sum(int(float(r.get("matched_count") or 0)) for r in evaluated)
        weighted_accuracy = _weighted_avg(evaluated, "eval_accuracy")
        weighted_baseline = _weighted_avg(evaluated, "majority_baseline_accuracy")
        alpha = (weighted_accuracy - weighted_baseline) if weighted_accuracy is not None and weighted_baseline is not None else None
        coverages = [_safe_float(r.get("coverage")) for r in items]
        coverages = [x for x in coverages if x is not None]
        pf, pf_inf = _profit_factor_from_rows(evaluated)
        group = {
            "side_filter": key[0],
            "rolling_sample_mode": key[1],
            "profit_lookahead": key[2],
            "market_filter_enabled": key[3],
            "market_filter_ma": key[4],
            "run_count": len(items),
            "evaluated_count": len(evaluated),
            "total_matched": int(total_matched),
            "weighted_accuracy": weighted_accuracy,
            "weighted_baseline": weighted_baseline,
            "weighted_accuracy_minus_baseline": alpha,
            "avg_filter_auc": _weighted_avg(items, "filter_auc", weight_key="accepted_count"),
            "avg_filter_brier": _weighted_avg(items, "filter_brier", weight_key="accepted_count"),
            "avg_eval_auc": _weighted_avg(evaluated, "eval_auc"),
            "avg_eval_brier": _weighted_avg(evaluated, "eval_brier"),
            "avg_coverage": (sum(coverages) / len(coverages)) if coverages else None,
            "weighted_avg_net_return": _weighted_avg(evaluated, "avg_net_return"),
            "total_net_return": sum(float(_safe_float(r.get("total_net_return")) or 0.0) for r in evaluated) if evaluated else None,
            "win_rate_return": _weighted_avg(evaluated, "win_rate_return"),
            "profit_factor": pf,
            "profit_factor_is_infinite": bool(pf_inf),
            "max_drawdown": _max_drawdown_from_rows(evaluated),
            "sample_run_dir": str((items[0] or {}).get("run_dir") or ""),
        }
        out.append(group)
    out.sort(key=_group_score, reverse=True)
    return out


def _group_score(group: Dict) -> float:
    alpha = _safe_float(group.get("weighted_accuracy_minus_baseline"))
    acc = _safe_float(group.get("weighted_accuracy"))
    auc = _safe_float(group.get("avg_filter_auc")) or _safe_float(group.get("avg_eval_auc"))
    brier = _safe_float(group.get("avg_filter_brier")) or _safe_float(group.get("avg_eval_brier"))
    avg_net = _safe_float(group.get("weighted_avg_net_return"))
    total_net = _safe_float(group.get("total_net_return"))
    pf = _profit_factor_gate_value(group)
    max_dd = _safe_float(group.get("max_drawdown"))
    trades = max(0, int(float(group.get("total_matched") or 0)))
    score = 0.0
    score += 100.0 * float(alpha or -1.0)
    score += 10.0 * float(acc or 0.0)
    score += 5.0 * float(auc or 0.0)
    score += 50.0 * float(avg_net or 0.0)
    score += 5.0 * float(total_net or 0.0)
    if pf is not None and math.isfinite(float(pf)):
        score += min(10.0, float(pf)) / 10.0
    elif pf is not None:
        score += 1.0
    if max_dd is not None:
        score -= 5.0 * float(max_dd)
    if brier is not None:
        score -= 5.0 * float(brier)
    score += math.log1p(float(trades)) / 10.0
    return score


def _select_group(groups: List[Dict], args) -> Dict:
    candidates = []
    for group in groups or []:
        if args.side_filter and str(group.get("side_filter") or "") != str(args.side_filter):
            continue
        if args.rolling_sample_mode and str(group.get("rolling_sample_mode") or "") != str(args.rolling_sample_mode):
            continue
        if args.lookahead and int(group.get("profit_lookahead") or 0) != int(args.lookahead):
            continue
        if args.market_filter in ("on", "off"):
            want = args.market_filter == "on"
            if bool(group.get("market_filter_enabled")) != want:
                continue
        if args.market_ma is not None and int(group.get("market_filter_ma") or 0) != int(args.market_ma):
            continue
        if int(group.get("evaluated_count") or 0) < int(args.min_evaluated):
            continue
        if int(group.get("total_matched") or 0) < int(args.min_trades):
            continue
        alpha = _safe_float(group.get("weighted_accuracy_minus_baseline"))
        if alpha is None and not bool(args.allow_pending):
            continue
        if alpha is not None and alpha < float(args.min_alpha):
            continue
        acc = _safe_float(group.get("weighted_accuracy"))
        if acc is None and not bool(args.allow_pending):
            continue
        if acc is not None and acc < float(args.min_accuracy):
            continue
        brier = _safe_float(group.get("avg_filter_brier")) or _safe_float(group.get("avg_eval_brier"))
        if args.max_brier is not None and (brier is None or brier > float(args.max_brier)):
            continue
        pf = _profit_factor_gate_value(group)
        if args.min_profit_factor is not None:
            if pf is None and not bool(args.allow_pending):
                continue
            if pf is not None and float(pf) < float(args.min_profit_factor):
                continue
        avg_net = _safe_float(group.get("weighted_avg_net_return"))
        if args.min_avg_net_return is not None:
            if avg_net is None and not bool(args.allow_pending):
                continue
            if avg_net is not None and avg_net < float(args.min_avg_net_return):
                continue
        total_net = _safe_float(group.get("total_net_return"))
        if args.min_total_net_return is not None:
            if total_net is None and not bool(args.allow_pending):
                continue
            if total_net is not None and total_net < float(args.min_total_net_return):
                continue
        max_dd = _safe_float(group.get("max_drawdown"))
        if args.max_drawdown is not None:
            if max_dd is None and not bool(args.allow_pending):
                continue
            if max_dd is not None and max_dd > float(args.max_drawdown):
                continue
        candidates.append(group)
    if not candidates:
        raise RuntimeError("no matrix configuration passed selector gates")
    candidates.sort(key=_group_score, reverse=True)
    return candidates[0]


def _load_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _matrix_as_of_bounds(rows: List[Dict]) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    dates = []
    for row in rows or []:
        raw = str(row.get("as_of_date") or "").strip()[:10]
        if not raw:
            continue
        try:
            dates.append(dt.date.fromisoformat(raw))
        except Exception:
            pass
    if not dates:
        return None, None
    return min(dates), max(dates)


def _parse_codes(raw: str) -> List[str]:
    out = []
    seen = set()
    for part in str(raw or "").split(","):
        code = str(part or "").strip()
        if not code:
            continue
        try:
            code = normalize_code(code)
        except Exception:
            pass
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _load_codes(args, as_of: Optional[dt.date]) -> List[str]:
    explicit = _parse_codes(args.codes)
    if explicit:
        return explicit[: int(args.limit)] if int(args.limit or 0) > 0 else explicit

    client = CClickHouseAPI.get_client()
    if client is None:
        raise RuntimeError("ClickHouse is not available; pass --codes or fix DB connection")

    pool_id = str(args.pool_id or "").strip()
    if pool_id.lower() in ("", "active_hs_mainboard_a_share", "mainboard", "mainboard_a"):
        day = as_of or dt.date.today()
        df = _load_active_mainboard(client, day, limit=int(args.limit or 0))
        return [normalize_code(str(x)) for x in df["code"].astype(str).tolist()] if not df.empty else []

    codes = CClickHouseAPI.get_pool_members(pool_id=pool_id, table="stock_constituent", limit=int(args.limit or 5000) or 5000)
    out = []
    for code in codes or []:
        try:
            out.append(normalize_code(str(code)))
        except Exception:
            continue
    return list(dict.fromkeys(out))


def _find_trained_meta(model_key: str, bundle_path: str) -> Dict:
    meta = get_pretrained_model_detail_by_key(model_key) if model_key else None
    if meta:
        return meta
    for item in list_pretrained_model_metas(limit=None) or []:
        if isinstance(item, dict) and str(item.get("bundle_path") or "") == str(bundle_path):
            return item
    return {}


def export_bundle(args) -> int:
    load_dotenv(ROOT / ".env")
    matrix_dir = _resolve_path(args.matrix_dir) if args.matrix_dir else _latest_matrix_dir(args.output_dir)
    if matrix_dir is None or not matrix_dir.exists():
        raise RuntimeError("matrix directory not found")
    if bool(args.rebuild_report):
        report_matrix(matrix_dir)
    rows = build_result_rows(matrix_dir)
    if not rows:
        raise RuntimeError(f"no matrix rows under {matrix_dir}")
    groups = _group_rows(rows)
    selected = _select_group(groups, args)

    _, max_as_of = _matrix_as_of_bounds(rows)
    sample_meta = _load_json(Path(str(selected.get("sample_run_dir") or "")) / "metadata.json")
    filt = sample_meta.get("filter") if isinstance(sample_meta.get("filter"), dict) else {}
    chan = sample_meta.get("chan") if isinstance(sample_meta.get("chan"), dict) else {}

    frequency = str(args.frequency or chan.get("frequency") or "1d")
    freq_raw, level = parse_frequency(frequency)
    data_src = str(args.data_src or "clickhouse")
    data_src_type = parse_data_src(data_src)
    autype = parse_autype(args.autype)
    data_length_days = max(30, int(args.data_length_days or 730))
    begin_date = args.begin_date
    if not begin_date:
        anchor = max_as_of or dt.date.today()
        begin_date = (anchor - dt.timedelta(days=int(data_length_days))).isoformat()

    codes = _load_codes(args, max_as_of)
    if not codes:
        raise RuntimeError("no training codes resolved")

    feature_preset = args.feature_preset if args.feature_preset is not None else filt.get("feature_preset")
    model_type = str(args.model or filt.get("model") or "xgboost")
    trade_cost = float(args.trade_cost if args.trade_cost is not None else (filt.get("trade_cost") or 0.0))
    enforce_t1 = bool(args.enforce_t1 or filt.get("enforce_t1"))
    rolling_mode = str(selected.get("rolling_sample_mode") or args.rolling_sample_mode or "bsp")
    lookahead = int(selected.get("profit_lookahead") or args.lookahead or filt.get("profit_lookahead") or 3)
    side_filter = str(selected.get("side_filter") or args.side_filter or "buy")

    selection = {
        "matrix_dir": str(matrix_dir),
        "selected_group": json_safe_value(selected),
        "training": {
            "code_count": int(len(codes)),
            "begin_date": begin_date,
            "frequency": freq_raw,
            "data_src": data_src,
            "autype": getattr(autype, "name", str(autype)),
            "model": model_type,
            "feature_preset": feature_preset,
            "rolling_sample_mode": rolling_mode,
            "profit_lookahead": int(lookahead),
            "side_filter": side_filter,
            "rank_mode": str(args.rank_mode),
            "trade_cost": float(trade_cost),
            "enforce_t1": bool(enforce_t1),
        },
    }

    if args.dry_run:
        print(json.dumps(selection, ensure_ascii=False, indent=2))
        return 0

    path, result = pretrain_and_persist_model(
        codes=codes,
        level=level,
        data_src_type=data_src_type,
        begin_time=begin_date,
        model_type=model_type,
        frequency=freq_raw,
        data_src=data_src,
        calibrate_method=str(args.calibrate_method),
        profit_threshold=args.profit_threshold,
        auto_profit_quantile=float(args.auto_profit_quantile),
        profit_lookahead=int(lookahead),
        autype=autype,
        trade_cost=float(trade_cost),
        topk_frac=float(args.topk_frac),
        portfolio_min_score=float(args.portfolio_min_score),
        portfolio_top_n=int(args.portfolio_top_n),
        rank_mode=str(args.rank_mode),
        rolling_sample_mode=rolling_mode,
        rolling_stride=int(args.rolling_stride),
        feature_preset=feature_preset,
        side_filter=side_filter,
        enforce_t1=bool(enforce_t1),
        strategy_preset={
            "name": "matrix_selected_live_bundle",
            "matrix_dir": str(matrix_dir.name),
            "selected_group": json_safe_value(selected),
        },
    )
    if not path:
        raise RuntimeError(str((result or {}).get("detail") or "pretrain failed"))

    model_key = str((result or {}).get("model_key") or "")
    trained_meta = _find_trained_meta(model_key, str(path))
    inner = trained_meta.get("meta", {}) if isinstance(trained_meta.get("meta"), dict) else {}
    StorageManager().upsert_pretrained_model(
        model_key=model_key,
        model_type=model_type,
        frequency=freq_raw,
        data_src=data_src,
        params=selection,
        meta={
            "bundle_path": trained_meta.get("bundle_path") or str(path),
            "feature_count": trained_meta.get("feature_count", result.get("feature_count") if isinstance(result, dict) else 0),
            "sample_count": result.get("sample_count") if isinstance(result, dict) else inner.get("sample_count"),
            "trained_at": trained_meta.get("trained_at") or (result.get("trained_at") if isinstance(result, dict) else None),
            "accuracy": inner.get("accuracy") or (result.get("accuracy") if isinstance(result, dict) else None),
        },
    )
    if args.model_name:
        StorageManager().set_pretrained_model_name(model_key, str(args.model_name))

    selection["export_result"] = json_safe_value(result)
    selection["bundle_path"] = str(path)
    selection["model_key"] = model_key
    out_path = matrix_dir / "selected_live_model_bundle.json"
    out_path.write_text(json.dumps(json_safe_value(selection), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(json_safe_value(selection), ensure_ascii=False, indent=2, allow_nan=False))
    print(f"saved selection: {out_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Select a matrix configuration and export a live-ready pretrained model bundle.")
    parser.add_argument("--matrix-dir", default="")
    parser.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions")
    parser.add_argument("--rebuild-report", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-pending", action="store_true", help="allow selecting unevaluated groups; useful only for dry-run/plumbing checks")
    parser.add_argument("--side-filter", default="buy")
    parser.add_argument("--rolling-sample-mode", default="")
    parser.add_argument("--lookahead", type=int, default=0)
    parser.add_argument("--market-filter", choices=["any", "on", "off"], default="any")
    parser.add_argument("--market-ma", type=int, default=None)
    parser.add_argument("--min-evaluated", type=int, default=1)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--min-alpha", type=float, default=0.0)
    parser.add_argument("--min-accuracy", type=float, default=0.52)
    parser.add_argument("--max-brier", type=float, default=None)
    parser.add_argument("--min-profit-factor", type=float, default=None)
    parser.add_argument("--min-avg-net-return", type=float, default=None)
    parser.add_argument("--min-total-net-return", type=float, default=None)
    parser.add_argument("--max-drawdown", type=float, default=None)
    parser.add_argument("--pool-id", default="active_hs_mainboard_a_share")
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--begin-date", default="")
    parser.add_argument("--data-length-days", type=int, default=730)
    parser.add_argument("--frequency", default="")
    parser.add_argument("--data-src", default="clickhouse")
    parser.add_argument("--autype", default="qfq")
    parser.add_argument("--model", default="")
    parser.add_argument("--feature-preset", default=None)
    parser.add_argument("--rank-mode", default="cross_section")
    parser.add_argument("--rolling-stride", type=int, default=1)
    parser.add_argument("--trade-cost", type=float, default=None)
    parser.add_argument("--enforce-t1", action="store_true")
    parser.add_argument("--calibrate-method", default="none")
    parser.add_argument("--profit-threshold", type=float, default=0.0)
    parser.add_argument("--auto-profit-quantile", type=float, default=0.7)
    parser.add_argument("--topk-frac", type=float, default=0.2)
    parser.add_argument("--portfolio-min-score", type=float, default=0.6)
    parser.add_argument("--portfolio-top-n", type=int, default=2)
    parser.add_argument("--model-name", default="")
    args = parser.parse_args(argv)
    return export_bundle(args)


if __name__ == "__main__":
    raise SystemExit(main())
