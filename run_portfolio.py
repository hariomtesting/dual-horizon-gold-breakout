"""
Multi-Asset Scalper Portfolio Validator
Executes US100 + XAUUSD + EURUSD simultaneously across 14 months of M5 tick data.
Demonstrates the Prop Desk Multi-Asset Portfolio rule for Green Monthly Consistency.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import argparse
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from strategy.portfolio_scalper import MultiAssetScalper
from backtest.monte_carlo import MonteCarloSimulator


def run_portfolio_benchmark(bar_count: int = 80000):
    if not mt5.initialize():
        print("MT5 initialization failed", flush=True)
        return

    print("=" * 85, flush=True)
    print("      PROP DESK MULTI-ASSET ORDER FLOW SCALPER PORTFOLIO BENCHMARK       ", flush=True)
    print("      Basket: US100 (Nasdaq) + XAUUSD (Gold) + EURUSD (FX)              ", flush=True)
    print("=" * 85, flush=True)

    symbols = ["US100", "XAUUSD", "EURUSD"]
    data_dict = {}

    for sym in symbols:
        print(f"[DataLoader] Fetching {bar_count:,} M5 bars for {sym} from MT5...", flush=True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, bar_count)
        if rates is None or len(rates) == 0:
            print(f"Failed to fetch {sym}", flush=True)
            continue
        df = pd.DataFrame(rates)
        df['datetime'] = pd.to_datetime(df['time'], unit='s')
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['session_id'] = df['datetime'].dt.floor('D')
        data_dict[sym] = df
        print(f"[DataLoader] Loaded {len(df):,} bars for {sym} ({df['datetime'].iloc[0].date()} to {df['datetime'].iloc[-1].date()})", flush=True)

    portfolio = MultiAssetScalper(
        symbols=symbols,
        risk_per_trade=0.0075, # 0.75% risk per trade across portfolio
        daily_target_pct=2.0,  # Lock in profits after +2% daily
        daily_stop_pct=2.0,    # Hard daily circuit breaker
        risk_reward=1.35,      # 1:1.35 Reward-to-Risk target
        max_concurrent_trades=2
    )

    spread_dict = {"US100": 0.50, "XAUUSD": 0.22, "EURUSD": 0.00010}

    print("\n>>> [Portfolio Simulation] Executing synchronized multi-asset event loop...", flush=True)
    results = portfolio.run_portfolio_simulation(
        data_dict,
        initial_balance=10000.0,
        spread_dict=spread_dict
    )

    print("\n" + "=" * 85, flush=True)
    print("                 MULTI-ASSET PORTFOLIO PERFORMANCE SUMMARY                 ", flush=True)
    print("=" * 85, flush=True)
    print(f"Initial Capital             : ${results['initial_balance']:,.2f}", flush=True)
    print(f"Final Capital               : ${results['final_balance']:,.2f}", flush=True)
    print(f"Net Profit ($)              : ${results['net_profit']:,.2f} ({results['net_return_pct']:+.2f}%)", flush=True)
    print(f"Total Scalps Executed       : {results['total_trades']} Trades", flush=True)
    print(f"Portfolio Trade Win Rate    : {results['trade_win_rate_pct']:.2f}%", flush=True)
    print(f"Profit Factor               : {results['profit_factor']:.2f}", flush=True)
    print(f"Max Peak-to-Trough Drawdown : {results['max_drawdown_pct']:.2f}%", flush=True)
    print(f"Total Calendar Months       : {results['total_months']} Months", flush=True)
    print(f"Profitable Months           : {results['profitable_months']} of {results['total_months']} Months", flush=True)
    print(f"Monthly Consistency Rate    : {results['monthly_consistency_rate_pct']:.1f}% GREEN MONTHS", flush=True)

    print("\n" + "-" * 85, flush=True)
    print("                    MONTH-BY-MONTH PORTFOLIO AUDIT                        ", flush=True)
    print("-" * 85, flush=True)
    print(f"{'Month':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit ($)':<12} | {'Return (%)':<10} | {'Status':<10}", flush=True)
    print("-" * 85, flush=True)

    for m in results['monthly_breakdown']:
        status_tag = "[PROFIT]" if m['is_green'] else "[LOSS]"
        print(f"{m['month']:<10} | {m['trades']:<8} | {m['win_rate_pct']:<9.1f}% | ${m['profit_dollars']:<11,.2f} | {m['return_pct']:>+9.2f}% | {status_tag:<10}", flush=True)

    print("-" * 85, flush=True)

    # Multi-Asset Monte Carlo Simulation
    print("\n>>> [Monte Carlo 1,000 Runs] Stress-testing resampled portfolio trade streams...", flush=True)
    mc = MonteCarloSimulator(num_simulations=1000, initial_balance=10000.0, ruin_drawdown_pct=15.0)
    mc_res = mc.run(results['trades'])

    if 'error' not in mc_res:
        print("-" * 85, flush=True)
        print("              MULTI-ASSET MONTE CARLO STRESS TEST (1,000 Runs)            ", flush=True)
        print("-" * 85, flush=True)
        print(f"Probability of Ruin (>15% Drawdown) : {mc_res['prob_of_ruin_pct']:.2f}%", flush=True)
        print(f"Median Expected Drawdown            : {mc_res['drawdown_stats']['median_dd_pct']:.2f}%", flush=True)
        print(f"95th Percentile Worst-Case Drawdown : {mc_res['drawdown_stats']['percentile_95_dd_pct']:.2f}%", flush=True)
        print(f"Expected Median Capital             : ${mc_res['equity_stats']['median_equity']:,.2f}", flush=True)
        print(f"5th - 95th Percentile Capital Range : ${mc_res['equity_stats']['percentile_5_equity']:,.2f} to ${mc_res['equity_stats']['percentile_95_equity']:,.2f}", flush=True)
        print("-" * 85, flush=True)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=80000, help="Number of M5 bars per symbol")
    args = parser.parse_args()

    run_portfolio_benchmark(bar_count=args.bars)

