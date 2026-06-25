"""
ZerodhaBot — Full Trade History & Strategy Analytics
Accessible from the Streamlit sidebar as "Analytics".
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import pytz

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
IST = pytz.timezone("Asia/Kolkata")

st.set_page_config(page_title="ZerodhaBot Analytics", page_icon="📊", layout="wide")
st.title("📊 Full Trade History & Strategy Analytics")
st.caption("Based on all trade logs since inception (Mar 2026 → present)")

STARTING_CAPITAL = 200_000


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_all_trades():
    trades = []
    for f in sorted((ROOT / "journaling" / "logs").glob("trades_*.json")):
        date = f.stem.replace("trades_", "")
        try:
            for t in json.loads(f.read_text()):
                trades.append({
                    "date":       date,
                    "trade_id":   t.get("trade_id", ""),
                    "symbol":     t.get("symbol", ""),
                    "strategy":   t.get("strategy", "?"),
                    "direction":  t.get("direction", "?"),
                    "quality":    t.get("quality", "?"),
                    "regime":     t.get("regime_at_entry", "?"),
                    "entry_price": t.get("entry_price", 0),
                    "exit_price":  t.get("exit_price", 0),
                    "entry_qty":   t.get("entry_qty", 0),
                    "net_pnl":    t.get("net_pnl") or 0,
                    "state":      t.get("state", ""),
                    "entry_time": t.get("entry_time", ""),
                    "exit_time":  t.get("exit_time", ""),
                    "candles_held": t.get("candles_held", 0),
                })
        except Exception:
            pass
    return pd.DataFrame(trades)


@st.cache_data(ttl=60)
def load_account_history():
    rows = []
    for f in sorted((ROOT / "reporting" / "output").glob("report_*.json")):
        date = f.stem.replace("report_", "")
        try:
            d = json.loads(f.read_text())
            av = d.get("account_value")
            if av:
                rows.append({"date": date, "account_value": float(av)})
        except Exception:
            pass
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


df = load_all_trades()
df_acct = load_account_history()

if df.empty:
    st.warning("No trade data found in journaling/logs/")
    st.stop()

df["date"] = pd.to_datetime(df["date"])
df["win"] = df["net_pnl"] > 0
df["loss"] = df["net_pnl"] < 0


# ── Top KPI row ───────────────────────────────────────────────────────────────
total_pnl    = df["net_pnl"].sum()
total_trades = len(df)
wins         = df["win"].sum()
wr           = wins / total_trades * 100 if total_trades else 0
best_day     = df.groupby("date")["net_pnl"].sum().max()
worst_day    = df.groupby("date")["net_pnl"].sum().min()

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Starting Capital",  f"₹{STARTING_CAPITAL:,.0f}")
k2.metric("Total Trade P&L",   f"₹{total_pnl:+,.0f}",
          delta=f"{total_pnl/STARTING_CAPITAL*100:+.1f}%")
k3.metric("Total Trades",      total_trades,
          delta=f"{wins} wins / {int(df['loss'].sum())} losses")
k4.metric("Overall Win Rate",  f"{wr:.1f}%")
k5.metric("Best Day",          f"₹{best_day:+,.0f}")
k6.metric("Worst Day",         f"₹{worst_day:+,.0f}")

st.divider()

# ── Account value timeline ────────────────────────────────────────────────────
st.subheader("Account Value Over Time")

if not df_acct.empty:
    fig_acct = go.Figure()
    fig_acct.add_trace(go.Scatter(
        x=df_acct["date"], y=df_acct["account_value"],
        mode="lines+markers", name="Account Value",
        line=dict(color="#2196F3", width=2),
        fill="tozeroy", fillcolor="rgba(33,150,243,0.08)",
    ))
    # Mark corruption dates
    for corrupt_date, label in [("2026-06-22", "Corrupt -₹66k"), ("2026-06-23", "Corrupt -₹43k")]:
        cdt = pd.to_datetime(corrupt_date)
        row = df_acct[df_acct["date"] == cdt]
        if not row.empty:
            fig_acct.add_vline(x=cdt, line_dash="dot", line_color="orange", opacity=0.7)
            fig_acct.add_annotation(x=cdt, y=row["account_value"].values[0],
                                    text=label, showarrow=True, arrowcolor="orange",
                                    font=dict(color="orange", size=10))
    fig_acct.add_hline(y=STARTING_CAPITAL, line_dash="dash", line_color="gray",
                       annotation_text="Starting ₹2L", annotation_position="top right")
    fig_acct.update_layout(height=350, xaxis_title="Date",
                           yaxis_title="Account Value (₹)",
                           yaxis_tickformat="₹,.0f",
                           hovermode="x unified")
    st.plotly_chart(fig_acct, use_container_width=True)
    st.caption("Orange dotted lines mark account_state.json corruption events (not real trading losses). "
               "True P&L from trades is ₹{:+,.0f}.".format(int(total_pnl)))

st.divider()

# ── Cumulative trade P&L ──────────────────────────────────────────────────────
st.subheader("Cumulative Trade P&L (from all logged trades)")

df_daily = df.groupby("date")["net_pnl"].sum().reset_index().sort_values("date")
df_daily["cumulative_pnl"] = df_daily["net_pnl"].cumsum()
df_daily["color"] = df_daily["net_pnl"].apply(lambda x: "#4CAF50" if x >= 0 else "#F44336")

col_cum, col_bar = st.columns([2, 1])
with col_cum:
    fig_cum = go.Figure()
    fig_cum.add_trace(go.Scatter(
        x=df_daily["date"], y=df_daily["cumulative_pnl"],
        mode="lines+markers", name="Cumulative P&L",
        line=dict(color="#4CAF50" if total_pnl >= 0 else "#F44336", width=2),
        fill="tozeroy",
        fillcolor="rgba(76,175,80,0.1)" if total_pnl >= 0 else "rgba(244,67,54,0.1)",
    ))
    fig_cum.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_cum.update_layout(height=300, xaxis_title="Date", yaxis_title="Cumulative P&L (₹)",
                          hovermode="x unified")
    st.plotly_chart(fig_cum, use_container_width=True)

with col_bar:
    fig_bar = go.Figure(go.Bar(
        x=df_daily["date"], y=df_daily["net_pnl"],
        marker_color=df_daily["color"], name="Daily P&L"
    ))
    fig_bar.update_layout(height=300, title="Daily P&L", xaxis_title="Date",
                          yaxis_title="₹", hovermode="x unified")
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# ── Strategy performance ───────────────────────────────────────────────────────
st.subheader("Strategy Performance Breakdown")

strat_df = df.groupby("strategy").agg(
    trades=("net_pnl", "count"),
    wins=("win", "sum"),
    losses=("loss", "sum"),
    net_pnl=("net_pnl", "sum"),
    avg_pnl=("net_pnl", "mean"),
    best=("net_pnl", "max"),
    worst=("net_pnl", "min"),
).reset_index()
strat_df["win_rate"] = (strat_df["wins"] / strat_df["trades"] * 100).round(1)
strat_df = strat_df.sort_values("net_pnl")

col_s1, col_s2 = st.columns([1, 1])

with col_s1:
    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in strat_df["net_pnl"]]
    fig_strat = go.Figure(go.Bar(
        y=strat_df["strategy"], x=strat_df["net_pnl"],
        orientation="h", marker_color=colors,
        text=[f"₹{v:+,.0f}" for v in strat_df["net_pnl"]],
        textposition="outside",
    ))
    fig_strat.update_layout(height=300, title="Net P&L by Strategy",
                            xaxis_title="Net P&L (₹)", yaxis_title="")
    st.plotly_chart(fig_strat, use_container_width=True)

with col_s2:
    fig_wr = go.Figure(go.Bar(
        y=strat_df["strategy"], x=strat_df["win_rate"],
        orientation="h",
        marker_color=["#4CAF50" if v >= 45 else "#FF9800" if v >= 35 else "#F44336"
                      for v in strat_df["win_rate"]],
        text=[f"{v:.0f}%" for v in strat_df["win_rate"]],
        textposition="outside",
    ))
    fig_wr.add_vline(x=50, line_dash="dash", line_color="gray",
                     annotation_text="50% target")
    fig_wr.update_layout(height=300, title="Win Rate by Strategy",
                         xaxis_title="Win Rate %", yaxis_title="",
                         xaxis_range=[0, 90])
    st.plotly_chart(fig_wr, use_container_width=True)

# Strategy table
strat_display = strat_df[["strategy","trades","wins","losses","win_rate","net_pnl","avg_pnl","best","worst"]].copy()
strat_display.columns = ["Strategy","Trades","Wins","Losses","Win Rate %","Net P&L","Avg P&L","Best Trade","Worst Trade"]
strat_display = strat_display.sort_values("Net P&L", ascending=False)

def style_pnl(val):
    if isinstance(val, (int, float)):
        return "color: #4CAF50" if val > 0 else ("color: #F44336" if val < 0 else "")
    return ""

st.dataframe(
    strat_display.style
        .format({"Net P&L": "₹{:+,.0f}", "Avg P&L": "₹{:+,.0f}",
                 "Best Trade": "₹{:+,.0f}", "Worst Trade": "₹{:+,.0f}",
                 "Win Rate %": "{:.1f}%"})
        .applymap(style_pnl, subset=["Net P&L","Avg P&L","Best Trade","Worst Trade"]),
    use_container_width=True, hide_index=True
)

st.divider()

# ── Regime performance ────────────────────────────────────────────────────────
st.subheader("Performance by Market Regime")

regime_df = df.groupby("regime").agg(
    trades=("net_pnl", "count"),
    wins=("win", "sum"),
    net_pnl=("net_pnl", "sum"),
    avg_pnl=("net_pnl", "mean"),
).reset_index()
regime_df["win_rate"] = (regime_df["wins"] / regime_df["trades"] * 100).round(1)
regime_df = regime_df.sort_values("net_pnl")

regime_colors = {
    "strong_bull": "#1B5E20", "weak_bull": "#4CAF50",
    "sideways": "#FF9800", "weak_bear": "#F44336",
    "strong_bear": "#B71C1C", "high_volatility": "#9C27B0", "?": "#9E9E9E",
}

col_r1, col_r2 = st.columns(2)
with col_r1:
    fig_reg = go.Figure(go.Bar(
        x=regime_df["regime"], y=regime_df["net_pnl"],
        marker_color=[regime_colors.get(r, "#9E9E9E") for r in regime_df["regime"]],
        text=[f"₹{v:+,.0f}" for v in regime_df["net_pnl"]],
        textposition="outside",
    ))
    fig_reg.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_reg.update_layout(height=300, title="Net P&L by Regime", yaxis_title="₹")
    st.plotly_chart(fig_reg, use_container_width=True)

with col_r2:
    fig_reg2 = go.Figure(go.Bar(
        x=regime_df["regime"], y=regime_df["trades"],
        marker_color=[regime_colors.get(r, "#9E9E9E") for r in regime_df["regime"]],
        text=regime_df["trades"], textposition="outside",
    ))
    fig_reg2.update_layout(height=300, title="Trade Count by Regime", yaxis_title="Trades")
    st.plotly_chart(fig_reg2, use_container_width=True)

regime_display = regime_df[["regime","trades","wins","win_rate","net_pnl","avg_pnl"]].copy()
regime_display.columns = ["Regime","Trades","Wins","Win Rate %","Net P&L","Avg P&L/Trade"]
st.dataframe(
    regime_display.style
        .format({"Net P&L": "₹{:+,.0f}", "Avg P&L/Trade": "₹{:+,.0f}", "Win Rate %": "{:.1f}%"})
        .applymap(style_pnl, subset=["Net P&L","Avg P&L/Trade"]),
    use_container_width=True, hide_index=True
)

st.divider()

# ── Strategy × Regime heatmap ─────────────────────────────────────────────────
st.subheader("Strategy × Regime P&L Heatmap")

pivot = df.pivot_table(values="net_pnl", index="strategy", columns="regime",
                       aggfunc="sum", fill_value=0)
fig_heat = px.imshow(
    pivot,
    color_continuous_scale=["#B71C1C", "#F44336", "#FFEB3B", "#4CAF50", "#1B5E20"],
    color_continuous_midpoint=0,
    text_auto=".0f",
    title="Net P&L (₹) — Strategy × Regime",
    aspect="auto",
)
fig_heat.update_layout(height=350)
st.plotly_chart(fig_heat, use_container_width=True)

st.divider()

# ── Symbol performance ────────────────────────────────────────────────────────
st.subheader("Top & Bottom Performing Symbols")

sym_df = df.groupby("symbol").agg(
    trades=("net_pnl", "count"),
    wins=("win", "sum"),
    net_pnl=("net_pnl", "sum"),
).reset_index()
sym_df["win_rate"] = (sym_df["wins"] / sym_df["trades"] * 100).round(1)
sym_df = sym_df.sort_values("net_pnl")

col_top, col_bot = st.columns(2)
with col_bot:
    worst5 = sym_df.head(5)
    fig_w = go.Figure(go.Bar(
        y=worst5["symbol"], x=worst5["net_pnl"], orientation="h",
        marker_color="#F44336",
        text=[f"₹{v:+,.0f} ({r:.0f}% WR)" for v, r in zip(worst5["net_pnl"], worst5["win_rate"])],
        textposition="outside",
    ))
    fig_w.update_layout(height=280, title="5 Worst Symbols", xaxis_title="Net P&L (₹)")
    st.plotly_chart(fig_w, use_container_width=True)

with col_top:
    best5 = sym_df.tail(5).iloc[::-1]
    fig_b = go.Figure(go.Bar(
        y=best5["symbol"], x=best5["net_pnl"], orientation="h",
        marker_color="#4CAF50",
        text=[f"₹{v:+,.0f} ({r:.0f}% WR)" for v, r in zip(best5["net_pnl"], best5["win_rate"])],
        textposition="outside",
    ))
    fig_b.update_layout(height=280, title="5 Best Symbols", xaxis_title="Net P&L (₹)")
    st.plotly_chart(fig_b, use_container_width=True)

st.divider()

# ── All trades table ──────────────────────────────────────────────────────────
st.subheader("All Trades")

with st.expander("Filters", expanded=False):
    fc1, fc2, fc3, fc4 = st.columns(4)
    strat_filter  = fc1.multiselect("Strategy",  sorted(df["strategy"].unique()),  default=[])
    regime_filter = fc2.multiselect("Regime",    sorted(df["regime"].unique()),    default=[])
    dir_filter    = fc3.multiselect("Direction", sorted(df["direction"].unique()), default=[])
    outcome       = fc4.radio("Outcome", ["All", "Wins only", "Losses only"])

df_view = df.copy()
if strat_filter:  df_view = df_view[df_view["strategy"].isin(strat_filter)]
if regime_filter: df_view = df_view[df_view["regime"].isin(regime_filter)]
if dir_filter:    df_view = df_view[df_view["direction"].isin(dir_filter)]
if outcome == "Wins only":   df_view = df_view[df_view["net_pnl"] > 0]
if outcome == "Losses only": df_view = df_view[df_view["net_pnl"] < 0]

display_cols = ["date","symbol","strategy","direction","quality","regime",
                "entry_price","exit_price","entry_qty","net_pnl","state","candles_held"]
df_show = df_view[display_cols].sort_values("date", ascending=False).reset_index(drop=True)
df_show["date"] = df_show["date"].dt.strftime("%Y-%m-%d")

st.dataframe(
    df_show.style
        .format({"entry_price": "₹{:.2f}", "exit_price": "₹{:.2f}", "net_pnl": "₹{:+,.2f}"})
        .applymap(style_pnl, subset=["net_pnl"]),
    use_container_width=True, height=400,
)
st.caption(f"Showing {len(df_show)} of {len(df)} trades")

st.divider()
st.caption("ZerodhaBot Analytics | journaling/logs/ + reporting/output/ | Refresh: every 60s")
if st.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()
