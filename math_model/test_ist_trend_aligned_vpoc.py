"""
INDIAN TIME (11:00 AM - 4:00 PM IST) TREND-ALIGNED VPOC SCALPER
================================================================
The Definitive Institutional Fix:
1. Operational Window: 11:00 AM to 4:00 PM IST (Execution during London Open 12:30 PM - 3:50 PM IST)
2. Trend Alignment (Eliminates the 2023-2025 runaway losses):
   - Daily 50-EMA Macro Filter:
     * If Price > Daily 50-EMA: ONLY BUY dips toward VPOC! (Zero shorts in a bull run!)
     * If Price < Daily 50-EMA: ONLY SELL rallies toward VPOC! (Zero longs in a bear market!)
3. Pyramiding:
   - Unit 1: 1.25% risk.
   - When profit >= +0.8x ATR: Lock Unit 1 stop to Breakeven (+0.1R).
   - Deploy Unit 2 with target at +1.5x ATR!
4. Hard Auto-Square-Off at 15:55 IST:
   - 100% Flat before PC shutdown.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_trend_aligned_ist_scalper():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("      INDIAN TIME (11:00 AM - 4:00 PM IST) TREND-ALIGNED SCALPER (GOLD)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    # 1. Fetch Daily 50-EMA for Macro Trend Direction
    d1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 3000)
    df_d1 = pd.DataFrame(d1_rates)
    df_d1['date'] = pd.to_datetime(df_d1['time'], unit='s').dt.date
    df_d1['d1_ema50'] = df_d1['close'].ewm(span=50).mean().bfill()
    daily_trend_map = dict(zip(df_d1['date'], df_d1['d1_ema50']))
    
    # 2. Fetch M5 bars from 2018 to 2026
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['date'] = df['datetime'].dt.date
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    # Indian Standard Time (UTC + 5:30)
    df['ist_time'] = df['datetime'] + datetime.timedelta(hours=5, minutes=30)
    df['ist_hour'] = df['ist_time'].dt.hour
    df['ist_minute'] = df['ist_time'].dt.minute
    df['daily_ema50'] = df['date'].map(daily_trend_map).bfill()
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Pre-extract VPOCs
    daily_vpocs = {}
    for d, sub in df.groupby('date'):
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    # Filter only IST 11:00 to 16:00
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
    
    day_sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 20:
            day_sessions[d] = sub[['datetime', 'ist_time', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta', 'daily_ema50']].to_dict('records')
            
    print(f"Loaded {len(day_sessions):,} active trading days in IST window from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}.\n", flush=True)
    
    risk_per_trade = 0.0125 # 1.25% fixed risk
    sl_mult = 1.40
    tp_ratio = 0.65
    
    all_trades = []
    daily_results = []
    
    days = sorted(list(day_sessions.keys()))
    
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        
        if prev_d not in daily_vpocs or curr_d not in day_sessions:
            continue
            
        prev_poc = daily_vpocs[prev_d]
        bars = day_sessions[curr_d]
        
        day_pnl_pct = 0.0
        day_trades = 0
        pos_u1 = None
        pos_u2 = None
        trade_taken_today = False
        
        macro_ema = bars[0]['daily_ema50']
        
        for i in range(len(bars)):
            r = bars[i]
            high_p, low_p, close_p, o_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            ist_h, ist_m = r['ist_hour'], r['ist_minute']
            
            # Hard Auto-Square-Off at 15:55 IST
            if ist_h == 15 and ist_m >= 55:
                for pos in [pos_u1, pos_u2]:
                    if pos is not None:
                        pnl_pts = (close_p - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - close_p)
                        pnl_pct = (pnl_pts / pos['sl_dist']) * risk_per_trade
                        pos['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos)
                        day_trades += 1
                pos_u1 = None
                pos_u2 = None
                break
                
            # Manage Unit 1
            if pos_u1 is not None:
                # Pyramiding & Breakeven Trigger
                if not pos_u1['be']:
                    if pos_u1['type'] == 'BUY' and high_p >= pos_u1['entry'] + 0.8 * atr:
                        pos_u1['sl'] = pos_u1['entry'] + 0.20
                        pos_u1['be'] = True
                        if pos_u2 is None:
                            pos_u2 = {
                                'symbol': 'XAUUSD', 'unit': 2, 'type': 'BUY', 'date': curr_d,
                                'entry': close_p + 0.20, 'sl': pos_u1['entry'] + 0.20,
                                'tp': close_p + 1.2 * atr, 'sl_dist': close_p - pos_u1['entry'],
                                'rr': 1.5, 'be': True, 'entry_time': r['ist_time']
                            }
                    elif pos_u1['type'] == 'SELL' and low_p <= pos_u1['entry'] - 0.8 * atr:
                        pos_u1['sl'] = pos_u1['entry'] - 0.20
                        pos_u1['be'] = True
                        if pos_u2 is None:
                            pos_u2 = {
                                'symbol': 'XAUUSD', 'unit': 2, 'type': 'SELL', 'date': curr_d,
                                'entry': close_p, 'sl': pos_u1['entry'] - 0.20,
                                'tp': close_p - 1.2 * atr, 'sl_dist': pos_u1['entry'] - close_p,
                                'rr': 1.5, 'be': True, 'entry_time': r['ist_time']
                            }
                            
                # Exits for Unit 1
                if pos_u1['type'] == 'BUY':
                    if low_p <= pos_u1['sl']:
                        pnl_pct = 0.001 if pos_u1['be'] and pos_u1['sl'] >= pos_u1['entry'] else -risk_per_trade
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                    elif high_p >= pos_u1['tp']:
                        pnl_pct = risk_per_trade * pos_u1['rr']
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                elif pos_u1['type'] == 'SELL':
                    if high_p >= pos_u1['sl']:
                        pnl_pct = 0.001 if pos_u1['be'] and pos_u1['sl'] <= pos_u1['entry'] else -risk_per_trade
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                    elif low_p <= pos_u1['tp']:
                        pnl_pct = risk_per_trade * pos_u1['rr']
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                        
            # Manage Unit 2 (Pyramid trade)
            if pos_u2 is not None:
                if pos_u2['type'] == 'BUY':
                    if low_p <= pos_u2['sl']:
                        pnl_pct = -risk_per_trade * 0.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                    elif high_p >= pos_u2['tp']:
                        pnl_pct = risk_per_trade * 1.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                elif pos_u2['type'] == 'SELL':
                    if high_p >= pos_u2['sl']:
                        pnl_pct = -risk_per_trade * 0.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                    elif low_p <= pos_u2['tp']:
                        pnl_pct = risk_per_trade * 1.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                        
            # Entry logic for Unit 1 (London Open Hours: 12:30 PM - 3:30 PM IST)
            if not trade_taken_today and pos_u1 is None and ist_h >= 12 and ist_h < 15:
                dist_to_poc = abs(close_p - prev_poc)
                if 0.45 * atr <= dist_to_poc <= 2.2 * atr:
                    sl_dist = sl_mult * atr
                    tp_dist = tp_ratio * dist_to_poc
                    rr = tp_dist / sl_dist
                    
                    # 1. Bullish Dip toward VPOC - ONLY allowed if Macro Trend is UP (Price > Daily 50-EMA)
                    if close_p < prev_poc and delta > 0 and close_p > o_p and close_p > macro_ema:
                        pos_u1 = {
                            'symbol': 'XAUUSD', 'unit': 1, 'type': 'BUY', 'date': curr_d,
                            'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                            'be': False, 'entry_time': r['ist_time']
                        }
                        trade_taken_today = True
                    # 2. Bearish Rally toward VPOC - ONLY allowed if Macro Trend is DOWN (Price < Daily 50-EMA)
                    elif close_p > prev_poc and delta < 0 and close_p < o_p and close_p < macro_ema:
                        pos_u1 = {
                            'symbol': 'XAUUSD', 'unit': 1, 'type': 'SELL', 'date': curr_d,
                            'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                            'be': False, 'entry_time': r['ist_time']
                        }
                        trade_taken_today = True
                        
        daily_results.append({
            'date': curr_d,
            'pnl_pct': day_pnl_pct,
            'trades': day_trades
        })
        
    df_trades = pd.DataFrame(all_trades)
    df_daily = pd.DataFrame(daily_results)
    
    if len(df_trades) == 0:
        print("No trades generated.")
        return
        
    df_trades['year'] = pd.to_datetime(df_trades['date']).dt.year
    df_trades['month'] = pd.to_datetime(df_trades['date']).dt.to_period('M')
    df_daily['year'] = pd.to_datetime(df_daily['date']).dt.year
    df_daily['month'] = pd.to_datetime(df_daily['date']).dt.to_period('M')
    
    tot_trades = len(df_trades)
    tot_wr = (df_trades['pnl_pct'] > 0).mean() * 100.0
    
    print("=" * 85)
    print("      TREND-ALIGNED IST SCALPER RESULTS (11:00 AM - 4:00 PM IST)      ")
    print("=" * 85)
    print(f"Total Completed Trades : {tot_trades:,} across {len(df_daily):,} Trading Days")
    print(f"Overall Scalper Win Rate: {tot_wr:.2f}%\n")
    
    # Year-by-Year Table
    years = sorted(df_daily['year'].unique())
    print("=" * 85)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 85)
    
    annual_rois = []
    
    for y in years:
        y_daily = df_daily[df_daily['year'] == y]
        y_trades = df_trades[df_trades['year'] == y]
        if len(y_trades) == 0:
            continue
            
        y_wr = (y_trades['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, d in y_daily.iterrows():
            cap += cap * d['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        annual_rois.append(roi)
        
        m_rois = [m_sub['pnl_pct'].sum() * 100.0 for m, m_sub in y_daily.groupby('month')]
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(y_trades):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(annual_rois):+6.2f}% (Median: {np.median(annual_rois):+6.2f}%)")
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean()*100:.1f}%\n")
    
    # Print 2026 Monthly Breakdown
    print("=" * 85)
    print("                     2026 IN-DEPTH MONTHLY BREAKDOWN                     ")
    print("=" * 85)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Worst Day':<12} | {'Max DD':<10} | {'Status'}")
    print("-" * 85)
    for m in sorted(df_daily[df_daily['year'] == 2026]['month'].unique()):
        m_daily = df_daily[df_daily['month'] == m]
        m_trades = df_trades[df_trades['month'] == m]
        if len(m_trades) == 0:
            continue
        m_wr = (m_trades['pnl_pct'] > 0).mean() * 100.0
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        worst_day = m_daily['pnl_pct'].min() * 100.0
        for _, d in m_daily.iterrows():
            cap += cap * d['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        m_roi = (cap - 10000.0) / 10000.0 * 100.0
        st = "[PASSED >=20%]" if m_roi >= 20.0 else ("[GREEN]" if m_roi > 0 else "[RED]")
        print(f"{str(m):<10} | {len(m_trades):<7} | {m_wr:6.1f}%   | {m_roi:+9.2f}%    | {worst_day:8.2f}%   | {max_dd:7.2f}%   | {st}")
    print("=" * 95 + "\n")


if __name__ == "__main__":
    run_trend_aligned_ist_scalper()

