"""
CONSISTENCY ENHANCER EXPERIMENT
Investigating how to turn 8/11 into 10/11 or 11/11 (>=90% green months):
1. Partial Profit Taking: Bank 50% at +1.0R, hold 50% to +2.0R
2. Asian Range Compression Filter: Only take straddle when Asian Range (06:00-12:30 IST) is compressed (< 1.5x ATR)
3. Target Asymmetry Calibration: Testing TP = 1.2x, 1.4x, 1.6x, 1.8x, 2.0x
4. Asian Session High/Low Breakout vs Base Straddle
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
    
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
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
        
        # Keep full day from 06:00 to 16:00 for Asian range analysis
        df_full = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_full.groupby('date'):
            if len(sub) >= 40:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
        asset_data[s] = sessions
    return asset_data

def run_variant(asset_data, tp_ratio=1.6, sl_ratio=0.8, partial_tp=False, max_asian_ratio=None):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Asian bars: 06:00 to 12:30
            asian_bars = [b for b in bars if b['ist_hour'] < 12 or (b['ist_hour'] == 12 and b['ist_minute'] <= 30)]
            post_bars = [b for b in bars if b['ist_hour'] > 12 or (b['ist_hour'] == 12 and b['ist_minute'] > 30)]
            
            if len(asian_bars) < 20 or len(post_bars) < 20:
                continue
                
            base_b = asian_bars[-1]
            base_p = base_b['close']
            atr_v = base_b['atr']
            if atr_v <= 0:
                continue
                
            # Asian range
            asian_high = max([b['high'] for b in asian_bars])
            asian_low = min([b['low'] for b in asian_bars])
            asian_range = asian_high - asian_low
            
            # Filter: If max_asian_ratio set, skip if asian_range > max_asian_ratio * atr
            if max_asian_ratio is not None and asian_range > (max_asian_ratio * atr_v):
                continue
                
            entry_offset = 0.35 * atr_v
            sl_dist = sl_ratio * atr_v
            tp_dist = tp_ratio * atr_v
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active_trade = None
            
            for b in post_bars:
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active_trade is not None:
                        # Close at market
                        if active_trade['type'] == 'BUY':
                            pnl_r = (b['close'] - active_trade['entry']) / sl_dist
                        else:
                            pnl_r = (active_trade['entry'] - b['close']) / sl_dist
                            
                        # If partial TP was already hit, remaining half closed
                        if active_trade.get('partial_hit', False):
                            day_pnl += (0.5 * 1.0 + 0.5 * pnl_r) * 0.010
                        else:
                            day_pnl += pnl_r * 0.010
                        active_trade = None
                    break
                    
                if active_trade is None:
                    if b['high'] >= buy_trigger:
                        active_trade = {
                            'type': 'BUY',
                            'entry': buy_trigger,
                            'sl': buy_trigger - sl_dist,
                            'tp': buy_trigger + tp_dist,
                            'partial_tp': buy_trigger + (1.0 * sl_dist),
                            'partial_hit': False
                        }
                    elif b['low'] <= sell_trigger:
                        active_trade = {
                            'type': 'SELL',
                            'entry': sell_trigger,
                            'sl': sell_trigger + sl_dist,
                            'tp': sell_trigger - tp_dist,
                            'partial_tp': sell_trigger - (1.0 * sl_dist),
                            'partial_hit': False
                        }
                else:
                    ttype = active_trade['type']
                    entry_p = active_trade['entry']
                    sl_p = active_trade['sl']
                    tp_p = active_trade['tp']
                    
                    if partial_tp and not active_trade['partial_hit']:
                        if ttype == 'BUY' and b['high'] >= active_trade['partial_tp']:
                            active_trade['partial_hit'] = True
                            # SL of remaining half moved to BE
                            active_trade['sl'] = entry_p
                        elif ttype == 'SELL' and b['low'] <= active_trade['partial_tp']:
                            active_trade['partial_hit'] = True
                            active_trade['sl'] = entry_p
                            
                    # Check exits
                    if ttype == 'BUY':
                        if b['high'] >= tp_p:
                            if active_trade.get('partial_hit', False):
                                # 50% closed at +1.0R, 50% closed at tp_ratio/sl_ratio
                                day_pnl += (0.5 * 1.0 + 0.5 * (tp_ratio / sl_ratio)) * 0.010
                            else:
                                day_pnl += (tp_ratio / sl_ratio) * 0.010
                            active_trade = None
                            break
                        elif b['low'] <= active_trade['sl']:
                            if active_trade.get('partial_hit', False):
                                # 50% was closed at +1.0R, remaining stopped at BE (0.0R) -> Net +0.5R!
                                day_pnl += 0.5 * 1.0 * 0.010
                            else:
                                day_pnl += -1.0 * 0.010
                            active_trade = None
                            break
                    else: # SELL
                        if b['low'] <= tp_p:
                            if active_trade.get('partial_hit', False):
                                day_pnl += (0.5 * 1.0 + 0.5 * (tp_ratio / sl_ratio)) * 0.010
                            else:
                                day_pnl += (tp_ratio / sl_ratio) * 0.010
                            active_trade = None
                            break
                        elif b['high'] >= active_trade['sl']:
                            if active_trade.get('partial_hit', False):
                                day_pnl += 0.5 * 1.0 * 0.010
                            else:
                                day_pnl += -1.0 * 0.010
                            active_trade = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_res = pd.DataFrame(portfolio_daily)
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['month'] = pd.to_datetime(df_res['date']).dt.to_period('M')
    
    monthly = df_res.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_res.groupby('year')['pnl_pct'].sum() * 100.0
    
    green_months_cnt = (monthly > 0).sum()
    total_months_cnt = len(monthly)
    green_pct = (green_months_cnt / total_months_cnt) * 100.0
    
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    total_2026 = len(m_2026)
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'yearly': yearly.to_dict(),
        'overall_green': f"{green_months_cnt}/{total_months_cnt} ({green_pct:.1f}%)",
        'green_2026': f"{green_2026}/{total_2026} ({green_2026/total_2026*100:.1f}%)",
        'm_2026_series': m_2026.to_dict()
    }

if __name__ == '__main__':
    data = load_data()
    print("="*85)
    print("       TESTING CONSISTENCY ENHANCERS: PARTIAL TP & VOLATILITY FILTERS       ")
    print("="*85)
    
    tests = [
        ("A. Baseline (SL=0.8, TP=1.6, Full Exit)", 1.6, 0.8, False, None),
        ("B. Partial TP (50% at +1.0R, 50% at +2.0R, SL to BE)", 1.6, 0.8, True, None),
        ("C. Tighter Target (SL=0.8, TP=1.2 = 1.5:1 R:R)", 1.2, 0.8, False, None),
        ("D. Partial TP + Asian Compression Filter (Asian < 2.5x ATR)", 1.6, 0.8, True, 2.5),
        ("E. Baseline + Asian Compression Filter (Asian < 2.5x ATR)", 1.6, 0.8, False, 2.5),
        ("F. Baseline + Asian Compression Filter (Asian < 2.0x ATR)", 1.6, 0.8, False, 2.0),
        ("G. Partial TP + Asian Compression Filter (Asian < 2.0x ATR)", 1.6, 0.8, True, 2.0),
    ]
    
    for name, tp_r, sl_r, part, max_as in tests:
        res = run_variant(data, tp_ratio=tp_r, sl_ratio=sl_r, partial_tp=part, max_asian_ratio=max_as)
        print(f"\n--- {name} ---")
        print(f"  Avg Annual ROI  : +{res['avg_annual']:.2f}% (Min Year: +{res['min_year']:.2f}%)")
        print(f"  Overall Green   : {res['overall_green']}")
        print(f"  2026 Green      : {res['green_2026']}")
        print(f"  Yearly Breakdown: " + ", ".join([f"{y}: +{v:.1f}%" if v>0 else f"{y}: {v:.1f}%" for y, v in res['yearly'].items()]))
        print(f"  2026 Monthly    : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026_series'].items()]))

    mt5.shutdown()

