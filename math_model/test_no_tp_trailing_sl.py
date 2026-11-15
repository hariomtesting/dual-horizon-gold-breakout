"""
EXPERIMENT: NO TP & SL TRAILING MECHANICS (2022 - 2026)
Assets: XAUUSD, EURUSD (11:00 - 16:00 IST)
Anchor: 13:00 IST | Cutoff: 15:00 IST | Square-off: 15:55 IST

Tests:
1. Baseline: Fixed SL (0.75x ATR) & Fixed TP (1.35x ATR)
2. No TP: Pure Time Exit at 15:55 IST (Fixed SL 0.75x ATR)
3. No TP + Continuous ATR Trailing (Trail dist = 0.75x ATR)
4. No TP + Continuous ATR Trailing (Trail dist = 1.00x ATR)
5. No TP + Continuous ATR Trailing (Trail dist = 1.25x ATR)
6. No TP + Step-wise R Trail (+1.0R -> BE, +1.5R -> +0.75R, +2.0R -> +1.5R)
7. Wide TP (3.0x ATR = 4:1 R:R) + Step-wise R Trail
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

def simulate_trailing_variant(asset_data, mode="baseline", trail_dist_mult=0.75, fixed_tp_mult=1.35):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    total_trades = 0
    wins = 0
    trade_pnls = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Find base at 13:00 IST
            base_idx = -1
            for i, b in enumerate(bars):
                if b['ist_hour'] == 13 and b['ist_minute'] == 0:
                    base_idx = i
                    break
            if base_idx == -1 or base_idx >= len(bars) - 5:
                continue
                
            base_p = bars[base_idx]['close']
            atr_v = bars[base_idx]['atr']
            if atr_v <= 0:
                continue
                
            entry_offset = 0.30 * atr_v
            sl_dist = 0.75 * atr_v
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active = None
            
            for b in bars[base_idx+1:]:
                # 15:55 IST Auto-square-off
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active is not None:
                        if active['type'] == 'BUY':
                            pnl_r = (b['close'] - active['entry']) / sl_dist
                        else:
                            pnl_r = (active['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        trade_pnls.append(pnl_r)
                        if pnl_r > 0:
                            wins += 1
                        active = None
                    break
                    
                if active is None:
                    # 15:00 IST Order cutoff
                    if b['ist_hour'] >= 15:
                        break
                    if b['high'] >= buy_trigger:
                        active = {
                            'type': 'BUY',
                            'entry': buy_trigger,
                            'sl': buy_trigger - sl_dist,
                            'best_price': buy_trigger,
                            'tp': buy_trigger + (fixed_tp_mult * atr_v) if fixed_tp_mult else None
                        }
                    elif b['low'] <= sell_trigger:
                        active = {
                            'type': 'SELL',
                            'entry': sell_trigger,
                            'sl': sell_trigger + sl_dist,
                            'best_price': sell_trigger,
                            'tp': sell_trigger - (fixed_tp_mult * atr_v) if fixed_tp_mult else None
                        }
                else:
                    ttype = active['type']
                    entry_p = active['entry']
                    
                    # Update best price reached
                    if ttype == 'BUY':
                        if b['high'] > active['best_price']:
                            active['best_price'] = b['high']
                    else:
                        if b['low'] < active['best_price']:
                            active['best_price'] = b['low']
                            
                    # UPDATE DYNAMIC TRAILING SL
                    if mode == "baseline":
                        pass # Fixed SL, does not move
                    elif mode == "atr_trail":
                        trail_gap = trail_dist_mult * atr_v
                        if ttype == 'BUY':
                            new_sl = active['best_price'] - trail_gap
                            if new_sl > active['sl']:
                                active['sl'] = new_sl
                        else:
                            new_sl = active['best_price'] + trail_gap
                            if new_sl < active['sl']:
                                active['sl'] = new_sl
                    elif mode == "step_trail":
                        # Step-wise R trailing
                        if ttype == 'BUY':
                            gain_r = (active['best_price'] - entry_p) / sl_dist
                            if gain_r >= 3.0:
                                active['sl'] = max(active['sl'], entry_p + 2.25 * sl_dist)
                            elif gain_r >= 2.0:
                                active['sl'] = max(active['sl'], entry_p + 1.50 * sl_dist)
                            elif gain_r >= 1.5:
                                active['sl'] = max(active['sl'], entry_p + 0.75 * sl_dist)
                            elif gain_r >= 1.0:
                                active['sl'] = max(active['sl'], entry_p) # Breakeven
                        else:
                            gain_r = (entry_p - active['best_price']) / sl_dist
                            if gain_r >= 3.0:
                                active['sl'] = min(active['sl'], entry_p - 2.25 * sl_dist)
                            elif gain_r >= 2.0:
                                active['sl'] = min(active['sl'], entry_p - 1.50 * sl_dist)
                            elif gain_r >= 1.5:
                                active['sl'] = min(active['sl'], entry_p - 0.75 * sl_dist)
                            elif gain_r >= 1.0:
                                active['sl'] = min(active['sl'], entry_p)
                                
                    # CHECK EXITS
                    trade_closed = False
                    pnl_r = 0.0
                    
                    if ttype == 'BUY':
                        # Check TP if set
                        if active['tp'] is not None and b['high'] >= active['tp']:
                            pnl_r = (active['tp'] - entry_p) / sl_dist
                            trade_closed = True
                        elif b['low'] <= active['sl']:
                            pnl_r = (active['sl'] - entry_p) / sl_dist
                            trade_closed = True
                    else: # SELL
                        if active['tp'] is not None and b['low'] <= active['tp']:
                            pnl_r = (entry_p - active['tp']) / sl_dist
                            trade_closed = True
                        elif b['high'] >= active['sl']:
                            pnl_r = (entry_p - active['sl']) / sl_dist
                            trade_closed = True
                            
                    if trade_closed:
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        trade_pnls.append(pnl_r)
                        if pnl_r > 0:
                            wins += 1
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
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    
    cum = (1.0 + df_res['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    max_pnl_trade = max(trade_pnls) if trade_pnls else 0
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if any(p > 0 for p in trade_pnls) else 0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%)",
        'green_2026': f"{green_2026}/11 ({green_2026/11*100:.1f}%)",
        'win_rate': win_rate,
        'max_dd': abs(dd),
        'max_win_r': max_pnl_trade,
        'avg_win_r': avg_win,
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def main():
    print("Loading historical M5 data for XAUUSD & EURUSD (2022-2026)...")
    data = load_data()
    print("Data loaded. Starting simulations...\n")
    
    tests = [
        ("1. Baseline (Fixed SL 0.75x, Fixed TP 1.35x = 1.8:1 R:R)", "baseline", None, 1.35),
        ("2. No TP + Pure Time Exit at 15:55 IST (Fixed SL)", "baseline", None, None),
        ("3. No TP + Tight ATR Trail (0.75x ATR)", "atr_trail", 0.75, None),
        ("4. No TP + Medium ATR Trail (1.00x ATR)", "atr_trail", 1.00, None),
        ("5. No TP + Wide ATR Trail (1.25x ATR)", "atr_trail", 1.25, None),
        ("6. No TP + Step-wise R Trail (+1.0R->BE, +1.5R->0.75R, +2.0R->1.5R)", "step_trail", None, None),
        ("7. Wide TP (3.0x ATR = 4:1 R:R) + Step-wise R Trail", "step_trail", None, 3.0),
        ("8. Moderate TP (2.0x ATR = 2.67:1 R:R) + Step-wise R Trail", "step_trail", None, 2.0),
    ]
    
    for label, mode, t_mult, tp_mult in tests:
        res = simulate_trailing_variant(data, mode=mode, trail_dist_mult=t_mult, fixed_tp_mult=tp_mult)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']}")
        print(f"   Win Rate      : {res['win_rate']:.1f}% | Avg Win: +{res['avg_win_r']:.2f}R | Best Trade: +{res['max_win_r']:.2f}R")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)

if __name__ == '__main__':
    main()

