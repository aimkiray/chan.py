"""Service layer for experiment-matrix runs.

Note on layering: this service deliberately delegates to the in-process
entry points of ``backend.tools.run_experiment_matrix`` (``run_matrix``,
``evaluate_runs``, ``report_matrix``) so that API-triggered matrix jobs share
the exact same code path and artifact contract as the CLI. Moving the core
grid/execution logic into the service and reducing the tool to a thin argv
wrapper is a worthwhile refactor, but is intentionally left for a dedicated
change to keep behavioral parity.
"""

import datetime
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from backend.tools.run_experiment_matrix import (
    MATRIX_ID_RE,
    build_group_summary,
    build_result_rows,
    evaluate_runs,
    report_matrix,
    run_matrix,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_ROOT = REPO_ROOT / "backend" / "model_cache" / "daily_direction_predictions"
# A matrix that reports "running" (fewer completed predictions than configured)
# but whose manifest has not been touched for this long is treated as
# interrupted (worker killed/restarted) rather than alive.
MATRIX_STALE_SEC = float(os.environ.get("MLCHAN_MATRIX_STALE_SEC", "3600") or 3600)


def matrix_root() -> Path:
    MATRIX_ROOT.mkdir(parents=True, exist_ok=True)
    return MATRIX_ROOT


def make_matrix_id() -> str:
    base = f"matrix_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    root = matrix_root()
    if not (root / base).exists():
        return base
    for i in range(2, 1000):
        candidate = f"{base}_{i}"
        if not (root / candidate).exists():
            return candidate
    raise RuntimeError("unable to allocate matrix id")


def resolve_matrix_dir(matrix_id: str, must_exist: bool = True) -> Path:
    mid = str(matrix_id or "").strip()
    if not MATRIX_ID_RE.match(mid):
        raise ValueError("invalid matrix_id")
    root = matrix_root().resolve()
    path = (root / mid).resolve()
    if root not in [path, *path.parents]:
        raise ValueError("matrix path escapes cache root")
    if must_exist and not path.exists():
        raise FileNotFoundError("matrix not found")
    return path


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _matrix_dirs() -> List[Path]:
    root = matrix_root()
    dirs = [p for p in root.iterdir() if p.is_dir() and MATRIX_ID_RE.match(p.name)]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs


def _unique(items: List[Any]) -> List[Any]:
    out = []
    seen = set()
    for item in items:
        if item is None or item == "":
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _matrix_parameters(rows: List[Dict[str, Any]], manifest: Dict[str, Any]) -> Dict[str, Any]:
    configs = []
    for item in manifest.get("runs") or []:
        cfg = item.get("config") if isinstance(item, dict) else None
        if isinstance(cfg, dict):
            configs.append(cfg)
    return {
        "as_of_dates": _unique([r.get("as_of_date") for r in rows] + [c.get("as_of_date") for c in configs]),
        "rolling_modes": _unique([r.get("rolling_sample_mode") for r in rows] + [c.get("rolling_sample_mode") for c in configs]),
        "side_filters": _unique([r.get("side_filter") for r in rows] + [c.get("side_filter") for c in configs]),
        "lookaheads": _unique([_safe_int(r.get("profit_lookahead")) for r in rows] + [_safe_int(c.get("profit_lookahead")) for c in configs]),
        "market_mas": _unique([_safe_int(r.get("market_filter_ma")) for r in rows] + [_safe_int(c.get("market_filter_ma")) for c in configs]),
        "market_filter_enabled": any(bool(r.get("market_filter_enabled")) for r in rows) or any(bool(c.get("market_filter_enabled")) for c in configs),
    }


def _progress(rows: List[Dict[str, Any]], manifest: Dict[str, Any]) -> Dict[str, int]:
    total = _safe_int(manifest.get("config_count")) or len(rows)
    completed_predictions = len(rows)
    completed_evaluations = sum(1 for r in rows if r.get("eval_status") == "evaluated")
    pending_evaluations = max(0, completed_predictions - completed_evaluations)
    return {
        "total_runs": int(total),
        "completed_predictions": int(completed_predictions),
        "completed_evaluations": int(completed_evaluations),
        "pending_evaluations": int(pending_evaluations),
    }


def _status_from_progress(progress: Dict[str, int]) -> str:
    total = int(progress.get("total_runs") or 0)
    preds = int(progress.get("completed_predictions") or 0)
    evals = int(progress.get("completed_evaluations") or 0)
    pending = int(progress.get("pending_evaluations") or 0)
    if total > 0 and preds < total:
        return "running"
    if preds > 0 and pending > 0:
        return "pending_evaluation"
    if preds > 0 and evals >= preds:
        return "completed"
    return "empty"


def _matrix_status_with_heartbeat(matrix_dir: Path, manifest_path: Path, progress: Dict[str, int]) -> str:
    status = _status_from_progress(progress)
    if status != "running":
        return status
    # The manifest is flushed after every config; if it has not been touched
    # for MATRIX_STALE_SEC the producing worker is most likely gone.
    try:
        mtime = manifest_path.stat().st_mtime if manifest_path.exists() else matrix_dir.stat().st_mtime
    except Exception:
        mtime = None
    if mtime is not None and (time.time() - float(mtime)) > MATRIX_STALE_SEC:
        return "interrupted"
    return status


def matrix_summary(matrix_dir: Path) -> Dict[str, Any]:
    manifest_path = matrix_dir / "matrix_manifest.json"
    manifest = _load_json(manifest_path)
    rows = build_result_rows(matrix_dir)
    progress = _progress(rows, manifest)
    params = _matrix_parameters(rows, manifest)
    generated_at = manifest.get("created_at")
    if not generated_at:
        try:
            generated_at = datetime.datetime.fromtimestamp(matrix_dir.stat().st_mtime).isoformat(timespec="seconds")
        except Exception:
            generated_at = None
    dates = params.get("as_of_dates") or []
    return {
        "matrix_id": matrix_dir.name,
        "generated_at": generated_at,
        "start_date": min(dates) if dates else None,
        "end_date": max(dates) if dates else None,
        "status": _matrix_status_with_heartbeat(matrix_dir, manifest_path, progress),
        "progress": progress,
        "parameters": params,
    }


def list_matrices(limit: int = 100) -> List[Dict[str, Any]]:
    out = []
    for matrix_dir in _matrix_dirs()[: max(1, int(limit or 100))]:
        try:
            out.append(matrix_summary(matrix_dir))
        except Exception:
            continue
    return out


def _normalize_group(row: Dict[str, Any]) -> Dict[str, Any]:
    market_enabled = bool(row.get("market_filter_enabled"))
    market_ma = _safe_int(row.get("market_filter_ma")) if market_enabled else 0
    alpha = _safe_float(row.get("weighted_accuracy_minus_baseline"))
    return {
        "group_key": f"side_filter={row.get('side_filter')},rolling_mode={row.get('rolling_sample_mode')},ma={market_ma or 'off'},lookahead={row.get('profit_lookahead')}",
        "side_filter": row.get("side_filter"),
        "rolling_mode": row.get("rolling_sample_mode"),
        "lookahead": _safe_int(row.get("profit_lookahead")),
        "market_filter_enabled": market_enabled,
        "market_ma": market_ma,
        "run_count": _safe_int(row.get("run_count")) or 0,
        "evaluated_count": _safe_int(row.get("evaluated_count")) or 0,
        "trade_count": _safe_int(row.get("total_matched")) or 0,
        "eval_accuracy": _safe_float(row.get("weighted_accuracy")),
        "baseline_accuracy": _safe_float(row.get("weighted_baseline")),
        "alpha": alpha,
        "coverage": _safe_float(row.get("avg_coverage")),
        "avg_net_return": _safe_float(row.get("weighted_avg_net_return")),
        "total_net_return": _safe_float(row.get("total_net_return")),
        "win_rate_return": _safe_float(row.get("win_rate_return")),
        "profit_factor": _safe_float(row.get("profit_factor")),
        "profit_factor_is_infinite": bool(row.get("profit_factor_is_infinite")),
        "max_drawdown": _safe_float(row.get("max_drawdown")),
    }


def _normalize_run(row: Dict[str, Any]) -> Dict[str, Any]:
    market_enabled = bool(row.get("market_filter_enabled"))
    run_dir = Path(str(row.get("run_dir") or ""))
    return {
        "run_id": run_dir.name,
        "date": row.get("as_of_date"),
        "target_date": row.get("target_date"),
        "side_filter": row.get("side_filter"),
        "rolling_mode": row.get("rolling_sample_mode"),
        "lookahead": _safe_int(row.get("profit_lookahead")),
        "market_filter_enabled": market_enabled,
        "market_ma": (_safe_int(row.get("market_filter_ma")) if market_enabled else 0),
        "market_filter_passed": row.get("market_filter_passed"),
        "status": row.get("eval_status"),
        "accepted_count": _safe_int(row.get("accepted_count")) or 0,
        "stock_count": _safe_int(row.get("stock_count")) or 0,
        "coverage": _safe_float(row.get("coverage")),
        "trade_count": _safe_int(row.get("matched_count")) or 0,
        "accuracy": _safe_float(row.get("eval_accuracy")),
        "baseline_accuracy": _safe_float(row.get("majority_baseline_accuracy")),
        "alpha": _safe_float(row.get("accuracy_minus_baseline")),
        "roc_auc": _safe_float(row.get("eval_auc")),
        "brier_score": _safe_float(row.get("eval_brier")),
        "actual_up_rate": _safe_float(row.get("actual_up_rate")),
        "predicted_up_rate": _safe_float(row.get("predicted_up_rate")),
        "up_prediction_accuracy": _safe_float(row.get("up_prediction_accuracy")),
        "down_prediction_accuracy": _safe_float(row.get("down_prediction_accuracy")),
        "avg_net_return": _safe_float(row.get("avg_net_return")),
        "total_net_return": _safe_float(row.get("total_net_return")),
        "win_rate_return": _safe_float(row.get("win_rate_return")),
        "profit_factor": _safe_float(row.get("profit_factor")),
        "profit_factor_is_infinite": bool(row.get("profit_factor_is_infinite")),
        "max_drawdown": _safe_float(row.get("max_drawdown")),
    }


def matrix_detail(matrix_id: str, refresh_report: bool = False) -> Dict[str, Any]:
    matrix_dir = resolve_matrix_dir(matrix_id)
    if refresh_report:
        report_matrix(matrix_dir)
    rows = build_result_rows(matrix_dir)
    groups = build_group_summary(rows)
    report = ""
    report_path = matrix_dir / "matrix_report.md"
    if report_path.exists():
        try:
            report = report_path.read_text(encoding="utf-8")
        except Exception:
            report = ""
    summary = matrix_summary(matrix_dir)
    return {
        **summary,
        "report_markdown": report,
        "group_summaries": [_normalize_group(g) for g in groups],
        "runs": [_normalize_run(r) for r in rows],
    }


def rebuild_matrix_report(matrix_id: str) -> Dict[str, Any]:
    return matrix_detail(matrix_id, refresh_report=True)


def _execution_for_job(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the persisted execution status for job consumers.

    The tool writes ``not_ready`` when an evaluation pass skipped every run
    (target-date data not loaded yet). Surface it as ``pending_evaluation``,
    the status name the matrix API/frontend already understands, and pin
    ``return_code`` so the job-level status mapping in ``main.py`` does not
    silently report such runs as success.
    """
    out = dict(execution) if isinstance(execution, dict) else {}
    if str(out.get("status") or "") == "not_ready":
        out["status"] = "pending_evaluation"
        out["return_code"] = 2
    return out


def evaluate_matrix(matrix_id: str, target_date: Optional[str] = None, eval_lookahead: Optional[int] = None) -> Dict[str, Any]:
    matrix_dir = resolve_matrix_dir(matrix_id)
    args = SimpleNamespace(target_date=target_date, eval_lookahead=eval_lookahead, dry_run=False)
    eval_summary = evaluate_runs(args, matrix_dir)
    report_matrix(matrix_dir)
    execution = _load_json(matrix_dir / "matrix_execution_status.json")
    if not execution:
        execution = {"status": eval_summary.get("status"), "evaluation_summary": eval_summary}
    return {"detail": matrix_detail(matrix_id), "execution": _execution_for_job(execution)}


def _matrix_dir_from_argv(argv: List[str]) -> Optional[Path]:
    """Extract --matrix-dir from argv (both ``--matrix-dir X`` and
    ``--matrix-dir=X`` forms). Only whitelisted matrix dirs under
    ``matrix_root()`` are trusted for status-file reads; anything else is
    ignored so a crafted argv cannot point the service at arbitrary paths.
    """
    items = list(argv or [])
    value: Optional[str] = None
    for idx, item in enumerate(items):
        item = str(item)
        if item == "--matrix-dir" and idx + 1 < len(items):
            value = str(items[idx + 1])
        elif item.startswith("--matrix-dir="):
            value = item.split("=", 1)[1]
    if not value:
        return None
    try:
        path = Path(value)
    except Exception:
        return None
    if not MATRIX_ID_RE.match(path.name):
        return None
    try:
        root = matrix_root().resolve()
        resolved = path.resolve()
        if root not in [resolved, *resolved.parents]:
            return None
    except Exception:
        return None
    return path


def run_matrix_with_args(argv: List[str]) -> Dict[str, Any]:
    matrix_dir = _matrix_dir_from_argv(argv)
    ret = int(run_matrix(argv))
    execution = {}
    if matrix_dir is not None:
        execution = _load_json(matrix_dir / "matrix_execution_status.json")
    # ``_load_json`` returns whatever JSON was persisted; a non-dict payload
    # (e.g. a JSON list) is truthy and would explode on the item assignment
    # below, so normalize to a dict before touching ``return_code``.
    if not isinstance(execution, dict):
        execution = {}
    if not execution:
        execution = {"status": "success" if ret == 0 else "error"}
    execution["return_code"] = ret
    return _execution_for_job(execution)
