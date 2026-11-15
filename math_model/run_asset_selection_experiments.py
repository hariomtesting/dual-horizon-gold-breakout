"""
SYSTEMATIC MULTI-ASSET TEST & SELECTION LABORATORY
=================================================
Objective: Rigorously test and select the highest-performing quantitative models
tailored to each specific asset class under full live broker fees.

Tests:
1. US INDICES: DJ30.r & NAS100.r (Wall Street Cash Open: 19:00 IST Momentum Retest)
2. FOREX MAJORS: EURUSD & GBPUSD (European Open Liquidity Sweep Fade / Mean-Reversion)
3. GOLD: XAUUSD (All-Weather Single-Session Split-Target Benchmark)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data_from_pos(symbol, n_bars=150000, tf=mt5.TIMEFRAME_M5):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    return df

def run_all_tests():
    if not mt5.initialize():
        print("MT5 initialization failed!")
        return
        
    print("\n" + "=" * 115)
    print("TEST 1: US INDICES (DJ30 & NAS100) AT US CASH OPEN (19:00 IST)")
    print("=" * 115)
    print(f"{'ASSET':<12} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 115)
    
    for sym in ['DJ30.r', 'NAS100.r']:
        df = load_data_from_pos(sym, n_bars=150000)
        if df is None:
            continue
            
        # US Session: 18:30 to 23:30 IST
        df_us = df[(df['ist_hour'] >= 18) & (df['ist_hour'] <= 23)].copy()
        sessions = {}
        for d, sub in df_us.groupby('date'):
            if len(sub) >= 40:
                sessions[d] = sub.to_dict('records')
                
        def make_us_signal_fn():
            def sig(bars, i, state):
                row = bars[i]
                h = row['ist_hour']
                m = row['ist_minute']
                if 'phase' not in state:
                    state['phase'] = 'WAIT_OPEN'
                    state['base'] = None
                    state['atr'] = None
                    state['done'] = False
                if state['done'] or h >= 23:
                    state['done'] = True
                    return None
                if state['phase'] == 'WAIT_OPEN' and h == 19 and m == 0:
                    state['phase'] = 'WAIT_BREAK'
                    state['base'] = row['close']
                    state['atr'] = row['atr']
                    return None
                if state['phase'] == 'WAIT_BREAK':
                    base = state['base']
                    atr = state['atr']
                    break_d = 1.2 * atr
                    retest_d = 0.4 * atr
                    sl_d = 2.0 * atr
                    tp_d = 2.5 * sl_d
                    if row['high'] >= base + break_d:
                        state['phase'] = 'WAIT_LIMIT'
                        state['dir'] = 'BUY'
                        state['lp'] = base + retest_d
                        state['sl'] = (base + retest_d) - sl_d
                        state['tp'] = (base + retest_d) + tp_d
                    elif row['low'] <= base - break_d:
                        state['phase'] = 'WAIT_LIMIT'
                        state['dir'] = 'SELL'
                        state['lp'] = base - retest_d
                        state['sl'] = (base - retest_d) + sl_d
                        state['tp'] = (base - retest_d) - tp_d
                elif state['phase'] == 'WAIT_LIMIT':
                    lp = state['lp']
                    if state['dir'] == 'BUY' and row['low'] <= lp:
                        state['done'] = True
                        return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                    elif state['dir'] == 'SELL' and row['high'] >= lp:
                        state['done'] = True
                        return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                return None
            return sig

        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.02,
            spread_override=1.5 if 'DJ' in sym else 1.2,
            commission_per_lot=4.00,
            daily_loss_limit_pct=2.0,
            compounding=True
        )
        rep = sim.run_bars(sessions, make_us_signal_fn())
        print(f"{sym:<12} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")

    print("\n" + "=" * 115)
    print("TEST 2: FOREX MAJORS (EURUSD & GBPUSD) - ASIAN LIQUIDITY SWEEP MEAN-REVERSION")
    print("=" * 115)
    print(f"{'ASSET':<12} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 115)
    
    for sym in ['EURUSD', 'GBPUSD']:
        df = load_data_from_pos(sym, n_bars=150000)
        if df is None:
            continue
            
        df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy()
        sessions = {}
        for d, sub in df_day.groupby('date'):
            if len(sub) >= 70:
                sub = sub.copy().reset_index(drop=True)
                asian = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
                if len(asian) >= 20:
                    sub['asian_high'] = asian['high'].max()
                    sub['asian_low'] = asian['low'].min()
                    sub['asian_mid'] = (sub['asian_high'] + sub['asian_low']) / 2.0
                    sessions[d] = sub.to_dict('records')
                    
        def make_fx_fade_sig():
            def sig(bars, i, state):
                row = bars[i]
                h = row['ist_hour']
                m = row['ist_minute']
                if 'phase' not in state:
                    state['phase'] = 'WAIT_SWEEP'
                    state['done'] = False
                if state['done'] or h >= 15:
                    state['done'] = True
                    return None
                    
                if state['phase'] == 'WAIT_SWEEP' and h >= 12:
                    ah = row['asian_high']
                    al = row['asian_low']
                    amid = row['asian_mid']
                    
                    sweep_pips = 0.0004 # 4 pips
                    # Bear trap: Wick below Asian low, closes back inside
                    if row['low'] <= al - sweep_pips and row['close'] > al:
                        state['done'] = True
                        sl = row['low'] - 0.0006
                        tp = amid
                        if (row['close'] - sl) >= 0.0008:
                            return {'type': 'BUY_LIMIT', 'trigger': row['close'], 'sl': sl, 'tp': tp}
                    # Bull trap: Wick above Asian high, closes back inside
                    elif row['high'] >= ah + sweep_pips and row['close'] < ah:
                        state['done'] = True
                        sl = row['high'] + 0.0006
                        tp = amid
                        if (sl - row['close']) >= 0.0008:
                            return {'type': 'SELL_LIMIT', 'trigger': row['close'], 'sl': sl, 'tp': tp}
                return None
            return sig

        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.02,
            spread_override=0.00014 if 'EUR' in sym else 0.00018,
            commission_per_lot=6.00,
            daily_loss_limit_pct=2.0,
            compounding=True
        )
        rep = sim.run_bars(sessions, make_fx_fade_sig())
        print(f"{sym:<12} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")

    mt5.shutdown()
    print("=" * 115)

if __name__ == "__main__":
    run_all_tests()
