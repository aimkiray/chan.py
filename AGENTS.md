# Repository Guidelines

## Project Structure & Module Organization

This repository combines a Chan theory calculation library, a FastAPI backend, and a Nuxt/Vue frontend. Core Python modules live at the root and in domain folders such as `Bi/`, `Seg/`, `KLine/`, `ZS/`, `BuySellPoint/`, `Math/`, `DataAPI/`, `Plot/`, `Common/`, and `ChanModel/`. API services are in `backend/`, with live-trading helpers and smoke tests in `backend/live/`. The frontend is in `frontend/`; Vue components are under `frontend/components/`, composables under `frontend/composables/`, locales under `frontend/locales/`, and CSS under `frontend/assets/css/`. Documentation and examples include `README.md`, `quick_guide.md`, `backend/API.md`, `main.py`, and `Debug/`.

## Build, Test, and Development Commands

- `python -m venv venv` then `venv\Scripts\activate`: create and activate Python.
- `pip install -r requirements.txt`: install backend and analysis dependencies. Python 3.11+ is expected.
- `python backend/main.py --port 8001`: run the FastAPI backend locally.
- `cd frontend; npm install`: install Nuxt frontend dependencies.
- `cd frontend; npm run dev`: start the frontend development server.
- `.\dev.ps1`: start both backend and frontend after clearing common local ports.
- `cd frontend; npm run generate`: build static frontend output used by `deploy.ps1`.
- `.\deploy.ps1`: generate frontend assets and prepare `mlchan_deploy/`.

## Coding Style & Naming Conventions

Python code uses 4-space indentation, explicit imports, and existing class names such as `CChan`, `CChanConfig`, and `StorageManager`. Keep domain abbreviations consistent with existing folders and enums, for example `KL_TYPE`, `DATA_SRC`, `BSP`, `ZS`, and `Bi`. Vue files use PascalCase component names, while composables use `useX` naming. Prefer focused changes that preserve the current script-oriented layout.

## Testing Guidelines

There is no formal pytest configuration yet. Use targeted smoke scripts for live/backend changes, especially `python backend/live/smoke_test_live_step.py` and `python backend/live/smoke_test_autorun.py`. For API changes, run `python backend/main.py --port 8001` and verify affected endpoints against `backend/API.md`. For frontend changes, run `npm run dev` and `npm run generate` from `frontend/`.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style prefixes, mainly `feat:` followed by a concise Chinese or English summary. Continue that pattern, for example `feat: add portfolio risk controls` or `fix: correct live order status`. Pull requests should describe the change, list backend/frontend areas touched, note manual test commands, link related issues, and include screenshots for UI changes.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local configuration. Never commit secrets, databases, model caches, deployment folders, virtual environments, or notification webhooks.
