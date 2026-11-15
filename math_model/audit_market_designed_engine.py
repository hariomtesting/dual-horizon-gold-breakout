"""
EMPIRICAL STRATEGY EVALUATION: REAL-WORLD LIVE MARKET SIMULATOR
===============================================================
Running market-derived models on 5 years of XAUUSD (Gold) data
strictly through LiveMarketSimulator with:
- Live Vantage Spread: $0.30/oz
- Commission: $6.00/lot round turn ($0.06/oz)
- Latency Slippage: $0.15 entry, $0.08 exit (345ms ping simulation)
- Auto Square-off: 15:55 IST (100% Flat daily)
- Circuit Breaker: -2.00% daily loss limit
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_gold_sessions():
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
    
    # Calculate Asian Session metrics (05:30 to 11:30 IST)
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

def run_tests():
    print("Loading 2022-2026 Gold 5-minute data from MT5...")
    sessions = load_gold_sessions()
    print(f"Loaded {len(sessions)} trading days. Initializing LiveMarketSimulator...\n")
    
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    # -------------------------------------------------------------
    # MODEL 1: Pullback / Retest Model (Tight SL $4.00, TP $10.00)
    # -------------------------------------------------------------
    print("-" * 100)
    print("TESTING MODEL 1: PULLBACK RETEST ATTEMPT (SL $4.00, TP $10.00)")
    print("-" * 100)
    def signal_pullback(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'phase' not in state:
            state['phase'] = 'WAIT_BREAK'
            state['peak'] = None
            state['dir'] = None
            state['taken'] = False
        if state['taken'] or h >= 14:
            return None
        
        ah = row['asian_high']
        al = row['asian_low']
        
        if state['phase'] == 'WAIT_BREAK':
            if (h == 11 and m >= 30) or h == 12:
                if row['high'] > ah + 1.50:
                    state['phase'] = 'WAIT_PULLBACK'
                    state['dir'] = 'BUY'
                    state['peak'] = row['high']
                elif row['low'] < al - 1.50:
                    state['phase'] = 'WAIT_PULLBACK'
                    state['dir'] = 'SELL'
                    state['peak'] = row['low']
        elif state['phase'] == 'WAIT_PULLBACK':
            if state['dir'] == 'BUY':
                if row['high'] > state['peak']:
                    state['peak'] = row['high']
                if row['low'] <= state['peak'] - 3.00 and row['close'] > row['open']:
                    state['taken'] = True
                    return {'type': 'BUY', 'trigger': row['close'], 'sl': row['close'] - 4.00, 'tp': row['close'] + 10.00}
            else:
                if row['low'] < state['peak']:
                    state['peak'] = row['low']
                if row['high'] >= state['peak'] + 3.00 and row['close'] < row['open']:
                    state['taken'] = True
                    return {'type': 'SELL', 'trigger': row['close'], 'sl': row['close'] + 4.00, 'tp': row['close'] - 10.00}
        return None

    rep_pullback = sim.run_bars(sessions, signal_pullback)
    sim.print_report(rep_pullback)

    # -------------------------------------------------------------
    # MODEL 2: Asian Box Breakout (SL $10.00, TP $25.00, 2.5:1 RR)
    # Entry offset +$2.00, 200 EMA trend aligned, Cutoff 14:30 IST
    # -------------------------------------------------------------
    print("-" * 100)
    print("TESTING MODEL 2: ASIAN BOX BREAKOUT (SL $10.00, TP $25.00, 2.5:1 RR)")
    print("-" * 100)
    def signal_asian_box(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'taken' not in state:
            state['taken'] = False
        if state['taken']:
            return None
        # Only enter between 11:30 and 14:30 IST
        if h < 11 or (h == 11 and m < 30) or h > 14 or (h == 14 and m >= 30):
            return None
        
        ah = row['asian_high']
        al = row['asian_low']
        ema = row['ema200']
        
        # Long condition: above EMA200 and breaks Asian High + $2.00
        if row['close'] >= ema and row['high'] >= ah + 2.00:
            state['taken'] = True
            trig = ah + 2.00
            return {'type': 'BUY', 'trigger': trig, 'sl': trig - 10.00, 'tp': trig + 25.00}
        # Short condition: below EMA200 and breaks Asian Low - $2.00
        elif row['close'] <= ema and row['low'] <= al - 2.00:
            state['taken'] = True
            trig = al - 2.00
            return {'type': 'SELL', 'trigger': trig, 'sl': trig + 10.00, 'tp': trig - 25.00}
        return None

    rep_asian = sim.run_bars(sessions, signal_asian_box)
    sim.print_report(rep_asian)

    # -------------------------------------------------------------
    # MODEL 3: Asian Box with Compression Filter (Asian Range <= $14.00)
    # Avoids pre-exhausted days!
    # -------------------------------------------------------------
    print("-" * 100)
    print("TESTING MODEL 3: ASIAN BOX WITH COMPRESSION FILTER (Range <= $14.00, SL $10.00, TP $25.00)")
    print("-" * 100)
    def signal_asian_compressed(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'taken' not in state:
            state['taken'] = False
        if state['taken']:
            return None
        if h < 11 or (h == 11 and m < 30) or h > 14 or (h == 14 and m >= 30):
            return None
        
        # Compression Filter: Skip if Asian session was already blown out (> $14.00) or totally dead (< $4.00)
        rng = row['asian_range']
        if rng > 14.00 or rng < 4.00:
            return None
            
        ah = row['asian_high']
        al = row['asian_low']
        ema = row['ema200']
        
        if row['close'] >= ema and row['high'] >= ah + 2.00:
            state['taken'] = True
            trig = ah + 2.00
            return {'type': 'BUY', 'trigger': trig, 'sl': trig - 10.00, 'tp': trig + 25.00}
        elif row['close'] <= ema and row['low'] <= al - 2.00:
            state['taken'] = True
            trig = al - 2.00
            return {'type': 'SELL', 'trigger': trig, 'sl': trig + 10.00, 'tp': trig - 25.00}
        return None

    rep_compressed = sim.run_bars(sessions, signal_asian_compressed)
    sim.print_report(rep_compressed)

if __name__ == "__main__":
    run_tests()

