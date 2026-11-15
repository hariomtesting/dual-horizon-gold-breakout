"""
COMPREHENSIVE OPTIMIZATION FOR SINGLE SNIPER STRADDLE
Assets: XAUUSD, EURUSD (2022 - 2026)
Focus:
1. Base Anchor Time: 12:00, 12:15, 12:30, 12:45, 13:00, 13:30 IST
2. Order Expiration (Fill Cutoff): 14:00, 14:30, 15:00 IST
3. Entry Offset: 0.25x, 0.30x, 0.35x, 0.40x ATR
4. SL / TP Ratio: SL 0.7-0.9, TP 1.1-1.6 ATR
5. Single-Shot OCO: Delete opposite order when one triggers
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
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate_params(asset_data, anchor_h, anchor_m, cutoff_h, cutoff_m, entry_mult, sl_mult, tp_mult):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    total_trades = 0
    wins = 0
    
    for curr_d in all_dates:
        day_pnl = 0.0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Find base at anchor time
            base_idx = -1
            for i, b in enumerate(bars):
                if b['ist_hour'] == anchor_h and b['ist_minute'] == anchor_m:
                    base_idx = i
                    break
            if base_idx == -1 or base_idx >= len(bars) - 5:
                continue
                
            base_p = bars[base_idx]['close']
            atr_v = bars[base_idx]['atr']
            if atr_v <= 0:
                continue
                
            entry_offset = entry_mult * atr_v
            sl_dist = sl_mult * atr_v
            tp_dist = tp_mult * atr_v
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active = None
            cancelled = False
            
            for b in bars[base_idx+1:]:
                # Auto square off at 15:55 IST
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active is not None:
                        if active['type'] == 'BUY':
                            pnl_r = (b['close'] - active['entry']) / sl_dist
                        else:
                            pnl_r = (active['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        active = None
                    break
                    
                if active is None:
                    # Check order expiration cutoff
                    if not cancelled and (b['ist_hour'] > cutoff_h or (b['ist_hour'] == cutoff_h and b['ist_minute'] >= cutoff_m)):
                        cancelled = True
                        break # No entry after cutoff
                        
                    if not cancelled:
                        if b['high'] >= buy_trigger:
                            active = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
                        elif b['low'] <= sell_trigger:
                            active = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
                else:
                    if active['type'] == 'BUY':
                        if b['high'] >= active['tp']:
                            day_pnl += (tp_mult / sl_mult) * 0.010
                            total_trades += 1
                            wins += 1
                            active = None
                            break
                        elif b['low'] <= active['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            active = None
                            break
                    else: # SELL
                        if b['low'] <= active['tp']:
                            day_pnl += (tp_mult / sl_mult) * 0.010
                            total_trades += 1
                            wins += 1
                            active = None
                            break
                        elif b['high'] >= active['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            active = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_res = pd.DataFrame(portfolio_daily)
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['month'] = pd.to_datetime(df_res['date']).dt.to_period('M')
    
    monthly = df_res.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_res.groupby('year')['pnl_pct'].sum() * 100.0
    
    green_cnt = (monthly > 0).sum()
    total_cnt = len(monthly)
    overall_green_pct = (green_cnt / total_cnt) * 100.0 if total_cnt > 0 else 0
    
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    total_2026 = len(m_2026)
    
    # Max Drawdown
    cum = (1.0 + df_res['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_cnt}/{total_cnt} ({overall_green_pct:.1f}%)",
        'overall_green_pct': overall_green_pct,
        'green_2026': f"{green_2026}/{total_2026} ({green_2026/total_2026*100:.1f}%)",
        'green_2026_cnt': green_2026,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def run_grid():
    print("Loading historical M5 data for XAUUSD & EURUSD...")
    data = load_data()
    print("Data loaded successfully. Starting grid optimization...\n")
    
    grid = [
        # (Anchor, Cutoff, Entry, SL, TP)
        # Baseline
        ((12, 30), (15, 30), 0.35, 0.80, 1.60, "Baseline (12:30 Anchor, No Cutoff, 0.35 Entry, 0.8 SL, 1.6 TP)"),
        
        # Test Cutoff Times (avoid entering late afternoon chop)
        ((12, 30), (14, 30), 0.35, 0.80, 1.60, "Cutoff 14:30 IST (Cancel unfilled orders at 2:30 PM)"),
        ((12, 30), (14, 00), 0.35, 0.80, 1.60, "Cutoff 14:00 IST (Cancel unfilled orders at 2:00 PM)"),
        ((12, 30), (15, 00), 0.35, 0.80, 1.60, "Cutoff 15:00 IST (Cancel unfilled orders at 3:00 PM)"),
        
        # Test Anchor Times (12:00, 12:15, 12:45, 13:00)
        ((12, 15), (14, 30), 0.35, 0.80, 1.60, "Anchor 12:15 IST (Early Base) + 14:30 Cutoff"),
        ((12, 45), (14, 30), 0.35, 0.80, 1.60, "Anchor 12:45 IST (Later Base) + 14:30 Cutoff"),
        ((13, 00), (15, 00), 0.35, 0.80, 1.60, "Anchor 13:00 IST (Pre-London Open Base)"),
        
        # Test Target Asymmetry (TP = 1.2, 1.3, 1.4, 1.5 with 14:30 Cutoff)
        ((12, 30), (14, 30), 0.35, 0.80, 1.20, "12:30 Anchor, Cutoff 14:30, TP=1.2 (1.5:1 R:R)"),
        ((12, 30), (14, 30), 0.35, 0.80, 1.30, "12:30 Anchor, Cutoff 14:30, TP=1.3 (1.63:1 R:R)"),
        ((12, 30), (14, 30), 0.35, 0.80, 1.40, "12:30 Anchor, Cutoff 14:30, TP=1.4 (1.75:1 R:R)"),
        ((12, 30), (14, 30), 0.35, 0.75, 1.35, "12:30 Anchor, Cutoff 14:30, SL=0.75, TP=1.35 (1.8:1 R:R)"),
        ((12, 30), (14, 30), 0.30, 0.75, 1.35, "12:30 Anchor, Cutoff 14:30, Entry=0.30, SL=0.75, TP=1.35"),
        ((12, 30), (14, 30), 0.30, 0.80, 1.40, "12:30 Anchor, Cutoff 14:30, Entry=0.30, SL=0.80, TP=1.40"),
        
        # Anchor 13:00 with Cutoff 15:00
        ((13, 00), (15, 00), 0.30, 0.75, 1.35, "Anchor 13:00 IST, Cutoff 15:00, Entry=0.30, SL=0.75, TP=1.35"),
        ((13, 00), (15, 00), 0.35, 0.80, 1.40, "Anchor 13:00 IST, Cutoff 15:00, Entry=0.35, SL=0.80, TP=1.40"),
    ]
    
    results = []
    for anchor, cutoff, ent, sl, tp, label in grid:
        res = evaluate_params(data, anchor[0], anchor[1], cutoff[0], cutoff[1], ent, sl, tp)
        res['label'] = label
        results.append(res)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}%")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)
        
    # Sort by consistency (overall green pct, then 2026 green, then avg annual ROI)
    results.sort(key=lambda x: (x['overall_green_pct'], x['green_2026_cnt'], x['avg_annual']), reverse=True)
    
    print("\n" + "=" * 90)
    print("                      TOP 3 OPTIMIZED CONFIGURATIONS                      ")
    print("=" * 90)
    for i, r in enumerate(results[:3], 1):
        print(f"\nRANK #{i}: {r['label']}")
        print(f"  * Overall Consistency: {r['overall_green']}")
        print(f"  * 2026 Consistency   : {r['green_2026']}")
        print(f"  * Avg Annual ROI     : +{r['avg_annual']:.2f}% (Worst Year: +{r['min_year']:.2f}%)")
        print(f"  * Win Rate / Max DD  : {r['win_rate']:.1f}% Win Rate | {r['max_dd']:.2f}% Max DD")
        print(f"  * Yearly Walk        : " + ", ".join([f"{y}: +{v:.1f}%" for y, v in r['yearly'].items()]))

if __name__ == '__main__':
    run_grid()

