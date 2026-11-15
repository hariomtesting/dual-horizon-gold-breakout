"""
SSRN PAPER #4729284 BENCHMARK: OPENING RANGE BREAKOUT WITH RELATIVE VOLUME (RVOL)
=================================================================================
Source:
"A Profitable Day Trading Strategy For The U.S. Equity Market" (SSRN Paper #4729284, 2024)
Authors: Carlo Zarattini, Andrea Barbon, and Andrew Aziz
Paper Reported Stats: 1,600% Total Return, Sharpe 2.81, 41.6%+ Annual Return net of fees.

Rules:
1. Opening Range: Mark High (H_orb) and Low (L_orb) of the first 5-minute bar of the cash session.
2. Relative Volume (RVOL) Filter:
   - Volume on opening bar must be >= 1.5x (or 2.0x) rolling 20-day average volume at that hour.
3. Directional Bias: Macro EMA200 (Long only if above EMA200, Short only if below EMA200).
4. Maker Retest Limit Entry:
   - Waits for breakout, places Limit Order at H_orb / L_orb to avoid adverse taker slippage.
5. Asymmetric R:R: SL = 1.0x to 1.5x ATR, Target = 2.5x to 3.0x SL distance.
6. Hard daily close at session square-off.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data_with_volume(symbol, start_h, end_h, n_bars=150000):
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
    
    # EMA200 & ATR14
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Tick Volume Rolling Average (20 periods)
    df['vol_ma20'] = df['tick_volume'].rolling(20, min_periods=5).mean()
    df['rvol'] = df['tick_volume'] / (df['vol_ma20'] + 1e-5)
    
    df_window = df[(df['ist_hour'] >= start_h - 2) & (df['ist_hour'] <= end_h)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_ssrn_orb_sig(open_h=19, open_m=0, rvol_min=1.4, sl_atr=1.5, rr=2.5, cutoff_h=23):
    def sig(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'WAIT_OPEN'
            state['h_orb'] = None
            state['l_orb'] = None
            state['atr'] = None
            state['done'] = False
            
        if state['done'] or h >= cutoff_h:
            state['done'] = True
            return None
            
        # 1. Capture 5-minute Opening Bar
        if state['phase'] == 'WAIT_OPEN' and h == open_h and m == open_m:
            rvol = row['rvol']
            if rvol < rvol_min:
                state['done'] = True # Reject session (insufficient institutional volume)
                return None
            state['phase'] = 'WAIT_BREAK'
            state['h_orb'] = row['high']
            state['l_orb'] = row['low']
            state['atr'] = row['atr']
            state['ema'] = row['ema200']
            return None
            
        # 2. Wait for Breakout of the ORB bar
        if state['phase'] == 'WAIT_BREAK':
            horb = state['h_orb']
            lorb = state['l_orb']
            atr = state['atr']
            ema = state['ema']
            sl_dist = sl_atr * atr
            tp_dist = sl_dist * rr
            
            # Long Breakout: Above H_orb & Above EMA200
            if row['high'] >= horb + (0.1 * atr) and horb >= ema:
                state['phase'] = 'LIMIT_WAIT'
                state['dir'] = 'BUY'
                state['lp'] = horb # Retest limit at opening range high
                state['sl'] = horb - sl_dist
                state['tp'] = horb + tp_dist
            # Short Breakout: Below L_orb & Below EMA200
            elif row['low'] <= lorb - (0.1 * atr) and lorb <= ema:
                state['phase'] = 'LIMIT_WAIT'
                state['dir'] = 'SELL'
                state['lp'] = lorb # Retest limit at opening range low
                state['sl'] = lorb + sl_dist
                state['tp'] = lorb - tp_dist
            return None
            
        # 3. Wait for Retest Fill (Maker Limit Order)
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
        print("MT5 init failed")
        return
        
    assets = [
        {'sym': 'NAS100.r', 'name': 'Nasdaq 100 (US Index)', 'open_h': 19, 'end_h': 23, 'spread': 1.2, 'comm': 4.0},
        {'sym': 'XAUUSD', 'name': 'Gold (Commodity)', 'open_h': 12, 'end_h': 16, 'spread': 0.30, 'comm': 6.0},
        {'sym': 'EURUSD', 'name': 'Euro (FX Major)', 'open_h': 12, 'end_h': 16, 'spread': 0.00014, 'comm': 6.0},
    ]
    
    print("\n" + "=" * 135)
    print("SSRN PAPER #4729284: 5-MIN ORB WITH RELATIVE VOLUME (RVOL) BENCHMARK")
    print("Evaluated strictly under Live Market Simulator (Vantage Spreads, Commission, Slippage, 2.5% Compounding Risk)")
    print("=" * 135)
    print(f"{'ASSET NAME':<24} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 135)
    
    for a in assets:
        sym = a['sym']
        sessions = load_data_with_volume(sym, a['open_h'], a['end_h'])
        if not sessions:
            continue
            
        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.025,
            spread_override=a['spread'],
            commission_per_lot=a['comm'],
            daily_loss_limit_pct=2.5,
            compounding=True
        )
        
        sig_fn = make_ssrn_orb_sig(
            open_h=a['open_h'],
            open_m=0,
            rvol_min=1.3,
            sl_atr=1.5,
            rr=2.5,
            cutoff_h=a['end_h'] - 1
        )
        rep = sim.run_bars(sessions, sig_fn)
        
        print(f"{a['name']:<24} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")
        
    mt5.shutdown()
    print("=" * 135)

if __name__ == "__main__":
    main()

