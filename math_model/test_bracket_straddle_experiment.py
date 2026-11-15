"""
BRACKET ORDER EXPERIMENT: BREAKOUT STRADDLE vs LIMIT REVERSAL (XAUUSD & EURUSD)
================================================================================
Investigates the User's Idea: Placing Pending Orders Above and Below the Current Price
1. Interpretation A: Breakout Straddle (Buy Stop Above + Sell Stop Below)
   - Wait for consolidation / morning base.
   - Place Buy Stop above current price, Sell Stop below current price.
   - If market breaks out, enter in direction of breakout.
2. Interpretation B: Grid / Fading Limits (Sell Limit Above + Buy Limit Below)
   - Place Sell Limit above current price, Buy Limit below current price.
   - When market spikes up, sell; when market dips down, buy.

Operating Window: 11:00 AM to 4:00 PM IST (Auto-square-off at 15:55 IST).
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def test_bracket_orders(symbol="XAUUSD", dt_start=2022):
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
    
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    df_ist = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_ist.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub[['broker_dt', 'ist_dt', 'ist_hour', 'ist_minute', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
            
    print("=" * 85, flush=True)
    print(f"      TESTING BRACKET PENDING ORDERS ON {symbol} (11:00 - 16:00 IST)      ", flush=True)
    print("=" * 85 + "\n", flush=True)
    
    # -------------------------------------------------------------
    # MODEL 1: BREAKOUT STRADDLE (Buy Stop Above + Sell Stop Below)
    # -------------------------------------------------------------
    # At 12:30 PM IST (London/European cash open), measure price and ATR.
    # Place Buy Stop at Price + 0.6*ATR, Sell Stop at Price - 0.6*ATR.
    # Target: 1.5*ATR (2.5:1 R:R), Stop: 0.6*ATR (the entry price of the other side)
    
    trades_m1 = []
    trades_m2 = []
    
    risk_per_trade = 0.010 # 1.0%
    
    for curr_d, bars in sessions.items():
        if len(bars) < 25:
            continue
            
        # Bar 18 is 12:30 PM IST
        base_bar = bars[18]
        p_base = base_bar['close']
        atr_base = base_bar['atr']
        
        # -----------------------------
        # Simulate Model 1: Breakout
        # -----------------------------
        buy_trigger = p_base + 0.5 * atr_base
        sell_trigger = p_base - 0.5 * atr_base
        
        pos_m1 = None
        for i in range(19, len(bars)):
            r = bars[i]
            h, l, c = r['high'], r['low'], r['close']
            
            # Auto square off at 15:55 IST
            if r['ist_hour'] == 15 and r['ist_minute'] >= 55:
                if pos_m1 is not None:
                    pts = (c - pos_m1['entry']) if pos_m1['type'] == 'BUY' else (pos_m1['entry'] - c)
                    pnl = (pts / pos_m1['sl_dist']) * risk_per_trade
                    pos_m1['pnl_pct'] = pnl
                    trades_m1.append(pos_m1)
                    pos_m1 = None
                break
                
            if pos_m1 is not None:
                if pos_m1['type'] == 'BUY':
                    if l <= pos_m1['sl']:
                        pos_m1['pnl_pct'] = -risk_per_trade
                        trades_m1.append(pos_m1)
                        pos_m1 = None
                        break
                    elif h >= pos_m1['tp']:
                        pos_m1['pnl_pct'] = risk_per_trade * pos_m1['rr']
                        trades_m1.append(pos_m1)
                        pos_m1 = None
                        break
                elif pos_m1['type'] == 'SELL':
                    if h >= pos_m1['sl']:
                        pos_m1['pnl_pct'] = -risk_per_trade
                        trades_m1.append(pos_m1)
                        pos_m1 = None
                        break
                    elif l <= pos_m1['tp']:
                        pos_m1['pnl_pct'] = risk_per_trade * pos_m1['rr']
                        trades_m1.append(pos_m1)
                        pos_m1 = None
                        break
            else:
                # Check pending triggers
                if h >= buy_trigger:
                    sl_dist = 0.8 * atr_base
                    tp_dist = 1.6 * atr_base # 2.0:1 R:R
                    pos_m1 = {
                        'type': 'BUY', 'date': curr_d, 'entry': buy_trigger,
                        'sl': buy_trigger - sl_dist, 'tp': buy_trigger + tp_dist,
                        'sl_dist': sl_dist, 'rr': tp_dist / sl_dist
                    }
                elif l <= sell_trigger:
                    sl_dist = 0.8 * atr_base
                    tp_dist = 1.6 * atr_base
                    pos_m1 = {
                        'type': 'SELL', 'date': curr_d, 'entry': sell_trigger,
                        'sl': sell_trigger + sl_dist, 'tp': sell_trigger - tp_dist,
                        'sl_dist': sl_dist, 'rr': tp_dist / sl_dist
                    }
                    
        # -----------------------------
        # Simulate Model 2: Fade Limit
        # -----------------------------
        # Place Sell Limit at p_base + 1.0*ATR, Buy Limit at p_base - 1.0*ATR
        # Target: Return to p_base (1.0*ATR), Stop: 1.0*ATR further
        sell_limit = p_base + 1.0 * atr_base
        buy_limit = p_base - 1.0 * atr_base
        
        pos_m2 = None
        for i in range(19, len(bars)):
            r = bars[i]
            h, l, c = r['high'], r['low'], r['close']
            
            if r['ist_hour'] == 15 and r['ist_minute'] >= 55:
                if pos_m2 is not None:
                    pts = (c - pos_m2['entry']) if pos_m2['type'] == 'BUY' else (pos_m2['entry'] - c)
                    pnl = (pts / pos_m2['sl_dist']) * risk_per_trade
                    pos_m2['pnl_pct'] = pnl
                    trades_m2.append(pos_m2)
                    pos_m2 = None
                break
                
            if pos_m2 is not None:
                if pos_m2['type'] == 'BUY':
                    if l <= pos_m2['sl']:
                        pos_m2['pnl_pct'] = -risk_per_trade
                        trades_m2.append(pos_m2)
                        pos_m2 = None
                        break
                    elif h >= pos_m2['tp']:
                        pos_m2['pnl_pct'] = risk_per_trade * pos_m2['rr']
                        trades_m2.append(pos_m2)
                        pos_m2 = None
                        break
                elif pos_m2['type'] == 'SELL':
                    if h >= pos_m2['sl']:
                        pos_m2['pnl_pct'] = -risk_per_trade
                        trades_m2.append(pos_m2)
                        pos_m2 = None
                        break
                    elif l <= pos_m2['tp']:
                        pos_m2['pnl_pct'] = risk_per_trade * pos_m2['rr']
                        trades_m2.append(pos_m2)
                        pos_m2 = None
                        break
            else:
                # Sell Limit trigger: Price spikes up to p_base + 1.0 ATR
                if h >= sell_limit:
                    sl_dist = 1.0 * atr_base
                    tp_dist = 1.0 * atr_base # Target back to p_base (1:1 R:R)
                    pos_m2 = {
                        'type': 'SELL', 'date': curr_d, 'entry': sell_limit,
                        'sl': sell_limit + sl_dist, 'tp': sell_limit - tp_dist,
                        'sl_dist': sl_dist, 'rr': 1.0
                    }
                # Buy Limit trigger: Price dips down to p_base - 1.0 ATR
                elif l <= buy_limit:
                    sl_dist = 1.0 * atr_base
                    tp_dist = 1.0 * atr_base
                    pos_m2 = {
                        'type': 'BUY', 'date': curr_d, 'entry': buy_limit,
                        'sl': buy_limit - sl_dist, 'tp': buy_limit + tp_dist,
                        'sl_dist': sl_dist, 'rr': 1.0
                    }
                    
    # Results Analysis
    df1 = pd.DataFrame(trades_m1)
    df2 = pd.DataFrame(trades_m2)
    
    print(f"--- MODEL 1: BREAKOUT STRADDLE (BUY STOP ABOVE / SELL STOP BELOW) on {symbol} ---")
    if len(df1) > 0:
        df1['year'] = pd.to_datetime(df1['date']).dt.year
        wr1 = (df1['pnl_pct'] > 0).mean() * 100.0
        roi1 = df1['pnl_pct'].sum() * 100.0
        print(f"Total Trades: {len(df1):,} | Win Rate: {wr1:.2f}% | Total PnL: {roi1:+6.2f}%")
        for y in sorted(df1['year'].unique()):
            sub = df1[df1['year'] == y]
            print(f"   Year {y}: Trades={len(sub):<4} WR={(sub['pnl_pct']>0).mean()*100:5.1f}% ROI={sub['pnl_pct'].sum()*100:+6.2f}%")
            
    print(f"\n--- MODEL 2: FADE LIMIT BRACKET (SELL LIMIT ABOVE / BUY LIMIT BELOW) on {symbol} ---")
    if len(df2) > 0:
        df2['year'] = pd.to_datetime(df2['date']).dt.year
        wr2 = (df2['pnl_pct'] > 0).mean() * 100.0
        roi2 = df2['pnl_pct'].sum() * 100.0
        print(f"Total Trades: {len(df2):,} | Win Rate: {wr2:.2f}% | Total PnL: {roi2:+6.2f}%")
        for y in sorted(df2['year'].unique()):
            sub = df2[df2['year'] == y]
            print(f"   Year {y}: Trades={len(sub):<4} WR={(sub['pnl_pct']>0).mean()*100:5.1f}% ROI={sub['pnl_pct'].sum()*100:+6.2f}%")
    print("\n" + "=" * 85 + "\n")


if __name__ == "__main__":
    test_bracket_orders("XAUUSD", 2022)
    test_bracket_orders("EURUSD", 2022)

