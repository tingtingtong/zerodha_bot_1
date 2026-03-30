"""
ZerodhaBot — Main entry point.

Usage:
  python main.py                  # paper trading (default)
  python main.py --mode paper
  python main.py --mode semi_auto
  python main.py --mode live
  python main.py --reset-daily    # manual daily reset
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytz
import yaml
from dotenv import load_dotenv

load_dotenv()

# ── Project root on sys.path ─────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")


def setup_logging(log_level: str, log_dir: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_file = Path(log_dir) / f"bot_{today}.log"

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Windows console may not support UTF-8; reconfigure if possible
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=getattr(logging, log_level.upper(), "INFO"),
                        format=fmt, datefmt=datefmt, handlers=handlers)
    return logging.getLogger("ZerodhaBot")


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_broker(config: dict, mode: str):
    if mode == "paper":
        from brokers.simulated_broker import SimulatedBroker
        account_value = config["account"]["starting_capital"]
        return SimulatedBroker(initial_capital=account_value)
    elif mode in ("semi_auto", "live"):
        from brokers.zerodha_auth import ZerodhaTokenManager
        from brokers.zerodha_broker import ZerodhaExecutionAdapter
        tm = ZerodhaTokenManager()
        kite = tm.get_kite()
        if not tm.is_session_valid():
            raise RuntimeError(f"Zerodha session invalid. Login at: {tm.get_login_url()}")
        return ZerodhaExecutionAdapter(kite)
    raise ValueError(f"Unknown mode: {mode}")


def build_data_registry(config: dict):
    from data_providers.provider_registry import DataProviderRegistry
    mode = config.get("data", {}).get("provider_mode", "free_only")
    if mode == "free_only":
        return DataProviderRegistry.build_free_only()
    api_key = os.getenv("ZERODHA_API_KEY", "")
    token_path = "config/.zerodha_token.json"
    try:
        import json
        with open(token_path) as f:
            tok = json.load(f).get("access_token", "")
        return DataProviderRegistry.build_zerodha_primary(api_key, tok)
    except Exception:
        return DataProviderRegistry.build_free_only()


def main():
    parser = argparse.ArgumentParser(description="ZerodhaBot Trading System")
    parser.add_argument("--mode", default=None, help="paper | semi_auto | live")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--reset-daily", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    mode = args.mode or config["bot"].get("mode", "live")
    config["bot"]["mode"] = mode

    logger = setup_logging(
        config["bot"].get("log_level", "INFO"),
        config["bot"].get("log_dir", "journaling/logs"),
    )
    logger.info(f"{'='*60}")
    logger.info(f"  ZerodhaBot v{config['bot']['version']}  |  Mode: {mode.upper()}")
    logger.info(f"{'='*60}")

    # Write PID file so Telegram commander can track this process
    pid_file = ROOT / "journaling" / "bot.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # ── Load persisted account value ──────────────────────────────
    from journaling.trade_journal import TradeJournal
    journal = TradeJournal(config["reporting"]["journal_dir"])
    account_value = journal.load_account_state(
        default=config["account"]["starting_capital"]
    )
    logger.info(f"Account value loaded: ₹{account_value:,.2f}")

    # ── Components ────────────────────────────────────────────────
    from risk.risk_engine import RiskEngine
    from execution.order_manager import OrderManager
    from execution.reconciler import Reconciler
    from research.watchlist_builder import WatchlistBuilder
    from research.event_calendar import EventCalendar
    from strategies.strategy_registry import get_active_strategies
    from utils.time_utils import is_trading_day, is_market_open, now_ist, minutes_to_close, MARKET_OPEN
    from utils.notification import TelegramNotifier
    from utils.charge_calculator import estimate_round_trip_charges, Segment
    from journaling.audit_logger import AuditLogger
    from reporting.daily_report import generate_daily_report, format_daily_report, save_daily_report
    from config.capital_tiers import get_tier

    broker = build_broker(config, mode)
    data = build_data_registry(config)

    # In live mode, use actual broker margin as the capital base
    if mode == "live":
        live_margin = broker.get_available_margin()
        if live_margin > 0:
            account_value = live_margin
            logger.info(f"Live mode: using actual Zerodha margin ₹{account_value:,.2f}")

    risk = RiskEngine(account_value, config.get("risk", {}))
    order_mgr = OrderManager(broker, risk)
    watchlist_builder = WatchlistBuilder()
    event_cal = EventCalendar()
    strategies = get_active_strategies(config["strategy"].get("active", ["ema_pullback"]))
    notifier = TelegramNotifier()
    audit = AuditLogger(config["reporting"]["journal_dir"])

    # ── Daily reset if requested ───────────────────────────────────
    if args.reset_daily:
        risk.reset_daily()
        logger.info("Daily reset complete.")
        return

    # ── Pre-market check ──────────────────────────────────────────
    now = now_ist()
    if not is_trading_day(now.date()):
        logger.info("Today is a market holiday or weekend. Bot exiting.")
        return

    # ── Reconcile on startup ──────────────────────────────────────
    reconciler = Reconciler(broker, "journaling/account_state.json")
    recon = reconciler.reconcile()
    if recon["orphaned"]:
        logger.critical(f"Orphaned positions found: {recon['orphaned']}. Review immediately.")
        audit.log_error("reconciler", "orphaned_positions", recon)

    # ── Market regime + watchlist (pre-market) ────────────────────
    logger.info("Building pre-market watchlist...")
    from datetime import timedelta
    nifty_daily = data.get_historical("^NSEI", "1d",
                                      now - timedelta(days=400), now)
    # Use yfinance VIX proxy
    try:
        vix_data = data.get_index_data("INDIA_VIX")
        vix = vix_data.get("close", 15.0)
    except Exception:
        vix = 15.0

    # ── Load NSE corporate actions into event calendar ─────────────
    try:
        from research.nse_corporate_actions import load_into_calendar
        ca_summary = load_into_calendar(
            event_cal,
            lookahead_days=config["market_research"].get("avoid_event_within_days", 7),
        )
        if ca_summary["symbols"]:
            logger.info(
                f"Corporate actions: {ca_summary['ex_dates']} ex-dates, "
                f"{ca_summary['board_meetings']} results — "
                f"blocking: {ca_summary['symbols']}"
            )
    except Exception as e:
        logger.warning(f"NSE corporate actions fetch failed (non-critical): {e}")

    event_symbols = event_cal.get_event_symbols_today(
        within_days=config["market_research"].get("avoid_event_within_days", 3)
    )
    regime, watchlist = watchlist_builder.build(
        data_registry=data,
        nifty_daily=nifty_daily,
        vix=vix,
        config=config["market_research"],
        event_symbols=list(event_symbols),
    )

    audit.log_regime(regime.regime.value, vix, regime.recommendation)
    logger.info(f"Regime: {regime.regime.value} | VIX: {vix} | Watchlist: {[w.symbol for w in watchlist[:5]]}")

    # ── Send pre-market brief to Telegram ─────────────────────────
    if config["notifications"]["enabled"]:
        from config.capital_tiers import get_tier_summary
        from utils.time_utils import next_trading_day
        tier = get_tier_summary(risk.account_value)

        # Assess blockers and trade probability
        blockers = []
        if vix >= 20:
            blockers.append(f"VIX {vix:.1f} — elevated, cautious sizing")
        if vix >= 25:
            blockers.append(f"VIX {vix:.1f} >= 25 — new trades halted per risk rules")
        if regime.recommendation == "stay_flat":
            blockers.append("Regime: STAY FLAT — no new trades today")
        if risk.kill_switch_active:
            blockers.append("Kill switch active")
        if tier["max_risk_per_trade_inr"] < 150:
            blockers.append(f"Capital Rs.{risk.account_value:,.0f} — tight stop needed (<Rs.{tier['max_risk_per_trade_inr']:.0f} risk/trade)")

        # Probability logic
        if regime.recommendation == "stay_flat" or risk.kill_switch_active:
            probability = "Low"
        elif vix >= 25 and regime.regime.value in ("weak_bear", "strong_bear"):
            probability = "Low"
        elif regime.regime.value in ("strong_bull", "weak_bull") and vix < 20:
            probability = "High"
        elif len(watchlist) >= 5 and vix < 25:
            probability = "Medium"
        else:
            probability = "Low"

        active_strategies = [s.replace("_", " ").title() for s in config["strategy"].get("active", [])]
        notifier.send_premarket_brief(
            regime=regime.regime.value,
            vix=vix,
            account_value=risk.account_value,
            tier_name=tier["tier_name"],
            max_risk_inr=tier["max_risk_per_trade_inr"],
            max_trades=tier["max_trades_per_day"],
            watchlist=[w.symbol for w in watchlist[:5]],
            strategies=active_strategies,
            blockers=blockers,
            trade_probability=probability,
            next_trading_day=next_trading_day(),
        )

    if regime.recommendation == "stay_flat":
        logger.info("Regime: STAY FLAT today. Bot will monitor but not trade.")
        if config["notifications"]["enabled"]:
            notifier.send(
                f"📊 <b>BOT STARTED — STAY FLAT</b>\n"
                f"Regime: {regime.regime.value.upper()}  |  VIX: {vix:.1f}\n"
                f"No trades today. Updates every 15 mins will follow."
            )
        # Stay-flat monitoring loop — sends 15-min updates, exits at market close
        last_status_slot = -1
        hourly_steps = []
        while True:
            now = now_ist()
            if not is_market_open():
                if now.time() < MARKET_OPEN:
                    time.sleep(30)
                    continue
                logger.info("Market closed. Exiting stay-flat monitor.")
                # Save account state + EOD report on stay_flat days
                journal.save_account_state(account_value, 0.0)
                report = generate_daily_report(
                    trades=[], account_value=account_value, starting_value=account_value,
                    daily_pnl=0.0, regime=regime.regime.value, vix=vix,
                    kill_switch_triggered=False, rejected_trades=[],
                )
                save_daily_report(report, config["reporting"]["report_dir"])
                if config["notifications"]["enabled"]:
                    notifier.send(
                        f"🔒 <b>MARKET CLOSED</b>\n"
                        f"Regime: {regime.regime.value.upper()}  |  VIX: {vix:.1f}\n"
                        f"No trades today — stay flat day.\n"
                        f"Account: Rs.{account_value:,.0f}"
                    )
                break
            cur_slot = now.hour * 4 + now.minute // 15  # changes every 15 mins
            if config["notifications"]["enabled"] and cur_slot != last_status_slot:
                last_status_slot = cur_slot
                hourly_steps = []
                # Refresh VIX
                try:
                    prev_vix = vix
                    vix = data.get_index_data("INDIA_VIX").get("close", vix)
                    hourly_steps.append(f"📡 VIX checked: {vix:.1f} (was {prev_vix:.1f})")
                except Exception:
                    hourly_steps.append(f"📡 VIX check failed, using last: {vix:.1f}")
                # Regime reasoning
                hourly_steps.append(
                    f"📊 Regime: {regime.regime.value.upper()} — "
                    f"NIFTY {'above' if regime.above_200ema else 'below'} EMA200, "
                    f"{'above' if regime.above_50ema else 'below'} EMA50, "
                    f"slope {regime.ema50_slope_pct:+.2f}%, ADX {regime.adx:.0f}"
                )
                # VIX gate reasoning
                if vix >= 20:
                    hourly_steps.append(f"🚫 VIX {vix:.1f} >= 20 — trading halted (threshold: 20)")
                # Re-check regime — exit stay-flat if VIX has dropped enough
                if vix < 25:
                    logger.info(f"VIX dropped to {vix:.1f} — re-evaluating regime, restarting bot.")
                    if config["notifications"]["enabled"]:
                        notifier.send(
                            f"VIX dropped to {vix:.1f} — conditions improved.\n"
                            f"Restarting bot to scan for trades."
                        )
                    sys.exit(0)  # watchdog will restart the bot fresh
                # Regime gate reasoning
                if regime.recommendation == "stay_flat":
                    hourly_steps.append(f"🚫 Regime not bullish — no signal scan performed")
                hourly_steps.append(f"💤 Bot in STAY FLAT mode — no orders placed")
                notifier.send_hourly_status(
                    hour=now.strftime("%I:%M %p IST"),
                    regime=regime.regime.value,
                    vix=vix,
                    account_value=risk.account_value,
                    daily_pnl=0.0,
                    open_trades=[],
                    trades_today=0,
                    kill_switch=False,
                    steps=hourly_steps,
                )
                logger.info(f"Stay-flat 15-min status sent at {now.strftime('%H:%M')}")
            time.sleep(60)
        return

    regime_bullish = regime.regime.value in ("strong_bull", "weak_bull")
    rejected_trades = []
    force_exit_time = config["strategy"].get("force_exit_ist", "15:15")
    last_status_slot = -1
    hourly_steps = []  # accumulates steps each 15 mins, resets on new slot
    _data_failures: dict = {}  # sym -> (fail_count, last_fail_time)
    _last_data_time = time.time()  # pre-market watchlist build counts as fresh data

    # ── Main trading loop ─────────────────────────────────────────
    logger.info("Entering main trading loop...")

    while True:
        now = now_ist()

        # Market closed?
        if not is_market_open():
            # Before market open — wait instead of running EOD
            if now.time() < MARKET_OPEN:
                wait_secs = max(10, (
                    now.replace(hour=9, minute=15, second=0, microsecond=0) - now
                ).total_seconds())
                logger.info(f"Pre-market: waiting {wait_secs/60:.1f} min for market open at 09:15...")
                time.sleep(min(wait_secs, 60))
                continue
            logger.info("Market closed. Running EOD tasks.")
            break

        # Force exit time
        if now.strftime("%H:%M") >= force_exit_time:
            logger.info(f"Force exit time {force_exit_time} reached — closing all positions")
            order_mgr.emergency_exit_all("force_exit_eod")
            break

        # Kill switch check
        nifty_now = data.get_index_data("NIFTY50")
        kill, kill_reason = risk.check_market_kill_switch(
            nifty_chg_pct=nifty_now.get("change_pct", 0),
            vix=vix,
            api_errors=0,
            data_stale_min=int((time.time() - _last_data_time) / 60),
        )
        if kill or risk.kill_switch_active:
            reason = kill_reason if kill else "risk_engine_kill"
            logger.critical(f"KILL SWITCH: {reason}")
            audit.log_kill_switch(reason, risk.account_value, risk.daily_pnl)
            order_mgr.emergency_exit_all(reason)
            notifier.send_kill_switch(reason, risk.account_value, risk.daily_pnl)
            break

        # Refresh watchlist periodically
        cur_time_str = now.strftime("%H:%M")
        refresh_times = config["market_research"].get("watchlist_refresh_times_ist", ["09:20", "10:00", "14:00"])
        if cur_time_str in refresh_times:
            _, watchlist = watchlist_builder.build(
                data, nifty_daily, vix, config["market_research"], list(event_symbols)
            )
            hourly_steps.append(f"🔄 Watchlist refreshed at {cur_time_str} — {len(watchlist)} symbols qualified")
            logger.info(f"Watchlist refreshed at {cur_time_str}")

        # ── Look for signals ───────────────────────────────────────
        active_candidates = watchlist_builder.active_candidates
        from_dt = now - timedelta(days=config["data"].get("intraday_lookback_days", 3))

        # Always include ETF symbols for ETFMomentum strategy
        etf_symbols = config["market_research"].get("etf_symbols", [])
        active_symbols_set = {t.symbol for t in order_mgr.active_trades.values()}
        etf_candidates = [
            type("C", (), {"symbol": s, "price": 0, "score": 1.0})()
            for s in etf_symbols if s not in active_symbols_set
        ]
        all_candidates = list(active_candidates) + etf_candidates

        for candidate in all_candidates:
            sym = candidate.symbol

            if sym in {t.symbol for t in order_mgr.active_trades.values()}:
                continue

            fc, lt = _data_failures.get(sym, (0, 0))
            if fc >= 3 and time.time() - lt < 300:  # 5 min cooldown
                continue

            try:
                df_15m = data.get_historical(sym, "15m", from_dt, now)
                df_daily = data.get_historical(sym, "1d", now - timedelta(days=120), now)
                _last_data_time = time.time()
            except Exception as e:
                now_ts = time.time()
                fc, lt = _data_failures.get(sym, (0, 0))
                _data_failures[sym] = (fc + 1, now_ts)
                if fc + 1 >= 3:
                    logger.warning(f"Data fetch failed {sym} ({fc+1}x): {e} — skipping for 5 min")
                else:
                    logger.warning(f"Data fetch failed {sym}: {e}")
                audit.log_error("data_fetch", sym, {"error": str(e), "fail_count": fc + 1})
                continue

            charges_est = estimate_round_trip_charges(
                candidate.price, candidate.price,
                qty=max(1, int(risk.sizer.max_per_trade() / max(candidate.price, 1))),
                segment=Segment.EQUITY_INTRADAY,
            )

            for strategy in strategies:
                setup = strategy.generate_signal(
                    symbol=sym,
                    df_primary=df_15m,
                    df_daily=df_daily,
                    regime_bullish=regime_bullish,
                    capital_per_trade=risk.sizer.max_per_trade(),
                    charges_estimate=charges_est,
                )

                if not setup.is_valid or setup.signal.value == "no_trade":
                    rejected_trades.append({
                        "symbol": sym, "strategy": strategy.strategy_name,
                        "reason": setup.rejection_reason, "time": now.isoformat(),
                    })
                    hourly_steps.append(f"🔍 {sym} [{strategy.strategy_name}] — no signal ({setup.rejection_reason})")
                    continue

                hourly_steps.append(f"✨ {sym} [{strategy.strategy_name}] — signal found! Grade {setup.setup_quality}, RR {setup.reward_risk_ratio:.1f}")
                audit.log_signal(sym, strategy.strategy_name, setup.setup_quality, setup.reason)

                risk_check = risk.check_trade(
                    symbol=sym,
                    entry_price=setup.entry_price,
                    stop_loss=setup.stop_loss,
                    proposed_qty=max(1, int(risk.sizer.max_per_trade() / setup.entry_price)),
                    open_positions_value=sum(p.qty * p.current_price for p in broker.get_positions()),
                    setup_quality=setup.setup_quality,
                    charges_estimate=charges_est,
                    avg_daily_volume=int(candidate.score * 10000),
                )

                audit.log_risk_decision(sym, risk_check.decision.value,
                                         risk_check.reason, risk_check.adjusted_qty)

                if risk_check.decision.value != "approved":
                    hourly_steps.append(f"🚫 {sym} risk check REJECTED — {risk_check.reason}")

                if risk_check.decision.value == "approved":
                    from execution.trade_state_machine import TradeRecord
                    trade = TradeRecord(
                        symbol=sym,
                        strategy=strategy.strategy_name,
                        setup_quality=setup.setup_quality,
                        entry_price=setup.entry_price,
                        entry_qty=risk_check.adjusted_qty,
                        stop_loss=setup.stop_loss,
                        target_1=setup.target_1,
                        target_2=setup.target_2,
                        breakeven_trigger=setup.breakeven_trigger,
                        trailing_step=setup.trailing_step,
                        regime_at_entry=regime.regime.value,
                    )

                    if mode == "semi_auto":
                        logger.info(
                            f"\n{'='*50}\nSIGNAL: {sym} {setup.setup_quality}-grade\n"
                            f"Entry:₹{setup.entry_price}  SL:₹{setup.stop_loss}  "
                            f"T1:₹{setup.target_1}  T2:₹{setup.target_2}\n"
                            f"Qty:{risk_check.adjusted_qty}  RR:{setup.reward_risk_ratio}\n"
                            f"Press ENTER to execute, or any key+ENTER to skip...\n{'='*50}"
                        )
                        user_in = input()
                        if user_in.strip():
                            logger.info(f"User skipped {sym}")
                            continue

                    success = order_mgr.execute_entry(trade, setup.entry_price)
                    if success:
                        hourly_steps.append(f"✅ {sym} ORDER PLACED — qty={risk_check.adjusted_qty} @ Rs.{setup.entry_price:.2f}")
                        journal.save_trade(trade)
                        if config["notifications"]["enabled"]:
                            notifier.send_trade_entry(
                                sym, risk_check.adjusted_qty, setup.entry_price,
                                setup.stop_loss, setup.target_1, setup.target_2,
                                setup.setup_quality,
                            )
                        break  # One trade at a time per loop

        # ── Update open trade prices and manage exits ──────────────
        for trade_id, trade in list(order_mgr.active_trades.items()):
            try:
                quote = data.get_quote(trade.symbol)
                order_mgr.tick(trade.symbol, quote.ltp)
                if isinstance(broker, __import__("brokers.simulated_broker", fromlist=["SimulatedBroker"]).SimulatedBroker):
                    broker.update_position_price(trade.symbol, quote.ltp)
                journal.save_trade(trade)
            except Exception as e:
                logger.warning(f"Tick update failed {trade.symbol}: {e}")

        # Save account state
        journal.save_account_state(risk.account_value, risk.daily_pnl)

        # ── 15-min status report ──────────────────────────────────
        cur_slot = now.hour * 4 + now.minute // 15
        if config["notifications"]["enabled"] and cur_slot != last_status_slot:
            last_status_slot = cur_slot
            open_trades_info = [
                {
                    "symbol": t.symbol,
                    "qty": t.entry_qty,
                    "unrealised_pnl": (getattr(t, "current_price", t.entry_price) - t.entry_price) * t.entry_qty,
                }
                for t in order_mgr.active_trades.values()
            ]
            # Add summary step if nothing notable happened
            if not hourly_steps:
                hourly_steps.append(f"🔍 Scanned {len(watchlist_builder.active_candidates)} candidates — no valid signals found")
            notifier.send_hourly_status(
                hour=now.strftime("%I:%M %p IST"),
                regime=regime.regime.value,
                vix=vix,
                account_value=risk.account_value,
                daily_pnl=risk.daily_pnl,
                open_trades=open_trades_info,
                trades_today=len(order_mgr.completed_trades),
                kill_switch=risk.kill_switch_active,
                steps=hourly_steps,
            )
            hourly_steps = []  # reset for next hour
            logger.info(f"15-min status report sent at {now.strftime('%H:%M')}")

        # Sleep until next 15-min candle
        sleep_seconds = 60  # Check every minute; adapt to 15-min in production
        logger.debug(f"Sleeping {sleep_seconds}s | Open trades: {len(order_mgr.active_trades)}")
        time.sleep(sleep_seconds)

    # ── End of day ────────────────────────────────────────────────
    all_trades = [t.to_dict() for t in order_mgr.completed_trades]
    report = generate_daily_report(
        trades=all_trades,
        account_value=risk.account_value,
        starting_value=account_value,
        daily_pnl=risk.daily_pnl,
        regime=regime.regime.value,
        vix=vix,
        kill_switch_triggered=risk.kill_switch_active,
        rejected_trades=rejected_trades,
    )
    save_daily_report(report, config["reporting"]["report_dir"])
    print(f"\n{format_daily_report(report)}\n")

    if config["notifications"]["enabled"]:
        notifier.send_daily_summary(report)

    # Sync final account value from broker (live mode) so saved state matches Zerodha
    final_value = risk.account_value
    if mode == "live":
        try:
            live_margin = broker.get_available_margin()
            if live_margin > 0:
                final_value = live_margin
                logger.info(f"EOD account sync from Zerodha: Rs.{final_value:,.2f}")
        except Exception:
            pass
    journal.save_account_state(final_value, risk.daily_pnl)
    risk.reset_daily()
    pid_file = ROOT / "journaling" / "bot.pid"
    pid_file.unlink(missing_ok=True)
    logger.info("Bot session complete.")


if __name__ == "__main__":
    main()
