"""
========================================================================================
V2 INSTITUTIONAL OPTIMIZED GOLD BREAKOUT ENSEMBLE
========================================================================================
Combines:
1. Preset 6 (1H Optimized Runner): 15L/3R, 2.0 ATR SL, 4.5 ATR TP, 200 EMA Filter, 40% Salvage
2. Preset 5 (1H Optimized Swing): 12L/3R, 1.5 ATR SL, 3.5 ATR TP, 200 EMA Filter, 40% Salvage
3. Preset 2 (1D Macro Trend Runner): 15L/2R, 0.44% SL, 3.10% TP, 200 EMA Filter
========================================================================================
"""

import os
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import pytz

def run_v2_optimized_ensemble():
    mt5.initialize()
    
    # Load 1H and 1D data
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 26000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    df_1h = df_1h[df_1h['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)
    
    h, l, c = df_1h['high'], df_1h['low'], df_1h['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df_1h['atr'] = tr.rolling(14).mean().bfill()
    df_1h['ema200'] = c.ewm(span=200, adjust=False).mean()
    
    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 2500)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    df_1d = df_1d[df_1d['dt'] >= pd.to_datetime("2022-09-01", utc=True)].reset_index(drop=True)
    h_d, l_d, c_d = df_1d['high'], df_1d['low'], df_1d['close']
    tr_d = np.maximum(h_d - l_d, np.maximum((h_d - c_d.shift(1)).abs(), (l_d - c_d.shift(1)).abs()))
    df_1d['atr'] = tr_d.rolling(14).mean().bfill()
    df_1d['ema200'] = c_d.ewm(span=200, adjust=False).mean()
    
    # Precompute fractals
    from optimize_gold_breakout_engine import compute_confirmed_fractals, simulate_optimized
    
    df_p6 = compute_confirmed_fractals(df_1h.copy(), 15, 3)
    df_p5 = compute_confirmed_fractals(df_1h.copy(), 12, 3)
    df_p2 = compute_confirmed_fractals(df_1d.copy(), 15, 2)
    
    print("=" * 100)
    print("V2 INSTITUTIONAL OPTIMIZED GOLD BREAKOUT BENCHMARK (2022 - 2026)")
    print("=" * 100)
    
    # 1. Preset 6 V2 Optimized
    p6_cfg = {
        'pre_offset': 0.0, # at breakout
        'use_atr_stops': True, 'sl_atr_mult': 2.0, 'tp_atr_mult': 4.5, 'be_atr_mult': 0.8,
        'use_trend_filter': True, 'salvage': True, 'salvage_threshold_ratio': 0.45,
        'salvage_min_bars': 2, 'salvage_tp_dist_pct': 0.0015
    }
    res_p6 = simulate_optimized(df_p6, p6_cfg, initial_capital=10000.0, risk_pct=0.010)
    print(f"• Preset 6 (1H ATR Runner) : Trades: {res_p6['trades']:<4} | CAGR: +{res_p6['cagr']:<5}% | PF: {res_p6['pf']:<4.2f} | DD: {res_p6['max_dd']:<4.1f}% | WinRate: {res_p6['win_rate']}% | Calmar: {res_p6['calmar']}")
    
    # 2. Preset 5 V2 Optimized
    p5_cfg = {
        'pre_offset': 0.0,
        'use_atr_stops': True, 'sl_atr_mult': 1.5, 'tp_atr_mult': 3.5, 'be_atr_mult': 0.6,
        'use_trend_filter': True, 'salvage': True, 'salvage_threshold_ratio': 0.45,
        'salvage_min_bars': 2, 'salvage_tp_dist_pct': 0.0015
    }
    res_p5 = simulate_optimized(df_p5, p5_cfg, initial_capital=10000.0, risk_pct=0.0075)
    print(f"• Preset 5 (1H ATR Swing)  : Trades: {res_p5['trades']:<4} | CAGR: +{res_p5['cagr']:<5}% | PF: {res_p5['pf']:<4.2f} | DD: {res_p5['max_dd']:<4.1f}% | WinRate: {res_p5['win_rate']}% | Calmar: {res_p5['calmar']}")
    
    # 3. Preset 2 V2 Optimized
    p2_cfg = {
        'pre_offset': 0.0005,
        'use_atr_stops': False, 'sl_pct': 0.0044, 'tp_pct': 0.0310, 'be_trigger_pct': 0.0030,
        'use_trend_filter': True, 'salvage': False
    }
    res_p2 = simulate_optimized(df_p2, p2_cfg, initial_capital=10000.0, risk_pct=0.010)
    print(f"• Preset 2 (1D Macro Runner): Trades: {res_p2['trades']:<4} | CAGR: +{res_p2['cagr']:<5}% | PF: {res_p2['pf']:<4.2f} | DD: {res_p2['max_dd']:<4.1f}% | WinRate: {res_p2['win_rate']}% | Calmar: {res_p2['calmar']}")
    
    # 4. Combined V2 Ensemble
    from generate_gold_breakout_quant_report import simulate_combined_ensemble
    v2_runs = {
        'Preset 6 (1H Runner)': res_p6,
        'Preset 5 (1H Swing)': res_p5,
        'Preset 2 (1D Runner)': res_p2
    }
    comb_v2 = simulate_combined_ensemble(v2_runs, initial_capital=10000.0)
    print("-" * 100)
    print(f"🏆 COMBINED V2 OPTIMIZED ENSEMBLE:")
    print(f"   Total Trades : {comb_v2['trades']} trades across 4 years (~24 trades/month)")
    print(f"   CAGR Return  : +{comb_v2['cagr']:.2f}% per year")
    print(f"   Profit Factor: {comb_v2['pf']:.2f}")
    print(f"   Win Rate     : {comb_v2['win_rate']:.1f}%")
    print(f"   Max Drawdown : {comb_v2['max_dd']:.2f}%")
    print(f"   Sharpe Ratio : {comb_v2['sharpe']:.2f}")
    print(f"   Calmar Ratio : {comb_v2['calmar']:.2f}")
    print("=" * 100)

if __name__ == '__main__':
    run_v2_optimized_ensemble()

