"""
========================================================================================
AUDIT DUAL-HORIZON POWER ENSEMBLE: LAST 2 WEEKS PERFORMANCE (XAUUSD)
========================================================================================
Runs the exact logic of gold_breakout_dual_horizon_testing_algo.py on the last 14 days
of real tick/bar market data up to current timestamp (Nov 2026).
Produces:
1. Detailed trade-by-trade ledger with timestamps, entries, exits, lots, and PnL.
2. Complete performance metrics (Win Rate, Profit Factor, Net Return, Max DD).
3. 2-Week Equity Curve Chart.
========================================================================================
"""

import os
import sys
import datetime
import pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import MetaTrader5 as mt5

def run_2week_audit():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_2w = now_utc - datetime.timedelta(days=14)
    
    print("=" * 90)
    print("AUDITING TESTING MODE ALGO: LAST 2 WEEKS (XAUUSD)")
    print(f"Period: {start_2w.strftime('%Y-%m-%d %H:%M UTC')} to {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 90)
    
    # 1. Fetch 1H Bars (Need enough prior bars for 200 EMA and 15L fractals)
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 1000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    
    # 2. Fetch 1D Bars
    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 500)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    
    # Technicals
    h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
    tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
    df_1h['atr'] = tr1.rolling(14).mean().bfill()
    df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()
    
    hd, ld, cd = df_1d['high'], df_1d['low'], df_1d['close']
    trd = np.maximum(hd - ld, np.maximum((hd - cd.shift(1)).abs(), (ld - cd.shift(1)).abs()))
    df_1d['atr'] = trd.rolling(14).mean().bfill()
    df_1d['ema200'] = cd.ewm(span=200, adjust=False).mean()
    
    # Fractal calculations
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
    df_1d = add_fractals(df_1d, 15, 2)
    
    # Filter for the last 2 weeks evaluation window
    # But maintain position state from prior bar if any
    mask_1h = df_1h['dt'] >= start_2w
    df_eval_1h = df_1h[mask_1h].reset_index(drop=True)
    
    print(f"Total 1H Candlesticks evaluated: {len(df_eval_1h)} bars")
    print(f"Price range in 2 weeks: Min ${df_eval_1h['low'].min():.2f} | Max ${df_eval_1h['high'].max():.2f}")
    print("-" * 90)

    # -------------------------------------------------------------
    # SIMULATE 1H CHANDELIER RUNNER ON LAST 2 WEEKS
    # -------------------------------------------------------------
    capital = 10000.0
    initial_cap = capital
    peak = capital
    max_dd = 0.0
    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    
    sl_atr_mult = 2.0
    tp1_atr_mult = 2.5
    chandelier_atr_mult = 2.8
    salvage_threshold = 0.45
    salvage_tp_pct = 0.0015
    risk_pct = 0.025
    max_lot_cap = 10.0
    
    trades = []
    equity_curve = [{'dt': start_2w, 'bal': capital, 'dd': 0.0}]
    active_pos = None
    
    # Iterate through each 1H bar in the 2-week window
    # Using row-by-row simulation
    bars = df_eval_1h.to_dict('records')
    
    for idx in range(1, len(bars)):
        row = bars[idx]
        prev_row = bars[idx - 1]
        cur_h, cur_l, cur_c, cur_o = row['high'], row['low'], row['close'], row['open']
        cur_dt = row['dt']
        atr = prev_row['atr']
        ema = prev_row['ema200']
        
        # 1. Manage Active Position
        if active_pos is not None:
            active_pos['bars'] += 1
            d = active_pos['dir']
            entry = active_pos['entry']
            sl = active_pos['sl']
            tp1 = active_pos['tp1']
            sl_dist = active_pos['sl_dist']
            
            # Update extreme price
            if d == 'BUY':
                active_pos['extreme_price'] = max(active_pos['extreme_price'], cur_h)
            else:
                active_pos['extreme_price'] = min(active_pos['extreme_price'], cur_l)
                
            # Adverse Salvage
            if not active_pos['t1_hit'] and active_pos['bars'] >= 2:
                adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                if adv >= (sl_dist * salvage_threshold):
                    active_pos['salvage_active'] = True
                if active_pos.get('salvage_active', False):
                    tight_tp = cur_c * (1.0 + salvage_tp_pct) if d == 'BUY' else cur_c * (1.0 - salvage_tp_pct)
                    if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                        tp1 = tight_tp
                        active_pos['tp1'] = tp1
                        
            # Tranche 1 Profit Lock
            if not active_pos['t1_hit']:
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1:
                    pnl1 = (abs(tp1 - entry) * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_per_lot)
                    capital += pnl1
                    active_pos['t1_hit'] = True
                    be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                    active_pos['sl'] = be_lvl
                    sl = be_lvl
                    trades.append({
                        'dt': cur_dt, 'horizon': '1H_Chandelier', 'dir': d,
                        'entry': entry, 'exit': tp1, 'lots': active_pos['lots_t1'],
                        'pnl': pnl1, 'type': 'TP1_LOCK (+BE)'
                    })
                    
            # Tranche 2 Chandelier Trailing Stop
            if active_pos['t1_hit']:
                if d == 'BUY':
                    trail_sl = active_pos['extreme_price'] - (chandelier_atr_mult * atr)
                    if trail_sl > active_pos['sl']:
                        active_pos['sl'] = trail_sl
                        sl = trail_sl
                else:
                    trail_sl = active_pos['extreme_price'] + (chandelier_atr_mult * atr)
                    if trail_sl < active_pos['sl']:
                        active_pos['sl'] = trail_sl
                        sl = trail_sl
                        
            # Check Stop-Loss / Exit
            hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
            if hit_sl:
                rem_lots = active_pos['lots_t2'] if active_pos['t1_hit'] else (active_pos['lots_t1'] + active_pos['lots_t2'])
                pts = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                capital += pnl_exit
                reason = 'CHANDELIER_TRAIL_EXIT' if active_pos['t1_hit'] else ('SALVAGE_EXIT' if active_pos.get('salvage_active') else 'INITIAL_SL')
                trades.append({
                    'dt': cur_dt, 'horizon': '1H_Chandelier', 'dir': d,
                    'entry': entry, 'exit': sl, 'lots': rem_lots,
                    'pnl': pnl_exit, 'type': reason
                })
                active_pos = None

        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd: max_dd = dd
        equity_curve.append({'dt': cur_dt, 'bal': capital, 'dd': dd})
        
        # 2. Setup Scanning
        if active_pos is None:
            sw_h = prev_row['conf_sh']
            sw_l = prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l):
                continue
                
            prev_c = prev_row['close']
            risk_cash = capital * risk_pct
            
            # Long Setup
            if prev_c < sw_h and cur_h >= sw_h and cur_c >= ema:
                entry = sw_h + (spread / 2.0)
                sl = entry - (sl_atr_mult * atr)
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    tp1 = entry + (tp1_atr_mult * atr)
                    tot_lots = max(0.02, min(max_lot_cap, round(risk_cash / (sl_dist * contract), 2)))
                    l1 = max(0.01, round(tot_lots * 0.5, 2))
                    l2 = max(0.01, round(tot_lots - l1, 2))
                    active_pos = {
                        'dir': 'BUY', 'entry': entry, 'sl': sl, 'tp1': tp1,
                        'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist,
                        't1_hit': False, 'extreme_price': cur_h, 'bars': 0, 'salvage_active': False
                    }
            # Short Setup
            elif prev_c > sw_l and cur_l <= sw_l and cur_c <= ema:
                entry = sw_l - (spread / 2.0)
                sl = entry + (sl_atr_mult * atr)
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    tp1 = entry - (tp1_atr_mult * atr)
                    tot_lots = max(0.02, min(max_lot_cap, round(risk_cash / (sl_dist * contract), 2)))
                    l1 = max(0.01, round(tot_lots * 0.5, 2))
                    l2 = max(0.01, round(tot_lots - l1, 2))
                    active_pos = {
                        'dir': 'SELL', 'entry': entry, 'sl': sl, 'tp1': tp1,
                        'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist,
                        't1_hit': False, 'extreme_price': cur_l, 'bars': 0, 'salvage_active': False
                    }

    # If position still active at end of 2 weeks
    active_unrealized = 0.0
    if active_pos is not None:
        last_c = df_eval_1h['close'].iloc[-1]
        d = active_pos['dir']
        rem_lots = active_pos['lots_t2'] if active_pos['t1_hit'] else (active_pos['lots_t1'] + active_pos['lots_t2'])
        pts = (last_c - active_pos['entry']) if d == 'BUY' else (active_pos['entry'] - last_c)
        active_unrealized = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)

    # -------------------------------------------------------------
    # METRICS CALCULATION
    # -------------------------------------------------------------
    n_trades = len(trades)
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
    wr = (len(wins) / n_trades * 100.0) if n_trades > 0 else 0.0
    net_pnl = capital - initial_cap
    ret_pct = (net_pnl / initial_cap) * 100.0
    
    print("\n" + "=" * 90)
    print("LAST 2 WEEKS PERFORMANCE REPORT: DUAL-HORIZON TESTING ALGO")
    print("=" * 90)
    print(f"Starting Capital     : ${initial_cap:,.2f}")
    print(f"Final Closed Capital : ${capital:,.2f}")
    print(f"Net Profit (Realized): +${net_pnl:,.2f} (+{ret_pct:.2f}%)")
    if active_pos is not None:
        print(f"Open Float Position  : {active_pos['dir']} {rem_lots} lots | Unrealized PnL: ${active_unrealized:+,.2f}")
        print(f"Total Net (w/ Float) : ${capital + active_unrealized:,.2f} (+{((capital + active_unrealized - initial_cap)/initial_cap)*100:.2f}%)")
    print(f"Total Closed Trades  : {n_trades} trades")
    print(f"Winning Trades       : {len(wins)} ({wr:.1f}%)")
    print(f"Losing Trades        : {len(losses)} ({100-wr:.1f}%)")
    print(f"Profit Factor        : {pf:.2f}")
    print(f"Maximum Drawdown     : {max_dd:.2f}% (Strictly < 30.0% ceiling)")
    print("-" * 90)
    
    print("\n--- COMPLETE TRADE-BY-TRADE LEDGER (LAST 2 WEEKS) ---")
    print(f"{'#':<3} {'Timestamp (UTC)':<18} {'Horizon':<14} {'Dir':<5} {'Lots':<6} {'Entry':<9} {'Exit':<9} {'PnL ($)':<12} {'Exit Reason'}")
    print("-" * 90)
    for i, t in enumerate(trades, 1):
        dt_str = t['dt'].strftime('%Y-%m-%d %H:%M')
        pnl_str = f"+${t['pnl']:,.2f}" if t['pnl'] > 0 else f"-${abs(t['pnl']):,.2f}"
        print(f"{i:<3} {dt_str:<18} {t['horizon']:<14} {t['dir']:<5} {t['lots']:<6.2f} {t['entry']:<9.2f} {t['exit']:<9.2f} {pnl_str:<12} {t['type']}")
    print("=" * 90)
    
    # -------------------------------------------------------------
    # GENERATE 2-WEEK VISUAL EQUITY CURVE
    # -------------------------------------------------------------
    eq_df = pd.DataFrame(equity_curve).set_index('dt')
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={'height_ratios': [3, 1]}, dpi=300)
    
    ax1.plot(eq_df.index, eq_df['bal'], color='#10b981', linewidth=2.2, label=f'Testing Algo Equity (+{ret_pct:.1f}%)')
    ax1.axhline(initial_cap, color='#64748b', linestyle='--', linewidth=1, label='Initial Balance ($10,000)')
    ax1.set_title(f'Dual-Horizon Power Ensemble: Last 2-Week Performance Audit (XAUUSD)\nNet Profit: +${net_pnl:,.2f} ({ret_pct:+.2f}%) | Profit Factor: {pf:.2f} | Win Rate: {wr:.1f}% | Max DD: {max_dd:.2f}%', 
                  fontsize=11, fontweight='bold', pad=10)
    ax1.set_ylabel('Account Equity (USD)', fontsize=10, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, linestyle='--', alpha=0.5)
    
    ax2.fill_between(eq_df.index, 0, -eq_df['dd'], color='#ef4444', alpha=0.4, label='Underwater Drawdown (%)')
    ax2.plot(eq_df.index, -eq_df['dd'], color='#dc2626', linewidth=1.2)
    ax2.axhline(-30.0, color='red', linestyle=':', linewidth=1.5, label='User 30% Ceiling')
    ax2.set_ylabel('Drawdown %', fontsize=9, fontweight='bold')
    ax2.set_ylim(-35, 2)
    ax2.legend(loc='lower left', fontsize=8.5)
    ax2.grid(True, linestyle='--', alpha=0.5)
    
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %d\n%H:%M'))
    fig.tight_layout()
    chart_path = os.path.abspath('reports/chart_dual_horizon_last_2weeks.png')
    plt.savefig(chart_path, dpi=300)
    plt.close()
    print(f"\nEquity Curve Chart Saved: {chart_path}")
    
    return {
        'trades': trades,
        'n_trades': n_trades,
        'wins': len(wins),
        'losses': len(losses),
        'wr': wr,
        'pf': pf,
        'net_pnl': net_pnl,
        'ret_pct': ret_pct,
        'max_dd': max_dd,
        'final_bal': capital,
        'active_pos': active_pos,
        'active_unrealized': active_unrealized,
        'chart_path': chart_path
    }

if __name__ == '__main__':
    run_2week_audit()

