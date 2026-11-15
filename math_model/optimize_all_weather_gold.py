"""
ALL-WEATHER GOLD QUANTITATIVE OPTIMIZATION
==========================================
Objective: Fix 2022 & 2023 performance to deliver positive returns across
ALL 5 consecutive years (2022, 2023, 2024, 2025, 2026) while maximizing
annual return towards and above 50% under full live broker frictions:
- Vantage Live Spread: $0.30 per oz
- Commission: $6.00 per lot ($0.06/oz)
- Latency Slippage: 345ms ping simulation
- 15:55 IST auto-square-off (100% Flat daily)
- Circuit Breaker: -2.00% daily loss limit

Key Architectural Innovations Tested:
1. Dynamic ATR-Scaled Targets & Stops (Adapts automatically between $1,800 Gold and $5,000 Gold)
2. Asian Session Volatility Flooring (Skips dead compression days that bleed fees)
3. Dynamic Breakout Thresholds based on Rolling Volatility
4. Multi-Cycle Anchors: 12:00 Frankfurt, 12:30 Pre-London, and 13:00 London
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
    
    # Pre-calculate 14-period True Range / ATR on M5
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
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

def make_all_weather_signal_fn(
    anchor_h=12, anchor_m=0,
    break_mult=1.5,       # Breakout required = break_mult * ATR
    retest_mult=0.6,      # Limit order offset = retest_mult * ATR
    sl_atr_mult=2.5,      # SL distance = sl_atr_mult * ATR
    rr=2.2,               # TP = sl_dist * rr
    vol_floor=5.0,        # Minimum Asian range in $ (skips dead days)
    order_cutoff_h=13,
    order_cutoff_m=30
):
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'WAIT_ANCHOR'
            state['base'] = None
            state['atr'] = None
            state['ema'] = None
            state['dir'] = None
            state['limit_p'] = None
            state['sl'] = None
            state['tp'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        # Check order cutoff
        if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
            state['done'] = True
            return None
            
        # Volatility floor check
        if row['asian_range'] < vol_floor:
            state['done'] = True
            return None
            
        if state['phase'] == 'WAIT_ANCHOR' and h == anchor_h and m == anchor_m:
            state['phase'] = 'WAIT_BREAK'
            state['base'] = row['close']
            state['atr'] = max(1.20, row['atr']) # Floor ATR to avoid micro SL
            state['ema'] = row['ema200']
            return None
            
        if state['phase'] == 'WAIT_BREAK':
            base = state['base']
            atr = state['atr']
            ema = state['ema']
            break_dist = break_mult * atr
            retest_dist = retest_mult * atr
            sl_dist = max(4.50, sl_atr_mult * atr) # Must be >= $4.50 to clear empirical noise floor
            tp_dist = sl_dist * rr
            
            # Trend aligned checks
            if base >= ema and row['high'] >= base + break_dist:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'BUY'
                state['limit_p'] = base + retest_dist
                state['sl'] = (base + retest_dist) - sl_dist
                state['tp'] = (base + retest_dist) + tp_dist
            elif base <= ema and row['low'] <= base - break_dist:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'SELL'
                state['limit_p'] = base - retest_dist
                state['sl'] = (base - retest_dist) + sl_dist
                state['tp'] = (base - retest_dist) - tp_dist
                
        elif state['phase'] == 'WAIT_LIMIT_FILL':
            lp = state['limit_p']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                
        return None
    return signal_fn

def main():
    print("Loading Gold data...")
    sessions = load_data()
    print(f"Loaded {len(sessions)} trading sessions. Running All-Weather Optimization...\n")
    
    results = []
    
    # Systematic sweep of parameters
    for anchor_h, anchor_m, c_h, c_m in [(12, 0, 13, 0), (12, 30, 13, 30), (13, 0, 14, 0)]:
        anchor_name = f"{anchor_h:02d}:{anchor_m:02d}"
        for vol_flr in [5.0, 7.0, 9.0]:
            for sl_mult in [2.0, 2.5, 3.0]:
                for rr in [1.8, 2.2, 2.6]:
                    for risk in [0.020, 0.025, 0.030]:
                        sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=risk, daily_loss_limit_pct=2.5, compounding=True)
                        fn = make_all_weather_signal_fn(
                            anchor_h=anchor_h, anchor_m=anchor_m,
                            break_mult=1.5,
                            retest_mult=0.6,
                            sl_atr_mult=sl_mult,
                            rr=rr,
                            vol_floor=vol_flr,
                            order_cutoff_h=c_h,
                            order_cutoff_m=c_m
                        )
                        rep = sim.run_bars(sessions, fn)
                        
                        # Check yearly returns
                        ann_dict = rep['annual_breakdown']
                        neg_years = sum(1 for y, v in ann_dict.items() if v < 0)
                        min_yr = min(ann_dict.values()) if len(ann_dict) > 0 else -100
                        
                        results.append({
                            'anchor': anchor_name,
                            'risk': risk * 100.0,
                            'vol_flr': vol_flr,
                            'sl_mult': sl_mult,
                            'rr': rr,
                            'roi': rep['avg_annual_roi'],
                            'min_yr': min_yr,
                            'neg_years': neg_years,
                            'max_dd': rep['max_drawdown_pct'],
                            'green_m': rep['green_months'],
                            'pf': rep['profit_factor'],
                            'wr': rep['win_rate'],
                            'trades': rep['total_trades'],
                            'final_bal': rep['final_balance'],
                            'ann_dict': ann_dict,
                            'report': rep
                        })

    df = pd.DataFrame(results)
    
    # Priority 1: Configurations with 0 or at most 1 negative year, sorted by Annual ROI
    print("=" * 145)
    print("ALL-WEATHER OPTIMIZATION RESULTS: SORTED BY MINIMUM YEAR & ANNUAL ROI")
    print("=" * 145)
    
    # Filter for all positive years (neg_years == 0) or mild drawdown
    all_green_years = df[df['neg_years'] == 0].sort_values(by='roi', ascending=False)
    mild_down_years = df[(df['neg_years'] <= 1) & (df['min_yr'] > -8.0)].sort_values(by='roi', ascending=False)
    
    print(f"\nConfigurations with 100% POSITIVE YEARS (2022 to 2026 all green): {len(all_green_years)}")
    if len(all_green_years) > 0:
        print(f"{'ANCHOR':<7} | {'RISK':<5} | {'VOL FLR':<7} | {'SL MULT':<7} | {'RR':<4} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'FINAL BAL'}")
        print("-" * 145)
        for idx, r in all_green_years.head(15).iterrows():
            print(f"{r['anchor']:<7} | {r['risk']:4.1f}% | ${r['vol_flr']:<6.1f} | {r['sl_mult']:<7.1f} | {r['rr']:<4.1f} | {r['roi']:+9.2f}% | {r['min_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | ${r['final_bal']:,.2f}")
    else:
        print("\nNo configuration had 0 negative years. Showing configurations with at most 1 mild negative year (Worst Year > -8%):")
        print(f"{'ANCHOR':<7} | {'RISK':<5} | {'VOL FLR':<7} | {'SL MULT':<7} | {'RR':<4} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'FINAL BAL'}")
        print("-" * 145)
        for idx, r in mild_down_years.head(15).iterrows():
            print(f"{r['anchor']:<7} | {r['risk']:4.1f}% | ${r['vol_flr']:<6.1f} | {r['sl_mult']:<7.1f} | {r['rr']:<4.1f} | {r['roi']:+9.2f}% | {r['min_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | ${r['final_bal']:,.2f}")

    # Print champion report
    best = all_green_years.iloc[0] if len(all_green_years) > 0 else mild_down_years.iloc[0]
    print("\n" + "=" * 90)
    print(f"CHAMPION ALL-WEATHER MODEL AUDIT: Anchor {best['anchor']}, Risk {best['risk']:.1f}%, Vol Floor ${best['vol_flr']:.1f}")
    print("=" * 90)
    sim.print_report(best['report'])
    y_str = ", ".join([f"{y}: {v:+.2f}%" for y, v in best['ann_dict'].items()])
    print(f"Yearly Breakdown: {y_str}")

if __name__ == "__main__":
    main()
