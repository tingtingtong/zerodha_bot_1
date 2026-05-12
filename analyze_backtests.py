import json
from pathlib import Path

files = sorted(Path('backtesting/results').glob('*.json'))
rows = []
for f in files:
    try:
        parts = f.stem.split('_')
        sym = parts[1]
        if parts[3] in ('2021','2023','2024','2025','2026'):
            strat = parts[2]; start, end = parts[3], parts[4]
        else:
            strat = parts[2]+'_'+parts[3]; start, end = parts[4], parts[5]
        d = json.loads(f.read_text())
        rows.append({'sym':sym,'strat':strat,'start':start,'end':end,'d':d})
    except:
        pass

BASE = 150000

PERIOD_LABEL = {
    '2026-01': '3 months  (Jan-Mar 2026)',
    '2025-01': '15 months (2025-Mar 2026)',
    '2023-01': '3 years   (2023-Mar 2026)',
    '2021-01': '5 years   (2021-Mar 2026)',
}

STRAT_SHORT = {'ema_pullback': 'EMA_Pull', 'mean_reversion': 'Mean_Rev', 'etf_momentum': 'ETF_Mom'}

hdr = f"{'Symbol':<11}{'Strategy':<11}{'Trades':>6}{'WR%':>6}{'PF':>5}  | {'10k':>8}{'50k':>9}{'1L':>9}{'1.5L':>9} | {'Sharpe':>7}{'MaxDD%':>7}{'Charges':>10}"
sep = '-' * 115

prev_period = ''
for r in sorted(rows, key=lambda x: (x['start'], x['sym'], x['strat'])):
    d   = r['d']
    pnl = d.get('net_pnl', 0)
    chg = d.get('total_charges', 0)
    wr  = d.get('win_rate', 0) * 100
    pf  = d.get('profit_factor', 0)
    tr  = d.get('total_trades', 0)
    sh  = d.get('sharpe_ratio', 0)
    dd  = d.get('max_drawdown_pct', 0)

    label = PERIOD_LABEL.get(r['start'][:7], f"{r['start'][:7]}>{r['end'][:7]}")
    if label != prev_period:
        print()
        print(f'  {"="*10} {label} {"="*10}')
        print(hdr)
        print(sep)
        prev_period = label

    # Scale P&L linearly with capital (position size proportional to capital)
    p10  = pnl * (10000  / BASE)
    p50  = pnl * (50000  / BASE)
    p100 = pnl * (100000 / BASE)
    p150 = pnl

    strat_s = STRAT_SHORT.get(r['strat'], r['strat'][:10])
    win_flag = '*' if pnl > 0 else ' '
    print(f"{win_flag}{r['sym']:<10}{strat_s:<11}{tr:>6}{wr:>6.1f}{pf:>5.2f}  | {p10:>+8.0f}{p50:>+9.0f}{p100:>+9.0f}{p150:>+9.0f} | {sh:>7.2f}{dd:>7.1f}{chg:>14.0f}")

print()
print('=' * 115)
print('SUMMARY — Best performers per period')
print('=' * 115)

# Best P&L per period
from collections import defaultdict
by_period = defaultdict(list)
for r in rows:
    label = PERIOD_LABEL.get(r['start'][:7], r['start'][:7])
    by_period[label].append(r)

for period, rs in sorted(by_period.items()):
    best = sorted(rs, key=lambda x: x['d'].get('net_pnl',0), reverse=True)[:3]
    print(f"\n  {period}")
    print(f"  {'Rank':<5}{'Symbol':<11}{'Strategy':<13}{'Win%':>6}{'@10k':>9}{'@50k':>9}{'@1L':>9}{'@1.5L':>10}{'Sharpe':>8}")
    for i, r in enumerate(best, 1):
        d   = r['d']
        pnl = d.get('net_pnl', 0)
        wr  = d.get('win_rate', 0) * 100
        sh  = d.get('sharpe_ratio', 0)
        strat_s = STRAT_SHORT.get(r['strat'], r['strat'][:12])
        print(f"  #{i:<4}{r['sym']:<11}{strat_s:<13}{wr:>6.1f}{pnl*(10000/BASE):>+9.0f}{pnl*(50000/BASE):>+9.0f}{pnl*(100000/BASE):>+9.0f}{pnl:>+10.0f}{sh:>8.2f}")

print()
print('P&L values scaled linearly from ₹1.5L base run. Sharpe/WR/PF are capital-independent.')
