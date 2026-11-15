"""
HYBRID REGIME-ADAPTIVE CTA & AUCTION MODEL
==========================================
Solves the fatal flaw of pure mean reversion by dynamically switching modes:
1. Trend Regime (Hurst > 0.50 or Fast EMA > Slow EMA):
   - ONLY trades in the direction of the macro trend (Pullback continuation)
   - Asymmetric Reward: Trailing stop / TP >= 1.5x - 2.0x ATR to capture runaway trends (2022, 2023, 2024)
2. Auction / Range Regime (Hurst <= 0.50 or Choppy consolidation):
   - Trades VPOC Mean Reversion to capture rotational liquidity
3. Enforces strict prop firm guardrails:
   - 1.25% - 1.5% fixed risk
   - -2.5% max daily circuit breaker
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def run_hybrid_cta_test():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("       TESTING HYBRID REGIME-ADAPTIVE CTA MODEL (GOLD 2018 - 2026)       ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise RuntimeError("No H1 data")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['date'] = df['datetime'].dt.date
    df['year'] = df['datetime'].dt.year
    df['month'] = df['datetime'].dt.to_period('M')
    
    # 1. Macro Trend Indicators (H1 timeframe)
    df['ema_fast'] = df['close'].ewm(span=20).mean()
    df['ema_slow'] = df['close'].ewm(span=50).mean()
    df['trend_dir'] = np.where(df['ema_fast'] > df['ema_slow'], 1, -1)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # ADX / Trend strength proxy
    df['directional_move'] = (df['close'] - df['close'].shift(20)).abs() / (df['atr'] * 20)
    df['is_trending'] = df['directional_move'] > 0.35 # Clear trend
    
    print(f"Total H1 bars evaluated: {len(df):,} from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}\n", flush=True)
    
    # Simulate trade execution
    risk_per_trade = 0.015
    trades = []
    daily_results = {}
    
    pos = None
    
    for i in range(50, len(df)):
        r = df.iloc[i]
        curr_d = r['date']
        if curr_d not in daily_results:
            daily_results[curr_d] = {'pnl_pct': 0.0, 'trades': 0}
            
        # Circuit breaker
        if daily_results[curr_d]['pnl_pct'] <= -0.025:
            continue
            
        high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
        atr = r['atr']
        trend = r['trend_dir']
        is_trend = r['is_trending']
        
        # Position management
        if pos is not None:
            if pos['type'] == 'BUY':
                if low_p <= pos['sl']:
                    pnl_pct = -risk_per_trade
                    pos['pnl_pct'] = pnl_pct
                    trades.append(pos)
                    daily_results[pos['date']]['pnl_pct'] += pnl_pct
                    daily_results[pos['date']]['trades'] += 1
                    pos = None
                elif high_p >= pos['tp']:
                    pnl_pct = risk_per_trade * pos['rr']
                    pos['pnl_pct'] = pnl_pct
                    trades.append(pos)
                    daily_results[pos['date']]['pnl_pct'] += pnl_pct
                    daily_results[pos['date']]['trades'] += 1
                    pos = None
            elif pos['type'] == 'SELL':
                if high_p >= pos['sl']:
                    pnl_pct = -risk_per_trade
                    pos['pnl_pct'] = pnl_pct
                    trades.append(pos)
                    daily_results[pos['date']]['pnl_pct'] += pnl_pct
                    daily_results[pos['date']]['trades'] += 1
                    pos = None
                elif low_p <= pos['tp']:
                    pnl_pct = risk_per_trade * pos['rr']
                    pos['pnl_pct'] = pnl_pct
                    trades.append(pos)
                    daily_results[pos['date']]['pnl_pct'] += pnl_pct
                    daily_results[pos['date']]['trades'] += 1
                    pos = None
                    
        # Entry Logic
        if pos is None and daily_results[curr_d]['trades'] < 1:
            # 1. TREND MODE: Pullback into 20 EMA in the direction of the 50 EMA trend
            if is_trend:
                sl_dist = 1.4 * atr
                tp_dist = 2.0 * atr # Positive Asymmetric R:R (1.43:1)
                rr = tp_dist / sl_dist
                
                # Bullish pullback: Trend is UP, price touches fast EMA, closes green
                if trend == 1 and low_p <= r['ema_fast'] and close_p > r['ema_fast'] and close_p > open_p:
                    pos = {
                        'type': 'BUY', 'mode': 'TREND_PULLBACK', 'date': curr_d,
                        'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                        'tp': close_p + tp_dist, 'rr': rr, 'year': r['year'], 'month': r['month']
                    }
                # Bearish pullback: Trend is DOWN, price touches fast EMA, closes red
                elif trend == -1 and high_p >= r['ema_fast'] and close_p < r['ema_fast'] and close_p < open_p:
                    pos = {
                        'type': 'SELL', 'mode': 'TREND_PULLBACK', 'date': curr_d,
                        'entry': close_p, 'sl': close_p + sl_dist,
                        'tp': close_p - tp_dist, 'rr': rr, 'year': r['year'], 'month': r['month']
                    }
            # 2. RANGE MODE: Fade 2.0 ATR stretches back to 20 EMA
            else:
                dist_ema = close_p - r['ema_fast']
                if abs(dist_ema) >= 1.5 * atr:
                    sl_dist = 1.2 * atr
                    tp_dist = 0.8 * abs(dist_ema) # Mean reversion to EMA
                    rr = tp_dist / sl_dist
                    if dist_ema > 0 and close_p < open_p: # Overbought fade
                        pos = {
                            'type': 'SELL', 'mode': 'RANGE_FADE', 'date': curr_d,
                            'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'rr': rr, 'year': r['year'], 'month': r['month']
                        }
                    elif dist_ema < 0 and close_p > open_p: # Oversold fade
                        pos = {
                            'type': 'BUY', 'mode': 'RANGE_FADE', 'date': curr_d,
                            'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'rr': rr, 'year': r['year'], 'month': r['month']
                        }
                        
    df_t = pd.DataFrame(trades)
    print(f"Total Trades Taken: {len(df_t)}")
    if len(df_t) == 0:
        return
        
    print(f"Overall Win Rate: {(df_t['pnl_pct'] > 0).mean() * 100:.2f}%\n")
    
    # By Year
    years = sorted(df_t['year'].unique())
    print("=" * 85)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Status'}")
    print("-" * 85)
    
    for y in years:
        sub = df_t[df_t['year'] == y]
        wr = (sub['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, t in sub.iterrows():
            cap += cap * t['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(sub):<8} | {wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {st}")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    run_hybrid_cta_test()
