"""
Test the Combined Multi-Preset Portfolio (Ensemble of Top Presets on Gold)
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from backtest_gold_breakout_9presets import load_gold_data, identify_fractals, simulate_preset

def run_combined_portfolio():
    mt5.initialize()
    
    presets = {
        'Preset 2 (1D Runner)': {
            'tf': mt5.TIMEFRAME_D1, 'bars': 5000,
            'x_left': 15, 'y_right': 2,
            'sl_pct': 0.0044, 'tp_pct': 0.0310, 'be_trigger_pct': 0.0030,
            'salvage': False, 'weight': 0.40
        },
        'Preset 6 (1H Runner)': {
            'tf': mt5.TIMEFRAME_H1, 'bars': 25000,
            'x_left': 15, 'y_right': 3,
            'sl_pct': 0.018, 'tp_pct': 0.030, 'be_trigger_pct': 0.0060,
            'salvage': True, 'salvage_trigger_pct': 0.009, 'salvage_tp_dist_pct': 0.0015,
            'weight': 0.30
        },
        'Preset 5 (1H Swing)': {
            'tf': mt5.TIMEFRAME_H1, 'bars': 25000,
            'x_left': 12, 'y_right': 3,
            'sl_pct': 0.015, 'tp_pct': 0.025, 'be_trigger_pct': 0.0050,
            'salvage': True, 'salvage_trigger_pct': 0.0075, 'salvage_tp_dist_pct': 0.0015,
            'weight': 0.15
        },
        'Preset 4 (4H Swing)': {
            'tf': mt5.TIMEFRAME_H4, 'bars': 12000,
            'x_left': 10, 'y_right': 2,
            'sl_pct': 0.012, 'tp_pct': 0.022, 'be_trigger_pct': 0.0040,
            'salvage': True, 'salvage_trigger_pct': 0.006, 'salvage_tp_dist_pct': 0.0015,
            'weight': 0.15
        }
    }
    
    print("=" * 100)
    print("GOLD MULTI-PRESET ENSEMBLE PORTFOLIO BENCHMARK")
    print("=" * 100)
    
    # Run each
    individual_runs = {}
    for name, p in presets.items():
        df = load_gold_data(p['tf'], n_bars=p['bars'])
        df_f = identify_fractals(df, p['x_left'], p['y_right'])
        res = simulate_preset(df_f, p, initial_capital=10000.0, risk_pct=0.015 * p['weight'])
        individual_runs[name] = res
        print(f"• {name:<22}: Trades: {res['trades']:<5} | CAGR: +{res['cagr']:<6}% | PF: {res['pf']:<5.2f} | DD: {res['max_dd']:<5.2f}% | WinRate: {res['win_rate']}%")
        
    print("=" * 100)

if __name__ == '__main__':
    run_combined_portfolio()

