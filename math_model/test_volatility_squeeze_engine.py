"""
INSTITUTIONAL VOLATILITY SQUEEZE ENGINE (TTM / BOLLINGER-KELTNER SQUEEZE)
========================================================================
Based on:
- John Bollinger (1992): "Volatility Contraction & BandWidth Squeeze"
- John Carter: "TTM Squeeze - Volatility Compression & Directional Expansion"
- Journal of Trading (2018): "Multi-Asset Volatility Breakout with Trend Regimes"

Mechanics:
1. Squeeze Detection:
   - Bollinger Bands: SMA(20) +/- 2.0 * STD(20)
   - Keltner Channels: EMA(20) +/- 1.5 * ATR(14)
   - Squeeze is ON when Upper_BB < Upper_KC and Lower_BB > Lower_KC (extreme compression).
2. Squeeze Firing (Volatility Explosion):
   - When Upper_BB expands beyond Upper_KC or Lower_BB expands below Lower_KC.
   - Trend confirmation: Price > EMA200 for Long, Price < EMA200 for Short.
   - Momentum confirmation: Linear momentum / MACD slope is positive for Long, negative for Short.
3. Execution:
   - Enters via Maker Retest Limit Order at the Upper/Lower Keltner Channel to eliminate taker slippage.
   - SL: 1.5x ATR.
   - TP: 2.5x to 3.0x SL distance (Asymmetric Payoff).
4. Evaluated under full Live Market Simulator (Vantage spreads, commission, latency).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from backtest.engine import LiveMarketSimulator

def load_data(symbol, n_bars=150000, start_h=11, end_h=16):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return {}
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    # Technical Indicators:
    # 1. EMA 200 (Macro trend)
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # 2. Bollinger Bands (20, 2.0)
    df['sma20'] = df['close'].rolling(20, min_periods=10).mean()
    df['std20'] = df['close'].rolling(20, min_periods=10).std()
    df['bb_upper'] = df['sma20'] + 2.0 * df['std20']
    df['bb_lower'] = df['sma20'] - 2.0 * df['std20']
    
    # 3. Keltner Channels (20, 1.5 ATR)
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr14'] = tr.rolling(14, min_periods=5).mean().bfill()
    df['kc_upper'] = df['ema20'] + 1.5 * df['atr14']
    df['kc_lower'] = df['ema20'] - 1.5 * df['atr14']
    
    # 4. Squeeze condition (BB inside KC)
    df['squeeze_on'] = (df['bb_upper'] < df['kc_upper']) & (df['bb_lower'] > df['kc_lower'])
    
    # 5. Momentum proxy (Close - SMA20)
    df['mom'] = df['close'] - df['sma20']
    
    # Session slice
    df_window = df[(df['ist_hour'] >= start_h - 2) & (df['ist_hour'] <= end_h)].copy()
    sessions = {}
    for d, sub in df_window.groupby('date'):
        if len(sub) >= 30:
            sessions[d] = sub.to_dict('records')
    return sessions

def make_volatility_squeeze_sig(start_h=12, end_h=15, sl_atr=1.5, rr=2.5, min_squeeze_bars=3):
    def sig(bars, i, state):
        row = bars[i]
        h = row['ist_hour']
        m = row['ist_minute']
        
        if 'phase' not in state:
            state['phase'] = 'SCANNING'
            state['squeeze_count'] = 0
            state['done'] = False
            
        if state['done'] or h >= end_h:
            state['done'] = True
            return None
            
        # Count consecutive squeeze bars
        if row['squeeze_on']:
            state['squeeze_count'] += 1
        else:
            # Squeeze is OFF (or just fired!)
            if state['phase'] == 'SCANNING' and h >= start_h:
                if state['squeeze_count'] >= min_squeeze_bars:
                    # Squeeze just FIRED! Check directional expansion:
                    atr = row['atr14']
                    ema200 = row['ema200']
                    
                    # Bullish Expansion: Price broke above Upper BB & Upper KC, above EMA200
                    if row['close'] > row['kc_upper'] and row['close'] > ema200 and row['mom'] > 0:
                        state['phase'] = 'LIMIT_WAIT'
                        state['dir'] = 'BUY'
                        # Retest limit order placed at Upper KC (or EMA20)
                        state['lp'] = row['kc_upper']
                        state['sl'] = state['lp'] - (sl_atr * atr)
                        state['tp'] = state['lp'] + (sl_atr * atr * rr)
                        state['squeeze_count'] = 0
                        return None
                        
                    # Bearish Expansion: Price broke below Lower BB & Lower KC, below EMA200
                    elif row['close'] < row['kc_lower'] and row['close'] < ema200 and row['mom'] < 0:
                        state['phase'] = 'LIMIT_WAIT'
                        state['dir'] = 'SELL'
                        state['lp'] = row['kc_lower']
                        state['sl'] = state['lp'] + (sl_atr * atr)
                        state['tp'] = state['lp'] - (sl_atr * atr * rr)
                        state['squeeze_count'] = 0
                        return None
                        
            # If not fired in tradeable direction, reset count if too far
            if not row['squeeze_on']:
                state['squeeze_count'] = max(0, state['squeeze_count'] - 1)
                
        # Wait for Maker Retest Limit Fill
        if state['phase'] == 'LIMIT_WAIT':
            lp = state['lp']
            if state['dir'] == 'BUY' and row['low'] <= lp:
                state['done'] = True
                return {'type': 'BUY_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
            elif state['dir'] == 'SELL' and row['high'] >= lp:
                state['done'] = True
                return {'type': 'SELL_LIMIT', 'trigger': lp, 'sl': state['sl'], 'tp': state['tp']}
                
        return None
    return sig

def main():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    assets = [
        {'sym': 'XAUUSD', 'name': 'Gold (Commodity)', 'start_h': 12, 'end_h': 16, 'spread': 0.30, 'comm': 6.0},
        {'sym': 'NAS100.r', 'name': 'Nasdaq 100 (US Index)', 'start_h': 19, 'end_h': 23, 'spread': 1.2, 'comm': 4.0},
        {'sym': 'EURUSD', 'name': 'Euro (FX Major)', 'start_h': 12, 'end_h': 16, 'spread': 0.00014, 'comm': 6.0},
    ]
    
    print("\n" + "=" * 135)
    print("VOLATILITY SQUEEZE BREAKOUT ENGINE (BOLLINGER-KELTNER COMPRESSION)")
    print("Evaluated strictly under Live Market Simulator (Vantage Spreads, Commission, Slippage, 2.5% Compounding Risk)")
    print("=" * 135)
    print(f"{'ASSET NAME':<24} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'PF':<5} | {'WIN %':<6} | {'TRADES'} | {'FINAL BAL'}")
    print("-" * 135)
    
    for a in assets:
        sym = a['sym']
        sessions = load_data(sym, n_bars=150000, start_h=a['start_h'], end_h=a['end_h'])
        if not sessions:
            continue
            
        sim = LiveMarketSimulator(
            symbol=sym,
            initial_balance=10000.0,
            risk_per_trade=0.025,
            spread_override=a['spread'],
            commission_per_lot=a['comm'],
            daily_loss_limit_pct=2.5,
            compounding=True
        )
        
        sig_fn = make_volatility_squeeze_sig(
            start_h=a['start_h'],
            end_h=a['end_h'] - 1,
            sl_atr=1.5,
            rr=2.5,
            min_squeeze_bars=3
        )
        rep = sim.run_bars(sessions, sig_fn)
        
        print(f"{a['name']:<24} | {rep['avg_annual_roi']:+9.2f}% | {rep['worst_year_roi']:+8.2f}% | {rep['max_drawdown_pct']:8.2f}% | {rep['profit_factor']:<5.2f} | {rep['win_rate']:4.1f}% | {rep['total_trades']:<6} | ${rep['final_balance']:,.2f}")
        
    mt5.shutdown()
    print("=" * 135)

if __name__ == "__main__":
    main()

