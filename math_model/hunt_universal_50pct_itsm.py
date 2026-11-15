"""
MULTI-ASSET UNIVERSAL CHAMPION HUNT (> 50% ANNUAL ROI)
=====================================================
Target: Push SSRN Intraday Time-Series Momentum (ITSM) above +50% Annual ROI
across both Gold (Commodity) and Nasdaq 100 (Equity Index) under full live broker fees.

Tweaks to evaluate:
1. EMA200 Trend Filter (Only trade in direction of macro trend)
2. Impulse Threshold tau (0.8x vs 1.2x vs 1.5x ATR)
3. Split Target Execution (Tranche 1 @ 1.4R locks profit, Tranche 2 @ 3.0R/4.0R runners)
4. Risk scaling (2.0% vs 2.5% vs 3.0%)
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

def make_optimized_itsm_sig(open_start_h, tau_atr, sl_atr, rr, use_ema=True, cutoff_h=15):
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
            if state['cnt'] >= 6: # 30 mins (6 M5 bars)
                p0 = state['p0']
                p1 = row['close']
                atr = state['atr']
                ema = state['ema']
                r_norm = (p1 - p0) / atr
                
                # Check EMA filter & impulse threshold
                if r_norm >= tau_atr and (not use_ema or p1 >= ema):
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'BUY'
                    state['lp'] = p1 - (0.3 * atr)
                    state['sl'] = state['lp'] - (sl_atr * atr)
                    state['tp'] = state['lp'] + (sl_atr * atr * rr)
                elif r_norm <= -tau_atr and (not use_ema or p1 <= ema):
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
        
    print("\n" + "=" * 125)
    print("HUNTING > 50% ANNUAL ROI: UNIVERSAL INTRADAY MOMENTUM (ITSM)")
    print("Evaluating Gold (12:00 IST) and Nasdaq 100 (19:00 IST) across parameters")
    print("=" * 125)
    print(f"{'ASSET':<10} | {'TAU':<5} | {'SL':<5} | {'RR':<4} | {'EMA':<5} | {'RISK':<5} | {'ANNUAL ROI':<12} | {'PF':<5} | {'WIN%':<6} | {'MAX DD':<8} | {'FINAL BAL'}")
    print("-" * 125)
    
    targets = [
        {'sym': 'NAS100.r', 'name': 'Nasdaq', 'start_h': 19, 'end_h': 23, 'spread': 1.2, 'comm': 4.0},
        {'sym': 'XAUUSD', 'name': 'Gold', 'start_h': 12, 'end_h': 16, 'spread': 0.30, 'comm': 6.0}
    ]
    
    for t in targets:
        sessions = load_data(t['sym'], t['start_h'], t['end_h'])
        for tau in [1.0, 1.4]:
            for sl_mult in [1.5, 2.0]:
                for rr in [2.5, 3.0]:
                    for risk in [0.02, 0.03]:
                        sim = LiveMarketSimulator(
                            symbol=t['sym'],
                            initial_balance=10000.0,
                            risk_per_trade=risk,
                            spread_override=t['spread'],
                            commission_per_lot=t['comm'],
                            daily_loss_limit_pct=2.5,
                            compounding=True
                        )
                        sig_fn = make_optimized_itsm_sig(
                            open_start_h=t['start_h'],
                            tau_atr=tau,
                            sl_atr=sl_mult,
                            rr=rr,
                            use_ema=True,
                            cutoff_h=t['end_h'] - 1
                        )
                        rep = sim.run_bars(sessions, sig_fn)
                        if rep['avg_annual_roi'] >= 30.0 or rep['profit_factor'] >= 1.15:
                            print(f"{t['name']:<10} | {tau:<5.1f} | {sl_mult:<5.1f} | {rr:<4.1f} | YES   | {risk*100:<4.1f}% | {rep['avg_annual_roi']:+9.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['max_drawdown_pct']:6.2f}% | ${rep['final_balance']:,.2f}")
                            
    mt5.shutdown()
    print("=" * 125)

if __name__ == "__main__":
    main()

