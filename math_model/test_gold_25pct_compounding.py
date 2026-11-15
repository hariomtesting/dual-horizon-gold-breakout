"""
GOLD 25% MONTHLY ACCELERATOR & PROP FIRM EVALUATOR
=================================================
Optimizes:
1. Dual Session Execution: London Session (07:00-11:00 UTC) + NY Session (13:00-17:30 UTC)
2. Asymmetric Daily Risk Management (Anti-Martingale):
   - Base Risk: 1.50%
   - After Win on Day: Risk 1.25% (protects day's gain)
   - After Loss on Day: Risk 0.75% (prevents reaching 4.5% daily limit; max daily loss = -2.25%)
   - Daily Target Lock: Once daily PnL >= +2.50%, lock all profits and stop for the day!
3. Target Ratio Grid on Gold: 0.55, 0.75, 1.00 (full VPOC reversion) with Breakeven trigger.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_gold_25pct_simulation():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50000)
    if rates is None or len(rates) == 0:
        raise RuntimeError("Failed to load rates")
        
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    
    spread = 0.20
    of = OrderFlowEngine(tick_size=0.10)
    df = of.calculate_delta(df)
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    days = sorted(df['date'].unique())
    daily_vpocs = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_vpocs[d] = vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            
    day_sessions = []
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        if prev_d not in daily_vpocs:
            continue
        prev_poc = daily_vpocs[prev_d]
        df_day = df[df['date'] == curr_d]
        
        # Split into London (07:00-11:30) and New York (13:00-17:30)
        lon_sub = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 11)].reset_index(drop=True)
        ny_sub = df_day[(df_day['hour'] >= 13) & (df_day['hour'] <= 17)].reset_index(drop=True)
        
        lon_recs = lon_sub[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records') if len(lon_sub) >= 15 else []
        ny_recs = ny_sub[['datetime', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records') if len(ny_sub) >= 15 else []
        
        if lon_recs or ny_recs:
            day_sessions.append((curr_d, prev_poc, lon_recs, ny_recs))
            
    print("=" * 85)
    print("      GOLD 25% MONTHLY ACCELERATOR & PROP FIRM SIMULATION      ")
    print(f"      Total Evaluated Trading Days: {len(day_sessions)}      ")
    print("=" * 85 + "\n")
    
    # Test configurations of Target Distance & Breakeven
    test_configs = [
        {'name': 'Champion Original (TP 0.55x, SL 1.50x, Base 1.5%)', 'tp_ratio': 0.55, 'sl_mult': 1.50, 'be': False, 'base_risk': 0.015},
        {'name': 'Dual-Session Aggressive (TP 0.65x, SL 1.40x, Base 1.75%)', 'tp_ratio': 0.65, 'sl_mult': 1.40, 'be': False, 'base_risk': 0.0175},
        {'name': 'Dual-Session High-Target (TP 0.85x, SL 1.50x, BE Active, Base 2.0%)', 'tp_ratio': 0.85, 'sl_mult': 1.50, 'be': True, 'base_risk': 0.020},
        {'name': 'Pure 25% Prop Master (TP 0.70x, SL 1.50x, Base 2.0% Asymmetric)', 'tp_ratio': 0.70, 'sl_mult': 1.50, 'be': False, 'base_risk': 0.020}
    ]
    
    for cfg in test_configs:
        print(f"\n{'-'*85}")
        print(f"CONFIGURATION: {cfg['name']}")
        print(f"{'-'*85}")
        
        all_trades = []
        daily_results = []
        
        base_risk = cfg['base_risk']
        tp_ratio = cfg['tp_ratio']
        sl_mult = cfg['sl_mult']
        use_be = cfg['be']
        
        for curr_d, prev_poc, lon_recs, ny_recs in day_sessions:
            day_pnl_pct = 0.0
            day_trades = 0
            day_stopped = False
            
            # Execute sessions sequentially: London first, then NY
            sessions = [('LONDON', lon_recs), ('NEWYORK', ny_recs)]
            
            for s_name, bars in sessions:
                if day_stopped or not bars:
                    continue
                    
                # Determine asymmetric risk for this trade
                if day_trades == 0:
                    trade_risk = base_risk
                elif day_pnl_pct > 0:
                    trade_risk = base_risk * 0.80 # cushion profits
                else:
                    trade_risk = base_risk * 0.50 # defense mode (stops drawdown)
                    
                pos = None
                session_trade_taken = False
                
                for i in range(len(bars)):
                    r = bars[i]
                    high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
                    atr, delta = r['atr'], r['delta']
                    
                    # Manage active position
                    if pos is not None:
                        # Breakeven check
                        if use_be and not pos['be']:
                            if pos['type'] == 'BUY' and high_p >= pos['entry'] + (pos['tp_dist'] * 0.5):
                                pos['sl'] = pos['entry'] + spread
                                pos['be'] = True
                            elif pos['type'] == 'SELL' and low_p <= pos['entry'] - (pos['tp_dist'] * 0.5):
                                pos['sl'] = pos['entry'] - spread
                                pos['be'] = True
                                
                        if pos['type'] == 'BUY':
                            if low_p <= pos['sl']:
                                pnl_pct = 0.0 if pos.get('be', False) and pos['sl'] >= pos['entry'] else -trade_risk
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                all_trades.append(pos)
                                day_trades += 1
                                session_trade_taken = True
                                pos = None
                                break
                            elif high_p >= pos['tp']:
                                pnl_pct = trade_risk * pos['rr']
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                all_trades.append(pos)
                                day_trades += 1
                                session_trade_taken = True
                                pos = None
                                break
                        elif pos['type'] == 'SELL':
                            if high_p >= pos['sl']:
                                pnl_pct = 0.0 if pos.get('be', False) and pos['sl'] <= pos['entry'] else -trade_risk
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                all_trades.append(pos)
                                day_trades += 1
                                session_trade_taken = True
                                pos = None
                                break
                            elif low_p <= pos['tp']:
                                pnl_pct = trade_risk * pos['rr']
                                day_pnl_pct += pnl_pct
                                pos['pnl_pct'] = pnl_pct
                                all_trades.append(pos)
                                day_trades += 1
                                session_trade_taken = True
                                pos = None
                                break
                                
                    # Entry logic
                    if pos is None and not session_trade_taken and i >= 3:
                        full_dist = abs(close_p - prev_poc)
                        if 0.45 * atr <= full_dist <= 2.2 * atr:
                            sl_dist = sl_mult * atr
                            tp_dist = tp_ratio * full_dist
                            rr = tp_dist / sl_dist
                            
                            if close_p < prev_poc and delta > 0 and close_p > open_p:
                                pos = {
                                    'type': 'BUY', 'session': s_name, 'date': curr_d,
                                    'entry': close_p + spread, 'sl': close_p - sl_dist,
                                    'tp': close_p + tp_dist, 'tp_dist': tp_dist,
                                    'rr': rr, 'be': False, 'entry_time': r['datetime']
                                }
                            elif close_p > prev_poc and delta < 0 and close_p < open_p:
                                pos = {
                                    'type': 'SELL', 'session': s_name, 'date': curr_d,
                                    'entry': close_p, 'sl': close_p + sl_dist,
                                    'tp': close_p - tp_dist, 'tp_dist': tp_dist,
                                    'rr': rr, 'be': False, 'entry_time': r['datetime']
                                }
                                
                    # Circuit breaker triggers
                    if day_pnl_pct >= 0.025: # Profit Lock Target reached
                        day_stopped = True
                        break
                    if day_pnl_pct <= -0.025: # Daily stop loss limit hit (never exceed 2.5% max loss)
                        day_stopped = True
                        break
                        
                # End of session close
                if pos is not None:
                    pnl = (pos['tp'] - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - pos['tp'])
                    pnl_pct = (trade_risk * pos['rr'] * 0.5) if pnl > 0 else -trade_risk
                    pos['pnl_pct'] = pnl_pct
                    day_pnl_pct += pnl_pct
                    all_trades.append(pos)
                    day_trades += 1
                    pos = None
                    
            daily_results.append({
                'date': curr_d,
                'pnl_pct': day_pnl_pct,
                'trades': day_trades
            })
            
        df_trades = pd.DataFrame(all_trades)
        df_daily = pd.DataFrame(daily_results)
        
        df_trades['month'] = pd.to_datetime(df_trades['date']).dt.to_period('M')
        df_daily['month'] = pd.to_datetime(df_daily['date']).dt.to_period('M')
        
        tot_wr = (df_trades['pnl_pct'] > 0).mean() * 100.0
        tot_trades = len(df_trades)
        
        print(f"Total Completed Trades: {tot_trades} | System Win Rate: {tot_wr:5.2f}%\n")
        
        months = sorted(df_trades['month'].unique())
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
            
            status = "[TARGET HIT >=25%]" if roi >= 25.0 else ("[GREEN]" if roi > 0 else "[RED]")
            print(f"{str(m):<10} | {len(m_trades):<7} | {wr:6.1f}%   | {roi:+9.2f}%    | {abs(worst_day):9.2f}%     | {max_dd:9.2f}%     | {status}")
            
        print("-" * 85)
        print(f"AVERAGE MONTHLY RETURN : {np.mean(monthly_rois):+6.2f}% (Median: {np.median(monthly_rois):+6.2f}%)")
        print(f"BEST MONTH / WORST     : {np.max(monthly_rois):+6.2f}% / {np.min(monthly_rois):+6.2f}%")
        print(f"AVERAGE MONTHLY MAX DD : {np.mean(monthly_max_dds):6.2f}% (Worst Month Max DD: {np.max(monthly_max_dds):6.2f}%)")
        print(f"MONTHS >= 25% RETURN   : {(np.array(monthly_rois) >= 25.0).mean() * 100.0:.1f}%")
        print(f"ALL-TIME GREEN MONTHS  : {(np.array(monthly_rois) > 0).mean() * 100.0:.1f}%")


if __name__ == "__main__":
    run_gold_25pct_simulation()

