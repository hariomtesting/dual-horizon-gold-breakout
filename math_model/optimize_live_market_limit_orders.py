"""
LIVE MARKET OPTIMIZATION: PENDING LIMIT & STOP ORDER ARCHITECTURES
==================================================================
Optimizing the Dual-Session Pending Order Strategy to survive & thrive
in the REAL live market (Vantage spreads, commissions, 345ms latency).

Order Architectures Tested:
1. Breakout Stop Orders (Anchor 12:00 Frankfurt & 13:00 London)
2. Mean-Reversion Limit Orders (Fade Spikes: Sell Limit Above, Buy Limit Below)
3. Trend Retest Limit Orders (Breakout confirmed -> Enter on Pullback with Limit Order)
4. Parameter Sweeps across Offsets ($1.00 to $4.00), SL ($5.00 to $12.00), and RR (1.5 to 3.0)
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
    
    # Precompute 14-period ATR
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Filter 05:30 to 16:00 IST
    df_day = df[(df['ist_hour'] >= 5) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 70:
            sub = sub.copy().reset_index(drop=True)
            asian_bars = sub[(sub['ist_hour'] < 11) | ((sub['ist_hour'] == 11) & (sub['ist_minute'] <= 30))]
            if len(asian_bars) >= 20:
                sub['asian_high'] = asian_bars['high'].max()
                sub['asian_low'] = asian_bars['low'].min()
                sub['asian_range'] = sub['asian_high'] - sub['asian_low']
                sessions[d] = sub.to_dict('records')
    return sessions

# -----------------------------------------------------------------
# 1. BREAKOUT STOP STRATEGY GENERATOR
# -----------------------------------------------------------------
def make_breakout_stop_fn(anchor_h=12, anchor_m=0, offset=1.50, sl=7.50, rr=2.0, cutoff_h=13, cutoff_m=30, trend_filter=True):
    tp = sl * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'armed' not in state:
            state['armed'] = False
            state['base'] = None
            state['ema'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        # Order Cutoff
        if h > cutoff_h or (h == cutoff_h and m >= cutoff_m):
            state['done'] = True
            return None
            
        # Arming at Anchor Time
        if not state['armed'] and h == anchor_h and m == anchor_m:
            state['armed'] = True
            state['base'] = row['close']
            state['ema'] = row['ema200']
            return None
            
        if state['armed'] and not state['done']:
            base = state['base']
            ema = state['ema']
            
            allow_buy = (base >= ema) if trend_filter else True
            allow_sell = (base <= ema) if trend_filter else True
            
            # Check Buy Stop
            if allow_buy and row['high'] >= base + offset:
                state['done'] = True
                trig = base + offset
                return {'type': 'BUY_STOP', 'trigger': trig, 'sl': trig - sl, 'tp': trig + tp}
            # Check Sell Stop
            elif allow_sell and row['low'] <= base - offset:
                state['done'] = True
                trig = base - offset
                return {'type': 'SELL_STOP', 'trigger': trig, 'sl': trig + sl, 'tp': trig - tp}
        return None
    return signal_fn

# -----------------------------------------------------------------
# 2. MEAN-REVERSION FADE LIMIT STRATEGY GENERATOR
# -----------------------------------------------------------------
def make_fade_limit_fn(anchor_h=12, anchor_m=0, offset=3.00, sl=6.00, tp=6.00, cutoff_h=14, cutoff_m=0):
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'armed' not in state:
            state['armed'] = False
            state['base'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        if h > cutoff_h or (h == cutoff_h and m >= cutoff_m):
            state['done'] = True
            return None
            
        if not state['armed'] and h == anchor_h and m == anchor_m:
            state['armed'] = True
            state['base'] = row['close']
            return None
            
        if state['armed'] and not state['done']:
            base = state['base']
            # Buy Limit placed below base
            buy_limit_p = base - offset
            # Sell Limit placed above base
            sell_limit_p = base + offset
            
            if row['low'] <= buy_limit_p:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': buy_limit_p, 'sl': buy_limit_p - sl, 'tp': buy_limit_p + tp}
            elif row['high'] >= sell_limit_p:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': sell_limit_p, 'sl': sell_limit_p + sl, 'tp': sell_limit_p - tp}
        return None
    return signal_fn

# -----------------------------------------------------------------
# 3. TREND RETEST LIMIT ORDER (Wait for Breakout, Enter via Limit on Retest)
# -----------------------------------------------------------------
def make_retest_limit_fn(anchor_h=12, anchor_m=0, break_offset=2.50, limit_retest_offset=1.00, sl=7.50, rr=2.5, cutoff_h=14, cutoff_m=0):
    tp = sl * rr
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        if 'phase' not in state:
            state['phase'] = 'WAIT_ANCHOR'
            state['base'] = None
            state['dir'] = None
            state['limit_price'] = None
            state['done'] = False
            
        if state['done']:
            return None
            
        if h > cutoff_h or (h == cutoff_h and m >= cutoff_m):
            state['done'] = True
            return None
            
        if state['phase'] == 'WAIT_ANCHOR' and h == anchor_h and m == anchor_m:
            state['phase'] = 'WAIT_BREAK'
            state['base'] = row['close']
            state['ema'] = row['ema200']
            return None
            
        if state['phase'] == 'WAIT_BREAK':
            base = state['base']
            ema = state['ema']
            if base >= ema and row['high'] >= base + break_offset:
                # Upward breakout confirmed! Set BUY_LIMIT on pullback
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'BUY'
                state['limit_price'] = base + limit_retest_offset
            elif base <= ema and row['low'] <= base - break_offset:
                # Downward breakout confirmed! Set SELL_LIMIT on pullback
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'SELL'
                state['limit_price'] = base - limit_retest_offset
                
        elif state['phase'] == 'WAIT_LIMIT_FILL':
            lp = state['limit_price']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': lp - sl, 'tp': lp + tp}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': lp + sl, 'tp': lp - tp}
        return None
    return signal_fn

def main():
    print("Loading 2022-2026 Gold data...")
    sessions = load_data()
    print(f"Loaded {len(sessions)} trading sessions. Initializing LiveMarketSimulator...\n")
    sim = LiveMarketSimulator(symbol="XAUUSD", initial_balance=10000.0, risk_per_trade=0.010)
    
    results = []
    
    # -------------------------------------------------------------
    # SUITE A: Sweeping Breakout Stop Orders (Anchor 12:00 Frankfurt & 13:00 London)
    # -------------------------------------------------------------
    print("Evaluating Suite A: Breakout Stop Orders...")
    for anchor_h in [12, 13]:
        anchor_name = "12:00 Frankfurt" if anchor_h == 12 else "13:00 London"
        cutoff_h = 13 if anchor_h == 12 else 14
        cutoff_m = 30
        for sl in [6.00, 7.50, 9.00, 10.00]:
            for rr in [1.8, 2.0, 2.5]:
                for offset in [1.50, 2.00, 2.50]:
                    fn = make_breakout_stop_fn(anchor_h=anchor_h, anchor_m=0, offset=offset, sl=sl, rr=rr, cutoff_h=cutoff_h, cutoff_m=cutoff_m, trend_filter=True)
                    rep = sim.run_bars(sessions, fn)
                    results.append({
                        'family': 'Breakout Stop',
                        'config': f"{anchor_name} | SL ${sl:.1f} | RR {rr:.1f} | Off ${offset:.1f}",
                        'roi': rep['avg_annual_roi'],
                        'worst_yr': rep['worst_year_roi'],
                        'max_dd': rep['max_drawdown_pct'],
                        'green_m': rep['green_months'],
                        'm2026': rep['consistency_2026'],
                        'pf': rep['profit_factor'],
                        'wr': rep['win_rate'],
                        'trades': rep['total_trades'],
                        'avg_r': rep['avg_r_per_trade'],
                        'toll': rep['total_broker_toll_paid']
                    })

    # -------------------------------------------------------------
    # SUITE B: Sweeping Mean-Reversion Fade Limit Orders
    # -------------------------------------------------------------
    print("Evaluating Suite B: Fade Limit Orders...")
    for anchor_h in [12, 13]:
        anchor_name = "12:00 Frankfurt" if anchor_h == 12 else "13:00 London"
        for offset in [2.50, 3.50, 5.00]:
            for sl in [6.00, 8.00, 10.00]:
                for tp in [4.00, 6.00, 8.00]:
                    fn = make_fade_limit_fn(anchor_h=anchor_h, anchor_m=0, offset=offset, sl=sl, tp=tp)
                    rep = sim.run_bars(sessions, fn)
                    results.append({
                        'family': 'Fade Limit',
                        'config': f"Fade {anchor_name} | Off ${offset:.1f} | SL ${sl:.1f} | TP ${tp:.1f}",
                        'roi': rep['avg_annual_roi'],
                        'worst_yr': rep['worst_year_roi'],
                        'max_dd': rep['max_drawdown_pct'],
                        'green_m': rep['green_months'],
                        'm2026': rep['consistency_2026'],
                        'pf': rep['profit_factor'],
                        'wr': rep['win_rate'],
                        'trades': rep['total_trades'],
                        'avg_r': rep['avg_r_per_trade'],
                        'toll': rep['total_broker_toll_paid']
                    })

    # -------------------------------------------------------------
    # SUITE C: Sweeping Trend Retest Limit Orders
    # -------------------------------------------------------------
    print("Evaluating Suite C: Trend Retest Limit Orders...")
    for anchor_h in [12, 13]:
        anchor_name = "12:00 Frankfurt" if anchor_h == 12 else "13:00 London"
        for bo in [2.50, 3.50]:
            for rto in [0.50, 1.00, 1.50]:
                for sl in [6.00, 7.50, 9.00]:
                    for rr in [2.0, 2.5]:
                        fn = make_retest_limit_fn(anchor_h=anchor_h, anchor_m=0, break_offset=bo, limit_retest_offset=rto, sl=sl, rr=rr)
                        rep = sim.run_bars(sessions, fn)
                        results.append({
                            'family': 'Retest Limit',
                            'config': f"Retest {anchor_name} | Break ${bo:.1f} | Lim ${rto:.1f} | SL ${sl:.1f} | RR {rr:.1f}",
                            'roi': rep['avg_annual_roi'],
                            'worst_yr': rep['worst_year_roi'],
                            'max_dd': rep['max_drawdown_pct'],
                            'green_m': rep['green_months'],
                            'm2026': rep['consistency_2026'],
                            'pf': rep['profit_factor'],
                            'wr': rep['win_rate'],
                            'trades': rep['total_trades'],
                            'avg_r': rep['avg_r_per_trade'],
                            'toll': rep['total_broker_toll_paid']
                        })

    df_all = pd.DataFrame(results)
    
    print("\n" + "=" * 145)
    print("TOP PERFORMING ORDER ARCHITECTURES IN THE LIVE MARKET SIMULATOR (NET OF ALL VANTAGE FEES & SLIPPAGE)")
    print("=" * 145)
    print(f"{'FAMILY':<14} | {'CONFIGURATION':<62} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'GREEN MTHS':<15} | {'PF':<5} | {'TRADES'}")
    print("-" * 145)
    
    top_models = df_all.sort_values(by='roi', ascending=False)
    for idx, r in top_models.head(25).iterrows():
        print(f"{r['family']:<14} | {r['config']:<62} | {r['roi']:+9.2f}% | {r['worst_yr']:+8.2f}% | {r['max_dd']:8.2f}% | {r['green_m']:<15} | {r['pf']:<5.2f} | {r['trades']}")
    print("=" * 145)

    # Breakdown by Family
    print("\nFAMILY COMPARISON SUMMARY:")
    for fam, grp in df_all.groupby('family'):
        best_in_fam = grp.sort_values(by='roi', ascending=False).iloc[0]
        print(f"  * {fam:<15} Best ROI: {best_in_fam['roi']:+6.2f}% | Best Config: {best_in_fam['config']} (PF: {best_in_fam['pf']:.2f}, Max DD: {best_in_fam['max_dd']:.2f}%)")

if __name__ == "__main__":
    main()

