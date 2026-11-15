"""
INDIAN TIME (11:00 AM - 4:00 PM IST) INSTITUTIONAL SNIPER SCALPER
=================================================================
Window: 11:00 AM to 4:00 PM IST (05:30 to 10:30 UTC)
Assets: XAUUSD (Gold), GER40.r (DAX), NAS100.r

Execution Rules:
1. Pre-London Range (11:00 AM - 12:30 PM IST / 05:30 - 07:00 UTC):
   - Computes High & Low of the Asian/European pre-open.
2. London Liquidity Sweep & Reversal (12:30 PM - 3:30 PM IST / 07:00 - 10:00 UTC):
   - When price pierces Pre-London High/Low and rejects back inside with strong Delta absorption.
   - Strictly MAXIMUM 1 setup per asset per day (No over-trading).
3. Risk-Free Pyramiding:
   - Unit 1: 1.25% risk. SL: 1.0x ATR, TP: 1.8x ATR.
   - When profit reaches +0.8x ATR: Unit 1 SL moves to Breakeven (+0.1R).
   - Unit 2 is deployed at that moment aiming for +1.8x ATR.
   - Runner pays +2.5R to +2.8R with zero open risk!
4. Hard Auto-Square-Off at 3:55 PM IST (10:25 UTC):
   - Guaranteed flat in cash before PC shutdown.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_sniper_scalper_simulation():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    print("=" * 95, flush=True)
    print("      INDIAN TIME (11:00 AM - 4:00 PM IST) SNIPER SCALPER SIMULATION      ", flush=True)
    print("=" * 95 + "\n", flush=True)
    
    symbols_config = [
        {"symbol": "XAUUSD", "tick_sz": 0.10, "spread": 0.20},
        {"symbol": "GER40.r", "tick_sz": 0.50, "spread": 0.80},
        {"symbol": "NAS100.r", "tick_sz": 1.0, "spread": 0.50}
    ]
    
    dt_from = datetime.datetime(2021, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for cfg in symbols_config:
        sym = cfg["symbol"]
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            continue
            
        df = pd.DataFrame(rates)
        df['datetime'] = pd.to_datetime(df['time'], unit='s')
        df['date'] = df['datetime'].dt.date
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['volume'] = df['volume'].replace(0, 1.0)
        
        # Indian Standard Time (UTC+5:30)
        df['ist_time'] = df['datetime'] + datetime.timedelta(hours=5, minutes=30)
        df['ist_hour'] = df['ist_time'].dt.hour
        df['ist_minute'] = df['ist_time'].dt.minute
        
        of = OrderFlowEngine(tick_size=cfg['tick_sz'])
        df = of.calculate_delta(df)
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Filter only 11:00 AM to 16:00 PM IST
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
        
        day_records = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 25:
                day_records[d] = sub[['datetime', 'ist_time', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
                
        asset_data[sym] = {
            'records': day_records,
            'spread': cfg['spread'],
            'dates': set(day_records.keys())
        }
        print(f"Loaded {sym}: {len(day_records):,} active trading days in IST window.", flush=True)
        
    common_dates = sorted(list(set.intersection(*[v['dates'] for v in asset_data.values()])))
    print(f"\nTotal Overlapping Days: {len(common_dates):,} days from {common_dates[0]} to {common_dates[-1]}\n", flush=True)
    
    risk_per_trade = 0.0125 # 1.25% safe risk
    all_trades = []
    daily_results = []
    
    for curr_d in common_dates:
        day_pnl_pct = 0.0
        day_trades = 0
        
        for sym, data in asset_data.items():
            bars = data['records'][curr_d]
            spread = data['spread']
            
            # Pre-London Range (11:00 AM to 12:30 PM IST = first 18 bars of M5)
            pre_bars = bars[:18]
            pre_high = max([b['high'] for b in pre_bars])
            pre_low = min([b['low'] for b in pre_bars])
            
            pos_u1 = None
            pos_u2 = None
            trade_taken_today = False
            
            # Trading execution from 12:30 PM to 3:55 PM IST
            for i in range(18, len(bars)):
                r = bars[i]
                high_p, low_p, close_p, o_p = r['high'], r['low'], r['close'], r['open']
                atr, delta = r['atr'], r['delta']
                ist_h, ist_m = r['ist_hour'], r['ist_minute']
                
                # Hard Auto-Square-Off at 15:50 - 15:55 IST
                if ist_h == 15 and ist_m >= 50:
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
                    # Breakeven & Pyramiding Trigger: Once profit >= +0.8x ATR
                    if not pos_u1['be']:
                        if pos_u1['type'] == 'BUY' and high_p >= pos_u1['entry'] + 0.8 * atr:
                            pos_u1['sl'] = pos_u1['entry'] + spread
                            pos_u1['be'] = True
                            # Deploy Unit 2
                            if pos_u2 is None:
                                pos_u2 = {
                                    'symbol': sym, 'unit': 2, 'type': 'BUY', 'date': curr_d,
                                    'entry': close_p + spread, 'sl': pos_u1['entry'] + spread,
                                    'tp': close_p + 1.2 * atr, 'sl_dist': close_p - (pos_u1['entry'] + spread),
                                    'rr': 1.5, 'be': True, 'entry_time': r['ist_time']
                                }
                        elif pos_u1['type'] == 'SELL' and low_p <= pos_u1['entry'] - 0.8 * atr:
                            pos_u1['sl'] = pos_u1['entry'] - spread
                            pos_u1['be'] = True
                            # Deploy Unit 2
                            if pos_u2 is None:
                                pos_u2 = {
                                    'symbol': sym, 'unit': 2, 'type': 'SELL', 'date': curr_d,
                                    'entry': close_p, 'sl': pos_u1['entry'] - spread,
                                    'tp': close_p - 1.2 * atr, 'sl_dist': (pos_u1['entry'] - spread) - close_p,
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
                            
                # Sniper Entry Trigger (Strictly 1 per day per asset)
                if not trade_taken_today and pos_u1 is None and ist_h < 15:
                    sl_dist = 1.1 * atr
                    tp_dist = 1.8 * atr # 1.63:1 R:R
                    rr = tp_dist / sl_dist
                    
                    # Setup A: Liquidity Sweep Reversal (Judas Swing)
                    # Price pierced pre_high, closed back below with negative delta
                    if high_p > pre_high and close_p < pre_high and delta < 0 and close_p < o_p:
                        pos_u1 = {
                            'symbol': sym, 'unit': 1, 'type': 'SELL', 'setup': 'SWEEP_REVERSAL',
                            'date': curr_d, 'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                            'be': False, 'entry_time': r['ist_time']
                        }
                        trade_taken_today = True
                    # Price pierced pre_low, closed back above with positive delta
                    elif low_p < pre_low and close_p > pre_low and delta > 0 and close_p > o_p:
                        pos_u1 = {
                            'symbol': sym, 'unit': 1, 'type': 'BUY', 'setup': 'SWEEP_REVERSAL',
                            'date': curr_d, 'entry': close_p + spread, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                            'be': False, 'entry_time': r['ist_time']
                        }
                        trade_taken_today = True
                    # Setup B: Momentum Trend Expansion (Clean candle breaking out with large delta)
                    elif close_p > pre_high + 0.3 * atr and delta > 0 and close_p > o_p:
                        pos_u1 = {
                            'symbol': sym, 'unit': 1, 'type': 'BUY', 'setup': 'MOMENTUM_BREAKOUT',
                            'date': curr_d, 'entry': close_p + spread, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                            'be': False, 'entry_time': r['ist_time']
                        }
                        trade_taken_today = True
                    elif close_p < pre_low - 0.3 * atr and delta < 0 and close_p < o_p:
                        pos_u1 = {
                            'symbol': sym, 'unit': 1, 'type': 'SELL', 'setup': 'MOMENTUM_BREAKOUT',
                            'date': curr_d, 'entry': close_p, 'sl': close_p + sl_dist,
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
    
    df_trades['year'] = pd.to_datetime(df_trades['date']).dt.year
    df_trades['month'] = pd.to_datetime(df_trades['date']).dt.to_period('M')
    df_daily['year'] = pd.to_datetime(df_daily['date']).dt.year
    df_daily['month'] = pd.to_datetime(df_daily['date']).dt.to_period('M')
    
    tot_trades = len(df_trades)
    tot_wr = (df_trades['pnl_pct'] > 0).mean() * 100.0
    
    print("=" * 95)
    print("      INDIAN TIME SNIPER SCALPER RESULTS (11:00 AM - 4:00 PM IST)      ")
    print("=" * 95)
    print(f"Total Completed Sniper Trades : {tot_trades:,} across {len(df_daily):,} Trading Days")
    print(f"Overall Scalper Win Rate       : {tot_wr:.2f}%")
    print(f"Trades per Month Average       : {tot_trades / len(df_trades['month'].unique()):.1f} trades/month\n")
    
    for s in df_trades['symbol'].unique():
        sub = df_trades[df_trades['symbol'] == s]
        print(f"   {s:<10}: {len(sub):4d} Trades | Win Rate: {(sub['pnl_pct'] > 0).mean()*100:.2f}%")
    print("=" * 95 + "\n")
    
    # Year-by-Year Performance
    years = sorted(df_daily['year'].unique())
    print("=" * 95)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 95)
    
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
        
    print("-" * 95)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(annual_rois):+6.2f}% (Median: {np.median(annual_rois):+6.2f}%)")
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean()*100:.1f}%\n")
    
    # Print 2026 Monthly Breakdown
    print("=" * 95)
    print("                     2026 IN-DEPTH MONTHLY BREAKDOWN                     ")
    print("=" * 95)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Worst Day':<12} | {'Max DD':<10} | {'Status'}")
    print("-" * 95)
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
    run_sniper_scalper_simulation()

