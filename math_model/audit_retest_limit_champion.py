"""
AUDIT REPORT: CHAMPION RETEST LIMIT ORDER ARCHITECTURE
======================================================
Detailed Year-by-Year & Month-by-Month Live Market Simulation Audit
for the 12:00 Frankfurt & Dual-Session Retest Limit Order Models.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
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
    return sessions

def make_retest_limit_fn(anchor_h=12, anchor_m=0, break_offset=3.50, limit_retest_offset=1.50, sl=6.00, rr=2.0, cutoff_h=14, cutoff_m=0):
    tp = sl * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'phase' not in state:
            state['phase'] = 'WAIT_ANCHOR'
            state['base'] = None
            state['dir'] = None
            state['limit_price'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        if h > cutoff_h or (h == cutoff_h and m >= cutoff_m):
            state['done'] = True
            return None
            
        if state['phase'] == 'WAIT_ANCHOR' and h == anchor_h and m == anchor_m:
            state['phase'] = 'WAIT_BREAK'
            state['base'] = row['close']
            state['ema'] = row['ema200']
            return None
            
        if state['phase'] == 'WAIT_BREAK':
            base = state['base']
            ema = state['ema']
            if base >= ema and row['high'] >= base + break_offset:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'BUY'
                state['limit_price'] = base + limit_retest_offset
            elif base <= ema and row['low'] <= base - break_offset:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'SELL'
                state['limit_price'] = base - limit_retest_offset
                
        elif state['phase'] == 'WAIT_LIMIT_FILL':
            lp = state['limit_price']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': lp - sl, 'tp': lp + tp}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': lp + sl, 'tp': lp - tp}
        return None
    return signal_fn

def make_asian_box_retest_limit_fn(break_offset=2.50, limit_retest_offset=0.50, sl=6.00, rr=2.0, cutoff_h=14, cutoff_m=30):
    tp = sl * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'phase' not in state:
            state['phase'] = 'WAIT_BREAK'
            state['dir'] = None
            state['limit_price'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        if h < 11 or (h == 11 and m < 30) or h > cutoff_h or (h == cutoff_h and m >= cutoff_m):
            if h >= cutoff_h:
                state['done'] = True
            return None
            
        ah = row['asian_high']
        al = row['asian_low']
        ema = row['ema200']
        
        if state['phase'] == 'WAIT_BREAK':
            if row['close'] >= ema and row['high'] >= ah + break_offset:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'BUY'
                state['limit_price'] = ah + limit_retest_offset
            elif row['close'] <= ema and row['low'] <= al - break_offset:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'SELL'
                state['limit_price'] = al - limit_retest_offset
                
        elif state['phase'] == 'WAIT_LIMIT_FILL':
            lp = state['limit_price']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': lp - sl, 'tp': lp + tp}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': lp + sl, 'tp': lp - tp}
        return None
    return signal_fn

def main():
    print("Loading data...")
    sessions = load_data()
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    print("\n" + "=" * 90)
    print("AUDIT 1: FRANKFURT 12:00 RETEST LIMIT ORDER (BREAK $3.50 -> BUY LIMIT AT $1.50, RR 2.0)")
    print("=" * 90)
    fn1 = make_retest_limit_fn(anchor_h=12, anchor_m=0, break_offset=3.50, limit_retest_offset=1.50, sl=6.00, rr=2.0)
    rep1 = sim.run_bars(sessions, fn1)
    sim.print_report(rep1)
    
    print("\n" + "=" * 90)
    print("AUDIT 2: FRANKFURT 12:00 RETEST LIMIT ORDER (BREAK $3.50 -> BUY LIMIT AT $1.50, RR 2.5)")
    print("=" * 90)
    fn2 = make_retest_limit_fn(anchor_h=12, anchor_m=0, break_offset=3.50, limit_retest_offset=1.50, sl=6.00, rr=2.5)
    rep2 = sim.run_bars(sessions, fn2)
    sim.print_report(rep2)

    print("\n" + "=" * 90)
    print("AUDIT 3: ASIAN BOX RETEST LIMIT ORDER (BREAK BOX + $2.50 -> LIMIT AT BOX + $0.50, RR 2.0)")
    print("=" * 90)
    fn3 = make_asian_box_retest_limit_fn(break_offset=2.50, limit_retest_offset=0.50, sl=6.00, rr=2.0)
    rep3 = sim.run_bars(sessions, fn3)
    sim.print_report(rep3)

if __name__ == "__main__":
    main()

