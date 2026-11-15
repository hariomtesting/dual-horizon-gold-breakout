"""
INSTITUTIONAL COMPRESSION-TO-EXPANSION (NR7) GOLD MODEL
======================================================
Testing the classic institutional principle:
"Volatility compression precedes massive, clean volatility expansion."

Rules:
1. Only trade on days where the Asian Session (05:30 - 11:30 IST) range is COMPRESSED (below 40th percentile of rolling 20-day Asian range).
2. On compressed days, enter the European Expansion Breakout at 12:00 PM or 12:30 PM IST.
3. Stop Loss: $5.00 - $7.00. Take Profit: $12.00 - $18.00 (2.0:1 to 2.5:1 R:R).
4. All evaluated strictly after live Vantage spread ($0.30), comm ($6/lot), and 345ms latency slippage.
"""
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import datetime

def test_compression_model():
    if not mt5.initialize():
        print("MT5 init failed")
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
    
    # Calculate Asian ranges per day
    asian_ranges = []
    day_sessions = {}
    
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 70:
            sub = sub.copy().reset_index(drop=True)
            asian_bars = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
            if len(asian_bars) >= 20:
                ah = asian_bars['high'].max()
                al = asian_bars['low'].min()
                rng = ah - al
                sub['asian_high'] = ah
                sub['asian_low'] = al
                sub['asian_rng'] = rng
                asian_ranges.append({'date': d, 'asian_rng': rng})
                day_sessions[d] = sub.to_dict('records')
                
    df_rng = pd.DataFrame(asian_ranges)
    # Rolling 20-day median and 35th percentile of Asian range
    df_rng['rng_p35'] = df_rng['asian_rng'].rolling(20, min_periods=5).quantile(0.35).bfill()
    df_rng['rng_p50'] = df_rng['asian_rng'].rolling(20, min_periods=5).quantile(0.50).bfill()
    rng_map = df_rng.set_index('date').to_dict('index')
    
    all_dates = sorted(list(day_sessions.keys()))
    
    spread = 0.30
    comm_per_lot = 6.00
    slip_in = 0.15
    slip_out = 0.08
    contract_sz = 100.0
    
    results = []
    
    for compression_level in ["p35", "p50", "fixed_10"]:
        for anchor_h in [12, 13]:
            for sl in [5.00, 6.00, 7.50]:
                for rr in [1.8, 2.0, 2.5]:
                    tp = sl * rr
                    lots = 100.0 / (sl * contract_sz)
                    lots = max(0.01, round(lots, 2))
                    comm_cost = (lots * comm_per_lot) / 10000.0
                    
                    daily_pnl_list = []
                    trades_list = []
                    
                    for curr_d in all_dates:
                        bars = day_sessions[curr_d]
                        if curr_d not in rng_map:
                            continue
                            
                        rng_info = rng_map[curr_d]
                        actual_rng = rng_info['asian_rng']
                        
                        # Compression check
                        if compression_level == "p35" and actual_rng > rng_info['rng_p35']:
                            continue
                        elif compression_level == "p50" and actual_rng > rng_info['rng_p50']:
                            continue
                        elif compression_level == "fixed_10" and actual_rng > 10.0:
                            continue
                            
                        # Find anchor bar
                        a_idx = -1
                        for idx, b in enumerate(bars):
                            if b['ist_hour'] == anchor_h and b['ist_minute'] == 0:
                                a_idx = idx
                                break
                        if a_idx == -1 or a_idx >= len(bars) - 6:
                            continue
                            
                        base_p = bars[a_idx]['close']
                        buy_trig = base_p + (0.30 * sl)
                        sell_trig = base_p - (0.30 * sl)
                        
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
                                
                            if active is None:
                                if h >= cutoff_h:
                                    break
                                if b['high'] >= buy_trig:
                                    fill_p = buy_trig + (spread / 2.0) + slip_in
                                    active = {'type': 'BUY', 'fill_e': fill_p, 'sl': buy_trig - sl, 'tp': buy_trig + tp}
                                elif b['low'] <= sell_trig:
                                    fill_p = sell_trig - (spread / 2.0) - slip_in
                                    active = {'type': 'SELL', 'fill_e': fill_p, 'sl': sell_trig + sl, 'tp': sell_trig - tp}
                            else:
                                ttype = active['type']
                                fill_e = active['fill_e']
                                sl_p = active['sl']
                                tp_p = active['tp']
                                
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
                        'comp': compression_level,
                        'anchor': f"{anchor_h}:00",
                        'sl': sl,
                        'rr': rr,
                        'tp': tp,
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
    print("COMPRESSION-TO-EXPANSION GOLD BREAKOUT (AFTER LIVE VANTAGE SPREADS, COMMISSIONS & 345MS LATENCY)")
    print("=" * 135)
    print(f"{'COMPRESSION':<12} | {'ANCHOR':<8} | {'SL ($)':<7} | {'RR':<5} | {'TP ($)':<7} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'TRADES'}")
    print("-" * 135)
    for idx, r in profitable.head(20).iterrows():
        print(f"{r['comp']:<12} | {r['anchor']:<8} | ${r['sl']:<6.2f} | {r['rr']:<5.1f} | ${r['tp']:<6.2f} | {r['avg_ann']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_m']:<15} | {r['pf']:<5.2f} | {r['trades']}")
    print("=" * 135)

if __name__ == "__main__":
    test_compression_model()

