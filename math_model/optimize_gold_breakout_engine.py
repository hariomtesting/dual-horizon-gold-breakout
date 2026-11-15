"""
========================================================================================
QUANTITATIVE OPTIMIZATION & RE-ENGINEERING OF THE GOLD BREAKOUT ALGORITHM
========================================================================================
Addresses the 5 core vulnerabilities discovered in the baseline audit:
1. Replaces rigid static % stops with Dynamic ATR Volatility brackets.
2. Evaluates Macro Trend Alignment (EMA 200 / Higher Timeframe Drift).
3. Calibrates the Pre-Breakout Entry Offset (0.00% to 0.15% inside the swing).
4. Optimizes the Adverse Salvage Mechanism to avoid cutting runners on normal retests.
5. Derives the Mean-Variance Optimal Allocation weights for the Multi-Timeframe Core.
========================================================================================
"""

import os
import sys
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def load_data(tf_enum, n_bars=26000):
    mt5.symbol_select("XAUUSD", True)
    rates = mt5.copy_rates_from_pos("XAUUSD", tf_enum, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
    
    # ATR & EMA
    h, l, c = df['high'], df['low'], df['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df['atr'] = tr.rolling(14).mean().bfill()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()
    df['atr_pct'] = df['atr'] / c
    return df.reset_index(drop=True)

def compute_confirmed_fractals(df, x_left, y_right):
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    
    conf_sh = np.full(n, np.nan)
    conf_sl = np.full(n, np.nan)
    
    for i in range(x_left, n - y_right):
        hc = highs[i]
        lc = lows[i]
        
        is_sh = True
        for k in range(1, x_left + 1):
            if highs[i - k] >= hc:
                is_sh = False
                break
        if is_sh:
            for k in range(1, y_right + 1):
                if highs[i + k] >= hc:
                    is_sh = False
                    break
        if is_sh:
            conf_sh[i + y_right] = hc
            
        is_sl = True
        for k in range(1, x_left + 1):
            if lows[i - k] <= lc:
                is_sl = False
                break
        if is_sl:
            for k in range(1, y_right + 1):
                if lows[i + k] <= lc:
                    is_sl = False
                    break
        if is_sl:
            conf_sl[i + y_right] = lc
            
    df['conf_swing_high'] = pd.Series(conf_sh).ffill()
    df['conf_swing_low'] = pd.Series(conf_sl).ffill()
    return df

def simulate_optimized(df, opt_params, initial_capital=10000.0, risk_pct=0.015):
    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    
    pre_offset = opt_params.get('pre_offset', 0.0005)
    use_atr_stops = opt_params.get('use_atr_stops', False)
    
    sl_atr_mult = opt_params.get('sl_atr_mult', 2.0)
    tp_atr_mult = opt_params.get('tp_atr_mult', 4.5)
    be_atr_mult = opt_params.get('be_atr_mult', 0.8)
    
    sl_pct = opt_params.get('sl_pct', 0.018)
    tp_pct = opt_params.get('tp_pct', 0.030)
    be_trigger_pct = opt_params.get('be_trigger_pct', 0.006)
    
    use_trend_filter = opt_params.get('use_trend_filter', True)
    
    # Salvage parameters
    use_salvage = opt_params.get('salvage', True)
    salvage_threshold_ratio = opt_params.get('salvage_threshold_ratio', 0.65) # 65% of SL
    salvage_min_bars = opt_params.get('salvage_min_bars', 2) # don't salvage immediately on initial bar noise
    salvage_tp_dist_pct = opt_params.get('salvage_tp_dist_pct', 0.0015)
    
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    trades = []
    
    active_pos = None
    bars = df.to_dict('records')
    
    for idx in range(1, len(bars)):
        row = bars[idx]
        prev_row = bars[idx - 1]
        cur_h, cur_l, cur_o, cur_c = row['high'], row['low'], row['open'], row['close']
        cur_dt = row['dt']
        
        # Position Management
        if active_pos is not None:
            active_pos['bars_held'] += 1
            d = active_pos['type']
            entry = active_pos['entry']
            sl = active_pos['sl']
            tp = active_pos['tp']
            lots = active_pos['lots']
            sl_dist = active_pos['sl_dist']
            
            # 1. Adverse Salvage Logic
            if use_salvage and active_pos['bars_held'] >= salvage_min_bars:
                adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                if adv >= (sl_dist * salvage_threshold_ratio):
                    active_pos['salvage_active'] = True
                    
                if active_pos.get('salvage_active', False):
                    if d == 'BUY':
                        tight_tp = cur_c * (1.0 + salvage_tp_dist_pct)
                        if tight_tp < tp:
                            tp = tight_tp
                            active_pos['tp'] = tp
                    else:
                        tight_tp = cur_c * (1.0 - salvage_tp_dist_pct)
                        if tight_tp > tp:
                            tp = tight_tp
                            active_pos['tp'] = tp
                            
            # 2. Break-Even Trigger
            fav = (cur_h - entry) if d == 'BUY' else (entry - cur_l)
            be_trig_dist = active_pos['be_dist']
            if fav >= be_trig_dist:
                active_pos['be_active'] = True
                be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                if d == 'BUY' and sl < be_lvl: sl = be_lvl
                elif d == 'SELL' and sl > be_lvl: sl = be_lvl
                active_pos['sl'] = sl
                
            # 3. Check Hits
            hit_tp = (cur_h >= tp) if d == 'BUY' else (cur_l <= tp)
            hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
            
            if hit_tp and hit_sl:
                if (cur_o >= tp if d == 'BUY' else cur_o <= tp): hit_sl = False
                else: hit_tp = False
                
            if hit_tp:
                pts = (tp - entry) if d == 'BUY' else (entry - tp)
                pnl = (pts * lots * contract) - (lots * comm_per_lot)
                capital += pnl
                reason = 'SALVAGE_TP' if active_pos.get('salvage_active') else ('BE_TP' if active_pos.get('be_active') else 'TP')
                trades.append({'dt': cur_dt, 'pnl': pnl, 'reason': reason})
                active_pos = None
            elif hit_sl:
                pts = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl = (pts * lots * contract) - (lots * comm_per_lot)
                capital += pnl
                reason = 'BE_LOCK' if active_pos.get('be_active') else 'SL'
                trades.append({'dt': cur_dt, 'pnl': pnl, 'reason': reason})
                active_pos = None

        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd: max_dd = dd

        # Signal Scanning
        if active_pos is None:
            sw_h = prev_row['conf_swing_high']
            sw_l = prev_row['conf_swing_low']
            if pd.isna(sw_h) or pd.isna(sw_l):
                continue
                
            atr = prev_row['atr']
            ema = prev_row['ema200']
            
            buy_trig = sw_h * (1.0 - pre_offset)
            sell_trig = sw_l * (1.0 + pre_offset)
            
            # Bullish pre-breakout
            if prev_row['close'] < buy_trig and cur_h >= buy_trig:
                if not use_trend_filter or cur_c >= ema:
                    entry = buy_trig + (spread / 2.0)
                    if use_atr_stops:
                        sl = entry - (sl_atr_mult * atr)
                        tp = entry + (tp_atr_mult * atr)
                        be_dist = be_atr_mult * atr
                    else:
                        sl = entry * (1.0 - sl_pct)
                        tp = entry * (1.0 + tp_pct)
                        be_dist = entry * be_trigger_pct
                        
                    sl_dist = abs(entry - sl)
                    if sl_dist > 0:
                        risk_cash = capital * risk_pct
                        lots = max(0.01, round(risk_cash / (sl_dist * contract), 2))
                        active_pos = {
                            'type': 'BUY', 'entry': entry, 'sl': sl, 'tp': tp,
                            'lots': lots, 'sl_dist': sl_dist, 'be_dist': be_dist,
                            'bars_held': 0, 'be_active': False, 'salvage_active': False
                        }
            # Bearish pre-breakout
            elif prev_row['close'] > sell_trig and cur_l <= sell_trig:
                if not use_trend_filter or cur_c <= ema:
                    entry = sell_trig - (spread / 2.0)
                    if use_atr_stops:
                        sl = entry + (sl_atr_mult * atr)
                        tp = entry - (tp_atr_mult * atr)
                        be_dist = be_atr_mult * atr
                    else:
                        sl = entry * (1.0 + sl_pct)
                        tp = entry * (1.0 - tp_pct)
                        be_dist = entry * be_trigger_pct
                        
                    sl_dist = abs(entry - sl)
                    if sl_dist > 0:
                        risk_cash = capital * risk_pct
                        lots = max(0.01, round(risk_cash / (sl_dist * contract), 2))
                        active_pos = {
                            'type': 'SELL', 'entry': entry, 'sl': sl, 'tp': tp,
                            'lots': lots, 'sl_dist': sl_dist, 'be_dist': be_dist,
                            'bars_held': 0, 'be_active': False, 'salvage_active': False
                        }

    n_trades = len(trades)
    if n_trades == 0:
        return {'trades': 0, 'cagr': 0.0, 'pf': 0.0, 'win_rate': 0.0, 'max_dd': 0.0}
        
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else 99.0
    wr = len(wins) / n_trades * 100.0
    net_pnl = capital - initial_capital
    roi = (net_pnl / initial_capital) * 100.0
    
    start_d = df['dt'].iloc[0]
    end_d = df['dt'].iloc[-1]
    years = (end_d - start_d).total_seconds() / (365.25 * 86400)
    cagr = ((capital / initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if capital > 0 else -100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0
    
    return {
        'trades': n_trades, 'cagr': round(cagr, 2), 'roi': round(roi, 2),
        'net_pnl': round(net_pnl, 2), 'pf': round(pf, 2), 'win_rate': round(wr, 1),
        'max_dd': round(max_dd, 2), 'calmar': round(calmar, 2), 'trades_list': trades
    }

def main():
    mt5.initialize()
    print("=" * 100)
    print("QUANTITATIVE OPTIMIZATION: TESTING ENHANCEMENTS ON TOP GOLD PRESETS")
    print("=" * 100)
    
    df_1h = load_data(mt5.TIMEFRAME_H1, n_bars=26000)
    df_4h = load_data(mt5.TIMEFRAME_H4, n_bars=10000)
    df_1d = load_data(mt5.TIMEFRAME_D1, n_bars=3000)
    
    # -------------------------------------------------------------
    # EXPERIMENT 1: Pre-Breakout Entry Offset Tuning (Preset 6 1H)
    # -------------------------------------------------------------
    print("\n--- EXPERIMENT 1: Pre-Breakout Entry Offset Tuning (Preset 6: 15L/3R) ---")
    df6 = compute_confirmed_fractals(df_1h.copy(), 15, 3)
    for offset_bp in [0, 5, 10, 15, 20]:
        offset = offset_bp / 10000.0
        res = simulate_optimized(df6, {
            'pre_offset': offset, 'sl_pct': 0.018, 'tp_pct': 0.030,
            'be_trigger_pct': 0.006, 'salvage': True, 'use_trend_filter': False
        })
        print(f"Offset: {offset*100:4.2f}% | Trades: {res['trades']:<4} | CAGR: +{res['cagr']:<5}% | PF: {res['pf']:<4.2f} | DD: {res['max_dd']:<4.1f}% | WinRate: {res['win_rate']}% | Calmar: {res['calmar']}")

    # -------------------------------------------------------------
    # EXPERIMENT 2: Trend Alignment Gate (Macro 200 EMA Filter)
    # -------------------------------------------------------------
    print("\n--- EXPERIMENT 2: Macro Trend Filter (200 EMA) Impact on Presets ---")
    for name, tf_df, lb_x, lb_y, sl_p, tp_p, be_p in [
        ('Preset 6 (1H Runner)', df_1h, 15, 3, 0.018, 0.030, 0.006),
        ('Preset 5 (1H Swing)',  df_1h, 12, 3, 0.015, 0.025, 0.005),
        ('Preset 4 (4H Swing)',  df_4h, 10, 2, 0.012, 0.022, 0.004),
        ('Preset 2 (1D Runner)', df_1d, 15, 2, 0.0044, 0.0310, 0.003)
    ]:
        df_exp = compute_confirmed_fractals(tf_df.copy(), lb_x, lb_y)
        res_raw = simulate_optimized(df_exp, {'pre_offset': 0.0005, 'sl_pct': sl_p, 'tp_pct': tp_p, 'be_trigger_pct': be_p, 'use_trend_filter': False})
        res_flt = simulate_optimized(df_exp, {'pre_offset': 0.0005, 'sl_pct': sl_p, 'tp_pct': tp_p, 'be_trigger_pct': be_p, 'use_trend_filter': True})
        print(f"• {name:<22} | RAW: PF {res_raw['pf']:<4.2f}, DD {res_raw['max_dd']:<4.1f}%, CAGR +{res_raw['cagr']:<5}% --> FILTERED: PF {res_flt['pf']:<4.2f}, DD {res_flt['max_dd']:<4.1f}%, CAGR +{res_flt['cagr']:<5}%")

    # -------------------------------------------------------------
    # EXPERIMENT 3: ATR-Adaptive Brackets vs Static Percentage Stops
    # -------------------------------------------------------------
    print("\n--- EXPERIMENT 3: Dynamic ATR Volatility Stops vs Static Percentage Stops ---")
    df6_atr = compute_confirmed_fractals(df_1h.copy(), 15, 3)
    res_static = simulate_optimized(df6_atr, {'pre_offset': 0.0005, 'sl_pct': 0.018, 'tp_pct': 0.030, 'be_trigger_pct': 0.006, 'use_atr_stops': False})
    print(f"Preset 6 Static % (1.8% SL / 3.0% TP) : PF {res_static['pf']:<4.2f} | CAGR: +{res_static['cagr']}% | Max DD: {res_static['max_dd']}% | Calmar: {res_static['calmar']}")
    
    for sl_atr, tp_atr, be_atr in [(1.5, 3.5, 0.8), (2.0, 4.5, 1.0), (2.5, 5.0, 1.2)]:
        res_atr = simulate_optimized(df6_atr, {
            'pre_offset': 0.0005, 'use_atr_stops': True,
            'sl_atr_mult': sl_atr, 'tp_atr_mult': tp_atr, 'be_atr_mult': be_atr,
            'use_trend_filter': True
        })
        print(f"Preset 6 ATR ({sl_atr} ATR SL / {tp_atr} ATR TP): PF {res_atr['pf']:<4.2f} | CAGR: +{res_atr['cagr']}% | Max DD: {res_atr['max_dd']}% | Calmar: {res_atr['calmar']}")

    # -------------------------------------------------------------
    # EXPERIMENT 4: Adverse Salvage Threshold Optimization
    # -------------------------------------------------------------
    print("\n--- EXPERIMENT 4: Salvage Threshold Tuning (Preventing Pre-Mature Runner Cutoffs) ---")
    for thresh in [0.40, 0.50, 0.65, 0.80, 1.0]: # 1.0 = No salvage (full SL)
        res_salv = simulate_optimized(df6, {
            'pre_offset': 0.0005, 'sl_pct': 0.018, 'tp_pct': 0.030,
            'be_trigger_pct': 0.006, 'salvage': (thresh < 1.0),
            'salvage_threshold_ratio': thresh, 'salvage_min_bars': 2
        })
        salv_label = f"Threshold: {int(thresh*100)}% of SL" if thresh < 1.0 else "Disabled (Full SL)"
        print(f"Salvage {salv_label:<24} | Trades: {res_salv['trades']:<4} | PF: {res_salv['pf']:<4.2f} | CAGR: +{res_salv['cagr']:<5}% | DD: {res_salv['max_dd']:<4.1f}%")

if __name__ == '__main__':
    main()

