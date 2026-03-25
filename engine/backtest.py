"""
Backtest Engine — runs the GridStrategy against historical OHLCV candles.

Usage:
    from engine.backtest import BacktestEngine
    engine = BacktestEngine(strategy, candles)
    results = engine.run()
    engine.print_report(results)
"""
import math
from dataclasses import dataclass, field
from datetime import datetime

from strategy.grid import GridStrategy, GridLevel, LevelStatus
from data.db import insert_trade, insert_grid, record_balance


@dataclass
class BacktestResult:
    # Configuration
    book: str
    start_date: datetime
    end_date: datetime
    initial_investment: float

    # Performance
    final_balance_mxn: float
    final_balance_btc: float
    total_pnl: float
    roi_pct: float
    buy_and_hold_roi_pct: float
    alpha_pct: float  # roi - buy_and_hold

    # Trade stats
    total_trades: int
    buy_trades: int
    sell_trades: int
    win_rate_pct: float
    avg_pnl_per_trade: float

    # Risk
    max_drawdown_pct: float
    sharpe_ratio: float

    # Equity curve (timestamp → portfolio value in MXN)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)


class BacktestEngine:
    """
    Simulates grid trading on historical candle data.

    The simulation rule per candle:
      - If candle.low crosses a BUY level  → fill the buy
      - If candle.high crosses a SELL level → fill the sell
      - Both can happen in the same candle (buy first, then sell)
    """

    FEE_RATE = 0.001  # 0.1% taker fee (Bitso standard)

    def __init__(self, strategy: GridStrategy, candles: list, grid_id: int = None):
        self.strategy = strategy
        self.candles = candles
        self.grid_id = grid_id

    def run(self, persist: bool = False) -> BacktestResult:
        if not self.candles:
            raise ValueError("No candles provided for backtest")

        strategy = self.strategy
        first_candle = self.candles[0]
        last_candle = self.candles[-1]
        first_price = float(first_candle["close"] if hasattr(first_candle, "__getitem__") else first_candle.close)
        last_price = float(last_candle["close"] if hasattr(last_candle, "__getitem__") else last_candle.close)

        # Initialise virtual balances
        balance_mxn = strategy.investment
        balance_btc = 0.0
        initial_mxn = balance_mxn

        # Persist to DB if requested
        if persist and self.grid_id is None:
            self.grid_id = insert_grid(
                strategy.book,
                strategy.lower_price,
                strategy.upper_price,
                strategy.grid_levels,
                strategy.investment,
                "backtest",
            )

        # Build initial grid levels
        levels = strategy.build_initial_levels(first_price)

        # Tracking
        equity_curve: list[tuple[datetime, float]] = []
        total_trades = 0
        buy_trades = 0
        sell_trades = 0
        winning_trades = 0
        trade_pnls: list[float] = []
        portfolio_values: list[float] = []

        for candle in self.candles:
            ts = candle["ts"] if hasattr(candle, "__getitem__") else candle.timestamp
            low = float(candle["low"] if hasattr(candle, "__getitem__") else candle.low)
            high = float(candle["high"] if hasattr(candle, "__getitem__") else candle.high)
            close = float(candle["close"] if hasattr(candle, "__getitem__") else candle.close)

            levels, fills = strategy.evaluate_fills(levels, low, high)

            for fill in fills:
                fee = fill["price"] * fill["amount"] * self.FEE_RATE
                if fill["side"] == "buy":
                    cost = fill["price"] * fill["amount"] + fee
                    if balance_mxn >= cost:
                        balance_mxn -= cost
                        balance_btc += fill["amount"]
                        buy_trades += 1
                elif fill["side"] == "sell":
                    proceeds = fill["price"] * fill["amount"] - fee
                    balance_mxn += proceeds
                    balance_btc -= fill["amount"]
                    sell_trades += 1

                pnl = fill["pnl"] - fee
                trade_pnls.append(pnl)
                if pnl > 0:
                    winning_trades += 1
                total_trades += 1

                if persist and self.grid_id:
                    insert_trade(
                        self.grid_id,
                        fill["index"],
                        fill["side"],
                        fill["price"],
                        fill["amount"],
                        pnl,
                        fee,
                        "backtest",
                    )

            # Portfolio value in MXN (mark-to-market)
            portfolio_mxn = balance_mxn + balance_btc * close
            equity_curve.append((ts, portfolio_mxn))
            portfolio_values.append(portfolio_mxn)

            if persist and self.grid_id and len(equity_curve) % 7 == 0:
                record_balance("backtest", portfolio_mxn, balance_btc)

        # ── Metrics ───────────────────────────────────────────────────────────

        final_portfolio = portfolio_values[-1] if portfolio_values else initial_mxn
        total_pnl = final_portfolio - initial_mxn
        roi_pct = (total_pnl / initial_mxn) * 100

        bah_roi_pct = ((last_price - first_price) / first_price) * 100

        win_rate = (winning_trades / total_trades * 100) if total_trades else 0
        avg_pnl = (sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0

        max_drawdown = self._max_drawdown(portfolio_values)
        sharpe = self._sharpe_ratio(portfolio_values)

        return BacktestResult(
            book=strategy.book,
            start_date=equity_curve[0][0] if equity_curve else datetime.utcnow(),
            end_date=equity_curve[-1][0] if equity_curve else datetime.utcnow(),
            initial_investment=initial_mxn,
            final_balance_mxn=balance_mxn,
            final_balance_btc=balance_btc,
            total_pnl=total_pnl,
            roi_pct=roi_pct,
            buy_and_hold_roi_pct=bah_roi_pct,
            alpha_pct=roi_pct - bah_roi_pct,
            total_trades=total_trades,
            buy_trades=buy_trades,
            sell_trades=sell_trades,
            win_rate_pct=win_rate,
            avg_pnl_per_trade=avg_pnl,
            max_drawdown_pct=max_drawdown,
            sharpe_ratio=sharpe,
            equity_curve=equity_curve,
        )

    # ── Statistical helpers ────────────────────────────────────────────────────

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        """Maximum peak-to-trough drawdown as a percentage."""
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
        """
        Simplified annual Sharpe ratio based on daily returns.
        risk_free: annual rate (default 4%)
        """
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
    def print_report(r: BacktestResult) -> None:
        sep = "─" * 50
        print(f"\n{sep}")
        print(f"  BitsyBot Backtest — {r.book.upper()}")
        print(f"  {r.start_date.date()} → {r.end_date.date()}")
        print(sep)
        print(f"  Initial investment : {r.initial_investment:>12,.2f} MXN")
        print(f"  Final portfolio    : {r.final_balance_mxn + r.final_balance_btc * 0:>12,.2f} MXN")
        print(f"  Total P&L          : {r.total_pnl:>+12,.2f} MXN")
        print(f"  ROI                : {r.roi_pct:>+11.2f}%")
        print(f"  Buy-and-hold ROI   : {r.buy_and_hold_roi_pct:>+11.2f}%")
        print(f"  Alpha              : {r.alpha_pct:>+11.2f}%")
        print(sep)
        print(f"  Total trades       : {r.total_trades:>12}")
        print(f"  Buy / Sell         : {r.buy_trades:>6} / {r.sell_trades:<6}")
        print(f"  Win rate           : {r.win_rate_pct:>11.1f}%")
        print(f"  Avg P&L / trade    : {r.avg_pnl_per_trade:>+12,.2f} MXN")
        print(sep)
        print(f"  Max drawdown       : {r.max_drawdown_pct:>11.2f}%")
        print(f"  Sharpe ratio       : {r.sharpe_ratio:>12.3f}")
        print(sep + "\n")
