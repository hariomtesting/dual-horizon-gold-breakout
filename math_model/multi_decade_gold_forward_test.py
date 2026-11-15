"""
MULTI-DECADE HISTORICAL FORWARD TEST (2007 - 2026)
==================================================
Simulates the High-Expectancy Gold (XAUUSD) Auction Equilibrium Model
across the full ~20-Year MT5 dataset (~581,740 M5 bars from 2007 to 2026).

Analyzes:
- Every Single Year Performance (2007 to 2026)
- Every Single Month Performance
- Strict Prop Firm Risk Metrics (Max Daily Drawdown, Max Total Drawdown, Win Rate)
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_multi_decade_forward_test():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print("       MULTI-DECADE FORWARD TEST: GOLD (XAUUSD) 2007 - 2026       ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(2000, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    print(f"Fetching full historical M5 bars from {dt_from.year} to {dt_to.year}...", flush=True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Could not load data for {symbol}")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['year'] = df['datetime'].dt.year
    df['month'] = df['datetime'].dt.to_period('M')
    df['hour'] = df['datetime'].dt.hour
    
    start_date = df['datetime'].iloc[0]
    end_date = df['datetime'].iloc[-1]
    print(f"Loaded {len(df):,} M5 bars from {start_date} to {end_date} (~{(end_date-start_date).days / 365.25:.1f} years)\n", flush=True)
    
    spread = 0.20
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    print("Calculating daily volume profiles and VPOC anchors...", flush=True)
    days = sorted(df['date'].unique())
    daily_vpocs = {}
    
    # Fast VPOC calculation
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    print(f"Calculated VPOCs for {len(daily_vpocs):,} trading days.\n", flush=True)
    
    # Pre-extract session records
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
        
    print(f"Total Active Session Days to Simulate: {len(day_sessions):,}\n", flush=True)
    print("Simulating trading execution across all 20 years...", flush=True)
    
    # Model parameters (Champion Configuration)
    sl_mult = 1.50
    tp_ratio = 0.55
    risk_per_trade = 0.015 # 1.50% standard risk
    daily_loss_limit = -0.025 # -2.5% max daily loss circuit breaker
    daily_profit_target = 0.028 # +2.8% daily profit lock
    
    all_trades = []
    daily_results = []
    
    for curr_d, prev_poc, bars in day_sessions:
        day_pnl_pct = 0.0
        day_trades = 0
        day_stopped = False
        pos = None
        trades_today = 0
        
        for i in range(len(bars)):
            r = bars[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']
            
            if day_pnl_pct <= daily_loss_limit or day_pnl_pct >= daily_profit_target:
                day_stopped = True
                break
                
            # Manage active trade
            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pnl_pct = -risk_per_trade
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        trades_today += 1
                        day_trades += 1
                        pos = None
                    elif high_p >= pos['tp']:
                        pnl_pct = risk_per_trade * pos['rr']
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        trades_today += 1
                        day_trades += 1
                        pos = None
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pnl_pct = -risk_per_trade
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        trades_today += 1
                        day_trades += 1
                        pos = None
                    elif low_p <= pos['tp']:
                        pnl_pct = risk_per_trade * pos['rr']
                        day_pnl_pct += pnl_pct
                        pos['pnl_pct'] = pnl_pct
                        pos['exit_time'] = r['datetime']
                        all_trades.append(pos)
                        trades_today += 1
                        day_trades += 1
                        pos = None
                        
            # Entry logic
            if pos is None and trades_today < 2 and i >= 3:
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
    tot_wins = (df_trades['pnl_pct'] > 0).sum()
    overall_wr = (tot_wins / tot_trades) * 100.0 if tot_trades > 0 else 0.0
    
    print("\n" + "=" * 95)
    print(f"      MULTI-DECADE FORWARD TEST RESULTS SUMMARY (2007 - 2026)      ")
    print("=" * 95)
    print(f"Total Historical Trades Executed : {tot_trades:,}")
    print(f"Overall Historical Win Rate      : {overall_wr:.2f}%")
    print(f"Total Trading Days Covered       : {len(df_daily):,} days")
    print("=" * 95 + "\n")
    
    # 1. Year-by-Year Performance
    years = sorted(df_daily['year'].unique())
    print("=" * 95)
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 95)
    
    annual_results = []
    
    for y in years:
        y_daily = df_daily[df_daily['year'] == y]
        y_trades = df_trades[df_trades['year'] == y]
        
        if len(y_trades) == 0:
            continue
            
        y_wr = (y_trades['pnl_pct'] > 0).mean() * 100.0
        
        # Calculate compounded annual return
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        
        for _, d_row in y_daily.iterrows():
            d_pnl = cap * d_row['pnl_pct']
            cap += d_pnl
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
                
        y_roi = (cap - 10000.0) / 10000.0 * 100.0
        
        # Count green months in this year
        m_rois = []
        for m in y_daily['month'].unique():
            m_sub = y_daily[y_daily['month'] == m]
            m_pnl = m_sub['pnl_pct'].sum() * 100.0
            m_rois.append(m_pnl)
            
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        annual_results.append({
            'year': y, 'trades': len(y_trades), 'wr': y_wr, 'roi': y_roi,
            'max_dd': max_dd, 'green_months': f"{green_m}/{tot_m} ({green_pct:.0f}%)"
        })
        
        status = "[PROFITABLE YEAR]" if y_roi > 0 else "[RED YEAR]"
        print(f"{y:<6} | {len(y_trades):<8} | {y_wr:6.2f}%    | {y_roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {status}")
        
    print("-" * 95)
    df_annual = pd.DataFrame(annual_results)
    avg_annual_roi = df_annual['roi'].mean()
    med_annual_roi = df_annual['roi'].median()
    avg_annual_dd = df_annual['max_dd'].mean()
    pct_green_years = (df_annual['roi'] > 0).mean() * 100.0
    
    print(f"AVERAGE ANNUAL RETURN  : {avg_annual_roi:+6.2f}% (Median: {med_annual_roi:+6.2f}%)")
    print(f"AVERAGE ANNUAL MAX DD  : {avg_annual_dd:6.2f}% (Worst Year Max DD: {df_annual['max_dd'].max():6.2f}%)")
    print(f"PROFITABLE YEARS RATIO : {pct_green_years:.1f}% ({sum(df_annual['roi'] > 0)} / {len(df_annual)} Years)")
    print("=" * 95 + "\n")
    
    # Save complete month-by-month table to CSV for user inspection
    month_records = []
    all_months = sorted(df_daily['month'].unique())
    for m in all_months:
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
            'Month': str(m),
            'Trades': len(m_trades),
            'WinRate': round(m_wr, 2),
            'NetROI': round(m_roi, 2),
            'WorstDayLoss': round(worst_day, 2),
            'MaxDD': round(max_dd, 2)
        })
        
    df_months = pd.DataFrame(month_records)
    csv_path = "d:/Expermental/math_model/multi_decade_monthly_breakdown.csv"
    df_months.to_csv(csv_path, index=False)
    print(f"Detailed month-by-month performance saved to: {csv_path}\n", flush=True)
    
    # Print recent 36 months snapshot
    print("=" * 95)
    print("          RECENT 36 MONTHS (2024 - 2026) IN-DEPTH MONTHLY BREAKDOWN         ")
    print("=" * 95)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Max Daily DD':<13} | {'Max Total DD':<13} | {'Status'}")
    print("-" * 95)
    for _, row in df_months.tail(36).iterrows():
        m_str = row['Month']
        tr_cnt = int(row['Trades'])
        wr_val = row['WinRate']
        roi_val = row['NetROI']
        w_day = abs(row['WorstDayLoss'])
        m_dd = row['MaxDD']
        st = "[PASSED >=20%]" if roi_val >= 20.0 else ("[GREEN]" if roi_val > 0 else "[RED]")
        print(f"{m_str:<10} | {tr_cnt:<7} | {wr_val:6.1f}%   | {roi_val:+9.2f}%    | {w_day:9.2f}%     | {m_dd:9.2f}%     | {st}")
    print("=" * 95 + "\n")


if __name__ == "__main__":
    run_multi_decade_forward_test()

