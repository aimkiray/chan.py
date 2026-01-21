import os
import sys
import types
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import backend.main as m


class DummyStorage(m.storage.__class__):
    pass


class DummyModel:
    def predict_proba(self, X):
        return [[0.1, 0.9] for _ in X]


class K:
    def __init__(self, ts, close=10.0, open_=10.0, high=10.0, low=10.0):
        self.time = types.SimpleNamespace(ts=int(ts))
        self.close = float(close)
        self.open = float(open_)
        self.high = float(high)
        self.low = float(low)


def main():
    import DataAPI.ClickHouseAPI as ch

    ch.CClickHouseAPI.get_pool_members = classmethod(lambda cls, pool_id=None, table=None, limit=None: ["300001", "300002"])

    def dummy_bundle(*a, **k):
        return {"model": DummyModel(), "feature_meta": ["f1"], "key": "dummy", "meta": {"rank_mode": "cross_section"}}

    m.load_pretrained_model_bundle = dummy_bundle
    m.load_pretrained_model_bundle_by_key = lambda key: None
    m.cross_section_rank_transform_rows = lambda rows: rows

    base_ts = 1700000000
    stock_kl = [K(base_ts + i * 1800) for i in range(600)]

    def fake_fetch(code, level, begin, end, data_src, autype=None, use_online_latest=False, **kw):
        code = str(code)
        if "399006" in code:
            return []
        return stock_kl

    def fake_get_chan_data(code, *a, **k):
        ot = types.SimpleNamespace(ts=base_ts + 600 * 1800)
        bsp = {"last": {"is_buy": True, "open_time": ot, "feature": {"f1": 1.0}}}
        return None, bsp, None, None

    m.fetch_stock_data = fake_fetch
    m.get_chan_data = fake_get_chan_data

    sim_a = "sim_a"
    sim_b = "sim_b"

    payload = {
        "pool_id": "index:IDX399673",
        "frequency": "30m",
        "data_src": "clickhouse",
        "autype": "qfq",
        "lookback_days": 10,
        "top_n": 2,
        "min_prob": 0.25,
        "holding_period": 3,
        "trade_cost": 0.0002,
        "max_workers": 1,
        "limit": 2,
        "risk_filter_enabled": True,
        "risk_mode": "drop",
        "risk_drop_threshold": -0.02,
        "risk_flat_drop_threshold": -0.03,
        "risk_gap_down_threshold": -0.015,
        "risk_intraday_enabled": True,
        "risk_intraday_bear_threshold": -0.015,
        "risk_intraday_bull_threshold": -0.03,
        "risk_trend_ma_window": 20,
        "risk_data_fail_mode": "reduce",
        "risk_intraday_data_fail_mode": "reduce",
        "enable_rolling_lookback": True,
    }

    ra = asyncio.run(m.live_create_run(m.LiveRunCreateRequest(run_id=None, name="A", sim_id=sim_a, params={**payload, "sim_id": sim_a}, start=False)))
    rb = asyncio.run(m.live_create_run(m.LiveRunCreateRequest(run_id=None, name="B", sim_id=sim_b, params={**payload, "sim_id": sim_b}, start=True)))
    rid_a = ra["item"]["run_id"]
    rid_b = rb["item"]["run_id"]

    runs = asyncio.run(m.live_list_runs(status=None, limit=50))
    assert any(x.get("run_id") == rid_a for x in runs["items"])
    assert any(x.get("run_id") == rid_b for x in runs["items"])

    asyncio.run(m.live_start_run(rid_a))
    asyncio.run(m.live_stop_run(rid_b))

    req = m.LivePaperTradeStepRequest(**{**payload, "sim_id": sim_a, "run_id": rid_a})
    out = asyncio.run(m.live_paper_trade_step(req))
    assert isinstance(out, dict)
    steps = asyncio.run(m.live_list_steps(sim_id=sim_a, run_id=rid_a, page=1, page_size=20))
    assert int(steps.get("total") or 0) >= 1

    m.storage.update_live_run_fields(rid_a, {"status": "running", "auto_resume": 1, "next_exec_ts": 0})
    asyncio.run(m._on_startup_live_runs())
    r = m.storage.get_live_run(rid_a)
    assert int(r.get("next_exec_ts") or 0) > 0

    print("ok")


if __name__ == "__main__":
    main()

