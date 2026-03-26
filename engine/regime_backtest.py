"""
Regime Backtest Engine — simulates the regime strategy against historical candles.

Iterates day-by-day across all books simultaneously, detects regimes using
a sliding window of candles, and evaluates signals per book per day.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime

from tqdm import tqdm

from strategy.regime import MarketRegime, RegimeDetector, RegimeStrategy
from data.db import (
    get_candles, insert_trade, record_balance, insert_regime_change,
)


@dataclass
class RegimeBacktestResult:
    books: list[str]
    start_date: datetime
    end_date: datetime
    initial_investment: float

    final_balance_mxn: float
    final_holdings: dict[str, float]
    final_portfolio_mxn: float
    total_pnl: float
    roi_pct: float

    total_trades: int
    buy_trades: int
    sell_trades: int

    max_drawdown_pct: float
    sharpe_ratio: float

    regime_changes: int
    buy_and_hold_roi_pct: float
    alpha_pct: float  # roi - buy_and_hold
    per_book_summary: list[dict] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)


class RegimeBacktestEngine:
    """
    Simulates the regime strategy on historical daily candles for multiple books.

    For each trading day:
      1. Use the trailing 200-candle window per book to detect regime
      2. Evaluate signals based on regime, holdings, and available MXN
      3. Execute fills at the day's close price
      4. Record portfolio value
    """

    FEE_RATE = 0.001  # 0.1% taker fee

    def __init__(
        self,
        books: list[str],
        detector: RegimeDetector,
        strategy: RegimeStrategy,
        time_bucket: str = "1d",
        start_date: datetime = None,
        end_date: datetime = None,
        cooldown_days: int = 30,
        persistence_days: int = 5,
    ):
        self.books = books
        self.detector = detector
        self.strategy = strategy
        self.time_bucket = time_bucket
        self.start_date = start_date
        self.end_date = end_date
        self.cooldown_days = cooldown_days
        self.persistence_days = persistence_days

    def run(self, persist: bool = False) -> RegimeBacktestResult:
        # Load candles for all books
        book_candles: dict[str, list] = {}
        for book in self.books:
            rows = get_candles(book, self.time_bucket, self.start_date, self.end_date)
            if rows:
                book_candles[book] = rows

        if not book_candles:
            raise ValueError("No candles found for any book. Run: python main.py --fetch-all")

        active_books = list(book_candles.keys())
        if not active_books:
            raise ValueError("No books with candle data")

        # Build a unified date index from all books
        all_dates = set()
        book_date_map: dict[str, dict[datetime, object]] = {b: {} for b in active_books}
        for book, candles in book_candles.items():
            for c in candles:
                ts = c["ts"] if hasattr(c, "__getitem__") else c.timestamp
                # Ensure it's a datetime object
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                all_dates.add(ts)
                book_date_map[book][ts] = c

        sorted_dates = sorted(all_dates)

        # Virtual balances
        balance_mxn = self.strategy.total_investment_mxn
        initial_mxn = balance_mxn
        holdings: dict[str, float] = {b: 0.0 for b in active_books}
        last_buy_time: dict[str, datetime | None] = {b: None for b in active_books}
        current_regimes: dict[str, MarketRegime | None] = {b: None for b in active_books}

        # Cost basis tracking
        total_spent: dict[str, float] = {b: 0.0 for b in active_books}  # total MXN spent on buys
        average_cost: dict[str, float] = {b: 0.0 for b in active_books}  # average cost per unit

        # Cooldown tracking: prevent regime changes more than once per cooldown window
        last_regime_change_ts: dict[str, datetime | None] = {b: None for b in active_books}

        # Persistence tracking: require N consecutive days of same signal before committing
        pending_regime: dict[str, MarketRegime | None] = {b: None for b in active_books}
        pending_count: dict[str, int] = {b: 0 for b in active_books}

        # Tracking
        equity_curve: list[tuple[datetime, float]] = []
        portfolio_values: list[float] = []
        total_trades = 0
        buy_trades = 0
        sell_trades = 0
        regime_changes = 0
        per_book_trades: dict[str, list[dict]] = {b: [] for b in active_books}

        # We need at least 200 candles per book for regime detection.
        # Build a sliding window: for each date, collect all candles up to that date per book.
        book_candle_lists: dict[str, list] = {b: [] for b in active_books}
        # Track first candle price per book for buy-and-hold benchmark (before window trimming)
        book_first_prices: dict[str, float] = {}

        for day_ts in tqdm(sorted_dates, desc="Regime backtest", unit="day", ncols=80):
            # Accumulate candles per book
            day_prices: dict[str, float] = {}
            for book in active_books:
                candle = book_date_map[book].get(day_ts)
                if candle is not None:
                    close = float(candle["close"] if hasattr(candle, "__getitem__") else candle.close)
                    if book not in book_first_prices:
                        book_first_prices[book] = close
                    book_candle_lists[book].append(candle)
                    # Keep only trailing 250 candles for regime detection (need max 200 for SMA-200)
                    if len(book_candle_lists[book]) > 250:
                        book_candle_lists[book] = book_candle_lists[book][-250:]
                    day_prices[book] = close

            # Detect regimes and evaluate signals per book
            for book in active_books:
                if book not in day_prices:
                    continue

                candle_window = book_candle_lists[book]
                if len(candle_window) < 200:
                    continue

                old_regime = current_regimes[book]
                raw_regime = self.detector.detect(candle_window, old_regime)
                if raw_regime is None:
                    continue

                # Persistence: require N consecutive days of same signal before committing
                if raw_regime == pending_regime[book]:
                    pending_count[book] += 1
                else:
                    pending_regime[book] = raw_regime
                    pending_count[book] = 1

                if pending_count[book] >= self.persistence_days:
                    new_regime = raw_regime
                else:
                    new_regime = old_regime if old_regime is not None else raw_regime

                if new_regime != old_regime:
                    # Enforce cooldown: skip regime change if within cooldown window
                    if (
                        last_regime_change_ts[book] is not None
                        and (day_ts - last_regime_change_ts[book]).days < self.cooldown_days
                    ):
                        # Too soon — keep old regime, use it for signals
                        new_regime = old_regime
                    else:
                        current_regimes[book] = new_regime
                        regime_changes += 1
                        last_regime_change_ts[book] = day_ts
                        if persist:
                            insert_regime_change(book, new_regime.value)

                price = day_prices[book]
                signals = self.strategy.evaluate(
                    book=book,
                    regime=new_regime,
                    current_price=price,
                    holdings_base=holdings[book],
                    available_mxn=balance_mxn,
                    last_buy_time=last_buy_time[book],
                    now=day_ts,
                    average_cost=average_cost[book] if holdings[book] > 0 else None,
                )

                for sig in signals:
                    fee = price * sig.amount * self.FEE_RATE

                    if sig.side == "buy":
                        cost = price * sig.amount + fee
                        if balance_mxn < cost:
                            continue
                        balance_mxn -= cost
                        total_spent[book] += cost
                        holdings[book] += sig.amount
                        # Update average cost basis
                        if holdings[book] > 0:
                            average_cost[book] = total_spent[book] / holdings[book]
                        last_buy_time[book] = day_ts
                        buy_trades += 1
                    else:
                        if holdings[book] < sig.amount:
                            continue
                        proceeds = price * sig.amount - fee
                        balance_mxn += proceeds
                        # Reduce total spent proportionally
                        if holdings[book] > 0:
                            pct_sold = sig.amount / holdings[book]
                            total_spent[book] *= (1 - pct_sold)
                        holdings[book] -= sig.amount
                        # Recalculate average cost
                        if holdings[book] > 0:
                            average_cost[book] = total_spent[book] / holdings[book]
                        else:
                            average_cost[book] = 0.0
                        sell_trades += 1

                    total_trades += 1
                    per_book_trades[book].append({
                        "side": sig.side, "price": price,
                        "amount": sig.amount, "fee": fee,
                        "reason": sig.reason, "ts": day_ts,
                    })

                    if persist:
                        insert_trade(
                            grid_id=None, level_index=None,
                            side=sig.side, price=price,
                            amount=sig.amount, pnl=0.0,
                            fees=fee, mode="backtest",
                        )

            # Mark-to-market portfolio
            portfolio_mxn = balance_mxn
            for book in active_books:
                if book in day_prices and holdings[book] > 0:
                    portfolio_mxn += holdings[book] * day_prices[book]

            equity_curve.append((day_ts, portfolio_mxn))
            portfolio_values.append(portfolio_mxn)

            if persist and len(equity_curve) % 7 == 0:
                record_balance("backtest", portfolio_mxn, 0.0, book="regime_portfolio")

        # ── Final metrics ─────────────────────────────────────────────────
        final_portfolio = portfolio_values[-1] if portfolio_values else initial_mxn
        total_pnl = final_portfolio - initial_mxn
        roi_pct = (total_pnl / initial_mxn) * 100 if initial_mxn else 0

        # Buy-and-hold comparison: equal weight across all books on day 1
        buy_hold_value = initial_mxn
        if sorted_dates and len(sorted_dates) > 1:
            per_book_investment = initial_mxn / len(active_books)
            buy_hold_value = 0.0
            for book in active_books:
                book_list = book_candle_lists[book]
                first_price = book_first_prices.get(book, 0.0)
                if book_list and first_price > 0:
                    last_price = float(book_list[-1]["close"] if hasattr(book_list[-1], "__getitem__") else book_list[-1].close)
                    amount = per_book_investment / first_price
                    buy_hold_value += amount * last_price

        buy_hold_roi = ((buy_hold_value - initial_mxn) / initial_mxn) * 100 if initial_mxn else 0
        alpha = roi_pct - buy_hold_roi

        # Per-book summary
        per_book_summary = []
        for book in active_books:
            trades = per_book_trades[book]
            last_price = 0.0
            if book_candle_lists[book]:
                last_c = book_candle_lists[book][-1]
                last_price = float(last_c["close"] if hasattr(last_c, "__getitem__") else last_c.close)
            holding_value = holdings[book] * last_price
            per_book_summary.append({
                "book": book,
                "regime": current_regimes[book].value if current_regimes[book] else "n/a",
                "holdings": holdings[book],
                "holding_value_mxn": holding_value,
                "trades": len(trades),
                "buys": sum(1 for t in trades if t["side"] == "buy"),
                "sells": sum(1 for t in trades if t["side"] == "sell"),
            })

        return RegimeBacktestResult(
            books=active_books,
            start_date=equity_curve[0][0] if equity_curve else datetime.utcnow(),
            end_date=equity_curve[-1][0] if equity_curve else datetime.utcnow(),
            initial_investment=initial_mxn,
            final_balance_mxn=balance_mxn,
            final_holdings=dict(holdings),
            final_portfolio_mxn=final_portfolio,
            total_pnl=total_pnl,
            roi_pct=roi_pct,
            total_trades=total_trades,
            buy_trades=buy_trades,
            sell_trades=sell_trades,
            max_drawdown_pct=self._max_drawdown(portfolio_values),
            sharpe_ratio=self._sharpe_ratio(portfolio_values),
            regime_changes=regime_changes,
            buy_and_hold_roi_pct=buy_hold_roi,
            alpha_pct=alpha,
            per_book_summary=per_book_summary,
            equity_curve=equity_curve,
        )

    # ── Statistical helpers ────────────────────────────────────────────────────

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        if not values:
            return 0.0
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _sharpe_ratio(values: list[float], risk_free: float = 0.04) -> float:
        if len(values) < 2:
            return 0.0
        returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / n
        std_r = math.sqrt(variance) if variance > 0 else 0.0
        if std_r == 0:
            return 0.0
        daily_rf = risk_free / 252
        return round((mean_r - daily_rf) / std_r * math.sqrt(252), 3)

    # ── Reporting ──────────────────────────────────────────────────────────────

    @staticmethod
    def print_report(r: RegimeBacktestResult) -> None:
        sep = "-" * 60
        print(f"\n{sep}")
        print(f"  BitsyBot Regime Backtest")
        print(f"  {r.start_date.date()} -> {r.end_date.date()}")
        print(f"  Books: {', '.join(r.books)}")
        print(sep)
        print(f"  Initial investment : {r.initial_investment:>12,.2f} MXN")
        print(f"  Final portfolio    : {r.final_portfolio_mxn:>12,.2f} MXN")
        print(f"  Cash (MXN)         : {r.final_balance_mxn:>12,.2f} MXN")
        print(f"  Total P&L          : {r.total_pnl:>+12,.2f} MXN")
        print(f"  ROI                : {r.roi_pct:>+11.2f}%")
        print(f"  Buy-and-hold ROI   : {r.buy_and_hold_roi_pct:>+11.2f}%")
        print(f"  Alpha (vs B&H)     : {r.alpha_pct:>+11.2f}%")
        print(sep)
        print(f"  Total trades       : {r.total_trades:>12}")
        print(f"  Buy / Sell         : {r.buy_trades:>6} / {r.sell_trades:<6}")
        print(f"  Regime changes     : {r.regime_changes:>12}")
        print(sep)
        print(f"  Max drawdown       : {r.max_drawdown_pct:>11.2f}%")
        print(f"  Sharpe ratio       : {r.sharpe_ratio:>12.3f}")
        print(sep)

        # Per-book breakdown
        print(f"\n  {'Book':<15} {'Regime':<15} {'Holdings':>12} {'Value (MXN)':>14} {'Trades':>8}")
        print(f"  {'-'*64}")
        for s in r.per_book_summary:
            print(f"  {s['book']:<15} {s['regime']:<15} {s['holdings']:>12.8f} "
                  f"{s['holding_value_mxn']:>14,.2f} {s['trades']:>8}")
        print()
