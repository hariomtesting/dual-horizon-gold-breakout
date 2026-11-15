import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def get_data(symbol, n_bars=20000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    ist = pytz.timezone('Asia/Kolkata')
    df['ist_time'] = df['time'].dt.tz_convert(ist)
    df['date'] = df['ist_time'].dt.date
    df['hour'] = df['ist_time'].dt.hour
    df['minute'] = df['ist_time'].dt.minute
    df['time_num'] = df['hour'] * 100 + df['minute']
    
    # ATR & EMA
    h, l, c = df['high'], df['low'], df['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df['atr'] = tr.rolling(14).mean()
    df['ema_fast'] = c.ewm(span=20, adjust=False).mean()
    df['ema_trend'] = c.ewm(span=200, adjust=False).mean()
    return df.dropna().reset_index(drop=True)

def backtest_asset(symbol, df, config, initial_capital=10000.0):
    sym_info = mt5.symbol_info(symbol)
    if not sym_info:
        return None
    
    point = sym_info.point
    contract = sym_info.trade_contract_size
    spread_pts = sym_info.spread
    spread_cost = spread_pts * point
    comm_per_lot = 6.0
    
    lookback = config.get('lookback', 12)
    dist_atr = config.get('dist_atr', 0.20)
    sl_atr = config.get('sl_atr', 0.20)
    tp1_r = config.get('tp1_r', 1.4)
    tp2_r = config.get('tp2_r', 2.5)
    risk_pct = config.get('risk_pct', 0.0075)
    max_trades_day = config.get('max_trades_day', 3)
    mode = config.get('mode', 'SWEEP') # 'SWEEP', 'MOMENTUM', 'SESSION_BREAK'
    
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    trades = []
    
    dates = df['date'].unique()
    
    for dt in dates:
        day_df = df[df['date'] == dt].copy()
        if len(day_df) < lookback + 10:
            continue
            
        trades_today = 0
        active_pos = None
        
        for idx in range(lookback, len(day_df)):
            row = day_df.iloc[idx]
            t_num = row['time_num']
            
            # Daytime operational window: strictly 10:00 to 15:55 IST
            if t_num < 1000:
                continue
                
            # Mandatory 15:55 flat square-off (No VPS overnight risk)
            if t_num >= 1555:
                if active_pos is not None:
                    exit_p = row['close']
                    if active_pos['type'] == 'BUY':
                        pnl = (exit_p - active_pos['entry']) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                    else:
                        pnl = (active_pos['entry'] - exit_p) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                    capital += pnl
                    active_pos['exit_time'] = row['ist_time']
                    active_pos['pnl'] = pnl
                    active_pos['exit_reason'] = 'SQUARE_OFF_1555'
                    trades.append(active_pos)
                    active_pos = None
                break
                
            # Manage active position
            if active_pos is not None:
                h_p, l_p = row['high'], row['low']
                # Check Stop Loss
                if active_pos['type'] == 'BUY':
                    if l_p <= active_pos['sl']:
                        loss = (active_pos['sl'] - active_pos['entry']) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                        capital += loss
                        active_pos['exit_time'] = row['ist_time']
                        active_pos['pnl'] = loss
                        active_pos['exit_reason'] = 'SL'
                        trades.append(active_pos)
                        active_pos = None
                        continue
                    # Check TP1
                    if not active_pos['tp1_hit'] and h_p >= active_pos['tp1']:
                        tp1_pnl = (active_pos['tp1'] - active_pos['entry']) * contract * active_pos['t1_vol'] - (comm_per_lot * active_pos['t1_vol'])
                        capital += tp1_pnl
                        active_pos['tp1_hit'] = True
                        active_pos['sl'] = active_pos['entry'] # Breakeven lock!
                        active_pos['vol'] = active_pos['t2_vol']
                    # Check TP2
                    if active_pos['tp1_hit'] and h_p >= active_pos['tp2']:
                        tp2_pnl = (active_pos['tp2'] - active_pos['entry']) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                        capital += tp2_pnl
                        active_pos['exit_time'] = row['ist_time']
                        active_pos['pnl'] = tp1_pnl + tp2_pnl
                        active_pos['exit_reason'] = 'TP2'
                        trades.append(active_pos)
                        active_pos = None
                        continue
                else: # SELL
                    if h_p >= active_pos['sl']:
                        loss = (active_pos['entry'] - active_pos['sl']) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                        capital += loss
                        active_pos['exit_time'] = row['ist_time']
                        active_pos['pnl'] = loss
                        active_pos['exit_reason'] = 'SL'
                        trades.append(active_pos)
                        active_pos = None
                        continue
                    # Check TP1
                    if not active_pos['tp1_hit'] and l_p <= active_pos['tp1']:
                        tp1_pnl = (active_pos['entry'] - active_pos['tp1']) * contract * active_pos['t1_vol'] - (comm_per_lot * active_pos['t1_vol'])
                        capital += tp1_pnl
                        active_pos['tp1_hit'] = True
                        active_pos['sl'] = active_pos['entry'] # Breakeven lock!
                        active_pos['vol'] = active_pos['t2_vol']
                    # Check TP2
                    if active_pos['tp1_hit'] and l_p <= active_pos['tp2']:
                        tp2_pnl = (active_pos['entry'] - active_pos['tp2']) * contract * active_pos['vol'] - (comm_per_lot * active_pos['vol'])
                        capital += tp2_pnl
                        active_pos['exit_time'] = row['ist_time']
                        active_pos['pnl'] = tp1_pnl + tp2_pnl
                        active_pos['exit_reason'] = 'TP2'
                        trades.append(active_pos)
                        active_pos = None
                        continue

            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

            # Look for new signal
            if active_pos is None and trades_today < max_trades_day and t_num < 1520:
                atr = row['atr']
                if atr <= 0:
                    continue
                
                prev_bars = day_df.iloc[idx - lookback : idx]
                swing_hi = prev_bars['high'].max()
                swing_lo = prev_bars['low'].min()
                
                cur_o, cur_h, cur_l, cur_c = row['open'], row['high'], row['low'], row['close']
                ema = row['ema_trend']
                
                sig = None
                
                if mode == 'SWEEP':
                    # Bullish Sweep of recent low
                    if cur_l < swing_lo and (swing_lo - cur_l) >= (dist_atr * atr) and cur_c > swing_lo:
                        if cur_c > ema:
                            sig = 'BUY'
                            entry = cur_c + spread_cost
                            sl = cur_l - (sl_atr * atr)
                    # Bearish Sweep of recent high
                    elif cur_h > swing_hi and (cur_h - swing_hi) >= (dist_atr * atr) and cur_c < swing_hi:
                        if cur_c < ema:
                            sig = 'SELL'
                            entry = cur_c - spread_cost
                            sl = cur_h + (sl_atr * atr)
                elif mode == 'MOMENTUM':
                    # Momentum Breakout beyond swing
                    if cur_c > swing_hi and cur_c > ema:
                        sig = 'BUY'
                        entry = cur_c + spread_cost
                        sl = cur_c - (sl_atr * atr)
                    elif cur_c < swing_lo and cur_c < ema:
                        sig = 'SELL'
                        entry = cur_c - spread_cost
                        sl = cur_c + (sl_atr * atr)
                elif mode == 'SESSION_BREAK':
                    # Session open expansion (e.g. at 12:00-13:30 European open)
                    if 1200 <= t_num <= 1400:
                        if cur_c > swing_hi:
                            sig = 'BUY'
                            entry = cur_c + spread_cost
                            sl = cur_c - (sl_atr * atr)
                        elif cur_c < swing_lo:
                            sig = 'SELL'
                            entry = cur_c - spread_cost
                            sl = cur_c + (sl_atr * atr)
                
                if sig is not None:
                    sl_dist = abs(entry - sl)
                    if sl_dist > 0:
                        risk_usd = capital * risk_pct
                        raw_vol = risk_usd / (sl_dist * contract)
                        # Normalize volume
                        vol = max(0.01, round(raw_vol, 2))
                        if vol > 0.01:
                            t1_vol = round(vol * 0.5, 2)
                            t2_vol = round(vol - t1_vol, 2)
                            if t1_vol >= 0.01 and t2_vol >= 0.01:
                                tp1 = entry + (tp1_r * sl_dist) if sig == 'BUY' else entry - (tp1_r * sl_dist)
                                tp2 = entry + (tp2_r * sl_dist) if sig == 'BUY' else entry - (tp2_r * sl_dist)
                                active_pos = {
                                    'symbol': symbol,
                                    'type': sig,
                                    'entry': entry,
                                    'sl': sl,
                                    'tp1': tp1,
                                    'tp2': tp2,
                                    'vol': vol,
                                    't1_vol': t1_vol,
                                    't2_vol': t2_vol,
                                    'tp1_hit': False,
                                    'entry_time': row['ist_time']
                                }
                                trades_today += 1
                                
    # Performance summary
    n_trades = len(trades)
    if n_trades == 0:
        return {'symbol': symbol, 'trades': 0, 'pnl': 0.0, 'roi': 0.0, 'win_rate': 0.0, 'pf': 0.0, 'max_dd': 0.0}
    
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    total_gain = sum(wins) if wins else 0.0
    total_loss = abs(sum(losses)) if losses else 0.0
    pf = (total_gain / total_loss) if total_loss > 0 else 99.0
    win_rate = (len(wins) / n_trades) * 100
    net_pnl = sum(pnls)
    roi = (net_pnl / initial_capital) * 100
    
    # Calculate days in sample
    n_days = len(dates)
    trades_per_month = (n_trades / n_days) * 21.0 if n_days > 0 else 0
    calmar = (roi / (max_dd * 100)) if max_dd > 0 else 0.0
    
    return {
        'symbol': symbol,
        'mode': mode,
        'config': config,
        'days': n_days,
        'trades': n_trades,
        'trades_month': round(trades_per_month, 1),
        'net_pnl': round(net_pnl, 2),
        'roi': round(roi, 2),
        'win_rate': round(win_rate, 2),
        'pf': round(pf, 2),
        'max_dd': round(max_dd * 100, 2),
        'calmar': round(calmar, 2)
    }

def main():
    mt5.initialize()
    candidates = ['XAUUSD', 'BTCUSD', 'ETHUSD', 'GER40.r', 'NAS100.r', 'DJ30.r', 'EURUSD', 'GBPUSD', 'USDJPY', 'CL-OIL']
    print(f"=== SCREENING {len(candidates)} CANDIDATE ASSETS ACROSS TAILORED DECOUPLED STRATEGIES ===")
    print(f"Operational Window: Strictly 10:00 - 15:55 IST | Mandatory 15:55 Flat Close (NO VPS)\n")
    
    results = []
    
    # For each asset, test its natural microstructure profiles
    # Gold: wick sweep with tight buffer (0.20 ATR)
    # Crypto: sweep vs momentum with wider buffer
    # Indices: sweep vs session open expansion
    # FX: sweep with low buffer
    
    profiles = {
        'XAUUSD': [
            {'lookback': 12, 'dist_atr': 0.20, 'sl_atr': 0.20, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'Wick Sweep (Tight 0.20 ATR)'},
            {'lookback': 8, 'dist_atr': 0.15, 'sl_atr': 0.25, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SWEEP', 'desc': 'Fast Wick Sweep'}
        ],
        'BTCUSD': [
            {'lookback': 12, 'dist_atr': 0.25, 'sl_atr': 0.35, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SWEEP', 'desc': 'Crypto Liquidity Sweep'},
            {'lookback': 16, 'dist_atr': 0.0, 'sl_atr': 0.50, 'tp1_r': 1.5, 'tp2_r': 3.5, 'mode': 'MOMENTUM', 'desc': 'Crypto Momentum Break'}
        ],
        'ETHUSD': [
            {'lookback': 12, 'dist_atr': 0.25, 'sl_atr': 0.35, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SWEEP', 'desc': 'ETH Liquidity Sweep'},
            {'lookback': 16, 'dist_atr': 0.0, 'sl_atr': 0.50, 'tp1_r': 1.5, 'tp2_r': 3.5, 'mode': 'MOMENTUM', 'desc': 'ETH Momentum Break'}
        ],
        'GER40.r': [
            {'lookback': 12, 'dist_atr': 0.15, 'sl_atr': 0.25, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'DAX Wick Sweep'},
            {'lookback': 8, 'dist_atr': 0.0, 'sl_atr': 0.35, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SESSION_BREAK', 'desc': 'DAX European Open Auction Break'}
        ],
        'NAS100.r': [
            {'lookback': 12, 'dist_atr': 0.20, 'sl_atr': 0.30, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'Nasdaq Wick Sweep'},
            {'lookback': 16, 'dist_atr': 0.0, 'sl_atr': 0.40, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SESSION_BREAK', 'desc': 'Nasdaq Daytime Break'}
        ],
        'DJ30.r': [
            {'lookback': 12, 'dist_atr': 0.20, 'sl_atr': 0.30, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'Dow 30 Wick Sweep'}
        ],
        'EURUSD': [
            {'lookback': 12, 'dist_atr': 0.15, 'sl_atr': 0.20, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'EURUSD Liquidity Sweep'},
            {'lookback': 8, 'dist_atr': 0.0, 'sl_atr': 0.30, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SESSION_BREAK', 'desc': 'EURUSD London Fix Break'}
        ],
        'GBPUSD': [
            {'lookback': 12, 'dist_atr': 0.15, 'sl_atr': 0.20, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'GBPUSD Liquidity Sweep'},
            {'lookback': 8, 'dist_atr': 0.0, 'sl_atr': 0.30, 'tp1_r': 1.5, 'tp2_r': 3.0, 'mode': 'SESSION_BREAK', 'desc': 'GBPUSD London Open Break'}
        ],
        'USDJPY': [
            {'lookback': 12, 'dist_atr': 0.15, 'sl_atr': 0.20, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'USDJPY Liquidity Sweep'}
        ],
        'CL-OIL': [
            {'lookback': 12, 'dist_atr': 0.20, 'sl_atr': 0.25, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'Crude Oil Wick Sweep'}
        ]
    }
    
    best_per_asset = []
    
    for sym in candidates:
        print(f"Testing {sym}...")
        df = get_data(sym, n_bars=20000)
        if df is None:
            print(f"  [!] No data for {sym}")
            continue
            
        sym_profiles = profiles.get(sym, [{'lookback': 12, 'dist_atr': 0.20, 'sl_atr': 0.20, 'tp1_r': 1.4, 'tp2_r': 2.5, 'mode': 'SWEEP', 'desc': 'Default Sweep'}])
        
        asset_tests = []
        for prof in sym_profiles:
            res = backtest_asset(sym, df, prof)
            if res and res['trades'] > 5:
                res['desc'] = prof.get('desc', 'Custom')
                asset_tests.append(res)
                
        if asset_tests:
            # Pick highest Calmar / PF for this asset
            best = sorted(asset_tests, key=lambda x: (x['pf'] > 1.3, x['calmar'], x['net_pnl']), reverse=True)[0]
            best_per_asset.append(best)
            print(f"  -> Best: {best['desc']} | ROI: +{best['roi']}% | PF: {best['pf']} | DD: {best['max_dd']}% | WinRate: {best['win_rate']}% | Trades: {best['trades']} ({best['trades_month']}/mo)")
    
    print("\n" + "="*95)
    print("OBJECTIVE QUANTITATIVE RANKING ACROSS ALL CANDIDATE ASSETS (BY CALMAR & PROFIT FACTOR)")
    print("="*95)
    
    ranked = sorted(best_per_asset, key=lambda x: (x['pf'], x['calmar']), reverse=True)
    
    print(f"{'Rank':<5} {'Symbol':<10} {'Strategy Description':<30} {'Net PnL':<12} {'ROI %':<10} {'PF':<8} {'Max DD%':<10} {'WinRate':<10} {'Trades/Mo':<10}")
    print("-" * 95)
    for i, r in enumerate(ranked):
        pnl_str = f"+${r['net_pnl']:.2f}" if r['net_pnl'] >= 0 else f"-${abs(r['net_pnl']):.2f}"
        roi_str = f"+{r['roi']:.2f}%" if r['roi'] >= 0 else f"{r['roi']:.2f}%"
        badge = " [TOP 5]" if i < 5 else ""
        print(f"#{i+1:<4} {r['symbol']:<10} {r['desc']:<30} {pnl_str:<12} {roi_str:<10} {r['pf']:<8.2f} {r['max_dd']:<10.2f} {r['win_rate']:<9.1f}% {r['trades_month']:<10.1f}{badge}")
        
    print("="*95)

if __name__ == '__main__':
    main()

