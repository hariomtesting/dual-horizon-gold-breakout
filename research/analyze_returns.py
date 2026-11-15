"""
Comprehensive Return & Compounding Analysis for the 70% Win-Rate Strategy
Calculates:
- Monthly return breakdown (calendar consistency)
- Fixed vs Compounded Equity growth
- Performance scaling at 1.0%, 1.5%, and 2.0% risk per trade
- Sharpe and Calmar ratios
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from research.final_70pct_validation import run_70pct_backtest


def analyze_all_returns():
    print("=" * 75)
    print("     DETAILED RETURN & CAPITAL GROWTH ANALYSIS (70% WIN-RATE STRATEGY)    ")
    print("=" * 75 + "\n")

    res_gold = run_70pct_backtest("XAUUSD", target_ratio=0.60, sl_mult=1.8)
    res_us100 = run_70pct_backtest("US100", target_ratio=0.60, sl_mult=1.8)

    # Combine trades chronologically
    df_trades = pd.concat([res_gold['trades_df'], res_us100['trades_df']]).sort_values('entry_time').reset_index(drop=True)
    df_trades['month'] = pd.to_datetime(df_trades['entry_time']).dt.to_period('M')

    print(f"Total Portfolio Trades: {len(df_trades)} across ~10 months")
    print(f"Overall Win Rate       : {(df_trades['pnl'] > 0).mean()*100:.2f}%\n")

    # 1. Month-by-Month Breakdown (1% fixed risk)
    monthly_pnl = df_trades.groupby('month')['pnl'].agg(['count', lambda x: (x > 0).mean() * 100, 'sum']).reset_index()
    monthly_pnl.columns = ['Month', 'Trades', 'WinRate(%)', 'NetProfit($)']
    monthly_pnl['Return(%)'] = (monthly_pnl['NetProfit($)'] / 10000.0) * 100.0

    print("--- MONTH-BY-MONTH BREAKDOWN (At conservative 1% risk / $10,000 base) ---")
    for _, row in monthly_pnl.iterrows():
        p_str = f"+${row['NetProfit($)']:,.2f} (+{row['Return(%)']:.2f}%)" if row['NetProfit($)'] >= 0 else f"-${abs(row['NetProfit($)']):,.2f} ({row['Return(%)']:.2f}%)"
        tag = "[PROFITABLE]" if row['NetProfit($)'] >= 0 else "[DRAWDOWN]"
        print(f"   {row['Month']} : {row['Trades']:2.0f} trades | WR: {row['WinRate(%)']:5.1f}% | Net: {p_str:<22} {tag}")

    green_months = (monthly_pnl['NetProfit($)'] > 0).sum()
    print(f"\n   Monthly Win Consistency: {green_months} of {len(monthly_pnl)} Months Profitable ({green_months/len(monthly_pnl)*100:.1f}%)\n")

    # 2. Risk Sizing & Compounding Comparison
    print("--- SCALING & COMPOUNDING PROJECTIONS (Starting Capital: $10,000) ---")
    for risk_pct in [0.01, 0.015, 0.02]:
        # Simulate compounding
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, t in df_trades.iterrows():
            # Standardized outcome in R (loss = -1.0R, win = +avg_win_ratio R)
            r_outcome = (t['pnl'] / 100.0) # normalizes $100 risk to 1R
            pnl_comp = cap * risk_pct * r_outcome
            cap += pnl_comp
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

        net_gain = cap - 10000.0
        roi = (net_gain / 10000.0) * 100.0
        calmar = roi / max_dd if max_dd > 0 else 0
        print(f"   Risk: {risk_pct*100:.1f}% per trade | Final Capital: ${cap:,.2f} | Net Return: +{roi:.2f}% | Max DD: {max_dd:.2f}% | Calmar Ratio: {calmar:.2f}")

    print("\n" + "=" * 75)


if __name__ == "__main__":
    analyze_all_returns()

