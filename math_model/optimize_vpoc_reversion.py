"""
OPTIMIZATION OF PRIOR DAY VPOC INSTITUTIONAL REVERSION SCALPER
Assets: XAUUSD, EURUSD (2022 - 2026)
Operating Window: 11:00 AM - 16:00 IST (Strictly no VPS needed)
Auto-Square-Off: 15:55 IST

Core Concept:
1. Compute Prior Calendar Day's Volume Point of Control (Yesterday's VPOC).
2. Between 11:00 AM and 14:00 IST:
   - If Price deviates >= min_deviation * ATR from Yesterday's VPOC:
     * If Price < VPOC and M5 Candle shows rejection/absorption (Close > Open), BUY toward VPOC!
     * If Price > VPOC and M5 Candle shows rejection/absorption (Close < Open), SELL toward VPOC!
3. Target & Stop:
   - Target: Distance back to VPOC or TP_mult * ATR (Testing positive R:R: 1.2:1, 1.5:1, 1.8:1)
   - Stop Loss: SL_mult * ATR (0.6x to 0.9x ATR)
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
        
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['volume'] = df['volume'].replace(0, 1.0)
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Calculate daily VPOC for each calendar day
        # Price bucketed by tick size
        tick_sz = 0.10 if "XAU" in s else 0.0001
        daily_vpoc = {}
        for d, sub in df.groupby('date'):
            if len(sub) >= 40:
                # Volume profile weighted histogram
                price_bins = np.round(sub['close'] / tick_sz) * tick_sz
                vpoc_p = price_bins.value_counts().index[0]
                daily_vpoc[d] = vpoc_p
                
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        sorted_dates = sorted(list(df['date'].unique()))
        
        for i, d in enumerate(sorted_dates):
            if i == 0:
                continue
            prev_d = sorted_dates[i-1]
            if prev_d not in daily_vpoc:
                continue
            prev_vpoc_val = daily_vpoc[prev_d]
            
            sub = df_ist[df_ist['date'] == d]
            if len(sub) >= 30:
                sessions[d] = {
                    'prev_vpoc': prev_vpoc_val,
                    'bars': sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
                }
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate_vpoc(asset_data, min_dev=0.8, sl_mult=0.75, tp_mult=1.35):
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
            data = sessions[curr_d]
            prev_vpoc = data['prev_vpoc']
            bars = data['bars']
            
            active = None
            
            for b in bars:
                # Square off at 15:55 IST
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if active is not None:
                        pnl_r = (b['close'] - active['entry']) / active['sl_dist'] if active['type'] == 'BUY' else (active['entry'] - b['close']) / active['sl_dist']
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        active = None
                    break
                    
                if active is None:
                    # Scan for entry between 11:00 and 14:00 IST
                    if b['ist_hour'] > 14:
                        break
                    atr_v = b['atr']
                    if atr_v <= 0:
                        continue
                        
                    dev_pips = b['close'] - prev_vpoc
                    dev_atr = dev_pips / atr_v
                    
                    sl_dist = sl_mult * atr_v
                    tp_dist = tp_mult * atr_v
                    
                    # BUY Reversion: Price is far BELOW Yesterday's VPOC and bounces (Close > Open)
                    if dev_atr <= -min_dev and b['close'] > b['open']:
                        active = {
                            'type': 'BUY',
                            'entry': b['close'],
                            'sl': b['close'] - sl_dist,
                            'tp': b['close'] + tp_dist,
                            'sl_dist': sl_dist,
                            'tp_dist': tp_dist
                        }
                    # SELL Reversion: Price is far ABOVE Yesterday's VPOC and rejects (Close < Open)
                    elif dev_atr >= min_dev and b['close'] < b['open']:
                        active = {
                            'type': 'SELL',
                            'entry': b['close'],
                            'sl': b['close'] + sl_dist,
                            'tp': b['close'] - tp_dist,
                            'sl_dist': sl_dist,
                            'tp_dist': tp_dist
                        }
                else:
                    ttype = active['type']
                    entry_p = active['entry']
                    sl_dist = active['sl_dist']
                    tp_dist = active['tp_dist']
                    
                    if ttype == 'BUY':
                        if b['high'] >= active['tp']:
                            day_pnl += (tp_dist / sl_dist) * 0.010
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
                            day_pnl += (tp_dist / sl_dist) * 0.010
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
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_2026 = (m_2026 > 0).sum()
    
    cum = (1.0 + df_res['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_cnt}/{total_cnt} ({green_cnt/total_cnt*100:.1f}%)",
        'green_2026': f"{green_2026}/11 ({green_2026/11*100:.1f}%)",
        'win_rate': win_rate,
        'total_trades': total_trades,
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def main():
    print("Loading historical data for VPOC Reversion Grid Search...")
    data = load_data()
    print("Data loaded. Starting parameter grid...\n")
    
    grid = [
        # (min_dev, sl_mult, tp_mult, label)
        (0.6, 0.75, 1.10, "Dev >= 0.6x ATR, SL=0.75, TP=1.10 (1.47:1 R:R)"),
        (0.8, 0.75, 1.10, "Dev >= 0.8x ATR, SL=0.75, TP=1.10 (1.47:1 R:R)"),
        (1.0, 0.75, 1.10, "Dev >= 1.0x ATR, SL=0.75, TP=1.10 (1.47:1 R:R)"),
        (0.8, 0.75, 1.25, "Dev >= 0.8x ATR, SL=0.75, TP=1.25 (1.67:1 R:R)"),
        (1.0, 0.75, 1.25, "Dev >= 1.0x ATR, SL=0.75, TP=1.25 (1.67:1 R:R)"),
        (0.8, 0.75, 1.35, "Dev >= 0.8x ATR, SL=0.75, TP=1.35 (1.80:1 R:R)"),
        (1.0, 0.75, 1.35, "Dev >= 1.0x ATR, SL=0.75, TP=1.35 (1.80:1 R:R)"),
        (1.2, 0.75, 1.35, "Dev >= 1.2x ATR, SL=0.75, TP=1.35 (1.80:1 R:R)"),
    ]
    
    for m_dev, sl, tp, label in grid:
        res = evaluate_vpoc(data, min_dev=m_dev, sl_mult=sl, tp_mult=tp)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}% ({res['total_trades']} trades)")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)

if __name__ == '__main__':
    main()

