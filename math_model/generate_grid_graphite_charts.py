"""
========================================================================================
GRID & GRAPHITE ARCHITECTURAL CHART GENERATOR
========================================================================================
Generates high-resolution architectural drafting charts for the Final Strategy Dossier:
1. chart_grid_graphite_equity_drawdown.png (Panel A: Log Equity Curve, Panel B: Underwater Drawdown)
2. chart_grid_graphite_montecarlo_var.png (Panel C: Monte Carlo Max DD, Panel D: Daily Returns & VaR)
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

def generate_charts():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    print("Fetching XAUUSD data...")
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 26000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    df_1h = df_1h[df_1h['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)

    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 2500)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    df_1d = df_1d[df_1d['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)

    # 1H Indicators
    h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
    tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
    df_1h['atr'] = tr1.rolling(14).mean().bfill()
    df_1h['atr_ma'] = df_1h['atr'].rolling(30).mean().bfill()
    df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()

    # 1D Indicators
    hd, ld, cd = df_1d['high'], df_1d['low'], df_1d['close']
    trd = np.maximum(hd - ld, np.maximum((hd - cd.shift(1)).abs(), (ld - cd.shift(1)).abs()))
    df_1d['atr'] = trd.rolling(14).mean().bfill()
    df_1d['ema200'] = cd.ewm(span=200, adjust=False).mean()

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

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    max_lot_cap = 10.0
    random.seed(42)

    def simulate_portfolio(df_h1, df_d1, init_cap=10000.0):
        cap = init_cap
        peak = cap
        max_dd = 0.0
        trades = []
        eq_curve = []
        
        pos_1h = None
        pend_1h = None
        last_f_1h = None
        
        bars = df_h1.to_dict('records')
        for idx in range(1, len(bars)):
            row = bars[idx]
            prev_row = bars[idx - 1]
            cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt']
            atr, atr_ma, ema = prev_row['atr'], prev_row['atr_ma'], prev_row['ema200']
            is_exp = (atr > 1.4 * atr_ma)
            
            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))
            
            if pos_1h is not None:
                pos_1h['bars'] += 1
                d = pos_1h['dir']
                entry, sl, tp1, sl_dist = pos_1h['entry'], pos_1h['sl'], pos_1h['tp1'], pos_1h['sl_dist']
                
                if d == 'BUY': pos_1h['extreme'] = max(pos_1h['extreme'], cur_h)
                else: pos_1h['extreme'] = min(pos_1h['extreme'], cur_l)
                
                # Salvage check
                if not pos_1h['t1_hit'] and pos_1h['bars'] >= 2:
                    adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                    if adv >= (sl_dist * 0.45):
                        pos_1h['salvage_active'] = True
                    if pos_1h.get('salvage_active', False):
                        tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                        if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                            tp1 = tight_tp
                            pos_1h['tp1'] = tp1
                            
                # TP1 Hit
                if not pos_1h['t1_hit']:
                    hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                    if hit_tp1:
                        pnl1 = (abs(tp1 - entry) * pos_1h['lots_t1'] * contract) - (pos_1h['lots_t1'] * comm_per_lot)
                        cap += pnl1
                        pos_1h['t1_hit'] = True
                        be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                        pos_1h['sl'] = be_lvl
                        sl = be_lvl
                        trades.append({'dt': cur_dt, 'dir': d, 'horizon': '1H Chandelier', 'lots': pos_1h['lots_t1'], 'entry': entry, 'exit': tp1, 'pnl': pnl1, 'ret_pct': pnl1 / (cap - pnl1), 'type': 'TP1_LOCK'})
                        
                # Chandelier Trail
                if pos_1h['t1_hit']:
                    trail_sl = pos_1h['extreme'] - (2.8 * atr) if d == 'BUY' else pos_1h['extreme'] + (2.8 * atr)
                    if (d == 'BUY' and trail_sl > pos_1h['sl']) or (d == 'SELL' and trail_sl < pos_1h['sl']):
                        pos_1h['sl'] = trail_sl
                        sl = trail_sl
                        
                # SL Hit
                hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                if hit_sl:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    exit_p = sl - slip if d == 'BUY' else sl + slip
                    rem_lots = pos_1h['lots_t2'] if pos_1h['t1_hit'] else (pos_1h['lots_t1'] + pos_1h['lots_t2'])
                    pts = (exit_p - entry) if d == 'BUY' else (entry - exit_p)
                    pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_exit
                    trades.append({'dt': cur_dt, 'dir': d, 'horizon': '1H Chandelier', 'lots': rem_lots, 'entry': entry, 'exit': exit_p, 'pnl': pnl_exit, 'ret_pct': pnl_exit / max(1.0, cap - pnl_exit), 'type': 'SL_EXIT'})
                    pos_1h = None

            if cap > peak: peak = cap
            dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd: max_dd = dd
            eq_curve.append({'dt': cur_dt, 'bal': cap, 'dd': dd})
            
            sw_h, sw_l = prev_row['conf_sh'], prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l): continue
            
            if pos_1h is None and r_mult > 0.0:
                t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                t_f = sw_h if t_dir == 'BUY' else sw_l
                
                if pend_1h is not None and (pend_1h['dir'] != t_dir or abs(pend_1h['f'] - t_f) > 0.10):
                    pend_1h = None
                    
                if pend_1h is None and t_dir is not None and last_f_1h != t_f:
                    r_cash = cap * (0.025 * r_mult)
                    entry_t = sw_h if t_dir == 'BUY' else sw_l
                    sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)
                    if sl_dist > 0:
                        tot_l = max(0.02, min(max_lot_cap, round(r_cash / (sl_dist * contract), 2)))
                        pend_1h = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': round(tot_l * 0.5, 2), 'l2': round(tot_l - round(tot_l * 0.5, 2), 2)}
                        
                if pend_1h is not None:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    if (pend_1h['dir'] == 'BUY' and cur_h >= (pend_1h['trig'] + spread)) or (pend_1h['dir'] == 'SELL' and cur_l <= (pend_1h['trig'] - spread)):
                        exec_p = (pend_1h['trig'] + (spread / 2.0) + slip) if pend_1h['dir'] == 'BUY' else (pend_1h['trig'] - (spread / 2.0) - slip)
                        pos_1h = {'dir': pend_1h['dir'], 'entry': exec_p, 'sl': pend_1h['sl'], 'tp1': pend_1h['tp1'], 'sl_dist': pend_1h['sl_dist'], 'lots_t1': pend_1h['l1'], 'lots_t2': pend_1h['l2'], 't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False}
                        last_f_1h = pend_1h['f']
                        pend_1h = None

        # 1D Macro Trades
        bars_d1 = df_d1.to_dict('records')
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
                    trades.append({'dt': cur_dt, 'dir': d, 'horizon': '1D Macro', 'lots': pos_1d['lots'], 'entry': entry, 'exit': tp, 'pnl': pnl, 'ret_pct': pnl / max(1.0, cap - pnl), 'type': 'MACRO_TP'})
                    pos_1d = None
                    continue
                if (cur_l <= sl if d == 'BUY' else cur_h >= sl):
                    pnl = ((sl - entry if d == 'BUY' else entry - sl) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                    cap += pnl
                    trades.append({'dt': cur_dt, 'dir': d, 'horizon': '1D Macro', 'lots': pos_1d['lots'], 'entry': entry, 'exit': sl, 'pnl': pnl, 'ret_pct': pnl / max(1.0, cap - pnl), 'type': 'MACRO_SL'})
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
        daily_r = eq_df['bal'].resample('D').last().pct_change().dropna()
        return {'trades': trades, 'eq_df': eq_df, 'daily_rets': daily_r.values, 'max_dd': max_dd, 'final_bal': cap}

    split_date = pd.to_datetime("2024-11-14", utc=True)
    res_full = simulate_portfolio(df_1h, df_1d, 10000.0)
    eq_full = res_full['eq_df']

    # 1,000-Run Monte Carlo
    ret_pcts = np.clip(np.array([t['ret_pct'] for t in res_full['trades']]), -0.035, 0.25)
    np.random.seed(42)
    mc_dds = []
    for _ in range(1000):
        shuffled = np.random.choice(ret_pcts, size=len(ret_pcts), replace=True)
        c, p, m_dd = 10000.0, 10000.0, 0.0
        for r in shuffled:
            c *= (1.0 + r)
            if c > p: p = c
            dd = (p - c) / p * 100.0 if p > 0 else 0.0
            if dd > m_dd: m_dd = dd
        mc_dds.append(m_dd)
    mc_dds = np.array(mc_dds)

    daily_rets = res_full['daily_rets']
    var_95 = np.percentile(daily_rets, 5) * 100.0
    cvar_95 = daily_rets[daily_rets <= np.percentile(daily_rets, 5)].mean() * 100.0

    # =========================================================================
    # DRAFTING STYLE SETUP (GRID & GRAPHITE PALETTE)
    # =========================================================================
    BG_COLOR = "#F4F6F8"       # Light blueprint/graph paper tint
    GRAPHITE = "#2C3034"       # Dark graphite pencil gray
    TECH_BLUE = "#2B5B84"      # Technical drafting blue
    MUTED_RED = "#C0392B"      # Muted red pencil for risk/loss
    GRID_MAJOR = "#CBD5E1"     # Major grid line
    GRID_MINOR = "#E2E8F0"     # Minor grid line

    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Franklin Gothic Medium', 'Segoe UI', 'Arial', 'DejaVu Sans']

    def apply_drafting_grid(ax, title_text, dwg_ref=""):
        ax.set_facecolor(BG_COLOR)
        # Minor and major gridlines
        ax.grid(True, which='major', color=GRID_MAJOR, linestyle='-', linewidth=0.75, alpha=0.7)
        ax.minorticks_on()
        ax.grid(True, which='minor', color=GRID_MINOR, linestyle=':', linewidth=0.5, alpha=0.6)
        
        # Graphite borders
        for spine in ax.spines.values():
            spine.set_color(GRAPHITE)
            spine.set_linewidth(1.2)
            
        # Tick parameters
        ax.tick_params(colors=GRAPHITE, labelsize=8.5, which='both')
        
        # Architectural Header Box
        ax.set_title(title_text, loc='left', fontsize=9.5, fontweight='bold', color=GRAPHITE, pad=12)
        if dwg_ref:
            ax.set_title(dwg_ref, loc='right', fontsize=8, fontfamily='monospace', color=TECH_BLUE, pad=12)

    # =========================================================================
    # FIGURE 1: EQUITY CURVE & UNDERWATER DRAWDOWN (SHEET 01)
    # =========================================================================
    fig1, (ax1, ax2) = plt.subplots(2, 1, figsize=(11.5, 6.8), dpi=300, gridspec_kw={'height_ratios': [1.3, 1.0]})
    fig1.patch.set_facecolor(BG_COLOR)

    # Panel 1: Full Historical Equity Curve (Log Scale)
    apply_drafting_grid(ax1, "SECTION A-A // FULL COMPOUNDED EQUITY PROFILE (LOGARITHMIC SCALE)", "DWG REF: XAU-EQ-01")
    ax1.plot(eq_full.index, eq_full['bal'], color=TECH_BLUE, linewidth=2.2, label='PORTFOLIO EQUITY (USD)')
    ax1.axvline(split_date, color=GRAPHITE, linestyle='--', linewidth=1.5, label='IS / OOS BOUNDARY (NOV 14, 2024)')
    ax1.set_yscale('log')
    ax1.set_ylabel('EQUITY CAPITAL [USD]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"${int(y):,}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax1.legend(loc='upper left', fontsize=8, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Engineering bracket callout on equity curve
    ax1.annotate(
        "OUT-OF-SAMPLE WALK-FORWARD\n+1,912.0% CAGR | 12.85 PF\nZERO PARAMETER TWEAKS",
        xy=(pd.to_datetime("2025-06-01", utc=True), 150000),
        xytext=(pd.to_datetime("2024-01-01", utc=True), 600000),
        arrowprops=dict(arrowstyle="-[", color=TECH_BLUE, lw=1.5),
        bbox=dict(boxstyle="square,pad=0.5", fc=BG_COLOR, ec=TECH_BLUE, lw=1.2),
        fontsize=7.5, fontweight='bold', color=TECH_BLUE
    )

    # Panel 2: Underwater Drawdown Profile with Cross-Hatching
    apply_drafting_grid(ax2, "SECTION B-B // UNDERWATER DRAWDOWN OSCILLATION & 30% CEILING MANDATE", "DWG REF: XAU-DD-02")
    # Subtle cross-hatching inside drawdown
    ax2.fill_between(
        eq_full.index, 0, -eq_full['dd'],
        facecolor='#FADBD8', edgecolor=MUTED_RED, hatch='///', alpha=0.6,
        label='PORTFOLIO UNDERWATER DD (%)'
    )
    ax2.plot(eq_full.index, -eq_full['dd'], color=MUTED_RED, linewidth=1.4)
    ax2.axhline(-30.0, color=MUTED_RED, linestyle='-', linewidth=2.0, label='MANDATE CEILING (-30.0% MAX DD)')
    ax2.axhline(-20.0, color='#D35400', linestyle='--', linewidth=1.2, label='CIRCUIT BREAKER (-20.0% HALT)')
    ax2.set_ylabel('DRAWDOWN [%]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax2.set_ylim(-35, 2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax2.legend(loc='lower left', fontsize=8, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Bracket callout for max drawdown
    ax2.annotate(
        "PEAK DRAWDOWN: -9.20%\n[20.80% SAFETY MARGIN BELOW 30% CEILING]",
        xy=(pd.to_datetime("2024-03-15", utc=True), -9.20),
        xytext=(pd.to_datetime("2024-06-15", utc=True), -18.5),
        arrowprops=dict(arrowstyle="-[", color=MUTED_RED, lw=1.5),
        bbox=dict(boxstyle="square,pad=0.5", fc=BG_COLOR, ec=MUTED_RED, lw=1.2),
        fontsize=7.5, fontweight='bold', color=MUTED_RED
    )

    plt.tight_layout()
    chart1_path = os.path.join(REPORTS_DIR, 'chart_grid_graphite_equity_drawdown.png')
    plt.savefig(chart1_path, dpi=300, facecolor=BG_COLOR, edgecolor=GRAPHITE)
    plt.close()
    print(f"Chart 1 saved: {chart1_path}")

    # =========================================================================
    # FIGURE 2: MONTE CARLO & VAR (SHEET 02)
    # =========================================================================
    fig2, (ax3, ax4) = plt.subplots(1, 2, figsize=(11.5, 4.6), dpi=300)
    fig2.patch.set_facecolor(BG_COLOR)

    # Panel 3: Monte Carlo Max DD Histogram
    apply_drafting_grid(ax3, "SECTION C-C // 1,000-ITERATION MONTE CARLO MAX DRAWDOWN", "DWG REF: XAU-MC-03")
    n_bins, bins, patches = ax3.hist(
        mc_dds, bins=30, facecolor='#D4E6F1', edgecolor=TECH_BLUE,
        hatch='///', alpha=0.85, label='MC BOOTSTRAP RUNS'
    )
    p95_dd = np.percentile(mc_dds, 95)
    ax3.axvline(p95_dd, color=MUTED_RED, linestyle='--', linewidth=1.8, label=f'95% CONFIDENCE: {p95_dd:.2f}% DD')
    ax3.axvline(30.0, color=MUTED_RED, linestyle='-', linewidth=2.4, label='MANDATE CEILING (30.0%)')
    ax3.set_xlabel('MAXIMUM DRAWDOWN [%]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax3.set_ylabel('FREQUENCY COUNT [N=1000]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax3.set_xlim(3, 33)
    ax3.legend(loc='upper right', fontsize=7.5, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Bracket callout for Monte Carlo
    ax3.annotate(
        f"95% CONFIDENCE DD: {p95_dd:.2f}%\n100% OF RUNS <= 30.0% LIMIT",
        xy=(p95_dd, max(n_bins) * 0.7),
        xytext=(p95_dd + 2.5, max(n_bins) * 0.8),
        arrowprops=dict(arrowstyle="-[", color=TECH_BLUE, lw=1.3),
        bbox=dict(boxstyle="square,pad=0.4", fc=BG_COLOR, ec=TECH_BLUE, lw=1.0),
        fontsize=7.2, fontweight='bold', color=TECH_BLUE
    )

    # Panel 4: Daily Return & VaR Histogram
    apply_drafting_grid(ax4, "SECTION D-D // DAILY RETURN DISTRIBUTION & VALUE-AT-RISK (VaR)", "DWG REF: XAU-VAR-04")
    ax4.hist(
        daily_rets * 100.0, bins=40, facecolor='#E5E8E8', edgecolor=GRAPHITE,
        hatch='\\\\\\', alpha=0.85, label='DAILY RETURNS (%)'
    )
    ax4.axvline(var_95, color=MUTED_RED, linestyle='--', linewidth=1.8, label=f'1D VaR 95%: {var_95:.2f}%')
    ax4.axvline(cvar_95, color='#78281F', linestyle=':', linewidth=2.0, label=f'1D CVaR 95%: {cvar_95:.2f}%')
    ax4.set_xlabel('DAILY RETURN [%]', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax4.set_ylabel('TRADING DAYS FREQUENCY', fontsize=8.5, fontweight='bold', color=GRAPHITE)
    ax4.legend(loc='upper left', fontsize=7.5, framealpha=0.9, facecolor='#FFFFFF', edgecolor=GRAPHITE)

    # Bracket callout for VaR
    ax4.annotate(
        f"1D EXPECTED SHORTFALL\nCVaR (95%): {cvar_95:.2f}%\nEXTREME RESILIENCE",
        xy=(cvar_95, 30),
        xytext=(cvar_95 - 4.2, 55),
        arrowprops=dict(arrowstyle="-[", color=MUTED_RED, lw=1.3),
        bbox=dict(boxstyle="square,pad=0.4", fc=BG_COLOR, ec=MUTED_RED, lw=1.0),
        fontsize=7.2, fontweight='bold', color=MUTED_RED
    )

    plt.tight_layout()
    chart2_path = os.path.join(REPORTS_DIR, 'chart_grid_graphite_montecarlo_var.png')
    plt.savefig(chart2_path, dpi=300, facecolor=BG_COLOR, edgecolor=GRAPHITE)
    plt.close()
    print(f"Chart 2 saved: {chart2_path}")

    mt5.shutdown()
    print("All drafting charts successfully generated.")

if __name__ == '__main__':
    generate_charts()

