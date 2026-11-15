"""
TESTING VWAP AS AN INSTITUTIONAL REGIME FILTER FOR THE SNIPER
Can VWAP elevate our Champion Snipers (12:00 Frankfurt & 13:00 London)
by filtering out counter-trend whipsaws?
Institutional Rule:
- Only arm BUY STOP if Anchor Price > Day VWAP (Institutions are net long)
- Only arm SELL STOP if Anchor Price < Day VWAP (Institutions are net short)
"""

import pandas as pd
import numpy as np
from optimize_intraday_vwap_scalper import load_full_day_data, analyze

def simulate_sniper_with_vwap_filter(asset_data, anchor_hour, anchor_minute, vwap_filter=True, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=15, order_cutoff_m=0):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    daily_results = []
    trade_stats = []
    rr = tp_mult / sl_mult
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Find anchor bar
            anchor_idx = -1
            for idx, b in enumerate(bars):
                if b['ist_hour'] == anchor_hour and b['ist_minute'] == anchor_minute:
                    anchor_idx = idx
                    break
                    
            if anchor_idx == -1 or anchor_idx >= len(bars) - 6:
                continue
                
            base_p = bars[anchor_idx]['close']
            atr_v = bars[anchor_idx]['atr']
            vwap_v = bars[anchor_idx]['vwap_day']
            
            if atr_v <= 0 or np.isnan(atr_v) or np.isnan(vwap_v):
                continue
                
            entry_dist = entry_mult * atr_v
            sl_dist = sl_mult * atr_v
            tp_dist = tp_mult * atr_v
            
            buy_trig = base_p + entry_dist
            sell_trig = base_p - entry_dist
            
            # VWAP Filter Decision
            allow_buy = True
            allow_sell = True
            if vwap_filter:
                allow_buy = (base_p >= vwap_v)   # Only buy if above Day VWAP
                allow_sell = (base_p <= vwap_v)  # Only sell if below Day VWAP
                
            active = None
            
            for i in range(anchor_idx + 1, len(bars)):
                b = bars[i]
                h = b['ist_hour']
                m = b['ist_minute']
                
                # Auto square off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        pnl_r = (b['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        day_trades += 1
                        trade_stats.append(pnl_r)
                        active = None
                    break
                    
                if active is None:
                    if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
                        break
                    if allow_buy and b['high'] >= buy_trig:
                        active = {'type': 'BUY', 'entry': buy_trig, 'sl': buy_trig - sl_dist, 'tp': buy_trig + tp_dist}
                    elif allow_sell and b['low'] <= sell_trig:
                        active = {'type': 'SELL', 'entry': sell_trig, 'sl': sell_trig + sl_dist, 'tp': sell_trig - tp_dist}
                else:
                    ttype = active['type']
                    if ttype == 'BUY':
                        if b['high'] >= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif b['low'] <= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                    else: # SELL
                        if b['low'] <= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif b['high'] >= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_res = pd.DataFrame(daily_results)
    return df_res, trade_stats

def main():
    asset_data = load_full_day_data()
    
    print("\n" + "=" * 100)
    print("TESTING IMPACT OF VWAP FILTER ON 12:00 PM & 1:00 PM SNIPERS")
    print("=" * 100)
    
    # 1. 13:00 Sniper Unfiltered vs VWAP Filtered
    df_13_raw, ts_13_raw = simulate_sniper_with_vwap_filter(asset_data, 13, 0, vwap_filter=False)
    r_13_raw = analyze(df_13_raw, ts_13_raw, "1:00 PM Sniper (Raw / Bi-Directional)")
    
    df_13_vwap, ts_13_vwap = simulate_sniper_with_vwap_filter(asset_data, 13, 0, vwap_filter=True)
    r_13_vwap = analyze(df_13_vwap, ts_13_vwap, "1:00 PM Sniper (VWAP Directional Filter)")

    # 2. 12:00 Sniper Unfiltered vs VWAP Filtered
    df_12_raw, ts_12_raw = simulate_sniper_with_vwap_filter(asset_data, 12, 0, vwap_filter=False, order_cutoff_h=12, order_cutoff_m=55)
    r_12_raw = analyze(df_12_raw, ts_12_raw, "12:00 PM Frankfurt (Raw / Bi-Directional)")
    
    df_12_vwap, ts_12_vwap = simulate_sniper_with_vwap_filter(asset_data, 12, 0, vwap_filter=True, order_cutoff_h=12, order_cutoff_m=55)
    r_12_vwap = analyze(df_12_vwap, ts_12_vwap, "12:00 PM Frankfurt (VWAP Directional Filter)")

    print(f"{'STRATEGY':<45} | {'ROI/YR':<10} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5}")
    print("-" * 100)
    for r in [r_13_raw, r_13_vwap, r_12_raw, r_12_vwap]:
        print(f"{r['label']:<45} | {r['avg_annual']:+8.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['pf']:<5}")
    print("=" * 100)

if __name__ == "__main__":
    main()

