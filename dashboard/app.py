"""
BitsyBot Streamlit Dashboard.

Run with:
    streamlit run dashboard/app.py
"""
import subprocess
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

import config
from data.db import (
    get_balance_history, get_candles, get_trades, get_regime_history, init_db,
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


@st.cache_data(ttl=config.DASHBOARD_REFRESH_SECONDS)
def load_regime_history() -> pd.DataFrame:
    rows = get_regime_history(limit=5000)
    if not rows:
        return pd.DataFrame(columns=["id", "book", "regime", "detected_at"])
    df = pd.DataFrame(rows, columns=rows[0].keys())
    df["detected_at"] = pd.to_datetime(df["detected_at"])
    return df


def fmt_mxn(v: float) -> str:
    return f"${v:,.2f} MXN"


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


REGIME_COLORS = {
    "bull": "green",
    "bear": "red",
    "accumulation": "orange",
}


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("BitsyBot")
    st.caption("Grid & Regime Trading Bot")
    st.divider()

    selected_strategy = st.selectbox("Strategy", ["grid", "regime"], index=0)
    selected_mode = st.selectbox("Mode", ["paper", "live", "backtest"], index=0)

    if selected_strategy == "grid":
        if selected_mode == "backtest":
            project_root = str(Path(__file__).parent.parent)
            if "backtest_running" not in st.session_state:
                st.session_state.backtest_running = False
            if "backtest_output" not in st.session_state:
                st.session_state.backtest_output = None

            if st.button("Run Backtest", disabled=st.session_state.backtest_running):
                st.session_state.backtest_running = True
                with st.status("Running backtest...", expanded=True) as status:
                    try:
                        result = subprocess.run(
                            [sys.executable, "main.py", "--mode", "backtest"],
                            cwd=project_root,
                            capture_output=True,
                            text=True,
                            timeout=300,
                        )
                        if result.returncode == 0:
                            st.session_state.backtest_output = result.stdout
                            status.update(label="Backtest complete!", state="complete")
                            st.write(result.stdout)
                        else:
                            st.session_state.backtest_output = result.stderr or result.stdout
                            status.update(label="Backtest failed", state="error")
                            st.error(result.stderr or result.stdout)
                    except subprocess.TimeoutExpired:
                        st.session_state.backtest_output = "Backtest timed out after 5 minutes."
                        status.update(label="Backtest timed out", state="error")
                        st.error("Backtest timed out after 5 minutes.")
                    finally:
                        st.session_state.backtest_running = False
                        st.cache_data.clear()

            if st.session_state.backtest_output:
                with st.expander("Last backtest output"):
                    st.code(st.session_state.backtest_output)

        st.divider()
        st.subheader("Grid Configuration")
        st.metric("Book", config.BOOK.upper())
        st.metric("Lower Price", fmt_mxn(config.GRID_LOWER))
        st.metric("Upper Price", fmt_mxn(config.GRID_UPPER))
        st.metric("Grid Levels", config.GRID_LEVELS)
        st.metric("Investment", fmt_mxn(config.INVESTMENT_MXN))
        step = (config.GRID_UPPER - config.GRID_LOWER) / config.GRID_LEVELS
        st.metric("Step Size", fmt_mxn(step))

    else:  # regime
        st.divider()
        st.subheader("Regime Configuration")
        st.metric("Books", len(config.REGIME_BOOKS))
        for book in config.REGIME_BOOKS:
            st.caption(f"  {book}")
        st.metric("Investment", fmt_mxn(config.REGIME_INVESTMENT_MXN))
        st.metric("Poll Interval", f"{config.REGIME_POLL_INTERVAL}s")
        st.metric("ATR Threshold", f"{config.REGIME_ATR_THRESHOLD}%")
        st.metric("DCA Interval", f"{config.REGIME_DCA_INTERVAL_DAYS} days")

    st.divider()
    if st.button("Clear cache & refresh"):
        st.cache_data.clear()
        st.rerun()


# ── Grid Strategy View ─────────────────────────────────────────────────────────

if selected_strategy == "grid":
    bal_df = load_balance_history(selected_mode)
    trades_df = load_trades(selected_mode)

    st.title(f"BitsyBot Grid — {selected_mode.upper()} Mode")

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

    # Equity curve
    st.subheader("Portfolio Value Over Time")
    if bal_df.empty:
        st.info("No balance history yet. Start the bot to see the equity curve.")
    else:
        tab1, tab2, tab3 = st.tabs(["Full History", "Monthly", "Weekly"])
        with tab1:
            st.line_chart(bal_df.set_index("timestamp")["balance_mxn"], use_container_width=True)
        with tab2:
            monthly = bal_df.set_index("timestamp")["balance_mxn"].resample("ME").last().dropna()
            st.bar_chart(monthly, use_container_width=True)
        with tab3:
            weekly = bal_df.set_index("timestamp")["balance_mxn"].resample("W").last().dropna()
            st.line_chart(weekly, use_container_width=True)

    # Yearly earnings
    if not bal_df.empty:
        st.subheader("Yearly Earnings Breakdown")
        bal_df["year"] = bal_df["timestamp"].dt.year
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
                    "year": "Year", "start_balance": "Start (MXN)",
                    "end_balance": "End (MXN)", "pnl_fmt": "P&L", "roi_fmt": "ROI",
                }),
                use_container_width=True, hide_index=True,
            )
        with col_b:
            st.bar_chart(yearly.set_index("year")["pnl"], use_container_width=True)

    st.divider()

    # Grid visualisation
    st.subheader("Grid Price Levels")
    step = (config.GRID_UPPER - config.GRID_LOWER) / config.GRID_LEVELS
    price_lines = [round(config.GRID_LOWER + i * step, 2) for i in range(config.GRID_LEVELS + 1)]
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
            use_container_width=True, hide_index=True,
        )
    with col_g2:
        st.bar_chart(
            grid_data.set_index("price")[["price"]].assign(level=1),
            use_container_width=True,
        )

    st.divider()

    # Trade log
    st.subheader("Trade Log")
    if trades_df.empty:
        st.info("No trades recorded yet.")
    else:
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            side_filter = st.multiselect("Side", ["buy", "sell"], default=["buy", "sell"])
        with col_f2:
            date_range = st.date_input(
                "Date range",
                value=(trades_df["timestamp"].min().date(), trades_df["timestamp"].max().date()),
            )

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
                "timestamp": "Time", "side": "Side", "price": "Price (MXN)",
                "amount": "Amount (BTC)", "pnl": "P&L (MXN)", "fees": "Fees (MXN)",
            })
            .style.format({
                "Price (MXN)": "{:,.2f}", "Amount (BTC)": "{:.6f}",
                "P&L (MXN)": "{:+,.2f}", "Fees (MXN)": "{:.4f}",
            }),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # Historical candles
    st.subheader(f"Historical Price — {config.BOOK.upper()} ({config.BACKTEST_TIME_BUCKET})")
    candles_df = load_candles_df(config.BOOK, config.BACKTEST_TIME_BUCKET)
    if candles_df.empty:
        st.info("No historical candles stored. Run: `python main.py --fetch`")
    else:
        st.line_chart(candles_df.set_index("ts")["close"], use_container_width=True)
        st.caption(f"{len(candles_df)} candles | "
                   f"{candles_df['ts'].min().date()} -> {candles_df['ts'].max().date()}")


# ── Regime Strategy View ───────────────────────────────────────────────────────

elif selected_strategy == "regime":
    st.title(f"BitsyBot Regime — {selected_mode.upper()} Mode")

    regime_df = load_regime_history()
    trades_df = load_trades(selected_mode)
    bal_df = load_balance_history(selected_mode)

    # ── Per-coin regime status ─────────────────────────────────────────────
    st.subheader("Current Regime Per Coin")

    if regime_df.empty:
        st.info("No regime data yet. Run the regime engine to detect market regimes.")
    else:
        cols = st.columns(len(config.REGIME_BOOKS))
        for i, book in enumerate(config.REGIME_BOOKS):
            book_regimes = regime_df[regime_df["book"] == book]
            if not book_regimes.empty:
                current = book_regimes.iloc[0]  # most recent (already sorted DESC)
                regime_val = current["regime"]
                color = REGIME_COLORS.get(regime_val, "gray")
                with cols[i]:
                    st.markdown(f"**{book.replace('_mxn', '').upper()}**")
                    st.markdown(
                        f'<span style="color:{color}; font-size:1.4em; font-weight:bold;">'
                        f'{regime_val.upper()}</span>',
                        unsafe_allow_html=True,
                    )
                    st.caption(f"Since {current['detected_at'].strftime('%Y-%m-%d %H:%M')}")
            else:
                with cols[i]:
                    st.markdown(f"**{book.replace('_mxn', '').upper()}**")
                    st.caption("No data")

    st.divider()

    # ── Regime history timeline ────────────────────────────────────────────
    st.subheader("Regime History Timeline")

    if regime_df.empty:
        st.info("No regime history to display.")
    else:
        selected_book = st.selectbox(
            "Filter by book",
            ["All"] + config.REGIME_BOOKS,
            key="regime_book_filter",
        )
        display_df = regime_df if selected_book == "All" else regime_df[regime_df["book"] == selected_book]

        st.dataframe(
            display_df[["detected_at", "book", "regime"]]
            .rename(columns={
                "detected_at": "Detected At", "book": "Book", "regime": "Regime",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    # ── Portfolio equity curve ─────────────────────────────────────────────
    st.subheader("Portfolio Value Over Time")
    if bal_df.empty:
        st.info("No balance history yet. Start the regime engine to track portfolio value.")
    else:
        st.line_chart(bal_df.set_index("timestamp")["balance_mxn"], use_container_width=True)

    st.divider()

    # ── Per-coin price charts ──────────────────────────────────────────────
    st.subheader("Price Charts Per Coin")
    chart_cols = st.columns(min(3, len(config.REGIME_BOOKS)))
    for i, book in enumerate(config.REGIME_BOOKS):
        col_idx = i % len(chart_cols)
        cdf = load_candles_df(book, config.BACKTEST_TIME_BUCKET)
        with chart_cols[col_idx]:
            st.markdown(f"**{book.replace('_mxn', '').upper()}**")
            if cdf.empty:
                st.caption("No candle data")
            else:
                st.line_chart(cdf.set_index("ts")["close"], use_container_width=True, height=200)

    st.divider()

    # ── Trade log ──────────────────────────────────────────────────────────
    st.subheader("Trade Log")
    if trades_df.empty:
        st.info("No trades recorded yet.")
    else:
        st.dataframe(
            trades_df[["timestamp", "side", "price", "amount", "pnl", "fees"]]
            .sort_values("timestamp", ascending=False)
            .rename(columns={
                "timestamp": "Time", "side": "Side", "price": "Price (MXN)",
                "amount": "Amount", "pnl": "P&L (MXN)", "fees": "Fees (MXN)",
            })
            .style.format({
                "Price (MXN)": "{:,.2f}", "Amount": "{:.8f}",
                "P&L (MXN)": "{:+,.2f}", "Fees (MXN)": "{:.4f}",
            }),
            use_container_width=True, hide_index=True,
        )


st.caption("BitsyBot — auto-refreshes every 30s")
