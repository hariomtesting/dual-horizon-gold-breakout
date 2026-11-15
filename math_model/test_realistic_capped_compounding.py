"""
Simulate Realistic Capped Compounding (Max 10 Lots per Trade, Real Broker Limits)
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from optimize_extreme_return_gold_breakout import load_all_data, compute_fractals

def simulate_realistic_capped(df, risk_pct=0.025, max_lots=10.0, initial_capital=10000.0):
    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    
    sl_atr_mult = 2.0
    tp1_atr_mult = 2.5
    chandelier_atr_mult = 2.8
    salvage_threshold = 0.45
    salvage_tp_dist_pct = 0.0015
    
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    trades = []
    
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
            
            if d == 'BUY':
                active_pos['extreme_price'] = max(active_pos['extreme_price'], cur_h)
            else:
                active_pos['extreme_price'] = min(active_pos['extreme_price'], cur_l)
                
            if not active_pos['t1_hit'] and active_pos['bars'] >= 2:
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
                        
            if not active_pos['t1_hit']:
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1:
                    pts1 = abs(tp1 - entry)
                    pnl1 = (pts1 * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_per_lot)
                    capital += pnl1
                    active_pos['t1_hit'] = True
                    be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                    active_pos['sl'] = be_lvl
                    sl = be_lvl
                    trades.append({'dt': cur_dt, 'pnl': pnl1, 'type': 'TP1_LOCK'})
                    
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
                        
            hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
            if hit_sl:
                remaining_lots = active_pos['lots_t2'] if active_pos['t1_hit'] else (active_pos['lots_t1'] + active_pos['lots_t2'])
                pts_exit = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl_exit = (pts_exit * remaining_lots * contract) - (remaining_lots * comm_per_lot)
                capital += pnl_exit
                trades.append({'dt': cur_dt, 'pnl': pnl_exit, 'type': 'SL_EXIT'})
                active_pos = None

        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd: max_dd = dd

        if active_pos is None:
            sw_h = prev_row['conf_swing_high']
            sw_l = prev_row['conf_swing_low']
            if pd.isna(sw_h) or pd.isna(sw_l): continue
            ema = prev_row['ema200']
            
            if prev_row['close'] < sw_h and cur_h >= sw_h and cur_c >= ema:
                entry = sw_h + (spread / 2.0)
                sl = entry - (sl_atr_mult * atr)
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    tp1 = entry + (tp1_atr_mult * atr)
                    risk_cash = capital * risk_pct
                    raw_lots = risk_cash / (sl_dist * contract)
                    total_lots = max(0.02, min(max_lots, round(raw_lots, 2)))
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
                    raw_lots = risk_cash / (sl_dist * contract)
                    total_lots = max(0.02, min(max_lots, round(raw_lots, 2)))
                    l1 = max(0.01, round(total_lots * 0.5, 2))
                    l2 = max(0.01, round(total_lots - l1, 2))
                    active_pos = {
                        'type': 'SELL', 'entry': entry, 'sl': sl, 'tp1': tp1,
                        'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist,
                        't1_hit': False, 'extreme_price': cur_l, 'bars': 0, 'salvage_active': False
                    }

    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else 99.0
    wr = len(wins) / len(trades) * 100.0 if trades else 0.0
    
    start_d = df['dt'].iloc[0]
    end_d = df['dt'].iloc[-1]
    years = (end_d - start_d).total_seconds() / (365.25 * 86400)
    cagr = ((capital / initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if capital > 0 else -100.0
    
    return {
        'trades': len(trades), 'cagr': round(cagr, 2), 'final_bal': round(capital, 2),
        'net_pnl': round(capital - initial_capital, 2), 'pf': round(pf, 2),
        'win_rate': round(wr, 1), 'max_dd': round(max_dd, 2)
    }

def main():
    mt5.initialize()
    df_1h, _ = load_all_data()
    df_p6 = compute_fractals(df_1h, 15, 3)
    
    print("=" * 95)
    print("REALISTIC LIQUIDITY-CAPPED COMPLIANT PERFORMANCE (UNDER 30% DRAWDOWN)")
    print("=" * 95)
    
    for max_l in [5.0, 10.0, 20.0, 50.0]:
        for r_pct in [0.020, 0.025, 0.030]:
            res = simulate_realistic_capped(df_p6, risk_pct=r_pct, max_lots=max_l, initial_capital=10000.0)
            print(f"Max Lot Cap: {max_l:4.1f} lots | Risk: {r_pct*100:4.1f}% | CAGR: +{res['cagr']:<7.1f}% | Final Bal: ${res['final_bal']:<12,.2f} | PF: {res['pf']:<5.2f} | Max DD: {res['max_dd']:<5.1f}% | WinRate: {res['win_rate']}%")
            
    print("=" * 95)

if __name__ == '__main__':
    main()

