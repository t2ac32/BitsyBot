"""
BitsyBot Streamlit Dashboard.

Run with:
    streamlit run dashboard/app.py
"""
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

import config
from data.db import (
    get_balance_history, get_candles, get_trades, init_db,
)

st.set_page_config(
    page_title="BitsyBot Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Ensure DB exists
init_db()


# ── Helpers ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=config.DASHBOARD_REFRESH_SECONDS)
def load_balance_history(mode: str) -> pd.DataFrame:
    rows = get_balance_history(mode)
    if not rows:
        return pd.DataFrame(columns=["timestamp", "balance_mxn", "balance_btc"])
    df = pd.DataFrame(rows, columns=rows[0].keys())
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@st.cache_data(ttl=config.DASHBOARD_REFRESH_SECONDS)
def load_trades(mode: str) -> pd.DataFrame:
    rows = get_trades(mode=mode, limit=1000)
    if not rows:
        return pd.DataFrame(columns=["timestamp", "side", "price", "amount", "pnl", "fees"])
    df = pd.DataFrame(rows, columns=rows[0].keys())
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@st.cache_data(ttl=60)
def load_candles_df(book: str, bucket: str) -> pd.DataFrame:
    rows = get_candles(book, bucket)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rows[0].keys())
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def fmt_mxn(v: float) -> str:
    return f"${v:,.2f} MXN"


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("BitsyBot")
    st.caption("Grid Trading Bot")
    st.divider()

    selected_mode = st.selectbox("Mode", ["paper", "live", "backtest"], index=0)
    st.divider()

    st.subheader("Grid Configuration")
    st.metric("Book", config.BOOK.upper())
    st.metric("Lower Price", fmt_mxn(config.GRID_LOWER))
    st.metric("Upper Price", fmt_mxn(config.GRID_UPPER))
    st.metric("Grid Levels", config.GRID_LEVELS)
    st.metric("Investment", fmt_mxn(config.INVESTMENT_MXN))

    step = (config.GRID_UPPER - config.GRID_LOWER) / config.GRID_LEVELS
    st.metric("Step Size", fmt_mxn(step))
    st.divider()

    if st.button("Clear cache & refresh"):
        st.cache_data.clear()
        st.rerun()


# ── Load data ──────────────────────────────────────────────────────────────────

bal_df = load_balance_history(selected_mode)
trades_df = load_trades(selected_mode)

# ── Overview metrics ───────────────────────────────────────────────────────────

st.title(f"BitsyBot — {selected_mode.upper()} Mode")

initial = config.PAPER_INITIAL_BALANCE if selected_mode == "paper" else config.INVESTMENT_MXN
current_balance = bal_df["balance_mxn"].iloc[-1] if not bal_df.empty else initial
total_pnl = current_balance - initial
roi_pct = (total_pnl / initial * 100) if initial else 0

total_trades = len(trades_df)
total_buys = len(trades_df[trades_df["side"] == "buy"]) if not trades_df.empty else 0
total_sells = len(trades_df[trades_df["side"] == "sell"]) if not trades_df.empty else 0
win_trades = len(trades_df[trades_df["pnl"] > 0]) if not trades_df.empty else 0
win_rate = (win_trades / total_trades * 100) if total_trades else 0

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Portfolio", fmt_mxn(current_balance), fmt_pct(roi_pct))
col2.metric("Total P&L", fmt_mxn(total_pnl))
col3.metric("Total Trades", total_trades, f"{total_buys}B / {total_sells}S")
col4.metric("Win Rate", f"{win_rate:.1f}%")
col5.metric("Mode", selected_mode.capitalize())

st.divider()


# ── Equity curve ───────────────────────────────────────────────────────────────

st.subheader("Portfolio Value Over Time")

if bal_df.empty:
    st.info("No balance history yet. Start the bot to see the equity curve.")
else:
    tab1, tab2, tab3 = st.tabs(["Full History", "Monthly", "Weekly"])

    with tab1:
        st.line_chart(bal_df.set_index("timestamp")["balance_mxn"], use_container_width=True)

    with tab2:
        monthly = (
            bal_df.set_index("timestamp")["balance_mxn"]
            .resample("ME")
            .last()
            .dropna()
        )
        st.bar_chart(monthly, use_container_width=True)

    with tab3:
        weekly = (
            bal_df.set_index("timestamp")["balance_mxn"]
            .resample("W")
            .last()
            .dropna()
        )
        st.line_chart(weekly, use_container_width=True)


# ── Yearly earnings ────────────────────────────────────────────────────────────

if not bal_df.empty:
    st.subheader("Yearly Earnings Breakdown")
    bal_df["year"] = bal_df["timestamp"].dt.year
    bal_df["month"] = bal_df["timestamp"].dt.to_period("M")

    yearly = bal_df.groupby("year").agg(
        start_balance=("balance_mxn", "first"),
        end_balance=("balance_mxn", "last"),
    ).reset_index()
    yearly["pnl"] = yearly["end_balance"] - yearly["start_balance"]
    yearly["roi_pct"] = yearly["pnl"] / yearly["start_balance"] * 100
    yearly["pnl_fmt"] = yearly["pnl"].map(lambda x: f"${x:+,.2f}")
    yearly["roi_fmt"] = yearly["roi_pct"].map(lambda x: f"{x:+.2f}%")

    col_a, col_b = st.columns([2, 3])
    with col_a:
        st.dataframe(
            yearly[["year", "start_balance", "end_balance", "pnl_fmt", "roi_fmt"]]
            .rename(columns={
                "year": "Year",
                "start_balance": "Start (MXN)",
                "end_balance": "End (MXN)",
                "pnl_fmt": "P&L",
                "roi_fmt": "ROI",
            }),
            use_container_width=True,
            hide_index=True,
        )
    with col_b:
        st.bar_chart(yearly.set_index("year")["pnl"], use_container_width=True)


st.divider()


# ── Grid visualisation ─────────────────────────────────────────────────────────

st.subheader("Grid Price Levels")

price_lines = [
    round(config.GRID_LOWER + i * step, 2) for i in range(config.GRID_LEVELS + 1)
]
filled_prices = set(trades_df["price"].round(2).tolist()) if not trades_df.empty else set()

grid_data = pd.DataFrame({
    "price": price_lines,
    "side": ["buy" if p < (config.GRID_LOWER + config.GRID_UPPER) / 2 else "sell" for p in price_lines],
    "filled": [p in filled_prices for p in price_lines],
})
grid_data["status"] = grid_data["filled"].map(lambda x: "Filled" if x else "Open")

col_g1, col_g2 = st.columns([1, 2])
with col_g1:
    st.dataframe(
        grid_data[["price", "side", "status"]]
        .rename(columns={"price": "Price (MXN)", "side": "Side", "status": "Status"}),
        use_container_width=True,
        hide_index=True,
    )
with col_g2:
    st.bar_chart(
        grid_data.set_index("price")[["price"]].assign(level=1),
        use_container_width=True,
    )


st.divider()


# ── Trade log ──────────────────────────────────────────────────────────────────

st.subheader("Trade Log")

if trades_df.empty:
    st.info("No trades recorded yet.")
else:
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        side_filter = st.multiselect("Side", ["buy", "sell"], default=["buy", "sell"])
    with col_f2:
        if not trades_df.empty:
            date_range = st.date_input(
                "Date range",
                value=(trades_df["timestamp"].min().date(), trades_df["timestamp"].max().date()),
            )
        else:
            date_range = None

    filtered = trades_df[trades_df["side"].isin(side_filter)].copy()
    if date_range and len(date_range) == 2:
        filtered = filtered[
            (filtered["timestamp"].dt.date >= date_range[0])
            & (filtered["timestamp"].dt.date <= date_range[1])
        ]

    st.dataframe(
        filtered[["timestamp", "side", "price", "amount", "pnl", "fees"]]
        .sort_values("timestamp", ascending=False)
        .rename(columns={
            "timestamp": "Time",
            "side": "Side",
            "price": "Price (MXN)",
            "amount": "Amount (BTC)",
            "pnl": "P&L (MXN)",
            "fees": "Fees (MXN)",
        })
        .style.format({
            "Price (MXN)": "{:,.2f}",
            "Amount (BTC)": "{:.6f}",
            "P&L (MXN)": "{:+,.2f}",
            "Fees (MXN)": "{:.4f}",
        }),
        use_container_width=True,
        hide_index=True,
    )


# ── Historical candles ─────────────────────────────────────────────────────────

st.divider()
st.subheader(f"Historical Price — {config.BOOK.upper()} ({config.BACKTEST_TIME_BUCKET})")

candles_df = load_candles_df(config.BOOK, config.BACKTEST_TIME_BUCKET)
if candles_df.empty:
    st.info("No historical candles stored. Run: `python main.py --fetch`")
else:
    st.line_chart(candles_df.set_index("ts")["close"], use_container_width=True)
    st.caption(f"{len(candles_df)} candles | "
               f"{candles_df['ts'].min().date()} → {candles_df['ts'].max().date()}")

st.caption("BitsyBot — auto-refreshes every 30s")
