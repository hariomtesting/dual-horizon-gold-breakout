"""
TRUE ASIAN RANGE BREAKOUT OPTIMIZER (2022 - 2026)
Assets: XAUUSD, EURUSD
Hours: Asian Range measured from 06:00 AM to 12:30/13:00 IST.
Trade Window: 12:30/13:00 to 16:00 IST (Runs on local PC without VPS).
Auto-Square-Off: 15:55 IST (100% Flat).

Optimization Grid:
1. Asian Window: 06:00 - 12:30 vs 06:00 - 13:00 IST
2. Asian Compression Gate: Skip if Asian Range > Max_Ratio * ATR
3. Entry Buffer: 0.05x, 0.10x, 0.15x ATR outside Asian High / Low
4. Stop Loss: 0.60x, 0.75x, 0.90x ATR vs Asian Midpoint
5. Take Profit: 1.20x, 1.45x, 1.80x ATR
6. Single-Shot OCO & 15:00 IST Cutoff
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Keep 06:00 to 16:00 IST
        df_ist = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 50:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate_asian_breakout(asset_data, end_asian_h=13, end_asian_m=0, max_asian_atr=None, buffer_mult=0.08, sl_mode="atr", sl_mult=0.75, tp_mult=1.45):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    total_trades = 0
    wins = 0
    
    for curr_d in all_dates:
        day_pnl = 0.0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Split Asian bars vs Execution bars
            asian_bars = []
            exec_bars = []
            for b in bars:
                if b['ist_hour'] < end_asian_h or (b['ist_hour'] == end_asian_h and b['ist_minute'] <= end_asian_m):
                    asian_bars.append(b)
                else:
                    exec_bars.append(b)
                    
            if len(asian_bars) < 20 or len(exec_bars) < 15:
                continue
                
            asian_high = max(b['high'] for b in asian_bars)
            asian_low = min(b['low'] for b in asian_bars)
            asian_mid = (asian_high + asian_low) / 2.0
            asian_range = asian_high - asian_low
            
            atr_v = asian_bars[-1]['atr']
            if atr_v <= 0:
                continue
                
            # Compression gate: If Asian range is too blown out, skip day
            if max_asian_atr is not None and asian_range > (max_asian_atr * atr_v):
                continue
                
            entry_buffer = buffer_mult * atr_v
            buy_trigger = asian_high + entry_buffer
            sell_trigger = asian_low - entry_buffer
            
            if sl_mode == "atr":
                sl_dist = sl_mult * atr_v
            elif sl_mode == "midpoint":
                sl_dist = max(buy_trigger - asian_mid, 0.5 * atr_v)
            else:
                sl_dist = sl_mult * atr_v
                
            tp_dist = tp_mult * atr_v
            rr = tp_dist / sl_dist
            
            pos = None
            
            for b in exec_bars:
                # 15:55 IST Auto-square-off
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos is not None:
                        pnl_r = (b['close'] - pos['entry']) / sl_dist if pos['type'] == 'BUY' else (pos['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        pos = None
                    break
                    
                if pos is None:
                    # 15:00 Cutoff
                    if b['ist_hour'] >= 15:
                        break
                    if b['high'] >= buy_trigger:
                        pos = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
                    elif b['low'] <= sell_trigger:
                        pos = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
                else:
                    if pos['type'] == 'BUY':
                        if b['high'] >= pos['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            pos = None
                            break
                        elif b['low'] <= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            pos = None
                            break
                    else: # SELL
                        if b['low'] <= pos['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            pos = None
                            break
                        elif b['high'] >= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            pos = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_res = pd.DataFrame(portfolio_daily)
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['month'] = pd.to_datetime(df_res['date']).dt.to_period('M')
    
    monthly = df_res.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_res.groupby('year')['pnl_pct'].sum() * 100.0
    
    green_cnt = (monthly > 0).sum()
    total_cnt = len(monthly)
    overall_green_pct = (green_cnt / total_cnt) * 100.0 if total_cnt > 0 else 0
    
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    total_2026 = len(m_2026)
    
    cum = (1.0 + df_res['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_cnt}/{total_cnt} ({overall_green_pct:.1f}%)",
        'overall_green_pct': overall_green_pct,
        'green_2026': f"{green_2026}/{total_2026} ({green_2026/total_2026*100:.1f}%)",
        'green_2026_cnt': green_2026,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def main():
    print("Loading historical data for True Asian Range Breakout Optimizer...")
    data = load_data()
    print("Data loaded. Starting optimization grid...\n")
    
    grid = [
        # (end_h, end_m, max_atr, buffer, sl_mode, sl, tp, label)
        (13, 0, None, 0.10, "atr", 0.75, 1.45, "1. Baseline Asian Breakout (13:00 IST Cut, Buffer 0.10, SL 0.75, TP 1.45)"),
        (12, 30, None, 0.10, "atr", 0.75, 1.45, "2. Earlier Asian End (12:30 IST Cut, Buffer 0.10, SL 0.75, TP 1.45)"),
        (13, 0, 8.0, 0.10, "atr", 0.75, 1.45, "3. Compression Filter: Skip if Asian Range > 8.0x ATR"),
        (13, 0, 6.0, 0.10, "atr", 0.75, 1.45, "4. Tight Compression Filter: Skip if Asian Range > 6.0x ATR"),
        (13, 0, 6.0, 0.05, "atr", 0.75, 1.45, "5. Buffer 0.05x ATR + Compression < 6.0x ATR"),
        (13, 0, 6.0, 0.15, "atr", 0.75, 1.45, "6. Buffer 0.15x ATR (Avoid False Wicks) + Compression < 6.0x ATR"),
        (13, 0, 6.0, 0.10, "atr", 0.65, 1.35, "7. Tighter Risk: SL=0.65, TP=1.35 (2.08:1 R:R)"),
        (13, 0, 6.0, 0.10, "atr", 0.70, 1.40, "8. Balanced: SL=0.70, TP=1.40 (2.00:1 R:R)"),
        (13, 0, 6.0, 0.10, "atr", 0.80, 1.60, "9. Wider Runner: SL=0.80, TP=1.60 (2.00:1 R:R)"),
        (13, 0, 6.0, 0.10, "midpoint", 0.75, 1.45, "10. Asian Midpoint SL (SL at 50% Asian Range)"),
    ]
    
    results = []
    for h, m, max_r, buf, sl_m, sl, tp, label in grid:
        res = evaluate_asian_breakout(data, end_asian_h=h, end_asian_m=m, max_asian_atr=max_r, buffer_mult=buf, sl_mode=sl_m, sl_mult=sl, tp_mult=tp)
        res['label'] = label
        results.append(res)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}% ({res['total_trades']} trades)")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)
        
    results.sort(key=lambda x: (x['overall_green_pct'], x['green_2026_cnt'], x['avg_annual']), reverse=True)
    
    print("\n" + "=" * 90)
    print("                 TOP OPTIMIZED ASIAN BREAKOUT CONFIGURATIONS                ")
    print("=" * 90)
    for i, r in enumerate(results[:3], 1):
        print(f"\nRANK #{i}: {r['label']}")
        print(f"  * Overall Consistency: {r['overall_green']}")
        print(f"  * 2026 Consistency   : {r['green_2026']}")
        print(f"  * Avg Annual ROI     : +{r['avg_annual']:.2f}% (Worst Year: +{r['min_year']:.2f}%)")
        print(f"  * Win Rate / Max DD  : {r['win_rate']:.1f}% Win Rate | {r['max_dd']:.2f}% Max DD")
        print(f"  * Yearly Walk        : " + ", ".join([f"{y}: +{v:.1f}%" for y, v in r['yearly'].items()]))

if __name__ == '__main__':
    main()

