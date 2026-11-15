"""
FOREX PAIRS BENCHMARK: SSRN INTRADAY TIME-SERIES MOMENTUM (ITSM)
================================================================
Evaluating Forex pairs currently active in MT5 MarketWatch:
- EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, EURAUD
Under Real Live Vantage Broker Fees:
- Actual spreads, $6.00/lot commission, 345ms latency slippage.
- Risk: 2.0% per trade, Compounding, -2.0% daily circuit breaker.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_forex_sessions(symbol, start_h=12, end_h=16, n_bars=70000):
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
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    df_window = df[(df['ist_hour'] >= start_h - 2) & (df['ist_hour'] <= end_h)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_forex_itsm_sig(open_start_h=12, tau_atr=1.2, sl_atr=1.5, rr=2.5, cutoff_h=15):
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
        print("MT5 initialization failed!")
        return
        
    pairs = [
        {'sym': 'EURUSD', 'spread': 0.00014, 'name': 'Euro / US Dollar'},
        {'sym': 'GBPUSD', 'spread': 0.00018, 'name': 'British Pound / USD'},
        {'sym': 'USDJPY', 'spread': 0.015,   'name': 'US Dollar / Yen'},
        {'sym': 'USDCAD', 'spread': 0.00018, 'name': 'US Dollar / CAD'},
        {'sym': 'USDCHF', 'spread': 0.00018, 'name': 'US Dollar / Swiss Franc'},
        {'sym': 'EURAUD', 'spread': 0.00022, 'name': 'Euro / Australian Dollar'},
    ]
    
    print("\n" + "=" * 135)
    print("ALL-FOREX SUITE: SSRN INTRADAY TIME-SERIES MOMENTUM (ITSM) PERFORMANCE SCORECARD")
    print("Evaluated strictly under Live Market Simulator (Vantage Spreads, Commission, Slippage, 2.0% Compounding Risk)")
    print("=" * 135)
    print(f"{'PAIR':<10} | {'NAME':<25} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 135)
    
    for p in pairs:
        sym = p['sym']
        sessions = load_forex_sessions(sym, start_h=12, end_h=16, n_bars=70000)
        if not sessions:
            print(f"{sym:<10} | {p['name']:<25} | NO DATA IN MT5")
            continue
            
        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.02,
            spread_override=p['spread'],
            commission_per_lot=6.00,
            daily_loss_limit_pct=2.0,
            compounding=True
        )
        
        sig_fn = make_forex_itsm_sig(open_start_h=12, tau_atr=1.2, sl_atr=1.5, rr=2.5, cutoff_h=15)
        rep = sim.run_bars(sessions, sig_fn)
        
        print(f"{sym:<10} | {p['name']:<25} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")
        
    mt5.shutdown()
    print("=" * 135)

if __name__ == "__main__":
    main()

