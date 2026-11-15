"""
OPTIMIZING FRANKFURT PRE-OPEN SNIPER CUTOFF & OVERLAP RULES
Testing whether Frankfurt pending orders should be canceled before 1:00 PM IST (e.g. 12:55 IST)
to prevent double pending orders, or if keeping them active until 14:00 IST is superior.
"""
import pandas as pd
import numpy as np
from compare_and_select_champion_sibling import load_data, simulate_anchor_sniper, analyze_strategy, print_result_card

asset_data = load_data()

print("\n--- TEST 1: Cancel Frankfurt Pending Orders at 12:55 IST (Strict Zero-Overlap) ---")
df_f1255, ts_f1255 = simulate_anchor_sniper(asset_data, 12, 0, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=12, order_cutoff_m=55)
res_f1255 = analyze_strategy(df_f1255, ts_f1255, "Frankfurt Sniper (Cutoff 12:55 IST)")
print_result_card(res_f1255)

print("\n--- TEST 2: Cancel Frankfurt Pending Orders at 13:30 IST ---")
df_f1330, ts_f1330 = simulate_anchor_sniper(asset_data, 12, 0, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=13, order_cutoff_m=30)
res_f1330 = analyze_strategy(df_f1330, ts_f1330, "Frankfurt Sniper (Cutoff 13:30 IST)")
print_result_card(res_f1330)

print("\n--- TEST 3: Cancel Frankfurt Pending Orders at 14:00 IST ---")
df_f1400, ts_f1400 = simulate_anchor_sniper(asset_data, 12, 0, entry_mult=0.30, sl_mult=0.75, tp_mult=1.45, order_cutoff_h=14, order_cutoff_m=0)
res_f1400 = analyze_strategy(df_f1400, ts_f1400, "Frankfurt Sniper (Cutoff 14:00 IST)")
print_result_card(res_f1400)

