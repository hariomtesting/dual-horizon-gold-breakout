import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import MetaTrader5 as mt5
from math_model.test_daytime_liquidity_scalper import DaytimeLiquidityScalper, load_m5_data

def main():
    if not mt5.initialize():
        print("MT5 init failed")
        return
        
    dfs = {
        'XAUUSD': load_m5_data('XAUUSD', 60000),
        'BTCUSD': load_m5_data('BTCUSD', 60000)
    }
    mt5.shutdown()
    
    print("\n" + "=" * 90)
    print("PURE DUAL-CORE (GOLD + BITCOIN) LIQUIDITY SWEEP SCALPER (10:00 AM - 15:55 IST)")
    print("=" * 90)
    
    for r in [0.0075, 0.010, 0.0125]:
        scalper = DaytimeLiquidityScalper(10000.0, risk_per_trade=r)
        del scalper.specs['GER40.r']
        res = scalper.backtest(dfs)
        print(f"Risk: {r*100:4.2f}% | CAGR: {res['cagr']:+8.2f}% | Max DD: {res['max_drawdown_pct']:5.2f}% | Sharpe: {res['sharpe_ratio']:4.2f} | PF: {res['profit_factor']:4.2f} | Win%: {res['win_rate']:4.1f}% | Trades: {res['total_trades']:<4} | Final: ${res['final_balance']:,.2f}")
        
    print("=" * 90)

if __name__ == "__main__":
    main()
