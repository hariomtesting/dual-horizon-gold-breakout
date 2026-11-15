"""
HYBRID EXPERIMENT: SPLIT POSITION (50% FIXED TP + 50% NO TP RUNNER)
Testing if combining Fixed TP consistency with No TP runner upside gives the ultimate model.
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def test_hybrid():
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
    
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    portfolio_daily = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
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
            fixed_tp_dist = 1.35 * atr_v
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active = None
            
            for b in bars[base_idx+1:]:
                # 15:55 IST square off
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active is not None:
                        # Order A exit if not hit
                        if not active['order_a_closed']:
                            pnl_a = (b['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - b['close']) / sl_dist
                            day_pnl += 0.5 * pnl_a * 0.010
                        # Order B runner exit
                        pnl_b = (b['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - b['close']) / sl_dist
                        day_pnl += 0.5 * pnl_b * 0.010
                        active = None
                    break
                    
                if active is None:
                    if b['ist_hour'] >= 15:
                        break
                    if b['high'] >= buy_trigger:
                        active = {
                            'type': 'BUY',
                            'entry': buy_trigger,
                            'sl': buy_trigger - sl_dist,
                            'tp_a': buy_trigger + fixed_tp_dist,
                            'order_a_closed': False,
                            'best_price': buy_trigger
                        }
                    elif b['low'] <= sell_trigger:
                        active = {
                            'type': 'SELL',
                            'entry': sell_trigger,
                            'sl': sell_trigger + sl_dist,
                            'tp_a': sell_trigger - fixed_tp_dist,
                            'order_a_closed': False,
                            'best_price': sell_trigger
                        }
                else:
                    ttype = active['type']
                    entry_p = active['entry']
                    
                    if ttype == 'BUY':
                        if b['high'] > active['best_price']:
                            active['best_price'] = b['high']
                        # Check Order A TP
                        if not active['order_a_closed'] and b['high'] >= active['tp_a']:
                            active['order_a_closed'] = True
                            day_pnl += 0.5 * (fixed_tp_dist / sl_dist) * 0.010
                            # Move SL of Order B to Breakeven
                            active['sl'] = max(active['sl'], entry_p)
                            
                        # Trailing for Order B (tight 0.75x ATR trail once in profit)
                        if active['order_a_closed']:
                            new_sl = active['best_price'] - (0.75 * atr_v)
                            if new_sl > active['sl']:
                                active['sl'] = new_sl
                                
                        # Check SL for remaining
                        if b['low'] <= active['sl']:
                            # If Order A was open, it lost
                            if not active['order_a_closed']:
                                day_pnl += 0.5 * -1.0 * 0.010
                            # Order B PnL
                            pnl_b = (active['sl'] - entry_p) / sl_dist
                            day_pnl += 0.5 * pnl_b * 0.010
                            active = None
                            break
                    else: # SELL
                        if b['low'] < active['best_price']:
                            active['best_price'] = b['low']
                        if not active['order_a_closed'] and b['low'] <= active['tp_a']:
                            active['order_a_closed'] = True
                            day_pnl += 0.5 * (fixed_tp_dist / sl_dist) * 0.010
                            active['sl'] = min(active['sl'], entry_p)
                            
                        if active['order_a_closed']:
                            new_sl = active['best_price'] + (0.75 * atr_v)
                            if new_sl < active['sl']:
                                active['sl'] = new_sl
                                
                        if b['high'] >= active['sl']:
                            if not active['order_a_closed']:
                                day_pnl += 0.5 * -1.0 * 0.010
                            pnl_b = (entry_p - active['sl']) / sl_dist
                            day_pnl += 0.5 * pnl_b * 0.010
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
    
    print("\n" + "="*80)
    print("      HYBRID MODEL: 50% FIXED TP (1.8R) + 50% RUNNER (NO TP + TRAIL)      ")
    print("="*80)
    print(f"Avg Annual ROI  : +{yearly.mean():.2f}% | Worst Year: +{yearly.min():.2f}% | Max DD: {abs(dd):.2f}%")
    print(f"Overall Green   : {green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%) | 2026 Green: {green_2026}/11 ({green_2026/11*100:.1f}%)")
    print("Yearly Walk     : " + ", ".join([f"{y}: +{v:.1f}%" for y, v in yearly.items()]))
    print("2026 Months     : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in m_2026.items()]))

if __name__ == '__main__':
    test_hybrid()

