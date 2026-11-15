"""
Main Execution Launcher for the Dual-Session Institutional Sniper
================================================================
Operating Window : 11:00 AM - 16:00 IST (Strictly no VPS needed)
Assets           : XAUUSD, EURUSD
Cycle 1 Anchor   : 12:00 PM IST (Frankfurt Pre-Open, Cutoff: 12:55 PM IST)
Cycle 2 Anchor   : 13:00 PM IST (London Open, Cutoff: 15:00 PM IST)
Auto-Square-Off  : 15:55 IST (100% flat before PC shutdown)

Usage:
  python start_live_algo.py               # Starts in safe simulated Paper Trading mode
  python start_live_algo.py --mode live   # Sends real orders to MT5 demo/live account
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import time
import argparse
import MetaTrader5 as mt5
from execution.multi_asset_trader import LiveMathematicalTrader


def main():
    parser = argparse.ArgumentParser(description="1:00 PM IST London Institutional Sniper Launcher")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Execution mode (default: paper)")
    parser.add_argument("--risk", type=float, default=0.010, help="Risk per trade as decimal (default: 0.010 = 1.0 pct)")
    parser.add_argument("--target", type=float, default=4.0, help="Daily target percent to lock profits (default: 4.0 pct)")
    parser.add_argument("--stop", type=float, default=2.0, help="Daily max loss circuit breaker (default: 2.0 pct)")
    args = parser.parse_args()

    is_paper = (args.mode == "paper")
    symbols = ["XAUUSD", "EURUSD"]

    trader = LiveMathematicalTrader(
        symbols=symbols,
        paper_mode=is_paper,
        risk_pct=args.risk,
        daily_target_pct=args.target,
        daily_stop_pct=args.stop
    )

    if not trader.initialize():
        print("[Launcher] Could not initialize MT5. Exiting.")
        sys.exit(1)

    print("\n[Launcher] Running continuous live monitoring loop. Press Ctrl+C to stop.\n", flush=True)

    try:
        while True:
            trader.run_heartbeat()
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[Launcher] Stopping algo safely. Shutting down MT5 connection...", flush=True)
        mt5.shutdown()
        print("[Launcher] Algo stopped successfully.")


if __name__ == "__main__":
    main()
