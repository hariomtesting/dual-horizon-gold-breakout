"""
========================================================================================
GENERATE 10:00 TO 16:00 IST DEDICATED AUDIT CHART (NORMAL CORPORATE STYLE)
========================================================================================
"""

import os
import random
import datetime
import pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
import MetaTrader5 as mt5

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports'))
os.makedirs(REPORTS_DIR, exist_ok=True)

def generate_ist_chart():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    ist = pytz.timezone('Asia/Kolkata')
    
    start_date = pd.to_datetime("2024-11-14 00:00:00", utc=True)
    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
    
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

    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 1200)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    hd, ld, cd = df_1d['high'], df_1d['low'], df_1d['close']
    trd = np.maximum(hd - ld, np.maximum((hd - cd.shift(1)).abs(), (ld - cd.shift(1)).abs()))
    df_1d['atr'] = trd.rolling(14).mean().bfill()
    df_1d['ema200'] = cd.ewm(span=200, adjust=False).mean()
    df_1d = add_fractals(df_1d, 15, 2)

    df_eval_1h = df_1h[(df_1h['dt_utc'] >= start_date) & (df_1h['dt_utc'] <= end_date)].reset_index(drop=True)
    df_eval_1d = df_1d[(df_1d['dt'] >= start_date) & (df_1d['dt'] <= end_date)].reset_index(drop=True)

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    max_lot_cap = 10.0

    def run_sim(time_filter=None, force_flat_at_16=False, include_macro=False):
        random.seed(42)
        cap = 10000.0
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
            
            if pos is not None:
                if force_flat_at_16 and cur_hour_ist >= 16:
                    d = pos['dir']
                    exit_p = cur_c
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (exit_p - pos['entry']) if d == 'BUY' else (pos['entry'] - exit_p)
                    pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_exit
                    trades.append({'dt': cur_dt, 'pnl': pnl_exit, 'type': 'FLAT_1600_IST'})
                    pos = None
                else:
                    pos['bars'] += 1
                    d = pos['dir']
                    entry, sl, tp1, sl_dist = pos['entry'], pos['sl'], pos['tp1'], pos['sl_dist']
                    
                    if d == 'BUY': pos['extreme'] = max(pos['extreme'], cur_h)
                    else: pos['extreme'] = min(pos['extreme'], cur_l)
                    
                    if not pos['t1_hit'] and pos['bars'] >= 2:
                        adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                        if adv >= (sl_dist * 0.45):
                            pos['salvage_active'] = True
                        if pos.get('salvage_active', False):
                            tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                            if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                                tp1 = tight_tp
                                pos['tp1'] = tp1
                                
                    if not pos['t1_hit']:
                        hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                        if hit_tp1:
                            pnl1 = (abs(tp1 - entry) * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm_per_lot)
                            cap += pnl1
                            pos['t1_hit'] = True
                            be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                            pos['sl'] = be_lvl
                            sl = be_lvl
                            trades.append({'dt': cur_dt, 'pnl': pnl1, 'type': 'TP1'})
                            
                    if pos['t1_hit']:
                        trail_sl = pos['extreme'] - (2.8 * atr) if d == 'BUY' else pos['extreme'] + (2.8 * atr)
                        if (d == 'BUY' and trail_sl > pos['sl']) or (d == 'SELL' and trail_sl < pos['sl']):
                            pos['sl'] = trail_sl
                            sl = trail_sl
                            
                    hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                    if hit_sl:
                        slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                        exit_p = sl - slip if d == 'BUY' else sl + slip
                        rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                        pts = (exit_p - entry) if d == 'BUY' else (entry - exit_p)
                        pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                        cap += pnl_exit
                        trades.append({'dt': cur_dt, 'pnl': pnl_exit, 'type': 'SL'})
                        pos = None

            if cap > peak: peak = cap
            dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd: max_dd = dd
            eq_curve.append({'dt': cur_dt, 'bal': cap, 'dd': dd})
            
            if time_filter is not None:
                start_h, end_h = time_filter
                if not (start_h <= cur_hour_ist < end_h):
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
                    r_cash = cap * (0.025 * r_mult)
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

        if include_macro and not force_flat_at_16:
            bars_d1 = df_eval_1d.to_dict('records')
            pos_1d = None
            for idx in range(1, len(bars_d1)):
                row = bars_d1[idx]
                prev_row = bars_d1[idx - 1]
                cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt']
                ema = prev_row['ema200']
                
                if pos_1d is not None:
                    d, entry, sl, tp = pos_1d['dir'], pos_1d['entry'], pos_1d['sl'], pos_1d['tp']
                    fav = (cur_h - entry) if d == 'BUY' else (entry - cur_l)
                    if not pos_1d.get('be_locked') and fav >= (entry * 0.0030):
                        pos_1d['be_locked'] = True
                        pos_1d['sl'] = entry + 0.15 if d == 'BUY' else entry - 0.15
                        sl = pos_1d['sl']
                        
                    if (cur_h >= tp if d == 'BUY' else cur_l <= tp):
                        pnl = (abs(tp - entry) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                        cap += pnl
                        trades.append({'dt': cur_dt, 'pnl': pnl, 'type': 'MACRO_TP'})
                        pos_1d = None
                        continue
                    if (cur_l <= sl if d == 'BUY' else cur_h >= sl):
                        pnl = ((sl - entry if d == 'BUY' else entry - sl) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                        cap += pnl
                        trades.append({'dt': cur_dt, 'pnl': pnl, 'type': 'MACRO_SL'})
                        pos_1d = None
                        continue
                        
                sw_hd, sw_ld = prev_row['conf_sh'], prev_row['conf_sl']
                if pd.isna(sw_hd) or pd.isna(sw_ld): continue
                
                if pos_1d is None:
                    t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                    if t_dir == 'BUY' and prev_row['close'] < sw_hd and cur_h >= sw_hd:
                        e_p = sw_hd + (spread / 2.0)
                        sl = e_p * (1.0 - 0.0044)
                        tp = e_p * (1.0 + 0.0310)
                        sl_dist = abs(e_p - sl)
                        lots = max(0.01, min(max_lot_cap, round((cap * 0.030) / (sl_dist * contract), 2)))
                        pos_1d = {'dir': 'BUY', 'entry': e_p, 'sl': sl, 'tp': tp, 'lots': lots, 'be_locked': False}
                    elif t_dir == 'SELL' and prev_row['close'] > sw_ld and cur_l <= sw_ld:
                        e_p = sw_ld - (spread / 2.0)
                        sl = e_p * (1.0 + 0.0044)
                        tp = e_p * (1.0 - 0.0310)
                        sl_dist = abs(sl - e_p)
                        lots = max(0.01, min(max_lot_cap, round((cap * 0.030) / (sl_dist * contract), 2)))
                        pos_1d = {'dir': 'SELL', 'entry': e_p, 'sl': sl, 'tp': tp, 'lots': lots, 'be_locked': False}

        eq_df = pd.DataFrame(eq_curve).set_index('dt')
        return eq_df, max_dd, cap, trades

    eq_24h, dd_24h, bal_24h, t_24h = run_sim(time_filter=None, force_flat_at_16=False, include_macro=True)
    eq_ist_flat, dd_ist_flat, bal_ist_flat, t_ist_flat = run_sim(time_filter=(10, 16), force_flat_at_16=True)
    eq_ist_hold, dd_ist_hold, bal_ist_hold, t_ist_hold = run_sim(time_filter=(10, 16), force_flat_at_16=False)

    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(14, 8.8), dpi=300)
    fig.patch.set_facecolor('#ffffff')

    for ax in [ax1, ax2, ax3, ax4]:
        ax.set_facecolor('#ffffff')
        ax.grid(True, linestyle='--', alpha=0.55, color='#cbd5e1')
        for s in ax.spines.values():
            s.set_color('#64748b')
            s.set_linewidth(0.8)

    # Panel 1: Compounded Equity Growth (10:00 to 16:00 IST Focus)
    ax1.plot(eq_24h.index, eq_24h['bal'], color='#94a3b8', linewidth=1.5, linestyle=':', label=f'24-Hour Benchmark (${bal_24h:,.0f})')
    ax1.plot(eq_ist_hold.index, eq_ist_hold['bal'], color='#059669', linewidth=2.4, label=f'10:00-16:00 IST Session Entry (${bal_ist_hold:,.0f})')
    ax1.plot(eq_ist_flat.index, eq_ist_flat['bal'], color='#2563eb', linewidth=2.0, label=f'10:00-16:00 IST Strict Flat Close (${bal_ist_flat:,.0f})')
    ax1.set_yscale('log')
    ax1.set_title('A. 2-Year Compounded Equity Growth (10:00 AM – 4:00 PM IST)', fontsize=10.5, fontweight='bold', color='#0f172a')
    ax1.set_ylabel('Account Equity (USD)', fontsize=9, fontweight='bold', color='#1e293b')
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"${int(y):,}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax1.legend(loc='upper left', fontsize=8.5, framealpha=0.95, facecolor='#ffffff', edgecolor='#cbd5e1')

    # Panel 2: Underwater Drawdown Profiles
    ax2.plot(eq_24h.index, -eq_24h['dd'], color='#94a3b8', linewidth=1.0, linestyle=':', label=f'24-Hour Benchmark (Peak: {dd_24h:.2f}%)')
    ax2.fill_between(eq_ist_flat.index, 0, -eq_ist_flat['dd'], color='#3b82f6', alpha=0.35, label=f'10:00-16:00 IST Flat (Peak: {dd_ist_flat:.2f}%)')
    ax2.plot(eq_ist_hold.index, -eq_ist_hold['dd'], color='#059669', linewidth=2.0, label=f'10:00-16:00 IST Session Entry (Peak: {dd_ist_hold:.2f}%)')
    ax2.axhline(-30.0, color='#dc2626', linestyle='-', linewidth=2.0, label='Mandate Ceiling (-30.0%)')
    ax2.axhline(-20.0, color='#d97706', linestyle='--', linewidth=1.2, label='Circuit Breaker (-20.0%)')
    ax2.set_title('B. Underwater Drawdown Profiles (Strict &le; 30.0% Mandate)', fontsize=10.5, fontweight='bold', color='#0f172a')
    ax2.set_ylabel('Drawdown %', fontsize=9, fontweight='bold', color='#1e293b')
    ax2.set_ylim(-35, 2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax2.legend(loc='lower left', fontsize=8.5, framealpha=0.95, facecolor='#ffffff', edgecolor='#cbd5e1')

    # Panel 3: Monthly Net PnL of 10-16 IST Strict Flat Mode
    tdf = pd.DataFrame(t_ist_flat)
    tdf['month'] = pd.to_datetime(tdf['dt']).dt.to_period('M')
    m_pnl = tdf.groupby('month')['pnl'].sum()
    m_labels = [str(m)[2:] for m in m_pnl.index]
    m_vals = m_pnl.values
    colors_bars = ['#059669' if v >= 0 else '#dc2626' for v in m_vals]
    
    ax3.bar(range(len(m_vals)), m_vals, color=colors_bars, edgecolor='#0f172a', width=0.65, alpha=0.85)
    ax3.set_title('C. Monthly Net PnL (USD) — 10:00 to 16:00 IST Strict Flat Engine', fontsize=10.5, fontweight='bold', color='#0f172a')
    ax3.set_ylabel('Monthly Net Profit (USD)', fontsize=9, fontweight='bold', color='#1e293b')
    ax3.set_xticks(range(len(m_vals)))
    ax3.set_xticklabels(m_labels, rotation=45, fontsize=7.5, color='#1e293b')
    ax3.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"${int(y):,}"))

    # Panel 4: Peak Drawdown vs Mandate Comparison
    cats = ['10-16 IST\n(Strict Flat Close)', '10-16 IST\n(Session Entry)', '24-Hour\nBenchmark']
    dds = [dd_ist_flat, dd_ist_hold, dd_24h]
    colors_d = ['#2563eb', '#059669', '#64748b']
    b_dd = ax4.bar(cats, dds, color=colors_d, edgecolor='#0f172a', width=0.45, alpha=0.9)
    ax4.axhline(30.0, color='#dc2626', linestyle='-', linewidth=2.0, label='User Mandate Ceiling (30.0%)')
    ax4.set_title('D. Peak Drawdown vs 30% User Mandate Ceiling', fontsize=10.5, fontweight='bold', color='#0f172a')
    ax4.set_ylabel('Peak Drawdown %', fontsize=9, fontweight='bold', color='#1e293b')
    ax4.set_ylim(0, 35)
    ax4.legend(loc='upper right', fontsize=8.5, framealpha=0.95, facecolor='#ffffff', edgecolor='#cbd5e1')
    
    for b in b_dd:
        h = b.get_height()
        ax4.annotate(f"{h:.2f}%\n(+{30.0-h:.1f}% Buffer)", xy=(b.get_x() + b.get_width()/2, h), xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8.5, fontweight='bold', color='#0f172a')

    plt.tight_layout()
    chart_path = os.path.join(REPORTS_DIR, 'chart_gold_2year_10_to_4_ist_audit.png')
    plt.savefig(chart_path, dpi=300, facecolor='#ffffff')
    plt.close()
    print(f"IST audit chart saved: {chart_path}")
    mt5.shutdown()

if __name__ == '__main__':
    generate_ist_chart()

