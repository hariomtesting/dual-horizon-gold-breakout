"""
THE STRATEGY DESIGNED BY THE MARKET: LONDON RETEST & ORDER BLOCK SNIPER (GOLD)
==============================================================================
Designed directly from the 5-year Market DNA findings:
1. Finding 1: Volatility & Direction peak at 13:00 - 14:00 IST (London Open).
2. Finding 2: Trend Continuation is 1.5x more likely than Reversal (51% vs 36%).
3. Finding 3: Raw breakouts pull back by a median of $4.92 (Retest Tax).
4. Solution: NEVER chase the breakout at the peak. Wait for the $3.00 - $5.00 pullback retest!
5. Result: Tight Stop Loss ($3.50 - $4.50), Massive Target ($10.00 - $14.00) = 2.5:1 to 3:1 R:R!
6. Evaluated strictly through the new LiveMarketSimulator with full Vantage fees ($0.30 spread, $6/lot comm, 345ms ping).
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
    
    # Pre-calculate Asian Range (05:30 to 11:30 IST) for each day
    asian_map = {}
    for d, sub in df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].groupby('date'):
        asian_bars = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
        if len(asian_bars) >= 20:
            asian_map[d] = {
                'asian_high': asian_bars['high'].max(),
                'asian_low': asian_bars['low'].min(),
                'asian_mid': (asian_bars['high'].max() + asian_bars['low'].min()) / 2.0
            }
            
    df['asian_high'] = df['date'].map(lambda d: asian_map[d]['asian_high'] if d in asian_map else np.nan)
    df['asian_low'] = df['date'].map(lambda d: asian_map[d]['asian_low'] if d in asian_map else np.nan)
    df['asian_mid'] = df['date'].map(lambda d: asian_map[d]['asian_mid'] if d in asian_map else np.nan)
    return df.dropna().reset_index(drop=True)

def create_market_designed_signal_fn(sl_dollars=4.00, rr=2.5, pullback_depth=3.00):
    """
    State machine:
    Phase 1: Look for initial breakout of Asian High or Low between 11:30 and 13:00 IST.
    Phase 2: Wait for pullback retest into the broken level between 12:30 and 14:00 IST.
    Phase 3: Trigger entry on confirmation candle in the trend direction!
    """
    tp_dollars = sl_dollars * rr
    
    def signal_fn(df_day, i, state):
        row = df_day[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        # Reset state on new day
        if 'phase' not in state:
            state['phase'] = 'WAIT_BREAKOUT'
            state['breakout_dir'] = None
            state['breakout_peak'] = None
            state['taken'] = False
            
        if state['taken']:
            return None
            
        ah = row['asian_high']
        al = row['asian_low']
        
        # Phase 1: Wait for Breakout (11:30 to 13:00 IST)
        if state['phase'] == 'WAIT_BREAKOUT':
            if (h == 11 and m >= 30) or (h == 12) or (h == 13 and m == 0):
                if row['high'] > ah + 1.50: # Clean break of Asian High
                    state['phase'] = 'WAIT_PULLBACK'
                    state['breakout_dir'] = 'BUY'
                    state['breakout_peak'] = row['high']
                elif row['low'] < al - 1.50: # Clean break of Asian Low
                    state['phase'] = 'WAIT_PULLBACK'
                    state['breakout_dir'] = 'SELL'
                    state['breakout_peak'] = row['low']
                    
        # Phase 2: Wait for Natural Market Pullback Retest (12:30 to 14:00 IST)
        elif state['phase'] == 'WAIT_PULLBACK':
            # Stop looking after 14:00 IST
            if h >= 14:
                state['phase'] = 'DONE'
                return None
                
            bdir = state['breakout_dir']
            if bdir == 'BUY':
                if row['high'] > state['breakout_peak']:
                    state['breakout_peak'] = row['high']
                    
                # Pullback condition: price pulls back towards Asian High (within $ pullback_depth)
                pullback_target = ah + 0.50
                if row['low'] <= state['breakout_peak'] - pullback_depth and row['low'] >= ah - 1.50:
                    # Pullback verified! Check for bullish rejection (Close > Open)
                    if row['close'] > row['open']:
                        state['taken'] = True
                        entry_trig = row['close']
                        return {
                            'type': 'BUY',
                            'trigger': entry_trig,
                            'sl': entry_trig - sl_dollars,
                            'tp': entry_trig + tp_dollars
                        }
            else: # SELL
                if row['low'] < state['breakout_peak']:
                    state['breakout_peak'] = row['low']
                    
                # Pullback condition: price rallies back towards Asian Low
                if row['high'] >= state['breakout_peak'] + pullback_depth and row['high'] <= al + 1.50:
                    # Bearish rejection (Close < Open)
                    if row['close'] < row['open']:
                        state['taken'] = True
                        entry_trig = row['close']
                        return {
                            'type': 'SELL',
                            'trigger': entry_trig,
                            'sl': entry_trig + sl_dollars,
                            'tp': entry_trig - tp_dollars
                        }
                        
        return None

    return signal_fn

def main():
    print("Loading 2022-2026 Gold data...")
    df = load_data()
    print("Pre-grouping daily sessions for ultra-fast simulation...")
    daily_sessions = {d: sub.to_dict('records') for d, sub in df.groupby('date') if len(sub) >= 20}
    print(f"Loaded {len(daily_sessions)} trading days. Testing the Market-Designed Strategy through LiveMarketSimulator...\n")
    
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    # Test sweeps of the Market-Designed Architecture
    results = []
    for sl in [3.50, 4.00, 4.50, 5.00]:
        for rr in [2.0, 2.5, 3.0]:
            for pb in [2.50, 3.00, 4.00]:
                sig_fn = create_market_designed_signal_fn(sl_dollars=sl, rr=rr, pullback_depth=pb)
                report = sim.run_bars(daily_sessions, sig_fn)
                results.append({
                    'sl': sl, 'rr': rr, 'pb': pb,
                    'roi': report['avg_annual_roi'],
                    'worst_yr': report['worst_year_roi'],
                    'max_dd': report['max_drawdown_pct'],
                    'green_m': report['green_months'],
                    'm2026': report['consistency_2026'],
                    'pf': report['profit_factor'],
                    'wr': report['win_rate'],
                    'trades': report['total_trades'],
                    'avg_r': report['avg_r_per_trade'],
                    'toll': report['total_broker_toll_paid'],
                    'report': report
                })

    df_res = pd.DataFrame(results)
    profitable = df_res.sort_values(by='roi', ascending=False)
    
    print("=" * 135)
    print("MARKET-DESIGNED LONDON RETEST SNIPER (EVALUATED UNDER FULL LIVE VANTAGE SPREADS & FEES)")
    print("=" * 135)
    print(f"{'CONFIGURATION':<32} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'2026':<6} | {'PF':<5} | {'TRADES'}")
    print("-" * 135)
    for idx, r in profitable.head(15).iterrows():
        cfg_name = f"SL ${r['sl']:.2f} | RR {r['rr']:.1f} | PB ${r['pb']:.2f}"
        print(f"{cfg_name:<32} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_m']:<15} | {r['m2026']:<6} | {r['pf']:<5.2f} | {r['trades']}")
    print("=" * 135)

    best = profitable.iloc[0]['report']
    sim.print_report(best)

if __name__ == "__main__":
    main()
