import argparse
import concurrent.futures
import datetime
import time

from DataAPI.ClickHouseAPI import CClickHouseAPI
from backend.chan_service import fetch_stock_data, get_chan_data, normalize_code, train_time_split_backtest
from backend.live.common import day_key_from_ts, parse_autype, parse_data_src, parse_frequency
from backend.storage import StorageManager


def _score_one(code, level, begin, end, data_src, autype, enable_rolling_lookback: bool, profit_lookahead: int, trade_cost: float, min_prob: float, walk_forward_max_folds: int, only_features=None, feature_preset: str = "lr_default"):
    kl_list = fetch_stock_data(code, level, begin, end, data_src, autype=autype, use_online_latest=bool(data_src.name.lower() == "click_house"))
    if not kl_list or len(kl_list) < 600:
        return code, None
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
        return code, None
    _, _, acc = train_time_split_backtest(
        bsp_dict,
        model_type="lr",
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
        return code, None
    dk = day_key_from_ts(last_ts)
    if dk is None:
        return code, None
    try:
        entry_price = float(getattr(kl_list[-1], "close", 0.0) or 0.0)
    except Exception:
        entry_price = 0.0
    return code, {
        "day": int(dk),
        "ts": int(last_ts),
        "prob": float(last.get("prob")),
        "entry_price": float(entry_price),
    }


def run_once(args, storage: StorageManager):
    freq_raw, level = parse_frequency(args.frequency)
    data_src = parse_data_src(args.data_src)
    autype = parse_autype(args.autype)
    pool_id = str(args.pool_id or "index:IDX399673").strip()
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
    feature_preset = str(getattr(args, "feature_preset", "") or "").strip() or "lr_default"

    storage.increment_bars_held(1)
    positions = storage.list_positions()
    pos_map = {p.get("stock_code"): p for p in (positions or []) if p.get("stock_code")}
    to_force_sell = [p["stock_code"] for p in (positions or []) if int(p.get("bars_held") or 0) >= int(holding_period)]

    results = []
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
            ): code
            for code in codes
        }
        for fut in concurrent.futures.as_completed(futs):
            code = futs[fut]
            try:
                _, r = fut.result()
            except Exception:
                r = None
            if r:
                results.append({"code": code, **r})

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
    sell_codes = list(dict.fromkeys([c for c in (to_force_sell + rebalance_out) if c]))
    buy_codes = [c for c in target_codes if c not in current_codes] if has_targets else []

    if sell_codes:
        storage.delete_positions(sell_codes)
    for t in targets:
        if t["code"] in buy_codes:
            storage.upsert_position(t["code"], int(t.get("ts") or now_ts), float(t.get("entry_price") or 0.0), 0)

    after_pos = storage.list_positions()
    print(f"ts={now_ts} day={cur_day} freq={freq_raw} pool={pool_id} top_n={top_n} holding={holding_period}")
    print(f"targets={[(t['code'], round(float(t['prob']),6)) for t in targets]}")
    print(f"sell={sell_codes}")
    print(f"buy={buy_codes}")
    print(f"positions={[(p.get('stock_code'), int(p.get('bars_held') or 0)) for p in (after_pos or [])]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool_id", type=str, default="index:IDX399673")
    ap.add_argument("--frequency", type=str, default="30m")
    ap.add_argument("--data_src", type=str, default="clickhouse")
    ap.add_argument("--autype", type=str, default="qfq")
    ap.add_argument("--lookback_days", type=int, default=365 * 5)
    ap.add_argument("--enable_rolling_lookback", type=int, default=1)
    ap.add_argument("--top_n", type=int, default=2)
    ap.add_argument("--min_prob", type=float, default=0.25)
    ap.add_argument("--holding_period", type=int, default=3)
    ap.add_argument("--trade_cost", type=float, default=0.0002)
    ap.add_argument("--walk_forward_max_folds", type=int, default=25)
    ap.add_argument("--only_features", type=str, default="")
    ap.add_argument("--feature_preset", type=str, default="lr_default")
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
