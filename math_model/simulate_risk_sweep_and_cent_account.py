"""
========================================================================================
SIMULATE RISK SWEEP & CENT ACCOUNT DYNAMICS: 10:00 TO 16:00 IST
========================================================================================
Answers:
1. Cent Account (10k Cents = $100 USD): exact balance trajectory, dollar drawdown, lot sizing.
2. Exact Risk Right Now: 2.5% base nominal risk with Half-Kelly de-leveraging.
3. Risk Sweep: What happens if we scale risk to 1.0%, 1.5%, 2.0%, 2.5%, 3.5%, 5.0%, 7.0%?
========================================================================================
"""

import os
import random
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def run_risk_sweep():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    ist = pytz.timezone('Asia/Kolkata')
    
    start_date = pd.to_datetime("2024-11-14 00:00:00", utc=True)
    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
    
    # 1. Fetch 1H Bars
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 18000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt_utc'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    df_1h['dt_ist'] = df_1h['dt_utc'].dt.tz_convert(ist)
    df_1h['hour_ist'] = df_1h['dt_ist'].dt.hour
    
    h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
    tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
    df_1h['atr'] = tr1.rolling(14).mean().bfill()
    df_1h['atr_ma'] = df_1h['atr'].rolling(30).mean().bfill()
    df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()

    def add_fractals(df, x_left, y_right):
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)
        sh_arr = np.full(n, np.nan)
        sl_arr = np.full(n, np.nan)
        for i in range(x_left, n - y_right):
            hc = highs[i]
            lc = lows[i]
            if all(highs[i - k] < hc for k in range(1, x_left + 1)) and all(highs[i + k] < hc for k in range(1, y_right + 1)):
                sh_arr[i + y_right] = hc
            if all(lows[i - k] > lc for k in range(1, x_left + 1)) and all(lows[i + k] > lc for k in range(1, y_right + 1)):
                sl_arr[i + y_right] = lc
        df['conf_sh'] = pd.Series(sh_arr).ffill()
        df['conf_sl'] = pd.Series(sl_arr).ffill()
        return df

    df_1h = add_fractals(df_1h, 15, 3)
    df_eval_1h = df_1h[(df_1h['dt_utc'] >= start_date) & (df_1h['dt_utc'] <= end_date)].reset_index(drop=True)

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    max_lot_cap = 10.0

    def simulate_ist(base_risk_pct=0.025, mode='STRICT_FLAT', init_balance=10000.0, is_cent=False):
        random.seed(42)
        cap = init_balance
        peak = cap
        max_dd = 0.0
        trades = []
        eq_curve = []
        
        pos = None
        pending = None
        last_f = None
        
        bars = df_eval_1h.to_dict('records')
        for idx in range(1, len(bars)):
            row = bars[idx]
            prev_row = bars[idx - 1]
            cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt_utc']
            cur_hour_ist = row['hour_ist']
            atr, atr_ma, ema = prev_row['atr'], prev_row['atr_ma'], prev_row['ema200']
            is_exp = (atr > 1.4 * atr_ma)
            
            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))
            
            # Position management
            if pos is not None:
                if mode == 'STRICT_FLAT' and cur_hour_ist >= 16:
                    d = pos['dir']
                    exit_p = cur_c
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (exit_p - pos['entry']) if d == 'BUY' else (pos['entry'] - exit_p)
                    pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_exit
                    trades.append(pnl_exit)
                    pos = None
                else:
                    pos['bars'] += 1
                    d = pos['dir']
                    entry, sl, tp1, sl_dist = pos['entry'], pos['sl'], pos['tp1'], pos['sl_dist']
                    
                    if d == 'BUY': pos['extreme'] = max(pos['extreme'], cur_h)
                    else: pos['extreme'] = min(pos['extreme'], cur_l)
                    
                    # Salvage
                    if not pos['t1_hit'] and pos['bars'] >= 2:
                        adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                        if adv >= (sl_dist * 0.45):
                            pos['salvage_active'] = True
                        if pos.get('salvage_active', False):
                            tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                            if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                                tp1 = tight_tp
                                pos['tp1'] = tp1
                                
                    # TP1 Hit
                    if not pos['t1_hit']:
                        hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                        if hit_tp1:
                            pnl1 = (abs(tp1 - entry) * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm_per_lot)
                            cap += pnl1
                            pos['t1_hit'] = True
                            be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                            pos['sl'] = be_lvl
                            sl = be_lvl
                            trades.append(pnl1)
                            
                    # Chandelier Trail
                    if pos['t1_hit']:
                        trail_sl = pos['extreme'] - (2.8 * atr) if d == 'BUY' else pos['extreme'] + (2.8 * atr)
                        if (d == 'BUY' and trail_sl > pos['sl']) or (d == 'SELL' and trail_sl < pos['sl']):
                            pos['sl'] = trail_sl
                            sl = trail_sl
                            
                    # Stop Loss
                    hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                    if hit_sl:
                        slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                        exit_p = sl - slip if d == 'BUY' else sl + slip
                        rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                        pts = (exit_p - entry) if d == 'BUY' else (entry - exit_p)
                        pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                        cap += pnl_exit
                        trades.append(pnl_exit)
                        pos = None

            if cap > peak: peak = cap
            dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd: max_dd = dd
            eq_curve.append({'dt': cur_dt, 'bal': cap, 'dd': dd})
            
            # Setup detection (10:00 to 16:00 IST window)
            if not (10 <= cur_hour_ist < 16):
                pending = None
                continue
                
            sw_h, sw_l = prev_row['conf_sh'], prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l): continue
            
            if pos is None and r_mult > 0.0:
                t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                t_f = sw_h if t_dir == 'BUY' else sw_l
                
                if pending is not None and (pending['dir'] != t_dir or abs(pending['f'] - t_f) > 0.10):
                    pending = None
                    
                if pending is None and t_dir is not None and last_f != t_f:
                    r_cash = cap * (base_risk_pct * r_mult)
                    entry_t = sw_h if t_dir == 'BUY' else sw_l
                    sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)
                    if sl_dist > 0:
                        tot_l = max(0.02, min(max_lot_cap, round(r_cash / (sl_dist * contract), 2)))
                        pending = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': round(tot_l * 0.5, 2), 'l2': round(tot_l - round(tot_l * 0.5, 2), 2)}
                        
                if pending is not None:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    if (pending['dir'] == 'BUY' and cur_h >= (pending['trig'] + spread)) or (pending['dir'] == 'SELL' and cur_l <= (pending['trig'] - spread)):
                        exec_p = (pending['trig'] + (spread / 2.0) + slip) if pending['dir'] == 'BUY' else (pending['trig'] - (spread / 2.0) - slip)
                        pos = {'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'], 'tp1': pending['tp1'], 'sl_dist': pending['sl_dist'], 'lots_t1': pending['l1'], 'lots_t2': pending['l2'], 't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False}
                        last_f = pending['f']
                        pending = None

        n = len(trades)
        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p < 0]
        gw = sum(wins) if wins else 0.0
        gl = abs(sum(losses)) if losses else 0.0
        pf = (gw / gl) if gl > 0 else 99.0
        wr = (len(wins) / n * 100.0) if n > 0 else 0.0
        cagr = ((max(1.0, cap) / init_balance) ** (1.0 / 2.0) - 1.0) * 100.0
        calmar = cagr / max_dd if max_dd > 0 else 0.0
        
        return {
            'risk_pct': base_risk_pct * 100.0,
            'final_bal': cap,
            'net_pnl': cap - init_balance,
            'cagr': cagr,
            'max_dd': max_dd,
            'pf': pf,
            'wr': wr,
            'n_trades': n,
            'calmar': calmar
        }

    print("=" * 100)
    print("PART 1: RISK SWEEP ANALYSIS (10:00 TO 16:00 IST - STRICT FLAT MODE A)")
    print("=" * 100)
    
    risk_levels = [0.010, 0.015, 0.020, 0.025, 0.035, 0.050, 0.075, 0.100]
    
    print(f"{'Risk %':<8} | {'Final Balance ($)':<18} | {'Net Profit ($)':<16} | {'CAGR %':<10} | {'Max DD %':<10} | {'Profit Factor':<14} | {'Calmar Ratio':<12} | {'Mandate <= 30%'}")
    print("-" * 115)
    for r in risk_levels:
        res = simulate_ist(base_risk_pct=r, mode='STRICT_FLAT', init_balance=10000.0)
        status = "PASSED ✅" if res['max_dd'] <= 30.0 else "BREACHED ❌"
        print(f"{res['risk_pct']:<7.1f}% | ${res['final_bal']:<17,.2f} | ${res['net_pnl']:<15,.2f} | +{res['cagr']:<9.1f}% | {res['max_dd']:<9.2f}% | {res['pf']:<14.2f} | {res['calmar']:<12.1f} | {status}")

    print("\n" + "=" * 100)
    print("PART 2: RISK SWEEP ANALYSIS (10:00 TO 16:00 IST - SESSION ENTRY MODE B)")
    print("=" * 100)
    print(f"{'Risk %':<8} | {'Final Balance ($)':<18} | {'Net Profit ($)':<16} | {'CAGR %':<10} | {'Max DD %':<10} | {'Profit Factor':<14} | {'Calmar Ratio':<12} | {'Mandate <= 30%'}")
    print("-" * 115)
    for r in risk_levels:
        res = simulate_ist(base_risk_pct=r, mode='SESSION_ENTRY', init_balance=10000.0)
        status = "PASSED ✅" if res['max_dd'] <= 30.0 else "BREACHED ❌"
        print(f"{res['risk_pct']:<7.1f}% | ${res['final_bal']:<17,.2f} | ${res['net_pnl']:<15,.2f} | +{res['cagr']:<9.1f}% | {res['max_dd']:<9.2f}% | {res['pf']:<14.2f} | {res['calmar']:<12.1f} | {status}")

    print("\n" + "=" * 100)
    print("PART 3: 10,000 CENT ACCOUNT ($100 USD REAL MONEY) PERFORMANCE")
    print("=" * 100)
    # Cent account: initial 10,000 cents = $100.00 USD
    res_cent_flat = simulate_ist(base_risk_pct=0.025, mode='STRICT_FLAT', init_balance=10000.0, is_cent=True)
    res_cent_hold = simulate_ist(base_risk_pct=0.025, mode='SESSION_ENTRY', init_balance=10000.0, is_cent=True)
    
    print(f"Starting Capital: 10,000 Cents = $100.00 USD Real Money")
    print(f"Risk per trade  : 2.5% = 250 Cents = $2.50 USD per trade")
    print(f"Mode A (Strict Flat @ 15:55 IST):")
    print(f"  Final Balance : {res_cent_flat['final_bal']:,.2f} Cents = ${res_cent_flat['final_bal']/100:,.2f} USD")
    print(f"  Net Profit    : +{res_cent_flat['net_pnl']:,.2f} Cents = +${res_cent_flat['net_pnl']/100:,.2f} USD (+{res_cent_flat['cagr']:.1f}% CAGR)")
    print(f"  Max Drawdown  : {res_cent_flat['max_dd']:.2f}% = Only -${(100.0 * res_cent_flat['max_dd']/100):.2f} USD peak cash drawdown!")
    print(f"Mode B (Session Entry Hold/Trail):")
    print(f"  Final Balance : {res_cent_hold['final_bal']:,.2f} Cents = ${res_cent_hold['final_bal']/100:,.2f} USD")
    print(f"  Net Profit    : +{res_cent_hold['net_pnl']:,.2f} Cents = +${res_cent_hold['net_pnl']/100:,.2f} USD (+{res_cent_hold['cagr']:.1f}% CAGR)")
    print(f"  Max Drawdown  : {res_cent_hold['max_dd']:.2f}% = Only -${(100.0 * res_cent_hold['max_dd']/100):.2f} USD peak cash drawdown!")
    print("=" * 100)

    mt5.shutdown()

if __name__ == '__main__':
    run_risk_sweep()
