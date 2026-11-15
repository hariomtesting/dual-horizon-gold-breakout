"""
ATR PARAMETER SENSITIVITY GRID SEARCH: LONDON OPEN STRADDLE
============================================================
Evaluates:
1. Entry Trigger Distance: [0.3x, 0.4x, 0.5x, 0.6x, 0.7x, 0.8x] ATR
2. Stop Loss Distance    : [0.6x, 0.8x, 1.0x] ATR
3. Take Profit Distance  : [1.2x, 1.5x, 1.8x, 2.0x, 2.4x] ATR (Risk:Reward from 1.5:1 to 3.0:1)
4. ATR Calculation Period: [7, 14, 21]

Runs on Gold (XAUUSD) & EURUSD strictly within 11:00 AM - 4:00 PM IST (15:55 IST square-off).
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def run_atr_calibration():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    # Pre-extract data
    asset_dfs = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['tr'] = tr
        asset_dfs[s] = df
        
    print("=" * 95, flush=True)
    print("           ATR PARAMETER SENSITIVITY GRID SEARCH (XAUUSD + EURUSD)           ", flush=True)
    print("=" * 95 + "\n", flush=True)
    
    # Test ATR Periods: 7, 14, 21
    # Test Entry Offsets: 0.3, 0.4, 0.5, 0.6, 0.8
    # Test SL Multipliers: 0.6, 0.8, 1.0
    # Test TP Multipliers: 1.2, 1.6, 2.0, 2.4
    
    results = []
    
    test_grid = [
        # (atr_period, entry_offset, sl_mult, tp_mult)
        (7, 0.4, 0.8, 1.6),
        (7, 0.5, 0.8, 1.6),
        (7, 0.6, 0.8, 1.6),
        (14, 0.3, 0.8, 1.6),
        (14, 0.4, 0.8, 1.6),
        (14, 0.5, 0.6, 1.5),
        (14, 0.5, 0.8, 1.6), # Baseline
        (14, 0.5, 0.8, 2.0),
        (14, 0.5, 1.0, 2.0),
        (14, 0.6, 0.8, 1.6),
        (14, 0.6, 0.8, 2.0),
        (14, 0.7, 0.8, 2.0),
        (14, 0.8, 0.8, 2.0),
        (21, 0.4, 0.8, 1.6),
        (21, 0.5, 0.8, 1.6),
        (21, 0.6, 0.8, 2.0),
    ]
    
    for atr_period, entry_offset, sl_mult, tp_mult in test_grid:
        rr = tp_mult / sl_mult
        
        # Build sessions for each asset with this ATR period
        asset_sessions = {}
        for s in symbols:
            df = asset_dfs[s].copy()
            df['atr'] = df['tr'].rolling(atr_period, min_periods=5).mean().bfill()
            df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
            sessions = {}
            for d, sub in df_ist.groupby('date'):
                if len(sub) >= 30:
                    sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
            asset_sessions[s] = sessions
            
        all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_sessions.values()])))
        
        portfolio_trades = []
        portfolio_daily = []
        risk_per_trade = 0.010 # 1.0%
        
        for curr_d in all_dates:
            day_pnl = 0.0
            
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
                    
                    if r['ist_hour'] == 15 and r['ist_minute'] >= 55:
                        if pos is not None:
                            pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                            pnl = (pts / pos['sl_dist']) * risk_per_trade
                            day_pnl += pnl
                            portfolio_trades.append({'pnl_pct': pnl, 'year': curr_d.year, 'month': curr_d.month})
                            pos = None
                        break
                        
                    if pos is not None:
                        if pos['type'] == 'BUY':
                            if l <= pos['sl']:
                                pnl = -risk_per_trade
                                day_pnl += pnl
                                portfolio_trades.append({'pnl_pct': pnl, 'year': curr_d.year, 'month': curr_d.month})
                                pos = None
                                break
                            elif h >= pos['tp']:
                                pnl = risk_per_trade * rr
                                day_pnl += pnl
                                portfolio_trades.append({'pnl_pct': pnl, 'year': curr_d.year, 'month': curr_d.month})
                                pos = None
                                break
                        elif pos['type'] == 'SELL':
                            if h >= pos['sl']:
                                pnl = -risk_per_trade
                                day_pnl += pnl
                                portfolio_trades.append({'pnl_pct': pnl, 'year': curr_d.year, 'month': curr_d.month})
                                pos = None
                                break
                            elif l <= pos['tp']:
                                pnl = risk_per_trade * rr
                                day_pnl += pnl
                                portfolio_trades.append({'pnl_pct': pnl, 'year': curr_d.year, 'month': curr_d.month})
                                pos = None
                                break
                    else:
                        if h >= buy_trigger:
                            sl_dist = sl_mult * atr_base
                            tp_dist = tp_mult * atr_base
                            pos = {
                                'type': 'BUY', 'entry': buy_trigger,
                                'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist,
                                'sl_dist': sl_dist
                            }
                        elif l <= sell_trigger:
                            sl_dist = sl_mult * atr_base
                            tp_dist = tp_mult * atr_base
                            pos = {
                                'type': 'SELL', 'entry': sell_trigger,
                                'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist,
                                'sl_dist': sl_dist
                            }
                            
            portfolio_daily.append({'date': curr_d, 'year': curr_d.year, 'pnl_pct': day_pnl})
            
        df_t = pd.DataFrame(portfolio_trades)
        df_d = pd.DataFrame(portfolio_daily)
        
        wr = (df_t['pnl_pct'] > 0).mean() * 100.0 if len(df_t) > 0 else 0
        
        # Calculate yearly ROIs & Max DD
        annual_rois = []
        for y, y_sub in df_d.groupby('year'):
            cap = 10000.0
            for _, r in y_sub.iterrows():
                cap += cap * r['pnl_pct']
            annual_rois.append((cap - 10000.0) / 10000.0 * 100.0)
            
        # Overall Max DD
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, r in df_d.iterrows():
            cap += cap * r['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
                
        avg_annual_roi = np.mean(annual_rois)
        green_years = sum(1 for r in annual_rois if r > 0)
        tot_years = len(annual_rois)
        
        cfg_name = f"ATR({atr_period}) Entry={entry_offset:.1f}x SL={sl_mult:.1f}x TP={tp_mult:.1f}x (RR {rr:.1f}:1)"
        results.append({
            'config': cfg_name,
            'trades': len(df_t),
            'win_rate': wr,
            'avg_annual_roi': avg_annual_roi,
            'max_dd': max_dd,
            'green_years': f"{green_years}/{tot_years}"
        })
        
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values('avg_annual_roi', ascending=False).reset_index(drop=True)
    
    print(f"{'Configuration':<52} | {'Trades':<7} | {'Win Rate':<9} | {'Avg Annual ROI':<15} | {'Max DD':<8} | {'Green Years'}")
    print("-" * 108)
    for _, row in df_res.iterrows():
        print(f"{row['config']:<52} | {row['trades']:<7} | {row['win_rate']:6.2f}%  | {row['avg_annual_roi']:+12.2f}%    | {row['max_dd']:6.2f}%  | {row['green_years']}")
    print("=" * 108 + "\n")


if __name__ == "__main__":
    run_atr_calibration()

