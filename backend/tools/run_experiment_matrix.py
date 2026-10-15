import argparse
import csv
import datetime as dt
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DataAPI.ClickHouseAPI import CClickHouseAPI  # noqa: E402
from backend.tools.daily_direction_snapshot import (  # noqa: E402
    _market_trend_filter_state,
    main as snapshot_main,
)


def _resolve_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return ROOT / p


def _parse_date(raw: Optional[str]) -> Optional[dt.date]:
    if raw is None or str(raw).strip() == "":
        return None
    return dt.date.fromisoformat(str(raw).strip())


def _csv_items(raw: str, default: Iterable[str]) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return [str(x) for x in default]
    out = []
    seen = set()
    for part in text.split(","):
        value = str(part or "").strip()
        if not value or value in seen:
            continue
        out.append(value)
        seen.add(value)
    return out


def _csv_ints(raw: str, default: Iterable[int]) -> List[int]:
    out = []
    for item in _csv_items(raw, [str(x) for x in default]):
        try:
            value = int(float(item))
        except Exception:
            continue
        if value > 0 and value not in out:
            out.append(value)
    return out


def _latest_matrix_dir(output_dir: str) -> Optional[Path]:
    base = _resolve_path(output_dir)
    if not base.exists():
        return None
    items = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("matrix_")]
    if not items:
        return None
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return items[0]


def _trading_dates(client, start: Optional[dt.date], end: Optional[dt.date], max_dates: int = 0) -> List[str]:
    if start is None and end is None:
        rows = client.execute("SELECT max(date) FROM stock_daily")
        value = rows[0][0] if rows and rows[0] else None
        if not isinstance(value, dt.date):
            raise RuntimeError("stock_daily has no max(date)")
        return [value.isoformat()]
    if start is None or end is None:
        raise RuntimeError("--start-date and --end-date must be provided together")
    query = """
        SELECT DISTINCT date
        FROM stock_daily
        WHERE date >= %(start)s
          AND date <= %(end)s
        ORDER BY date ASC
    """
    rows = client.execute(query, {"start": start, "end": end})
    dates = [r[0].isoformat() for r in rows or [] if isinstance(r[0], dt.date)]
    if max_dates and int(max_dates) > 0:
        dates = dates[-int(max_dates):]
    return dates


def _matrix_dir_for_run(args) -> Path:
    if args.matrix_dir:
        p = _resolve_path(args.matrix_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    p = _resolve_path(args.output_dir) / f"matrix_{timestamp}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _matrix_dir_for_read(args) -> Path:
    if args.matrix_dir:
        p = _resolve_path(args.matrix_dir)
    else:
        p = _latest_matrix_dir(args.output_dir)
    if p is None or not p.exists():
        raise RuntimeError(f"no matrix directory found under {args.output_dir}")
    return p


def _load_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_dirs(matrix_dir: Path) -> List[Path]:
    return sorted([p for p in matrix_dir.glob("mainboard_chan_bsp_*") if p.is_dir() and (p / "metadata.json").exists()])


def _config_key(meta: Dict) -> Dict:
    chan = meta.get("chan") if isinstance(meta.get("chan"), dict) else {}
    filt = meta.get("filter") if isinstance(meta.get("filter"), dict) else {}
    market = meta.get("market_filter") if isinstance(meta.get("market_filter"), dict) else {}
    market_enabled = bool(market.get("enabled", False))
    return {
        "as_of_date": str(meta.get("as_of_date") or ""),
        "side_filter": str(chan.get("side_filter") or ""),
        "rolling_sample_mode": str(chan.get("rolling_sample_mode") or ""),
        "profit_lookahead": int(filt.get("profit_lookahead") or meta.get("eval_lookahead") or 1),
        "market_filter_enabled": market_enabled,
        "market_filter_index": str(market.get("index_code") or "") if market_enabled else "",
        "market_filter_ma": int(market.get("ma_window") or 0) if market_enabled else 0,
    }


def _discover_existing_keys(matrix_dir: Path) -> set:
    keys = set()
    for run_dir in _run_dirs(matrix_dir):
        meta = _load_json(run_dir / "metadata.json")
        key = _config_key(meta)
        keys.add(tuple(sorted(key.items())))
    return keys


def _discover_existing_run_map(matrix_dir: Path) -> Dict[Tuple, Path]:
    out = {}
    for run_dir in _run_dirs(matrix_dir):
        meta = _load_json(run_dir / "metadata.json")
        key = tuple(sorted(_config_key(meta).items()))
        if key:
            out[key] = run_dir
    return out


def _config_key_tuple(config: Dict) -> Tuple:
    return tuple(sorted({
        "as_of_date": str(config.get("as_of_date") or ""),
        "side_filter": str(config.get("side_filter") or ""),
        "rolling_sample_mode": str(config.get("rolling_sample_mode") or ""),
        "profit_lookahead": int(config.get("profit_lookahead") or 1),
        "market_filter_enabled": bool(config.get("market_filter_enabled")),
        "market_filter_index": str(config.get("market_filter_index") or "") if bool(config.get("market_filter_enabled")) else "",
        "market_filter_ma": int(config.get("market_filter_ma") or 0) if bool(config.get("market_filter_enabled")) else 0,
    }.items()))


def _base_config_key(config: Dict) -> Tuple:
    return (
        str(config.get("as_of_date") or ""),
        str(config.get("rolling_sample_mode") or ""),
        str(config.get("side_filter") or ""),
        int(config.get("profit_lookahead") or 1),
    )


def _build_grid(args, client) -> List[Dict]:
    if args.dates:
        dates = _csv_items(args.dates, [])
        for value in dates:
            try:
                _parse_date(value)
            except ValueError:
                raise RuntimeError(f"invalid --dates entry (expected ISO YYYY-MM-DD): {value!r}")
    else:
        if client is None:
            raise RuntimeError("ClickHouse is required when --dates is not provided")
        dates = _trading_dates(client, _parse_date(args.start_date), _parse_date(args.end_date), args.max_dates)
    rolling_modes = _csv_items(args.rolling_modes, ["none", "bsp"])
    side_filters = _csv_items(args.side_filters, ["buy", "sell", "both"])
    lookaheads = _csv_ints(args.lookaheads, [1, 3])
    market_mas = _csv_ints(args.market_mas, [20])

    market_specs = []
    for ma in market_mas:
        market_specs.append({"enabled": True, "ma": int(ma)})
    if args.include_no_market_filter or not market_specs:
        market_specs.append({"enabled": False, "ma": 0})

    out = []
    for as_of in dates:
        for rolling in rolling_modes:
            for side in side_filters:
                for lookahead in lookaheads:
                    for market in market_specs:
                        out.append(
                            {
                                "as_of_date": str(as_of),
                                "rolling_sample_mode": str(rolling),
                                "side_filter": str(side),
                                "profit_lookahead": int(lookahead),
                                "market_filter_enabled": bool(market["enabled"]),
                                "market_filter_index": str(args.market_filter_index or "sh000001") if bool(market["enabled"]) else "",
                                "market_filter_ma": int(market["ma"]),
                            }
                        )
    return out


def _snapshot_cmd(config: Dict, args, matrix_dir: Path) -> List[str]:
    cmd = [
        "predict-chan-bsp",
        "--as-of", config["as_of_date"],
        "--frequency", args.frequency,
        "--rolling-sample-mode", config["rolling_sample_mode"],
        "--profit-lookahead", str(config["profit_lookahead"]),
        "--side-filter", config["side_filter"],
        "--min-score", str(args.min_score),
        "--data-length-days", str(args.data_length_days),
        "--per-code-sample-cap", str(args.per_code_sample_cap),
        "--min-total-samples", str(args.min_total_samples),
        "--min-train", str(args.min_train),
        "--min-val", str(args.min_val),
        "--min-test", str(args.min_test),
        "--backtest-mode", args.backtest_mode,
        "--feature-preset", args.feature_preset,
        "--trade-cost", str(args.trade_cost),
        "--max-signal-age-bars", str(args.max_signal_age_bars),
        "--n-jobs", str(args.n_jobs),
        "--progress-every", str(args.progress_every),
        "--output-dir", str(matrix_dir),
    ]
    if args.limit and int(args.limit) > 0:
        cmd.extend(["--limit", str(args.limit)])
    if config["market_filter_enabled"]:
        cmd.extend([
            "--market-filter-enabled",
            "--market-filter-index", config["market_filter_index"],
            "--market-filter-ma", str(config["market_filter_ma"]),
        ])
    else:
        cmd.append("--no-market-filter")
    if args.no_rank_transform:
        cmd.append("--no-rank-transform")
    if args.no_enforce_t1:
        cmd.append("--no-enforce-t1")
    return cmd


def _read_csv_rows(path: Path) -> Tuple[List[str], List[Dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for r in reader]
        return list(reader.fieldnames or []), rows


def _write_csv_rows(path: Path, fieldnames: List[str], rows: List[Dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "buy", "up"}


def _rerank_accepted(rows: List[Dict]) -> None:
    accepted = []
    for idx, row in enumerate(rows):
        if str(row.get("status") or "") == "accepted":
            try:
                conf = float(row.get("confidence") or 0.0)
            except Exception:
                conf = 0.0
            accepted.append((idx, conf))
        row["rank_by_confidence"] = ""
    accepted.sort(key=lambda item: (-item[1], item[0]))
    for rank, (idx, _) in enumerate(accepted, start=1):
        rows[idx]["rank_by_confidence"] = str(rank)


def _sort_prediction_rows(rows: List[Dict]) -> List[Dict]:
    def rank_key(row: Dict):
        raw_rank = row.get("rank_by_confidence")
        if raw_rank is None or str(raw_rank).strip() == "":
            return (1, 0.0, "")
        try:
            return (0, float(raw_rank), "")
        except Exception:
            return (0, 0.0, str(raw_rank))

    return sorted(
        rows,
        key=lambda row: (
            str(row.get("status") or ""),
            rank_key(row),
            str(row.get("code") or ""),
        ),
    )


def _status_counts(rows: List[Dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "")
        out[status] = out.get(status, 0) + 1
    return out


def _derived_run_id(base_meta: Dict, config: Dict) -> str:
    target = str(base_meta.get("target_date") or "")
    for label, value in (("as_of_date", config.get("as_of_date")), ("target_date", target)):
        text = str(value or "").strip()
        if not text:
            continue
        try:
            _parse_date(text)
        except ValueError:
            raise RuntimeError(f"invalid {label} for derived run id: {text!r}")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ma = int(config.get("market_filter_ma") or 0)
    return (
        f"mainboard_chan_bsp_{base_meta.get('chan', {}).get('frequency', '1d')}_"
        f"{config.get('as_of_date')}_to_{target}_{stamp}_derived_ma{ma}"
    )


def _derive_market_filtered_run(base_run_dir: Path, config: Dict, args, matrix_dir: Path) -> Path:
    meta_path = base_run_dir / "metadata.json"
    pred_path = base_run_dir / "predictions.csv"
    if not meta_path.exists() or not pred_path.exists():
        raise RuntimeError(f"base run is incomplete: {base_run_dir}")

    base_meta = _load_json(meta_path)
    as_of = _parse_date(config.get("as_of_date"))
    if as_of is None:
        raise RuntimeError("derived market run requires as_of_date")

    market_args = SimpleNamespace(
        market_filter_enabled=True,
        market_filter_index=str(config.get("market_filter_index") or getattr(args, "market_filter_index", "sh000001") or "sh000001"),
        market_filter_ma=int(config.get("market_filter_ma") or 20),
        market_filter_data_fail_mode=str(getattr(args, "market_filter_data_fail_mode", "allow") or "allow"),
    )
    market_state = _market_trend_filter_state(market_args, as_of)

    fieldnames, rows = _read_csv_rows(pred_path)
    if "market_filter_reason" not in fieldnames:
        fieldnames.append("market_filter_reason")
    if "rank_by_confidence" not in fieldnames:
        fieldnames.append("rank_by_confidence")

    if bool(market_state.get("enabled")) and not bool(market_state.get("passed")):
        reason = str(market_state.get("reason") or "")
        for row in rows:
            if str(row.get("status") or "") != "accepted":
                continue
            if not _truthy(row.get("raw_signal_is_buy")):
                continue
            row["status"] = "filtered_market_trend"
            row["market_filter_reason"] = reason
            row["pred_up"] = ""
            row["pred_direction"] = ""
    _rerank_accepted(rows)
    rows = _sort_prediction_rows(rows)

    run_id = _derived_run_id(base_meta, config)
    run_dir = matrix_dir / run_id
    suffix = 2
    while run_dir.exists():
        run_dir = matrix_dir / f"{run_id}_{suffix}"
        suffix += 1
    if matrix_dir.resolve() not in run_dir.resolve().parents:
        raise RuntimeError(f"derived run_dir escapes matrix_dir: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    new_pred_path = run_dir / "predictions.csv"
    new_meta_path = run_dir / "metadata.json"
    _write_csv_rows(new_pred_path, fieldnames, rows)

    status_counts = _status_counts(rows)
    accepted_count = int(status_counts.get("accepted", 0))
    meta = dict(base_meta)
    meta["run_id"] = run_dir.name
    meta["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    meta["market_filter"] = market_state
    meta["derived_from"] = {
        "run_id": base_meta.get("run_id"),
        "run_dir": str(base_run_dir),
        "method": "post_model_market_filter",
    }
    pool = dict(meta.get("pool") if isinstance(meta.get("pool"), dict) else {})
    pool["accepted_count"] = accepted_count
    pool["status_counts"] = status_counts
    meta["pool"] = pool
    files = dict(meta.get("files") if isinstance(meta.get("files"), dict) else {})
    files["predictions_csv"] = str(new_pred_path)
    files["metadata_json"] = str(new_meta_path)
    meta["files"] = files
    notes = list(meta.get("notes") or [])
    notes.append("This run was derived from an unfiltered model snapshot by applying the market trend filter after scoring.")
    meta["notes"] = notes
    new_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def _run_snapshot_config(config: Dict, args, matrix_dir: Path) -> Dict:
    cmd = _snapshot_cmd(config, args, matrix_dir)
    before = set(_run_dirs(matrix_dir))
    started = time.time()
    ret = snapshot_main(cmd)
    after = set(_run_dirs(matrix_dir))
    new_dirs = sorted(list(after - before), key=lambda p: p.stat().st_mtime)
    run_dir = new_dirs[-1] if new_dirs else None
    return {
        "config": dict(config),
        "return_code": int(ret),
        "elapsed_sec": round(time.time() - started, 3),
        "run_dir": str(run_dir) if run_dir else None,
        "execution_mode": "full",
    }


def _group_grid_by_base(grid: List[Dict]) -> List[List[Dict]]:
    groups: List[List[Dict]] = []
    index: Dict[Tuple, int] = {}
    for config in grid:
        key = _base_config_key(config)
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(config)
    return groups


def _execution_status(summary: Dict) -> str:
    failed = int(summary.get("failed_count") or 0)
    skipped = int(summary.get("skipped_count") or 0)
    skipped_existing = int(summary.get("skipped_existing_count") or 0)
    dry_run = int(summary.get("dry_run_count") or 0)
    completed = int(summary.get("completed_count") or summary.get("evaluated_count") or 0)
    effective_completed = completed + skipped_existing + dry_run
    total = int(summary.get("total_count") or summary.get("total_runs") or 0)
    if failed > 0 and effective_completed <= 0:
        return "error"
    if failed > 0 or skipped > 0:
        return "partial"
    if total > 0 and effective_completed >= total:
        return "success"
    if effective_completed > 0:
        return "partial"
    return "empty"


def _write_execution_status(matrix_dir: Path, status: Dict) -> None:
    try:
        status["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        (matrix_dir / "matrix_execution_status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def run_predictions(args, matrix_dir: Path, grid: List[Dict]) -> Dict:
    manifest_path = matrix_dir / "matrix_manifest.json"
    now = dt.datetime.now().isoformat(timespec="seconds")
    existing_manifest = _load_json(manifest_path) if bool(args.resume) and manifest_path.exists() else {}
    manifest = dict(existing_manifest) if isinstance(existing_manifest, dict) else {}
    prior_runs = manifest.get("runs") if isinstance(manifest.get("runs"), list) else []
    manifest.update(
        {
            "created_at": manifest.get("created_at") or now,
            "matrix_dir": str(matrix_dir),
            "config_count": int(len(grid)),
            "runs": list(prior_runs),
            "optimization": {
                "market_filter_derivation": not bool(getattr(args, "no_market_filter_derivation", False)),
                "description": "Market MA variants are derived from the no-market snapshot when available.",
            },
        }
    )
    if bool(args.resume) and existing_manifest:
        manifest["resumed_at"] = now
    summary = {
        "stage": "prediction",
        "total_count": int(len(grid)),
        "completed_count": 0,
        "failed_count": 0,
        "skipped_existing_count": 0,
        "dry_run_count": 0,
        "full_run_count": 0,
        "derived_count": 0,
        "failed_runs": [],
    }

    def flush_manifest() -> None:
        summary["status"] = _execution_status(summary)
        manifest["prediction_summary"] = dict(summary)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    existing_run_map = _discover_existing_run_map(matrix_dir) if args.resume else {}
    processed_count = 0
    groups = _group_grid_by_base(grid)
    for group in groups:
        base_config = next((c for c in group if not bool(c.get("market_filter_enabled"))), None)
        can_derive_market = bool(base_config is not None and not getattr(args, "no_market_filter_derivation", False))

        if not can_derive_market:
            for config in group:
                processed_count += 1
                key = _config_key_tuple(config)
                if args.resume and key in existing_run_map:
                    print(f"[{processed_count}/{len(grid)}] skip existing {config}")
                    summary["skipped_existing_count"] += 1
                    flush_manifest()
                    continue
                cmd = _snapshot_cmd(config, args, matrix_dir)
                print(f"[{processed_count}/{len(grid)}] run {config}")
                if args.dry_run:
                    print("  " + " ".join(cmd))
                    summary["dry_run_count"] += 1
                    flush_manifest()
                    continue
                run_record = _run_snapshot_config(config, args, matrix_dir)
                summary["full_run_count"] += 1
                if int(run_record.get("return_code") or 0) == 0:
                    summary["completed_count"] += 1
                    if run_record.get("run_dir"):
                        existing_run_map[key] = Path(str(run_record["run_dir"]))
                else:
                    summary["failed_count"] += 1
                    summary["failed_runs"].append(run_record)
                manifest["runs"].append(run_record)
                flush_manifest()
            continue

        base_key = _config_key_tuple(base_config)
        base_run_dir = existing_run_map.get(base_key) if args.resume else None
        processed_count += 1
        if base_run_dir is not None:
            print(f"[{processed_count}/{len(grid)}] skip existing base {base_config}")
            summary["skipped_existing_count"] += 1
            flush_manifest()
        else:
            cmd = _snapshot_cmd(base_config, args, matrix_dir)
            print(f"[{processed_count}/{len(grid)}] run base {base_config}")
            if args.dry_run:
                print("  " + " ".join(cmd))
                summary["dry_run_count"] += 1
                flush_manifest()
            else:
                run_record = _run_snapshot_config(base_config, args, matrix_dir)
                summary["full_run_count"] += 1
                if int(run_record.get("return_code") or 0) == 0:
                    summary["completed_count"] += 1
                    if run_record.get("run_dir"):
                        base_run_dir = Path(str(run_record["run_dir"]))
                        existing_run_map[base_key] = base_run_dir
                else:
                    summary["failed_count"] += 1
                    summary["failed_runs"].append(run_record)
                manifest["runs"].append(run_record)
                flush_manifest()

        for config in group:
            if config is base_config:
                continue
            processed_count += 1
            key = _config_key_tuple(config)
            if args.resume and key in existing_run_map:
                print(f"[{processed_count}/{len(grid)}] skip existing derived {config}")
                summary["skipped_existing_count"] += 1
                flush_manifest()
                continue
            if not bool(config.get("market_filter_enabled")):
                continue
            if args.dry_run:
                print(f"[{processed_count}/{len(grid)}] derive market filter {config}")
                summary["dry_run_count"] += 1
                flush_manifest()
                continue
            if base_run_dir is None:
                print(f"[{processed_count}/{len(grid)}] base unavailable, run full {config}")
                run_record = _run_snapshot_config(config, args, matrix_dir)
                summary["full_run_count"] += 1
            else:
                print(f"[{processed_count}/{len(grid)}] derive market filter {config}")
                started = time.time()
                try:
                    derived_dir = _derive_market_filtered_run(base_run_dir, config, args, matrix_dir)
                    run_record = {
                        "config": dict(config),
                        "return_code": 0,
                        "elapsed_sec": round(time.time() - started, 3),
                        "run_dir": str(derived_dir),
                        "execution_mode": "derived_market_filter",
                        "derived_from": str(base_run_dir),
                    }
                    existing_run_map[key] = derived_dir
                    summary["derived_count"] += 1
                except Exception as e:
                    run_record = {
                        "config": dict(config),
                        "return_code": 1,
                        "elapsed_sec": round(time.time() - started, 3),
                        "run_dir": None,
                        "execution_mode": "derived_market_filter",
                        "derived_from": str(base_run_dir),
                        "error": str(e),
                    }
            if int(run_record.get("return_code") or 0) == 0:
                summary["completed_count"] += 1
            else:
                summary["failed_count"] += 1
                summary["failed_runs"].append(run_record)
            manifest["runs"].append(run_record)
            flush_manifest()
    if args.dry_run:
        print(f"dry-run config_count={len(grid)}")
    else:
        print(f"matrix predictions saved under: {matrix_dir}")
    flush_manifest()
    return summary


def evaluate_runs(args, matrix_dir: Path) -> Dict:
    runs = _run_dirs(matrix_dir)
    summary = {
        "stage": "evaluation",
        "total_runs": int(len(runs)),
        "evaluated_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "skipped_runs": [],
        "failed_runs": [],
        "target_date": getattr(args, "target_date", None),
        "eval_lookahead": getattr(args, "eval_lookahead", None),
    }
    if not runs:
        print(f"no run directories under {matrix_dir}")
        summary["status"] = "empty"
        _write_execution_status(matrix_dir, {"evaluation_summary": summary, "status": "empty"})
        return summary
    for idx, run_dir in enumerate(runs, start=1):
        print(f"[eval {idx}/{len(runs)}] {run_dir.name}")
        eval_args = [
            "evaluate",
            "--run-dir", str(run_dir),
            "--output-dir", str(matrix_dir),
        ]
        if args.target_date:
            eval_args.extend(["--target-date", args.target_date])
        if args.eval_lookahead:
            eval_args.extend(["--eval-lookahead", str(args.eval_lookahead)])
        if args.dry_run:
            print("  " + " ".join(eval_args))
            summary["skipped_count"] += 1
            summary["skipped_runs"].append({"run_dir": str(run_dir), "return_code": None, "reason": "dry_run"})
            continue
        ret = snapshot_main(eval_args)
        record = {"run_dir": str(run_dir), "return_code": int(ret)}
        if int(ret) == 0:
            summary["evaluated_count"] += 1
        elif int(ret) == 2:
            summary["skipped_count"] += 1
            summary["skipped_runs"].append(record)
            print(f"  evaluation skipped/failed return_code={ret}")
        else:
            summary["failed_count"] += 1
            summary["failed_runs"].append(record)
            print(f"  evaluation skipped/failed return_code={ret}")
    summary["status"] = _execution_status(summary)
    _write_execution_status(matrix_dir, {"evaluation_summary": summary, "status": summary["status"]})
    return summary


def _latest_eval_summary(run_dir: Path) -> Dict:
    items = list(run_dir.glob("evaluation_summary_*.json"))
    if not items:
        return {}
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return _load_json(items[0])


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def build_result_rows(matrix_dir: Path) -> List[Dict]:
    rows = []
    for run_dir in _run_dirs(matrix_dir):
        meta = _load_json(run_dir / "metadata.json")
        ev = _latest_eval_summary(run_dir)
        chan = meta.get("chan") if isinstance(meta.get("chan"), dict) else {}
        filt = meta.get("filter") if isinstance(meta.get("filter"), dict) else {}
        market = meta.get("market_filter") if isinstance(meta.get("market_filter"), dict) else {}
        pool = meta.get("pool") if isinstance(meta.get("pool"), dict) else {}
        filt_acc = filt.get("accuracy") if isinstance(filt.get("accuracy"), dict) else {}
        accepted_count = int(pool.get("accepted_count") or 0)
        stock_count = int(pool.get("stock_count") or pool.get("prediction_count") or 0)
        coverage = (accepted_count / stock_count) if stock_count > 0 else None
        eval_accuracy = _safe_float(ev.get("accuracy")) if ev else None
        baseline = _safe_float(ev.get("majority_baseline_accuracy")) if ev else None
        rows.append(
            {
                "run_dir": str(run_dir),
                "as_of_date": meta.get("as_of_date"),
                "target_date": (ev.get("target_date") if ev else meta.get("target_date")),
                "side_filter": chan.get("side_filter"),
                "rolling_sample_mode": chan.get("rolling_sample_mode"),
                "profit_lookahead": filt.get("profit_lookahead") or meta.get("eval_lookahead"),
                "market_filter_enabled": market.get("enabled"),
                "market_filter_index": market.get("index_code"),
                "market_filter_ma": market.get("ma_window"),
                "market_filter_passed": market.get("passed"),
                "accepted_count": accepted_count,
                "stock_count": stock_count,
                "coverage": coverage,
                "filter_sample_count": filt.get("merged_sample_count"),
                "filter_accuracy": filt_acc.get("accuracy"),
                "filter_auc": filt_acc.get("roc_auc"),
                "filter_brier": filt_acc.get("brier_score"),
                "eval_status": "evaluated" if ev else "pending",
                "matched_count": ev.get("matched_count") if ev else None,
                "eval_accuracy": eval_accuracy,
                "majority_baseline_accuracy": baseline,
                "accuracy_minus_baseline": (eval_accuracy - baseline) if eval_accuracy is not None and baseline is not None else None,
                "eval_auc": ev.get("roc_auc") if ev else None,
                "eval_brier": ev.get("brier_score") if ev else None,
                "actual_up_rate": ev.get("actual_up_rate") if ev else None,
                "predicted_up_rate": ev.get("predicted_up_rate") if ev else None,
                "up_prediction_accuracy": ev.get("up_prediction_accuracy") if ev else None,
                "down_prediction_accuracy": ev.get("down_prediction_accuracy") if ev else None,
                "trade_cost": ev.get("trade_cost") if ev else None,
                "avg_net_return": ev.get("avg_net_return") if ev else None,
                "median_net_return": ev.get("median_net_return") if ev else None,
                "total_net_return": ev.get("total_net_return") if ev else None,
                "win_rate_return": ev.get("win_rate_return") if ev else None,
                "gross_profit_sum": ev.get("gross_profit_sum") if ev else None,
                "gross_loss_sum": ev.get("gross_loss_sum") if ev else None,
                "profit_factor": ev.get("profit_factor") if ev else None,
                "profit_factor_is_infinite": bool(ev.get("profit_factor_is_infinite")) if ev else False,
                "max_drawdown": ev.get("max_drawdown") if ev else None,
            }
        )
    return rows


def write_csv(path: Path, rows: List[Dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value, digits: int = 4) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def _fmt_pct(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def _fmt_profit_factor(value, is_infinite: bool = False) -> str:
    if is_infinite:
        return "inf"
    return _fmt(value, 3)


def _weighted_metric(items: List[Dict], key: str, weight_key: str = "matched_count") -> Optional[float]:
    num = 0.0
    den = 0.0
    for row in items or []:
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


def _profit_factor_from_items(items: List[Dict]) -> tuple:
    pos = 0.0
    loss = 0.0
    saw_inf = False
    for row in items or []:
        pos += float(_safe_float(row.get("gross_profit_sum")) or 0.0)
        loss += float(_safe_float(row.get("gross_loss_sum")) or 0.0)
        if bool(row.get("profit_factor_is_infinite")):
            saw_inf = True
    if loss <= 0:
        return (None, bool(pos > 0 or saw_inf))
    return (pos / loss, False)


def _max_drawdown_from_rows(items: List[Dict]) -> Optional[float]:
    dated = []
    for row in items or []:
        ret = _safe_float(row.get("avg_net_return"))
        if ret is None:
            continue
        dated.append((str(row.get("as_of_date") or ""), float(ret)))
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


def build_group_summary(rows: List[Dict]) -> List[Dict]:
    groups: Dict[tuple, List[Dict]] = {}
    for row in rows:
        key = (
            row.get("side_filter"),
            row.get("rolling_sample_mode"),
            row.get("profit_lookahead"),
            bool(row.get("market_filter_enabled")),
            row.get("market_filter_ma") if bool(row.get("market_filter_enabled")) else 0,
        )
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in groups.items():
        evaluated = [r for r in items if r.get("eval_accuracy") is not None and r.get("matched_count") not in [None, ""]]
        total_matched = sum(int(r.get("matched_count") or 0) for r in evaluated)
        if total_matched > 0:
            weighted_acc = sum(float(r.get("eval_accuracy") or 0.0) * int(r.get("matched_count") or 0) for r in evaluated) / total_matched
            weighted_base = sum(float(r.get("majority_baseline_accuracy") or 0.0) * int(r.get("matched_count") or 0) for r in evaluated) / total_matched
        else:
            weighted_acc = None
            weighted_base = None
        coverages = [float(r.get("coverage")) for r in items if r.get("coverage") is not None]
        pf, pf_inf = _profit_factor_from_items(evaluated)
        out.append(
            {
                "side_filter": key[0],
                "rolling_sample_mode": key[1],
                "profit_lookahead": key[2],
                "market_filter_enabled": key[3],
                "market_filter_ma": key[4],
                "run_count": len(items),
                "evaluated_count": len(evaluated),
                "total_matched": total_matched,
                "weighted_accuracy": weighted_acc,
                "weighted_baseline": weighted_base,
                "weighted_accuracy_minus_baseline": (weighted_acc - weighted_base) if weighted_acc is not None and weighted_base is not None else None,
                "avg_coverage": (sum(coverages) / len(coverages)) if coverages else None,
                "weighted_avg_net_return": _weighted_metric(evaluated, "avg_net_return"),
                "total_net_return": sum(float(_safe_float(r.get("total_net_return")) or 0.0) for r in evaluated) if evaluated else None,
                "win_rate_return": _weighted_metric(evaluated, "win_rate_return"),
                "gross_profit_sum": sum(float(_safe_float(r.get("gross_profit_sum")) or 0.0) for r in evaluated) if evaluated else 0.0,
                "gross_loss_sum": sum(float(_safe_float(r.get("gross_loss_sum")) or 0.0) for r in evaluated) if evaluated else 0.0,
                "profit_factor": pf,
                "profit_factor_is_infinite": bool(pf_inf),
                "max_drawdown": _max_drawdown_from_rows(evaluated),
            }
        )
    out.sort(
        key=lambda r: (
            r.get("weighted_accuracy_minus_baseline") if r.get("weighted_accuracy_minus_baseline") is not None else -999,
            r.get("weighted_accuracy") if r.get("weighted_accuracy") is not None else -999,
            r.get("total_matched") or 0,
        ),
        reverse=True,
    )
    return out


def write_report(matrix_dir: Path, rows: List[Dict], groups: List[Dict]) -> None:
    manifest = _load_json(matrix_dir / "matrix_manifest.json")
    pred_summary = manifest.get("prediction_summary") if isinstance(manifest.get("prediction_summary"), dict) else {}
    manifest_runs = manifest.get("runs") if isinstance(manifest.get("runs"), list) else []
    manifest_full_runs = sum(1 for r in manifest_runs if isinstance(r, dict) and str(r.get("execution_mode") or "full") == "full")
    manifest_derived_runs = sum(1 for r in manifest_runs if isinstance(r, dict) and str(r.get("execution_mode") or "") == "derived_market_filter")
    report = []
    report.append("# Experiment Matrix Report")
    report.append(f"Generated on: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Matrix Directory: `{matrix_dir.name}`\n")
    report.append("## Summary")
    report.append(f"- Runs: `{len(rows)}`")
    report.append(f"- Evaluated Runs: `{sum(1 for r in rows if r.get('eval_status') == 'evaluated')}`")
    report.append(f"- Config Groups: `{len(groups)}`")
    if pred_summary or manifest_runs:
        report.append(f"- Full Prediction Runs: `{manifest_full_runs}`")
        report.append(f"- Derived Market Runs: `{manifest_derived_runs}`")
    report.append("")
    report.append("## Top Configurations")
    report.append("| Side | Rolling | Lookahead | Market MA | Runs | Evaluated | Trades | Accuracy | Baseline | Delta | Avg Net | PF | Max DD | Coverage |")
    report.append("| :--- | :--- | ---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in groups[:20]:
        market = f"MA{row.get('market_filter_ma')}" if row.get("market_filter_enabled") else "off"
        report.append(
            "| "
            f"{row.get('side_filter')} | {row.get('rolling_sample_mode')} | {row.get('profit_lookahead')} | {market} | "
            f"{row.get('run_count')} | {row.get('evaluated_count')} | {row.get('total_matched')} | "
            f"{_fmt_pct(row.get('weighted_accuracy'))} | {_fmt_pct(row.get('weighted_baseline'))} | "
            f"{_fmt_pct(row.get('weighted_accuracy_minus_baseline'))} | {_fmt_pct(row.get('weighted_avg_net_return'))} | "
            f"{_fmt_profit_factor(row.get('profit_factor'), bool(row.get('profit_factor_is_infinite')))} | "
            f"{_fmt_pct(row.get('max_drawdown'))} | {_fmt_pct(row.get('avg_coverage'))} |"
        )
    report.append("\n## Notes")
    report.append("- `Delta` is weighted evaluation accuracy minus weighted majority-class baseline.")
    report.append("- `Avg Net`, `PF`, and `Max DD` use accepted signal returns after configured trade cost.")
    report.append("- Coverage is accepted predictions divided by snapshot universe size.")
    report.append("- Pending runs usually mean target-day data is not loaded yet or no accepted predictions exist.")
    content = "\n".join(report)
    (matrix_dir / "matrix_report.md").write_text(content, encoding="utf-8")
    print(content)
    print(f"\nSaved matrix report: {matrix_dir / 'matrix_report.md'}")


def report_matrix(matrix_dir: Path) -> None:
    rows = build_result_rows(matrix_dir)
    groups = build_group_summary(rows)
    write_csv(matrix_dir / "matrix_results.csv", rows)
    write_csv(matrix_dir / "matrix_group_summary.csv", groups)
    write_report(matrix_dir, rows, groups)


def run_matrix(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run and summarize cross-date Chan BSP experiment matrices.")
    parser.add_argument("--dates", default="", help="comma-separated as-of dates")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-dates", type=int, default=0)
    parser.add_argument("--rolling-modes", default="none,bsp")
    parser.add_argument("--side-filters", default="buy,sell,both")
    parser.add_argument("--lookaheads", default="1,3")
    parser.add_argument("--market-mas", default="20")
    parser.add_argument("--include-no-market-filter", action="store_true")
    parser.add_argument("--frequency", default="1d")
    parser.add_argument("--data-length-days", type=int, default=730)
    parser.add_argument("--feature-preset", default="purify_compact")
    parser.add_argument("--min-score", type=float, default=0.55)
    parser.add_argument("--trade-cost", type=float, default=0.001)
    parser.add_argument("--max-signal-age-bars", type=int, default=3)
    parser.add_argument("--per-code-sample-cap", type=int, default=500)
    parser.add_argument("--min-total-samples", type=int, default=2000)
    parser.add_argument("--min-train", type=int, default=1000)
    parser.add_argument("--min-val", type=int, default=300)
    parser.add_argument("--min-test", type=int, default=1000)
    parser.add_argument("--backtest-mode", default="time_split")
    parser.add_argument("--market-filter-index", default="sh000001")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--n-jobs", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=200)
    parser.add_argument("--no-rank-transform", action="store_true")
    parser.add_argument("--no-enforce-t1", action="store_true")
    parser.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions")
    parser.add_argument("--matrix-dir", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-market-filter-derivation", action="store_true", help="disable deriving MA variants from no-market snapshots")
    parser.add_argument("--evaluate-after-predict", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--target-date", default=None, help="manual evaluation target date override")
    parser.add_argument("--eval-lookahead", type=int, default=None)
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    if args.evaluate_only or args.report_only:
        matrix_dir = _matrix_dir_for_read(args)
    else:
        matrix_dir = _matrix_dir_for_run(args)

    if args.report_only:
        report_matrix(matrix_dir)
        _write_execution_status(matrix_dir, {"status": "success", "stage": "report"})
        return 0

    if args.evaluate_only:
        eval_summary = evaluate_runs(args, matrix_dir)
        report_matrix(matrix_dir)
        status = _execution_status(eval_summary)
        _write_execution_status(matrix_dir, {"status": status, "evaluation_summary": eval_summary})
        return 0 if status == "success" else 2

    client = None
    if not args.dates:
        client = CClickHouseAPI.get_client()
    if client is None and not args.dates:
        raise RuntimeError("ClickHouse is not available")
    grid = _build_grid(args, client)
    print(f"matrix_dir={matrix_dir}")
    print(f"config_count={len(grid)}")
    pred_summary = run_predictions(args, matrix_dir, grid)
    eval_summary = None
    if args.evaluate_after_predict:
        eval_summary = evaluate_runs(args, matrix_dir)
        report_matrix(matrix_dir)
    status = _execution_status(pred_summary)
    if eval_summary is not None:
        eval_status = _execution_status(eval_summary)
        if status == "error" or eval_status == "error":
            status = "error"
        elif status == "partial" or eval_status == "partial":
            status = "partial"
        elif status == "empty":
            status = eval_status
    _write_execution_status(
        matrix_dir,
        {
            "status": status,
            "prediction_summary": pred_summary,
            **({"evaluation_summary": eval_summary} if eval_summary is not None else {}),
        },
    )
    return 0 if status == "success" else 2


if __name__ == "__main__":
    raise SystemExit(run_matrix())
