"""
Empirical Daily Profit DNA Extraction
Analyzes how to achieve an 80%+ Daily Green Close Rate:
1. Daily Profit Target Lock (+0.75%, +1.0%, +1.5% -> Stop trading for the day)
2. Daily Loss Circuit Breaker (-1.0%, -1.5% -> Stop trading)
3. Partial Profit Taking (Scaling out 50% at +0.5R + Breakeven)
4. Multi-Asset Uncorrelated Basket (Gold + US100 + EURUSD)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from research.final_70pct_validation import run_70pct_backtest


def extract_daily_green_dna():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    print("\n" + "=" * 75)
    print("      EXTRACTING DAILY GREEN CLOSE SECRETS ACROSS 440+ SESSIONS      ")
    print("=" * 75 + "\n")

    # Load trades from Gold and US100
    res_gold = run_70pct_backtest("XAUUSD", target_ratio=0.60, sl_mult=1.8)
    res_us100 = run_70pct_backtest("US100", target_ratio=0.60, sl_mult=1.8)

    df_gold = res_gold['trades_df'].copy()
    df_us100 = res_us100['trades_df'].copy()

    df_all = pd.concat([df_gold, df_us100]).sort_values('entry_time').reset_index(drop=True)
    df_all['date'] = pd.to_datetime(df_all['entry_time']).dt.date

    # 1. Base Case: What % of days with trades end green WITHOUT daily target lock?
    daily_raw = df_all.groupby('date')['pnl'].sum().reset_index()
    raw_green_days = (daily_raw['pnl'] > 0).sum()
    raw_red_days = (daily_raw['pnl'] < 0).sum()
    raw_breakeven = (daily_raw['pnl'] == 0).sum()
    total_active_days = len(daily_raw)
    raw_green_rate = (raw_green_days / total_active_days) * 100.0

    print(f"[BASE CASE: RAW UNFILTERED DAILY CLOSES]")
    print(f"   Active Trading Days: {total_active_days}")
    print(f"   Green Days         : {raw_green_days} ({raw_green_rate:.1f}%)")
    print(f"   Red Days           : {raw_red_days} ({(raw_red_days/total_active_days)*100:.1f}%)")
    print(f"   Scratch / BE Days  : {raw_breakeven}\n")

    # 2. Testing Daily Profit Lock Mechanics
    # Once cumulative PnL for the day reaches +$100 (+1.0%) or +$75 (+0.75%), stop trading!
    print("--- TESTING DAILY PROFIT TARGET LOCK & STOP CIRCUIT BREAKERS ---")
    for target_lock in [50.0, 75.0, 100.0, 150.0]:
        for stop_lock in [100.0, 150.0]:
            day_results = []
            for d, group in df_all.groupby('date'):
                cum_pnl = 0.0
                for _, trade in group.iterrows():
                    pnl = trade['pnl']
                    cum_pnl += pnl
                    # If daily target reached -> Lock profit and stop trading for the day
                    if cum_pnl >= target_lock:
                        break
                    # If daily loss limit hit -> Stop trading to prevent revenge trading
                    if cum_pnl <= -stop_lock:
                        break
                day_results.append(cum_pnl)

            arr = np.array(day_results)
            g_days = (arr > 0).sum()
            r_days = (arr < 0).sum()
            be_days = (arr == 0).sum()
            tot = len(arr)
            g_rate = (g_days / tot) * 100.0
            tot_pnl = arr.sum()
            avg_green = arr[arr > 0].mean() if g_days > 0 else 0
            avg_red = arr[arr < 0].mean() if r_days > 0 else 0

            tag = "[TOP TIER >=75%]" if g_rate >= 75.0 else "[STANDARD]"
            print(f"{tag} Target Lock: +${target_lock:3.0f} | Max Daily Stop: -${stop_lock:3.0f}")
            print(f"   Green Days Rate : {g_rate:5.1f}% ({g_days} Green / {r_days} Red / {be_days} BE)")
            print(f"   Total Net Profit: ${tot_pnl:,.2f}")
            print(f"   Avg Green Day   : +${avg_green:.2f} | Avg Red Day: ${avg_red:.2f}\n")


if __name__ == "__main__":
    extract_daily_green_dna()

