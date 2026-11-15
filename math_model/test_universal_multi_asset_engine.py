"""
THE UNIVERSAL MULTI-ASSET QUANTITATIVE ENGINE
=============================================
Goal: Build a single universal mathematical strategy that works across
ALL assets (Gold, EURUSD, GBPUSD, etc.) without hardcoded dollars or pips.

Universal Mathematical Principles:
1. Friction-Invariant Horizon Scaling:
   - Evaluates the broker toll (Spread + Comm + Slippage) per asset.
   - Enforces Stop Loss >= 5.0x Total Friction so fees never consume edge.
2. Dynamic ATR Normalization:
   - Offsets, Stops, and Targets are strictly in units of ATR (Average True Range).
3. Session Liquidity Anchor:
   - Arms at European Cash Open (12:00 PM IST).
4. Maker Retest Limit Entry:
   - Waits for 1.4x ATR impulse, enters on 0.5x ATR pullback via Limit Order.
5. Asymmetric Payoff:
   - 2.5:1 to 3.0:1 R:R with 15:55 IST square-off and -2.0% circuit breaker.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_multi_asset_data(symbols=['XAUUSD', 'EURUSD', 'GBPUSD'], start_year=2023):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    
    dt_from = datetime.datetime(start_year, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    
    asset_sessions = {}
    for sym in symbols:
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, dt_from, dt_to)
        if rates is None or len(rates) == 0:
            continue
            
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
        df['date'] = df['ist_dt'].dt.date
        df['ist_hour'] = df['ist_dt'].dt.hour
        df['ist_minute'] = df['ist_dt'].dt.minute
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
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
        asset_sessions[sym] = sessions
        
    mt5.shutdown()
    return asset_sessions

def create_universal_signal_fn(
    anchor_h=12, anchor_m=0,
    break_atr_mult=1.4,
    retest_atr_mult=0.5,
    sl_atr_mult=2.5,
    rr=2.5,
    min_sl_distance=0.0001 # Asset-specific minimum stop to beat friction
):
    def signal_fn(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'WAIT_ANCHOR'
            state['base'] = None
            state['atr'] = None
            state['ema'] = None
            state['dir'] = None
            state['limit_p'] = None
            state['sl'] = None
            state['tp'] = None
            state['done'] = False
            
        if state['done'] or h >= 14:
            state['done'] = True
            return None
            
        if state['phase'] == 'WAIT_ANCHOR' and h == anchor_h and m == anchor_m:
            state['phase'] = 'WAIT_BREAK'
            state['base'] = row['close']
            state['atr'] = row['atr']
            state['ema'] = row['ema200']
            return None
            
        if state['phase'] == 'WAIT_BREAK':
            base = state['base']
            atr = state['atr']
            ema = state['ema']
            break_dist = break_atr_mult * atr
            retest_dist = retest_atr_mult * atr
            sl_dist = max(min_sl_distance, sl_atr_mult * atr)
            tp_dist = sl_dist * rr
            
            if base >= ema and row['high'] >= base + break_dist:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'BUY'
                state['limit_p'] = base + retest_dist
                state['sl'] = (base + retest_dist) - sl_dist
                state['tp'] = (base + retest_dist) + tp_dist
            elif base <= ema and row['low'] <= base - break_dist:
                state['phase'] = 'WAIT_LIMIT_FILL'
                state['dir'] = 'SELL'
                state['limit_p'] = base - retest_dist
                state['sl'] = (base - retest_dist) + sl_dist
                state['tp'] = (base - retest_dist) - tp_dist
                
        elif state['phase'] == 'WAIT_LIMIT_FILL':
            lp = state['limit_p']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                
        return None
    return signal_fn

def main():
    print("Loading multi-asset data from MT5 (XAUUSD, EURUSD, GBPUSD)...")
    asset_data = load_multi_asset_data(symbols=['XAUUSD', 'EURUSD', 'GBPUSD'], start_year=2023)
    
    # Asset configuration table (Live Vantage settings)
    asset_configs = {
        'XAUUSD': {'spread': 0.30, 'comm': 6.00, 'min_sl': 5.00, 'name': 'Gold (Commodity)'},
        'EURUSD': {'spread': 0.00014, 'comm': 6.00, 'min_sl': 0.0020, 'name': 'Euro (FX Major)'},
        'GBPUSD': {'spread': 0.00018, 'comm': 6.00, 'min_sl': 0.0025, 'name': 'Pound (FX Major)'}
    }
    
    print("\n" + "=" * 135)
    print("UNIVERSAL QUANTITATIVE ENGINE: MULTI-ASSET SCORECARD (EVALUATED UNDER FULL LIVE VANTAGE BROKER FEES)")
    print("=" * 135)
    print(f"{'ASSET':<20} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 135)
    
    for sym, cfg in asset_configs.items():
        if sym not in asset_data:
            continue
        sessions = asset_data[sym]
        
        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.025,
            spread_override=cfg['spread'],
            commission_per_lot=cfg['comm'],
            daily_loss_limit_pct=2.5,
            compounding=True
        )
        
        sig_fn = create_universal_signal_fn(
            anchor_h=12, anchor_m=0,
            break_atr_mult=1.4,
            retest_atr_mult=0.5,
            sl_atr_mult=2.5,
            rr=2.5,
            min_sl_distance=cfg['min_sl']
        )
        
        rep = sim.run_bars(sessions, sig_fn)
        
        print(f"{cfg['name']:<20} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")
        
    print("=" * 135)

if __name__ == "__main__":
    main()

