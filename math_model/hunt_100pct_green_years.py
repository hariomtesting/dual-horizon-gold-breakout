import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from math_model.test_split_target_quantum_engine import load_data, run_split_target_simulation

sessions = load_data()

print("=" * 140)
print("HUNTING FOR 100% GREEN YEARS (2022 TO 2026 ALL POSITIVE) WITH SPLIT-TARGET ENGINE")
print("=" * 140)

found = []
for risk in [0.020, 0.025, 0.030, 0.035]:
    for sl_mult in [2.0, 2.5, 3.0]:
        for tp1 in [1.0, 1.2, 1.4]:
            for tp2 in [2.5, 3.0, 3.5]:
                for vf in [6.0, 7.5, 9.0]:
                    res = run_split_target_simulation(sessions, risk_pct=risk, sl_atr_mult=sl_mult, tp1_r=tp1, tp2_r=tp2, vol_floor=vf, compounding=True)
                    min_y = res['min_year']
                    if min_y >= 0.0:
                        found.append(res)
                        y_str = ", ".join([f"{y}: {v:+.1f}%" for y, v in res['annual_dict'].items()])
                        print(f"[100% GREEN!] Risk {risk*100:.1f}% | SL {sl_mult:.1f}x | TP1 {tp1:.1f}R | TP2 {tp2:.1f}R | VF ${vf:.1f} -> Annual ROI: {res['annual_roi']:+6.1f}% | Max DD: {res['max_dd']:5.1f}% | Final: ${res['final_balance']:,.0f} | {y_str}")
                    elif min_y > -6.0:
                        y_str = ", ".join([f"{y}: {v:+.1f}%" for y, v in res['annual_dict'].items()])
                        print(f"[MILD] Risk {risk*100:.1f}% | SL {sl_mult:.1f}x | TP1 {tp1:.1f}R | TP2 {tp2:.1f}R | VF ${vf:.1f} -> Annual ROI: {res['annual_roi']:+6.1f}% | Min Yr: {min_y:+5.1f}% | Final: ${res['final_balance']:,.0f} | {y_str}")

print(f"\nTotal 100% Green Year configurations found: {len(found)}")

