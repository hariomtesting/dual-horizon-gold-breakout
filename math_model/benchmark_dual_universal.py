"""
UNIVERSAL QUANTITATIVE PORTFOLIO BENCHMARK
==========================================
Asset 1: Gold (XAUUSD) @ 12:00 PM IST European Session
Asset 2: Nasdaq 100 (NAS100.r) @ 19:00 IST US Session
Strategy: SSRN Intraday Time-Series Momentum (ITSM) + Retest Limit Orders
Evaluation: Live Market Simulator (Vantage Spreads, Commission, Slippage)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data(symbol, start_h, end_h):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 150000)
    if rates is None or len(rates) == 0:
        return {}
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    df_window = df[(df['ist_hour'] >= start_h - 2) & (df['ist_hour'] <= end_h)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_optimized_itsm_sig(open_start_h, tau_atr=1.4, sl_atr=1.5, rr=3.0, cutoff_h=15):
    def sig(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'phase' not in state:
            state['phase'] = 'WAIT_OPEN'
            state['p0'] = None
            state['atr'] = None
            state['done'] = False
        if state['done'] or h >= cutoff_h:
            state['done'] = True
            return None
        if state['phase'] == 'WAIT_OPEN' and h == open_start_h and m == 0:
            state['phase'] = 'EVAL'
            state['p0'] = row['open']
            state['atr'] = row['atr']
            state['ema'] = row['ema200']
            state['cnt'] = 0
            return None
        if state['phase'] == 'EVAL':
            state['cnt'] += 1
            if state['cnt'] >= 6:
                p0 = state['p0']
                p1 = row['close']
                atr = state['atr']
                ema = state['ema']
                r_norm = (p1 - p0) / atr
                if r_norm >= tau_atr and p1 >= ema:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'BUY'
                    state['lp'] = p1 - (0.3 * atr)
                    state['sl'] = state['lp'] - (sl_atr * atr)
                    state['tp'] = state['lp'] + (sl_atr * atr * rr)
                elif r_norm <= -tau_atr and p1 <= ema:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'SELL'
                    state['lp'] = p1 + (0.3 * atr)
                    state['sl'] = state['lp'] + (sl_atr * atr)
                    state['tp'] = state['lp'] - (sl_atr * atr * rr)
                else:
                    state['done'] = True
                return None
        if state['phase'] == 'LIMIT_WAIT':
            lp = state['lp']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
        return None
    return sig

def main():
    if not mt5.initialize():
        return
        
    print("\n" + "=" * 115)
    print("UNIVERSAL QUANTITATIVE STRATEGY: DUAL-ASSET VERIFICATION")
    print("=" * 115)
    
    # 1. Gold Simulation
    gold_sessions = load_data('XAUUSD', 12, 16)
    sim_gold = LiveMarketSimulator('XAUUSD', 10000.0, 0.025, spread_override=0.30, commission_per_lot=6.0, daily_loss_limit_pct=2.5, compounding=True)
    rep_gold = sim_gold.run_bars(gold_sessions, make_optimized_itsm_sig(12, 1.4, 1.5, 3.0, 15))
    
    # 2. Nasdaq Simulation
    nas_sessions = load_data('NAS100.r', 19, 23)
    sim_nas = LiveMarketSimulator('NAS100.r', 10000.0, 0.025, spread_override=1.20, commission_per_lot=4.0, daily_loss_limit_pct=2.5, compounding=True)
    rep_nas = sim_nas.run_bars(nas_sessions, make_optimized_itsm_sig(19, 1.4, 1.5, 3.0, 22))
    
    mt5.shutdown()
    
    print(f"{'ASSET':<12} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 115)
    print(f"{'Gold (XAU)':<12} | {rep_gold['avg_annual_roi']:+9.2f}% | {rep_gold['worst_year_roi']:+8.2f}% | {rep_gold['max_drawdown_pct']:8.2f}% | {rep_gold['profit_factor']:<5.2f} | {rep_gold['win_rate']:4.1f}% | {rep_gold['total_trades']:<6} | ${rep_gold['final_balance']:,.2f}")
    print(f"{'Nasdaq 100':<12} | {rep_nas['avg_annual_roi']:+9.2f}% | {rep_nas['worst_year_roi']:+8.2f}% | {rep_nas['max_drawdown_pct']:8.2f}% | {rep_nas['profit_factor']:<5.2f} | {rep_nas['win_rate']:4.1f}% | {rep_nas['total_trades']:<6} | ${rep_nas['final_balance']:,.2f}")
    print("=" * 115)
    
if __name__ == "__main__":
    main()

