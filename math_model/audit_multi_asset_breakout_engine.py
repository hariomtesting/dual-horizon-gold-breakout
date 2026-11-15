"""
========================================================================================
MULTI-ASSET QUANT AUDIT: DUAL-HORIZON BREAKOUT & CHANDELIER RUNNER
ASSETS: BTCUSD, ETHUSD, EURUSD, GBPUSD, NAS100, XAUUSD
BROKER: VantageMarkets-Demo | BASELINE RISK: 2.5% PER TRADE
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
    print("🌍 MULTI-ASSET QUANTITATIVE STRATEGY AUDIT INITIALIZING")
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

    # Assets to test
    asset_configs = [
        {'symbol': 'BTCUSD', 'name': 'Bitcoin Bullion (24/7)', 'is_24_7': True, 'spread_pts': 18.0, 'comm': 5.0, 'contract': 1.0, 'min_lot': 0.01, 'max_lot': 20.0},
        {'symbol': 'BTCUSD', 'name': 'Bitcoin (10-16 IST Window)', 'is_24_7': False, 'spread_pts': 18.0, 'comm': 5.0, 'contract': 1.0, 'min_lot': 0.01, 'max_lot': 20.0},
        {'symbol': 'ETHUSD', 'name': 'Ethereum Bullion (24/7)', 'is_24_7': True, 'spread_pts': 1.5, 'comm': 3.0, 'contract': 1.0, 'min_lot': 0.01, 'max_lot': 50.0},
        {'symbol': 'EURUSD', 'name': 'Euro / US Dollar FX', 'is_24_7': False, 'spread_pts': 0.00015, 'comm': 6.0, 'contract': 100000.0, 'min_lot': 0.01, 'max_lot': 50.0},
        {'symbol': 'GBPUSD', 'name': 'British Pound / USD FX', 'is_24_7': False, 'spread_pts': 0.00020, 'comm': 6.0, 'contract': 100000.0, 'min_lot': 0.01, 'max_lot': 50.0},
        {'symbol': 'NAS100.r', 'name': 'Nasdaq 100 Tech Index', 'is_24_7': False, 'spread_pts': 1.8, 'comm': 4.0, 'contract': 1.0, 'min_lot': 0.1, 'max_lot': 50.0},
        {'symbol': 'XAUUSD', 'name': 'Gold Bullion (Baseline Benchmark)', 'is_24_7': False, 'spread_pts': 0.28, 'comm': 6.0, 'contract': 100.0, 'min_lot': 0.01, 'max_lot': 50.0}
    ]

    all_results = []
    equity_trajectories = {}

    for cfg in asset_configs:
        sym = cfg['symbol']
        label = cfg['name']
        print(f"\n--- Testing Asset: {label} [{sym}] ---")

        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 16000)
        if rates is None or len(rates) < 1000:
            print(f"Skipping {sym}: Insufficient bars")
            continue

        df = pd.DataFrame(rates)
        df['dt_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df['dt_ist'] = df['dt_utc'].dt.tz_convert(ist)
        df['hour_ist'] = df['dt_ist'].dt.hour

        # Calculate Indicators
        h, l, c = df['high'], df['low'], df['close']
        tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
        df['atr'] = tr.rolling(14).mean().bfill()
        df['atr_ma'] = df['atr'].rolling(30).mean().bfill()
        df['ema200'] = c.ewm(span=200, adjust=False).mean()

        # Zero-lookahead 15L/3R Fractals
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

        # Filter to common 1.5 to 2 Year test window
        start_date = pd.to_datetime("2025-02-25 00:00:00", utc=True)
        end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
        dataset = df[(df['dt_utc'] >= start_date) & (df['dt_utc'] <= end_date)].reset_index(drop=True)

        if len(dataset) < 500:
            dataset = df.iloc[-8000:].reset_index(drop=True)

        # Simulation
        initial_cap = 10000.0
        cap = initial_cap
        peak = initial_cap
        max_dd = 0.0
        trades = []
        equity_curve = [initial_cap]
        dates_curve = [dataset['dt_utc'].iloc[0]]

        spread = cfg['spread_pts']
        comm = cfg['comm']
        contract = cfg['contract']
        min_lot = cfg['min_lot']
        max_lot = cfg['max_lot']
        is_24_7 = cfg['is_24_7']
        risk_pct = 0.025 # 2.5% base risk

        pos = None
        pending = None
        last_f = None

        bars = dataset.to_dict('records')
        for idx in range(1, len(bars)):
            row = bars[idx]
            prev_row = bars[idx - 1]
            cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt_utc']
            cur_hour_ist = row['hour_ist']
            atr, atr_ma, ema = prev_row['atr'], prev_row['atr_ma'], prev_row['ema200']
            if atr <= 0: continue

            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))

            # 1. Active Position Management (Chandelier Trail)
            if pos is not None:
                pos['bars'] += 1
                d = pos['dir']
                entry, sl, tp1, sl_dist = pos['entry'], pos['sl'], pos['tp1'], pos['sl_dist']

                if d == 'BUY': pos['extreme'] = max(pos['extreme'], cur_h)
                else: pos['extreme'] = min(pos['extreme'], cur_l)

                # Salvage gate
                if not pos['t1_hit'] and pos['bars'] >= 2:
                    adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                    if adv >= (sl_dist * 0.45):
                        pos['salvage_active'] = True
                    if pos.get('salvage_active', False):
                        tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                        if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                            tp1 = tight_tp
                            pos['tp1'] = tp1

                # TP1 Check
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1 and not pos['t1_hit']:
                    pos['t1_hit'] = True
                    p1_pts = (tp1 - entry) if d == 'BUY' else (entry - tp1)
                    pnl_t1 = (p1_pts * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm)
                    cap += pnl_t1
                    # Lock Break-Even
                    be_buffer = 0.15 * (atr / 2.0)
                    pos['sl'] = (entry + be_buffer) if d == 'BUY' else (entry - be_buffer)
                    sl = pos['sl']

                # Chandelier Trailing
                if pos['t1_hit']:
                    chand_sl = (pos['extreme'] - (2.8 * atr)) if d == 'BUY' else (pos['extreme'] + (2.8 * atr))
                    if d == 'BUY' and chand_sl > pos['sl']: pos['sl'] = chand_sl
                    elif d == 'SELL' and chand_sl < pos['sl']: pos['sl'] = chand_sl
                    sl = pos['sl']

                # Stop or Trail Hit
                hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                if hit_sl:
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (sl - entry) if d == 'BUY' else (entry - sl)
                    pnl_rem = (pts * rem_lots * contract) - (rem_lots * comm)
                    cap += pnl_rem
                    tot_trade_pnl = (pnl_t1 + pnl_rem) if pos['t1_hit'] else pnl_rem
                    trades.append({'dt': cur_dt, 'dir': d, 'pnl': tot_trade_pnl, 't1_hit': pos['t1_hit']})
                    pos = None

            # Drawdown update
            if cap > peak: peak = cap
            cur_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if cur_dd > max_dd: max_dd = cur_dd
            equity_curve.append(cap)
            dates_curve.append(cur_dt)

            # 2. Pre-Breakout Orders
            if not is_24_7:
                is_entry_window = (10 <= cur_hour_ist < 16)
                if not is_entry_window:
                    pending = None
                    continue

            sw_h, sw_l = prev_row['conf_sh'], prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l): continue

            if pos is None and r_mult > 0.0:
                t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                t_f = sw_h if t_dir == 'BUY' else sw_l

                if pending is not None and (pending['dir'] != t_dir or abs(pending['f'] - t_f) > (0.1 * atr)):
                    pending = None

                if pending is None and t_dir is not None and last_f != t_f:
                    r_cash = cap * (risk_pct * r_mult)
                    entry_t = sw_h if t_dir == 'BUY' else sw_l
                    sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)

                    if sl_dist > 0:
                        lot_unrounded = r_cash / (sl_dist * contract)
                        tot_l = max(min_lot * 2.0, min(max_lot, round(lot_unrounded, 2)))
                        l1 = max(min_lot, round(tot_l * 0.5, 2))
                        l2 = max(min_lot, round(tot_l - l1, 2))
                        pending = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': l1, 'l2': l2}

                if pending is not None:
                    slip = 0.05 * atr
                    if (pending['dir'] == 'BUY' and cur_h >= (pending['trig'] + spread)) or (pending['dir'] == 'SELL' and cur_l <= (pending['trig'] - spread)):
                        exec_p = (pending['trig'] + (spread / 2.0) + slip) if pending['dir'] == 'BUY' else (pending['trig'] - (spread / 2.0) - slip)
                        pos = {'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'], 'tp1': pending['tp1'], 'sl_dist': pending['sl_dist'], 'lots_t1': pending['l1'], 'lots_t2': pending['l2'], 't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False}
                        last_f = pending['f']
                        pending = None

        df_trades = pd.DataFrame(trades)
        num_trades = len(df_trades)
        if num_trades == 0:
            print(f"No trades for {sym}")
            continue

        wins = df_trades[df_trades['pnl'] > 0]
        losses = df_trades[df_trades['pnl'] < 0]
        t1_hits = df_trades[df_trades['t1_hit'] == True]

        win_rate = len(wins) / num_trades * 100.0
        t1_win_rate = len(t1_hits) / num_trades * 100.0
        gross_profit = wins['pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.0
        net_profit = cap - initial_cap
        total_return_pct = (net_profit / initial_cap) * 100.0
        calmar = total_return_pct / max_dd if max_dd > 0 else 99.0

        res_item = {
            'symbol': sym,
            'name': label,
            'is_24_7': is_24_7,
            'total_trades': num_trades,
            'win_rate': win_rate,
            'tp1_rate': t1_win_rate,
            'profit_factor': profit_factor,
            'total_return_pct': total_return_pct,
            'max_dd': max_dd,
            'calmar': calmar,
            'final_equity': cap
        }
        all_results.append(res_item)
        equity_trajectories[label] = {'dates': dates_curve, 'equity': equity_curve}

        print(f"Result [{label}]:")
        print(f"  • Total Return  : +{total_return_pct:>10,.1f}% (Final: ${cap:,.2f})")
        print(f"  • Max Drawdown  : {max_dd:>6.2f}% (Mandate < 30%: {'PASS' if max_dd < 30 else 'FAIL'})")
        print(f"  • Win Rate      : {win_rate:>6.1f}% (TP1 Lock: {t1_win_rate:.1f}%)")
        print(f"  • Profit Factor : {profit_factor:>6.2f} | Trades: {num_trades}")

    # =========================================================================
    # GENERATE PUBLICATION CHART
    # =========================================================================
    print("\nGenerating Multi-Asset Performance Visual Chart...")
    reports_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports'))
    os.makedirs(reports_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 14))
    fig.patch.set_facecolor('#0d1117')

    # Plot 1: Equity Curves
    ax1.set_facecolor('#161b22')
    colors = ['#f7931a', '#e3b341', '#627eea', '#388bfd', '#a371f7', '#39d353', '#f0883e']
    for idx, (lbl, traj) in enumerate(equity_trajectories.items()):
        c = colors[idx % len(colors)]
        eq = traj['equity']
        # Resample to common 100 points
        indices = np.linspace(0, len(eq) - 1, 100).astype(int)
        sampled_eq = [eq[i] for i in indices]
        ax1.plot(sampled_eq, label=lbl, color=c, linewidth=2.2)

    ax1.set_yscale('log')
    ax1.set_title('Cross-Asset Performance Comparison: Dual-Horizon Chandelier Engine ($10,000 Initial)', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax1.set_ylabel('Account Equity ($ USD - Log Scale)', color='#c9d1d9', fontsize=11)
    ax1.set_xlabel('Normalized Trading Progress (Percentiles)', color='#c9d1d9', fontsize=11)
    ax1.tick_params(colors='#8b949e')
    ax1.grid(True, linestyle=':', alpha=0.25, color='#8b949e')
    ax1.legend(loc='upper left', facecolor='#21262d', edgecolor='#30363d', labelcolor='#f0f6fc', fontsize=9)

    # Plot 2: Return vs Drawdown Bar Chart
    ax2.set_facecolor('#161b22')
    labels = [r['name'].split('(')[0].strip() for r in all_results]
    x = np.arange(len(labels))
    width = 0.35

    returns = [min(r['total_return_pct'], 10000.0) for r in all_results]
    dds = [r['max_dd'] for r in all_results]

    rects1 = ax2.bar(x - width/2, returns, width, label='Total Return (%) [Cap 10,000%]', color='#3fb950', alpha=0.85)
    ax2_twin = ax2.twinx()
    rects2 = ax2_twin.bar(x + width/2, dds, width, label='Max Drawdown (%) [Ceiling 30%]', color='#f85149', alpha=0.85)
    ax2_twin.axhline(30.0, color='#ff0000', linestyle='--', linewidth=2.0, label='30% Max DD Ceiling')

    ax2.set_ylabel('Total Return (%)', color='#3fb950', fontsize=11)
    ax2_twin.set_ylabel('Max Drawdown (%)', color='#f85149', fontsize=11)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, color='#c9d1d9', fontsize=9, weight='bold', rotation=15)
    ax2.tick_params(colors='#8b949e')
    ax2_twin.tick_params(colors='#8b949e')
    ax2_twin.set_ylim(0, 35)

    ax2.set_title('Risk-Adjusted Efficiency Across Asset Classes (Crypto, FX, Indices, Gold)', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax2.grid(True, linestyle=':', alpha=0.25, color='#8b949e')

    plt.tight_layout(pad=3.0)
    chart_path = os.path.join(reports_dir, 'chart_multi_asset_comparison.png')
    plt.savefig(chart_path, dpi=300, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"Saved Multi-Asset Chart to: {chart_path}")

    # Save JSON
    json_path = os.path.join(reports_dir, 'multi_asset_backtest_results.json')
    with open(json_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"Saved Multi-Asset JSON to: {json_path}")

    mt5.shutdown()
    return all_results, chart_path

if __name__ == '__main__':
    main()
