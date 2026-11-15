"""
ISOLATING THE FRICTION RATIO: XAUUSD (GOLD) VS EURUSD
Revealing why micro-scalping FX fails under real spreads while Gold has structural tolerance.
"""
import pandas as pd
import numpy as np
from simulate_live_market_frictions import load_data, simulate_with_frictions, analyze

asset_data = load_data()

tier1_params = {
    "spread_gold": 0.30, "spread_eur": 0.00012,
    "comm_gold_per_lot": 6.00, "comm_eur_per_lot": 6.00,
    "slip_entry_gold": 0.10, "slip_entry_eur": 0.00005,
    "slip_exit_gold": 0.05, "slip_exit_eur": 0.00003
}

print("=" * 90)
print("ASSET COMPARISON UNDER REAL-WORLD TIER 1 LIVE FRICTIONS (1:00 PM SNIPER)")
print("=" * 90)

for sym in ["XAUUSD", "EURUSD"]:
    single = {sym: asset_data[sym]}
    
    # 1. Zero friction
    df_raw, ts_raw = simulate_with_frictions(single, anchor_hour=13, anchor_minute=0)
    r_raw = analyze(df_raw, ts_raw, f"{sym} - Frictionless Baseline")
    
    # 2. Tier 1 live friction
    df_fric, ts_fric = simulate_with_frictions(single, anchor_hour=13, anchor_minute=0, **tier1_params)
    r_fric = analyze(df_fric, ts_fric, f"{sym} - Tier 1 Live ECN")

    print(f"\nASSET: {sym}")
    print(f"  [Frictionless] ROI: {r_raw['avg_annual']:+8.2f}% | PF: {r_raw['pf']} | Avg R: {r_raw['avg_r']} | Max DD: {r_raw['max_dd']:.2f}%")
    print(f"  [Live Tier 1]  ROI: {r_fric['avg_annual']:+8.2f}% | PF: {r_fric['pf']} | Avg R: {r_fric['avg_r']} | Max DD: {r_fric['max_dd']:.2f}%")

print("=" * 90)

