import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import the main entry points of the snapshot tool
from backend.tools.daily_direction_snapshot import main as snapshot_main


def _resolve_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return ROOT / p


def _latest_suite_dir(output_dir: str) -> Optional[Path]:
    base = _resolve_path(output_dir)
    if not base.exists():
        return None
    suites = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("suite_")]
    if not suites:
        return None
    suites.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return suites[0]


def _load_run_side(run_dir: Path) -> Optional[str]:
    meta_path = run_dir / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        side = str(((meta.get("chan") or {}).get("side_filter")) or "").strip().lower()
        return side if side in {"buy", "sell", "both"} else None
    except Exception:
        return None


def _find_suite_runs(suite_dir: Path) -> dict:
    out = {}
    for child in sorted(suite_dir.glob("mainboard_chan_bsp_*")):
        if not child.is_dir():
            continue
        side = _load_run_side(child)
        if not side:
            continue
        prev = out.get(side)
        if prev is None or child.stat().st_mtime > prev.stat().st_mtime:
            out[side] = child
    return out


def run_experiment_suite(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrate Buy/Sell/Both comparison experiments and generate a unified report."
    )
    # Orchestration arguments
    parser.add_argument("--as-of", default=None, help="as-of date")
    parser.add_argument("--target-date", default=None, help="override target date")
    parser.add_argument("--frequency", default="1d", help="Chan level, e.g. 1d or 5m")
    parser.add_argument("--rolling-sample-mode", default="none", help="rolling sample mode (none/bsp/every_bar)")
    parser.add_argument("--profit-lookahead", type=int, default=3, help="profit lookahead N bars")
    parser.add_argument("--min-score", type=float, default=0.55, help="confidence threshold")
    parser.add_argument("--limit", type=int, default=0, help="optional stock limit for testing")
    parser.add_argument("--market-filter-enabled", action="store_true", default=True, help="enable market filter")
    parser.add_argument("--no-market-filter", action="store_false", dest="market_filter_enabled")
    parser.add_argument("--market-filter-index", default="sh000001", help="market filter index")
    parser.add_argument("--market-filter-ma", type=int, default=20, help="market filter MA window")
    parser.add_argument("--output-dir", default="backend/model_cache/daily_direction_predictions", help="predictions root")
    parser.add_argument("--suite-dir", default=None, help="existing suite directory for evaluation")
    parser.add_argument("--evaluate-suite", action="store_true", help="run evaluation on the generated runs")
    
    args = parser.parse_args(argv)

    scenarios = ["buy", "sell", "both"]
    if args.evaluate_suite:
        suite_dir = _resolve_path(args.suite_dir) if args.suite_dir else _latest_suite_dir(args.output_dir)
        if suite_dir is None or not suite_dir.exists():
            raise SystemExit(f"No suite directory found under {args.output_dir}")
        run_dirs = _find_suite_runs(suite_dir)
        missing = [s for s in scenarios if s not in run_dirs]
        if missing:
            raise SystemExit(f"Suite {suite_dir} is missing scenario run(s): {', '.join(missing)}")
        print(f"======================================================================")
        print(f"Evaluating Experiment Suite: {suite_dir.name}")
        print(f"======================================================================\n")
        evaluate_suite(suite_dir, run_dirs, args)
        return 0

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_dir = _resolve_path(args.output_dir) / f"suite_{timestamp}"
    suite_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"======================================================================")
    print(f"Starting Experiment Suite: {suite_dir.name}")
    print(f"As-Of Date: {args.as_of or 'latest'}")
    print(f"Lookahead: {args.profit_lookahead} bars, Rolling Mode: {args.rolling_sample_mode}")
    print(f"======================================================================\n")

    run_dirs = {}
    for side in scenarios:
        print(f"---> Running prediction for Side Filter: {side.upper()}...")
        cmd_args = [
            "predict-chan-bsp",
            "--frequency", args.frequency,
            "--rolling-sample-mode", args.rolling_sample_mode,
            "--profit-lookahead", str(args.profit_lookahead),
            "--min-score", str(args.min_score),
            "--side-filter", side,
            "--output-dir", str(suite_dir),
            "--limit", str(args.limit),
        ]
        if args.as_of:
            cmd_args.extend(["--as-of", args.as_of])
        if args.target_date:
            cmd_args.extend(["--target-date", args.target_date])
        if args.market_filter_enabled:
            cmd_args.extend([
                "--market-filter-enabled",
                "--market-filter-index", args.market_filter_index,
                "--market-filter-ma", str(args.market_filter_ma)
            ])
        else:
            cmd_args.append("--no-market-filter")

        t_start = time.time()
        ret = snapshot_main(cmd_args)
        t_elapsed = time.time() - t_start
        print(f"---> Finished prediction for Side Filter: {side.upper()} in {t_elapsed:.1f}s. Return Code: {ret}\n")
        
        # Locate the generated run directory
        # Subdirectories will match: mainboard_chan_bsp_{freq}_{as_of}_to_{target}_{timestamp}
        subdirs = sorted(suite_dir.glob("mainboard_chan_bsp_*"))
        if subdirs:
            # The latest directory created for this run
            for sd in reversed(subdirs):
                if sd not in run_dirs.values():
                    run_dirs[side] = sd
                    break

    print(f"Suite prediction snapshots successfully generated in: {suite_dir}")
    return 0


def evaluate_suite(suite_dir: Path, run_dirs: dict, args) -> None:
    eval_results = {}
    for side, r_dir in run_dirs.items():
        print(f"---> Evaluating Run: {r_dir.name} ({side.upper()})...")
        eval_args = [
            "evaluate",
            "--run-dir", str(r_dir),
            "--output-dir", str(suite_dir),
        ]
        if args.target_date:
            eval_args.extend(["--target-date", args.target_date])

        ret = snapshot_main(eval_args)
        if ret == 0:
            eval_jsons = list(r_dir.glob("evaluation_summary_*.json"))
            if eval_jsons:
                eval_jsons.sort(key=lambda p: p.stat().st_mtime)
                with eval_jsons[-1].open("r", encoding="utf-8") as f:
                    eval_results[side] = json.load(f)
        else:
            print(f"[Warning] Evaluation failed or data not ready for {side.upper()} (return code {ret}).")

    if eval_results:
        generate_markdown_report(suite_dir, eval_results)
    else:
        print("No evaluation summaries could be loaded (target data may be unavailable, or the suite has no accepted predictions).")


def generate_markdown_report(suite_dir: Path, results: dict):
    lines = []
    lines.append(f"# Experiment Suite Comparison Report")
    lines.append(f"Generated on: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Suite Directory: `{suite_dir.name}`\n")
    
    # Common Meta
    any_res = list(results.values())[0]
    lines.append(f"### Metadata")
    lines.append(f"- **As-Of Date**: `{any_res.get('as_of_date')}`")
    lines.append(f"- **Target Date**: `{any_res.get('target_date')}`")
    lines.append(f"- **Lookahead**: `{any_res.get('eval_lookahead', 1)}` bars\n")

    lines.append(f"### Performance Comparison Table")
    lines.append(f"| Metric / Scenario | BUY Only Strategy | SELL Only Strategy | BOTH Baseline Strategy |")
    lines.append(f"| :--- | :---: | :---: | :---: |")
    
    # Matched count
    lines.append(f"| **Matched Count** (Trades) | {results.get('buy', {}).get('matched_count', '-')} | {results.get('sell', {}).get('matched_count', '-')} | {results.get('both', {}).get('matched_count', '-')} |")
    
    # Model AUC
    lines.append(f"| **Evaluation ROC-AUC** | {format_val(results.get('buy', {}).get('roc_auc'))} | {format_val(results.get('sell', {}).get('roc_auc'))} | {format_val(results.get('both', {}).get('roc_auc'))} |")
    
    # Model Brier Score
    lines.append(f"| **Evaluation Brier Score** | {format_val(results.get('buy', {}).get('brier_score'))} | {format_val(results.get('sell', {}).get('brier_score'))} | {format_val(results.get('both', {}).get('brier_score'))} |")
    
    # Overall Accuracy
    lines.append(f"| **Overall Evaluation Accuracy** | {format_pct(results.get('buy', {}).get('accuracy'))} | {format_pct(results.get('sell', {}).get('accuracy'))} | {format_pct(results.get('both', {}).get('accuracy'))} |")
    
    # Buy Direction Accuracy
    lines.append(f"| **Buy Signal Accuracy** | {format_pct(results.get('buy', {}).get('up_prediction_accuracy'))} | {format_pct(results.get('sell', {}).get('up_prediction_accuracy'))} | {format_pct(results.get('both', {}).get('up_prediction_accuracy'))} |")
    
    # Sell Direction Accuracy
    lines.append(f"| **Sell Signal Accuracy** | {format_pct(results.get('buy', {}).get('down_prediction_accuracy'))} | {format_pct(results.get('sell', {}).get('down_prediction_accuracy'))} | {format_pct(results.get('both', {}).get('down_prediction_accuracy'))} |")
    
    lines.append("\n### Analysis Notes")
    lines.append("- **ROC-AUC**: Represents the model's ranking ability. Higher is better (0.50 is random noise).")
    lines.append("- **Brier Score**: Measures probability calibration. Lower is better (<= 0.25 is usable).")
    lines.append("- **Buy/Sell Accuracy**: Evaluates how many predictions of 'up' (for buys) and 'down' (for sells) matched the actual price movement on the target date.\n")
    
    report_content = "\n".join(lines)
    report_path = suite_dir / "comparison_report.md"
    report_path.write_text(report_content, encoding="utf-8")
    
    print("\n" + "=" * 70)
    print(report_content)
    print("=" * 70 + "\n")
    print(f"Saved Comparison Report to: {report_path}")


def format_val(val):
    if val is None:
        return "-"
    return f"{float(val):.4f}"


def format_pct(val):
    if val is None:
        return "-"
    return f"{float(val)*100:.2f}%"


if __name__ == "__main__":
    sys.exit(run_experiment_suite())
