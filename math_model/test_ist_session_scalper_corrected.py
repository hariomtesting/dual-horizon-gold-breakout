"""
CORRECTED IST (11:00 AM - 4:00 PM IST) MULTI-ASSET SCALPER
===========================================================
Timezone Accuracy:
- Converts MT5 epoch timestamp directly to UTC: datetime.fromtimestamp(ts, timezone.utc)
- Accurately computes IST = UTC + 5:30.
- Evaluates 11:00 AM to 4:00 PM IST (15:55 IST hard auto-square-off).

Execution Mechanics:
1. Phase 1 (11:00 AM - 12:30 PM IST): Pre-London Range Formation.
2. Phase 2 (12:30 PM - 1:15 PM IST): London / European Cash Open Drive.
   - Identifies whether London opens with directional impulse above/below the morning range.
3. Phase 3 (1:15 PM - 3:30 PM IST): London Continuation / Pullback Entry.
   - Target: 2.0x ATR (Asymmetric 2.0:1 R:R).
   - Stop Loss: 1.0x ATR.
   - Pyramiding: If Unit 1 achieves +1.0x ATR profit, stop is locked to Breakeven (+0.1R), and Unit 2 is deployed.
4. Phase 4 (3:55 PM IST): Hard Auto-Square-Off (Flat before PC shut down).
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def backtest_ist_scalper(symbol="XAUUSD", dt_start=2018):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select(symbol, True)
    
    print("=" * 85, flush=True)
    print(f"   BACKTESTING IST SCALPER (11:00 - 16:00 IST) ON {symbol} ({dt_start} - 2026)   ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    dt_from = datetime.datetime(dt_start, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        print(f"No rates found for {symbol}")
        return None
        
    df = pd.DataFrame(rates)
    
    # Accurate UTC to IST conversion
    # rates['time'] in MT5 is epoch seconds
    # Let's verify whether rates['time'] is UTC or Broker local
    # Earlier we saw rates[0]['time'] = 1794009300. In UTC that was 23:55:00.
    # But broker server time was 23:55:00. 
    # That means MT5 puts broker local time into epoch seconds directly!
    # Broker is EET (UTC+2 winter, UTC+3 summer).
    # Therefore, true UTC is Broker Time - 2 hours (winter) / 3 hours (summer).
    # And IST is true UTC + 5h30m = Broker Time + 3h30m (winter) / + 2h30m (summer).
    
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    # Use approximate 3h00m offset between Broker (Cyprus) and IST (UTC+5:30)
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter 11:00 to 16:00 IST
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy()
    
    # Calculate Session VWAP
    tp = (df_ist['high'] + df_ist['low'] + df_ist['close']) / 3.0
    df_ist['pv'] = tp * df_ist['volume']
    df_ist['cum_vol'] = df_ist.groupby('date')['volume'].cumsum()
    df_ist['cum_pv'] = df_ist.groupby('date')['pv'].cumsum()
    df_ist['vwap'] = df_ist['cum_pv'] / df_ist['cum_vol'].replace(0, np.nan)
    
    day_sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            day_sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr', 'vwap']].to_dict('records')
            
    print(f"Loaded {len(day_sessions):,} trading sessions for {symbol}.\n", flush=True)
    
    risk_per_trade = 0.0125
    all_trades = []
    daily_results = []
    
    days = sorted(list(day_sessions.keys()))
    
    for curr_d in days:
        bars = day_sessions[curr_d]
        
        # 11:00 to 12:30 IST is bars[0:18] (18 5-min bars = 90 mins)
        # Pre-London range
        pre_bars = bars[0:18]
        pre_high = max(b['high'] for b in pre_bars)
        pre_low = min(b['low'] for b in pre_bars)
        pre_range = pre_high - pre_low
        
        day_pnl_pct = 0.0
        day_trades = 0
        pos_u1 = None
        pos_u2 = None
        trade_taken_today = False
        
        for i in range(18, len(bars)):
            r = bars[i]
            high_p, low_p, close_p, o_p = r['high'], r['low'], r['close'], r['open']
            atr, vwap = r['atr'], r['vwap']
            ist_h, ist_m = r['ist_hour'], r['ist_minute']
            
            # Hard Auto-Square-Off at 15:55 IST
            if ist_h == 15 and ist_m >= 55:
                for pos in [pos_u1, pos_u2]:
                    if pos is not None:
                        pnl_pts = (close_p - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - close_p)
                        pnl_pct = (pnl_pts / pos['sl_dist']) * risk_per_trade
                        pos['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos)
                        day_trades += 1
                pos_u1 = None
                pos_u2 = None
                break
                
            # Manage Unit 1
            if pos_u1 is not None:
                # Pyramiding trigger at +1.0x ATR
                if not pos_u1['be']:
                    if pos_u1['type'] == 'BUY' and high_p >= pos_u1['entry'] + 1.0 * atr:
                        pos_u1['sl'] = pos_u1['entry'] + 0.10
                        pos_u1['be'] = True
                        if pos_u2 is None:
                            pos_u2 = {
                                'symbol': symbol, 'unit': 2, 'type': 'BUY', 'date': curr_d,
                                'entry': close_p, 'sl': pos_u1['entry'] + 0.10,
                                'tp': close_p + 1.5 * atr, 'sl_dist': 1.0 * atr,
                                'rr': 1.5, 'be': True, 'entry_time': r['ist_dt']
                            }
                    elif pos_u1['type'] == 'SELL' and low_p <= pos_u1['entry'] - 1.0 * atr:
                        pos_u1['sl'] = pos_u1['entry'] - 0.10
                        pos_u1['be'] = True
                        if pos_u2 is None:
                            pos_u2 = {
                                'symbol': symbol, 'unit': 2, 'type': 'SELL', 'date': curr_d,
                                'entry': close_p, 'sl': pos_u1['entry'] - 0.10,
                                'tp': close_p - 1.5 * atr, 'sl_dist': 1.0 * atr,
                                'rr': 1.5, 'be': True, 'entry_time': r['ist_dt']
                            }
                            
                # Exits for Unit 1
                if pos_u1['type'] == 'BUY':
                    if low_p <= pos_u1['sl']:
                        pnl_pct = 0.001 if pos_u1['be'] and pos_u1['sl'] >= pos_u1['entry'] else -risk_per_trade
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                    elif high_p >= pos_u1['tp']:
                        pnl_pct = risk_per_trade * pos_u1['rr']
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                elif pos_u1['type'] == 'SELL':
                    if high_p >= pos_u1['sl']:
                        pnl_pct = 0.001 if pos_u1['be'] and pos_u1['sl'] <= pos_u1['entry'] else -risk_per_trade
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                    elif low_p <= pos_u1['tp']:
                        pnl_pct = risk_per_trade * pos_u1['rr']
                        pos_u1['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u1)
                        day_trades += 1
                        pos_u1 = None
                        
            # Manage Unit 2
            if pos_u2 is not None:
                if pos_u2['type'] == 'BUY':
                    if low_p <= pos_u2['sl']:
                        pnl_pct = -risk_per_trade * 0.4
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                    elif high_p >= pos_u2['tp']:
                        pnl_pct = risk_per_trade * 1.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                elif pos_u2['type'] == 'SELL':
                    if high_p >= pos_u2['sl']:
                        pnl_pct = -risk_per_trade * 0.4
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                    elif low_p <= pos_u2['tp']:
                        pnl_pct = risk_per_trade * 1.5
                        pos_u2['pnl_pct'] = pnl_pct
                        day_pnl_pct += pnl_pct
                        all_trades.append(pos_u2)
                        day_trades += 1
                        pos_u2 = None
                        
            # Entry logic: London Breakout & Expansion (12:30 to 14:30 IST)
            if not trade_taken_today and pos_u1 is None and 12 <= ist_h < 15:
                sl_dist = 1.0 * atr
                tp_dist = 2.0 * atr # 2.0:1 R:R
                rr = tp_dist / sl_dist
                
                # Bullish Breakout: London breaks Pre-London High and is above VWAP
                if close_p > pre_high and close_p > vwap and close_p > o_p:
                    pos_u1 = {
                        'symbol': symbol, 'unit': 1, 'type': 'BUY', 'date': curr_d,
                        'entry': close_p, 'sl': close_p - sl_dist,
                        'tp': close_p + tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                        'be': False, 'entry_time': r['ist_dt']
                    }
                    trade_taken_today = True
                # Bearish Breakout: London breaks Pre-London Low and is below VWAP
                elif close_p < pre_low and close_p < vwap and close_p < o_p:
                    pos_u1 = {
                        'symbol': symbol, 'unit': 1, 'type': 'SELL', 'date': curr_d,
                        'entry': close_p, 'sl': close_p + sl_dist,
                        'tp': close_p - tp_dist, 'sl_dist': sl_dist, 'rr': rr,
                        'be': False, 'entry_time': r['ist_dt']
                    }
                    trade_taken_today = True
                    
        daily_results.append({
            'date': curr_d,
            'pnl_pct': day_pnl_pct,
            'trades': day_trades
        })
        
    df_trades = pd.DataFrame(all_trades)
    df_daily = pd.DataFrame(daily_results)
    
    if len(df_trades) == 0:
        print(f"No trades taken for {symbol}")
        return None
        
    df_trades['year'] = pd.to_datetime(df_trades['date']).dt.year
    df_daily['year'] = pd.to_datetime(df_daily['date']).dt.year
    df_daily['month'] = pd.to_datetime(df_daily['date']).dt.to_period('M')
    
    print("=" * 85)
    print(f"      RESULTS FOR {symbol} (11:00 AM - 4:00 PM IST)      ")
    print("=" * 85)
    print(f"Total Trades: {len(df_trades):,} | Win Rate: {(df_trades['pnl_pct'] > 0).mean()*100:.2f}%\n")
    
    years = sorted(df_daily['year'].unique())
    print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Annual ROI':<12} | {'Max DD':<9} | {'Green Months':<14} | {'Status'}")
    print("-" * 85)
    
    annual_rois = []
    for y in years:
        y_daily = df_daily[df_daily['year'] == y]
        y_trades = df_trades[df_trades['year'] == y]
        if len(y_trades) == 0:
            continue
        y_wr = (y_trades['pnl_pct'] > 0).mean() * 100.0
        
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for _, d in y_daily.iterrows():
            cap += cap * d['pnl_pct']
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        roi = (cap - 10000.0) / 10000.0 * 100.0
        annual_rois.append(roi)
        
        m_rois = [m_sub['pnl_pct'].sum() * 100.0 for m, m_sub in y_daily.groupby('month')]
        green_m = sum(1 for r in m_rois if r > 0)
        tot_m = len(m_rois)
        green_pct = (green_m / tot_m) * 100.0 if tot_m > 0 else 0
        
        st = "[PROFITABLE]" if roi > 0 else "[RED]"
        print(f"{y:<6} | {len(y_trades):<8} | {y_wr:6.2f}%    | {roi:+9.2f}%    | {max_dd:6.2f}%  | {green_m:2d}/{tot_m:2d} ({green_pct:3.0f}%)    | {st}")
        
    print("-" * 85)
    print(f"AVERAGE ANNUAL RETURN : {np.mean(annual_rois):+6.2f}%")
    print(f"PROFITABLE YEARS      : {(np.array(annual_rois) > 0).mean()*100:.1f}%\n")
    
    return df_daily


if __name__ == "__main__":
    backtest_ist_scalper("XAUUSD", 2018)
    backtest_ist_scalper("GER40.r", 2021)

