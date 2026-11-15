"""
INSTITUTIONAL STRATEGY COMPARISON & SELECTION ENGINE (2022-2026)
Benchmark: Champion 1:00 PM Sniper (+136.88% ROI, 91.5% Green Months, 21.09% Max DD)

Candidates:
1. Frankfurt Pre-Open Sniper (12:00 PM IST Base Anchor)
2. Frankfurt Cash Open Sniper (12:30 PM IST Base Anchor)
3. Intraday Session VWAP Band Absorption Scalper (11:00 AM - 13:30 IST)
4. Combined Ensemble (Champion 1:00 PM + Selected Winner)
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data():
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
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
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Keep full 11:00 to 16:00 IST session
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 40:
                sessions[d] = sub.copy().reset_index(drop=True)
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def simulate_anchor_sniper(asset_data, anchor_hour, anchor_minute, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=15, order_cutoff_m=0):
    """Simulates an anchor-based breakout sniper (like 12:00, 12:30, or 13:00 IST)."""
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    daily_results = []
    trade_stats = []
    
    rr = tp_mult / sl_mult
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            df_day = sessions[curr_d]
            
            # Find anchor bar
            anchor_idx = df_day[(df_day['ist_hour'] == anchor_hour) & (df_day['ist_minute'] == anchor_minute)].index
            if len(anchor_idx) == 0:
                continue
            a_idx = anchor_idx[0]
            if a_idx >= len(df_day) - 6:
                continue
                
            base_p = df_day.loc[a_idx, 'close']
            atr_v = df_day.loc[a_idx, 'atr']
            if atr_v <= 0 or np.isnan(atr_v):
                continue
                
            entry_dist = entry_mult * atr_v
            sl_dist = sl_mult * atr_v
            tp_dist = tp_mult * atr_v
            
            buy_trig = base_p + entry_dist
            sell_trig = base_p - entry_dist
            
            active = None
            
            for i in range(a_idx + 1, len(df_day)):
                row = df_day.loc[i]
                h = row['ist_hour']
                m = row['ist_minute']
                
                # Auto square off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        pnl_r = (row['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - row['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        day_trades += 1
                        trade_stats.append(pnl_r)
                        active = None
                    break
                    
                if active is None:
                    # Check cutoff for order validity
                    if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
                        break
                    if row['high'] >= buy_trig:
                        active = {'type': 'BUY', 'entry': buy_trig, 'sl': buy_trig - sl_dist, 'tp': buy_trig + tp_dist}
                    elif row['low'] <= sell_trig:
                        active = {'type': 'SELL', 'entry': sell_trig, 'sl': sell_trig + sl_dist, 'tp': sell_trig - tp_dist}
                else:
                    ttype = active['type']
                    if ttype == 'BUY':
                        if row['high'] >= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif row['low'] <= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                    else:
                        if row['low'] <= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif row['high'] >= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                            
        # Cap daily loss at -2.00%
        if day_pnl < -0.020:
            day_pnl = -0.020
            
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_res = pd.DataFrame(daily_results)
    return df_res, trade_stats

def simulate_vwap_scalper(asset_data, band_sigma=1.5, sl_mult=0.65, tp_mult=1.30):
    """
    Intraday Session VWAP Band Absorption Scalper:
    Operates 11:15 AM to 13:30 IST.
    Computes Cumulative VWAP and Std Dev from 11:00 AM IST.
    When price pierces Upper Band and closes back inside (Bearish absorption) -> Short.
    When price pierces Lower Band and closes back inside (Bullish absorption) -> Long.
    Single trade per day per asset. Auto-square-off at 15:55 IST.
    """
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    daily_results = []
    trade_stats = []
    rr = tp_mult / sl_mult
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            df_day = sessions[curr_d].copy()
            
            # Calculate session VWAP from 11:00 AM IST
            # Using typical price and volume
            tp = (df_day['high'] + df_day['low'] + df_day['close']) / 3.0
            vol = df_day['tick_volume'].replace(0, 1) if 'tick_volume' in df_day else pd.Series(1, index=df_day.index)
            cum_vol = vol.cumsum()
            cum_pv = (tp * vol).cumsum()
            vwap = cum_pv / cum_vol
            
            # Cumulative std dev
            cum_var = ((tp - vwap) ** 2 * vol).cumsum() / cum_vol
            cum_std = np.sqrt(np.maximum(cum_var, 1e-8))
            
            df_day['vwap'] = vwap
            df_day['upper_band'] = vwap + band_sigma * cum_std
            df_day['lower_band'] = vwap - band_sigma * cum_std
            
            active = None
            taken = False
            
            for i in range(3, len(df_day)):
                row = df_day.iloc[i]
                prev_row = df_day.iloc[i-1]
                h = row['ist_hour']
                m = row['ist_minute']
                atr_v = row['atr']
                
                # Auto square off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        sl_dist = active['sl_dist']
                        pnl_r = (row['close'] - active['entry']) / sl_dist if active['type'] == 'BUY' else (active['entry'] - row['close']) / sl_dist
                        day_pnl += pnl_r * 0.010
                        day_trades += 1
                        trade_stats.append(pnl_r)
                        active = None
                    break
                    
                if active is None and not taken:
                    # Only take trades between 11:15 AM and 13:15 IST
                    if (h == 11 and m >= 15) or (h == 12) or (h == 13 and m <= 15):
                        sl_dist = sl_mult * atr_v
                        tp_dist = tp_mult * atr_v
                        if atr_v <= 0 or np.isnan(atr_v):
                            continue
                            
                        # Bearish absorption: prev pierced upper band, current closes back below upper band
                        if prev_row['high'] > prev_row['upper_band'] and row['close'] < row['upper_band']:
                            active = {
                                'type': 'SELL',
                                'entry': row['close'],
                                'sl': row['close'] + sl_dist,
                                'tp': row['close'] - tp_dist,
                                'sl_dist': sl_dist
                            }
                            taken = True
                        # Bullish absorption: prev pierced lower band, current closes back above lower band
                        elif prev_row['low'] < prev_row['lower_band'] and row['close'] > row['lower_band']:
                            active = {
                                'type': 'BUY',
                                'entry': row['close'],
                                'sl': row['close'] - sl_dist,
                                'tp': row['close'] + tp_dist,
                                'sl_dist': sl_dist
                            }
                            taken = True
                elif active is not None:
                    ttype = active['type']
                    if ttype == 'BUY':
                        if row['high'] >= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif row['low'] <= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                    else:
                        if row['low'] <= active['tp']:
                            day_pnl += rr * 0.010
                            day_trades += 1
                            trade_stats.append(rr)
                            active = None
                            break
                        elif row['high'] >= active['sl']:
                            day_pnl -= 1.0 * 0.010
                            day_trades += 1
                            trade_stats.append(-1.0)
                            active = None
                            break
                            
        if day_pnl < -0.020:
            day_pnl = -0.020
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': day_trades})
        
    df_res = pd.DataFrame(daily_results)
    return df_res, trade_stats

def analyze_strategy(df_res, trade_stats, name="Strategy"):
    df_res['year'] = pd.to_datetime(df_res['date']).dt.year
    df_res['ym'] = pd.to_datetime(df_res['date']).dt.strftime('%Y-%m')
    
    # Equity curve and Max Drawdown
    equity = (1.0 + df_res['pnl_pct']).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min() * 100.0
    
    # Monthly returns
    monthly = df_res.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0)
    total_months = len(monthly)
    green_months = (monthly > 0).sum()
    pct_green = (green_months / total_months * 100.0) if total_months > 0 else 0
    
    # 2026 consistency
    m2026 = monthly[monthly.index.str.startswith('2026')]
    g2026 = (m2026 > 0).sum()
    t2026 = len(m2026)
    
    # Annual breakdown
    annual = df_res.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
    avg_annual = annual.mean()
    worst_year = annual.min()
    
    # Win rate and Profit Factor
    if len(trade_stats) > 0:
        ts = np.array(trade_stats)
        wr = (ts > 0).sum() / len(ts) * 100.0
        gains = ts[ts > 0].sum()
        losses = abs(ts[ts < 0].sum())
        pf = gains / losses if losses > 0 else np.nan
    else:
        wr = 0
        pf = 0
        
    res = {
        'name': name,
        'avg_annual': avg_annual,
        'worst_year': worst_year,
        'max_dd': max_dd,
        'total_months': total_months,
        'green_months': green_months,
        'pct_green': pct_green,
        'm2026_green': f"{g2026}/{t2026}",
        'trade_count': len(trade_stats),
        'win_rate': wr,
        'profit_factor': pf,
        'annual_dict': annual.to_dict(),
        'monthly_series': monthly
    }
    return res

def print_result_card(r):
    print("=" * 80)
    print(f"STRATEGY: {r['name']}")
    print("=" * 80)
    print(f"Avg Annual Return : +{r['avg_annual']:.2f}%")
    print(f"Worst Year Return : +{r['worst_year']:.2f}%")
    print(f"Max Drawdown      : {r['max_dd']:.2f}%")
    print(f"5-Year Green Mths : {r['green_months']}/{r['total_months']} ({r['pct_green']:.1f}%)")
    print(f"2026 Consistency  : {r['m2026_green']} green months")
    print(f"Trade Count       : {r['trade_count']} trades")
    print(f"Trade Win Rate    : {r['win_rate']:.1f}%")
    print(f"Profit Factor     : {r['profit_factor']:.2f}")
    print("Annual Returns:")
    for yr, ret in r['annual_dict'].items():
        print(f"   {yr}: {ret:+.2f}%")
    print("=" * 80)

def main():
    print("Loading tick/M5 data from MT5...")
    asset_data = load_data()
    print("Data loaded successfully.")
    
    print("\n--- BENCHMARK: 1:00 PM Golden Base Sniper ---")
    df_1300, ts_1300 = simulate_anchor_sniper(asset_data, 13, 0, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45)
    res_1300 = analyze_strategy(df_1300, ts_1300, "Benchmark 1:00 PM Sniper (Golden RR 1.93:1)")
    print_result_card(res_1300)
    
    print("\n--- CANDIDATE 1A: Frankfurt Pre-Open Sniper (12:00 PM IST) ---")
    df_1200, ts_1200 = simulate_anchor_sniper(asset_data, 12, 0, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=14, order_cutoff_m=0)
    res_1200 = analyze_strategy(df_1200, ts_1200, "Frankfurt Pre-Open Sniper (12:00 PM IST)")
    print_result_card(res_1200)

    print("\n--- CANDIDATE 1B: Frankfurt Cash Open Sniper (12:30 PM IST) ---")
    df_1230, ts_1230 = simulate_anchor_sniper(asset_data, 12, 30, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=14, order_cutoff_m=30)
    res_1230 = analyze_strategy(df_1230, ts_1230, "Frankfurt Cash Open Sniper (12:30 PM IST)")
    print_result_card(res_1230)
    
    print("\n--- CANDIDATE 2A: Intraday VWAP Band Absorption Scalper (1.5 Sigma) ---")
    df_vwap15, ts_vwap15 = simulate_vwap_scalper(asset_data, band_sigma=1.5, sl_mult=0.65, tp_mult=1.30)
    res_vwap15 = analyze_strategy(df_vwap15, ts_vwap15, "Intraday VWAP Scalper (1.5 Sigma)")
    print_result_card(res_vwap15)

    print("\n--- CANDIDATE 2B: Intraday VWAP Band Absorption Scalper (1.2 Sigma) ---")
    df_vwap12, ts_vwap12 = simulate_vwap_scalper(asset_data, band_sigma=1.2, sl_mult=0.65, tp_mult=1.30)
    res_vwap12 = analyze_strategy(df_vwap12, ts_vwap12, "Intraday VWAP Scalper (1.2 Sigma)")
    print_result_card(res_vwap12)

    # Now let's test the COMBINED PORTFOLIOS with the Champion 1:00 PM Sniper
    # Case A: 1:00 PM Sniper + 12:00 PM Frankfurt Sniper
    df_comb_1200 = pd.merge(df_1300[['date', 'pnl_pct']], df_1200[['date', 'pnl_pct']], on='date', suffixes=('_1300', '_1200'))
    # Combined daily pnl with daily max loss -2.0% circuit breaker
    df_comb_1200['pnl_pct'] = np.maximum(-0.020, df_comb_1200['pnl_pct_1300'] + df_comb_1200['pnl_pct_1200'])
    res_comb_1200 = analyze_strategy(df_comb_1200, ts_1300 + ts_1200, "PORTFOLIO: 1:00 PM Sniper + 12:00 PM Frankfurt Sniper")
    print("\n--- COMBINED ENSEMBLE A ---")
    print_result_card(res_comb_1200)

    # Case B: 1:00 PM Sniper + VWAP Absorption Scalper
    df_comb_vwap = pd.merge(df_1300[['date', 'pnl_pct']], df_vwap15[['date', 'pnl_pct']], on='date', suffixes=('_1300', '_vwap'))
    df_comb_vwap['pnl_pct'] = np.maximum(-0.020, df_comb_vwap['pnl_pct_1300'] + df_comb_vwap['pnl_pct_vwap'])
    res_comb_vwap = analyze_strategy(df_comb_vwap, ts_1300 + ts_vwap15, "PORTFOLIO: 1:00 PM Sniper + VWAP Absorption Scalper")
    print("\n--- COMBINED ENSEMBLE B ---")
    print_result_card(res_comb_vwap)

if __name__ == "__main__":
    main()

