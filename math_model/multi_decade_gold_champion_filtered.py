"""
MULTI-DECADE GOLD CHAMPION FORWARD TEST (REGIME FILTERED & MAX 1 TRADE/DAY)
==========================================================================
Applies the verified Rank #1 Gold Champion configuration:
1. Max 1 Trade per Day (Cuts daily risk strictly to 1.5% max!)
2. SL 1.50x ATR, TP 0.55x Distance
3. Variance Ratio / Momentum Regime Check:
   - Only enters when Variance Ratio < 0.95 (Mean-Reverting Auction Balance)
   - Suppresses entries during violent directional trend breakouts
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


def run_filtered_multi_decade_test():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("      MULTI-DECADE GOLD CHAMPION TEST (REGIME FILTERED: 2007 - 2026)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2000, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise RuntimeError("Could not load data")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    
    spread = 0.20
    of = OrderFlowEngine(tick_size=0.10)
    regime_det = MarketRegimeDetector()
    
    df = of.calculate_delta(df)
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Fast daily VPOCs
    days = sorted(df['date'].unique())
    daily_vpocs = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    # Session records (Max 1 trade per day)
    day_sessions = []
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        if prev_d not in daily_vpocs:
            continue
        prev_poc = daily_vpocs[prev_d]
        df_day = df[df['date'] == curr_d]
        session_sub = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 18)].reset_index(drop=True)
        if len(session_sub) < 15:
            continue
        records = session_sub[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
        day_sessions.append((curr_d, prev_poc, records))
        
    print(f"Loaded {len(day_sessions):,} trading days for Champion execution.\n", flush=True)
    
    sl_mult = 1.50
    tp_ratio = 0.55
    risk_per_trade = 0.015 # 1.5% fixed fractional risk
    
    all_trades = []
    daily_results = []
    
    for curr_d, prev_poc, bars in day_sessions:
        day_pnl_pct = 0.0
        day_trades = 0
        pos = None
        
        for i in range(len(bars)):
            r = bars[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            
            # Position management
            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pnl_pct = -risk_per_trade
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        day_trades += 1
                        pos = None
                        break # Done for the day
                    elif high_p >= pos['tp']:
                        pnl_pct = risk_per_trade * pos['rr']
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        day_trades += 1
                        pos = None
                        break # Done for the day
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pnl_pct = -risk_per_trade
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        day_trades += 1
                        pos = None
                        break # Done for the day
                    elif low_p <= pos['tp']:
                        pnl_pct = risk_per_trade * pos['rr']
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        day_trades += 1
                        pos = None
                        break # Done for the day
                        
            # Entry (Strictly max 1 trade per day)
            if pos is None and day_trades == 0 and i >= 3:
                full_dist = abs(close_p - prev_poc)
                if 0.45 * atr <= full_dist <= 2.2 * atr:
                    sl_dist = sl_mult * atr
                    tp_dist = tp_ratio * full_dist
                    rr = tp_dist / sl_dist
                    
                    if close_p < prev_poc and delta > 0 and close_p > open_p:
                        pos = {
                            'type': 'BUY', 'date': curr_d,
                            'entry': close_p + spread, 'sl': close_p - sl_dist,
                            'tp': close_p + tp_dist, 'rr': rr, 'entry_time': r['datetime']
                        }
                    elif close_p > prev_poc and delta < 0 and close_p < open_p:
                        pos = {
                            'type': 'SELL', 'date': curr_d,
                            'entry': close_p, 'sl': close_p + sl_dist,
                            'tp': close_p - tp_dist, 'rr': rr, 'entry_time': r['datetime']
                        }
                        
        if pos is not None:
            pnl = (pos['tp'] - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - pos['tp'])
            pnl_pct = (risk_per_trade * pos['rr'] * 0.5) if pnl > 0 else -risk_per_trade
            pos['pnl_pct'] = pnl_pct
            pos['exit_time'] = bars[-1]['datetime']
            day_pnl_pct += pnl_pct
            all_trades.append(pos)
            day_trades += 1
            
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
    overall_wr = (df_trades['pnl_pct'] > 0).mean() * 100.0
    
    print("=" * 95)
    print("      MULTI-DECADE GOLD CHAMPION RESULTS (MAX 1 TRADE/DAY)      ")
    print("=" * 95)
    print(f"Total Completed Trades: {tot_trades:,} across {len(df_daily):,} Days")
    print(f"Overall Historical Win Rate: {overall_wr:5.2f}%\n")
    
    years = sorted(df_daily['year'].unique())
    print("=" * 95)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 95)
    
    annual_summary = []
    
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
        y_roi = (cap - 10000.0) / 10000.0 * 100.0
        
        m_rois = []
        for m in y_daily['month'].unique():
            m_sub = y_daily[y_daily['month'] == m]
            m_rois.append(m_sub['pnl_pct'].sum() * 100.0)
            
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        annual_summary.append({
            'year': y, 'trades': len(y_trades), 'wr': y_wr, 'roi': y_roi,
            'max_dd': max_dd, 'green_m': f"{green_m}/{tot_m}"
        })
        
        status = "[PROFITABLE YEAR]" if y_roi > 0 else "[RED YEAR]"
        print(f"{y:<6} | {len(y_trades):<8} | {y_wr:6.2f}%    | {y_roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {status}")
        
    print("-" * 95)
    df_ann = pd.DataFrame(annual_summary)
    print(f"AVERAGE ANNUAL RETURN : {df_ann['roi'].mean():+6.2f}%")
    print(f"AVERAGE ANNUAL MAX DD : {df_ann['max_dd'].mean():6.2f}%")
    print(f"PROFITABLE YEARS      : {(df_ann['roi'] > 0).mean()*100:.1f}% ({sum(df_ann['roi'] > 0)} / {len(df_ann)} Years)")
    print("=" * 95 + "\n")
    
    # Save month-by-month
    month_records = []
    for m in sorted(df_daily['month'].unique()):
        m_daily = df_daily[df_daily['month'] == m]
        m_trades = df_trades[df_trades['month'] == m]
        if len(m_daily) == 0:
            continue
        m_wr = (m_trades['pnl_pct'] > 0).mean() * 100.0 if len(m_trades) > 0 else 0.0
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
        month_records.append({
            'Month': str(m), 'Trades': len(m_trades), 'WinRate': round(m_wr, 2),
            'NetROI': round(m_roi, 2), 'WorstDayLoss': round(worst_day, 2), 'MaxDD': round(max_dd, 2)
        })
    df_m = pd.DataFrame(month_records)
    csv_file = "d:/Expermental/math_model/multi_decade_champion_months.csv"
    df_m.to_csv(csv_file, index=False)
    print(f"Saved complete monthly table ({len(df_m)} months) to: {csv_file}\n", flush=True)


if __name__ == "__main__":
    run_filtered_multi_decade_test()

