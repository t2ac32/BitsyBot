"""
Multi-Coin Regime Trading Engine.

Polls multiple books, detects market regime per coin, and executes
signals based on bull/bear/accumulation classification.
"""
import signal
import sys
import time
from datetime import datetime

from exchange.client import BitsoClient
from strategy.regime import MarketRegime, RegimeDetector, RegimeStrategy, Signal
from data.db import (
    get_candles, insert_regime_change, get_last_regime,
    record_balance, insert_trade, get_trades,
)


class RegimeEngine:
    """
    Runs regime-based multi-coin trading in paper or live mode.

    Parameters
    ----------
    client : BitsoClient
    books : list[str]
        Trading pairs to monitor.
    detector : RegimeDetector
    strategy : RegimeStrategy
    mode : str
        "paper" or "live"
    initial_balance_mxn : float
        Starting MXN for paper mode.
    poll_interval : int
        Seconds between regime checks.
    """

    FEE_RATE = 0.001  # 0.1% taker fee

    def __init__(
        self,
        client: BitsoClient,
        books: list[str],
        detector: RegimeDetector,
        strategy: RegimeStrategy,
        mode: str = "paper",
        initial_balance_mxn: float = 10_000.0,
        poll_interval: int = 3600,
        cooldown_days: int = 30,
    ):
        self.client = client
        self.books = books
        self.detector = detector
        self.strategy = strategy
        self.mode = mode
        self.balance_mxn = initial_balance_mxn
        self.poll_interval = poll_interval
        self.cooldown_days = cooldown_days
        self._running = False

        # Per-book state
        self.holdings: dict[str, float] = {book: 0.0 for book in books}
        self.regimes: dict[str, MarketRegime | None] = {book: None for book in books}
        self.last_buy_time: dict[str, datetime | None] = {book: None for book in books}
        self.last_regime_change_ts: dict[str, datetime | None] = {book: None for book in books}
        self.total_spent: dict[str, float] = {book: 0.0 for book in books}
        self.average_cost: dict[str, float] = {book: 0.0 for book in books}

    def start(self) -> None:
        print(f"[Regime/{self.mode}] Starting regime engine")
        print(f"[Regime/{self.mode}] Books: {', '.join(self.books)}")
        print(f"[Regime/{self.mode}] Initial balance: {self.balance_mxn:,.2f} MXN")
        print(f"[Regime/{self.mode}] Poll interval: {self.poll_interval}s")

        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Detect initial regimes
        self._detect_all_regimes()
        self._print_regime_summary()

        self._running = True
        self._loop()

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[Regime/{self.mode}] Tick error: {exc}")
            time.sleep(self.poll_interval)

    def _tick(self) -> None:
        self._detect_all_regimes()

        for book in self.books:
            regime = self.regimes[book]
            if regime is None:
                continue

            try:
                ticker = self.client.get_ticker(book)
                current_price = ticker.last
            except Exception as exc:
                print(f"[Regime/{self.mode}] Ticker error for {book}: {exc}")
                continue

            signals = self.strategy.evaluate(
                book=book,
                regime=regime,
                current_price=current_price,
                holdings_base=self.holdings[book],
                available_mxn=self.balance_mxn,
                last_buy_time=self.last_buy_time[book],
                average_cost=self.average_cost[book] if self.holdings[book] > 0 else None,
            )

            for sig in signals:
                self._execute_signal(sig, current_price)

        # Record total portfolio value
        self._record_portfolio()

    def _detect_all_regimes(self) -> None:
        for book in self.books:
            candles = get_candles(book, "1d")
            if not candles:
                continue

            old_regime = self.regimes[book]
            committed_regime = self.detector.detect(candles, old_regime)
            if not committed_regime:
                continue

            if committed_regime != old_regime:
                now = datetime.utcnow()
                # Enforce cooldown: skip regime change if within cooldown window
                if (
                    old_regime is not None
                    and self.last_regime_change_ts[book] is not None
                    and (now - self.last_regime_change_ts[book]).days < self.cooldown_days
                ):
                    # Too soon since last change — keep old regime
                    pass
                else:
                    self.regimes[book] = committed_regime
                    self.last_regime_change_ts[book] = now
                    insert_regime_change(book, committed_regime.value)
                    if old_regime is not None:
                        print(f"[Regime/{self.mode}] {book}: {old_regime.value} → {committed_regime.value}")
                    else:
                        print(f"[Regime/{self.mode}] {book}: initial regime = {committed_regime.value}")

    def _execute_signal(self, sig: Signal, current_price: float) -> None:
        fee = current_price * sig.amount * self.FEE_RATE

        if sig.side == "buy":
            cost = current_price * sig.amount + fee
            if self.balance_mxn < cost:
                return  # insufficient funds, skip silently
            if self.mode == "live":
                try:
                    self.client.place_order(
                        book=sig.book, side="buy", order_type="market",
                        amount=sig.amount,
                    )
                except Exception as exc:
                    print(f"[Regime/live] Order error {sig.book}: {exc}")
                    return
            self.balance_mxn -= cost
            self.total_spent[sig.book] += cost
            self.holdings[sig.book] += sig.amount
            # Update average cost
            if self.holdings[sig.book] > 0:
                self.average_cost[sig.book] = self.total_spent[sig.book] / self.holdings[sig.book]
            self.last_buy_time[sig.book] = datetime.utcnow()
            action = "BUY "

        else:  # sell
            if self.holdings[sig.book] < sig.amount:
                return  # insufficient holdings
            if self.mode == "live":
                try:
                    self.client.place_order(
                        book=sig.book, side="sell", order_type="market",
                        amount=sig.amount,
                    )
                except Exception as exc:
                    print(f"[Regime/live] Order error {sig.book}: {exc}")
                    return
            proceeds = current_price * sig.amount - fee
            self.balance_mxn += proceeds
            # Reduce total spent proportionally
            if self.holdings[sig.book] > 0:
                pct_sold = sig.amount / self.holdings[sig.book]
                self.total_spent[sig.book] *= (1 - pct_sold)
            self.holdings[sig.book] -= sig.amount
            # Recalculate average cost
            if self.holdings[sig.book] > 0:
                self.average_cost[sig.book] = self.total_spent[sig.book] / self.holdings[sig.book]
            else:
                self.average_cost[sig.book] = 0.0
            action = "SELL"

        insert_trade(
            grid_id=None,
            level_index=None,
            side=sig.side,
            price=current_price,
            amount=sig.amount,
            pnl=0.0,
            fees=fee,
            mode=self.mode,
        )

        print(
            f"[Regime/{self.mode}] {action} {sig.book} | "
            f"{sig.amount:.8f} @ {current_price:,.2f} MXN | "
            f"Fee: {fee:.2f} | {sig.reason}"
        )

    def _record_portfolio(self) -> None:
        total = self.balance_mxn
        for book in self.books:
            if self.holdings[book] > 0:
                try:
                    ticker = self.client.get_ticker(book)
                    total += self.holdings[book] * ticker.last
                except Exception:
                    pass  # skip if ticker fails
        record_balance(self.mode, total, 0.0, book="regime_portfolio")

    def _print_regime_summary(self) -> None:
        print(f"\n{'─' * 60}")
        print(f"{'Book':<15} {'Regime':<15} {'Holdings':>15}")
        print(f"{'─' * 60}")
        for book in self.books:
            regime = self.regimes[book]
            regime_str = regime.value if regime else "insufficient data"
            holdings = self.holdings[book]
            print(f"{book:<15} {regime_str:<15} {holdings:>15.8f}")
        print(f"{'─' * 60}")
        print(f"Available MXN: {self.balance_mxn:>12,.2f}")
        print()

    def _handle_shutdown(self, sig, frame) -> None:
        print(f"\n[Regime/{self.mode}] Shutting down gracefully...")
        self._running = False
        self._print_regime_summary()
        sys.exit(0)
