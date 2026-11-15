import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

mt5.initialize()
symbols = ['XAUUSD', 'EURUSD']
for s in symbols:
    mt5.symbol_select(s, True)
dt_from = datetime.datetime(2022, 1, 1)
dt_to = datetime.datetime(2026, 11, 30)

for sym in symbols:
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, dt_from, dt_to)
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    df['ema50'] = df['close'].ewm(span=600, adjust=False).mean()
    df_ist = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    
    trades = []
    for d, sub in df_ist.groupby('date'):
        bars = sub.to_dict('records')
        asian_bars = [b for b in bars if b['ist_hour'] < 12 or (b['ist_hour'] == 12 and b['ist_minute'] <= 30)]
        exec_bars = [b for b in bars if b['ist_hour'] > 12 or (b['ist_hour'] == 12 and b['ist_minute'] > 30)]
        if len(asian_bars) < 20 or len(exec_bars) < 15:
            continue
        asian_high = max(b['high'] for b in asian_bars)
        asian_low = min(b['low'] for b in asian_bars)
        base = asian_bars[-1]
        atr_v = base['atr']
        ema_v = base['ema50']
        if atr_v <= 0: continue
        
        buy_ok = base['close'] >= ema_v
        sell_ok = base['close'] <= ema_v
        
        buy_trig = asian_high + 0.08 * atr_v
        sell_trig = asian_low - 0.08 * atr_v
        sl_dist = 0.65 * atr_v
        tp_dist = 1.35 * atr_v
        rr = tp_dist / sl_dist
        
        pos = None
        for b in exec_bars:
            if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                if pos:
                    pnl = (b['close'] - pos['entry'])/sl_dist if pos['type']=='BUY' else (pos['entry'] - b['close'])/sl_dist
                    trades.append({'date': d, 'pnl_r': pnl})
                break
            if pos is None and b['ist_hour'] < 15:
                if buy_ok and b['high'] >= buy_trig:
                    pos = {'type': 'BUY', 'entry': buy_trig, 'sl': buy_trig - sl_dist, 'tp': buy_trig + tp_dist}
                elif sell_ok and b['low'] <= sell_trig:
                    pos = {'type': 'SELL', 'entry': sell_trig, 'sl': sell_trig + sl_dist, 'tp': sell_trig - tp_dist}
            elif pos is not None:
                if pos['type'] == 'BUY':
                    if b['high'] >= pos['tp']:
                        trades.append({'date': d, 'pnl_r': rr})
                        break
                    elif b['low'] <= pos['sl']:
                        trades.append({'date': d, 'pnl_r': -1.0})
                        break
                else:
                    if b['low'] <= pos['tp']:
                        trades.append({'date': d, 'pnl_r': rr})
                        break
                    elif b['high'] >= pos['sl']:
                        trades.append({'date': d, 'pnl_r': -1.0})
                        break
                        
    df_t = pd.DataFrame(trades)
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    wr = (df_t['pnl_r'] > 0).mean() * 100.0
    print(f"=== {sym} Trend-Filtered Asian Breakout ===")
    print(f"Total Trades: {len(df_t)} | Win Rate: {wr:.1f}% | Net R: {df_t['pnl_r'].sum():+.1f}R")
    for y, sub in df_t.groupby('year'):
        print(f"  {y}: Trades={len(sub):<3} WR={(sub['pnl_r']>0).mean()*100:4.1f}% Net R={sub['pnl_r'].sum():+6.1f}R")

mt5.shutdown()

