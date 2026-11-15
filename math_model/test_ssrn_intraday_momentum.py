"""
SSRN ACADEMIC STRATEGY IMPLEMENTATION: INTRADAY MOMENTUM & VOLATILITY EXPANSION
================================================================================
Academic Sources:
1. Gao, Han, & Zhou (2018), "Intraday Momentum", Journal of Financial Economics & SSRN.
   - Core Principle: First 30-minute institutional return (12:00 to 12:30 IST) dictates
     the directional drift of the subsequent session with high statistical significance.
2. Crabel (1990) & Modern SSRN extensions:
   - Opening Range Breakout with Volatility Contraction overlay on Precious Metals (Gold).

Target Objective:
- Evaluate whether this SSRN strategy achieves > 50% Annual Return under full live
  Vantage broker frictions ($0.30 spread, $6.00/lot commission, 345ms latency slippage).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_gold_data():
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
    
    # Pre-group sessions between 05:30 and 16:00 IST
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

def make_ssrn_intraday_momentum_fn(
    mom_threshold=2.50, # Minimum $ move in the first 30 mins (12:00 to 12:30 IST)
    sl_dollars=6.00,
    rr_multiplier=2.5,  # 2.5:1 to 3.5:1 asymmetric R:R
    use_trailing=False,
    entry_style="LIMIT_RETEST" # "LIMIT_RETEST" or "BREAKOUT_STOP"
):
    tp_dollars = sl_dollars * rr_multiplier
    
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'WAIT_1200'
            state['p_1200'] = None
            state['p_1230'] = None
            state['ema'] = None
            state['dir'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        # Hard cutoff: Stop looking after 14:00 IST
        if h >= 14:
            state['done'] = True
            return None
            
        # Step 1: Record 12:00 PM Open
        if state['phase'] == 'WAIT_1200' and h == 12 and m == 0:
            state['p_1200'] = row['open']
            state['ema'] = row['ema200']
            state['phase'] = 'MEASURE_30MIN'
            return None
            
        # Step 2: Measure 30-minute Gao-Han-Zhou Momentum Vector at 12:30 PM IST
        elif state['phase'] == 'MEASURE_30MIN' and h == 12 and m == 30:
            p_1200 = state['p_1200']
            p_1230 = row['close']
            ema = state['ema']
            delta = p_1230 - p_1200
            
            # Condition A: Strong Bullish Momentum Vector
            if delta >= mom_threshold and p_1230 >= ema:
                state['dir'] = 'BUY'
                state['phase'] = 'ARMED'
                state['trigger_ref'] = p_1230
            # Condition B: Strong Bearish Momentum Vector
            elif delta <= -mom_threshold and p_1230 <= ema:
                state['dir'] = 'SELL'
                state['phase'] = 'ARMED'
                state['trigger_ref'] = p_1230
            else:
                # No clear institutional consensus; stay flat today
                state['done'] = True
                return None
                
        # Step 3: Trigger Execution after 12:30 PM IST
        elif state['phase'] == 'ARMED':
            ref_p = state['trigger_ref']
            if state['dir'] == 'BUY':
                if entry_style == "LIMIT_RETEST":
                    # Place Buy Limit on slight pullback to get Maker execution
                    lim_p = ref_p - 1.00
                    if row['low'] <= lim_p:
                        state['done'] = True
                        return {'type': 'BUY_LIMIT', 'trigger': lim_p, 'sl': lim_p - sl_dollars, 'tp': lim_p + tp_dollars}
                else: # BREAKOUT_STOP
                    trig_p = ref_p + 1.00
                    if row['high'] >= trig_p:
                        state['done'] = True
                        return {'type': 'BUY_STOP', 'trigger': trig_p, 'sl': trig_p - sl_dollars, 'tp': trig_p + tp_dollars}
            else: # SELL
                if entry_style == "LIMIT_RETEST":
                    lim_p = ref_p + 1.00
                    if row['high'] >= lim_p:
                        state['done'] = True
                        return {'type': 'SELL_LIMIT', 'trigger': lim_p, 'sl': lim_p + sl_dollars, 'tp': lim_p - tp_dollars}
                else:
                    trig_p = ref_p - 1.00
                    if row['low'] <= trig_p:
                        state['done'] = True
                        return {'type': 'SELL_STOP', 'trigger': trig_p, 'sl': trig_p + sl_dollars, 'tp': trig_p - tp_dollars}
                        
        return None
    return signal_fn

def main():
    print("Loading 2022-2026 Gold 5-minute data from MT5...")
    sessions = load_gold_data()
    print(f"Loaded {len(sessions)} trading sessions across 5 years.\n")
    print("=" * 135)
    print("TESTING SSRN INTRADAY MOMENTUM STRATEGY (GAO, HAN, ZHOU & CRABEL FRAMEWORK)")
    print("Target: Discover if any variant delivers > 50% Annual Return under Live Broker Frictions")
    print("=" * 135)

    all_tests = []
    
    # Test sweeps across:
    # 1. Risk per trade: 1.0%, 1.5%, 2.0%
    # 2. Compounding: True vs False
    # 3. Momentum threshold: $2.00, $3.00, $4.00
    # 4. Stop Loss: $5.00, $6.00, $7.50
    # 5. R:R Multiplier: 2.0, 2.5, 3.0, 3.5
    for risk in [0.010, 0.015, 0.020]:
        for compound in [False, True]:
            for mom in [2.00, 3.00, 4.00]:
                for sl in [5.00, 6.00, 7.50]:
                    for rr in [2.0, 2.5, 3.0]:
                        sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=risk, compounding=compound)
                        fn = make_ssrn_intraday_momentum_fn(mom_threshold=mom, sl_dollars=sl, rr_multiplier=rr, entry_style="LIMIT_RETEST")
                        rep = sim.run_bars(sessions, fn)
                        all_tests.append({
                            'risk': risk * 100.0,
                            'compound': compound,
                            'mom': mom,
                            'sl': sl,
                            'rr': rr,
                            'roi': rep['avg_annual_roi'],
                            'worst_yr': rep['worst_year_roi'],
                            'max_dd': rep['max_drawdown_pct'],
                            'green_m': rep['green_months'],
                            'm2026': rep['consistency_2026'],
                            'pf': rep['profit_factor'],
                            'wr': rep['win_rate'],
                            'trades': rep['total_trades'],
                            'final_bal': rep['final_balance'],
                            'report': rep
                        })

    df = pd.DataFrame(all_tests)
    sorted_df = df.sort_values(by='roi', ascending=False)
    
    print(f"{'RISK':<5} | {'CMPD':<5} | {'MOM':<6} | {'SL':<6} | {'RR':<5} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'FINAL BAL'}")
    print("-" * 135)
    for idx, r in sorted_df.head(25).iterrows():
        cmpd_str = "YES" if r['compound'] else "NO"
        print(f"{r['risk']:4.1f}% | {cmpd_str:<5} | ${r['mom']:<5.1f} | ${r['sl']:<5.1f} | {r['rr']:<5.1f} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | ${r['final_bal']:,.2f}")
    print("=" * 135)

    # Check if any configurations reached > 50% Annual ROI
    fifty_plus = df[df['roi'] >= 50.0]
    print(f"\nTotal Configurations hitting the > 50% Annual Return Benchmark: {len(fifty_plus)} out of {len(df)}")
    if len(fifty_plus) > 0:
        print("\nTOP 5 CONFIGURATIONS DELIVERING > 50% ANNUAL ROI:")
        for idx, r in fifty_plus.head(5).iterrows():
            cmpd_str = "YES" if r['compound'] else "NO"
            print(f"  * Risk {r['risk']:.1f}% (Compound: {cmpd_str}) | Mom ${r['mom']:.1f} | SL ${r['sl']:.1f} | RR {r['rr']:.1f} -> Annual ROI: {r['roi']:+6.2f}% | Max DD: {r['max_dd']:.2f}% | PF: {r['pf']:.2f} | Final: ${r['final_bal']:,.2f}")
            sim.print_report(r['report'])

if __name__ == "__main__":
    main()

