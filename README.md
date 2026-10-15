# MLCHAN

MLCHAN is a Chan theory research and trading-lab application. It combines the upstream `chan.py` structure calculator with a FastAPI backend, a Nuxt/Vue dashboard, ClickHouse market data, model-based buy/sell-point filtering, backtesting, strategy presets, and paper/live-trading helpers.

The original upstream `chan.py` README has been preserved as [README.chanpy.md](./README.chanpy.md). Use this README for the current repository.

## What This Project Does

- Calculates Chan structures: K-lines, fractals, `Bi`, segments, `ZS`, and buy/sell points.
- Builds ML features around filtered Chan buy/sell points, including morphology, state, momentum, and sample semantics.
- Trains and evaluates XGBoost, LightGBM, MLP, and Logistic Regression models.
- Runs vector, wolfpack, daily snapshot, and cross-date experiment-matrix backtests.
- Provides a web dashboard for chart analysis, pretraining, strategy screening, portfolio backtesting, paper trading, and experiment matrices.
- Supports local ClickHouse data for A-share K-line analysis, including minute and daily levels.

## Project Structure

```text
.
├── Chan.py, ChanConfig.py      # Core Chan entry points
├── Bi/, Seg/, KLine/, ZS/      # Chan structure calculation modules
├── BuySellPoint/, Math/        # BSP and indicator logic
├── Combiner/                   # K-line merge/combine helpers
├── DataAPI/                    # Data adapters, including ClickHouse
├── App/                        # Standalone scanner GUI (ashare_bsp_scanner_gui.py)
├── Debug/                      # Strategy demo scripts (upstream examples)
├── backend/                    # FastAPI service, strategy, backtest, live tools
│   ├── main.py                 # API entry point
│   ├── chan_service.py         # ML feature extraction, training, prediction
│   ├── tools/                  # Daily snapshots and experiment matrix scripts
│   ├── presets/                # Strategy preset definitions and manager
│   └── live/                   # Paper/live-trading helpers and smoke tests
├── frontend/                   # Nuxt 3 dashboard
├── quick_guide.md              # Upstream usage guide
└── backend/API.md              # API reference notes
```

## Requirements

- Python 3.11+
- Node.js and npm
- Local ClickHouse if using `data_src=clickhouse`
- A configured `.env` file based on `.env.example`

Install Python dependencies:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Install frontend dependencies:

```powershell
cd frontend
npm install
```

## Run Locally

Start backend:

```powershell
python backend/main.py --port 8001
```

Start frontend:

```powershell
cd frontend
npm run dev
```

Or start the local development stack with:

```powershell
.\dev.ps1
```

Build static frontend assets:

```powershell
cd frontend
npm run generate
```

Prepare deployment output:

```powershell
.\deploy.ps1
```

## Core Workflows

### Single-Stock Analysis

Use the dashboard analysis tabs or call `/api/analyze` to calculate Chan structures, extract features, train/load a model, and score the latest buy/sell point.

### Pretraining

Use the Pretrain panel or `/api/pretrain_jobs` to train reusable model bundles. Feature presets such as `purify_compact`, side filters such as `buy`, and T+1-aware labels are supported.

### Strategy Backtesting

Use the Portfolio Backtest panel or `/api/backtest/wolfpack` for T+1, transaction-cost, risk-filter, and strategy-preset-aware simulations.

### Cross-Date Experiment Matrix

Run multi-configuration Chan BSP experiments from CLI:

```powershell
python backend\tools\run_experiment_matrix.py --start-date 2026-06-20 --end-date 2026-07-07 --max-dates 10 --rolling-modes none,bsp --side-filters buy,sell,both --lookaheads 1,3 --market-mas 20,60 --include-no-market-filter --resume
```

Evaluate after target-day data is available:

```powershell
python backend\tools\run_experiment_matrix.py --evaluate-only --matrix-dir <matrix_dir>
```

The web dashboard includes an Experiment Matrix tab for listing matrices, launching jobs, evaluating pending runs, viewing alpha heatmaps, and reading generated reports.

## Data And Configuration

Copy `.env.example` to `.env` and configure local paths, database credentials, and optional service settings. ClickHouse-related code lives in [DataAPI/ClickHouseAPI.py](./DataAPI/ClickHouseAPI.py). Do not commit `.env`, local databases, model caches, deployment folders, or generated test artifacts.

## Testing And Validation

There is no single full test suite yet. Use focused checks:

```powershell
python -m py_compile backend\main.py backend\chan_service.py backend\tools\daily_direction_snapshot.py backend\tools\run_experiment_matrix.py
python backend\live\smoke_test_live_step.py
python backend\live\smoke_test_autorun.py
cd frontend
npm run build
```

For API changes, run the backend locally and verify affected endpoints against [backend/API.md](./backend/API.md). For UI changes, run `npm run dev` and inspect the relevant dashboard panel.

## Notes

This repository is a research and engineering tool. Model scores, buy/sell points, backtests, and experiment reports are not investment advice. Treat them as signals to validate, filter, and risk-control before any simulated or live use.
