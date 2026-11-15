import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from math_model.test_split_target_quantum_engine import load_data
import datetime, pandas as pd, numpy as np

sessions = load_data()

spread = 0.30
comm_per_lot = 6.00
slip_out = 0.08
contract_sz = 100.0

all_dates = sorted(list(sessions.keys()))
records = []

for risk_pct in [0.025, 0.030]:
    for sl_mult in [2.0, 2.5]:
        for tp1_r in [1.2, 1.4]:
            for tp2_r in [2.5, 3.0]:
                for vf in [5.0, 7.0]:
                    balance = 10000.0
                    daily_records = []
                    
                    for curr_d in all_dates:
                        bars = sessions[curr_d]
                        day_pnl = 0.0
                        
                        if bars[0]['asian_range'] < vf:
                            daily_records.append({'date': curr_d, 'pnl_pct': 0.0})
                            continue
                            
                        c = {'h': 12, 'm': 0, 'cutoff_h': 13, 'cutoff_m': 0, 'state': 'WAIT_ANCHOR', 'base': None, 'dir': None, 'limit_p': None, 'sl_dist': None, 'done': False}
                        active_positions = []
                        
                        for i, b in enumerate(bars):
                            h = b['ist_hour']
                            m = b['ist_minute']
                            
                            if h == 15 and m >= 55:
                                for pos in active_positions:
                                    ttype = pos['type']
                                    exit_p = (b['close'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (b['close'] + (spread/2.0) + slip_out)
                                    net_pts = (exit_p - pos['fill_p']) if ttype == 'BUY' else (pos['fill_p'] - exit_p)
                                    pnl = (net_pts * pos['lots'] * contract_sz) - pos['comm']
                                    day_pnl += pnl
                                active_positions = []
                                break
                                
                            if day_pnl <= -(balance * 0.025):
                                for pos in active_positions:
                                    ttype = pos['type']
                                    exit_p = (b['close'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (b['close'] + (spread/2.0) + slip_out)
                                    net_pts = (exit_p - pos['fill_p']) if ttype == 'BUY' else (pos['fill_p'] - exit_p)
                                    pnl = (net_pts * pos['lots'] * contract_sz) - pos['comm']
                                    day_pnl += pnl
                                active_positions = []
                                break
                                
                            remaining = []
                            for pos in active_positions:
                                ttype = pos['type']
                                hit_tp = (b['high'] >= pos['tp']) if ttype == 'BUY' else (b['low'] <= pos['tp'])
                                hit_sl = (b['low'] <= pos['sl']) if ttype == 'BUY' else (b['high'] >= pos['sl'])
                                
                                if hit_tp:
                                    exit_p = (pos['tp'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (pos['tp'] + (spread/2.0) + slip_out)
                                    net_pts = (exit_p - pos['fill_p']) if ttype == 'BUY' else (pos['fill_p'] - exit_p)
                                    pnl = (net_pts * pos['lots'] * contract_sz) - pos['comm']
                                    day_pnl += pnl
                                    if pos['tranche'] == 1:
                                        for other in active_positions:
                                            if other['tranche'] == 2:
                                                other['sl'] = other['fill_p'] + (0.10 if ttype == 'BUY' else -0.10)
                                elif hit_sl:
                                    exit_p = (pos['sl'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (pos['sl'] + (spread/2.0) + slip_out)
                                    net_pts = (exit_p - pos['fill_p']) if ttype == 'BUY' else (pos['fill_p'] - exit_p)
                                    pnl = (net_pts * pos['lots'] * contract_sz) - pos['comm']
                                    day_pnl += pnl
                                else:
                                    remaining.append(pos)
                            active_positions = remaining
                            
                            if not c['done']:
                                if h > c['cutoff_h'] or (h == c['cutoff_h'] and m >= c['cutoff_m']):
                                    c['done'] = True
                                    continue
                                if c['state'] == 'WAIT_ANCHOR' and h == c['h'] and m == c['m']:
                                    c['base'] = b['close']
                                    c['ema'] = b['ema200']
                                    c['atr'] = max(1.20, b['atr'])
                                    c['state'] = 'WAIT_BREAK'
                                    continue
                                if c['state'] == 'WAIT_BREAK':
                                    base = c['base']
                                    atr = c['atr']
                                    ema = c['ema']
                                    break_dist = 1.4 * atr
                                    retest_dist = 0.6 * atr
                                    sl_dist = max(4.50, sl_mult * atr)
                                    c['sl_dist'] = sl_dist
                                    if base >= ema and b['high'] >= base + break_dist:
                                        c['state'] = 'WAIT_LIMIT_FILL'
                                        c['dir'] = 'BUY'
                                        c['limit_p'] = base + retest_dist
                                    elif base <= ema and b['low'] <= base - break_dist:
                                        c['state'] = 'WAIT_LIMIT_FILL'
                                        c['dir'] = 'SELL'
                                        c['limit_p'] = base - retest_dist
                                elif c['state'] == 'WAIT_LIMIT_FILL':
                                    lp = c['limit_p']
                                    sl_dist = c['sl_dist']
                                    cdir = c['dir']
                                    filled = False
                                    if cdir == 'BUY' and b['low'] <= lp:
                                        fill_p = lp + (spread / 2.0)
                                        filled = True
                                    elif cdir == 'SELL' and b['high'] >= lp:
                                        fill_p = lp - (spread / 2.0)
                                        filled = True
                                    if filled:
                                        c['done'] = True
                                        risk_per_tranche = (balance * (risk_pct / 2.0))
                                        raw_lots = risk_per_tranche / (sl_dist * contract_sz)
                                        lots = max(0.01, round(raw_lots, 2))
                                        comm = lots * comm_per_lot
                                        sl_price = (fill_p - sl_dist) if cdir == 'BUY' else (fill_p + sl_dist)
                                        tp1_price = (fill_p + sl_dist * tp1_r) if cdir == 'BUY' else (fill_p - sl_dist * tp1_r)
                                        tp2_price = (fill_p + sl_dist * tp2_r) if cdir == 'BUY' else (fill_p - sl_dist * tp2_r)
                                        active_positions.append({'tranche': 1, 'type': cdir, 'fill_p': fill_p, 'sl': sl_price, 'tp': tp1_price, 'lots': lots, 'comm': comm})
                                        active_positions.append({'tranche': 2, 'type': cdir, 'fill_p': fill_p, 'sl': sl_price, 'tp': tp2_price, 'lots': lots, 'comm': comm})

                        max_daily_loss = -(balance * 0.025)
                        if day_pnl < max_daily_loss:
                            day_pnl = max_daily_loss
                        balance += day_pnl
                        daily_records.append({'date': curr_d, 'pnl_pct': (day_pnl / (balance - day_pnl)) if (balance - day_pnl) > 0 else 0.0})

                    df_d = pd.DataFrame(daily_records)
                    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
                    annual = df_d.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
                    equity = (1.0 + df_d['pnl_pct']).cumprod()
                    max_dd = ((equity - equity.cummax()) / equity.cummax()).min() * 100.0
                    
                    records.append({
                        'risk': risk_pct * 100,
                        'sl_mult': sl_mult,
                        'tp1': tp1_r,
                        'tp2': tp2_r,
                        'vf': vf,
                        'roi': annual.mean(),
                        'worst_yr': annual.min(),
                        'max_dd': max_dd,
                        'final_bal': balance,
                        'annual_dict': annual.to_dict()
                    })

df_res = pd.DataFrame(records).sort_values(by='roi', ascending=False)
print("=" * 140)
print(f"{'CONFIGURATION':<55} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'FINAL BAL'}")
print("-" * 140)
for idx, r in df_res.head(15).iterrows():
    cfg = f"Risk {r['risk']:.1f}% | SL {r['sl_mult']:.1f}x | TP1 {r['tp1']:.1f}R | TP2 {r['tp2']:.1f}R | VF ${r['vf']:.0f}"
    print(f"{cfg:<55} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | ${r['final_bal']:,.2f}")
print("=" * 140)

best = df_res.iloc[0]
print("\nBEST CONFIGURATION YEAR-BY-YEAR:")
for y, v in best['annual_dict'].items():
    print(f"  {y}: {v:+.2f}%")
