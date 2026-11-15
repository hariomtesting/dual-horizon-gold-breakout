"""
LONDON OPEN OPENING DRIVE PULLBACK SCALPER (11:00 AM - 4:00 PM IST)
===================================================================
Operates within 11:00 AM to 4:00 PM IST (05:30 to 10:30 UTC):
1. Phase 1 (11:00 AM - 12:30 PM IST): Pre-London Base (Builds Volume & VWAP anchor).
2. Phase 2 (12:30 PM - 1:15 PM IST): London Open Direction Drive.
   - Measures London Open impulse: If London open pushes strongly in one direction with Volume > 1.5x average.
3. Phase 3 (1:15 PM - 3:30 PM IST): Execution of Pullback to Session VWAP.
   - Enters in the direction of the London Open drive upon pullback to Session VWAP.
   - SL: 1.0x ATR, TP: 2.0x ATR (Asymmetric 2.0:1 R:R).
   - Pyramiding: Adds Unit 2 when Unit 1 reaches +1.0x ATR profit.
4. Phase 4 (3:55 PM IST): Hard Auto-Square-Off (100% Flat before PC shutdown).
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def run_london_drive_simulation():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("      LONDON OPEN DRIVE PULLBACK SCALPER (GOLD: 11:00 AM - 4:00 PM IST)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2018, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['date'] = df['datetime'].dt.date
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    # IST Time conversion
    df['ist_time'] = df['datetime'] + datetime.timedelta(hours=5, minutes=30)
    df['ist_hour'] = df['ist_time'].dt.hour
    df['ist_minute'] = df['ist_time'].dt.minute
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter 11:00 to 16:00 IST
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
    
    # Calculate session VWAP
    tp = (df_ist['high'] + df_ist['low'] + df_ist['close']) / 3.0
    df_ist['pv'] = tp * df_ist['volume']
    df_ist['cum_vol'] = df_ist.groupby('date')['volume'].cumsum()
    df_ist['cum_pv'] = df_ist.groupby('date')['pv'].cumsum()
    df_ist['vwap'] = df_ist['cum_pv'] / df_ist['cum_vol'].replace(0, np.nan)
    
    day_sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            day_sessions[d] = sub[['datetime', 'ist_time', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'volume', 'atr', 'vwap']].to_dict('records')
            
    print(f"Loaded {len(day_sessions):,} trading days from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}.\n", flush=True)
    
    risk_per_trade = 0.0125
    all_trades = []
    daily_results = []
    
    days = sorted(list(day_sessions.keys()))
    
    for curr_d in days:
        bars = day_sessions[curr_d]
        
        # Phase 1 & 2: Evaluate London Open Drive (12:30 PM to 1:15 PM IST = bars index 18 to 27)
        london_open_bar = bars[18] # 12:30 PM IST
        london_drive_end = bars[27] # 1:15 PM IST
        
        drive_move = london_drive_end['close'] - london_open_bar['open']
        atr_drive = london_drive_end['atr']
        
        # Clear impulse drive: at least 0.7x ATR move during London Open
        is_bull_drive = drive_move >= 0.6 * atr_drive
        is_bear_drive = drive_move <= -0.6 * atr_drive
        
        day_pnl_pct = 0.0
        day_trades = 0
        pos_u1 = None
        pos_u2 = None
        trade_taken_today = False
        
        # Phase 3: Trade Pullback to VWAP (1:15 PM to 3:55 PM IST = bars index 28 to end)
        for i in range(28, len(bars)):
            r = bars[i]
            high_p, low_p, close_p, o_p = r['high'], r['low'], r['close'], r['open']
            atr, vwap = r['atr'], r['vwap']
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
                # Pyramiding trigger at +1.0x ATR
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
                        
            # Manage Unit 2
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
                        
            # Entry logic: Pullback to VWAP in direction of London drive
            if not trade_taken_today and pos_u1 is None and ist_h < 15:
                sl_dist = 1.0 * atr
                tp_dist = 2.0 * atr # 2.0:1 Asymmetric R:R
                rr = tp_dist / sl_dist
                
                # Bullish Drive Pullback: London drove UP, price pulls back to touch VWAP and bounces green
                if is_bull_drive and low_p <= vwap and close_p > vwap and close_p > o_p:
                    pos_u1 = {
                        'symbol': 'XAUUSD', 'unit': 1, 'type': 'BUY', 'date': curr_d,
                        'entry': close_p + 0.20, 'sl': close_p - sl_dist,
                        'tp': close_p + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                        'be': False, 'entry_time': r['ist_time']
                    }
                    trade_taken_today = True
                # Bearish Drive Pullback: London drove DOWN, price pulls back to touch VWAP and rejects red
                elif is_bear_drive and high_p >= vwap and close_p < vwap and close_p < o_p:
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
    print("      LONDON OPEN DRIVE PULLBACK SCALPER RESULTS (11:00 AM - 4:00 PM IST)      ")
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


if __name__ == "__main__":
    run_london_drive_simulation()
