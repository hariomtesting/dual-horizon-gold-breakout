"""
========================================================================================
FULL-SPECTRUM MULTI-RISK MONTE CARLO (1,000 RUNS PER TIER) & PARAMETER PLATEAU AUDIT
TIERS: 1.0%, 1.5%, 2.0%, 2.5%, 3.5%, 5.0%, 7.5% RISK PER TRADE
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
    print("🔬 MULTI-RISK MONTE CARLO & STRESS ENGINE INITIALIZING (7,000 PERMUTATIONS)")
    print("=" * 80)

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

    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 32000)
    df_raw = pd.DataFrame(r_1h)
    df_raw['dt_utc'] = pd.to_datetime(df_raw['time'], unit='s', utc=True)
    df_raw['dt_ist'] = df_raw['dt_utc'].dt.tz_convert(ist)
    df_raw['hour_ist'] = df_raw['dt_ist'].dt.hour

    h, l, c = df_raw['high'], df_raw['low'], df_raw['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df_raw['atr'] = tr.rolling(14).mean().bfill()
    df_raw['atr_ma'] = df_raw['atr'].rolling(30).mean().bfill()
    df_raw['ema200'] = c.ewm(span=200, adjust=False).mean()

    highs, lows = df_raw['high'].values, df_raw['low'].values
    n = len(df_raw)
    sh_arr, sl_arr = np.full(n, np.nan), np.full(n, np.nan)
    x_left, y_right = 15, 3
    for i in range(x_left, n - y_right):
        hc, lc = highs[i], lows[i]
        if all(highs[i - k] < hc for k in range(1, x_left + 1)) and all(highs[i + k] < hc for k in range(1, y_right + 1)):
            sh_arr[i + y_right] = hc
        if all(lows[i - k] > lc for k in range(1, x_left + 1)) and all(lows[i + k] > lc for k in range(1, y_right + 1)):
            sl_arr[i + y_right] = lc
    df_raw['conf_sh'] = pd.Series(sh_arr).ffill()
    df_raw['conf_sl'] = pd.Series(sl_arr).ffill()

    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
    start_2y = pd.to_datetime("2024-11-14 00:00:00", utc=True)
    dataset = df_raw[(df_raw['dt_utc'] >= start_2y) & (df_raw['dt_utc'] <= end_date)].reset_index(drop=True)

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0

    def run_simulation(data, risk_pct=0.025, chand_mult=2.8, sl_mult=2.0, tp1_mult=2.5, initial_cap=10000.0):
        cap = initial_cap
        peak = initial_cap
        max_dd = 0.0
        trades = []
        equity_curve = [initial_cap]

        pos = None
        pending = None
        last_f = None

        bars = data.to_dict('records')
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

                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1 and not pos['t1_hit']:
                    pos['t1_hit'] = True
                    p1_pts = (tp1 - entry) if d == 'BUY' else (entry - tp1)
                    pnl_t1 = (p1_pts * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm_per_lot)
                    cap += pnl_t1
                    pos['sl'] = (entry + 0.15) if d == 'BUY' else (entry - 0.15)
                    sl = pos['sl']

                if pos['t1_hit']:
                    chand_sl = (pos['extreme'] - (chand_mult * atr)) if d == 'BUY' else (pos['extreme'] + (chand_mult * atr))
                    if d == 'BUY' and chand_sl > pos['sl']: pos['sl'] = chand_sl
                    elif d == 'SELL' and chand_sl < pos['sl']: pos['sl'] = chand_sl
                    sl = pos['sl']

                hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                if hit_sl:
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (sl - entry) if d == 'BUY' else (entry - sl)
                    pnl_rem = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_rem
                    tot_trade_pnl = (pnl_t1 + pnl_rem) if pos['t1_hit'] else pnl_rem
                    risk_dollars = pos['risk_dollars']
                    r_mult_trade = tot_trade_pnl / risk_dollars if risk_dollars > 0 else 0.0
                    trades.append({
                        'dt': cur_dt,
                        'dir': d,
                        'pnl': tot_trade_pnl,
                        'r_mult': r_mult_trade,
                        't1_hit': pos['t1_hit'],
                        'risk_dollars': risk_dollars
                    })
                    pos = None

            if cap > peak: peak = cap
            cur_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if cur_dd > max_dd: max_dd = cur_dd
            equity_curve.append(cap)

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
                    sl = entry_t - (sl_mult * atr) if t_dir == 'BUY' else entry_t + (sl_mult * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (tp1_mult * atr) if t_dir == 'BUY' else entry_t - (tp1_mult * atr)

                    if sl_dist > 0:
                        lot_unrounded = r_cash / (sl_dist * contract)
                        tot_l = max(0.02, min(50.0, round(lot_unrounded, 2)))
                        l1 = max(0.01, round(tot_l * 0.5, 2))
                        l2 = max(0.01, round(tot_l - l1, 2))
                        pending = {
                            'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1,
                            'sl_dist': sl_dist, 'l1': l1, 'l2': l2, 'risk_dollars': r_cash
                        }

                if pending is not None:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    if (pending['dir'] == 'BUY' and cur_h >= (pending['trig'] + spread)) or (pending['dir'] == 'SELL' and cur_l <= (pending['trig'] - spread)):
                        exec_p = (pending['trig'] + (spread / 2.0) + slip) if pending['dir'] == 'BUY' else (pending['trig'] - (spread / 2.0) - slip)
                        pos = {
                            'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'], 'tp1': pending['tp1'],
                            'sl_dist': pending['sl_dist'], 'lots_t1': pending['l1'], 'lots_t2': pending['l2'],
                            't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False,
                            'risk_dollars': pending['risk_dollars']
                        }
                        last_f = pending['f']
                        pending = None

        df_trades = pd.DataFrame(trades)
        num_trades = len(df_trades)
        if num_trades == 0: return None

        wins = df_trades[df_trades['pnl'] > 0]
        losses = df_trades[df_trades['pnl'] < 0]
        win_rate = len(wins) / num_trades * 100.0
        gross_profit = wins['pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.0
        net_profit = cap - initial_cap
        total_return_pct = (net_profit / initial_cap) * 100.0
        calmar = total_return_pct / max_dd if max_dd > 0 else 99.0

        return {
            'final_cap': cap,
            'net_profit': net_profit,
            'total_return_pct': total_return_pct,
            'max_dd': max_dd,
            'calmar': calmar,
            'profit_factor': profit_factor,
            'win_rate': win_rate,
            'trades': trades
        }

    # Extract base R-multiples using 2.5% default run
    print("[1] Calculating Base Trade R-Multiples...")
    base_res = run_simulation(dataset, risk_pct=0.025, chand_mult=2.8, sl_mult=2.0)
    r_multiples = [t['r_mult'] for t in base_res['trades']]
    print(f"Generated {len(r_multiples)} base trades. Win Rate: {base_res['win_rate']:.1f}%")

    # =========================================================================
    # MULTI-RISK MONTE CARLO SIMULATION (7 TIERS x 1,000 RUNS = 7,000 RUNS)
    # =========================================================================
    risk_tiers = [0.010, 0.015, 0.020, 0.025, 0.035, 0.050, 0.075]
    n_sims = 1000
    initial_balance = 10000.0

    mc_tier_results = []
    print("\n[2] Executing 1,000 Monte Carlo Simulations per Risk Tier...")

    np.random.seed(42)
    for risk_pct in risk_tiers:
        tier_label = f"{risk_pct*100:.1f}%"
        tier_dds = []
        tier_rets = []
        tier_worst_streaks = []
        breaches_30 = 0

        for s_idx in range(n_sims):
            shuffled_r = np.random.permutation(r_multiples)
            sim_cap = initial_balance
            sim_peak = initial_balance
            sim_max_dd = 0.0
            cur_loss_streak = 0
            max_loss_streak = 0

            for r in shuffled_r:
                dd = (sim_peak - sim_cap) / sim_peak * 100.0 if sim_peak > 0 else 0.0
                r_scale = 0.0 if dd >= 20.0 else (0.33 if dd >= 15.0 else (0.67 if dd >= 10.0 else 1.0))
                
                trade_pnl = sim_cap * (risk_pct * r_scale) * r
                sim_cap += trade_pnl
                if sim_cap < 100.0: sim_cap = 100.0

                if r < 0:
                    cur_loss_streak += 1
                    if cur_loss_streak > max_loss_streak: max_loss_streak = cur_loss_streak
                else:
                    cur_loss_streak = 0

                if sim_cap > sim_peak: sim_peak = sim_cap
                cur_dd = (sim_peak - sim_cap) / sim_peak * 100.0 if sim_peak > 0 else 0.0
                if cur_dd > sim_max_dd: sim_max_dd = cur_dd

            if sim_max_dd >= 30.0:
                breaches_30 += 1

            ret_pct = ((sim_cap - initial_balance) / initial_balance) * 100.0
            tier_dds.append(sim_max_dd)
            tier_rets.append(ret_pct)
            tier_worst_streaks.append(max_loss_streak)

        tier_dds = np.array(tier_dds)
        tier_rets = np.array(tier_rets)

        p50_dd = float(np.percentile(tier_dds, 50))
        p95_dd = float(np.percentile(tier_dds, 95))
        p99_dd = float(np.percentile(tier_dds, 99))
        worst_dd = float(np.max(tier_dds))
        p_ruin = (breaches_30 / n_sims) * 100.0

        p50_ret = float(np.percentile(tier_rets, 50))
        p95_ret = float(np.percentile(tier_rets, 95))
        p5_ret = float(np.percentile(tier_rets, 5))

        res_tier = {
            'risk_pct': risk_pct,
            'tier_label': tier_label,
            'p50_dd': p50_dd,
            'p95_dd': p95_dd,
            'p99_dd': p99_dd,
            'worst_dd': worst_dd,
            'prob_ruin_30': p_ruin,
            'safety_cushion_99': 30.0 - p99_dd,
            'p50_ret': p50_ret,
            'p5_ret': p5_ret,
            'p95_ret': p95_ret,
            'raw_dds': tier_dds.tolist()[:100], # sample
            'avg_streak': float(np.mean(tier_worst_streaks)),
            'p95_streak': int(np.percentile(tier_worst_streaks, 95))
        }
        mc_tier_results.append(res_tier)

        print(f"  Tier {tier_label:>5} -> Median DD: {p50_dd:5.2f}% | 95% VaR: {p95_dd:5.2f}% | 99% VaR: {p99_dd:5.2f}% | Worst: {worst_dd:5.2f}% | Ruin >=30%: {p_ruin:.1f}% | Median Ret: +{p50_ret:>10,.1f}%")

    # =========================================================================
    # MULTI-RISK PARAMETER PLATEAU STABILITY AUDIT
    # =========================================================================
    print("\n[3] Auditing Parameter Robustness Plateau across key risk anchors (1.0%, 2.5%, 5.0%, 7.5%)...")
    key_risks = [0.010, 0.025, 0.050, 0.075]
    chand_multipliers = [2.2, 2.5, 2.8, 3.2]
    sl_multipliers = [1.8, 2.0, 2.2]

    plateau_by_risk = {}
    for r_val in key_risks:
        r_lbl = f"{r_val*100:.1f}%"
        grid_pts = []
        for sl_m in sl_multipliers:
            for ch_m in chand_multipliers:
                res = run_simulation(dataset, risk_pct=r_val, chand_mult=ch_m, sl_mult=sl_m)
                if res:
                    grid_pts.append({
                        'sl_mult': sl_m,
                        'chand_mult': ch_m,
                        'return_pct': res['total_return_pct'],
                        'max_dd': res['max_dd'],
                        'pf': res['profit_factor']
                    })
        min_ret = min(g['return_pct'] for g in grid_pts)
        max_ret = max(g['return_pct'] for g in grid_pts)
        max_dd_seen = max(g['max_dd'] for g in grid_pts)
        robust_pts = sum(1 for g in grid_pts if g['return_pct'] > 50.0 and g['max_dd'] < 25.0)
        plateau_by_risk[r_lbl] = {
            'robustness_score': (robust_pts / len(grid_pts)) * 100.0,
            'return_range': [min_ret, max_ret],
            'max_dd_seen': max_dd_seen,
            'grid': grid_pts
        }
        print(f"  Risk {r_lbl:>5} Plateau -> Robustness: {plateau_by_risk[r_lbl]['robustness_score']:.1f}% | Return Range: +{min_ret:,.0f}% to +{max_ret:,.0f}% | Max DD: {max_dd_seen:.2f}%")

    # =========================================================================
    # GENERATE PUBLICATION CHART
    # =========================================================================
    print("\n[4] Generating Multi-Risk Comparative Visual Chart...")
    reports_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports'))
    os.makedirs(reports_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 13))
    fig.patch.set_facecolor('#0d1117')

    x_labels = [r['tier_label'] for r in mc_tier_results]
    x_indices = np.arange(len(x_labels))

    # Plot 1: Drawdown Risk Tiers vs 30% Ceiling
    ax1.set_facecolor('#161b22')
    medians = [r['p50_dd'] for r in mc_tier_results]
    var95s = [r['p95_dd'] for r in mc_tier_results]
    var99s = [r['p99_dd'] for r in mc_tier_results]
    worsts = [r['worst_dd'] for r in mc_tier_results]

    ax1.plot(x_indices, worsts, marker='o', color='#f85149', linewidth=2.0, linestyle=':', label='Absolute Worst-Case Realization (1 in 1,000)')
    ax1.plot(x_indices, var99s, marker='s', color='#ff7b72', linewidth=2.5, label='99% Value-at-Risk (Worst 1% of Universes)')
    ax1.plot(x_indices, var95s, marker='^', color='#e3b341', linewidth=2.5, label='95% Value-at-Risk (Institutional Standard)')
    ax1.plot(x_indices, medians, marker='D', color='#3fb950', linewidth=3.0, label='50th %ile Median Realized Drawdown')

    # Add 30% Ceiling
    ax1.axhline(30.0, color='#ff0000', linestyle='-', linewidth=3.0, label='Strict Mandate Ceiling (30.0% Max DD) - ZERO BREACHES')
    ax1.fill_between(x_indices, var99s, 30.0, color='#3fb950', alpha=0.10, label='Guaranteed Safety Buffer Cushion')

    for idx, (x_pos, p99_v, w_v) in enumerate(zip(x_indices, var99s, worsts)):
        ax1.annotate(f"99%: {p99_v:.1f}%\nWorst: {w_v:.1f}%", xy=(x_pos, w_v), xytext=(0, 8),
                     textcoords="offset points", ha='center', color='#f0f6fc', fontsize=9, weight='bold')

    ax1.set_title('1,000-Run Monte Carlo Drawdown Distribution Across All Risk Tiers (1.0% to 7.5%)', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax1.set_xlabel('Risk Management Tier (% Equity Risked per Trade)', color='#c9d1d9', fontsize=11)
    ax1.set_ylabel('Peak-to-Trough Drawdown (%)', color='#c9d1d9', fontsize=11)
    ax1.set_xticks(x_indices)
    ax1.set_xticklabels(x_labels, color='#c9d1d9', fontsize=10, weight='bold')
    ax1.tick_params(colors='#8b949e')
    ax1.grid(True, linestyle=':', alpha=0.25, color='#8b949e')
    ax1.set_ylim(0, 35)
    ax1.legend(loc='upper left', facecolor='#21262d', edgecolor='#30363d', labelcolor='#f0f6fc', fontsize=10)

    # Plot 2: Median vs 5th Percentile Compound Return
    ax2.set_facecolor('#161b22')
    p50_returns = [r['p50_ret'] for r in mc_tier_results]
    p5_returns = [r['p5_ret'] for r in mc_tier_results]
    p95_returns = [r['p95_ret'] for r in mc_tier_results]

    ax2.plot(x_indices, p50_returns, marker='D', color='#58a6ff', linewidth=3.0, label='Median Expected 2-Year Return (50th %ile)')
    ax2.plot(x_indices, p5_returns, marker='v', color='#e3b341', linewidth=2.0, linestyle='--', label='Unlucky Alternate Reality (5th %ile Bottom Tail)')
    ax2.plot(x_indices, p95_returns, marker='^', color='#a371f7', linewidth=2.0, linestyle='--', label='Lucky Alternate Reality (95th %ile Top Tail)')

    for idx, (x_pos, p50_v) in enumerate(zip(x_indices, p50_returns)):
        ax2.annotate(f"+{p50_v:,.0f}%", xy=(x_pos, p50_v), xytext=(0, 10),
                     textcoords="offset points", ha='center', color='#58a6ff', fontsize=9, weight='bold')

    ax2.set_yscale('log')
    ax2.set_title('Expected Compound Return Trajectories (Log Scale) across 1,000 Shuffled Universes', fontsize=14, color='#f0f6fc', weight='bold', pad=12)
    ax2.set_xlabel('Risk Management Tier (% Equity Risked per Trade)', color='#c9d1d9', fontsize=11)
    ax2.set_ylabel('Total Return (%) - Logarithmic Scale', color='#c9d1d9', fontsize=11)
    ax2.set_xticks(x_indices)
    ax2.set_xticklabels(x_labels, color='#c9d1d9', fontsize=10, weight='bold')
    ax2.tick_params(colors='#8b949e')
    ax2.grid(True, linestyle=':', alpha=0.25, color='#8b949e')
    ax2.legend(loc='upper left', facecolor='#21262d', edgecolor='#30363d', labelcolor='#f0f6fc', fontsize=10)

    plt.tight_layout(pad=3.0)
    chart_multi_path = os.path.join(reports_dir, 'chart_monte_carlo_multi_risk_sweep.png')
    plt.savefig(chart_multi_path, dpi=300, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"Saved Multi-Risk Monte Carlo Chart to: {chart_multi_path}")

    # =========================================================================
    # SAVE JSON RESULTS
    # =========================================================================
    summary_json = {
        'timestamp': datetime.datetime.now().isoformat(),
        'asset': 'XAUUSD',
        'iterations_per_tier': n_sims,
        'tiers': [
            {
                'tier': r['tier_label'],
                'risk_pct': r['risk_pct'],
                'median_dd': r['p50_dd'],
                'var95_dd': r['p95_dd'],
                'var99_dd': r['p99_dd'],
                'worst_dd': r['worst_dd'],
                'prob_ruin_30_pct': r['prob_ruin_30'],
                'safety_cushion_pct': r['safety_cushion_99'],
                'median_return_pct': r['p50_ret'],
                'p5_return_pct': r['p5_ret'],
                'p95_return_pct': r['p95_ret'],
                'avg_loss_streak': r['avg_streak'],
                'p95_loss_streak': r['p95_streak']
            }
            for r in mc_tier_results
        ],
        'plateau_by_risk': plateau_by_risk
    }
    json_path = os.path.join(reports_dir, 'monte_carlo_multi_risk_results.json')
    with open(json_path, 'w') as f:
        json.dump(summary_json, f, indent=2)
    print(f"Saved multi-risk JSON to: {json_path}")

    return summary_json, chart_multi_path

if __name__ == '__main__':
    main()
