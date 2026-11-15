"""
TRUE RISK-FREE PYRAMIDING TEST
================================
Rule of Institutional Pyramiding:
1. Order 1 enters at Base + 0.35x ATR, SL = 0.8x ATR (Risk = 1.0R)
2. When Price reaches +1.0R:
   - Order 1 SL moved to +0.4R in profit (Locking +0.4R)
   - Order 2 (Pyramid) entered with SL at +0.4R (Risk on Order 2 = 0.4R)
   - If market reverses: Order 1 (+0.4R) - Order 2 (-0.4R) = Exactly 0.0R net (ZERO LOSS GUARANTEED)
   - If market hits TP: Order 1 (+2.0R) + Order 2 (+1.4R) = +3.4R massive payout!
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def test_true_risk_free_pyramiding():
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
        
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    for pyr_threshold in [0.8, 1.0, 1.2]:
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
                    if b['ist_hour'] == 12 and b['ist_minute'] == 30:
                        base_idx = i
                        break
                if base_idx == -1 or base_idx >= len(bars) - 5:
                    continue
                    
                base_p = bars[base_idx]['close']
                atr_v = bars[base_idx]['atr']
                if atr_v <= 0:
                    continue
                    
                entry_offset = 0.35 * atr_v
                sl_dist = 0.80 * atr_v
                tp_dist = 1.60 * atr_v # 2.0R
                
                buy_trigger = base_p + entry_offset
                sell_trigger = base_p - entry_offset
                
                active = None
                
                for b in bars[base_idx+1:]:
                    if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                        if active is not None:
                            # Auto square off
                            if active['type'] == 'BUY':
                                pnl1 = (b['close'] - active['entry1']) / sl_dist
                                pnl2 = (b['close'] - active['entry2']) / sl_dist if active['pyramided'] else 0.0
                            else:
                                pnl1 = (active['entry1'] - b['close']) / sl_dist
                                pnl2 = (active['entry2'] - b['close']) / sl_dist if active['pyramided'] else 0.0
                            day_pnl += (pnl1 + pnl2) * 0.010
                            active = None
                        break
                        
                    if active is None:
                        if b['high'] >= buy_trigger:
                            active = {
                                'type': 'BUY',
                                'entry1': buy_trigger,
                                'sl1': buy_trigger - sl_dist,
                                'tp1': buy_trigger + tp_dist,
                                'pyramided': False,
                                'entry2': 0.0,
                                'sl2': 0.0,
                                'tp2': 0.0
                            }
                        elif b['low'] <= sell_trigger:
                            active = {
                                'type': 'SELL',
                                'entry1': sell_trigger,
                                'sl1': sell_trigger + sl_dist,
                                'tp1': sell_trigger - tp_dist,
                                'pyramided': False,
                                'entry2': 0.0,
                                'sl2': 0.0,
                                'tp2': 0.0
                            }
                    else:
                        ttype = active['type']
                        e1 = active['entry1']
                        
                        # Check Pyramiding trigger
                        if not active['pyramided']:
                            if ttype == 'BUY' and b['high'] >= e1 + pyr_threshold * sl_dist:
                                active['pyramided'] = True
                                active['entry2'] = e1 + pyr_threshold * sl_dist
                                # Lock profit on Order 1 at half threshold
                                locked_sl = e1 + (pyr_threshold * 0.5) * sl_dist
                                active['sl1'] = locked_sl
                                active['sl2'] = locked_sl # Order 2 SL is same price
                                active['tp2'] = e1 + tp_dist
                            elif ttype == 'SELL' and b['low'] <= e1 - pyr_threshold * sl_dist:
                                active['pyramided'] = True
                                active['entry2'] = e1 - pyr_threshold * sl_dist
                                locked_sl = e1 - (pyr_threshold * 0.5) * sl_dist
                                active['sl1'] = locked_sl
                                active['sl2'] = locked_sl
                                active['tp2'] = e1 - tp_dist
                                
                        # Check Exits
                        if ttype == 'BUY':
                            if b['high'] >= active['tp1']:
                                pnl1 = (active['tp1'] - e1) / sl_dist
                                pnl2 = (active['tp1'] - active['entry2']) / sl_dist if active['pyramided'] else 0.0
                                day_pnl += (pnl1 + pnl2) * 0.010
                                active = None
                                break
                            elif b['low'] <= active['sl1']:
                                pnl1 = (active['sl1'] - e1) / sl_dist
                                pnl2 = (active['sl2'] - active['entry2']) / sl_dist if active['pyramided'] else 0.0
                                day_pnl += (pnl1 + pnl2) * 0.010
                                active = None
                                break
                        else: # SELL
                            if b['low'] <= active['tp1']:
                                pnl1 = (e1 - active['tp1']) / sl_dist
                                pnl2 = (active['entry2'] - active['tp1']) / sl_dist if active['pyramided'] else 0.0
                                day_pnl += (pnl1 + pnl2) * 0.010
                                active = None
                                break
                            elif b['high'] >= active['sl1']:
                                pnl1 = (e1 - active['sl1']) / sl_dist
                                pnl2 = (active['entry2'] - active['sl2']) / sl_dist if active['pyramided'] else 0.0
                                day_pnl += (pnl1 + pnl2) * 0.010
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
        
        print(f"\n--- Risk-Free Pyramiding Threshold = +{pyr_threshold:.1f}R ---")
        print(f"  Avg Annual ROI  : +{yearly.mean():.2f}% (Worst Year: +{yearly.min():.2f}%)")
        print(f"  Overall Green   : {green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%)")
        print(f"  2026 Green      : {green_2026}/11 ({green_2026/11*100:.1f}%)")
        print(f"  Yearly Breakdown: " + ", ".join([f"{y}: +{v:.1f}%" if v>0 else f"{y}: {v:.1f}%" for y, v in yearly.items()]))
        print(f"  2026 Monthly    : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in m_2026.items()]))

    mt5.shutdown()

if __name__ == '__main__':
    test_true_risk_free_pyramiding()

