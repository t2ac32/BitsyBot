"""
BitsyBot — entry point.

Usage:
    python main.py                    # uses MODE from config.py / .env
    python main.py --mode paper
    python main.py --mode backtest
    python main.py --mode live
    python main.py --fetch            # download/update historical candles
"""
import argparse
import sys

import config
from data.db import init_db
from exchange.client import BitsoClient
from strategy.grid import GridStrategy


def build_strategy() -> GridStrategy:
    return GridStrategy(
        book=config.BOOK,
        lower_price=config.GRID_LOWER,
        upper_price=config.GRID_UPPER,
        grid_levels=config.GRID_LEVELS,
        investment=config.INVESTMENT_MXN,
        mode=config.MODE,
    )


def build_client() -> BitsoClient:
    return BitsoClient(
        api_key=config.BITSO_API_KEY,
        api_secret=config.BITSO_API_SECRET,
    )


def cmd_fetch(client: BitsoClient) -> None:
    from data.fetcher import fetch_and_store
    print(f"Fetching {config.BACKTEST_DAYS} days of {config.BACKTEST_TIME_BUCKET} "
          f"candles for {config.BOOK}...")
    total = fetch_and_store(
        client,
        book=config.BOOK,
        time_bucket=config.BACKTEST_TIME_BUCKET,
        days_back=config.BACKTEST_DAYS,
    )
    print(f"Done. {total} new candles stored.")


def cmd_backtest(client: BitsoClient, strategy: GridStrategy) -> None:
    from data.db import get_candles
    from engine.backtest import BacktestEngine

    rows = get_candles(config.BOOK, config.BACKTEST_TIME_BUCKET)
    if not rows:
        print("No historical candles found. Run: python main.py --fetch")
        sys.exit(1)

    print(f"Running backtest on {len(rows)} candles ({config.BOOK})...")
    engine = BacktestEngine(strategy, rows)
    results = engine.run(persist=True)
    BacktestEngine.print_report(results)


def cmd_paper(client: BitsoClient, strategy: GridStrategy) -> None:
    from engine.paper import PaperEngine
    engine = PaperEngine(
        client,
        strategy,
        initial_balance_mxn=config.PAPER_INITIAL_BALANCE,
        poll_interval=config.POLL_INTERVAL_SECONDS,
    )
    engine.start()


def cmd_live(client: BitsoClient, strategy: GridStrategy) -> None:
    if not config.BITSO_API_KEY or not config.BITSO_API_SECRET:
        print("ERROR: BITSO_API_KEY and BITSO_API_SECRET must be set for live mode.")
        sys.exit(1)
    confirm = input("WARNING: Live mode uses real funds. Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)
    from engine.live import LiveEngine
    engine = LiveEngine(client, strategy)
    engine.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="BitsyBot — Grid Trading Bot")
    parser.add_argument(
        "--mode",
        choices=["paper", "live", "backtest"],
        default=None,
        help="Override mode from config",
    )
    parser.add_argument("--fetch", action="store_true", help="Download historical candles")
    args = parser.parse_args()

    # Initialise database
    init_db()

    mode = args.mode or config.MODE
    client = build_client()
    strategy = build_strategy()

    print(f"BitsyBot starting | mode={mode} | book={config.BOOK}")
    print(f"Grid: {config.GRID_LOWER:,.0f}–{config.GRID_UPPER:,.0f} MXN "
          f"| {config.GRID_LEVELS} levels | {config.INVESTMENT_MXN:,.0f} MXN investment")

    if args.fetch:
        cmd_fetch(client)
        return

    if mode == "backtest":
        cmd_backtest(client, strategy)
    elif mode == "paper":
        cmd_paper(client, strategy)
    elif mode == "live":
        cmd_live(client, strategy)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
