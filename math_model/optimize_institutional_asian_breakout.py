"""
INSTITUTIONAL ASIAN BREAKOUT OPTIMIZATION (2022 - 2026)
Assets: XAUUSD, EURUSD
Hours: Asian Range measured from 06:00 to 12:30 IST.
Execution Window: 12:30 to 16:00 IST (Auto-square-off at 15:55 IST).

Key Enhancements Tested:
1. Candle Close Confirmation (M5 Close outside Asian High/Low vs Blind Stop Order)
2. Breakout Bar Stop Loss (SL placed at Breakout Bar Low/High + buffer for tight risk)
3. Trend Filter (H1 50 EMA slope/alignment)
4. Target Calibration: 1.5x, 1.8x, 2.0x, 2.2x ATR
5. Asian Coiled Spring Filter (Asian Range < 2.5x to 4.0x M5 ATR)
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
        df['ema50'] = df['close'].ewm(span=600, adjust=False).mean()
        
        df_ist = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 50:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'ema50']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate(asset_data, entry_mode="close", use_trend=False, sl_mode="atr", sl_mult=0.75, tp_mult=1.50, max_asian_ratio=None):
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
            
            asian_bars = [b for b in bars if b['ist_hour'] < 12 or (b['ist_hour'] == 12 and b['ist_minute'] <= 30)]
            exec_bars = [b for b in bars if b['ist_hour'] > 12 or (b['ist_hour'] == 12 and b['ist_minute'] > 30)]
            
            if len(asian_bars) < 20 or len(exec_bars) < 15:
                continue
                
            asian_high = max(b['high'] for b in asian_bars)
            asian_low = min(b['low'] for b in asian_bars)
            asian_range = asian_high - asian_low
            
            base_bar = asian_bars[-1]
            atr_v = base_bar['atr']
            ema_v = base_bar['ema50']
            if atr_v <= 0:
                continue
                
            # Coiled spring filter
            if max_asian_ratio is not None and asian_range > (max_asian_ratio * atr_v):
                continue
                
            buy_allowed = True
            sell_allowed = True
            if use_trend:
                buy_allowed = (base_bar['close'] >= ema_v)
                sell_allowed = (base_bar['close'] <= ema_v)
                
            pos = None
            
            for b in exec_bars:
                # 15:55 IST Square off
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos is not None:
                        pnl_r = (b['close'] - pos['entry']) / pos['sl_dist'] if pos['type'] == 'BUY' else (pos['entry'] - b['close']) / pos['sl_dist']
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        pos = None
                    break
                    
                if pos is None:
                    if b['ist_hour'] >= 15: # 15:00 order cutoff
                        break
                        
                    triggered = False
                    ttype = None
                    entry_p = 0.0
                    sl_dist = 0.0
                    
                    if entry_mode == "close":
                        # M5 Candle CLOSE outside Asian Range
                        if buy_allowed and b['close'] > asian_high:
                            triggered = True
                            ttype = 'BUY'
                            entry_p = b['close']
                            if sl_mode == "bar":
                                sl_dist = max(entry_p - b['low'], 0.5 * atr_v)
                            else:
                                sl_dist = sl_mult * atr_v
                        elif sell_allowed and b['close'] < asian_low:
                            triggered = True
                            ttype = 'SELL'
                            entry_p = b['close']
                            if sl_mode == "bar":
                                sl_dist = max(b['high'] - entry_p, 0.5 * atr_v)
                            else:
                                sl_dist = sl_mult * atr_v
                    else: # "touch" (Stop order)
                        buy_trig = asian_high + (0.08 * atr_v)
                        sell_trig = asian_low - (0.08 * atr_v)
                        if buy_allowed and b['high'] >= buy_trig:
                            triggered = True
                            ttype = 'BUY'
                            entry_p = buy_trig
                            sl_dist = sl_mult * atr_v
                        elif sell_allowed and b['low'] <= sell_trig:
                            triggered = True
                            ttype = 'SELL'
                            entry_p = sell_trig
                            sl_dist = sl_mult * atr_v
                            
                    if triggered:
                        tp_dist = tp_mult * atr_v
                        pos = {
                            'type': ttype,
                            'entry': entry_p,
                            'sl': entry_p - sl_dist if ttype == 'BUY' else entry_p + sl_dist,
                            'tp': entry_p + tp_dist if ttype == 'BUY' else entry_p - tp_dist,
                            'sl_dist': sl_dist,
                            'tp_dist': tp_dist
                        }
                else:
                    ttype = pos['type']
                    entry_p = pos['entry']
                    sl_dist = pos['sl_dist']
                    tp_dist = pos['tp_dist']
                    rr = tp_dist / sl_dist
                    
                    if ttype == 'BUY':
                        if b['high'] >= pos['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            pos = None
                            break
                        elif b['low'] <= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            pos = None
                            break
                    else:
                        if b['low'] <= pos['tp']:
                            day_pnl += rr * 0.010
                            total_trades += 1
                            wins += 1
                            pos = None
                            break
                        elif b['high'] >= pos['sl']:
                            day_pnl += -1.0 * 0.010
                            total_trades += 1
                            pos = None
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

def main():
    print("Loading historical data for Institutional Asian Breakout Optimization...")
    data = load_data()
    print("Data loaded. Starting grid search...\n")
    
    grid = [
        # (entry_mode, use_trend, sl_mode, sl_mult, tp_mult, max_asian_ratio, label)
        ("close", False, "atr", 0.75, 1.45, None, "1. Candle Close Breakout (M5 Close > Asian High, SL 0.75, TP 1.45)"),
        ("close", True, "atr", 0.75, 1.45, None, "2. Candle Close + H1 Trend Filter (SL 0.75, TP 1.45)"),
        ("close", True, "bar", 0.75, 1.45, None, "3. Candle Close + Trend Filter + Breakout Bar SL"),
        ("close", True, "atr", 0.70, 1.40, None, "4. Candle Close + Trend Filter (SL 0.70, TP 1.40 = 2.0:1)"),
        ("close", True, "atr", 0.70, 1.50, None, "5. Candle Close + Trend Filter (SL 0.70, TP 1.50 = 2.14:1)"),
        ("close", True, "atr", 0.75, 1.65, None, "6. Candle Close + Trend Filter (SL 0.75, TP 1.65 = 2.20:1)"),
        ("close", True, "atr", 0.70, 1.40, 8.0, "7. Candle Close + Trend Filter + Asian Compression < 8.0x ATR"),
        ("close", True, "atr", 0.70, 1.40, 6.0, "8. Candle Close + Trend Filter + Asian Compression < 6.0x ATR"),
        ("touch", True, "atr", 0.70, 1.40, None, "9. Stop Order + Trend Filter (SL 0.70, TP 1.40)"),
        ("touch", True, "atr", 0.65, 1.35, None, "10. Stop Order + Trend Filter (SL 0.65, TP 1.35 = 2.08:1)"),
    ]
    
    results = []
    for em, tr, slm, sl, tp, mar, label in grid:
        res = evaluate(data, entry_mode=em, use_trend=tr, sl_mode=slm, sl_mult=sl, tp_mult=tp, max_asian_ratio=mar)
        res['label'] = label
        results.append(res)
        print(f"[{label}]")
        print(f"   Avg Annual ROI: +{res['avg_annual']:.2f}% | Worst Year: +{res['min_year']:.2f}% | Max DD: {res['max_dd']:.2f}%")
        print(f"   Overall Green : {res['overall_green']} | 2026 Green: {res['green_2026']} | Win Rate: {res['win_rate']:.1f}% ({res['total_trades']} trades)")
        print(f"   2026 Months   : " + ", ".join([f"{str(m)[5:]}: {v:+.1f}%" for m, v in res['m_2026'].items()]))
        print("-" * 80)
        
    results.sort(key=lambda x: (x['overall_green_pct'], x['green_2026_cnt'], x['avg_annual']), reverse=True)
    
    print("\n" + "=" * 90)
    print("                 TOP OPTIMIZED ASIAN BREAKOUT CONFIGURATIONS                ")
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

