"""
MATHEMATICAL VERIFICATION: DAILY ATR DISPLACEMENT & NORMALIZATION
================================================================
Compares:
1. Flawed M5 ATR normalization (Stop loss $3 on an $80 daily range)
2. Proper Daily ATR normalization (Real auction displacement & structural stops)
Across 2018 - 2026.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def test_daily_normalization():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85)
    print("      TESTING DAILY ATR NORMALIZATION ON GOLD (2018 - 2026)      ")
    print("=" * 85 + "\n")
    
    # 1. Fetch Daily ATRs
    d1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 3000)
    df_d1 = pd.DataFrame(d1_rates)
    df_d1['date'] = pd.to_datetime(df_d1['time'], unit='s').dt.date
    tr_d1 = np.maximum(df_d1['high'] - df_d1['low'], np.maximum((df_d1['high'] - df_d1['close'].shift()).abs(), (df_d1['low'] - df_d1['close'].shift()).abs()))
    df_d1['daily_atr'] = tr_d1.rolling(14, min_periods=5).mean().bfill()
    daily_atr_map = dict(zip(df_d1['date'], df_d1['daily_atr']))
    
    # 2. Fetch M5 bars
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    df['daily_atr'] = df['date'].map(daily_atr_map).bfill()
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    # Precompute VPOCs and session records
    daily_vpocs = {}
    day_sessions = {}
    for d, sub in df.groupby('date'):
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            session_sub = sub[(sub['hour'] >= 7) & (sub['hour'] <= 18)]
            if len(session_sub) >= 15:
                day_sessions[d] = session_sub[['datetime', 'open', 'high', 'low', 'close', 'delta']].to_dict('records')
            
    print(f"Loaded {len(daily_vpocs):,} daily VPOCs with true Daily ATR.\n", flush=True)
    
    # Test True Daily ATR Normalization:
    # Displacement: 0.25 to 1.20 x Daily ATR
    # Stop Loss: 0.35 x Daily ATR
    # Take Profit: Reversion back to VPOC
    
    risk_per_trade = 0.015 # 1.5% fixed risk
    trades = []
    
    days = sorted(list(daily_vpocs.keys()))
    
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        
        if prev_d not in daily_vpocs or curr_d not in day_sessions:
            continue
            
        prev_poc = daily_vpocs[prev_d]
        d_atr = daily_atr_map.get(prev_d, 20.0)
        session_sub = day_sessions[curr_d]
            
        pos = None
        trades_today = 0
        
        for i in range(len(session_sub)):
            r = session_sub[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            delta = r['delta']
            
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
                        
            if pos is None and trades_today == 0 and i >= 3:
                dist_to_poc = abs(close_p - prev_poc)
                
                # Real displacement relative to DAILY ATR
                if 0.20 * d_atr <= dist_to_poc <= 1.20 * d_atr:
                    sl_dist = 0.30 * d_atr
                    tp_dist = 0.55 * dist_to_poc
                    rr = tp_dist / sl_dist
                    
                    if close_p < prev_poc and delta > 0 and close_p > open_p:
                        pos = {
                            'type': 'BUY', 'date': curr_d,
                            'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'rr': rr,
                            'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                        }
                    elif close_p > prev_poc and delta < 0 and close_p < open_p:
                        pos = {
                            'type': 'SELL', 'date': curr_d,
                            'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'rr': rr,
                            'year': r['datetime'].year, 'month': r['datetime'].to_period('M')
                        }
                        
    df_t = pd.DataFrame(trades)
    print(f"Total Completed Trades: {len(df_t)}")
    if len(df_t) == 0:
        return
        
    tot_wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print(f"Overall Win Rate: {tot_wr:.2f}%\n")
    
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
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean() * 100.0:.1f}%\n")


if __name__ == "__main__":
    test_daily_normalization()
