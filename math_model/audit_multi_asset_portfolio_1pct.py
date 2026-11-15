"""
========================================================================================
QUANT AUDIT: MULTI-ASSET PORTFOLIO (1.0% RISK PER ASSET)
SESSION: 10:00 - 16:00 IST NO-VPS HOLD & CHANDELIER TRAIL
BASKET: GOLD (XAUUSD) + BITCOIN (BTCUSD) + NASDAQ 100 (NAS100.r)
========================================================================================
"""

import os
import json
import random
import datetime
import pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import MetaTrader5 as mt5

def main():
    print("=" * 80)
    print("🛡️ MULTI-ASSET PORTFOLIO SIMULATION (1.0% RISK • 10-16 IST HOLD & TRAIL)")
    print("=" * 80)

    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_account.json'))
    init_kwargs = {}
    with open(cfg_path) as f:
        c = json.load(f)
        if c.get('path'): init_kwargs['path'] = c['path']
        if c.get('login'): init_kwargs['login'] = int(c['login'])
        if c.get('password'): init_kwargs['password'] = c['password']
        if c.get('server'): init_kwargs['server'] = c['server']

    if not mt5.initialize(**init_kwargs):
        print("[ERROR] Could not initialize MT5")
        return

    utc = pytz.timezone('UTC')
    ist = pytz.timezone('Asia/Kolkata')

    assets = {
        'XAUUSD': {'name': 'Gold Bullion', 'spread': 0.28, 'comm': 6.0, 'contract': 100.0, 'min_lot': 0.01, 'max_lot': 50.0},
        'BTCUSD': {'name': 'Bitcoin', 'spread': 18.0, 'comm': 5.0, 'contract': 1.0, 'min_lot': 0.01, 'max_lot': 20.0},
        'NAS100.r': {'name': 'Nasdaq 100', 'spread': 1.8, 'comm': 4.0, 'contract': 1.0, 'min_lot': 0.1, 'max_lot': 50.0}
    }

    # Fetch data and align common timestamps
    dfs = {}
    for sym in assets.keys():
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 16000)
        df = pd.DataFrame(rates)
        df['dt_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df['dt_ist'] = df['dt_utc'].dt.tz_convert(ist)
        df['hour_ist'] = df['dt_ist'].dt.hour

        h, l, c_s = df['high'], df['low'], df['close']
        tr = np.maximum(h - l, np.maximum((h - c_s.shift(1)).abs(), (l - c_s.shift(1)).abs()))
        df['atr'] = tr.rolling(14).mean().bfill()
        df['atr_ma'] = df['atr'].rolling(30).mean().bfill()
        df['ema200'] = c_s.ewm(span=200, adjust=False).mean()

        # Fractals
        highs, lows = df['high'].values, df['low'].values
        n = len(df)
        sh_arr, sl_arr = np.full(n, np.nan), np.full(n, np.nan)
        x_left, y_right = 15, 3
        for i in range(x_left, n - y_right):
            hc, lc = highs[i], lows[i]
            if all(highs[i - k] < hc for k in range(1, x_left + 1)) and all(highs[i + k] < hc for k in range(1, y_right + 1)):
                sh_arr[i + y_right] = hc
            if all(lows[i - k] > lc for k in range(1, x_left + 1)) and all(lows[i + k] > lc for k in range(1, y_right + 1)):
                sl_arr[i + y_right] = lc
        df['conf_sh'] = pd.Series(sh_arr).ffill()
        df['conf_sl'] = pd.Series(sl_arr).ffill()
        dfs[sym] = df.set_index('dt_utc')

    start_date = pd.to_datetime("2025-02-25 00:00:00", utc=True)
    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)

    # Common time index
    common_idx = dfs['XAUUSD'].loc[start_date:end_date].index
    print(f"Common simulation bars: {len(common_idx)} (Feb 2025 - Nov 2026)")

    # Run simulations:
    # 1. Gold Solo (1% Risk)
    # 2. Gold + Bitcoin (1% Risk each)
    # 3. Gold + Bitcoin + Nasdaq (1% Risk each)
    portfolios_to_test = [
        {'name': 'Solo Gold (1.0% Risk)', 'symbols': ['XAUUSD'], 'risk_per_asset': 0.010},
        {'name': 'Dual-Asset: Gold + BTC (1.0% Risk)', 'symbols': ['XAUUSD', 'BTCUSD'], 'risk_per_asset': 0.010},
        {'name': 'Tri-Asset: Gold + BTC + NAS100 (1.0% Risk)', 'symbols': ['XAUUSD', 'BTCUSD', 'NAS100.r'], 'risk_per_asset': 0.010}
    ]

    portfolio_results = []
    curves_dict = {}

    for port in portfolios_to_test:
        p_name = port['name']
        p_syms = port['symbols']
        risk_per_asset = port['risk_per_asset']
        print(f"\n--- Simulating: {p_name} ---")

        initial_cap = 10000.0
        cap = initial_cap
        peak = initial_cap
        max_dd = 0.0
        all_trades = []
        equity_curve = [initial_cap]

        positions = {s: None for s in p_syms}
        pendings = {s: None for s in p_syms}
        last_fs = {s: None for s in p_syms}

        for cur_dt in common_idx:
            # Check Drawdown
            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_scale = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))

            # 1. Manage Active Positions for each symbol
            for sym in p_syms:
                if sym not in dfs or cur_dt not in dfs[sym].index: continue
                row = dfs[sym].loc[cur_dt]
                cur_h, cur_l, cur_c = row['high'], row['low'], row['close']
                atr = row['atr']
                if atr <= 0: continue

                pos = positions[sym]
                if pos is not None:
                    pos['bars'] += 1
                    d = pos['dir']
                    entry, sl, tp1, sl_dist = pos['entry'], pos['sl'], pos['tp1'], pos['sl_dist']
                    cfg = assets[sym]
                    contract = cfg['contract']
                    comm = cfg['comm']

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
                    hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                    if hit_tp1 and not pos['t1_hit']:
                        pos['t1_hit'] = True
                        p1_pts = (tp1 - entry) if d == 'BUY' else (entry - tp1)
                        pnl_t1 = (p1_pts * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm)
                        cap += pnl_t1
                        be_buf = 0.15 * (atr / 2.0)
                        pos['sl'] = (entry + be_buf) if d == 'BUY' else (entry - be_buf)
                        sl = pos['sl']

                    # Chandelier Trail
                    if pos['t1_hit']:
                        chand_sl = (pos['extreme'] - (2.8 * atr)) if d == 'BUY' else (pos['extreme'] + (2.8 * atr))
                        if d == 'BUY' and chand_sl > pos['sl']: pos['sl'] = chand_sl
                        elif d == 'SELL' and chand_sl < pos['sl']: pos['sl'] = chand_sl
                        sl = pos['sl']

                    # Stop Exit
                    hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                    if hit_sl:
                        rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                        pts = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl_rem = (pts * rem_lots * contract) - (rem_lots * comm)
                        cap += pnl_rem
                        tot_pnl = (pnl_t1 + pnl_rem) if pos['t1_hit'] else pnl_rem
                        all_trades.append({'dt': cur_dt, 'sym': sym, 'pnl': tot_pnl, 't1_hit': pos['t1_hit']})
                        positions[sym] = None

            # Update Peak & DD
            if cap > peak: peak = cap
            cur_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if cur_dd > max_dd: max_dd = cur_dd
            equity_curve.append(cap)

            # 2. Manage Pre-Breakout Orders (10:00 to 16:00 IST Window)
            for sym in p_syms:
                if sym not in dfs or cur_dt not in dfs[sym].index: continue
                row = dfs[sym].loc[cur_dt]
                cur_hour_ist = row['hour_ist']
                is_window = (10 <= cur_hour_ist < 16)

                if not is_window:
                    pendings[sym] = None
                    continue

                cur_h, cur_l, cur_c = row['high'], row['low'], row['close']
                atr, ema = row['atr'], row['ema200']
                sw_h, sw_l = row['conf_sh'], row['conf_sl']
                if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0: continue

                pos = positions[sym]
                pend = pendings[sym]
                cfg = assets[sym]
                contract = cfg['contract']
                spread = cfg['spread']
                min_lot, max_lot = cfg['min_lot'], cfg['max_lot']

                if pos is None and r_scale > 0.0:
                    t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                    t_f = sw_h if t_dir == 'BUY' else sw_l

                    if pend is not None and (pend['dir'] != t_dir or abs(pend['f'] - t_f) > (0.1 * atr)):
                        pendings[sym] = None

                    if pendings[sym] is None and t_dir is not None and last_fs[sym] != t_f:
                        r_cash = cap * (risk_per_asset * r_scale)
                        entry_t = sw_h if t_dir == 'BUY' else sw_l
                        sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                        sl_dist = abs(entry_t - sl)
                        tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)

                        if sl_dist > 0:
                            lot_unrounded = r_cash / (sl_dist * contract)
                            tot_l = max(min_lot * 2.0, min(max_lot, round(lot_unrounded, 2)))
                            l1 = max(min_lot, round(tot_l * 0.5, 2))
                            l2 = max(min_lot, round(tot_l - l1, 2))
                            pendings[sym] = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': l1, 'l2': l2}

                    if pendings[sym] is not None:
                        pend = pendings[sym]
                        slip = 0.05 * atr
                        if (pend['dir'] == 'BUY' and cur_h >= (pend['trig'] + spread)) or (pend['dir'] == 'SELL' and cur_l <= (pend['trig'] - spread)):
                            exec_p = (pend['trig'] + (spread / 2.0) + slip) if pend['dir'] == 'BUY' else (pend['trig'] - (spread / 2.0) - slip)
                            positions[sym] = {
                                'dir': pend['dir'], 'entry': exec_p, 'sl': pend['sl'], 'tp1': pend['tp1'],
                                'sl_dist': pend['sl_dist'], 'lots_t1': pend['l1'], 'lots_t2': pend['l2'],
                                't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False
                            }
                            last_fs[sym] = pend['f']
                            pendings[sym] = None

        df_t = pd.DataFrame(all_trades)
        num_trades = len(df_t)
        wins = df_t[df_t['pnl'] > 0]
        losses = df_t[df_t['pnl'] < 0]
        t1_hits = df_t[df_t['t1_hit'] == True]

        win_rate = len(wins) / num_trades * 100.0 if num_trades > 0 else 0.0
        t1_rate = len(t1_hits) / num_trades * 100.0 if num_trades > 0 else 0.0
        gp = wins['pnl'].sum() if len(wins) > 0 else 0.0
        gl = abs(losses['pnl'].sum()) if len(losses) > 0 else 0.0
        pf = gp / gl if gl > 0 else 99.0
        net_prof = cap - initial_cap
        ret_pct = (net_prof / initial_cap) * 100.0
        calmar = ret_pct / max_dd if max_dd > 0 else 99.0

        res_entry = {
            'portfolio': p_name,
            'symbols': p_syms,
            'initial_balance': initial_cap,
            'final_balance': cap,
            'net_profit': net_prof,
            'total_return_pct': ret_pct,
            'max_dd': max_dd,
            'calmar': calmar,
            'win_rate': win_rate,
            't1_rate': t1_rate,
            'profit_factor': pf,
            'total_trades': num_trades
        }
        portfolio_results.append(res_entry)
        curves_dict[p_name] = equity_curve

        print(f"Results for {p_name}:")
        print(f"  • Total Return  : +{ret_pct:,.1f}% (Final: ${cap:,.2f})")
        print(f"  • Max Drawdown  : {max_dd:.2f}% (Safety Cushion below 30%: +{30.0 - max_dd:.2f}%)")
        print(f"  • Win Rate      : {win_rate:.1f}% (TP1 Lock Rate: {t1_rate:.1f}%)")
        print(f"  • Profit Factor : {pf:.2f} | Total Trades: {num_trades}")

    # =========================================================================
    # GENERATE VISUAL CHART
    # =========================================================================
    print("\nGenerating Multi-Asset Portfolio Chart...")
    reports_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports'))
    os.makedirs(reports_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 12))
    fig.patch.set_facecolor('#0d1117')

    # Plot 1: Curves
    ax1.set_facecolor('#161b22')
    p_colors = ['#e3b341', '#3fb950', '#58a6ff']
    for idx, (lbl, eq) in enumerate(curves_dict.items()):
        indices = np.linspace(0, len(eq) - 1, 100).astype(int)
        sampled_eq = [eq[i] for i in indices]
        ax1.plot(sampled_eq, label=lbl, color=p_colors[idx], linewidth=2.5)

    ax1.set_yscale('log')
    ax1.set_title('1.0% Risk Multi-Asset Portfolio Growth (10:00 - 16:00 IST Hold & Trail)', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax1.set_ylabel('Account Equity ($ USD - Log)', color='#c9d1d9', fontsize=11)
    ax1.tick_params(colors='#8b949e')
    ax1.grid(True, linestyle=':', alpha=0.25, color='#8b949e')
    ax1.legend(loc='upper left', facecolor='#21262d', edgecolor='#30363d', labelcolor='#f0f6fc', fontsize=10)

    # Plot 2: Drawdown Comparison vs Mandate
    ax2.set_facecolor('#161b22')
    names = [r['portfolio'].split(':')[0] for r in portfolio_results]
    dds = [r['max_dd'] for r in portfolio_results]
    rets = [r['total_return_pct'] for r in portfolio_results]

    x = np.arange(len(names))
    width = 0.35

    ax2.bar(x - width/2, dds, width, color='#f85149', alpha=0.85, label='Max Drawdown (%) [Target < 5%]')
    ax2.axhline(30.0, color='#ff0000', linestyle='--', linewidth=2.5, label='Mandate Ceiling: 30.0%')
    ax2.set_ylabel('Max Peak-to-Trough Drawdown (%)', color='#f85149', fontsize=11)
    ax2.set_ylim(0, 35)

    ax2_twin = ax2.twinx()
    ax2_twin.bar(x + width/2, rets, width, color='#3fb950', alpha=0.85, label='Total Return (%)')
    ax2_twin.set_ylabel('Total Return (%)', color='#3fb950', fontsize=11)

    for i, (dd_val, ret_val) in enumerate(zip(dds, rets)):
        ax2.annotate(f"DD: {dd_val:.2f}%", xy=(i - width/2, dd_val), xytext=(0, 6),
                     textcoords="offset points", ha='center', color='#f85149', weight='bold', fontsize=10)
        ax2_twin.annotate(f"+{ret_val:,.0f}%", xy=(i + width/2, ret_val), xytext=(0, 6),
                          textcoords="offset points", ha='center', color='#3fb950', weight='bold', fontsize=10)

    ax2.set_xticks(x)
    ax2.set_xticklabels(names, color='#c9d1d9', fontsize=11, weight='bold')
    ax2.tick_params(colors='#8b949e')
    ax2_twin.tick_params(colors='#8b949e')
    ax2.set_title('Portfolio Risk vs Return Efficiency: Extreme Drawdown Suppression', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax2.grid(True, linestyle=':', alpha=0.25, color='#8b949e')

    plt.tight_layout(pad=3.0)
    chart_p_path = os.path.join(reports_dir, 'chart_multi_asset_portfolio_1pct.png')
    plt.savefig(chart_p_path, dpi=300, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"Saved Portfolio Chart to: {chart_p_path}")

    # Save JSON
    json_path = os.path.join(reports_dir, 'multi_asset_portfolio_1pct_results.json')
    with open(json_path, 'w') as f:
        json.dump(portfolio_results, f, indent=2)
    print(f"Saved Portfolio JSON to: {json_path}")

    mt5.shutdown()
    return portfolio_results, chart_p_path

if __name__ == '__main__':
    main()
