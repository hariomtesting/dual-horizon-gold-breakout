"""
CORRECTED MULTI-ASSET JUDAS SWEEP TEST (XAUUSD + EURUSD: 2022-2026)
Liquidity Sweep of 11:00-12:30 IST Pre-London Range
SL = 0.75x ATR, TP = 1.35x ATR (1.80:1 R:R)
Auto-Square-Off at 15:55 IST
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def test_judas():
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
            pre_bars = bars[0:18] # 11:00 to 12:30 IST
            pre_high = max(b['high'] for b in pre_bars)
            pre_low = min(b['low'] for b in pre_bars)
            
            pos = None
            for b in bars[18:]:
                h, l, c, o, atr = b['high'], b['low'], b['close'], b['open'], b['atr']
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos:
                        pnl_r = (c - pos['entry'])/pos['sl_dist'] if pos['type']=='BUY' else (pos['entry'] - c)/pos['sl_dist']
                        day_pnl += pnl_r * 0.010
                        trades.append({'date': curr_d, 'pnl_r': pnl_r})
                    break
                if pos is None and b['ist_hour'] < 15:
                    sl_dist = 0.75 * atr
                    tp_dist = 1.35 * atr
                    # Bearish Sweep: Pushed above pre-high but closed back below with red candle
                    if h > pre_high and c < pre_high and c < o:
                        pos = {'type': 'SELL', 'entry': c, 'sl': c + sl_dist, 'tp': c - tp_dist, 'sl_dist': sl_dist}
                    # Bullish Sweep: Swept below pre-low but closed back above with green candle
                    elif l < pre_low and c > pre_low and c > o:
                        pos = {'type': 'BUY', 'entry': c, 'sl': c - sl_dist, 'tp': c + tp_dist, 'sl_dist': sl_dist}
                elif pos is not None:
                    if pos['type'] == 'BUY':
                        if b['high'] >= pos['tp']:
                            day_pnl += (1.35/0.75) * 0.010
                            trades.append({'date': curr_d, 'pnl_r': 1.35/0.75})
                            pos = None
                            break
                        elif b['low'] <= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            trades.append({'date': curr_d, 'pnl_r': -1.0})
                            pos = None
                            break
                    else:
                        if b['low'] <= pos['tp']:
                            day_pnl += (1.35/0.75) * 0.010
                            trades.append({'date': curr_d, 'pnl_r': 1.35/0.75})
                            pos = None
                            break
                        elif b['high'] >= pos['sl']:
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
    
    print("=" * 80)
    print("       CORRECTED JUDAS SWEEP SCALPER (XAUUSD + EURUSD: 2022-2026)       ")
    print("=" * 80)
    print(f"Total Trades: {len(df_t)} | Win Rate: {wr:.2f}% | Avg Annual ROI: +{yearly.mean():.2f}%")
    print("Yearly Walk : " + ", ".join([f"{y}: {v:+.1f}%" for y, v in yearly.items()]))
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    print("2026 Months : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in m_2026.items()]))

if __name__ == '__main__':
    test_judas()

