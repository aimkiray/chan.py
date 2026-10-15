import argparse
import datetime
import re
import time
from typing import Optional

import requests

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.live.notifier import send_text_env

# CN 30m-bar decision points (server-side _next_cn_30m_exec_ts uses the
# same table); only consulted as a fallback when the response carries no
# usable next_exec_ts.
_STEP_TIMES = [
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


def _sh_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception as e:
        print(
            f"[paper_trade_runner] zoneinfo 'Asia/Shanghai' unavailable ({e}); "
            "falling back to host-local time — CN session times will be wrong on non-CST hosts "
            "(install tzdata or run with TZ=Asia/Shanghai)",
            file=sys.stderr,
        )
        return None


def _load_trading_days(path: str):
    """Load a trading-day calendar: one date per line, `YYYY-MM-DD` or
    `YYYYMMDD`; blank lines and `#` comments are ignored.

    Returns a set of datetime.date, or None when unconfigured/unreadable —
    in that case the runner falls back to weekday-only scheduling, which
    means exchange holidays still wake the loop (documented limitation;
    provide a calendar file for production use).
    """
    p = str(path or "").strip()
    if not p:
        return None
    days = set()
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln, raw in enumerate(f, 1):
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                s = s.split()[0].replace("-", "").replace("/", "")
                try:
                    days.add(datetime.date(int(s[0:4]), int(s[4:6]), int(s[6:8])))
                except Exception:
                    print(f"[paper_trade_runner] trading calendar {p}:{ln}: unparseable date {raw.strip()!r}; skipped", file=sys.stderr)
    except Exception as e:
        print(f"[paper_trade_runner] cannot load trading calendar {p}: {e}; falling back to weekday-only", file=sys.stderr)
        return None
    if not days:
        print(f"[paper_trade_runner] trading calendar {p} parsed 0 days; falling back to weekday-only", file=sys.stderr)
        return None
    return days


def _is_trading_day(d: datetime.date, cal) -> bool:
    if cal is None:
        return d.weekday() < 5
    return d in cal


def _roll_to_trading_day(d: datetime.date, cal) -> datetime.date:
    """d itself when it is a trading day, else the next one."""
    while not _is_trading_day(d, cal):
        d = d + datetime.timedelta(days=1)
    return d


def _next_trading_day(d: datetime.date, cal) -> datetime.date:
    return _roll_to_trading_day(d + datetime.timedelta(days=1), cal)


def _parse_hhmm(v, default=(10, 0)):
    try:
        parts = str(v or "").strip().split(":")
        hh = int(parts[0]) if parts and parts[0].strip() else int(default[0])
        mm = int(parts[1]) if len(parts) > 1 and parts[1].strip() else int(default[1])
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except Exception:
        return int(default[0]), int(default[1])


def _next_cn_step_time(now: datetime.datetime, offset_seconds: int, cal=None) -> datetime.datetime:
    d = _roll_to_trading_day(now.date(), cal)
    tz = now.tzinfo
    off = max(0, min(120, int(offset_seconds or 0)))
    if d != now.date():
        hh, mm = _STEP_TIMES[0]
        return datetime.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz) + datetime.timedelta(seconds=off)

    cands = []
    for hh, mm in _STEP_TIMES:
        cands.append(datetime.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz) + datetime.timedelta(seconds=off))
    for t in cands:
        if t > now:
            return t

    nd = _next_trading_day(d, cal)
    hh, mm = _STEP_TIMES[0]
    return datetime.datetime(nd.year, nd.month, nd.day, hh, mm, 0, tzinfo=tz) + datetime.timedelta(seconds=off)


def _resp_next_exec_dt(resp_next_ts: int, now: datetime.datetime, tz, cal):
    """Response next_exec_ts is authoritative when it points to the future.

    The server reports next_exec_ts=now while the market is open — that is
    'asap', not a schedule, so callers must keep the local 30m cadence then
    (returning None here does exactly that). When a local trading calendar
    is configured, the date part is rolled forward across holidays the
    server's weekday-only calendar cannot see."""
    try:
        ts = int(resp_next_ts or 0)
    except Exception:
        return None
    now_ts = int(now.timestamp())
    if ts <= 0 or ts <= now_ts:
        return None
    if ts > now_ts + 10 * 24 * 3600:
        return None  # implausible; ignore rather than sleep ~forever
    dt = datetime.datetime.fromtimestamp(ts, tz=tz)
    rolled = _roll_to_trading_day(dt.date(), cal)
    if rolled != dt.date():
        dt = datetime.datetime(rolled.year, rolled.month, rolled.day, dt.hour, dt.minute, dt.second, tzinfo=tz)
    return dt


def _full_book_t1_defer(now: datetime.datetime, data: dict, top_n: int, t1_decision_time: str, offset_seconds: int, cal):
    """Server-parity T+1 deferral (_run_live_step_task in backend/main.py).

    The pause is only allowed to constrain the *new-buy direction*: it
    applies solely when the book is full (positions >= top_n), in which
    case either fresh buys just happened or every held position is a
    same-day entry — all sells are T+1-blocked server-side anyway, so the
    next useful decision point is the next trading day's decision time.
    A partially filled book returns None and keeps the 30m cadence so
    sell/risk windows are never skipped."""
    positions = (data or {}).get("positions") or []
    if not isinstance(positions, list) or not positions:
        return None
    try:
        top_n = max(1, int(top_n or 2))
    except Exception:
        top_n = 2
    if len(positions) < top_n:
        return None
    hh, mm = _parse_hhmm(t1_decision_time)
    off = datetime.timedelta(seconds=int(offset_seconds or 0))
    tz = now.tzinfo
    today = now.date()

    def _at(d: datetime.date) -> datetime.datetime:
        return datetime.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz) + off

    buy_list = (data or {}).get("buy") or []
    if buy_list:
        return _at(_next_trading_day(today, cal))
    max_ed = 0
    for p in positions:
        try:
            ed = int((p or {}).get("entry_day") or 0)
        except Exception:
            ed = 0
        if ed > max_ed:
            max_ed = ed
    try:
        day = int((data or {}).get("day") or 0)
    except Exception:
        day = 0
    if day and int(max_ed) >= int(day):
        return _at(_next_trading_day(today, cal))
    decision_today = _at(today)
    if decision_today > now:
        return decision_today
    return _at(_next_trading_day(today, cal))


def _fmt_pct(x: Optional[float]) -> str:
    try:
        if x is None:
            return "-"
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "-"


def _safe_excerpt(text, limit: int = 180) -> str:
    """First-line whitespace-collapsed excerpt for notification channels —
    raw response bodies / exception text can embed server internals."""
    s = str(text or "")
    s = s.splitlines()[0] if s else ""
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > int(limit):
        s = s[: int(limit)] + "..."
    return s


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
    # The step response has no top-level `blocked` list; blocked orders
    # surface via pending_orders with status == "blocked".
    blocked = resp.get("blocked")
    if isinstance(blocked, list):
        bcnt = len(blocked)
    else:
        bcnt = 0
        for o in (resp.get("pending_orders") or []):
            if isinstance(o, dict) and str(o.get("status") or "").strip().lower() == "blocked":
                bcnt += 1
    return f"day={day} ts={ts} session={market_session} risk={level} reason={reason} gap={gap} d1={d1} intr={intr_change} trend={intr_trend} top={codes} sell={sells} buy={buys} blocked={bcnt}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api_base", type=str, default="http://127.0.0.1:8000")
    ap.add_argument("--endpoint", type=str, default="/api/live/paper_trade_step")
    ap.add_argument("--offset_seconds", type=int, default=5, help="seconds after each 30m boundary; clamped to [0,120] like the server")
    ap.add_argument("--timeout_sec", type=float, default=20.0)
    ap.add_argument("--dry_run", type=int, default=0)
    ap.add_argument("--trading_days_file", type=str, default="", help="trading calendar file (one YYYY-MM-DD per line); also via MLCHAN_TRADING_DAYS_FILE. Without it the schedule is weekday-only and exchange holidays still trigger steps")

    ap.add_argument("--run_id", type=str, default="", help="attach steps to a live run; empty = unmanaged mode. When set and the run is not 'running', the server replies cmd=stop and this runner exits")
    ap.add_argument("--ignore_run_status", type=int, default=0, help="1 = run steps even if the run row is not running")
    ap.add_argument("--portfolio_id", type=int, default=0, help="portfolio id forwarded to the step; 0 = none")
    ap.add_argument("--initial_capital", type=float, default=0.0, help="optional initial cash forwarded to the step; 0 = unset")
    ap.add_argument("--model", type=str, default="xgboost", help="scoring model passed to the step (server default: xgboost)")
    ap.add_argument("--pretrained_model_key", type=str, default="", help="optional pretrained model bundle key")
    ap.add_argument("--require_pretrained_model", type=int, default=-1, help="-1 = server env default; 0/1 override")

    ap.add_argument("--sim_id", type=str, default="default")
    ap.add_argument("--pool_id", type=str, default="index:IDX399673")
    ap.add_argument("--frequency", type=str, default="30m")
    ap.add_argument("--data_src", type=str, default="clickhouse")
    ap.add_argument("--autype", type=str, default="qfq", help="forwarded to the step; must match the pretrained bundle's autype")
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

    try:
        offset_seconds = int(args.offset_seconds)
    except Exception:
        offset_seconds = 5
    offset_clamped = max(0, min(120, int(offset_seconds)))
    if offset_clamped != offset_seconds:
        print(f"[paper_trade_runner] --offset_seconds {offset_seconds} clamped to {offset_clamped} (server range 0-120)", file=sys.stderr)
    offset_seconds = offset_clamped

    cal = _load_trading_days(args.trading_days_file or os.environ.get("MLCHAN_TRADING_DAYS_FILE") or "")
    if cal is None:
        print("[paper_trade_runner] no trading calendar configured; weekday-only schedule — exchange holidays will still wake steps", file=sys.stderr)

    tz = _sh_tz()
    next_earliest_run_time = None
    try:
        while True:
            now = datetime.datetime.now(tz=tz)
            nxt = _next_cn_step_time(now, offset_seconds, cal)
            if next_earliest_run_time and next_earliest_run_time > nxt:
                nxt = next_earliest_run_time
            wait = (nxt - now).total_seconds()
            if int(args.dry_run or 0) > 0:
                print(f"now={now.isoformat()} next={nxt.isoformat()} wait={wait:.2f}s")
                return
            if wait > 0:
                time.sleep(float(wait))

            run_id = str(args.run_id or "").strip()
            portfolio_id = int(args.portfolio_id or 0)
            payload = {
                "sim_id": str(args.sim_id),
                "run_id": run_id or None,
                "ignore_run_status": bool(int(args.ignore_run_status or 0)),
                "portfolio_id": int(portfolio_id) if portfolio_id > 0 else None,
                "initial_capital": float(args.initial_capital) if float(args.initial_capital or 0.0) > 0 else None,
                "pretrained_model_key": str(args.pretrained_model_key).strip() or None,
                "require_pretrained_model": (None if int(args.require_pretrained_model) < 0 else bool(int(args.require_pretrained_model))),
                "model": str(args.model),
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
                    excerpt = _safe_excerpt(r.text)
                    msg = f"paper_trade_step http={r.status_code} body={excerpt}"
                    print(msg)
                    send_text_env(msg)
                    continue
                data = r.json()
                if str((data or {}).get("cmd")) == "stop" or str((data or {}).get("status")) == "stopped":
                    msg = f"Paper trade run stopped by server: {_safe_excerpt((data or {}).get('reason') or 'unknown', 120)}"
                    print(msg)
                    send_text_env(msg)
                    return
            except Exception as e:
                msg = f"paper_trade_step failed: {type(e).__name__}: {_safe_excerpt(e)}"
                print(msg)
                send_text_env(msg)
                continue

            line = _summarize(data if isinstance(data, dict) else {})
            print(line)
            send_text_env(line)

            # Next-run scheduling: the response's next_exec_ts wins when it
            # points to the future (auction pause, lunch break, next trading
            # day); while the market is open it reads "now" and the local
            # 30m boundary table applies instead.
            now = datetime.datetime.now(tz=tz)
            cands = []
            resp_next_dt = _resp_next_exec_dt((data or {}).get("next_exec_ts"), now, tz, cal)
            if resp_next_dt is not None:
                cands.append(resp_next_dt)
            defer = _full_book_t1_defer(now, data if isinstance(data, dict) else {}, args.top_n, args.t1_decision_time, offset_seconds, cal)
            if defer is not None:
                cands.append(defer)
            if cands:
                next_earliest_run_time = max(cands)
                print(f"next run not before {next_earliest_run_time.isoformat()}")
            else:
                next_earliest_run_time = None
    except KeyboardInterrupt:
        print("Paper trade runner stopped by user.")



if __name__ == "__main__":
    main()
