# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BitsyBot is a Python grid trading bot for the **Bitso** cryptocurrency exchange. It implements a grid strategy across three execution modes: **backtest** (historical simulation), **paper** (live prices, simulated orders), and **live** (real orders on Bitso).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Fetch historical candles (required before backtesting)
python main.py --fetch

# Run in different modes
python main.py --mode paper       # Paper trading (default)
python main.py --mode backtest    # Backtest on historical data
python main.py --mode live        # Live trading (requires API keys, prompts confirmation)

# Launch dashboard
streamlit run dashboard/app.py
```

## Architecture

Three execution engines share a common `GridStrategy`:

```
main.py (CLI + argparse)
  ├── engine/backtest.py  — iterates historical candles, calculates metrics (Sharpe, drawdown, alpha)
  ├── engine/paper.py     — polls live ticker, simulates fills with virtual balances
  └── engine/live.py      — places real limit orders, polls for fills, cancels on shutdown

All engines use:
  ├── strategy/grid.py       — grid level construction, fill detection, counter-order logic
  ├── exchange/client.py     — Bitso REST API with HMAC-SHA256 auth
  ├── exchange/models.py     — dataclasses: Candle, Order, Trade, Balance, Ticker
  ├── data/db.py             — SQLite (WAL mode) schema + helpers
  ├── data/fetcher.py        — paginated candle download with rate limiting
  └── dashboard/app.py       — Streamlit dashboard (equity curve, grid viz, trade log)
```

**Grid strategy flow:** Divide price range into N levels → place buys below current price, sells above → on fill, place counter-order one level away → track realized P&L on round trips.

## Configuration

All config lives in `config.py` with env var overrides from `.env`. Key variables:

- `BITSYBOT_MODE` — paper/live/backtest
- `BITSO_API_KEY` / `BITSO_API_SECRET` — exchange credentials
- `BITSYBOT_BOOK` — trading pair (default: btc_mxn)
- `BITSYBOT_GRID_LOWER` / `GRID_UPPER` / `GRID_LEVELS` — grid parameters
- `BITSYBOT_INVESTMENT_MXN` — capital to deploy

## Database

SQLite with tables: `grids`, `grid_levels`, `trades`, `balance_history`, `candles`. All engines persist trades and balances for dashboard visualization. Schema is auto-created in `data/db.py:init_db()`.

## Git Workflow

- **`main`** is production. Never commit directly to main.
- **`develop`** is the integration branch. All new work merges here first.
- For new features or fixes, create a feature branch off `develop`, do the work, then open a PR into `develop`.
- Only merge `develop` into `main` when the user explicitly approves a production release.
- When spawning new agents (Task tool), always ensure they work on the `develop` branch or a feature branch off `develop` — never on `main`.
- Do NOT add "Co-Authored-By" lines to commit messages.

## Key Design Decisions

- All three engines simulate/apply a **0.1% taker fee** for realistic P&L
- Paper and live engines handle **SIGINT** for graceful shutdown (live cancels open orders)
- Candle fetcher paginates in **90-day windows** with 0.2s sleep between requests (Bitso API limits)
- `notifier/` module exists as an empty placeholder for future alerting
- No test framework is currently set up
