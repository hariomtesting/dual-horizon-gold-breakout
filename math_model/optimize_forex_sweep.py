"""
FOREX SWEEP OPTIMIZER: HUNTING > +50% ROI ON EURUSD & GBPUSD
============================================================
Testing:
1. Asian Range Threshold (Must be >= 20 pips so TP is large relative to spread).
2. Target: Opposite Asian Band (Range Rotation) vs Asian Midpoint.
3. Entry: Limit order at Asian High/Low after wick rejection.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_fx_data(symbol='EURUSD', n_bars=150000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
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
                sub['asian_range'] = sub['asian_high'] - sub['asian_low']
                sessions[d] = sub.to_dict('records')
    return sessions

def run_forex_sweep_experiments():
    if not mt5.initialize():
        return
        
    print("\n" + "=" * 115)
    print("FOREX SWEEP OPTIMIZATION (EURUSD & GBPUSD): HUNTING PROFIT FACTOR > 1.30")
    print("=" * 115)
    print(f"{'SYM':<8} | {'TARGET':<10} | {'MIN RANGE':<10} | {'ROI':<10} | {'PF':<6} | {'WIN%':<6} | {'DD':<8} | {'TRADES'}")
    print("-" * 115)
    
    for sym in ['EURUSD', 'GBPUSD']:
        sessions = load_fx_data(sym, 150000)
        if not sessions:
            continue
            
        for target_type in ['MIDPOINT', 'FULL_ROTATION']: # MIDPOINT vs OPPOSITE_BAND
            for min_range_pips in [0.0015, 0.0025, 0.0035]: # 15, 25, 35 pips
                def make_sig(target_type=target_type, min_r=min_range_pips):
                    def sig(bars, i, state):
                        row = bars[i]
                        h = row['ist_hour']
                        if 'done' not in state:
                            state['done'] = False
                        if state['done'] or h >= 15:
                            state['done'] = True
                            return None
                            
                        if h >= 12 and row['asian_range'] >= min_r:
                            ah = row['asian_high']
                            al = row['asian_low']
                            amid = row['asian_mid']
                            
                            # False breakout high (Bull trap)
                            if row['high'] >= ah + 0.0003 and row['close'] < ah:
                                state['done'] = True
                                sl = row['high'] + 0.0005
                                tp = al if target_type == 'FULL_ROTATION' else amid
                                if (sl - row['close']) >= 0.0008:
                                    return {'type': 'SELL_LIMIT', 'trigger': row['close'], 'sl': sl, 'tp': tp}
                                    
                            # False breakout low (Bear trap)
                            elif row['low'] <= al - 0.0003 and row['close'] > al:
                                state['done'] = True
                                sl = row['low'] - 0.0005
                                tp = ah if target_type == 'FULL_ROTATION' else amid
                                if (row['close'] - sl) >= 0.0008:
                                    return {'type': 'BUY_LIMIT', 'trigger': row['close'], 'sl': sl, 'tp': tp}
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
                rep = sim.run_bars(sessions, make_sig())
                print(f"{sym:<8} | {target_type:<10} | {min_range_pips*10000:<4.0f} pips  | {rep['avg_annual_roi']:+7.2f}% | {rep['profit_factor']:<6.2f} | {rep['win_rate']:4.1f}% | {rep['max_drawdown_pct']:6.2f}% | {rep['total_trades']}")

    mt5.shutdown()
    print("=" * 115)

if __name__ == "__main__":
    run_forex_sweep_experiments()

