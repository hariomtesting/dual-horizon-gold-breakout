"""
DETAILED ANALYSIS OF GER40 (DAX) INSTITUTIONAL LIQUIDITY SWEEP (11:00 AM - 4:00 PM IST)
========================================================================================
Let's test GER40.r standalone across 2021-2026:
- Every year
- Every month
- Daily max drawdown
- Prop firm compliance
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def evaluate_ger40_scalper():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "GER40.r"
    mt5.symbol_select(symbol, True)
    
    dt_from = datetime.datetime(2021, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
    
    day_sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            day_sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
            
    risk_per_trade = 0.0125
    trades = []
    daily_results = []
    
    for curr_d, bars in day_sessions.items():
        pre_bars = bars[0:18] # 11:00 to 12:30 IST
        pre_high = max(b['high'] for b in pre_bars)
        pre_low = min(b['low'] for b in pre_bars)
        
        pos = None
        trades_today = 0
        day_pnl = 0.0
        
        for i in range(18, len(bars)):
            r = bars[i]
            h, l, c, o = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            ist_h, ist_m = r['ist_hour'], r['ist_minute']
            
            # Auto-square-off at 15:55 IST
            if ist_h == 15 and ist_m >= 55:
                if pos is not None:
                    pnl_pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                    pnl_pct = (pnl_pts / pos['sl_dist']) * risk_per_trade
                    pos['pnl_pct'] = pnl_pct
                    day_pnl += pnl_pct
                    trades.append(pos)
                    trades_today += 1
                    pos = None
                break
                
            if pos is not None:
                if pos['type'] == 'BUY':
                    if l <= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif h >= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                elif pos['type'] == 'SELL':
                    if h >= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif l <= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        
            # Entry logic
            if pos is None and trades_today == 0 and 12 <= ist_h < 15:
                sl_dist = 1.0 * atr
                tp_dist = 1.8 * atr
                rr = tp_dist / sl_dist
                
                if h > pre_high and c < pre_high and c < o and delta < 0:
                    pos = {
                        'symbol': symbol, 'type': 'SELL', 'date': curr_d,
                        'entry': c, 'sl': h + 0.10, 'tp': c - tp_dist,
                        'sl_dist': max((h + 0.10) - c, 0.5 * atr), 'rr': rr,
                        'entry_time': r['ist_dt']
                    }
                elif l < pre_low and c > pre_low and c > o and delta > 0:
                    pos = {
                        'symbol': symbol, 'type': 'BUY', 'date': curr_d,
                        'entry': c, 'sl': l - 0.10, 'tp': c + tp_dist,
                        'sl_dist': max(c - (l - 0.10), 0.5 * atr), 'rr': rr,
                        'entry_time': r['ist_dt']
                    }
                    
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_t = pd.DataFrame(trades)
    df_d = pd.DataFrame(daily_results)
    
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_t['month'] = pd.to_datetime(df_t['date']).dt.to_period('M')
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    
    print("=" * 85)
    print("   GER40 (DAX) LIQUIDITY SWEEP SCALPER (11:00 AM - 4:00 PM IST) PERFORMANCE   ")
    print("=" * 85)
    print(f"Total Completed Trades: {len(df_t):,} across {len(df_d):,} Trading Days")
    print(f"Overall Scalper Win Rate: {(df_t['pnl_pct'] > 0).mean()*100:.2f}%\n")
    
    years = sorted(df_d['year'].unique())
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 85)
    
    rois = []
    for y in years:
        y_d = df_d[df_d['year'] == y]
        y_t = df_t[df_t['year'] == y]
        if len(y_t) == 0:
            continue
        y_wr = (y_t['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, row in y_d.iterrows():
            cap += cap * row['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        rois.append(roi)
        
        m_rois = [m_sub['pnl_pct'].sum() * 100.0 for m, m_sub in y_d.groupby('month')]
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(y_t):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(rois):+6.2f}% (Median: {np.median(rois):+6.2f}%)")
    print(f"PROFITABLE YEARS      : {(np.array(rois) > 0).mean()*100:.1f}%\n")
    
    # Print 2026 Monthly Breakdown
    print("=" * 85)
    print("                     2026 IN-DEPTH MONTHLY BREAKDOWN                     ")
    print("=" * 85)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Worst Day':<12} | {'Max DD':<10} | {'Status'}")
    print("-" * 85)
    
    y_2026_d = df_d[df_d['year'] == 2026]
    y_2026_t = df_t[df_t['year'] == 2026]
    
    for m, m_sub in y_2026_d.groupby('month'):
        t_sub = y_2026_t[y_2026_t['month'] == m]
        m_trades = len(t_sub)
        if m_trades == 0:
            continue
        m_wr = (t_sub['pnl_pct'] > 0).mean() * 100.0
        m_ret = m_sub['pnl_pct'].sum() * 100.0
        worst_d = m_sub['pnl_pct'].min() * 100.0
        
        m_peak = 10000.0
        m_cap = 10000.0
        m_max_dd = 0.0
        for _, r in m_sub.iterrows():
            m_cap += m_cap * r['pnl_pct']
            if m_cap > m_peak:
                m_peak = m_cap
            dd = (m_peak - m_cap) / m_peak * 100.0
            if dd > m_max_dd:
                m_max_dd = dd
                
        status = "[GREEN]" if m_ret > 0 else "[RED]"
        print(f"{str(m):<10} | {m_trades:<7} | {m_wr:6.1f}%  | {m_ret:+9.2f}%    | {worst_d:+9.2f}%   | {m_max_dd:7.2f}%   | {status}")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    evaluate_ger40_scalper()

