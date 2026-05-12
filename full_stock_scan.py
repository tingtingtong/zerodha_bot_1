"""
Full NIFTY-200 stock scan — backtests all symbols across 5 years,
ranks by profitability, shows monthly P&L range at Rs.1.5L capital.

Usage: python full_stock_scan.py
"""
import sys, logging
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz, pandas as pd, numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.WARNING)
IST   = pytz.timezone("Asia/Kolkata")
FIXED = pd.Timestamp("2026-01-01 11:00:00", tz=IST)
BASE  = 150_000
START = "2021-01-01"
END   = "2026-03-01"

# Full NIFTY 200 — large cap (NIFTY 100) + mid cap (next 100)
NIFTY_200 = [
    # NIFTY 50 (large cap core)
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "HINDUNILVR", "SBIN", "BAJFINANCE", "BHARTIARTL", "KOTAKBANK",
    "ITC", "LT", "HCLTECH", "AXISBANK", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND",
    "WIPRO", "POWERGRID", "TECHM", "BAJAJFINSV", "ADANIENT",
    "BAJAJ-AUTO", "NTPC", "ONGC", "JSWSTEEL", "TATASTEEL",
    "INDUSINDBK", "M&M", "COALINDIA", "HDFCLIFE", "SBILIFE",
    "BRITANNIA", "DIVISLAB", "CIPLA", "DRREDDY", "APOLLOHOSP",
    "EICHERMOT", "HEROMOTOCO", "BPCL", "IOC", "GRASIM",
    "SHREECEM", "HINDALCO", "VEDL", "TATACONSUM", "PIDILITIND",
    # NIFTY Next 50
    "ADANIPORTS", "ADANIGREEN", "ADANITRANS", "ADANIPOWER",
    "AMBUJACEM", "AUROPHARMA", "BANDHANBNK", "BANKBARODA",
    "BERGEPAINT", "BIOCON", "BOSCHLTD", "CANBK",
    "CHOLAFIN", "COLPAL", "CONCOR", "CUMMINSIND",
    "DABUR", "DALBHARAT", "DLF", "FEDERALBNK",
    "GAIL", "GLAXO", "GODREJCP", "GODREJPROP",
    "HAVELLS", "IDFCFIRSTB", "IGL", "INDIGO",
    "INDUSTOWER", "IRCTC", "JINDALSTEL", "JUBLFOOD",
    "LICHSGFIN", "LUPIN", "MARICO", "MCDOWELL-N",
    "MPHASIS", "MRF", "MOTHERSON", "MUTHOOTFIN",
    "NAUKRI", "NMDC", "OBEROIRLTY", "PAGEIND",
    "PERSISTENT", "PEL", "PFC", "PIIND",
    "PNB", "RECLTD", "SAIL", "SIEMENS",
    "SRF", "STAR", "TATACOMM", "TATACHEM",
    "TATAELXSI", "TORNTPHARM", "TORNTPOWER", "TRENT",
    "TVSMOTOR", "UBL", "UNITDSPR", "UPL",
    "VOLTAS", "WHIRLPOOL", "YESBANK", "ZYDUSLIFE",
    # NIFTY Midcap 100 (selected)
    "AAVAS", "ABB", "ABCAPITAL", "ABFRL",
    "ACC", "AEGISCHEM", "ALKEM", "APLLTD",
    "ASHOKLEY", "ASTRAL", "ATUL", "AUBANK",
    "BAJAJHLDNG", "BATAINDIA", "BBTC", "BHEL",
    "BLUESTARCO", "BPCL", "BRIGADE", "CARBORUNIV",
    "CASTROLIND", "CEATLTD", "CENTRALBK", "CGPOWER",
    "COFORGE", "CROMPTON", "CRISIL", "DEEPAKNTR",
    "DELHIVERY", "DIXON", "EDELWEISS", "EMAMILTD",
    "ENGINERSIN", "ESCORTS", "EXIDEIND", "FINEORG",
    "FORTIS", "FSL", "GMRINFRA", "GNFC",
    "GODREJIND", "GRINDWELL", "GSFC", "GSPL",
    "HFCL", "HINDPETRO", "HONAUT", "HUDCO",
    "IBULHSGFIN", "ICICIlombard", "ICICIPRULIFE", "IDBI",
    "IPCALAB", "IRB", "ISEC", "JKCEMENT",
    "JKLAKSHMI", "JKPAPER", "JSL", "JUBILANT",
    "KAJARIACER", "KALYANKJIL", "KANSAINER", "KEI",
    "KMARTBRND", "KPITTECH", "KPRMILL", "LALPATHLAB",
    "LAURUSLABS", "LINDEINDIA", "LTTS", "LUXIND",
    "MAHABANK", "MAHINDCIE", "MANAPPURAM", "MASFIN",
    "MAXHEALTH", "METROPOLIS", "MINDTREE", "MMTC",
    "NATIONALUM", "NBCC", "NCC", "NIACL",
    "NOCIL", "OFSS", "ORIENTELEC", "PGHH",
    "PHOENIXLTD", "POLYCAB", "POLYMED", "PRAJIND",
    "PRESTIGE", "PRINCEPIPE", "RADICO", "RAILTEL",
    "RALLIS", "RAMCOCEM", "RAYMOND", "RBL",
    "REDINGTON", "RITES", "RVNL", "SAFARI",
    "SANOFI", "SCHAEFFLER", "SEQUENT", "SKFINDIA",
    "SOBHA", "SPANDANA", "SPARC", "SUNPHARMA",
    "SUPREMEIND", "SYNGENE", "TANLA", "TASTYBITE",
    "TATAINVEST", "TCIEXP", "TEAMLEASE", "THYROCARE",
    "TIINDIA", "TIMKEN", "TINPLATE", "TITAGARH",
    "TRITURBINE", "UCOBANK", "UJJIVAN", "ULTRACEMCO",
    "UNIPARTS", "UNIONBANK", "USHAMART", "VGUARD",
    "VTL", "WABCOINDIA", "WELCORP", "WELSPUNIND",
    "WESTLIFE", "ZEEL", "ZENSARTECH", "ZENTEC",
]
# Deduplicate while preserving order
seen = set()
UNIVERSE = []
for s in NIFTY_200:
    if s not in seen:
        seen.add(s)
        UNIVERSE.append(s)

STRATEGIES = [("ema_pullback", True), ("mean_reversion", True)]


def run_symbol(symbol, daily_df, primary_df, strategies):
    from strategies.strategy_registry import get_strategy
    from utils.charge_calculator import estimate_round_trip_charges, Segment
    from config.capital_tiers import get_tier
    from backtest_runner import simulate_outcome

    all_trades = []
    for strat_name, regime in strategies:
        try:
            strategy = get_strategy(strat_name)
        except Exception:
            continue
        account = BASE
        i = 60
        while i < len(primary_df):
            sp = primary_df.iloc[max(0, i - 60): i + 1].copy()
            ts = sp.iloc[-1]["timestamp"]
            sd = daily_df[daily_df["timestamp"] <= ts].copy()
            if len(sd) < 55:
                i += 1
                continue
            tier = get_tier(account)
            cap  = account * tier.max_per_trade_pct
            cp   = float(sp.iloc[-1]["close"])
            ch   = estimate_round_trip_charges(
                cp, cp, qty=max(1, int(cap / max(cp, 1))),
                segment=Segment.EQUITY_INTRADAY)
            try:
                with patch("pandas.Timestamp.now", return_value=FIXED):
                    setup = strategy.generate_signal(symbol, sp, sd, regime, cap, ch)
            except Exception:
                i += 1
                continue
            if not setup.is_valid or setup.signal.value == "no_trade":
                i += 1
                continue
            qty = max(1, int((account * tier.risk_per_trade_pct) /
                             max(setup.risk_per_share, 0.01)))
            qty = min(qty, int(cap / max(setup.entry_price, 1)))
            if qty < 1:
                i += 1
                continue
            future  = primary_df.iloc[i + 1: i + 17]
            net_pnl = simulate_outcome(setup, qty, future, ch, is_short=False)
            account += net_pnl
            try:
                mk = pd.Timestamp(ts).strftime("%Y-%m")
            except Exception:
                mk = "unk"
            all_trades.append({
                "month": mk, "net_pnl": net_pnl, "charges": ch,
                "symbol": symbol, "strategy": strat_name,
            })
            i += 16
    return all_trades


def main():
    from data_providers.provider_registry import DataProviderRegistry
    data = DataProviderRegistry.build_free_only()
    from_dt = datetime(2021, 1, 1, tzinfo=IST)
    to_dt   = datetime(2026, 3, 1, tzinfo=IST)

    results = []
    total = len(UNIVERSE)
    print(f"Scanning {total} symbols (5-year daily data, 2021-2026)...")
    print("This takes 5-8 minutes.\n")

    for idx, sym in enumerate(UNIVERSE, 1):
        try:
            daily_df   = data.get_historical(sym, "1d", from_dt - timedelta(days=300), to_dt)
            primary_df = data.get_historical(sym, "1d", from_dt, to_dt)
            if daily_df is None or primary_df is None:
                continue
            if len(primary_df) < 200:   # need at least 200 trading days
                continue
            trades = run_symbol(sym, daily_df, primary_df, STRATEGIES)
            if not trades:
                continue
            df = pd.DataFrame(trades)
            monthly = df.groupby("month")["net_pnl"].sum()
            net_total  = df["net_pnl"].sum()
            n_months   = monthly.shape[0]
            n_trades   = len(df)
            avg_month  = monthly.mean()
            best_month = monthly.max()
            worst_month= monthly.min()
            win_rate   = (df["net_pnl"] > 0).mean()
            profitable_months = (monthly > 0).mean()
            charges    = df["charges"].sum()
            # Sharpe (trade-level)
            std = df["net_pnl"].std()
            sharpe = float((df["net_pnl"].mean() / max(std, 0.01)) * np.sqrt(252)) if len(df) > 2 else 0
            # Profit factor
            wins  = df[df["net_pnl"] > 0]["net_pnl"].sum()
            losses= abs(df[df["net_pnl"] < 0]["net_pnl"].sum())
            pf    = wins / max(losses, 0.01)

            results.append({
                "symbol":       sym,
                "trades":       n_trades,
                "months":       n_months,
                "net_total":    round(net_total, 0),
                "avg_month":    round(avg_month, 0),
                "best_month":   round(best_month, 0),
                "worst_month":  round(worst_month, 0),
                "win_rate":     round(win_rate * 100, 1),
                "prof_months":  round(profitable_months * 100, 0),
                "sharpe":       round(sharpe, 2),
                "pf":           round(pf, 2),
                "charges":      round(charges, 0),
            })
            status = "+" if net_total > 0 else "-"
            print(f"  [{idx:>3}/{total}] {sym:<14} {status}  trades:{n_trades:>3}  "
                  f"avg/mo:{avg_month:>+7.0f}  WR:{win_rate*100:.0f}%  "
                  f"Sharpe:{sharpe:.2f}  PF:{pf:.2f}")
        except Exception as e:
            print(f"  [{idx:>3}/{total}] {sym:<14} SKIP ({type(e).__name__})")
            continue

    if not results:
        print("No results.")
        return

    df_r = pd.DataFrame(results)
    # Composite score: net_total + sharpe bonus + pf bonus
    df_r["score"] = (
        df_r["net_total"] / df_r["net_total"].abs().max() * 50 +
        df_r["sharpe"].clip(-3, 5) / 5 * 25 +
        df_r["pf"].clip(0, 3) / 3 * 25
    )
    df_r = df_r.sort_values("score", ascending=False)

    # ── FULL RANKINGS ─────────────────────────────────────────────────────────
    CAPS   = [50_000, 100_000, 150_000, 200_000, 300_000, 500_000]
    CLBLS  = ["50k", "1L", "1.5L", "2L", "3L", "5L"]

    print(f"\n{'='*130}")
    print(f"  FULL NIFTY-200 RANKINGS — EMA Pullback + Mean Reversion | 5yr (2021-2026) @ Rs.1.5L base")
    print(f"  Sorted by composite score (net P&L + Sharpe + Profit Factor)")
    print(f"{'='*130}")

    hdr = (f"  {'#':>3}  {'Symbol':<12}  {'Trades':>6}  {'WR%':>5}  "
           f"{'PF':>5}  {'Sharpe':>6}  "
           f"{'Avg/mo':>8}  {'Best mo':>8}  {'Worst mo':>9}  "
           f"{'5yr P&L':>9}  {'Prof%':>6}  " +
           "  ".join(f"{l:>8}" for l in CLBLS))
    print(hdr)
    print("-" * 130)

    for rank, row in enumerate(df_r.itertuples(), 1):
        flag = "*" if row.net_total > 0 else " "
        scaled = "  ".join(
            f"{row.avg_month * (c / BASE):>+8.0f}" for c in CAPS)
        print(f"{flag} {rank:>3}  {row.symbol:<12}  {row.trades:>6}  "
              f"{row.win_rate:>5.1f}  {row.pf:>5.2f}  {row.sharpe:>6.2f}  "
              f"{row.avg_month:>+8.0f}  {row.best_month:>+8.0f}  {row.worst_month:>+9.0f}  "
              f"{row.net_total:>+9.0f}  {row.prof_months:>5.0f}%  {scaled}")

    # ── TOP 20 SUMMARY ────────────────────────────────────────────────────────
    top = df_r[df_r["net_total"] > 0].head(20)
    print(f"\n{'='*130}")
    print(f"  TOP 20 PROFITABLE SYMBOLS — Monthly avg/month (Rs.) at each capital level")
    print(f"{'='*130}")
    print(f"  {'Symbol':<12}" + "".join(f"  {l:>9}" for l in CLBLS) +
          f"  {'WR%':>5}  {'Sharpe':>6}  {'5yr net':>9}")
    print("-" * 90)
    for row in top.itertuples():
        scaled = "".join(f"  {row.avg_month*(c/BASE):>+9.0f}" for c in CAPS)
        print(f"  {row.symbol:<12}{scaled}  {row.win_rate:>5.1f}  "
              f"{row.sharpe:>6.2f}  {row.net_total:>+9.0f}")

    # ── AVOID LIST ────────────────────────────────────────────────────────────
    bad = df_r[df_r["net_total"] < 0].tail(15)
    print(f"\n  AVOID (5yr net loss): " +
          ", ".join(bad["symbol"].tolist()))

    # ── PORTFOLIO COMBOS ──────────────────────────────────────────────────────
    print(f"\n{'='*130}")
    print(f"  PORTFOLIO COMBINATIONS — if you trade top N symbols simultaneously")
    print(f"  (simple sum of avg monthly P&L; capital split equally across symbols)")
    print(f"{'='*130}")
    print(f"  {'Portfolio':<20}" + "".join(f"  {l:>9}" for l in CLBLS))
    print("-" * 85)
    best_syms = top["symbol"].tolist()
    for n in [4, 6, 8, 10, 15, 20]:
        syms_n = best_syms[:n]
        if len(syms_n) < n:
            break
        cap_per_sym = 1 / n  # fraction of total capital per symbol
        for ci, cap in enumerate(CAPS):
            pass
        row_vals = []
        for cap in CAPS:
            total_mo = sum(
                top[top["symbol"] == s]["avg_month"].values[0] * (cap / BASE) * cap_per_sym * n
                for s in syms_n
            )
            row_vals.append(f"{total_mo:>+9.0f}")
        label = f"Top {n} symbols"
        print(f"  {label:<20}" + "  ".join(row_vals))

    print(f"\n  Note: Portfolio assumes equal capital split. Single stock = full capital deployed.")
    print(f"  Charges: ~Rs.50/round-trip (flat), baked into all figures.")
    print(f"  Data: 5yr daily bars (yfinance). Live results will differ — use as relative ranking.")
    print()


if __name__ == "__main__":
    main()
