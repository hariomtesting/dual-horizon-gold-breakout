"""
========================================================================================
AUDIT LAST 2 DAYS PERFORMANCE (NOV 12 - NOV 14, 2026)
========================================================================================
1. Evaluates Dual-Horizon Testing Mode Algo on XAUUSD across the last 48 hours of market action.
2. Reconciles live broker MT5 deals executed over the last 2 days on Vantage Account 26380100.
========================================================================================
"""

import sys
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def audit_2days():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    utc = pytz.timezone('UTC')
    acc = mt5.account_info()
    print("=" * 90)
    print("AUDIT: LAST 2 DAYS PERFORMANCE RECONCILIATION")
    print(f"Current Broker Time: {datetime.datetime.now(utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Account: {acc.login} | Server: {acc.server} | Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
    print("=" * 90)

    # -------------------------------------------------------------
    # 1. LIVE BROKER ACCOUNT DEALS IN LAST 2 DAYS
    # -------------------------------------------------------------
    t_start = datetime.datetime.now(utc) - datetime.timedelta(days=2)
    deals = mt5.history_deals_get(t_start, datetime.datetime.now(utc))
    print("\n--- 1. LIVE BROKER ACCOUNT DEALS EXECUTED (LAST 48 HOURS) ---")
    if deals and len(deals) > 0:
        df_deals = pd.DataFrame([d._asdict() for d in deals])
        df_deals['dt'] = pd.to_datetime(df_deals['time'], unit='s', utc=True)
        # Filter deal entries/exits
        df_deals = df_deals[df_deals['entry'].isin([1, 2])] # 1: out, 2: inout (exits)
        closed_deals = df_deals[df_deals['profit'] != 0.0]
        total_pnl = closed_deals['profit'].sum() + closed_deals['commission'].sum() + closed_deals['swap'].sum()
        wins = closed_deals[closed_deals['profit'] > 0]
        losses = closed_deals[closed_deals['profit'] < 0]
        wr = (len(wins) / len(closed_deals) * 100.0) if len(closed_deals) > 0 else 0.0
        
        print(f"Total Closed Deals  : {len(closed_deals)}")
        print(f"Total Net PnL       : ${total_pnl:+,.2f}")
        print(f"Win Rate            : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
        print("\nRecent 10 Closed Deals:")
        for idx, row in closed_deals.tail(10).iterrows():
            d_str = row['dt'].strftime('%Y-%m-%d %H:%M')
            print(f"  • {d_str} | Ticket: {row['ticket']} | Sym: {row['symbol']:<8} | Lots: {row['volume']:<5.2f} | PnL: ${row['profit']:+8.2f} | Comm: ${row['commission']:.2f} | Comment: {row['comment']}")
    else:
        print("No live closed deals in the last 48 hours.")
        
    # -------------------------------------------------------------
    # 2. DUAL-HORIZON TESTING MODE ALGO (LAST 48 HOURS)
    # -------------------------------------------------------------
    print("\n" + "=" * 90)
    print("--- 2. DUAL-HORIZON TESTING ALGO (XAUUSD SIMULATION OVER LAST 2 DAYS) ---")
    print("=" * 90)
    
    r_1h = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 500)
    df_1h = pd.DataFrame(r_1h)
    df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
    
    # Technicals
    h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
    tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
    df_1h['atr'] = tr1.rolling(14).mean().bfill()
    df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()
    
    # 15L / 3R fractals
    highs = df_1h['high'].values
    lows = df_1h['low'].values
    n = len(df_1h)
    sh_arr = np.full(n, np.nan)
    sl_arr = np.full(n, np.nan)
    for i in range(15, n - 3):
        hc, lc = highs[i], lows[i]
        if all(highs[i - k] < hc for k in range(1, 16)) and all(highs[i + k] < hc for k in range(1, 4)):
            sh_arr[i + 3] = hc
        if all(lows[i - k] > lc for k in range(1, 16)) and all(lows[i + k] > lc for k in range(1, 4)):
            sl_arr[i + 3] = lc
    df_1h['conf_sh'] = pd.Series(sh_arr).ffill()
    df_1h['conf_sl'] = pd.Series(sl_arr).ffill()
    
    # Filter 2-day period: Nov 12 00:00 UTC to Friday market close Nov 13
    start_2d = pd.to_datetime("2026-11-12 00:00:00", utc=True)
    df_2d = df_1h[df_1h['dt'] >= start_2d].reset_index(drop=True)
    
    print(f"2-Day Evaluated Bars: {len(df_2d)} 1H bars")
    print(f"2-Day Price Range   : Low ${df_2d['low'].min():.2f} to High ${df_2d['high'].max():.2f}")
    
    capital = 10000.0
    initial_cap = capital
    spread = 0.28
    comm_per_lot = 6.0
    contract = 100.0
    
    trades = []
    active_pos = None
    bars = df_2d.to_dict('records')
    peak = capital
    max_dd = 0.0
    
    for idx in range(1, len(bars)):
        row = bars[idx]
        prev_row = bars[idx - 1]
        cur_h, cur_l, cur_c = row['high'], row['low'], row['close']
        cur_dt = row['dt']
        atr = prev_row['atr']
        ema = prev_row['ema200']
        
        if active_pos is not None:
            active_pos['bars'] += 1
            d = active_pos['dir']
            entry = active_pos['entry']
            sl = active_pos['sl']
            tp1 = active_pos['tp1']
            sl_dist = active_pos['sl_dist']
            
            if d == 'BUY': active_pos['extreme_price'] = max(active_pos['extreme_price'], cur_h)
            else: active_pos['extreme_price'] = min(active_pos['extreme_price'], cur_l)
            
            # Salvage
            if not active_pos['t1_hit'] and active_pos['bars'] >= 2:
                adv = (entry - cur_l) if d == 'BUY' else (cur_h - entry)
                if adv >= (sl_dist * 0.45):
                    active_pos['salvage_active'] = True
                if active_pos.get('salvage_active', False):
                    tight_tp = cur_c * 1.0015 if d == 'BUY' else cur_c * 0.9985
                    if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                        tp1 = tight_tp
                        active_pos['tp1'] = tp1
                        
            # T1 Profit Lock
            if not active_pos['t1_hit']:
                hit_tp1 = (cur_h >= tp1) if d == 'BUY' else (cur_l <= tp1)
                if hit_tp1:
                    pnl1 = (abs(tp1 - entry) * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_per_lot)
                    capital += pnl1
                    active_pos['t1_hit'] = True
                    be_lvl = entry + 0.15 if d == 'BUY' else entry - 0.15
                    active_pos['sl'] = be_lvl
                    sl = be_lvl
                    trades.append({'dt': cur_dt, 'dir': d, 'lots': active_pos['lots_t1'], 'entry': entry, 'exit': tp1, 'pnl': pnl1, 'type': 'TP1_LOCK (+BE)'})
                    
            # T2 Chandelier Trailing Stop
            if active_pos['t1_hit']:
                trail_sl = active_pos['extreme_price'] - (2.8 * atr) if d == 'BUY' else active_pos['extreme_price'] + (2.8 * atr)
                if (d == 'BUY' and trail_sl > active_pos['sl']) or (d == 'SELL' and trail_sl < active_pos['sl']):
                    active_pos['sl'] = trail_sl
                    sl = trail_sl
                    
            # Stop-Loss exit
            hit_sl = (cur_l <= sl) if d == 'BUY' else (cur_h >= sl)
            if hit_sl:
                rem_lots = active_pos['lots_t2'] if active_pos['t1_hit'] else (active_pos['lots_t1'] + active_pos['lots_t2'])
                pts = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl_exit = (pts * rem_lots * contract) - (rem_lots * comm_per_lot)
                capital += pnl_exit
                reason = 'CHANDELIER_TRAIL_EXIT' if active_pos['t1_hit'] else ('SALVAGE_EXIT' if active_pos.get('salvage_active') else 'INITIAL_SL')
                trades.append({'dt': cur_dt, 'dir': d, 'lots': rem_lots, 'entry': entry, 'exit': sl, 'pnl': pnl_exit, 'type': reason})
                active_pos = None

        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd: max_dd = dd
        
        if active_pos is None:
            sw_h = prev_row['conf_sh']
            sw_l = prev_row['conf_sl']
            if pd.isna(sw_h) or pd.isna(sw_l): continue
            prev_c = prev_row['close']
            risk_cash = capital * 0.025
            
            if prev_c < sw_h and cur_h >= sw_h and cur_c >= ema:
                entry = sw_h + (spread / 2.0)
                sl = entry - (2.0 * atr)
                sl_dist = abs(entry - sl)
                tp1 = entry + (2.5 * atr)
                tot_lots = max(0.02, min(10.0, round(risk_cash / (sl_dist * contract), 2)))
                l1 = max(0.01, round(tot_lots * 0.5, 2))
                l2 = max(0.01, round(tot_lots - l1, 2))
                active_pos = {'dir': 'BUY', 'entry': entry, 'sl': sl, 'tp1': tp1, 'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist, 't1_hit': False, 'extreme_price': cur_h, 'bars': 0, 'salvage_active': False}
            elif prev_c > sw_l and cur_l <= sw_l and cur_c <= ema:
                entry = sw_l - (spread / 2.0)
                sl = entry + (2.0 * atr)
                sl_dist = abs(entry - sl)
                tp1 = entry - (2.5 * atr)
                tot_lots = max(0.02, min(10.0, round(risk_cash / (sl_dist * contract), 2)))
                l1 = max(0.01, round(tot_lots * 0.5, 2))
                l2 = max(0.01, round(tot_lots - l1, 2))
                active_pos = {'dir': 'SELL', 'entry': entry, 'sl': sl, 'tp1': tp1, 'lots_t1': l1, 'lots_t2': l2, 'sl_dist': sl_dist, 't1_hit': False, 'extreme_price': cur_l, 'bars': 0, 'salvage_active': False}

    print("\n--- 2-DAY PERFORMANCE SUMMARY (DUAL-HORIZON TESTING ALGO) ---")
    net_pnl = capital - initial_cap
    ret_pct = (net_pnl / initial_cap) * 100.0
    wins = [t for t in trades if t['pnl'] > 0]
    wr = (len(wins) / len(trades) * 100.0) if len(trades) > 0 else 0.0
    print(f"Starting Capital   : ${initial_cap:,.2f}")
    print(f"Final Capital      : ${capital:,.2f}")
    print(f"Net Profit         : +${net_pnl:,.2f} (+{ret_pct:.2f}%)")
    print(f"Total Trades       : {len(trades)}")
    print(f"Win Rate           : {wr:.1f}% ({len(wins)} Wins / {len(trades)-len(wins)} Losses)")
    print(f"Max Drawdown       : {max_dd:.2f}% (Ceiling: 30.0%)")
    print("\nTrades Executed Over Last 2 Days:")
    for i, t in enumerate(trades, 1):
        p_str = f"+${t['pnl']:,.2f}" if t['pnl'] > 0 else f"-${abs(t['pnl']):,.2f}"
        print(f"  {i}. {t['dt'].strftime('%Y-%m-%d %H:%M UTC')} | {t['dir']} {t['lots']:.2f} lots | In: ${t['entry']:.2f} -> Out: ${t['exit']:.2f} | PnL: {p_str:<12} | {t['type']}")
    print("=" * 90)

if __name__ == '__main__':
    audit_2days()
