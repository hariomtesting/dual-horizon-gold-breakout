"""
EMPIRICAL TEST: RECURRING OCO BRACKET WITH 5-MINUTE RE-ARM
==========================================================
User Idea:
1. Place 1 Buy and 1 Sell order at a fixed distance (offset) from current price.
2. Both orders have TP and SL.
3. When one order triggers, the other gets deleted (OCO Bracket).
4. After hitting TP or SL, wait 5 minutes, then place new orders from current price.
5. Operating window: 11:00 AM to 16:00 IST (Auto-square-off at 15:55 IST).

Tested Models:
- Model A: Breakout Stop Bracket (Buy Stop above + Sell Stop below)
- Model B: Fading Limit Bracket (Sell Limit above + Buy Limit below)
- Model C: Trend-Filtered Breakout Bracket (Only place order aligned with 200 EMA)

Full Live Market Simulator Frictions:
- Vantage Live Spread: $0.30 per oz
- Commission: $6.00/lot ($0.06/oz)
- Latency Slippage: 345ms ($0.15 entry for stops, $0.00 for maker limits, $0.08 exits)
- Hard auto square-off at 15:55 IST
- Daily circuit breaker: -2.00% max daily loss
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M5, dt_from, dt_to)
    mt5.shutdown()
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    df_day = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 40:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_continuous_bracket_signal_fn(
    order_type="BREAKOUT", # "BREAKOUT" (Buy Stop above, Sell Stop below) or "FADE_LIMIT" (Sell Limit above, Buy Limit below)
    offset_dollars=2.50,
    sl_dollars=6.00,
    rr_multiplier=2.0,
    trend_filter=False,
    cooldown_bars=1 # 1 bar on M5 = 5 minutes
):
    tp_dollars = sl_dollars * rr_multiplier
    
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        # Stop taking new setups after 15:00 IST to allow trades to finish before 15:55 IST
        if h >= 15:
            return None
            
        if 'phase' not in state:
            state['phase'] = 'READY'
            state['base'] = None
            state['ema'] = None
            state['cooldown_until'] = 0
            state['last_exit_idx'] = -1
            
        # Check 5-minute cooldown
        if i < state['cooldown_until']:
            return None
            
        # State 1: READY -> Arm new bracket from current price
        if state['phase'] == 'READY':
            state['base'] = row['close']
            state['ema'] = row['ema200']
            state['phase'] = 'ARMED'
            return None
            
        # State 2: ARMED -> Check if either order is triggered
        elif state['phase'] == 'ARMED':
            base = state['base']
            ema = state['ema']
            
            allow_buy = (base >= ema) if trend_filter else True
            allow_sell = (base <= ema) if trend_filter else True
            
            if order_type == "BREAKOUT":
                # Buy Stop above base, Sell Stop below base
                buy_trig = base + offset_dollars
                sell_trig = base - offset_dollars
                
                if allow_buy and row['high'] >= buy_trig:
                    state['phase'] = 'READY' # After trade finishes, arm again
                    state['cooldown_until'] = i + cooldown_bars + 1
                    return {'type': 'BUY_STOP', 'trigger': buy_trig, 'sl': buy_trig - sl_dollars, 'tp': buy_trig + tp_dollars}
                elif allow_sell and row['low'] <= sell_trig:
                    state['phase'] = 'READY'
                    state['cooldown_until'] = i + cooldown_bars + 1
                    return {'type': 'SELL_STOP', 'trigger': sell_trig, 'sl': sell_trig + sl_dollars, 'tp': sell_trig - tp_dollars}
                    
            elif order_type == "FADE_LIMIT":
                # Buy Limit below base, Sell Limit above base
                buy_limit = base - offset_dollars
                sell_limit = base + offset_dollars
                
                if allow_buy and row['low'] <= buy_limit:
                    state['phase'] = 'READY'
                    state['cooldown_until'] = i + cooldown_bars + 1
                    return {'type': 'BUY_LIMIT', 'trigger': buy_limit, 'sl': buy_limit - sl_dollars, 'tp': buy_limit + tp_dollars}
                elif allow_sell and row['high'] >= sell_limit:
                    state['phase'] = 'READY'
                    state['cooldown_until'] = i + cooldown_bars + 1
                    return {'type': 'SELL_LIMIT', 'trigger': sell_limit, 'sl': sell_limit + sl_dollars, 'tp': sell_limit - tp_dollars}
                    
        return None
    return signal_fn

def main():
    print("Loading 5-year Gold 5-minute data from MT5...")
    sessions = load_data()
    print(f"Loaded {len(sessions)} trading sessions. Testing Continuous OCO Bracket Strategy with 5-min Re-Arm...\n")
    
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    results = []
    
    # -------------------------------------------------------------
    # SWEEP 1: BREAKOUT STOP BRACKET (Buy Stop Above, Sell Stop Below)
    # -------------------------------------------------------------
    print("Evaluating Suite 1: Breakout Stop Bracket (Continuous)...")
    for offset in [1.50, 2.50, 3.50, 5.00]:
        for sl in [5.00, 7.50, 10.00]:
            for rr in [1.5, 2.0, 2.5]:
                for trend in [False, True]:
                    fn = make_continuous_bracket_signal_fn(
                        order_type="BREAKOUT",
                        offset_dollars=offset,
                        sl_dollars=sl,
                        rr_multiplier=rr,
                        trend_filter=trend,
                        cooldown_bars=1
                    )
                    rep = sim.run_bars(sessions, fn)
                    results.append({
                        'type': 'Breakout Stop' + (' (Trend)' if trend else ' (Bi-Dir)'),
                        'offset': offset, 'sl': sl, 'rr': rr,
                        'roi': rep['avg_annual_roi'],
                        'worst_yr': rep['worst_year_roi'],
                        'max_dd': rep['max_drawdown_pct'],
                        'green_m': rep['green_months'],
                        'm2026': rep['consistency_2026'],
                        'pf': rep['profit_factor'],
                        'wr': rep['win_rate'],
                        'trades': rep['total_trades'],
                        'toll': rep['total_broker_toll_paid']
                    })

    # -------------------------------------------------------------
    # SWEEP 2: FADING LIMIT BRACKET (Sell Limit Above, Buy Limit Below)
    # -------------------------------------------------------------
    print("Evaluating Suite 2: Fading Limit Bracket (Continuous)...")
    for offset in [2.50, 3.50, 5.00]:
        for sl in [5.00, 7.50, 10.00]:
            for rr in [1.0, 1.5, 2.0]:
                for trend in [False, True]:
                    fn = make_continuous_bracket_signal_fn(
                        order_type="FADE_LIMIT",
                        offset_dollars=offset,
                        sl_dollars=sl,
                        rr_multiplier=rr,
                        trend_filter=trend,
                        cooldown_bars=1
                    )
                    rep = sim.run_bars(sessions, fn)
                    results.append({
                        'type': 'Fade Limit' + (' (Trend)' if trend else ' (Bi-Dir)'),
                        'offset': offset, 'sl': sl, 'rr': rr,
                        'roi': rep['avg_annual_roi'],
                        'worst_yr': rep['worst_year_roi'],
                        'max_dd': rep['max_drawdown_pct'],
                        'green_m': rep['green_months'],
                        'm2026': rep['consistency_2026'],
                        'pf': rep['profit_factor'],
                        'wr': rep['win_rate'],
                        'trades': rep['total_trades'],
                        'toll': rep['total_broker_toll_paid']
                    })

    df_res = pd.DataFrame(results)
    
    print("\n" + "=" * 145)
    print("CONTINUOUS OCO BRACKET RE-ARM RESULTS (EVALUATED UNDER FULL LIVE VANTAGE FEES & SLIPPAGE)")
    print("=" * 145)
    print(f"{'TYPE':<25} | {'OFFSET':<8} | {'SL':<6} | {'RR':<5} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'TOLL PAID'}")
    print("-" * 145)
    
    top_models = df_res.sort_values(by='roi', ascending=False)
    for idx, r in top_models.head(20).iterrows():
        print(f"{r['type']:<25} | ${r['offset']:<7.2f} | ${r['sl']:<5.2f} | {r['rr']:<5.1f} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | {r['trades']:<6} | ${r['toll']:,.2f}")
    print("=" * 145)

    print("\nBOTTOM 5 (WORST PERFORMERS - FRICTION & WHIPSAW TRAPS):")
    for idx, r in top_models.tail(5).iterrows():
        print(f"{r['type']:<25} | ${r['offset']:<7.2f} | ${r['sl']:<5.2f} | {r['rr']:<5.1f} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | {r['trades']:<6} | ${r['toll']:,.2f}")

if __name__ == "__main__":
    main()

