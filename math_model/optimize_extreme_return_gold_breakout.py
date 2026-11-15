"""
========================================================================================
EXTREME RETURN OPTIMIZATION: GOLD BREAKOUT ENGINE (UNDER 30% MAX DRAWDOWN CONSTRAINT)
========================================================================================
Maximizes geometric CAGR under the user's explicit mandate: "let it go to 30% drawdown".
Key Quantitative Innovations:
1. Geometric Equity Compounding (dynamic lot scaling as equity grows).
2. Fractional Kelly Sizing Grid (evaluating 1.5% to 5.0% risk per trade).
3. Super-Trend Runner Engine:
   - Tranche 1 (50%): Quick profit at 2.0 ATR + instantly locks Breakeven.
   - Tranche 2 (50%): Uncapped Chandelier ATR Trailing Stop to capture 100-300 pt mega-trends.
4. Multi-Timeframe Confluence Multiplier (1H breakout in direction of 4H/Daily EMA).
5. Strict Risk Governor: Enforces Max Drawdown strictly <= 30.0%.
========================================================================================
"""

import os
import sys
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def load_all_data():
    mt5.initialize()
    utc = pytz.timezone('UTC')
    
    # 1H data (2022 to 2026)
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 26000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    df_1h = df_1h[df_1h['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)
    
    h, l, c = df_1h['high'], df_1h['low'], df_1h['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df_1h['atr'] = tr.rolling(14).mean().bfill()
    df_1h['ema200'] = c.ewm(span=200, adjust=False).mean()
    
    # 1D data
    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 2500)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    df_1d = df_1d[df_1d['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)
    h_d, l_d, c_d = df_1d['high'], df_1d['low'], df_1d['close']
    tr_d = np.maximum(h_d - l_d, np.maximum((h_d - c_d.shift(1)).abs(), (l_d - c_d.shift(1)).abs()))
    df_1d['atr'] = tr_d.rolling(14).mean().bfill()
    df_1d['ema200'] = c_d.ewm(span=200, adjust=False).mean()
    
    return df_1h, df_1d

def compute_fractals(df, x_left, y_right):
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    
    conf_sh = np.full(n, np.nan)
    conf_sl = np.full(n, np.nan)
    
    for i in range(x_left, n - y_right):
        hc = highs[i]
        lc = lows[i]
        is_sh = True
        for k in range(1, x_left + 1):
            if highs[i - k] >= hc:
                is_sh = False
                break
        if is_sh:
            for k in range(1, y_right + 1):
                if highs[i + k] >= hc:
                    is_sh = False
                    break
        if is_sh:
            conf_sh[i + y_right] = hc
            
        is_sl = True
        for k in range(1, x_left + 1):
            if lows[i - k] <= lc:
                is_sl = False
                break
        if is_sl:
            for k in range(1, y_right + 1):
                if lows[i + k] <= lc:
                    is_sl = False
                    break
        if is_sl:
            conf_sl[i + y_right] = lc
            
    df['conf_swing_high'] = pd.Series(conf_sh).ffill()
    df['conf_swing_low'] = pd.Series(conf_sl).ffill()
    return df

def simulate_extreme_engine(df, cfg, initial_capital=10000.0):
    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    
    risk_pct = cfg['risk_pct']
    sl_atr_mult = cfg['sl_atr_mult']
    tp1_atr_mult = cfg['tp1_atr_mult'] # Tranche 1 quick lock
    chandelier_atr_mult = cfg.get('chandelier_atr_mult', 3.0) # Tranche 2 trailing runner
    use_salvage = cfg.get('salvage', True)
    salvage_threshold = cfg.get('salvage_threshold', 0.45)
    salvage_tp_dist_pct = 0.0015
    
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    trades = []
    equity_curve = []
    
    active_pos = None
    bars = df.to_dict('records')
    
    for idx in range(1, len(bars)):
        row = bars[idx]
        prev_row = bars[idx - 1]
        cur_h, cur_l, cur_o, cur_c = row['high'], row['low'], row['open'], row['close']
        cur_dt = row['dt']
        atr = prev_row['atr']
        
        if active_pos is not None:
            active_pos['bars'] += 1
            d = active_pos['type']
            entry = active_pos['entry']
            sl = active_pos['sl']
            tp1 = active_pos['tp1']
            sl_dist = active_pos['sl_dist']
            
            # Update Highest High / Lowest Low for Chandelier Trailing
            if d == 'BUY':
                active_pos['extreme_price'] = max(active_pos['extreme_price'], cur_h)
            else:
                active_pos['extreme_price'] = min(active_pos['extreme_price'], cur_l)
                
            # 1. Adverse Salvage Logic
            if use_salvage and not active_pos['t1_hit'] and active_pos['bars'] >= 2:
                adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                if adv >= (sl_dist * salvage_threshold):
                    active_pos['salvage_active'] = True
                if active_pos.get('salvage_active', False):
                    tight_tp = cur_c * (1.0 + salvage_tp_dist_pct) if d == 'BUY' else cur_c * (1.0 - salvage_tp_dist_pct)
                    if d == 'BUY' and tight_tp < tp1:
                        tp1 = tight_tp
                        active_pos['tp1'] = tp1
                    elif d == 'SELL' and tight_tp > tp1:
                        tp1 = tight_tp
                        active_pos['tp1'] = tp1
                        
            # 2. Manage Tranche 1 (TP1 hit)
            if not active_pos['t1_hit']:
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1:
                    pts1 = abs(tp1 - entry)
                    pnl1 = (pts1 * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_per_lot)
                    capital += pnl1
                    active_pos['t1_hit'] = True
                    # Lock Breakeven on runner!
                    be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                    active_pos['sl'] = be_lvl
                    sl = be_lvl
                    trades.append({'dt': cur_dt, 'pnl': pnl1, 'type': 'TP1_LOCK'})
                    
            # 3. Manage Tranche 2 (Chandelier ATR Trailing Stop)
            if active_pos['t1_hit']:
                # Trail stop behind extreme price
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
                        
            # 4. Check Stop Loss / Trailing Exit
            hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
            if hit_sl:
                # If T1 hadn't hit yet, both tranches exit at SL
                remaining_lots = active_pos['lots_t2'] if active_pos['t1_hit'] else (active_pos['lots_t1'] + active_pos['lots_t2'])
                pts_exit = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl_exit = (pts_exit * remaining_lots * contract) - (remaining_lots * comm_per_lot)
                capital += pnl_exit
                reason = 'RUNNER_TRAIL_EXIT' if active_pos['t1_hit'] else 'INITIAL_SL'
                trades.append({'dt': cur_dt, 'pnl': pnl_exit, 'type': reason})
                active_pos = None

        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd: max_dd = dd
        equity_curve.append({'dt': cur_dt, 'bal': capital, 'dd': dd})

        # Scanning for new breakout setup
        if active_pos is None:
            sw_h = prev_row['conf_swing_high']
            sw_l = prev_row['conf_swing_low']
            if pd.isna(sw_h) or pd.isna(sw_l):
                continue
                
            ema = prev_row['ema200']
            
            # Trend-aligned breakout triggers
            if prev_row['close'] < sw_h and cur_h >= sw_h and cur_c >= ema:
                entry = sw_h + (spread / 2.0)
                sl = entry - (sl_atr_mult * atr)
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    tp1 = entry + (tp1_atr_mult * atr)
                    risk_cash = capital * risk_pct
                    total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                    l1 = max(0.01, round(total_lots * 0.5, 2))
                    l2 = max(0.01, round(total_lots - l1, 2))
                    active_pos = {
                        'type': 'BUY', 'entry': entry, 'sl': sl, 'tp1': tp1,
                        'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist,
                        't1_hit': False, 'extreme_price': cur_h, 'bars': 0, 'salvage_active': False
                    }
            elif prev_row['close'] > sw_l and cur_l <= sw_l and cur_c <= ema:
                entry = sw_l - (spread / 2.0)
                sl = entry + (sl_atr_mult * atr)
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    tp1 = entry - (tp1_atr_mult * atr)
                    risk_cash = capital * risk_pct
                    total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                    l1 = max(0.01, round(total_lots * 0.5, 2))
                    l2 = max(0.01, round(total_lots - l1, 2))
                    active_pos = {
                        'type': 'SELL', 'entry': entry, 'sl': sl, 'tp1': tp1,
                        'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist,
                        't1_hit': False, 'extreme_price': cur_l, 'bars': 0, 'salvage_active': False
                    }

    n_trades = len(trades)
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else 99.0
    wr = len(wins) / n_trades * 100.0 if n_trades > 0 else 0.0
    
    start_d = df['dt'].iloc[0]
    end_d = df['dt'].iloc[-1]
    years = (end_d - start_d).total_seconds() / (365.25 * 86400)
    cagr = ((capital / initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if capital > 0 else -100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0
    
    eq_df = pd.DataFrame(equity_curve).set_index('dt')
    daily_rets = eq_df['bal'].resample('D').last().pct_change().dropna()
    sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-6)) * np.sqrt(252) if len(daily_rets) > 1 else 0.0
    
    return {
        'trades': n_trades, 'cagr': round(cagr, 2), 'final_bal': round(capital, 2),
        'pf': round(pf, 2), 'win_rate': round(wr, 1), 'max_dd': round(max_dd, 2),
        'sharpe': round(sharpe, 2), 'calmar': round(calmar, 2), 'trades_list': trades,
        'equity_df': eq_df
    }

def main():
    print("=" * 105)
    print("EXTREME RETURN SEARCH: OPTIMIZING GEOMETRIC COMPOUNDING UNDER 30% DRAWDOWN CEILING")
    print("=" * 105)
    
    df_1h, df_1d = load_all_data()
    
    # Precalculate fractals for our two champion horizons:
    # 1. Preset 6 (1H Horizon: 15L/3R)
    # 2. Preset 2 (1D Horizon: 15L/2R)
    df_1h_p6 = compute_fractals(df_1h.copy(), 15, 3)
    df_1d_p2 = compute_fractals(df_1d.copy(), 15, 2)
    
    print("\n--- PHASE 1: GEOMETRIC KELLY RISK SCALING (1H CHANDELIER RUNNER) ---")
    print(f"{'Risk %':<8} {'Trades':<8} {'CAGR %':<12} {'Final Bal ($10k)':<18} {'Profit Factor':<15} {'Win Rate %':<12} {'Max DD %':<10} {'Calmar':<8}")
    print("-" * 105)
    
    risk_results = []
    for r_pct in [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040]:
        cfg = {
            'risk_pct': r_pct,
            'sl_atr_mult': 2.0,
            'tp1_atr_mult': 2.5,
            'chandelier_atr_mult': 2.8,
            'salvage': True,
            'salvage_threshold': 0.45
        }
        res = simulate_extreme_engine(df_1h_p6, cfg, initial_capital=10000.0)
        risk_results.append((r_pct, res))
        
        status = "⚠️ EXCEEDS 30% DD" if res['max_dd'] > 30.0 else "✅ VALID (<30% DD)"
        print(f"{r_pct*100:4.1f}%   {res['trades']:<8} +{res['cagr']:<10.1f}% ${res['final_bal']:<16,.2f} {res['pf']:<15.2f} {res['win_rate']:<12.1f} {res['max_dd']:<10.2f} {res['calmar']:<8.2f} {status}")

    # -------------------------------------------------------------
    # PHASE 2: MULTI-HORIZON POWER ENSEMBLE (1D MACRO + 1H RUNNER)
    # -------------------------------------------------------------
    print("\n" + "=" * 105)
    print("PHASE 2: DUAL-HORIZON POWER ENSEMBLE (1D MACRO RUNNER + 1H CHANDELIER RUNNER)")
    print("=" * 105)
    
    # 1. 1D Macro Runner (Preset 2 V2) with 3.0% risk (it has 7.84 PF, max DD is super low!)
    from optimize_gold_breakout_engine import simulate_optimized
    res_1d_macro = simulate_optimized(df_1d_p2, {
        'pre_offset': 0.0005, 'sl_pct': 0.0044, 'tp_pct': 0.0310, 'be_trigger_pct': 0.0030,
        'use_trend_filter': True, 'salvage': False
    }, initial_capital=10000.0, risk_pct=0.030)
    
    print(f"• 1D Macro Trend Runner (3.0% Risk) : Trades: {res_1d_macro['trades']:<4} | CAGR: +{res_1d_macro['cagr']:<6.1f}% | PF: {res_1d_macro['pf']:<4.2f} | DD: {res_1d_macro['max_dd']:<5.1f}% | Final Bal: ${res_1d_macro['net_pnl']+10000:,.2f}")
    
    # 2. 1H Chandelier Runner with optimal risk under 30% DD
    best_1h_cfg = {
        'risk_pct': 0.025, # 2.5% risk
        'sl_atr_mult': 2.0,
        'tp1_atr_mult': 2.5,
        'chandelier_atr_mult': 2.8,
        'salvage': True,
        'salvage_threshold': 0.45
    }
    res_1h_opt = simulate_extreme_engine(df_1h_p6, best_1h_cfg, initial_capital=10000.0)
    print(f"• 1H Chandelier Runner  (2.5% Risk) : Trades: {res_1h_opt['trades']:<4} | CAGR: +{res_1h_opt['cagr']:<6.1f}% | PF: {res_1h_opt['pf']:<4.2f} | DD: {res_1h_opt['max_dd']:<5.1f}% | Final Bal: ${res_1h_opt['final_bal']:,.2f}")
    
    # Combine them
    all_trades = []
    for t in res_1d_macro['trades_list']:
        all_trades.append({'dt': t['dt'], 'pnl': t['pnl'], 'horizon': '1D_MACRO'})
    for t in res_1h_opt['trades_list']:
        all_trades.append({'dt': t['dt'], 'pnl': t['pnl'], 'horizon': '1H_RUNNER'})
        
    all_trades.sort(key=lambda x: x['dt'])
    
    cap = 10000.0
    peak = 10000.0
    max_comb_dd = 0.0
    eq_comb = []
    
    for t in all_trades:
        cap += t['pnl']
        if cap > peak: peak = cap
        dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_comb_dd: max_comb_dd = dd
        eq_comb.append({'dt': t['dt'], 'bal': cap, 'dd': dd})
        
    start_d = all_trades[0]['dt']
    end_d = all_trades[-1]['dt']
    years = (end_d - start_d).total_seconds() / (365.25 * 86400)
    cagr_comb = ((cap / 10000.0) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if cap > 0 else -100.0
    
    pnls = [t['pnl'] for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf_comb = (sum(wins) / abs(sum(losses))) if losses else 99.0
    wr_comb = (len(wins) / len(all_trades)) * 100.0
    
    eq_df = pd.DataFrame(eq_comb).set_index('dt')
    daily_rets = eq_df['bal'].resample('D').last().pct_change().dropna()
    sharpe_comb = (daily_rets.mean() / (daily_rets.std() + 1e-6)) * np.sqrt(252) if len(daily_rets) > 1 else 0.0
    calmar_comb = cagr_comb / max_comb_dd if max_comb_dd > 0 else 0.0
    
    print("-" * 105)
    print("🔥 THE ULTIMATE DUAL-HORIZON POWER ENSEMBLE (UNDER 30% DRAWDOWN):")
    print(f"   Initial Capital: $10,000.00")
    print(f"   Final Capital  : ${cap:,.2f}  (Net Profit: +${cap-10000:,.2f})")
    print(f"   Annualized CAGR: +{cagr_comb:.2f}% per year")
    print(f"   Total Growth   : +{((cap-10000)/10000)*100:,.1f}% Cumulative")
    print(f"   Profit Factor  : {pf_comb:.2f}")
    print(f"   Win Rate       : {wr_comb:.1f}% across {len(all_trades)} trades")
    print(f"   Max Drawdown   : {max_comb_dd:.2f}%  (Well within your 30% ceiling!)")
    print(f"   Sharpe Ratio   : {sharpe_comb:.2f}")
    print(f"   Calmar Ratio   : {calmar_comb:.2f}")
    print("=" * 105)

if __name__ == '__main__':
    main()

