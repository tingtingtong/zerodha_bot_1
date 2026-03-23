# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ZerodhaBot is an automated intraday trading bot for Indian stock markets (NSE) using the Zerodha broker. It runs in paper, semi-auto, or live mode with multi-strategy signal generation, dynamic capital-tier risk management, and full trade journaling.

## Commands

### Running the Bot

```bash
# Paper trading (no real money)
python main.py --mode paper

# Semi-auto (bot signals, user approves each trade)
python main.py --mode semi_auto

# Live trading (real capital, auto-execute)
python main.py --mode live

# Full simulation with synthetic data (no broker required)
python simulate_trading.py
```

### PowerShell Shortcuts (Windows)
```powershell
.\start_paper.ps1        # Auto-login + dashboard + paper trading
.\start_semi_auto.ps1    # Auto-login + dashboard + semi-auto mode
.\run_backtest.ps1 -Symbol RELIANCE -Strategy ema_pullback -Start 2024-01-01 -End 2024-12-31 -Capital 50000
```

### Backtesting
```bash
python backtest_runner.py --symbol RELIANCE --strategy ema_pullback --start 2023-01-01 --end 2024-01-01 --capital 100000
```

### Dashboard
```bash
streamlit run dashboard/app.py   # Opens on http://localhost:8501
```

### Tests
```bash
pytest tests/
pytest tests/test_risk_engine.py   # Run a single test file
pytest tests/ --cov                # With coverage
```

### Setup
```bash
pip install -r requirements.txt
playwright install chromium         # Required for auto-login
cp .env.example .env                # Fill in credentials
```

## Architecture

### Execution Flow

1. **Pre-market**: `MarketRegimeDetector` analyzes NIFTY trend → `WatchlistBuilder` scores and ranks NIFTY-200 stocks → top 10 candidates selected
2. **Loop (every 60s)**: Strategies scan candidates → signals pass through `RiskEngine` → `OrderManager` executes approved trades
3. **Trade lifecycle**: Entry (limit) → SL placed → T1 partial exit (50% qty) + breakeven move → trailing stop → T2 full exit or SL/time exit
4. **EOD**: `DailyReport` generated, account state persisted, daily counters reset

### Key Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| Main loop | `main.py` | Orchestrates all components, pre-market to EOD |
| Risk engine | `risk/risk_engine.py` | Kill switches, position sizing, daily/weekly limits |
| Capital tiers | `config/capital_tiers.py` | 5 tiers (Nano ₹10k → Large ₹2M+) with percentage-based limits |
| Order manager | `execution/order_manager.py` | Entry/exit logic, partial exits, trailing stops |
| Trade state machine | `execution/trade_state_machine.py` | `SIGNAL → ENTRY_ORDERED → ENTRY_FILLED → SL_PLACED → CLOSED_*` |
| Market regime | `research/market_regime.py` | NIFTY EMA/ADX analysis → Strong Bull / Sideways / Bear |
| Strategies | `strategies/` | `ema_pullback.py`, `etf_momentum.py`; all extend `BaseStrategy` |
| Brokers | `brokers/` | `SimulatedBroker` (paper), `ZerodhaBroker` (live); both extend `BrokerBase` |
| Data providers | `data_providers/` | Fallback chain: Zerodha → yfinance; extend `DataProviderBase` |
| Trade journal | `journaling/trade_journal.py` | Persists trades to JSON by date |
| Audit logger | `journaling/audit_logger.py` | JSONL event log of signals, risk decisions, orders |

### Critical Design Constraints

- **All risk/position parameters must be percentage-based — never hardcoded INR amounts.** Capital tiers (`config/capital_tiers.py`) auto-detect tier from account value and provide all limits as percentages. This is a core design invariant.
- VIX ≥ 20 → halt new trades; VIX ≥ 30 → kill switch (close all positions)
- NIFTY daily fall ≥ -1.5% → halt; ≥ -2.5% → kill switch
- 3 consecutive losses → pause; 2 consecutive losses → 50% position size

### Adding a New Strategy

1. Create `strategies/your_strategy.py` extending `BaseStrategy`
2. Implement `generate_signal(symbol, candles)` returning `SignalResult`
3. Register in `strategies/strategy_registry.py`
4. Enable in `config/config.yaml` under `strategy.active_strategies`

### Data Provider Fallback

Configured via `config.yaml` `data.provider_mode`:
- `free_only`: yfinance only (no credentials needed)
- `zerodha`: Zerodha historical API only
- `zerodha_with_fallback`: Zerodha first, falls back to yfinance on error

### Configuration

All bot behavior is controlled by `config/config.yaml`. Key sections:
- `bot.mode`: paper / semi_auto / live
- `risk`: per-trade risk %, daily loss limit, min R:R, consecutive loss halt
- `strategy.trading_hours`: default 9:45–14:45 IST
- `market_research.universe`: default NIFTY_200

Environment variables (`.env`): `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, `ZERODHA_USER_ID`, `ZERODHA_PASSWORD`, `ZERODHA_TOTP_SECRET`, `TELEGRAM_BOT_TOKEN` (optional)

### Auto-Login Warning

Auto-login (`brokers/zerodha_auto_login.py`) uses Playwright to authenticate via TOTP. The TOTP secret in `.env` must match the seed used by your authenticator app. **Verify `ZERODHA_TOTP_SECRET` before running** — incorrect attempts can trigger account lockout.

### Output Paths

- Trades: `journaling/logs/trades_YYYY-MM-DD.json`
- Audit: `journaling/logs/audit_YYYY-MM-DD.jsonl`
- Reports: `reporting/output/report_YYYY-MM-DD.json`
- Backtest results: `backtesting/results/backtest_SYMBOL_STRATEGY_START_END.json`
- Zerodha token: `config/.zerodha_token.json` (24-hour validity)
