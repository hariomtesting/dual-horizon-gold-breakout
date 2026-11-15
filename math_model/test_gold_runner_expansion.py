"""
INSTITUTIONAL RUNNER EXPANSION: GOLD SESSION SNIPER
Testing the professional multi-target architecture:
- 50% Take Profit at 1.8R ($12.00)
- 50% Runner trailed at 1.0x ATR ($6.00) to capture $25 - $40 expansion waves!
- All evaluated after full live Vantage spreads ($0.30), comm ($6/lot), and 345ms latency.
"""
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import datetime

def test_runner_expansion():
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
                sub['asian_range'] = sub['asian_high'] - sub['asian_low']
                sessions[d] = sub.to_dict('records')
                
    all_dates = sorted(list(sessions.keys()))
    
    spread = 0.30
    comm_per_lot = 6.00
    slip_in = 0.15
    slip_out = 0.08
    contract_sz = 100.0
    
    results = []
    
    for anchor_h, anchor_name in [(12, "12:00 Frankfurt"), (13, "13:00 London")]:
        for sl_dollars in [5.00, 6.00, 7.50, 9.00]:
            for tp1_mult in [1.5, 1.8, 2.0]:
                tp1_dist = sl_dollars * tp1_mult
                trail_dist = sl_dollars # trail buffer
                
                lots = 100.0 / (sl_dollars * contract_sz)
                lots = max(0.01, round(lots, 2))
                comm_cost = (lots * comm_per_lot) / 10000.0
                
                daily_pnl_list = []
                trades_list = []
                
                for curr_d in all_dates:
                    bars = sessions[curr_d]
                    
                    a_idx = -1
                    for idx, b in enumerate(bars):
                        if b['ist_hour'] == anchor_h and b['ist_minute'] == 0:
                            a_idx = idx
                            break
                    if a_idx == -1 or a_idx >= len(bars) - 6:
                        continue
                        
                    base_p = bars[a_idx]['close']
                    ema_val = bars[a_idx]['ema200']
                    
                    # Trend Direction
                    allow_buy = (base_p >= ema_val)
                    allow_sell = (base_p <= ema_val)
                    
                    entry_offset = 0.30 * sl_dollars
                    buy_trig = base_p + entry_offset
                    sell_trig = base_p - entry_offset
                    
                    active = None
                    day_pnl = 0.0
                    cutoff_h = 13 if anchor_h == 12 else 15
                    
                    for i in range(a_idx + 1, len(bars)):
                        b = bars[i]
                        h = b['ist_hour']
                        m = b['ist_minute']
                        
                        # 15:55 IST square off
                        if h == 15 and m >= 55:
                            if active is not None:
                                remaining_lots = active['lots_rem']
                                if remaining_lots > 0:
                                    if active['type'] == 'BUY':
                                        exit_p = b['close'] - (spread / 2.0) - slip_out
                                        net_pts = exit_p - active['fill_e']
                                    else:
                                        exit_p = b['close'] + (spread / 2.0) + slip_out
                                        net_pts = active['fill_e'] - exit_p
                                    trade_pnl = ((net_pts * remaining_lots * contract_sz) / 10000.0) - (active['comm_rem'])
                                    day_pnl += trade_pnl
                                    trades_list.append(trade_pnl / 0.010)
                                active = None
                            break
                            
                        if active is None:
                            if h >= cutoff_h:
                                break
                            if allow_buy and b['high'] >= buy_trig:
                                fill_p = buy_trig + (spread / 2.0) + slip_in
                                active = {
                                    'type': 'BUY', 'fill_e': fill_p,
                                    'sl': buy_trig - sl_dollars,
                                    'tp1': buy_trig + tp1_dist,
                                    'tp1_hit': False,
                                    'lots_total': lots,
                                    'lots_rem': lots,
                                    'comm_rem': comm_cost,
                                    'peak_p': fill_p
                                }
                            elif allow_sell and b['low'] <= sell_trig:
                                fill_p = sell_trig - (spread / 2.0) - slip_in
                                active = {
                                    'type': 'SELL', 'fill_e': fill_p,
                                    'sl': sell_trig + sl_dollars,
                                    'tp1': sell_trig - tp1_dist,
                                    'tp1_hit': False,
                                    'lots_total': lots,
                                    'lots_rem': lots,
                                    'comm_rem': comm_cost,
                                    'peak_p': fill_p
                                }
                        else:
                            ttype = active['type']
                            fill_e = active['fill_e']
                            sl_p = active['sl']
                            tp1_p = active['tp1']
                            
                            if ttype == 'BUY':
                                # Update peak price for trailing
                                if b['high'] > active['peak_p']:
                                    active['peak_p'] = b['high']
                                    if active['tp1_hit']: # trail runner
                                        new_sl = active['peak_p'] - trail_dist
                                        if new_sl > active['sl']:
                                            active['sl'] = new_sl
                                            
                                # Check TP1 hit (50% close)
                                if not active['tp1_hit'] and b['high'] >= tp1_p:
                                    half_lots = round(active['lots_rem'] / 2.0, 2)
                                    if half_lots > 0:
                                        exit_p = tp1_p - (spread / 2.0) - slip_out
                                        pnl_50 = (((exit_p - fill_e) * half_lots * contract_sz) / 10000.0) - (active['comm_rem'] / 2.0)
                                        day_pnl += pnl_50
                                        trades_list.append(pnl_50 / 0.010)
                                        active['lots_rem'] -= half_lots
                                        active['comm_rem'] -= (active['comm_rem'] / 2.0)
                                    active['tp1_hit'] = True
                                    # Move SL to Breakeven + spread for the runner
                                    active['sl'] = fill_e + spread + 0.10
                                    
                                # Check SL hit
                                if b['low'] <= active['sl']:
                                    exit_p = active['sl'] - (spread / 2.0) - slip_out
                                    rem_lots = active['lots_rem']
                                    pnl_rem = (((exit_p - fill_e) * rem_lots * contract_sz) / 10000.0) - active['comm_rem']
                                    day_pnl += pnl_rem
                                    trades_list.append(pnl_rem / 0.010)
                                    active = None
                                    break
                            else: # SELL
                                if b['low'] < active['peak_p']:
                                    active['peak_p'] = b['low']
                                    if active['tp1_hit']:
                                        new_sl = active['peak_p'] + trail_dist
                                        if new_sl < active['sl']:
                                            active['sl'] = new_sl
                                            
                                if not active['tp1_hit'] and b['low'] <= tp1_p:
                                    half_lots = round(active['lots_rem'] / 2.0, 2)
                                    if half_lots > 0:
                                        exit_p = tp1_p + (spread / 2.0) + slip_out
                                        pnl_50 = (((fill_e - exit_p) * half_lots * contract_sz) / 10000.0) - (active['comm_rem'] / 2.0)
                                        day_pnl += pnl_50
                                        trades_list.append(pnl_50 / 0.010)
                                        active['lots_rem'] -= half_lots
                                        active['comm_rem'] -= (active['comm_rem'] / 2.0)
                                    active['tp1_hit'] = True
                                    active['sl'] = fill_e - spread - 0.10
                                    
                                if b['high'] >= active['sl']:
                                    exit_p = active['sl'] + (spread / 2.0) + slip_out
                                    rem_lots = active['lots_rem']
                                    pnl_rem = (((fill_e - exit_p) * rem_lots * contract_sz) / 10000.0) - active['comm_rem']
                                    day_pnl += pnl_rem
                                    trades_list.append(pnl_rem / 0.010)
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
                    'anchor': anchor_name,
                    'sl': sl_dollars,
                    'tp1_mult': tp1_mult,
                    'tp1_val': tp1_dist,
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
    print("GOLD RUNNER ARCHITECTURE (50% TP1 + 50% TRAILING RUNNER UNDER LIVE VANTAGE FRICTIONS)")
    print("=" * 135)
    print(f"{'ANCHOR':<18} | {'SL ($)':<7} | {'TP1 MULT':<9} | {'TP1 ($)':<8} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'TRADES'}")
    print("-" * 135)
    for idx, r in profitable.head(20).iterrows():
        print(f"{r['anchor']:<18} | ${r['sl']:<6.2f} | {r['tp1_mult']:<9.1f} | ${r['tp1_val']:<7.2f} | {r['avg_ann']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_m']:<15} | {r['pf']:<5.2f} | {r['trades']}")
    print("=" * 135)

if __name__ == "__main__":
    test_runner_expansion()

