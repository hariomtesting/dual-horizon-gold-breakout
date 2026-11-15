"""
Compare Gold Breakout Presets: Full Swing (Overnight) vs Daytime Only (10:00-15:55 IST Flat Close)
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import pytz
from backtest_gold_breakout_9presets import load_gold_data, identify_fractals

def test_daytime_cutoff_impact():
    mt5.initialize()
    df_1h = load_gold_data(mt5.TIMEFRAME_H1, n_bars=25000)
    
    # Test Preset 6 (15L/3R, 1.8% SL, 3.0% TP) and Preset 5
    for p_name, p in [
        ('Preset 6 (1H Runner)', {'x_left': 15, 'y_right': 3, 'sl_pct': 0.018, 'tp_pct': 0.030, 'be_trigger_pct': 0.006, 'salvage': True, 'salvage_trigger': 0.009}),
        ('Preset 5 (1H Swing)', {'x_left': 12, 'y_right': 3, 'sl_pct': 0.015, 'tp_pct': 0.025, 'be_trigger_pct': 0.005, 'salvage': True, 'salvage_trigger': 0.0075})
    ]:
        df_f = identify_fractals(df_1h.copy(), p['x_left'], p['y_right'])
        df_f['hour'] = df_f['ist_time'].dt.hour
        
        # 1. Overnight Hold
        # 2. Strict 15:55 IST Square-off
        print(f"\n--- Testing {p_name} ---")
        
        # Simulate both
        for mode in ['OVERNIGHT_SWING', 'DAYTIME_FLAT_1555']:
            trades = []
            active = None
            cap = 10000.0
            peak = 10000.0
            max_dd = 0.0
            
            bars = df_f.to_dict('records')
            for i in range(1, len(bars)):
                r = bars[i]
                prev = bars[i-1]
                h = r['hour']
                
                # If daytime mode: only enter between 10:00 and 15:00, force close at 16:00 (15:55)
                if mode == 'DAYTIME_FLAT_1555':
                    if h >= 16 and active is not None:
                        # Force flat close
                        pnl = (r['close'] - active['entry']) * active['lots'] * 100.0 if active['type'] == 'BUY' else (active['entry'] - r['close']) * active['lots'] * 100.0
                        cap += pnl - (active['lots'] * 6.0)
                        trades.append(pnl)
                        active = None
                        continue
                        
                if active is not None:
                    # check SL / TP
                    d = active['type']
                    entry = active['entry']
                    sl = active['sl']
                    tp = active['tp']
                    lots = active['lots']
                    
                    # Salvage
                    if p['salvage']:
                        adv = (entry - r['low']) if d == 'BUY' else (r['high'] - entry)
                        if adv >= (entry * p['salvage_trigger']):
                            active['salvage'] = True
                            if d == 'BUY': tp = min(tp, r['close'] * 1.0015)
                            else: tp = max(tp, r['close'] * 0.9985)
                            
                    # BE
                    fav = (r['high'] - entry) if d == 'BUY' else (entry - r['low'])
                    if fav >= (entry * p['be_trigger_pct']):
                        be_lvl = entry * 1.0005 if d == 'BUY' else entry * 0.9995
                        if d == 'BUY' and sl < be_lvl: sl = be_lvl
                        elif d == 'SELL' and sl > be_lvl: sl = be_lvl
                        
                    hit_tp = (r['high'] >= tp) if d == 'BUY' else (r['low'] <= tp)
                    hit_sl = (r['low'] <= sl) if d == 'BUY' else (r['high'] >= sl)
                    
                    if hit_tp:
                        pnl = (tp - entry) * lots * 100.0 if d == 'BUY' else (entry - tp) * lots * 100.0
                        cap += pnl - (lots * 6.0)
                        trades.append(pnl)
                        active = None
                    elif hit_sl:
                        pnl = (sl - entry) * lots * 100.0 if d == 'BUY' else (entry - sl) * lots * 100.0
                        cap += pnl - (lots * 6.0)
                        trades.append(pnl)
                        active = None
                        
                if cap > peak: peak = cap
                dd = (peak - cap) / peak * 100.0
                if dd > max_dd: max_dd = dd
                
                # Entry
                if active is None:
                    if mode == 'DAYTIME_FLAT_1555' and (h < 10 or h >= 15):
                        continue
                    sw_h = prev['conf_swing_high']
                    sw_l = prev['conf_swing_low']
                    if pd.isna(sw_h) or pd.isna(sw_l): continue
                    
                    bt = sw_h * 0.9995
                    st = sw_l * 1.0005
                    
                    if prev['close'] < bt and r['high'] >= bt:
                        entry = bt + 0.14
                        sl = entry * (1.0 - p['sl_pct'])
                        tp = entry * (1.0 + p['tp_pct'])
                        lots = max(0.01, round((cap * 0.015) / (abs(entry - sl) * 100.0), 2))
                        active = {'type': 'BUY', 'entry': entry, 'sl': sl, 'tp': tp, 'lots': lots, 'salvage': False}
                    elif prev['close'] > st and r['low'] <= st:
                        entry = st - 0.14
                        sl = entry * (1.0 + p['sl_pct'])
                        tp = entry * (1.0 - p['tp_pct'])
                        lots = max(0.01, round((cap * 0.015) / (abs(entry - sl) * 100.0), 2))
                        active = {'type': 'SELL', 'entry': entry, 'sl': sl, 'tp': tp, 'lots': lots, 'salvage': False}
                        
            wins = [p for p in trades if p > 0]
            losses = [p for p in trades if p < 0]
            gw = sum(wins) if wins else 0
            gl = abs(sum(losses)) if losses else 0
            pf = (gw / gl) if gl > 0 else 99
            wr = len(wins) / len(trades) * 100 if trades else 0
            roi = (cap - 10000.0) / 10000.0 * 100.0
            print(f"  [{mode:<18}]: Trades: {len(trades):<5} | ROI: +{roi:<7.1f}% | PF: {pf:<5.2f} | Max DD: {max_dd:<5.2f}% | WinRate: {wr:.1f}%")

if __name__ == '__main__':
    test_daytime_cutoff_impact()

