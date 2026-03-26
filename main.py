"""
BitsyBot — entry point.

Usage:
    python main.py                              # uses MODE + STRATEGY from config.py / .env
    python main.py --mode paper
    python main.py --mode backtest
    python main.py --mode live
    python main.py --fetch                      # download/update historical candles (grid book)
    python main.py --fetch-all                  # download candles for all regime books
    python main.py --strategy regime --mode paper   # regime strategy in paper mode
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


def cmd_fetch_all(client: BitsoClient) -> None:
    from data.fetcher import fetch_multiple_books
    books = config.REGIME_BOOKS
    print(f"Fetching {config.BACKTEST_DAYS} days of {config.BACKTEST_TIME_BUCKET} "
          f"candles for {len(books)} books: {', '.join(books)}")
    results = fetch_multiple_books(
        client,
        books=books,
        time_bucket=config.BACKTEST_TIME_BUCKET,
        days_back=config.BACKTEST_DAYS,
    )
    print(f"\nDone. New candles per book:")
    for book, count in results.items():
        print(f"  {book}: {count}")


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


def cmd_regime(client: BitsoClient, mode: str, start_date: str = None, end_date: str = None) -> None:
    from datetime import datetime
    from strategy.regime import RegimeDetector, RegimeStrategy

    detector = RegimeDetector(atr_threshold_pct=config.REGIME_ATR_THRESHOLD)
    strategy = RegimeStrategy(
        books=config.REGIME_BOOKS,
        total_investment_mxn=config.REGIME_INVESTMENT_MXN,
        dca_interval_days=config.REGIME_DCA_INTERVAL_DAYS,
        sell_pct=config.REGIME_SELL_PCT,
        buy_pct_bear=config.REGIME_BUY_PCT_BEAR,
        buy_pct_accum=config.REGIME_BUY_PCT_ACCUM,
        downtrend_protection_pct=config.REGIME_DOWNTREND_PROTECTION_PCT,
    )

    if mode == "backtest":
        from engine.regime_backtest import RegimeBacktestEngine

        # Parse date range if provided (normalize dates to YYYY-MM-DD format)
        def normalize_date(date_str: str) -> str:
            """Pad single-digit months/days: 2022-1-3 -> 2022-01-03"""
            parts = date_str.split('-')
            if len(parts) == 3:
                year, month, day = parts
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            return date_str

        start_dt = datetime.fromisoformat(normalize_date(start_date)) if start_date else None
        end_dt = datetime.fromisoformat(normalize_date(end_date)) if end_date else None

        date_info = ""
        if start_dt or end_dt:
            date_info = f" ({start_date or 'all'} to {end_date or 'all'})"

        print(f"Running regime backtest on {len(config.REGIME_BOOKS)} books{date_info}...")
        engine = RegimeBacktestEngine(
            books=config.REGIME_BOOKS,
            detector=detector,
            strategy=strategy,
            time_bucket=config.BACKTEST_TIME_BUCKET,
            start_date=start_dt,
            end_date=end_dt,
            cooldown_days=config.REGIME_COOLDOWN_DAYS,
        )
        results = engine.run(persist=True)
        RegimeBacktestEngine.print_report(results)
        return

    if mode == "live":
        if not config.BITSO_API_KEY or not config.BITSO_API_SECRET:
            print("ERROR: BITSO_API_KEY and BITSO_API_SECRET must be set for live mode.")
            sys.exit(1)
        confirm = input("WARNING: Live regime mode uses real funds. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    from engine.regime_engine import RegimeEngine
    engine = RegimeEngine(
        client=client,
        books=config.REGIME_BOOKS,
        detector=detector,
        strategy=strategy,
        mode=mode,
        initial_balance_mxn=config.REGIME_INVESTMENT_MXN,
        poll_interval=config.REGIME_POLL_INTERVAL,
        cooldown_days=config.REGIME_COOLDOWN_DAYS,
    )
    engine.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="BitsyBot — Grid & Regime Trading Bot")
    parser.add_argument(
        "--mode",
        choices=["paper", "live", "backtest"],
        default=None,
        help="Override mode from config",
    )
    parser.add_argument(
        "--strategy",
        choices=["grid", "regime"],
        default=None,
        help="Trading strategy to use (default: grid)",
    )
    parser.add_argument("--fetch", action="store_true", help="Download historical candles for grid book")
    parser.add_argument("--fetch-all", action="store_true", help="Download candles for all regime books")
    parser.add_argument("--start-date", type=str, help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, help="Backtest end date (YYYY-MM-DD)")
    args = parser.parse_args()

    # Initialise database
    init_db()

    mode = args.mode or config.MODE
    strategy_name = args.strategy or config.STRATEGY
    client = build_client()

    if args.fetch:
        cmd_fetch(client)
        return

    if args.fetch_all:
        cmd_fetch_all(client)
        return

    if strategy_name == "regime":
        print(f"BitsyBot starting | strategy=regime | mode={mode}")
        print(f"Books: {', '.join(config.REGIME_BOOKS)} | "
              f"{config.REGIME_INVESTMENT_MXN:,.0f} MXN investment")
        cmd_regime(client, mode, args.start_date, args.end_date)
    else:
        grid_strategy = build_strategy()
        print(f"BitsyBot starting | strategy=grid | mode={mode} | book={config.BOOK}")
        print(f"Grid: {config.GRID_LOWER:,.0f}–{config.GRID_UPPER:,.0f} MXN "
              f"| {config.GRID_LEVELS} levels | {config.INVESTMENT_MXN:,.0f} MXN investment")

        if mode == "backtest":
            cmd_backtest(client, grid_strategy)
        elif mode == "paper":
            cmd_paper(client, grid_strategy)
        elif mode == "live":
            cmd_live(client, grid_strategy)
        else:
            print(f"Unknown mode: {mode}")
            sys.exit(1)


if __name__ == "__main__":
    main()
