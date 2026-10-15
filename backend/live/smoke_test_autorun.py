import os
import sys
import types
import asyncio
import tempfile

# Enable live-run autostart for _on_startup_live_runs and allow the
# stubbed-bundle / on-the-fly paths without a real pre-trained bundle.
os.environ.setdefault("MLCHAN_LIVE_AUTOSTART", "1")
os.environ.setdefault("MLCHAN_LIVE_REQUIRE_PRETRAINED_MODEL", "0")

# Redirect backend.main's module-level StorageManager() to a throwaway sqlite
# file BEFORE importing it — the import itself opens and WAL/schema-ensures the
# configured analysis.db, which would otherwise rewrite the repo's real DB even
# though every test write below goes through the substituted DummyStorage.
_SMOKE_TMP = tempfile.mkdtemp(prefix="mlchan_smoke_autorun_")
os.environ.setdefault("MLCHAN_ANALYSIS_DB", os.path.join(_SMOKE_TMP, "analysis.db"))

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
    import atexit
    import datetime
    import shutil
    import DataAPI.ClickHouseAPI as ch

    # Isolate live-run/step/order writes from the real analysis.db: point the
    # module-level storage at a DummyStorage backed by a throwaway sqlite file.
    # _SMOKE_TMP already holds the import-time MLCHAN_ANALYSIS_DB redirect, so
    # one cleanup removes both throwaway DBs (dispose engines first — open
    # sqlite handles block rmtree on Windows).
    _tmp_dir = _SMOKE_TMP
    _tmp_storage = DummyStorage(db_path=os.path.join(_tmp_dir, "smoke_test_autorun.db"))
    _orig_module_storage = m.storage
    m.storage = _tmp_storage

    def _cleanup_tmp_storage():
        for st in (_tmp_storage, _orig_module_storage):
            try:
                st.engine.dispose()
            except Exception:
                pass
        shutil.rmtree(_tmp_dir, ignore_errors=True)

    atexit.register(_cleanup_tmp_storage)

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
    orig_sched = m._start_live_run_scheduler
    try:
        m._start_live_run_scheduler = lambda: None
        asyncio.run(m._on_startup_live_runs())
    finally:
        m._start_live_run_scheduler = orig_sched
    r = m.storage.get_live_run(rid_a)
    assert int(r.get("next_exec_ts") or 0) > 0

    import backend.wolfpack_backtest as wb
    from backend.live.common import cn_ts_from_day_key_minutes

    dk = 20240102
    decision_min = 10 * 60
    daily_oc_by_code = {
        "sz.300001": {
            int(dk): {
                "open": 10.0,
                "close": 10.5,
                "decision_px": 10.2,
            }
        }
    }
    sim = wb._simulate_universe_daily_topn(
        candidates=[{"day": int(dk), "code": "sz.300001", "prob": 0.9, "ts": int(cn_ts_from_day_key_minutes(int(dk), 9 * 60 + 30) or 0)}],
        start_date="2024-01-02",
        end_date="2024-01-02",
        trade_cost=0.0,
        top_n=1,
        daily_oc_by_code=daily_oc_by_code,
        sell_mode="delayed",
        t1_decision_time="10:00",
        enforce_t1=False,
    )
    assert isinstance(sim, dict)
    trade_logs = list(sim.get("trade_logs") or [])
    assert any(
        (t.get("side") == "buy")
        and (t.get("code") == "sz.300001")
        and (int(t.get("trade_ts") or 0) == int(cn_ts_from_day_key_minutes(int(dk), int(decision_min)) or 0))
        for t in trade_logs
    )

    orig_storage = m.storage
    orig_step_impl = m._live_paper_trade_step_impl
    orig_time_fn = m.time.time
    orig_enqueue = m._enqueue_live_task_dedup
    try:
        class LocalStorage:
            def __init__(self, run: dict):
                self._run = dict(run or {})

            def get_live_run(self, run_id: str):
                return dict(self._run)

            def update_live_run_fields(self, run_id: str, fields: dict):
                self._run.update(fields or {})
                return dict(self._run)

        def run_case(now_ts: int, out_payload: dict):
            rid = "r_test"
            base_run = {
                "run_id": rid,
                "status": "running",
                "steps": 0,
                "portfolio_id": 1,
                "params": {
                    "sim_id": "sim_test",
                    "portfolio_id": 1,
                    "t1_decision_time": "10:00",
                    "top_n": 2,
                },
            }
            ls = LocalStorage(base_run)
            m.storage = ls
            m.time.time = lambda: float(now_ts)
            m._live_paper_trade_step_impl = lambda _req: dict(out_payload)
            m._run_live_step_task(rid, "sim_test", False, 5)
            return int(ls.get_live_run(rid).get("next_exec_ts") or 0)

        day = datetime.date(2026, 1, 5)
        prev_day = datetime.date(2026, 1, 2)
        nd = datetime.date(2026, 1, 6)
        today_dk = int(day.strftime("%Y%m%d"))
        prev_dk = int(prev_day.strftime("%Y%m%d"))
        expect_today_decision = int(m._cn_ts_on_date_hhmm(day, 10, 0, 5))
        expect_next_decision = int(m._cn_ts_on_date_hhmm(nd, 10, 0, 5))

        cn_tz = datetime.timezone(datetime.timedelta(hours=8))
        ts_1005 = int(datetime.datetime(2026, 1, 5, 10, 5, 0, tzinfo=cn_tz).timestamp())
        ts_0940 = int(datetime.datetime(2026, 1, 5, 9, 40, 0, tzinfo=cn_tz).timestamp())
        ts_1010 = int(datetime.datetime(2026, 1, 5, 10, 10, 0, tzinfo=cn_tz).timestamp())

        nxt1 = run_case(
            ts_1005,
            {"ts": 0, "day": today_dk, "buy": ["sz.300001"], "positions": [{"stock_code": "sz.300001", "entry_day": today_dk}]},
        )
        assert int(nxt1) == int(m._next_cn_30m_exec_ts(int(ts_1005), 5))

        nxt2 = run_case(
            ts_0940,
            {"ts": 0, "day": today_dk, "buy": [], "positions": [{"stock_code": "sz.300001", "entry_day": prev_dk}]},
        )
        assert int(nxt2) == int(expect_today_decision)

        nxt3 = run_case(
            ts_1010,
            {"ts": 0, "day": today_dk, "buy": [], "positions": [{"stock_code": "sz.300001", "entry_day": prev_dk}]},
        )
        assert int(nxt3) == int(m._next_cn_30m_exec_ts(int(ts_1010), 5))

        rid_off = "r_off"
        run_off = {
            "run_id": rid_off,
            "status": "running",
            "steps": 0,
            "portfolio_id": 1,
            "params": {
                "sim_id": "sim_test",
                "portfolio_id": 1,
                "t1_decision_time": "10:00",
                "top_n": 2,
            },
        }
        ls_off = LocalStorage(run_off)
        m.storage = ls_off
        off_ts_cn = int(datetime.datetime(2026, 1, 5, 8, 0, 0, tzinfo=cn_tz).timestamp())
        m.time.time = lambda: float(off_ts_cn)
        m._live_paper_trade_step_impl = lambda _req: (_ for _ in ()).throw(RuntimeError("should not run"))
        m._run_live_step_task(rid_off, "sim_test", False, 5)
        assert int(ls_off.get_live_run(rid_off).get("next_exec_ts") or 0) == int((m._cn_market_session(int(off_ts_cn), 5) or {}).get("next_exec_ts") or 0)

        class LocalStorageStart:
            def __init__(self, run: dict):
                self._run = dict(run or {})

            def get_live_run(self, run_id: str):
                return dict(self._run)

            def update_live_run_fields(self, run_id: str, fields: dict):
                self._run.update(fields or {})
                return dict(self._run)

        run0 = {
            "run_id": "r_start",
            "sim_id": "sim_test",
            "portfolio_id": 1,
            "status": "stopped",
            "steps": 1,
            "offset_seconds": 5,
            "notify_enabled": 0,
            # live_start_run calls _prepare_live_run_params(wants_schedule=True),
            # which requires non-empty params incl. pool_id/frequency/data_src/autype.
            "params": {
                "sim_id": "sim_test",
                "portfolio_id": 1,
                "pool_id": "index:IDX399673",
                "frequency": "30m",
                "data_src": "clickhouse",
                "autype": "qfq",
                "top_n": 2,
                "t1_decision_time": "10:00",
            },
        }
        ls2 = LocalStorageStart(run0)
        m.storage = ls2

        called = {"n": 0}
        m._enqueue_live_task_dedup = lambda *a, **k: called.__setitem__("n", int(called["n"]) + 1) or True

        off_ts = int(datetime.datetime(2026, 1, 5, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
        m.time.time = lambda: float(off_ts)
        asyncio.run(m.live_start_run("r_start"))
        r_after = ls2.get_live_run("r_start")
        assert int(r_after.get("next_exec_ts") or 0) == int((m._cn_market_session(int(off_ts), 5) or {}).get("next_exec_ts") or 0)
        assert int(called["n"]) == 0
    finally:
        m.storage = orig_storage
        m._live_paper_trade_step_impl = orig_step_impl
        m.time.time = orig_time_fn
        m._enqueue_live_task_dedup = orig_enqueue

    import backend.wolfpack_backtest as wb
    import concurrent.futures as cf

    os.environ["MLCHAN_MIN_KL_SIZE"] = "1"
    os.environ["MLCHAN_MIN_BSP_SIZE"] = "1"

    class LocalExecutor:
        def __init__(self, max_workers=None):
            self._max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            fut = cf.Future()
            try:
                fut.set_result(fn(*args, **kwargs))
            except Exception as e:
                fut.set_exception(e)
            return fut

    orig_pool = wb.concurrent.futures.ProcessPoolExecutor
    wb.concurrent.futures.ProcessPoolExecutor = LocalExecutor
    try:
        base_ts = int(datetime.datetime(2024, 1, 2, 1, 30, 0, tzinfo=datetime.timezone.utc).timestamp())
        stock_kl = [K(base_ts + i * 1800) for i in range(10)]
        wb.fetch_stock_data = lambda *a, **k: stock_kl

        def make_klu_chain():
            nodes = []
            for i in range(6):
                n = types.SimpleNamespace()
                n.close = 10.0 + float(i)
                n.high = n.close + 0.5
                n.low = n.close - 1.0
                n.time = types.SimpleNamespace(ts=base_ts + i * 1800)
                n.next = None
                nodes.append(n)
            for i in range(len(nodes) - 1):
                nodes[i].next = nodes[i + 1]
            return nodes[0]

        klu0 = make_klu_chain()
        bsp_dict = {
            "k0": {
                "is_buy": True,
                "open_time": types.SimpleNamespace(ts=base_ts + 1800),
                "feature": {"f1": 1.0},
                "bsp_obj": types.SimpleNamespace(klu=klu0),
            }
        }
        wb.get_chan_data = lambda *a, **k: (None, bsp_dict, None, None)

        called = {"n": 0, "kwargs": None}

        def fake_train(bsp_dict_arg, **kwargs):
            called["n"] = int(called["n"]) + 1
            called["kwargs"] = dict(kwargs or {})
            return None, None, {
                "predictions": [
                    {
                        "ts": base_ts + 1800,
                        "prob": 0.9,
                        "label": 0,
                        "is_buy": True,
                        "profit": 0.02,
                        "min_profit": -0.01,
                    }
                ]
            }

        wb.train_time_split_backtest = fake_train

        out = wb.WolfpackBacktester().run(
            pool_codes=["sz.300001"],
            start_date="2024-01-02",
            end_date="2024-01-03",
            frequency="30m",
            data_src="clickhouse",
            autype="qfq",
            enable_rolling_lookback=True,
            holding_period=3,
            min_prob=0.25,
            trade_cost=0.0,
            walk_forward_max_folds=5,
            max_workers=1,
            portfolio_top_n=1,
            index_filter_enabled=False,
            enforce_t1=False,
            model="xgboost",
            pretrained_model_key=None,
            rank_mode="raw",
            lookback_days=10,
            only_features="f1,f2",
            feature_preset="purify_compact",
        )
        assert out.get("status") == "success"
        assert int(called["n"]) >= 1
        assert called["kwargs"] is not None
        assert str(called["kwargs"].get("model_type")) == "xgboost"
        assert bool(called["kwargs"].get("rank_transform_enabled")) is False
        assert str(called["kwargs"].get("only_features")) == "f1,f2"
        assert str(called["kwargs"].get("feature_preset")) == "purify_compact"
        assert int(out.get("params", {}).get("lookback_days") or 0) == 10
        assert str(out.get("params", {}).get("feature_preset")) == "purify_compact"

        wb.load_pretrained_model_bundle_by_key = lambda key: {"model": DummyModel(), "feature_meta": ["f1"], "key": str(key)}

        def should_not_call(*a, **k):
            raise RuntimeError("train_time_split_backtest should not be called when pretrained_model_key is provided")

        wb.train_time_split_backtest = should_not_call

        out2 = wb.WolfpackBacktester().run(
            pool_codes=["sz.300001"],
            start_date="2024-01-02",
            end_date="2024-01-03",
            frequency="30m",
            data_src="clickhouse",
            autype="qfq",
            enable_rolling_lookback=True,
            holding_period=3,
            min_prob=0.25,
            trade_cost=0.0,
            walk_forward_max_folds=5,
            max_workers=1,
            portfolio_top_n=1,
            index_filter_enabled=False,
            enforce_t1=False,
            model="xgboost",
            pretrained_model_key="dummy_key",
            rank_mode="raw",
            lookback_days=10,
            only_features="f1,f2",
            feature_preset="purify_compact",
        )
        assert out2.get("status") == "success"
        assert out2.get("params", {}).get("pretrained_model_key") == "dummy_key"
        assert int(out2.get("candidates") or 0) >= 1
    finally:
        wb.concurrent.futures.ProcessPoolExecutor = orig_pool

    print("ok")


if __name__ == "__main__":
    main()
