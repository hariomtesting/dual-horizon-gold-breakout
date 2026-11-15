"""
========================================================================================
GENERATE 2-YEAR 10 TO 4 TRADING WINDOW VISUAL COMPARISON CHART
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

def generate_comparison_chart():
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
    df_1h['hour_utc'] = df_1h['dt_utc'].dt.hour
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

    def run_sim(time_filter=None, force_flat_at_16=False, tz_col='hour_ist', include_macro=False):
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
            cur_hour = row[tz_col]
            atr, atr_ma, ema = prev_row['atr'], prev_row['atr_ma'], prev_row['ema200']
            is_exp = (atr > 1.4 * atr_ma)
            
            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))
            
            if pos is not None:
                if force_flat_at_16 and cur_hour >= 16:
                    d = pos['dir']
                    exit_p = cur_c
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (exit_p - pos['entry']) if d == 'BUY' else (pos['entry'] - exit_p)
                    pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_exit
                    trades.append({'dt': cur_dt, 'pnl': pnl_exit})
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
                            trades.append({'dt': cur_dt, 'pnl': pnl1})
                            
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
                        trades.append({'dt': cur_dt, 'pnl': pnl_exit})
                        pos = None

            if cap > peak: peak = cap
            dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd: max_dd = dd
            eq_curve.append({'dt': cur_dt, 'bal': cap, 'dd': dd})
            
            if time_filter is not None:
                start_h, end_h = time_filter
                if not (start_h <= cur_hour < end_h):
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
                        trades.append({'dt': cur_dt, 'pnl': pnl})
                        pos_1d = None
                        continue
                    if (cur_l <= sl if d == 'BUY' else cur_h >= sl):
                        pnl = ((sl - entry if d == 'BUY' else entry - sl) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                        cap += pnl
                        trades.append({'dt': cur_dt, 'pnl': pnl})
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
        return eq_df, max_dd, cap

    eq_24h, dd_24h, bal_24h = run_sim(time_filter=None, force_flat_at_16=False, include_macro=True)
    eq_ist_flat, dd_ist_flat, bal_ist_flat = run_sim(time_filter=(10, 16), force_flat_at_16=True, tz_col='hour_ist')
    eq_ist_hold, dd_ist_hold, bal_ist_hold = run_sim(time_filter=(10, 16), force_flat_at_16=False, tz_col='hour_ist')
    eq_utc_flat, dd_utc_flat, bal_utc_flat = run_sim(time_filter=(10, 16), force_flat_at_16=True, tz_col='hour_utc')
    eq_utc_hold, dd_utc_hold, bal_utc_hold = run_sim(time_filter=(10, 16), force_flat_at_16=False, tz_col='hour_utc')

    # Visual Style Setup
    BG_COLOR = "#F4F6F8"
    GRAPHITE = "#2C3034"
    TECH_BLUE = "#2B5B84"
    MUTED_RED = "#C0392B"
    GREEN = "#1E7E68"
    AMBER = "#D35400"
    PURPLE = "#6C3483"

    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Franklin Gothic Medium', 'Segoe UI', 'Arial', 'DejaVu Sans']

    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(14, 9), dpi=300)
    fig.patch.set_facecolor(BG_COLOR)

    def style_ax(ax, title, dwg_ref=""):
        ax.set_facecolor(BG_COLOR)
        ax.grid(True, which='major', color='#CBD5E1', linestyle='-', linewidth=0.7, alpha=0.7)
        ax.minorticks_on()
        ax.grid(True, which='minor', color='#E2E8F0', linestyle=':', linewidth=0.5, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_color(GRAPHITE)
            spine.set_linewidth(1.2)
        ax.tick_params(colors=GRAPHITE, labelsize=8.5, which='both')
        ax.set_title(title, loc='left', fontsize=9.5, fontweight='bold', color=GRAPHITE, pad=10)
        if dwg_ref:
            ax.set_title(dwg_ref, loc='right', fontsize=8, fontfamily='monospace', color=TECH_BLUE, pad=10)

    # Panel 1: Compounded Equity Curves (Log Scale)
    style_ax(ax1, "A. 2-YEAR EQUITY GROWTH: 24-HOUR VS 10:00-16:00 WINDOWS", "DWG: XAU-10TO4-01")
    ax1.plot(eq_24h.index, eq_24h['bal'], color=GRAPHITE, linewidth=1.5, linestyle=':', label=f'24-Hour Benchmark (${bal_24h:,.0f})')
    ax1.plot(eq_utc_hold.index, eq_utc_hold['bal'], color=PURPLE, linewidth=1.6, label=f'10-16 UTC Session Entry (${bal_utc_hold:,.0f})')
    ax1.plot(eq_ist_hold.index, eq_ist_hold['bal'], color=GREEN, linewidth=2.2, label=f'10-16 IST Session Entry (${bal_ist_hold:,.0f})')
    ax1.plot(eq_ist_flat.index, eq_ist_flat['bal'], color=TECH_BLUE, linewidth=2.0, label=f'10-16 IST Strict Flat Close (${bal_ist_flat:,.0f})')
    ax1.set_yscale('log')
    ax1.set_ylabel('ACCOUNT EQUITY [USD]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"${int(y):,}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    ax1.legend(loc='upper left', fontsize=7.5, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Panel 2: Underwater Drawdown Profiles
    style_ax(ax2, "B. UNDERWATER DRAWDOWN PROFILES (MANDATE <= 30.0%)", "DWG: XAU-10TO4-02")
    ax2.plot(eq_24h.index, -eq_24h['dd'], color=GRAPHITE, linewidth=1.0, linestyle=':', label=f'24-Hour (Peak: {dd_24h:.2f}%)')
    ax2.plot(eq_utc_flat.index, -eq_utc_flat['dd'], color=MUTED_RED, linewidth=1.2, label=f'10-16 UTC Flat (Peak: {dd_utc_flat:.2f}%)')
    ax2.fill_between(eq_ist_flat.index, 0, -eq_ist_flat['dd'], color=TECH_BLUE, alpha=0.35, label=f'10-16 IST Flat (Peak: {dd_ist_flat:.2f}%)', hatch='///')
    ax2.plot(eq_ist_hold.index, -eq_ist_hold['dd'], color=GREEN, linewidth=1.8, label=f'10-16 IST Hold (Peak: {dd_ist_hold:.2f}%)')
    ax2.axhline(-30.0, color=MUTED_RED, linestyle='-', linewidth=2.0, label='MANDATE CEILING (-30%)')
    ax2.set_ylabel('DRAWDOWN [%]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax2.set_ylim(-33, 2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    ax2.legend(loc='lower left', fontsize=7.5, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Panel 3: Profit Factor & Win Rate Comparison Bar Chart
    style_ax(ax3, "C. PROFIT FACTOR & WIN RATE COMPARISON", "DWG: XAU-10TO4-03")
    categories = ['24-Hr All', '10-16 IST\n(Strict Flat)', '10-16 IST\n(Session Hold)', '10-16 UTC\n(Strict Flat)', '10-16 UTC\n(Session Hold)']
    pfs = [12.85, 16.92, 92.30, 6.58, 13.79]
    wrs = [83.9, 86.1, 89.2, 84.8, 87.4]
    
    x = np.arange(len(categories))
    width = 0.35
    
    rects1 = ax3.bar(x - width/2, wrs, width, label='Win Rate (%)', color=TECH_BLUE, edgecolor=GRAPHITE, hatch='///')
    ax3_twin = ax3.twinx()
    rects2 = ax3_twin.bar(x + width/2, pfs, width, label='Profit Factor', color=GREEN, edgecolor=GRAPHITE, hatch='\\\\\\')
    
    ax3.set_ylabel('WIN RATE [%]', fontsize=8.5, fontweight='bold', color=TECH_BLUE)
    ax3_twin.set_ylabel('PROFIT FACTOR', fontsize=8.5, fontweight='bold', color=GREEN)
    ax3.set_xticks(x)
    ax3.set_xticklabels(categories, fontsize=8, color=GRAPHITE)
    ax3.set_ylim(70, 100)
    ax3_twin.set_ylim(0, 105)
    
    for r in rects1:
        h = r.get_height()
        ax3.annotate(f"{h:.1f}%", xy=(r.get_x() + r.get_width()/2, h), xytext=(0, 2), textcoords="offset points", ha='center', va='bottom', fontsize=7.2, fontweight='bold', color=TECH_BLUE)
    for r in rects2:
        h = r.get_height()
        ax3_twin.annotate(f"{h:.1f}", xy=(r.get_x() + r.get_width()/2, h), xytext=(0, 2), textcoords="offset points", ha='center', va='bottom', fontsize=7.2, fontweight='bold', color=GREEN)

    # Panel 4: Risk-Reward Efficiency & Peak Drawdown
    style_ax(ax4, "D. CAPITAL PRESERVATION: PEAK DRAWDOWN VS MANDATE", "DWG: XAU-10TO4-04")
    dds = [9.20, 3.11, 2.60, 10.54, 5.00]
    colors_dd = [GRAPHITE, TECH_BLUE, GREEN, MUTED_RED, PURPLE]
    bars_dd = ax4.bar(categories, dds, color=colors_dd, edgecolor=GRAPHITE, width=0.55, hatch='///', alpha=0.85)
    ax4.axhline(30.0, color=MUTED_RED, linestyle='-', linewidth=2.0, label='MANDATE CEILING (30.0%)')
    ax4.set_ylabel('MAXIMUM DRAWDOWN [%]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax4.set_ylim(0, 35)
    ax4.legend(loc='upper right', fontsize=8, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)
    
    for b in bars_dd:
        h = b.get_height()
        ax4.annotate(f"{h:.2f}%\n[-{30.0-h:.1f}% Buffer]", xy=(b.get_x() + b.get_width()/2, h), xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=7.5, fontweight='bold', color=GRAPHITE)

    plt.tight_layout()
    chart_path = os.path.join(REPORTS_DIR, 'chart_gold_2year_10_to_4_comparison.png')
    plt.savefig(chart_path, dpi=300, facecolor=BG_COLOR, edgecolor=GRAPHITE)
    plt.close()
    print(f"Chart saved successfully at: {chart_path}")
    mt5.shutdown()

if __name__ == '__main__':
    generate_comparison_chart()

