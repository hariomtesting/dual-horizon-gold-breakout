"""
MULTI-ASSET DUAL-SESSION VPOC FORWARD TEST (2012 - 2026)
=======================================================
Forward walk backtest of the user's selected champion system:
- Assets: XAUUSD (Gold) + NAS100.r (Nasdaq)
- Setup: Pure VPOC Magnet Front-Run (SL: 1.5x ATR, TP: 0.55x VPOC distance)
- Sessions: London (07:00-11:30 UTC) & New York (13:00-17:30 UTC)
- Asymmetric Daily Risk Management:
  * Base Risk: 1.50%
  * After Win on Day: Risk 1.25% (Protects gains)
  * After Loss on Day: Risk 0.75% (Defense mode)
  * Daily Loss Circuit Breaker: -2.50% (Trading halts for the day; impossible to breach 4.5% prop limit)
  * Daily Profit Target Lock: +2.80% (Trading halts to lock green day)

Outputs:
1. Every Single Year Performance (2012 to 2026)
2. Every Single Month Performance across all years
3. Saves complete multi-year monthly dataset to CSV
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def prepare_asset_data(symbol: str, tick_sz: float, spread: float, n_bars: int = 600000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Could not load data for {symbol}")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    
    of = OrderFlowEngine(tick_size=tick_sz)
    df = of.calculate_delta(df)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Fast daily VPOC calculation using groupby
    daily_vpocs = {}
    grouped = df.groupby('date')
    
    for d, sub in grouped:
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    # Group session records by date
    day_sessions = {}
    session_df = df[(df['hour'] >= 7) & (df['hour'] <= 18)]
    for d, sub in session_df.groupby('date'):
        if len(sub) >= 15:
            lon = sub[(sub['hour'] >= 7) & (sub['hour'] <= 11)]
            ny = sub[(sub['hour'] >= 13) & (sub['hour'] <= 17)]
            day_sessions[d] = {
                'lon': lon[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records') if len(lon) >= 10 else [],
                'ny': ny[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records') if len(ny) >= 10 else []
            }
            
    return {
        'symbol': symbol,
        'spread': spread,
        'vpocs': daily_vpocs,
        'sessions': day_sessions,
        'earliest': df['datetime'].iloc[0],
        'latest': df['datetime'].iloc[-1],
        'total_bars': len(df)
    }


def run_full_multi_asset_forward_test():
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
        
    print("=" * 95, flush=True)
    print("      MULTI-ASSET DUAL-SESSION VPOC FORWARD TEST (GOLD + NASDAQ)      ", flush=True)
    print("=" * 95 + "\n", flush=True)
    
    print("1. Loading historical M5 bars for XAUUSD (Gold)...", flush=True)
    gold = prepare_asset_data("XAUUSD", 0.10, 0.20, 600000)
    print(f"   Loaded {gold['total_bars']:,} bars from {gold['earliest']} to {gold['latest']}")
    
    print("2. Loading historical M5 bars for NAS100.r (Nasdaq)...", flush=True)
    nas = prepare_asset_data("NAS100.r", 1.0, 0.50, 600000)
    print(f"   Loaded {nas['total_bars']:,} bars from {nas['earliest']} to {nas['latest']}\n")
    
    # Overlapping active days
    common_dates = sorted(list(set(gold['sessions'].keys()).intersection(set(nas['sessions'].keys()))))
    print(f"Total Common Overlapping Trading Days: {len(common_dates):,} days")
    print(f"Simulation Period: {common_dates[0]} to {common_dates[-1]} (~{(common_dates[-1] - common_dates[0]).days / 365.25:.1f} Years)\n", flush=True)
    
    # Parameters matching the user's exact quote
    base_risk = 0.0150          # 1.50% base risk
    daily_loss_limit = -0.0250  # -2.50% circuit breaker
    daily_profit_target = 0.0280 # +2.80% profit lock
    
    sl_mult = 1.50
    tp_ratio = 0.55
    
    all_trades = []
    daily_results = []
    
    for d_idx in range(1, len(common_dates)):
        prev_d = common_dates[d_idx - 1]
        curr_d = common_dates[d_idx]
        
        day_pnl_pct = 0.0
        day_trades = 0
        day_stopped = False
        
        # Test sessions: London first, then New York
        for session_name in ['lon', 'ny']:
            if day_stopped:
                break
                
            # Evaluate both assets in this session: Gold first, then Nasdaq
            for asset in [gold, nas]:
                if day_stopped:
                    break
                    
                sym = asset['symbol']
                spread = asset['spread']
                vpocs = asset['vpocs']
                sessions = asset['sessions']
                
                if prev_d not in vpocs or curr_d not in sessions:
                    continue
                    
                prev_poc = vpocs[prev_d]
                bars = sessions[curr_d][session_name]
                if not bars:
                    continue
                    
                # Dynamic asymmetric risk for this trade
                if day_trades == 0:
                    trade_risk = base_risk
                elif day_pnl_pct > 0:
                    trade_risk = base_risk * 0.833 # Risk 1.25% after profit
                else:
                    trade_risk = base_risk * 0.50  # Risk 0.75% after loss (defense)
                    
                pos = None
                
                for i in range(len(bars)):
                    r = bars[i]
                    high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
                    atr, delta = r['atr'], r['delta']
                    
                    # Manage position
                    if pos is not None:
                        if pos['type'] == 'BUY':
                            if low_p <= pos['sl']:
                                pnl_pct = -trade_risk
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                pos['exit_time'] = r['datetime']
                                all_trades.append(pos)
                                day_trades += 1
                                pos = None
                                break # 1 trade per asset per session
                            elif high_p >= pos['tp']:
                                pnl_pct = trade_risk * pos['rr']
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                pos['exit_time'] = r['datetime']
                                all_trades.append(pos)
                                day_trades += 1
                                pos = None
                                break # 1 trade per asset per session
                        elif pos['type'] == 'SELL':
                            if high_p >= pos['sl']:
                                pnl_pct = -trade_risk
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                pos['exit_time'] = r['datetime']
                                all_trades.append(pos)
                                day_trades += 1
                                pos = None
                                break
                            elif low_p <= pos['tp']:
                                pnl_pct = trade_risk * pos['rr']
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                pos['exit_time'] = r['datetime']
                                all_trades.append(pos)
                                day_trades += 1
                                pos = None
                                break
                                
                    # Entry
                    if pos is None and i >= 3:
                        full_dist = abs(close_p - prev_poc)
                        if 0.45 * atr <= full_dist <= 2.2 * atr:
                            sl_dist = sl_mult * atr
                            tp_dist = tp_ratio * full_dist
                            rr = tp_dist / sl_dist
                            
                            if close_p < prev_poc and delta > 0 and close_p > open_p:
                                pos = {
                                    'symbol': sym, 'session': session_name.upper(), 'type': 'BUY',
                                    'date': curr_d, 'entry': close_p + spread, 'sl': close_p - sl_dist,
                                    'tp': close_p + tp_dist, 'rr': rr, 'entry_time': r['datetime']
                                }
                            elif close_p > prev_poc and delta < 0 and close_p < open_p:
                                pos = {
                                    'symbol': sym, 'session': session_name.upper(), 'type': 'SELL',
                                    'date': curr_d, 'entry': close_p, 'sl': close_p + sl_dist,
                                    'tp': close_p - tp_dist, 'rr': rr, 'entry_time': r['datetime']
                                }
                                
                    # Daily Circuit Breakers
                    if day_pnl_pct <= daily_loss_limit or day_pnl_pct >= daily_profit_target:
                        day_stopped = True
                        break
                        
                # End of session square off
                if pos is not None:
                    pnl = (pos['tp'] - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - pos['tp'])
                    pnl_pct = (trade_risk * pos['rr'] * 0.5) if pnl > 0 else -trade_risk
                    pos['pnl_pct'] = pnl_pct
                    pos['exit_time'] = bars[-1]['datetime']
                    day_pnl_pct += pnl_pct
                    all_trades.append(pos)
                    day_trades += 1
                    pos = None
                    
                if day_pnl_pct <= daily_loss_limit or day_pnl_pct >= daily_profit_target:
                    day_stopped = True
                    break
                    
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
    
    print("=" * 95, flush=True)
    print("      MULTI-ASSET DUAL-SESSION HISTORICAL SIMULATION RESULTS      ", flush=True)
    print("=" * 95, flush=True)
    print(f"Total Historical Trades Executed : {tot_trades:,}")
    print(f"Overall Historical Win Rate      : {tot_wr:.2f}%")
    for s in ['XAUUSD', 'NAS100.r']:
        sub = df_trades[df_trades['symbol'] == s]
        print(f"   {s:<10}: {len(sub):5,d} Trades | Win Rate: {(sub['pnl_pct'] > 0).mean()*100:.2f}%")
    print("=" * 95 + "\n", flush=True)
    
    # 1. YEAR-BY-YEAR TABLE
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
    print(f"AVERAGE ANNUAL RETURN : {df_ann['roi'].mean():+6.2f}% (Median: {df_ann['roi'].median():+6.2f}%)")
    print(f"AVERAGE ANNUAL MAX DD : {df_ann['max_dd'].mean():6.2f}% (Worst Year Max DD: {df_ann['max_dd'].max():6.2f}%)")
    print(f"PROFITABLE YEARS      : {(df_ann['roi'] > 0).mean()*100:.1f}% ({sum(df_ann['roi'] > 0)} / {len(df_ann)} Years)")
    print("=" * 95 + "\n")
    
    # 2. COMPLETE MONTH-BY-MONTH DATASET
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
    csv_file = "d:/Expermental/math_model/multi_asset_dual_session_months.csv"
    df_m.to_csv(csv_file, index=False)
    print(f"Saved complete monthly table ({len(df_m)} months) to: {csv_file}\n", flush=True)
    
    # 3. PRINT ALL MONTHS IN CLEAN CHUNKS
    print("=" * 95)
    print("                     COMPLETE MONTH-BY-MONTH RECORD (ALL YEARS)                     ")
    print("=" * 95)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Max Daily DD':<13} | {'Max Total DD':<13} | {'Status'}")
    print("-" * 95)
    for _, row in df_m.iterrows():
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
    run_full_multi_asset_forward_test()

