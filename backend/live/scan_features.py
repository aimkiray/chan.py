import argparse
import datetime
import os
import sys
import time

# Allow `python backend/live/scan_features.py ...` (README-style invocation):
# repo root must be on sys.path for the DataAPI/backend imports below —
# paper_trade_runner.py and the smoke scripts already do the same bootstrap.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from DataAPI.ClickHouseAPI import CClickHouseAPI
from backend.chan_service import fetch_stock_data, get_chan_data, normalize_code
from backend.live.common import parse_autype, parse_data_src, parse_frequency, range_ratio_250


def _latest_bsp3_zs_height(code, level, begin, end, data_src, autype, kl_list, enable_rolling_lookback: bool):
    try:
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
        )
    except Exception:
        return None
    last_v = None
    last_ts = 0
    for _, info in (bsp_dict or {}).items():
        try:
            ot = info.get("open_time")
            ts = int(getattr(ot, "ts", 0) or 0)
        except Exception:
            ts = 0
        if ts <= 0 or ts < last_ts:
            continue
        feat = None
        try:
            feat = info.get("feature")
        except Exception:
            feat = None
        if not feat:
            continue
        try:
            v = feat.get("bsp3_zs_height")
        except Exception:
            v = None
        if v is None:
            continue
        try:
            last_v = float(v)
            last_ts = ts
        except Exception:
            continue
    return last_v


def run_once(args):
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

    out = []
    failed = []
    for code in codes:
        kl_list = None
        try:
            kl_list = fetch_stock_data(code, level, begin, end, data_src, autype=autype, use_online_latest=bool(data_src.name.lower() == "click_house"))
        except Exception as e:
            # Per-code degradation: one bad ticker must not abort the whole scan
            # round — record it and keep going (mirrors paper_trade's per-future
            # isolation).
            print(f"[scan_features] fetch_stock_data failed for {code}: {e}", file=sys.stderr)
            failed.append(code)
        # vol=None marks "no data" (fetch failed or empty), distinct from a
        # legitimately flat 0.0 range ratio — the two used to share vol=0 and
        # were indistinguishable in the sorted output.
        data_ok = bool(kl_list)
        vol = range_ratio_250(kl_list) if data_ok else None
        zs = None
        if data_ok and str(args.bsp_feature_mode or "latest_bsp").strip().lower() == "latest_bsp":
            zs = _latest_bsp3_zs_height(code, level, begin, end, data_src, autype, kl_list, enable_rolling_lookback=bool(args.enable_rolling_lookback))
        out.append((code, vol, zs))

    out.sort(key=lambda x: (float(x[1]) if x[1] is not None else -1.0, float(x[2] if x[2] is not None else -1e18)), reverse=True)
    ts = int(time.time())
    print(f"ts={ts} freq={freq_raw} pool={pool_id} n={len(out)} failed={len(failed)}")
    for code, vol, zs in out:
        zs_s = "None" if zs is None else f"{float(zs):.6f}"
        vol_s = "None" if vol is None else f"{float(vol):.6f}"
        print(f"{code}\trange_ratio_250={vol_s}\tbsp3_zs_height={zs_s}")
    if failed:
        print(f"failed_codes={','.join(str(c) for c in failed)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool_id", type=str, default="index:IDX399673")
    ap.add_argument("--frequency", type=str, default="30m")
    ap.add_argument("--data_src", type=str, default="clickhouse")
    ap.add_argument("--autype", type=str, default="qfq")
    ap.add_argument("--lookback_days", type=int, default=120)
    ap.add_argument("--enable_rolling_lookback", type=int, default=1)
    ap.add_argument("--bsp_feature_mode", type=str, default="latest_bsp")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--interval_seconds", type=int, default=1800)
    args = ap.parse_args()
    if int(args.loop or 0) <= 0:
        run_once(args)
        return
    while True:
        try:
            run_once(args)
        except Exception as e:
            print(f"scan failed: {e}")
        time.sleep(max(5, int(args.interval_seconds or 1800)))


if __name__ == "__main__":
    main()
