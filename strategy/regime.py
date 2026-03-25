"""
Regime Detection Strategy — detects bull/bear/accumulation per coin and generates signals.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from strategy.indicators import sma, atr, rsi, macd


class MarketRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    ACCUMULATION = "accumulation"


@dataclass
class Signal:
    book: str
    side: str           # 'buy' | 'sell'
    amount: float       # amount in quote currency (MXN) to spend/receive
    reason: str


class RegimeDetector:
    """
    Determines the current market regime for a single book using:
    - SMA-20 and SMA-50 for trend direction
    - RSI-14 for momentum
    - MACD for trend confirmation
    - ATR-14 for volatility measurement

    Parameters
    ----------
    atr_threshold_pct : float
        ATR as a percentage of price above which the market is considered
        high-volatility (accumulation zone). Default 3.5 means 3.5%.
    sma_convergence_pct : float
        When SMA-20 and SMA-50 are within this % of each other,
        they're considered "flat" (accumulation). Default 1.0%.
    sma_short : int
        Short SMA period. Default 20.
    sma_long : int
        Long SMA period. Default 50.
    rsi_bull_threshold : float
        RSI above this is bullish. Default 55.
    rsi_bear_threshold : float
        RSI below this is bearish. Default 45.
    """

    def __init__(
        self,
        atr_threshold_pct: float = 3.5,
        sma_convergence_pct: float = 1.0,
        sma_short: int = 20,
        sma_long: int = 50,
        rsi_bull_threshold: float = 55.0,
        rsi_bear_threshold: float = 45.0,
    ):
        self.atr_threshold_pct = atr_threshold_pct
        self.sma_convergence_pct = sma_convergence_pct
        self.sma_short = sma_short
        self.sma_long = sma_long
        self.rsi_bull_threshold = rsi_bull_threshold
        self.rsi_bear_threshold = rsi_bear_threshold

    def detect(self, candles: list) -> Optional[MarketRegime]:
        """
        Analyze candles and return the current regime using multiple indicators.

        candles: list of objects/rows with .close, .high, .low attributes (or dict-like).
        Needs at least sma_long candles (default 50).
        Returns None if insufficient data.

        Priority: BULL > BEAR > ACCUMULATION (relaxed detection)
        """
        closes = [self._get(c, "close") for c in candles]
        highs = [self._get(c, "high") for c in candles]
        lows = [self._get(c, "low") for c in candles]

        if len(closes) < self.sma_long:
            return None

        sma_short_vals = sma(closes, self.sma_short)
        sma_long_vals = sma(closes, self.sma_long)
        rsi_values = rsi(closes, 14)
        macd_line, signal_line, histogram = macd(closes, 12, 26, 9)
        atr_values = atr(highs, lows, closes, 14)

        if not sma_short_vals or not sma_long_vals or not rsi_values:
            return None

        current_price = closes[-1]
        current_sma_short = sma_short_vals[-1]
        current_sma_long = sma_long_vals[-1]
        current_rsi = rsi_values[-1]
        current_atr = atr_values[-1] if atr_values else 0

        # SMA trend direction (is short SMA rising?)
        sma_trending_up = len(sma_short_vals) > 5 and sma_short_vals[-1] > sma_short_vals[-5]
        sma_trending_down = len(sma_short_vals) > 5 and sma_short_vals[-1] < sma_short_vals[-5]

        # MACD momentum
        macd_bullish = False
        macd_bearish = False
        if macd_line and histogram:
            macd_bullish = histogram[-1] > 0  # MACD above signal
            macd_bearish = histogram[-1] < 0  # MACD below signal

        # ATR as percentage of current price
        atr_pct = (current_atr / current_price) * 100 if current_price > 0 else 0

        # ── BULL Detection (relaxed criteria) ──
        bull_signals = 0

        if current_price > current_sma_short:
            bull_signals += 1
        if current_sma_short > current_sma_long:
            bull_signals += 1
        if current_rsi > self.rsi_bull_threshold:
            bull_signals += 1
        if sma_trending_up:
            bull_signals += 1
        if macd_bullish:
            bull_signals += 1

        # BULL if 2+ signals (was 5/5 before, now 2/5)
        if bull_signals >= 2:
            return MarketRegime.BULL

        # ── BEAR Detection ──
        bear_signals = 0

        if current_price < current_sma_short:
            bear_signals += 1
        if current_sma_short < current_sma_long:
            bear_signals += 1
        if current_rsi < self.rsi_bear_threshold:
            bear_signals += 1
        if sma_trending_down:
            bear_signals += 1
        if macd_bearish:
            bear_signals += 1

        # BEAR if 3+ signals (stricter than bull)
        if bear_signals >= 3:
            return MarketRegime.BEAR

        # ── ACCUMULATION (fallback) ──
        # High volatility OR no clear trend
        if atr_pct > self.atr_threshold_pct:
            return MarketRegime.ACCUMULATION

        # Mixed signals
        return MarketRegime.ACCUMULATION

    @staticmethod
    def _get(candle, field: str) -> float:
        """Extract field from candle (supports both Row/dict and dataclass)."""
        if hasattr(candle, field):
            return float(getattr(candle, field))
        return float(candle[field])


class RegimeStrategy:
    """
    Generates trading signals based on detected regimes across multiple books.

    Parameters
    ----------
    books : list[str]
        Trading pairs to manage (e.g. ["btc_mxn", "eth_mxn"]).
    total_investment_mxn : float
        Shared investment pool across all coins.
    dca_interval_days : int
        Minimum days between accumulation buys per book.
    sell_pct : float
        Fraction of holdings to sell per bull signal (e.g. 0.2 = 20%).
    buy_pct_bear : float
        Fraction of available pool to buy per bear signal (e.g. 0.05 = 5%).
    buy_pct_accum : float
        Fraction of available pool to buy per accumulation signal (e.g. 0.05 = 5%).
    downtrend_protection_pct : float
        Skip accumulation buys if price is down more than this % from cost basis.
        Default 20.0 means skip if price is >20% below average cost.
    """

    def __init__(
        self,
        books: list[str],
        total_investment_mxn: float,
        dca_interval_days: int = 90,
        sell_pct: float = 0.20,        # More aggressive profit-taking
        buy_pct_bear: float = 0.05,    # Conservative buys in bear
        buy_pct_accum: float = 0.05,   # Conservative buys in accumulation
        downtrend_protection_pct: float = 20.0,
    ):
        self.books = books
        self.total_investment_mxn = total_investment_mxn
        self.dca_interval_days = dca_interval_days
        self.sell_pct = sell_pct
        self.buy_pct_bear = buy_pct_bear
        self.buy_pct_accum = buy_pct_accum
        self.downtrend_protection_pct = downtrend_protection_pct
        # Per-book allocation = pool / number of books
        self.allocation_per_book = total_investment_mxn / len(books) if books else 0

    def evaluate(
        self,
        book: str,
        regime: MarketRegime,
        current_price: float,
        holdings_base: float,
        available_mxn: float,
        last_buy_time: Optional[datetime] = None,
        now: Optional[datetime] = None,
        average_cost: Optional[float] = None,
    ) -> list[Signal]:
        """
        Generate signals for a single book given its regime.

        Parameters
        ----------
        book : str
        regime : MarketRegime
        current_price : float
        holdings_base : float
            Current holdings in base currency for this book.
        available_mxn : float
            MXN available to spend.
        last_buy_time : datetime or None
            When the last buy was executed for this book.
        now : datetime or None
            Current time (for testing).
        average_cost : float or None
            Average cost basis per unit of base currency. Used for downtrend protection.
        """
        if now is None:
            now = datetime.utcnow()

        signals: list[Signal] = []

        # ── STOP-LOSS: Sell if down >30% from cost basis (regardless of regime) ──
        if average_cost and average_cost > 0 and holdings_base > 0:
            drawdown_pct = ((average_cost - current_price) / average_cost) * 100
            if drawdown_pct > 30.0:  # Down more than 30%
                # Emergency sell ALL holdings
                signals.append(Signal(
                    book=book,
                    side="sell",
                    amount=holdings_base,
                    reason=f"STOP-LOSS triggered — down {drawdown_pct:.1f}% from cost basis",
                ))
                return signals  # Don't evaluate other signals

        if regime == MarketRegime.BULL:
            # Take profits — sell a fraction of holdings
            if holdings_base > 0:
                sell_amount_base = holdings_base * self.sell_pct
                if sell_amount_base * current_price > 1.0:  # minimum ~1 MXN
                    signals.append(Signal(
                        book=book,
                        side="sell",
                        amount=sell_amount_base,
                        reason=f"BULL regime — taking {self.sell_pct*100:.0f}% profits",
                    ))

        elif regime == MarketRegime.BEAR:
            # Accumulate conservatively — buy with a small fraction of available pool
            buy_mxn = min(available_mxn, self.allocation_per_book) * self.buy_pct_bear
            if buy_mxn > 1.0 and current_price > 0:
                buy_amount_base = buy_mxn / current_price
                signals.append(Signal(
                    book=book,
                    side="buy",
                    amount=buy_amount_base,
                    reason=f"BEAR regime — conservative DCA {buy_mxn:,.2f} MXN ({self.buy_pct_bear*100:.0f}%)",
                ))

        elif regime == MarketRegime.ACCUMULATION:
            # Small DCA buy only if enough time has passed AND not in deep drawdown
            days_since_buy = float("inf")
            if last_buy_time is not None:
                days_since_buy = (now - last_buy_time).total_seconds() / 86400

            # Downtrend protection: skip if price is down >X% from average cost
            if average_cost and average_cost > 0 and holdings_base > 0:
                drawdown_pct = ((average_cost - current_price) / average_cost) * 100
                if drawdown_pct > self.downtrend_protection_pct:
                    # Skip buy — price is too far below cost basis
                    return signals

            if days_since_buy >= self.dca_interval_days:
                buy_mxn = min(available_mxn, self.allocation_per_book) * self.buy_pct_accum
                if buy_mxn > 1.0 and current_price > 0:
                    buy_amount_base = buy_mxn / current_price
                    signals.append(Signal(
                        book=book,
                        side="buy",
                        amount=buy_amount_base,
                        reason=f"ACCUMULATION regime — periodic DCA {buy_mxn:,.2f} MXN ({days_since_buy:.0f}d since last)",
                    ))

        return signals
