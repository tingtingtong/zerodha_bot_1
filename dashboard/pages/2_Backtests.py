"""
ZerodhaBot — Backtest Results Explorer
Reads directly from mlflow.db — no MLflow server required.
Accessible from the Streamlit sidebar as "Backtests".
"""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="ZerodhaBot Backtests", page_icon="🧪", layout="wide")
st.title("🧪 Backtest Results Explorer")
st.caption("Powered by MLflow — all runs tracked automatically via backtest_runner.py")


# ── Load MLflow data ───────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_mlflow_runs() -> pd.DataFrame:
    try:
        import mlflow
        mlflow.set_tracking_uri(f"sqlite:///{ROOT}/mlflow.db")
        experiments = mlflow.search_experiments()
        if not experiments:
            return pd.DataFrame()

        exp_ids = [e.experiment_id for e in experiments if e.name != "Default"]
        if not exp_ids:
            exp_ids = [e.experiment_id for e in experiments]

        runs = mlflow.search_runs(
            experiment_ids=exp_ids,
            order_by=["start_time DESC"],
        )
        if runs.empty:
            return pd.DataFrame()

        # Flatten: rename mlflow column prefixes
        runs = runs.rename(columns=lambda c: (
            c.replace("params.", "").replace("metrics.", "").replace("tags.", "")
        ))
        return runs
    except Exception as e:
        st.error(f"Could not load MLflow data: {e}")
        return pd.DataFrame()


df_raw = load_mlflow_runs()

if df_raw.empty:
    st.warning("No backtest runs found in mlflow.db. Run a backtest first:\n\n"
               "```\npython backtest_runner.py --symbol BHEL --strategy supertrend_rsi "
               "--start 2025-06-01 --end 2026-06-01 --capital 200000\n```")
    st.stop()


# ── Build clean DataFrame ──────────────────────────────────────────────────────

METRIC_COLS = [
    "win_rate", "profit_factor", "sharpe_ratio", "net_pnl",
    "max_drawdown_pct", "total_trades", "expectancy",
    "max_consecutive_losses", "charge_drag_pct", "passed",
]
PARAM_COLS  = ["symbol", "strategy", "start", "end", "initial_capital", "interval"]

keep = ["run_id", "experiment_id", "start_time", "verdict", "failure_reasons"] + PARAM_COLS + METRIC_COLS
existing = [c for c in keep if c in df_raw.columns]
df = df_raw[existing].copy()

# Coerce numerics
for col in METRIC_COLS:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

if "passed" in df.columns:
    df["verdict"] = df["passed"].apply(lambda x: "PASS" if x == 1.0 else "FAIL")

if "start_time" in df.columns:
    df["run_date"] = pd.to_datetime(df["start_time"]).dt.strftime("%Y-%m-%d %H:%M")

df = df.sort_values("start_time", ascending=False).reset_index(drop=True)


# ── Sidebar filters ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")
    strategies = sorted(df["strategy"].dropna().unique()) if "strategy" in df.columns else []
    symbols    = sorted(df["symbol"].dropna().unique())   if "symbol" in df.columns else []

    sel_strategy = st.multiselect("Strategy", strategies, default=[])
    sel_symbol   = st.multiselect("Symbol",   symbols,   default=[])
    sel_verdict  = st.radio("Verdict", ["All", "PASS only", "FAIL only"])

    st.divider()
    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

df_view = df.copy()
if sel_strategy: df_view = df_view[df_view["strategy"].isin(sel_strategy)]
if sel_symbol:   df_view = df_view[df_view["symbol"].isin(sel_symbol)]
if sel_verdict == "PASS only": df_view = df_view[df_view["verdict"] == "PASS"]
if sel_verdict == "FAIL only": df_view = df_view[df_view["verdict"] == "FAIL"]


# ── Top KPIs ───────────────────────────────────────────────────────────────────

total_runs  = len(df_view)
pass_count  = (df_view["verdict"] == "PASS").sum() if "verdict" in df_view.columns else 0
fail_count  = total_runs - pass_count
pass_rate   = pass_count / total_runs * 100 if total_runs else 0
best_sharpe = df_view["sharpe_ratio"].max() if "sharpe_ratio" in df_view.columns else 0
best_pnl    = df_view["net_pnl"].max()      if "net_pnl"     in df_view.columns else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Runs",   total_runs)
k2.metric("PASS",         pass_count, delta=f"{pass_rate:.0f}% pass rate")
k3.metric("FAIL",         fail_count)
k4.metric("Best Sharpe",  f"{best_sharpe:.2f}" if pd.notna(best_sharpe) else "—")
k5.metric("Best Net P&L", f"₹{best_pnl:+,.0f}" if pd.notna(best_pnl) else "—")

st.divider()


# ── Pass/Fail by Strategy ──────────────────────────────────────────────────────

if "strategy" in df_view.columns and "verdict" in df_view.columns:
    st.subheader("Pass / Fail by Strategy")
    verdict_counts = (
        df_view.groupby(["strategy", "verdict"])
        .size()
        .reset_index(name="count")
    )
    fig_verd = px.bar(
        verdict_counts, x="strategy", y="count", color="verdict",
        color_discrete_map={"PASS": "#4CAF50", "FAIL": "#F44336"},
        barmode="group", title="PASS vs FAIL count per strategy",
    )
    fig_verd.update_layout(height=300, xaxis_title="", yaxis_title="Runs")
    st.plotly_chart(fig_verd, use_container_width=True)

st.divider()


# ── Scatter: Win Rate vs Profit Factor ────────────────────────────────────────

if {"win_rate", "profit_factor", "symbol", "strategy", "verdict"}.issubset(df_view.columns):
    st.subheader("Win Rate vs Profit Factor")
    col_sc1, col_sc2 = st.columns(2)

    with col_sc1:
        fig_sc = px.scatter(
            df_view.dropna(subset=["win_rate", "profit_factor"]),
            x="win_rate", y="profit_factor",
            color="verdict",
            color_discrete_map={"PASS": "#4CAF50", "FAIL": "#F44336"},
            hover_data=["symbol", "strategy", "net_pnl", "sharpe_ratio"],
            symbol="strategy",
            title="Win Rate % vs Profit Factor",
        )
        fig_sc.add_vline(x=0.42, line_dash="dash", line_color="gray",
                         annotation_text="Min WR 42%")
        fig_sc.add_hline(y=1.25, line_dash="dash", line_color="gray",
                         annotation_text="Min PF 1.25")
        fig_sc.update_layout(height=380)
        st.plotly_chart(fig_sc, use_container_width=True)

    with col_sc2:
        fig_sh = px.scatter(
            df_view.dropna(subset=["sharpe_ratio", "max_drawdown_pct"]),
            x="max_drawdown_pct", y="sharpe_ratio",
            color="verdict",
            color_discrete_map={"PASS": "#4CAF50", "FAIL": "#F44336"},
            hover_data=["symbol", "strategy", "net_pnl"],
            symbol="strategy",
            title="Max Drawdown % vs Sharpe Ratio",
        )
        fig_sh.add_vline(x=18, line_dash="dash", line_color="gray",
                         annotation_text="Max DD 18%")
        fig_sh.update_layout(height=380)
        st.plotly_chart(fig_sh, use_container_width=True)

st.divider()


# ── Net P&L by Symbol (per strategy) ──────────────────────────────────────────

if {"net_pnl", "symbol", "strategy"}.issubset(df_view.columns):
    st.subheader("Net P&L by Symbol")
    fig_pnl = px.bar(
        df_view.dropna(subset=["net_pnl"]).sort_values("net_pnl"),
        x="net_pnl", y="symbol", color="strategy",
        orientation="h",
        text=df_view.dropna(subset=["net_pnl"]).sort_values("net_pnl")["net_pnl"].apply(
            lambda v: f"₹{v:+,.0f}"
        ),
        title="Net P&L per Backtest Run",
    )
    fig_pnl.add_vline(x=0, line_dash="dash", line_color="gray")
    fig_pnl.update_layout(height=max(300, len(df_view) * 28), xaxis_title="Net P&L (₹)",
                           yaxis_title="")
    st.plotly_chart(fig_pnl, use_container_width=True)

st.divider()


# ── Full Results Table ─────────────────────────────────────────────────────────

st.subheader("All Backtest Runs")

display_cols = [c for c in [
    "run_date", "strategy", "symbol", "start", "end", "interval",
    "total_trades", "win_rate", "profit_factor", "expectancy",
    "net_pnl", "max_drawdown_pct", "sharpe_ratio",
    "max_consecutive_losses", "charge_drag_pct", "verdict", "failure_reasons",
] if c in df_view.columns]

df_table = df_view[display_cols].copy()

def style_verdict(val):
    if val == "PASS": return "color: #4CAF50; font-weight: bold"
    if val == "FAIL": return "color: #F44336"
    return ""

def style_num(val):
    if isinstance(val, (int, float)):
        return "color: #4CAF50" if val > 0 else ("color: #F44336" if val < 0 else "")
    return ""

fmt = {}
if "win_rate"         in df_table.columns: fmt["win_rate"]         = "{:.1%}"
if "profit_factor"    in df_table.columns: fmt["profit_factor"]    = "{:.2f}"
if "sharpe_ratio"     in df_table.columns: fmt["sharpe_ratio"]     = "{:.2f}"
if "net_pnl"          in df_table.columns: fmt["net_pnl"]          = "₹{:+,.0f}"
if "expectancy"       in df_table.columns: fmt["expectancy"]       = "₹{:+,.0f}"
if "max_drawdown_pct" in df_table.columns: fmt["max_drawdown_pct"] = "{:.1f}%"
if "charge_drag_pct"  in df_table.columns: fmt["charge_drag_pct"]  = "{:.1f}%"

styled = df_table.style.format(fmt, na_rep="—")
if "verdict" in df_table.columns:
    styled = styled.applymap(style_verdict, subset=["verdict"])
if "net_pnl" in df_table.columns:
    styled = styled.applymap(style_num, subset=["net_pnl"])

st.dataframe(styled, use_container_width=True, height=420, hide_index=True)
st.caption(f"Showing {len(df_table)} runs | mlflow.db at project root | Auto-refreshes every 30s")
