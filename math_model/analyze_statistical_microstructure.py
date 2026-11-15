"""
FAST STATISTICAL SCALPER RESEARCH (NUMPY OPTIMIZED)
===================================================
Computes exact conditional probabilities of mean-reversion & momentum continuation:
P(Revert to VWAP within k bars | Z-Score >= z_th)
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def analyze_symbol(symbol="GER40.r", dt_start=2024):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select(symbol, True)
    
    dt_from = datetime.datetime(dt_start, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        return
        
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
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    
    # Calculate intraday session VWAP and Standard Deviation Bands
    tp = (df_ist['high'] + df_ist['low'] + df_ist['close']) / 3.0
    df_ist['pv'] = tp * df_ist['volume']
    df_ist['cum_vol'] = df_ist.groupby('date')['volume'].cumsum()
    df_ist['cum_pv'] = df_ist.groupby('date')['pv'].cumsum()
    df_ist['vwap'] = df_ist['cum_pv'] / df_ist['cum_vol'].replace(0, np.nan)
    
    # VWAP Variance & Standard Deviation
    df_ist['dev_sq'] = ((tp - df_ist['vwap']) ** 2) * df_ist['volume']
    df_ist['cum_dev_sq'] = df_ist.groupby('date')['dev_sq'].cumsum()
    df_ist['vwap_std'] = np.sqrt(df_ist['cum_dev_sq'] / df_ist['cum_vol'].replace(0, np.nan)).bfill()
    df_ist['z_score'] = (df_ist['close'] - df_ist['vwap']) / df_ist['vwap_std'].replace(0, np.nan)
    
    total_days = df_ist['date'].nunique()
    print("=" * 85)
    print(f"      MICROSTRUCTURE STATISTICAL ANALYSIS: {symbol} (11:00 - 16:00 IST)      ")
    print("=" * 85)
    print(f"Total M5 Bars: {len(df_ist):,} across {total_days:,} Days\n")
    
    # Fast numpy analysis per session
    sessions = [sub.reset_index(drop=True) for _, sub in df_ist.groupby('date') if len(sub) >= 30]
    
    z_thresholds = [1.5, 1.8, 2.0, 2.2, 2.5]
    
    for z_th in z_thresholds:
        tot_events = 0
        reverts = 0
        continuation_runs = 0
        
        for sess in sessions:
            closes = sess['close'].values
            highs = sess['high'].values
            lows = sess['low'].values
            vwaps = sess['vwap'].values
            zs = sess['z_score'].values
            n = len(closes)
            
            # Start after bar 6 (30 mins of session elapsed)
            for i in range(6, n - 6):
                z = zs[i]
                vwap = vwaps[i]
                
                # Upper tail event: Price > +Z*std
                if z >= z_th and zs[i-1] < z_th: # first cross
                    tot_events += 1
                    # Look ahead up to 12 bars (1 hour)
                    future_lows = lows[i+1:min(i+13, n)]
                    if len(future_lows) > 0 and np.min(future_lows) <= vwap:
                        reverts += 1
                        
                # Lower tail event: Price < -Z*std
                elif z <= -z_th and zs[i-1] > -z_th: # first cross
                    tot_events += 1
                    future_highs = highs[i+1:min(i+13, n)]
                    if len(future_highs) > 0 and np.max(future_highs) >= vwap:
                        reverts += 1
                        
        prob = (reverts / tot_events * 100.0) if tot_events > 0 else 0
        ev_per_day = tot_events / len(sessions) if len(sessions) > 0 else 0
        print(f"Threshold |Z| >= {z_th:3.1f} | Setups: {tot_events:4,d} ({ev_per_day:3.1f}/day) | Reversion to VWAP: {reverts:4,d} ({prob:5.2f}%)")
        
    print("\n" + "=" * 85 + "\n")


if __name__ == "__main__":
    analyze_symbol("GER40.r", 2024)
    analyze_symbol("XAUUSD", 2024)
