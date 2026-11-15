"""
Order Flow Scalper Multi-Month Validator
Tests whether the intraday scalper closes in net profit month after month.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import argparse
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from strategy.orderflow_scalper import OrderFlowScalper
from backtest.monte_carlo import MonteCarloSimulator


def run_scalper_benchmark(symbol: str = "US100", bar_count: int = 80000):
    if not mt5.initialize():
        print("MT5 initialization failed", flush=True)
        return

    print("=" * 80, flush=True)
    print(f"   ORDER FLOW SCALPER BENCHMARK: {symbol} (M5 Scalping)", flush=True)
    print("=" * 80, flush=True)

    print(f"[DataLoader] Fetching {bar_count:,} M5 bars for {symbol} from MT5...", flush=True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bar_count)
    if rates is None or len(rates) == 0:
        print(f"Failed to fetch data for {symbol}", flush=True)
        return

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['session_id'] = df['datetime'].dt.floor('D')

    print(f"[DataLoader] Loaded {len(df):,} bars | {df['datetime'].iloc[0].date()} to {df['datetime'].iloc[-1].date()}", flush=True)

    scalper = OrderFlowScalper(
        band_multiplier=2.0,
        risk_reward=1.3,
        max_daily_loss_pct=1.5,
        max_daily_profit_pct=1.5,
        cooldown_bars=4
    )

    spread = 0.50 if "100" in symbol else 0.20
    results = scalper.run_scalping_simulation(
        df,
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread_points=spread,
        max_hold_bars=18
    )

    print("\n" + "=" * 80, flush=True)
    print("                    SCALPER PERFORMANCE SUMMARY                       ", flush=True)
    print("=" * 80, flush=True)
    print(f"Initial Capital             : ${results['initial_balance']:,.2f}", flush=True)
    print(f"Final Capital               : ${results['final_balance']:,.2f}", flush=True)
    print(f"Net Profit ($)              : ${results['net_profit']:,.2f} ({results['net_return_pct']:+.2f}%)", flush=True)
    print(f"Total Scalps Executed       : {results['total_trades']}", flush=True)
    print(f"Trade Win Rate              : {results['trade_win_rate_pct']:.2f}%", flush=True)
    print(f"Profit Factor               : {results['profit_factor']:.2f}", flush=True)
    print(f"Max Peak-to-Trough Drawdown : {results['max_drawdown_pct']:.2f}%", flush=True)
    print(f"Total Calendar Months       : {results['total_months']} Months", flush=True)
    print(f"Profitable Months           : {results['profitable_months']} of {results['total_months']} Months", flush=True)
    print(f"Monthly Consistency Rate    : {results['monthly_consistency_rate_pct']:.1f}% GREEN MONTHS", flush=True)

    print("\n" + "-" * 80, flush=True)
    print("                   MONTH-BY-MONTH DETAILED AUDIT                      ", flush=True)
    print("-" * 80, flush=True)
    print(f"{'Month':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit ($)':<14} | {'Return (%)':<12} | {'Status':<10}", flush=True)
    print("-" * 80, flush=True)

    for m in results['monthly_breakdown']:
        status_tag = "[PROFIT]" if m['is_green'] else "[LOSS]"
        print(f"{m['month']:<10} | {m['trades']:<8} | {m['win_rate_pct']:<9.1f}% | ${m['profit_dollars']:<13,.2f} | {m['return_pct']:>+10.2f}% | {status_tag:<10}", flush=True)

    print("-" * 80, flush=True)

    # Monte Carlo Stress Test
    print("\n>>> [Monte Carlo Analysis] Simulating 1,000 resamplings of scalping trades...", flush=True)
    mc = MonteCarloSimulator(num_simulations=1000, initial_balance=10000.0, ruin_drawdown_pct=15.0)
    mc_res = mc.run(results['trades'])

    if 'error' not in mc_res:
        print("-" * 80, flush=True)
        print("                 MONTE CARLO STRESS TEST (1,000 Runs)                 ", flush=True)
        print("-" * 80, flush=True)
        print(f"Probability of Ruin (>15% Drawdown) : {mc_res['prob_of_ruin_pct']:.2f}%", flush=True)
        print(f"Median Expected Drawdown            : {mc_res['drawdown_stats']['median_dd_pct']:.2f}%", flush=True)
        print(f"95th Percentile Worst-Case Drawdown : {mc_res['drawdown_stats']['percentile_95_dd_pct']:.2f}%", flush=True)
        print(f"Expected Median Capital             : ${mc_res['equity_stats']['median_equity']:,.2f}", flush=True)
        print(f"5th - 95th Percentile Capital Range : ${mc_res['equity_stats']['percentile_5_equity']:,.2f} to ${mc_res['equity_stats']['percentile_95_equity']:,.2f}", flush=True)
        print("-" * 80, flush=True)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="US100", help="Trading symbol (e.g. US100, XAUUSD)")
    parser.add_argument("--bars", type=int, default=80000, help="Number of M5 bars")
    args = parser.parse_args()

    run_scalper_benchmark(symbol=args.symbol, bar_count=args.bars)

