"""
INSTITUTIONAL ASYMMETRIC EXPECTANCY MODEL (R:R >= 1.35)
========================================================
Solves the inverted R:R trap (where wins were 0.5R and losses were 1.0R).
Enforces:
1. True Positive Expectancy: Target is strictly 1.35x to 1.50x of Risk.
   - Breakeven Win Rate drops to only 42.5%!
2. Entry Trigger: Liquidity Sweep Reversal at Session High/Low (London/NY)
   - When price sweeps session liquidity and closes back inside with delta absorption.
3. Tests across 2018 - 2026 on Gold (XAUUSD) to verify year-by-year profitability.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_asymmetric_expectancy_test():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("      TESTING ASYMMETRIC EXPECTANCY MODEL (R:R = 1.40, BE RATE = 41.7%)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise RuntimeError("No M5 data")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Pre-extract session records by day
    # Active Sessions: London (07:00-11:30 UTC) and NY (13:00-17:30 UTC)
    session_data = {}
    for d, sub in df[(df['hour'] >= 7) & (df['hour'] <= 18)].groupby('date'):
        if len(sub) >= 20:
            session_data[d] = sub[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
            
    print(f"Loaded {len(session_data):,} trading days from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}.\n", flush=True)
    
    risk_per_trade = 0.0125 # 1.25% fixed risk
    reward_mult = 1.40      # Take profit is 1.40x of Stop Loss (Positive R:R!)
    
    trades = []
    daily_results = {}
    
    for curr_d, bars in session_data.items():
        daily_results[curr_d] = {'pnl_pct': 0.0, 'trades': 0}
        
        # Track session rolling high and low
        roll_high = bars[0]['high']
        roll_low = bars[0]['low']
        
        pos = None
        
        for i in range(1, len(bars)):
            r = bars[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            
            # Position management
            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pnl_pct = -risk_per_trade
                        pos['pnl_pct'] = pnl_pct
                        trades.append(pos)
                        daily_results[curr_d]['pnl_pct'] += pnl_pct
                        daily_results[curr_d]['trades'] += 1
                        pos = None
                        break # 1 trade per day max
                    elif high_p >= pos['tp']:
                        pnl_pct = risk_per_trade * reward_mult
                        pos['pnl_pct'] = pnl_pct
                        trades.append(pos)
                        daily_results[curr_d]['pnl_pct'] += pnl_pct
                        daily_results[curr_d]['trades'] += 1
                        pos = None
                        break # 1 trade per day max
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pnl_pct = -risk_per_trade
                        pos['pnl_pct'] = pnl_pct
                        trades.append(pos)
                        daily_results[curr_d]['pnl_pct'] += pnl_pct
                        daily_results[curr_d]['trades'] += 1
                        pos = None
                        break # 1 trade per day max
                    elif low_p <= pos['tp']:
                        pnl_pct = risk_per_trade * reward_mult
                        pos['pnl_pct'] = pnl_pct
                        trades.append(pos)
                        daily_results[curr_d]['pnl_pct'] += pnl_pct
                        daily_results[curr_d]['trades'] += 1
                        pos = None
                        break # 1 trade per day max
                        
            # Entry logic: Liquidity Sweep of Rolling High/Low after initial 60m (i >= 12)
            if pos is None and daily_results[curr_d]['trades'] == 0 and i >= 12:
                sl_dist = 1.30 * atr
                tp_dist = sl_dist * reward_mult # Strictly 1.40x reward
                
                # Bearish Liquidity Sweep: Price pierced rolling high, but closed back below with negative delta
                if high_p > roll_high and close_p < roll_high and delta < 0 and close_p < open_p:
                    pos = {
                        'type': 'SELL', 'date': curr_d,
                        'entry': close_p, 'sl': close_p + sl_dist,
                        'tp': close_p - tp_dist, 'rr': reward_mult,
                        'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                    }
                # Bullish Liquidity Sweep: Price pierced rolling low, but closed back above with positive delta
                elif low_p < roll_low and close_p > roll_low and delta > 0 and close_p > open_p:
                    pos = {
                        'type': 'BUY', 'date': curr_d,
                        'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                        'tp': close_p + tp_dist, 'rr': reward_mult,
                        'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                    }
                    
            roll_high = max(roll_high, high_p)
            roll_low = min(roll_low, low_p)
            
    df_t = pd.DataFrame(trades)
    print(f"Total Completed Trades: {len(df_t):,}")
    if len(df_t) == 0:
        return
        
    tot_wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print(f"Overall Win Rate: {tot_wr:.2f}% (Required Breakeven: {100.0 / (1.0 + reward_mult):.2f}%)\n")
    
    # Year by Year Breakdown
    years = sorted(df_t['year'].unique())
    print("=" * 85)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Status'}")
    print("-" * 85)
    
    annual_rois = []
    for y in years:
        sub = df_t[df_t['year'] == y]
        wr = (sub['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, t in sub.iterrows():
            cap += cap * t['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        annual_rois.append(roi)
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(sub):<8} | {wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(annual_rois):+6.2f}%")
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean()*100:.1f}%\n")


if __name__ == "__main__":
    run_asymmetric_expectancy_test()
