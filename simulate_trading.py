"""
simulate_trading.py — Full end-to-end simulation with synthetic bullish market data.

Runs the complete bot pipeline:
  synthetic data -> regime detection -> watchlist -> signals ->
  risk checks -> paper trades -> order manager -> journal -> EOD report

Usage:
  python simulate_trading.py
"""

import sys
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")

logging.basicConfig(
    level=logging.WARNING,         # suppress noise; we print our own output
    format="%(asctime)s | %(name)s | %(message)s",
)
logger = logging.getLogger("Simulation")

# -- Helpers -------------------------------------------------------------------

def _ema(s, n):
    k = 2 / (n + 1)
    e = [s[0]]
    for p in s[1:]:
        e.append(p * k + e[-1] * (1 - k))
    return np.array(e)


def make_nifty_daily(n=300, base=21000, trend=0.0006, volatility=0.008):
    """Synthesise daily NIFTY candles in a clear uptrend above all EMAs."""
    np.random.seed(42)
    now = datetime.now(IST).replace(hour=15, minute=30, second=0, microsecond=0)
    dates = [now - timedelta(days=(n - i)) for i in range(n)]
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + trend + np.random.normal(0, volatility)))
    closes = np.array(closes)
    rows = []
    for i, (d, c) in enumerate(zip(dates, closes)):
        atr = c * 0.012
        o = c * (1 + np.random.uniform(-0.004, 0.004))
        h = max(o, c) + abs(np.random.normal(0, atr * 0.4))
        l = min(o, c) - abs(np.random.normal(0, atr * 0.4))
        rows.append({
            "timestamp": d, "symbol": "^NSEI",
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "volume": int(np.random.uniform(8e8, 1.5e9)),
        })
    return pd.DataFrame(rows)


def make_stock_15m(symbol, n_days=5, base=1500, trend=0.0004, volatility=0.003):
    """
    Engineer 15-min candles to satisfy all EMA pullback signal conditions:
      - EMA9 > EMA21 (bullish ribbon)
      - price > EMA9 (above ribbon)
      - pullback low touched EMA9 within last 4 candles
      - RSI > 60 (12+ of last 14 candles are gains)
      - last candle volume >= 1.6x average
    """
    np.random.seed(hash(symbol) % (2**32))
    now = datetime.now(IST).replace(hour=14, minute=0, second=0, microsecond=0)
    candles_per_day = 20
    total = n_days * candles_per_day
    avg_vol = 200000

    # Build close prices: strong uptrend throughout, tiny 1-candle dip near end
    closes = [base]
    for i in range(1, total):
        tail = total - i
        if tail == 3:
            # single-candle pullback that will touch EMA9 from above
            closes.append(closes[-1] * 0.9985)
        elif tail == 2:
            closes.append(closes[-1] * 1.0025)   # quick recovery
        elif tail == 1:
            closes.append(closes[-1] * 1.0035)   # signal candle up
        else:
            closes.append(closes[-1] * (1 + abs(np.random.normal(trend * 2, volatility * 0.3))))

    closes = np.array(closes)
    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    atr_val = base * 0.006

    rows = []
    for i in range(total):
        tail = total - i
        day_offset = i // candles_per_day
        candle_offset = i % candles_per_day
        ts = now - timedelta(days=(n_days - 1 - day_offset), minutes=(candles_per_day - 1 - candle_offset) * 15)
        c = float(closes[i])

        if tail == 3:
            # Pullback candle: low must touch EMA9
            o = c * 1.001
            l = float(ema9[i]) * 0.9995   # ensures pullback condition fires
            h = o + atr_val * 0.3
            vol = int(avg_vol * 0.75)
        elif tail == 1:
            # Signal candle: strong close above EMA9, big volume
            o = c * 0.999
            h = c + atr_val * 0.8
            l = o - atr_val * 0.1
            vol = int(avg_vol * 1.9)   # 1.9x avg volume
        else:
            o = c * (1 + np.random.uniform(-0.002, 0.002))
            h = max(o, c) + atr_val * 0.4
            l = min(o, c) - atr_val * 0.2
            vol = int(avg_vol * np.random.uniform(0.85, 1.15))

        rows.append({
            "timestamp": ts, "symbol": symbol,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "volume": vol,
        })
    return pd.DataFrame(rows)


def make_stock_daily(symbol, n_days=120, base=1500, trend=0.0004, volatility=0.012):
    """Synthesise daily candles — stock clearly above 50 EMA."""
    np.random.seed(hash(symbol + "daily") % (2**32))
    now = datetime.now(IST).replace(hour=15, minute=30, second=0, microsecond=0)
    price = base * 0.60   # start 40% lower — ensures current price well above 50 EMA
    rows = []
    for i in range(n_days):
        day = now - timedelta(days=(n_days - i))
        price *= (1 + trend + np.random.normal(0, volatility))
        atr = price * 0.012
        o = price * (1 + np.random.uniform(-0.005, 0.005))
        h = max(o, price) + abs(np.random.normal(0, atr * 0.4))
        l = min(o, price) - abs(np.random.normal(0, atr * 0.3))
        rows.append({
            "timestamp": day, "symbol": symbol,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(price, 2),
            "volume": int(np.random.uniform(1e6, 5e6)),
        })
    return pd.DataFrame(rows)


# -- Main simulation ------------------------------------------------------------

def run():
    with open("config/config.yaml") as f:
        config = yaml.safe_load(f)

    print()
    print("=" * 62)
    print("  ZERODHABOT — FULL SIMULATION (Synthetic Bullish Market)")
    print("=" * 62)

    # -- 1. Account & tier ----------------------------------------
    from config.capital_tiers import get_tier
    from risk.risk_engine import RiskEngine

    account_value = 20000.0
    tier = get_tier(account_value)
    risk = RiskEngine(account_value, config.get("risk", {}))

    print(f"\n{'-'*62}")
    print(f"  ACCOUNT & RISK SETUP")
    print(f"{'-'*62}")
    print(f"  Capital        : Rs.{account_value:,.2f}")
    print(f"  Tier           : {tier.name}")
    print(f"  Risk/trade     : {tier.risk_per_trade_pct:.1%}  (Rs.{risk.sizer.max_risk_per_trade():.0f})")
    print(f"  Daily loss cap : Rs.{risk.sizer.max_daily_loss():.0f}")
    print(f"  Max deployable : Rs.{risk.sizer.max_deployable():.0f}")
    print(f"  Max trades/day : {tier.max_trades_per_day}")
    print(f"  Max positions  : {tier.max_open_positions}")

    # -- 2. Market regime -----------------------------------------
    from research.market_regime import MarketRegimeDetector

    nifty_daily = make_nifty_daily()
    vix = 14.2   # simulated low VIX = risk-on

    detector = MarketRegimeDetector()
    regime = detector.detect(nifty_daily, vix)

    print(f"\n{'-'*62}")
    print(f"  MARKET REGIME (SYNTHETIC)")
    print(f"{'-'*62}")
    print(f"  NIFTY last close : {nifty_daily['close'].iloc[-1]:,.0f}")
    print(f"  VIX              : {vix}")
    print(f"  Regime           : {regime.regime.value.upper()}")
    print(f"  200 EMA          : {'ABOVE' if regime.above_200ema else 'below'}")
    print(f"  50 EMA           : {'ABOVE' if regime.above_50ema else 'below'}")
    print(f"  ADX              : {regime.adx:.1f}")
    print(f"  Recommendation   : {regime.recommendation.upper()}")

    if regime.recommendation == "stay_flat":
        print("\n  Regime is STAY FLAT — adjusting synthetic data...")

    # -- 3. Signal generation -------------------------------------
    from strategies.strategy_registry import get_strategy
    from utils.charge_calculator import estimate_round_trip_charges, Segment

    candidates = [
        ("RELIANCE",   1480, "ema_pullback"),
        ("TCS",        3580, "ema_pullback"),
        ("HDFCBANK",    880, "ema_pullback"),
        ("NIFTYBEES",  260,  "etf_momentum"),
        ("INFY",       1720, "ema_pullback"),
    ]

    print(f"\n{'-'*62}")
    print(f"  SIGNAL SCAN ({len(candidates)} candidates)")
    print(f"{'-'*62}")

    trade_setups = []
    for symbol, base_price, strat_name in candidates:
        strategy = get_strategy(strat_name)
        df_15m  = make_stock_15m(symbol, n_days=5, base=base_price)
        df_daily = make_stock_daily(symbol, n_days=120, base=base_price)

        # Force bullish ribbon in last 5 candles
        c = df_15m["close"].values.copy()
        ema9  = _ema(c, 9)
        ema21 = _ema(c, 21)
        # Adjust close of last few bars to create a clean pullback-then-bounce
        for j in range(-4, 0):
            df_15m.loc[df_15m.index[j], "close"] = float(ema9[j]) * (1.001 + 0.002 * (j + 5))
            df_15m.loc[df_15m.index[j], "low"]   = float(ema9[j]) * 0.9985
            df_15m.loc[df_15m.index[j], "high"]  = float(ema9[j]) * 1.008
        df_15m.loc[df_15m.index[-1], "close"] = float(ema9[-1]) * 1.003
        df_15m.loc[df_15m.index[-1], "low"]   = float(ema9[-1]) * 0.999
        df_15m.loc[df_15m.index[-1], "high"]  = float(ema9[-1]) * 1.010

        cap = account_value * tier.max_per_trade_pct
        price = float(df_15m["close"].iloc[-1])
        charges = estimate_round_trip_charges(
            price, price,
            qty=max(1, int(cap / price)),
            segment=Segment.EQUITY_INTRADAY,
        )

        setup = strategy.generate_signal(
            symbol=symbol,
            df_primary=df_15m,
            df_daily=df_daily,
            regime_bullish=True,
            capital_per_trade=cap,
            charges_estimate=charges,
        )

        status = "SIGNAL" if setup.is_valid else f"skip ({setup.rejection_reason})"
        print(f"  {symbol:<12} [{strat_name:<12}] => {status}")
        if setup.is_valid:
            print(f"             Entry=Rs.{setup.entry_price:.2f}  SL=Rs.{setup.stop_loss:.2f}  "
                  f"T1=Rs.{setup.target_1:.2f}  T2=Rs.{setup.target_2:.2f}  "
                  f"Grade={setup.setup_quality}  RR={setup.reward_risk_ratio:.2f}")
            trade_setups.append((symbol, setup, charges))

    # -- 4. Risk checks -------------------------------------------
    from risk.risk_engine import RiskDecision

    print(f"\n{'-'*62}")
    print(f"  RISK ENGINE CHECKS")
    print(f"{'-'*62}")

    approved_trades = []
    for symbol, setup, charges in trade_setups:
        result = risk.check_trade(
            symbol=symbol,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            proposed_qty=max(1, int((account_value * tier.max_per_trade_pct) / setup.entry_price)),
            open_positions_value=sum(s.entry_price * r.adjusted_qty for _, s, r in approved_trades),
            setup_quality=setup.setup_quality,
            charges_estimate=charges,
        )
        verdict = "APPROVED" if result.decision == RiskDecision.APPROVED else f"REJECTED ({result.reason})"
        print(f"  {symbol:<12} => {verdict}", end="")
        if result.decision == RiskDecision.APPROVED:
            print(f"  qty={result.adjusted_qty}  capital=Rs.{result.adjusted_capital:,.0f}")
            approved_trades.append((symbol, setup, result))
        else:
            print()

    # -- 5. Paper trade execution ----------------------------------
    from brokers.simulated_broker import SimulatedBroker
    from brokers.base import OrderRequest, OrderSide, OrderType, ProductType
    from execution.trade_state_machine import TradeRecord, TradeState
    from journaling.trade_journal import TradeJournal
    from journaling.audit_logger import AuditLogger
    from utils.charge_calculator import estimate_round_trip_charges, Segment

    broker = SimulatedBroker(initial_capital=account_value)
    journal = TradeJournal("journaling/logs")
    audit = AuditLogger("journaling/logs")

    print(f"\n{'-'*62}")
    print(f"  PAPER TRADE EXECUTION")
    print(f"{'-'*62}")

    executed_trades = []
    for symbol, setup, risk_result in approved_trades:
        qty = risk_result.adjusted_qty
        order = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=qty,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            price=setup.entry_price,
            tag="simulation",
        )
        resp = broker.place_order(order)
        print(f"  BUY  {symbol:<12} qty={qty}  @ Rs.{setup.entry_price:.2f}  "
              f"=> {resp.status.value.upper()}  order_id={resp.order_id}")
        audit.log_order("BUY", symbol, qty, setup.entry_price, resp.order_id, resp.status.value)

        trade = TradeRecord(
            symbol=symbol,
            strategy=setup.strategy_name,
            setup_quality=setup.setup_quality,
            entry_price=setup.entry_price,
            entry_qty=qty,
            stop_loss=setup.stop_loss,
            target_1=setup.target_1,
            target_2=setup.target_2,
            breakeven_trigger=setup.breakeven_trigger,
            trailing_step=setup.trailing_step,
            regime_at_entry=regime.regime.value,
        )
        journal.save_trade(trade)
        executed_trades.append((symbol, setup, risk_result, trade, resp))

    print(f"\n  Broker capital after entries: Rs.{broker.capital:,.2f}")

    # -- 6. Simulate price outcomes --------------------------------
    print(f"\n{'-'*62}")
    print(f"  SIMULATED PRICE OUTCOMES")
    print(f"{'-'*62}")

    np.random.seed(99)
    outcomes = []
    for symbol, setup, risk_result, trade, resp in executed_trades:
        qty = risk_result.adjusted_qty
        entry = setup.entry_price
        sl    = setup.stop_loss
        t1    = setup.target_1
        t2    = setup.target_2
        rps   = entry - sl

        # Randomly decide outcome (weighted towards winning in bull market)
        outcome = np.random.choice(["sl", "t1", "t2", "time"], p=[0.25, 0.30, 0.35, 0.10])

        if outcome == "t2":
            exit_price = t2
            half = qty // 2
            charges_paid = estimate_round_trip_charges(entry, t1, half, Segment.EQUITY_INTRADAY) + \
                           estimate_round_trip_charges(entry, t2, qty - half, Segment.EQUITY_INTRADAY)
            net_pnl = (t1 - entry) * half + (t2 - entry) * (qty - half) - charges_paid
            result_label = "T2 HIT (full target)"
            state = TradeState.CLOSED_PROFIT
        elif outcome == "t1":
            exit_price = t1
            charges_paid = estimate_round_trip_charges(entry, t1, qty, Segment.EQUITY_INTRADAY)
            net_pnl = (t1 - entry) * qty - charges_paid
            result_label = "T1 HIT (partial exit)"
            state = TradeState.CLOSED_PROFIT
        elif outcome == "sl":
            exit_price = sl
            charges_paid = estimate_round_trip_charges(entry, sl, qty, Segment.EQUITY_INTRADAY)
            net_pnl = (sl - entry) * qty - charges_paid
            result_label = "SL HIT"
            state = TradeState.CLOSED_LOSS
        else:
            exit_price = entry * (1 + np.random.uniform(-0.003, 0.007))
            charges_paid = estimate_round_trip_charges(entry, exit_price, qty, Segment.EQUITY_INTRADAY)
            net_pnl = (exit_price - entry) * qty - charges_paid
            result_label = "TIME EXIT"
            state = TradeState.CLOSED_TIME

        # Update simulated broker
        sell_order = OrderRequest(
            symbol=symbol, side=OrderSide.SELL,
            quantity=qty, order_type=OrderType.MARKET,
            product=ProductType.MIS,
            price=exit_price, tag="simulation_exit",
        )
        broker.place_order(sell_order)

        # Update trade record
        trade.transition(state)
        trade.exit_price = round(exit_price, 2)
        trade.net_pnl = round(net_pnl, 2)
        trade.charges = round(charges_paid, 2)
        journal.save_trade(trade)

        pnl_str = f"Rs.{net_pnl:+.2f}"
        icon = "+" if net_pnl > 0 else "-"
        print(f"  [{icon}] {symbol:<12} {result_label:<24} exit=Rs.{exit_price:.2f}  "
              f"P&L={pnl_str}  charges=Rs.{charges_paid:.2f}")

        risk.record_result(net_pnl, symbol)
        outcomes.append({
            "symbol": symbol, "setup_quality": setup.setup_quality,
            "entry": entry, "exit": exit_price, "qty": qty,
            "net_pnl": round(net_pnl, 2), "charges": round(charges_paid, 2),
            "result": result_label, "state": state.value,
        })

    # -- 7. EOD Summary --------------------------------------------
    from reporting.daily_report import generate_daily_report, format_daily_report, save_daily_report
    from backtesting.performance_metrics import PerformanceCalculator

    trades_for_report = [
        {"net_pnl": o["net_pnl"], "charges": o["charges"],
         "state": o["state"], "symbol": o["symbol"]}
        for o in outcomes
    ]

    report = generate_daily_report(
        trades=trades_for_report,
        account_value=risk.account_value,
        starting_value=account_value,
        daily_pnl=risk.daily_pnl,
        regime=regime.regime.value,
        vix=vix,
        kill_switch_triggered=risk.kill_switch_active,
        rejected_trades=[],
    )
    save_daily_report(report, "journaling/reports")

    print(f"\n{'-'*62}")
    print(f"  EOD PERFORMANCE REPORT")
    print(f"{'-'*62}")
    print(f"  Trades executed  : {report['trades']}")
    print(f"  Wins / Losses    : {report['wins']} W  /  {report['losses']} L")
    print(f"  Win Rate         : {report['win_rate']:.1f}%")
    print(f"  Gross Profit     : Rs.{report['gross_profit']:+,.2f}")
    print(f"  Gross Loss       : Rs.{report['gross_loss']:+,.2f}")
    print(f"  Total Charges    : Rs.{report['total_charges']:,.2f}")
    print(f"  Net P&L (day)    : Rs.{report['daily_pnl']:+,.2f}")
    print(f"  Account Value    : Rs.{report['account_value']:,.2f}  "
          f"({report['total_return_pct']:+.2f}% all-time)")
    print(f"  Regime           : {report['regime'].upper()}")
    print(f"  Kill Switch      : {'TRIGGERED' if report['kill_switch'] else 'Normal'}")

    # Risk state
    print(f"\n  Risk State After Session:")
    print(f"  Consecutive losses : {risk.consecutive_losses}")
    print(f"  Kill switch        : {'ACTIVE' if risk.kill_switch_active else 'OFF'}")
    print(f"  Daily P&L vs limit : Rs.{risk.daily_pnl:+.2f} / -Rs.{risk.sizer.max_daily_loss():.0f}")

    print(f"\n{'-'*62}")
    print(f"  FILES WRITTEN")
    print(f"{'-'*62}")
    today = datetime.now(IST).strftime("%Y-%m-%d")
    print(f"  journaling/logs/trades_{today}.json")
    print(f"  journaling/logs/audit_{today}.jsonl")
    print(f"  journaling/reports/report_{today}.json")
    print(f"  journaling/reports/report_{today}.txt")

    print(f"\n{'='*62}")
    print(f"  SIMULATION COMPLETE")
    print(f"{'='*62}")
    print()


if __name__ == "__main__":
    run()
