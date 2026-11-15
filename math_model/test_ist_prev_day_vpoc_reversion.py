"""
PREVIOUS DAY VPOC INSTITUTIONAL REVERSION SCALPER (XAUUSD & EURUSD)
===================================================================
Operating strictly within 11:00 AM - 4:00 PM IST (No VPS needed):
1. Yesterday's Volume Profile:
   - Compute Previous Day's Volume Point of Control (Yesterday's VPOC).
2. Intraday Deviation & Edge (11:00 AM - 4:00 PM IST):
   - Measure distance from current price to Yesterday's VPOC.
   - If 0.45x ATR <= Distance <= 2.2x ATR:
     * BUY if Price < Prev_VPOC and Delta > 0 and Close > Open (Absorption Bounce)
     * SELL if Price > Prev_VPOC and Delta < 0 and Close < Open (Absorption Rejection)
3. Target & Stop:
   - Target: 0.65x distance back toward Yesterday's VPOC (High-probability magnet)
   - Stop Loss: 1.4x ATR
4. Circuit Breakers:
   - Daily loss limit: -2.0% (Stops trading immediately after 2 losses, protecting prop firm)
   - Auto-square-off at 15:55 IST (100% Flat)
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_ist_prev_vpoc_simulation(symbols=["XAUUSD", "EURUSD"], dt_start=2021):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    for s in symbols:
        mt5.symbol_select(s, True)
        
    print("=" * 90, flush=True)
    print(f"      PREVIOUS DAY VPOC REVERSION SCALPER ({', '.join(symbols)}: 11:00 - 16:00 IST)      ", flush=True)
    print("=" * 90 + "\n", flush=True)
    
    dt_from = datetime.datetime(dt_start, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            print(f"Failed to fetch {s}", flush=True)
            continue
            
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['volume'] = df['volume'].replace(0, 1.0)
        
        of = OrderFlowEngine(tick_size=0.0001 if 'EUR' in s else 0.10)
        df = of.calculate_delta(df)
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Calculate daily VPOC for each full calendar day
        daily_vpocs = {}
        for d, sub in df.groupby('date'):
            if len(sub) >= 40:
                vp = of.calculate_volume_profile(sub)
                daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
                
        # Filter only 11:00 to 16:00 IST for execution
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
                
        asset_data[s] = {
            'symbol': s,
            'vpocs': daily_vpocs,
            'sessions': sessions,
            'spread': 0.0001 if 'EUR' in s else 0.20
        }
        print(f"Loaded {s:<8}: {len(sessions):,} sessions with daily VPOCs.", flush=True)
        
    risk_per_trade = 0.010 # 1.0% risk per trade
    sl_mult = 1.40
    tp_ratio = 0.65
    
    # Common dates
    all_dates = sorted(list(set.union(*[set(data['sessions'].keys()) for data in asset_data.values()])))
    
    portfolio_daily = []
    portfolio_trades = []
    
    for d_idx in range(1, len(all_dates)):
        prev_d = all_dates[d_idx - 1]
        curr_d = all_dates[d_idx]
        
        day_pnl = 0.0
        day_trades = 0
        day_stopped = False
        
        for s in symbols:
            if day_stopped:
                break
            d_dict = asset_data[s]
            if prev_d not in d_dict['vpocs'] or curr_d not in d_dict['sessions']:
                continue
                
            prev_poc = d_dict['vpocs'][prev_d]
            bars = d_dict['sessions'][curr_d]
            spread = d_dict['spread']
            
            pos = None
            asset_trades_today = 0
            max_asset_trades = 2 # up to 2 trades per asset
            
            for i in range(len(bars)):
                r = bars[i]
                h, l, c, o = r['high'], r['low'], r['close'], r['open']
                atr, delta = r['atr'], r['delta']
                ist_h, ist_m = r['ist_hour'], r['ist_minute']
                
                # Daily circuit breaker: Stop after -2.0% loss today
                if day_pnl <= -0.020:
                    day_stopped = True
                    break
                    
                # Hard Auto-Square-Off at 15:55 IST
                if ist_h == 15 and ist_m >= 55:
                    if pos is not None:
                        pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                        pnl = (pts / pos['sl_dist']) * risk_per_trade
                        pos['pnl_pct'] = pnl
                        day_pnl += pnl
                        portfolio_trades.append(pos)
                        day_trades += 1
                        pos = None
                    break
                    
                # Manage Open Position
                if pos is not None:
                    if pos['type'] == 'BUY':
                        if l <= pos['sl']:
                            pos['pnl_pct'] = -risk_per_trade
                            day_pnl += pos['pnl_pct']
                            portfolio_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                        elif h >= pos['tp']:
                            pos['pnl_pct'] = risk_per_trade * pos['rr']
                            day_pnl += pos['pnl_pct']
                            portfolio_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                    elif pos['type'] == 'SELL':
                        if h >= pos['sl']:
                            pos['pnl_pct'] = -risk_per_trade
                            day_pnl += pos['pnl_pct']
                            portfolio_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                        elif l <= pos['tp']:
                            pos['pnl_pct'] = risk_per_trade * pos['rr']
                            day_pnl += pos['pnl_pct']
                            portfolio_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                            
                # Entry Logic: Deviation from Yesterday's VPOC
                if pos is None and asset_trades_today < max_asset_trades and 11 <= ist_h < 15:
                    full_dist = abs(c - prev_poc)
                    if 0.45 * atr <= full_dist <= 2.2 * atr:
                        sl_dist = sl_mult * atr
                        tp_dist = tp_ratio * full_dist
                        rr = tp_dist / sl_dist
                        
                        # Bullish Absorption Bounce: Below Prev VPOC, Delta > 0, Green Candle
                        if c < prev_poc and delta > 0 and c > o:
                            pos = {
                                'symbol': s, 'type': 'BUY', 'date': curr_d,
                                'entry': c + spread, 'sl': c - sl_dist,
                                'tp': c + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                                'entry_time': r['ist_dt']
                            }
                        # Bearish Absorption Reversal: Above Prev VPOC, Delta < 0, Red Candle
                        elif c > prev_poc and delta < 0 and c < o:
                            pos = {
                                'symbol': s, 'type': 'SELL', 'date': curr_d,
                                'entry': c, 'sl': c + sl_dist,
                                'tp': c - tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                                'entry_time': r['ist_dt']
                            }
                            
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_t = pd.DataFrame(portfolio_trades)
    df_d = pd.DataFrame(portfolio_daily)
    
    if len(df_t) == 0:
        print("No trades found.", flush=True)
        return
        
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_t['month'] = pd.to_datetime(df_t['date']).dt.to_period('M')
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    
    wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print("\n" + "=" * 90, flush=True)
    print("      PREVIOUS DAY VPOC REVERSION SCALPER RESULTS (11:00 AM - 4:00 PM IST)      ", flush=True)
    print("=" * 90, flush=True)
    print(f"Total Completed Trades : {len(df_t):,} across {len(df_d):,} Trading Days", flush=True)
    print(f"Average Trades per Day : {len(df_t) / len(df_d):.2f}", flush=True)
    print(f"Overall Portfolio Win Rate: {wr:.2f}%\n", flush=True)
    
    # Per Symbol Breakdown
    print("-" * 90, flush=True)
    print(f"{'Asset':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Avg RR':<8} | {'Total PnL':<12}")
    print("-" * 90, flush=True)
    for sym in symbols:
        sym_t = df_t[df_t['symbol'] == sym]
        if len(sym_t) > 0:
            sym_wr = (sym_t['pnl_pct'] > 0).mean() * 100.0
            sym_pnl = sym_t['pnl_pct'].sum() * 100.0
            avg_rr = sym_t['rr'].mean()
            print(f"{sym:<10} | {len(sym_t):<8} | {sym_wr:6.2f}%    | {avg_rr:5.2f}R   | {sym_pnl:+9.2f}%", flush=True)
    print("-" * 90 + "\n", flush=True)
    
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
    run_ist_prev_vpoc_simulation(["XAUUSD", "EURUSD"], 2021)

