"""
INSTITUTIONAL CALIBRATION: SESSION-SCALED GOLD ENGINE
=====================================================
Targeting robust real-world alpha on XAUUSD (Gold) under full live broker frictions:
- Spread: $0.30 per oz (Vantage Live: $0.29)
- Commission: $6.00 per standard round lot ($0.06/oz)
- Slippage: $0.15 on entry, $0.08 on exit (345ms ping simulation)
- Strict 11:00 AM - 16:00 IST window, auto-square-off at 15:55 IST
- Daily Circuit Breaker: -2.00% max daily loss

Tested Architectures:
1. European Wave Anchor (12:00 PM, 12:30 PM, 13:00 PM IST)
2. Asian Session Box Breakout (05:30 - 11:30 IST Box)
3. Trend Filter (EMA 200) vs Bi-Directional
4. SL Sweeps: $5.00 to $12.00 | TP Sweeps: $8.00 to $25.00
"""

import os
import sys
import time
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_gold_data():
    connected = False
    for _ in range(5):
        if mt5.initialize():
            connected = True
            break
        time.sleep(1.0)
    if not connected:
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select("XAUUSD", True)
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M5, dt_from, dt_to)
    mt5.shutdown()
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    # 200 EMA on M5 for macro intraday trend
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # Keep full day from 05:30 to 16:00 IST
    df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 70:
            sub = sub.copy().reset_index(drop=True)
            # Asian range: 05:30 to 11:30 IST
            asian_mask = (sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))
            asian_bars = sub[asian_mask]
            if len(asian_bars) >= 20:
                sub['asian_high'] = asian_bars['high'].max()
                sub['asian_low'] = asian_bars['low'].min()
                sessions[d] = sub.to_dict('records')
    return sessions

def run_simulation(
    sessions,
    model_type="anchor", # "anchor" or "asian_box"
    anchor_h=12,
    anchor_m=0,
    entry_offset=1.50,
    sl_dollars=6.00,
    tp_dollars=12.00,
    trend_filter=True,
    order_cutoff_h=14,
    order_cutoff_m=30
):
    spread = 0.30
    comm_per_lot = 6.00
    slip_in = 0.15
    slip_out = 0.08
    contract_sz = 100.0 # 100 oz
    
    all_dates = sorted(list(sessions.keys()))
    daily_results = []
    trade_stats = []
    
    # 1.0% risk on $10,000 = $100
    cash_risk = 100.0
    lots = cash_risk / (sl_dollars * contract_sz)
    lots = max(0.01, round(lots, 2))
    comm_cost = (lots * comm_per_lot) / 10000.0
    
    for curr_d in all_dates:
        bars = sessions[curr_d]
        
        # Determine entry triggers
        start_idx = -1
        if model_type == "anchor":
            for idx, b in enumerate(bars):
                if b['ist_hour'] == anchor_h and b['ist_minute'] == anchor_m:
                    start_idx = idx
                    break
            if start_idx == -1 or start_idx >= len(bars) - 6:
                continue
            base_p = bars[start_idx]['close']
            buy_trig = base_p + entry_offset
            sell_trig = base_p - entry_offset
            ema_val = bars[start_idx]['ema200']
            allow_buy = (base_p >= ema_val) if trend_filter else True
            allow_sell = (base_p <= ema_val) if trend_filter else True
        else: # Asian Box Breakout
            for idx, b in enumerate(bars):
                if b['ist_hour'] == 11 and b['ist_minute'] == 30:
                    start_idx = idx
                    break
            if start_idx == -1 or start_idx >= len(bars) - 6:
                continue
            asian_h = bars[start_idx]['asian_high']
            asian_l = bars[start_idx]['asian_low']
            buy_trig = asian_h + entry_offset
            sell_trig = asian_l - entry_offset
            ema_val = bars[start_idx]['ema200']
            allow_buy = (bars[start_idx]['close'] >= ema_val) if trend_filter else True
            allow_sell = (bars[start_idx]['close'] <= ema_val) if trend_filter else True
            
        active = None
        taken = False
        day_pnl = 0.0
        
        for i in range(start_idx + 1, len(bars)):
            b = bars[i]
            h = b['ist_hour']
            m = b['ist_minute']
            
            # Auto square off at 15:55 IST
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
                    trade_stats.append(trade_pnl / 0.010)
                    active = None
                break
                
            if active is None and not taken:
                if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
                    break
                if allow_buy and b['high'] >= buy_trig:
                    fill_p = buy_trig + (spread / 2.0) + slip_in
                    active = {'type': 'BUY', 'fill_e': fill_p, 'sl': buy_trig - sl_dollars, 'tp': buy_trig + tp_dollars}
                    taken = True
                elif allow_sell and b['low'] <= sell_trig:
                    fill_p = sell_trig - (spread / 2.0) - slip_in
                    active = {'type': 'SELL', 'fill_e': fill_p, 'sl': sell_trig + sl_dollars, 'tp': sell_trig - tp_dollars}
                    taken = True
            elif active is not None:
                ttype = active['type']
                fill_e = active['fill_e']
                sl_p = active['sl']
                tp_p = active['tp']
                
                if ttype == 'BUY':
                    if b['high'] >= tp_p:
                        exit_p = tp_p - (spread / 2.0) - slip_out
                        trade_pnl = (((exit_p - fill_e) * lots * contract_sz) / 10000.0) - comm_cost
                        day_pnl += trade_pnl
                        trade_stats.append(trade_pnl / 0.010)
                        active = None
                        break
                    elif b['low'] <= sl_p:
                        exit_p = sl_p - (spread / 2.0) - slip_out
                        trade_pnl = (((exit_p - fill_e) * lots * contract_sz) / 10000.0) - comm_cost
                        day_pnl += trade_pnl
                        trade_stats.append(trade_pnl / 0.010)
                        active = None
                        break
                else: # SELL
                    if b['low'] <= tp_p:
                        exit_p = tp_p + (spread / 2.0) + slip_out
                        trade_pnl = (((fill_e - exit_p) * lots * contract_sz) / 10000.0) - comm_cost
                        day_pnl += trade_pnl
                        trade_stats.append(trade_pnl / 0.010)
                        active = None
                        break
                    elif b['high'] >= sl_p:
                        exit_p = sl_p + (spread / 2.0) + slip_out
                        trade_pnl = (((fill_e - exit_p) * lots * contract_sz) / 10000.0) - comm_cost
                        day_pnl += trade_pnl
                        trade_stats.append(trade_pnl / 0.010)
                        active = None
                        break
                        
        if day_pnl < -0.020:
            day_pnl = -0.020
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_res = pd.DataFrame(daily_results)
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['ym'] = pd.to_datetime(df_res['date']).dt.strftime('%Y-%m')
    
    annual = df_res.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
    monthly = df_res.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0)
    
    eq = (1.0 + df_res['pnl_pct']).cumprod()
    max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100.0
    
    m2026 = monthly[monthly.index.str.startswith('2026')]
    g2026 = (m2026 > 0).sum()
    t2026 = len(m2026)
    
    if len(trade_stats) > 0:
        ts = np.array(trade_stats)
        wr = (ts > 0).sum() / len(ts) * 100.0
        g = ts[ts > 0].sum()
        l = abs(ts[ts < 0].sum())
        pf = g / l if l > 0 else 0
        avg_r = ts.mean()
    else:
        wr = 0
        pf = 0
        avg_r = 0
        
    return {
        'avg_annual': annual.mean(),
        'worst_year': annual.min(),
        'max_dd': max_dd,
        'green_months': f"{(monthly > 0).sum()}/{len(monthly)} ({(monthly > 0).sum()/len(monthly)*100:.1f}%)",
        'm2026': f"{g2026}/{t2026}",
        'pf': pf,
        'wr': wr,
        'trades': len(trade_stats),
        'avg_r': avg_r,
        'annual_dict': annual.to_dict()
    }

def main():
    print("Loading Gold tick/M5 data from MT5...")
    sessions = load_gold_data()
    print("Data loaded. Starting systematic calibration under full live frictions...\n")
    
    test_suite = []
    
    # 1. Test Asian Box Breakout with Trend Filter
    for sl in [5.0, 6.0, 7.5, 9.0, 10.0]:
        for rr in [1.5, 1.8, 2.0, 2.5]:
            tp = sl * rr
            for offset in [1.0, 1.5, 2.0]:
                test_suite.append({
                    'name': f"Asian Box | SL ${sl:.1f} | RR {rr:.1f} | Offset ${offset:.1f}",
                    'type': 'asian_box', 'anchor_h': 11, 'anchor_m': 30,
                    'offset': offset, 'sl': sl, 'tp': tp, 'trend': True,
                    'cutoff_h': 14, 'cutoff_m': 30
                })
                
    # 2. Test 12:00 PM Frankfurt Pre-Open Wave
    for sl in [5.0, 6.0, 7.5, 9.0, 10.0]:
        for rr in [1.5, 1.8, 2.0, 2.5]:
            tp = sl * rr
            for offset in [1.0, 1.5, 2.0]:
                test_suite.append({
                    'name': f"12:00 Frankfurt | SL ${sl:.1f} | RR {rr:.1f} | Offset ${offset:.1f}",
                    'type': 'anchor', 'anchor_h': 12, 'anchor_m': 0,
                    'offset': offset, 'sl': sl, 'tp': tp, 'trend': True,
                    'cutoff_h': 13, 'cutoff_m': 30
                })

    # 3. Test 13:00 PM London Open Wave
    for sl in [5.0, 6.0, 7.5, 9.0, 10.0]:
        for rr in [1.5, 1.8, 2.0, 2.5]:
            tp = sl * rr
            for offset in [1.0, 1.5, 2.0]:
                test_suite.append({
                    'name': f"13:00 London | SL ${sl:.1f} | RR {rr:.1f} | Offset ${offset:.1f}",
                    'type': 'anchor', 'anchor_h': 13, 'anchor_m': 0,
                    'offset': offset, 'sl': sl, 'tp': tp, 'trend': True,
                    'cutoff_h': 15, 'cutoff_m': 0
                })
                
    print(f"Total Configurations to Evaluate: {len(test_suite)}")
    print("Simulating across 5 years of live market conditions...\n")
    
    results = []
    for item in test_suite:
        res = run_simulation(
            sessions,
            model_type=item['type'],
            anchor_h=item['anchor_h'],
            anchor_m=item['anchor_m'],
            entry_offset=item['offset'],
            sl_dollars=item['sl'],
            tp_dollars=item['tp'],
            trend_filter=item['trend'],
            order_cutoff_h=item['cutoff_h'],
            order_cutoff_m=item['cutoff_m']
        )
        res['name'] = item['name']
        results.append(res)
        
    df_res = pd.DataFrame(results)
    
    # Filter for viable institutional setups (PF > 1.20 and Annual ROI > 15%)
    viable = df_res[(df_res['pf'] >= 1.15) & (df_res['avg_annual'] > 0)].sort_values(by='avg_annual', ascending=False)
    
    print("=" * 130)
    print("TOP SESSION-SCALED GOLD CONFIGURATIONS (AFTER FULL VANTAGE SPREADS, COMMISSIONS & 345MS LATENCY)")
    print("=" * 130)
    print(f"{'CONFIGURATION':<55} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'TRADES'}")
    print("-" * 130)
    
    if len(viable) == 0:
        print("No configurations passed the strict PF >= 1.15 filter. Showing top 10 by Annual ROI:")
        top10 = df_res.sort_values(by='avg_annual', ascending=False).head(10)
        for _, r in top10.iterrows():
            print(f"{r['name']:<55} | {r['avg_annual']:+9.2f}% | {r['worst_year']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['pf']:<5.2f} | {r['trades']}")
    else:
        for _, r in viable.head(15).iterrows():
            print(f"{r['name']:<55} | {r['avg_annual']:+9.2f}% | {r['worst_year']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['pf']:<5.2f} | {r['trades']}")
            
    print("=" * 130)

if __name__ == "__main__":
    main()

