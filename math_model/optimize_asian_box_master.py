"""
ADVANCED OPTIMIZATION: ASIAN RANGE BREAKOUT
Assets: XAUUSD, EURUSD (2022 - 2026)
Hours: Asian Range (06:00 to 12:30 IST)
Trade Window: 12:30 to 16:00 IST (Auto-square-off at 15:55 IST)

Target: Transform +52% ROI / 62% Green Months into an Institutional Engine:
1. Dynamic Extension Targets (Target = 0.5x, 0.75x, 1.0x of Asian Range vs ATR TP)
2. Asymmetric R:R tuning (SL = 0.5x to 0.8x ATR, TP = 1.2x to 1.8x ATR)
3. Trend Confirmation: Only buy Asian High if 12:30 close > EMA 50; only sell Asian Low if close < EMA 50
4. Asset-Specific tuning for Gold vs EURUSD
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
        df['ema50'] = df['close'].ewm(span=600, adjust=False).mean() # M5 equivalent of H1 EMA 50
        
        df_ist = df[(df['ist_hour'] >= 6) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 50:
                sessions[d] = sub[['ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'ema50']].to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def evaluate(asset_data, use_trend_filter=False, tp_mode="atr", sl_mult=0.70, tp_mult=1.40, range_tp_mult=0.75, entry_buf_mult=0.08):
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
            if atr_v <= 0 or asian_range <= 0:
                continue
                
            buy_allowed = True
            sell_allowed = True
            if use_trend_filter:
                buy_allowed = (base_bar['close'] >= ema_v)
                sell_allowed = (base_bar['close'] <= ema_v)
                
            buy_trigger = asian_high + (entry_buf_mult * atr_v)
            sell_trigger = asian_low - (entry_buf_mult * atr_v)
            
            sl_dist = sl_mult * atr_v
            
            if tp_mode == "atr":
                tp_dist = tp_mult * atr_v
            else: # "range"
                tp_dist = max(range_tp_mult * asian_range, 1.0 * atr_v)
                
            rr = tp_dist / sl_dist
            
            pos = None
            
            for b in exec_bars:
                if b['ist_hour'] == 15 and b['ist_minute'] >= 55:
                    if pos is not None:
                        pnl_r = (b['close'] - pos['entry']) / sl_dist if pos['type'] == 'BUY' else (pos['entry'] - b['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        total_trades += 1
                        if pnl_r > 0:
                            wins += 1
                        pos = None
                    break
                    
                if pos is None:
                    if b['ist_hour'] >= 15: # 15:00 order cutoff
                        break
                    if buy_allowed and b['high'] >= buy_trigger:
                        pos = {'type': 'BUY', 'entry': buy_trigger, 'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist}
                    elif sell_allowed and b['low'] <= sell_trigger:
                        pos = {'type': 'SELL', 'entry': sell_trigger, 'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist}
                else:
                    if pos['type'] == 'BUY':
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
                    else: # SELL
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
    print("Loading data for Asian Range Deep Optimization...")
    data = load_data()
    print("Data loaded. Starting grid search...\n")
    
    grid = [
        # (trend, tp_mode, sl, tp, range_tp, buf, label)
        (False, "atr", 0.75, 1.45, 0.75, 0.08, "1. Unfiltered Asian Breakout (SL 0.75x, TP 1.45x ATR = 1.93:1)"),
        (True, "atr", 0.75, 1.45, 0.75, 0.08, "2. Trend-Filtered (Trade only with H1 EMA 50 trend)"),
        (False, "range", 0.75, 1.45, 0.50, 0.08, "3. Range Target: TP = 50% of Asian Range"),
        (False, "range", 0.75, 1.45, 0.75, 0.08, "4. Range Target: TP = 75% of Asian Range"),
        (False, "range", 0.75, 1.45, 1.00, 0.08, "5. Range Target: TP = 100% of Asian Range (Measured Move)"),
        (True, "range", 0.75, 1.45, 0.75, 0.08, "6. Trend-Filtered + TP = 75% of Asian Range"),
        (False, "atr", 0.65, 1.30, 0.75, 0.08, "7. Tighter Risk: SL=0.65, TP=1.30 (2.0:1 R:R)"),
        (False, "atr", 0.70, 1.25, 0.75, 0.08, "8. High-Probability TP: SL=0.70, TP=1.25 (1.78:1 R:R)"),
        (False, "atr", 0.70, 1.40, 0.75, 0.12, "9. Wider Buffer (0.12x ATR) + SL=0.70, TP=1.40"),
        (True, "atr", 0.70, 1.40, 0.75, 0.12, "10. Trend Filter + Buffer 0.12x + SL=0.70, TP=1.40"),
    ]
    
    results = []
    for tr, tp_m, sl, tp, r_tp, buf, label in grid:
        res = evaluate(data, use_trend_filter=tr, tp_mode=tp_m, sl_mult=sl, tp_mult=tp, range_tp_mult=r_tp, entry_buf_mult=buf)
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

