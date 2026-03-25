"""
Grid Trading Strategy.

Creates equally-spaced price levels between lower and upper bounds.
Each level alternates between a buy order (below price) and a sell order (above price).
On fill:
  - BUY filled  → place a SELL at one level above
  - SELL filled → place a BUY at one level below
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LevelStatus(str, Enum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass
class GridLevel:
    index: int
    price: float
    side: str              # 'buy' | 'sell'
    status: LevelStatus = LevelStatus.OPEN
    order_id: Optional[str] = None
    fill_price: Optional[float] = None  # actual execution price


@dataclass
class GridState:
    grid_id: int
    book: str
    lower_price: float
    upper_price: float
    levels_count: int
    investment: float
    mode: str
    levels: list[GridLevel] = field(default_factory=list)
    realized_pnl: float = 0.0
    total_trades: int = 0


class GridStrategy:
    """
    Manages the creation and evaluation of a price grid.

    Parameters
    ----------
    book : str
        Trading pair, e.g. "btc_mxn"
    lower_price : float
        Lowest grid line (in quote currency, e.g. MXN)
    upper_price : float
        Highest grid line
    grid_levels : int
        Number of grid intervals (creates grid_levels+1 price lines)
    investment : float
        Total capital to deploy (in MXN)
    mode : str
        "paper" | "live" | "backtest"
    """

    def __init__(
        self,
        book: str,
        lower_price: float,
        upper_price: float,
        grid_levels: int,
        investment: float,
        mode: str = "paper",
    ):
        if lower_price >= upper_price:
            raise ValueError("lower_price must be less than upper_price")
        if grid_levels < 2:
            raise ValueError("grid_levels must be >= 2")

        self.book = book
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.grid_levels = grid_levels
        self.investment = investment
        self.mode = mode

        self._price_lines: list[float] = self._compute_price_lines()
        # Amount of base currency per grid level
        self.amount_per_level: float = self._compute_amount_per_level()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _compute_price_lines(self) -> list[float]:
        """Divide [lower, upper] into grid_levels equal intervals."""
        step = (self.upper_price - self.lower_price) / self.grid_levels
        return [
            round(self.lower_price + i * step, 2)
            for i in range(self.grid_levels + 1)
        ]

    def _compute_amount_per_level(self) -> float:
        """
        Split investment equally across all buy levels.
        amount_per_level is in base currency (BTC), calculated at lower_price as reference.
        """
        mxn_per_level = self.investment / self.grid_levels
        # Use lower_price as worst-case buy price for sizing
        return round(mxn_per_level / self.lower_price, 8)

    @property
    def price_lines(self) -> list[float]:
        return self._price_lines

    @property
    def step_size(self) -> float:
        return self._price_lines[1] - self._price_lines[0]

    # ── Grid initialisation ────────────────────────────────────────────────────

    def build_initial_levels(self, current_price: float) -> list[GridLevel]:
        """
        Build the initial set of grid levels relative to the current price.
        - Lines below current_price → BUY orders
        - Lines above current_price → SELL orders
        """
        levels = []
        for i, price in enumerate(self._price_lines):
            side = "buy" if price < current_price else "sell"
            levels.append(GridLevel(index=i, price=price, side=side))
        return levels

    # ── Fill evaluation ────────────────────────────────────────────────────────

    def evaluate_fills(
        self,
        levels: list[GridLevel],
        candle_low: float,
        candle_high: float,
    ) -> tuple[list[GridLevel], list[dict]]:
        """
        Check if any open levels were crossed by a candle (low, high).
        Returns updated levels and a list of fill events.

        Fill event dict:
          { index, side, price, amount, pnl }
        """
        fills = []
        updated_levels = list(levels)  # shallow copy

        for level in updated_levels:
            if level.status != LevelStatus.OPEN:
                continue

            filled = False
            if level.side == "buy" and candle_low <= level.price:
                filled = True
            elif level.side == "sell" and candle_high >= level.price:
                filled = True

            if filled:
                level.status = LevelStatus.FILLED
                level.fill_price = level.price

                # Calculate realised P&L (only complete sell→buy round trips)
                pnl = 0.0
                if level.side == "sell":
                    # Find paired buy below this level
                    paired_buy = self._find_paired_buy(updated_levels, level.index)
                    if paired_buy and paired_buy.fill_price:
                        pnl = (level.price - paired_buy.fill_price) * self.amount_per_level

                fills.append({
                    "index": level.index,
                    "side": level.side,
                    "price": level.price,
                    "amount": self.amount_per_level,
                    "pnl": pnl,
                })

                # Open the opposite counter-order
                counter = self._create_counter_level(updated_levels, level)
                if counter:
                    updated_levels.append(counter)

        return updated_levels, fills

    def _find_paired_buy(
        self, levels: list[GridLevel], sell_index: int
    ) -> Optional[GridLevel]:
        """Find the most recent filled BUY at the level below the sell."""
        target_index = sell_index - 1
        candidates = [
            l for l in levels
            if l.index == target_index and l.side == "buy" and l.status == LevelStatus.FILLED
        ]
        return candidates[-1] if candidates else None

    def _create_counter_level(
        self, levels: list[GridLevel], filled: GridLevel
    ) -> Optional[GridLevel]:
        """
        After a fill, open the opposite order one level away.
        BUY fill at index i  → open SELL at price_lines[i+1]
        SELL fill at index i → open BUY  at price_lines[i-1]
        """
        if filled.side == "buy":
            next_index = filled.index + 1
            if next_index < len(self._price_lines):
                # Avoid duplicate open SELL at same price
                existing = [
                    l for l in levels
                    if l.index == next_index and l.side == "sell"
                    and l.status == LevelStatus.OPEN
                ]
                if not existing:
                    return GridLevel(
                        index=next_index,
                        price=self._price_lines[next_index],
                        side="sell",
                    )
        else:  # sell filled
            prev_index = filled.index - 1
            if prev_index >= 0:
                existing = [
                    l for l in levels
                    if l.index == prev_index and l.side == "buy"
                    and l.status == LevelStatus.OPEN
                ]
                if not existing:
                    return GridLevel(
                        index=prev_index,
                        price=self._price_lines[prev_index],
                        side="buy",
                    )
        return None

    # ── Utility ────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "book": self.book,
            "lower_price": self.lower_price,
            "upper_price": self.upper_price,
            "grid_levels": self.grid_levels,
            "step_size": self.step_size,
            "investment_mxn": self.investment,
            "amount_per_level_btc": self.amount_per_level,
            "price_lines": self._price_lines,
        }
