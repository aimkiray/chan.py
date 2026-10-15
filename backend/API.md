# MLCHAN Backend API

后端基于 FastAPI，默认不需要鉴权（本地研究用途）。除 `/api/download/{code}` 返回 CSV 外，其余接口均为 JSON。

> **覆盖说明**：本文档仅展开常用接口；其余已注册路由（实验矩阵、回测/策略预设、live runner 系列等）见文末[其他接口索引](#其他接口索引未展开)。权威清单以 `backend/main.py` 中的 `@app.*` 路由装饰器为准。

## 约定

- **Content-Type**: `application/json`
- **错误返回**: FastAPI `HTTPException`，形如 `{"detail":"..."}`（HTTP 4xx/5xx）

## 健康检查

### `GET /health`

响应：

```json
{ "status": "ok" }
```

## 分析与校验

### `POST /api/analyze`

对单只股票做“缠论结构 + AI 评分”分析，并可选择使用预训练模型/融合等能力。

请求体（`AnalyzeRequest`，字段与默认值见 [backend/main.py](./main.py)）：

- `code` (str, required): 股票代码（如 `002701`/`sh.600000`，会自动规范化）
- `trigger_step` (bool, default `true`): 逐步计算（模拟实盘）
- `bi_strict` (bool, default `true`): 严格笔
- `frequency` (str, default `"1d"`): 周期（`1m/5m/15m/30m/60m/1d/1w/1mo`）
- `data_src` (str, default `"clickhouse"`): 数据源（`clickhouse/baostock/akshare`）
- `model` (str, default `"xgboost"`): 模型（`xgboost/lightgbm/mlp/logistic_regression`）
- `do_predict` (bool, default `false`): 是否执行模型评分
- `autype` (str, default `"qfq"`): 复权（`qfq/hfq/none`）
- `force_refresh` (bool, default `false`): 忽略缓存/强制重新训练
- `data_length_mode` (str, default `"default"`): `"default"` / `"max"`
- `data_length_years` (float|null): 指定年数（优先于 mode）
- `start_date` / `end_date` (str|null): 指定日期范围（YYYY-MM-DD）
- `use_pretrained` (bool|null): 是否优先加载预训练
- `pretrained_model_key` (str|null): 指定预训练模型 key
- `blend_models` (bool|null): 是否融合（预训练 + 在线训练）
- `enable_rolling_lookback` (bool, default `true`): 长周期上下文特征
- `rank_transform_enabled` (bool|null): Rank 变换开关（覆盖后端默认）
- `only_features` (any|null): 仅使用指定特征（前端透传）
- `profit_threshold` (float|null, default `0.01`)、`auto_profit_quantile`、`profit_lookahead`
- `use_atr_label` / `atr_period` / `atr_mult`: ATR 动态标签
- `backtest_mode` / `walk_forward_*` / `trade_cost` / `topk_frac` / `quantile_bin_count`
- `calibrate_method` (str, default `"none"`)
- `use_scale_pos_weight` (bool, default `false`)
- `xgb_*` / `lgb_*`: 部分超参覆写

响应（简化）：

```json
{
  "status": "success",
  "data": { "...": "chart + structures" },
  "signal": { "...": "latest signal" },
  "accuracy": { "...": "metrics summary" },
  "stock_name": "xxx",
  "data_warnings": []
}
```

### `POST /api/validate`

对一组股票做批量校验（更偏离线验证/回测质量检查）。

请求体（`ValidateRequest`）：与 `AnalyzeRequest` 类似，但 `codes` 为数组，且不含预训练/融合字段。

响应（简化）：

```json
{
  "status": "success",
  "begin_time": "YYYY-MM-DD",
  "end_time": "YYYY-MM-DD",
  "frequency": "1d",
  "data_src": "clickhouse",
  "model": "xgboost",
  "autype": "qfq",
  "results": [
    { "code": "sz.300059", "status": "success", "accuracy": { "...": "metrics" } }
  ]
}
```

## 预训练（作业 + 模型管理）

### `POST /api/pretrain_jobs`

提交一个预训练作业（排队/后台执行），返回 `job_id`。

请求体（`PretrainRequest`）核心字段：

- `codes` (list[str], required): 股票列表
- `frequency` (str, default `"1d"`)
- `data_src` (str, default `"clickhouse"`)
- `model` (str, default `"xgboost"`)
- `data_length_mode` (str, default `"max"`) / `data_length_years`
- `force_refresh` (bool, default `false`)
- `autype` (str, default `"hfq"`)
- `pool_name` (str|null): 用于显示/保存 meta
- `profit_threshold` (float|null) / `auto_profit_quantile` / `profit_lookahead`
- `high_vol_atr_pct_min` (float|null): 波动率过滤（ATR%）
- `use_atr_label` / `atr_period` / `atr_mult`
- `rank_mode` (str, default `"sample"`): `"sample"` / `"cross_section"`
- `rolling_sample_mode` (str|null): `"bsp"` / `"every_bar"`（不传则后端默认）
- `rolling_stride` (int|null)
- `only_features` (list[str]|null)

响应：

```json
{ "job_id": "..." }
```

### `GET /api/pretrain_jobs/{job_id}`

查询作业详情（含 `status/stage/progress/params/result` 等）。

### `GET /api/pretrain_jobs`

分页列出作业：`page`/`page_size`/`limit`。

### `DELETE /api/pretrain_jobs/{job_id}`

删除作业记录。

### `POST /api/pretrain_jobs/batch_delete`

批量删除作业。

### `POST /api/pretrain`

同步执行一次预训练并返回结果（会阻塞，适合小规模/调试）。

### `GET /api/pretrained_models`

分页列出已训练完成的预训练模型。

### `GET /api/pretrained_models/{key}`

获取模型详情（含 `bundle_path/feature_count/sample_count/accuracy/trained_at/name/display_name` 等）。

### `POST /api/pretrained_models/{key}/name`

设置/清空模型名称（用于前端展示）。

请求体：

```json
{ "name": "My Model" }
```

### `DELETE /api/pretrained_models/{key}`

删除模型（文件 + DB 元信息）。

### `POST /api/pretrained_models/batch_delete`

批量删除模型。

## 股票池与下载

### `GET /api/stock_pools`

列出 ClickHouse `stock_constituent` 表中的股票池。

### `GET /api/stock_pools/{pool_id}/members?limit=5000`

返回股票池成员列表（规范化后的 `sh./sz.` code）。

### `GET /api/download/{code}?frequency=1d`

下载历史数据 CSV（`Content-Disposition` 附带文件名）。

## 历史记录

### `GET /api/history?code=...&page=1&page_size=10`

分页获取历史分析记录。

### `GET /api/history/{result_id}`

获取单条历史记录详情。

### `DELETE /api/history/{result_id}`

删除历史记录。

### `POST /api/history/batch_delete`

批量删除历史记录：

```json
{ "result_ids": [1, 2, 3] }
```

## 组合（Portfolio）

### `GET /api/portfolios`

列出组合。

### `POST /api/portfolios`

创建组合：

```json
{ "name": "My Portfolio", "description": "" }
```

### `GET /api/portfolios/{portfolio_id}`

组合详情（含 positions）。

### `DELETE /api/portfolios/{portfolio_id}`

删除组合。

### `POST /api/portfolios/{portfolio_id}/positions`

添加持仓：

```json
{ "code": "sz.300059", "volume": 100, "price": 12.34, "name": "" }
```

## 回测

### `POST /api/backtest/vector`

向量化回测（默认使用创业板50内置池；也可传 `pool_id` 触发不同池逻辑）。

请求体（`VectorBacktestRequest`）包含：`pool_id/start_date/end_date/model_key/frequency/data_src/autype/enable_rolling_lookback/portfolio_top_n/holding_period/min_signal_score/initial_capital/max_workers/...`

### `POST /api/backtest/wolfpack`

狼群战术回测（更偏实盘工程化，含指数滤网/止损等）。字段见 `WolfpackBacktestRequest`。

## 策略选股（异步）

### `POST /api/strategy/run`

提交一次策略运行（异步），返回 `run_id`。

请求体（`StrategyRequest`）核心字段：

- `strategy_name` (str, required)
- `scope` (str, default `"hs300"`) / `pool_id` (str|null)
- `model`/`autype`/`frequency`
- `min_accuracy` / `min_recent_accuracy` / `recent_accuracy_years`
- `min_signal_score` / `min_bsp_count` / `min_test_count`
- `high_vol_atr_pct_min` / `use_atr_label` / `atr_*`
- `profit_threshold` / `auto_profit_quantile` / `profit_lookahead`
- `data_length_years`
- `chan_config` (dict|null): 缠论结构参数覆写
- `require_signal` / `signal_lookback` / `signal_direction`
- `enable_rolling_lookback`
- `amp_whitelist_days` / `amp_whitelist_top_frac`
- `portfolio_top_n` / `holding_period`

### `GET /api/strategy/runs`

分页列出运行记录。

### `GET /api/strategy/runs/{run_id}`

运行详情（含 params、results、progress、status）。

### `POST /api/strategy/runs/{run_id}/cancel`

取消运行（若在队列中则移除；若已完成则返回已终止标识）。

### `DELETE /api/strategy/runs/{run_id}`

删除运行记录。

### `POST /api/strategy/runs/batch_delete`

批量删除运行记录：

```json
{ "run_ids": [101, 102] }
```

## 实盘工具（Live）

### `POST /api/live/scan_features`

全池特征扫描（用于快速筛选候选）。字段见 `LiveScanFeaturesRequest`。

并行度：`max_workers` 会受环境变量 `MLCHAN_LIVE_MAX_WORKERS` 上限约束。

### `POST /api/live/paper_trade_step`

模拟盘单步执行（Top-N 目标持仓 + 买卖清单）。字段见 `LivePaperTradeStepRequest`。

推荐的实盘/模拟盘跑法：

- 常驻进程：运行 [paper_trade_runner.py](./live/paper_trade_runner.py)，它会按 A 股 30m 决策点定时调用该接口，并输出一行风控/选股摘要到 stdout，可选推送 webhook（见 `.env.example` 的 `MLCHAN_NOTIFY_*`）。
- 防崩溃降级：当指数数据缺失/接口抖动时，可通过 `risk_data_fail_mode` 与 `risk_intraday_data_fail_mode` 选择降级策略（`pass|reduce|flat`，默认 `reduce`），避免异常导致实盘进程退出。

### `GET /api/live/positions`

获取当前模拟持仓。

### `POST /api/live/positions/reset`

清空模拟持仓（危险操作）。

## 其他接口索引（未展开）

以下路由已在 `backend/main.py` 注册但本文档未展开字段说明；括号内为该文件中的装饰器行号，便于查阅。

### 实验矩阵（Experiment Matrix）

| 方法 | 路径 | 说明 | 位置 |
|---|---|---|---|
| GET | `/api/experiments/matrix` | 列出实验矩阵 | main.py:1479 |
| POST | `/api/experiments/matrix` | 创建/启动实验矩阵 | main.py:1511 |
| GET | `/api/experiments/matrix/{matrix_id}` | 矩阵详情 | main.py:1498 |
| GET | `/api/experiments/matrix/jobs/{job_id}` | 矩阵任务状态 | main.py:1487 |
| POST | `/api/experiments/matrix/{matrix_id}/evaluate` | 评估矩阵 | main.py:1577 |
| POST | `/api/experiments/matrix/{matrix_id}/report` | 生成矩阵报告 | main.py:1637 |

### 回测/策略预设

| 方法 | 路径 | 说明 | 位置 |
|---|---|---|---|
| GET | `/api/backtest/presets` | 列出回测预设 | main.py:2611 |
| POST | `/api/backtest/presets` | 新建回测预设 | main.py:2619 |
| GET | `/api/backtest/presets/{preset_id}` | 预设详情 | main.py:2636 |
| GET | `/api/strategy_presets` | 列出策略预设 | main.py:2649 |
| GET | `/api/strategy_presets/{name}` | 预设详情 | main.py:2664 |
| POST | `/api/strategy_presets/evaluate` | 评估预设 | main.py:2674 |
| POST | `/api/strategy_presets/select` | 选择预设 | main.py:2686 |

### 其它杂项

| 方法 | 路径 | 说明 | 位置 |
|---|---|---|---|
| GET | `/api/indices` | 指数列表 | main.py:3602 |
| GET | `/api/health/clickhouse` | ClickHouse 连通性检查 | main.py:3451 |
| POST | `/api/strategy/stop` | 停止策略执行 | main.py:5080 |
| POST | `/api/strategy/runs/{run_id}/stop` | 停止指定策略运行 | main.py:7115 |

### 实盘 Live（扩展）

| 方法 | 路径 | 说明 | 位置 |
|---|---|---|---|
| GET | `/api/live/orders` | 模拟订单列表 | main.py:4137 |
| GET | `/api/live/trades` | 成交记录 | main.py:4147 |
| GET/POST | `/api/live/sims` | 模拟盘实例查询/创建 | main.py:4162,4170 |
| GET/POST | `/api/live/runs` | live runner 查询/创建 | main.py:4178,4242 |
| GET | `/api/live/runs/{run_id}` | runner 详情 | main.py:4605 |
| DELETE | `/api/live/runs/{run_id}` | 删除 runner | main.py:4897 |
| GET | `/api/live/runs/{run_id}/diag` | runner 诊断 | main.py:4617 |
| POST | `/api/live/runs/{run_id}/start` | 启动 runner | main.py:4703 |
| POST | `/api/live/runs/{run_id}/stop` | 停止 runner | main.py:4754 |
| POST | `/api/live/runs/{run_id}/step_once` | 手动单步 | main.py:4766 |
| GET | `/api/live/steps` | 决策步骤记录 | main.py:4252 |
| GET | `/api/live/steps/stocks` | 步骤选股明细 | main.py:4259 |
| GET | `/api/live/equity_curve` | 净值曲线 | main.py:4267 |
| GET | `/api/live/positions_report` | 持仓报告 | main.py:4909 |
| POST | `/api/live/steps/reset` | 重置步骤记录 | main.py:5069 |

### 前端静态服务（仅部署形态）

当 `static/` 目录存在时（`deploy.ps1` 部署产物），`GET /` 返回 `index.html`，`GET /{catchall}` 兜底返回静态文件或 SPA 回退（main.py:7189 起）。
