"""
COIN FLIP TRADING STRATEGY EXPERIMENT (1:1 vs 1:2 R:R)
======================================================
Objective:
Empirically test whether a random direction (50% Buy / 50% Sell) combined with:
1. 1:1 Risk-to-Reward (TP = 1.0x SL)
2. 1:2 Risk-to-Reward (TP = 2.0x SL)
can be profitable, break even, or lose money in financial markets (accounting for spreads).

Runs 10 Monte Carlo iterations (different random seeds) across real M5 Gold (XAUUSD) & DAX (GER40) bars.
"""

import os
import sys
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def run_coin_flip_experiment(symbol="XAUUSD", n_seeds=10, max_trades_per_seed=1500):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
        
    mt5.symbol_select(symbol, True)
    
    # Fetch historical M5 data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100000)
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    
    # Estimate typical spread in points
    info = mt5.symbol_info(symbol)
    spread_pts = (info.spread * info.point) if info else 0.20
    
    # Calculate ATR
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    records = df[['datetime', 'open', 'high', 'low', 'close', 'atr']].to_dict('records')
    total_bars = len(records)
    
    print("=" * 85, flush=True)
    print(f"      COIN FLIP STRATEGY EXPERIMENT: {symbol} (1:1 vs 1:2 RISK:REWARD)      ", flush=True)
    print("=" * 85, flush=True)
    print(f"Total Bars Tested: {total_bars:,} | Spread: {spread_pts:.2f} points\n", flush=True)
    
    configs = [
        {"name": "Coin Flip 1:1 R:R", "sl_mult": 1.0, "tp_mult": 1.0, "rr": 1.0},
        {"name": "Coin Flip 1:2 R:R", "sl_mult": 1.0, "tp_mult": 2.0, "rr": 2.0}
    ]
    
    risk_per_trade = 0.0125 # 1.25% fixed risk
    
    for cfg in configs:
        name = cfg['name']
        sl_mult = cfg['sl_mult']
        tp_mult = cfg['tp_mult']
        rr = cfg['rr']
        
        all_seed_rois = []
        all_seed_wrs = []
        all_seed_dds = []
        all_seed_trades = []
        
        for seed in range(n_seeds):
            np.random.seed(42 + seed * 101)
            
            cap = 10000.0
            peak = cap
            max_dd = 0.0
            completed_trades = []
            
            i = 20
            while i < total_bars - 100 and len(completed_trades) < max_trades_per_seed:
                r = records[i]
                atr = r['atr']
                if atr <= 0 or np.isnan(atr):
                    i += 1
                    continue
                    
                # 50/50 Coin Flip: 1 = BUY, 0 = SELL
                coin = np.random.randint(0, 2)
                side = 'BUY' if coin == 1 else 'SELL'
                
                entry_p = r['close']
                sl_dist = sl_mult * atr
                tp_dist = tp_mult * atr
                
                if side == 'BUY':
                    entry_p += spread_pts / 2.0 # buy at ask
                    sl = entry_p - sl_dist
                    tp = entry_p + tp_dist
                else:
                    entry_p -= spread_pts / 2.0 # sell at bid
                    sl = entry_p + sl_dist
                    tp = entry_p - tp_dist
                    
                # Track trade execution forward bar by bar
                trade_done = False
                pnl_pct = 0.0
                j = i + 1
                while j < min(i + 200, total_bars):
                    bar_f = records[j]
                    h_f = bar_f['high']
                    l_f = bar_f['low']
                    
                    if side == 'BUY':
                        # Check SL and TP
                        hit_sl = (l_f <= sl)
                        hit_tp = (h_f >= tp)
                        if hit_sl and hit_tp:
                            # Conservative assumption: hit SL first
                            pnl_pct = -risk_per_trade
                            trade_done = True
                            break
                        elif hit_sl:
                            pnl_pct = -risk_per_trade
                            trade_done = True
                            break
                        elif hit_tp:
                            pnl_pct = risk_per_trade * rr
                            trade_done = True
                            break
                    else: # SELL
                        hit_sl = (h_f >= sl)
                        hit_tp = (l_f <= tp)
                        if hit_sl and hit_tp:
                            pnl_pct = -risk_per_trade
                            trade_done = True
                            break
                        elif hit_sl:
                            pnl_pct = -risk_per_trade
                            trade_done = True
                            break
                        elif hit_tp:
                            pnl_pct = risk_per_trade * rr
                            trade_done = True
                            break
                    j += 1
                    
                if not trade_done:
                    # Timeout exit
                    bar_last = records[min(j, total_bars - 1)]
                    c_last = bar_last['close']
                    pts = (c_last - entry_p) if side == 'BUY' else (entry_p - c_last)
                    pnl_pct = (pts / sl_dist) * risk_per_trade
                    
                cap += cap * pnl_pct
                if cap > peak:
                    peak = cap
                dd = (peak - cap) / peak * 100.0
                if dd > max_dd:
                    max_dd = dd
                    
                completed_trades.append({'pnl_pct': pnl_pct, 'win': pnl_pct > 0})
                
                # Advance to next bar after trade ends
                i = j + 1
                
            roi = (cap - 10000.0) / 10000.0 * 100.0
            wr = np.mean([t['win'] for t in completed_trades]) * 100.0
            
            all_seed_rois.append(roi)
            all_seed_wrs.append(wr)
            all_seed_dds.append(max_dd)
            all_seed_trades.append(len(completed_trades))
            
        print(f"--- Configuration: {name} ---")
        print(f"Theoretical Breakeven Win Rate : {100.0 / (1.0 + rr):.1f}%")
        print(f"Actual Average Win Rate        : {np.mean(all_seed_wrs):.2f}% (Range: {min(all_seed_wrs):.1f}% - {max(all_seed_wrs):.1f}%)")
        print(f"Average Return (ROI)           : {np.mean(all_seed_rois):+6.2f}% (Range: {min(all_seed_rois):+6.1f}% to {max(all_seed_rois):+6.1f}%)")
        print(f"Average Max Drawdown           : {np.mean(all_seed_dds):.2f}% (Worst: {max(all_seed_dds):.2f}%)")
        print(f"Average Trades Completed       : {np.mean(all_seed_trades):.0f}")
        pct_profitable = (np.array(all_seed_rois) > 0).mean() * 100.0
        print(f"Profitable Simulations         : {pct_profitable:.1f}% ({sum(1 for r in all_seed_rois if r > 0)}/{n_seeds})\n")


if __name__ == "__main__":
    run_coin_flip_experiment("XAUUSD", n_seeds=10, max_trades_per_seed=1000)
    run_coin_flip_experiment("GER40.r", n_seeds=10, max_trades_per_seed=1000)

