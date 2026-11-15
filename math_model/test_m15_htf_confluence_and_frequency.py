"""
M15 HIGHER-TIMEFRAME (HTF) CONFLUENCE & 5-HOUR TRADE FREQUENCY AUDIT
===================================================================
Research Focus:
1. Compare Pure M5 Scalper vs M5 + M15 HTF Trend Confluence Scalper.
2. Measure impact on Max Consecutive Loss Streak (targeting <= 4 losses).
3. Compute exact trade frequency distribution in the 5-hour daytime window (10:00 - 15:55 IST):
   - Average trades per 5-hour session.
   - % of days with 0, 1, 2, 3 trades.
   - Average trade duration (in minutes).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_multi_tf_data(symbol, n_m5_bars=75000):
    mt5.symbol_select(symbol, True)
    
    # 1. Fetch M5 bars
    rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_m5_bars)
    if rates_m5 is None or len(rates_m5) == 0:
        return None
    df_m5 = pd.DataFrame(rates_m5)
    df_m5['broker_dt'] = pd.to_datetime(df_m5['time'], unit='s')
    df_m5['ist_dt'] = df_m5['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
    df_m5['date'] = df_m5['ist_dt'].dt.date
    df_m5['year'] = df_m5['ist_dt'].dt.year
    df_m5['ist_hour'] = df_m5['ist_dt'].dt.hour
    df_m5['ist_minute'] = df_m5['ist_dt'].dt.minute
    
    # M5 Indicators
    df_m5['ema200'] = df_m5['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df_m5['high'] - df_m5['low'], np.maximum((df_m5['high'] - df_m5['close'].shift()).abs(), (df_m5['low'] - df_m5['close'].shift()).abs()))
    df_m5['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    df_m5['swing_high'] = df_m5['high'].shift(1).rolling(12).max()
    df_m5['swing_low'] = df_m5['low'].shift(1).rolling(12).min()
    
    # 2. Fetch M15 bars for Higher-Timeframe Confluence
    rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, int(n_m5_bars / 3) + 200)
    df_m15 = pd.DataFrame(rates_m15)
    df_m15['broker_dt'] = pd.to_datetime(df_m15['time'], unit='s')
    df_m15['ist_dt'] = df_m15['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
    df_m15['m15_ema50'] = df_m15['close'].ewm(span=50, adjust=False).mean()
    df_m15['m15_ema200'] = df_m15['close'].ewm(span=200, adjust=False).mean()
    
    # Merge M15 trend onto M5 bars using asof merge (prevent lookahead bias)
    df_m5 = pd.merge_asof(
        df_m5.sort_values('broker_dt'),
        df_m15[['broker_dt', 'm15_ema50', 'm15_ema200']].sort_values('broker_dt'),
        on='broker_dt',
        direction='backward'
    )
    return df_m5

def backtest_scalper(df, symbol="XAUUSD", use_m15_htf=False, skip_11am_chop=False, risk_pct=0.010, initial_cap=10000.0):
    contract = 100.0 if "XAU" in symbol else 1.0
    spread = 0.30 if "XAU" in symbol else 17.05
    comm_lot = 6.00
    
    dates = sorted(df['date'].unique())
    balance = initial_cap
    peak_balance = initial_cap
    daily_records = []
    all_trades = []
    daily_trade_counts = []
    trade_durations_mins = []
    
    for cur_date in dates:
        bars = df[df['date'] == cur_date].to_dict('records')
        if len(bars) < 30:
            continue
            
        active_pos = None
        pending_limit = None
        trades_today = 0
        day_pnl = 0.0
        
        for row in bars:
            h = row['ist_hour']
            m = row['ist_minute']
            
            if h < 10:
                continue
                
            # Hard 15:55 IST Cutoff
            if (h == 15 and m >= 55) or h > 15:
                if pending_limit:
                    pending_limit = None
                if active_pos:
                    exit_p = row['close'] - (spread/2.0) if active_pos['dir'] == 'BUY' else row['close'] + (spread/2.0)
                    pts = (exit_p - active_pos['entry']) if active_pos['dir'] == 'BUY' else (active_pos['entry'] - exit_p)
                    lots = active_pos['lots_t1'] + active_pos['lots_t2']
                    pnl = (pts * lots * contract) - (lots * comm_lot)
                    day_pnl += pnl
                    dur = ((h - active_pos['entry_h']) * 60) + (m - active_pos['entry_m'])
                    trade_durations_mins.append(max(5, dur))
                    all_trades.append({'date': cur_date, 'hour': h, 'pnl': pnl, 'type': '15:55_CUTOFF', 'sym': symbol})
                    active_pos = None
                break
                
            # Manage Active Position
            if active_pos is not None:
                d = active_pos['dir']
                entry = active_pos['entry']
                sl = active_pos['sl']
                tp1 = active_pos['tp1']
                tp2 = active_pos['tp2']
                
                # Tranche 1 check
                if active_pos['lots_t1'] > 0:
                    hit_tp1 = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit_tp1:
                        pnl1 = (abs(tp1 - entry) * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_lot)
                        day_pnl += pnl1
                        active_pos['lots_t1'] = 0.0
                        active_pos['sl'] = entry + (0.05 * abs(entry - sl)) if d == 'BUY' else entry - (0.05 * abs(entry - sl))
                        sl = active_pos['sl']
                        
                # Tranche 2 check
                if active_pos['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active_pos['lots_t2'] * contract) - (active_pos['lots_t2'] * comm_lot)
                        day_pnl += pnl2
                        dur = ((h - active_pos['entry_h']) * 60) + (m - active_pos['entry_m'])
                        trade_durations_mins.append(max(5, dur))
                        all_trades.append({'date': cur_date, 'hour': h, 'pnl': pnl2, 'type': 'TP2_RUNNER', 'sym': symbol})
                        active_pos = None
                        continue
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active_pos['lots_t2'] * contract) - (active_pos['lots_t2'] * comm_lot)
                        day_pnl += pnl2
                        dur = ((h - active_pos['entry_h']) * 60) + (m - active_pos['entry_m'])
                        trade_durations_mins.append(max(5, dur))
                        all_trades.append({'date': cur_date, 'hour': h, 'pnl': pnl2, 'type': 'SL_OR_BE', 'sym': symbol})
                        active_pos = None
                        continue
                continue
                
            # Check Pending Retest Limit
            if pending_limit is not None:
                lp = pending_limit['lp']
                filled = (row['low'] <= lp) if pending_limit['dir'] == 'BUY' else (row['high'] >= lp)
                if filled:
                    active_pos = {
                        'dir': pending_limit['dir'],
                        'entry': lp,
                        'sl': pending_limit['sl'],
                        'tp1': pending_limit['tp1'],
                        'tp2': pending_limit['tp2'],
                        'lots_t1': pending_limit['lots_t1'],
                        'lots_t2': pending_limit['lots_t2'],
                        'entry_h': h,
                        'entry_m': m
                    }
                    pending_limit = None
                    continue
                else:
                    pending_limit['bars_alive'] += 1
                    if pending_limit['bars_alive'] >= 6:
                        pending_limit = None
                        
            # Signal Detection
            if active_pos is None and pending_limit is None and trades_today < 3:
                # 11:00 - 11:59 Transition Filter
                if skip_11am_chop and h == 11:
                    continue
                    
                sw_h = row['swing_high']
                sw_l = row['swing_low']
                atr = row['atr']
                if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0:
                    continue
                    
                sweep_dist = 0.20 * atr
                is_bull_sweep = (row['low'] < (sw_l - sweep_dist)) and (row['close'] > sw_l)
                is_bear_sweep = (row['high'] > (sw_h + sweep_dist)) and (row['close'] < sw_h)
                
                # M15 Higher-Timeframe Confluence Filter
                if use_m15_htf:
                    m15_bull = row['m15_ema50'] >= row['m15_ema200'] and row['close'] >= row['m15_ema50']
                    m15_bear = row['m15_ema50'] <= row['m15_ema200'] and row['close'] <= row['m15_ema50']
                else:
                    m15_bull = True
                    m15_bear = True
                    
                risk_cash = balance * risk_pct
                
                if is_bull_sweep and row['close'] >= row['ema200'] and m15_bull:
                    lp = (row['close'] + row['low']) / 2.0
                    sl = row['low'] - (0.20 * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                        lots_t1 = round(total_lots / 2.0, 2)
                        lots_t2 = total_lots - lots_t1
                        pending_limit = {
                            'dir': 'BUY', 'lp': lp, 'sl': sl,
                            'tp1': lp + (1.4 * sl_dist),
                            'tp2': lp + (2.5 * sl_dist),
                            'lots_t1': lots_t1, 'lots_t2': lots_t2,
                            'bars_alive': 0
                        }
                        trades_today += 1
                elif is_bear_sweep and row['close'] <= row['ema200'] and m15_bear:
                    lp = (row['close'] + row['high']) / 2.0
                    sl = row['high'] + (0.20 * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                        lots_t1 = round(total_lots / 2.0, 2)
                        lots_t2 = total_lots - lots_t1
                        pending_limit = {
                            'dir': 'SELL', 'lp': lp, 'sl': sl,
                            'tp1': lp - (1.4 * sl_dist),
                            'tp2': lp - (2.5 * sl_dist),
                            'lots_t1': lots_t1, 'lots_t2': lots_t2,
                            'bars_alive': 0
                        }
                        trades_today += 1
                        
        daily_trade_counts.append(trades_today)
        if day_pnl < -(balance * 0.03):
            day_pnl = -(balance * 0.03)
            
        balance += day_pnl
        balance = max(100.0, balance)
        if balance > peak_balance:
            peak_balance = balance
            
        daily_records.append({
            'date': cur_date, 'balance': balance, 'daily_pnl': day_pnl,
            'dd_pct': ((peak_balance - balance) / peak_balance) * 100.0
        })
        
    df_rec = pd.DataFrame(daily_records)
    n_days = len(daily_records)
    years = n_days / 252.0
    cagr = ((balance / initial_cap) ** (1.0 / max(0.1, years)) - 1.0) * 100.0
    max_dd = df_rec['dd_pct'].max()
    
    win_trades = [t for t in all_trades if t['pnl'] > 0]
    loss_trades = [t for t in all_trades if t['pnl'] < 0]
    win_rate = (len(win_trades) / len(all_trades) * 100.0) if all_trades else 0.0
    gross_p = sum(t['pnl'] for t in win_trades)
    gross_l = abs(sum(t['pnl'] for t in loss_trades))
    pf = (gross_p / gross_l) if gross_l > 0 else 999.0
    
    # Consecutive losses
    streak = 0
    max_loss_streak = 0
    for t in all_trades:
        if t['pnl'] < 0:
            streak += 1
            if streak > max_loss_streak:
                max_loss_streak = streak
        else:
            streak = 0
            
    # Frequency distribution
    dt_series = pd.Series(daily_trade_counts)
    total_days = len(dt_series)
    pct_0 = (dt_series == 0).sum() / total_days * 100.0
    pct_1 = (dt_series == 1).sum() / total_days * 100.0
    pct_2 = (dt_series == 2).sum() / total_days * 100.0
    pct_3 = (dt_series >= 3).sum() / total_days * 100.0
    avg_trades_day = dt_series.mean()
    avg_duration = np.mean(trade_durations_mins) if trade_durations_mins else 0.0
    
    return {
        'final_balance': balance, 'cagr': cagr, 'max_dd': max_dd,
        'pf': pf, 'win_rate': win_rate, 'total_trades': len(all_trades),
        'max_loss_streak': max_loss_streak,
        'avg_trades_day': avg_trades_day,
        'pct_0': pct_0, 'pct_1': pct_1, 'pct_2': pct_2, 'pct_3': pct_3,
        'avg_duration_mins': avg_duration
    }

def main():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    print("Loading multi-timeframe bars (M5 + M15) for Gold...")
    df_gold = load_multi_tf_data("XAUUSD", 75000)
    mt5.shutdown()
    
    print("\n" + "=" * 110)
    print("COMPARATIVE RESEARCH: PURE M5 SCALPER vs M5 + M15 HTF CONFLUENCE SCALPER")
    print("Strictly Daytime (10:00 AM - 15:55 IST) | Real Vantage Spreads & Fees")
    print("=" * 110)
    
    # 1. Baseline Pure M5
    res_base = backtest_scalper(df_gold, use_m15_htf=False, skip_11am_chop=False)
    # 2. M15 HTF Confluence
    res_htf = backtest_scalper(df_gold, use_m15_htf=True, skip_11am_chop=False)
    # 3. M15 HTF Confluence + Skip 11:00 AM Chop
    res_htf_filtered = backtest_scalper(df_gold, use_m15_htf=True, skip_11am_chop=True)
    
    print(f"\n{'CONFIGURATION':<38} | {'CAGR ROI':<10} | {'MAX DD':<8} | {'SHARPE/PF':<12} | {'WIN RATE':<10} | {'MAX LOSS STREAK'}")
    print("-" * 110)
    print(f"{'1. Baseline (Pure M5 Scalper)':<38} | {res_base['cagr']:+8.2f}% | {res_base['max_dd']:6.2f}% | PF: {res_base['pf']:<6.2f}  | {res_base['win_rate']:5.1f}%    | {res_base['max_loss_streak']} consecutive losses")
    print(f"{'2. M15 HTF Trend Confluence':<38} | {res_htf['cagr']:+8.2f}% | {res_htf['max_dd']:6.2f}% | PF: {res_htf['pf']:<6.2f}  | {res_htf['win_rate']:5.1f}%    | {res_htf['max_loss_streak']} consecutive losses")
    print(f"{'3. M15 HTF + Skip 11 AM Transition':<38} | {res_htf_filtered['cagr']:+8.2f}% | {res_htf_filtered['max_dd']:6.2f}% | PF: {res_htf_filtered['pf']:<6.2f}  | {res_htf_filtered['win_rate']:5.1f}%    | {res_htf_filtered['max_loss_streak']} consecutive losses")
    print("=" * 110)
    
    print("\n" + "=" * 110)
    print("EXACT TRADE FREQUENCY AUDIT IN THE 5-HOUR DAYTIME WINDOW (10:00 AM - 15:55 IST):")
    print("=" * 110)
    for name, r in [("Baseline Pure M5", res_base), ("M15 HTF Confluence", res_htf), ("M15 HTF + Clean Windows", res_htf_filtered)]:
        print(f"\n📊 Strategy Variant: {name}")
        print(f"  • Total Trades Taken     : {r['total_trades']} trades")
        print(f"  • Average Trades / Day   : {r['avg_trades_day']:.2f} trades per 5-hour session")
        print(f"  • Average Trade Duration : {r['avg_duration_mins']:.1f} minutes (~{r['avg_duration_mins']/60:.1f} hours)")
        print(f"  • Daily Trade Breakdown  :")
        print(f"      - Days with 0 Trades : {r['pct_0']:5.1f}% (Patience / capital preserved)")
        print(f"      - Days with 1 Trade  : {r['pct_1']:5.1f}%")
        print(f"      - Days with 2 Trades : {r['pct_2']:5.1f}%")
        print(f"      - Days with 3 Trades : {r['pct_3']:5.1f}% (Max daily cap reached)")
    print("=" * 110 + "\n")

if __name__ == "__main__":
    main()

