import types
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import backend.main as m


class DummyStorage:
    def list_positions(self, sim_id=None, portfolio_id=None):
        return []

    def increment_bars_held(self, n, sim_id=None, portfolio_id=None):
        return None

    def list_live_orders(self, status=None, limit=1000, sim_id=None, portfolio_id=None):
        return []

    def list_live_trades(self, limit=200, sim_id=None, portfolio_id=None):
        return []

    def delete_positions(self, codes, sim_id=None, portfolio_id=None):
        return None

    def upsert_position(self, code, entry_time, entry_price, bars_held, sim_id=None, portfolio_id=None):
        return None

    def add_live_trades(self, trades, sim_id=None, portfolio_id=None):
        return None

    def mark_orders_executed(self, ids, executed_ts=0, sim_id=None, portfolio_id=None):
        return None

    def replace_blocked_live_orders(self, blocked, requested_ts=0, sim_id=None, portfolio_id=None):
        return None

    def add_live_orders(self, orders, sim_id=None, portfolio_id=None):
        return None

    def replace_pending_live_orders(self, pending, requested_ts=0, sim_id=None, portfolio_id=None):
        return None


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
    m.storage = DummyStorage()

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

    payload = {
        "sim_id": "test",
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
        "risk_intraday_data_fail_mode": "flat",
    }
    req = m.LivePaperTradeStepRequest(**payload)
    j = asyncio.run(m.live_paper_trade_step(req))
    risk = (j or {}).get("risk") or {}
    assert "data_fail_mode" in risk and "intraday_data_fail_mode" in risk
    assert risk.get("data_fail_mode") == "reduce"
    assert risk.get("intraday_data_fail_mode") == "flat"

    payload["risk_data_fail_mode"] = "flat"
    payload["risk_intraday_data_fail_mode"] = "reduce"
    req2 = m.LivePaperTradeStepRequest(**payload)
    j2 = asyncio.run(m.live_paper_trade_step(req2))
    risk2 = (j2 or {}).get("risk") or {}
    assert risk2.get("data_fail_mode") == "flat"
    assert risk2.get("intraday_data_fail_mode") == "reduce"

    print("ok")


if __name__ == "__main__":
    main()
