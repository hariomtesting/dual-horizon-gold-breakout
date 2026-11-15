"""
Simulate Aggressive Risk Profiles (1% vs 2% vs 5% vs 10% Risk Per Trade)
Evaluates exact compounding growth, maximum drawdown, and volatility drag
across 400 real historical trades of Gold (XAUUSD) and Nasdaq (NAS100.r).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from math_model.optimize_frequency_returns import analyze_asset_frequency


def run_aggressive_risk_simulation():
    print("=" * 80)
    print("      AGGRESSIVE RISK SIMULATION: 1% vs 2% vs 5% vs 10% PER TRADE      ")
    print("=" * 80 + "\n")

    # Load real trades from Gold and Nasdaq
    df_gold = analyze_asset_frequency("XAUUSD", max_trades_per_day=2)
    df_nas = analyze_asset_frequency("NAS100.r", max_trades_per_day=2)

    df_comb = pd.concat([df_gold, df_nas]).sort_values('entry_time').reset_index(drop=True)
    total_trades = len(df_comb)
    wins = df_comb[df_comb['pnl'] > 0]
    wr = (len(wins) / total_trades) * 100.0

    print(f"Dataset: {total_trades} Real Trades across ~10 months (Gold + Nasdaq)")
    print(f"Empirical Win Rate: {wr:.2f}% ({len(wins)} Wins / {total_trades - len(wins)} Losses)\n")

    risk_levels = [0.01, 0.02, 0.03, 0.05, 0.075, 0.10]

    results = []

    for risk in risk_levels:
        capital = 10000.0
        peak = capital
        max_dd = 0.0
        equity_curve = [capital]
        daily_losses = []

        # Standardized R-multipliers: Loss = -1.0R, Win = +(pnl / 100.0)R
        for _, t in df_comb.iterrows():
            r_mult = t['pnl'] / 100.0 # Standardized to 1R = $100 on $10k base
            
            # Compounded dollar return for this trade
            trade_pnl = capital * risk * r_mult
            capital += trade_pnl
            capital = max(capital, 0.0) # Account cannot go below $0
            equity_curve.append(capital)

            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100.0 if peak > 0 else 100.0
            if dd > max_dd:
                max_dd = dd

            if trade_pnl < 0:
                daily_losses.append(abs(trade_pnl))

        net_profit = capital - 10000.0
        roi = (net_profit / 10000.0) * 100.0
        calmar = roi / max_dd if max_dd > 0 else 0
        worst_single_loss = max(daily_losses) if len(daily_losses) > 0 else 0

        results.append({
            'Risk(%)': risk * 100.0,
            'FinalCapital': capital,
            'NetROI(%)': roi,
            'MaxDD(%)': max_dd,
            'CalmarRatio': calmar,
            'WorstLoss($)': worst_single_loss
        })

    df_res = pd.DataFrame(results)

    print("--- COMPREHENSIVE PERFORMANCE & RISK COMPARISON ($10,000 BASE) ---")
    for _, r in df_res.iterrows():
        print(f"Risk: {r['Risk(%)']:5.1f}% per trade | Final: ${r['FinalCapital']:11,.2f} | Net ROI: +{r['NetROI(%)']:7.2f}% | Max DD: {r['MaxDD(%)']:5.2f}% | Calmar: {r['CalmarRatio']:4.2f}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    run_aggressive_risk_simulation()

