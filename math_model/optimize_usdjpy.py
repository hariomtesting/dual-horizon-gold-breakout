"""
USDJPY OPTIMIZATION: HUNTING > 50% ANNUAL ROI
=============================================
Evaluating USDJPY across:
1. Session Timing: 12:00 IST (London Open) vs 06:30 IST (Tokyo Open)
2. Risk Scaling: 2.0% vs 3.0% with Compounding
3. Asymmetric Payoffs: 2.5:1 vs 3.5:1
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def test_usdjpy():
    if not mt5.initialize():
        return
    mt5.symbol_select('USDJPY', True)
    rates = mt5.copy_rates_from_pos('USDJPY', mt5.TIMEFRAME_M5, 0, 100000)
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # 11:00 to 18:00 IST window (holding into London afternoon)
    df_window = df[(df['ist_hour'] >= 10) & (df['ist_hour'] <= 18)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 40:
            sessions[d] = sub.to_dict('records')
            
    print("\n" + "=" * 115)
    print("USDJPY DEEP OPTIMIZATION: HUNTING > 50% ANNUAL RETURN")
    print("=" * 115)
    print(f"{'OPEN HR':<8} | {'TAU':<5} | {'SL':<5} | {'RR':<4} | {'RISK':<5} | {'ANNUAL ROI':<12} | {'PF':<5} | {'WIN %':<6} | {'MAX DD':<8} | {'FINAL BAL'}")
    print("-" * 115)
    
    for open_h in [12, 13]:
        for tau in [1.0, 1.4]:
            for sl_atr in [1.2, 1.5]:
                for rr in [2.5, 3.5]:
                    for risk in [0.02, 0.03]:
                        def make_sig(open_h=open_h, tau=tau, sl_atr=sl_atr, rr=rr):
                            def sig(bars, i, state):
                                row = bars[i]
                                h = row['ist_hour']
                                m = row['ist_minute']
                                if 'phase' not in state:
                                    state['phase'] = 'WAIT_OPEN'
                                    state['done'] = False
                                if state['done'] or h >= 17:
                                    state['done'] = True
                                    return None
                                if state['phase'] == 'WAIT_OPEN' and h == open_h and m == 0:
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
                                        if r_norm >= tau and p1 >= ema:
                                            state['phase'] = 'LIMIT_WAIT'
                                            state['dir'] = 'BUY'
                                            state['lp'] = p1 - (0.25 * atr)
                                            state['sl'] = state['lp'] - (sl_atr * atr)
                                            state['tp'] = state['lp'] + (sl_atr * atr * rr)
                                        elif r_norm <= -tau and p1 <= ema:
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

                        sim = LiveMarketSimulator('USDJPY', 10000.0, risk, spread_override=0.015, commission_per_lot=6.0, daily_loss_limit_pct=2.5, compounding=True)
                        rep = sim.run_bars(sessions, make_sig(open_h, tau, sl_atr, rr))
                        if rep['avg_annual_roi'] >= 20.0 or rep['profit_factor'] >= 1.05:
                            print(f"{open_h:<8} | {tau:<5.1f} | {sl_atr:<5.1f} | {rr:<4.1f} | {risk*100:<4.1f}% | {rep['avg_annual_roi']:+9.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['max_drawdown_pct']:6.2f}% | ${rep['final_balance']:,.2f}")
                            
    mt5.shutdown()
    print("=" * 115)

if __name__ == "__main__":
    test_usdjpy()

