"""
Live Trading Engine — places real orders on Bitso.

WARNING: This uses real funds. Only enable after validating with paper trading.
"""
import time
import signal
import sys

from exchange.client import BitsoClient
from strategy.grid import GridStrategy, GridLevel, LevelStatus
from data.db import (
    insert_grid, insert_grid_level, update_level_status,
    insert_trade, record_balance,
)


class LiveEngine:
    """
    Runs a grid strategy with real Bitso orders.

    Differences from PaperEngine:
    - Calls client.place_order() for each grid level
    - Polls open_orders() to detect fills (instead of price simulation)
    - Cancels all orders on shutdown
    """

    POLL_INTERVAL = 30  # seconds

    def __init__(self, client: BitsoClient, strategy: GridStrategy):
        self.client = client
        self.strategy = strategy
        self.grid_id: int = None
        self.levels: list[GridLevel] = []
        self._running = False

    def start(self) -> None:
        print(f"[Live] Starting LIVE trading — {self.strategy.book}")
        print("[Live] WARNING: Real funds will be used.")

        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Check balances
        balances = self.client.get_balances()
        mxn = balances.get("mxn")
        btc = balances.get("btc")
        print(f"[Live] Balance — MXN: {mxn.available:,.2f} | BTC: {btc.available:.6f}")

        if mxn.available < self.strategy.investment:
            raise RuntimeError(
                f"Insufficient MXN: need {self.strategy.investment:,.2f}, "
                f"have {mxn.available:,.2f}"
            )

        ticker = self.client.get_ticker(self.strategy.book)
        current_price = ticker.last

        # Persist grid
        self.grid_id = insert_grid(
            self.strategy.book,
            self.strategy.lower_price,
            self.strategy.upper_price,
            self.strategy.grid_levels,
            self.strategy.investment,
            "live",
        )

        # Build levels and place orders
        self.levels = self.strategy.build_initial_levels(current_price)
        self._place_all_orders()

        print(f"[Live] Grid active (id={self.grid_id}). Press Ctrl+C to stop.")
        self._running = True
        self._loop()

    def _place_all_orders(self) -> None:
        for level in self.levels:
            try:
                order = self.client.place_order(
                    book=self.strategy.book,
                    side=level.side,
                    order_type="limit",
                    amount=self.strategy.amount_per_level,
                    price=level.price,
                )
                level.order_id = order.order_id
                insert_grid_level(
                    self.grid_id, level.index, level.price, level.side, order.order_id
                )
                print(f"[Live] Placed {level.side.upper()} order @ {level.price:,.2f} | id={order.order_id}")
                time.sleep(0.1)  # rate limit
            except Exception as exc:
                print(f"[Live] Failed to place {level.side} @ {level.price}: {exc}")

    def _loop(self) -> None:
        while self._running:
            try:
                self._check_fills()
            except Exception as exc:
                print(f"[Live] Poll error: {exc}")
            time.sleep(self.POLL_INTERVAL)

    def _check_fills(self) -> None:
        open_order_ids = {
            o.order_id for o in self.client.get_open_orders(self.strategy.book)
        }
        balances = self.client.get_balances()
        portfolio_mxn = (
            balances["mxn"].available
            + balances.get("btc", type("", (), {"available": 0})()).available
            * self._last_price()
        )
        record_balance("live", portfolio_mxn, balances.get("btc", type("", (), {"available": 0})()).available)

        for level in self.levels:
            if level.status == LevelStatus.OPEN and level.order_id:
                if level.order_id not in open_order_ids:
                    # Order filled — place counter order
                    level.status = LevelStatus.FILLED
                    update_level_status(self.grid_id, level.index, "filled")
                    print(f"[Live] FILLED {level.side.upper()} @ {level.price:,.2f} (level #{level.index})")

                    insert_trade(
                        self.grid_id, level.index, level.side,
                        level.price, self.strategy.amount_per_level,
                        0, 0, "live",
                    )

                    counter = self.strategy._create_counter_level(self.levels, level)
                    if counter:
                        try:
                            order = self.client.place_order(
                                book=self.strategy.book,
                                side=counter.side,
                                order_type="limit",
                                amount=self.strategy.amount_per_level,
                                price=counter.price,
                            )
                            counter.order_id = order.order_id
                            self.levels.append(counter)
                            insert_grid_level(
                                self.grid_id, counter.index, counter.price,
                                counter.side, order.order_id
                            )
                            print(f"[Live] Counter {counter.side.upper()} @ {counter.price:,.2f}")
                        except Exception as exc:
                            print(f"[Live] Failed counter order: {exc}")

    def _last_price(self) -> float:
        try:
            return self.client.get_ticker(self.strategy.book).last
        except Exception:
            return 0.0

    def _handle_shutdown(self, sig, frame) -> None:
        print("\n[Live] Shutting down — cancelling all open orders...")
        self._running = False
        try:
            self.client.cancel_all_orders(self.strategy.book)
            print("[Live] All orders cancelled.")
        except Exception as exc:
            print(f"[Live] Error cancelling orders: {exc}")
        sys.exit(0)
