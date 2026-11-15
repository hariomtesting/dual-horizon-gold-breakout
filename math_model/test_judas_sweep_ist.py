"""
TESTING LONDON OPEN LIQUIDITY SWEEP (JUDAS SWING FADE) (11:00 AM - 4:00 PM IST)
=================================================================================
Hypothesis:
Simple breakout buying at London Open fails (38% WR) because institutions run stops above Asian/Pre-London Highs,
then reverse into true trend.
Mechanics:
1. 11:00 AM - 12:30 PM IST: Build Pre-London Range (High / Low).
2. 12:30 PM - 14:30 PM IST:
   - If Price sweeps ABOVE Pre-London High, but fails to close above (rejection wick or Delta < 0), SELL back toward Session VWAP / Range Low!
   - If Price sweeps BELOW Pre-London Low, but fails to close below (rejection wick or Delta > 0), BUY back toward Session VWAP / Range High!
3. Target: 1.5x to 2.0x ATR.
4. Auto-Square-Off: 15:55 IST.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_sweep_test(symbol="XAUUSD", dt_start=2018):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select(symbol, True)
    
    dt_from = datetime.datetime(dt_start, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter 11:00 to 16:00 IST
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
    
    # Calculate Session VWAP
    tp = (df_ist['high'] + df_ist['low'] + df_ist['close']) / 3.0
    df_ist['pv'] = tp * df_ist['volume']
    df_ist['cum_vol'] = df_ist.groupby('date')['volume'].cumsum()
    df_ist['cum_pv'] = df_ist.groupby('date')['pv'].cumsum()
    df_ist['vwap'] = df_ist['cum_pv'] / df_ist['cum_vol'].replace(0, np.nan)
    
    day_sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            day_sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta', 'vwap']].to_dict('records')
            
    print(f"Loaded {len(day_sessions):,} sessions for {symbol}.", flush=True)
    
    risk_per_trade = 0.0125
    trades = []
    daily_results = []
    
    for curr_d, bars in day_sessions.items():
        pre_bars = bars[0:18] # 11:00 to 12:30 IST
        pre_high = max(b['high'] for b in pre_bars)
        pre_low = min(b['low'] for b in pre_bars)
        
        pos = None
        trades_today = 0
        day_pnl = 0.0
        
        for i in range(18, len(bars)):
            r = bars[i]
            h, l, c, o = r['high'], r['low'], r['close'], r['open']
            atr, delta, vwap = r['atr'], r['delta'], r['vwap']
            ist_h, ist_m = r['ist_hour'], r['ist_minute']
            
            # Hard close at 15:55 IST
            if ist_h == 15 and ist_m >= 55:
                if pos is not None:
                    pnl_pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                    pnl_pct = (pnl_pts / pos['sl_dist']) * risk_per_trade
                    pos['pnl_pct'] = pnl_pct
                    day_pnl += pnl_pct
                    trades.append(pos)
                    trades_today += 1
                    pos = None
                break
                
            # Manage Open Position
            if pos is not None:
                if pos['type'] == 'BUY':
                    if l <= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif h >= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                elif pos['type'] == 'SELL':
                    if h >= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif l <= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        
            # Entry logic: Liquidity Sweep of Pre-London High/Low
            if pos is None and trades_today == 0 and 12 <= ist_h < 15:
                sl_dist = 1.0 * atr
                tp_dist = 1.8 * atr # 1.8:1 R:R
                rr = tp_dist / sl_dist
                
                # Bearish Judas Sweep: High pushed above pre_high, but close fell back below pre_high and closed RED with Delta < 0
                if h > pre_high and c < pre_high and c < o and delta < 0:
                    pos = {
                        'symbol': symbol, 'type': 'SELL', 'date': curr_d,
                        'entry': c, 'sl': h + 0.10, 'tp': c - tp_dist,
                        'sl_dist': max((h + 0.10) - c, 0.5 * atr), 'rr': rr,
                        'entry_time': r['ist_dt']
                    }
                # Bullish Judas Sweep: Low swept below pre_low, but close bounced back above pre_low and closed GREEN with Delta > 0
                elif l < pre_low and c > pre_low and c > o and delta > 0:
                    pos = {
                        'symbol': symbol, 'type': 'BUY', 'date': curr_d,
                        'entry': c, 'sl': l - 0.10, 'tp': c + tp_dist,
                        'sl_dist': max(c - (l - 0.10), 0.5 * atr), 'rr': rr,
                        'entry_time': r['ist_dt']
                    }
                    
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_t = pd.DataFrame(trades)
    df_d = pd.DataFrame(daily_results)
    if len(df_t) == 0:
        print("No trades found.")
        return
        
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    
    wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print(f"\nTotal Trades: {len(df_t):,} | Win Rate: {wr:.2f}%")
    
    for y in sorted(df_d['year'].unique()):
        y_d = df_d[df_d['year'] == y]
        y_t = df_t[df_t['year'] == y]
        if len(y_t) == 0:
            continue
        y_wr = (y_t['pnl_pct'] > 0).mean() * 100.0
        roi = y_d['pnl_pct'].sum() * 100.0
        print(f"Year {y}: Trades={len(y_t):<4} WR={y_wr:5.1f}% ROI={roi:+6.2f}%")


if __name__ == "__main__":
    run_sweep_test("XAUUSD", 2018)
    run_sweep_test("GER40.r", 2021)

