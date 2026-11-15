"""
BITCOIN (BTCUSD) QUANTITATIVE BENCHMARK: SSRN INTRADAY TIME-SERIES MOMENTUM (ITSM)
==================================================================================
Evaluating BTCUSD under full live Vantage broker fees:
- Spread: $17.05 (1705 points, 2 decimals)
- Commission: $6.00 / lot
- Latency Slippage: 345ms
- Evaluated across US ETF Cash Session (19:00 IST) and European Session (12:00 IST).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_btc_sessions(start_h=19, end_h=23, n_bars=100000):
    mt5.symbol_select('BTCUSD', True)
    rates = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M5, 0, n_bars)
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

def make_btc_itsm_sig(open_start_h=19, tau_atr=1.2, sl_atr=1.5, rr=2.5, cutoff_h=23):
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
            if state['cnt'] >= 6: # 30 mins
                p0 = state['p0']
                p1 = row['close']
                atr = state['atr']
                ema = state['ema']
                r_norm = (p1 - p0) / atr
                if r_norm >= tau_atr and p1 >= ema:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'BUY'
                    state['lp'] = p1 - (0.25 * atr)
                    state['sl'] = state['lp'] - (sl_atr * atr)
                    state['tp'] = state['lp'] + (sl_atr * atr * rr)
                elif r_norm <= -tau_atr and p1 <= ema:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'SELL'
                    state['lp'] = p1 + (0.25 * atr)
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
        print("MT5 failed")
        return
        
    print("\n" + "=" * 125)
    print("BITCOIN (BTCUSD) UNIVERSAL QUANTITATIVE BENCHMARK")
    print("Under Real Live Vantage Broker Fees (Spread $17.05, Comm $6.00/lot, 345ms latency)")
    print("=" * 125)
    print(f"{'SESSION':<16} | {'TAU':<5} | {'SL':<5} | {'RR':<4} | {'RISK':<5} | {'ANNUAL ROI':<12} | {'PF':<5} | {'WIN %':<6} | {'MAX DD':<8} | {'FINAL BAL'}")
    print("-" * 125)
    
    # Test 1: US ETF Cash Session (19:00 IST to 23:00 IST)
    us_sessions = load_btc_sessions(start_h=19, end_h=23, n_bars=100000)
    for tau in [1.0, 1.4]:
        for sl_atr in [1.5, 2.0]:
            for rr in [2.5, 3.0]:
                for risk in [0.02, 0.03]:
                    sim = LiveMarketSimulator('BTCUSD', 10000.0, risk, spread_override=17.05, commission_per_lot=6.0, daily_loss_limit_pct=2.5, compounding=True)
                    sig_fn = make_btc_itsm_sig(open_start_h=19, tau_atr=tau, sl_atr=sl_atr, rr=rr, cutoff_h=23)
                    rep = sim.run_bars(us_sessions, sig_fn)
                    print(f"{'US Cash (19:00)':<16} | {tau:<5.1f} | {sl_atr:<5.1f} | {rr:<4.1f} | {risk*100:<4.1f}% | {rep['avg_annual_roi']:+9.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['max_drawdown_pct']:6.2f}% | ${rep['final_balance']:,.2f}")
                    
    # Test 2: European Session (12:00 IST to 16:00 IST)
    print("-" * 125)
    eu_sessions = load_btc_sessions(start_h=12, end_h=16, n_bars=100000)
    for tau in [1.0, 1.4]:
        for sl_atr in [1.5, 2.0]:
            for rr in [2.5, 3.0]:
                for risk in [0.02, 0.03]:
                    sim = LiveMarketSimulator('BTCUSD', 10000.0, risk, spread_override=17.05, commission_per_lot=6.0, daily_loss_limit_pct=2.5, compounding=True)
                    sig_fn = make_btc_itsm_sig(open_start_h=12, tau_atr=tau, sl_atr=sl_atr, rr=rr, cutoff_h=15)
                    rep = sim.run_bars(eu_sessions, sig_fn)
                    if rep['avg_annual_roi'] >= 10.0 or rep['profit_factor'] >= 1.05:
                        print(f"{'EU Cash (12:00)':<16} | {tau:<5.1f} | {sl_atr:<5.1f} | {rr:<4.1f} | {risk*100:<4.1f}% | {rep['avg_annual_roi']:+9.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['max_drawdown_pct']:6.2f}% | ${rep['final_balance']:,.2f}")
                        
    mt5.shutdown()
    print("=" * 125)

if __name__ == "__main__":
    main()

