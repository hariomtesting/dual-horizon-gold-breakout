"""
EVALUATING PYRAMIDING & MULTI-ASSET ON GER40 + XAUUSD + NAS100 (11:00 AM - 4:00 PM IST)
=======================================================================================
Levers to increase return:
1. Pyramiding: When Trade 1 hits +0.8x ATR profit, move SL to breakeven, add Unit 2 with target at 1.8x ATR.
2. Multi-Asset Portfolio: Combine GER40.r (European power session) + XAUUSD + NAS100.r.
3. Asymmetric R:R: Risk 1.25%, reward 2.0R to 3.5R with pyramid runner.
4. Zero Overnight Holding: Hard flat at 15:55 IST.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def simulate_multi_asset_ist_portfolio():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    symbols = ["GER40.r", "XAUUSD", "NAS100.r"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2021, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            continue
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['volume'] = df['volume'].replace(0, 1.0)
        
        of = OrderFlowEngine(tick_size=0.10)
        df = of.calculate_delta(df)
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Filter 11:00 to 16:00 IST
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
        
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 30:
                sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'delta']].to_dict('records')
        asset_data[s] = sessions
        print(f"Loaded {s}: {len(sessions):,} sessions.")
        
    # Run Portfolio Simulation with Pyramiding
    risk_per_trade = 0.0125 # 1.25%
    
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    portfolio_trades = []
    
    for curr_d in all_dates:
        day_pnl_pct = 0.0
        day_trades = 0
        
        for s in symbols:
            if curr_d not in asset_data[s]:
                continue
            bars = asset_data[s][curr_d]
            pre_bars = bars[0:18] # 11:00 to 12:30 IST
            pre_high = max(b['high'] for b in pre_bars)
            pre_low = min(b['low'] for b in pre_bars)
            
            pos_u1 = None
            pos_u2 = None
            traded_today = False
            
            for i in range(18, len(bars)):
                r = bars[i]
                h, l, c, o = r['high'], r['low'], r['close'], r['open']
                atr, delta = r['atr'], r['delta']
                ist_h, ist_m = r['ist_hour'], r['ist_minute']
                
                # Hard close at 15:55 IST
                if ist_h == 15 and ist_m >= 55:
                    for pos in [pos_u1, pos_u2]:
                        if pos is not None:
                            pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                            pnl = (pts / pos['sl_dist']) * risk_per_trade
                            pos['pnl_pct'] = pnl
                            day_pnl_pct += pnl
                            portfolio_trades.append(pos)
                            day_trades += 1
                    pos_u1 = None
                    pos_u2 = None
                    break
                    
                # Manage Unit 1 & Pyramiding trigger
                if pos_u1 is not None:
                    # Pyramiding check: if Unit 1 reaches +0.8x ATR profit, lock to Breakeven (+0.1R) and add Unit 2
                    if not pos_u1['be']:
                        if pos_u1['type'] == 'BUY' and h >= pos_u1['entry'] + 0.8 * atr:
                            pos_u1['sl'] = pos_u1['entry'] + 0.10
                            pos_u1['be'] = True
                            if pos_u2 is None:
                                pos_u2 = {
                                    'symbol': s, 'unit': 2, 'type': 'BUY', 'date': curr_d,
                                    'entry': c, 'sl': pos_u1['entry'] + 0.10, 'tp': c + 1.2 * atr,
                                    'sl_dist': max(c - (pos_u1['entry'] + 0.10), 0.5 * atr), 'rr': 1.5,
                                    'be': True
                                }
                        elif pos_u1['type'] == 'SELL' and l <= pos_u1['entry'] - 0.8 * atr:
                            pos_u1['sl'] = pos_u1['entry'] - 0.10
                            pos_u1['be'] = True
                            if pos_u2 is None:
                                pos_u2 = {
                                    'symbol': s, 'unit': 2, 'type': 'SELL', 'date': curr_d,
                                    'entry': c, 'sl': pos_u1['entry'] - 0.10, 'tp': c - 1.2 * atr,
                                    'sl_dist': max((pos_u1['entry'] - 0.10) - c, 0.5 * atr), 'rr': 1.5,
                                    'be': True
                                }
                                
                    # Exits for Unit 1
                    if pos_u1['type'] == 'BUY':
                        if l <= pos_u1['sl']:
                            pos_u1['pnl_pct'] = 0.001 if pos_u1['be'] and pos_u1['sl'] >= pos_u1['entry'] else -risk_per_trade
                            day_pnl_pct += pos_u1['pnl_pct']
                            portfolio_trades.append(pos_u1)
                            day_trades += 1
                            pos_u1 = None
                        elif h >= pos_u1['tp']:
                            pos_u1['pnl_pct'] = risk_per_trade * pos_u1['rr']
                            day_pnl_pct += pos_u1['pnl_pct']
                            portfolio_trades.append(pos_u1)
                            day_trades += 1
                            pos_u1 = None
                    elif pos_u1['type'] == 'SELL':
                        if h >= pos_u1['sl']:
                            pos_u1['pnl_pct'] = 0.001 if pos_u1['be'] and pos_u1['sl'] <= pos_u1['entry'] else -risk_per_trade
                            day_pnl_pct += pos_u1['pnl_pct']
                            portfolio_trades.append(pos_u1)
                            day_trades += 1
                            pos_u1 = None
                        elif l <= pos_u1['tp']:
                            pos_u1['pnl_pct'] = risk_per_trade * pos_u1['rr']
                            day_pnl_pct += pos_u1['pnl_pct']
                            portfolio_trades.append(pos_u1)
                            day_trades += 1
                            pos_u1 = None
                            
                # Exits for Unit 2 (Pyramid trade)
                if pos_u2 is not None:
                    if pos_u2['type'] == 'BUY':
                        if l <= pos_u2['sl']:
                            pos_u2['pnl_pct'] = -risk_per_trade * 0.4
                            day_pnl_pct += pos_u2['pnl_pct']
                            portfolio_trades.append(pos_u2)
                            day_trades += 1
                            pos_u2 = None
                        elif h >= pos_u2['tp']:
                            pos_u2['pnl_pct'] = risk_per_trade * 1.5
                            day_pnl_pct += pos_u2['pnl_pct']
                            portfolio_trades.append(pos_u2)
                            day_trades += 1
                            pos_u2 = None
                    elif pos_u2['type'] == 'SELL':
                        if h >= pos_u2['sl']:
                            pos_u2['pnl_pct'] = -risk_per_trade * 0.4
                            day_pnl_pct += pos_u2['pnl_pct']
                            portfolio_trades.append(pos_u2)
                            day_trades += 1
                            pos_u2 = None
                        elif l <= pos_u2['tp']:
                            pos_u2['pnl_pct'] = risk_per_trade * 1.5
                            day_pnl_pct += pos_u2['pnl_pct']
                            portfolio_trades.append(pos_u2)
                            day_trades += 1
                            pos_u2 = None
                            
                # Entry for Unit 1: Liquidity Sweep
                if pos_u1 is None and not traded_today and 12 <= ist_h < 15:
                    sl_dist = 1.0 * atr
                    tp_dist = 1.8 * atr
                    rr = tp_dist / sl_dist
                    
                    if h > pre_high and c < pre_high and c < o and delta < 0:
                        pos_u1 = {
                            'symbol': s, 'unit': 1, 'type': 'SELL', 'date': curr_d,
                            'entry': c, 'sl': h + 0.10, 'tp': c - tp_dist,
                            'sl_dist': max((h + 0.10) - c, 0.5 * atr), 'rr': rr,
                            'be': False
                        }
                        traded_today = True
                    elif l < pre_low and c > pre_low and c > o and delta > 0:
                        pos_u1 = {
                            'symbol': s, 'unit': 1, 'type': 'BUY', 'date': curr_d,
                            'entry': c, 'sl': l - 0.10, 'tp': c + tp_dist,
                            'sl_dist': max(c - (l - 0.10), 0.5 * atr), 'rr': rr,
                            'be': False
                        }
                        traded_today = True
                        
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl_pct, 'trades': day_trades})
        
    df_t = pd.DataFrame(portfolio_trades)
    df_d = pd.DataFrame(portfolio_daily)
    
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    
    print("\n" + "=" * 90)
    print("      MULTI-ASSET (GER40 + GOLD + NAS100) IST SCALPER + PYRAMIDING RESULTS      ")
    print("=" * 90)
    print(f"Total Completed Trades: {len(df_t):,} across {len(df_d):,} Trading Days")
    print(f"Overall Portfolio Win Rate: {(df_t['pnl_pct'] > 0).mean()*100:.2f}%\n")
    
    years = sorted(df_d['year'].unique())
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 90)
    
    rois = []
    for y in years:
        y_d = df_d[df_d['year'] == y]
        y_t = df_t[df_t['year'] == y]
        if len(y_t) == 0:
            continue
        y_wr = (y_t['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, row in y_d.iterrows():
            cap += cap * row['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        rois.append(roi)
        
        m_rois = [m_sub['pnl_pct'].sum() * 100.0 for m, m_sub in y_d.groupby('month')]
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(y_t):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}")
        
    print("-" * 90)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(rois):+6.2f}%")
    print(f"PROFITABLE YEARS      : {(np.array(rois) > 0).mean()*100:.1f}%\n")


if __name__ == "__main__":
    simulate_multi_asset_ist_portfolio()

