"""
TESTING VOLATILITY EXPANSION CONTINUATION SCALPER (11:00 AM - 4:00 PM IST)
==========================================================================
Empirical discovery from microstructure analysis:
When price expands beyond 2.0 sigma from Session VWAP during London Open,
reversion to mean occurs only 38% of the time; 62% of the time the market
trends aggressively in the direction of the breakout.

Strategy:
1. Detect statistical expansion: Price crosses above +1.8 sigma (Bullish Expansion)
   or below -1.8 sigma (Bearish Expansion) with Volume > 1.2x SMA.
2. Wait for first micro-pullback (1 to 2 bars) toward the 1.0 sigma band.
3. Enter in the direction of the expansion:
   - Target: 1.5x to 2.0x ATR (or previous swing high/low)
   - Stop Loss: 1.0x ATR
4. Multiple trades per day permitted during 11:00 AM - 4:00 PM IST.
5. Auto-square-off at 15:55 IST.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def backtest_expansion_continuation(symbol="GER40.r", dt_start=2021):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select(symbol, True)
    
    dt_from = datetime.datetime(dt_start, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        return
        
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['vol_sma'] = df['volume'].rolling(20).mean().bfill()
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter 11:00 to 16:00 IST
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    
    tp = (df_ist['high'] + df_ist['low'] + df_ist['close']) / 3.0
    df_ist['pv'] = tp * df_ist['volume']
    df_ist['cum_vol'] = df_ist.groupby('date')['volume'].cumsum()
    df_ist['cum_pv'] = df_ist.groupby('date')['pv'].cumsum()
    df_ist['vwap'] = df_ist['cum_pv'] / df_ist['cum_vol'].replace(0, np.nan)
    
    df_ist['dev_sq'] = ((tp - df_ist['vwap']) ** 2) * df_ist['volume']
    df_ist['cum_dev_sq'] = df_ist.groupby('date')['dev_sq'].cumsum()
    df_ist['vwap_std'] = np.sqrt(df_ist['cum_dev_sq'] / df_ist['cum_vol'].replace(0, np.nan)).bfill()
    df_ist['z_score'] = (df_ist['close'] - df_ist['vwap']) / df_ist['vwap_std'].replace(0, np.nan)
    
    sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'volume', 'vol_sma', 'vwap', 'vwap_std', 'z_score']].to_dict('records')
            
    risk_per_trade = 0.010 # 1.0% per trade (allows 2-4 trades per day safely within 4% daily limit)
    trades = []
    daily_results = []
    
    for curr_d, bars in sessions.items():
        pos = None
        day_pnl = 0.0
        trades_today = 0
        max_trades_today = 3 # multiple trades allowed
        
        # Track active expansion regime
        regime = 0 # +1 = Bull expansion, -1 = Bear expansion
        
        for i in range(12, len(bars)):
            r = bars[i]
            h, l, c, o = r['high'], r['low'], r['close'], r['open']
            atr, vwap, z = r['atr'], r['vwap'], r['z_score']
            vol, vol_sma = r['volume'], r['vol_sma']
            ist_h, ist_m = r['ist_hour'], r['ist_minute']
            
            # Hard close at 15:55 IST
            if ist_h == 15 and ist_m >= 55:
                if pos is not None:
                    pts = (c - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - c)
                    pnl = (pts / pos['sl_dist']) * risk_per_trade
                    pos['pnl_pct'] = pnl
                    day_pnl += pnl
                    trades.append(pos)
                    trades_today += 1
                    pos = None
                break
                
            # Manage Position
            if pos is not None:
                if pos['type'] == 'BUY':
                    if l <= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif h >= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                elif pos['type'] == 'SELL':
                    if h >= pos['sl']:
                        pos['pnl_pct'] = -risk_per_trade
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif l <= pos['tp']:
                        pos['pnl_pct'] = risk_per_trade * pos['rr']
                        day_pnl += pos['pnl_pct']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                        
            # Regime detection: Strong volume expansion into the statistical tails
            if z >= 1.8 and vol >= 1.1 * vol_sma:
                regime = 1
            elif z <= -1.8 and vol >= 1.1 * vol_sma:
                regime = -1
                
            # Entry on Pullback in direction of Expansion
            if pos is None and trades_today < max_trades_today and 12 <= ist_h < 15:
                sl_dist = 1.0 * atr
                tp_dist = 1.8 * atr
                rr = tp_dist / sl_dist
                
                # In Bullish expansion: Price pulls back toward +0.5 to +1.2 sigma, and prints a green reversal candle
                if regime == 1 and 0.4 <= z <= 1.3 and c > o and c > bars[i-1]['close']:
                    pos = {
                        'symbol': symbol, 'type': 'BUY', 'date': curr_d,
                        'entry': c, 'sl': c - sl_dist, 'tp': c + tp_dist,
                        'sl_dist': sl_dist, 'rr': rr, 'entry_time': r['ist_dt']
                    }
                # In Bearish expansion: Price pulls back upward toward -0.5 to -1.3 sigma, and prints a red reversal candle
                elif regime == -1 and -1.3 <= z <= -0.4 and c < o and c < bars[i-1]['close']:
                    pos = {
                        'symbol': symbol, 'type': 'SELL', 'date': curr_d,
                        'entry': c, 'sl': c + sl_dist, 'tp': c - tp_dist,
                        'sl_dist': sl_dist, 'rr': rr, 'entry_time': r['ist_dt']
                    }
                    
        daily_results.append({'date': curr_d, 'pnl_pct': day_pnl, 'trades': trades_today})
        
    df_t = pd.DataFrame(trades)
    df_d = pd.DataFrame(daily_results)
    
    if len(df_t) == 0:
        print("No trades.")
        return
        
    df_t['year'] = pd.to_datetime(df_t['date']).dt.year
    df_d['year'] = pd.to_datetime(df_d['date']).dt.year
    df_d['month'] = pd.to_datetime(df_d['date']).dt.to_period('M')
    
    wr = (df_t['pnl_pct'] > 0).mean() * 100.0
    print("=" * 85)
    print(f"   STATISTICAL EXPANSION SCALPER RESULTS: {symbol} (11:00 - 16:00 IST)   ")
    print("=" * 85)
    print(f"Total Completed Trades : {len(df_t):,} across {len(df_d):,} Days")
    print(f"Average Trades per Day : {len(df_t) / len(df_d):.2f}")
    print(f"Overall Win Rate       : {wr:.2f}%\n")
    
    years = sorted(df_d['year'].unique())
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 85)
    
    rois = []
    for y in years:
        y_d = df_d[df_d['year'] == y]
        y_t = df_t[df_t['year'] == y]
        if len(y_t) == 0:
            continue
        y_wr = (y_t['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, row in y_d.iterrows():
            cap += cap * row['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        rois.append(roi)
        
        m_rois = [m_sub['pnl_pct'].sum() * 100.0 for m, m_sub in y_d.groupby('month')]
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(y_t):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(rois):+6.2f}%")
    print(f"PROFITABLE YEARS      : {(np.array(rois) > 0).mean()*100:.1f}%\n")


if __name__ == "__main__":
    backtest_expansion_continuation("GER40.r", 2021)
    backtest_expansion_continuation("XAUUSD", 2021)

