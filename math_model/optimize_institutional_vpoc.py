"""
INSTITUTIONAL VPOC REVERSION OPTIMIZATION (2022 - 2026)
Assets: XAUUSD, EURUSD
Hours: 11:00 AM - 14:00 IST Entry Window | Auto-Square-Off at 15:55 IST
Core Edge: Auction Theory - Price deviating from Previous Day's Volume Point of Control (VPOC)
reverts back to fair value.

Optimizations Tested:
1. Distance Threshold: Price must deviate >= 0.8x, 1.0x, 1.2x ATR from Yesterday's VPOC
2. Rejection Confirmation: M5 Absorption Hammer/Shooting Star or Volume Exhaustion
3. Stop Loss: 0.65x, 0.75x, 0.85x ATR
4. Target: Magnet to VPOC with minimum 1.20:1 up to 1.60:1 Positive R:R
5. Time-of-Day Filter: Only between 11:00 AM and 13:30 IST (before US data releases)
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
        
        # Calculate daily VPOC for each calendar day using price binning
        tick_sz = 0.10 if "XAU" in s else 0.0001
        daily_vpoc = {}
        for d, sub in df.groupby('date'):
            if len(sub) >= 40:
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
                    'bars': sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'volume']].to_dict('records')
                }
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate_vpoc_strategy(asset_data, min_dev_atr=1.0, sl_mult=0.70, tp_mult=1.20, use_rejection_wick=True, end_entry_h=13):
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
            
            pos = None
            trades_today = 0
            
            for b in bars:
                # 15:55 IST Square off
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos is not None:
                        pnl_r = (b['close'] - pos['entry']) / pos['sl_dist'] if pos['type'] == 'BUY' else (pos['entry'] - b['close']) / pos['sl_dist']
                        day_pnl += pnl_r * 0.010
                        trades.append({'date': curr_d, 'symbol': sym, 'pnl_r': pnl_r})
                        pos = None
                    break
                    
                if pos is None and trades_today == 0:
                    # Entry window: 11:00 AM to end_entry_h (e.g. 13:30 or 14:00)
                    if b['ist_hour'] > end_entry_h:
                        break
                        
                    atr_v = b['atr']
                    if atr_v <= 0:
                        continue
                        
                    dev = b['close'] - prev_vpoc
                    dev_atr = dev / atr_v
                    
                    sl_dist = sl_mult * atr_v
                    tp_dist = tp_mult * atr_v
                    
                    body = abs(b['close'] - b['open'])
                    total_bar = b['high'] - b['low'] if (b['high'] - b['low']) > 0 else 1e-5
                    
                    # BULLISH ABSORPTION REVERSION (Price is far below VPOC and prints rejection hammer)
                    # Lower wick >= 40% of bar and close in upper half
                    lower_wick = min(b['open'], b['close']) - b['low']
                    is_bull_rejection = (lower_wick / total_bar >= 0.35) or (b['close'] > b['open']) if use_rejection_wick else (b['close'] > b['open'])
                    
                    # BEARISH ABSORPTION REVERSION (Price is far above VPOC and prints rejection shooting star)
                    upper_wick = b['high'] - max(b['open'], b['close'])
                    is_bear_rejection = (upper_wick / total_bar >= 0.35) or (b['close'] < b['open']) if use_rejection_wick else (b['close'] < b['open'])
                    
                    if dev_atr <= -min_dev_atr and is_bull_rejection:
                        pos = {
                            'type': 'BUY',
                            'entry': b['close'],
                            'sl': b['close'] - sl_dist,
                            'tp': b['close'] + tp_dist,
                            'sl_dist': sl_dist,
                            'tp_dist': tp_dist,
                            'rr': tp_dist / sl_dist
                        }
                        trades_today += 1
                    elif dev_atr >= min_dev_atr and is_bear_rejection:
                        pos = {
                            'type': 'SELL',
                            'entry': b['close'],
                            'sl': b['close'] + sl_dist,
                            'tp': b['close'] - tp_dist,
                            'sl_dist': sl_dist,
                            'tp_dist': tp_dist,
                            'rr': tp_dist / sl_dist
                        }
                        trades_today += 1
                elif pos is not None:
                    if pos['type'] == 'BUY':
                        if b['high'] >= pos['tp']:
                            day_pnl += pos['rr'] * 0.010
                            trades.append({'date': curr_d, 'symbol': sym, 'pnl_r': pos['rr']})
                            pos = None
                            break
                        elif b['low'] <= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            trades.append({'date': curr_d, 'symbol': sym, 'pnl_r': -1.0})
                            pos = None
                            break
                    else:
                        if b['low'] <= pos['tp']:
                            day_pnl += pos['rr'] * 0.010
                            trades.append({'date': curr_d, 'symbol': sym, 'pnl_r': pos['rr']})
                            pos = None
                            break
                        elif b['high'] >= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            trades.append({'date': curr_d, 'symbol': sym, 'pnl_r': -1.0})
                            pos = None
                            break
                            
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
    overall_green_pct = (green_m / total_m) * 100.0 if total_m > 0 else 0
    
    m_2026 = monthly[monthly.index.astype(str).str.startswith('2026')]
    green_26 = (m_2026 > 0).sum()
    
    cum = (1.0 + df_d['pnl_pct']).cumprod()
    dd = (cum / cum.cummax() - 1.0).min() * 100.0
    
    return {
        'avg_annual': yearly.mean(),
        'min_year': yearly.min(),
        'overall_green': f"{green_m}/{total_m} ({overall_green_pct:.1f}%)",
        'overall_green_pct': overall_green_pct,
        'green_2026': f"{green_26}/11 ({green_26/11*100:.1f}%)",
        'green_2026_cnt': green_26,
        'win_rate': wr,
        'total_trades': len(df_t),
        'max_dd': abs(dd),
        'yearly': yearly.to_dict(),
        'm_2026': m_2026.to_dict()
    }

def main():
    print("Loading historical data for VPOC Reversion Optimizer...")
    data = load_data()
    print("Data loaded. Starting grid search...\n")
    
    grid = [
        # (min_dev, sl, tp, rejection_wick, end_h, label)
        (0.8, 0.70, 1.10, False, 13, "1. Dev >= 0.8x ATR, SL=0.70, TP=1.10 (1.57:1 R:R)"),
        (1.0, 0.70, 1.10, False, 13, "2. Dev >= 1.0x ATR, SL=0.70, TP=1.10 (1.57:1 R:R)"),
        (1.2, 0.70, 1.10, False, 13, "3. Dev >= 1.2x ATR, SL=0.70, TP=1.10 (1.57:1 R:R)"),
        (1.0, 0.65, 1.15, True, 13,  "4. Rejection Wick + Dev >= 1.0x ATR, SL=0.65, TP=1.15 (1.77:1)"),
        (1.0, 0.60, 1.20, True, 13,  "5. Rejection Wick + Dev >= 1.0x ATR, SL=0.60, TP=1.20 (2.00:1)"),
        (1.2, 0.60, 1.20, True, 13,  "6. Rejection Wick + Dev >= 1.2x ATR, SL=0.60, TP=1.20 (2.00:1)"),
        (1.0, 0.70, 1.25, True, 14,  "7. Window to 14:00 IST + Rejection Wick + SL=0.70, TP=1.25 (1.78:1)"),
        (1.2, 0.70, 1.25, True, 14,  "8. Window to 14:00 IST + Dev >= 1.2x + Rejection Wick (1.78:1)"),
    ]
    
    results = []
    for dev, sl, tp, wick, end_h, label in grid:
        res = evaluate_vpoc_strategy(data, min_dev_atr=dev, sl_mult=sl, tp_mult=tp, use_rejection_wick=wick, end_entry_h=end_h)
        res['label'] = label
        results.append(res)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}% ({res['total_trades']} trades)")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)
        
    results.sort(key=lambda x: (x['overall_green_pct'], x['green_2026_cnt'], x['avg_annual']), reverse=True)
    
    print("\n" + "=" * 90)
    print("                 TOP OPTIMIZED VPOC REVERSION CONFIGURATIONS                ")
    print("=" * 90)
    for i, r in enumerate(results[:3], 1):
        print(f"\nRANK #{i}: {r['label']}")
        print(f"  * Overall Consistency: {r['overall_green']}")
        print(f"  * 2026 Consistency   : {r['green_2026']}")
        print(f"  * Avg Annual ROI     : +{r['avg_annual']:.2f}% (Worst Year: +{r['min_year']:.2f}%)")
        print(f"  * Win Rate / Max DD  : {r['win_rate']:.1f}% Win Rate | {r['max_dd']:.2f}% Max DD")
        print(f"  * Yearly Walk        : " + ", ".join([f"{y}: +{v:.1f}%" for y, v in r['yearly'].items()]))

if __name__ == '__main__':
    main()

