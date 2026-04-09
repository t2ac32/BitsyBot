"""
Paper Trading Engine — simulates live grid trading with real prices but no real orders.

Loop every POLL_INTERVAL_SECONDS:
  1. Fetch live ticker from Bitso
  2. Evaluate if any grid levels were crossed since last tick
  3. Simulate fills, update virtual balances
  4. Persist to SQLite
"""
import time
import signal
import sys
from datetime import datetime

import config as cfg
from exchange.client import BitsoClient
from strategy.grid import GridStrategy, GridLevel, LevelStatus
from data.db import (
    insert_grid, insert_grid_level, update_level_status,
    insert_trade, record_balance, get_active_levels, get_trade_stats,
)
from notifier import get_notifier


class PaperEngine:
    """
    Runs a grid strategy in paper-trading mode.

    Parameters
    ----------
    client : BitsoClient
        Must be initialised (API keys optional for ticker-only mode)
    strategy : GridStrategy
    initial_balance_mxn : float
        Starting virtual MXN balance
    poll_interval : int
        Seconds between price checks
    """

    FEE_RATE = 0.001  # 0.1% taker fee simulation

    def __init__(
        self,
        client: BitsoClient,
        strategy: GridStrategy,
        initial_balance_mxn: float = 10_000.0,
        poll_interval: int = 10,
    ):
        self.client = client
        self.strategy = strategy
        self.balance_mxn = initial_balance_mxn
        self.balance_btc = 0.0
        self.poll_interval = poll_interval
        self.grid_id: int = None
        self.levels: list[GridLevel] = []
        self._running = False
        self._last_price: float = None
        self.notifier = get_notifier(cfg)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialise the grid and start the polling loop."""
        print(f"[Paper] Starting paper trading — {self.strategy.book}")
        print(f"[Paper] Initial balance: {self.balance_mxn:,.2f} MXN")

        # Register SIGINT handler for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Get current price and build grid
        ticker = self.client.get_ticker(self.strategy.book)
        current_price = ticker.last
        print(f"[Paper] Current price: {current_price:,.2f} MXN")

        # Persist grid to DB
        self.grid_id = insert_grid(
            self.strategy.book,
            self.strategy.lower_price,
            self.strategy.upper_price,
            self.strategy.grid_levels,
            self.strategy.investment,
            "paper",
        )

        # Build initial levels
        self.levels = self.strategy.build_initial_levels(current_price)
        for level in self.levels:
            insert_grid_level(self.grid_id, level.index, level.price, level.side)

        print(f"[Paper] Grid created (id={self.grid_id}): "
              f"{len(self.levels)} levels, step={self.strategy.step_size:,.2f} MXN")

        self._last_price = current_price
        self._running = True

        if self.notifier:
            self.notifier.start_command_listener(
                lambda: get_trade_stats("paper"), "paper"
            )

        self._loop()

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[Paper] Tick error: {exc}")
            time.sleep(self.poll_interval)

    def _tick(self) -> None:
        ticker = self.client.get_ticker(self.strategy.book)
        price = ticker.last

        # Simulate candle between last_price and current_price
        low = min(price, self._last_price)
        high = max(price, self._last_price)

        self.levels, fills = self.strategy.evaluate_fills(self.levels, low, high)

        for fill in fills:
            self._process_fill(fill, price)

        self._last_price = price

        # Record balance snapshot every tick
        portfolio_mxn = self.balance_mxn + self.balance_btc * price
        record_balance("paper", portfolio_mxn, self.balance_btc)

        if fills:
            self._print_status(price)

    def _process_fill(self, fill: dict, current_price: float) -> None:
        fee = fill["price"] * fill["amount"] * self.FEE_RATE

        if fill["side"] == "buy":
            cost = fill["price"] * fill["amount"] + fee
            if self.balance_mxn < cost:
                print(f"[Paper] Insufficient MXN for buy at {fill['price']:,.2f} — skipping")
                return
            self.balance_mxn -= cost
            self.balance_btc += fill["amount"]
            action = "BUY "
        else:
            if self.balance_btc < fill["amount"]:
                print(f"[Paper] Insufficient BTC for sell at {fill['price']:,.2f} — skipping")
                return
            proceeds = fill["price"] * fill["amount"] - fee
            self.balance_mxn += proceeds
            self.balance_btc -= fill["amount"]
            action = "SELL"

        pnl = fill["pnl"] - fee

        insert_trade(
            self.grid_id,
            fill["index"],
            fill["side"],
            fill["price"],
            fill["amount"],
            pnl,
            fee,
            "paper",
        )
        update_level_status(self.grid_id, fill["index"], "filled")

        if self.notifier:
            self.notifier.send_trade(
                fill["side"], self.strategy.book, fill["price"],
                fill["amount"], pnl, fee, "paper",
            )

        print(
            f"[Paper] {action} level #{fill['index']:02d} @ {fill['price']:>12,.2f} MXN "
            f"| {fill['amount']:.6f} BTC | P&L: {pnl:>+10,.2f} MXN | Fee: {fee:.2f}"
        )

    def _print_status(self, price: float) -> None:
        portfolio = self.balance_mxn + self.balance_btc * price
        open_levels = sum(1 for l in self.levels if l.status == LevelStatus.OPEN)
        print(
            f"[Paper] Price: {price:>12,.2f} | "
            f"MXN: {self.balance_mxn:>12,.2f} | "
            f"BTC: {self.balance_btc:.6f} | "
            f"Portfolio: {portfolio:>12,.2f} | "
            f"Open levels: {open_levels}"
        )

    def _handle_shutdown(self, sig, frame) -> None:
        print("\n[Paper] Shutting down gracefully...")
        self._running = False
        if self.notifier:
            self.notifier.stop_command_listener()
        sys.exit(0)

    # ── Status accessors ───────────────────────────────────────────────────────

    def get_portfolio_value(self, current_price: float) -> float:
        return self.balance_mxn + self.balance_btc * current_price

    def get_open_levels(self) -> list[GridLevel]:
        return [l for l in self.levels if l.status == LevelStatus.OPEN]
