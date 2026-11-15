"""
MULTI-ASSET EVALUATION: CRYPTO & FOREX SUITE UNDER THE INSTITUTIONAL BACKUP MODEL
================================================================================
Evaluating:
1. Crypto Assets: BTCUSD, ETHUSD, SOLUSD
2. Forex Pairs: USDJPY, EURUSD, GBPUSD, EURAUD, USDCAD

Under the Full Institutional Backup Framework:
- Dynamic Drawdown De-Leveraging (Half-Kelly Sizing)
- Dynamic Volatility Drought Filter
- Asymmetric Split-Target Recovery (50% @ 1.4R, 50% @ 3.5R with BE trailing)
- Full Real Vantage Spreads, Commissions ($6/lot), and Latency Slippage (345ms).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from math_model.test_institutional_portfolio_with_backup_model import InstitutionalPortfolioSimulator, load_data

def evaluate_multi_asset_suite():
    if not mt5.initialize():
        print("MT5 failed to initialize")
        return
        
    # Asset specifications table
    suite_configs = {
        # --- CRYPTO ASSETS ---
        'BTCUSD': {'type': 'Crypto', 'contract': 1.0, 'spread': 17.05, 'comm': 6.0, 'open_h': 12, 'tau': 1.4, 'sl': 2.0, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 120.0, 'bars': 60000},
        'ETHUSD': {'type': 'Crypto', 'contract': 1.0, 'spread': 1.20,  'comm': 6.0, 'open_h': 12, 'tau': 1.4, 'sl': 2.0, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 8.0,   'bars': 50000},
        'SOLUSD': {'type': 'Crypto', 'contract': 10.0, 'spread': 0.15, 'comm': 6.0, 'open_h': 12, 'tau': 1.4, 'sl': 2.0, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 0.8,   'bars': 30000},
        
        # --- FOREX PAIRS ---
        'USDJPY': {'type': 'Forex',  'contract': 100000.0, 'spread': 0.015,   'comm': 6.0, 'open_h': 13, 'tau': 1.0, 'sl': 1.5, 'cutoff_h': 17, 'cutoff_m': 0, 'min_atr': 0.04,  'bars': 60000},
        'EURUSD': {'type': 'Forex',  'contract': 100000.0, 'spread': 0.00014, 'comm': 6.0, 'open_h': 12, 'tau': 1.2, 'sl': 1.5, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 0.0004,'bars': 60000},
        'GBPUSD': {'type': 'Forex',  'contract': 100000.0, 'spread': 0.00018, 'comm': 6.0, 'open_h': 12, 'tau': 1.2, 'sl': 1.5, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 0.0005,'bars': 60000},
        'EURAUD': {'type': 'Forex',  'contract': 100000.0, 'spread': 0.00022, 'comm': 6.0, 'open_h': 12, 'tau': 1.2, 'sl': 1.5, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 0.0006,'bars': 60000},
        'USDCAD': {'type': 'Forex',  'contract': 100000.0, 'spread': 0.00018, 'comm': 6.0, 'open_h': 12, 'tau': 1.2, 'sl': 1.5, 'cutoff_h': 16, 'cutoff_m': 0, 'min_atr': 0.0004,'bars': 60000},
    }
    
    print("\n" + "=" * 135)
    print("INSTITUTIONAL SUITE EVALUATION: CRYPTO & FOREX ASSETS (PROTECTED BACKUP MODEL)")
    print("Under Real Live Vantage Broker Fees (Spreads, Commission, Latency Slippage, Compounding Risk)")
    print("=" * 135)
    print(f"{'ASSET':<10} | {'CLASS':<8} | {'CAGR ROI':<12} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL ($10k)'} | {'STATUS'}")
    print("-" * 135)
    
    for sym, cfg in suite_configs.items():
        df = load_data(sym, n_bars=cfg['bars'])
        if df is None:
            print(f"{sym:<10} | {cfg['type']:<8} | NO DATA IN MT5")
            continue
            
        sim = InstitutionalPortfolioSimulator(initial_capital=10000.0, use_backup_model=True)
        sim.specs = {sym: cfg}
        res = sim.run({sym: df})
        
        status = "🏆 PASS (> 50%)" if res['cagr'] >= 50.0 else ("✅ PROFITABLE" if res['cagr'] > 0 else "❌ FAILS")
        print(f"{sym:<10} | {cfg['type']:<8} | {res['cagr']:+9.2f}% | {res['max_drawdown_pct']:8.2f}% | {res['profit_factor']:<5.2f} | {res['win_rate']:4.1f}% | {res['total_trades']:<6} | ${res['final_balance']:<16,.2f} | {status}")
        
    mt5.shutdown()
    print("=" * 135)

if __name__ == "__main__":
    evaluate_multi_asset_suite()

