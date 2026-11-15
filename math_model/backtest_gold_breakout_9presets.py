"""
========================================================================================
GOLD BREAKOUT TRADING ALGORITHM - 9-PRESET SYSTEMATIC BACKTEST ENGINE
========================================================================================
Implements the full specification:
- Multi-preset swing breakout algorithm on XAUUSD (Gold)
- Fractal mechanism (X candles left, Y candles right, strictly confirmed without lookahead)
- Pre-breakout entry (placed slightly inside the swing level)
- Trade Management (% of price):
  * Initial SL & TP (% of entry price)
  * Break-Even trigger & Trailing Stop
  * Adverse Position Salvage Logic (tight TP trailing when position moves adverse by 50% SL)
- Supports individual testing of Presets 1-9 AND Combined Multi-Preset Portfolio
- Real ECN friction: $0.28 Gold spread + $6.00/lot round-turn commission
========================================================================================
"""

import os
import sys
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def load_gold_data(timeframe_enum, n_bars=30000):
    mt5.symbol_select("XAUUSD", True)
    rates = mt5.copy_rates_from_pos("XAUUSD", timeframe_enum, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    ist = pytz.timezone('Asia/Kolkata')
    df['ist_time'] = df['time'].dt.tz_convert(ist)
    df['date'] = df['ist_time'].dt.date
    return df.reset_index(drop=True)

def identify_fractals(df, x_left, y_right):
    """
    Identifies swing highs and lows strictly without lookahead bias.
    A candle at index i is a Swing High if:
      - for k in 1..x_left: high[i-k] < high[i]
      - for k in 1..y_right: high[i+k] < high[i]
    CRITICAL: This fractal at index i is only confirmed at candle (i + y_right).
    Therefore, at candle j = i + y_right, the confirmed swing high is high[i].
    """
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    
    confirmed_swing_high = np.full(n, np.nan)
    confirmed_swing_low = np.full(n, np.nan)
    
    # We iterate and register confirmed swings at index i + y_right
    for i in range(x_left, n - y_right):
        h_center = highs[i]
        l_center = lows[i]
        
        # Check swing high
        is_sh = True
        for k in range(1, x_left + 1):
            if highs[i - k] >= h_center:
                is_sh = False
                break
        if is_sh:
            for k in range(1, y_right + 1):
                if highs[i + k] >= h_center:
                    is_sh = False
                    break
        if is_sh:
            confirmed_swing_high[i + y_right] = h_center
            
        # Check swing low
        is_sl = True
        for k in range(1, x_left + 1):
            if lows[i - k] <= l_center:
                is_sl = False
                break
        if is_sl:
            for k in range(1, y_right + 1):
                if lows[i + k] <= l_center:
                    is_sl = False
                    break
        if is_sl:
            confirmed_swing_low[i + y_right] = l_center
            
    df['conf_swing_high'] = pd.Series(confirmed_swing_high).ffill()
    df['conf_swing_low'] = pd.Series(confirmed_swing_low).ffill()
    return df

def simulate_preset(df, preset_cfg, initial_capital=10000.0, risk_pct=0.015):
    """
    Simulates a specific preset strategy with:
    - Pre-breakout pending entry
    - Initial % SL and TP
    - Break-even trigger and trailing stop
    - Adverse position salvage mechanism
    - Real spread and commissions
    """
    spread = 0.28 # $0.28/oz on Vantage
    comm_per_lot = 6.0 # $6.00/lot
    contract = 100.0 # 100 oz per standard lot
    
    pre_entry_offset = preset_cfg.get('pre_entry_offset', 0.0005) # 0.05% pre-entry
    sl_pct = preset_cfg['sl_pct'] # e.g. 0.03 for 3%
    tp_pct = preset_cfg['tp_pct'] # e.g. 0.0075 for 0.75%
    be_trigger_pct = preset_cfg.get('be_trigger_pct', 0.0045) # e.g. 0.45%
    be_buffer_pct = 0.0005 # 0.05% above entry
    trail_dist_pct = preset_cfg.get('trail_dist_pct', None)
    
    use_salvage = preset_cfg.get('salvage', True)
    salvage_trigger_pct = preset_cfg.get('salvage_trigger_pct', sl_pct * 0.5) # 50% of initial SL
    salvage_tp_dist_pct = preset_cfg.get('salvage_tp_dist_pct', 0.0015) # 0.15% tight TP
    
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
        
        # 1. Manage Active Trade
        if active_pos is not None:
            d = active_pos['type']
            entry = active_pos['entry']
            sl = active_pos['sl']
            tp = active_pos['tp']
            lots = active_pos['lots']
            
            # A. Check Adverse Position Salvage Trigger
            if use_salvage:
                if d == 'BUY':
                    adverse_dist = entry - cur_l
                    if adverse_dist >= (entry * salvage_trigger_pct):
                        active_pos['salvage_active'] = True
                else: # SELL
                    adverse_dist = cur_h - entry
                    if adverse_dist >= (entry * salvage_trigger_pct):
                        active_pos['salvage_active'] = True
                        
                # If salvage is active, trail TP tightly
                if active_pos.get('salvage_active', False):
                    if d == 'BUY':
                        tight_tp = cur_c * (1.0 + salvage_tp_dist_pct)
                        if tight_tp < tp:
                            tp = tight_tp
                            active_pos['tp'] = tp
                    else: # SELL
                        tight_tp = cur_c * (1.0 - salvage_tp_dist_pct)
                        if tight_tp > tp:
                            tp = tight_tp
                            active_pos['tp'] = tp
                            
            # B. Check Break-Even Trigger & Trailing Stop
            if d == 'BUY':
                favorable_move = cur_h - entry
                if favorable_move >= (entry * be_trigger_pct):
                    active_pos['be_active'] = True
                    be_level = entry * (1.0 + be_buffer_pct)
                    if sl < be_level:
                        sl = be_level
                        active_pos['sl'] = sl
                        
                # Trailing stop if active
                if active_pos.get('be_active', False) and trail_dist_pct is not None:
                    trail_level = cur_h * (1.0 - trail_dist_pct)
                    if trail_level > sl:
                        sl = trail_level
                        active_pos['sl'] = sl
                        
                # Check Exits on this candle
                hit_tp = (cur_h >= tp)
                hit_sl = (cur_l <= sl)
                
                if hit_tp and hit_sl:
                    # Ambiguous intra-candle: worst-case conservative assumption -> exit at open proximity
                    if cur_o >= tp:
                        hit_sl = False
                    else:
                        hit_tp = False
                        
                if hit_tp:
                    pnl = (tp - entry) * lots * contract - (lots * comm_per_lot)
                    capital += pnl
                    reason = 'SALVAGE_TP' if active_pos.get('salvage_active') else ('BE_TP' if active_pos.get('be_active') else 'TP')
                    trades.append({'pnl': pnl, 'reason': reason, 'entry_time': active_pos['time'], 'exit_time': row['ist_time'], 'type': d})
                    active_pos = None
                    continue
                elif hit_sl:
                    pnl = (sl - entry) * lots * contract - (lots * comm_per_lot)
                    capital += pnl
                    reason = 'BE_LOCK' if active_pos.get('be_active') else 'SL'
                    trades.append({'pnl': pnl, 'reason': reason, 'entry_time': active_pos['time'], 'exit_time': row['ist_time'], 'type': d})
                    active_pos = None
                    continue
                    
            else: # SELL
                favorable_move = entry - cur_l
                if favorable_move >= (entry * be_trigger_pct):
                    active_pos['be_active'] = True
                    be_level = entry * (1.0 - be_buffer_pct)
                    if sl > be_level:
                        sl = be_level
                        active_pos['sl'] = sl
                        
                # Trailing stop if active
                if active_pos.get('be_active', False) and trail_dist_pct is not None:
                    trail_level = cur_l * (1.0 + trail_dist_pct)
                    if trail_level < sl:
                        sl = trail_level
                        active_pos['sl'] = sl
                        
                # Check Exits
                hit_tp = (cur_l <= tp)
                hit_sl = (cur_h >= sl)
                
                if hit_tp and hit_sl:
                    if cur_o <= tp:
                        hit_sl = False
                    else:
                        hit_tp = False
                        
                if hit_tp:
                    pnl = (entry - tp) * lots * contract - (lots * comm_per_lot)
                    capital += pnl
                    reason = 'SALVAGE_TP' if active_pos.get('salvage_active') else ('BE_TP' if active_pos.get('be_active') else 'TP')
                    trades.append({'pnl': pnl, 'reason': reason, 'entry_time': active_pos['time'], 'exit_time': row['ist_time'], 'type': d})
                    active_pos = None
                    continue
                elif hit_sl:
                    pnl = (entry - sl) * lots * contract - (lots * comm_per_lot)
                    capital += pnl
                    reason = 'BE_LOCK' if active_pos.get('be_active') else 'SL'
                    trades.append({'pnl': pnl, 'reason': reason, 'entry_time': active_pos['time'], 'exit_time': row['ist_time'], 'type': d})
                    active_pos = None
                    continue
                    
        # Track Drawdown
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

        # 2. Check for Pre-Breakout Entry Signal if Flat
        if active_pos is None:
            sw_h = prev_row['conf_swing_high']
            sw_l = prev_row['conf_swing_low']
            
            if pd.isna(sw_h) or pd.isna(sw_l):
                continue
                
            # Pre-breakout buy level (placed slightly below swing high)
            buy_trigger = sw_h * (1.0 - pre_entry_offset)
            # Pre-breakout sell level (placed slightly above swing low)
            sell_trigger = sw_l * (1.0 + pre_entry_offset)
            
            # Check if this candle triggered the pre-breakout
            # Bullish pre-breakout
            if prev_row['close'] < buy_trigger and cur_h >= buy_trigger:
                entry = buy_trigger + (spread / 2.0)
                sl = entry * (1.0 - sl_pct)
                tp = entry * (1.0 + tp_pct)
                sl_dist = abs(entry - sl)
                risk_cash = capital * risk_pct
                lots = max(0.01, round(risk_cash / (sl_dist * contract), 2))
                active_pos = {
                    'type': 'BUY',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'lots': lots,
                    'be_active': False,
                    'salvage_active': False,
                    'time': row['ist_time']
                }
            # Bearish pre-breakout
            elif prev_row['close'] > sell_trigger and cur_l <= sell_trigger:
                entry = sell_trigger - (spread / 2.0)
                sl = entry * (1.0 + sl_pct)
                tp = entry * (1.0 - tp_pct)
                sl_dist = abs(entry - sl)
                risk_cash = capital * risk_pct
                lots = max(0.01, round(risk_cash / (sl_dist * contract), 2))
                active_pos = {
                    'type': 'SELL',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'lots': lots,
                    'be_active': False,
                    'salvage_active': False,
                    'time': row['ist_time']
                }

    # Calculate statistics
    n_trades = len(trades)
    if n_trades == 0:
        return {'trades': 0, 'cagr': 0.0, 'pf': 0.0, 'win_rate': 0.0, 'max_dd': 0.0, 'net_pnl': 0.0}
        
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else 99.0
    win_rate = (len(wins) / n_trades) * 100.0
    net_pnl = capital - initial_capital
    roi = (net_pnl / initial_capital) * 100.0
    
    n_days = len(df['date'].unique())
    years = n_days / 252.0
    cagr = ((capital / initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if capital > 0 else -100.0
    
    # Trade exit reason breakdown
    reasons = pd.Series([t['reason'] for t in trades]).value_counts().to_dict()
    
    return {
        'trades': n_trades,
        'trades_month': round((n_trades / n_days) * 21.0, 1) if n_days > 0 else 0,
        'cagr': round(cagr, 2),
        'roi': round(roi, 2),
        'net_pnl': round(net_pnl, 2),
        'final_bal': round(capital, 2),
        'max_dd': round(max_dd, 2),
        'pf': round(pf, 2),
        'win_rate': round(win_rate, 1),
        'reasons': reasons,
        'trade_list': trades
    }

def main():
    mt5.initialize()
    print("=" * 115)
    print("GOLD BREAKOUT TRADING ALGORITHM - QUANTITATIVE AUDIT & BENCHMARK (PRESETS 1 TO 9)")
    print("Real ECN Conditions: Gold Spread $0.28/oz | $6.00/lot Commission | Zero-Lookahead Fractals")
    print("=" * 115)
    
    # Definitions of all 9 Presets from specification
    presets = {
        'Preset 1': {
            'tf': mt5.TIMEFRAME_D1, 'tf_name': '1D', 'bars': 5000,
            'x_left': 25, 'y_right': 3,
            'sl_pct': 0.030, 'tp_pct': 0.0075, 'be_trigger_pct': 0.0045,
            'salvage': True, 'salvage_trigger_pct': 0.015, 'salvage_tp_dist_pct': 0.0015,
            'desc': 'Daily Major Peaks (25L/3R) | 3.0% SL, 0.75% TP, 0.45% BE (High Win Rate)'
        },
        'Preset 2': {
            'tf': mt5.TIMEFRAME_D1, 'tf_name': '1D', 'bars': 5000,
            'x_left': 15, 'y_right': 2,
            'sl_pct': 0.0044, 'tp_pct': 0.0310, 'be_trigger_pct': 0.0030,
            'salvage': False,
            'desc': 'Daily Trend Runner (15L/2R) | 0.44% SL, 3.10% TP (Large Runners)'
        },
        'Preset 3': {
            'tf': mt5.TIMEFRAME_D1, 'tf_name': '1D', 'bars': 5000,
            'x_left': 7, 'y_right': 2,
            'sl_pct': 0.015, 'tp_pct': 0.015, 'be_trigger_pct': 0.0050,
            'salvage': True, 'salvage_trigger_pct': 0.0075, 'salvage_tp_dist_pct': 0.0015,
            'desc': 'Daily Moderate Swings (7L/2R) | 1.5% SL, 1.5% TP'
        },
        'Preset 4': {
            'tf': mt5.TIMEFRAME_H4, 'tf_name': '4H', 'bars': 12000,
            'x_left': 10, 'y_right': 2,
            'sl_pct': 0.012, 'tp_pct': 0.022, 'be_trigger_pct': 0.0040,
            'salvage': True, 'salvage_trigger_pct': 0.006, 'salvage_tp_dist_pct': 0.0015,
            'desc': '4-Hour Intermediate Swings (10L/2R) | 1.2% SL, 2.2% TP'
        },
        'Preset 5': {
            'tf': mt5.TIMEFRAME_H1, 'tf_name': '1H', 'bars': 25000,
            'x_left': 12, 'y_right': 3,
            'sl_pct': 0.015, 'tp_pct': 0.025, 'be_trigger_pct': 0.0050,
            'salvage': True, 'salvage_trigger_pct': 0.0075, 'salvage_tp_dist_pct': 0.0015,
            'desc': '1-Hour Wide Breathing Room (12L/3R) | 1.5% SL, 2.5% TP'
        },
        'Preset 6': {
            'tf': mt5.TIMEFRAME_H1, 'tf_name': '1H', 'bars': 25000,
            'x_left': 15, 'y_right': 3,
            'sl_pct': 0.018, 'tp_pct': 0.030, 'be_trigger_pct': 0.0060,
            'salvage': True, 'salvage_trigger_pct': 0.009, 'salvage_tp_dist_pct': 0.0015,
            'desc': '1-Hour Wider Runner (15L/3R) | 1.8% SL, 3.0% TP'
        },
        'Preset 7': {
            'tf': mt5.TIMEFRAME_H1, 'tf_name': '1H', 'bars': 25000,
            'x_left': 8, 'y_right': 2,
            'sl_pct': 0.006, 'tp_pct': 0.010, 'be_trigger_pct': 0.0025,
            'salvage': True, 'salvage_trigger_pct': 0.003, 'salvage_tp_dist_pct': 0.0010,
            'desc': '1-Hour Tight Leash (8L/2R) | 0.6% SL, 1.0% TP'
        },
        'Preset 8': {
            'tf': mt5.TIMEFRAME_H1, 'tf_name': '1H', 'bars': 25000,
            'x_left': 10, 'y_right': 2,
            'sl_pct': 0.007, 'tp_pct': 0.012, 'be_trigger_pct': 0.0030,
            'salvage': True, 'salvage_trigger_pct': 0.0035, 'salvage_tp_dist_pct': 0.0010,
            'desc': '1-Hour Tight Leash Moderate (10L/2R) | 0.7% SL, 1.2% TP'
        },
        'Preset 9': {
            'tf': mt5.TIMEFRAME_H1, 'tf_name': '1H', 'bars': 25000,
            'x_left': 5, 'y_right': 5,
            'sl_pct': 0.020, 'tp_pct': 0.007, 'be_trigger_pct': 0.0025,
            'salvage': True, 'salvage_trigger_pct': 0.010, 'salvage_tp_dist_pct': 0.0015,
            'desc': '1-Hour Minor Mounds (5L/5R) | High Frequency (>1800 trades), 2.0% SL, 0.7% TP'
        }
    }
    
    # Load data for each unique timeframe
    data_by_tf = {}
    for p_name, p in presets.items():
        tf = p['tf']
        if tf not in data_by_tf:
            print(f"Loading {p['tf_name']} data for XAUUSD ({p['bars']} bars)...")
            data_by_tf[tf] = load_gold_data(tf, n_bars=p['bars'])
            
    print("\n" + "=" * 115)
    print("RUNNING BACKTESTS FOR EACH INDIVIDUAL PRESET (PRESETS 1 TO 9)")
    print("=" * 115)
    
    results = {}
    
    print(f"{'Preset':<10} {'TF':<5} {'Total Trades':<14} {'CAGR %':<12} {'Profit Factor':<15} {'Win Rate %':<12} {'Max DD %':<10} {'Net Profit':<12}")
    print("-" * 115)
    
    for p_name, p in presets.items():
        raw_df = data_by_tf[p['tf']].copy()
        df_frac = identify_fractals(raw_df, p['x_left'], p['y_right'])
        res = simulate_preset(df_frac, p, initial_capital=10000.0, risk_pct=0.015)
        results[p_name] = res
        
        cagr_str = f"+{res['cagr']:.2f}%" if res['cagr'] >= 0 else f"{res['cagr']:.2f}%"
        pnl_str = f"+${res['net_pnl']:.2f}" if res['net_pnl'] >= 0 else f"-${abs(res['net_pnl']):.2f}"
        
        print(f"{p_name:<10} {p['tf_name']:<5} {res['trades']:<14} {cagr_str:<12} {res['pf']:<15.2f} {res['win_rate']:<12.1f} {res['max_dd']:<10.2f} {pnl_str:<12}")
        
    print("=" * 115)
    
    print("\nDETAILED EXIT REASON ANALYSIS (SALVAGE RECOVERY EFFICIENCY):")
    print("-" * 75)
    for p_name, res in results.items():
        reasons_str = ", ".join([f"{k}: {v}" for k, v in res.get('reasons', {}).items()])
        print(f"• {p_name:<10}: {reasons_str}")
    print("=" * 75)

if __name__ == '__main__':
    main()

