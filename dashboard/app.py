"""
ZerodhaBot Dashboard — Streamlit

Run: streamlit run dashboard/app.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import pytz

IST = pytz.timezone("Asia/Kolkata")

st.set_page_config(
    page_title="ZerodhaBot Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("📈 ZerodhaBot — Live Dashboard")


def load_account_state():
    try:
        with open("journaling/account_state.json") as f:
            return json.load(f)
    except Exception:
        return {"account_value": 20000, "daily_pnl": 0, "last_updated": "N/A"}


def load_todays_trades():
    today = datetime.now(IST).strftime("%Y-%m-%d")
    fp = Path(f"journaling/logs/trades_{today}.json")
    if fp.exists():
        with open(fp) as f:
            return json.load(f)
    return []


def load_daily_reports(days: int = 30):
    reports = []
    for fp in sorted(Path("reporting/output").glob("daily_*.json"), reverse=True)[:days]:
        try:
            with open(fp) as f:
                reports.append(json.load(f))
        except Exception:
            pass
    return reports


# ── Header metrics ────────────────────────────────────────────────
state = load_account_state()
account_value = state.get("account_value", 20000)
daily_pnl = state.get("daily_pnl", 0)
last_updated = state.get("last_updated", "N/A")

col1, col2, col3, col4 = st.columns(4)
col1.metric("💰 Account Value", f"₹{account_value:,.2f}",
            delta=f"₹{daily_pnl:+,.2f} today")
col2.metric("📊 Daily P&L", f"₹{daily_pnl:+,.2f}",
            delta=f"{(daily_pnl/account_value*100):+.2f}%" if account_value else "0%")

trades = load_todays_trades()
closed = [t for t in trades if "closed" in t.get("state", "")]
col3.metric("🔄 Trades Today", len(closed),
            delta=f"{sum(1 for t in closed if (t.get('net_pnl') or 0) > 0)} wins")
col4.metric("🕐 Last Updated", last_updated[-8:] if len(last_updated) > 8 else last_updated)

st.divider()

# ── Current Open Positions ─────────────────────────────────────────
st.subheader("📌 Open Positions")
open_trades = [t for t in trades if t.get("state") in (
    "entry_filled", "sl_placed", "target_1_hit", "breakeven_moved", "trailing_active"
)]
if open_trades:
    df_open = pd.DataFrame(open_trades)[["symbol", "strategy", "entry_price", "stop_loss",
                                           "target_1", "target_2", "candles_held", "quality"]]
    st.dataframe(df_open, use_container_width=True)
else:
    st.info("No open positions")

# ── Today's Closed Trades ──────────────────────────────────────────
st.subheader("✅ Today's Closed Trades")
if closed:
    df_closed = pd.DataFrame(closed)
    cols = [c for c in ["symbol", "strategy", "quality", "entry_price", "exit_price",
                         "entry_qty", "net_pnl", "charges", "state"] if c in df_closed.columns]
    df_view = df_closed[cols].copy()
    if "net_pnl" in df_view.columns:
        def color_pnl(val):
            color = "green" if val > 0 else "red"
            return f"color: {color}"
        st.dataframe(df_view.style.applymap(color_pnl, subset=["net_pnl"]),
                     use_container_width=True)
    else:
        st.dataframe(df_view, use_container_width=True)
else:
    st.info("No closed trades today")

st.divider()

# ── Historical Performance ─────────────────────────────────────────
st.subheader("📈 Historical Performance (Last 30 Days)")
reports = load_daily_reports(30)
if reports:
    df_hist = pd.DataFrame(reports)
    df_hist["date"] = pd.to_datetime(df_hist["date"])
    df_hist = df_hist.sort_values("date")

    # Cumulative P&L chart
    df_hist["cumulative_pnl"] = df_hist["net_pnl"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_hist["date"], y=df_hist["cumulative_pnl"],
        mode="lines+markers", name="Cumulative P&L",
        line=dict(color="green", width=2),
        fill="tozeroy",
    ))
    fig.update_layout(title="Cumulative P&L", xaxis_title="Date",
                      yaxis_title="P&L (₹)", height=350)
    st.plotly_chart(fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        # Daily P&L bar chart
        colors = ["green" if v >= 0 else "red" for v in df_hist["net_pnl"]]
        fig2 = go.Figure(go.Bar(x=df_hist["date"], y=df_hist["net_pnl"],
                                  marker_color=colors, name="Daily P&L"))
        fig2.update_layout(title="Daily P&L", height=300)
        st.plotly_chart(fig2, use_container_width=True)
    with col_b:
        # Win rate over time
        if "win_rate_pct" in df_hist.columns:
            fig3 = px.line(df_hist, x="date", y="win_rate_pct", title="Daily Win Rate (%)")
            fig3.add_hline(y=50, line_dash="dash", line_color="gray")
            fig3.update_layout(height=300)
            st.plotly_chart(fig3, use_container_width=True)

    # Summary stats
    st.subheader("📊 30-Day Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Trades", int(df_hist["trades"].sum()))
    c2.metric("Total Net P&L", f"₹{df_hist['net_pnl'].sum():+,.2f}")
    c3.metric("Avg Win Rate", f"{df_hist['win_rate_pct'].mean():.1f}%")
    c4.metric("Total Charges", f"₹{df_hist['charges'].sum():,.2f}")
    c5.metric("Best Day", f"₹{df_hist['net_pnl'].max():+,.2f}")
else:
    st.info("No historical report data yet. Run the bot to generate reports.")

# ── Auto-refresh ───────────────────────────────────────────────────
st.divider()
st.caption(f"Auto-refresh every 30s | Data from journaling/logs/ | Built with ZerodhaBot v1.0")
if st.button("🔄 Refresh Now"):
    st.rerun()

# Streamlit auto-rerun
import time
time.sleep(30)
st.rerun()
