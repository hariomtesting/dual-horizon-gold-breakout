"""
========================================================================================
AUDIT GOLD TESTING MODE ALGO: PAST 2 YEARS (NOV 2024 - NOV 2026)
COMPARISON: 24-HOUR VS 10:00 TO 16:00 WINDOW (IST, UTC & BROKER)
========================================================================================
"""

import os
import random
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def run_audit():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    ist = pytz.timezone('Asia/Kolkata')
    
    start_date = pd.to_datetime("2024-11-14 00:00:00", utc=True)
    end_date = pd.to_datetime("2026-11-14 00:00:00", utc=True)
    
    # 1. Fetch 1H Bars
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 18000)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt_utc'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    df_1h['dt_ist'] = df_1h['dt_utc'].dt.tz_convert(ist)
    df_1h['hour_utc'] = df_1h['dt_utc'].dt.hour
    df_1h['hour_ist'] = df_1h['dt_ist'].dt.hour
    df_1h['hour_broker'] = pd.to_datetime(df_1h['time'], unit='s').dt.hour
    
    # Technical Indicators
    h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
    tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
    df_1h['atr'] = tr1.rolling(14).mean().bfill()
    df_1h['atr_ma'] = df_1h['atr'].rolling(30).mean().bfill()
    df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()

    # Zero-Lookahead Fractals
    def add_fractals(df, x_left, y_right):
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)
        sh_arr = np.full(n, np.nan)
        sl_arr = np.full(n, np.nan)
        for i in range(x_left, n - y_right):
            hc = highs[i]
            lc = lows[i]
            if all(highs[i - k] < hc for k in range(1, x_left + 1)) and all(highs[i + k] < hc for k in range(1, y_right + 1)):
                sh_arr[i + y_right] = hc
            if all(lows[i - k] > lc for k in range(1, x_left + 1)) and all(lows[i + k] > lc for k in range(1, y_right + 1)):
                sl_arr[i + y_right] = lc
        df['conf_sh'] = pd.Series(sh_arr).ffill()
        df['conf_sl'] = pd.Series(sl_arr).ffill()
        return df

    df_1h = add_fractals(df_1h, 15, 3)

    # Also 1D Macro data for 24-hr ensemble comparison
    r_1d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 1200)
    df_1d = pd.DataFrame(r_1d)
    df_1d['dt'] = pd.to_datetime(df_1d['time'], unit='s', utc=True)
    hd, ld, cd = df_1d['high'], df_1d['low'], df_1d['close']
    trd = np.maximum(hd - ld, np.maximum((hd - cd.shift(1)).abs(), (ld - cd.shift(1)).abs()))
    df_1d['atr'] = trd.rolling(14).mean().bfill()
    df_1d['ema200'] = cd.ewm(span=200, adjust=False).mean()
    df_1d = add_fractals(df_1d, 15, 2)

    df_eval_1h = df_1h[(df_1h['dt_utc'] >= start_date) & (df_1h['dt_utc'] <= end_date)].reset_index(drop=True)
    df_eval_1d = df_1d[(df_1d['dt'] >= start_date) & (df_1d['dt'] <= end_date)].reset_index(drop=True)

    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    max_lot_cap = 10.0

    def simulate(time_filter=None, force_flat_at_16=False, tz_col='hour_ist', include_macro=False):
        """
        time_filter: tuple of (start_hour, end_hour) e.g. (10, 16)
        force_flat_at_16: whether to force close at hour >= 16
        """
        random.seed(42)
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        trades = []
        eq_curve = []
        
        pos = None
        pending = None
        last_f = None
        
        bars = df_eval_1h.to_dict('records')
        for idx in range(1, len(bars)):
            row = bars[idx]
            prev_row = bars[idx - 1]
            cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt_utc']
            cur_hour = row[tz_col]
            atr, atr_ma, ema = prev_row['atr'], prev_row['atr_ma'], prev_row['ema200']
            is_exp = (atr > 1.4 * atr_ma)
            
            c_dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            r_mult = 0.0 if c_dd >= 20.0 else (0.33 if c_dd >= 15.0 else (0.67 if c_dd >= 10.0 else 1.0))
            
            # 1. Manage Active Position
            if pos is not None:
                # Force flat square-off at 16:00 if enabled
                if force_flat_at_16 and cur_hour >= 16:
                    d = pos['dir']
                    exit_p = cur_c
                    rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                    pts = (exit_p - pos['entry']) if d == 'BUY' else (pos['entry'] - exit_p)
                    pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                    cap += pnl_exit
                    trades.append({'dt': cur_dt, 'dir': d, 'pnl': pnl_exit, 'type': 'DAYTIME_1600_FLAT'})
                    pos = None
                else:
                    pos['bars'] += 1
                    d = pos['dir']
                    entry, sl, tp1, sl_dist = pos['entry'], pos['sl'], pos['tp1'], pos['sl_dist']
                    
                    if d == 'BUY': pos['extreme'] = max(pos['extreme'], cur_h)
                    else: pos['extreme'] = min(pos['extreme'], cur_l)
                    
                    # Salvage
                    if not pos['t1_hit'] and pos['bars'] >= 2:
                        adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                        if adv >= (sl_dist * 0.45):
                            pos['salvage_active'] = True
                        if pos.get('salvage_active', False):
                            tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                            if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                                tp1 = tight_tp
                                pos['tp1'] = tp1
                                
                    # TP1 Hit
                    if not pos['t1_hit']:
                        hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                        if hit_tp1:
                            pnl1 = (abs(tp1 - entry) * pos['lots_t1'] * contract) - (pos['lots_t1'] * comm_per_lot)
                            cap += pnl1
                            pos['t1_hit'] = True
                            be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                            pos['sl'] = be_lvl
                            sl = be_lvl
                            trades.append({'dt': cur_dt, 'dir': d, 'pnl': pnl1, 'type': 'TP1_LOCK'})
                            
                    # Chandelier Trail
                    if pos['t1_hit']:
                        trail_sl = pos['extreme'] - (2.8 * atr) if d == 'BUY' else pos['extreme'] + (2.8 * atr)
                        if (d == 'BUY' and trail_sl > pos['sl']) or (d == 'SELL' and trail_sl < pos['sl']):
                            pos['sl'] = trail_sl
                            sl = trail_sl
                            
                    # Stop Loss
                    hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
                    if hit_sl:
                        slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                        exit_p = sl - slip if d == 'BUY' else sl + slip
                        rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                        pts = (exit_p - entry) if d == 'BUY' else (entry - exit_p)
                        pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                        cap += pnl_exit
                        reason = 'CHANDELIER_TRAIL' if pos['t1_hit'] else ('SALVAGE' if pos.get('salvage_active') else 'INITIAL_SL')
                        trades.append({'dt': cur_dt, 'dir': d, 'pnl': pnl_exit, 'type': reason})
                        pos = None

            if cap > peak: peak = cap
            dd = (peak - cap) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd: max_dd = dd
            eq_curve.append({'dt': cur_dt, 'bal': cap, 'dd': dd})
            
            # 2. Check Time Filter for New Setups
            if time_filter is not None:
                start_h, end_h = time_filter
                # Can only prepare setups within [start_h, end_h)
                if not (start_h <= cur_hour < end_h):
                    pending = None
                    continue
                    
            sw_h, sw_l = prev_row['conf_sh'], prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l): continue
            
            if pos is None and r_mult > 0.0:
                t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                t_f = sw_h if t_dir == 'BUY' else sw_l
                
                if pending is not None and (pending['dir'] != t_dir or abs(pending['f'] - t_f) > 0.10):
                    pending = None
                    
                if pending is None and t_dir is not None and last_f != t_f:
                    r_cash = cap * (0.025 * r_mult)
                    entry_t = sw_h if t_dir == 'BUY' else sw_l
                    sl = entry_t - (2.0 * atr) if t_dir == 'BUY' else entry_t + (2.0 * atr)
                    sl_dist = abs(entry_t - sl)
                    tp1 = entry_t + (2.5 * atr) if t_dir == 'BUY' else entry_t - (2.5 * atr)
                    if sl_dist > 0:
                        tot_l = max(0.02, min(max_lot_cap, round(r_cash / (sl_dist * contract), 2)))
                        pending = {'dir': t_dir, 'trig': entry_t, 'f': t_f, 'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist, 'l1': round(tot_l * 0.5, 2), 'l2': round(tot_l - round(tot_l * 0.5, 2), 2)}
                        
                if pending is not None:
                    slip = round(random.uniform(0.12, 0.28), 2) if is_exp else round(random.uniform(0.04, 0.12), 2)
                    if (pending['dir'] == 'BUY' and cur_h >= (pending['trig'] + spread)) or (pending['dir'] == 'SELL' and cur_l <= (pending['trig'] - spread)):
                        exec_p = (pending['trig'] + (spread / 2.0) + slip) if pending['dir'] == 'BUY' else (pending['trig'] - (spread / 2.0) - slip)
                        pos = {'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'], 'tp1': pending['tp1'], 'sl_dist': pending['sl_dist'], 'lots_t1': pending['l1'], 'lots_t2': pending['l2'], 't1_hit': False, 'extreme': exec_p, 'bars': 0, 'salvage_active': False}
                        last_f = pending['f']
                        pending = None

        # Add macro trades if enabled and not strictly flat
        if include_macro and not force_flat_at_16:
            bars_d1 = df_eval_1d.to_dict('records')
            pos_1d = None
            for idx in range(1, len(bars_d1)):
                row = bars_d1[idx]
                prev_row = bars_d1[idx - 1]
                cur_h, cur_l, cur_c, cur_dt = row['high'], row['low'], row['close'], row['dt']
                ema = prev_row['ema200']
                
                if pos_1d is not None:
                    d, entry, sl, tp = pos_1d['dir'], pos_1d['entry'], pos_1d['sl'], pos_1d['tp']
                    fav = (cur_h - entry) if d == 'BUY' else (entry - cur_l)
                    if not pos_1d.get('be_locked') and fav >= (entry * 0.0030):
                        pos_1d['be_locked'] = True
                        pos_1d['sl'] = entry + 0.15 if d == 'BUY' else entry - 0.15
                        sl = pos_1d['sl']
                        
                    if (cur_h >= tp if d == 'BUY' else cur_l <= tp):
                        pnl = (abs(tp - entry) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                        cap += pnl
                        trades.append({'dt': cur_dt, 'dir': d, 'pnl': pnl, 'type': 'MACRO_TP'})
                        pos_1d = None
                        continue
                    if (cur_l <= sl if d == 'BUY' else cur_h >= sl):
                        pnl = ((sl - entry if d == 'BUY' else entry - sl) * pos_1d['lots'] * contract) - (pos_1d['lots'] * comm_per_lot)
                        cap += pnl
                        trades.append({'dt': cur_dt, 'dir': d, 'pnl': pnl, 'type': 'MACRO_SL'})
                        pos_1d = None
                        continue
                        
                sw_hd, sw_ld = prev_row['conf_sh'], prev_row['conf_sl']
                if pd.isna(sw_hd) or pd.isna(sw_ld): continue
                
                if pos_1d is None:
                    t_dir = 'BUY' if cur_c >= ema else ('SELL' if cur_c <= ema else None)
                    if t_dir == 'BUY' and prev_row['close'] < sw_hd and cur_h >= sw_hd:
                        e_p = sw_hd + (spread / 2.0)
                        sl = e_p * (1.0 - 0.0044)
                        tp = e_p * (1.0 + 0.0310)
                        sl_dist = abs(e_p - sl)
                        lots = max(0.01, min(max_lot_cap, round((cap * 0.030) / (sl_dist * contract), 2)))
                        pos_1d = {'dir': 'BUY', 'entry': e_p, 'sl': sl, 'tp': tp, 'lots': lots, 'be_locked': False}
                    elif t_dir == 'SELL' and prev_row['close'] > sw_ld and cur_l <= sw_ld:
                        e_p = sw_ld - (spread / 2.0)
                        sl = e_p * (1.0 + 0.0044)
                        tp = e_p * (1.0 - 0.0310)
                        sl_dist = abs(sl - e_p)
                        lots = max(0.01, min(max_lot_cap, round((cap * 0.030) / (sl_dist * contract), 2)))
                        pos_1d = {'dir': 'SELL', 'entry': e_p, 'sl': sl, 'tp': tp, 'lots': lots, 'be_locked': False}

        trades.sort(key=lambda x: x['dt'])
        n = len(trades)
        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gw = sum(wins) if wins else 0.0
        gl = abs(sum(losses)) if losses else 0.0
        pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
        wr = (len(wins) / n * 100.0) if n > 0 else 0.0
        years = 2.0
        cagr = ((max(1.0, cap) / 10000.0) ** (1.0 / years) - 1.0) * 100.0
        
        return {
            'n_trades': n, 'wins': len(wins), 'losses': len(losses), 'wr': wr, 'pf': pf,
            'final_bal': cap, 'net_pnl': cap - 10000.0, 'cagr': cagr, 'max_dd': max_dd,
            'trades': trades, 'eq_curve': eq_curve
        }

    print("=" * 100)
    print("RUNNING 2-YEAR COMPARISON MATRIX: 24-HOUR BENCHMARK VS 10 TO 4 WINDOWS")
    print("=" * 100)

    # 1. 24-Hour Benchmark (Dual-Horizon Ensemble)
    res_24h = simulate(time_filter=None, force_flat_at_16=False, include_macro=True)
    
    # 2. 10:00 to 16:00 IST (Mode A: Strict Flat Square-Off at 16:00 IST)
    res_ist_flat = simulate(time_filter=(10, 16), force_flat_at_16=True, tz_col='hour_ist')
    
    # 3. 10:00 to 16:00 IST (Mode B: Session Entry, Hold/Trail)
    res_ist_hold = simulate(time_filter=(10, 16), force_flat_at_16=False, tz_col='hour_ist')

    # 4. 10:00 to 16:00 UTC (Mode C: Strict Flat Square-Off at 16:00 UTC - London/NY Core)
    res_utc_flat = simulate(time_filter=(10, 16), force_flat_at_16=True, tz_col='hour_utc')
    
    # 5. 10:00 to 16:00 UTC (Mode D: Session Entry, Hold/Trail)
    res_utc_hold = simulate(time_filter=(10, 16), force_flat_at_16=False, tz_col='hour_utc')

    # 6. 10:00 to 16:00 Broker Time (Mode E: Strict Flat)
    res_brk_flat = simulate(time_filter=(10, 16), force_flat_at_16=True, tz_col='hour_broker')
    
    # 7. 10:00 to 16:00 Broker Time (Mode F: Session Entry, Hold/Trail)
    res_brk_hold = simulate(time_filter=(10, 16), force_flat_at_16=False, tz_col='hour_broker')

    configs = [
        ("24-Hour Benchmark (All Hours + Macro)", res_24h),
        ("10:00 - 16:00 IST (Strict Flat Close @ 16:00)", res_ist_flat),
        ("10:00 - 16:00 IST (Session Entry + Chandelier Trail)", res_ist_hold),
        ("10:00 - 16:00 UTC (Strict Flat Close @ 16:00)", res_utc_flat),
        ("10:00 - 16:00 UTC (Session Entry + Chandelier Trail)", res_utc_hold),
        ("10:00 - 16:00 Broker (Strict Flat Close @ 16:00)", res_brk_flat),
        ("10:00 - 16:00 Broker (Session Entry + Chandelier Trail)", res_brk_hold),
    ]

    for name, r in configs:
        print(f"\n{name}:")
        print(f"  Trades: {r['n_trades']} | Win Rate: {r['wr']:.1f}% | Profit Factor: {r['pf']:.2f}")
        print(f"  Net Profit: ${r['net_pnl']:,.2f} | Final Balance: ${r['final_bal']:,.2f} | CAGR: +{r['cagr']:.1f}%")
        print(f"  Peak Drawdown: {r['max_dd']:.2f}% (Mandate <= 30%: {'PASSED ✅' if r['max_dd'] <= 30.0 else 'FAILED ❌'})")

    mt5.shutdown()

if __name__ == '__main__':
    run_audit()

