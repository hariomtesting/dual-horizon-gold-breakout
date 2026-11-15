"""
TESTING ASIAN HIGH/LOW BREAKOUT (LONDON STRADDLE AT ASIAN EXTREMES)
11:00 to 13:00 IST defines Asian/Pre-London High and Low.
At 13:00 IST:
Buy Stop = Pre-London High + 0.10x ATR
Sell Stop = Pre-London Low - 0.10x ATR
SL = 0.75x ATR, TP = 1.45x ATR (1.93:1 R:R)
Auto-Square-Off at 15:55 IST
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def test_asian_breakout():
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
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    portfolio_daily = []
    trades = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            pre_bars = [b for b in bars if b['ist_hour'] < 13]
            post_bars = [b for b in bars if b['ist_hour'] >= 13]
            if len(pre_bars) < 15 or len(post_bars) < 15:
                continue
                
            pre_high = max(b['high'] for b in pre_bars)
            pre_low = min(b['low'] for b in pre_bars)
            atr_v = pre_bars[-1]['atr']
            if atr_v <= 0:
                continue
                
            buy_trigger = pre_high + (0.10 * atr_v)
            sell_trigger = pre_low - (0.10 * atr_v)
            sl_dist = 0.75 * atr_v
            tp_dist = 1.45 * atr_v
            
            pos = None
            for b in post_bars:
                h, l, c, atr = b['high'], b['low'], b['close'], b['atr']
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos:
                        pnl_r = (c - pos['entry'])/sl_dist if pos['type']=='BUY' else (pos['entry'] - c)/sl_dist
                        day_pnl += pnl_r * 0.010
                        trades.append({'date': curr_d, 'pnl_r': pnl_r})
                    break
                if pos is None and b['ist_hour'] < 15:
                    if h >= buy_trigger:
                        pos = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
                    elif l <= sell_trigger:
                        pos = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
                elif pos is not None:
                    if pos['type'] == 'BUY':
                        if h >= pos['tp']:
                            day_pnl += (tp_dist / sl_dist) * 0.010
                            trades.append({'date': curr_d, 'pnl_r': tp_dist / sl_dist})
                            pos = None
                            break
                        elif l <= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            trades.append({'date': curr_d, 'pnl_r': -1.0})
                            pos = None
                            break
                    else:
                        if l <= pos['tp']:
                            day_pnl += (tp_dist / sl_dist) * 0.010
                            trades.append({'date': curr_d, 'pnl_r': tp_dist / sl_dist})
                            pos = None
                            break
                        elif h >= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            trades.append({'date': curr_d, 'pnl_r': -1.0})
                            pos = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_d = pd.DataFrame(portfolio_daily)
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    monthly = df_d.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_d.groupby('year')['pnl_pct'].sum() * 100.0
    df_t = pd.DataFrame(trades)
    wr = (df_t['pnl_r'] > 0).mean() * 100.0
    green_m = (monthly > 0).sum()
    total_m = len(monthly)
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    
    print("=" * 80)
    print("      ASIAN HIGH/LOW BREAKOUT STRADDLE (XAUUSD + EURUSD: 2022-2026)      ")
    print("=" * 80)
    print(f"Total Trades: {len(df_t)} | Win Rate: {wr:.2f}% | Avg Annual ROI: +{yearly.mean():.2f}%")
    print(f"Overall Green Months: {green_m}/{total_m} ({green_m/total_m*100:.1f}%) | 2026 Green: {(m_2026>0).sum()}/11")
    print("Yearly Walk : " + ", ".join([f"{y}: {v:+.1f}%" for y, v in yearly.items()]))
    print("2026 Months : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in m_2026.items()]))

if __name__ == '__main__':
    test_asian_breakout()

