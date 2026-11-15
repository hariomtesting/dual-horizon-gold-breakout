"""
EMPIRICAL MARKET DNA INTERROGATION ENGINE: "ASKING THE MARKET"
==============================================================
Instead of human assumptions, we interrogate 5 years of tick/M5 data (2022-2026)
across XAUUSD (Gold) and EURUSD during the 11:00 AM - 16:00 IST operational window.

The Market answers 5 core questions:
1. THE VOLATILITY CLOCK: Which exact 30-minute window has the highest clean expansion?
2. DIRECTIONAL PERSISTENCE: If an impulse occurs at Frankfurt (12:00), does London (13:00) CONTINUE or REVERSE?
3. BREAKOUT RUN DISTANCE (MFE): When Asian High/Low breaks, how far does price travel ($5, $10, $15, $20)?
4. ADVERSE PULLBACK (MAE): What is the optimal Stop Loss distance that survives normal retests?
5. FRICTION RATIO: Which patterns have an edge-to-friction ratio > 10:1 (immune to real spreads & commissions)?
"""

import os
import sys
import time
import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data():
    connected = False
    for _ in range(5):
        if mt5.initialize():
            connected = True
            break
        time.sleep(1.0)
    if not connected:
        raise RuntimeError("MT5 init failed")
        
    symbols = ["XAUUSD", "EURUSD"]
    for s in symbols:
        mt5.symbol_select(s, True)
        
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_data = {}
    for s in symbols:
        rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            continue
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Keep full day from Asian open (05:30) to end of European window (16:00 IST)
        df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
        sessions = {}
        for d, sub in df_day.groupby('date'):
            if len(sub) >= 70:
                sub = sub.copy().reset_index(drop=True)
                # Asian range: 05:30 to 11:30 IST
                asian_bars = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
                if len(asian_bars) >= 20:
                    sub['asian_high'] = asian_bars['high'].max()
                    sub['asian_low'] = asian_bars['low'].min()
                    sub['asian_mid'] = (sub['asian_high'] + sub['asian_low']) / 2.0
                    sub['asian_range'] = sub['asian_high'] - sub['asian_low']
                    sessions[d] = sub.to_dict('records')
        asset_data[s] = sessions
    mt5.shutdown()
    return asset_data

def interrogate_market(asset_data):
    for sym in ["XAUUSD", "EURUSD"]:
        sessions = asset_data[sym]
        all_dates = sorted(list(sessions.keys()))
        is_gold = ("XAU" in sym)
        unit_name = "$" if is_gold else "pips"
        unit_scale = 1.0 if is_gold else 10000.0 # 0.00010 = 1 pip
        
        print("\n" + "=" * 90)
        print(f"MARKET DNA INTERROGATION: {sym} (Analyzed across {len(all_dates)} days, 2022-2026)")
        print("=" * 90)
        
        # -------------------------------------------------------------
        # QUESTION 1: THE VOLATILITY CLOCK (Which 30-min window moves most?)
        # -------------------------------------------------------------
        intervals = [
            ("11:00 - 11:30 (Late Asian)", 11, 0, 11, 30),
            ("11:30 - 12:00 (Pre-Frankfurt)", 11, 30, 12, 0),
            ("12:00 - 12:30 (Frankfurt Pre-Open)", 12, 0, 12, 30),
            ("12:30 - 13:00 (Frankfurt Cash Open)", 12, 30, 13, 0),
            ("13:00 - 13:30 (London Cash Open)", 13, 0, 13, 30),
            ("13:30 - 14:00 (London Initial Drive)", 13, 30, 14, 0),
            ("14:00 - 14:30 (Mid-London Session)", 14, 0, 14, 30),
            ("14:30 - 15:00 (Pre-US Setup)", 14, 30, 15, 0),
            ("15:00 - 15:30 (US Pre-Market)", 15, 0, 15, 30),
            ("15:30 - 15:55 (Pre-Close / Settlement)", 15, 30, 15, 55),
        ]
        
        clock_stats = []
        for name, sh, sm, eh, em in intervals:
            moves = []
            ranges = []
            for d in all_dates:
                bars = sessions[d]
                sub = [b for b in bars if (b['ist_hour'] > sh or (b['ist_hour'] == sh and b['ist_minute'] >= sm)) and
                                          (b['ist_hour'] < eh or (b['ist_hour'] == eh and b['ist_minute'] <= em))]
                if len(sub) >= 4:
                    o = sub[0]['open']
                    c = sub[-1]['close']
                    h = max(b['high'] for b in sub)
                    l = min(b['low'] for b in sub)
                    ranges.append((h - l) * unit_scale)
                    moves.append(abs(c - o) * unit_scale) # Net directional push
                    
            if len(ranges) > 0:
                clock_stats.append({
                    'window': name,
                    'median_range': np.median(ranges),
                    'median_direction': np.median(moves),
                    'expansion_ratio': np.median(moves) / np.median(ranges) if np.median(ranges) > 0 else 0
                })
                
        print("\n[QUESTION 1: THE VOLATILITY CLOCK]")
        print(f"{'TIME WINDOW (IST)':<38} | {'MEDIAN RANGE':<14} | {'NET DIRECTION':<14} | {'EFFICIENCY RATIO'}")
        print("-" * 85)
        for cs in clock_stats:
            print(f"{cs['window']:<38} | {cs['median_range']:>7.2f} {unit_name:<4} | {cs['median_direction']:>7.2f} {unit_name:<4} | {cs['expansion_ratio']*100:>6.1f}%")
            
        # -------------------------------------------------------------
        # QUESTION 2: DIRECTIONAL PERSISTENCE (Continuations vs Traps)
        # -------------------------------------------------------------
        # If Frankfurt (12:00 - 12:30) is strong BULLISH/BEARISH:
        # What is the probability that London (12:30 - 14:00) CONTINUES the move vs REVERSES?
        cont_count = 0
        rev_count = 0
        neutral_count = 0
        
        for d in all_dates:
            bars = sessions[d]
            f_bars = [b for b in bars if b['ist_hour'] == 12 and b['ist_minute'] <= 30]
            l_bars = [b for b in bars if (b['ist_hour'] == 12 and b['ist_minute'] > 30) or (b['ist_hour'] == 13)]
            
            if len(f_bars) >= 5 and len(l_bars) >= 10:
                f_move = f_bars[-1]['close'] - f_bars[0]['open']
                l_move = l_bars[-1]['close'] - l_bars[0]['open']
                
                # Minimum threshold for significance
                thresh = 2.0 if is_gold else (8.0 / 10000.0) # $2 on Gold, 8 pips on EUR
                if abs(f_move) >= thresh:
                    if (f_move > 0 and l_move > 0) or (f_move < 0 and l_move < 0):
                        cont_count += 1
                    elif (f_move > 0 and l_move < -thresh) or (f_move < 0 and l_move > thresh):
                        rev_count += 1
                    else:
                        neutral_count += 1
                        
        total_p = cont_count + rev_count + neutral_count
        print("\n[QUESTION 2: CONTINUATION VS REVERSAL PROBABILITY]")
        if total_p > 0:
            print(f"When Frankfurt (12:00-12:30 IST) prints an impulse move:")
            print(f"  -> London CONTINUATION Probability : {cont_count/total_p*100:.1f}% ({cont_count}/{total_p} days)")
            print(f"  -> London REVERSAL / TRAP Probability: {rev_count/total_p*100:.1f}% ({rev_count}/{total_p} days)")
            print(f"  -> London Neutral / Chop Probability : {neutral_count/total_p*100:.1f}% ({neutral_count}/{total_p} days)")
            
        # -------------------------------------------------------------
        # QUESTION 3 & 4: MFE (RUN DISTANCE) & MAE (OPTIMAL STOP LOSS)
        # -------------------------------------------------------------
        # When Asian High or Low is breached between 11:30 and 14:00 IST:
        # What is the actual Maximum Favorable Excursion (MFE) before 15:55 IST?
        # What is the Maximum Adverse Excursion (MAE) after breakout?
        high_breaks_mfe = []
        high_breaks_mae = []
        low_breaks_mfe = []
        low_breaks_mae = []
        
        for d in all_dates:
            bars = sessions[d]
            start_i = -1
            for idx, b in enumerate(bars):
                if b['ist_hour'] == 11 and b['ist_minute'] == 30:
                    start_i = idx
                    break
            if start_i == -1:
                continue
                
            ah = bars[start_i]['asian_high']
            al = bars[start_i]['asian_low']
            
            # Look for first break between 11:30 and 14:00 IST
            broken_high = False
            broken_low = False
            break_i = -1
            break_dir = None
            
            for i in range(start_i, len(bars)):
                b = bars[i]
                if b['ist_hour'] >= 14:
                    break
                if not broken_high and b['high'] > ah:
                    broken_high = True
                    break_i = i
                    break_dir = 'HIGH'
                    break
                elif not broken_low and b['low'] < al:
                    broken_low = True
                    break_i = i
                    break_dir = 'LOW'
                    break
                    
            if break_i != -1:
                # Track post-break MFE and MAE until 15:55 IST
                post_bars = bars[break_i:]
                if break_dir == 'HIGH':
                    trigger_p = ah
                    max_fav = max(b['high'] for b in post_bars) - trigger_p
                    max_adv = trigger_p - min(b['low'] for b in post_bars)
                    high_breaks_mfe.append(max_fav * unit_scale)
                    high_breaks_mae.append(max_adv * unit_scale)
                else:
                    trigger_p = al
                    max_fav = trigger_p - min(b['low'] for b in post_bars)
                    max_adv = max(b['high'] for b in post_bars) - trigger_p
                    low_breaks_mfe.append(max_fav * unit_scale)
                    low_breaks_mae.append(max_adv * unit_scale)

        all_mfe = high_breaks_mfe + low_breaks_mfe
        all_mae = high_breaks_mae + low_breaks_mae
        
        print("\n[QUESTION 3 & 4: EMPIRICAL RUN DISTANCE (MFE) & PULLBACK DEPTH (MAE)]")
        if len(all_mfe) > 0:
            mfe_p25 = np.percentile(all_mfe, 25)
            mfe_p50 = np.percentile(all_mfe, 50) # Median run
            mfe_p75 = np.percentile(all_mfe, 75)
            
            mae_p50 = np.percentile(all_mae, 50)
            mae_p75 = np.percentile(all_mae, 75)
            mae_p90 = np.percentile(all_mae, 90) # Stop that survives 90% of retests
            
            print(f"Total Asian Range Breakouts Analyzed: {len(all_mfe)} days")
            print(f"  -> 25% of Breakouts Run At Least    : {mfe_p75:.2f} {unit_name}")
            print(f"  -> Median Breakout Run (50% MFE)    : {mfe_p50:.2f} {unit_name} (Realistic Target!)")
            print(f"  -> 75% of Breakouts Run At Least    : {mfe_p25:.2f} {unit_name}")
            print(f"  -- Pullback Profile (MAE) --")
            print(f"  -> 50% of Breakouts Pull Back By    : {mae_p50:.2f} {unit_name}")
            print(f"  -> 75% of Breakouts Survive SL of   : {mae_p75:.2f} {unit_name}")
            print(f"  -> 90% of Breakouts Survive SL of   : {mae_p90:.2f} {unit_name} (Immunity Stop Loss!)")
            
            # Calculate Natural Market Edge
            market_rr = mfe_p50 / mae_p75 if mae_p75 > 0 else 0
            print(f"  -> Natural Market R:R Ratio         : {market_rr:.2f}:1")

        # -------------------------------------------------------------
        # QUESTION 5: THE EDGE-TO-FRICTION RATIO
        # -------------------------------------------------------------
        # Real Vantage round-turn friction:
        # Gold: $0.30 spread + $0.06 comm + $0.23 slip = $0.59
        # EURUSD: 1.4 pips spread + 0.6 comm + 0.6 slip = 2.60 pips
        real_friction = 0.59 if is_gold else 2.60
        median_target = mfe_p50 if len(all_mfe) > 0 else 1.0
        friction_pct = (real_friction / median_target) * 100.0
        
        print("\n[QUESTION 5: THE FRICTION IMMUNITY SCORE]")
        print(f"  -> Real Round-Turn Broker Toll      : {real_friction:.2f} {unit_name}")
        print(f"  -> Friction as % of Median Target   : {friction_pct:.1f}%")
        if friction_pct < 10.0:
            print(f"  -> VERDICT: [HIGHLY VIABLE] Friction consumes < 10% of market move. Strong alpha potential!")
        elif friction_pct < 20.0:
            print(f"  -> VERDICT: [MODERATE] Friction consumes 10-20%. Requires high selectivity.")
        else:
            print(f"  -> VERDICT: [UNVIABLE FOR SCALPING] Friction consumes > 20% of target move. Edge will be destroyed.")
        print("=" * 90)

if __name__ == "__main__":
    asset_data = load_data()
    interrogate_market(asset_data)

