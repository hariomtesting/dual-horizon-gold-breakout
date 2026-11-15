"""
UNIVERSAL INTRADAY TIME-SERIES MOMENTUM (ITSM) BENCHMARK
=======================================================
Based on:
- Moskowitz, Ooi, Pedersen (2012) "Time Series Momentum"
- Gao, Han, Li, Zhou (2018) "Market Intraday Momentum" (Journal of Financial Economics)
- Li, Sakkas, Urquhart (2022) "Intraday Time Series Momentum: Global Evidence"

Universal Architecture:
1. Unit-Free Metric: Normalized Session Opening Return:
   R_norm = (P_open_end - P_open_start) / ATR_14
2. Execution:
   - If |R_norm| >= tau (Impulse Filter, e.g. 1.0x ATR)
   - Enter via Maker Limit Order on shallow pullback (0.3x ATR) to avoid taker slippage
   - Stop Loss = 2.0x ATR
   - Split Target: Tranche 1 at 1.4R (locks 50%), Tranche 2 at 3.0R (runners)
3. Evaluated strictly under Live Market Simulator (Vantage spreads, commission, latency).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def get_session_bars(symbol, n_bars=150000, start_h=12, end_h=16):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return {}
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    # 200 EMA & 14 ATR
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter hours
    df_window = df[(df['ist_hour'] >= start_h - 2) & (df['ist_hour'] <= end_h)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_itsm_signal_fn(open_start_h=12, open_start_m=0, open_eval_m=30, tau_atr=1.0, sl_atr=2.0, rr1=1.4, rr2=3.0, cutoff_h=15):
    def signal_fn(bars, i, state):
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
            
        # 1. Record open price
        if state['phase'] == 'WAIT_OPEN' and h == open_start_h and m == open_start_m:
            state['phase'] = 'EVALUATING'
            state['p0'] = row['open']
            state['atr'] = row['atr']
            state['eval_count'] = 0
            return None
            
        # 2. Evaluate opening interval return (first 30 mins)
        if state['phase'] == 'EVALUATING':
            state['eval_count'] += 1
            if state['eval_count'] >= (open_eval_m // 5):
                p0 = state['p0']
                p1 = row['close']
                atr = state['atr']
                r_norm = (p1 - p0) / atr
                
                # Check if impulse crosses threshold tau
                if r_norm >= tau_atr:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'BUY'
                    # Resting limit order on 0.3x ATR pullback
                    state['limit_p'] = p1 - (0.3 * atr)
                    state['sl'] = state['limit_p'] - (sl_atr * atr)
                    state['tp'] = state['limit_p'] + (sl_atr * atr * rr2) # Runner target
                elif r_norm <= -tau_atr:
                    state['phase'] = 'LIMIT_WAIT'
                    state['dir'] = 'SELL'
                    state['limit_p'] = p1 + (0.3 * atr)
                    state['sl'] = state['limit_p'] + (sl_atr * atr)
                    state['tp'] = state['limit_p'] - (sl_atr * atr * rr2)
                else:
                    state['done'] = True # No trade today (filter rejected)
                return None
                
        # 3. Wait for Limit Fill
        if state['phase'] == 'LIMIT_WAIT':
            lp = state['limit_p']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                
        return None
    return signal_fn

def run_universal_test():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    assets = [
        {'sym': 'XAUUSD', 'name': 'Gold (Commodity)', 'start_h': 12, 'end_h': 16, 'spread': 0.30, 'comm': 6.0},
        {'sym': 'EURUSD', 'name': 'Euro (FX Major)', 'start_h': 12, 'end_h': 16, 'spread': 0.00014, 'comm': 6.0},
        {'sym': 'GBPUSD', 'name': 'Pound (FX Major)', 'start_h': 12, 'end_h': 16, 'spread': 0.00018, 'comm': 6.0},
        {'sym': 'DJ30.r', 'name': 'Dow Jones (US Index)', 'start_h': 19, 'end_h': 23, 'spread': 1.5, 'comm': 4.0},
        {'sym': 'NAS100.r', 'name': 'Nasdaq 100 (US Index)', 'start_h': 19, 'end_h': 23, 'spread': 1.2, 'comm': 4.0},
    ]
    
    print("\n" + "=" * 135)
    print("SSRN INTRADAY TIME-SERIES MOMENTUM (ITSM): UNIVERSAL ASSET EVALUATION")
    print("Under Real Live Vantage Broker Fees (Spread, Commission, Latency Slippage, 2% Risk, Compounding)")
    print("=" * 135)
    print(f"{'ASSET NAME':<24} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 135)
    
    for a in assets:
        sym = a['sym']
        sessions = get_session_bars(sym, n_bars=150000, start_h=a['start_h'], end_h=a['end_h'])
        if not sessions:
            continue
            
        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.02,
            spread_override=a['spread'],
            commission_per_lot=a['comm'],
            daily_loss_limit_pct=2.0,
            compounding=True
        )
        
        sig_fn = make_itsm_signal_fn(open_start_h=a['start_h'], open_start_m=0, open_eval_m=30, tau_atr=0.8, sl_atr=2.0, rr2=2.5, cutoff_h=a['end_h'] - 1)
        rep = sim.run_bars(sessions, sig_fn)
        
        print(f"{a['name']:<24} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")
        
    mt5.shutdown()
    print("=" * 135)

if __name__ == "__main__":
    run_universal_test()
