"""
CHAMPION ATR STRADDLE (0.35x Entry, 0.8x SL, 1.6x TP) - FULL YEAR & MONTH WALK
==============================================================================
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def run_champion_atr_straddle():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_sessions = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
        asset_sessions[s] = sessions
        
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_sessions.values()])))
    
    entry_offset = 0.35 # Tight early entry
    sl_mult = 0.80
    tp_mult = 1.60 # 2.0:1 R:R
    rr = tp_mult / sl_mult
    risk_per_trade = 0.010 # 1.0%
    
    portfolio_trades = []
    portfolio_daily = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for s in symbols:
            if curr_d not in asset_sessions[s]:
                continue
            bars = asset_sessions[s][curr_d]
            if len(bars) < 25:
                continue
                
            base_bar = bars[18] # 12:30 PM IST
            p_base = base_bar['close']
            atr_base = base_bar['atr']
            
            buy_trigger = p_base + entry_offset * atr_base
            sell_trigger = p_base - entry_offset * atr_base
            
            pos = None
            
            for i in range(19, len(bars)):
                r = bars[i]
                h, l, c = r['high'], r['low'], r['close']
                
                # Auto square off at 15:55 IST
                if r['ist_hour'] == 15 and r['ist_minute'] >= 55:
                    if pos is not None:
                        pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                        pnl = (pts / pos['sl_dist']) * risk_per_trade
                        day_pnl += pnl
                        pos['pnl_pct'] = pnl
                        portfolio_trades.append(pos)
                        day_trades += 1
                        pos = None
                    break
                    
                if pos is not None:
                    if pos['type'] == 'BUY':
                        if l <= pos['sl']:
                            pnl = -risk_per_trade
                            day_pnl += pnl
                            pos['pnl_pct'] = pnl
                            portfolio_trades.append(pos)
                            day_trades += 1
                            pos = None
                            break
                        elif h >= pos['tp']:
                            pnl = risk_per_trade * rr
                            day_pnl += pnl
                            pos['pnl_pct'] = pnl
                            portfolio_trades.append(pos)
                            day_trades += 1
                            pos = None
                            break
                    elif pos['type'] == 'SELL':
                        if h >= pos['sl']:
                            pnl = -risk_per_trade
                            day_pnl += pnl
                            pos['pnl_pct'] = pnl
                            portfolio_trades.append(pos)
                            day_trades += 1
                            pos = None
                            break
                        elif l <= pos['tp']:
                            pnl = risk_per_trade * rr
                            day_pnl += pnl
                            pos['pnl_pct'] = pnl
                            portfolio_trades.append(pos)
                            day_trades += 1
                            pos = None
                            break
                else:
                    if h >= buy_trigger:
                        sl_dist = sl_mult * atr_base
                        tp_dist = tp_mult * atr_base
                        pos = {
                            'symbol': s, 'type': 'BUY', 'date': curr_d,
                            'entry': buy_trigger, 'sl': buy_trigger - sl_dist,
                            'tp': buy_trigger + tp_dist, 'sl_dist': sl_dist
                        }
                    elif l <= sell_trigger:
                        sl_dist = sl_mult * atr_base
                        tp_dist = tp_mult * atr_base
                        pos = {
                            'symbol': s, 'type': 'SELL', 'date': curr_d,
                            'entry': sell_trigger, 'sl': sell_trigger + sl_dist,
                            'tp': sell_trigger - tp_dist, 'sl_dist': sl_dist
                        }
                        
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_t = pd.DataFrame(portfolio_trades)
    df_d = pd.DataFrame(portfolio_daily)
    
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_t['month'] = pd.to_datetime(df_t['date']).dt.to_period('M')
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    
    wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print("\n" + "=" * 90, flush=True)
    print("      CHAMPION ATR STRADDLE RESULTS (XAUUSD + EURUSD: 11:00 - 16:00 IST)      ", flush=True)
    print("=" * 90, flush=True)
    print(f"Total Completed Trades : {len(df_t):,} across {len(df_d):,} Trading Days", flush=True)
    print(f"Overall Portfolio Win Rate: {wr:.2f}%\n", flush=True)
    
    # Annual Breakdown
    years = sorted(df_d['year'].unique())
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Worst Day':<11} | {'Green Months':<14} | {'Status'}", flush=True)
    print("-" * 90, flush=True)
    
    rois = []
    for y in years:
        y_d = df_d[df_d['year'] == y]
        y_t = df_t[df_t['year'] == y]
        if len(y_t) == 0:
            continue
        y_wr = (y_t['pnl_pct'] > 0).mean() * 100.0
        worst_d = y_d['pnl_pct'].min() * 100.0
        
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
        print(f"{y:<6} | {len(y_t):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {worst_d:+6.2f}%    | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}", flush=True)
        
    print("-" * 90, flush=True)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(rois):+6.2f}% (Median: {np.median(rois):+6.2f}%)", flush=True)
    print(f"PROFITABLE YEARS      : {(np.array(rois) > 0).mean()*100:.1f}%\n", flush=True)
    
    # 2026 Monthly Breakdown
    print("=" * 90, flush=True)
    print("                     2026 IN-DEPTH MONTHLY BREAKDOWN                     ", flush=True)
    print("=" * 90, flush=True)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Worst Day':<12} | {'Max DD':<10} | {'Status'}", flush=True)
    print("-" * 90, flush=True)
    
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
        print(f"{str(m):<10} | {m_trades:<7} | {m_wr:6.1f}%  | {m_ret:+9.2f}%    | {worst_d:+9.2f}%   | {m_max_dd:7.2f}%   | {status}", flush=True)
    print("=" * 90 + "\n", flush=True)


if __name__ == "__main__":
    run_champion_atr_straddle()

