"""
DEEP OPTIMIZATION & DIAGNOSTIC ENGINE: INTRADAY VWAP SCALPER (2022-2026)
Investigating why Candidate 2 underperformed and transforming it into an Institutional-Grade System.

Diagnoses & Upgrades tested:
1. Baseline: 11:00 AM Anchor, 1.5 sigma, naive entry (Previous Candidate 2)
2. Mature Day-Anchored VWAP: Anchored to 05:30 IST (Asian Open), mature volume profile by 11:00 AM IST
3. Extreme Band Sweeps: 1.5 sigma vs 2.0 sigma vs 2.5 sigma (Statistical boundary)
4. Liquidity Sweep Confluence: Asian High/Low Sweep + VWAP Band Rejection (Judas Swing)
5. Macro Trend Filter: Only trade in alignment with 200 EMA / Daily Slope
6. Continuation Model: VWAP Pullback / Bounce (Trend Following instead of Fading)
7. Exit Mechanics: Fixed Positive R:R (1.5:1, 1.93:1) vs Reversion to VWAP Line
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_full_day_data():
    import time
    connected = False
    for attempt in range(5):
        if mt5.initialize():
            connected = True
            break
        time.sleep(1.5)
        
    if not connected:
        raise RuntimeError("MT5 initialization failed after 5 retries")
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            print(f"Failed to fetch rates for {s}")
            continue
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        # ATR 14
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # 200 EMA for Macro Trend
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # We need data from Asian Open (05:30 IST) to End of Day (16:00 IST)
        # So we include hours 5 through 15
        df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        
        sessions = {}
        for d, sub in df_day.groupby('date'):
            # Must have at least 80 bars (from 05:30 to 16:00)
            if len(sub) >= 70:
                sub = sub.copy().reset_index(drop=True)
                
                # Precompute Asian Range (05:30 to 11:00 IST)
                asian_mask = (sub['ist_hour'] < 11) | ((sub['ist_hour'] == 5) & (sub['ist_minute'] >= 30))
                asian_bars = sub[asian_mask]
                if len(asian_bars) >= 20:
                    asian_high = asian_bars['high'].max()
                    asian_low = asian_bars['low'].min()
                else:
                    asian_high = sub['high'].iloc[:20].max()
                    asian_low = sub['low'].iloc[:20].min()
                
                # Compute Day-Anchored VWAP from 05:30 IST
                tp = (sub['high'] + sub['low'] + sub['close']) / 3.0
                vol = sub['tick_volume'].replace(0, 1) if 'tick_volume' in sub else pd.Series(1, index=sub.index)
                cum_vol = vol.cumsum()
                cum_pv = (tp * vol).cumsum()
                vwap = cum_pv / cum_vol
                cum_var = ((tp - vwap) ** 2 * vol).cumsum() / cum_vol
                cum_std = np.sqrt(np.maximum(cum_var, 1e-8))
                
                sub['vwap_day'] = vwap
                sub['std_day'] = cum_std
                sub['asian_high'] = asian_high
                sub['asian_low'] = asian_low
                
                # Also compute 11:00 AM Anchored VWAP (to compare baseline)
                idx_11 = sub[sub['ist_hour'] >= 11].index
                if len(idx_11) > 0:
                    start_i = idx_11[0]
                    sub['vwap_11'] = np.nan
                    sub['std_11'] = np.nan
                    sub_11 = sub.iloc[start_i:].copy()
                    tp11 = (sub_11['high'] + sub_11['low'] + sub_11['close']) / 3.0
                    vol11 = sub_11['tick_volume'].replace(0, 1)
                    cvol11 = vol11.cumsum()
                    cpv11 = (tp11 * vol11).cumsum()
                    v11 = cpv11 / cvol11
                    cvar11 = ((tp11 - v11) ** 2 * vol11).cumsum() / cvol11
                    sub.loc[start_i:, 'vwap_11'] = v11
                    sub.loc[start_i:, 'std_11'] = np.sqrt(np.maximum(cvar11, 1e-8))
                else:
                    sub['vwap_11'] = vwap
                    sub['std_11'] = cum_std
                    
                sessions[d] = sub.to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def simulate_vwap_variant(
    asset_data,
    anchor_mode="day",          # "11am" or "day"
    band_sigma=2.0,             # 1.5, 2.0, 2.5
    strategy_type="reversion",  # "reversion", "reversion_sweep", "trend_pullback"
    trend_filter=False,         # require EMA200 alignment
    tp_mode="fixed",            # "fixed" (ATR multiplier) or "vwap" (target VWAP line)
    sl_mult=0.75,
    tp_mult=1.45,
    entry_start_h=11,
    entry_start_m=15,
    entry_end_h=13,
    entry_end_m=30
):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    daily_results = []
    trade_stats = []
    rr_fixed = tp_mult / sl_mult
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            active = None
            taken = False
            
            for i in range(1, len(bars)):
                b = bars[i]
                prev_b = bars[i-1]
                h = b['ist_hour']
                m = b['ist_minute']
                
                # 1. Hard Auto-Square-Off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        sl_d = active['sl_dist']
                        pnl_r = (b['close'] - active['entry']) / sl_d if active['type'] == 'BUY' else (active['entry'] - b['close']) / sl_d
                        day_pnl += pnl_r * 0.010
                        day_trades += 1
                        trade_stats.append(pnl_r)
                        active = None
                    break
                    
                # 2. Check Exits for active trade
                if active is not None:
                    ttype = active['type']
                    entry_p = active['entry']
                    sl_d = active['sl_dist']
                    sl_p = active['sl']
                    tp_p = active['tp']
                    
                    if ttype == 'BUY':
                        if b['high'] >= tp_p:
                            actual_rr = (tp_p - entry_p) / sl_d
                            day_pnl += actual_rr * 0.010
                            day_trades += 1
                            trade_stats.append(actual_rr)
                            active = None
                            break
                        elif b['low'] <= sl_p:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                    else: # SELL
                        if b['low'] <= tp_p:
                            actual_rr = (entry_p - tp_p) / sl_d
                            day_pnl += actual_rr * 0.010
                            day_trades += 1
                            trade_stats.append(actual_rr)
                            active = None
                            break
                        elif b['high'] >= sl_p:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                    continue
                    
                # 3. Check Entries if not taken today
                if not taken and active is None:
                    # Time window check
                    after_start = (h > entry_start_h) or (h == entry_start_h and m >= entry_start_m)
                    before_end = (h < entry_end_h) or (h == entry_end_h and m <= entry_end_m)
                    
                    if not (after_start and before_end):
                        continue
                        
                    vwap_val = b['vwap_11'] if anchor_mode == "11am" else b['vwap_day']
                    std_val = b['std_11'] if anchor_mode == "11am" else b['std_day']
                    atr_v = b['atr']
                    
                    if np.isnan(vwap_val) or np.isnan(std_val) or std_val <= 0 or atr_v <= 0:
                        continue
                        
                    upper_band = vwap_val + band_sigma * std_val
                    lower_band = vwap_val - band_sigma * std_val
                    
                    prev_upper = prev_b['vwap_day'] + band_sigma * prev_b['std_day']
                    prev_lower = prev_b['vwap_day'] - band_sigma * prev_b['std_day']
                    
                    # Macro trend condition
                    trend_bullish = b['close'] > b['ema200']
                    trend_bearish = b['close'] < b['ema200']
                    
                    # Entry logic based on strategy_type
                    if strategy_type == "reversion":
                        # Pure absorption at band
                        # Short signal: prev penetrated upper band, now closed inside
                        if prev_b['high'] > prev_upper and b['close'] < upper_band:
                            if not trend_filter or trend_bearish: # If trend filter on, only fade if macro allows
                                sl_dist = sl_mult * atr_v
                                tp_dist = tp_mult * atr_v if tp_mode == "fixed" else max(0.5 * atr_v, b['close'] - vwap_val)
                                active = {
                                    'type': 'SELL',
                                    'entry': b['close'],
                                    'sl': b['close'] + sl_dist,
                                    'tp': b['close'] - tp_dist,
                                    'sl_dist': sl_dist
                                }
                                taken = True
                        # Long signal: prev penetrated lower band, now closed inside
                        elif prev_b['low'] < prev_lower and b['close'] > lower_band:
                            if not trend_filter or trend_bullish:
                                sl_dist = sl_mult * atr_v
                                tp_dist = tp_mult * atr_v if tp_mode == "fixed" else max(0.5 * atr_v, vwap_val - b['close'])
                                active = {
                                    'type': 'BUY',
                                    'entry': b['close'],
                                    'sl': b['close'] - sl_dist,
                                    'tp': b['close'] + tp_dist,
                                    'sl_dist': sl_dist
                                }
                                taken = True
                                
                    elif strategy_type == "reversion_sweep":
                        # Liquidity Sweep: Price sweeps Asian High/Low while outside the band!
                        asian_h = b['asian_high']
                        asian_l = b['asian_low']
                        
                        # Bearish Judas Sweep: High pierced Asian High & Upper Band, now closed back below Asian High
                        if prev_b['high'] >= asian_h and prev_b['high'] >= prev_upper and b['close'] < asian_h:
                            if not trend_filter or trend_bearish:
                                sl_dist = max(sl_mult * atr_v, prev_b['high'] - b['close'] + 0.10 * atr_v)
                                tp_dist = tp_mult * atr_v if tp_mode == "fixed" else max(0.5 * atr_v, b['close'] - vwap_val)
                                active = {
                                    'type': 'SELL',
                                    'entry': b['close'],
                                    'sl': b['close'] + sl_dist,
                                    'tp': b['close'] - tp_dist,
                                    'sl_dist': sl_dist
                                }
                                taken = True
                        # Bullish Judas Sweep: Low pierced Asian Low & Lower Band, now closed back above Asian Low
                        elif prev_b['low'] <= asian_l and prev_b['low'] <= prev_lower and b['close'] > asian_l:
                            if not trend_filter or trend_bullish:
                                sl_dist = max(sl_mult * atr_v, b['close'] - prev_b['low'] + 0.10 * atr_v)
                                tp_dist = tp_mult * atr_v if tp_mode == "fixed" else max(0.5 * atr_v, vwap_val - b['close'])
                                active = {
                                    'type': 'BUY',
                                    'entry': b['close'],
                                    'sl': b['close'] - sl_dist,
                                    'tp': b['close'] + tp_dist,
                                    'sl_dist': sl_dist
                                }
                                taken = True
                                
                    elif strategy_type == "trend_pullback":
                        # Trend Pullback: Price is in uptrend (above EMA200), pulls back to touch VWAP midline, bounces!
                        # Bullish bounce: prev touched VWAP midline, current candle closes bullish above VWAP
                        if trend_bullish and prev_b['low'] <= prev_b['vwap_day'] and b['close'] > b['vwap_day'] and b['close'] > b['open']:
                            sl_dist = sl_mult * atr_v
                            tp_dist = tp_mult * atr_v
                            active = {
                                'type': 'BUY',
                                'entry': b['close'],
                                'sl': b['close'] - sl_dist,
                                'tp': b['close'] + tp_dist,
                                'sl_dist': sl_dist
                            }
                            taken = True
                        elif trend_bearish and prev_b['high'] >= prev_b['vwap_day'] and b['close'] < b['vwap_day'] and b['close'] < b['open']:
                            sl_dist = sl_mult * atr_v
                            tp_dist = tp_mult * atr_v
                            active = {
                                'type': 'SELL',
                                'entry': b['close'],
                                'sl': b['close'] + sl_dist,
                                'tp': b['close'] - tp_dist,
                                'sl_dist': sl_dist
                            }
                            taken = True

        # Cap daily loss at -2.00%
        if day_pnl < -0.020:
            day_pnl = -0.020
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_res = pd.DataFrame(daily_results)
    return df_res, trade_stats

def analyze(df_res, trade_stats, label):
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['ym'] = pd.to_datetime(df_res['date']).dt.strftime('%Y-%m')
    
    equity = (1.0 + df_res['pnl_pct']).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min() * 100.0
    
    monthly = df_res.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0)
    total_m = len(monthly)
    green_m = (monthly > 0).sum()
    pct_green = (green_m / total_m * 100.0) if total_m > 0 else 0
    
    annual = df_res.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
    avg_annual = annual.mean()
    worst_yr = annual.min()
    
    m2026 = monthly[monthly.index.str.startswith('2026')]
    g2026 = (m2026 > 0).sum()
    t2026 = len(m2026)
    
    if len(trade_stats) > 0:
        ts = np.array(trade_stats)
        wr = (ts > 0).sum() / len(ts) * 100.0
        gains = ts[ts > 0].sum()
        losses = abs(ts[ts < 0].sum())
        pf = gains / losses if losses > 0 else np.nan
    else:
        wr = 0
        pf = 0
        
    return {
        'label': label,
        'avg_annual': avg_annual,
        'worst_yr': worst_yr,
        'max_dd': max_dd,
        'green_months': f"{green_m}/{total_m} ({pct_green:.1f}%)",
        'm2026': f"{g2026}/{t2026}",
        'trades': len(trade_stats),
        'win_rate': f"{wr:.1f}%",
        'pf': f"{pf:.2f}",
        'annual_dict': annual.to_dict()
    }

def print_summary(res_list):
    print("\n" + "=" * 115)
    print(f"{'VARIANT / OPTIMIZATION':<45} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'2026':<6} | {'PF':<5}")
    print("=" * 115)
    for r in res_list:
        print(f"{r['label']:<45} | {r['avg_annual']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['m2026']:<6} | {r['pf']:<5}")
    print("=" * 115)

def main():
    print("Loading data from MT5 with full Asian & Intraday volume context...")
    asset_data = load_full_day_data()
    print("Data loaded. Starting systematic evaluation & optimization...")
    
    results = []
    
    # 0. Baseline Candidate 2 (11:00 AM Anchor, 1.5 sigma, naive absorption)
    df0, ts0 = simulate_vwap_variant(asset_data, anchor_mode="11am", band_sigma=1.5, strategy_type="reversion", trend_filter=False, tp_mode="fixed", sl_mult=0.65, tp_mult=1.30)
    results.append(analyze(df0, ts0, "0. Baseline Candidate 2 (11am Anchor, 1.5s)"))
    
    # 1. Day-Anchored VWAP (05:30 IST) at 1.5 sigma
    df1, ts1 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=1.5, strategy_type="reversion", trend_filter=False, tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df1, ts1, "1. Day-Anchored VWAP (1.5 sigma)"))

    # 2. Day-Anchored VWAP at 2.0 sigma (True statistical boundary)
    df2, ts2 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=2.0, strategy_type="reversion", trend_filter=False, tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df2, ts2, "2. Day-Anchored VWAP (2.0 sigma)"))

    # 3. Day-Anchored VWAP at 2.5 sigma (Extreme tail reversal)
    df3, ts3 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=2.5, strategy_type="reversion", trend_filter=False, tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df3, ts3, "3. Day-Anchored VWAP (2.5 sigma)"))

    # 4. Day-Anchored VWAP (2.0s) + Reversion to VWAP Midline
    df4, ts4 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=2.0, strategy_type="reversion", trend_filter=False, tp_mode="vwap", sl_mult=0.75)
    results.append(analyze(df4, ts4, "4. Day-Anchored (2.0s) Target VWAP Line"))

    # 5. Day-Anchored VWAP (2.0s) + Macro Trend Filter (EMA 200)
    df5, ts5 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=2.0, strategy_type="reversion", trend_filter=True, tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df5, ts5, "5. Day-Anchored (2.0s) + Trend Filter"))

    # 6. Asian High/Low Judas Sweep + VWAP Band Rejection (Institutional Fakeout)
    df6, ts6 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=1.5, strategy_type="reversion_sweep", trend_filter=False, tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df6, ts6, "6. Asian Liquidity Sweep + VWAP Rejection"))

    # 7. Asian Liquidity Sweep + VWAP Midline Target
    df7, ts7 = simulate_vwap_variant(asset_data, anchor_mode="day", band_sigma=1.5, strategy_type="reversion_sweep", trend_filter=False, tp_mode="vwap", sl_mult=0.75)
    results.append(analyze(df7, ts7, "7. Asian Sweep + Target VWAP Line"))

    # 8. VWAP Pullback Continuation (Trend-following bounce off VWAP)
    df8, ts8 = simulate_vwap_variant(asset_data, anchor_mode="day", strategy_type="trend_pullback", tp_mode="fixed", sl_mult=0.75, tp_mult=1.45)
    results.append(analyze(df8, ts8, "8. Trend-Following VWAP Bounce/Pullback"))

    print_summary(results)
    
    # Detailed breakdown of the best optimization
    best = max(results, key=lambda x: x['avg_annual'] if float(x['pf']) > 1.20 else -999)
    print(f"\nTop Optimized VWAP Strategy: {best['label']}")
    print("Annual breakdown:")
    for yr, val in best['annual_dict'].items():
        print(f"   {yr}: {val:+.2f}%")

if __name__ == "__main__":
    main()
