import argparse
import datetime
import json
import time
from typing import Optional

import requests

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.live.notifier import send_text_env


def _sh_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return None


def _next_weekday(d: datetime.date) -> datetime.date:
    while d.weekday() >= 5:
        d = d + datetime.timedelta(days=1)
    return d


def _next_cn_step_time(now: datetime.datetime, offset_seconds: int) -> datetime.datetime:
    d = _next_weekday(now.date())
    tz = now.tzinfo
    times = [
        (9, 30),
        (10, 0),
        (10, 30),
        (11, 0),
        (11, 30),
        (13, 0),
        (13, 30),
        (14, 0),
        (14, 30),
        (15, 0),
    ]
    if d != now.date():
        for hh, mm in times:
            return datetime.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz) + datetime.timedelta(seconds=int(offset_seconds))

    cands = []
    for hh, mm in times:
        cands.append(datetime.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz) + datetime.timedelta(seconds=int(offset_seconds)))
    for t in cands:
        if t > now:
            return t

    nd = _next_weekday(d + datetime.timedelta(days=1))
    return datetime.datetime(nd.year, nd.month, nd.day, 9, 30, 0, tzinfo=tz) + datetime.timedelta(seconds=int(offset_seconds))


def _fmt_pct(x: Optional[float]) -> str:
    try:
        if x is None:
            return "-"
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "-"


def _summarize(resp: dict) -> str:
    day = resp.get("day")
    ts = resp.get("ts")
    market_session = resp.get("market_session") or ""
    risk = resp.get("risk") or {}
    level = risk.get("level")
    gap = _fmt_pct(risk.get("gap_open_pct"))
    d1 = _fmt_pct(risk.get("ret1d"))
    intr = risk.get("intraday") or {}
    intr_change = _fmt_pct(intr.get("change"))
    intr_trend = intr.get("trend") or "-"
    reason = risk.get("reason") or "-"

    tgs = resp.get("targets") or []
    codes = [str(t.get("code")) for t in tgs if isinstance(t, dict) and t.get("code")]
    sells = [str(x) for x in (resp.get("sell") or [])]
    buys = [str(x) for x in (resp.get("buy") or [])]
    blocked = resp.get("blocked") or []
    bcnt = len(blocked) if isinstance(blocked, list) else 0
    return f"day={day} ts={ts} session={market_session} risk={level} reason={reason} gap={gap} d1={d1} intr={intr_change} trend={intr_trend} top={codes} sell={sells} buy={buys} blocked={bcnt}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api_base", type=str, default="http://127.0.0.1:8000")
    ap.add_argument("--endpoint", type=str, default="/api/live/paper_trade_step")
    ap.add_argument("--offset_seconds", type=int, default=5)
    ap.add_argument("--timeout_sec", type=float, default=20.0)
    ap.add_argument("--dry_run", type=int, default=0)

    ap.add_argument("--sim_id", type=str, default="default")
    ap.add_argument("--pool_id", type=str, default="index:IDX399673")
    ap.add_argument("--frequency", type=str, default="30m")
    ap.add_argument("--data_src", type=str, default="clickhouse")
    ap.add_argument("--autype", type=str, default="qfq")
    ap.add_argument("--lookback_days", type=int, default=365 * 5)
    ap.add_argument("--top_n", type=int, default=2)
    ap.add_argument("--min_prob", type=float, default=0.25)
    ap.add_argument("--holding_period", type=int, default=3)
    ap.add_argument("--trade_cost", type=float, default=0.0002)
    ap.add_argument("--walk_forward_max_folds", type=int, default=25)
    ap.add_argument("--max_workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)

    ap.add_argument("--risk_filter_enabled", type=int, default=1)
    ap.add_argument("--risk_mode", type=str, default="drop")
    ap.add_argument("--risk_index_code", type=str, default="399006")
    ap.add_argument("--risk_drop_threshold", type=float, default=-0.02)
    ap.add_argument("--risk_flat_drop_threshold", type=float, default=-0.03)
    ap.add_argument("--risk_gap_down_threshold", type=float, default=-0.015)
    ap.add_argument("--risk_intraday_enabled", type=int, default=1)
    ap.add_argument("--risk_intraday_bear_threshold", type=float, default=-0.015)
    ap.add_argument("--risk_intraday_bull_threshold", type=float, default=-0.03)
    ap.add_argument("--risk_trend_ma_window", type=int, default=20)
    ap.add_argument("--risk_data_fail_mode", type=str, default="reduce")
    ap.add_argument("--risk_intraday_data_fail_mode", type=str, default="reduce")
    ap.add_argument("--sell_mode", type=str, default="baseline")
    ap.add_argument("--t1_decision_time", type=str, default="10:00")
    ap.add_argument("--t1_gap_strong_threshold", type=float, default=0.03)
    ap.add_argument("--t1_gap_weak_threshold", type=float, default=-0.02)
    ap.add_argument("--t1_gap_very_strong_threshold", type=float, default=0.05)

    args = ap.parse_args()

    tz = _sh_tz()
    next_earliest_run_time = None
    try:
        while True:
            now = datetime.datetime.now(tz=tz)
            nxt = _next_cn_step_time(now, int(args.offset_seconds))
            if next_earliest_run_time and next_earliest_run_time > nxt:
                nxt = next_earliest_run_time
            wait = (nxt - now).total_seconds()
            if int(args.dry_run or 0) > 0:
                print(f"now={now.isoformat()} next={nxt.isoformat()} wait={wait:.2f}s")
                return
            if wait > 0:
                time.sleep(float(wait))

            payload = {
                "sim_id": str(args.sim_id),
                "pool_id": str(args.pool_id),
                "frequency": str(args.frequency),
                "data_src": str(args.data_src),
                "autype": str(args.autype),
                "lookback_days": int(args.lookback_days),
                "top_n": int(args.top_n),
                "min_prob": float(args.min_prob),
                "holding_period": int(args.holding_period),
                "trade_cost": float(args.trade_cost),
                "walk_forward_max_folds": int(args.walk_forward_max_folds),
                "max_workers": int(args.max_workers),
                "limit": int(args.limit),
                "risk_filter_enabled": bool(int(args.risk_filter_enabled)),
                "risk_mode": str(args.risk_mode),
                "risk_index_code": str(args.risk_index_code),
                "risk_drop_threshold": float(args.risk_drop_threshold),
                "risk_flat_drop_threshold": float(args.risk_flat_drop_threshold),
                "risk_gap_down_threshold": float(args.risk_gap_down_threshold),
                "risk_intraday_enabled": bool(int(args.risk_intraday_enabled)),
                "risk_intraday_bear_threshold": float(args.risk_intraday_bear_threshold),
                "risk_intraday_bull_threshold": float(args.risk_intraday_bull_threshold),
                "risk_trend_ma_window": int(args.risk_trend_ma_window),
                "risk_data_fail_mode": str(args.risk_data_fail_mode),
                "risk_intraday_data_fail_mode": str(args.risk_intraday_data_fail_mode),
                "sell_mode": str(args.sell_mode),
                "t1_decision_time": str(args.t1_decision_time),
                "t1_gap_strong_threshold": float(args.t1_gap_strong_threshold),
                "t1_gap_weak_threshold": float(args.t1_gap_weak_threshold),
                "t1_gap_very_strong_threshold": float(args.t1_gap_very_strong_threshold),
            }

            url = str(args.api_base).rstrip("/") + str(args.endpoint)
            try:
                r = requests.post(url, json=payload, timeout=float(args.timeout_sec))
                if not (200 <= int(r.status_code) < 300):
                    msg = f"paper_trade_step http={r.status_code} {r.text[:500]}"
                    print(msg)
                    send_text_env(msg)
                    continue
                data = r.json()
                if str((data or {}).get("cmd")) == "stop" or str((data or {}).get("status")) == "stopped":
                    msg = f"Paper trade run stopped by server: {str((data or {}).get('reason') or 'unknown')}"
                    print(msg)
                    send_text_env(msg)
                    return
            except Exception as e:
                msg = f"paper_trade_step failed: {e}"
                print(msg)
                send_text_env(msg)
                continue

            line = _summarize(data if isinstance(data, dict) else {})
            print(line)
            send_text_env(line)

            # Throttling Logic
            buy_list = (data or {}).get("buy") or []
            targets = (data or {}).get("targets") or []

            if buy_list:
                # T+1 Logic: Pause until tomorrow 9:30
                # Assuming T+1 lock prevents any further action today
                nd = _next_weekday(now.date() + datetime.timedelta(days=1))
                next_start = datetime.datetime(nd.year, nd.month, nd.day, 9, 30, 0, tzinfo=tz)
                if not next_earliest_run_time or next_start > next_earliest_run_time:
                    next_earliest_run_time = next_start
                print(f"Bought positions (T+1), pausing until {next_start}...")

            elif not targets:
                # No targets: Enforce 30m delay
                # If default interval is already 30m, this confirms it. 
                # If strategy frequency is higher, this throttles it.
                delay_target = datetime.datetime.now(tz=tz) + datetime.timedelta(minutes=30)
                if not next_earliest_run_time or delay_target > next_earliest_run_time:
                     next_earliest_run_time = delay_target
                print("No targets, waiting 30 mins...")
            else:
                next_earliest_run_time = None
    except KeyboardInterrupt:
        print("Paper trade runner stopped by user.")



if __name__ == "__main__":
    main()
