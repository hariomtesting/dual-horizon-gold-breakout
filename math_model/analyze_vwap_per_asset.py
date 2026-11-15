"""
PER-ASSET VWAP DIAGNOSTIC
Break down VWAP performance on XAUUSD (Gold) vs EURUSD individually.
"""
import pandas as pd
import numpy as np
from optimize_intraday_vwap_scalper import load_full_day_data, simulate_vwap_variant, analyze

asset_data = load_full_day_data()

print("=" * 80)
print("INDIVIDUAL ASSET BREAKDOWN: VWAP PULLBACK & REVERSION")
print("=" * 80)

for sym in ["XAUUSD", "EURUSD"]:
    single_data = {sym: asset_data[sym]}
    
    # Reversion 1.5s
    df_rev, ts_rev = simulate_vwap_variant(single_data, anchor_mode="day", band_sigma=1.5, strategy_type="reversion", sl_mult=0.75, tp_mult=1.45)
    r_rev = analyze(df_rev, ts_rev, f"{sym} - Reversion (1.5s)")
    
    # Trend Pullback
    df_pb, ts_pb = simulate_vwap_variant(single_data, anchor_mode="day", strategy_type="trend_pullback", sl_mult=0.75, tp_mult=1.45)
    r_pb = analyze(df_pb, ts_pb, f"{sym} - Trend Pullback")

    print(f"\nASSET: {sym}")
    print(f"  [Reversion] ROI: {r_rev['avg_annual']:+.2f}% | Max DD: {r_rev['max_dd']:.2f}% | PF: {r_rev['pf']} | Green: {r_rev['green_months']}")
    print(f"  [Pullback]  ROI: {r_pb['avg_annual']:+.2f}% | Max DD: {r_pb['max_dd']:.2f}% | PF: {r_pb['pf']} | Green: {r_pb['green_months']}")

