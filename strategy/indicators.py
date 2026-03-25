"""
Technical indicator calculations — pure functions operating on lists of floats.
"""


def sma(prices: list[float], period: int) -> list[float]:
    """
    Simple Moving Average.

    Returns a list of length len(prices) - period + 1.
    First value corresponds to prices[period-1].
    """
    if len(prices) < period:
        return []
    result = []
    window_sum = sum(prices[:period])
    result.append(window_sum / period)
    for i in range(period, len(prices)):
        window_sum += prices[i] - prices[i - period]
        result.append(window_sum / period)
    return result


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> list[float]:
    """
    Average True Range.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR is the SMA of True Range over `period` bars.

    Returns a list of length len(closes) - period.
    (We lose 1 bar for TR calculation, then period-1 for the SMA.)
    """
    n = len(closes)
    if n < 2 or n < period + 1:
        return []

    # Calculate True Range series (starts at index 1)
    tr_values = []
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_values.append(max(hl, hc, lc))

    return sma(tr_values, period)


def rsi(prices: list[float], period: int = 14) -> list[float]:
    """
    Relative Strength Index.

    RSI = 100 - (100 / (1 + RS))
    where RS = average gain / average loss over `period` bars.

    Returns a list of length len(prices) - period.
    """
    if len(prices) < period + 1:
        return []

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    rsi_values = []
    # Use simple moving average for first RSI value
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))
        rsi_values.append(rsi_val)

        # Smoothed moving average for subsequent values
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return rsi_values


def ema(prices: list[float], period: int) -> list[float]:
    """
    Exponential Moving Average.

    EMA = price * k + EMA_prev * (1 - k)
    where k = 2 / (period + 1)

    Returns a list of same length as prices, with first value = SMA of first period.
    """
    if len(prices) < period:
        return []

    k = 2.0 / (period + 1)
    ema_values = []

    # Start with SMA for first value
    ema_val = sum(prices[:period]) / period
    ema_values.append(ema_val)

    for i in range(period, len(prices)):
        ema_val = prices[i] * k + ema_val * (1 - k)
        ema_values.append(ema_val)

    return ema_values


def macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
    """
    Moving Average Convergence Divergence.

    MACD Line = EMA(fast) - EMA(slow)
    Signal Line = EMA(MACD Line, signal period)
    Histogram = MACD Line - Signal Line

    Returns (macd_line, signal_line, histogram).
    All lists have length = len(prices) - slow - signal + 1.
    """
    if len(prices) < slow + signal:
        return [], [], []

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    if not ema_fast or not ema_slow:
        return [], [], []

    # Align the two EMAs (fast starts later than slow)
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]

    signal_line = ema(macd_line, signal)

    if not signal_line:
        return [], [], []

    # Align histogram with signal line
    offset2 = len(macd_line) - len(signal_line)
    histogram = [macd_line[i + offset2] - signal_line[i] for i in range(len(signal_line))]

    # Return aligned values
    macd_aligned = macd_line[offset2:]

    return macd_aligned, signal_line, histogram
