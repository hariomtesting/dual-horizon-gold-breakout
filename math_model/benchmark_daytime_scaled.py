import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from math_model.benchmark_daytime_portfolio_10am_4pm import load_data, DaytimePortfolioSimulator

class ScaledDaytimeSimulator(DaytimePortfolioSimulator):
    def __init__(self, initial_capital=10000.0, base_risk=0.02):
        super().__init__(initial_capital=initial_capital, use_safeguards=True)
        self.base_risk = base_risk
        del self.specs['USDJPY']
        del self.specs['GER40.r']
        
    def run(self, asset_dfs):
        common_dates = sorted(list(set.intersection(*[set(df['date'].unique()) for df in asset_dfs.values()])))
        balance = self.initial_capital
        peak_balance = self.initial_capital
        equity_curve = [balance]
        daily_records = []
        all_trades = []
        
        for cur_date in common_dates:
            dd_pct = ((peak_balance - balance) / peak_balance) * 100.0
            if self.use_safeguards:
                if dd_pct >= 12.0:
                    current_risk = self.base_risk * 0.33 # Bunker
                elif dd_pct >= 8.0:
                    current_risk = self.base_risk * 0.67 # Caution
                else:
                    current_risk = self.base_risk
            else:
                current_risk = self.base_risk
                
            day_pnl = 0.0
            day_trades = []
            
            for sym, cfg in self.specs.items():
                if sym not in asset_dfs:
                    continue
                bars = asset_dfs[sym][asset_dfs[sym]['date'] == cur_date].to_dict('records')
                if len(bars) < 30:
                    continue
                if self.use_safeguards and bars[0]['atr'] < cfg['min_atr']:
                    continue
                    
                pnl, trades = self._simulate_daytime_session(bars, sym, cfg, balance, current_risk)
                day_pnl += pnl
                day_trades.extend(trades)
                
            if day_pnl < -(balance * 0.04):
                day_pnl = -(balance * 0.04)
                
            balance += day_pnl
            balance = max(100.0, balance)
            if balance > peak_balance:
                peak_balance = balance
                
            equity_curve.append(balance)
            daily_records.append({
                'date': cur_date, 'balance': balance, 'daily_pnl': day_pnl,
                'dd_pct': ((peak_balance - balance) / peak_balance) * 100.0
            })
            all_trades.extend(day_trades)
            
        return self._calc_stats(daily_records, equity_curve, all_trades)

def main():
    if not mt5.initialize():
        print("MT5 init failed")
        return
        
    dfs = {
        'XAUUSD': load_data('XAUUSD', 50000),
        'BTCUSD': load_data('BTCUSD', 50000)
    }
    mt5.shutdown()
    
    print("\n" + "=" * 110)
    print("DAYTIME DUAL-CORE (GOLD + BITCOIN) RISK SCALING: STRICTLY 10:00 AM - 16:00 IST")
    print("Zero Overnight Risk | 15:55 IST Hard Auto-Square-Off | Live Vantage Real Frictions")
    print("=" * 110)
    print(f"{'BASE RISK / TRADE':<20} | {'CAGR (ANNUAL ROI)':<20} | {'MAX DRAWDOWN':<15} | {'PROFIT FACTOR':<15} | {'WIN RATE'}")
    print("-" * 110)
    
    for r in [0.015, 0.018, 0.020, 0.022, 0.025]:
        sim = ScaledDaytimeSimulator(10000.0, base_risk=r)
        res = sim.run(dfs)
        print(f"{r*100:4.1f}% risk per trade   | {res['cagr']:+16.2f}%    | {res['max_drawdown_pct']:11.2f}%    | {res['profit_factor']:11.2f}    | {res['win_rate']:4.1f}%")
        
    print("=" * 110)

if __name__ == "__main__":
    main()

