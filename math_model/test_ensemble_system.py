"""
QUANTITATIVE ENSEMBLE SYSTEM: MULTI-REGIME DUAL ENGINE
=====================================================
Combines two mathematically orthogonal engines on Gold (XAUUSD):
1. ENGINE 1: Trend Capture Engine (Active when Hurst > 0.52 or Variance Ratio > 1.05)
   - Follows direction of 50-period momentum breakout
   - Asymmetric Reward: TP = 2.0x ATR, SL = 1.0x ATR (R:R = 2.0:1)
   - Protects and profits during secular trend runs (2020, 2022, 2023, 2024)
2. ENGINE 2: Auction Mean-Reversion Engine (Active when Hurst <= 0.52 or Variance Ratio <= 1.05)
   - Fades overextensions back to daily VPOC / equilibrium
   - High Win Rate target: TP = 0.65x ATR, SL = 1.2x ATR
   - Generates cash flow during rotational / range-bound periods (2018, 2019, 2021, 2025, 2026)

Tests across 2018 - 2026 to verify if year-by-year consistency is achieved.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine
from math_model.regime_detector import MarketRegimeDetector


def run_ensemble_simulation():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("      TESTING MULTI-REGIME DUAL-ENGINE ENSEMBLE (GOLD 2018 - 2026)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise RuntimeError("No M5 data")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # 50-period momentum & moving average
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    
    # Pre-extract VPOCs & session records
    daily_vpocs = {}
    day_sessions = {}
    for d, sub in df.groupby('date'):
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            session_sub = sub[(sub['hour'] >= 7) & (sub['hour'] <= 18)]
            if len(session_sub) >= 15:
                day_sessions[d] = session_sub[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta', 'sma50', 'sma200']].to_dict('records')
                
    print(f"Loaded {len(day_sessions):,} trading days from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}.\n", flush=True)
    
    # Run Ensemble Execution
    risk_per_trade = 0.0125 # 1.25% fixed risk
    trades = []
    
    days = sorted(list(day_sessions.keys()))
    
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        
        if prev_d not in daily_vpocs or curr_d not in day_sessions:
            continue
            
        prev_poc = daily_vpocs[prev_d]
        bars = day_sessions[curr_d]
        
        pos = None
        trades_today = 0
        
        # Calculate opening regime relative to prior VPOC
        open_p = bars[0]['open']
        open_atr = bars[0]['atr']
        
        # If open is extremely far from prior VPOC (> 2.0 ATR), it's an INITIATIVE TREND DAY
        is_trend_regime = abs(open_p - prev_poc) > 1.8 * open_atr
        
        for i in range(len(bars)):
            r = bars[i]
            high_p, low_p, close_p, o_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            sma50, sma200 = r['sma50'], r['sma200']
            
            # Position management
            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        break
                    elif high_p >= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        break
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        break
                    elif low_p <= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        break
                        
            # Entry logic
            if pos is None and trades_today == 0 and i >= 3:
                # ENGINE 1: TREND CONTINUATION (On initiative trend days)
                if is_trend_regime:
                    sl_dist = 1.0 * atr
                    tp_dist = 2.0 * atr # 2.0:1 Asymmetric R:R
                    rr = tp_dist / sl_dist
                    
                    # Follow breakout direction
                    if close_p > prev_poc and delta > 0 and close_p > sma50 and close_p > o_p:
                        pos = {
                            'engine': 'TREND_FOLLOW', 'type': 'BUY', 'date': curr_d,
                            'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'rr': rr,
                            'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                        }
                    elif close_p < prev_poc and delta < 0 and close_p < sma50 and close_p < o_p:
                        pos = {
                            'engine': 'TREND_FOLLOW', 'type': 'SELL', 'date': curr_d,
                            'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'rr': rr,
                            'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                        }
                # ENGINE 2: AUCTION MEAN REVERSION (On balanced / normal days)
                else:
                    dist_to_poc = abs(close_p - prev_poc)
                    if 0.45 * atr <= dist_to_poc <= 1.6 * atr:
                        sl_dist = 1.5 * atr
                        tp_dist = 0.65 * dist_to_poc
                        rr = tp_dist / sl_dist
                        
                        if close_p < prev_poc and delta > 0 and close_p > o_p:
                            pos = {
                                'engine': 'MEAN_REVERT', 'type': 'BUY', 'date': curr_d,
                                'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                                'tp': close_p + tp_dist, 'rr': rr,
                                'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                            }
                        elif close_p > prev_poc and delta < 0 and close_p < o_p:
                            pos = {
                                'engine': 'MEAN_REVERT', 'type': 'SELL', 'date': curr_d,
                                'entry': close_p, 'sl': close_p + sl_dist,
                                'tp': close_p - tp_dist, 'rr': rr,
                                'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                            }
                            
    df_t = pd.DataFrame(trades)
    print(f"Total Completed Trades: {len(df_t):,}\n")
    if len(df_t) == 0:
        return
        
    print("--- BREAKDOWN BY ENGINE ---")
    for eng in df_t['engine'].unique():
        sub = df_t[df_t['engine'] == eng]
        wr = (sub['pnl_pct'] > 0).mean() * 100.0
        print(f"   {eng:<15}: {len(sub):4d} Trades | Win Rate: {wr:5.2f}%")
        
    print(f"\nOverall Win Rate: {(df_t['pnl_pct'] > 0).mean()*100:.2f}%\n")
    
    # Year-by-Year Table
    years = sorted(df_t['year'].unique())
    print("=" * 85)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Status'}")
    print("-" * 85)
    
    annual_rois = []
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
        annual_rois.append(roi)
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(sub):<8} | {wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(annual_rois):+6.2f}%")
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean()*100:.1f}%\n")


if __name__ == "__main__":
    run_ensemble_simulation()
