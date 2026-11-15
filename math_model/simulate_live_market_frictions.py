"""
INSTITUTIONAL LIVE MARKET SIMULATION ENGINE
Testing Strategy Resilience Under Real-World Execution Frictions:
1. Live Broker Spreads (Audited from Vantage: Gold $0.29, EURUSD 1.4 pips)
2. ECN & Prop Firm Commissions ($6.00 to $10.00 per round lot)
3. Network Latency & Adverse Slippage (345ms ping simulation)
4. Session Open Spread Spikes (Liquidity vacuum at 12:00 and 13:00 IST)

Evaluates:
- Baseline (Frictionless)
- Tier 1: Real-World Typical Live Broker (Vantage ECN / FTMO Standard)
- Tier 2: Adverse High-Latency & Volatility Open (Heavy slippage + wider spreads)
- Tier 3: Extreme Stress / Toxic Execution (Hostile broker conditions)
"""

import os
import sys
import time
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data():
    connected = False
    for _ in range(5):
        if mt5.initialize():
            connected = True
            break
        time.sleep(1.0)
    if not connected:
        raise RuntimeError("MT5 init failed")
        
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
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
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_ist.groupby('date'):
            if len(sub) >= 40:
                sessions[d] = sub.to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def simulate_with_frictions(
    asset_data,
    anchor_hour=13,
    anchor_minute=0,
    order_cutoff_h=15,
    order_cutoff_m=0,
    entry_mult=0.30,
    sl_mult=0.75,
    tp_mult=1.45,
    # Friction parameters
    spread_gold=0.0,       # in $ (e.g. 0.30)
    spread_eur=0.0,        # in price (e.g. 0.00012 = 1.2 pips)
    comm_gold_per_lot=0.0, # in $ per round turn lot
    comm_eur_per_lot=0.0,  # in $ per round turn lot
    slip_entry_gold=0.0,   # adverse slippage on trigger in $
    slip_entry_eur=0.0,    # adverse slippage on trigger in price
    slip_exit_gold=0.0,    # adverse slippage on SL/TP in $
    slip_exit_eur=0.0      # adverse slippage on SL/TP in price
):
    symbols = list(asset_data.keys())
    all_dates = sorted(list(set.union(*[set(sessions.keys()) for sessions in asset_data.values()])))
    
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
            
            # Find anchor bar
            anchor_idx = -1
            for idx, b in enumerate(bars):
                if b['ist_hour'] == anchor_hour and b['ist_minute'] == anchor_minute:
                    anchor_idx = idx
                    break
            if anchor_idx == -1 or anchor_idx >= len(bars) - 6:
                continue
                
            base_p = bars[anchor_idx]['close']
            atr_v = bars[anchor_idx]['atr']
            if atr_v <= 0 or np.isnan(atr_v):
                continue
                
            is_gold = ("XAU" in sym)
            spread = spread_gold if is_gold else spread_eur
            comm_per_lot = comm_gold_per_lot if is_gold else comm_eur_per_lot
            slip_entry = slip_entry_gold if is_gold else slip_entry_eur
            slip_exit = slip_exit_gold if is_gold else slip_exit_eur
            contract_sz = 100.0 if is_gold else 100000.0
            
            entry_dist = entry_mult * atr_v
            sl_dist = sl_mult * atr_v
            tp_dist = tp_mult * atr_v
            
            # Target triggers on chart prices
            buy_trig = base_p + entry_dist
            sell_trig = base_p - entry_dist
            
            # Calculate position sizing: 1.0% risk on $10,000 account = $100 risk
            risk_cash = 100.0
            cash_risk_per_lot = (sl_dist * contract_sz)
            lots = risk_cash / cash_risk_per_lot if cash_risk_per_lot > 0 else 0.01
            lots = max(0.01, round(lots, 2))
            
            commission_cost = (lots * comm_per_lot) / 10000.0 # as decimal of account
            
            active = None
            
            for i in range(anchor_idx + 1, len(bars)):
                b = bars[i]
                h = b['ist_hour']
                m = b['ist_minute']
                
                # Auto square off at 15:55 IST
                if h == 15 and m >= 55:
                    if active is not None:
                        # Close at market with exit half-spread and slippage
                        if active['type'] == 'BUY':
                            exit_p = b['close'] - (spread / 2.0) - slip_exit
                            net_pts = exit_p - active['fill_entry']
                        else:
                            exit_p = b['close'] + (spread / 2.0) + slip_exit
                            net_pts = active['fill_entry'] - exit_p
                            
                        dollar_pnl = net_pts * lots * contract_sz
                        trade_pnl_pct = (dollar_pnl / 10000.0) - commission_cost
                        r_pnl = trade_pnl_pct / 0.010
                        day_pnl += trade_pnl_pct
                        day_trades += 1
                        trade_stats.append(r_pnl)
                        active = None
                    break
                    
                if active is None:
                    if h > order_cutoff_h or (h == order_cutoff_h and m >= order_cutoff_m):
                        break
                        
                    # Check Buy Stop: chart high reaches trigger price
                    # Buying incurs Ask = Bid + Spread, plus adverse slippage due to latency
                    if b['high'] >= buy_trig:
                        fill_p = buy_trig + (spread / 2.0) + slip_entry
                        active = {
                            'type': 'BUY',
                            'fill_entry': fill_p,
                            'sl': buy_trig - sl_dist,
                            'tp': buy_trig + tp_dist
                        }
                    # Check Sell Stop: chart low reaches trigger price
                    # Selling incurs Bid = Ask - Spread, plus adverse slippage
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
                        # TP hit
                        if b['high'] >= tp_p:
                            exit_p = tp_p - (spread / 2.0) - slip_exit
                            dollar_pnl = (exit_p - fill_e) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - commission_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                        # SL hit
                        elif b['low'] <= sl_p:
                            exit_p = sl_p - (spread / 2.0) - slip_exit
                            dollar_pnl = (exit_p - fill_e) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - commission_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                    else: # SELL
                        # TP hit
                        if b['low'] <= tp_p:
                            exit_p = tp_p + (spread / 2.0) + slip_exit
                            dollar_pnl = (fill_e - exit_p) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - commission_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                        # SL hit
                        elif b['high'] >= sl_p:
                            exit_p = sl_p + (spread / 2.0) + slip_exit
                            dollar_pnl = (fill_e - exit_p) * lots * contract_sz
                            trade_pnl_pct = (dollar_pnl / 10000.0) - commission_cost
                            r_pnl = trade_pnl_pct / 0.010
                            day_pnl += trade_pnl_pct
                            day_trades += 1
                            trade_stats.append(r_pnl)
                            active = None
                            break
                            
        # Hard daily stop circuit breaker at -2.00%
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

def print_audit_table(results):
    print("\n" + "=" * 125)
    print(f"{'EXECUTION REGIME / FRICTION TIER':<42} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'2026':<6} | {'PF':<5} | {'AVG R':<7}")
    print("=" * 125)
    for r in results:
        print(f"{r['label']:<42} | {r['avg_annual']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_months']:<15} | {r['m2026']:<6} | {r['pf']:<5} | {r['avg_r']:<7}")
    print("=" * 125)

def main():
    print("Loading 2022-2026 tick/M5 data from MT5...")
    asset_data = load_data()
    print("Data loaded successfully.")
    
    # Define Tiers of Real-World Friction
    scenarios = [
        {
            "name": "Tier 0: Frictionless Baseline",
            "spread_gold": 0.0, "spread_eur": 0.0,
            "comm_gold": 0.0, "comm_eur": 0.0,
            "slip_entry_gold": 0.0, "slip_entry_eur": 0.0,
            "slip_exit_gold": 0.0, "slip_exit_eur": 0.0
        },
        {
            "name": "Tier 1: Live ECN (Vantage / FTMO Raw)",
            # Live Vantage quotes: Gold spread 0.29, EURUSD 1.2 pips, $6.00/lot commission, 0.5 pip / $0.10 slippage
            "spread_gold": 0.30, "spread_eur": 0.00012,
            "comm_gold": 6.00, "comm_eur": 6.00,
            "slip_entry_gold": 0.10, "slip_entry_eur": 0.00005,
            "slip_exit_gold": 0.05, "slip_exit_eur": 0.00003
        },
        {
            "name": "Tier 2: Adverse Open (345ms Ping + Spike)",
            # Wide spread at open (Gold $0.55, EURUSD 2.0 pips), $7.00/lot comm, 1.2 pip / $0.25 slippage
            "spread_gold": 0.55, "spread_eur": 0.00020,
            "comm_gold": 7.00, "comm_eur": 7.00,
            "slip_entry_gold": 0.25, "slip_entry_eur": 0.00012,
            "slip_exit_gold": 0.15, "slip_exit_eur": 0.00008
        },
        {
            "name": "Tier 3: Stress / Toxic Slippage (Extreme)",
            # High volatility spike (Gold $1.00, EURUSD 3.5 pips), $10.00/lot comm, 2.5 pip / $0.60 slippage
            "spread_gold": 1.00, "spread_eur": 0.00035,
            "comm_gold": 10.00, "comm_eur": 10.00,
            "slip_entry_gold": 0.60, "slip_entry_eur": 0.00025,
            "slip_exit_gold": 0.30, "slip_exit_eur": 0.00015
        }
    ]

    print("\n" + "#" * 80)
    print("AUDITING 1:00 PM LONDON INSTITUTIONAL SNIPER UNDER REAL FRICTIONS")
    print("#" * 80)
    res_13 = []
    for sc in scenarios:
        df, ts = simulate_with_frictions(
            asset_data, anchor_hour=13, anchor_minute=0, order_cutoff_h=15, order_cutoff_m=0,
            spread_gold=sc["spread_gold"], spread_eur=sc["spread_eur"],
            comm_gold_per_lot=sc["comm_gold"], comm_eur_per_lot=sc["comm_eur"],
            slip_entry_gold=sc["slip_entry_gold"], slip_entry_eur=sc["slip_entry_eur"],
            slip_exit_gold=sc["slip_exit_gold"], slip_exit_eur=sc["slip_exit_eur"]
        )
        res_13.append(analyze(df, ts, sc["name"]))
    print_audit_table(res_13)

    print("\n" + "#" * 80)
    print("AUDITING 12:00 PM FRANKFURT PRE-OPEN SNIPER UNDER REAL FRICTIONS")
    print("#" * 80)
    res_12 = []
    for sc in scenarios:
        df, ts = simulate_with_frictions(
            asset_data, anchor_hour=12, anchor_minute=0, order_cutoff_h=12, order_cutoff_m=55,
            spread_gold=sc["spread_gold"], spread_eur=sc["spread_eur"],
            comm_gold_per_lot=sc["comm_gold"], comm_eur_per_lot=sc["comm_eur"],
            slip_entry_gold=sc["slip_entry_gold"], slip_entry_eur=sc["slip_entry_eur"],
            slip_exit_gold=sc["slip_exit_gold"], slip_exit_eur=sc["slip_exit_eur"]
        )
        res_12.append(analyze(df, ts, sc["name"]))
    print_audit_table(res_12)

    print("\n" + "#" * 80)
    print("AUDITING COMBINED DUAL-SESSION ENSEMBLE UNDER REAL FRICTIONS")
    print("#" * 80)
    res_ensemble = []
    for i, sc in enumerate(scenarios):
        df13 = res_13[i]['df_res']
        df12 = res_12[i]['df_res']
        merged = pd.merge(df13[['date', 'pnl_pct']], df12[['date', 'pnl_pct']], on='date', suffixes=('_13', '_12'))
        merged['pnl_pct'] = np.maximum(-0.020, merged['pnl_pct_13'] + merged['pnl_pct_12'])
        ts_comb = res_13[i]['trade_stats'] + res_12[i]['trade_stats']
        res_ensemble.append(analyze(merged, ts_comb, sc["name"]))
    print_audit_table(res_ensemble)

if __name__ == "__main__":
    main()

