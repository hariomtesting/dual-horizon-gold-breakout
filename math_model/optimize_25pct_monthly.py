"""
25% Monthly Return Quantitative Optimization Engine
Tests multi-asset session scalping on Gold (XAUUSD) and Nasdaq (NAS100.r)
to evaluate the exact trade frequency, risk sizing, and session filters
required to generate >= 25% monthly return while containing drawdowns.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from math_model.optimize_frequency_returns import analyze_asset_frequency


def run_25pct_monthly_optimization():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    print("=" * 85)
    print("      QUANTITATIVE OPTIMIZATION: GENERATING 25%+ MONTHLY RETURNS      ")
    print("=" * 85 + "\n")

    # Load high-frequency multi-session trades on Gold and Nasdaq
    df_gold = analyze_asset_frequency("XAUUSD", max_trades_per_day=2)
    df_nas = analyze_asset_frequency("NAS100.r", max_trades_per_day=2)

    df_comb = pd.concat([df_gold, df_nas]).sort_values('entry_time').reset_index(drop=True)
    df_comb['month'] = pd.to_datetime(df_comb['entry_time']).dt.to_period('M')

    months = sorted(df_comb['month'].unique())
    print(f"Total Available Historical Trades: {len(df_comb)} across {len(months)} Months")
    print(f"Combined Win Rate: {(df_comb['pnl'] > 0).mean()*100:.2f}%\n")

    # Test risk tiers: 1.5%, 2.0%, 2.5%
    for risk in [0.015, 0.020, 0.025]:
        print(f"--- TESTING RISK TIER: {risk*100:.1f}% PER TRADE ---")
        monthly_returns = []
        monthly_drawdowns = []

        # Simulate month by month
        for m in months:
            m_trades = df_comb[df_comb['month'] == m]
            if len(m_trades) < 10:
                continue

            cap = 10000.0
            peak = cap
            m_max_dd = 0.0

            for _, t in m_trades.iterrows():
                r_mult = t['pnl'] / 100.0
                pnl = cap * risk * r_mult
                cap += pnl

                if cap > peak:
                    peak = cap
                dd = (peak - cap) / peak * 100.0
                if dd > m_max_dd:
                    m_max_dd = dd

            m_roi = (cap - 10000.0) / 10000.0 * 100.0
            monthly_returns.append(m_roi)
            monthly_drawdowns.append(m_max_dd)

        avg_return = np.mean(monthly_returns)
        med_return = np.median(monthly_returns)
        max_month = np.max(monthly_returns)
        min_month = np.min(monthly_returns)
        avg_dd = np.mean(monthly_drawdowns)
        max_overall_dd = np.max(monthly_drawdowns)
        pct_months_above_20 = (np.array(monthly_returns) >= 20.0).mean() * 100.0
        pct_months_above_25 = (np.array(monthly_returns) >= 25.0).mean() * 100.0

        print(f"   Average Monthly Return   : +{avg_return:5.2f}% (Median: +{med_return:5.2f}%)")
        print(f"   Best Month / Worst Month : +{max_month:5.2f}% / {min_month:5.2f}%")
        print(f"   Months >= 25% Return     : {pct_months_above_25:5.1f}% of months")
        print(f"   Months >= 20% Return     : {pct_months_above_20:5.1f}% of months")
        print(f"   Average Monthly Drawdown : {avg_dd:5.2f}% (Worst Month DD: {max_overall_dd:5.2f}%)\n")

    # Detailed Month-by-Month Table at 2.0% Risk
    print("--- MONTH-BY-MONTH BREAKDOWN AT 2.0% RISK PER TRADE ---")
    cap = 10000.0
    for m in months:
        m_trades = df_comb[df_comb['month'] == m]
        if len(m_trades) < 10:
            continue
        m_cap = 10000.0
        m_peak = m_cap
        m_dd = 0.0
        wins = 0

        for _, t in m_trades.iterrows():
            r_mult = t['pnl'] / 100.0
            if r_mult > 0:
                wins += 1
            pnl = m_cap * 0.02 * r_mult
            m_cap += pnl
            if m_cap > m_peak:
                m_peak = m_cap
            dd = (m_peak - m_cap) / m_peak * 100.0
            if dd > m_dd:
                m_dd = dd

        m_roi = (m_cap - 10000.0) / 10000.0 * 100.0
        wr = (wins / len(m_trades)) * 100.0
        target_hit = "[TARGET HIT >=25%]" if m_roi >= 25.0 else ("[GREEN]" if m_roi > 0 else "[RED]")
        print(f"   {m} : {len(m_trades):2d} Trades | WR: {wr:5.1f}% | Net ROI: {m_roi:+6.2f}% | Max DD: {m_dd:5.2f}% | {target_hit}")

    print("\n" + "=" * 85)


if __name__ == "__main__":
    run_25pct_monthly_optimization()

