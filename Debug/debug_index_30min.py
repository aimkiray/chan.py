
import sys
import os
import datetime
sys.path.insert(0, os.getcwd())
try:
    from DataAPI.ClickHouseAPI import CClickHouseAPI
    from Common.CEnum import KL_TYPE, AUTYPE, DATA_FIELD
    from Common.CTime import CTime
    from KLine.KLine_Unit import CKLine_Unit
    
    client = CClickHouseAPI.get_client()

    def _klu_key(klu: CKLine_Unit):
        t = getattr(klu, "time", None)
        if t is None:
            return None
        return (int(t.year), int(t.month), int(t.day), int(t.hour), int(t.minute))

    def _make_1m(y, m, d, hh, mm, o, h, l, c, vol=0.0, amt=0.0):
        return CKLine_Unit(
            {
                DATA_FIELD.FIELD_TIME: CTime(y, m, d, hh, mm, auto=False),
                DATA_FIELD.FIELD_OPEN: float(o),
                DATA_FIELD.FIELD_HIGH: float(h),
                DATA_FIELD.FIELD_LOW: float(l),
                DATA_FIELD.FIELD_CLOSE: float(c),
                DATA_FIELD.FIELD_VOLUME: float(vol),
                DATA_FIELD.FIELD_TURNOVER: float(amt),
            }
        )

    def _run_synth():
        api = CClickHouseAPI(
            code="sz399006",
            k_type=KL_TYPE.K_5M,
            begin_date="2024-01-02",
            end_date="2024-01-02",
            autype=AUTYPE.NONE,
            asset_type="INDEX",
        )
        lower = [
            _make_1m(2024, 1, 2, 9, 31, 10, 12, 9, 11, vol=100),
            _make_1m(2024, 1, 2, 9, 33, 11, 13, 10, 12, vol=200),
            _make_1m(2024, 1, 2, 9, 35, 12, 100, 11, 12, vol=300),
        ]
        out = api._resample_intraday_from_lower_data(
            target_k_type=KL_TYPE.K_5M,
            lower_data=lower,
            used_lower_k_type=KL_TYPE.K_1M,
            begin_date="2024-01-02",
            end_date="2024-01-02",
        )
        k = (2024, 1, 2, 9, 30)
        bar = None
        for x in out:
            if _klu_key(x) == k:
                bar = x
                break
        if bar is None:
            raise Exception("synth: missing 09:30 5m bar")
        if round(float(bar.open), 2) != 10.0:
            raise Exception(f"synth: open mismatch {bar.open}")
        if round(float(bar.close), 2) != 12.0:
            raise Exception(f"synth: close mismatch {bar.close}")
        if round(float(bar.high), 2) != 13.0:
            raise Exception(f"synth: high mismatch {bar.high}")
        if round(float(bar.low), 2) != 9.0:
            raise Exception(f"synth: low mismatch {bar.low}")
        if int(float(getattr(getattr(bar, 'trade_info', None), 'metric', {}).get(DATA_FIELD.FIELD_VOLUME, 0.0) or 0.0)) != 300:
            raise Exception("synth: volume mismatch")
        print("Synth OK: 1m→5m left-closed + 补洞 + 不包含09:35")

    if not client:
        print("No ClickHouse client connected, running synth checks only.")
        _run_synth()
        sys.exit(0)

    print("--- Check Index 30m Data in index_30min (399006) ---")
    try:
        query = "SELECT code, min(datetime), max(datetime), count(*) FROM index_30min WHERE code LIKE '%399006%' GROUP BY code"
        rows = client.execute(query)
        if not rows:
            print("No data for 399006 in index_30min")
        for r in rows:
            print(f"Index 30m {r[0]}: Start={r[1]}, End={r[2]}, Count={r[3]}")
    except Exception as e:
        print(f"index_30min query failed: {e}")

    _run_synth()

    try:
        codes = client.execute("SELECT DISTINCT code FROM index_1min WHERE code LIKE '%399006%' LIMIT 5")
        code = (codes[0][0] if codes and codes[0] else None) or "sz399006"
        last_dt_row = client.execute(
            "SELECT max(datetime) FROM index_1min WHERE code = %(code)s",
            {"code": code},
        )
        last_dt = last_dt_row[0][0] if last_dt_row and last_dt_row[0] else None
        if not isinstance(last_dt, datetime.datetime):
            print("ClickHouse check skipped: no index_1min data.")
            sys.exit(0)
        day = last_dt.date().strftime("%Y-%m-%d")

        lower_api = CClickHouseAPI(
            code=code,
            k_type=KL_TYPE.K_1M,
            begin_date=day,
            end_date=day,
            autype=AUTYPE.NONE,
            asset_type="INDEX",
        )
        lower = list(lower_api.get_kl_data() or [])
        if not lower:
            print("ClickHouse check skipped: empty 1m.")
            sys.exit(0)

        resample_api = CClickHouseAPI(
            code=code,
            k_type=KL_TYPE.K_30M,
            begin_date=day,
            end_date=day,
            autype=AUTYPE.NONE,
            asset_type="INDEX",
        )
        resampled = resample_api._resample_intraday_from_lower_data(
            target_k_type=KL_TYPE.K_30M,
            lower_data=lower,
            used_lower_k_type=KL_TYPE.K_1M,
            begin_date=day,
            end_date=day,
        )

        pre_api = CClickHouseAPI(
            code=code,
            k_type=KL_TYPE.K_30M,
            begin_date=day,
            end_date=day,
            autype=AUTYPE.NONE,
            asset_type="INDEX",
        )
        pre = list(pre_api.get_kl_data() or [])

        pre_by_k = {(_klu_key(x)): x for x in pre if _klu_key(x) is not None}
        mism = 0
        for x in resampled:
            k = _klu_key(x)
            y = pre_by_k.get(k)
            if y is None:
                continue
            if (
                round(float(x.open), 2) != round(float(y.open), 2)
                or round(float(x.high), 2) != round(float(y.high), 2)
                or round(float(x.low), 2) != round(float(y.low), 2)
                or round(float(x.close), 2) != round(float(y.close), 2)
            ):
                mism += 1
        print(f"ClickHouse OK: day={day} code={code} resampled={len(resampled)} pre={len(pre)} ohlc_mismatch={mism}")
    except Exception as e:
        print(f"ClickHouse check failed: {e}")

except Exception as e:
    print(f"Error: {e}")
