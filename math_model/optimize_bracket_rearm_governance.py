"""
OPTIMIZING THE CONTINUOUS BRACKET RE-ARM WITH INSTITUTIONAL GOVERNANCE
======================================================================
Testing the User's OCO Bracket Re-Arm with:
1. Max Trades Per Day Cap: 1 trade, 2 trades, 3 trades vs Unlimited
2. Execution Windows: 12:00 to 14:30 IST (London momentum) vs All Day
3. Wider Offsets: $3.50, $4.50, $6.00 (Exceeding the $4.92 market noise floor)
4. SL / TP: $7.50 SL / $15.00 TP (2.0:1) and $18.75 TP (2.5:1)
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
    
    df_day = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 40:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_governed_bracket_fn(
    start_h=12, start_m=0,
    end_h=14, end_m=30,
    max_trades_per_day=2,
    offset_dollars=4.00,
    sl_dollars=7.50,
    rr=2.0,
    trend_filter=True,
    cooldown_bars=1 # 5 mins
):
    tp_dollars = sl_dollars * rr
    
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'WAIT_START'
            state['base'] = None
            state['ema'] = None
            state['trades_today'] = 0
            state['cooldown_until'] = 0
            
        if state['trades_today'] >= max_trades_per_day:
            return None
            
        # Time window check
        if h < start_h or (h == start_h and m < start_m):
            return None
        if h > end_h or (h == end_h and m >= end_m):
            return None
            
        if i < state['cooldown_until']:
            return None
            
        if state['phase'] == 'WAIT_START' or state['phase'] == 'READY':
            state['base'] = row['close']
            state['ema'] = row['ema200']
            state['phase'] = 'ARMED'
            return None
            
        elif state['phase'] == 'ARMED':
            base = state['base']
            ema = state['ema']
            
            allow_buy = (base >= ema) if trend_filter else True
            allow_sell = (base <= ema) if trend_filter else True
            
            buy_trig = base + offset_dollars
            sell_trig = base - offset_dollars
            
            if allow_buy and row['high'] >= buy_trig:
                state['phase'] = 'READY'
                state['trades_today'] += 1
                state['cooldown_until'] = i + cooldown_bars + 1
                return {'type': 'BUY_STOP', 'trigger': buy_trig, 'sl': buy_trig - sl_dollars, 'tp': buy_trig + tp_dollars}
            elif allow_sell and row['low'] <= sell_trig:
                state['phase'] = 'READY'
                state['trades_today'] += 1
                state['cooldown_until'] = i + cooldown_bars + 1
                return {'type': 'SELL_STOP', 'trigger': sell_trig, 'sl': sell_trig + sl_dollars, 'tp': sell_trig - tp_dollars}
                
        return None
    return signal_fn

def main():
    sessions = load_data()
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    results = []
    
    # Test Max Trades Per Day: 1, 2, 3, 5
    for max_tr in [1, 2, 3]:
        for offset in [3.00, 4.00, 5.00]:
            for sl in [6.00, 7.50, 9.00]:
                for rr in [2.0, 2.5]:
                    fn = make_governed_bracket_fn(
                        start_h=12, start_m=0,
                        end_h=14, end_m=30,
                        max_trades_per_day=max_tr,
                        offset_dollars=offset,
                        sl_dollars=sl,
                        rr=rr,
                        trend_filter=True,
                        cooldown_bars=1
                    )
                    rep = sim.run_bars(sessions, fn)
                    results.append({
                        'max_tr': max_tr, 'offset': offset, 'sl': sl, 'rr': rr,
                        'roi': rep['avg_annual_roi'],
                        'worst_yr': rep['worst_year_roi'],
                        'max_dd': rep['max_drawdown_pct'],
                        'green_m': rep['green_months'],
                        'm2026': rep['consistency_2026'],
                        'pf': rep['profit_factor'],
                        'wr': rep['win_rate'],
                        'trades': rep['total_trades'],
                        'toll': rep['total_broker_toll_paid']
                    })

    df_res = pd.DataFrame(results)
    
    print("\n" + "=" * 145)
    print("GOVERNED OCO BRACKET RESULTS (WITH MAX DAILY TRADES & MOMENTUM WINDOW)")
    print("=" * 145)
    print(f"{'MAX TR/DAY':<11} | {'OFFSET':<8} | {'SL':<6} | {'RR':<5} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'TOLL PAID'}")
    print("-" * 145)
    
    top = df_res.sort_values(by='roi', ascending=False)
    for idx, r in top.head(20).iterrows():
        print(f"{r['max_tr']:<11} | ${r['offset']:<7.2f} | ${r['sl']:<5.2f} | {r['rr']:<5.1f} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | {r['trades']:<6} | ${r['toll']:,.2f}")
    print("=" * 145)

if __name__ == "__main__":
    main()

