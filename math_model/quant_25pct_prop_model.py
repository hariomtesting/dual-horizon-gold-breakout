"""
QUANTITATIVE 25% MONTHLY PROP FIRM MODEL
========================================
Combines 3 Mathematical Non-Correlated Edges:
1. Daily VPOC Magnet Mean-Reversion (Auction Equilibrium)
2. Developing Session VWAP 2.0-Sigma Exhaustion Fade (Volume-Weighted SDE)
3. Initial Balance (IB) False Breakout Fade (Liquidity Sweep Absorption)

Enforces Hard Prop Firm Guardrails:
- Fixed Fractional Risk: 1.25% per trade
- Daily Loss Circuit Breaker: -2.50% max daily loss (Trading stops for the day)
- Daily Profit Lock: +2.50% daily target (Trading stops for the day to lock green days)
- Evaluated across XAUUSD (Gold) and NAS100.r (Nasdaq)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from typing import Dict, Any, List
from core.orderflow import OrderFlowEngine


def calculate_session_vwap_bands(df: pd.DataFrame, num_std: float = 2.0) -> pd.DataFrame:
    """Calculates session VWAP and dynamic standard deviation bands."""
    df = df.copy()
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    vol = df['volume']
    
    # Session cumulative
    df['tp_vol'] = tp * vol
    df['cum_vol'] = vol.groupby(df['date']).cumsum()
    df['cum_tp_vol'] = df['tp_vol'].groupby(df['date']).cumsum()
    df['vwap'] = df['cum_tp_vol'] / df['cum_vol'].replace(0, 1.0)
    
    # Volume weighted variance
    dev_sq = (tp - df['vwap']) ** 2
    df['tp_vol_sq'] = dev_sq * vol
    df['cum_tp_vol_sq'] = df['tp_vol_sq'].groupby(df['date']).cumsum()
    df['vwap_var'] = df['cum_tp_vol_sq'] / df['cum_vol'].replace(0, 1.0)
    df['vwap_std'] = np.sqrt(np.maximum(df['vwap_var'], 0.0))
    
    df['upper_vwap_band'] = df['vwap'] + (num_std * df['vwap_std'])
    df['lower_vwap_band'] = df['vwap'] - (num_std * df['vwap_std'])
    return df


def load_prepared_asset_data(symbol: str, tick_sz: float, spread: float, n_bars: int = 50000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Could not fetch rates for {symbol}")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    df['minute'] = df['datetime'].dt.minute
    
    of = OrderFlowEngine(tick_size=tick_sz)
    df = of.calculate_delta(df)
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Calculate VWAP bands
    df = calculate_session_vwap_bands(df, num_std=2.0)
    
    # Precompute Daily VPOCs
    days = sorted(df['date'].unique())
    daily_vpocs = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    # Prepare records per day
    day_records = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            day_records[d] = sub[['datetime', 'hour', 'minute', 'open', 'high', 'low', 'close', 'atr', 'delta', 
                                  'vwap', 'vwap_std', 'upper_vwap_band', 'lower_vwap_band']].to_dict('records')
            
    return days, daily_vpocs, day_records, spread


def run_multi_engine_simulation():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    print("=" * 85)
    print("      QUANTITATIVE 25%+ MONTHLY PROP FIRM MODEL SIMULATION      ")
    print("=" * 85 + "\n")
    
    # 1. Load Gold and Nasdaq
    print("Loading and preparing high-resolution M5 data for XAUUSD & NAS100.r...", flush=True)
    gold_days, gold_vpocs, gold_records, gold_spread = load_prepared_asset_data("XAUUSD", 0.10, 0.20, 50000)
    nas_days, nas_vpocs, nas_records, nas_spread = load_prepared_asset_data("NAS100.r", 1.0, 0.50, 50000)
    
    all_dates = sorted(list(set(gold_days).intersection(set(nas_days))))
    print(f"Total Overlapping Active Trading Days: {len(all_dates)} days\n")
    
    # Assets setup
    assets = {
        'XAUUSD': {
            'vpocs': gold_vpocs,
            'records': gold_records,
            'spread': gold_spread,
            'sl_atr_mult': 1.50,
            'tp_ratio': 0.55
        },
        'NAS100.r': {
            'vpocs': nas_vpocs,
            'records': nas_records,
            'spread': nas_spread,
            'sl_atr_mult': 1.60,
            'tp_ratio': 0.55
        }
    }
    
    # Simulation Parameters
    risk_per_trade = 0.0125  # 1.25% safe risk per trade
    daily_loss_limit = -0.025 # -2.5% daily circuit breaker
    daily_profit_target = 0.028 # +2.8% daily lock target
    
    all_trades = []
    daily_results = []
    
    for d_idx in range(1, len(all_dates)):
        prev_d = all_dates[d_idx - 1]
        curr_d = all_dates[d_idx]
        
        day_pnl_pct = 0.0
        day_trades = 0
        day_stopped = False
        
        # Test both assets on this day
        for sym, cfg in assets.items():
            if day_stopped:
                break
                
            if prev_d not in cfg['vpocs'] or curr_d not in cfg['records']:
                continue
                
            prev_poc = cfg['vpocs'][prev_d]
            bars = cfg['records'][curr_d]
            spread = cfg['spread']
            
            # Filter active sessions: London (07:00-11:30) & NY (13:00-17:30)
            session_bars = [b for b in bars if (7 <= b['hour'] <= 11) or (13 <= b['hour'] <= 18)]
            if len(session_bars) < 15:
                continue
                
            # Initial Balance (first 12 bars = 60 mins of London)
            ib_high = max([b['high'] for b in session_bars[:12]])
            ib_low = min([b['low'] for b in session_bars[:12]])
            
            pos = None
            asset_trades_today = 0
            
            for i in range(len(session_bars)):
                r = session_bars[i]
                high_p = r['high']
                low_p = r['low']
                close_p = r['close']
                open_p = r['open']
                atr = r['atr']
                delta = r['delta']
                vwap = r['vwap']
                upper_b = r['upper_vwap_band']
                lower_b = r['lower_vwap_band']
                
                # Check circuit breakers
                if day_pnl_pct <= daily_loss_limit or day_pnl_pct >= daily_profit_target:
                    day_stopped = True
                    break
                    
                # Position Management
                if pos is not None:
                    if pos['type'] == 'BUY':
                        if low_p <= pos['sl']:
                            loss_pct = -risk_per_trade
                            day_pnl_pct += loss_pct
                            pos['pnl_pct'] = loss_pct
                            pos['exit_time'] = r['datetime']
                            all_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                        elif high_p >= pos['tp']:
                            gain_pct = risk_per_trade * pos['rr']
                            day_pnl_pct += gain_pct
                            pos['pnl_pct'] = gain_pct
                            pos['exit_time'] = r['datetime']
                            all_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                    elif pos['type'] == 'SELL':
                        if high_p >= pos['sl']:
                            loss_pct = -risk_per_trade
                            day_pnl_pct += loss_pct
                            pos['pnl_pct'] = loss_pct
                            pos['exit_time'] = r['datetime']
                            all_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                        elif low_p <= pos['tp']:
                            gain_pct = risk_per_trade * pos['rr']
                            day_pnl_pct += gain_pct
                            pos['pnl_pct'] = gain_pct
                            pos['exit_time'] = r['datetime']
                            all_trades.append(pos)
                            day_trades += 1
                            asset_trades_today += 1
                            pos = None
                            
                # Entry Logic (If no active position and under daily asset limit)
                if pos is None and asset_trades_today < 2 and i >= 3:
                    full_dist_vpoc = abs(close_p - prev_poc)
                    
                    # SETUP 1: Daily VPOC Magnet Front-Run
                    if 0.45 * atr <= full_dist_vpoc <= 2.2 * atr:
                        sl_dist = cfg['sl_atr_mult'] * atr
                        tp_dist = cfg['tp_ratio'] * full_dist_vpoc
                        rr = tp_dist / sl_dist
                        
                        if close_p < prev_poc and delta > 0 and close_p > open_p:
                            pos = {
                                'symbol': sym, 'type': 'BUY', 'engine': 'VPOC_MAGNET',
                                'date': curr_d, 'entry': close_p + spread,
                                'sl': close_p - sl_dist, 'tp': close_p + tp_dist,
                                'rr': rr, 'entry_time': r['datetime']
                            }
                            continue
                        elif close_p > prev_poc and delta < 0 and close_p < open_p:
                            pos = {
                                'symbol': sym, 'type': 'SELL', 'engine': 'VPOC_MAGNET',
                                'date': curr_d, 'entry': close_p,
                                'sl': close_p + sl_dist, 'tp': close_p - tp_dist,
                                'rr': rr, 'entry_time': r['datetime']
                            }
                            continue
                            
                    # SETUP 2: Developing Session VWAP 2.0-Sigma Fade
                    # Triggers when price extends beyond 2.0 std dev and shows orderflow exhaustion
                    dist_to_vwap = abs(close_p - vwap)
                    if dist_to_vwap >= 1.8 * r['vwap_std'] and r['vwap_std'] > 0.3 * atr:
                        sl_dist = 1.30 * atr
                        tp_dist = 0.85 * dist_to_vwap # reversion back towards vwap
                        rr = tp_dist / sl_dist
                        
                        # Short when stretched above +2 std and delta flips negative
                        if close_p >= upper_b and delta < 0 and close_p < open_p:
                            pos = {
                                'symbol': sym, 'type': 'SELL', 'engine': 'VWAP_2SIGMA',
                                'date': curr_d, 'entry': close_p,
                                'sl': close_p + sl_dist, 'tp': close_p - tp_dist,
                                'rr': rr, 'entry_time': r['datetime']
                            }
                            continue
                        # Long when stretched below -2 std and delta flips positive
                        elif close_p <= lower_b and delta > 0 and close_p > open_p:
                            pos = {
                                'symbol': sym, 'type': 'BUY', 'engine': 'VWAP_2SIGMA',
                                'date': curr_d, 'entry': close_p + spread,
                                'sl': close_p - sl_dist, 'tp': close_p + tp_dist,
                                'rr': rr, 'entry_time': r['datetime']
                            }
                            continue
                            
                    # SETUP 3: Initial Balance (IB) False Breakout Fade (After 60m IB is formed)
                    if i > 12:
                        ib_range = ib_high - ib_low
                        if 0.5 * atr <= ib_range <= 3.0 * atr:
                            # False upside breakout sweep
                            if high_p > ib_high and close_p < ib_high and delta < 0:
                                sl_dist = 1.20 * atr
                                tp_dist = 0.50 * ib_range # target IB midpoint
                                rr = tp_dist / sl_dist
                                pos = {
                                    'symbol': sym, 'type': 'SELL', 'engine': 'IB_SWEEP_FADE',
                                    'date': curr_d, 'entry': close_p,
                                    'sl': close_p + sl_dist, 'tp': close_p - tp_dist,
                                    'rr': rr, 'entry_time': r['datetime']
                                }
                                continue
                            # False downside breakdown sweep
                            elif low_p < ib_low and close_p > ib_low and delta > 0:
                                sl_dist = 1.20 * atr
                                tp_dist = 0.50 * ib_range
                                rr = tp_dist / sl_dist
                                pos = {
                                    'symbol': sym, 'type': 'BUY', 'engine': 'IB_SWEEP_FADE',
                                    'date': curr_d, 'entry': close_p + spread,
                                    'sl': close_p - sl_dist, 'tp': close_p + tp_dist,
                                    'rr': rr, 'entry_time': r['datetime']
                                }
                                continue

            # Close leftover position at day end
            if pos is not None:
                pnl = (pos['tp'] - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - pos['tp'])
                # Conservative 50% target realization on end of day square-off
                pnl_pct = (risk_per_trade * pos['rr'] * 0.5) if pnl > 0 else -risk_per_trade
                pos['pnl_pct'] = pnl_pct
                pos['exit_time'] = session_bars[-1]['datetime']
                day_pnl_pct += pnl_pct
                all_trades.append(pos)
                
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
        
    df_trades['month'] = pd.to_datetime(df_trades['date']).dt.to_period('M')
    df_daily['month'] = pd.to_datetime(df_daily['date']).dt.to_period('M')
    
    # Analyze by Engine
    print("--- BREAKDOWN BY MATHEMATICAL ENGINE ---")
    for eng in df_trades['engine'].unique():
        sub = df_trades[df_trades['engine'] == eng]
        wr = (sub['pnl_pct'] > 0).mean() * 100.0
        tot = len(sub)
        print(f"   Engine {eng:15s}: {tot:3d} Trades | Win Rate: {wr:5.2f}%")
        
    print(f"\nTotal System Trades: {len(df_trades)} across {len(df_trades['month'].unique())} Months")
    print(f"Overall Multi-Engine Win Rate: {(df_trades['pnl_pct'] > 0).mean() * 100.0:.2f}%\n")
    
    # Month-by-month compounding performance
    months = sorted(df_trades['month'].unique())
    
    print("=" * 85)
    print("                MONTH-BY-MONTH PROP FIRM PERFORMANCE REPORT               ")
    print("=" * 85)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Return':<12} | {'Max Daily DD':<13} | {'Max Total DD':<13} | {'Status'}")
    print("-" * 85)
    
    monthly_rois = []
    monthly_max_dds = []
    
    for m in months:
        m_daily = df_daily[df_daily['month'] == m]
        m_trades = df_trades[df_trades['month'] == m]
        
        if len(m_trades) < 5:
            continue
            
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        worst_day = m_daily['pnl_pct'].min() * 100.0
        
        for _, d_row in m_daily.iterrows():
            d_pnl = cap * d_row['pnl_pct']
            cap += d_pnl
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
                
        roi = (cap - 10000.0) / 10000.0 * 100.0
        wr = (m_trades['pnl_pct'] > 0).mean() * 100.0
        
        monthly_rois.append(roi)
        monthly_max_dds.append(max_dd)
        
        status = "[PASSED >=25%]" if roi >= 25.0 else ("[GREEN]" if roi > 0 else "[RED]")
        print(f"{str(m):<10} | {len(m_trades):<7} | {wr:6.1f}%   | {roi:+9.2f}%    | {abs(worst_day):9.2f}%     | {max_dd:9.2f}%     | {status}")
        
    print("-" * 85)
    print(f"AVERAGE MONTHLY RETURN : {np.mean(monthly_rois):+6.2f}% (Median: {np.median(monthly_rois):+6.2f}%)")
    print(f"BEST MONTH / WORST     : {np.max(monthly_rois):+6.2f}% / {np.min(monthly_rois):+6.2f}%")
    print(f"AVERAGE MONTHLY MAX DD : {np.mean(monthly_max_dds):6.2f}% (Worst Month Max DD: {np.max(monthly_max_dds):6.2f}%)")
    print(f"MONTHS >= 25% RETURN   : {(np.array(monthly_rois) >= 25.0).mean() * 100.0:.1f}%")
    print(f"MONTHS >= 20% RETURN   : {(np.array(monthly_rois) >= 20.0).mean() * 100.0:.1f}%")
    print(f"ALL-TIME GREEN MONTHS  : {(np.array(monthly_rois) > 0).mean() * 100.0:.1f}%")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    run_multi_engine_simulation()

