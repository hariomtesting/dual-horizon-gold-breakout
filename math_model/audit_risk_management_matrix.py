"""
========================================================================================
QUANTITATIVE RISK MANAGEMENT SWEEP & MULTI-RETURN SENSITIVITY AUDIT
ASSET: XAUUSD (Spot Gold Bullion) | STRATEGY: 10:00 - 16:00 IST HOLD & CHANDELIER TRAIL
========================================================================================
Analyzes all return types, win rate, peak drawdown, Calmar ratio, recovery factor,
and mathematical safety margins across risk levels: 1.0%, 1.5%, 2.0%, 2.5%, 3.5%, 5.0%, 7.5%.
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

def run_risk_matrix_audit():
    # Load MT5 config
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_account.json'))
    init_kwargs = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r') as f:
            c = json.load(f)
            if c.get('path') and os.path.exists(c['path']): init_kwargs['path'] = c['path']
            if c.get('login') and c.get('password') and c.get('server'):
                init_kwargs['login'] = int(c['login'])
                init_kwargs['password'] = c['password']
                init_kwargs['server'] = c['server']
                init_kwargs['timeout'] = 20000

    if not mt5.initialize(**init_kwargs):
        if not mt5.initialize():
            print("[ERROR] Failed to initialize MT5")
            return

    acc = mt5.account_info()
    print(f"Connected to MT5: Login {acc.login} | Server {acc.server} | Balance: ${acc.balance:,.2f}")

    utc = pytz.timezone('UTC')
    ist = pytz.timezone('Asia/Kolkata')

    # Fetch 1H Gold data (30,000 bars covers 5+ years)
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 32000)
    df = pd.DataFrame(r_1h)
    df['dt_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df['dt_ist'] = df['dt_utc'].dt.tz_convert(ist)
    df['hour_ist'] = df['dt_ist'].dt.hour

    # Indicators
    h, l, c = df['high'], df['low'], df['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df['atr'] = tr.rolling(14).mean().bfill()
    df['atr_ma'] = df['atr'].rolling(30).mean().bfill()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()

    # Zero-lookahead fractals 15L/3R
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

    # Time horizons: 2-Year (Nov 2024 - Nov 2026) and 5-Year (Nov 2021 - Nov 2026)
    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
    start_2y = pd.to_datetime("2024-11-14 00:00:00", utc=True)
    start_5y = pd.to_datetime("2021-11-14 00:00:00", utc=True)

    df_2y = df[(df['dt_utc'] >= start_2y) & (df['dt_utc'] <= end_date)].reset_index(drop=True)
    df_5y = df[(df['dt_utc'] >= start_5y) & (df['dt_utc'] <= end_date)].reset_index(drop=True)

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0

    def simulate_engine(dataset, risk_pct, initial_cap=10000.0):
        random.seed(42)
        cap = initial_cap
        peak = initial_cap
        max_dd = 0.0
        max_dollar_dd = 0.0
        trades = []
        equity_curve = [initial_cap]
        dates_curve = [dataset['dt_utc'].iloc[0]]

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
            is_exp = (atr > 1.4 * atr_ma)

            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))

            # 1. Manage Active Position (Hold & Trail past 16:00 IST)
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

                # TP1 Hit
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1 and not pos['t1_hit']:
                    pos['t1_hit'] = True
                    p1_pts = (tp1 - entry) if d == 'BUY' else (entry - tp1)
                    pnl_t1 = (p1_pts * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm_per_lot)
                    cap += pnl_t1
                    pos['sl'] = (entry + 0.15) if d == 'BUY' else (entry - 0.15)
                    sl = pos['sl']

                # Chandelier Trailing Stop
                if pos['t1_hit']:
                    chand_sl = (pos['extreme'] - (2.8 * atr)) if d == 'BUY' else (pos['extreme'] + (2.8 * atr))
                    if d == 'BUY' and chand_sl > pos['sl']: pos['sl'] = chand_sl
                    elif d == 'SELL' and chand_sl < pos['sl']: pos['sl'] = chand_sl
                    sl = pos['sl']

                # Stop-Loss or Trail Exit
                hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                if hit_sl:
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (sl - entry) if d == 'BUY' else (entry - sl)
                    pnl_rem = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_rem
                    tot_trade_pnl = (pnl_t1 + pnl_rem) if pos['t1_hit'] else pnl_rem
                    trades.append({'dt': cur_dt, 'dir': d, 'pnl': tot_trade_pnl, 't1_hit': pos['t1_hit']})
                    pos = None

            # Update Peak & Drawdown
            if cap > peak: peak = cap
            cur_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if cur_dd > max_dd: max_dd = cur_dd
            cur_dollar_dd = peak - cap
            if cur_dollar_dd > max_dollar_dd: max_dollar_dd = cur_dollar_dd

            equity_curve.append(cap)
            dates_curve.append(cur_dt)

            # 2. Manage Pre-Breakout Orders (Only between 10:00 and 16:00 IST)
            is_entry_window = (10 <= cur_hour_ist < 16)
            if not is_entry_window:
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
                    r_cash = cap * (risk_pct * r_mult)
                    entry_t = sw_h if t_dir == 'BUY' else sw_l
                    sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)

                    if sl_dist > 0:
                        # Dynamic sizing with safety limits
                        lot_unrounded = r_cash / (sl_dist * contract)
                        tot_l = max(0.02, min(50.0, round(lot_unrounded, 2)))
                        l1 = max(0.01, round(tot_l * 0.5, 2))
                        l2 = max(0.01, round(tot_l - l1, 2))
                        pending = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': l1, 'l2': l2}

                if pending is not None:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    if (pending['dir'] == 'BUY' and cur_h >= (pending['trig'] + spread)) or (pending['dir'] == 'SELL' and cur_l <= (pending['trig'] - spread)):
                        exec_p = (pending['trig'] + (spread / 2.0) + slip) if pending['dir'] == 'BUY' else (pending['trig'] - (spread / 2.0) - slip)
                        pos = {'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'], 'tp1': pending['tp1'], 'sl_dist': pending['sl_dist'], 'lots_t1': pending['l1'], 'lots_t2': pending['l2'], 't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False}
                        last_f = pending['f']
                        pending = None

        # Post-Processing Performance Statistics
        df_trades = pd.DataFrame(trades)
        num_trades = len(df_trades)
        if num_trades == 0:
            return None

        wins = df_trades[df_trades['pnl'] > 0]
        losses = df_trades[df_trades['pnl'] < 0]
        win_rate = len(wins) / num_trades * 100.0
        gross_profit = wins['pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.0
        net_profit = cap - initial_cap
        total_return_pct = (net_profit / initial_cap) * 100.0

        # Annualized metrics
        years = (dataset['dt_utc'].iloc[-1] - dataset['dt_utc'].iloc[0]).days / 365.25
        cagr = ((cap / initial_cap) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and cap > 0 else 0.0
        monthly_return = ((1.0 + cagr / 100.0) ** (1.0 / 12.0) - 1.0) * 100.0

        # Sharpe, Sortino, Calmar
        # Calculate daily returns from equity curve
        df_eq = pd.DataFrame({'dt': dates_curve, 'equity': equity_curve}).drop_duplicates('dt')
        df_eq['date'] = df_eq['dt'].dt.date
        df_daily = df_eq.groupby('date')['equity'].last().pct_change().dropna()
        daily_rf = 0.04 / 252.0
        excess_daily = df_daily - daily_rf
        sharpe = (excess_daily.mean() / df_daily.std() * np.sqrt(252.0)) if df_daily.std() > 0 else 0.0
        downside = df_daily[df_daily < 0]
        sortino = (excess_daily.mean() / downside.std() * np.sqrt(252.0)) if (len(downside) > 0 and downside.std() > 0) else 0.0
        calmar = (cagr / max_dd) if max_dd > 0 else 99.0
        recovery_factor = (net_profit / max_dollar_dd) if max_dollar_dd > 0 else 99.0

        # Max consecutive losses
        pnl_arr = df_trades['pnl'].values
        max_cons_losses = 0
        cur_loss_streak = 0
        for p in pnl_arr:
            if p < 0:
                cur_loss_streak += 1
                if cur_loss_streak > max_cons_losses: max_cons_losses = cur_loss_streak
            else:
                cur_loss_streak = 0

        # Average trade metrics
        avg_trade = net_profit / num_trades
        avg_win = wins['pnl'].mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses['pnl'].mean()) if len(losses) > 0 else 0.0
        win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        return {
            'risk_pct': risk_pct * 100.0,
            'initial_balance': initial_cap,
            'ending_equity': cap,
            'net_profit': net_profit,
            'total_return_pct': total_return_pct,
            'cagr': cagr,
            'monthly_return': monthly_return,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_trades': num_trades,
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'max_dd_pct': max_dd,
            'max_dollar_dd': max_dollar_dd,
            'safety_margin_pct': 30.0 - max_dd,
            'sharpe': sharpe,
            'sortino': sortino,
            'calmar': calmar,
            'recovery_factor': recovery_factor,
            'max_cons_losses': max_cons_losses,
            'avg_trade': avg_trade,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'win_loss_ratio': win_loss_ratio,
            'equity_curve': equity_curve,
            'dates_curve': dates_curve
        }

    # Evaluate across risk levels
    risk_levels = [0.010, 0.015, 0.020, 0.025, 0.035, 0.050, 0.075]

    print("\n" + "=" * 115)
    print("🚀 RUNNING 2-YEAR RISK MANAGEMENT SWEEP (NOV 2024 - NOV 2026 | 10:00 - 16:00 IST HOLD & TRAIL)")
    print("=" * 115)
    results_2y = []
    for r in risk_levels:
        res = simulate_engine(df_2y, r, initial_cap=10000.0)
        results_2y.append(res)
        print(f"Risk: {res['risk_pct']:4.1f}% | Win Rate: {res['win_rate']:5.1f}% | PF: {res['profit_factor']:5.2f} | Final: ${res['ending_equity']:14,.2f} | Return: +{res['total_return_pct']:8.1f}% | CAGR: +{res['cagr']:6.1f}% | Max DD: {res['max_dd_pct']:5.2f}% | Safety Cushion: {res['safety_margin_pct']:5.2f}%")

    print("\n" + "=" * 115)
    print("🏆 RUNNING 5-YEAR LONGITUDINAL STRESS TEST SWEEP (NOV 2021 - NOV 2026 | 10:00 - 16:00 IST HOLD & TRAIL)")
    print("=" * 115)
    results_5y = []
    for r in risk_levels:
        res = simulate_engine(df_5y, r, initial_cap=10000.0)
        results_5y.append(res)
        print(f"Risk: {res['risk_pct']:4.1f}% | Win Rate: {res['win_rate']:5.1f}% | PF: {res['profit_factor']:5.2f} | Final: ${res['ending_equity']:14,.2f} | Return: +{res['total_return_pct']:9.1f}% | CAGR: +{res['cagr']:6.1f}% | Max DD: {res['max_dd_pct']:5.2f}% | Safety Cushion: {res['safety_margin_pct']:5.2f}%")

    # Generate Chart
    os.makedirs('reports', exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    plt.subplots_adjust(hspace=0.28, wspace=0.22)

    # Plot 1: Risk vs CAGR & Max DD (2-Year)
    ax1 = axes[0, 0]
    risks = [r['risk_pct'] for r in results_2y]
    cagrs_2y = [r['cagr'] for r in results_2y]
    dds_2y = [r['max_dd_pct'] for r in results_2y]
    
    color = '#1f77b4'
    ax1.set_xlabel('Risk Management Parameter per Trade (%)', fontsize=11, fontweight='bold')
    ax1.set_ylabel('Annualized Return (CAGR %)', color=color, fontsize=11, fontweight='bold')
    line1 = ax1.plot(risks, cagrs_2y, color=color, marker='o', linewidth=2.5, label='CAGR (%)')
    ax1.tick_params(axis='y', labelcolor=color)
    ax1.grid(True, linestyle='--', alpha=0.5)

    ax1_twin = ax1.twinx()
    color2 = '#d62728'
    ax1_twin.set_ylabel('Peak Max Drawdown (%)', color=color2, fontsize=11, fontweight='bold')
    line2 = ax1_twin.plot(risks, dds_2y, color=color2, marker='s', linewidth=2.5, linestyle='--', label='Max Drawdown (%)')
    ax1_twin.axhline(30.0, color='darkred', linestyle=':', linewidth=2, label='30.0% Mandate Ceiling')
    ax1_twin.tick_params(axis='y', labelcolor=color2)
    ax1.set_title('2-Year Horizon: Risk vs CAGR vs Drawdown (10-16 IST)', fontsize=12, fontweight='bold')

    # Plot 2: Risk vs CAGR & Max DD (5-Year)
    ax2 = axes[0, 1]
    cagrs_5y = [r['cagr'] for r in results_5y]
    dds_5y = [r['max_dd_pct'] for r in results_5y]
    
    color = '#2ca02c'
    ax2.set_xlabel('Risk Management Parameter per Trade (%)', fontsize=11, fontweight='bold')
    ax2.set_ylabel('Annualized Return (CAGR %)', color=color, fontsize=11, fontweight='bold')
    ax2.plot(risks, cagrs_5y, color=color, marker='^', linewidth=2.5, label='5-Year CAGR (%)')
    ax2.tick_params(axis='y', labelcolor=color)
    ax2.grid(True, linestyle='--', alpha=0.5)

    ax2_twin = ax2.twinx()
    color2 = '#d62728'
    ax2_twin.set_ylabel('Peak Max Drawdown (%)', color=color2, fontsize=11, fontweight='bold')
    ax2_twin.plot(risks, dds_5y, color=color2, marker='s', linewidth=2.5, linestyle='--', label='Max Drawdown (%)')
    ax2_twin.axhline(30.0, color='darkred', linestyle=':', linewidth=2, label='30.0% Mandate Ceiling')
    ax2_twin.tick_params(axis='y', labelcolor=color2)
    ax2.set_title('5-Year Longitudinal: Risk vs CAGR vs Drawdown', fontsize=12, fontweight='bold')

    # Plot 3: 2-Year Equity Curves Across Key Risk Tiers (Log Scale)
    ax3 = axes[1, 0]
    selected_indices = [0, 3, 5, 6] # 1.0%, 2.5%, 5.0%, 7.5%
    tier_colors = ['#7f7f7f', '#1f77b4', '#ff7f0e', '#d62728']
    for idx, col in zip(selected_indices, tier_colors):
        r_item = results_2y[idx]
        ax3.plot(r_item['dates_curve'], r_item['equity_curve'], label=f"Risk {r_item['risk_pct']:.1f}% (Final: ${r_item['ending_equity']:,.0f})", color=col, linewidth=2)
    ax3.set_yscale('log')
    ax3.set_title('2-Year Capital Growth Trajectories (Log Scale)', fontsize=12, fontweight='bold')
    ax3.set_xlabel('Date (Nov 2024 – Nov 2026)', fontsize=10)
    ax3.set_ylabel('Account Equity ($ USD Log)', fontsize=10)
    ax3.grid(True, which='both', linestyle='--', alpha=0.4)
    ax3.legend(loc='upper left', fontsize=9)

    # Plot 4: Risk-Return Efficiency (Calmar & Sharpe Ratios)
    ax4 = axes[1, 1]
    calmars = [r['calmar'] for r in results_2y]
    sharpes = [r['sharpe'] for r in results_2y]
    x_pos = np.arange(len(risks))
    width = 0.35

    ax4.bar(x_pos - width/2, sharpes, width, label='Sharpe Ratio (Daily)', color='#4a90e2')
    ax4.bar(x_pos + width/2, [c / 10.0 for c in calmars], width, label='Calmar Ratio (/10)', color='#50e3c2')
    ax4.set_xticks(x_pos)
    ax4.set_xticklabels([f"{r:.1f}%" for r in risks])
    ax4.set_xlabel('Risk Management Parameter', fontsize=10, fontweight='bold')
    ax4.set_ylabel('Risk-Adjusted Efficiency Score', fontsize=10)
    ax4.set_title('Institutional Efficiency Metrics (Sharpe & Calmar)', fontsize=12, fontweight='bold')
    ax4.grid(True, linestyle='--', alpha=0.5)
    ax4.legend(loc='upper right', fontsize=9)

    chart_path = os.path.abspath('reports/chart_risk_management_sweep_matrix.png')
    plt.savefig(chart_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"\n📊 Master Risk Analysis Chart saved to: {chart_path}")

    # Export Full JSON metrics for programmatic reporting
    export_payload = {
        'timestamp': datetime.datetime.now().isoformat(),
        'asset': 'XAUUSD',
        'account_id': DEMO_ACCOUNT_ID,
        'strategy': '10:00 to 16:00 IST Hold and Trail No-VPS',
        'results_2_year': [{k: v for k, v in r.items() if k not in ['equity_curve', 'dates_curve']} for r in results_2y],
        'results_5_year': [{k: v for k, v in r.items() if k not in ['equity_curve', 'dates_curve']} for r in results_5y],
        'chart_path': chart_path
    }
    
    with open('reports/risk_management_sweep_results.json', 'w') as f:
        json.dump(export_payload, f, indent=4)
    print("📁 Exported JSON metrics to: reports/risk_management_sweep_results.json")

    mt5.shutdown()

if __name__ == '__main__':
    run_risk_matrix_audit()
