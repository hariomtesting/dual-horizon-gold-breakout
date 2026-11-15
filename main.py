"""
Order Flow & Heatmap Trading Algorithm Master Runner
Orchestrates:
1. Historical Backtesting
2. Walk-Forward / Forward-Backtest Validation
3. Monte Carlo Simulation (1,000 runs)
4. Paper Trading & Live MT5 Execution
"""

import argparse
import sys
import time
import pandas as pd
import numpy as np

from data.market_data import MarketDataLoader
from core.orderflow import OrderFlowEngine
from strategy.orderflow_strategy import OrderFlowStrategy
from backtest.engine import BacktestEngine
from backtest.walk_forward import WalkForwardValidator
from backtest.monte_carlo import MonteCarloSimulator
from execution.live_trader import MT5OrderFlowTrader


def print_banner():
    banner = """
    ======================================================================
       INSTITUTIONAL ORDER FLOW & VOLUME PROFILE TRADING ALGORITHM
       Components: VWAP/AVWAP/TWAP, Volume Profile (VPOC/VAH/VAL),
                   CVD/Delta Absorption, Monte Carlo & Live MT5 Paper Trading
    ======================================================================
    """
    print(banner)


def run_full_evaluation(symbol: str = "EURUSD", count: int = 5000):
    print(f"\n>>> [PHASE 1] FETCHING MARKET DATA FOR {symbol} (Count: {count} bars)...")
    df = MarketDataLoader.get_data(symbol=symbol, count=count)
    
    print("\n>>> [PHASE 2] RUNNING ORDER FLOW STRATEGY ON HISTORICAL DATA...")
    strategy = OrderFlowStrategy(tick_size=0.0001, vp_window=48)
    df_signals = strategy.generate_signals(df)
    
    total_signals = (df_signals['signal'] != 0).sum()
    print(f"Total Signals Triggered: {total_signals} ({ (df_signals['signal'] == 1).sum() } Buys, { (df_signals['signal'] == -1).sum() } Sells)")

    print("\n>>> [PHASE 3] EXECUTING FULL REALISTIC BACKTEST...")
    backtester = BacktestEngine(initial_balance=10000.0, risk_per_trade=0.01)
    results = backtester.run(df_signals)

    print("-" * 65)
    print("               BACKTEST PERFORMANCE SUMMARY               ")
    print("-" * 65)
    print(f"Initial Capital   : ${results['initial_balance']:,.2f}")
    print(f"Final Capital     : ${results['final_balance']:,.2f}")
    print(f"Net Profit ($)    : ${results['net_profit']:,.2f} ({results['net_return_pct']:+.2f}%)")
    print(f"Total Trades      : {results['total_trades']}")
    print(f"Win Rate          : {results['win_rate']:.2f}% ({results['winning_trades']} wins / {results['losing_trades']} losses)")
    print(f"Profit Factor     : {results['profit_factor']:.2f}")
    print(f"Max Drawdown      : {results['max_drawdown_pct']:.2f}%")
    print(f"Sharpe Ratio      : {results['sharpe_ratio']:.2f}")
    print(f"Average Win       : ${results['avg_win']:,.2f}")
    print(f"Average Loss      : ${results['avg_loss']:,.2f}")
    print("-" * 65)

    print("\n>>> [PHASE 4] RUNNING WALK-FORWARD / FORWARD-TEST VALIDATION...")
    wf = WalkForwardValidator(in_sample_ratio=0.65, initial_balance=10000.0)
    wf_res = wf.run(df, strategy)
    
    in_m = wf_res['in_sample_period']['metrics']
    out_m = wf_res['out_of_sample_forward_period']['metrics']
    print(f"In-Sample Return  : {in_m['net_return_pct']:+.2f}% (Trades: {in_m['total_trades']}, WinRate: {in_m['win_rate']:.1f}%, PF: {in_m['profit_factor']:.2f})")
    print(f"Out-of-Sample Ret : {out_m['net_return_pct']:+.2f}% (Trades: {out_m['total_trades']}, WinRate: {out_m['win_rate']:.1f}%, PF: {out_m['profit_factor']:.2f})")
    print(f"Walk-Forward Eff  : {wf_res['walk_forward_efficiency']:.2f}")
    print(f"Out-of-Sample Win : {'[PASSED - ROBUST]' if wf_res['is_robust'] else '[NEEDS TUNING]'}")

    print("\n>>> [PHASE 5] RUNNING 1,000-RUN MONTE CARLO STRESS TEST...")
    mc = MonteCarloSimulator(num_simulations=1000, initial_balance=10000.0, ruin_drawdown_pct=20.0)
    mc_res = mc.run(results['trades'])

    if 'error' in mc_res:
        print(f"Monte Carlo Note: {mc_res['error']}")
    else:
        print("-" * 65)
        print("               MONTE CARLO SIMULATION RESULTS (1,000 Runs)  ")
        print("-" * 65)
        print(f"Probability of Ruin (>20% Drawdown) : {mc_res['prob_of_ruin_pct']:.2f}%")
        print(f"Median Expected Drawdown            : {mc_res['drawdown_stats']['median_dd_pct']:.2f}%")
        print(f"95th Percentile Worst-Case Drawdown : {mc_res['drawdown_stats']['percentile_95_dd_pct']:.2f}%")
        print(f"99th Percentile Worst-Case Drawdown : {mc_res['drawdown_stats']['percentile_99_dd_pct']:.2f}%")
        print(f"Expected Median Capital             : ${mc_res['equity_stats']['median_equity']:,.2f}")
        print(f"5th - 95th Percentile Equity Range  : ${mc_res['equity_stats']['percentile_5_equity']:,.2f} to ${mc_res['equity_stats']['percentile_95_equity']:,.2f}")
        print("-" * 65)

    return results, wf_res, mc_res


def start_paper_trading(symbol: str = "EURUSD", cycles: int = 5):
    print(f"\n>>> [PHASE 6] STARTING LIVE PAPER TRADING ENGINE ON {symbol}...")
    trader = MT5OrderFlowTrader(symbol=symbol, paper_mode=True, risk_per_trade=0.01)
    if not trader.initialize_terminal():
        print("Could not initialize MT5 for paper trading.")
        return

    print(f"Monitoring live tick stream for {symbol}. Running {cycles} verification cycles...\n")
    for i in range(cycles):
        trader.run_cycle()
        time.sleep(2)
    print("\n[Paper Trading Verification Completed Successfully]")


if __name__ == "__main__":
    print_banner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD", help="Trading symbol (e.g. EURUSD, GBPUSD, XAUUSD)")
    parser.add_argument("--count", type=int, default=5000, help="Number of historical bars")
    parser.add_argument("--paper", action="store_true", help="Launch live paper trading")
    args = parser.parse_args()

    results, wf_res, mc_res = run_full_evaluation(symbol=args.symbol, count=args.count)
    start_paper_trading(symbol=args.symbol, cycles=3)

