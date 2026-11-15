"""
INSTITUTIONAL SOLUTION: FRICTION-IMMUNE SESSION ANCHOR SCALING
Testing ATR target scaling to make the 12:00 & 13:00 IST Snipers completely immune
to real broker spreads (Vantage: Gold 0.29, EURUSD 1.4 pips), $6/lot commission, and 345ms slippage.

Hypothesis:
When Stop Loss is micro (2.5 pips), friction destroys 95% of the edge.
When Stop Loss is scaled to Session ATR (15-25 pips on EURUSD, $6-$12 on Gold),
friction represents < 8% of the trade distance, preserving massive net alpha!
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_robust_data():
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
        
        # Calculate H1 ATR (Hourly ATR: 12 bars of M5)
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        # Daily / Session ATR proxy (rolling 288 bars = 24h, or 72 bars = 6h)
        df['session_atr'] = tr.rolling(72, min_periods=20).mean().bfill() * 2.5 # Scales to realistic session move
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 40:
                sessions[d] = sub.to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def simulate_scaled_sniper(
    asset_data,
    anchor_hour=13,
    anchor_minute=0,
    order_cutoff_h=15,
    order_cutoff_m=0,
    entry_offset_pips=5.0,  # entry buffer
    sl_pips=18.0,           # robust SL (18 pips on EURUSD, $6.00 on Gold)
    tp_pips=36.0,           # 2.0:1 R:R (36 pips on EURUSD, $12.00 on Gold)
    # Live Real-World Frictions (Tier 1 Live Vantage)
    apply_friction=True
):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
    # Real Vantage friction parameters
    spread_gold = 0.30 if apply_friction else 0.0
    spread_eur = 0.00014 if apply_friction else 0.0 # 1.4 pips live Vantage
    comm_per_lot = 6.00 if apply_friction else 0.0   # $6 per round lot
    slip_entry_gold = 0.12 if apply_friction else 0.0 # 345ms latency slippage
    slip_entry_eur = 0.00006 if apply_friction else 0.0
    slip_exit_gold = 0.06 if apply_friction else 0.0
    slip_exit_eur = 0.00004 if apply_friction else 0.0
    
    daily_results = []
    trade_stats = []
    
    for curr_d in all_dates:
        day_pnl = 0.0
        day_trades = 0
        
        for sym in symbols:
            sessions = asset_data[sym]
            if curr_d not in sessions:
                continue
            bars = sessions[curr_d]
            
            anchor_idx = -1
            for idx, b in enumerate(bars):
                if b['ist_hour'] == anchor_hour and b['ist_minute'] == anchor_minute:
                    anchor_idx = idx
                    break
            if anchor_idx == -1 or anchor_idx >= len(bars) - 6:
                continue
                
            base_p = bars[anchor_idx]['close']
            is_gold = ("XAU" in sym)
            
            # Asset scaling: pips to price
            # For EURUSD: 1 pip = 0.00010
            # For Gold: 1 "pip" = $0.35 (so sl_pips 18 = $6.30, tp_pips 36 = $12.60)
            pip_unit = 0.35 if is_gold else 0.00010
            contract_sz = 100.0 if is_gold else 100000.0
            spread = spread_gold if is_gold else spread_eur
            slip_entry = slip_entry_gold if is_gold else slip_entry_eur
            slip_exit = slip_exit_gold if is_gold else slip_exit_eur
            
            entry_dist = entry_offset_pips * pip_unit
            sl_dist = sl_pips * pip_unit
            tp_dist = tp_pips * pip_unit
            
            buy_trig = base_p + entry_dist
            sell_trig = base_p - entry_dist
            
            # 1.0% risk per trade on $10,000 account = $100
            cash_risk = 100.0
            lots = cash_risk / (sl_dist * contract_sz)
            lots = max(0.01, round(lots, 2))
            comm_cost = (lots * comm_per_lot) / 10000.0
            
            active = None
            
            for i in range(anchor_idx + 1, len(bars)):
                b = bars[i]
                h = b['ist_hour']
                m = b['ist_minute']
                
                # Auto square off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        if active['type'] == 'BUY':
                            exit_p = b['close'] - (spread / 2.0) - slip_exit
                            net_pts = exit_p - active['fill_entry']
                        else:
                            exit_p = b['close'] + (spread / 2.0) + slip_exit
                            net_pts = active['fill_entry'] - exit_p
                            
                        dollar_pnl = net_pts * lots * contract_sz
                        trade_pnl_pct = (dollar_pnl / 10000.0) - comm_cost
                        r_pnl = trade_pnl_pct / 0.010
                        day_pnl += trade_pnl_pct
                        day_trades += 1
                        trade_stats.append(r_pnl)
                        active = None
                    break
                    
                if active is None:
                    if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
                        break
                    if b['high'] >= buy_trig:
                        fill_p = buy_trig + (spread / 2.0) + slip_entry
                        active = {
                            'type': 'BUY',
                            'fill_entry': fill_p,
                            'sl': buy_trig - sl_dist,
                            'tp': buy_trig + tp_dist
                        }
                    elif b['low'] <= sell_trig:
                        fill_p = sell_trig - (spread / 2.0) - slip_entry
                        active = {
                            'type': 'SELL',
                            'fill_entry': fill_p,
                            'sl': sell_trig + sl_dist,
                            'tp': sell_trig - tp_dist
                        }
                else:
                    ttype = active['type']
                    fill_e = active['fill_entry']
                    sl_p = active['sl']
                    tp_p = active['tp']
                    
                    if ttype == 'BUY':
                        if b['high'] >= tp_p:
                            exit_p = tp_p - (spread / 2.0) - slip_exit
                            dollar_pnl = (exit_p - fill_e) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - comm_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                        elif b['low'] <= sl_p:
                            exit_p = sl_p - (spread / 2.0) - slip_exit
                            dollar_pnl = (exit_p - fill_e) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - comm_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                    else: # SELL
                        if b['low'] <= tp_p:
                            exit_p = tp_p + (spread / 2.0) + slip_exit
                            dollar_pnl = (fill_e - exit_p) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - comm_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                        elif b['high'] >= sl_p:
                            exit_p = sl_p + (spread / 2.0) + slip_exit
                            dollar_pnl = (fill_e - exit_p) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - comm_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                            
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
        avg_trade_r = ts.mean()
    else:
        wr = 0
        pf = 0
        avg_trade_r = 0
        
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
        'avg_r': f"{avg_trade_r:+.3f}R",
        'annual_dict': annual.to_dict(),
        'df_res': df_res,
        'trade_stats': trade_stats
    }

def main():
    print("Loading data from MT5...")
    asset_data = load_robust_data()
    print("Data loaded. Testing Target Scaling against Live Frictions...\n")
    
    # Sweep of Stop Loss & TP distances to find the sweet spot where broker friction is negligible (<8%)
    configs = [
        {"entry": 3.0, "sl": 10.0, "tp": 20.0, "name": "Scale A: SL 10 pips / TP 20 pips ($3.50 SL / $7 TP Gold)"},
        {"entry": 5.0, "sl": 15.0, "tp": 30.0, "name": "Scale B: SL 15 pips / TP 30 pips ($5.25 SL / $10.5 TP Gold)"},
        {"entry": 6.0, "sl": 18.0, "tp": 36.0, "name": "Scale C: SL 18 pips / TP 36 pips ($6.30 SL / $12.6 TP Gold)"},
        {"entry": 8.0, "sl": 22.0, "tp": 44.0, "name": "Scale D: SL 22 pips / TP 44 pips ($7.70 SL / $15.4 TP Gold)"},
        {"entry": 10.0, "sl": 25.0, "tp": 50.0, "name": "Scale E: SL 25 pips / TP 50 pips ($8.75 SL / $17.5 TP Gold)"}
    ]
    
    print("=" * 130)
    print(f"{'TARGET SCALING CONFIGURATION':<50} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'AVG R':<7}")
    print("=" * 130)
    
    for c in configs:
        # Run with live Vantage friction
        df_13, ts_13 = simulate_scaled_sniper(asset_data, anchor_hour=13, anchor_minute=0, entry_offset_pips=c["entry"], sl_pips=c["sl"], tp_pips=c["tp"], apply_friction=True)
        r = analyze(df_13, ts_13, c["name"])
        print(f"{r['label']:<50} | {r['avg_annual']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['pf']:<5} | {r['avg_r']:<7}")
        
    print("=" * 130)

if __name__ == "__main__":
    main()

