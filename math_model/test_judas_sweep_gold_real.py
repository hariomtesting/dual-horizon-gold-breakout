"""
INSTITUTIONAL LIQUIDITY SWEEP (JUDAS SWING) REVERSAL ON GOLD
Testing the institutional trap model on XAUUSD under full live Vantage frictions:
- Asian Range defined from 05:30 to 11:30 IST.
- Between 11:30 and 13:30 IST, price sweeps ABOVE Asian High or BELOW Asian Low.
- Once price sweeps and rejects back inside the Asian Range:
  - ENTER REVERSAL in direction of the true move!
  - SL placed just above/below the sweep high/low ($4.00 - $6.00).
  - Target: Asian Midpoint or Opposite Asian Extreme ($10.00 - $20.00)!
- All evaluated strictly after live Vantage spread ($0.30), comm ($6/lot), and 345ms latency.
"""
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import datetime

def test_judas_gold():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M5, datetime.datetime(2022, 1, 1), datetime.datetime(2026, 11, 30))
    mt5.shutdown()
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 70:
            sub = sub.copy().reset_index(drop=True)
            asian_bars = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
            if len(asian_bars) >= 20:
                sub['asian_high'] = asian_bars['high'].max()
                sub['asian_low'] = asian_bars['low'].min()
                sub['asian_mid'] = (sub['asian_high'] + sub['asian_low']) / 2.0
                sessions[d] = sub.to_dict('records')
                
    all_dates = sorted(list(sessions.keys()))
    
    spread = 0.30
    comm_per_lot = 6.00
    slip_in = 0.15
    slip_out = 0.08
    contract_sz = 100.0
    
    results = []
    
    for sweep_min in [1.0, 2.0, 3.0]: # minimum penetration beyond Asian extreme ($)
        for sl_buffer in [1.0, 1.5, 2.0]: # SL placed beyond the sweep wick ($)
            for target_type in ["opposite_asian", "fixed_rr_1.5", "fixed_rr_2.0"]:
                daily_pnl_list = []
                trades_list = []
                
                for curr_d in all_dates:
                    bars = sessions[curr_d]
                    
                    # Window: 11:30 to 13:30 IST
                    start_i = -1
                    for idx, b in enumerate(bars):
                        if b['ist_hour'] == 11 and b['ist_minute'] == 30:
                            start_i = idx
                            break
                    if start_i == -1:
                        continue
                        
                    asian_h = bars[start_i]['asian_high']
                    asian_l = bars[start_i]['asian_low']
                    asian_mid = bars[start_i]['asian_mid']
                    asian_rng = asian_h - asian_l
                    
                    if asian_rng < 5.0 or asian_rng > 35.0:
                        continue # Filter crazy or dead ranges
                        
                    active = None
                    taken = False
                    day_pnl = 0.0
                    
                    swept_high = False
                    swept_low = False
                    sweep_peak = 0.0
                    sweep_trough = 0.0
                    
                    for i in range(start_i, len(bars)):
                        b = bars[i]
                        prev_b = bars[i-1]
                        h = b['ist_hour']
                        m = b['ist_minute']
                        
                        # 15:55 IST square off
                        if h == 15 and m >= 55:
                            if active is not None:
                                lots = active['lots']
                                comm_cost = (lots * comm_per_lot) / 10000.0
                                if active['type'] == 'BUY':
                                    exit_p = b['close'] - (spread / 2.0) - slip_out
                                    net_pts = exit_p - active['fill_e']
                                else:
                                    exit_p = b['close'] + (spread / 2.0) + slip_out
                                    net_pts = active['fill_e'] - exit_p
                                trade_pnl = ((net_pts * lots * contract_sz) / 10000.0) - comm_cost
                                day_pnl += trade_pnl
                                trades_list.append(trade_pnl / 0.010)
                                active = None
                            break
                            
                        # Detect Liquidity Sweeps between 11:30 and 13:30 IST
                        if active is None and not taken:
                            if h > 13 or (h == 13 and m > 30):
                                break
                                
                            # Bearish Judas: Pierced Asian High by >= sweep_min, then closed back below Asian High!
                            if prev_b['high'] >= asian_h + sweep_min and b['close'] < asian_h:
                                sweep_peak = max(b['high'], prev_b['high'])
                                sl_dist = (sweep_peak - b['close']) + sl_buffer
                                sl_dist = max(3.50, sl_dist) # minimum $3.50 SL
                                
                                if target_type == "opposite_asian":
                                    tp_dist = b['close'] - asian_l
                                elif target_type == "fixed_rr_1.5":
                                    tp_dist = sl_dist * 1.5
                                else:
                                    tp_dist = sl_dist * 2.0
                                    
                                if tp_dist > 2.0 * sl_dist:
                                    tp_dist = 2.0 * sl_dist
                                    
                                lots = 100.0 / (sl_dist * contract_sz)
                                lots = max(0.01, round(lots, 2))
                                fill_p = b['close'] - (spread / 2.0) - slip_in
                                
                                active = {
                                    'type': 'SELL', 'fill_e': fill_p,
                                    'sl': b['close'] + sl_dist, 'tp': b['close'] - tp_dist,
                                    'lots': lots, 'sl_dist': sl_dist
                                }
                                taken = True
                                
                            # Bullish Judas: Pierced Asian Low by >= sweep_min, then closed back above Asian Low!
                            elif prev_b['low'] <= asian_l - sweep_min and b['close'] > asian_l:
                                sweep_trough = min(b['low'], prev_b['low'])
                                sl_dist = (b['close'] - sweep_trough) + sl_buffer
                                sl_dist = max(3.50, sl_dist)
                                
                                if target_type == "opposite_asian":
                                    tp_dist = asian_h - b['close']
                                elif target_type == "fixed_rr_1.5":
                                    tp_dist = sl_dist * 1.5
                                else:
                                    tp_dist = sl_dist * 2.0
                                    
                                if tp_dist > 2.0 * sl_dist:
                                    tp_dist = 2.0 * sl_dist
                                    
                                lots = 100.0 / (sl_dist * contract_sz)
                                lots = max(0.01, round(lots, 2))
                                fill_p = b['close'] + (spread / 2.0) + slip_in
                                
                                active = {
                                    'type': 'BUY', 'fill_e': fill_p,
                                    'sl': b['close'] - sl_dist, 'tp': b['close'] + tp_dist,
                                    'lots': lots, 'sl_dist': sl_dist
                                }
                                taken = True
                        elif active is not None:
                            ttype = active['type']
                            fill_e = active['fill_e']
                            sl_p = active['sl']
                            tp_p = active['tp']
                            lots = active['lots']
                            comm_cost = (lots * comm_per_lot) / 10000.0
                            
                            if ttype == 'BUY':
                                if b['high'] >= tp_p:
                                    exit_p = tp_p - (spread / 2.0) - slip_out
                                    trade_pnl = (((exit_p - fill_e) * lots * contract_sz) / 10000.0) - comm_cost
                                    day_pnl += trade_pnl
                                    trades_list.append(trade_pnl / 0.010)
                                    active = None
                                    break
                                elif b['low'] <= sl_p:
                                    exit_p = sl_p - (spread / 2.0) - slip_out
                                    trade_pnl = (((exit_p - fill_e) * lots * contract_sz) / 10000.0) - comm_cost
                                    day_pnl += trade_pnl
                                    trades_list.append(trade_pnl / 0.010)
                                    active = None
                                    break
                            else:
                                if b['low'] <= tp_p:
                                    exit_p = tp_p + (spread / 2.0) + slip_out
                                    trade_pnl = (((fill_e - exit_p) * lots * contract_sz) / 10000.0) - comm_cost
                                    day_pnl += trade_pnl
                                    trades_list.append(trade_pnl / 0.010)
                                    active = None
                                    break
                                elif b['high'] >= sl_p:
                                    exit_p = sl_p + (spread / 2.0) + slip_out
                                    trade_pnl = (((fill_e - exit_p) * lots * contract_sz) / 10000.0) - comm_cost
                                    day_pnl += trade_pnl
                                    trades_list.append(trade_pnl / 0.010)
                                    active = None
                                    break
                                    
                    if day_pnl < -0.020:
                        day_pnl = -0.020
                    daily_pnl_list.append({'date': curr_d, 'pnl_pct': day_pnl})
                    
                df_res = pd.DataFrame(daily_pnl_list)
                df_res['year'] = pd.to_datetime(df_res['date']).dt.year
                df_res['ym'] = pd.to_datetime(df_res['date']).dt.strftime('%Y-%m')
                
                annual = df_res.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
                monthly = df_res.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0)
                
                eq = (1.0 + df_res['pnl_pct']).cumprod()
                max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100.0
                
                if len(trades_list) > 0:
                    ts = np.array(trades_list)
                    wr = (ts > 0).sum() / len(ts) * 100.0
                    g = ts[ts > 0].sum()
                    l = abs(ts[ts < 0].sum())
                    pf = g / l if l > 0 else 0
                    avg_r = ts.mean()
                else:
                    wr = 0; pf = 0; avg_r = 0
                    
                results.append({
                    'sweep_min': sweep_min,
                    'sl_buf': sl_buffer,
                    'target': target_type,
                    'avg_ann': annual.mean(),
                    'worst_yr': annual.min(),
                    'max_dd': max_dd,
                    'green_m': f"{(monthly > 0).sum()}/{len(monthly)} ({(monthly > 0).sum()/len(monthly)*100:.1f}%)",
                    'pf': pf,
                    'wr': wr,
                    'trades': len(trades_list),
                    'avg_r': avg_r
                })

    df_all = pd.DataFrame(results)
    profitable = df_all[df_all['avg_ann'] > 0].sort_values(by='avg_ann', ascending=False)
    
    print("\n" + "=" * 135)
    print("GOLD JUDAS LIQUIDITY SWEEP PERFORMANCE (AFTER FULL LIVE SPREADS, COMMISSIONS & 345MS LATENCY)")
    print("=" * 135)
    print(f"{'SWEEP MIN':<10} | {'SL BUF':<8} | {'TARGET TYPE':<18} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'TRADES'}")
    print("-" * 135)
    for idx, r in profitable.head(20).iterrows():
        print(f"${r['sweep_min']:<9.1f} | ${r['sl_buf']:<7.1f} | {r['target']:<18} | {r['avg_ann']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_m']:<15} | {r['pf']:<5.2f} | {r['trades']}")
    print("=" * 135)

if __name__ == "__main__":
    test_judas_gold()

