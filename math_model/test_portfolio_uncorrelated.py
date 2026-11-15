"""
PORTFOLIO DIVERSIFICATION EXPERIMENT (11:00 - 16:00 IST)
Testing whether multi-asset diversification (XAUUSD + EURUSD + GBPUSD or GER40.r)
achieves >= 10/12 to 11/12 green months by smoothing out idiosyncratic choppy months.
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def test_portfolio_consistency():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    candidates = ["XAUUSD", "EURUSD", "GBPUSD", "GER40.r"]
    available_symbols = []
    for s in candidates:
        if mt5.symbol_select(s, True):
            available_symbols.append(s)
            
    print(f"Available symbols: {available_symbols}")
    
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for s in available_symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) < 1000:
            continue
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

    # Test baskets
    baskets = [
        ("Basket 1: XAUUSD + EURUSD (2 Assets)", ["XAUUSD", "EURUSD"]),
        ("Basket 2: XAUUSD + EURUSD + GBPUSD (3 Assets)", [s for s in ["XAUUSD", "EURUSD", "GBPUSD"] if s in asset_data]),
        ("Basket 3: XAUUSD + EURUSD + GER40.r (3 Assets)", [s for s in ["XAUUSD", "EURUSD", "GER40.r"] if s in asset_data]),
        ("Basket 4: XAUUSD + EURUSD + GBPUSD + GER40.r (4 Assets)", [s for s in ["XAUUSD", "EURUSD", "GBPUSD", "GER40.r"] if s in asset_data]),
    ]

    for b_name, b_symbols in baskets:
        all_dates = sorted(list(set.union(*[set(asset_data[s].keys()) for s in b_symbols])))
        portfolio_daily = []
        
        risk_per_trade = 0.010 / (len(b_symbols) / 2.0) # Scale risk so total daily risk exposure is constant
        
        for curr_d in all_dates:
            day_pnl = 0.0
            
            for sym in b_symbols:
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
                tp_dist = 1.60 * atr_v
                
                buy_trigger = base_p + entry_offset
                sell_trigger = base_p - entry_offset
                
                active = None
                
                for b in bars[base_idx+1:]:
                    if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                        if active is not None:
                            if active['type'] == 'BUY':
                                pnl_r = (b['close'] - active['entry']) / sl_dist
                            else:
                                pnl_r = (active['entry'] - b['close']) / sl_dist
                            day_pnl += pnl_r * risk_per_trade
                            active = None
                        break
                        
                    if active is None:
                        if b['high'] >= buy_trigger:
                            active = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
                        elif b['low'] <= sell_trigger:
                            active = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
                    else:
                        if active['type'] == 'BUY':
                            if b['high'] >= active['tp']:
                                day_pnl += 2.0 * risk_per_trade
                                active = None
                                break
                            elif b['low'] <= active['sl']:
                                day_pnl += -1.0 * risk_per_trade
                                active = None
                                break
                        else:
                            if b['low'] <= active['tp']:
                                day_pnl += 2.0 * risk_per_trade
                                active = None
                                break
                            elif b['high'] >= active['sl']:
                                day_pnl += -1.0 * risk_per_trade
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
        
        print(f"\n--- {b_name} ---")
        print(f"  Avg Annual ROI  : +{yearly.mean():.2f}% (Worst Year: +{yearly.min():.2f}%)")
        print(f"  Overall Green   : {green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%)")
        print(f"  2026 Green      : {green_2026}/11 ({green_2026/11*100:.1f}%)")
        print(f"  Yearly Breakdown: " + ", ".join([f"{y}: +{v:.1f}%" if v>0 else f"{y}: {v:.1f}%" for y, v in yearly.items()]))
        print(f"  2026 Monthly    : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in m_2026.items()]))

    mt5.shutdown()

if __name__ == '__main__':
    test_portfolio_consistency()

