"""
PYRAMIDING & CONSISTENCY OPTIMIZATION TEST
Testing:
1. Baseline: 0.35x ATR entry, 0.8x SL, 1.6x TP (2.0:1 R:R)
2. Trailing to Breakeven (BE): When trade reaches +1.0R, move SL to entry price
3. Pyramiding (Scale-in): When trade reaches +0.8R, move SL1 to BE, add Order 2 (scale-in)
4. Trend Filter: Trade only with H1 50 EMA trend (Buy if Close > EMA50, Sell if Close < EMA50)
5. Combined: Trend Filter + BE + Pyramiding

Target: Increase green months to 11/12 (>=90%) while boosting net annual returns.
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
        
        # H1 EMA50 equivalent on M5 is EMA 600 (12 * 50 = 600)
        df['ema_h1'] = df['close'].ewm(span=600, adjust=False).mean()
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'ema_h1']].to_dict('records')
        asset_data[s] = sessions
    return asset_data

def simulate_strategy(asset_data, use_be=False, use_pyramid=False, use_trend=False, entry_mult=0.35, sl_mult=0.80, tp_mult=1.60):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    total_trades = 0
    
    for curr_d in all_dates:
        day_pnl = 0.0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            # Find base at 12:30 IST
            base_idx = -1
            for i, b in enumerate(bars):
                if b['ist_hour'] == 12 and b['ist_minute'] == 30:
                    base_idx = i
                    break
            if base_idx == -1 or base_idx >= len(bars) - 5:
                continue
                
            base_p = bars[base_idx]['close']
            atr_v = bars[base_idx]['atr']
            ema_v = bars[base_idx]['ema_h1']
            
            if atr_v <= 0:
                continue
                
            entry_offset = entry_mult * atr_v
            sl_dist = sl_mult * atr_v
            tp_dist = tp_mult * atr_v
            
            buy_allowed = True
            sell_allowed = True
            if use_trend:
                # Only buy if base > EMA, only sell if base < EMA
                buy_allowed = (base_p >= ema_v)
                sell_allowed = (base_p <= ema_v)
            
            buy_trigger = base_p + entry_offset
            sell_trigger = base_p - entry_offset
            
            active_trade = None
            
            for b in bars[base_idx+1:]:
                # Check square off at 15:55
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active_trade is not None:
                        # Close remaining orders at market close
                        pnl_r = 0.0
                        for order in active_trade['orders']:
                            if active_trade['type'] == 'BUY':
                                pnl_r += (b['close'] - order['entry']) / sl_dist
                            else:
                                pnl_r += (order['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += len(active_trade['orders'])
                        active_trade = None
                    break
                    
                if active_trade is None:
                    # Check triggers
                    if buy_allowed and b['high'] >= buy_trigger:
                        active_trade = {
                            'type': 'BUY',
                            'orders': [{'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}],
                            'base_sl': buy_trigger - sl_dist,
                            'pyramided': False,
                            'be_set': False
                        }
                    elif sell_allowed and b['low'] <= sell_trigger:
                        active_trade = {
                            'type': 'SELL',
                            'orders': [{'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}],
                            'base_sl': sell_trigger + sl_dist,
                            'pyramided': False,
                            'be_set': False
                        }
                else:
                    ttype = active_trade['type']
                    entry_0 = active_trade['orders'][0]['entry']
                    
                    # Check Pyramiding / Scale-in condition:
                    # When price hits +0.8R (0.8 * sl_dist into profit)
                    if use_pyramid and not active_trade['pyramided']:
                        if ttype == 'BUY' and b['high'] >= entry_0 + 0.8 * sl_dist:
                            # Add 2nd order at entry_0 + 0.8 * sl_dist
                            p_entry = entry_0 + 0.8 * sl_dist
                            # Trail Order 1 SL to Breakeven (entry_0)
                            active_trade['orders'][0]['sl'] = entry_0
                            # Order 2 has SL at entry_0 (which is -0.8R for Order 2, but Order 1 is +0.8R -> Total open risk = 0!)
                            active_trade['orders'].append({
                                'entry': p_entry,
                                'sl': entry_0,
                                'tp': entry_0 + tp_dist
                            })
                            active_trade['pyramided'] = True
                            active_trade['be_set'] = True
                        elif ttype == 'SELL' and b['low'] <= entry_0 - 0.8 * sl_dist:
                            p_entry = entry_0 - 0.8 * sl_dist
                            active_trade['orders'][0]['sl'] = entry_0
                            active_trade['orders'].append({
                                'entry': p_entry,
                                'sl': entry_0,
                                'tp': entry_0 - tp_dist
                            })
                            active_trade['pyramided'] = True
                            active_trade['be_set'] = True
                    
                    # If not pyramiding but use_be is enabled:
                    elif use_be and not active_trade['be_set']:
                        if ttype == 'BUY' and b['high'] >= entry_0 + 1.0 * sl_dist:
                            active_trade['orders'][0]['sl'] = entry_0
                            active_trade['be_set'] = True
                        elif ttype == 'SELL' and b['low'] <= entry_0 - 1.0 * sl_dist:
                            active_trade['orders'][0]['sl'] = entry_0
                            active_trade['be_set'] = True
                            
                    # Check exits for all orders
                    trade_closed = False
                    pnl_r = 0.0
                    
                    if ttype == 'BUY':
                        # Check TP
                        if b['high'] >= active_trade['orders'][0]['tp']:
                            for order in active_trade['orders']:
                                pnl_r += (order['tp'] - order['entry']) / sl_dist
                            trade_closed = True
                        # Check SL
                        elif b['low'] <= active_trade['orders'][0]['sl']:
                            for order in active_trade['orders']:
                                sl_price = order['sl']
                                pnl_r += (sl_price - order['entry']) / sl_dist
                            trade_closed = True
                    else: # SELL
                        if b['low'] <= active_trade['orders'][0]['tp']:
                            for order in active_trade['orders']:
                                pnl_r += (order['entry'] - order['tp']) / sl_dist
                            trade_closed = True
                        elif b['high'] >= active_trade['orders'][0]['sl']:
                            for order in active_trade['orders']:
                                sl_price = order['sl']
                                pnl_r += (order['entry'] - sl_price) / sl_dist
                            trade_closed = True
                            
                    if trade_closed:
                        day_pnl += pnl_r * 0.010
                        total_trades += len(active_trade['orders'])
                        active_trade = None
                        break # Done for day on this symbol
                        
        # Portfolio daily circuit breaker: strictly cap at -2.0%
        if day_pnl < -0.020:
            day_pnl = -0.020
            
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_res = pd.DataFrame(portfolio_daily)
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['month'] = pd.to_datetime(df_res['date']).dt.to_period('M')
    
    # Calculate metrics
    monthly = df_res.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_res.groupby('year')['pnl_pct'].sum() * 100.0
    
    green_months_cnt = (monthly > 0).sum()
    total_months_cnt = len(monthly)
    green_pct = (green_months_cnt / total_months_cnt) * 100.0
    
    # Check 2026 specifically
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    total_2026 = len(m_2026)
    
    avg_annual = yearly.mean()
    min_year = yearly.min()
    
    return {
        'avg_annual': avg_annual,
        'min_year': min_year,
        'yearly': yearly.to_dict(),
        'green_months_overall': f"{green_months_cnt}/{total_months_cnt} ({green_pct:.1f}%)",
        'green_2026': f"{green_2026}/{total_2026} ({green_2026/total_2026*100:.1f}%)",
        'm_2026_series': m_2026.to_dict(),
        'total_trades': total_trades
    }

if __name__ == '__main__':
    data = load_data()
    print("="*85)
    print("        OPTIMIZATION & PYRAMIDING GRID EXPERIMENT (2022-2026)       ")
    print("="*85)
    
    configs = [
        ("1. Baseline ATR Straddle (No BE, No Pyramid, No Filter)", False, False, False),
        ("2. Straddle + Trailing Breakeven at +1.0R", True, False, False),
        ("3. Straddle + Pyramiding Scale-In (+0.8R)", False, True, False),
        ("4. Straddle + H1 Trend Filter (EMA 50)", False, False, True),
        ("5. Straddle + Pyramiding + H1 Trend Filter", False, True, True),
        ("6. Straddle + Pyramiding + BE + Trend Filter", True, True, True),
    ]
    
    for name, use_be, use_pyr, use_tr in configs:
        res = simulate_strategy(data, use_be=use_be, use_pyramid=use_pyr, use_trend=use_tr)
        print(f"\n--- {name} ---")
        print(f"  Avg Annual ROI  : +{res['avg_annual']:.2f}% (Worst Year: +{res['min_year']:.2f}%)")
        print(f"  Overall Green   : {res['green_months_overall']}")
        print(f"  2026 Green      : {res['green_2026']}")
        print(f"  Yearly Breakdown: " + ", ".join([f"{y}: +{v:.1f}%" if v>0 else f"{y}: {v:.1f}%" for y, v in res['yearly'].items()]))
        print(f"  2026 Monthly    : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026_series'].items()]))

    mt5.shutdown()

