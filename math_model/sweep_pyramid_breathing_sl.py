import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from math_model.test_pyramiding_live_simulator import load_gold_sessions, run_pyramiding_simulation

sessions = load_gold_sessions()

print("=" * 110)
print("SWEEPING PYRAMID BREATHING SL & TRIGGER DISTANCES")
print("=" * 110)

for lock in [-0.5, -0.25, 0.0, 0.25]:
    for trig in [0.75, 1.0, 1.25, 1.5]:
        vname = f"Pyramid Trig +{trig:.2f}R | SL1 moved to {lock:+.2f}R"
        res = run_pyramiding_simulation(
            sessions,
            variant_name=vname,
            r1_pct=0.010,
            r2_pct=0.010,
            pyr_trigger_r=trig,
            lock_sl1_r=lock,
            sl_dollars=6.00,
            rr_multiplier=2.0,
            use_limit_entry=True
        )
        print(f"{res['variant']:<50} | ROI: {res['avg_annual_roi']:+6.2f}% | Max DD: {res['max_dd']:6.2f}% | PF: {res['pf']:.2f} | Final: ${res['final_balance']:,.2f}")

