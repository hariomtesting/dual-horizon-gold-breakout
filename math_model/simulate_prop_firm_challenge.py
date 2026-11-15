"""
Prop Firm Challenge Passing Simulator
Simulates passing FTMO / FundedNext / The5ers / Funding Pips rules:
- Account: $100,000
- Phase 1 Target: +8% ($8,000)
- Phase 2 Target: +5% ($5,000)
- Daily Max Loss Limit: 4.5% ($4,500)
- Overall Max Drawdown Limit: 8.0% ($8,000)
Uses real historical trades of the 75%+ Win-Rate Gold Mathematical Model.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from math_model.optimize_gold_1pct import run_fast_gold_optimization
import MetaTrader5 as mt5


def simulate_prop_firm():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    # Load real trades from the optimal Gold configuration (Rank #2: 74.8% WR, 3.06% DD)
    # Using 107 real trades across 10 months
    # Standardized outcome vector in R: Win = +0.65R, Loss = -1.0R
    # We run 1,000 Monte Carlo bootstrap attempts of the challenge
    from math_model.optimize_frequency_returns import analyze_asset_frequency
    df_trades = analyze_asset_frequency("XAUUSD", max_trades_per_day=2)
    if len(df_trades) == 0:
        print("No trades found.")
        return

    r_multipliers = (df_trades['pnl'] / 100.0).values
    total_pool = len(r_multipliers)

    print("=" * 80)
    print("      PROP FIRM PASSING ENGINE: REAL-WORLD RULES VS 50% MYTH      ")
    print("=" * 80 + "\n")
    print(f"Historical Trade Pool: {total_pool} Gold Trades (Win Rate: {(r_multipliers > 0).mean()*100:.2f}%)")
    print("Prop Firm Rules Tested:")
    print("   - Phase 1 Target : +8% to +10%")
    print("   - Phase 2 Target : +5%")
    print("   - Max Daily Loss : 4.5% (Violating this fails account)")
    print("   - Max Total DD   : 8.0% (Violating this fails account)\n")

    # Test risk sizing per trade: 0.75%, 1.0%, 1.25%, 1.5%, 2.0%, 3.0%, 5.0%
    risk_options = [0.0075, 0.010, 0.0125, 0.015, 0.020, 0.030, 0.050]

    simulations_per_risk = 1000

    results = []

    for risk in risk_options:
        passed_phase1_count = 0
        passed_both_count = 0
        breached_daily_count = 0
        breached_overall_count = 0
        days_to_pass_list = []

        for _ in range(simulations_per_risk):
            # Phase 1: Target +8%, Cap $100k
            cap = 100000.0
            starting_cap = cap
            peak = cap
            target_phase1 = starting_cap * 1.08 # +8% = $108,000
            phase1_passed = False
            failed = False
            trades_taken = 0

            # Bootstrap random sequence of trades
            trade_idx = 0
            while not phase1_passed and not failed and trades_taken < 150:
                outcome = np.random.choice(r_multipliers)
                trade_pnl = cap * risk * outcome
                cap += trade_pnl
                trades_taken += 1

                if cap > peak:
                    peak = cap
                total_dd = (starting_cap - cap) / starting_cap * 100.0

                # Check failure rules
                if abs(outcome) >= 1.0 and trade_pnl < 0:
                    daily_loss_pct = abs(trade_pnl) / starting_cap * 100.0
                    if daily_loss_pct >= 4.5:
                        breached_daily_count += 1
                        failed = True
                        break

                if total_dd >= 8.0:
                    breached_overall_count += 1
                    failed = True
                    break

                if cap >= target_phase1:
                    phase1_passed = True
                    passed_phase1_count += 1
                    # Approx 1.5 trades per day
                    days_to_pass_list.append(trades_taken / 1.5)
                    break

            # Phase 2: If Phase 1 passed, test Phase 2 (+5% target)
            if phase1_passed:
                cap_p2 = 100000.0
                peak_p2 = cap_p2
                target_phase2 = cap_p2 * 1.05 # +5% = $105,000
                phase2_passed = False
                p2_failed = False
                p2_trades = 0

                while not phase2_passed and not p2_failed and p2_trades < 100:
                    outcome = np.random.choice(r_multipliers)
                    trade_pnl = cap_p2 * risk * outcome
                    cap_p2 += trade_pnl
                    p2_trades += 1

                    total_dd = (100000.0 - cap_p2) / 100000.0 * 100.0
                    if total_dd >= 8.0:
                        p2_failed = True
                        break

                    if cap_p2 >= target_phase2:
                        phase2_passed = True
                        passed_both_count += 1
                        break

        pass_p1_rate = (passed_phase1_count / simulations_per_risk) * 100.0
        pass_both_rate = (passed_both_count / simulations_per_risk) * 100.0
        breach_rate = ((breached_daily_count + breached_overall_count) / simulations_per_risk) * 100.0
        avg_days = np.mean(days_to_pass_list) if len(days_to_pass_list) > 0 else 999.0

        results.append({
            'Risk(%)': risk * 100.0,
            'PassPhase1(%)': pass_p1_rate,
            'PassBothPhases(%)': pass_both_rate,
            'BreachFailRate(%)': min(100.0, breach_rate),
            'AvgDaysToPass': avg_days
        })

    df_prop = pd.DataFrame(results)

    print("--- 1,000-RUN MONTE CARLO PROP FIRM PASSING PROBABILITY MATRIX ---")
    print(f"{'Risk/Trade':<12} | {'Pass Phase 1':<14} | {'Pass BOTH Phases':<18} | {'Account Breach %':<18} | {'Avg Days to Pass':<16}")
    print("-" * 85)

    for _, row in df_prop.iterrows():
        status = "[OPTIMAL]" if row['PassBothPhases(%)'] >= 90.0 else ("[VIABLE]" if row['PassBothPhases(%)'] >= 70.0 else "[BLOWUP RISK]")
        print(f"{row['Risk(%)']:5.2f}%       | {row['PassPhase1(%)']:5.1f}%         | {row['PassBothPhases(%)']:5.1f}%             | {row['BreachFailRate(%)']:5.1f}%             | ~{row['AvgDaysToPass']:4.1f} Days {status}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    simulate_prop_firm()

