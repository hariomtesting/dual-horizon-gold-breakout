"""
DEEP OPTIMIZATION: PURE FIXED TP CHAMPION (2022-2026)
Investigating optimizations to push consistency to 11/11 (100% in 2026) and maximize ROI:
1. Baseline: Entry 0.30, SL 0.75, TP 1.35 (1.80:1) on both XAUUSD & EURUSD
2. Asset-Specific Tuning (Gold tailored vs EURUSD tailored)
3. Near-Target Lock: If price reaches +1.2R, trail SL to +0.5R in profit (locks gain if it reverses near TP)
4. Session Range Buffer: Aligning entries with 11:00-13:00 session high/low
5. Volatility Floor: Filter out low-liquidity summer lull days (ATR < 15th percentile)
6. Fine TP Sweeps: 1.30x, 1.35x, 1.40x, 1.45x, 1.50x ATR
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
        # 20-day rolling percentile of ATR
        df['atr_p15'] = df['atr'].rolling(12 * 24 * 20, min_periods=100).quantile(0.15).bfill()
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'atr_p15']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate(asset_data, asset_params, near_target_lock=False, vol_filter=False, square_off_m=55):
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
            
            # Find base at 13:00 IST
            base_idx = -1
            for i, b in enumerate(bars):
                if b['ist_hour'] == 13 and b['ist_minute'] == 0:
                    base_idx = i
                    break
            if base_idx == -1 or base_idx >= len(bars) - 5:
                continue
                
            base_p = bars[base_idx]['close']
            atr_v = bars[base_idx]['atr']
            atr_floor = bars[base_idx]['atr_p15']
            if atr_v <= 0:
                continue
                
            # Volatility filter
            if vol_filter and atr_v < atr_floor:
                continue # Skip dead chop days
                
            p = asset_params.get(sym, asset_params.get("DEFAULT"))
            entry_offset = p['entry'] * atr_v
            sl_dist = p['sl'] * atr_v
            tp_dist = p['tp'] * atr_v
            rr = p['tp'] / p['sl']
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active = None
            
            for b in bars[base_idx+1:]:
                # Square off at specified time (e.g. 15:55 or 15:45)
                if b['ist_hour'] == 15 and b['ist_minute'] >= square_off_m:
                    if active is not None:
                        pnl_r = (b['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        active = None
                    break
                    
                if active is None:
                    if b['ist_hour'] >= 15: # 15:00 order cutoff
                        break
                    if b['high'] >= buy_trigger:
                        active = {
                            'type': 'BUY',
                            'entry': buy_trigger,
                            'sl': buy_trigger - sl_dist,
                            'tp': buy_trigger + tp_dist,
                            'locked': False
                        }
                    elif b['low'] <= sell_trigger:
                        active = {
                            'type': 'SELL',
                            'entry': sell_trigger,
                            'sl': sell_trigger + sl_dist,
                            'tp': sell_trigger - tp_dist,
                            'locked': False
                        }
                else:
                    ttype = active['type']
                    entry_p = active['entry']
                    
                    # Near-target lock: if price reaches +1.2R, trail SL to +0.5R in profit
                    if near_target_lock and not active['locked']:
                        if ttype == 'BUY' and b['high'] >= entry_p + (1.20 * sl_dist):
                            active['sl'] = entry_p + (0.50 * sl_dist)
                            active['locked'] = True
                        elif ttype == 'SELL' and b['low'] <= entry_p - (1.20 * sl_dist):
                            active['sl'] = entry_p - (0.50 * sl_dist)
                            active['locked'] = True
                            
                    # Check exits
                    if ttype == 'BUY':
                        if b['high'] >= active['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            active = None
                            break
                        elif b['low'] <= active['sl']:
                            pnl_r = (active['sl'] - entry_p) / sl_dist
                            day_pnl += pnl_r * 0.010
                            total_trades += 1
                            if pnl_r > 0:
                                wins += 1
                            active = None
                            break
                    else: # SELL
                        if b['low'] <= active['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            active = None
                            break
                        elif b['high'] >= active['sl']:
                            pnl_r = (entry_p - active['sl']) / sl_dist
                            day_pnl += pnl_r * 0.010
                            total_trades += 1
                            if pnl_r > 0:
                                wins += 1
                            active = None
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
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    
    cum = (1.0 + df_res['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%)",
        'green_2026': f"{green_2026}/11 ({green_2026/11*100:.1f}%)",
        'win_rate': win_rate,
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def main():
    print("Loading data...")
    data = load_data()
    print("Data loaded. Evaluating optimization candidates...\n")
    
    default_p = {"DEFAULT": {'entry': 0.30, 'sl': 0.75, 'tp': 1.35}}
    
    tests = [
        ("A. Baseline Champion (Entry 0.30, SL 0.75, TP 1.35)", default_p, False, False, 55),
        ("B. Near-Target Profit Lock (Lock +0.5R when reaching +1.2R)", default_p, True, False, 55),
        ("C. Volatility Floor Filter (Skip dead summer chop days)", default_p, False, True, 55),
        ("D. Asset-Specific: Gold TP 1.50 (2.0:1) & EURUSD TP 1.25 (1.67:1)", {
            "XAUUSD": {'entry': 0.30, 'sl': 0.75, 'tp': 1.50},
            "EURUSD": {'entry': 0.30, 'sl': 0.75, 'tp': 1.25}
        }, False, False, 55),
        ("E. Asset-Specific + Near-Target Profit Lock", {
            "XAUUSD": {'entry': 0.30, 'sl': 0.75, 'tp': 1.50},
            "EURUSD": {'entry': 0.30, 'sl': 0.75, 'tp': 1.25}
        }, True, False, 55),
        ("F. Fine TP Sweep: TP = 1.45x ATR (1.93:1 R:R)", {"DEFAULT": {'entry': 0.30, 'sl': 0.75, 'tp': 1.45}}, False, False, 55),
        ("G. Fine TP Sweep: TP = 1.50x ATR (2.00:1 R:R)", {"DEFAULT": {'entry': 0.30, 'sl': 0.75, 'tp': 1.50}}, False, False, 55),
        ("H. Square-off at 15:45 IST instead of 15:55 IST", default_p, False, False, 45),
    ]
    
    for name, p_dict, lock_flag, vol_flag, sq_m in tests:
        res = evaluate(data, p_dict, near_target_lock=lock_flag, vol_filter=vol_flag, square_off_m=sq_m)
        print(f"[{name}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}%")
        print(f"   Yearly Walk   : " + ", ".join([f"{y}: +{v:.1f}%" for y, v in res['yearly'].items()]))
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)

if __name__ == '__main__':
    main()

