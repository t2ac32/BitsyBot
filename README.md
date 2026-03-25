# BitsyBot
A trading bot to work on bitso

## Testing Strategies Over Different Time Periods

BitsyBot supports backtesting strategies over various time periods to evaluate performance across different market conditions (bull runs, bear markets, consolidation).

### Option 1: Change Default Period (Simplest)

Edit `config.py` line 37:
```python
BACKTEST_DAYS: int = int(os.getenv("BITSYBOT_BACKTEST_DAYS", "1095"))  # 3 years
```

Then fetch and test:
```bash
python main.py --fetch-all  # Download 3 years of data
python main.py --strategy regime --mode backtest
```

### Option 2: Use Environment Variable (No Code Change)

**Linux/Mac:**
```bash
export BITSYBOT_BACKTEST_DAYS=1095
python main.py --fetch-all
python main.py --strategy regime --mode backtest
```

**Windows:**
```cmd
set BITSYBOT_BACKTEST_DAYS=1095
python main.py --fetch-all
python main.py --strategy regime --mode backtest
```

### Option 3: Test Specific Date Ranges

First, fetch a large dataset once:
```bash
# Fetch 5 years of data (edit config.py BACKTEST_DAYS to 1825)
python main.py --fetch-all
```

Then test different periods without re-downloading:
```bash
# Test 2020-2022 (bull run period)
python main.py --strategy regime --mode backtest --start-date 2020-01-01 --end-date 2022-12-31

# Test 2022-2024 (bear + recovery)
python main.py --strategy regime --mode backtest --start-date 2022-01-01 --end-date 2024-12-31

# Test just 2021 (peak bull)
python main.py --strategy regime --mode backtest --start-date 2021-01-01 --end-date 2021-12-31
```

### Understanding Backtest Results

**Key Metrics:**

- **Final Portfolio**: Total value of cash + holdings at market prices on the last day
- **Total P&L**: Profit/loss compared to initial investment (positive = profit, negative = loss)
- **ROI %**: Return on investment percentage
- **Max Drawdown**: Largest peak-to-trough decline (lower is better for risk)
- **Sharpe Ratio**: Risk-adjusted returns (>1.0 is good, <0 means losing money with volatility)
- **Trades**: Total buy/sell executions
- **Regime Changes**: How many times the strategy switched between BULL/BEAR/ACCUMULATION

**Interpreting Results:**
- Negative ROI in bear markets is expected, but the strategy should **limit losses** compared to buy-and-hold
- Positive ROI in bull markets shows the strategy can capture uptrends
- Compare results across different periods to see if the strategy works over full market cycles
- Lower max drawdown with similar returns = better risk management

### Recommended Testing Approach

1. **Fetch 5 years of historical data** (one-time download)
2. **Test full period** to see overall performance
3. **Test sub-periods** to understand performance in different market conditions:
   - Bull markets (e.g., 2020-2021)
   - Bear markets (e.g., 2022)
   - Sideways/choppy markets (e.g., recent periods)
4. **Tune parameters** if needed (see `config.py` REGIME_* settings)
5. **Compare strategies**: Run grid backtest vs regime backtest on same period
