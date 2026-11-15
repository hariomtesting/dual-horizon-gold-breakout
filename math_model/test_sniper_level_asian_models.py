"""
EXPERIMENT: ELEVATING ASIAN BREAKOUT & VPOC TO SNIPER LEVEL (2022 - 2026)
Assets: XAUUSD, EURUSD
Hours: 11:00 - 16:00 IST (Auto-square-off at 15:55 IST)

Testing 4 Architectural Upgrades to match the Golden Sniper:
1. Compressed Asian Spring at 1:00 PM (Asian Range <= 3.0x ATR, Entry at Asian High/Low if <= 0.4x ATR away)
2. 1:00 PM Asian Extreme Breakout (Orders armed at 13:00 IST at Asian High/Low with 15:00 Cutoff)
3. 1:00 PM VPOC Exhaustion Sniper (Measure VPOC deviation at 13:00 IST Anchor with 1.8:1 R:R)
4. Dynamic VWAP Band Expansion at 1:00 PM (Buy Stop at VWAP + 1.2x ATR, Sell Stop at VWAP - 1.2x ATR)
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
        
        # Calculate daily VPOC
        tick_sz = 0.10 if "XAU" in s else 0.0001
        daily_vpoc = {}
        for d, sub in df.groupby('date'):
            if len(sub) >= 40:
                price_bins = np.round(sub['close'] / tick_sz) * tick_sz
                daily_vpoc[d] = price_bins.value_counts().index[0]
                
        # Keep 06:00 to 16:00 IST
        df_ist = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        sorted_dates = sorted(list(df['date'].unique()))
        
        for i, d in enumerate(sorted_dates):
            if i == 0: continue
            prev_d = sorted_dates[i-1]
            prev_vpoc_val = daily_vpoc.get(prev_d, None)
            sub = df_ist[df_ist['date'] == d]
            if len(sub) >= 50 and prev_vpoc_val is not None:
                sessions[d] = {
                    'prev_vpoc': prev_vpoc_val,
                    'bars': sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'volume']].to_dict('records')
                }
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def simulate_engine(asset_data, strategy_func):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    portfolio_daily = []
    trades = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            data = sessions[curr_d]
            prev_vpoc = data['prev_vpoc']
            bars = data['bars']
            
            pnl_r = strategy_func(bars, prev_vpoc)
            if pnl_r is not None:
                day_pnl += pnl_r * 0.010
                trades.append({'date': curr_d, 'pnl_r': pnl_r})
                
        if day_pnl < -0.020:
            day_pnl = -0.020
        portfolio_daily.append({'date': curr_d, 'pnl_pct': day_pnl})
        
    df_d = pd.DataFrame(portfolio_daily)
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    monthly = df_d.groupby('month')['pnl_pct'].sum() * 100.0
    yearly = df_d.groupby('year')['pnl_pct'].sum() * 100.0
    df_t = pd.DataFrame(trades)
    wr = (df_t['pnl_r'] > 0).mean() * 100.0 if len(df_t) > 0 else 0
    green_m = (monthly > 0).sum()
    total_m = len(monthly)
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_26 = (m_2026 > 0).sum()
    cum = (1.0 + df_d['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_m}/{total_m} ({green_m/total_m*100:.1f}%)",
        'green_2026': f"{green_26}/11 ({green_26/11*100:.1f}%)",
        'win_rate': wr,
        'total_trades': len(df_t),
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

# STRATEGY 1: 1:00 PM Asian Coiled Spring Sniper
# If 1:00 PM price is within 0.35x ATR of Asian High/Low, place breakout stop!
def strategy_coiled_asian_spring(bars, prev_vpoc):
    asian_bars = [b for b in bars if b['ist_hour'] < 13]
    post_bars = [b for b in bars if b['ist_hour'] >= 13]
    if len(asian_bars) < 20 or len(post_bars) < 15: return None
    
    asian_high = max(b['high'] for b in asian_bars)
    asian_low = min(b['low'] for b in asian_bars)
    base_p = asian_bars[-1]['close']
    atr_v = asian_bars[-1]['atr']
    if atr_v <= 0: return None
    
    dist_high = asian_high - base_p
    dist_low = base_p - asian_low
    
    buy_allowed = dist_high <= 0.40 * atr_v
    sell_allowed = dist_low <= 0.40 * atr_v
    
    if not buy_allowed and not sell_allowed:
        return None # Asian extremes too far away
        
    buy_trigger = asian_high + 0.05 * atr_v
    sell_trigger = asian_low - 0.05 * atr_v
    sl_dist = 0.70 * atr_v
    tp_dist = 1.40 * atr_v
    rr = tp_dist / sl_dist
    
    pos = None
    for b in post_bars:
        if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
            if pos:
                pnl = (b['close'] - pos['entry'])/sl_dist if pos['type']=='BUY' else (pos['entry'] - b['close'])/sl_dist
                return pnl
            return None
        if pos is None and b['ist_hour'] < 15:
            if buy_allowed and b['high'] >= buy_trigger:
                pos = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
            elif sell_allowed and b['low'] <= sell_trigger:
                pos = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
        elif pos is not None:
            if pos['type'] == 'BUY':
                if b['high'] >= pos['tp']: return rr
                elif b['low'] <= pos['sl']: return -1.0
            else:
                if b['low'] <= pos['tp']: return rr
                elif b['high'] >= pos['sl']: return -1.0
    return None

# STRATEGY 2: 1:00 PM VPOC Exhaustion Sniper
def strategy_1pm_vpoc_sniper(bars, prev_vpoc):
    asian_bars = [b for b in bars if b['ist_hour'] < 13]
    post_bars = [b for b in bars if b['ist_hour'] >= 13]
    if len(asian_bars) < 20 or len(post_bars) < 15: return None
    
    base_p = asian_bars[-1]['close']
    atr_v = asian_bars[-1]['atr']
    if atr_v <= 0: return None
    
    dev = (base_p - prev_vpoc) / atr_v
    # Only if 1:00 PM price has stretched >= 1.0x ATR away from Yesterday's VPOC
    if abs(dev) < 1.0: return None
    
    # Enter reversion toward VPOC
    ttype = 'BUY' if dev <= -1.0 else 'SELL'
    sl_dist = 0.70 * atr_v
    tp_dist = 1.35 * atr_v
    rr = tp_dist / sl_dist
    
    entry_p = base_p
    sl_p = entry_p - sl_dist if ttype == 'BUY' else entry_p + sl_dist
    tp_p = entry_p + tp_dist if ttype == 'BUY' else entry_p - tp_dist
    
    pos = {'type': ttype, 'entry': entry_p, 'sl': sl_p, 'tp': tp_p}
    
    for b in post_bars:
        if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
            pnl = (b['close'] - entry_p)/sl_dist if ttype == 'BUY' else (entry_p - b['close'])/sl_dist
            return pnl
        if ttype == 'BUY':
            if b['high'] >= pos['tp']: return rr
            elif b['low'] <= pos['sl']: return -1.0
        else:
            if b['low'] <= pos['tp']: return rr
            elif b['high'] >= pos['sl']: return -1.0
    return None

# STRATEGY 3: 1:00 PM Dual-Asset Synchronized Sniper (The Master Golden Sniper)
def strategy_golden_sniper(bars, prev_vpoc):
    asian_bars = [b for b in bars if b['ist_hour'] < 13]
    post_bars = [b for b in bars if b['ist_hour'] >= 13]
    if len(asian_bars) < 20 or len(post_bars) < 15: return None
    
    base_p = asian_bars[-1]['close']
    atr_v = asian_bars[-1]['atr']
    if atr_v <= 0: return None
    
    entry_offset = 0.30 * atr_v
    sl_dist = 0.75 * atr_v
    tp_dist = 1.45 * atr_v
    rr = tp_dist / sl_dist
    
    buy_trigger = base_p + entry_offset
    sell_trigger = base_p - entry_offset
    
    pos = None
    for b in post_bars:
        if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
            if pos:
                pnl = (b['close'] - pos['entry'])/sl_dist if pos['type']=='BUY' else (pos['entry'] - b['close'])/sl_dist
                return pnl
            return None
        if pos is None and b['ist_hour'] < 15:
            if b['high'] >= buy_trigger:
                pos = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
            elif b['low'] <= sell_trigger:
                pos = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
        elif pos is not None:
            if pos['type'] == 'BUY':
                if b['high'] >= pos['tp']: return rr
                elif b['low'] <= pos['sl']: return -1.0
            else:
                if b['low'] <= pos['tp']: return rr
                elif b['high'] >= pos['sl']: return -1.0
    return None

def main():
    print("Loading data for Sniper-Level Architectural Upgrade...")
    data = load_data()
    print("Data loaded. Simulating strategies...\n")
    
    models = [
        ("A. 1:00 PM Golden Base Sniper (Our Benchmark)", strategy_golden_sniper),
        ("B. 1:00 PM Asian Coiled Spring Sniper (Asian High/Low proximity)", strategy_coiled_asian_spring),
        ("C. 1:00 PM VPOC Exhaustion Sniper (13:00 Fair Value Deviation)", strategy_1pm_vpoc_sniper),
    ]
    
    for name, s_func in models:
        res = simulate_engine(data, s_func)
        print(f"[{name}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}% ({res['total_trades']} trades)")
        print("   Yearly Walk   : " + ", ".join([f"{y}: {v:+.1f}%" for y, v in res['yearly'].items()]))
        print("   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)

if __name__ == '__main__':
    main()

