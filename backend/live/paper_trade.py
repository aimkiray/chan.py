import argparse
import concurrent.futures
import datetime
import json
import os
import sys
import time

# Allow `python backend/live/paper_trade.py ...` (README-style invocation):
# repo root must be on sys.path for the DataAPI/backend imports below —
# paper_trade_runner.py and scan_features.py already do the same bootstrap.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from DataAPI.ClickHouseAPI import CClickHouseAPI
from backend.chan_service import fetch_stock_data, get_chan_data, normalize_code, train_time_split_backtest
from backend.live.common import day_key_from_ts, parse_autype, parse_data_src, parse_frequency
from backend.storage import (
    LivePosition,
    LiveSim,
    LiveStepRecord,
    LiveTrade,
    PositionStatus,
    StorageManager,
    _is_default_sim,
    _json_safe_value,
    _normalize_portfolio_id,
    _normalize_sim_id,
    _utc_now,
)

# Marker written into live_step_record.summary so this CLI can find the
# last bars_held watermark it committed — it lives inside the same
# transaction as the position writes, so a crash can never leave the
# watermark ahead of the actual increments (or vice versa).
_CLI_STEP_SOURCE = "cli_paper_trade"
_LOT_SIZE = 100
_SELL_TAX_RATE = 0.001  # sell-side stamp duty, same constant as the server path


def _normalize_model_type(model: str) -> str:
    """Align CLI model names with the server step (main.py): logistic
    aliases collapse to "lr" for train_time_split_backtest; the API
    default is xgboost."""
    m = str(model or "xgboost").strip().lower()
    if m in ("lr", "logreg", "logistic", "logistic_regression"):
        return "lr"
    if m == "xgb":
        return "xgboost"
    if m == "lgb":
        return "lightgbm"
    return m or "xgboost"


def _last_bar_ts(kl_list) -> int:
    try:
        k = list(kl_list)[-1]
        return int(getattr(getattr(k, "time", None), "ts", 0) or 0)
    except Exception:
        return 0


def _pos_entry_day(p: dict) -> int:
    try:
        ed = int(p.get("entry_day") or 0)
    except Exception:
        ed = 0
    if ed > 0:
        return ed
    try:
        return int(day_key_from_ts(int(p.get("entry_time") or 0)) or 0)
    except Exception:
        return 0


def _entry_day_of(entry_time: int):
    """Same UTC+8 day-key derivation as StorageManager.upsert_position,
    including its entry_time>0 guard: without it, entry_time=0 day-keys to
    19700101 — harmless today (buy fills always carry ts>0) but it would
    stamp a bogus entry_day and could trip the T+1 block for a position
    whose entry_time was never set."""
    try:
        entry_time = int(entry_time)
    except Exception:
        return None
    if entry_time <= 0:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(int(entry_time), tz=datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)
        return int(dt.year) * 10000 + int(dt.month) * 100 + int(dt.day)
    except Exception:
        return None


def _score_one(code, level, begin, end, data_src, autype, enable_rolling_lookback: bool, profit_lookahead: int, trade_cost: float, min_prob: float, walk_forward_max_folds: int, only_features=None, feature_preset=None, model_type: str = "xgboost"):
    kl_list = fetch_stock_data(code, level, begin, end, data_src, autype=autype, use_online_latest=bool(data_src.name.lower() == "click_house"))
    bar_ts = _last_bar_ts(kl_list)
    if not kl_list or len(kl_list) < 600:
        return code, None, bar_ts
    _, bsp_dict, _, _ = get_chan_data(
        code,
        True,
        True,
        level,
        data_src,
        preloaded_data=kl_list,
        do_predict=True,
        begin_time=begin,
        end_time=end,
        enable_rolling_lookback=enable_rolling_lookback,
        autype=autype,
        side_filter="buy",
    )
    if not bsp_dict:
        return code, None, bar_ts
    _, _, acc = train_time_split_backtest(
        bsp_dict,
        model_type=str(model_type or "xgboost"),
        backtest_mode="walk_forward",
        only_features=only_features,
        feature_preset=feature_preset,
        rank_transform_enabled=True,
        walk_forward_max_folds=int(walk_forward_max_folds),
        profit_threshold=0.01,
        profit_lookahead=int(profit_lookahead),
        trade_cost=float(trade_cost or 0.0),
        side_filter="buy",
    )
    preds = []
    if isinstance(acc, dict):
        preds = list(acc.get("predictions") or [])
    last = None
    last_ts = 0
    for r in preds:
        if not isinstance(r, dict):
            continue
        if not bool(r.get("is_buy")):
            continue
        try:
            prob = float(r.get("prob"))
        except Exception:
            continue
        if float(prob) < float(min_prob):
            continue
        try:
            ts = int(r.get("ts") or 0)
        except Exception:
            ts = 0
        if ts <= 0 or ts < last_ts:
            continue
        last = r
        last_ts = ts
    if last is None:
        return code, None, bar_ts
    dk = day_key_from_ts(last_ts)
    if dk is None:
        return code, None, bar_ts
    try:
        entry_price = float(getattr(kl_list[-1], "close", 0.0) or 0.0)
    except Exception:
        entry_price = 0.0
    return code, {
        "day": int(dk),
        "ts": int(last_ts),
        "prob": float(last.get("prob")),
        "entry_price": float(entry_price),
        "bar_ts": int(bar_ts),
    }, bar_ts


def _last_applied_bar_ts(storage: StorageManager, sim_id: str, pid, pool_id: str, freq_raw: str) -> int:
    """Newest bar ts this CLI already aged positions for, recovered from the
    summary of its own latest live_step_record row (transactional with the
    increments). 0 when none — first run always counts its first bar.

    The scan window is deliberately wide: when a server live-step writer and
    this CLI share the same sim_id+portfolio_id book, server step records can
    push the CLI's own last row off a small first page, losing the watermark
    and double-aging bars_held on the next run. 200 rows tolerate ~200
    interleaved foreign steps between CLI runs; a dedicated CLI sim_id
    remains the clean fix."""
    try:
        res = storage.list_live_steps_paged(sim_id=sim_id, portfolio_id=pid, page=1, page_size=200)
    except Exception as e:
        print(f"[paper_trade] cannot read bars_held watermark ({e}); assuming 0")
        return 0
    for it in (res or {}).get("items") or []:
        sm = it.get("summary") or {}
        if not isinstance(sm, dict):
            continue
        if sm.get("source") != _CLI_STEP_SOURCE:
            continue
        if str(sm.get("pool_id") or "") != str(pool_id):
            continue
        if str(sm.get("frequency") or "") != str(freq_raw):
            continue
        if int(it.get("portfolio_id") or 0) != int(pid or 0):
            continue
        try:
            bt = int(sm.get("bar_ts") or 0)
        except Exception:
            bt = 0
        if bt > 0:
            return bt
    return 0


def _commit_step(storage: StorageManager, *, sim_id, portfolio_id, inc_bars, sell_codes, buy_fills, trades, balance, step_record):
    """Apply the whole step write set in ONE BEGIN IMMEDIATE transaction.

    Previously delete_positions() and every upsert_position() committed on
    their own session, so a crash between them left a "sold but not bought"
    partial book. _upsert_retry owns BEGIN IMMEDIATE + commit + lock
    retries; work() below re-reads and rewrites on retry, staying
    idempotent. The step record (carrying the bars_held watermark) commits
    atomically with the position rows it describes.
    """
    sid = _normalize_sim_id(sim_id)
    pid = _normalize_portfolio_id(portfolio_id)
    use_status = _is_default_sim(sid) and pid is None
    # live_position stores "no portfolio" as the 0 sentinel (NULL can't take
    # part in the UNIQUE index); PositionStatus stays keyed on code only.
    stored_pid = pid if pid is not None else 0
    now_iso = _utc_now().isoformat() + "Z"
    sells = [str(c).strip() for c in (sell_codes or []) if str(c or "").strip()]
    sell_set = set(sells)
    buys = [b for b in (buy_fills or []) if isinstance(b, dict) and str(b.get("code") or "").strip()]

    def work(session):
        # 1) age held positions by one *new* bar (caller dedups on bar ts);
        #    codes being sold are skipped — their row is deleted next anyway.
        if inc_bars:
            if use_status:
                rows = session.query(PositionStatus).all()
            else:
                q = session.query(LivePosition).filter(LivePosition.sim_id == sid)
                if pid is not None:
                    q = q.filter(LivePosition.portfolio_id == pid)
                rows = q.all()
            for r in rows or []:
                if str(r.stock_code or "") in sell_set:
                    continue
                r.bars_held = int(r.bars_held or 0) + 1
                r.updated_at = now_iso
        # 2) sells
        if sells:
            if use_status:
                session.query(PositionStatus).filter(PositionStatus.stock_code.in_(sells)).delete(synchronize_session=False)
            else:
                q = session.query(LivePosition).filter(LivePosition.sim_id == sid, LivePosition.stock_code.in_(sells))
                if pid is not None:
                    q = q.filter(LivePosition.portfolio_id == pid)
                q.delete(synchronize_session=False)
        # 3) buys — mirrors StorageManager.upsert_position (entry_day from
        #    entry_time on the UTC+8 calendar; volume/name only on LivePosition).
        for b in buys:
            code = str(b.get("code")).strip()
            try:
                entry_time = int(b.get("ts") or 0)
            except Exception:
                entry_time = 0
            entry_day = _entry_day_of(entry_time)
            if use_status:
                row = session.query(PositionStatus).filter_by(stock_code=code).first()
                if row is None:
                    row = PositionStatus(stock_code=code)
                    session.add(row)
            else:
                row = session.query(LivePosition).filter_by(sim_id=sid, portfolio_id=stored_pid, stock_code=code).first()
                if row is None:
                    row = LivePosition(sim_id=sid, portfolio_id=stored_pid, stock_code=code)
                    session.add(row)
            row.entry_time = int(entry_time)
            if entry_day is not None:
                row.entry_day = int(entry_day)
            row.entry_price = float(b.get("price") or 0.0)
            row.bars_held = 0
            if not use_status:
                try:
                    vol = int(b.get("volume") or 0)
                except Exception:
                    vol = 0
                if vol > 0:
                    row.volume = vol
                if b.get("name"):
                    row.stock_name = str(b.get("name"))
            row.updated_at = now_iso
        # 4) audit rows (same shape as StorageManager.add_live_trades)
        for tr in trades or []:
            if not isinstance(tr, dict):
                continue
            code = str(tr.get("stock_code") or "").strip()
            side = str(tr.get("side") or "").strip().lower()
            if not code or side not in ["buy", "sell"]:
                continue
            meta = tr.get("meta")
            meta_json = None
            if meta is not None:
                try:
                    meta_json = json.dumps(_json_safe_value(meta), ensure_ascii=False, allow_nan=False)
                except Exception:
                    meta_json = None
            session.add(LiveTrade(
                sim_id=sid,
                portfolio_id=pid,
                stock_code=code,
                side=side,
                status=str(tr.get("status") or "executed"),
                trade_ts=int(tr.get("trade_ts") or 0),
                price=float(tr.get("price") or 0.0),
                meta_json=meta_json,
                created_at=_utc_now(),
            ))
        # 5) cash balance — only when a live_sim row exists (created via
        #    --initial_capital or the API); the legacy default book has no
        #    cash ledger.
        if balance is not None:
            row = session.query(LiveSim).filter(LiveSim.sim_id == sid).first()
            if row is not None:
                row.balance = float(balance)
                row.updated_at = _utc_now()
        # 6) step record carrying the bars_held watermark — same commit, so
        #    a crash can never desync "aged up to bar ts" from the rows.
        if isinstance(step_record, dict):
            session.add(LiveStepRecord(
                sim_id=sid,
                portfolio_id=pid,
                run_id=None,
                step_ts=int(step_record.get("step_ts") or 0),
                day=int(step_record.get("day") or 0),
                risk_json=None,
                targets_json=json.dumps(_json_safe_value(step_record.get("targets") or []), ensure_ascii=False, allow_nan=False),
                sell_json=json.dumps(_json_safe_value(step_record.get("sell") or []), ensure_ascii=False, allow_nan=False),
                buy_json=json.dumps(_json_safe_value(step_record.get("buy") or []), ensure_ascii=False, allow_nan=False),
                blocked_json=json.dumps(_json_safe_value(step_record.get("blocked") or []), ensure_ascii=False, allow_nan=False),
                summary_json=json.dumps(_json_safe_value(step_record.get("summary") or {}), ensure_ascii=False, allow_nan=False),
                result_json=None,
                error=None,
                created_at=_utc_now(),
            ))
        session.commit()

    storage._upsert_retry(work)


def run_once(args, storage: StorageManager):
    freq_raw, level = parse_frequency(args.frequency)
    data_src = parse_data_src(args.data_src)
    autype = parse_autype(args.autype)
    sim_id = _normalize_sim_id(getattr(args, "sim_id", "default"))
    pid = _normalize_portfolio_id(getattr(args, "portfolio_id", None))
    pool_id = str(args.pool_id or "index:IDX399673").strip()
    # NOTE: pool membership always comes from the ClickHouse constituent
    # tables regardless of --data_src (which selects only the bar-data
    # source) — same as the server-side step endpoint.
    pool_members = CClickHouseAPI.get_pool_members(pool_id)
    codes = [normalize_code(c) for c in (pool_members or [])]
    codes = list(dict.fromkeys([c for c in codes if c]))
    if args.limit and int(args.limit) > 0:
        codes = codes[: int(args.limit)]

    end = datetime.datetime.now().strftime("%Y-%m-%d")
    begin = (datetime.datetime.now() - datetime.timedelta(days=int(args.lookback_days))).strftime("%Y-%m-%d")
    now_ts = int(time.time())
    holding_period = int(args.holding_period)
    top_n = int(args.top_n)
    min_prob = float(args.min_prob)
    trade_cost = float(args.trade_cost)
    wf_folds = int(args.walk_forward_max_folds)
    only_features = None
    if str(getattr(args, "only_features", "") or "").strip():
        only_features = str(args.only_features).strip()
    model_type = _normalize_model_type(getattr(args, "model", "xgboost"))
    feature_preset = str(getattr(args, "feature_preset", "") or "").strip() or None
    if feature_preset is None and model_type == "lr":
        # Server parity: lr auto-falls back to the lr_default preset.
        feature_preset = "lr_default"

    positions = storage.list_positions(sim_id=sim_id, portfolio_id=pid)
    pos_map = {p.get("stock_code"): p for p in (positions or []) if p.get("stock_code")}

    results = []
    bar_ts_seen = 0
    try:
        mw = int(args.max_workers)
    except Exception:
        mw = 8
    mw = max(1, min(int(mw), int(len(codes) if codes else 1)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=mw) as ex:
        futs = {
            ex.submit(
                _score_one,
                code,
                level,
                begin,
                end,
                data_src,
                autype,
                bool(args.enable_rolling_lookback),
                holding_period,
                trade_cost,
                min_prob,
                wf_folds,
                only_features,
                feature_preset,
                model_type,
            ): code
            for code in codes
        }
        for fut in concurrent.futures.as_completed(futs):
            code = futs[fut]
            try:
                _, r, bt = fut.result()
            except Exception as e:
                print(f"[paper_trade] score failed code={code}: {type(e).__name__}: {e}")
                r, bt = None, 0
            try:
                bar_ts_seen = max(int(bar_ts_seen), int(bt or 0))
            except Exception:
                pass
            if r:
                results.append({"code": code, **r})

    # bars_held ages per *new market bar*, not per process run: the watermark
    # committed with the last write set makes reruns, retries and weekend
    # loops observing the same bar a no-op instead of premature force-sells.
    last_applied_bar_ts = _last_applied_bar_ts(storage, sim_id, pid, pool_id, freq_raw)
    inc_bars = bool(bar_ts_seen) and int(bar_ts_seen) > int(last_applied_bar_ts)

    # Holding-period check sees the post-increment age (same ordering as the
    # old increment-then-list code): a position crossing the threshold on
    # this new bar force-sells in this very step.
    age_inc = 1 if inc_bars else 0
    to_force_sell = [p["stock_code"] for p in (positions or []) if int(p.get("bars_held") or 0) + age_inc >= int(holding_period)]

    cur_day = day_key_from_ts(now_ts)
    if cur_day is None:
        cur_day = int((results[0].get("day") or 0) if results else 0)

    results.sort(key=lambda x: (int(x.get("day") or 0), float(x.get("prob") or 0.0)), reverse=True)
    todays = [r for r in results if int(r.get("day") or 0) == int(cur_day)]
    if (not todays) and bool(int(getattr(args, "use_latest_available_day", 1) or 1)) and results:
        try:
            cur_day = int(max(int(r.get("day") or 0) for r in results))
        except Exception:
            cur_day = int(cur_day or 0)
        todays = [r for r in results if int(r.get("day") or 0) == int(cur_day)]
    todays.sort(key=lambda x: float(x.get("prob") or 0.0), reverse=True)
    has_targets = bool(todays)
    targets = todays[: max(1, int(top_n))] if has_targets else []
    target_codes = [t["code"] for t in targets] if has_targets else list(pos_map.keys())

    current_codes = list(pos_map.keys())
    rebalance_out = [c for c in current_codes if c and c not in target_codes] if has_targets else []
    sell_candidates = list(dict.fromkeys([c for c in (to_force_sell + rebalance_out) if c]))
    buy_candidates = [c for c in target_codes if c not in current_codes] if has_targets else []

    # T+1 (cn_a): a position entered today may not be sold today, same gate
    # as the server path (main.py entry_day >= cur_day -> blocked). This
    # covers both rebalance drops and holding-period force sells.
    blocked = []
    sell_codes = []
    for c in sell_candidates:
        p = pos_map.get(c)
        ed = _pos_entry_day(p) if p else 0
        if p and ed and int(cur_day or 0) and int(ed) >= int(cur_day):
            blocked.append({"stock_code": str(c), "side": "sell", "price": float(p.get("entry_price") or 0.0), "reason": "T+1"})
            continue
        sell_codes.append(c)

    # Cash ledger: active only when a live_sim row exists (created via
    # --initial_capital or the API). trade_cost is charged inside the fill
    # price (net price on the trade row; raw quote kept in meta).
    cash = None
    sim_row = storage.get_live_sim(sim_id) if sim_id else None
    try:
        init_cap = float(getattr(args, "initial_capital", 0.0) or 0.0)
    except Exception:
        init_cap = 0.0
    if sim_row is None and init_cap > 0:
        try:
            storage.update_live_sim_initial_capital(sim_id, init_cap)
            sim_row = storage.get_live_sim(sim_id)
        except Exception as e:
            print(f"[paper_trade] init live_sim failed sim={sim_id}: {e}")
    if sim_row is not None:
        try:
            cash = float(sim_row.get("balance") or 0.0)
        except Exception:
            cash = None

    trades = []
    buy_fills = []
    balance_after = cash

    def _sell_quote(code: str) -> float:
        # No per-order quote cache in the CLI path; re-fetch a short window
        # and take the last close, falling back to entry price.
        px = 0.0
        try:
            q_begin = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
            kl = fetch_stock_data(code, level, q_begin, end, data_src, autype=autype, use_online_latest=bool(data_src.name.lower() == "click_house"))
            if kl:
                px = float(getattr(kl[-1], "close", 0.0) or 0.0)
        except Exception:
            px = 0.0
        if px <= 0.0:
            px = float((pos_map.get(code) or {}).get("entry_price") or 0.0)
        return px

    sold_codes = []
    for c in sell_codes:
        p = pos_map.get(c) or {}
        px = _sell_quote(c)
        if px <= 0.01:
            # No quote AND no usable entry_price — a ~0 sell record would
            # still delete the position inside _commit_step. Keep the row
            # and surface a blocked reason instead (same px guard as the
            # server step path in main.py).
            blocked.append({"stock_code": str(c), "side": "sell", "price": float(px or 0.0), "reason": "no_price"})
            continue
        try:
            vol = int(p.get("volume") or 0)
        except Exception:
            vol = 0
        if vol <= 0:
            vol = _LOT_SIZE  # legacy rows carry no volume — same 100-share default as the server path
        net_px = px * (1.0 - trade_cost - _SELL_TAX_RATE)
        if balance_after is not None:
            balance_after += net_px * vol
        sold_codes.append(c)
        trades.append({
            "stock_code": c,
            "side": "sell",
            "status": "executed",
            "trade_ts": int(now_ts),
            "price": float(net_px),
            "meta": {"source": "cli", "raw_price": px, "volume": int(vol), "trade_cost": float(trade_cost), "tax_rate": _SELL_TAX_RATE},
        })

    if buy_candidates:
        alloc = (balance_after / float(len(buy_candidates))) if balance_after is not None else None
        for t in targets:
            code = str(t.get("code") or "")
            if code not in buy_candidates:
                continue
            px = float(t.get("entry_price") or 0.0)
            if px <= 0.0:
                blocked.append({"stock_code": code, "side": "buy", "price": px, "reason": "no_price"})
                continue
            vol = 0
            if alloc is not None:
                try:
                    vol = int(alloc / (px * (1.0 + trade_cost)) // _LOT_SIZE) * _LOT_SIZE
                except Exception:
                    vol = 0
                if vol < _LOT_SIZE:
                    blocked.append({"stock_code": code, "side": "buy", "price": px, "reason": "Insufficient Cash"})
                    continue
                balance_after -= px * vol * (1.0 + trade_cost)
            net_px = px * (1.0 + trade_cost)
            buy_fills.append({"code": code, "ts": int(t.get("ts") or now_ts), "price": px, "volume": int(vol), "name": ""})
            trades.append({
                "stock_code": code,
                "side": "buy",
                "status": "executed",
                "trade_ts": int(now_ts),
                "price": float(net_px),
                "meta": {"source": "cli", "raw_price": px, "volume": int(vol), "prob": float(t.get("prob") or 0.0), "trade_cost": float(trade_cost)},
            })

    if inc_bars or sold_codes or buy_fills or trades or blocked:
        _commit_step(
            storage,
            sim_id=sim_id,
            portfolio_id=pid,
            inc_bars=inc_bars,
            sell_codes=sold_codes,
            buy_fills=buy_fills,
            trades=trades,
            balance=balance_after,
            step_record={
                "step_ts": int(now_ts),
                "day": int(cur_day or 0),
                "targets": [{"code": t.get("code"), "prob": float(t.get("prob") or 0.0)} for t in targets],
                "sell": sold_codes,
                "buy": [b.get("code") for b in buy_fills],
                "blocked": blocked,
                "summary": {
                    "source": _CLI_STEP_SOURCE,
                    "pool_id": pool_id,
                    "frequency": freq_raw,
                    "bar_ts": int(bar_ts_seen or 0),
                    "inc_bars": bool(inc_bars),
                },
            },
        )

    after_pos = storage.list_positions(sim_id=sim_id, portfolio_id=pid)
    print(f"ts={now_ts} day={cur_day} freq={freq_raw} pool={pool_id} top_n={top_n} holding={holding_period} model={model_type} sim={sim_id} pid={pid} inc_bars={int(bool(inc_bars))}")
    print(f"targets={[(t['code'], round(float(t['prob']),6)) for t in targets]}")
    print(f"sell={sold_codes}")
    print(f"buy={[b.get('code') for b in buy_fills]}")
    print(f"blocked={blocked}")
    if balance_after is not None:
        print(f"balance={float(balance_after):.2f}")
    print(f"positions={[(p.get('stock_code'), int(p.get('bars_held') or 0)) for p in (after_pos or [])]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim_id", type=str, default="default", help="live sim id; 'default' uses the legacy position_status table")
    ap.add_argument("--portfolio_id", type=int, default=0, help="portfolio id; 0 = none")
    ap.add_argument("--initial_capital", type=float, default=0.0, help="create the live_sim cash ledger with this capital when missing; 0 = no cash accounting")
    ap.add_argument("--pool_id", type=str, default="index:IDX399673", help="pool id; membership always resolves via ClickHouse constituent tables regardless of --data_src")
    ap.add_argument("--frequency", type=str, default="30m")
    ap.add_argument("--data_src", type=str, default="clickhouse", help="bar-data source only; pool membership is always ClickHouse")
    ap.add_argument("--autype", type=str, default="qfq")
    ap.add_argument("--lookback_days", type=int, default=365 * 5)
    ap.add_argument("--enable_rolling_lookback", type=int, default=1)
    ap.add_argument("--model", type=str, default="xgboost", help="scoring model, aligned with the API default (lr/logreg/logistic collapse to lr)")
    ap.add_argument("--feature_preset", type=str, default="", help="empty = auto (lr_default only for lr model, matching the server step)")
    ap.add_argument("--top_n", type=int, default=2)
    ap.add_argument("--min_prob", type=float, default=0.25)
    ap.add_argument("--holding_period", type=int, default=3)
    ap.add_argument("--trade_cost", type=float, default=0.0002, help="commission charged inside the recorded fill price (sells additionally charge 0.1%% stamp duty)")
    ap.add_argument("--walk_forward_max_folds", type=int, default=25)
    ap.add_argument("--only_features", type=str, default="")
    ap.add_argument("--max_workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--interval_seconds", type=int, default=1800)
    ap.add_argument("--use_latest_available_day", type=int, default=1)
    args = ap.parse_args()
    storage = StorageManager()
    if int(args.loop or 0) <= 0:
        run_once(args, storage)
        return
    while True:
        try:
            run_once(args, storage)
        except Exception as e:
            print(f"paper_trade failed: {e}")
        time.sleep(max(5, int(args.interval_seconds or 1800)))


if __name__ == "__main__":
    main()
