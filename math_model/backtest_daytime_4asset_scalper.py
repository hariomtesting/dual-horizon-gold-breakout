"""
EMPIRICAL AUDIT: 4-ASSET DAYTIME HIGH-FREQUENCY SCALPER
========================================================
Assets: GER40.r (DAX 40), XAUUSD (Gold), BTCUSD (Bitcoin), EURUSD (Euro)
Operating Window: Strictly 10:00 AM to 15:55 IST (Local PC, Zero VPS)
Mandatory Auto-Square-Off: 15:55 IST (100% Flat before PC shutdown)

Real-World Friction Model:
- Live Broker Spreads: Gold $0.28, BTC $17.0, DAX 2.45 pts, EUR 1.4 pips
- ECN Round-Turn Commission: $6.00/lot ($4.00/lot on DAX)
- Latency Slippage on entries & stops
- Split Target: TP1 @ 1.4R (locks profit + SL to Breakeven), TP2 @ 2.5R
"""

import os
import sys
import datetime
import numpy as np
import pandas as pd
from typing import Dict, Any, List
import MetaTrader5 as mt5


def fetch_daytime_data(symbol: str, count: int = 20000) -> Optional[pd.DataFrame]:
    if not mt5.initialize():
        return None
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
    df['date'] = df['ist_dt'].dt.date
    df['time_str'] = df['ist_dt'].dt.strftime('%H:%M')
    df['hour'] = df['ist_dt'].dt.hour
    df['minute'] = df['ist_dt'].dt.minute
    
    # Technical Indicators
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['swing_high'] = df['high'].shift(1).rolling(12).max()
    df['swing_low'] = df['low'].shift(1).rolling(12).min()
    
    vol_ma = df['tick_volume'].rolling(20, min_periods=5).mean().bfill()
    df['rvol'] = df['tick_volume'] / (vol_ma + 1e-8)
    
    return df


def get_specs(symbol: str) -> Dict[str, Any]:
    if "XAU" in symbol:
        return {'contract': 100.0, 'spread': 0.28, 'slip': 0.10, 'comm': 6.00, 'min_atr': 3.0}
    elif "BTC" in symbol:
        return {'contract': 1.0, 'spread': 17.0, 'slip': 3.0, 'comm': 6.00, 'min_atr': 80.0}
    elif "GER" in symbol or "40" in symbol:
        return {'contract': 1.0, 'spread': 2.45, 'slip': 0.50, 'comm': 4.00, 'min_atr': 15.0}
    elif "EUR" in symbol:
        return {'contract': 100000.0, 'spread': 0.00014, 'slip': 0.00005, 'comm': 6.00, 'min_atr': 0.0005}
    else:
        return {'contract': 100000.0, 'spread': 0.00020, 'slip': 0.00005, 'comm': 6.00, 'min_atr': 0.0005}


def backtest_symbol(symbol: str, df: pd.DataFrame, initial_balance: float = 10000.0, risk_pct: float = 0.005) -> Dict[str, Any]:
    specs = get_specs(symbol)
    contract = specs['contract']
    spread = specs['spread']
    slip = specs['slip']
    comm_lot = specs['comm']
    min_atr = specs['min_atr']
    
    daily_groups = df.groupby('date')
    balance = initial_balance
    trades = []
    daily_pnls = {}
    total_toll = 0.0
    
    for d, g in daily_groups:
        day_pnl = 0.0
        # Window: 10:00 to 15:55 IST
        day_bars = g[(g['hour'] >= 10) & ((g['hour'] < 15) | ((g['hour'] == 15) & (g['minute'] <= 55)))].copy()
        if len(day_bars) < 15:
            continue
            
        active_trade = None
        pending_limit = None
        trades_today = 0
        
        for idx, row in day_bars.iterrows():
            h, m = row['hour'], row['minute']
            atr = row['atr']
            if atr < min_atr:
                continue
                
            # 1. Hard 15:55 IST EOD Liquidation
            if h == 15 and m >= 55:
                if active_trade is not None:
                    d_type = active_trade['type']
                    fill = active_trade['fill']
                    lots_t1 = active_trade['lots_t1']
                    lots_t2 = active_trade['lots_t2']
                    t1_closed = active_trade['t1_closed']
                    rem_lots = lots_t2 if t1_closed else (lots_t1 + lots_t2)
                    
                    exit_p = (row['close'] - spread/2.0 - slip) if d_type == 'BUY' else (row['close'] + spread/2.0 + slip)
                    pts = (exit_p - fill) if d_type == 'BUY' else (fill - exit_p)
                    toll = rem_lots * (comm_lot + spread * contract + slip * contract)
                    pnl = (pts * rem_lots * contract) - toll
                    total_toll += toll
                    day_pnl += pnl
                    trades.append({'symbol': symbol, 'pnl': pnl, 'win': (pnl > 0), 'date': d, 'type': 'EOD_CLOSE'})
                    active_trade = None
                break
                
            # 2. Manage Active Position (Split Target + Breakeven Trailing)
            if active_trade is not None:
                d_type = active_trade['type']
                fill = active_trade['fill']
                sl = active_trade['sl']
                tp1 = active_trade['tp1']
                tp2 = active_trade['tp2']
                lots_t1 = active_trade['lots_t1']
                lots_t2 = active_trade['lots_t2']
                t1_closed = active_trade['t1_closed']
                
                hit_sl = (row['low'] <= sl) if d_type == 'BUY' else (row['high'] >= sl)
                hit_tp1 = (row['high'] >= tp1) if d_type == 'BUY' else (row['low'] <= tp1)
                hit_tp2 = (row['high'] >= tp2) if d_type == 'BUY' else (row['low'] <= tp2)
                
                if hit_sl:
                    rem_lots = lots_t2 if t1_closed else (lots_t1 + lots_t2)
                    pts = (sl - fill) if d_type == 'BUY' else (fill - sl)
                    toll = rem_lots * (comm_lot + spread * contract + slip * contract)
                    pnl = (pts * rem_lots * contract) - toll
                    total_toll += toll
                    day_pnl += pnl
                    trades.append({'symbol': symbol, 'pnl': pnl, 'win': (pnl > 0), 'date': d, 'type': 'SL'})
                    active_trade = None
                    continue
                elif hit_tp2:
                    rem_lots = lots_t2 if t1_closed else (lots_t1 + lots_t2)
                    pts = (tp2 - fill) if d_type == 'BUY' else (fill - tp2)
                    toll = rem_lots * (comm_lot + spread * contract + slip * contract)
                    pnl = (pts * rem_lots * contract) - toll
                    total_toll += toll
                    day_pnl += pnl
                    trades.append({'symbol': symbol, 'pnl': pnl, 'win': True, 'date': d, 'type': 'TP2'})
                    active_trade = None
                    continue
                elif hit_tp1 and not t1_closed:
                    pts = (tp1 - fill) if d_type == 'BUY' else (fill - tp1)
                    toll = lots_t1 * (comm_lot + spread * contract + slip * contract)
                    pnl1 = (pts * lots_t1 * contract) - toll
                    total_toll += toll
                    day_pnl += pnl1
                    trades.append({'symbol': symbol, 'pnl': pnl1, 'win': True, 'date': d, 'type': 'TP1'})
                    active_trade['t1_closed'] = True
                    active_trade['sl'] = fill # Lock Breakeven
                    continue
                    
            # 3. Check Pending Limit Fill (with 6-bar TTL)
            if pending_limit is not None:
                lp = pending_limit['lp']
                filled = (row['low'] <= lp) if pending_limit['type'] == 'BUY' else (row['high'] >= lp)
                if filled:
                    active_trade = pending_limit
                    pending_limit = None
                    continue
                else:
                    pending_limit['bars'] += 1
                    if pending_limit['bars'] >= 6: # 30 min expiration
                        pending_limit = None
                        
            # 4. Scan for Daytime Liquidity Sweep
            if active_trade is None and pending_limit is None and trades_today < 3:
                sw_h = row['swing_high']
                sw_l = row['swing_low']
                rvol = row['rvol']
                if pd.isna(sw_h) or pd.isna(sw_l):
                    continue
                    
                sweep_dist = 0.20 * atr
                is_bull = (row['low'] < (sw_l - sweep_dist)) and (row['close'] > sw_l) and (row['close'] >= row['ema200']) and (rvol >= 1.10)
                is_bear = (row['high'] > (sw_h + sweep_dist)) and (row['close'] < sw_h) and (row['close'] <= row['ema200']) and (rvol >= 1.10)
                
                if is_bull or is_bear:
                    t_dir = 'BUY' if is_bull else 'SELL'
                    # 25% wick retest maker limit (high fill probability)
                    lp = round((row['close'] * 0.75 + (row['low'] if is_bull else row['high']) * 0.25), 4)
                    sl_dist = 1.2 * atr
                    sl = (lp - sl_dist) if is_bull else (lp + sl_dist)
                    tp1 = (lp + 1.4 * sl_dist) if is_bull else (lp - 1.4 * sl_dist)
                    tp2 = (lp + 2.5 * sl_dist) if is_bull else (lp - 2.5 * sl_dist)
                    
                    risk_cash = balance * risk_pct
                    raw_lots = risk_cash / (sl_dist * contract + 1e-8)
                    total_lots = max(0.02, round(raw_lots, 2))
                    l1 = round(total_lots / 2.0, 2)
                    l2 = round(total_lots - l1, 2)
                    
                    pending_limit = {
                        'type': t_dir, 'lp': lp, 'fill': lp, 'sl': sl,
                        'tp1': tp1, 'tp2': tp2, 'lots_t1': l1, 'lots_t2': l2,
                        't1_closed': False, 'bars': 0
                    }
                    trades_today += 1
                    
        balance += day_pnl
        daily_pnls[d] = day_pnl
        
    df_t = pd.DataFrame(trades)
    return compile_stats(symbol, df_t, daily_pnls, initial_balance, balance, total_toll)


def compile_stats(name: str, df_t: pd.DataFrame, daily_pnls: Dict[Any, float], initial_bal: float, final_bal: float, toll: float) -> Dict[str, Any]:
    n_days = len(daily_pnls)
    n_trades = len(df_t)
    if n_trades == 0:
        return {'symbol': name, 'trades': 0, 'trades_per_day': 0.0, 'win_rate': 0.0, 'pnl': 0.0, 'return_pct': 0.0, 'pf': 0.0, 'max_dd': 0.0, 'sharpe': 0.0, 'toll': 0.0}
        
    trades_per_day = n_trades / (n_days + 1e-8)
    win_rate = (df_t['win'].sum() / n_trades) * 100.0
    
    wins = df_t[df_t['pnl'] > 0]['pnl'].sum()
    losses = abs(df_t[df_t['pnl'] < 0]['pnl'].sum())
    pf = wins / (losses + 1e-8)
    
    pnl_s = pd.Series(list(daily_pnls.values()))
    eq = initial_bal + pnl_s.cumsum()
    running_max = eq.cummax()
    dd = (running_max - eq) / running_max * 100.0
    max_dd = dd.max() if len(dd) > 0 else 0.0
    
    sharpe = (pnl_s.mean() / (pnl_s.std() + 1e-8)) * np.sqrt(252) if len(pnl_s) > 1 else 0.0
    
    return {
        'symbol': name,
        'trades': n_trades,
        'trades_per_day': round(trades_per_day, 2),
        'win_rate': round(win_rate, 1),
        'pnl': round(final_bal - initial_bal, 2),
        'return_pct': round(((final_bal - initial_bal) / initial_bal) * 100.0, 2),
        'pf': round(pf, 2),
        'max_dd': round(max_dd, 2),
        'sharpe': round(sharpe, 2),
        'toll': round(toll, 2),
        'daily_pnls': daily_pnls,
        'trades_df': df_t
    }


def run_portfolio_simulation():
    symbols = ['GER40.r', 'XAUUSD', 'BTCUSD', 'EURUSD']
    print("=" * 115)
    print("      RIGOROUS BACKTEST: 4-ASSET DAYTIME SCALPER (10:00 AM - 15:55 IST, ZERO VPS)      ")
    print("      Real Spreads + ECN Commissions ($6/lot) + Latency Slippage + 15:55 Flat Rule     ")
    print("=" * 115 + "\n")
    
    results = []
    daily_matrix = {}
    
    for s in symbols:
        print(f"Loading M5 historical data for {s}...")
        df = fetch_daytime_data(s, count=25000)
        if df is None:
            print(f"   [Error] Could not load data for {s}")
            continue
        print(f"   Loaded {len(df):,} bars across {df['date'].nunique()} trading days.")
        res = backtest_symbol(s, df)
        results.append(res)
        daily_matrix[s] = res['daily_pnls']
        
    # Portfolio Combination
    all_dates = sorted(list(set.union(*[set(d.keys()) for d in daily_matrix.values()])))
    port_daily = []
    initial_bal = 10000.0
    port_trades = 0
    total_toll = sum(r['toll'] for r in results)
    
    for d in all_dates:
        dpnl = sum(daily_matrix[s].get(d, 0.0) for s in daily_matrix.keys())
        port_daily.append(dpnl)
        
    pnl_s = pd.Series(port_daily)
    eq = initial_bal + pnl_s.cumsum()
    running_max = eq.cummax()
    dd = (running_max - eq) / running_max * 100.0
    max_dd = dd.max() if len(dd) > 0 else 0.0
    final_bal = eq.iloc[-1]
    
    all_trades_dfs = [r['trades_df'] for r in results if len(r['trades_df']) > 0]
    total_trades_df = pd.concat(all_trades_dfs) if all_trades_dfs else pd.DataFrame()
    win_rate = (total_trades_df['win'].sum() / len(total_trades_df)) * 100.0 if len(total_trades_df) > 0 else 0.0
    
    wins = total_trades_df[total_trades_df['pnl'] > 0]['pnl'].sum() if len(total_trades_df) > 0 else 0.0
    losses = abs(total_trades_df[total_trades_df['pnl'] < 0]['pnl'].sum()) if len(total_trades_df) > 0 else 1.0
    pf = wins / (losses + 1e-8)
    sharpe = (pnl_s.mean() / (pnl_s.std() + 1e-8)) * np.sqrt(252) if len(pnl_s) > 1 else 0.0
    
    print("\n" + "=" * 115)
    print("                          INDIVIDUAL ASSET & COMBINED PORTFOLIO RESULTS                          ")
    print("===================================================================================================")
    header = f"{'Symbol / Basket':<22} | {'Trades':<8} | {'Trades/Day':<11} | {'Win Rate':<9} | {'Net Profit':<11} | {'Return':<8} | {'PF':<5} | {'Max DD':<7} | {'Sharpe':<6}"
    print(header)
    print("-" * 115)
    
    for r in results:
        pnl_s = f"${r['pnl']:,.2f}"
        ret_s = f"{r['return_pct']:+.1f}%"
        print(f"{r['symbol']:<22} | {r['trades']:<8} | {r['trades_per_day']:<11} | {r['win_rate']:>5.1f}%   | {pnl_s:>11} | {ret_s:>8} | {r['pf']:>5.2f} | {r['max_dd']:>5.2f}% | {r['sharpe']:>6.2f}")
        
    print("-" * 115)
    pnl_total = f"${final_bal - initial_bal:,.2f}"
    ret_total = f"{((final_bal - initial_bal) / initial_bal) * 100.0:+.1f}%"
    tot_trades = len(total_trades_df)
    tot_daily = tot_trades / len(all_dates)
    print(f"{'COMBINED 4-ASSET':<22} | {tot_trades:<8} | {tot_daily:<11.2f} | {win_rate:>5.1f}%   | {pnl_total:>11} | {ret_total:>8} | {pf:>5.2f} | {max_dd:>5.2f}% | {sharpe:>6.2f}")
    print("=" * 115)
    print(f"\nTotal Broker Toll (Spreads + Commissions) Deducted: ${total_toll:,.2f}")
    print(f"Final Account Balance: ${final_bal:,.2f} on $10,000 initial capital.")


if __name__ == '__main__':
    run_portfolio_simulation()

