import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

def load_clean_data(symbol, n_bars=25000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    ist = pytz.timezone('Asia/Kolkata')
    df['ist_time'] = df['time'].dt.tz_convert(ist)
    df['date'] = df['ist_time'].dt.date
    df['ist_hour'] = df['ist_time'].dt.hour
    df['ist_minute'] = df['ist_time'].dt.minute
    df['time_num'] = df['ist_hour'] * 100 + df['ist_minute']
    
    # ATR & EMA
    h, l, c = df['high'], df['low'], df['close']
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    df['atr'] = tr.rolling(14).mean()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()
    return df.dropna().reset_index(drop=True)

def simulate_asset_decoupled(symbol, df, param_set, initial_capital=10000.0):
    sym_info = mt5.symbol_info(symbol)
    if not sym_info:
        return None
        
    point = sym_info.point
    spread_pts = sym_info.spread
    spread = spread_pts * point
    
    # Real broker commission per 1.0 standard lot
    # FX & Metals = $6/lot round turn; Indices & Crypto & Oil = $0/lot commission
    calc_mode = sym_info.trade_calc_mode
    if calc_mode in [0, 4]: # Forex & Metals
        comm_per_lot = 6.0
    else: # CFDs, Indices, Crypto
        comm_per_lot = 0.0
        
    # Calculate dollar value per 1.0 price unit move for 1.0 lot
    p1 = sym_info.ask if sym_info.ask > 0 else 100.0
    p2 = p1 + 1.0
    calc_profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, p1, p2)
    val_per_unit = calc_profit if (calc_profit is not None and calc_profit > 0) else (sym_info.trade_contract_size)
    
    lookback = param_set['lookback']
    dist_mult = param_set['dist_mult']
    sl_mult = param_set['sl_mult']
    tp1_r = param_set['tp1_r']
    tp2_r = param_set['tp2_r']
    start_time = param_set.get('start_time', 1000)
    end_time = param_set.get('end_time', 1555)
    max_trades_day = param_set.get('max_trades_day', 3)
    risk_pct = param_set.get('risk_pct', 0.0075) # 0.75% per trade
    
    # Precalculate rolling swings
    df['swing_high'] = df['high'].rolling(lookback).max().shift(1)
    df['swing_low'] = df['low'].rolling(lookback).min().shift(1)
    
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    trades = []
    daily_records = []
    
    dates = df['date'].unique()
    
    for cur_date in dates:
        day_df = df[df['date'] == cur_date]
        if len(day_df) < lookback + 10:
            continue
            
        day_pnl = 0.0
        trades_today = 0
        active = None
        pending = None
        
        bars = day_df.to_dict('records')
        
        for row in bars:
            t = row['time_num']
            
            # Operational window filter
            if t < start_time:
                continue
                
            # Mandatory 15:55 IST flat square-off (NO VPS)
            if t >= end_time:
                if active:
                    exit_p = row['close'] - (spread/2.0) if active['dir'] == 'BUY' else row['close'] + (spread/2.0)
                    pts = (exit_p - active['entry']) if active['dir'] == 'BUY' else (active['entry'] - exit_p)
                    lots = active['lots_t1'] + active['lots_t2']
                    pnl = (pts * lots * val_per_unit) - (lots * comm_per_lot)
                    day_pnl += pnl
                    trades.append({'pnl': pnl, 'type': 'CUTOFF_1555', 'entry_time': active['entry_time']})
                    active = None
                break
                
            # Manage active trade
            if active:
                d = active['dir']
                entry = active['entry']
                sl = active['sl']
                tp1 = active['tp1']
                tp2 = active['tp2']
                
                # Check Tranche 1 (50% TP1 hit)
                if active['lots_t1'] > 0:
                    hit = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit:
                        pnl1 = (abs(tp1 - entry) * active['lots_t1'] * val_per_unit) - (active['lots_t1'] * comm_per_lot)
                        day_pnl += pnl1
                        trades.append({'pnl': pnl1, 'type': 'TP1_WIN', 'entry_time': active['entry_time']})
                        active['lots_t1'] = 0.0
                        # Auto Breakeven lock!
                        active['sl'] = entry + (0.05 * (tp1 - entry)) if d == 'BUY' else entry - (0.05 * (entry - tp1))
                        sl = active['sl']
                        
                # Check Tranche 2 (Runner TP2 hit or SL/Breakeven hit)
                if active['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active['lots_t2'] * val_per_unit) - (active['lots_t2'] * comm_per_lot)
                        day_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'TP2_WIN', 'entry_time': active['entry_time']})
                        active = None
                        continue
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active['lots_t2'] * val_per_unit) - (active['lots_t2'] * comm_per_lot)
                        day_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'SL_EXIT', 'entry_time': active['entry_time']})
                        active = None
                        continue
                continue
                
            # Manage pending limit orders (TTL 6 bars)
            if pending:
                lp = pending['lp']
                filled = (row['low'] <= lp) if pending['dir'] == 'BUY' else (row['high'] >= lp)
                if filled:
                    active = pending
                    pending = None
                    continue
                else:
                    pending['bars'] += 1
                    if pending['bars'] >= 6:
                        pending = None
                        
            # Scan for liquidity sweep setup
            if active is None and pending is None and trades_today < max_trades_day and t < (end_time - 25):
                sw_h = row['swing_high']
                sw_l = row['swing_low']
                atr = row['atr']
                if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0:
                    continue
                    
                is_bull = (row['low'] < (sw_l - dist_mult * atr)) and (row['close'] > sw_l)
                is_bear = (row['high'] > (sw_h + dist_mult * atr)) and (row['close'] < sw_h)
                
                if is_bull and row['close'] >= row['ema200']:
                    lp = (row['close'] + row['low']) / 2.0
                    sl = row['low'] - (sl_mult * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        risk_usd = capital * risk_pct
                        raw_lots = risk_usd / (sl_dist * val_per_unit)
                        # Normalize volume according to symbol step
                        step = sym_info.volume_step
                        min_v = sym_info.volume_min
                        lots = max(min_v, round(round(raw_lots / step) * step, 2))
                        l1 = max(min_v, round(round((lots * 0.5) / step) * step, 2))
                        l2 = max(min_v, round(lots - l1, 2))
                        if (l1 + l2) >= min_v:
                            pending = {
                                'dir': 'BUY',
                                'entry': lp,
                                'lp': lp,
                                'sl': sl,
                                'tp1': lp + (tp1_r * sl_dist),
                                'tp2': lp + (tp2_r * sl_dist),
                                'lots_t1': l1,
                                'lots_t2': l2,
                                'bars': 0,
                                'entry_time': row['ist_time']
                            }
                            trades_today += 1
                            
                elif is_bear and row['close'] <= row['ema200']:
                    lp = (row['close'] + row['high']) / 2.0
                    sl = row['high'] + (sl_mult * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        risk_usd = capital * risk_pct
                        raw_lots = risk_usd / (sl_dist * val_per_unit)
                        step = sym_info.volume_step
                        min_v = sym_info.volume_min
                        lots = max(min_v, round(round(raw_lots / step) * step, 2))
                        l1 = max(min_v, round(round((lots * 0.5) / step) * step, 2))
                        l2 = max(min_v, round(lots - l1, 2))
                        if (l1 + l2) >= min_v:
                            pending = {
                                'dir': 'SELL',
                                'entry': lp,
                                'lp': lp,
                                'sl': sl,
                                'tp1': lp - (tp1_r * sl_dist),
                                'tp2': lp - (tp2_r * sl_dist),
                                'lots_t1': l1,
                                'lots_t2': l2,
                                'bars': 0,
                                'entry_time': row['ist_time']
                            }
                            trades_today += 1

        capital += day_pnl
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            
        daily_records.append({'date': cur_date, 'bal': capital, 'pnl': day_pnl, 'dd': dd})

    n_trades = len(trades)
    if n_trades < 5:
        return None
        
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else 99.0
    win_rate = (len(wins) / n_trades) * 100.0
    net_pnl = capital - initial_capital
    roi = (net_pnl / initial_capital) * 100.0
    
    n_days = len(dates)
    years = n_days / 252.0
    cagr = ((capital / initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0 if capital > 0 else -100.0
    trades_per_month = (n_trades / n_days) * 21.0 if n_days > 0 else 0.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0
    
    # Sharpe ratio
    daily_s = pd.Series([r['pnl'] for r in daily_records])
    sharpe = (daily_s.mean() / (daily_s.std() + 1e-6)) * np.sqrt(252) if len(daily_s) > 1 else 0.0

    return {
        'symbol': symbol,
        'params': param_set,
        'days': n_days,
        'trades': n_trades,
        'trades_month': round(trades_per_month, 1),
        'net_pnl': round(net_pnl, 2),
        'cagr': round(cagr, 2),
        'max_dd': round(max_dd, 2),
        'pf': round(pf, 2),
        'win_rate': round(win_rate, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2)
    }

def main():
    mt5.initialize()
    candidates = ['XAUUSD', 'BTCUSD', 'ETHUSD', 'GER40.r', 'NAS100.r', 'DJ30.r', 'EURUSD', 'GBPUSD', 'USDJPY', 'CL-OIL']
    
    print("=" * 100)
    print("OBJECTIVE ASSET SCREENING FOR DECOUPLED INDEPENDENT TRADING STRATEGY")
    print("Environment: Strictly 10:00 - 15:55 IST | Mandatory 15:55 Flat Close (NO VPS) | Real Broker Toll")
    print("=" * 100)
    
    best_results = []
    
    for sym in candidates:
        df = load_clean_data(sym, n_bars=25000)
        if df is None:
            print(f"Skipping {sym}: No data")
            continue
            
        print(f"Analyzing {sym:<8} ({len(df)} bars, ~{len(df['date'].unique())} trading days)...")
        
        # Grid of decoupled asset-specific parameter sets
        # Each asset has its own tailored lookbacks, session hours, wick buffers, and targets
        param_grid = []
        
        if 'XAU' in sym: # Gold
            for lb in [8, 12, 16]:
                for dist in [0.15, 0.20, 0.25]:
                    for sl in [0.20, 0.30]:
                        param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 2.5, 'start_time': 1000, 'name': f'Gold_LB{lb}_D{dist}_SL{sl}'})
        elif 'BTC' in sym: # Bitcoin
            for lb in [12, 16, 24]:
                for dist in [0.20, 0.30]:
                    for sl in [0.30, 0.40]:
                        for tp2 in [2.5, 3.5]:
                            param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': tp2, 'start_time': 1000, 'name': f'BTC_LB{lb}_D{dist}_SL{sl}_TP{tp2}'})
        elif 'ETH' in sym: # Ethereum
            for lb in [12, 16, 20]:
                for dist in [0.20, 0.30]:
                    for sl in [0.30, 0.45]:
                        param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 3.0, 'start_time': 1000, 'name': f'ETH_LB{lb}_D{dist}'})
        elif 'GER40' in sym: # DAX Index (Best active during European Cash Session 12:30 - 15:55 IST)
            for lb in [8, 12, 16]:
                for dist in [0.15, 0.20]:
                    for sl in [0.20, 0.30]:
                        for start_t in [1000, 1230]: # Test all daytime vs cash open
                            param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 2.5, 'start_time': start_t, 'name': f'GER40_LB{lb}_T{start_t}_D{dist}'})
        elif 'NAS100' in sym or 'DJ30' in sym: # US Indices
            for lb in [10, 14, 18]:
                for dist in [0.20, 0.25]:
                    for sl in [0.25, 0.35]:
                        for start_t in [1200, 1330]:
                            param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 2.5, 'start_time': start_t, 'name': f'{sym}_LB{lb}_T{start_t}'})
        elif 'EUR' in sym or 'GBP' in sym or 'JPY' in sym: # FX Majors
            for lb in [8, 12, 16]:
                for dist in [0.15, 0.20]:
                    for sl in [0.15, 0.25]:
                        for start_t in [1000, 1200]: # European open focus
                            param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 2.5, 'start_time': start_t, 'name': f'{sym}_LB{lb}_T{start_t}'})
        elif 'OIL' in sym: # Crude Oil
            for lb in [10, 14, 18]:
                for dist in [0.20, 0.25]:
                    for sl in [0.25, 0.35]:
                        param_grid.append({'lookback': lb, 'dist_mult': dist, 'sl_mult': sl, 'tp1_r': 1.4, 'tp2_r': 2.5, 'start_time': 1000, 'name': f'OIL_LB{lb}'})
                        
        sym_best = None
        for p in param_grid:
            res = simulate_asset_decoupled(sym, df, p)
            if res and res['trades'] >= 10:
                # Rank by Profit Factor & Calmar
                if sym_best is None:
                    sym_best = res
                else:
                    if res['pf'] > sym_best['pf']:
                        sym_best = res
                        
        if sym_best:
            best_results.append(sym_best)
            p = sym_best['params']
            print(f"  -> Best for {sym}: PF {sym_best['pf']} | CAGR +{sym_best['cagr']}% | DD {sym_best['max_dd']}% | WinRate {sym_best['win_rate']}% | Trades {sym_best['trades']} ({sym_best['trades_month']}/mo)")
            print(f"     Decoupled Params: Lookback={p['lookback']}, Dist={p['dist_mult']} ATR, SL={p['sl_mult']} ATR, Start={p.get('start_time', 1000)} IST, TP1={p['tp1_r']}R, TP2={p['tp2_r']}R\n")
        else:
            print(f"  -> {sym}: No profitable edge found within daytime constraint.\n")
            
    print("\n" + "=" * 105)
    print("FINAL QUANTITATIVE RANKING: TOP 5 PERFORMING DECOUPLED ASSETS (BY PROFIT FACTOR & CAGR)")
    print("=" * 105)
    
    ranked = sorted(best_results, key=lambda x: (x['pf'], x['cagr']), reverse=True)
    
    print(f"{'Rank':<5} {'Symbol':<10} {'CAGR %':<12} {'Max DD %':<10} {'Profit Factor':<15} {'Win Rate %':<12} {'Sharpe':<8} {'Calmar':<8} {'Trades/Mo':<10}")
    print("-" * 105)
    for i, r in enumerate(ranked):
        badge = " 🏆 [TOP 5]" if i < 5 else ""
        cagr_str = f"+{r['cagr']:.2f}%" if r['cagr'] >= 0 else f"{r['cagr']:.2f}%"
        print(f"#{i+1:<4} {r['symbol']:<10} {cagr_str:<12} {r['max_dd']:<10.2f} {r['pf']:<15.2f} {r['win_rate']:<12.1f} {r['sharpe']:<8.2f} {r['calmar']:<8.2f} {r['trades_month']:<10.1f}{badge}")
        
    print("=" * 105)

if __name__ == '__main__':
    main()

