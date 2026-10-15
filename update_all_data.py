import argparse
import pandas as pd
from clickhouse_driver import Client
from datetime import datetime, date, timedelta
from bisect import bisect_left, bisect_right
import logging
import time
import multiprocessing as mp
import os
import requests
import json
import signal
import sys
import queue
import subprocess
import re

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("update_data.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 数据库配置（敏感字段从环境变量读取，env 命名与 DataAPI/ClickHouseAPI.py 一致）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    _DB_PORT = int(os.getenv("DB_PORT", 9000))
except Exception:
    _DB_PORT = 9000

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'port': _DB_PORT,
    'user': os.getenv("DB_USER", "default"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "stock_data")
}

MARKET_CLOSE_TIME = (15, 0)

def _is_market_closed(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    h, m = MARKET_CLOSE_TIME
    return (now.hour, now.minute) >= (int(h), int(m))

# 缓存目录
CACHE_DIR = "temp_data_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def _clear_cache_dir():
    """清空缓存目录中的文件；跳过子目录（如 failed/ 保留区），避免 IsADirectoryError。"""
    for f in os.listdir(CACHE_DIR):
        fp = os.path.join(CACHE_DIR, f)
        if not (os.path.isfile(fp) or os.path.islink(fp)):
            continue
        try:
            os.remove(fp)
        except OSError as e:
            logger.warning(f"清理缓存文件失败 {fp}: {e}")

def _keep_failed_file(filepath: str) -> None:
    """写入失败的缓存文件移入 failed/ 子目录保留，避免瞬时 DB 故障丢数据。"""
    try:
        failed_dir = os.path.join(CACHE_DIR, "failed")
        os.makedirs(failed_dir, exist_ok=True)
        failed_path = os.path.join(failed_dir, os.path.basename(filepath))
        if os.path.exists(failed_path):
            failed_path = os.path.join(
                failed_dir, f"{int(time.time() * 1000)}_{os.path.basename(filepath)}"
            )
        os.replace(filepath, failed_path)
        logger.warning(f"写入失败，文件已保留供排查/重放: {failed_path}")
    except Exception as move_err:
        # 移动失败也绝不删除原文件
        logger.warning(f"移动失败文件到 failed/ 目录失败，保留原路径 {filepath}: {move_err}")

KLINE_SOURCE = "auto"

# 任务配置
TASKS = [
    {'table': 'stock_daily',       'period': 'daily',   'adjust': '',    'klt': '101'},
    {'table': 'stock_daily_qfq',   'period': 'daily',   'adjust': 'qfq', 'klt': '101'},
    {'table': 'stock_daily_hfq',   'period': 'daily',   'adjust': 'hfq', 'klt': '101'},
    {'table': 'stock_weekly',      'period': 'weekly',  'adjust': '',    'klt': '102'},
    {'table': 'stock_weekly_qfq',  'period': 'weekly',  'adjust': 'qfq', 'klt': '102'},
    {'table': 'stock_weekly_hfq',  'period': 'weekly',  'adjust': 'hfq', 'klt': '102'},
    {'table': 'stock_monthly',     'period': 'monthly', 'adjust': '',    'klt': '103'},
    {'table': 'stock_monthly_qfq', 'period': 'monthly', 'adjust': 'qfq', 'klt': '103'},
    {'table': 'stock_monthly_hfq', 'period': 'monthly', 'adjust': 'hfq', 'klt': '103'},
    # 分钟线数据任务
    {'table': 'stock_1min',        'period': '1',       'adjust': '',    'is_minute': True, 'klt': '1'},
    {'table': 'stock_1min_qfq',    'period': '1',       'adjust': 'qfq', 'is_minute': True, 'klt': '1'},
    {'table': 'stock_1min_hfq',    'period': '1',       'adjust': 'hfq', 'is_minute': True, 'klt': '1'},
    {'table': 'stock_5min',        'period': '5',       'adjust': '',    'is_minute': True, 'klt': '5'},
    {'table': 'stock_5min_qfq',    'period': '5',       'adjust': 'qfq', 'is_minute': True, 'klt': '5'},
    {'table': 'stock_5min_hfq',    'period': '5',       'adjust': 'hfq', 'is_minute': True, 'klt': '5'},
    {'table': 'stock_15min',       'period': '15',      'adjust': '',    'is_minute': True, 'klt': '15'},
    {'table': 'stock_15min_qfq',   'period': '15',      'adjust': 'qfq', 'is_minute': True, 'klt': '15'},
    {'table': 'stock_15min_hfq',   'period': '15',      'adjust': 'hfq', 'is_minute': True, 'klt': '15'},
    {'table': 'stock_30min',       'period': '30',      'adjust': '',    'is_minute': True, 'klt': '30'},
    {'table': 'stock_30min_qfq',   'period': '30',      'adjust': 'qfq', 'is_minute': True, 'klt': '30'},
    {'table': 'stock_30min_hfq',   'period': '30',      'adjust': 'hfq', 'is_minute': True, 'klt': '30'},
    {'table': 'stock_60min',       'period': '60',      'adjust': '',    'is_minute': True, 'klt': '60'},
    {'table': 'stock_60min_qfq',   'period': '60',      'adjust': 'qfq', 'is_minute': True, 'klt': '60'},
    {'table': 'stock_60min_hfq',   'period': '60',      'adjust': 'hfq', 'is_minute': True, 'klt': '60'},
]

INDEX_TASKS = [
    {"table": "index_daily", "period": "daily", "adjust": "", "klt": "101", "allow_zero_amount": True},
    {"table": "index_30min", "period": "30", "adjust": "", "is_minute": True, "klt": "30", "allow_zero_amount": True},
]

def fetch_stock_list_em():
    fs = "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23"
    fields = "f12,f14"
    pn = 1
    pz = 100

    codes = []
    names = []

    total = None
    while True:
        params = {
            'pn': str(pn),
            'pz': str(pz),
            'po': '1',
            'np': '1',
            'fltt': '2',
            'invt': '2',
            'fid': 'f3',
            'fs': fs,
            'fields': fields
        }
        payload = _em_clist_get(params, timeout=10) or {}
        data = payload.get('data') or {}
        if total is None:
            total = data.get('total')
        diff = data.get('diff') or []
        if not diff:
            break
        for item in diff:
            code = item.get('f12')
            name = item.get('f14')
            if not code:
                continue
            codes.append(str(code))
            names.append(str(name) if name is not None else '')
        if total is not None and len(codes) >= int(total):
            break
        pn += 1

    return pd.DataFrame({'代码': codes, '名称': names})


def _em_clist_get(params: dict, timeout: int = 10) -> dict | None:
    urls = [
        "https://push2.eastmoney.com/api/qt/clist/get",
        "https://57.push2.eastmoney.com/api/qt/clist/get",
        "http://57.push2.eastmoney.com/api/qt/clist/get",
        "http://push2.eastmoney.com/api/qt/clist/get",
    ]
    last_err = None
    for url in urls:
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            if resp is None:
                continue
            return resp.json()
        except Exception as e:
            last_err = e
            continue
    return None


def _tdx_connect_api():
    try:
        from pytdx.hq import TdxHq_API
    except Exception:
        return None

    servers = [
        ("183.60.224.178", 7709),
        ("183.60.224.178", 7721),
        ("101.227.73.20", 7709),
        ("119.147.212.81", 7709),
    ]

    api = TdxHq_API(raise_exception=False, auto_retry=True)
    for host, port in servers:
        try:
            if api.connect(host, port):
                return api
        except Exception:
            pass
    try:
        api.disconnect()
    except Exception:
        pass
    return None


def _tdx_market_from_prefixed_code(code: str, symbol_6digit: str) -> int | None:
    c = str(code or "")
    if c.startswith("sh"):
        return 1
    if c.startswith("sz"):
        return 0
    s6 = str(symbol_6digit or "")
    if s6.startswith("399"):
        return 0
    if s6.startswith("000"):
        return 1
    return None


def _tdx_klt_to_category(klt: str) -> int | None:
    k = str(klt or "")
    mapping = {
        "1": 8,
        "5": 0,
        "15": 1,
        "30": 2,
        "60": 3,
        "101": 4,
        "102": 5,
        "103": 6,
    }
    return mapping.get(k)


def _parse_yyyymmdd_or_iso_to_date(s: str | None) -> date | None:
    if not s:
        return None
    ss = str(s).strip()
    if not ss:
        return None
    if len(ss) >= 8 and ss[:8].isdigit():
        try:
            y = int(ss[0:4])
            m = int(ss[4:6])
            d = int(ss[6:8])
            return date(y, m, d)
        except Exception:
            pass
    try:
        return date.fromisoformat(ss[:10])
    except Exception:
        return None


def _is_index_code(code: str, symbol_6digit: str) -> bool:
    c = str(code or "")
    s6 = str(symbol_6digit or "")
    if c.startswith("sh") and s6.startswith("000"):
        return True
    if c.startswith("sh") and s6 == "999999":
        return True
    if c.startswith("sz") and s6.startswith("399"):
        return True
    return False


def _tdx_get_api_cached():
    cached = globals().get("_tdx_api")
    cached_pid = globals().get("_tdx_api_pid")
    pid = os.getpid()
    if cached is not None and cached_pid == pid:
        return cached

    api = _tdx_connect_api()
    if api is None:
        return None

    globals()["_tdx_api"] = api
    globals()["_tdx_api_pid"] = pid
    return api


def fetch_kline_tdx_index(stock_code: str, symbol_6digit: str, klt: str, beg: str | None = None) -> pd.DataFrame:
    category = _tdx_klt_to_category(str(klt))
    if category is None:
        return pd.DataFrame()

    if not _is_index_code(stock_code, symbol_6digit):
        return pd.DataFrame()

    market = _tdx_market_from_prefixed_code(stock_code, symbol_6digit)
    if market is None:
        return pd.DataFrame()

    code6 = str(symbol_6digit or "")[-6:]
    if len(code6) != 6 or (not code6.isdigit()):
        return pd.DataFrame()

    beg_d = _parse_yyyymmdd_or_iso_to_date(beg)

    api = _tdx_get_api_cached()
    if api is None:
        return pd.DataFrame()

    page_size = 800
    max_pages = 400
    offset = 0
    rows: list[dict] = []

    for _ in range(max_pages):
        bars = api.get_index_bars(category, market, code6, offset, page_size) or []
        if not bars:
            break
        offset += page_size

        oldest_dt_in_page: datetime | None = None
        for b in bars:
            if not b:
                continue
            try:
                y = int(b.get("year", 0))
                m = int(b.get("month", 0))
                d = int(b.get("day", 0))
                hh = int(b.get("hour", 0))
                mm = int(b.get("minute", 0))
                dt = datetime(y, m, d, hh, mm)
            except Exception:
                dt_str = str(b.get("datetime") or "").strip()
                try:
                    dt = datetime.strptime(dt_str + ":00", "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue

            if oldest_dt_in_page is None or dt < oldest_dt_in_page:
                oldest_dt_in_page = dt

            if beg_d is not None and dt.date() < beg_d:
                continue

            rows.append(
                {
                    "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(b.get("open", 0.0) or 0.0),
                    "close": float(b.get("close", 0.0) or 0.0),
                    "high": float(b.get("high", 0.0) or 0.0),
                    "low": float(b.get("low", 0.0) or 0.0),
                    "volume": float(b.get("vol", 0.0) or 0.0),
                    "amount": float(b.get("amount", 0.0) or 0.0),
                }
            )

        if beg_d is not None and oldest_dt_in_page is not None and oldest_dt_in_page.date() < beg_d:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()]
    if beg_d is not None:
        df = df[df["datetime"].dt.date >= beg_d]
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"]).reset_index(drop=True)
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def fetch_kline_tdx_security(stock_code: str, symbol_6digit: str, klt: str, beg: str | None = None) -> pd.DataFrame:
    category = _tdx_klt_to_category(str(klt))
    if category is None:
        return pd.DataFrame()

    market = _tdx_market_from_prefixed_code(stock_code, symbol_6digit)
    if market is None:
        return pd.DataFrame()

    code6 = str(symbol_6digit or "")[-6:]
    if len(code6) != 6 or (not code6.isdigit()):
        return pd.DataFrame()

    beg_d = _parse_yyyymmdd_or_iso_to_date(beg)

    api = _tdx_get_api_cached()
    if api is None:
        return pd.DataFrame()

    page_size = 800
    max_pages = 400
    offset = 0
    rows: list[dict] = []

    for _ in range(max_pages):
        bars = api.get_security_bars(category, market, code6, offset, page_size) or []
        if not bars:
            break
        offset += page_size

        oldest_dt_in_page: datetime | None = None
        for b in bars:
            if not b:
                continue
            try:
                y = int(b.get("year", 0))
                m = int(b.get("month", 0))
                d = int(b.get("day", 0))
                hh = int(b.get("hour", 0))
                mm = int(b.get("minute", 0))
                dt = datetime(y, m, d, hh, mm)
            except Exception:
                dt_str = str(b.get("datetime") or "").strip()
                try:
                    dt = datetime.strptime(dt_str + ":00", "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue

            if oldest_dt_in_page is None or dt < oldest_dt_in_page:
                oldest_dt_in_page = dt

            if beg_d is not None and dt.date() < beg_d:
                continue

            vol = float(b.get("vol", 0.0) or 0.0)
            if str(klt) in ("101", "102", "103", "1", "5", "15", "30", "60"):
                vol *= 100.0

            rows.append(
                {
                    "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(b.get("open", 0.0) or 0.0),
                    "close": float(b.get("close", 0.0) or 0.0),
                    "high": float(b.get("high", 0.0) or 0.0),
                    "low": float(b.get("low", 0.0) or 0.0),
                    "volume": vol,
                    "amount": float(b.get("amount", 0.0) or 0.0),
                }
            )

        if beg_d is not None and oldest_dt_in_page is not None and oldest_dt_in_page.date() < beg_d:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()]
    if beg_d is not None:
        df = df[df["datetime"].dt.date >= beg_d]
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"]).reset_index(drop=True)
    if "amount" in df.columns:
        try:
            if (pd.to_numeric(df["amount"], errors="coerce").fillna(0) == 0).all():
                df = df.drop(columns=["amount"])
        except Exception:
            pass
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def fetch_index_list_tdx() -> pd.DataFrame:
    api = _tdx_connect_api()
    if api is None:
        return pd.DataFrame({"代码": [], "名称": []})
    try:
        rows = []
        for market, prefix in [(1, "sh"), (0, "sz")]:
            start = 0
            step = 1000
            while True:
                items = api.get_security_list(market, start) or []
                if not items:
                    break
                for it in items:
                    code = str((it or {}).get("code") or "").strip()
                    name = str((it or {}).get("name") or "").strip()
                    if len(code) != 6 or not code.isdigit():
                        continue
                    if market == 1:
                        if (not code.startswith("000")) and code != "999999":
                            continue
                    elif market == 0:
                        if not code.startswith("399"):
                            continue
                    else:
                        continue
                    rows.append({"代码": f"{prefix}{code}", "名称": name})
                start += step
        if not rows:
            return pd.DataFrame({"代码": [], "名称": []})
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["代码"]).reset_index(drop=True)
        return df
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def fetch_index_list_em() -> pd.DataFrame:
    fs = "m:1+s:2,m:0+t:5"
    fields = "f12,f14,f13"
    pn = 1
    pz = 1000

    rows = []
    total = None
    while True:
        params = {
            "pn": str(pn),
            "pz": str(pz),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
        payload = None
        for _ in range(3):
            payload = _em_clist_get(params, timeout=10)
            if payload:
                break
            time.sleep(0.6)
        payload = payload or {}
        data = payload.get("data") or {}
        if total is None:
            total = data.get("total")
        diff = data.get("diff") or []
        if not diff:
            break
        for item in diff:
            code = str(item.get("f12") or "").strip()
            name = str(item.get("f14") or "").strip()
            market = item.get("f13")
            if len(code) != 6 or (not code.isdigit()):
                continue
            prefix = None
            if market == 1:
                prefix = "sh"
            elif market == 0:
                prefix = "sz"
            if prefix is None:
                if code.startswith("399"):
                    prefix = "sz"
                else:
                    prefix = "sh"
            rows.append({"代码": f"{prefix}{code}", "名称": name})
        if total is not None and len(rows) >= int(total):
            break
        pn += 1
    if not rows:
        return pd.DataFrame({"代码": [], "名称": []})
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["代码"]).reset_index(drop=True)
    return df


def sync_index_list():
    logger.info("正在同步最新指数列表...")
    df_parts = []
    try:
        df_tdx = fetch_index_list_tdx()
        if df_tdx is not None and not df_tdx.empty:
            df_parts.append(df_tdx)
    except Exception:
        pass
    try:
        df_em = fetch_index_list_em()
        if df_em is not None and not df_em.empty:
            df_parts.append(df_em)
    except Exception:
        pass

    df = pd.concat(df_parts, ignore_index=True) if df_parts else pd.DataFrame({"代码": [], "名称": []})
    df["代码"] = df["代码"].astype(str)
    df["名称"] = df["名称"].astype(str)
    df = df.drop_duplicates(subset=["代码"], keep="first").reset_index(drop=True)
    essential = {
        "sh000001": "上证指数",
        "sh000300": "沪深300",
    }
    code_name_map = dict(zip(df["代码"].tolist(), df["名称"].tolist()))
    for c, n in essential.items():
        if c not in code_name_map:
            code_name_map[c] = n
    if "sh999999" in code_name_map and "sh000001" not in code_name_map:
        code_name_map["sh000001"] = code_name_map.get("sh999999") or essential["sh000001"]
    df = pd.DataFrame({"代码": list(code_name_map.keys()), "名称": list(code_name_map.values())})
    df = df.drop_duplicates(subset=["代码"], keep="first").reset_index(drop=True)

    client = get_db_client()
    try:
        client.execute(
            """
            CREATE TABLE IF NOT EXISTS index_info (
                code LowCardinality(String),
                name String,
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (code)
            """
        )

        existing = client.execute("SELECT DISTINCT code FROM index_info")
        existing_codes = set(r[0] for r in existing or [])

        current_codes = set(df["代码"].astype(str).tolist())
        new_codes = current_codes - existing_codes
        if not new_codes:
            logger.info("没有发现新指数或回填目标已存在")
            return

        code_name_map = dict(zip(df["代码"].astype(str).tolist(), df["名称"].astype(str).tolist()))
        now_dt = datetime.now()
        new_data = [{"code": c, "name": code_name_map.get(c, ""), "update_time": now_dt} for c in sorted(new_codes)]
        client.execute("INSERT INTO index_info (code, name, update_time) VALUES", new_data)
        logger.info(f"成功添加 {len(new_data)} 个新指数")
    finally:
        client.disconnect()

def sync_stock_list():
    """
    同步最新股票列表：获取最新列表，插入新股
    """
    logger.info("正在同步最新股票列表...")
    try:
        df = fetch_stock_list_em()
        if df.empty:
            logger.warning("获取股票列表为空！")
            return
            
        client = get_db_client()
        # 获取库中已有股票
        existing_stocks = client.execute("SELECT DISTINCT code FROM stock_info")
        # 现有库中的代码都带有前缀 (sh/sz/bj)，去掉前缀后与东方财富的代码对比
        existing_symbols = set([row[0][2:] for row in existing_stocks if len(row[0]) > 2])
        
        # 东方财富返回的是纯数字代码
        current_symbols = set(df['代码'].tolist())
        
        new_symbols = current_symbols - existing_symbols
        if new_symbols:
            logger.info(f"发现 {len(new_symbols)} 只新股，正在添加...")
            new_data = []
            now_dt = datetime.now()
            
            # 建立代码到名称的映射
            code_name_map = dict(zip(df['代码'], df['名称']))
            
            for symbol in new_symbols:
                name = code_name_map.get(symbol, '')
                # 根据规则添加前缀
                if symbol.startswith('6'):
                    code = f"sh{symbol}"
                elif symbol.startswith(('0', '3')):
                    code = f"sz{symbol}"
                elif symbol.startswith(('4', '8', '92')):
                    code = f"bj{symbol}"
                else:
                    # 未知规则，默认不加或记录日志，这里先尝试加 sz (或者根据实际情况调整)
                    # 实际上大部分A股都涵盖在上述规则中
                    logger.warning(f"未知市场规则: {symbol}, 跳过")
                    continue

                new_data.append({'code': code, 'name': name, 'update_time': now_dt})
            
            if new_data:
                client.execute("INSERT INTO stock_info (code, name, update_time) VALUES", new_data)
                logger.info(f"成功添加 {len(new_data)} 只新股")
            else:
                logger.info("没有有效的新股需要添加")
        else:
            logger.info("没有发现新股")
            
        client.disconnect()
    except Exception as e:
        logger.error(f"同步股票列表失败: {e}")

# 列映射
COL_MAPPING = {
    '日期': 'date',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'volume',
    '成交额': 'amount',
    '振幅': 'amplitude',
    '涨跌幅': 'pct_chg',
    '涨跌额': 'change_amt',
    '换手率': 'turnover'
}

# 分钟线列映射 (Eastmoney 接口 -> 数据库列)
MINUTE_EM_COL_MAPPING = {
    '时间': 'datetime',
    '日期': 'datetime',
    '时间戳': 'datetime',
    '开盘': 'open',
    '开盘价': 'open',
    '收盘': 'close',
    '收盘价': 'close',
    '最高': 'high',
    '最高价': 'high',
    '最低': 'low',
    '最低价': 'low',
    '成交量': 'volume',
    '成交额': 'amount',
    '成交金额': 'amount',
    '振幅': 'amplitude',
    '涨跌幅': 'pct_chg',
    '涨幅': 'pct_chg',
    '涨跌额': 'change_amt',
    '换手率': 'turnover'
}

def get_db_client():
    return Client(**DB_CONFIG)

def _upsert_table_max_dates(client, table_name: str, rows: list[tuple], is_minute: bool):
    if not rows:
        return 0
    now = datetime.now()
    batch = []
    if is_minute:
        for code, max_dt in rows:
            if max_dt is None:
                continue
            max_d = max_dt.date() if isinstance(max_dt, datetime) else None
            if max_d is None:
                continue
            batch.append(
                {
                    "table": str(table_name),
                    "code": str(code),
                    "max_date": max_d,
                    "max_dt": max_dt if isinstance(max_dt, datetime) else None,
                    "update_time": now,
                }
            )
    else:
        for code, max_d in rows:
            if max_d is None:
                continue
            batch.append(
                {
                    "table": str(table_name),
                    "code": str(code),
                    "max_date": max_d if isinstance(max_d, date) else None,
                    "max_dt": None,
                    "update_time": now,
                }
            )
    if not batch:
        return 0
    client.execute(
        "INSERT INTO table_max_dates (table, code, max_date, max_dt, update_time) VALUES",
        batch,
    )
    return int(len(batch))


def get_max_dates_safe(table_name, is_minute=False, expected_codes: int | None = None):
    """
    获取指定表中每只股票的最新日期，但忽略今日数据以解决缺口问题
    如果最近的记录是今天，则忽略它，取上一条记录的日期，确保能重新拉取今天的完整数据
    或者更激进一点：只取昨天及之前的最大日期
    """
    client = get_db_client()
    logger.info(f"正在获取 {table_name} 的有效最新日期 (忽略今日)...")
    
    time_col = 'datetime' if is_minute else 'date'
    
    # 获取今天零点
    today_str = date.today().strftime('%Y-%m-%d')
    
    try:
        if is_minute:
            meta_query = """
                SELECT code, max_dt
                FROM table_max_dates
                WHERE table = %(table)s
                  AND max_dt < toDateTime(%(today_dt)s)
            """
            meta_rows = client.execute(meta_query, {"table": str(table_name), "today_dt": f"{today_str} 00:00:00"})
        else:
            meta_query = """
                SELECT code, max_date
                FROM table_max_dates
                WHERE table = %(table)s
                  AND max_date < toDate(%(today)s)
            """
            meta_rows = client.execute(meta_query, {"table": str(table_name), "today": today_str})

        if meta_rows:
            if expected_codes is None or len(meta_rows) >= int(expected_codes * 0.9):
                return {row[0]: row[1] for row in meta_rows}
    except Exception:
        meta_rows = []

    # 查询小于今天日期的最大值
    # 这样如果之前只更新了今天的数据但缺失了历史，start_date 会回退到更早的时间，从而触发补全
    if is_minute:
        query = f"SELECT code, max(datetime) FROM {table_name} WHERE datetime < toDateTime('{today_str} 00:00:00') GROUP BY code"
    else:
        query = f"SELECT code, max(date) FROM {table_name} WHERE date < toDate('{today_str}') GROUP BY code"
        
    try:
        result = client.execute(query)
        try:
            _upsert_table_max_dates(client, table_name=str(table_name), rows=result, is_minute=bool(is_minute))
        except Exception:
            pass
        return {row[0]: row[1] for row in result}
    except Exception as e:
        logger.warning(f"获取 {table_name} 最大日期失败 (可能是表不存在): {e}")
        return {}

def _build_table_max_dates_row(table_name: str, df: pd.DataFrame):
    if df is None or df.empty:
        return None
    if "code" not in df.columns or len(df.index) <= 0:
        return None
    code = str(df["code"].iloc[0])
    if not code:
        return None
    now = datetime.now()
    if "datetime" in df.columns:
        max_dt = pd.to_datetime(df["datetime"], errors="coerce").max()
        if pd.isna(max_dt):
            return None
        return {
            "table": str(table_name),
            "code": code,
            "max_date": max_dt.date(),
            "max_dt": max_dt.to_pydatetime(),
            "update_time": now,
        }
    if "date" in df.columns:
        max_d = pd.to_datetime(df["date"], errors="coerce").dt.date.max()
        if pd.isna(max_d):
            return None
        return {
            "table": str(table_name),
            "code": code,
            "max_date": max_d,
            "max_dt": None,
            "update_time": now,
        }
    return None

from functools import wraps

def retry(max_retries=3, delay=1, backoff=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries == max_retries:
                        raise e
                    time.sleep(current_delay)
                    current_delay *= backoff
            return None
        return wrapper
    return decorator

def validate_data(df, period, allow_zero_amount: bool = False):
    if df is None or df.empty:
        return False
    # 指数等无成交额品种 amount 合法全 0，对应任务以 allow_zero_amount 放宽该校验
    if 'amount' in df.columns and not allow_zero_amount:
        if (df['amount'] == 0).all():
            return False
    time_col = 'date' if 'date' in df.columns else 'datetime'
    if time_col not in df.columns:
        return False
    if df[time_col].isnull().any():
        return False
    return True


def _infer_index_code_from_filename(path: str) -> str | None:
    name = os.path.basename(str(path or "")).strip()
    if not name:
        return None
    m = re.search(r"(?i)\b([A-Z]{2})\.(\d{6})\b", name)
    if not m:
        m = re.search(r"(?i)\b([A-Z]{2})(\d{6})\b", name)
    if not m:
        return None
    ex = str(m.group(1)).lower()
    code6 = str(m.group(2))
    if ex in {"sh", "sz"}:
        return f"{ex}{code6}"
    return None


def _prefixed_code_from_dot_code(dot_code: str) -> str | None:
    s = str(dot_code or "").strip()
    if not s:
        return None
    m = re.match(r"^\s*(\d{6})\.(\w+)\s*$", s, flags=re.IGNORECASE)
    if not m:
        return None
    code6 = str(m.group(1))
    ex = str(m.group(2)).strip().lower()
    if ex == "sh":
        return f"sh{code6}"
    if ex == "sz":
        return f"sz{code6}"
    if ex == "bj":
        return f"bj{code6}"
    return None


def _infer_etf_code_from_filename(path: str) -> str | None:
    name = os.path.basename(str(path or "")).strip()
    if not name:
        return None
    m = re.search(r"(?i)\b(\d{6})\.(SH|SZ|BJ)\b", name)
    if not m:
        return None
    code6 = str(m.group(1))
    ex = str(m.group(2)).strip().lower()
    if ex in {"sh", "sz", "bj"}:
        return f"{ex}{code6}"
    return None


def _read_csv_flexible(path: str) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb2312"]
    last_err: Exception | None = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, sep=None, engine="python")
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err is not None else RuntimeError("read_csv failed")


def _iter_read_csv_etf_minute(path: str, chunksize: int = 300_000):
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb2312"]
    last_err: Exception | None = None
    for enc in encodings:
        try:
            it = pd.read_csv(
                path,
                encoding=enc,
                sep=",",
                engine="c",
                chunksize=int(chunksize),
                low_memory=False,
            )
            for chunk in it:
                yield chunk
            return
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err is not None else RuntimeError("read_csv failed")


def _parse_nullable_date_series(s: pd.Series) -> pd.Series:
    if s is None:
        return pd.Series([], dtype=object)
    s2 = s.copy()
    s2 = s2.astype(str).str.strip()
    s2 = s2.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaT": pd.NA})

    mask8 = s2.notna() & s2.str.fullmatch(r"\d{8}")
    mask10_dash = s2.notna() & s2.str.fullmatch(r"\d{4}-\d{2}-\d{2}")
    mask10_slash = s2.notna() & s2.str.fullmatch(r"\d{4}/\d{2}/\d{2}")
    dt = pd.Series(pd.NaT, index=s2.index, dtype="datetime64[ns]")
    if bool(mask8.any()):
        dt.loc[mask8] = pd.to_datetime(s2.loc[mask8], format="%Y%m%d", errors="coerce")
    if bool(mask10_dash.any()):
        dt.loc[mask10_dash] = pd.to_datetime(s2.loc[mask10_dash], format="%Y-%m-%d", errors="coerce")
    if bool(mask10_slash.any()):
        dt.loc[mask10_slash] = pd.to_datetime(s2.loc[mask10_slash], format="%Y/%m/%d", errors="coerce")

    remaining = ~(mask8 | mask10_dash | mask10_slash)
    if bool(remaining.any()):
        try:
            dt.loc[remaining] = pd.to_datetime(s2.loc[remaining], errors="coerce", format="mixed")
        except Exception:
            dt.loc[remaining] = pd.to_datetime(s2.loc[remaining], errors="coerce")

    out = dt.dt.date.astype(object)
    out = out.where(dt.notna(), None)
    return out


def _normalize_index_minute_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    raw_cols = list(df.columns)
    cols_lower = {str(c).strip().lower(): c for c in raw_cols}

    rename_map = {}
    for src, dst in MINUTE_EM_COL_MAPPING.items():
        c = cols_lower.get(str(src).strip().lower())
        if c is not None:
            rename_map[c] = dst

    english_map = {
        "datetime": "datetime",
        "date": "datetime",
        "time": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "vol": "volume",
        "amount": "amount",
        "amt": "amount",
        "turnover": "turnover",
        "pct_chg": "pct_chg",
        "pctchg": "pct_chg",
        "chg_pct": "pct_chg",
        "amplitude": "amplitude",
    }
    for k, v in english_map.items():
        c = cols_lower.get(k)
        if c is not None:
            rename_map[c] = v

    df = df.rename(columns=rename_map).copy()

    if "datetime" not in df.columns:
        if "date" in df.columns and "time" in df.columns:
            dt = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
            df["datetime"] = dt
        elif "date" in df.columns:
            df["datetime"] = df["date"]
        else:
            if raw_cols:
                first_col = raw_cols[0]
                dt_try = pd.to_datetime(df[first_col], errors="coerce")
                if bool(dt_try.notna().any()):
                    df["datetime"] = df[first_col]
                else:
                    return pd.DataFrame()
            else:
                return pd.DataFrame()

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    if ("open" not in df.columns and "close" not in df.columns and "high" not in df.columns and "low" not in df.columns) and len(raw_cols) >= 5:
        candidate_cols = [c for c in raw_cols[1:] if c in df.columns]
        if len(candidate_cols) >= 4:
            tmp = df[candidate_cols[:6]].copy()
            for c in candidate_cols[:6]:
                tmp[c] = pd.to_numeric(tmp[c], errors="coerce")

            n = min(len(tmp), 200)
            if n > 0:
                a0, a1, a2, a3 = candidate_cols[0], candidate_cols[1], candidate_cols[2], candidate_cols[3]
                open_a = tmp[a0].iloc[:n]
                high_a = tmp[a1].iloc[:n]
                low_a = tmp[a2].iloc[:n]
                close_a = tmp[a3].iloc[:n]
                valid_a = (high_a >= open_a) & (high_a >= close_a) & (low_a <= open_a) & (low_a <= close_a)
                score_a = int(valid_a.fillna(False).sum())

                open_b = tmp[a0].iloc[:n]
                close_b = tmp[a1].iloc[:n]
                high_b = tmp[a2].iloc[:n]
                low_b = tmp[a3].iloc[:n]
                valid_b = (high_b >= open_b) & (high_b >= close_b) & (low_b <= open_b) & (low_b <= close_b)
                score_b = int(valid_b.fillna(False).sum())

                if score_a >= score_b:
                    df["open"] = tmp[a0]
                    df["high"] = tmp[a1]
                    df["low"] = tmp[a2]
                    df["close"] = tmp[a3]
                    if len(candidate_cols) >= 5:
                        df["volume"] = tmp[candidate_cols[4]]
                    if len(candidate_cols) >= 6:
                        df["amount"] = tmp[candidate_cols[5]]
                else:
                    df["open"] = tmp[a0]
                    df["close"] = tmp[a1]
                    df["high"] = tmp[a2]
                    df["low"] = tmp[a3]
                    if len(candidate_cols) >= 5:
                        df["volume"] = tmp[candidate_cols[4]]
                    if len(candidate_cols) >= 6:
                        df["amount"] = tmp[candidate_cols[5]]

    for col in ("open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("open", "close", "high", "low"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)

    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["volume"] = df["volume"].fillna(0.0)

    if "amount" not in df.columns:
        df["amount"] = 0.0
    df["amount"] = df["amount"].fillna(0.0)

    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"]).reset_index(drop=True)

    if "pct_chg" not in df.columns or df["pct_chg"].isna().all():
        pre_close = df["close"].shift(1)
        pre_close = pre_close.fillna(df["open"])
        denom = pre_close.replace(0, pd.NA)
        pct = (df["close"] - pre_close) / denom * 100.0
        df["pct_chg"] = pd.to_numeric(pct, errors="coerce").fillna(0.0).astype("float64")

    if "amplitude" not in df.columns or df["amplitude"].isna().all():
        pre_close = df["close"].shift(1)
        pre_close = pre_close.fillna(df["open"])
        denom = pre_close.replace(0, pd.NA)
        amp = (df["high"] - df["low"]) / denom * 100.0
        df["amplitude"] = pd.to_numeric(amp, errors="coerce").fillna(0.0).astype("float64")

    keep = ["datetime", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
    for col in keep:
        if col not in df.columns:
            df[col] = 0.0
    return df[keep].copy()


def _normalize_etf_minute_df(df: pd.DataFrame) -> pd.DataFrame:
    return _normalize_index_minute_df(df)


def _aggregate_minute_bars(df_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df_1m is None or df_1m.empty:
        return pd.DataFrame()
    minutes = int(minutes)
    if minutes <= 1:
        return df_1m.copy()

    df = df_1m.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()].sort_values("datetime")
    if df.empty:
        return pd.DataFrame()

    df["bucket"] = df["datetime"].dt.floor(f"{minutes}min")
    g = df.groupby("bucket", sort=True)
    out = pd.DataFrame(
        {
            "datetime": g["datetime"].min(),
            "open": g["open"].first(),
            "close": g["close"].last(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "volume": g["volume"].sum(),
            "amount": g["amount"].sum(),
        }
    ).reset_index(drop=True)

    pre_close = out["close"].shift(1).fillna(out["open"])
    denom = pre_close.replace(0, pd.NA)
    out["pct_chg"] = ((out["close"] - pre_close) / denom * 100.0).fillna(0.0)
    out["amplitude"] = ((out["high"] - out["low"]) / denom * 100.0).fillna(0.0)
    return out[["datetime", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]]


def _ensure_index_minute_tables_exist(periods: list[int] | None = None):
    periods = periods or [1, 5, 15, 30, 60]
    client = get_db_client()
    try:
        for m in periods:
            table_name = f"index_{int(m)}min"
            client.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    datetime DateTime CODEC(Delta, ZSTD(1)),
                    code LowCardinality(String),
                    open Float32 CODEC(ZSTD(1)),
                    close Float32 CODEC(ZSTD(1)),
                    high Float32 CODEC(ZSTD(1)),
                    low Float32 CODEC(ZSTD(1)),
                    volume Float64 CODEC(ZSTD(1)),
                    amount Float64 CODEC(ZSTD(1)),
                    pct_chg Float32 CODEC(ZSTD(1)),
                    amplitude Float32 CODEC(ZSTD(1))
                ) ENGINE = ReplacingMergeTree()
                PARTITION BY toYYYYMM(datetime)
                ORDER BY (code, datetime)
                """
            )
    finally:
        client.disconnect()


def _insert_df_by_month_partitions(
    client: Client,
    table: str,
    df: pd.DataFrame,
    cols: list[str],
    max_rows_per_batch: int = 200_000,
):
    if df is None or df.empty:
        return 0
    if "datetime" not in df.columns:
        return 0

    dt = pd.to_datetime(df["datetime"], errors="coerce")
    mask = dt.notna()
    if not bool(mask.any()):
        return 0
    df2 = df.loc[mask].copy()
    dt2 = dt.loc[mask]
    ym = (dt2.dt.year.astype("int64") * 100 + dt2.dt.month.astype("int64")).astype("int64")
    df2["_ym"] = ym.values

    total = 0
    for part_ym, part_df in df2.groupby("_ym", sort=True):
        part_df = part_df.drop(columns=["_ym"])
        if max_rows_per_batch and int(max_rows_per_batch) > 0 and len(part_df) > int(max_rows_per_batch):
            step = int(max_rows_per_batch)
            for i in range(0, len(part_df), step):
                chunk = part_df.iloc[i : i + step]
                rows = chunk[cols].to_dict("records")
                client.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES",
                    rows,
                )
                total += len(rows)
        else:
            rows = part_df[cols].to_dict("records")
            client.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES",
                rows,
            )
            total += len(rows)
    return total


def _insert_df_by_month_partitions_date(
    client: Client,
    table: str,
    df: pd.DataFrame,
    cols: list[str],
    max_rows_per_batch: int = 200_000,
):
    if df is None or df.empty:
        return 0
    if "date" not in df.columns:
        return 0

    d = pd.to_datetime(df["date"], errors="coerce").dt.date
    mask = d.notna()
    if not bool(mask.any()):
        return 0
    df2 = df.loc[mask].copy()
    d2 = pd.to_datetime(df2["date"], errors="coerce")
    ym = (d2.dt.year.astype("int64") * 100 + d2.dt.month.astype("int64")).astype("int64")
    df2["_ym"] = ym.values

    total = 0
    for _, part_df in df2.groupby("_ym", sort=True):
        part_df = part_df.drop(columns=["_ym"])
        if max_rows_per_batch and int(max_rows_per_batch) > 0 and len(part_df) > int(max_rows_per_batch):
            step = int(max_rows_per_batch)
            for i in range(0, len(part_df), step):
                chunk = part_df.iloc[i : i + step]
                rows = chunk[cols].to_dict("records")
                client.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES",
                    rows,
                )
                total += len(rows)
        else:
            rows = part_df[cols].to_dict("records")
            client.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES",
                rows,
            )
            total += len(rows)
    return total


def import_index_minute_csvs(
    csv_paths: list[str],
    overwrite: bool = True,
    gen_periods: list[int] | None = None,
):
    csv_paths = [str(p) for p in (csv_paths or []) if str(p or "").strip()]
    if not csv_paths:
        raise RuntimeError("csv_paths 为空")

    gen_periods = gen_periods or [5, 15, 30, 60]
    gen_periods = [int(x) for x in gen_periods if int(x) > 1]

    ensure_tables_exist()
    _ensure_index_minute_tables_exist([1, *gen_periods])

    client = get_db_client()
    try:
        for path in csv_paths:
            code = _infer_index_code_from_filename(path)
            if not code:
                raise RuntimeError(f"无法从文件名识别指数代码: {path}")

            df_raw = _read_csv_flexible(path)
            df_1m = _normalize_index_minute_df(df_raw)
            if df_1m.empty:
                logger.warning(f"{path} 解析后为空，跳过")
                continue

            df_1m = df_1m.copy()
            df_1m["code"] = code

            df_1m["datetime"] = pd.to_datetime(df_1m["datetime"], errors="coerce")
            df_1m = df_1m[df_1m["datetime"].notna()].copy()
            if df_1m.empty:
                logger.warning(f"{path} datetime 全部为空，跳过")
                continue

            df_1m["datetime"] = df_1m["datetime"].dt.to_pydatetime()
            for c in ("open", "close", "high", "low", "pct_chg", "amplitude"):
                if c in df_1m.columns:
                    df_1m[c] = pd.to_numeric(df_1m[c], errors="coerce").fillna(0.0).astype("float64")
            for c in ("volume", "amount"):
                if c in df_1m.columns:
                    df_1m[c] = pd.to_numeric(df_1m[c], errors="coerce").fillna(0.0).astype("float64")

            dt_min = df_1m["datetime"].min()
            dt_max = df_1m["datetime"].max()
            if overwrite and dt_min is not None and dt_max is not None:
                client.execute(
                    """
                    ALTER TABLE index_1min DELETE WHERE code=%(code)s AND datetime >= %(b)s AND datetime <= %(e)s
                    """,
                    {"code": code, "b": dt_min, "e": dt_max},
                )

            cols_1m = ["datetime", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
            inserted_1m = _insert_df_by_month_partitions(
                client=client,
                table="index_1min",
                df=df_1m,
                cols=cols_1m,
            )
            logger.info(f"index_1min 导入完成 {code}: rows={inserted_1m} range=[{dt_min},{dt_max}]")

            for m in gen_periods:
                df_n = _aggregate_minute_bars(df_1m, m)
                if df_n.empty:
                    continue
                df_n = df_n.copy()
                df_n["code"] = code
                df_n["datetime"] = pd.to_datetime(df_n["datetime"], errors="coerce")
                df_n = df_n[df_n["datetime"].notna()].copy()
                if df_n.empty:
                    continue
                df_n["datetime"] = df_n["datetime"].dt.to_pydatetime()
                for c in ("open", "close", "high", "low", "pct_chg", "amplitude"):
                    if c in df_n.columns:
                        df_n[c] = pd.to_numeric(df_n[c], errors="coerce").fillna(0.0).astype("float64")
                for c in ("volume", "amount"):
                    if c in df_n.columns:
                        df_n[c] = pd.to_numeric(df_n[c], errors="coerce").fillna(0.0).astype("float64")
                table = f"index_{int(m)}min"
                n_min = df_n["datetime"].min()
                n_max = df_n["datetime"].max()
                if overwrite and n_min is not None and n_max is not None:
                    client.execute(
                        f"ALTER TABLE {table} DELETE WHERE code=%(code)s AND datetime >= %(b)s AND datetime <= %(e)s",
                        {"code": code, "b": n_min, "e": n_max},
                    )
                cols_n = ["datetime", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
                inserted_n = _insert_df_by_month_partitions(
                    client=client,
                    table=table,
                    df=df_n,
                    cols=cols_n,
                )
                logger.info(f"{table} 生成完成 {code}: rows={inserted_n} range=[{n_min},{n_max}]")
    finally:
        client.disconnect()


def _ensure_etf_tables_exist():
    client = get_db_client()
    try:
        client.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_info (
                code LowCardinality(String),
                dot_code String,
                short_name String,
                expanded_name String,
                full_name String,
                tracking_index_dot_code String,
                tracking_index_name String,
                establish_date Nullable(Date),
                list_date Nullable(Date),
                list_status String,
                exchange LowCardinality(String),
                manager String,
                custodian String,
                management_fee_rate Nullable(Float32),
                etf_type String,
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (code)
            """
        )
        client.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_benchmark_index (
                index_dot_code LowCardinality(String),
                index_full_name String,
                index_short_name String,
                publisher String,
                publish_date Nullable(Date),
                base_date Nullable(Date),
                base_point Nullable(Float32),
                constituent_adjust_cycle String,
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (index_dot_code)
            """
        )
        client.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_1min (
                datetime DateTime CODEC(Delta, ZSTD(1)),
                code LowCardinality(String),
                open Float32 CODEC(ZSTD(1)),
                close Float32 CODEC(ZSTD(1)),
                high Float32 CODEC(ZSTD(1)),
                low Float32 CODEC(ZSTD(1)),
                volume Float64 CODEC(ZSTD(1)),
                amount Float64 CODEC(ZSTD(1)),
                pct_chg Float32 CODEC(ZSTD(1)),
                amplitude Float32 CODEC(ZSTD(1))
            ) ENGINE = ReplacingMergeTree()
            PARTITION BY toYYYYMM(datetime)
            ORDER BY (code, datetime)
            """
        )
    finally:
        client.disconnect()


def _ensure_etf_kline_tables_exist():
    client = get_db_client()
    try:
        for table_name in ("etf_daily", "etf_weekly", "etf_monthly"):
            client.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    date Date CODEC(Delta, ZSTD(1)),
                    code LowCardinality(String),
                    open Float32 CODEC(ZSTD(1)),
                    close Float32 CODEC(ZSTD(1)),
                    high Float32 CODEC(ZSTD(1)),
                    low Float32 CODEC(ZSTD(1)),
                    volume Float64 CODEC(ZSTD(1)),
                    amount Float64 CODEC(ZSTD(1)),
                    pct_chg Float32 CODEC(ZSTD(1)),
                    amplitude Float32 CODEC(ZSTD(1))
                ) ENGINE = ReplacingMergeTree()
                PARTITION BY toYYYYMM(date)
                ORDER BY (code, date)
                """
            )
    finally:
        client.disconnect()


def _compute_pct_amplitude_for_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    pre_close = None
    pct_list = []
    amp_list = []
    for _, r in df.iterrows():
        o = float(r.get("open") or 0.0)
        c = float(r.get("close") or 0.0)
        h = float(r.get("high") or 0.0)
        l = float(r.get("low") or 0.0)
        if pre_close is None or pre_close <= 0:
            pre_close = o if o > 0 else (c if c > 0 else 0.0)
        if pre_close and pre_close > 0:
            pct = (c - pre_close) / pre_close * 100.0
            amp = (h - l) / pre_close * 100.0
        else:
            pct = 0.0
            amp = 0.0
        pct_list.append(float(pct))
        amp_list.append(float(amp))
        if c and c > 0:
            pre_close = c
    df["pct_chg"] = pct_list
    df["amplitude"] = amp_list
    return df


def import_etf_kline_tdx(
    etf_codes: list[str] | None = None,
    overwrite: bool = False,
    include_periods: list[str] | None = None,
):
    include_periods = include_periods or ["101", "102", "103"]
    include_periods = [str(x).strip() for x in include_periods if str(x).strip()]
    klt_to_table = {"101": "etf_daily", "102": "etf_weekly", "103": "etf_monthly"}
    include_periods = [p for p in include_periods if p in klt_to_table]
    if not include_periods:
        raise RuntimeError("include_periods 为空")

    _ensure_etf_tables_exist()
    _ensure_etf_kline_tables_exist()

    client = get_db_client()
    try:
        codes = etf_codes
        if codes is None:
            try:
                codes = [r[0] for r in client.execute("SELECT DISTINCT code FROM etf_info ORDER BY code")]
            except Exception:
                codes = []
        codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
        if not codes:
            raise RuntimeError("未找到 etf_info 的 code 列表")

        for i, code in enumerate(codes, start=1):
            symbol_6 = code[-6:]
            if len(symbol_6) != 6 or (not symbol_6.isdigit()):
                continue
            for klt in include_periods:
                table = klt_to_table[klt]
                df = fetch_kline_tdx_security(code, symbol_6, klt, beg=None)
                if df is None or df.empty:
                    continue
                dt = pd.to_datetime(df["datetime"], errors="coerce")
                df2 = df.copy()
                df2["__dt"] = dt
                df2 = df2[df2["__dt"].notna()].copy()
                if df2.empty:
                    continue
                df2["date"] = df2["__dt"].dt.date
                df2 = df2.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
                df2 = df2.drop(columns=["__dt"], errors="ignore")
                for c in ("open", "close", "high", "low", "volume", "amount"):
                    if c in df2.columns:
                        df2[c] = pd.to_numeric(df2[c], errors="coerce").fillna(0.0).astype("float64")
                df2 = _compute_pct_amplitude_for_ohlc_df(df2)
                df2["code"] = code

                if overwrite:
                    try:
                        client.execute(f"ALTER TABLE {table} DELETE WHERE code=%(code)s", {"code": code})
                    except Exception:
                        pass

                cols = ["date", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
                inserted = _insert_df_by_month_partitions_date(client, table, df2, cols, max_rows_per_batch=200_000)
                logger.info(f"{table} 通达信导入 {i}/{len(codes)} {code} rows={inserted}")
    finally:
        client.disconnect()


def import_etf_data(
    etf_minute_dir: str,
    etf_info_csv: str,
    etf_benchmark_csv: str,
    overwrite: bool = False,
):
    minute_dir = str(etf_minute_dir or "").strip() or "ETF_1min_2005_2022"
    info_path = str(etf_info_csv or "").strip() or "ETF基础信息列表.csv"
    bench_path = str(etf_benchmark_csv or "").strip() or "ETF基准指数列表.csv"

    minute_dir = os.path.abspath(minute_dir)
    info_path = os.path.abspath(info_path)
    bench_path = os.path.abspath(bench_path)

    _ensure_etf_tables_exist()

    now_dt = datetime.now()
    client = get_db_client()
    try:
        if os.path.exists(bench_path):
            df_bench = _read_csv_flexible(bench_path)
            if df_bench is not None and not df_bench.empty:
                df_bench = df_bench.copy()
                cols_lower = {str(c).strip().lower(): c for c in df_bench.columns}
                def _c(name: str):
                    return cols_lower.get(str(name).strip().lower())
                c_code = _c("指数代码")
                c_full = _c("指数全称")
                c_short = _c("指数简称")
                c_pub = _c("指数发布机构")
                c_pub_date = _c("指数发布日期")
                c_base_date = _c("指数基日")
                c_base_point = _c("指数基点")
                c_cycle = _c("指数成份证券调整周期")
                keep_cols = [c for c in [c_code, c_full, c_short, c_pub, c_pub_date, c_base_date, c_base_point, c_cycle] if c]
                df_bench = df_bench[keep_cols].copy() if keep_cols else pd.DataFrame()
                if not df_bench.empty:
                    df_bench = df_bench.rename(
                        columns={
                            c_code: "index_dot_code",
                            c_full: "index_full_name",
                            c_short: "index_short_name",
                            c_pub: "publisher",
                            c_pub_date: "publish_date",
                            c_base_date: "base_date",
                            c_base_point: "base_point",
                            c_cycle: "constituent_adjust_cycle",
                        }
                    )
                    for c in ["index_dot_code", "index_full_name", "index_short_name", "publisher", "constituent_adjust_cycle"]:
                        if c in df_bench.columns:
                            df_bench[c] = df_bench[c].fillna("").astype(str)
                    for c in ["publish_date", "base_date"]:
                        if c in df_bench.columns:
                            df_bench[c] = _parse_nullable_date_series(df_bench[c])
                    if "base_point" in df_bench.columns:
                        df_bench["base_point"] = pd.to_numeric(df_bench["base_point"], errors="coerce")
                        df_bench["base_point"] = df_bench["base_point"].where(df_bench["base_point"].notna(), None)
                    df_bench["update_time"] = now_dt
                    cols = [
                        "index_dot_code",
                        "index_full_name",
                        "index_short_name",
                        "publisher",
                        "publish_date",
                        "base_date",
                        "base_point",
                        "constituent_adjust_cycle",
                        "update_time",
                    ]
                    rows = df_bench[cols].to_dict("records")
                    if rows:
                        client.execute(f"INSERT INTO etf_benchmark_index ({','.join(cols)}) VALUES", rows)
                        logger.info(f"etf_benchmark_index 导入完成 rows={len(rows)}")

        if os.path.exists(info_path):
            df_info = _read_csv_flexible(info_path)
            if df_info is not None and not df_info.empty:
                df_info = df_info.copy()
                cols_lower = {str(c).strip().lower(): c for c in df_info.columns}
                def _c(name: str):
                    return cols_lower.get(str(name).strip().lower())
                c_code = _c("ETF代码")
                c_short = _c("ETF简称")
                c_expanded = _c("扩位简称")
                c_full = _c("基金全称")
                c_track_code = _c("跟踪指数代码")
                c_track_name = _c("跟踪指数名称")
                c_est = _c("设立日期")
                c_list = _c("上市日期")
                c_status = _c("上市状态")
                c_ex = _c("交易所")
                c_mgr = _c("基金管理人")
                c_cust = _c("基金托管人")
                c_fee = _c("管理费率")
                c_type = _c("ETF类型")
                keep_cols = [c for c in [c_code, c_short, c_expanded, c_full, c_track_code, c_track_name, c_est, c_list, c_status, c_ex, c_mgr, c_cust, c_fee, c_type] if c]
                df_info = df_info[keep_cols].copy() if keep_cols else pd.DataFrame()
                if not df_info.empty:
                    df_info = df_info.rename(
                        columns={
                            c_code: "dot_code",
                            c_short: "short_name",
                            c_expanded: "expanded_name",
                            c_full: "full_name",
                            c_track_code: "tracking_index_dot_code",
                            c_track_name: "tracking_index_name",
                            c_est: "establish_date",
                            c_list: "list_date",
                            c_status: "list_status",
                            c_ex: "exchange",
                            c_mgr: "manager",
                            c_cust: "custodian",
                            c_fee: "management_fee_rate",
                            c_type: "etf_type",
                        }
                    )
                    df_info["code"] = df_info["dot_code"].apply(_prefixed_code_from_dot_code)
                    df_info = df_info[df_info["code"].notna()].copy()
                    for c in [
                        "dot_code",
                        "short_name",
                        "expanded_name",
                        "full_name",
                        "tracking_index_dot_code",
                        "tracking_index_name",
                        "list_status",
                        "exchange",
                        "manager",
                        "custodian",
                        "etf_type",
                    ]:
                        if c in df_info.columns:
                            df_info[c] = df_info[c].fillna("").astype(str)
                    for c in ["establish_date", "list_date"]:
                        if c in df_info.columns:
                            df_info[c] = _parse_nullable_date_series(df_info[c])
                    if "management_fee_rate" in df_info.columns:
                        df_info["management_fee_rate"] = pd.to_numeric(df_info["management_fee_rate"], errors="coerce")
                        df_info["management_fee_rate"] = df_info["management_fee_rate"].where(df_info["management_fee_rate"].notna(), None)
                    df_info["update_time"] = now_dt
                    cols = [
                        "code",
                        "dot_code",
                        "short_name",
                        "expanded_name",
                        "full_name",
                        "tracking_index_dot_code",
                        "tracking_index_name",
                        "establish_date",
                        "list_date",
                        "list_status",
                        "exchange",
                        "manager",
                        "custodian",
                        "management_fee_rate",
                        "etf_type",
                        "update_time",
                    ]
                    rows = df_info[cols].to_dict("records")
                    if rows:
                        client.execute(f"INSERT INTO etf_info ({','.join(cols)}) VALUES", rows)
                        logger.info(f"etf_info 导入完成 rows={len(rows)}")

        if not os.path.isdir(minute_dir):
            raise RuntimeError(f"ETF分钟目录不存在: {minute_dir}")

        files = [os.path.join(minute_dir, f) for f in os.listdir(minute_dir) if str(f).lower().endswith(".csv")]
        files.sort()
        total_files = len(files)
        if total_files == 0:
            logger.warning(f"ETF分钟目录为空: {minute_dir}")
            return

        cols_1m = ["datetime", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
        for i, path in enumerate(files, start=1):
            code = _infer_etf_code_from_filename(path)
            if not code:
                continue
            if overwrite:
                client.execute("ALTER TABLE etf_1min DELETE WHERE code=%(code)s", {"code": code})

            inserted = 0
            for chunk in _iter_read_csv_etf_minute(path):
                df_1m = _normalize_etf_minute_df(chunk)
                if df_1m.empty:
                    continue
                df_1m = df_1m.copy()
                df_1m["code"] = code
                df_1m["datetime"] = pd.to_datetime(df_1m["datetime"], errors="coerce")
                df_1m = df_1m[df_1m["datetime"].notna()].copy()
                if df_1m.empty:
                    continue
                df_1m["datetime"] = df_1m["datetime"].dt.to_pydatetime()
                for c in ("open", "close", "high", "low", "pct_chg", "amplitude"):
                    if c in df_1m.columns:
                        df_1m[c] = pd.to_numeric(df_1m[c], errors="coerce").fillna(0.0).astype("float64")
                for c in ("volume", "amount"):
                    if c in df_1m.columns:
                        df_1m[c] = pd.to_numeric(df_1m[c], errors="coerce").fillna(0.0).astype("float64")
                inserted += _insert_df_by_month_partitions(client=client, table="etf_1min", df=df_1m, cols=cols_1m)

            logger.info(f"etf_1min 导入完成 {i}/{total_files} {code} file={os.path.basename(path)} rows={inserted}")
    finally:
        client.disconnect()


def _ensure_etf_minute_tables_exist(periods: list[int] | None = None):
    periods = periods or [1, 5, 15, 30, 60]
    periods = [int(p) for p in periods if int(p) > 0]
    client = get_db_client()
    try:
        for m in periods:
            table_name = f"etf_{int(m)}min"
            client.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    datetime DateTime CODEC(Delta, ZSTD(1)),
                    code LowCardinality(String),
                    open Float32 CODEC(ZSTD(1)),
                    close Float32 CODEC(ZSTD(1)),
                    high Float32 CODEC(ZSTD(1)),
                    low Float32 CODEC(ZSTD(1)),
                    volume Float64 CODEC(ZSTD(1)),
                    amount Float64 CODEC(ZSTD(1)),
                    pct_chg Float32 CODEC(ZSTD(1)),
                    amplitude Float32 CODEC(ZSTD(1))
                ) ENGINE = ReplacingMergeTree()
                PARTITION BY toYYYYMM(datetime)
                ORDER BY (code, datetime)
                """
            )
    finally:
        client.disconnect()


def _build_ohlc_pcts_for_sorted_rows(
    rows: list[tuple],
    pre_close_by_code: dict[str, float] | None = None,
):
    pre_close_by_code = pre_close_by_code or {}
    out = []
    for code, dt_value, o, c, h, l, vol, amt in rows:
        o_f = float(o) if o is not None else 0.0
        c_f = float(c) if c is not None else 0.0
        h_f = float(h) if h is not None else 0.0
        l_f = float(l) if l is not None else 0.0
        vol_f = float(vol) if vol is not None else 0.0
        amt_f = float(amt) if amt is not None else 0.0

        pre_close = pre_close_by_code.get(code)
        if pre_close is None or pre_close <= 0:
            pre_close = o_f if o_f > 0 else (c_f if c_f > 0 else 0.0)

        if pre_close and pre_close > 0:
            pct_chg = (c_f - pre_close) / pre_close * 100.0
            amplitude = (h_f - l_f) / pre_close * 100.0
        else:
            pct_chg = 0.0
            amplitude = 0.0

        if c_f and c_f > 0:
            pre_close_by_code[code] = c_f

        out.append(
            {
                "datetime": dt_value,
                "code": code,
                "open": o_f,
                "close": c_f,
                "high": h_f,
                "low": l_f,
                "volume": vol_f,
                "amount": amt_f,
                "pct_chg": float(pct_chg),
                "amplitude": float(amplitude),
            }
        )
    return out, pre_close_by_code


def _infer_minute_timestamp_shift_from_hhmm(min_hhmm: int | None, max_hhmm: int | None) -> int:
    if max_hhmm is None and min_hhmm is None:
        return 0
    try:
        max_v = int(max_hhmm) if max_hhmm is not None else None
        min_v = int(min_hhmm) if min_hhmm is not None else None
    except Exception:
        return 0

    if max_v in {1500, 1130}:
        return 1
    if max_v == 1459:
        return 0
    if min_v in {931, 925} and max_v in {1500, 1130}:
        return 1
    if min_v == 930:
        return 0
    return 0


def _infer_etf_1m_timestamp_shift_minutes(client: Client, codes: list[str], ym: int) -> int:
    if client is None or not codes:
        return 0
    code0 = str(codes[0])
    try:
        rows = client.execute(
            """
            SELECT
              min(toHour(datetime) * 100 + toMinute(datetime)) AS min_hhmm,
              max(toHour(datetime) * 100 + toMinute(datetime)) AS max_hhmm
            FROM etf_1min
            WHERE code=%(code)s
              AND toYYYYMM(datetime)=%(ym)s
            """,
            {"code": code0, "ym": int(ym)},
        )
    except Exception:
        return 0
    if not rows:
        return 0
    min_hhmm, max_hhmm = rows[0] if rows[0] else (None, None)
    return _infer_minute_timestamp_shift_from_hhmm(min_hhmm, max_hhmm)


def build_etf_minute_aggregates_from_1m(
    gen_periods: list[int] | None = None,
    overwrite: bool = True,
    etf_codes: list[str] | None = None,
    start_ym: int | None = None,
    end_ym: int | None = None,
    flush_rows: int = 200_000,
):
    gen_periods = gen_periods or [5, 15, 30, 60]
    gen_periods = [int(x) for x in gen_periods if int(x) > 1]
    if not gen_periods:
        raise RuntimeError("gen_periods 为空")

    _ensure_etf_minute_tables_exist([1, *gen_periods])

    client = get_db_client()
    try:
        codes = etf_codes
        if codes is None:
            try:
                codes = [r[0] for r in client.execute("SELECT DISTINCT code FROM etf_1min ORDER BY code")]
            except Exception:
                codes = []
        codes = [str(c) for c in (codes or []) if str(c or "").strip()]
        if not codes:
            raise RuntimeError("未找到 etf_1min 的 code 列表")

        if start_ym is None or end_ym is None:
            try:
                row = client.execute(
                    "SELECT min(toYYYYMM(datetime)), max(toYYYYMM(datetime)) FROM etf_1min WHERE code IN %(codes)s",
                    {"codes": codes},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                raise RuntimeError("无法从 etf_1min 推断月份范围")
            min_ym = int(row[0][0])
            max_ym = int(row[0][1])
            start_ym = min_ym if start_ym is None else int(start_ym)
            end_ym = max_ym if end_ym is None else int(end_ym)

        start_ym = int(start_ym)
        end_ym = int(end_ym)
        if start_ym > end_ym:
            start_ym, end_ym = end_ym, start_ym

        cols = ["datetime", "code", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]

        pre_close_by_period: dict[int, dict[str, float]] = {m: {} for m in gen_periods}
        for ym in _iter_ym_range(start_ym, end_ym):
            begin_d, end_d = _ym_to_month_bounds(int(ym))
            begin_dt = datetime(begin_d.year, begin_d.month, 1, 0, 0, 0)
            next_ym = _shift_ym(int(ym), 1)
            next_begin_d, _ = _ym_to_month_bounds(int(next_ym))
            end_next_dt = datetime(next_begin_d.year, next_begin_d.month, 1, 0, 0, 0)

            shift_min = _infer_etf_1m_timestamp_shift_minutes(client, codes, int(ym))
            for m in gen_periods:
                table = f"etf_{int(m)}min"
                if overwrite:
                    client.execute(
                        f"""
                        ALTER TABLE {table}
                        DELETE WHERE code IN %(codes)s AND datetime >= toDateTime(%(b)s) AND datetime < toDateTime(%(e)s)
                        """,
                        {"codes": codes, "b": begin_dt, "e": end_next_dt},
                    )

                try:
                    rows = client.execute(
                        f"""
                        SELECT
                            code,
                            toStartOfInterval(dt_for_bucket, INTERVAL {int(m)} minute) AS dt,
                            argMin(open, datetime) AS open,
                            argMax(close, datetime) AS close,
                            max(high) AS high,
                            min(low) AS low,
                            sum(volume) AS volume,
                            sum(amount) AS amount
                        FROM
                        (
                            SELECT
                                code,
                                open,
                                close,
                                high,
                                low,
                                volume,
                                amount,
                                datetime,
                                if(%(shift)s = 1, datetime - INTERVAL 1 minute, datetime) AS dt_shifted,
                                if(
                                    toHour(datetime) = 9 AND toMinute(datetime) < 30,
                                    toDateTime(toDate(datetime)) + INTERVAL 9 hour + INTERVAL 30 minute,
                                    dt_shifted
                                ) AS dt_for_bucket
                            FROM etf_1min
                            WHERE code IN %(codes)s
                              AND toYYYYMM(datetime) = %(ym)s
                        )
                        WHERE
                          (
                            (toHour(dt_for_bucket) = 9 AND toMinute(dt_for_bucket) >= 30)
                            OR toHour(dt_for_bucket) = 10
                            OR (toHour(dt_for_bucket) = 11 AND toMinute(dt_for_bucket) < 30)
                            OR toHour(dt_for_bucket) = 13
                            OR toHour(dt_for_bucket) = 14
                          )
                        GROUP BY code, dt
                        ORDER BY code, dt
                        """,
                        {"codes": codes, "ym": int(ym), "shift": int(shift_min)},
                    )
                except Exception:
                    rows = []

                if not rows:
                    continue

                batch = []
                inserted = 0
                computed, pre_close_by_period[m] = _build_ohlc_pcts_for_sorted_rows(rows, pre_close_by_period[m])
                for r in computed:
                    batch.append(r)
                    if flush_rows and len(batch) >= int(flush_rows):
                        client.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES", batch)
                        inserted += len(batch)
                        batch = []

                if batch:
                    client.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES", batch)
                    inserted += len(batch)

                logger.info(f"{table} 聚合完成 ym={ym} rows={inserted}")
    finally:
        client.disconnect()


_QFQ_FACTOR_CACHE = {}
_HFQ_FACTOR_CACHE = {}


def _get_table_columns(client, table: str, database: str | None = None):
    db_expr = "currentDatabase()" if database is None else "%(db)s"
    query = f"""
        SELECT name
        FROM system.columns
        WHERE database = {db_expr}
          AND table = %(table)s
        ORDER BY position ASC
    """
    params = {"table": table}
    if database is not None:
        params["db"] = database
    try:
        rows = client.execute(query, params)
        return [r[0] for r in rows]
    except Exception:
        return []

def _parse_ymd(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None
    return None

def _pick_column(cols_lower_map, candidates):
    for name in candidates:
        col = cols_lower_map.get(str(name).lower())
        if col:
            return col
    return None

def _get_factor_for_date(trading_dates, factor_by_date, d: date):
    if not factor_by_date:
        return 1.0
    factor = factor_by_date.get(d)
    if factor is not None:
        return float(factor)
    if trading_dates:
        idx = bisect_right(trading_dates, d) - 1
        if idx >= 0:
            prev_d = trading_dates[idx]
            prev_factor = factor_by_date.get(prev_d)
            if prev_factor is not None:
                return float(prev_factor)
    return 1.0

def _get_qfq_factors_by_date(client, normalized_code: str, begin_date: str, end_date: str):
    begin_dt = _parse_ymd(begin_date) or date(1990, 1, 1)
    end_dt = _parse_ymd(end_date) or date.today()
    begin_lookback_dt = begin_dt - timedelta(days=120)
    begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
    end_key = end_dt.strftime("%Y-%m-%d")
    cache_key = (normalized_code, begin_lookback, end_key)
    cached = _QFQ_FACTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    div_cols = _get_table_columns(client, "stock_dividend")
    div_cols_lower = {c.lower(): c for c in div_cols}
    div_code_col = _pick_column(div_cols_lower, ["code", "stock_code", "symbol", "ts_code", "sec_code"])
    div_date_col = _pick_column(div_cols_lower, ["ex_dividend_date", "ex_date", "exdate", "ex_dt", "exdividend_date"])
    div_bonus_col = _pick_column(div_cols_lower, ["bonus_share_ratio", "bonus_ratio", "bonus_share_rate", "bonus_share"])
    div_transfer_col = _pick_column(div_cols_lower, ["transfer_ratio", "transfer_share_ratio", "transfer_rate", "transfer_share"])
    div_cash_col = _pick_column(div_cols_lower, ["cash_dividend", "cash_dividend_per_share", "cash_div", "cash"])
    div_rights_ratio_col = _pick_column(div_cols_lower, ["rightsissue_ratio", "rights_issue_ratio", "rights_ratio", "allotment_ratio"])
    div_rights_price_col = _pick_column(div_cols_lower, ["rightsissue_price", "rights_issue_price", "rights_price", "allotment_price"])

    dividend_rows = []
    if div_code_col and div_date_col:
        bonus_expr = div_bonus_col if div_bonus_col else "0"
        transfer_expr = div_transfer_col if div_transfer_col else "0"
        cash_expr = div_cash_col if div_cash_col else "0"
        rights_ratio_expr = div_rights_ratio_col if div_rights_ratio_col else "0"
        rights_price_expr = div_rights_price_col if div_rights_price_col else "0"
        div_query = f"""
            SELECT {div_date_col}, {bonus_expr}, {transfer_expr}, {cash_expr}, {rights_ratio_expr}, {rights_price_expr}
            FROM stock_dividend
            WHERE {div_code_col} = %(code)s
              AND {div_date_col} >= %(begin)s
              AND {div_date_col} <= %(end)s
            ORDER BY {div_date_col} ASC
        """
        try:
            dividend_rows = client.execute(
                div_query,
                {"code": normalized_code, "begin": begin_lookback, "end": end_key},
            )
        except Exception:
            dividend_rows = []

    daily_query = """
        SELECT date, open, close
        FROM stock_daily
        WHERE code = %(code)s
          AND date >= %(begin)s
          AND date <= %(end)s
        ORDER BY date ASC
    """
    try:
        daily_rows = client.execute(daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key})
    except Exception:
        daily_rows = []

    if not daily_rows:
        result = ([], {})
        if len(_QFQ_FACTOR_CACHE) > 256:
            _QFQ_FACTOR_CACHE.clear()
        _QFQ_FACTOR_CACHE[cache_key] = result
        return result

    trading_dates = []
    daily_open_close = {}
    closes = []
    for dt_value, open_value, close_value in daily_rows:
        dt = _parse_ymd(dt_value)
        if dt is None:
            continue
        open_f = float(open_value) if open_value is not None else 0.0
        close_f = float(close_value) if close_value is not None else 0.0
        if close_f <= 0:
            continue
        trading_dates.append(dt)
        closes.append(close_f)
        daily_open_close[dt] = (open_f, close_f)

    if not trading_dates:
        result = ([], {})
        if len(_QFQ_FACTOR_CACHE) > 256:
            _QFQ_FACTOR_CACHE.clear()
        _QFQ_FACTOR_CACHE[cache_key] = result
        return result

    events = {}
    for ex_dt_value, bonus_value, transfer_value, cash_value, rights_ratio_value, rights_price_value in dividend_rows:
        ex_dt = _parse_ymd(ex_dt_value)
        if ex_dt is None:
            continue
        bonus = float(bonus_value) if bonus_value is not None else 0.0
        transfer = float(transfer_value) if transfer_value is not None else 0.0
        cash = float(cash_value) if cash_value is not None else 0.0
        rights_ratio = float(rights_ratio_value) if rights_ratio_value is not None else 0.0
        rights_price = float(rights_price_value) if rights_price_value is not None else 0.0
        prev = events.get(ex_dt)
        if prev is None:
            rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
            events[ex_dt] = (bonus + transfer, cash, rights_ratio, rights_amount)
        else:
            prev_rights_ratio = float(prev[2])
            prev_rights_amount = float(prev[3])
            add_rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
            events[ex_dt] = (
                prev[0] + bonus + transfer,
                prev[1] + cash,
                prev_rights_ratio + rights_ratio,
                prev_rights_amount + add_rights_amount,
            )

    unit_divisors = [1.0, 10.0, 100.0]
    baseline_err = None
    best_divisor = 1.0
    best_err = None
    best_n = 0

    if events:
        for divisor in unit_divisors:
            errs = []
            for ex_dt, (split_raw, cash_raw, rights_ratio_raw, rights_amount_raw) in events.items():
                ex_index = bisect_left(trading_dates, ex_dt)
                if ex_index <= 0 or ex_index >= len(trading_dates):
                    continue
                prev_close = closes[ex_index - 1]
                if prev_close <= 0:
                    continue
                obs_dt = trading_dates[ex_index]
                obs_open, obs_close = daily_open_close.get(obs_dt, (0.0, 0.0))
                obs_candidates = []
                if obs_open and obs_open > 0:
                    obs_candidates.append(obs_open)
                if obs_close and obs_close > 0:
                    obs_candidates.append(obs_close)
                if not obs_candidates:
                    continue
                split_ratio = float(split_raw) / divisor
                cash = float(cash_raw) / divisor
                rights_ratio = float(rights_ratio_raw) / divisor
                rights_amount = float(rights_amount_raw) / divisor
                theo = (prev_close - cash + rights_amount) / (1.0 + split_ratio + rights_ratio)
                err = min(abs(theo - obs) / max(obs, 1e-6) for obs in obs_candidates)
                errs.append(err)
            if not errs:
                continue
            errs.sort()
            med = errs[len(errs) // 2]
            if divisor == 1.0:
                baseline_err = med
            if best_err is None or med < best_err:
                best_err = med
                best_divisor = divisor
                best_n = len(errs)

    chosen_divisor = 1.0
    if best_err is not None and best_divisor != 1.0 and baseline_err is not None:
        if best_n >= 1 and best_err <= 0.03 and (baseline_err - best_err) >= 0.01:
            chosen_divisor = best_divisor

    ratio_by_ex_index = {}
    if events:
        for ex_dt, (split_ratio_raw, cash_dividend_raw, rights_ratio_raw, rights_amount_raw) in sorted(events.items(), key=lambda x: x[0]):
            ex_index = bisect_left(trading_dates, ex_dt)
            if ex_index >= len(trading_dates):
                continue
            if ex_index == 0:
                continue
            prev_close = closes[ex_index - 1]
            split_ratio = float(split_ratio_raw) / chosen_divisor
            cash_dividend = float(cash_dividend_raw) / chosen_divisor
            rights_ratio = float(rights_ratio_raw) / chosen_divisor
            rights_amount = float(rights_amount_raw) / chosen_divisor
            denom = (1.0 + split_ratio + rights_ratio) * prev_close
            if denom <= 0:
                continue
            ratio = (prev_close - cash_dividend + rights_amount) / denom
            if ratio <= 0:
                continue
            prior = ratio_by_ex_index.get(ex_index)
            ratio_by_ex_index[ex_index] = ratio if prior is None else (prior * ratio)

    factor = 1.0
    factor_by_date = {}
    for i in range(len(trading_dates) - 1, -1, -1):
        factor_by_date[trading_dates[i]] = factor
        ratio = ratio_by_ex_index.get(i)
        if ratio is not None:
            factor *= ratio

    result = (trading_dates, factor_by_date)
    if len(_QFQ_FACTOR_CACHE) > 256:
        _QFQ_FACTOR_CACHE.clear()
    _QFQ_FACTOR_CACHE[cache_key] = result
    return result


def _get_hfq_factors_by_date(client, normalized_code: str, begin_date: str, end_date: str):
    begin_dt = _parse_ymd(begin_date) or date(1990, 1, 1)
    end_dt = _parse_ymd(end_date) or date.today()
    begin_lookback_dt = begin_dt - timedelta(days=120)
    begin_lookback = begin_lookback_dt.strftime("%Y-%m-%d")
    end_key = end_dt.strftime("%Y-%m-%d")
    cache_key = (normalized_code, begin_lookback, end_key)
    cached = _HFQ_FACTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    div_cols = _get_table_columns(client, "stock_dividend")
    div_cols_lower = {c.lower(): c for c in div_cols}
    div_code_col = _pick_column(div_cols_lower, ["code", "stock_code", "symbol", "ts_code", "sec_code"])
    div_date_col = _pick_column(div_cols_lower, ["ex_dividend_date", "ex_date", "exdate", "ex_dt", "exdividend_date"])
    div_bonus_col = _pick_column(div_cols_lower, ["bonus_share_ratio", "bonus_ratio", "bonus_share_rate", "bonus_share"])
    div_transfer_col = _pick_column(div_cols_lower, ["transfer_ratio", "transfer_share_ratio", "transfer_rate", "transfer_share"])
    div_cash_col = _pick_column(div_cols_lower, ["cash_dividend", "cash_dividend_per_share", "cash_div", "cash"])
    div_rights_ratio_col = _pick_column(div_cols_lower, ["rightsissue_ratio", "rights_issue_ratio", "rights_ratio", "allotment_ratio"])
    div_rights_price_col = _pick_column(div_cols_lower, ["rightsissue_price", "rights_issue_price", "rights_price", "allotment_price"])

    dividend_rows = []
    if div_code_col and div_date_col:
        bonus_expr = div_bonus_col if div_bonus_col else "0"
        transfer_expr = div_transfer_col if div_transfer_col else "0"
        cash_expr = div_cash_col if div_cash_col else "0"
        rights_ratio_expr = div_rights_ratio_col if div_rights_ratio_col else "0"
        rights_price_expr = div_rights_price_col if div_rights_price_col else "0"
        div_query = f"""
            SELECT {div_date_col}, {bonus_expr}, {transfer_expr}, {cash_expr}, {rights_ratio_expr}, {rights_price_expr}
            FROM stock_dividend
            WHERE {div_code_col} = %(code)s
              AND {div_date_col} >= %(begin)s
              AND {div_date_col} <= %(end)s
            ORDER BY {div_date_col} ASC
        """
        try:
            dividend_rows = client.execute(
                div_query,
                {"code": normalized_code, "begin": begin_lookback, "end": end_key},
            )
        except Exception:
            dividend_rows = []

    daily_query = """
        SELECT date, open, close
        FROM stock_daily
        WHERE code = %(code)s
          AND date >= %(begin)s
          AND date <= %(end)s
        ORDER BY date ASC
    """
    try:
        daily_rows = client.execute(daily_query, {"code": normalized_code, "begin": begin_lookback, "end": end_key})
    except Exception:
        daily_rows = []

    if not daily_rows:
        result = ([], {})
        if len(_HFQ_FACTOR_CACHE) > 256:
            _HFQ_FACTOR_CACHE.clear()
        _HFQ_FACTOR_CACHE[cache_key] = result
        return result

    trading_dates = []
    daily_open_close = {}
    closes = []
    for dt_value, open_value, close_value in daily_rows:
        dt = _parse_ymd(dt_value)
        if dt is None:
            continue
        open_f = float(open_value) if open_value is not None else 0.0
        close_f = float(close_value) if close_value is not None else 0.0
        if close_f <= 0:
            continue
        trading_dates.append(dt)
        closes.append(close_f)
        daily_open_close[dt] = (open_f, close_f)

    if not trading_dates:
        result = ([], {})
        if len(_HFQ_FACTOR_CACHE) > 256:
            _HFQ_FACTOR_CACHE.clear()
        _HFQ_FACTOR_CACHE[cache_key] = result
        return result

    events = {}
    for ex_dt_value, bonus_value, transfer_value, cash_value, rights_ratio_value, rights_price_value in dividend_rows:
        ex_dt = _parse_ymd(ex_dt_value)
        if ex_dt is None:
            continue
        bonus = float(bonus_value) if bonus_value is not None else 0.0
        transfer = float(transfer_value) if transfer_value is not None else 0.0
        cash = float(cash_value) if cash_value is not None else 0.0
        rights_ratio = float(rights_ratio_value) if rights_ratio_value is not None else 0.0
        rights_price = float(rights_price_value) if rights_price_value is not None else 0.0
        prev = events.get(ex_dt)
        if prev is None:
            rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
            events[ex_dt] = (bonus + transfer, cash, rights_ratio, rights_amount)
        else:
            prev_rights_ratio = float(prev[2])
            prev_rights_amount = float(prev[3])
            add_rights_amount = rights_ratio * rights_price if rights_ratio > 0 and rights_price > 0 else 0.0
            events[ex_dt] = (
                prev[0] + bonus + transfer,
                prev[1] + cash,
                prev_rights_ratio + rights_ratio,
                prev_rights_amount + add_rights_amount,
            )

    unit_divisors = [1.0, 10.0, 100.0]
    baseline_err = None
    best_divisor = 1.0
    best_err = None
    best_n = 0

    if events:
        for divisor in unit_divisors:
            errs = []
            for ex_dt, (split_raw, cash_raw, rights_ratio_raw, rights_amount_raw) in events.items():
                ex_index = bisect_left(trading_dates, ex_dt)
                if ex_index <= 0 or ex_index >= len(trading_dates):
                    continue
                prev_close = closes[ex_index - 1]
                if prev_close <= 0:
                    continue
                obs_dt = trading_dates[ex_index]
                obs_open, obs_close = daily_open_close.get(obs_dt, (0.0, 0.0))
                obs_candidates = []
                if obs_open and obs_open > 0:
                    obs_candidates.append(obs_open)
                if obs_close and obs_close > 0:
                    obs_candidates.append(obs_close)
                if not obs_candidates:
                    continue
                split_ratio = float(split_raw) / divisor
                cash = float(cash_raw) / divisor
                rights_ratio = float(rights_ratio_raw) / divisor
                rights_amount = float(rights_amount_raw) / divisor
                theo = (prev_close - cash + rights_amount) / (1.0 + split_ratio + rights_ratio)
                err = min(abs(theo - obs) / max(obs, 1e-6) for obs in obs_candidates)
                errs.append(err)
            if not errs:
                continue
            errs.sort()
            med = errs[len(errs) // 2]
            if divisor == 1.0:
                baseline_err = med
            if best_err is None or med < best_err:
                best_err = med
                best_divisor = divisor
                best_n = len(errs)

    chosen_divisor = 1.0
    if best_err is not None and best_divisor != 1.0 and baseline_err is not None:
        if best_n >= 1 and best_err <= 0.03 and (baseline_err - best_err) >= 0.01:
            chosen_divisor = best_divisor

    ratio_by_ex_index = {}
    if events:
        for ex_dt, (split_ratio_raw, cash_dividend_raw, rights_ratio_raw, rights_amount_raw) in sorted(events.items(), key=lambda x: x[0]):
            ex_index = bisect_left(trading_dates, ex_dt)
            if ex_index >= len(trading_dates):
                continue
            if ex_index == 0:
                continue
            prev_close = closes[ex_index - 1]
            split_ratio = float(split_ratio_raw) / chosen_divisor
            cash_dividend = float(cash_dividend_raw) / chosen_divisor
            rights_ratio = float(rights_ratio_raw) / chosen_divisor
            rights_amount = float(rights_amount_raw) / chosen_divisor
            denom = (1.0 + split_ratio + rights_ratio) * prev_close
            if denom <= 0:
                continue
            ratio = (prev_close - cash_dividend + rights_amount) / denom
            if ratio <= 0:
                continue
            prior = ratio_by_ex_index.get(ex_index)
            ratio_by_ex_index[ex_index] = ratio if prior is None else (prior * ratio)

    factor = 1.0
    factor_by_date = {}
    for i in range(len(trading_dates)):
        ratio = ratio_by_ex_index.get(i)
        if ratio is not None:
            factor = factor / ratio
        factor_by_date[trading_dates[i]] = factor

    result = (trading_dates, factor_by_date)
    if len(_HFQ_FACTOR_CACHE) > 256:
        _HFQ_FACTOR_CACHE.clear()
    _HFQ_FACTOR_CACHE[cache_key] = result
    return result


def _generate_hfq_factors_worker(worker_args):
    codes, table_name, batch_size = worker_args
    client = get_db_client()
    try:
        batch = []
        inserted_total = 0
        batch_size = int(batch_size) if batch_size and int(batch_size) > 0 else 50000

        for code in codes:
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]

            trading_dates, factor_by_date = _get_hfq_factors_by_date(
                client,
                code,
                dmin.strftime("%Y-%m-%d"),
                dmax.strftime("%Y-%m-%d"),
            )
            if not factor_by_date:
                continue

            for d, f in factor_by_date.items():
                batch.append({"code": code, "date": d, "factor": f})

                if len(batch) >= batch_size:
                    client.execute(
                        f"INSERT INTO {table_name} (code, date, factor) VALUES",
                        batch,
                        settings={"async_insert": 1, "wait_for_async_insert": 0},
                    )
                    inserted_total += len(batch)
                    batch = []

        if batch:
            client.execute(
                f"INSERT INTO {table_name} (code, date, factor) VALUES",
                batch,
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
            inserted_total += len(batch)

        return inserted_total
    finally:
        client.disconnect()


def _populate_hfq_factor_temp_table(stock_codes: list[str], workers: int = 8):
    client = get_db_client()
    ts = int(time.time())
    table_name = f"stock_hfq_factors_temp_{ts}"

    try:
        client.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                code LowCardinality(String),
                date Date,
                factor Float64
            ) ENGINE = MergeTree()
            ORDER BY (code, date)
        """)
    finally:
        client.disconnect()

    logger.info(f"正在生成后复权因子表: {table_name} ...")

    workers = int(workers) if workers and int(workers) > 0 else 8
    workers = min(workers, max(1, len(stock_codes)))

    args_list = []
    if workers <= 1:
        args_list = [(stock_codes, table_name, 50000)]
    else:
        chunk_size = (len(stock_codes) + workers - 1) // workers
        for i in range(0, len(stock_codes), chunk_size):
            chunk = stock_codes[i : i + chunk_size]
            args_list.append((chunk, table_name, 50000))

    try:
        if workers <= 1:
            total = _generate_hfq_factors_worker(args_list[0])
        else:
            try:
                with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
                    parts = pool.map(_generate_hfq_factors_worker, args_list)
                total = sum(parts)
            except KeyboardInterrupt:
                logger.warning("用户中断，正在停止生成因子进程...")
                raise

        logger.info(f"因子表 {table_name} 生成完成，共写入 {total} 条因子数据")
        return table_name
    except Exception:
        client = get_db_client()
        try:
            client.execute(f"DROP TABLE IF EXISTS {table_name}")
        except Exception:
            pass
        finally:
            client.disconnect()
        raise


def _generate_qfq_factors_worker(worker_args):
    codes, table_name, batch_size = worker_args
    client = get_db_client()
    try:
        batch = []
        inserted_total = 0
        batch_size = int(batch_size) if batch_size and int(batch_size) > 0 else 50000

        for code in codes:
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]

            trading_dates, factor_by_date = _get_qfq_factors_by_date(
                client,
                code,
                dmin.strftime("%Y-%m-%d"),
                dmax.strftime("%Y-%m-%d"),
            )
            if not factor_by_date:
                continue

            for d, f in factor_by_date.items():
                batch.append({"code": code, "date": d, "factor": f})
                if len(batch) >= batch_size:
                    client.execute(
                        f"INSERT INTO {table_name} (code, date, factor) VALUES",
                        batch,
                        settings={"async_insert": 1, "wait_for_async_insert": 0},
                    )
                    inserted_total += len(batch)
                    batch = []

        if batch:
            client.execute(
                f"INSERT INTO {table_name} (code, date, factor) VALUES",
                batch,
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
            inserted_total += len(batch)

        return inserted_total
    finally:
        client.disconnect()


def _populate_qfq_factor_temp_table(stock_codes: list[str], workers: int = 8):
    client = get_db_client()
    ts = int(time.time())
    table_name = f"stock_qfq_factors_temp_{ts}"

    try:
        client.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                code LowCardinality(String),
                date Date,
                factor Float64
            ) ENGINE = MergeTree()
            ORDER BY (code, date)
        """)
    finally:
        client.disconnect()

    logger.info(f"正在生成前复权因子表: {table_name} ...")

    workers = int(workers) if workers and int(workers) > 0 else 8
    workers = min(workers, max(1, len(stock_codes)))

    args_list = []
    if workers <= 1:
        args_list = [(stock_codes, table_name, 50000)]
    else:
        chunk_size = (len(stock_codes) + workers - 1) // workers
        for i in range(0, len(stock_codes), chunk_size):
            chunk = stock_codes[i : i + chunk_size]
            args_list.append((chunk, table_name, 50000))

    try:
        if workers <= 1:
            total = _generate_qfq_factors_worker(args_list[0])
        else:
            try:
                with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
                    parts = pool.map(_generate_qfq_factors_worker, args_list)
                total = sum(parts)
            except KeyboardInterrupt:
                logger.warning("用户中断，正在停止生成因子进程...")
                raise

        logger.info(f"因子表 {table_name} 生成完成，共写入 {total} 条因子数据")
        return table_name
    except Exception:
        client = get_db_client()
        try:
            client.execute(f"DROP TABLE IF EXISTS {table_name}")
        except Exception:
            pass
        finally:
            client.disconnect()
        raise


def _get_minmax_month(client, table: str, code: str):
    try:
        row = client.execute(
            f"SELECT min(toYYYYMM(datetime)), max(toYYYYMM(datetime)) FROM {table} WHERE code=%(code)s",
            {"code": code},
        )
    except Exception:
        return (None, None)
    if not row:
        return (None, None)
    return (row[0][0], row[0][1])

def _get_minmax_daily_date(client, code: str):
    try:
        row = client.execute(
            "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
            {"code": code},
        )
    except Exception:
        return (None, None)
    if not row:
        return (None, None)
    return (row[0][0], row[0][1])

def _ym_to_month_bounds(ym: int):
    y = int(ym) // 100
    m = int(ym) % 100
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(y, m + 1, 1) - timedelta(days=1)
    return start, end

def _ym_range_to_date_bounds(start_ym: int | None, end_ym: int | None):
    start_d = _ym_to_month_bounds(int(start_ym))[0] if start_ym is not None else None
    end_d = _ym_to_month_bounds(int(end_ym))[1] if end_ym is not None else None
    return start_d, end_d

def _iter_ym_range(start_ym: int, end_ym: int):
    y = int(start_ym) // 100
    m = int(start_ym) % 100
    end_y = int(end_ym) // 100
    end_m = int(end_ym) % 100
    while (y < end_y) or (y == end_y and m <= end_m):
        yield y * 100 + m
        m += 1
        if m > 12:
            y += 1
            m = 1

def _shift_ym(ym: int, delta_months: int) -> int:
    y = int(ym) // 100
    m = int(ym) % 100
    m0 = (y * 12 + (m - 1)) + int(delta_months)
    y2 = m0 // 12
    m2 = m0 % 12 + 1
    return int(y2 * 100 + m2)

def _infer_recent_rebuild_ym_range(
    minute_mode: str,
    start_ym: int | None,
    end_ym: int | None,
    recent_months: int,
):
    if str(minute_mode) != "rebuild":
        return start_ym, end_ym
    if start_ym is not None or end_ym is not None:
        return start_ym, end_ym
    n = int(recent_months) if recent_months is not None else 0
    if n <= 0:
        return start_ym, end_ym
    end_guess = int((date.today() - timedelta(days=1)).strftime("%Y%m"))
    start_guess = _shift_ym(end_guess, -(n - 1))
    return start_guess, end_guess

def _get_missing_minute_months(client, base_table: str, qfq_table: str, code: str, start_ym: int, end_ym: int):
    query = f"""
        WITH
            b AS (
                SELECT toYYYYMM(datetime) AS ym, count() AS c_base
                FROM {base_table}
                WHERE code = %(code)s
                  AND toYYYYMM(datetime) >= %(start_ym)s
                  AND toYYYYMM(datetime) <= %(end_ym)s
                GROUP BY ym
            ),
            q AS (
                SELECT toYYYYMM(datetime) AS ym, count() AS c_qfq
                FROM {qfq_table}
                WHERE code = %(code)s
                  AND toYYYYMM(datetime) >= %(start_ym)s
                  AND toYYYYMM(datetime) <= %(end_ym)s
                GROUP BY ym
            )
        SELECT b.ym, b.c_base, ifNull(q.c_qfq, 0) AS c_qfq
        FROM b
        LEFT JOIN q USING ym
        WHERE c_qfq < c_base
        ORDER BY b.ym ASC
    """
    try:
        rows = client.execute(query, {"code": code, "start_ym": int(start_ym), "end_ym": int(end_ym)})
        return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]
    except Exception:
        return []

def _backfill_minute_qfq_for_month(
    client,
    base_table: str,
    qfq_table: str,
    code: str,
    ym: int,
    trading_dates,
    factor_by_date,
    only_missing: bool = True,
):
    start_d, end_d = _ym_to_month_bounds(ym)
    start_dt = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0)

    prev_row = None
    try:
        prev_row = client.execute(
            f"SELECT datetime, close FROM {base_table} WHERE code=%(code)s AND datetime < %(start)s ORDER BY datetime DESC LIMIT 1",
            {"code": code, "start": start_dt},
        )
    except Exception:
        prev_row = None
    pre_close = None
    if prev_row:
        prev_dt = prev_row[0][0]
        prev_close = float(prev_row[0][1]) if prev_row[0][1] is not None else 0.0
        prev_d = prev_dt.date() if isinstance(prev_dt, datetime) else start_d
        prev_factor = _get_factor_for_date(trading_dates, factor_by_date, prev_d)
        if prev_close > 0:
            pre_close = prev_close * prev_factor

    if only_missing:
        query = f"""
            SELECT b.datetime, b.open, b.close, b.high, b.low, b.volume, b.amount
            FROM {base_table} AS b
            LEFT JOIN {qfq_table} AS q
              ON q.code = b.code AND q.datetime = b.datetime
            WHERE b.code = %(code)s
              AND toYYYYMM(b.datetime) = %(ym)s
              AND q.code = ''
            ORDER BY b.datetime ASC
        """
    else:
        query = f"""
            SELECT datetime, open, close, high, low, volume, amount
            FROM {base_table}
            WHERE code = %(code)s
              AND toYYYYMM(datetime) = %(ym)s
            ORDER BY datetime ASC
        """
    try:
        rows = client.execute(query, {"code": code, "ym": int(ym)})
    except Exception:
        rows = []
    if not rows:
        return 0

    batch = []
    inserted = 0
    for dt_value, o, c, h, l, vol, amt in rows:
        dt = dt_value
        row_d = dt.date() if isinstance(dt, datetime) else start_d
        factor = _get_factor_for_date(trading_dates, factor_by_date, row_d)
        o_f = float(o) if o is not None else 0.0
        c_f = float(c) if c is not None else 0.0
        h_f = float(h) if h is not None else 0.0
        l_f = float(l) if l is not None else 0.0
        if c_f <= 0 and o_f > 0:
            c_f = o_f
        if o_f <= 0 and c_f > 0:
            o_f = c_f
        if h_f <= 0:
            h_f = max(o_f, c_f)
        if l_f <= 0:
            l_f = min(o_f, c_f)

        o_adj = o_f * factor
        c_adj = c_f * factor
        h_adj = h_f * factor
        l_adj = l_f * factor
        h_adj = max(h_adj, o_adj, c_adj)
        l_adj = min(l_adj, o_adj, c_adj)

        if pre_close is None or pre_close <= 0:
            pre_close = o_adj if o_adj > 0 else (c_adj if c_adj > 0 else 0.0)

        if pre_close and pre_close > 0:
            pct_chg = (c_adj - pre_close) / pre_close * 100.0
            amplitude = (h_adj - l_adj) / pre_close * 100.0
        else:
            pct_chg = 0.0
            amplitude = 0.0

        pre_close = c_adj if c_adj > 0 else pre_close

        batch.append(
            {
                "datetime": dt,
                "code": code,
                "open": float(o_adj),
                "close": float(c_adj),
                "high": float(h_adj),
                "low": float(l_adj),
                "volume": float(vol) if vol is not None else 0.0,
                "amount": float(amt) if amt is not None else 0.0,
                "pct_chg": float(pct_chg),
                "amplitude": float(amplitude),
            }
        )
        if len(batch) >= 5000:
            client.execute(
                f"INSERT INTO {qfq_table} (datetime,code,open,close,high,low,volume,amount,pct_chg,amplitude) VALUES",
                batch,
            )
            inserted += len(batch)
            batch = []

    if batch:
        client.execute(
            f"INSERT INTO {qfq_table} (datetime,code,open,close,high,low,volume,amount,pct_chg,amplitude) VALUES",
            batch,
        )
        inserted += len(batch)
    return inserted

def backfill_minute_qfq_tables(stock_codes: list[str] | None = None):
    ensure_tables_exist()
    client = get_db_client()
    try:
        try:
            if stock_codes is None:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = stock_codes or []

        factor_cache: dict[str, tuple[list[date], dict[date, float]]] = {}

        periods = ["1", "5", "15", "30", "60"]
        total_inserted = 0
        for p in periods:
            base_table = f"stock_{p}min"
            qfq_table = f"{base_table}_qfq"
            logger.info(f"开始检查补全: {qfq_table}")
            for i, code in enumerate(stock_codes):
                base_min_ym, base_max_ym = _get_minmax_month(client, base_table, code)
                if base_min_ym is None or base_max_ym is None:
                    continue
                start_ym = int(base_min_ym)
                end_ym = int(base_max_ym)

                missing_months = _get_missing_minute_months(client, base_table, qfq_table, code, start_ym, end_ym)
                if not missing_months:
                    continue

                cached = factor_cache.get(code)
                if cached is None:
                    dmin, dmax = _get_minmax_daily_date(client, code)
                    if dmin is None or dmax is None:
                        continue
                    trading_dates, factor_by_date = _get_qfq_factors_by_date(
                        client,
                        code,
                        dmin.strftime("%Y-%m-%d"),
                        dmax.strftime("%Y-%m-%d"),
                    )
                    factor_cache[code] = (trading_dates, factor_by_date)
                else:
                    trading_dates, factor_by_date = cached

                inserted = 0
                for ym, _, _ in missing_months:
                    inserted += _backfill_minute_qfq_for_month(
                        client,
                        base_table,
                        qfq_table,
                        code,
                        ym,
                        trading_dates,
                        factor_by_date,
                        True,
                    )
                if inserted:
                    total_inserted += inserted
                    logger.info(f"{qfq_table} 补全 {code}: +{inserted}")
                if (i + 1) % 200 == 0:
                    logger.info(f"{qfq_table} 进度: {i+1}/{len(stock_codes)}")
            logger.info(f"{qfq_table} 完成")
        logger.info(f"分钟线 _qfq 补全完成，总写入: {total_inserted}")
    finally:
        client.disconnect()


def _backfill_minute_qfq_for_codes(worker_args):
    codes, periods = worker_args
    client = get_db_client()
    factor_cache: dict[str, tuple[list[date], dict[date, float]]] = {}
    total_inserted = 0

    try:
        for p in periods:
            base_table = f"stock_{p}min"
            qfq_table = f"{base_table}_qfq"
            total_codes = len(codes)
            processed = 0
            inserted_p = 0
            start_ts = time.time()
            for code in codes:
                processed += 1
                base_min_ym, base_max_ym = _get_minmax_month(client, base_table, code)
                if base_min_ym is None or base_max_ym is None:
                    continue
                start_ym = int(base_min_ym)
                end_ym = int(base_max_ym)
                missing_months = _get_missing_minute_months(client, base_table, qfq_table, code, start_ym, end_ym)
                if not missing_months:
                    continue

                cached = factor_cache.get(code)
                if cached is None:
                    dmin, dmax = _get_minmax_daily_date(client, code)
                    if dmin is None or dmax is None:
                        continue
                    trading_dates, factor_by_date = _get_qfq_factors_by_date(
                        client,
                        code,
                        dmin.strftime("%Y-%m-%d"),
                        dmax.strftime("%Y-%m-%d"),
                    )
                    factor_cache[code] = (trading_dates, factor_by_date)
                else:
                    trading_dates, factor_by_date = cached

                inserted = 0
                for ym, _, _ in missing_months:
                    inserted += _backfill_minute_qfq_for_month(
                        client,
                        base_table,
                        qfq_table,
                        code,
                        ym,
                        trading_dates,
                        factor_by_date,
                        True,
                    )
                inserted_p += inserted

                if processed % 200 == 0:
                    elapsed = max(time.time() - start_ts, 1e-6)
                    speed = processed / elapsed
                    logger.info(f"{qfq_table} 并行进度: {processed}/{total_codes} (累计写入: {inserted_p}, 速度: {speed:.2f} 只/秒)")

            total_inserted += inserted_p
            elapsed = max(time.time() - start_ts, 1e-6)
            speed = processed / elapsed if processed > 0 else 0.0
            logger.info(f"{qfq_table} 并行分片完成: 处理={processed}/{total_codes} 写入={inserted_p} 速度={speed:.2f} 只/秒")

        return total_inserted
    finally:
        client.disconnect()


def backfill_minute_qfq_tables_parallel(
    stock_codes: list[str] | None = None,
    workers: int = 4,
    periods: list[str] | None = None,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        if stock_codes is None:
            try:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
            except Exception:
                stock_codes = []
    finally:
        client.disconnect()

    stock_codes = stock_codes or []

    periods = periods or ["1", "5", "15", "30", "60"]
    workers = int(workers) if workers and int(workers) > 0 else 4
    workers = min(workers, max(1, len(stock_codes)))

    if workers <= 1:
        total_inserted = _backfill_minute_qfq_for_codes((stock_codes, periods))
        logger.info(f"分钟线 _qfq 并行补全完成，总写入: {total_inserted}")
        return total_inserted

    chunk_size = (len(stock_codes) + workers - 1) // workers
    chunks = [stock_codes[i : i + chunk_size] for i in range(0, len(stock_codes), chunk_size)]
    tasks = [(c, periods) for c in chunks]
    total_inserted = 0
    try:
        with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
            for idx, part in enumerate(pool.imap_unordered(_backfill_minute_qfq_for_codes, tasks), start=1):
                total_inserted += int(part)
                logger.info(f"分钟线 _qfq 并行补全进度: {idx}/{len(tasks)} (累计写入: {total_inserted})")
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止子进程...")
        pool.terminate()
        pool.join()
        raise
    logger.info(f"分钟线 _qfq 并行补全完成，总写入: {total_inserted}")
    return total_inserted


def _wait_pending_mutations(client, table: str, timeout_s: int = 3600):
    start = time.time()
    while True:
        try:
            row = client.execute(
                """
                SELECT count()
                FROM system.mutations
                WHERE database = currentDatabase()
                  AND table = %(table)s
                  AND is_done = 0
                """,
                {"table": table},
            )
            pending = int(row[0][0]) if row else 0
        except Exception:
            pending = 0
        if pending <= 0:
            return True
        if time.time() - start > timeout_s:
            return False
        time.sleep(2)


def fix_stock_daily_volume_units(
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    dry_run: bool = True,
    mutations_sync: int = 2,
    code_chunk_size: int = 200,
) -> int:
    ensure_tables_exist()

    begin_d = _parse_ymd(begin_date) if begin_date else None
    end_d = _parse_ymd(end_date) if end_date else None

    client = get_db_client()
    try:
        if stock_codes is None:
            try:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
            except Exception:
                stock_codes = []
    finally:
        client.disconnect()

    stock_codes = stock_codes or []
    if not stock_codes:
        return 0

    def _iter_chunks(xs: list[str], n: int):
        n = max(1, int(n))
        for i in range(0, len(xs), n):
            yield xs[i : i + n]

    total_candidates = 0
    for codes_part in _iter_chunks(stock_codes, int(code_chunk_size)):
        where_parts = [
            "code IN %(codes)s",
            "volume > 0",
            "amount > 0",
            "close > 0",
            "(amount / volume) / close BETWEEN 80 AND 120",
        ]
        params: dict = {"codes": codes_part}
        if begin_d is not None:
            where_parts.append("date >= toDate(%(begin)s)")
            params["begin"] = begin_d
        if end_d is not None:
            where_parts.append("date <= toDate(%(end)s)")
            params["end"] = end_d
        where = " AND ".join(where_parts)

        client = get_db_client()
        try:
            if dry_run:
                n = int(client.execute(f"SELECT count() FROM stock_daily WHERE {where}", params)[0][0])
                total_candidates += n
                if n > 0:
                    rows = client.execute(
                        f"""
                        SELECT code, count() AS cnt, min(date) AS dmin, max(date) AS dmax
                        FROM stock_daily
                        WHERE {where}
                        GROUP BY code
                        ORDER BY cnt DESC, code ASC
                        LIMIT 20
                        """,
                        params,
                    )
                    logger.info(f"stock_daily 成交量单位疑似为手(需*100) rows={n} samples={rows}")
            else:
                n = int(client.execute(f"SELECT count() FROM stock_daily WHERE {where}", params)[0][0])
                total_candidates += n
                if n > 0:
                    client.execute(
                        f"ALTER TABLE stock_daily UPDATE volume = volume * 100.0 WHERE {where}",
                        params,
                        settings={"mutations_sync": int(mutations_sync)},
                    )
                    _wait_pending_mutations(client, "stock_daily", timeout_s=3600)
                    logger.info(f"stock_daily 成交量单位修复完成 codes={len(codes_part)} updated_rows~={n}")
        finally:
            client.disconnect()

    return int(total_candidates)


def _rebuild_daily_qfq_for_codes(worker_args):
    codes, tmp_table, flush_rows = worker_args
    client = get_db_client()
    try:
        inserted_total = 0
        batch = []
        flush_rows = int(flush_rows) if flush_rows and int(flush_rows) > 0 else 50000

        for code in codes:
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]
            trading_dates, factor_by_date = _get_qfq_factors_by_date(
                client,
                code,
                dmin.strftime("%Y-%m-%d"),
                dmax.strftime("%Y-%m-%d"),
            )
            if not trading_dates:
                continue

            try:
                rows = client.execute(
                    """
                    SELECT
                        date,
                        argMin(open, date) AS open,
                        argMax(close, date) AS close,
                        max(high) AS high,
                        min(low) AS low,
                        max(volume) AS volume,
                        max(amount) AS amount,
                        any(pct_chg) AS pct_chg,
                        any(amplitude) AS amplitude,
                        max(change_amt) AS change_amt,
                        any(turnover) AS turnover
                    FROM stock_daily
                    WHERE code=%(code)s
                    GROUP BY date
                    ORDER BY date ASC
                    """,
                    {"code": code},
                )
            except Exception:
                rows = []
            if not rows:
                continue

            for (
                d,
                o,
                c,
                h,
                l,
                vol,
                amt,
                pct_chg,
                amplitude,
                change_amt,
                turnover,
            ) in rows:
                d_date = d if isinstance(d, date) else _parse_ymd(d)
                if d_date is None:
                    continue
                factor = _get_factor_for_date(trading_dates, factor_by_date, d_date)
                o_f = float(o) if o is not None else 0.0
                c_f = float(c) if c is not None else 0.0
                h_f = float(h) if h is not None else 0.0
                l_f = float(l) if l is not None else 0.0
                batch.append(
                    {
                        "date": d_date,
                        "code": code,
                        "open": o_f * factor,
                        "close": c_f * factor,
                        "high": h_f * factor,
                        "low": l_f * factor,
                        "volume": float(vol) if vol is not None else 0.0,
                        "amount": float(amt) if amt is not None else 0.0,
                        "pct_chg": float(pct_chg) if pct_chg is not None else 0.0,
                        "amplitude": float(amplitude) if amplitude is not None else 0.0,
                        "change_amt": (float(change_amt) if change_amt is not None else 0.0) * factor,
                        "turnover": float(turnover) if turnover is not None else 0.0,
                    }
                )
                if len(batch) >= flush_rows:
                    client.execute(
                        f"INSERT INTO {tmp_table} (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                        batch,
                        settings={"async_insert": 1, "wait_for_async_insert": 0},
                    )
                    inserted_total += len(batch)
                    batch = []

        if batch:
            client.execute(
                f"INSERT INTO {tmp_table} (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                batch,
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
            inserted_total += len(batch)

        return inserted_total
    finally:
        client.disconnect()


def rebuild_minute_qfq_tables(
    stock_codes: list[str] | None = None,
    periods: list[str] | None = None,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []
    finally:
        client.disconnect()

    stock_codes = stock_codes or []
    rebuild_full_table = stock_codes is not None and len(stock_codes) == 0
    if rebuild_full_table:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    factor_table = None
    client = None
    try:
        factor_table = _populate_qfq_factor_temp_table(stock_codes, workers=8)

        periods = periods or ["1", "5", "15", "30", "60"]
        client = get_db_client()

        for p in periods:
            base_table = f"stock_{p}min"
            qfq_table = f"{base_table}_qfq"
            logger.info(f"开始重建: {qfq_table} (ASOF Join)")

            row = client.execute(f"SELECT min(toYYYYMM(datetime)), max(toYYYYMM(datetime)) FROM {base_table}")
            if not row or not row[0][0]:
                logger.warning(f"{base_table} 为空，跳过")
                continue

            min_ym, max_ym = row[0][0], row[0][1]
            start_ym_val = int(min_ym) if start_ym is None else max(int(min_ym), int(start_ym))
            end_ym_val = int(max_ym) if end_ym is None else min(int(max_ym), int(end_ym))
            if end_ym_val < start_ym_val:
                continue

            if rebuild_full_table:
                client.execute(f"TRUNCATE TABLE {qfq_table}")

            for ym in _iter_ym_range(start_ym_val, end_ym_val):
                logger.info(f"正在处理 {qfq_table} - {ym} ...")

                if not rebuild_full_table:
                    client.execute(
                        f"ALTER TABLE {qfq_table} DELETE WHERE code IN %(codes)s AND toYYYYMM(datetime) = %(ym)s",
                        {"codes": stock_codes, "ym": int(ym)},
                        settings={"mutations_sync": 2},
                    )

                query = f"""
                INSERT INTO {qfq_table}
                SELECT
                    m.datetime,
                    m.code,
                    m.open * ifNull(f.factor, 1.0),
                    m.close * ifNull(f.factor, 1.0),
                    m.high * ifNull(f.factor, 1.0),
                    m.low * ifNull(f.factor, 1.0),
                    m.volume,
                    m.amount,
                    m.pct_chg,
                    m.amplitude
                FROM
                (
                    SELECT *
                    FROM {base_table}
                    WHERE toYYYYMM(datetime) = %(ym)s
                      AND code IN %(codes)s
                    ORDER BY code, datetime
                ) AS m
                ASOF LEFT JOIN {factor_table} AS f
                  ON m.code = f.code AND toDate(m.datetime) >= f.date
                """
                client.execute(query, {"codes": stock_codes, "ym": int(ym)})

            logger.info(f"{qfq_table} 重建完成")
    finally:
        if factor_table:
            if client is None:
                try:
                    client = get_db_client()
                except Exception:
                    pass
            if client:
                try:
                    client.execute(f"DROP TABLE {factor_table}")
                except Exception:
                    pass
        if client:
            try:
                client.disconnect()
            except Exception:
                pass

def rebuild_daily_qfq_table(stock_codes: list[str] | None = None):
    ensure_tables_exist()
    client = get_db_client()
    try:
        try:
            if stock_codes is None:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = stock_codes or []

        existing_total = 0
        try:
            existing_total = int(client.execute("SELECT count() FROM stock_daily_qfq")[0][0])
        except Exception:
            existing_total = 0

        total_inserted = 0
        for i, code in enumerate(stock_codes):
            if existing_total > 0:
                try:
                    client.execute("ALTER TABLE stock_daily_qfq DELETE WHERE code = %(code)s", {"code": code})
                    _wait_pending_mutations(client, "stock_daily_qfq", timeout_s=3600)
                except Exception:
                    pass

            row = None
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]
            begin = dmin.strftime("%Y-%m-%d")
            end = dmax.strftime("%Y-%m-%d")
            trading_dates, factor_by_date = _get_qfq_factors_by_date(client, code, begin, end)
            if not trading_dates:
                continue

            try:
                rows = client.execute(
                    """
                    SELECT
                        date,
                        argMin(open, date) AS open,
                        argMax(close, date) AS close,
                        max(high) AS high,
                        min(low) AS low,
                        max(volume) AS volume,
                        max(amount) AS amount,
                        any(pct_chg) AS pct_chg,
                        any(amplitude) AS amplitude,
                        max(change_amt) AS change_amt,
                        any(turnover) AS turnover
                    FROM stock_daily
                    WHERE code=%(code)s
                    GROUP BY date
                    ORDER BY date ASC
                    """,
                    {"code": code},
                )
            except Exception:
                continue
            if not rows:
                continue

            batch = []
            inserted = 0
            for (
                d,
                o,
                c,
                h,
                l,
                vol,
                amt,
                pct_chg,
                amplitude,
                change_amt,
                turnover,
            ) in rows:
                d_date = d if isinstance(d, date) else _parse_ymd(d)
                if d_date is None:
                    continue
                factor = _get_factor_for_date(trading_dates, factor_by_date, d_date)
                o_f = float(o) if o is not None else 0.0
                c_f = float(c) if c is not None else 0.0
                h_f = float(h) if h is not None else 0.0
                l_f = float(l) if l is not None else 0.0

                batch.append(
                    {
                        "date": d_date,
                        "code": code,
                        "open": o_f * factor,
                        "close": c_f * factor,
                        "high": h_f * factor,
                        "low": l_f * factor,
                        "volume": float(vol) if vol is not None else 0.0,
                        "amount": float(amt) if amt is not None else 0.0,
                        "pct_chg": float(pct_chg) if pct_chg is not None else 0.0,
                        "amplitude": float(amplitude) if amplitude is not None else 0.0,
                        "change_amt": (float(change_amt) if change_amt is not None else 0.0) * factor,
                        "turnover": float(turnover) if turnover is not None else 0.0,
                    }
                )

                if len(batch) >= 5000:
                    client.execute(
                        "INSERT INTO stock_daily_qfq (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                        batch,
                    )
                    inserted += len(batch)
                    batch = []

            if batch:
                client.execute(
                    "INSERT INTO stock_daily_qfq (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                    batch,
                )
                inserted += len(batch)

            if inserted:
                total_inserted += inserted
                logger.info(f"stock_daily_qfq 重建 {code}: +{inserted}")
            if (i + 1) % 200 == 0:
                logger.info(f"stock_daily_qfq 重建进度: {i+1}/{len(stock_codes)}")

        logger.info(f"stock_daily_qfq 重建完成，总写入: {total_inserted}")
    finally:
        client.disconnect()


def rebuild_daily_qfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    flush_rows: int = 50000,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    ensure_tables_exist()
    
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    stock_codes = stock_codes or []
    rebuild_full_table = stock_codes is not None and len(stock_codes) == 0
    if rebuild_full_table:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    ts = int(time.time())
    tmp_table = f"stock_daily_qfq_tmp_{ts}"
    old_table = f"stock_daily_qfq_old_{ts}"

    factor_table = None
    try:
        factor_table = _populate_qfq_factor_temp_table(stock_codes, workers)

        start_date, end_date = _ym_range_to_date_bounds(start_ym, end_ym)
        has_range = start_date is not None or end_date is not None

        client = get_db_client()
        try:
            if rebuild_full_table and not has_range:
                client.execute(f"CREATE TABLE {tmp_table} AS stock_daily_qfq")
                
                logger.info("开始执行 SQL 批量构建前复权日线表 (ASOF Join)...")
                query = f"""
                INSERT INTO {tmp_table}
                SELECT
                    d.date,
                    d.code,
                    d.open * ifNull(f.factor, 1.0),
                    d.close * ifNull(f.factor, 1.0),
                    d.high * ifNull(f.factor, 1.0),
                    d.low * ifNull(f.factor, 1.0),
                    d.volume,
                    d.amount,
                    d.pct_chg,
                    d.amplitude,
                    d.change_amt * ifNull(f.factor, 1.0),
                    d.turnover
                FROM
                (
                    SELECT
                        code,
                        date,
                        argMin(open, date) AS open,
                        argMax(close, date) AS close,
                        max(high) AS high,
                        min(low) AS low,
                        max(volume) AS volume,
                        max(amount) AS amount,
                        any(pct_chg) AS pct_chg,
                        any(amplitude) AS amplitude,
                        max(change_amt) AS change_amt,
                        any(turnover) AS turnover
                    FROM stock_daily
                    WHERE code IN %(codes)s
                    GROUP BY code, date
                    ORDER BY code, date
                ) AS d
                ASOF LEFT JOIN {factor_table} AS f
                  ON d.code = f.code AND d.date >= f.date
                """
                client.execute(query, {"codes": stock_codes})

                client.execute(f"RENAME TABLE stock_daily_qfq TO {old_table}, {tmp_table} TO stock_daily_qfq")
                try:
                    client.execute(f"DROP TABLE {old_table}")
                except Exception:
                    pass
                logger.info("stock_daily_qfq 快速重建完成")
                return True

            delete_parts = []
            if not rebuild_full_table:
                delete_parts.append("code IN %(codes)s")
            if start_date is not None:
                delete_parts.append("date >= %(start_date)s")
            if end_date is not None:
                delete_parts.append("date <= %(end_date)s")
            delete_where = " AND ".join(delete_parts) if delete_parts else "1"
            delete_params = {"codes": stock_codes, "start_date": start_date, "end_date": end_date}

            if has_range:
                logger.info("开始执行 SQL 按范围重建前复权日线表 (ASOF Join)...")
            else:
                logger.info("开始执行 SQL 增量重建前复权日线表 (仅指定代码, ASOF Join)...")

            client.execute(
                f"ALTER TABLE stock_daily_qfq DELETE WHERE {delete_where}",
                delete_params,
                settings={"mutations_sync": 2},
            )

            base_where_parts = []
            if not rebuild_full_table:
                base_where_parts.append("code IN %(codes)s")
            if start_date is not None:
                base_where_parts.append("date >= %(start_date)s")
            if end_date is not None:
                base_where_parts.append("date <= %(end_date)s")
            base_where = ("WHERE " + " AND ".join(base_where_parts)) if base_where_parts else ""

            insert_query = f"""
            INSERT INTO stock_daily_qfq
            SELECT
                d.date,
                d.code,
                d.open * ifNull(f.factor, 1.0),
                d.close * ifNull(f.factor, 1.0),
                d.high * ifNull(f.factor, 1.0),
                d.low * ifNull(f.factor, 1.0),
                d.volume,
                d.amount,
                d.pct_chg,
                d.amplitude,
                d.change_amt * ifNull(f.factor, 1.0),
                d.turnover
            FROM
            (
                SELECT
                    code,
                    date,
                    argMin(open, date) AS open,
                    argMax(close, date) AS close,
                    max(high) AS high,
                    min(low) AS low,
                    max(volume) AS volume,
                    max(amount) AS amount,
                    any(pct_chg) AS pct_chg,
                    any(amplitude) AS amplitude,
                    max(change_amt) AS change_amt,
                    any(turnover) AS turnover
                FROM stock_daily
                {base_where}
                GROUP BY code, date
                ORDER BY code, date
            ) AS d
            ASOF LEFT JOIN {factor_table} AS f
              ON d.code = f.code AND d.date >= f.date
            """
            client.execute(insert_query, delete_params)
            if has_range:
                logger.info("stock_daily_qfq 按范围重建完成")
            else:
                logger.info("stock_daily_qfq 指定代码增量重建完成")
            return True
        except Exception as e:
            logger.error(f"stock_daily_qfq 构建失败: {e}")
            try:
                if rebuild_full_table:
                    client.execute(f"DROP TABLE IF EXISTS {tmp_table}")
            except Exception:
                pass
            raise
        finally:
            client.disconnect()

    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def rebuild_weekly_hfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()
    stock_codes = stock_codes or []
    factor_table = None
    try:
        factor_table = _populate_hfq_factor_temp_table(stock_codes, workers)
        return _rebuild_adjust_table_fast(
            base_table="stock_weekly",
            out_table="stock_weekly_hfq",
            factor_table=factor_table,
            stock_codes=stock_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            workers=workers,
        )
    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def rebuild_monthly_hfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()
    stock_codes = stock_codes or []
    factor_table = None
    try:
        factor_table = _populate_hfq_factor_temp_table(stock_codes, workers)
        return _rebuild_adjust_table_fast(
            base_table="stock_monthly",
            out_table="stock_monthly_hfq",
            factor_table=factor_table,
            stock_codes=stock_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            workers=workers,
        )
    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def _rebuild_adjust_table_fast(
    base_table: str,
    out_table: str,
    factor_table: str,
    stock_codes: list[str],
    start_ym: int | None = None,
    end_ym: int | None = None,
    workers: int = 8,
):
    ensure_tables_exist()
    start_date, end_date = _ym_range_to_date_bounds(start_ym, end_ym)
    has_range = start_date is not None or end_date is not None

    rebuild_full_table = stock_codes is not None and len(stock_codes) == 0
    if rebuild_full_table:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    stock_codes = stock_codes or []

    delete_parts = []
    if not rebuild_full_table:
        delete_parts.append("code IN %(codes)s")
    if start_date is not None:
        delete_parts.append("date >= %(start_date)s")
    if end_date is not None:
        delete_parts.append("date <= %(end_date)s")
    delete_where = " AND ".join(delete_parts) if delete_parts else "1"
    params = {"codes": stock_codes, "start_date": start_date, "end_date": end_date}

    base_where_parts = []
    if not rebuild_full_table:
        base_where_parts.append("code IN %(codes)s")
    if start_date is not None:
        base_where_parts.append("date >= %(start_date)s")
    if end_date is not None:
        base_where_parts.append("date <= %(end_date)s")
    base_where = ("WHERE " + " AND ".join(base_where_parts)) if base_where_parts else ""

    client = get_db_client()
    try:
        if has_range:
            logger.info(f"开始重建: {out_table} (范围 {start_date} - {end_date or 'latest'}, ASOF Join)")
        else:
            logger.info(f"开始重建: {out_table} (ASOF Join)")

        client.execute(
            f"ALTER TABLE {out_table} DELETE WHERE {delete_where}",
            params,
            settings={"mutations_sync": 2},
        )

        query = f"""
        INSERT INTO {out_table}
        SELECT
            d.date,
            d.code,
            d.open * ifNull(f.factor, 1.0),
            d.close * ifNull(f.factor, 1.0),
            d.high * ifNull(f.factor, 1.0),
            d.low * ifNull(f.factor, 1.0),
            d.volume,
            d.amount,
            d.pct_chg,
            d.amplitude,
            d.change_amt * ifNull(f.factor, 1.0),
            d.turnover
        FROM
        (
            SELECT
                code,
                date,
                argMin(open, date) AS open,
                argMax(close, date) AS close,
                max(high) AS high,
                min(low) AS low,
                max(volume) AS volume,
                max(amount) AS amount,
                any(pct_chg) AS pct_chg,
                any(amplitude) AS amplitude,
                max(change_amt) AS change_amt,
                any(turnover) AS turnover
            FROM {base_table}
            {base_where}
            GROUP BY code, date
            ORDER BY code, date
        ) AS d
        ASOF LEFT JOIN {factor_table} AS f
          ON d.code = f.code AND d.date >= f.date
        """
        client.execute(query, params)
        return True
    finally:
        client.disconnect()


def rebuild_weekly_qfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()
    stock_codes = stock_codes or []
    factor_table = None
    try:
        factor_table = _populate_qfq_factor_temp_table(stock_codes, workers)
        return _rebuild_adjust_table_fast(
            base_table="stock_weekly",
            out_table="stock_weekly_qfq",
            factor_table=factor_table,
            stock_codes=stock_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            workers=workers,
        )
    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def rebuild_monthly_qfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()
    stock_codes = stock_codes or []
    factor_table = None
    try:
        factor_table = _populate_qfq_factor_temp_table(stock_codes, workers)
        return _rebuild_adjust_table_fast(
            base_table="stock_monthly",
            out_table="stock_monthly_qfq",
            factor_table=factor_table,
            stock_codes=stock_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            workers=workers,
        )
    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def _fill_daily_hfq_missing_for_codes(worker_args):
    codes, flush_rows = worker_args
    client = get_db_client()
    try:
        inserted_total = 0
        batch = []
        flush_rows = int(flush_rows) if flush_rows and int(flush_rows) > 0 else 50000

        for code in codes:
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]

            trading_dates, factor_by_date = _get_hfq_factors_by_date(
                client,
                code,
                dmin.strftime("%Y-%m-%d"),
                dmax.strftime("%Y-%m-%d"),
            )
            if not trading_dates:
                continue

            try:
                rows = client.execute(
                    """
                    SELECT
                        d.date,
                        d.open,
                        d.close,
                        d.high,
                        d.low,
                        d.volume,
                        d.amount,
                        d.pct_chg,
                        d.amplitude,
                        d.change_amt,
                        d.turnover
                    FROM
                    (
                        SELECT
                            date,
                            argMin(open, date) AS open,
                            argMax(close, date) AS close,
                            max(high) AS high,
                            min(low) AS low,
                            max(volume) AS volume,
                            max(amount) AS amount,
                            any(pct_chg) AS pct_chg,
                            any(amplitude) AS amplitude,
                            max(change_amt) AS change_amt,
                            any(turnover) AS turnover
                        FROM stock_daily
                        WHERE code=%(code)s
                        GROUP BY date
                    ) AS d
                    LEFT JOIN stock_daily_hfq AS h
                      ON h.code = %(code)s AND h.date = d.date
                    WHERE h.code = ''
                    ORDER BY d.date ASC
                    """,
                    {"code": code},
                )
            except Exception:
                rows = []
            if not rows:
                continue

            for (
                d,
                o,
                c,
                h,
                l,
                vol,
                amt,
                pct_chg,
                amplitude,
                change_amt,
                turnover,
            ) in rows:
                d_date = d if isinstance(d, date) else _parse_ymd(d)
                if d_date is None:
                    continue
                factor = _get_factor_for_date(trading_dates, factor_by_date, d_date)
                o_f = float(o) if o is not None else 0.0
                c_f = float(c) if c is not None else 0.0
                h_f = float(h) if h is not None else 0.0
                l_f = float(l) if l is not None else 0.0
                batch.append(
                    {
                        "date": d_date,
                        "code": code,
                        "open": o_f * factor,
                        "close": c_f * factor,
                        "high": h_f * factor,
                        "low": l_f * factor,
                        "volume": float(vol) if vol is not None else 0.0,
                        "amount": float(amt) if amt is not None else 0.0,
                        "pct_chg": float(pct_chg) if pct_chg is not None else 0.0,
                        "amplitude": float(amplitude) if amplitude is not None else 0.0,
                        "change_amt": (float(change_amt) if change_amt is not None else 0.0) * factor,
                        "turnover": float(turnover) if turnover is not None else 0.0,
                    }
                )
                if len(batch) >= flush_rows:
                    client.execute(
                        "INSERT INTO stock_daily_hfq (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                        batch,
                        settings={"async_insert": 1, "wait_for_async_insert": 0},
                    )
                    inserted_total += len(batch)
                    batch = []

        if batch:
            client.execute(
                "INSERT INTO stock_daily_hfq (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                batch,
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
            inserted_total += len(batch)

        return inserted_total
    finally:
        client.disconnect()


def fill_daily_hfq_missing(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    flush_rows: int = 50000,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []
    finally:
        client.disconnect()

    workers = int(workers) if workers and int(workers) > 0 else 8
    workers = min(workers, max(1, len(stock_codes)))

    if workers <= 1:
        inserted_total = _fill_daily_hfq_missing_for_codes((stock_codes, int(flush_rows)))
        logger.info(f"stock_daily_hfq 续传补齐完成，总写入: {inserted_total}")
        return inserted_total

    chunk_size = (len(stock_codes) + workers - 1) // workers
    chunks = [stock_codes[i : i + chunk_size] for i in range(0, len(stock_codes), chunk_size)]
    try:
        with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
            parts = pool.map(_fill_daily_hfq_missing_for_codes, [(c, int(flush_rows)) for c in chunks])
        inserted_total = int(sum(parts))
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止子进程...")
        pool.terminate()
        pool.join()
        raise
    logger.info(f"stock_daily_hfq 续传补齐完成，总写入: {inserted_total}")
    return inserted_total


def _fill_daily_qfq_missing_for_codes(worker_args):
    codes, flush_rows = worker_args
    client = get_db_client()
    try:
        inserted_total = 0
        flush_rows = int(flush_rows) if flush_rows and int(flush_rows) > 0 else 50000

        if not codes:
            return 0

        factor_table = _populate_qfq_factor_temp_table(codes, workers=1)

        for code in codes:
            missing = 0
            try:
                row = client.execute(
                    """
                    SELECT count()
                    FROM
                    (
                        SELECT
                            date
                        FROM stock_daily
                        WHERE code = %(code)s
                        GROUP BY date
                    ) AS d
                    LEFT JOIN stock_daily_qfq AS q
                      ON q.code = %(code)s AND q.date = d.date
                    WHERE q.code = ''
                    """,
                    {"code": code},
                )
                missing = int(row[0][0]) if row and row[0][0] is not None else 0
            except Exception:
                missing = 0
            if missing <= 0:
                continue

            try:
                client.execute(
                    """
                    INSERT INTO stock_daily_qfq
                    SELECT
                        d.date,
                        d.code,
                        d.open * f.factor,
                        d.close * f.factor,
                        d.high * f.factor,
                        d.low * f.factor,
                        d.volume,
                        d.amount,
                        d.pct_chg,
                        d.amplitude,
                        d.change_amt * f.factor,
                        d.turnover
                    FROM
                    (
                        SELECT
                            %(code)s AS code,
                            date,
                            argMin(open, date) AS open,
                            argMax(close, date) AS close,
                            max(high) AS high,
                            min(low) AS low,
                            max(volume) AS volume,
                            max(amount) AS amount,
                            any(pct_chg) AS pct_chg,
                            any(amplitude) AS amplitude,
                            max(change_amt) AS change_amt,
                            any(turnover) AS turnover
                        FROM stock_daily
                        WHERE code = %(code)s
                        GROUP BY date
                    ) AS d
                    INNER JOIN {factor_table} AS f
                      ON d.code = f.code AND d.date = f.date
                    LEFT JOIN stock_daily_qfq AS q
                      ON q.code = d.code AND q.date = d.date
                    WHERE q.code = ''
                    ORDER BY d.date ASC
                    """.format(
                        factor_table=factor_table
                    ),
                    {"code": code},
                )
            except Exception:
                continue
            inserted_total += missing

        try:
            client.execute(f"DROP TABLE {factor_table}")
        except Exception:
            pass

        return inserted_total
    finally:
        client.disconnect()


def fill_daily_qfq_missing(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    flush_rows: int = 50000,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []
    finally:
        client.disconnect()

    workers = int(workers) if workers and int(workers) > 0 else 8
    workers = min(workers, max(1, len(stock_codes)))

    if workers <= 1:
        inserted_total = _fill_daily_qfq_missing_for_codes((stock_codes, int(flush_rows)))
        logger.info(f"stock_daily_qfq 续传补齐完成，总写入: {inserted_total}")
        return inserted_total

    chunk_size = (len(stock_codes) + workers - 1) // workers
    chunks = [stock_codes[i : i + chunk_size] for i in range(0, len(stock_codes), chunk_size)]
    try:
        with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
            parts = pool.map(_fill_daily_qfq_missing_for_codes, [(c, int(flush_rows)) for c in chunks])
        inserted_total = int(sum(parts))
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止子进程...")
        pool.terminate()
        pool.join()
        raise
    logger.info(f"stock_daily_qfq 续传补齐完成，总写入: {inserted_total}")
    return inserted_total


def validate_1m_qfq_vs_1d_qfq_bulk(
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_samples: int = 200,
    only_bad: bool = False,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        return _validate_1m_qfq_vs_1d_qfq_bulk_impl(
            client,
            begin_date=begin_date,
            end_date=end_date,
            error_threshold_pct=error_threshold_pct,
            top_n=top_n,
            max_bad_samples=max_bad_samples,
            only_bad=only_bad,
        )
    finally:
        client.disconnect()


def _validate_1m_qfq_vs_1d_qfq_bulk_impl(
    client,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_samples: int = 200,
    only_bad: bool = False,
):
    end_d = _parse_ymd(end_date) or (date.today() - timedelta(days=1))
    begin_d = _parse_ymd(begin_date) or (end_d - timedelta(days=180))
    begin_dt = f"{begin_d.strftime('%Y-%m-%d')} 00:00:00"
    end_next_dt = f"{(end_d + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"

    summary_query = f"""
        WITH
            agg AS (
                SELECT
                    code,
                    toDate(datetime) AS date,
                    argMin(open, datetime) AS open_1m,
                    argMax(close, datetime) AS close_1m,
                    max(high) AS high_1m,
                    min(low) AS low_1m,
                    sum(volume) AS volume_1m_raw,
                    sum(amount) AS amount_1m
                FROM stock_1min_qfq
                WHERE datetime >= toDateTime(%(begin_dt)s)
                  AND datetime < toDateTime(%(end_next_dt)s)
                GROUP BY code, date
            ),
            joined AS (
                SELECT
                    a.code AS code,
                    a.date AS date,
                    a.open_1m AS open_1m,
                    a.close_1m AS close_1m,
                    a.high_1m AS high_1m,
                    a.low_1m AS low_1m,
                    if(d.volume > 0 AND a.volume_1m_raw / d.volume BETWEEN 80 AND 120, a.volume_1m_raw / 100.0, a.volume_1m_raw) AS volume_1m,
                    a.amount_1m AS amount_1m,
                    d.open AS open_1d,
                    d.close AS close_1d,
                    d.high AS high_1d,
                    d.low AS low_1d,
                    d.volume AS volume_1d,
                    d.amount AS amount_1d
                FROM agg a
                INNER JOIN stock_daily_qfq d
                  ON d.code = a.code AND d.date = a.date
                WHERE d.date >= toDate(%(begin)s)
                  AND d.date <= toDate(%(end)s)
            ),
            diffs AS (
                SELECT
                    code,
                    date,
                    if(open_1d = 0, NULL, abs(open_1m - open_1d) / abs(open_1d) * 100) AS open_pct,
                    if(close_1d = 0, NULL, abs(close_1m - close_1d) / abs(close_1d) * 100) AS close_pct,
                    if(high_1d = 0, NULL, abs(high_1m - high_1d) / abs(high_1d) * 100) AS high_pct,
                    if(low_1d = 0, NULL, abs(low_1m - low_1d) / abs(low_1d) * 100) AS low_pct,
                    if(volume_1d = 0, NULL, abs(volume_1m - volume_1d) / abs(volume_1d) * 100) AS volume_pct,
                    if(amount_1d = 0, NULL, abs(amount_1m - amount_1d) / abs(amount_1d) * 100) AS amount_pct
                FROM joined
            ),
            flagged AS (
                SELECT
                    code,
                    date,
                    open_pct,
                    close_pct,
                    high_pct,
                    low_pct,
                    volume_pct,
                    amount_pct,
                    greatest(
                        ifNull(open_pct, 0),
                        ifNull(close_pct, 0),
                        ifNull(high_pct, 0),
                        ifNull(low_pct, 0),
                        ifNull(volume_pct, 0),
                        ifNull(amount_pct, 0)
                    ) AS max_pct,
                    (max_pct > %(thr)s) AS is_bad
                FROM diffs
            )
        SELECT
            code,
            count() AS days_compared,
            sum(is_bad) AS bad_days,
            max(greatest(ifNull(open_pct, 0), ifNull(close_pct, 0), ifNull(high_pct, 0), ifNull(low_pct, 0))) AS max_price_pct_diff,
            max(greatest(ifNull(volume_pct, 0), ifNull(amount_pct, 0))) AS max_value_pct_diff
        FROM flagged
        GROUP BY code
        {"HAVING bad_days > 0" if only_bad else ""}
        ORDER BY bad_days DESC, max_price_pct_diff DESC, max_value_pct_diff DESC, days_compared DESC
        LIMIT %(top_n)s
    """

    try:
        rows = client.execute(
            summary_query,
            {
                "begin_dt": begin_dt,
                "end_next_dt": end_next_dt,
                "begin": begin_d.strftime("%Y-%m-%d"),
                "end": end_d.strftime("%Y-%m-%d"),
                "thr": float(error_threshold_pct),
                "top_n": int(top_n) if top_n and top_n > 0 else 30,
            },
        )
    except Exception:
        rows = []

    df_summary = pd.DataFrame(rows, columns=["code", "days_compared", "bad_days", "max_price_pct_diff", "max_value_pct_diff"])

    bad_samples_query = f"""
        WITH
            agg AS (
                SELECT
                    code,
                    toDate(datetime) AS date,
                    argMin(open, datetime) AS open_1m,
                    argMax(close, datetime) AS close_1m,
                    max(high) AS high_1m,
                    min(low) AS low_1m,
                    sum(volume) AS volume_1m_raw,
                    sum(amount) AS amount_1m
                FROM stock_1min_qfq
                WHERE datetime >= toDateTime(%(begin_dt)s)
                  AND datetime < toDateTime(%(end_next_dt)s)
                GROUP BY code, date
            ),
            joined AS (
                SELECT
                    a.code AS code,
                    a.date AS date,
                    a.open_1m AS open_1m,
                    a.close_1m AS close_1m,
                    a.high_1m AS high_1m,
                    a.low_1m AS low_1m,
                    if(d.volume > 0 AND a.volume_1m_raw / d.volume BETWEEN 80 AND 120, a.volume_1m_raw / 100.0, a.volume_1m_raw) AS volume_1m,
                    a.amount_1m AS amount_1m,
                    d.open AS open_1d,
                    d.close AS close_1d,
                    d.high AS high_1d,
                    d.low AS low_1d,
                    d.volume AS volume_1d,
                    d.amount AS amount_1d
                FROM agg a
                INNER JOIN stock_daily_qfq d
                  ON d.code = a.code AND d.date = a.date
                WHERE d.date >= toDate(%(begin)s)
                  AND d.date <= toDate(%(end)s)
            )
        SELECT
            code,
            date,
            greatest(
                if(open_1d = 0, 0, abs(open_1m - open_1d) / abs(open_1d) * 100),
                if(close_1d = 0, 0, abs(close_1m - close_1d) / abs(close_1d) * 100),
                if(high_1d = 0, 0, abs(high_1m - high_1d) / abs(high_1d) * 100),
                if(low_1d = 0, 0, abs(low_1m - low_1d) / abs(low_1d) * 100),
                if(volume_1d = 0, 0, abs(volume_1m - volume_1d) / abs(volume_1d) * 100),
                if(amount_1d = 0, 0, abs(amount_1m - amount_1d) / abs(amount_1d) * 100)
            ) AS max_pct_diff,
            if(open_1d = 0, 0, abs(open_1m - open_1d) / abs(open_1d) * 100) AS open_pct,
            if(close_1d = 0, 0, abs(close_1m - close_1d) / abs(close_1d) * 100) AS close_pct,
            if(high_1d = 0, 0, abs(high_1m - high_1d) / abs(high_1d) * 100) AS high_pct,
            if(low_1d = 0, 0, abs(low_1m - low_1d) / abs(low_1d) * 100) AS low_pct,
            if(volume_1d = 0, 0, abs(volume_1m - volume_1d) / abs(volume_1d) * 100) AS volume_pct,
            if(amount_1d = 0, 0, abs(amount_1m - amount_1d) / abs(amount_1d) * 100) AS amount_pct
        FROM joined
        WHERE max_pct_diff > %(thr)s
        ORDER BY max_pct_diff DESC, code ASC, date ASC
        LIMIT %(lim)s
    """

    try:
        bad_rows = client.execute(
            bad_samples_query,
            {
                "begin_dt": begin_dt,
                "end_next_dt": end_next_dt,
                "begin": begin_d.strftime("%Y-%m-%d"),
                "end": end_d.strftime("%Y-%m-%d"),
                "thr": float(error_threshold_pct),
                "lim": int(max_bad_samples) if max_bad_samples and max_bad_samples > 0 else 200,
            },
        )
    except Exception:
        bad_rows = []

    df_bad = pd.DataFrame(
        bad_rows,
        columns=["code", "date", "max_pct_diff", "open_pct", "close_pct", "high_pct", "low_pct", "volume_pct", "amount_pct"],
    )
    return df_summary, df_bad


def validate_daily_adjust_consistency(
    adjust: str,
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    sample_codes: int = 50,
    error_threshold_pct: float = 0.05,
    max_bad_samples: int = 200,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        return _validate_daily_adjust_consistency_impl(
            client,
            adjust=adjust,
            stock_codes=stock_codes,
            begin_date=begin_date,
            end_date=end_date,
            sample_codes=sample_codes,
            error_threshold_pct=error_threshold_pct,
            max_bad_samples=max_bad_samples,
        )
    finally:
        client.disconnect()


def _validate_daily_adjust_consistency_impl(
    client,
    adjust: str,
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    sample_codes: int = 50,
    error_threshold_pct: float = 0.05,
    max_bad_samples: int = 200,
):
    adjust = str(adjust or "").strip().lower()
    if adjust not in ("qfq", "hfq"):
        raise ValueError("adjust must be qfq or hfq")

    end_d = _parse_ymd(end_date) or (date.today() - timedelta(days=1))
    begin_d = _parse_ymd(begin_date) or (end_d - timedelta(days=180))
    if begin_d > end_d:
        begin_d, end_d = end_d, begin_d

    sample_codes = int(sample_codes) if sample_codes and int(sample_codes) > 0 else 50
    error_threshold_pct = float(error_threshold_pct) if error_threshold_pct is not None else 0.05
    max_bad_samples = int(max_bad_samples) if max_bad_samples and int(max_bad_samples) > 0 else 200

    if stock_codes:
        codes = [str(c).strip() for c in stock_codes if str(c).strip()]
    else:
        try:
            rows = client.execute(
                "SELECT DISTINCT code FROM stock_info ORDER BY code LIMIT %(n)s",
                {"n": int(sample_codes)},
            )
            codes = [r[0] for r in rows]
        except Exception:
            codes = []

    if len(codes) > sample_codes:
        codes = codes[:sample_codes]

    adj_table = "stock_daily_qfq" if adjust == "qfq" else "stock_daily_hfq"
    summary_rows: list[dict] = []
    bad_rows: list[dict] = []

    def _factor_fn(code: str, begin_s: str, end_s: str):
        if adjust == "qfq":
            return _get_qfq_factors_by_date(client, code, begin_s, end_s)
        return _get_hfq_factors_by_date(client, code, begin_s, end_s)

    def _pct(a: float, b: float):
        denom = max(abs(b), 1e-6)
        return abs(a - b) / denom * 100.0

    for code in codes:
        try:
            base_rows = client.execute(
                """
                SELECT
                    date,
                    argMin(open, date) AS open,
                    argMax(close, date) AS close,
                    max(high) AS high,
                    min(low) AS low,
                    max(volume) AS volume,
                    max(amount) AS amount,
                    max(change_amt) AS change_amt,
                    any(turnover) AS turnover
                FROM stock_daily
                WHERE code = %(code)s
                  AND date >= %(begin)s
                  AND date <= %(end)s
                GROUP BY date
                ORDER BY date ASC
                """,
                {"code": code, "begin": begin_d, "end": end_d},
            )
        except Exception:
            base_rows = []

        if not base_rows:
            continue

        base_dates = [r[0] for r in base_rows if isinstance(r[0], date)]
        dmin = min(base_dates) if base_dates else begin_d
        dmax = max(base_dates) if base_dates else end_d
        trading_dates, factor_by_date = _factor_fn(code, dmin.strftime("%Y-%m-%d"), dmax.strftime("%Y-%m-%d"))

        try:
            adj_rows = client.execute(
                f"""
                SELECT date, open, close, high, low, volume, amount, change_amt, turnover
                FROM {adj_table}
                WHERE code = %(code)s
                  AND date >= %(begin)s
                  AND date <= %(end)s
                ORDER BY date ASC
                """,
                {"code": code, "begin": begin_d, "end": end_d},
            )
        except Exception:
            adj_rows = []

        adj_map: dict[date, tuple] = {}
        for r in adj_rows:
            d = r[0]
            if isinstance(d, date):
                adj_map[d] = r

        missing_dates = 0
        worst_pct = 0.0
        worst_field = ""
        worst_date = None

        for d, o, c, h, l, vol, amt, change_amt, turnover in base_rows:
            if not isinstance(d, date):
                continue

            stored = adj_map.get(d)
            if stored is None:
                missing_dates += 1
                if len(bad_rows) < max_bad_samples:
                    bad_rows.append({"adjust": adjust, "code": code, "date": d, "field": "__missing__", "pct_diff": None})
                continue

            factor = _get_factor_for_date(trading_dates, factor_by_date, d)
            f = float(factor)
            o_calc = (float(o) if o is not None else 0.0) * f
            c_calc = (float(c) if c is not None else 0.0) * f
            h_calc = (float(h) if h is not None else 0.0) * f
            l_calc = (float(l) if l is not None else 0.0) * f
            chg_calc = (float(change_amt) if change_amt is not None else 0.0) * f

            s_open = float(stored[1]) if stored[1] is not None else 0.0
            s_close = float(stored[2]) if stored[2] is not None else 0.0
            s_high = float(stored[3]) if stored[3] is not None else 0.0
            s_low = float(stored[4]) if stored[4] is not None else 0.0
            s_vol = float(stored[5]) if stored[5] is not None else 0.0
            s_amt = float(stored[6]) if stored[6] is not None else 0.0
            s_chg = float(stored[7]) if stored[7] is not None else 0.0

            diffs = {
                "open": _pct(s_open, o_calc),
                "close": _pct(s_close, c_calc),
                "high": _pct(s_high, h_calc),
                "low": _pct(s_low, l_calc),
                "change_amt": _pct(s_chg, chg_calc),
            }

            base_vol = float(vol) if vol is not None else 0.0
            base_amt = float(amt) if amt is not None else 0.0
            if abs(s_vol - base_vol) > 1e-6:
                diffs["volume"] = _pct(s_vol, base_vol)
            if abs(s_amt - base_amt) > 1e-6:
                diffs["amount"] = _pct(s_amt, base_amt)

            for k, v in diffs.items():
                if v > worst_pct:
                    worst_pct = float(v)
                    worst_field = str(k)
                    worst_date = d
                if v > error_threshold_pct and len(bad_rows) < max_bad_samples:
                    bad_rows.append({"adjust": adjust, "code": code, "date": d, "field": k, "pct_diff": float(v)})

        summary_rows.append(
            {
                "adjust": adjust,
                "code": code,
                "base_days": int(len(base_rows)),
                "adj_days": int(len(adj_map)),
                "missing_days": int(missing_dates),
                "max_pct_diff": float(worst_pct),
                "worst_field": worst_field,
                "worst_date": worst_date,
            }
        )
    
    df_summary = (
        pd.DataFrame(summary_rows)
        if summary_rows
        else pd.DataFrame(columns=["adjust", "code", "base_days", "adj_days", "missing_days", "max_pct_diff", "worst_field", "worst_date"])
    )
    df_bad = pd.DataFrame(bad_rows) if bad_rows else pd.DataFrame(columns=["adjust", "code", "date", "field", "pct_diff"])
    return df_summary, df_bad


def build_qfq_tables(
    stock_codes: list[str] | None = None,
    rebuild_daily: bool = True,
    daily_fast: bool = True,
    daily_workers: int = 8,
    minute_mode: str = "backfill",
    minute_workers: int = 4,
    minute_periods: list[str] | None = None,
    start_ym: int | None = None,
    end_ym: int | None = None,
    minute_rebuild_recent_months: int = 0,
    validate_recent_days: int = 60,
    validate_threshold_pct: float = 0.2,
    validate_top_n: int = 200,
    fix_bad_minute: bool = True,
    resume: bool = False,
):
    ensure_tables_exist()
    input_codes_provided = stock_codes is not None
    client = get_db_client()
    try:
        if stock_codes is None:
            try:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
            except Exception:
                stock_codes = []
    finally:
        client.disconnect()

    stock_codes = stock_codes or []
    if (not stock_codes) and (not input_codes_provided):
        raise RuntimeError("无法从 stock_info 获取股票列表，stock_codes 为空")

    start_ym, end_ym = _infer_recent_rebuild_ym_range(
        minute_mode=minute_mode,
        start_ym=start_ym,
        end_ym=end_ym,
        recent_months=minute_rebuild_recent_months,
    )

    if rebuild_daily:
        if resume:
            fill_daily_qfq_missing(stock_codes=stock_codes, workers=int(daily_workers), flush_rows=50000)
        elif daily_fast:
            rebuild_daily_qfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                flush_rows=50000,
                start_ym=start_ym,
                end_ym=end_ym,
            )
            rebuild_weekly_qfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                start_ym=start_ym,
                end_ym=end_ym,
            )
            rebuild_monthly_qfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                start_ym=start_ym,
                end_ym=end_ym,
            )
        else:
            rebuild_daily_qfq_table(stock_codes=stock_codes)

    minute_periods = minute_periods or ["1", "5", "15", "30", "60"]
    if minute_mode == "rebuild":
        rebuild_minute_qfq_tables(stock_codes=stock_codes, periods=minute_periods, start_ym=start_ym, end_ym=end_ym)
    else:
        if minute_workers and int(minute_workers) > 1:
            backfill_minute_qfq_tables_parallel(stock_codes=stock_codes, workers=int(minute_workers), periods=minute_periods)
        else:
            backfill_minute_qfq_tables(stock_codes=stock_codes)

    if validate_recent_days and int(validate_recent_days) > 0:
        end_d = date.today() - timedelta(days=1)
        begin_d = end_d - timedelta(days=int(validate_recent_days))
        df_summary, df_bad = validate_1m_qfq_vs_1d_qfq_bulk(
            begin_date=begin_d,
            end_date=end_d,
            error_threshold_pct=float(validate_threshold_pct),
            top_n=int(validate_top_n) if validate_top_n and int(validate_top_n) > 0 else 200,
            max_bad_samples=200,
            only_bad=True,
        )
        if fix_bad_minute and df_summary is not None and not df_summary.empty:
            bad_codes = df_summary["code"].tolist()
            if bad_codes:
                fix_start_ym = int(begin_d.strftime("%Y%m"))
                fix_end_ym = int(end_d.strftime("%Y%m"))
                rebuild_minute_qfq_tables(
                    stock_codes=bad_codes,
                    periods=["1"],
                    start_ym=fix_start_ym,
                    end_ym=fix_end_ym,
                )
                validate_1m_qfq_vs_1d_qfq_bulk(
                    begin_date=begin_d,
                    end_date=end_d,
                    error_threshold_pct=float(validate_threshold_pct),
                    top_n=int(validate_top_n) if validate_top_n and int(validate_top_n) > 0 else 200,
                    max_bad_samples=200,
                    only_bad=True,
                )
    return True


def _backfill_minute_hfq_for_month(
    client,
    base_table: str,
    hfq_table: str,
    code: str,
    ym: int,
    trading_dates,
    factor_by_date,
    only_missing: bool = True,
):
    start_d, end_d = _ym_to_month_bounds(ym)
    start_dt = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0)

    prev_row = None
    try:
        prev_row = client.execute(
            f"SELECT datetime, close FROM {base_table} WHERE code=%(code)s AND datetime < %(start)s ORDER BY datetime DESC LIMIT 1",
            {"code": code, "start": start_dt},
        )
    except Exception:
        prev_row = None
    pre_close = None
    if prev_row:
        prev_dt = prev_row[0][0]
        prev_close = float(prev_row[0][1]) if prev_row[0][1] is not None else 0.0
        prev_d = prev_dt.date() if isinstance(prev_dt, datetime) else start_d
        prev_factor = _get_factor_for_date(trading_dates, factor_by_date, prev_d)
        if prev_close > 0:
            pre_close = prev_close * prev_factor

    if only_missing:
        query = f"""
            SELECT b.datetime, b.open, b.close, b.high, b.low, b.volume, b.amount
            FROM {base_table} AS b
            LEFT JOIN {hfq_table} AS q
              ON q.code = b.code AND q.datetime = b.datetime
            WHERE b.code = %(code)s
              AND toYYYYMM(b.datetime) = %(ym)s
              AND q.code = ''
            ORDER BY b.datetime ASC
        """
    else:
        query = f"""
            SELECT datetime, open, close, high, low, volume, amount
            FROM {base_table}
            WHERE code = %(code)s
              AND toYYYYMM(datetime) = %(ym)s
            ORDER BY datetime ASC
        """
    try:
        rows = client.execute(query, {"code": code, "ym": int(ym)})
    except Exception:
        rows = []
    if not rows:
        return 0

    batch = []
    inserted = 0
    for dt_value, o, c, h, l, vol, amt in rows:
        dt = dt_value
        row_d = dt.date() if isinstance(dt, datetime) else start_d
        factor = _get_factor_for_date(trading_dates, factor_by_date, row_d)
        o_f = float(o) if o is not None else 0.0
        c_f = float(c) if c is not None else 0.0
        h_f = float(h) if h is not None else 0.0
        l_f = float(l) if l is not None else 0.0
        if c_f <= 0 and o_f > 0:
            c_f = o_f
        if o_f <= 0 and c_f > 0:
            o_f = c_f
        if h_f <= 0:
            h_f = max(o_f, c_f)
        if l_f <= 0:
            l_f = min(o_f, c_f)

        o_adj = o_f * factor
        c_adj = c_f * factor
        h_adj = h_f * factor
        l_adj = l_f * factor
        h_adj = max(h_adj, o_adj, c_adj)
        l_adj = min(l_adj, o_adj, c_adj)

        if pre_close is None or pre_close <= 0:
            pre_close = o_adj if o_adj > 0 else (c_adj if c_adj > 0 else 0.0)

        if pre_close and pre_close > 0:
            pct_chg = (c_adj - pre_close) / pre_close * 100.0
            amplitude = (h_adj - l_adj) / pre_close * 100.0
        else:
            pct_chg = 0.0
            amplitude = 0.0

        pre_close = c_adj if c_adj > 0 else pre_close

        batch.append(
            {
                "datetime": dt,
                "code": code,
                "open": float(o_adj),
                "close": float(c_adj),
                "high": float(h_adj),
                "low": float(l_adj),
                "volume": float(vol) if vol is not None else 0.0,
                "amount": float(amt) if amt is not None else 0.0,
                "pct_chg": float(pct_chg),
                "amplitude": float(amplitude),
            }
        )
        if len(batch) >= 5000:
            client.execute(
                f"INSERT INTO {hfq_table} (datetime,code,open,close,high,low,volume,amount,pct_chg,amplitude) VALUES",
                batch,
            )
            inserted += len(batch)
            batch = []

    if batch:
        client.execute(
            f"INSERT INTO {hfq_table} (datetime,code,open,close,high,low,volume,amount,pct_chg,amplitude) VALUES",
            batch,
        )
        inserted += len(batch)
    return inserted


def backfill_minute_hfq_tables(stock_codes: list[str] | None = None):
    ensure_tables_exist()
    client = get_db_client()
    try:
        try:
            if stock_codes is None:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = stock_codes or []

        factor_cache: dict[str, tuple[list[date], dict[date, float]]] = {}

        periods = ["1", "5", "15", "30", "60"]
        total_inserted = 0
        for p in periods:
            base_table = f"stock_{p}min"
            hfq_table = f"{base_table}_hfq"
            logger.info(f"开始检查补全: {hfq_table}")
            for i, code in enumerate(stock_codes):
                base_min_ym, base_max_ym = _get_minmax_month(client, base_table, code)
                if base_min_ym is None or base_max_ym is None:
                    continue
                start_ym = int(base_min_ym)
                end_ym = int(base_max_ym)

                missing_months = _get_missing_minute_months(client, base_table, hfq_table, code, start_ym, end_ym)
                if not missing_months:
                    continue

                cached = factor_cache.get(code)
                if cached is None:
                    dmin, dmax = _get_minmax_daily_date(client, code)
                    if dmin is None or dmax is None:
                        continue
                    trading_dates, factor_by_date = _get_hfq_factors_by_date(
                        client,
                        code,
                        dmin.strftime("%Y-%m-%d"),
                        dmax.strftime("%Y-%m-%d"),
                    )
                    factor_cache[code] = (trading_dates, factor_by_date)
                else:
                    trading_dates, factor_by_date = cached

                inserted = 0
                for ym, _, _ in missing_months:
                    inserted += _backfill_minute_hfq_for_month(
                        client,
                        base_table,
                        hfq_table,
                        code,
                        ym,
                        trading_dates,
                        factor_by_date,
                        True,
                    )
                if inserted:
                    total_inserted += inserted
                    logger.info(f"{hfq_table} 补全 {code}: +{inserted}")
                if (i + 1) % 200 == 0:
                    logger.info(f"{hfq_table} 进度: {i+1}/{len(stock_codes)}")
            logger.info(f"{hfq_table} 完成")
        logger.info(f"分钟线 _hfq 补全完成，总写入: {total_inserted}")
    finally:
        client.disconnect()


def _backfill_minute_hfq_for_codes(worker_args):
    codes, periods = worker_args
    client = get_db_client()
    factor_cache: dict[str, tuple[list[date], dict[date, float]]] = {}
    total_inserted = 0

    try:
        for p in periods:
            base_table = f"stock_{p}min"
            hfq_table = f"{base_table}_hfq"
            total_codes = len(codes)
            processed = 0
            inserted_p = 0
            start_ts = time.time()
            for code in codes:
                processed += 1
                base_min_ym, base_max_ym = _get_minmax_month(client, base_table, code)
                if base_min_ym is None or base_max_ym is None:
                    continue
                start_ym = int(base_min_ym)
                end_ym = int(base_max_ym)
                missing_months = _get_missing_minute_months(client, base_table, hfq_table, code, start_ym, end_ym)
                if not missing_months:
                    continue

                cached = factor_cache.get(code)
                if cached is None:
                    dmin, dmax = _get_minmax_daily_date(client, code)
                    if dmin is None or dmax is None:
                        continue
                    trading_dates, factor_by_date = _get_hfq_factors_by_date(
                        client,
                        code,
                        dmin.strftime("%Y-%m-%d"),
                        dmax.strftime("%Y-%m-%d"),
                    )
                    factor_cache[code] = (trading_dates, factor_by_date)
                else:
                    trading_dates, factor_by_date = cached

                inserted = 0
                for ym, _, _ in missing_months:
                    inserted += _backfill_minute_hfq_for_month(
                        client,
                        base_table,
                        hfq_table,
                        code,
                        ym,
                        trading_dates,
                        factor_by_date,
                        True,
                    )
                inserted_p += inserted

                if processed % 200 == 0:
                    elapsed = max(time.time() - start_ts, 1e-6)
                    speed = processed / elapsed
                    logger.info(f"{hfq_table} 并行进度: {processed}/{total_codes} (累计写入: {inserted_p}, 速度: {speed:.2f} 只/秒)")

            total_inserted += inserted_p
            elapsed = max(time.time() - start_ts, 1e-6)
            speed = processed / elapsed if processed > 0 else 0.0
            logger.info(f"{hfq_table} 并行分片完成: 处理={processed}/{total_codes} 写入={inserted_p} 速度={speed:.2f} 只/秒")
    finally:
        client.disconnect()
    
    return total_inserted


def backfill_minute_hfq_tables_parallel(
    stock_codes: list[str] | None = None,
    workers: int = 4,
    periods: list[str] | None = None,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []
    finally:
        client.disconnect()

    periods = periods or ["1", "5", "15", "30", "60"]
    workers = int(workers) if workers and int(workers) > 0 else 4
    workers = min(workers, max(1, len(stock_codes)))

    if workers <= 1:
        total_inserted = _backfill_minute_hfq_for_codes((stock_codes, periods))
        logger.info(f"分钟线 _hfq 并行补全完成，总写入: {total_inserted}")
        return total_inserted

    chunk_size = (len(stock_codes) + workers - 1) // workers
    chunks = [stock_codes[i : i + chunk_size] for i in range(0, len(stock_codes), chunk_size)]
    tasks = [(c, periods) for c in chunks]
    total_inserted = 0
    try:
        with mp.Pool(processes=workers, initializer=init_worker_ignore_sigint) as pool:
            for idx, part in enumerate(pool.imap_unordered(_backfill_minute_hfq_for_codes, tasks), start=1):
                total_inserted += int(part)
                logger.info(f"分钟线 _hfq 并行补全进度: {idx}/{len(tasks)} (累计写入: {total_inserted})")
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止子进程...")
        pool.terminate()
        pool.join()
        raise
    logger.info(f"分钟线 _hfq 并行补全完成，总写入: {total_inserted}")
    return total_inserted


def _rebuild_daily_hfq_for_codes(worker_args):
    codes, tmp_table, flush_rows = worker_args
    client = get_db_client()
    try:
        inserted_total = 0
        batch = []
        flush_rows = int(flush_rows) if flush_rows and int(flush_rows) > 0 else 50000

        for code in codes:
            try:
                row = client.execute(
                    "SELECT min(date), max(date) FROM stock_daily WHERE code=%(code)s",
                    {"code": code},
                )
            except Exception:
                row = None
            if not row or row[0][0] is None or row[0][1] is None:
                continue
            dmin, dmax = row[0][0], row[0][1]
            trading_dates, factor_by_date = _get_hfq_factors_by_date(
                client,
                code,
                dmin.strftime("%Y-%m-%d"),
                dmax.strftime("%Y-%m-%d"),
            )
            if not trading_dates:
                continue

            try:
                rows = client.execute(
                    """
                    SELECT
                        date,
                        argMin(open, date) AS open,
                        argMax(close, date) AS close,
                        max(high) AS high,
                        min(low) AS low,
                        max(volume) AS volume,
                        max(amount) AS amount,
                        any(pct_chg) AS pct_chg,
                        any(amplitude) AS amplitude,
                        max(change_amt) AS change_amt,
                        any(turnover) AS turnover
                    FROM stock_daily
                    WHERE code=%(code)s
                    GROUP BY date
                    ORDER BY date ASC
                    """,
                    {"code": code},
                )
            except Exception:
                rows = []
            if not rows:
                continue

            for (
                d,
                o,
                c,
                h,
                l,
                vol,
                amt,
                pct_chg,
                amplitude,
                change_amt,
                turnover,
            ) in rows:
                d_date = d if isinstance(d, date) else _parse_ymd(d)
                if d_date is None:
                    continue
                factor = _get_factor_for_date(trading_dates, factor_by_date, d_date)
                o_f = float(o) if o is not None else 0.0
                c_f = float(c) if c is not None else 0.0
                h_f = float(h) if h is not None else 0.0
                l_f = float(l) if l is not None else 0.0
                batch.append(
                    {
                        "date": d_date,
                        "code": code,
                        "open": o_f * factor,
                        "close": c_f * factor,
                        "high": h_f * factor,
                        "low": l_f * factor,
                        "volume": float(vol) if vol is not None else 0.0,
                        "amount": float(amt) if amt is not None else 0.0,
                        "pct_chg": float(pct_chg) if pct_chg is not None else 0.0,
                        "amplitude": float(amplitude) if amplitude is not None else 0.0,
                        "change_amt": (float(change_amt) if change_amt is not None else 0.0) * factor,
                        "turnover": float(turnover) if turnover is not None else 0.0,
                    }
                )
                if len(batch) >= flush_rows:
                    client.execute(
                        f"INSERT INTO {tmp_table} (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                        batch,
                        settings={"async_insert": 1, "wait_for_async_insert": 0},
                    )
                    inserted_total += len(batch)
                    batch = []

        if batch:
            client.execute(
                f"INSERT INTO {tmp_table} (date,code,open,close,high,low,volume,amount,pct_chg,amplitude,change_amt,turnover) VALUES",
                batch,
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
            inserted_total += len(batch)

        return inserted_total
    finally:
        client.disconnect()


def rebuild_daily_hfq_table_fast(
    stock_codes: list[str] | None = None,
    workers: int = 8,
    flush_rows: int = 50000,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    ensure_tables_exist()
    
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    stock_codes = stock_codes or []
    rebuild_full_table = stock_codes is not None and len(stock_codes) == 0
    if rebuild_full_table:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    ts = int(time.time())
    tmp_table = f"stock_daily_hfq_tmp_{ts}"
    old_table = f"stock_daily_hfq_old_{ts}"

    factor_table = None
    try:
        factor_table = _populate_hfq_factor_temp_table(stock_codes, workers)

        start_date, end_date = _ym_range_to_date_bounds(start_ym, end_ym)
        has_range = start_date is not None or end_date is not None

        client = get_db_client()
        try:
            if rebuild_full_table and not has_range:
                client.execute(f"CREATE TABLE {tmp_table} AS stock_daily_hfq")
                
                logger.info("开始执行 SQL 批量构建后复权日线表 (ASOF Join)...")
                query = f"""
                INSERT INTO {tmp_table}
                SELECT
                    d.date,
                    d.code,
                    d.open * ifNull(f.factor, 1.0),
                    d.close * ifNull(f.factor, 1.0),
                    d.high * ifNull(f.factor, 1.0),
                    d.low * ifNull(f.factor, 1.0),
                    d.volume,
                    d.amount,
                    d.pct_chg,
                    d.amplitude,
                    d.change_amt * ifNull(f.factor, 1.0),
                    d.turnover
                FROM
                (
                    SELECT
                        code,
                        date,
                        argMin(open, date) AS open,
                        argMax(close, date) AS close,
                        max(high) AS high,
                        min(low) AS low,
                        max(volume) AS volume,
                        max(amount) AS amount,
                        any(pct_chg) AS pct_chg,
                        any(amplitude) AS amplitude,
                        max(change_amt) AS change_amt,
                        any(turnover) AS turnover
                    FROM stock_daily
                    WHERE code IN %(codes)s
                    GROUP BY code, date
                    ORDER BY code, date
                ) AS d
                ASOF LEFT JOIN {factor_table} AS f
                  ON d.code = f.code AND d.date >= f.date
                """
                client.execute(query, {"codes": stock_codes})

                client.execute(f"RENAME TABLE stock_daily_hfq TO {old_table}, {tmp_table} TO stock_daily_hfq")
                try:
                    client.execute(f"DROP TABLE {old_table}")
                except Exception:
                    pass
                logger.info("stock_daily_hfq 快速重建完成")
                return True

            delete_parts = []
            if not rebuild_full_table:
                delete_parts.append("code IN %(codes)s")
            if start_date is not None:
                delete_parts.append("date >= %(start_date)s")
            if end_date is not None:
                delete_parts.append("date <= %(end_date)s")
            delete_where = " AND ".join(delete_parts) if delete_parts else "1"
            delete_params = {"codes": stock_codes, "start_date": start_date, "end_date": end_date}

            if has_range:
                logger.info("开始执行 SQL 按范围重建后复权日线表 (ASOF Join)...")
            else:
                logger.info("开始执行 SQL 增量重建后复权日线表 (仅指定代码, ASOF Join)...")

            client.execute(
                f"ALTER TABLE stock_daily_hfq DELETE WHERE {delete_where}",
                delete_params,
                settings={"mutations_sync": 2},
            )

            base_where_parts = []
            if not rebuild_full_table:
                base_where_parts.append("code IN %(codes)s")
            if start_date is not None:
                base_where_parts.append("date >= %(start_date)s")
            if end_date is not None:
                base_where_parts.append("date <= %(end_date)s")
            base_where = ("WHERE " + " AND ".join(base_where_parts)) if base_where_parts else ""

            insert_query = f"""
            INSERT INTO stock_daily_hfq
            SELECT
                d.date,
                d.code,
                d.open * ifNull(f.factor, 1.0),
                d.close * ifNull(f.factor, 1.0),
                d.high * ifNull(f.factor, 1.0),
                d.low * ifNull(f.factor, 1.0),
                d.volume,
                d.amount,
                d.pct_chg,
                d.amplitude,
                d.change_amt * ifNull(f.factor, 1.0),
                d.turnover
            FROM
            (
                SELECT
                    code,
                    date,
                    argMin(open, date) AS open,
                    argMax(close, date) AS close,
                    max(high) AS high,
                    min(low) AS low,
                    max(volume) AS volume,
                    max(amount) AS amount,
                    any(pct_chg) AS pct_chg,
                    any(amplitude) AS amplitude,
                    max(change_amt) AS change_amt,
                    any(turnover) AS turnover
                FROM stock_daily
                {base_where}
                GROUP BY code, date
                ORDER BY code, date
            ) AS d
            ASOF LEFT JOIN {factor_table} AS f
              ON d.code = f.code AND d.date >= f.date
            """
            client.execute(insert_query, delete_params)
            if has_range:
                logger.info("stock_daily_hfq 按范围重建完成")
            else:
                logger.info("stock_daily_hfq 指定代码增量重建完成")
            return True
        except Exception as e:
            logger.error(f"stock_daily_hfq 构建失败: {e}")
            try:
                if rebuild_full_table:
                    client.execute(f"DROP TABLE IF EXISTS {tmp_table}")
            except Exception:
                pass
            raise
        finally:
            client.disconnect()

    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def rebuild_minute_hfq_tables(
    stock_codes: list[str] | None = None,
    periods: list[str] | None = None,
    start_ym: int | None = None,
    end_ym: int | None = None,
):
    ensure_tables_exist()
    
    if stock_codes is None:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    stock_codes = stock_codes or []
    rebuild_full_table = stock_codes is not None and len(stock_codes) == 0
    if rebuild_full_table:
        client = get_db_client()
        try:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        except Exception:
            stock_codes = []
        finally:
            client.disconnect()

    factor_table = None
    try:
        factor_table = _populate_hfq_factor_temp_table(stock_codes, workers=8)

        periods = periods or ["1", "5", "15", "30", "60"]
        client = get_db_client()
        try:
            for p in periods:
                base_table = f"stock_{p}min"
                hfq_table = f"{base_table}_hfq"
                logger.info(f"开始重建: {hfq_table} (ASOF Join)")

                row = client.execute(f"SELECT min(toYYYYMM(datetime)), max(toYYYYMM(datetime)) FROM {base_table}")
                if not row or not row[0][0]:
                    logger.warning(f"{base_table} 为空，跳过")
                    continue

                min_ym, max_ym = row[0][0], row[0][1]
                start_ym_val = int(min_ym) if start_ym is None else max(int(min_ym), int(start_ym))
                end_ym_val = int(max_ym) if end_ym is None else min(int(max_ym), int(end_ym))
                if end_ym_val < start_ym_val:
                    continue

                if rebuild_full_table:
                    client.execute(f"TRUNCATE TABLE {hfq_table}")

                for ym in _iter_ym_range(start_ym_val, end_ym_val):
                    logger.info(f"正在处理 {hfq_table} - {ym} ...")

                    if not rebuild_full_table:
                        client.execute(
                            f"ALTER TABLE {hfq_table} DELETE WHERE code IN %(codes)s AND toYYYYMM(datetime) = %(ym)s",
                            {"codes": stock_codes, "ym": int(ym)},
                            settings={"mutations_sync": 2},
                        )

                    query = f"""
                    INSERT INTO {hfq_table}
                    SELECT
                        m.datetime,
                        m.code,
                        m.open * ifNull(f.factor, 1.0),
                        m.close * ifNull(f.factor, 1.0),
                        m.high * ifNull(f.factor, 1.0),
                        m.low * ifNull(f.factor, 1.0),
                        m.volume,
                        m.amount,
                        m.pct_chg,
                        m.amplitude
                    FROM
                    (
                        SELECT *
                        FROM {base_table}
                        WHERE toYYYYMM(datetime) = %(ym)s
                          AND code IN %(codes)s
                        ORDER BY code, datetime
                    ) AS m
                    ASOF LEFT JOIN {factor_table} AS f
                      ON m.code = f.code AND toDate(m.datetime) >= f.date
                    """
                    client.execute(query, {"codes": stock_codes, "ym": int(ym)})

                logger.info(f"{hfq_table} 重建完成")
        finally:
            client.disconnect()

    finally:
        if factor_table:
            client = get_db_client()
            try:
                client.execute(f"DROP TABLE IF EXISTS {factor_table}")
            except Exception:
                pass
            finally:
                client.disconnect()


def _detect_hfq_factor_changes(client, stock_codes: list[str]) -> set[str]:
    """
    检测分钟线 HFQ 因子是否与最新的日线 HFQ 因子不一致。
    返回需要重建的股票代码集合。
    """
    if not stock_codes:
        return set()

    inconsistent_codes = set()
    
    # 分批处理以避免 Query 过大
    batch_size = 1000
    for i in range(0, len(stock_codes), batch_size):
        batch = stock_codes[i : i + batch_size]
        
        # 查询逻辑：
        # 1. 获取 stock_1min_hfq 中每个股票的最新时间点 (last_dt) 和收盘价 (m_close)
        # 2. 获取 stock_1min 中对应时间点的收盘价 (rm_close)
        # 3. 获取 stock_daily_hfq 中对应日期的收盘价 (d_close)
        # 4. 获取 stock_daily 中对应日期的收盘价 (rd_close)
        # 5. 比较因子: F_min = m_close / rm_close, F_daily = d_close / rd_close
        # 6. 如果 |F_min - F_daily| / F_daily > 0.001 (0.1%), 则认为不一致
        
        query = f"""
        WITH 
            last_min AS (
                SELECT code, toDate(max(datetime)) as date, argMax(close, datetime) as close_hfq
                FROM stock_1min_hfq
                WHERE code IN %(codes)s
                GROUP BY code
            ),
            last_min_raw AS (
                SELECT code, toDate(max(datetime)) as date, argMax(close, datetime) as close_raw
                FROM stock_1min
                WHERE code IN %(codes)s
                GROUP BY code
            ),
            daily_factor AS (
                SELECT d.code, d.date, d.close as close_hfq, rd.close as close_raw
                FROM stock_daily_hfq AS d
                INNER JOIN stock_daily AS rd ON d.code = rd.code AND d.date = rd.date
                WHERE d.code IN %(codes)s
            )
        SELECT m.code
        FROM last_min AS m
        INNER JOIN last_min_raw AS r ON m.code = r.code AND m.date = r.date
        INNER JOIN daily_factor AS d ON m.code = d.code AND m.date = d.date
        WHERE r.close_raw > 0 AND d.close_raw > 0 AND d.close_hfq > 0
          AND abs((m.close_hfq / r.close_raw) - (d.close_hfq / d.close_raw)) > 0.001 * (d.close_hfq / d.close_raw)
        """
        try:
            rows = client.execute(query, {"codes": batch})
            for row in rows:
                inconsistent_codes.add(row[0])
        except Exception as e:
            logger.error(f"检测 HFQ 因子一致性失败: {e}")
            inconsistent_codes.update(batch)

    return inconsistent_codes

def build_hfq_tables(
    stock_codes: list[str] | None = None,
    rebuild_daily: bool = True,
    daily_fast: bool = True,
    daily_workers: int = 8,
    minute_mode: str = "backfill",
    minute_workers: int = 4,
    minute_periods: list[str] | None = None,
    start_ym: int | None = None,
    end_ym: int | None = None,
    minute_rebuild_recent_months: int = 0,
):
    ensure_tables_exist()
    input_codes_provided = stock_codes is not None
    client = get_db_client()
    try:
        if stock_codes is None:
            try:
                stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
            except Exception:
                stock_codes = []
    finally:
        client.disconnect()

    stock_codes = stock_codes or []
    if (not stock_codes) and (not input_codes_provided):
        raise RuntimeError("无法从 stock_info 获取股票列表，stock_codes 为空")

    start_ym, end_ym = _infer_recent_rebuild_ym_range(
        minute_mode=minute_mode,
        start_ym=start_ym,
        end_ym=end_ym,
        recent_months=minute_rebuild_recent_months,
    )

    # 1. 重建日线 HFQ (基准)
    if rebuild_daily:
        if daily_fast:
            rebuild_daily_hfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                flush_rows=50000,
                start_ym=start_ym,
                end_ym=end_ym,
            )
            rebuild_weekly_hfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                start_ym=start_ym,
                end_ym=end_ym,
            )
            rebuild_monthly_hfq_table_fast(
                stock_codes=stock_codes,
                workers=int(daily_workers),
                start_ym=start_ym,
                end_ym=end_ym,
            )
        else:
             # Fallback logic if needed, but currently rebuild_daily_hfq_table_fast is the main one
             pass

    minute_periods = minute_periods or ["1", "5", "15", "30", "60"]
    
    # 2. 分钟线处理
    if minute_mode == "rebuild":
        # 强制全量重建
        rebuild_minute_hfq_tables(stock_codes=stock_codes, periods=minute_periods, start_ym=start_ym, end_ym=end_ym)
    else:
        # backfill 模式 (默认)：智能判断
        # 策略：
        # A. 如果股票在 stock_1min_hfq 中不存在 -> Backfill (其实 Backfill 会处理空表情况，等同于从头补全)
        # B. 如果股票存在，但因子发生了变化 (与最新日线HFQ不一致) -> Rebuild
        # C. 如果股票存在且因子一致 -> Backfill (增量更新)
        
        logger.info("正在检测分钟线 HFQ 因子变化，以决定增量更新还是重建...")
        client = get_db_client()
        try:
            inconsistent_codes = _detect_hfq_factor_changes(client, stock_codes)
        finally:
            client.disconnect()
            
        rebuild_codes = list(inconsistent_codes)
        backfill_codes = [c for c in stock_codes if c not in inconsistent_codes]
        
        if rebuild_codes:
            logger.info(f"检测到 {len(rebuild_codes)} 只股票因子发生变化，将进行重建: {rebuild_codes[:5]}...")
            rebuild_minute_hfq_tables(stock_codes=rebuild_codes, periods=minute_periods, start_ym=start_ym, end_ym=end_ym)
            
        if backfill_codes:
            logger.info(f"剩余 {len(backfill_codes)} 只股票因子未变，进行增量补全...")
            if minute_workers and int(minute_workers) > 1:
                backfill_minute_hfq_tables_parallel(stock_codes=backfill_codes, workers=int(minute_workers), periods=minute_periods)
            else:
                backfill_minute_hfq_tables(stock_codes=backfill_codes)
                
    return True


def validate_1m_qfq_vs_1d_qfq(
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_dates_per_code: int = 3,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        return _validate_1m_qfq_vs_1d_qfq_impl(
            client,
            stock_codes=stock_codes,
            begin_date=begin_date,
            end_date=end_date,
            error_threshold_pct=error_threshold_pct,
            top_n=top_n,
            max_bad_dates_per_code=max_bad_dates_per_code,
        )
    finally:
        client.disconnect()


def _validate_1m_qfq_vs_1d_qfq_impl(
    client,
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_dates_per_code: int = 3,
):

    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []

    end_d = _parse_ymd(end_date) or (date.today() - timedelta(days=1))
    begin_d = _parse_ymd(begin_date) or (end_d - timedelta(days=180))
    begin_dt = f"{begin_d.strftime('%Y-%m-%d')} 00:00:00"
    end_next_dt = f"{(end_d + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"

    summary_rows: list[dict] = []
    bad_samples: list[dict] = []

    total_codes = len(stock_codes)
    start_ts = time.time()

    for idx, code in enumerate(stock_codes, start=1):
        try:
            daily_rows = client.execute(
                """
                SELECT date, open, close, high, low, volume, amount
                FROM stock_daily_qfq
                WHERE code=%(code)s
                  AND date >= %(begin)s
                  AND date <= %(end)s
                ORDER BY date ASC
                """,
                {"code": code, "begin": begin_d, "end": end_d},
            )
        except Exception:
            daily_rows = []

        if not daily_rows:
            continue

        try:
            minute_rows = client.execute(
                """
                SELECT
                    toDate(datetime) AS date,
                    argMin(open, datetime) AS open,
                    argMax(close, datetime) AS close,
                    max(high) AS high,
                    min(low) AS low,
                    sum(volume) AS volume,
                    sum(amount) AS amount
                FROM stock_1min_qfq
                WHERE code=%(code)s
                  AND datetime >= toDateTime(%(begin_dt)s)
                  AND datetime < toDateTime(%(end_next_dt)s)
                GROUP BY date
                ORDER BY date ASC
                """,
                {"code": code, "begin_dt": begin_dt, "end_next_dt": end_next_dt},
            )
        except Exception:
            minute_rows = []

        if not minute_rows:
            continue

        df_daily = pd.DataFrame(daily_rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
        df_min = pd.DataFrame(minute_rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
        if df_daily.empty or df_min.empty:
            continue

        for col in ["open", "close", "high", "low", "volume", "amount"]:
            df_daily[col] = pd.to_numeric(df_daily[col], errors="coerce")
            df_min[col] = pd.to_numeric(df_min[col], errors="coerce")

        df = df_min.merge(df_daily, on="date", how="inner", suffixes=("_1m", "_1d"))
        if df.empty:
            continue

        # 智能识别手数 (Daily Volume Unit Detection)
        # 通过 Amount / Volume / Price 判断是否为 100 倍关系
        # 避免仅依赖 Volume Ratio (因为 Volume 本身可能有差异)
        
        # 计算 Daily VWAP (Amount / Volume)
        daily_vol_safe = df["volume_1d"].replace(0, pd.NA)
        daily_vwap = df["amount_1d"] / daily_vol_safe
        
        # 比较 VWAP 与 Close Price
        # 如果 VWAP / Close ~ 100, 说明 Volume 是手数 (1手=100股)
        price_ratio = daily_vwap / df["close_1d"].replace(0, pd.NA)
        is_lots = (price_ratio >= 80.0) & (price_ratio <= 120.0)
        
        if is_lots.any():
            # 将 Daily Volume 转换为股数 (x100)
            df.loc[is_lots, "volume_1d"] *= 100.0

        # 原有的基于 Ratio 的兜底逻辑 (防止上述逻辑失效)
        vol_ratio = df["volume_1m"] / df["volume_1d"].replace(0, pd.NA)
        vol_ratio = vol_ratio.replace([float("inf"), float("-inf")], pd.NA)
        # 如果差异在 80-120 倍之间，且上面没有处理过 (避免重复乘除)
        # 这里统一逻辑：如果 Minute/Daily ~ 100，说明 Daily 是手，或者 Minute 是股
        # 上面已经把 Daily 乘了 100 (变成了股)。
        # 如果上面没识别出来 (比如 Price 也不对)，但 Volume Ratio 确实是 100
        # 那么再次尝试修正。
        # 现在的 Volume Ratio 应该是 1 (如果是股对股)。
        # 如果还是 100，说明上面没覆盖到。
        
        scale_mask = (vol_ratio >= 80.0) & (vol_ratio <= 120.0) & (~is_lots)
        if bool(scale_mask.any()):
             df.loc[scale_mask, "volume_1d"] *= 100.0 # 统一修正 Daily


        price_cols = ["open", "close", "high", "low"]
        value_cols = ["volume", "amount"]
        metric_cols = price_cols + value_cols

        pct_cols = {}
        for col in metric_cols:
            left = df[f"{col}_1m"]
            right = df[f"{col}_1d"]
            denom = right.abs().replace(0, pd.NA)
            pct = ((left - right).abs() / denom) * 100.0
            pct_cols[col] = pct
            df[f"{col}_pct_diff"] = pct

        max_price_pct = float(pd.concat([pct_cols[c] for c in price_cols], axis=1).max(axis=1).max(skipna=True))
        max_value_pct = float(pd.concat([pct_cols[c] for c in value_cols], axis=1).max(axis=1).max(skipna=True))

        bad_mask = pd.Series(False, index=df.index)
        for col in metric_cols:
            bad_mask = bad_mask | (df[f"{col}_pct_diff"] > float(error_threshold_pct))

        bad_days = int(bad_mask.sum())
        compared_days = int(len(df))

        summary_rows.append(
            {
                "code": code,
                "begin": begin_d,
                "end": end_d,
                "days_compared": compared_days,
                "bad_days": bad_days,
                "max_price_pct_diff": max_price_pct if pd.notna(max_price_pct) else 0.0,
                "max_value_pct_diff": max_value_pct if pd.notna(max_value_pct) else 0.0,
            }
        )

        if bad_days > 0 and max_bad_dates_per_code > 0:
            df_bad = df.loc[bad_mask, ["date"] + [f"{c}_pct_diff" for c in metric_cols]].copy()
            df_bad["max_pct_diff"] = df_bad[[f"{c}_pct_diff" for c in metric_cols]].max(axis=1)
            df_bad = df_bad.sort_values(["max_pct_diff", "date"], ascending=[False, True]).head(int(max_bad_dates_per_code))
            for _, r in df_bad.iterrows():
                bad_samples.append(
                    {
                        "code": code,
                        "date": r["date"],
                        "max_pct_diff": float(r["max_pct_diff"]) if pd.notna(r["max_pct_diff"]) else 0.0,
                        "open_pct": float(r["open_pct_diff"]) if pd.notna(r["open_pct_diff"]) else 0.0,
                        "close_pct": float(r["close_pct_diff"]) if pd.notna(r["close_pct_diff"]) else 0.0,
                        "high_pct": float(r["high_pct_diff"]) if pd.notna(r["high_pct_diff"]) else 0.0,
                        "low_pct": float(r["low_pct_diff"]) if pd.notna(r["low_pct_diff"]) else 0.0,
                        "volume_pct": float(r["volume_pct_diff"]) if pd.notna(r["volume_pct_diff"]) else 0.0,
                        "amount_pct": float(r["amount_pct_diff"]) if pd.notna(r["amount_pct_diff"]) else 0.0,
                    }
                )

        if idx % 200 == 0:
            elapsed = max(time.time() - start_ts, 1e-6)
            speed = idx / elapsed
            logger.info(f"QFQ 校验进度: {idx}/{total_codes} (速度: {speed:.2f} 只/秒)")

    df_summary = pd.DataFrame(summary_rows)
    if df_summary.empty:
        logger.info("QFQ 校验完成：无可对比数据")
        return df_summary, pd.DataFrame(bad_samples)

    df_summary = df_summary.sort_values(
        ["bad_days", "max_price_pct_diff", "max_value_pct_diff", "days_compared"],
        ascending=[False, False, False, False],
    )

    total_bad_codes = int((df_summary["bad_days"] > 0).sum())
    total_bad_days = int(df_summary["bad_days"].sum())
    total_days = int(df_summary["days_compared"].sum())
    logger.info(
        "QFQ 校验完成："
        f"对比股票={len(df_summary)}，"
        f"对比天数={total_days}，"
        f"异常股票={total_bad_codes}，"
        f"异常天数={total_bad_days}，"
        f"阈值={error_threshold_pct:.6g}%"
    )

    show_n = int(top_n) if top_n and top_n > 0 else 30
    logger.info("异常 Top 列表：\n" + df_summary.head(show_n).to_string(index=False))

    df_bad_samples = pd.DataFrame(bad_samples)
    if not df_bad_samples.empty:
        df_bad_samples = df_bad_samples.sort_values(["max_pct_diff", "code", "date"], ascending=[False, True, True])
        logger.info("异常日期样本：\n" + df_bad_samples.head(show_n).to_string(index=False))

    return df_summary, df_bad_samples


def validate_1m_hfq_vs_1d_hfq(
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_dates_per_code: int = 3,
):
    ensure_tables_exist()
    client = get_db_client()
    try:
        return _validate_1m_hfq_vs_1d_hfq_impl(
            client,
            stock_codes=stock_codes,
            begin_date=begin_date,
            end_date=end_date,
            error_threshold_pct=error_threshold_pct,
            top_n=top_n,
            max_bad_dates_per_code=max_bad_dates_per_code,
        )
    finally:
        client.disconnect()


def _validate_1m_hfq_vs_1d_hfq_impl(
    client,
    stock_codes: list[str] | None = None,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    error_threshold_pct: float = 0.2,
    top_n: int = 30,
    max_bad_dates_per_code: int = 3,
):

    try:
        if stock_codes is None:
            stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
    except Exception:
        stock_codes = stock_codes or []

    end_d = _parse_ymd(end_date) or (date.today() - timedelta(days=1))
    begin_d = _parse_ymd(begin_date) or (end_d - timedelta(days=180))
    begin_dt = f"{begin_d.strftime('%Y-%m-%d')} 00:00:00"
    end_next_dt = f"{(end_d + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"

    summary_rows: list[dict] = []
    bad_samples: list[dict] = []

    total_codes = len(stock_codes)
    start_ts = time.time()

    for idx, code in enumerate(stock_codes, start=1):
        try:
            daily_rows = client.execute(
                """
                SELECT date, open, close, high, low, volume, amount
                FROM stock_daily_hfq
                WHERE code=%(code)s
                  AND date >= %(begin)s
                  AND date <= %(end)s
                ORDER BY date ASC
                """,
                {"code": code, "begin": begin_d, "end": end_d},
            )
        except Exception:
            daily_rows = []

        if not daily_rows:
            continue

        try:
            minute_rows = client.execute(
                """
                SELECT
                    toDate(datetime) AS date,
                    argMin(open, datetime) AS open,
                    argMax(close, datetime) AS close,
                    max(high) AS high,
                    min(low) AS low,
                    sum(volume) AS volume,
                    sum(amount) AS amount
                FROM stock_1min_hfq
                WHERE code=%(code)s
                  AND datetime >= toDateTime(%(begin_dt)s)
                  AND datetime < toDateTime(%(end_next_dt)s)
                GROUP BY date
                ORDER BY date ASC
                """,
                {"code": code, "begin_dt": begin_dt, "end_next_dt": end_next_dt},
            )
        except Exception:
            minute_rows = []

        if not minute_rows:
            continue

        df_daily = pd.DataFrame(daily_rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
        df_min = pd.DataFrame(minute_rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
        if df_daily.empty or df_min.empty:
            continue

        for col in ["open", "close", "high", "low", "volume", "amount"]:
            df_daily[col] = pd.to_numeric(df_daily[col], errors="coerce")
            df_min[col] = pd.to_numeric(df_min[col], errors="coerce")

        df = df_min.merge(df_daily, on="date", how="inner", suffixes=("_1m", "_1d"))
        if df.empty:
            continue

        vol_ratio = df["volume_1m"] / df["volume_1d"].replace(0, pd.NA)
        vol_ratio = vol_ratio.replace([float("inf"), float("-inf")], pd.NA)
        scale_mask = (vol_ratio >= 80.0) & (vol_ratio <= 120.0)
        if bool(scale_mask.any()):
            df.loc[scale_mask, "volume_1m"] = df.loc[scale_mask, "volume_1m"] / 100.0

        price_cols = ["open", "close", "high", "low"]
        value_cols = ["volume", "amount"]
        metric_cols = price_cols + value_cols

        pct_cols = {}
        for col in metric_cols:
            left = df[f"{col}_1m"]
            right = df[f"{col}_1d"]
            denom = right.abs().replace(0, pd.NA)
            pct = ((left - right).abs() / denom) * 100.0
            pct_cols[col] = pct
            df[f"{col}_pct_diff"] = pct

        max_price_pct = float(pd.concat([pct_cols[c] for c in price_cols], axis=1).max(axis=1).max(skipna=True))
        max_value_pct = float(pd.concat([pct_cols[c] for c in value_cols], axis=1).max(axis=1).max(skipna=True))

        bad_mask = pd.Series(False, index=df.index)
        for col in metric_cols:
            bad_mask = bad_mask | (df[f"{col}_pct_diff"] > float(error_threshold_pct))

        bad_days = int(bad_mask.sum())
        compared_days = int(len(df))

        summary_rows.append(
            {
                "code": code,
                "begin": begin_d,
                "end": end_d,
                "days_compared": compared_days,
                "bad_days": bad_days,
                "max_price_pct_diff": max_price_pct if pd.notna(max_price_pct) else 0.0,
                "max_value_pct_diff": max_value_pct if pd.notna(max_value_pct) else 0.0,
            }
        )

        if bad_days > 0 and max_bad_dates_per_code > 0:
            df_bad = df.loc[bad_mask, ["date"] + [f"{c}_pct_diff" for c in metric_cols]].copy()
            df_bad["max_pct_diff"] = df_bad[[f"{c}_pct_diff" for c in metric_cols]].max(axis=1)
            df_bad = df_bad.sort_values(["max_pct_diff", "date"], ascending=[False, True]).head(int(max_bad_dates_per_code))
            for _, r in df_bad.iterrows():
                bad_samples.append(
                    {
                        "code": code,
                        "date": r["date"],
                        "max_pct_diff": float(r["max_pct_diff"]) if pd.notna(r["max_pct_diff"]) else 0.0,
                        "open_pct": float(r["open_pct_diff"]) if pd.notna(r["open_pct_diff"]) else 0.0,
                        "close_pct": float(r["close_pct_diff"]) if pd.notna(r["close_pct_diff"]) else 0.0,
                        "high_pct": float(r["high_pct_diff"]) if pd.notna(r["high_pct_diff"]) else 0.0,
                        "low_pct": float(r["low_pct_diff"]) if pd.notna(r["low_pct_diff"]) else 0.0,
                        "volume_pct": float(r["volume_pct_diff"]) if pd.notna(r["volume_pct_diff"]) else 0.0,
                        "amount_pct": float(r["amount_pct_diff"]) if pd.notna(r["amount_pct_diff"]) else 0.0,
                    }
                )

        if idx % 200 == 0:
            elapsed = max(time.time() - start_ts, 1e-6)
            speed = idx / elapsed
            logger.info(f"HFQ 校验进度: {idx}/{total_codes} (速度: {speed:.2f} 只/秒)")

    df_summary = pd.DataFrame(summary_rows)
    if df_summary.empty:
        logger.info("HFQ 校验完成：无可对比数据")
        return df_summary, pd.DataFrame(bad_samples)

    df_summary = df_summary.sort_values(
        ["bad_days", "max_price_pct_diff", "max_value_pct_diff", "days_compared"],
        ascending=[False, False, False, False],
    )

    total_bad_codes = int((df_summary["bad_days"] > 0).sum())
    total_bad_days = int(df_summary["bad_days"].sum())
    total_days = int(df_summary["days_compared"].sum())
    logger.info(
        "HFQ 校验完成："
        f"对比股票={len(df_summary)}，"
        f"对比天数={total_days}，"
        f"异常股票={total_bad_codes}，"
        f"异常天数={total_bad_days}，"
        f"阈值={error_threshold_pct:.6g}%"
    )

    show_n = int(top_n) if top_n and top_n > 0 else 30
    logger.info("异常 Top 列表：\n" + df_summary.head(show_n).to_string(index=False))

    df_bad_samples = pd.DataFrame(bad_samples)
    if not df_bad_samples.empty:
        df_bad_samples = df_bad_samples.sort_values(["max_pct_diff", "code", "date"], ascending=[False, True, True])
        logger.info("异常日期样本：\n" + df_bad_samples.head(show_n).to_string(index=False))

    return df_summary, df_bad_samples

def _em_market_from_prefixed_code(stock_code: str, symbol_6digit: str) -> str:
    sc = str(stock_code or "")
    if sc.startswith("sh"):
        return "1"
    if sc.startswith("sz"):
        return "0"
    if sc.startswith("bj"):
        return "0"
    s6 = str(symbol_6digit or "")
    return "1" if s6.startswith("6") else "0"

@retry(max_retries=3, delay=1, backoff=2)
def fetch_kline_em(stock_code, symbol_6digit, klt, adjust='', beg=None, end='20500000'):
    try:
        market = _em_market_from_prefixed_code(stock_code, symbol_6digit)
        secid = f"{market}.{symbol_6digit}"
        fqt = '1' if adjust == 'qfq' else '0'
        url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
        beg_val = beg if beg else '0'
        params = {
            'secid': secid,
            'klt': klt,
            'fqt': fqt,
            'lmt': '100000',
            'beg': beg_val,
            'end': end,
            'iscca': '1',
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
        }
        response = _http_get(url, params=params, timeout=5)
        data = response.json()
        if not data or 'data' not in data or not data['data'] or 'klines' not in data['data']:
            return pd.DataFrame()
        klines = data['data']['klines']
        rows = []
        for kline in klines:
            parts = kline.split(',')
            if len(parts) < 11: continue
            
            vol = float(parts[5])
            # API 返回的是手，转换为股
            if str(klt) in ('101', '102', '103', '1', '5', '15', '30', '60'):
                vol *= 100.0
                
            item = {
                'datetime': parts[0],
                'open': float(parts[1]),
                'close': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'volume': vol,
                'amount': float(parts[6]),
                'amplitude': float(parts[7]),
                'pct_chg': float(parts[8]),
                'change_amt': float(parts[9]),
            }
            # 分钟线通常不包含换手率，避免插入数据库时字段不匹配
            if str(klt) not in ('1', '5', '15', '30', '60'):
                item['turnover'] = float(parts[10])
            rows.append(item)
        return pd.DataFrame(rows)
    except Exception as e:
        raise e 

def _yyyymmdd_to_yyyy_mm_dd(s):
    if not s:
        return None
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s

@retry(max_retries=3, delay=1, backoff=2)
def fetch_kline_tencent(stock_code, klt, adjust='', beg=None):
    klt_map = {
        '101': 'day',
        '102': 'week',
        '103': 'month',
        '1': 'm1',
        '5': 'm5',
        '15': 'm15',
        '30': 'm30',
        '60': 'm60'
    }
    tag = klt_map.get(str(klt))
    if not tag:
        return pd.DataFrame()

    start = _yyyymmdd_to_yyyy_mm_dd(beg) or '2000-01-01'
    end = datetime.now().strftime('%Y-%m-%d')
    adj = 'qfq' if adjust == 'qfq' else ''
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        'param': f"{stock_code},{tag},{start},{end},2000,{adj}"
    }
    resp = _http_get(url, params=params, timeout=5)
    payload = resp.json()
    data = payload.get('data') or {}
    stock_payload = data.get(stock_code)
    if not stock_payload and data:
        stock_payload = next(iter(data.values()))
    if not stock_payload:
        return pd.DataFrame()
    klines = stock_payload.get(tag) or []
    if not klines:
        return pd.DataFrame()

    rows = []
    for item in klines:
        if not item or len(item) < 6:
            continue
        dt_str = item[0]
        try:
            vol = float(item[5])
            if str(klt) in ('101', '102', '103', '1', '5', '15', '30', '60'):
                vol *= 100.0

            rows.append({
                'datetime': dt_str,
                'open': float(item[1]),
                'close': float(item[2]),
                'high': float(item[3]),
                'low': float(item[4]),
                'volume': vol
            })
        except Exception:
            continue
    return pd.DataFrame(rows)

@retry(max_retries=3, delay=1, backoff=2)
def fetch_kline_sina(stock_code, klt):
    if str(klt) not in {'1', '5', '15', '30', '60'}:
        return pd.DataFrame()
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        'symbol': stock_code,
        'scale': str(klt),
        'ma': 'no',
        'datalen': '1023'
    }
    resp = _http_get(url, params=params, timeout=5)
    text = resp.text.strip()
    if not text or text == 'null':
        return pd.DataFrame()
    try:
        items = json.loads(text)
    except Exception:
        return pd.DataFrame()
    if not items:
        return pd.DataFrame()
    rows = []
    for it in items:
        try:
            dt_str = it.get('day')
            if not dt_str:
                continue
            rows.append({
                'datetime': dt_str,
                'open': float(it.get('open', 0) or 0),
                'close': float(it.get('close', 0) or 0),
                'high': float(it.get('high', 0) or 0),
                'low': float(it.get('low', 0) or 0),
                'volume': float(it.get('volume', 0) or 0) * 100.0
            })
        except Exception:
            continue
    return pd.DataFrame(rows)

def _df_prices_all_positive(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    for col in ("open", "close", "high", "low"):
        if col not in df.columns:
            continue
        try:
            if (pd.to_numeric(df[col], errors="coerce") <= 0).any():
                return False
        except Exception:
            return False
    return True

def fetch_kline_multi_source(stock_code, symbol_6digit, klt, adjust='', beg=None):
    source = str(globals().get("KLINE_SOURCE") or "auto").strip().lower()
    if source == "tdx" and (not adjust):
        if _is_index_code(str(stock_code), str(symbol_6digit)) and str(klt) in {"101", "102", "103", "30", "60"}:
            try:
                df = fetch_kline_tdx_index(str(stock_code), str(symbol_6digit), str(klt), beg=beg)
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
        else:
            try:
                df = fetch_kline_tdx_security(str(stock_code), str(symbol_6digit), str(klt), beg=beg)
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
    elif (not adjust) and _is_index_code(str(stock_code), str(symbol_6digit)) and str(klt) in {"101", "102", "103", "30", "60"}:
        try:
            df = fetch_kline_tdx_index(str(stock_code), str(symbol_6digit), str(klt), beg=beg)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
    try:
        df = fetch_kline_em(stock_code, symbol_6digit, klt, adjust, beg=beg)
        if df is not None and not df.empty:
            if adjust in ("qfq", "hfq") and (not _df_prices_all_positive(df)):
                df = pd.DataFrame()
            else:
                return df
    except Exception:
        pass
    try:
        df = fetch_kline_tencent(stock_code, klt, adjust, beg=beg)
        if df is not None and not df.empty:
            if adjust in ("qfq", "hfq") and (not _df_prices_all_positive(df)):
                df = pd.DataFrame()
            else:
                return df
    except Exception:
        pass
    if adjust != 'qfq':
        try:
            df = fetch_kline_sina(stock_code, klt)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
    return pd.DataFrame()

def init_worker_ignore_sigint():
    """
    Worker process initializer that ignores SIGINT (Ctrl+C).
    This ensures that only the main process handles the interrupt
    and can cleanly terminate the pool.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    # Windows下多进程写入同一个日志文件可能会导致竞争死锁
    # 在子进程中移除 FileHandler，只保留 StreamHandler
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)

def init_worker(queue, dates_map, kline_source: str = "auto"):
    # Ignore SIGINT in worker processes
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    global shared_queue
    global shared_dates_map
    global KLINE_SOURCE
    global _http_session
    shared_queue = queue
    shared_dates_map = dates_map
    KLINE_SOURCE = str(kline_source or "auto").strip().lower()
    _http_session = requests.Session()
    _http_session.headers.update({'User-Agent': 'Mozilla/5.0'})

def _http_get(url, params, timeout=5):
    sess = globals().get('_http_session')
    if sess is not None:
        return sess.get(url, params=params, timeout=timeout)
    return requests.get(url, params=params, timeout=timeout)

def download_stock_data(args):
    """
    下载进程工作函数：下载数据 -> 保存为 parquet -> 发送文件路径到队列
    """
    stock_info, tasks = args
    stock_code, symbol_6digit = stock_info
    file_queue = shared_queue
    max_dates_map = shared_dates_map
    
    try:
        for task in tasks:
            table_name = task['table']
            is_minute = task.get('is_minute', False)
            period = task['period']
            adjust = task['adjust']
            klt = task['klt']
            
            # 确定开始时间
            start_date = None
            if table_name in max_dates_map and stock_code in max_dates_map[table_name]:
                start_date = max_dates_map[table_name][stock_code]
            
            # 优化：如果盘中运行且数据已更新到昨日，则跳过
            if start_date:
                now = datetime.now()
                is_intraday = not _is_market_closed(now)
                yesterday_date = now.date() - timedelta(days=1)
                
                check_date = start_date
                if isinstance(check_date, datetime):
                    check_date = check_date.date()
                    
                if is_intraday and check_date >= yesterday_date:
                    # 已经是最新了，跳过
                    continue

            if not start_date:
                start_date = datetime(2000, 1, 1) if is_minute else date(2000, 1, 1)
            
            # 转换日期格式
            end_date_str = datetime.now().strftime('%Y%m%d')
            if isinstance(start_date, datetime):
                 start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(start_date, date):
                 start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
            else:
                 start_date_str = '2000-01-01 09:30:00'

            df = pd.DataFrame()
            try:
                beg = None
                if isinstance(start_date, datetime):
                    beg = start_date.strftime('%Y%m%d')
                elif isinstance(start_date, date):
                    beg = start_date.strftime('%Y%m%d')
                if is_minute:
                    df = fetch_kline_multi_source(stock_code, symbol_6digit, klt, adjust, beg=beg)
                else:
                    df = fetch_kline_multi_source(stock_code, symbol_6digit, klt, adjust, beg=beg)
                    if 'datetime' in df.columns:
                         df['date'] = pd.to_datetime(df['datetime']).dt.date
                         del df['datetime']

                # 校验和预处理
                if not validate_data(df, period, allow_zero_amount=task.get('allow_zero_amount', False)):
                    continue
                
                # 统一列
                if 'datetime' in df.columns:
                    df['datetime'] = pd.to_datetime(df['datetime'])
                df['code'] = stock_code
                
                now = datetime.now()
                if not _is_market_closed(now):
                    today_date = now.date()
                    if is_minute:
                         # 分钟线：过滤掉今天的
                         df = df[df['datetime'].dt.date < today_date]
                    else:
                         # 日线：过滤掉今天的
                         df = df[df['date'] < today_date]
                
                # 增量过滤
                if start_date:
                    if is_minute:
                        if isinstance(start_date, date) and not isinstance(start_date, datetime):
                             start_date = datetime.combine(start_date, datetime.min.time())
                        df = df[df['datetime'] > start_date]
                    else:
                        if isinstance(start_date, datetime):
                             start_date = start_date.date()
                        df = df[df['date'] > start_date]
                
                if df.empty:
                    continue

                # 补全列
                if is_minute:
                    # Calculate pct_chg and amplitude if missing
                    # Note: We need to sort by datetime to calculate correctly
                    if 'pct_chg' not in df.columns or 'amplitude' not in df.columns:
                        df = df.sort_values('datetime')
                        df['pre_close'] = df['close'].shift(1)
                        # For the first row, we can't calculate change accurately without previous data.
                        # We'll use open as a fallback or 0.
                        df['pre_close'] = df['pre_close'].fillna(df['open'])
                        
                        if 'pct_chg' not in df.columns:
                            df['pct_chg'] = (df['close'] - df['pre_close']) / df['pre_close'] * 100
                            df['pct_chg'] = df['pct_chg'].fillna(0.0)
                            
                        if 'amplitude' not in df.columns:
                            df['amplitude'] = (df['high'] - df['low']) / df['pre_close'] * 100
                            df['amplitude'] = df['amplitude'].fillna(0.0)
                            
                        if 'pre_close' in df.columns:
                            del df['pre_close']

                    required_cols = ['datetime', 'code', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'amplitude']
                    for col in required_cols:
                        if col not in df.columns: df[col] = 0.0
                    df_to_save = df[required_cols]
                else:
                    required_cols = ['date', 'code', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'amplitude', 'change_amt', 'turnover']
                    for col in required_cols:
                        if col not in df.columns: df[col] = 0.0
                    df_to_save = df[required_cols]

                # 保存到缓存文件
                filename = f"{table_name}_{stock_code}_{int(time.time()*1000)}.parquet"
                filepath = os.path.join(CACHE_DIR, filename)
                df_to_save.to_parquet(filepath, index=False)
                
                # 发送到队列
                file_queue.put((table_name, filepath))
                
            except Exception as e:
                logger.warning(f"处理 {stock_code} {table_name} 失败: {e}")

    except Exception as e:
        logger.error(f"Downloader failed for {stock_code}: {e}")

def _run_download_pipeline(
    code_table: str,
    tasks_to_run: list[dict],
    should_sync_list: bool,
    force_full: bool = False,
    kline_source: str = "auto",
):
    logger.info("=== 开始数据增量补全任务 (生产者-消费者模式) ===")

    _clear_cache_dir()

    if should_sync_list:
        if code_table == "stock_info":
            sync_stock_list()
        elif code_table == "index_info":
            sync_index_list()

    ensure_tables_exist()

    client = get_db_client()
    try:
        codes = [row[0] for row in client.execute(f"SELECT DISTINCT code FROM {code_table} ORDER BY code")]
        logger.info(f"共发现 {len(codes)} 条代码")
    finally:
        client.disconnect()

    max_dates_map = {}
    for task in tasks_to_run:
        table_name = task["table"]
        is_minute = task.get("is_minute", False)
        if force_full:
            max_dates_map[table_name] = {}
        else:
            max_dates_map[table_name] = get_max_dates_safe(table_name, is_minute, expected_codes=len(codes))

    manager = mp.Manager()
    file_queue = manager.Queue()
    stop_event = mp.Event()

    writers = []
    NUM_WRITERS = 4
    for _ in range(NUM_WRITERS):
        # daemon=True：主进程异常退出时 writer 随之终止，避免孤儿进程永久阻塞在 queue.get
        p = mp.Process(target=db_writer_worker, args=(file_queue, stop_event), daemon=True)
        p.start()
        writers.append(p)

    total_codes = len(codes)
    processed_count = 0
    start_time = time.time()

    DOWNLOAD_WORKERS = 12

    try:
        with mp.Pool(processes=DOWNLOAD_WORKERS, initializer=init_worker, initargs=(file_queue, max_dates_map, kline_source)) as pool:
            worker_args = []
            for code in codes:
                if code.startswith(("sh", "sz", "bj")):
                    symbol = code[2:]
                else:
                    symbol = code
                worker_args.append(((code, symbol), tasks_to_run))

            logger.info("开始多进程下载...")
            for _ in pool.imap_unordered(download_stock_data, worker_args):
                processed_count += 1
                if processed_count % 10 == 0:
                    elapsed = time.time() - start_time
                    speed = processed_count / max(elapsed, 1e-6)
                    logger.info(f"下载进度: {processed_count}/{total_codes} (速度: {speed:.2f} 只/秒)")
        logger.info("所有下载任务完成，等待写入完成...")
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止下载进程...")
        sys.exit(1)
    finally:
        # 无论正常结束/中断/其它异常，都通知 writer 退出并 join，避免孤儿进程
        stop_event.set()
        for p in writers:
            p.join()

    logger.info("全部完成！")

def db_writer_worker(file_queue, stop_event):
    """
    DB写入进程：保持连接，持续写入
    """
    client = None
    try:
        client = get_db_client()
        logger.info("DB Writer connected")
        
        while True:
            try:
                task = file_queue.get(timeout=1)
            except queue.Empty:
                if stop_event.is_set():
                    break
                continue

            table_name, filepath = task
                
            if not os.path.exists(filepath):
                continue
                
            try:
                df = pd.read_parquet(filepath)
                if not df.empty:
                    data = df.to_dict('records')
                    cols = list(df.columns)
                    col_expr = ",".join(cols)
                    client.execute(f"INSERT INTO {table_name} ({col_expr}) VALUES", data)
                    try:
                        row = _build_table_max_dates_row(table_name, df)
                        if row:
                            client.execute(
                                "INSERT INTO table_max_dates (table, code, max_date, max_dt, update_time) VALUES",
                                [row],
                            )
                    except Exception:
                        pass
                    
                os.remove(filepath)
                
            except Exception as e:
                logger.error(f"Write failed for {filepath}: {e}")
                # 失败文件不删除，移入 failed/ 保留，避免瞬时 DB 故障造成数据丢失
                _keep_failed_file(filepath)
                
    except Exception as e:
        logger.error(f"DB Writer crashed: {e}")
    finally:
        if client:
            client.disconnect()

def ensure_tables_exist():
    client = get_db_client()
    try:
        client.execute(
            """
            CREATE TABLE IF NOT EXISTS table_max_dates (
                table String,
                code LowCardinality(String),
                max_date Date,
                max_dt Nullable(DateTime),
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (table, code)
            """
        )

        client.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_info (
                code LowCardinality(String),
                name String,
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (code)
            """
        )

        client.execute(
            """
            CREATE TABLE IF NOT EXISTS index_info (
                code LowCardinality(String),
                name String,
                update_time DateTime
            ) ENGINE = ReplacingMergeTree(update_time)
            ORDER BY (code)
            """
        )

        # 确保所有表存在
        for task in (TASKS + INDEX_TASKS):
            table_name = task['table']
            is_minute = task.get('is_minute', False)
            
            if is_minute:
                sql = f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    datetime DateTime CODEC(Delta, ZSTD(1)),
                    code LowCardinality(String),
                    open Float32 CODEC(ZSTD(1)),
                    close Float32 CODEC(ZSTD(1)),
                    high Float32 CODEC(ZSTD(1)),
                    low Float32 CODEC(ZSTD(1)),
                    volume Float64 CODEC(ZSTD(1)),
                    amount Float64 CODEC(ZSTD(1)),
                    pct_chg Float32 CODEC(ZSTD(1)),
                    amplitude Float32 CODEC(ZSTD(1))
                ) ENGINE = ReplacingMergeTree()
                PARTITION BY toYYYYMM(datetime)
                ORDER BY (code, datetime)
                """
            else:
                # 日线表结构简化处理，假设已存在或使用 db_utils
                # 这里简单给一个 SQL
                sql = f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    date Date CODEC(Delta, ZSTD(1)),
                    code LowCardinality(String),
                    open Float32, close Float32, high Float32, low Float32,
                    volume Float64, amount Float64,
                    pct_chg Float32, amplitude Float32,
                    change_amt Float32, turnover Float32
                ) ENGINE = ReplacingMergeTree()
                ORDER BY (code, date)
                """
            client.execute(sql)
    finally:
        client.disconnect()

def main(target_adjust: list[str] | None = None, kline_source: str = "auto"):
    logger.info("=== 开始全量数据增量补全任务 (生产者-消费者模式) ===")
    
    # 0. 清理缓存目录
    _clear_cache_dir()
        
    # 0.1 同步股票列表 (新增/退市处理)
    sync_stock_list()
        
    # 1. 确保表存在
    ensure_tables_exist()

    # 确定要执行的任务
    tasks_to_run = TASKS
    if target_adjust is not None:
        tasks_to_run = [t for t in TASKS if t['adjust'] in target_adjust]
        logger.info(f"任务过滤: 仅下载 adjust={target_adjust} 的数据")
    
    client = get_db_client()
    try:
        # 2. 获取股票列表
        logger.info("获取股票列表...")
        stock_codes = [row[0] for row in client.execute("SELECT DISTINCT code FROM stock_info ORDER BY code")]
        logger.info(f"共发现 {len(stock_codes)} 只股票")
    finally:
        client.disconnect()
    
    # 3. 获取断点日期 (忽略今日)
    max_dates_map = {}
    for task in tasks_to_run:
        table_name = task['table']
        is_minute = task.get('is_minute', False)
        max_dates_map[table_name] = get_max_dates_safe(table_name, is_minute, expected_codes=len(stock_codes))
        
    # 4. 启动 DB Writer 进程
    # 使用 Manager Queue 以便在 Pool 中共享 (虽然 Writer 不是 Pool，但 Downloader 是)
    manager = mp.Manager()
    file_queue = manager.Queue()
    stop_event = mp.Event()
    
    writers = []
    NUM_WRITERS = 4
    for _ in range(NUM_WRITERS):
        # daemon=True：主进程异常退出时 writer 随之终止，避免孤儿进程永久阻塞在 queue.get
        p = mp.Process(target=db_writer_worker, args=(file_queue, stop_event), daemon=True)
        p.start()
        writers.append(p)
        
    # 5. 启动下载进程池
    
    total_stocks = len(stock_codes)
    processed_count = 0
    start_time = time.time()
    
    DOWNLOAD_WORKERS = 12 
    
    try:
        with mp.Pool(processes=DOWNLOAD_WORKERS, initializer=init_worker, initargs=(file_queue, max_dates_map, kline_source)) as pool:
            # 准备任务参数
            worker_args = []
            for code in stock_codes:
                if code.startswith(('sh', 'sz', 'bj')):
                    symbol = code[2:]
                else:
                    symbol = code
                worker_args.append(((code, symbol), tasks_to_run))
                
            # 异步提交
            logger.info("开始多进程下载...")
            # 使用 imap_unordered 可以实时获取进度
            for _ in pool.imap_unordered(download_stock_data, worker_args):
                processed_count += 1
                if processed_count % 10 == 0:
                    elapsed = time.time() - start_time
                    speed = processed_count / elapsed
                    logger.info(f"下载进度: {processed_count}/{total_stocks} (速度: {speed:.2f} 只/秒)")
        # 所有下载完成
        logger.info("所有下载任务完成，等待写入完成...")
    except KeyboardInterrupt:
        logger.warning("用户中断，正在停止下载进程...")
        sys.exit(1)
    finally:
        # 无论正常结束/中断/其它异常，都通知 writer 退出并 join，避免孤儿进程
        stop_event.set() # 通知 writer 可以退出了 (只要队列空了)
        for p in writers:
            p.join()

    # 只有当任务包含 qfq 时才尝试补全 qfq
    should_backfill_qfq = False
    for t in tasks_to_run:
        if t['adjust'] == 'qfq' and t.get('is_minute', False):
            should_backfill_qfq = True
            break
    
    if should_backfill_qfq:
        try:
            backfill_minute_qfq_tables(stock_codes=stock_codes)
        except Exception as e:
            logger.error(f"分钟线 _qfq 补全失败: {e}")

    logger.info("全部完成！")

def main_index(force_full: bool = False, kline_source: str = "auto"):
    _run_download_pipeline(
        code_table="index_info",
        tasks_to_run=INDEX_TASKS,
        should_sync_list=True,
        force_full=force_full,
        kline_source=kline_source,
    )


def parse_cli_args():
    p = argparse.ArgumentParser()
    p.add_argument("--all-three", action="store_true")
    p.add_argument("--kline", action="store_true")
    p.add_argument("--kline-source", default="auto", choices=["auto", "tdx"])
    p.add_argument("--import-index-minute", type=int, default=0)
    p.add_argument("--index-minute-csvs", default="")
    p.add_argument("--index-minute-gen-periods", default="5,15,30,60")
    p.add_argument("--index-minute-overwrite", type=int, default=1)
    p.add_argument("--import-etf-minute", type=int, default=0)
    p.add_argument("--etf-minute-dir", default="ETF_1min_2005_2022")
    p.add_argument("--etf-info-csv", default="ETF基础信息列表.csv")
    p.add_argument("--etf-benchmark-csv", default="ETF基准指数列表.csv")
    p.add_argument("--etf-minute-overwrite", type=int, default=0)
    p.add_argument("--import-etf-tdx", type=int, default=0)
    p.add_argument("--etf-tdx-overwrite", type=int, default=0)
    p.add_argument("--etf-tdx-codes", default="")
    p.add_argument("--etf-tdx-periods", default="daily,weekly,monthly")
    p.add_argument("--build-etf-minute-agg", type=int, default=0)
    p.add_argument("--etf-minute-gen-periods", default="5,15,30,60")
    p.add_argument("--etf-agg-codes", default="")
    p.add_argument("--etf-agg-start-ym", type=int, default=0)
    p.add_argument("--etf-agg-end-ym", type=int, default=0)
    p.add_argument("--etf-agg-overwrite", type=int, default=1)
    p.add_argument("--etf-agg-flush-rows", type=int, default=200_000)
    p.add_argument("--index", action="store_true")
    p.add_argument("--index-full", type=int, default=0)
    p.add_argument("--dividend", action="store_true")
    p.add_argument("--hot-sector", type=int, default=0)
    p.add_argument("--snapshot-date", default=date.today().strftime("%Y-%m-%d"))
    p.add_argument("--fix-daily-volume-unit", type=int, default=0)
    p.add_argument("--fix-daily-codes", default="")
    p.add_argument("--fix-daily-begin", default="")
    p.add_argument("--fix-daily-end", default="")
    p.add_argument("--fix-daily-dry-run", type=int, default=1)
    p.add_argument("--fix-daily-mutations-sync", type=int, default=2)
    p.add_argument("--build-qfq", action="store_true")
    p.add_argument("--qfq-codes", default="")
    p.add_argument("--qfq-minute-mode", default="backfill", choices=["backfill", "rebuild"])
    p.add_argument("--qfq-daily-fast", type=int, default=1)
    p.add_argument("--qfq-daily-workers", type=int, default=8)
    p.add_argument("--qfq-minute-workers", type=int, default=4)
    p.add_argument("--qfq-resume", type=int, default=0)
    p.add_argument("--qfq-start-ym", type=int, default=0)
    p.add_argument("--qfq-end-ym", type=int, default=0)
    p.add_argument("--qfq-minute-rebuild-months", type=int, default=0)
    p.add_argument("--qfq-validate-days", type=int, default=60)
    p.add_argument("--qfq-threshold", type=float, default=0.2)
    p.add_argument("--validate-daily-qfq", action="store_true", help="启用日线前复权数据一致性校验 (stock_daily_qfq vs stock_daily * factor)")

    # HFQ arguments
    p.add_argument("--build-hfq", action="store_true")
    p.add_argument("--hfq-codes", default="")
    p.add_argument("--hfq-minute-mode", default="backfill", choices=["backfill", "rebuild"])
    p.add_argument("--hfq-daily-fast", type=int, default=1)
    p.add_argument("--hfq-daily-workers", type=int, default=8)
    p.add_argument("--hfq-minute-workers", type=int, default=4)
    p.add_argument("--hfq-resume", type=int, default=0)
    p.add_argument("--hfq-start-ym", type=int, default=0)
    p.add_argument("--hfq-end-ym", type=int, default=0)
    p.add_argument("--hfq-minute-rebuild-months", type=int, default=0)
    p.add_argument("--hfq-validate-days", type=int, default=60)
    p.add_argument("--hfq-threshold", type=float, default=0.2)
    p.add_argument("--validate-daily-hfq", action="store_true", help="启用日线后复权数据一致性校验 (stock_daily_hfq vs stock_daily * factor)")

    # Common validation settings
    p.add_argument("--daily-validate-thr", type=float, default=0.05, help="日线复权一致性校验的误差阈值百分比 (默认 0.05)")
    p.add_argument("--daily-validate-days", type=int, default=180, help="日线复权一致性校验的回溯天数 (默认 180)")

    return p.parse_args()


def _run_script(args: list[str]):
    if not args:
        logger.warning("_run_script 调用缺少脚本参数，跳过执行")
        return
    script = args[0]
    if not os.path.isabs(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    if not os.path.exists(script):
        logger.warning(f"脚本不存在，跳过执行: {args[0]} (resolved: {script})")
        return
    cmd = [sys.executable, script, *args[1:]]
    logger.info("运行命令：" + " ".join(cmd))
    subprocess.run(cmd, check=True)


def run_pipeline():
    args = parse_cli_args()
    kline_source = str(args.kline_source)
    run_import_index_minute = bool(int(getattr(args, "import_index_minute", 0) or 0))
    run_import_etf_minute = bool(int(getattr(args, "import_etf_minute", 0) or 0))
    run_import_etf_tdx = bool(int(getattr(args, "import_etf_tdx", 0) or 0))
    run_build_etf_minute_agg = bool(int(getattr(args, "build_etf_minute_agg", 0) or 0))
    run_kline = args.kline or args.all_three
    run_index = bool(args.index)
    run_dividend = args.dividend or args.all_three
    run_hot_sector = (args.hot_sector and args.hot_sector > 0) or args.all_three
    hot_sector_n = args.hot_sector if args.hot_sector and args.hot_sector > 0 else 20

    # Check for any action flags
    has_action = (run_kline or run_dividend or run_hot_sector or 
                  (args.fix_daily_volume_unit and int(args.fix_daily_volume_unit) > 0) or
                  run_import_index_minute or
                  run_import_etf_minute or
                  run_import_etf_tdx or
                  run_build_etf_minute_agg or
                  run_index or
                  args.build_qfq or args.validate_daily_qfq or 
                  args.build_hfq or args.validate_daily_hfq)

    # Default behavior: Update all if no flags provided
    if not has_action:
        main()
        _run_script(["update_dividend_info.py"])
        return

    if args.fix_daily_volume_unit and int(args.fix_daily_volume_unit) > 0:
        codes = [c.strip() for c in str(args.fix_daily_codes).split(",") if c.strip()]
        begin = str(args.fix_daily_begin).strip() or None
        end = str(args.fix_daily_end).strip() or None
        dry_run = bool(int(args.fix_daily_dry_run))
        n = fix_stock_daily_volume_units(
            stock_codes=codes or None,
            begin_date=begin,
            end_date=end,
            dry_run=dry_run,
            mutations_sync=int(args.fix_daily_mutations_sync),
        )
        logger.info(f"stock_daily 成交量单位修复任务完成 dry_run={dry_run} candidates={n}")

    if run_import_index_minute:
        csvs = [s.strip() for s in str(args.index_minute_csvs).split(",") if s.strip()]
        periods = [int(x) for x in str(args.index_minute_gen_periods).split(",") if str(x).strip().isdigit()]
        overwrite = bool(int(args.index_minute_overwrite))
        import_index_minute_csvs(csv_paths=csvs, overwrite=overwrite, gen_periods=periods)

    if run_import_etf_minute:
        overwrite = bool(int(getattr(args, "etf_minute_overwrite", 0) or 0))
        import_etf_data(
            etf_minute_dir=str(getattr(args, "etf_minute_dir", "") or "").strip(),
            etf_info_csv=str(getattr(args, "etf_info_csv", "") or "").strip(),
            etf_benchmark_csv=str(getattr(args, "etf_benchmark_csv", "") or "").strip(),
            overwrite=overwrite,
        )

    if run_import_etf_tdx:
        overwrite = bool(int(getattr(args, "etf_tdx_overwrite", 0) or 0))
        codes = [c.strip() for c in str(getattr(args, "etf_tdx_codes", "") or "").split(",") if c.strip()]
        periods = [s.strip().lower() for s in str(getattr(args, "etf_tdx_periods", "") or "").split(",") if s.strip()]
        name_to_klt = {"daily": "101", "weekly": "102", "monthly": "103", "d": "101", "w": "102", "m": "103"}
        klts = [name_to_klt.get(p) for p in periods]
        klts = [k for k in klts if k]
        import_etf_kline_tdx(etf_codes=codes or None, overwrite=overwrite, include_periods=klts or None)

    if run_build_etf_minute_agg:
        periods = [int(x) for x in str(getattr(args, "etf_minute_gen_periods", "") or "").split(",") if str(x).strip().isdigit()]
        codes = [c.strip() for c in str(getattr(args, "etf_agg_codes", "") or "").split(",") if c.strip()]
        start_ym = int(getattr(args, "etf_agg_start_ym", 0) or 0)
        end_ym = int(getattr(args, "etf_agg_end_ym", 0) or 0)
        overwrite = bool(int(getattr(args, "etf_agg_overwrite", 1) or 0))
        flush_rows = int(getattr(args, "etf_agg_flush_rows", 200_000) or 200_000)
        build_etf_minute_aggregates_from_1m(
            gen_periods=periods or None,
            overwrite=overwrite,
            etf_codes=codes or None,
            start_ym=start_ym if start_ym > 0 else None,
            end_ym=end_ym if end_ym > 0 else None,
            flush_rows=flush_rows,
        )

    # 1. Update Phase
    if run_kline:
        # Determine target_adjust based on build flags
        target_adjust = [''] # Always include raw
        
        # If NO build flags are present (legacy mode), download everything
        if not (args.build_qfq or args.build_hfq):
             target_adjust = None # None means all
        else:
             # If build flags are present, ONLY download Raw.
             pass
             
        main(target_adjust=target_adjust, kline_source=kline_source)
    if run_index:
        main_index(force_full=bool(int(args.index_full)), kline_source=kline_source)
    if run_dividend:
        _run_script(["update_dividend_info.py"])
    if run_hot_sector:
        _run_script(
            [
                "update_constituent_info.py",
                "--top-sector",
                str(hot_sector_n),
                "--snapshot-date",
                str(args.snapshot_date),
            ]
        )

    # 2. Build/Validate Phase
    if args.build_qfq:
        codes = [c.strip() for c in str(args.qfq_codes).split(",") if c.strip()]
        build_qfq_tables(
            stock_codes=codes or None,
            rebuild_daily=True,
            daily_fast=bool(int(args.qfq_daily_fast)),
            daily_workers=int(args.qfq_daily_workers),
            minute_mode=str(args.qfq_minute_mode),
            minute_workers=int(args.qfq_minute_workers),
            start_ym=int(args.qfq_start_ym) if int(args.qfq_start_ym) > 0 else None,
            end_ym=int(args.qfq_end_ym) if int(args.qfq_end_ym) > 0 else None,
            minute_rebuild_recent_months=int(args.qfq_minute_rebuild_months),
            validate_recent_days=int(args.qfq_validate_days),
            validate_threshold_pct=float(args.qfq_threshold),
            resume=bool(int(args.qfq_resume)),
        )

    if args.validate_daily_qfq:
        codes = [c.strip() for c in str(args.qfq_codes).split(",") if c.strip()]
        validate_daily_adjust_consistency(
            adjust="qfq",
            stock_codes=codes or None,
            end_date=date.today(),
            begin_date=date.today() - timedelta(days=int(args.daily_validate_days)),
            error_threshold_pct=float(args.daily_validate_thr),
        )

    if args.build_hfq:
        codes = [c.strip() for c in str(args.hfq_codes).split(",") if c.strip()]
        build_hfq_tables(
            stock_codes=codes or None,
            rebuild_daily=True,
            daily_fast=bool(int(args.hfq_daily_fast)),
            daily_workers=int(args.hfq_daily_workers),
            minute_mode=str(args.hfq_minute_mode),
            minute_workers=int(args.hfq_minute_workers),
            start_ym=int(args.hfq_start_ym) if int(args.hfq_start_ym) > 0 else None,
            end_ym=int(args.hfq_end_ym) if int(args.hfq_end_ym) > 0 else None,
            minute_rebuild_recent_months=int(args.hfq_minute_rebuild_months),
        )
        if int(args.hfq_validate_days) > 0:
            validate_1m_hfq_vs_1d_hfq(
                stock_codes=codes or None,
                end_date=date.today(),
                begin_date=date.today() - timedelta(days=int(args.hfq_validate_days)),
                error_threshold_pct=float(args.hfq_threshold)
            )

    if args.validate_daily_hfq:
        codes = [c.strip() for c in str(args.hfq_codes).split(",") if c.strip()]
        validate_daily_adjust_consistency(
            adjust="hfq",
            stock_codes=codes or None,
            end_date=date.today(),
            begin_date=date.today() - timedelta(days=int(args.daily_validate_days)),
            error_threshold_pct=float(args.daily_validate_thr),
        )


if __name__ == "__main__":
    mp.freeze_support()
    # 无参也走 run_pipeline：其"无动作参数"默认路径 = main() + 分红更新，语义唯一来源
    run_pipeline()
