import types
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import backend.main as m


class DummyStorage:
    def __init__(self):
        self._positions = []
        self._orders = []
        self._trades = []
        self._sims = {"test": {"balance": 100000.0}}
        self._next_order_id = 1

    def list_live_runs(self, sim_id=None, status=None, limit=1, portfolio_id=None):
        return [{"run_id": "dummy", "status": "running", "sim_id": sim_id or "test"}]

    def get_live_run(self, run_id):
        return {"run_id": str(run_id), "portfolio_id": None}

    def get_live_sim(self, sim_id):
        return dict(self._sims.get(str(sim_id or ""), {"balance": 100000.0}))

    def update_live_sim_balance(self, sim_id, balance):
        sid = str(sim_id or "")
        cur = self._sims.get(sid) or {"balance": 100000.0}
        cur["balance"] = float(balance)
        self._sims[sid] = cur
        return None

    def add_live_step_record(self, **kwargs):
        return None

    def list_positions(self, sim_id=None, portfolio_id=None):
        return list(self._positions)

    def increment_bars_held(self, n, sim_id=None, portfolio_id=None):
        for p in self._positions:
            p["bars_held"] = int(p.get("bars_held") or 0) + int(n or 0)
        return None

    def list_live_orders(self, status=None, limit=1000, sim_id=None, portfolio_id=None):
        items = list(self._orders)
        if status:
            items = [o for o in items if str(o.get("status") or "") == str(status)]
        return items[: int(limit or 1000)]

    def list_live_trades(self, limit=200, sim_id=None, portfolio_id=None):
        return list(self._trades)[: int(limit or 200)]

    def delete_positions(self, codes, sim_id=None, portfolio_id=None):
        code_set = set([str(c) for c in (codes or []) if c])
        self._positions = [p for p in self._positions if str(p.get("stock_code") or "") not in code_set]
        return None

    def upsert_position(self, code, entry_time, entry_price, bars_held, sim_id=None, portfolio_id=None, **kwargs):
        code = str(code or "")
        if not code:
            return None
        entry_day = kwargs.get("entry_day")
        if entry_day is None:
            try:
                entry_day = m.day_key_from_ts(int(entry_time or 0)) or 0
            except Exception:
                entry_day = 0
        found = None
        for p in self._positions:
            if str(p.get("stock_code") or "") == code:
                found = p
                break
        if found is None:
            found = {"stock_code": code}
            self._positions.append(found)
        found.update(
            {
                "stock_code": code,
                "entry_time": int(entry_time or 0),
                "entry_price": float(entry_price or 0.0),
                "bars_held": int(bars_held or 0),
                "entry_day": int(entry_day or 0),
                "volume": int(kwargs.get("volume") or found.get("volume") or 100),
                "stock_name": str(kwargs.get("stock_name") or found.get("stock_name") or ""),
            }
        )
        return None

    def add_live_trades(self, trades, sim_id=None, portfolio_id=None):
        for t in trades or []:
            if isinstance(t, dict):
                self._trades.append(dict(t))
        return None

    def mark_orders_executed(self, ids, executed_ts=0, sim_id=None, portfolio_id=None):
        id_set = set([int(x) for x in (ids or []) if int(x or 0) > 0])
        for o in self._orders:
            try:
                oid = int(o.get("id") or 0)
            except Exception:
                oid = 0
            if oid and oid in id_set:
                o["status"] = "executed"
                o["executed_ts"] = int(executed_ts or 0)
        return None

    def replace_blocked_live_orders(self, blocked, requested_ts=0, sim_id=None, portfolio_id=None):
        return None

    def add_live_orders(self, orders, sim_id=None, portfolio_id=None):
        return None

    def replace_pending_live_orders(self, pending, requested_ts=0, sim_id=None, portfolio_id=None):
        self._orders = []
        for p in pending or []:
            if not isinstance(p, dict):
                continue
            self._orders.append(
                {
                    "id": int(self._next_order_id),
                    "stock_code": str(p.get("stock_code") or ""),
                    "side": str(p.get("side") or ""),
                    "status": "pending",
                    "requested_ts": int(requested_ts or 0),
                    "execute_after_ts": int(p.get("execute_after_ts") or 0),
                    "min_day": int(p.get("min_day") or 0),
                    "price": float(p.get("price") or 0.0),
                    "reason": p.get("reason"),
                }
            )
            self._next_order_id += 1
        return None


class DummyModel:
    def predict_proba(self, X):
        out = []
        for row in X:
            try:
                f1 = float(row[0]) if row else 0.0
            except Exception:
                f1 = 0.0
            p = max(0.01, min(0.99, float(f1) / 10.0))
            out.append([float(1.0 - p), float(p)])
        return out


class K:
    def __init__(self, ts, close=10.0, open_=10.0, high=10.0, low=10.0):
        self.time = types.SimpleNamespace(ts=int(ts))
        self.close = float(close)
        self.open = float(open_)
        self.high = float(high)
        self.low = float(low)


def main():
    st = DummyStorage()
    m.storage = st

    import DataAPI.ClickHouseAPI as ch

    ch.CClickHouseAPI.get_pool_members = classmethod(lambda cls, pool_id=None, table=None, limit=None: ["300001", "300002"])

    called = {"model_type": None, "bundle_kwargs": None}

    def dummy_bundle(*a, **k):
        if a:
            called["model_type"] = str(a[0])
        called["bundle_kwargs"] = dict(k or {})
        return {"model": DummyModel(), "feature_meta": ["f1"], "key": "dummy", "meta": {"rank_mode": "cross_section"}}

    m.load_pretrained_model_bundle = dummy_bundle
    m.load_pretrained_model_bundle_by_key = lambda key: None
    m.cross_section_rank_transform_rows = lambda rows: rows

    bar_end_1400 = int(datetime.datetime(2026, 1, 2, 6, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
    n_bars = 1200
    start_ts = bar_end_1400 - (n_bars - 1) * 1800
    stock_kl = [K(start_ts + i * 1800) for i in range(n_bars)]
    for k in stock_kl:
        if int(k.time.ts) == int(datetime.datetime(2026, 1, 2, 2, 0, 0, tzinfo=datetime.timezone.utc).timestamp()):
            k.open = 10.0
            k.close = 11.0
            k.high = 11.0
            k.low = 10.0
        if int(k.time.ts) == int(datetime.datetime(2026, 1, 2, 6, 0, 0, tzinfo=datetime.timezone.utc).timestamp()):
            k.open = 19.0
            k.close = 20.0
            k.high = 20.0
            k.low = 19.0

    def fake_fetch(code, level, begin, end, data_src, autype=None, use_online_latest=False, **kw):
        code = str(code)
        if "399006" in code:
            return []
        import time
        now_ts = int(time.time())
        return [k for k in stock_kl if int(getattr(getattr(k, "time", None), "ts", 0) or 0) <= int(now_ts)]

    def fake_get_chan_data(code, *a, **k):
        ot = types.SimpleNamespace(ts=max(int(getattr(getattr(x, "time", None), "ts", 0) or 0) for x in (stock_kl or [])))
        f1 = 10.0 if str(code).endswith("300002") else 1.0
        bsp = {"last": {"is_buy": True, "open_time": ot, "feature": {"f1": float(f1)}}}
        return None, bsp, None, None

    m.fetch_stock_data = fake_fetch
    m.get_chan_data = fake_get_chan_data
    m.get_stock_name = lambda code: str(code)
    try:
        from DataAPI.ClickHouseAPI import CClickHouseAPI

        CClickHouseAPI.get_pool_members = staticmethod(lambda pool_id=None, table="stock_constituent", limit=5000, **kw: ["sz.300001", "sz.300002"])
    except Exception:
        pass

    import time as _time

    ts_0940 = int(datetime.datetime(2026, 1, 2, 1, 40, 0, tzinfo=datetime.timezone.utc).timestamp())
    ts_1010 = int(datetime.datetime(2026, 1, 2, 2, 10, 0, tzinfo=datetime.timezone.utc).timestamp())
    ts_1410 = int(datetime.datetime(2026, 1, 2, 6, 10, 0, tzinfo=datetime.timezone.utc).timestamp())
    ts_0800 = int(datetime.datetime(2026, 1, 2, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
    ts_0925 = int(datetime.datetime(2026, 1, 2, 1, 25, 0, tzinfo=datetime.timezone.utc).timestamp())

    st._positions = [
        {"stock_code": "sz.300001", "entry_time": ts_0940 - 86400, "entry_price": 10.0, "bars_held": 1, "entry_day": 20260101, "volume": 100, "stock_name": "300001"},
        {"stock_code": "sz.300002", "entry_time": ts_0940 - 86400, "entry_price": 10.0, "bars_held": 1, "entry_day": 20260101, "volume": 100, "stock_name": "300002"},
    ]

    payload = {
        "sim_id": "test",
        "pool_id": "index:IDX399673",
        "frequency": "30m",
        "data_src": "clickhouse",
        "autype": "qfq",
        "lookback_days": 30,
        "top_n": 2,
        "min_prob": 0.25,
        "holding_period": 3,
        "trade_cost": 0.0002,
        "max_workers": 1,
        "limit": 2,
        "model": "xgboost",
        "feature_preset": "purify_compact",
        "point_in_time_pool": False,
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
        "sell_mode": "t1_gap_roll",
        "t1_decision_time": "10:00",
    }
    old_time = _time.time
    try:
        _time.time = lambda: float(ts_0940)
        req = m.LivePaperTradeStepRequest(**payload)
        j = asyncio.run(m.live_paper_trade_step(req))
        assert called.get("model_type") == "xgboost"
        assert str((called.get("bundle_kwargs") or {}).get("feature_preset")) == "purify_compact"
        risk = (j or {}).get("risk") or {}
        assert "data_fail_mode" in risk and "intraday_data_fail_mode" in risk
        assert risk.get("data_fail_mode") == "reduce"
        assert risk.get("intraday_data_fail_mode") == "flat"
        assert (j or {}).get("sell") == []
        assert any(p.get("stock_code") == "sz.300001" for p in (j or {}).get("positions") or [])

        payload_idx = dict(payload)
        payload_idx["risk_index_code"] = "sz159915"
        payload_idx["risk_data_fail_mode"] = "flat"
        payload_idx["risk_intraday_data_fail_mode"] = "flat"
        payload_idx["risk_intraday_enabled"] = False
        j_idx = asyncio.run(m.live_paper_trade_step(m.LivePaperTradeStepRequest(**payload_idx)))
        r_idx = (j_idx or {}).get("risk") or {}
        assert str(r_idx.get("index_code") or "") in ("sz.159915", "sz159915")
        assert int(r_idx.get("effective_top_n") or 0) == 2

        called_train = {"n": 0, "kwargs": None}
        orig_train = m.train_time_split_backtest
        orig_bundle = m.load_pretrained_model_bundle
        try:
            m.load_pretrained_model_bundle = lambda *a, **k: None

            def fake_train(bsp_dict_arg, **kwargs):
                called_train["n"] = int(called_train["n"]) + 1
                called_train["kwargs"] = dict(kwargs or {})
                return DummyModel(), ["f1"], {"accuracy": 0.0}

            m.train_time_split_backtest = fake_train

            payload_train = dict(payload)
            payload_train["train_on_fly"] = True
            reqt = m.LivePaperTradeStepRequest(**payload_train)
            jt = asyncio.run(m.live_paper_trade_step(reqt))
            assert int(called_train.get("n") or 0) >= 1
            assert str((called_train.get("kwargs") or {}).get("model_type")) == "xgboost"
            assert str((called_train.get("kwargs") or {}).get("feature_preset")) == "purify_compact"
            assert isinstance(jt, dict)
        finally:
            m.train_time_split_backtest = orig_train
            m.load_pretrained_model_bundle = orig_bundle

        _time.time = lambda: float(ts_1010)
        reqx = m.LivePaperTradeStepRequest(**payload)
        jx = asyncio.run(m.live_paper_trade_step(reqx))
        assert "sz.300001" in ((jx or {}).get("sell") or [])
        assert not any(p.get("stock_code") == "sz.300001" for p in (jx or {}).get("positions") or [])
    finally:
        _time.time = old_time

    st._positions = [
        {"stock_code": "sz.300001", "entry_time": ts_0940 - 86400, "entry_price": 10.0, "bars_held": 1, "entry_day": 20260101, "volume": 100, "stock_name": "300001"},
        {"stock_code": "sz.300002", "entry_time": ts_0940 - 86400, "entry_price": 10.0, "bars_held": 1, "entry_day": 20260101, "volume": 100, "stock_name": "300002"},
    ]

    payload_delay = dict(payload)
    payload_delay["sell_mode"] = "delayed"
    payload_delay["top_n"] = 1
    old_time = _time.time
    try:
        _time.time = lambda: float(ts_0940)
        reqd = m.LivePaperTradeStepRequest(**payload_delay)
        jd = asyncio.run(m.live_paper_trade_step(reqd))
        assert (jd or {}).get("sell") == []
        assert (jd or {}).get("buy") == []
        assert any(p.get("stock_code") == "sz.300001" for p in (jd or {}).get("positions") or [])

        _time.time = lambda: float(ts_1410)
        reqdx = m.LivePaperTradeStepRequest(**payload_delay)
        jdx = asyncio.run(m.live_paper_trade_step(reqdx))
        assert "sz.300001" in ((jdx or {}).get("sell") or [])
        assert not any(p.get("stock_code") == "sz.300001" for p in (jdx or {}).get("positions") or [])
        sell_trades = [t for t in (st._trades or []) if str(t.get("side") or "") == "sell" and str(t.get("stock_code") or "") == "sz.300001"]
        assert sell_trades and abs(float(sell_trades[-1].get("price") or 0.0) - 11.0) < 1e-6
    finally:
        _time.time = old_time

    payload["risk_data_fail_mode"] = "flat"
    payload["risk_intraday_data_fail_mode"] = "reduce"
    req2 = m.LivePaperTradeStepRequest(**payload)
    j2 = asyncio.run(m.live_paper_trade_step(req2))
    risk2 = (j2 or {}).get("risk") or {}
    assert risk2.get("data_fail_mode") == "flat"
    assert risk2.get("intraday_data_fail_mode") == "reduce"

    st._positions = []
    st._orders = []
    st._trades = []
    payload_off = dict(payload)
    payload_off["top_n"] = 1
    payload_off["risk_filter_enabled"] = False
    old_time = _time.time
    try:
        _time.time = lambda: float(ts_0800)
        j_off = asyncio.run(m.live_paper_trade_step(m.LivePaperTradeStepRequest(**payload_off)))
        assert isinstance(j_off, dict)
        assert (j_off or {}).get("market_open") is False
        assert st._positions == []
        assert len(st._orders) >= 1
        assert len(st._trades) == 0

        _time.time = lambda: float(ts_0925)
        j_0925 = asyncio.run(m.live_paper_trade_step(m.LivePaperTradeStepRequest(**payload_off)))
        assert isinstance(j_0925, dict)
        assert (j_0925 or {}).get("market_open") is False
        assert st._positions == []
        assert len(st._trades) == 0

        _time.time = lambda: float(ts_0940)
        j_0940 = asyncio.run(m.live_paper_trade_step(m.LivePaperTradeStepRequest(**payload_off)))
        assert isinstance(j_0940, dict)
        assert (j_0940 or {}).get("market_open") is True
        assert any(p.get("stock_code") == "sz.300002" for p in (st._positions or []))
        assert any(t.get("side") == "buy" for t in (st._trades or []))
    finally:
        _time.time = old_time

    print("ok")


if __name__ == "__main__":
    main()
