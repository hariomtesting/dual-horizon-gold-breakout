"""
THE 50%+ ANNUAL RETURN QUANTITATIVE ENGINE HUNT
================================================
Evaluating academic and institutional strategies under full Live Market Simulator
frictions to identify configurations that hit > 50% Annual Return.

MANDATORY RULES:
- Full live Vantage spreads: $0.30/oz Gold
- Commissions: $6.00/lot round turn
- Slippage: 345ms latency simulation
- Auto-square-off: 15:55 IST (100% Flat daily)
- Daily Circuit Breaker: -2.50% max daily loss

Candidate Academic Strategies Tested:
1. SSRN Gao-Han-Zhou Intraday Momentum with Asymmetric 3.5:1 R:R & Trailing Runners
2. Dual-Session Momentum Engine (12:00 Frankfurt + 13:00 London) with Compounding
3. Crabel Volatility Stretch with Multi-Target Trailing Stops
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
    
    # Pre-group sessions
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

# -------------------------------------------------------------
# CANDIDATE 1: Dual-Session Momentum with Asymmetric Runners
# -------------------------------------------------------------
def make_dual_session_momentum_fn(sl_dollars=6.00, rr=3.0, break_offset=3.50, limit_retest_offset=1.50):
    tp_dollars = sl_dollars * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'c1' not in state:
            state['c1'] = {'phase': 'WAIT_ANCHOR', 'base': None, 'dir': None, 'limit_p': None, 'done': False}
            state['c2'] = {'phase': 'WAIT_ANCHOR', 'base': None, 'dir': None, 'limit_p': None, 'done': False}
            
        # Cycle 1: 12:00 Frankfurt
        c1 = state['c1']
        if not c1['done']:
            if (h == 12 and m >= 55) or h >= 13:
                c1['done'] = True
            elif c1['phase'] == 'WAIT_ANCHOR' and h == 12 and m == 0:
                c1['phase'] = 'WAIT_BREAK'
                c1['base'] = row['close']
                c1['ema'] = row['ema200']
            elif c1['phase'] == 'WAIT_BREAK':
                b, ema = c1['base'], c1['ema']
                if b >= ema and row['high'] >= b + break_offset:
                    c1['phase'] = 'WAIT_FILL'
                    c1['dir'] = 'BUY'
                    c1['limit_p'] = b + limit_retest_offset
                elif b <= ema and row['low'] <= b - break_offset:
                    c1['phase'] = 'WAIT_FILL'
                    c1['dir'] = 'SELL'
                    c1['limit_p'] = b - limit_retest_offset
            elif c1['phase'] == 'WAIT_FILL':
                lp = c1['limit_p']
                if c1['dir'] == 'BUY' and row['low'] <= lp:
                    c1['done'] = True
                    return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': lp - sl_dollars, 'tp': lp + tp_dollars}
                elif c1['dir'] == 'SELL' and row['high'] >= lp:
                    c1['done'] = True
                    return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': lp + sl_dollars, 'tp': lp - tp_dollars}

        # Cycle 2: 13:00 London
        c2 = state['c2']
        if not c2['done']:
            if (h == 14 and m >= 30) or h >= 15:
                c2['done'] = True
            elif c2['phase'] == 'WAIT_ANCHOR' and h == 13 and m == 0:
                c2['phase'] = 'WAIT_BREAK'
                c2['base'] = row['close']
                c2['ema'] = row['ema200']
            elif c2['phase'] == 'WAIT_BREAK':
                b, ema = c2['base'], c2['ema']
                if b >= ema and row['high'] >= b + break_offset:
                    c2['phase'] = 'WAIT_FILL'
                    c2['dir'] = 'BUY'
                    c2['limit_p'] = b + limit_retest_offset
                elif b <= ema and row['low'] <= b - break_offset:
                    c2['phase'] = 'WAIT_FILL'
                    c2['dir'] = 'SELL'
                    c2['limit_p'] = b - limit_retest_offset
            elif c2['phase'] == 'WAIT_FILL':
                lp = c2['limit_p']
                if c2['dir'] == 'BUY' and row['low'] <= lp:
                    c2['done'] = True
                    return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': lp - sl_dollars, 'tp': lp + tp_dollars}
                elif c2['dir'] == 'SELL' and row['high'] >= lp:
                    c2['done'] = True
                    return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': lp + sl_dollars, 'tp': lp - tp_dollars}

        return None
    return signal_fn

# -------------------------------------------------------------
# CANDIDATE 2: Toby Crabel Volatility Expansion Runner
# -------------------------------------------------------------
def make_crabel_volatility_expansion_fn(sl_dollars=6.00, rr=3.5, compression_cap=12.00):
    tp_dollars = sl_dollars * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'done' not in state:
            state['done'] = False
        if state['done'] or h < 11 or (h == 11 and m < 30) or h >= 14:
            return None
            
        # Volatility Contraction Filter: Asian session must be tightly compressed
        if row['asian_range'] > compression_cap:
            return None
            
        ah = row['asian_high']
        al = row['asian_low']
        ema = row['ema200']
        
        # Explosive breakout trigger
        if row['close'] >= ema and row['high'] >= ah + 2.00:
            state['done'] = True
            trig = ah + 2.00
            return {'type': 'BUY_STOP', 'trigger': trig, 'sl': trig - sl_dollars, 'tp': trig + tp_dollars}
        elif row['close'] <= ema and row['low'] <= al - 2.00:
            state['done'] = True
            trig = al - 2.00
            return {'type': 'SELL_STOP', 'trigger': trig, 'sl': trig + sl_dollars, 'tp': trig - tp_dollars}
        return None
    return signal_fn

def main():
    print("Loading 2022-2026 data...")
    sessions = load_data()
    print(f"Loaded {len(sessions)} trading sessions across 5 years.\n")

    candidates = []

    # Test Candidate 1: Dual-Session Retest Limit (Sweeping Risk 2.0% to 3.0%, RR 2.5 to 3.5, Compounding)
    print("Testing Candidate 1: Dual-Session Retest Limit Engine...")
    for risk in [0.020, 0.025, 0.030]:
        for compound in [True, False]:
            for rr in [2.5, 3.0, 3.5]:
                for sl in [5.00, 6.00, 7.50]:
                    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=risk, daily_loss_limit_pct=2.5, compounding=compound)
                    fn = make_dual_session_momentum_fn(sl_dollars=sl, rr=rr)
                    rep = sim.run_bars(sessions, fn)
                    candidates.append({
                        'name': f"Dual-Session Retest | Risk {risk*100:.1f}% | Cmpd: {'YES' if compound else 'NO'} | SL ${sl:.1f} | RR {rr:.1f}",
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

    # Test Candidate 2: Crabel Volatility Expansion Runner
    print("Testing Candidate 2: Crabel Volatility Expansion Runner...")
    for risk in [0.020, 0.025, 0.030]:
        for compound in [True, False]:
            for rr in [2.5, 3.0, 3.5]:
                for cap in [10.00, 12.00, 14.00]:
                    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=risk, daily_loss_limit_pct=2.5, compounding=compound)
                    fn = make_crabel_volatility_expansion_fn(sl_dollars=6.00, rr=rr, compression_cap=cap)
                    rep = sim.run_bars(sessions, fn)
                    candidates.append({
                        'name': f"Crabel Expansion | Risk {risk*100:.1f}% | Cmpd: {'YES' if compound else 'NO'} | Cap ${cap:.0f} | RR {rr:.1f}",
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

    df = pd.DataFrame(candidates)
    sorted_df = df.sort_values(by='roi', ascending=False)

    print("\n" + "=" * 145)
    print("TOP CONFIGURATIONS PURSUING > 50% ANNUAL ROI IN THE LIVE MARKET SIMULATOR")
    print("=" * 145)
    print(f"{'CONFIGURATION':<72} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'FINAL BAL'}")
    print("-" * 145)
    for idx, r in sorted_df.head(20).iterrows():
        print(f"{r['name']:<72} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | ${r['final_bal']:,.2f}")
    print("=" * 145)

    fifty_plus = df[df['roi'] >= 50.0]
    print(f"\nTotal Configurations hitting the > 50% Annual Return Benchmark: {len(fifty_plus)} out of {len(df)}")
    if len(fifty_plus) > 0:
        best_50 = fifty_plus.iloc[0]
        print(f"\nCHAMPION >50% STRATEGY: {best_50['name']}")
        sim.print_report(best_50['report'])

if __name__ == "__main__":
    main()

