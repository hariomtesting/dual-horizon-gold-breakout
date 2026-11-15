"""
Comparative Analysis: Flexible Risk:Reward Ratios vs Rigid 1:2
Tests:
1. Target: 0.5x Stop (1:0.5) - Quick high-probability scalping
2. Target: 0.75x Stop (1:0.75) - VPOC Magnet front-run
3. Target: 1.0x Stop (1:1) - Equal risk/reward
4. Target: 1.25x Stop (1:1.25)
5. Target: 1.5x Stop (1:1.5)
6. Target: 2.0x Stop (1:2.0) - Rigid retail target
7. Dynamic Structural Exit (Exit at VPOC or when Order Flow Delta flips)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from typing import Dict, Any, List
from core.orderflow import OrderFlowEngine


def test_rr_variations(symbol: str = "XAUUSD", bar_count: int = 50000):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bar_count)
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['session_id'] = df['datetime'].dt.floor('D')
    df['hour'] = df['datetime'].dt.hour

    tick_sz = 1.0 if ("100" in symbol or "30" in symbol) else (0.10 if "XAU" in symbol else 0.0001)
    spread = 0.50 if "100" in symbol else (0.20 if "XAU" in symbol else 0.0001)
    of = OrderFlowEngine(tick_size=tick_sz)

    df = of.calculate_delta(df)
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()

    days = sorted(df['date'].unique())

    daily_profiles = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_profiles[d] = {
                'vpoc': vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            }

    # Test configurations of Target:Risk ratios
    # Stop is fixed at 1.5x ATR structural buffer
    # Target is varied from 0.5R to 2.0R, plus dynamic VPOC exit
    configs = [
        {"name": "Quick Scalp (1:0.5 R:R)",   "mode": "fixed_rr", "rr": 0.50},
        {"name": "Front-Run (1:0.75 R:R)",   "mode": "fixed_rr", "rr": 0.75},
        {"name": "Even Odds (1:1.0 R:R)",    "mode": "fixed_rr", "rr": 1.00},
        {"name": "Extended  (1:1.5 R:R)",    "mode": "fixed_rr", "rr": 1.50},
        {"name": "Rigid 1:2 (1:2.0 R:R)",    "mode": "fixed_rr", "rr": 2.00},
        {"name": "Dynamic VPOC Magnet Exit",  "mode": "vpoc",     "ratio": 0.70},
    ]

    print(f"\n{'='*80}", flush=True)
    print(f"   COMPARING REWARD:RISK RATIOS & FLEXIBLE EXITS ON {symbol} ({len(days)} Days)", flush=True)
    print(f"{'='*80}\n", flush=True)

    summary_rows = []

    for cfg in configs:
        trades = []
        for d_idx in range(1, len(days)):
            prev_d = days[d_idx - 1]
            curr_d = days[d_idx]

            if prev_d not in daily_profiles:
                continue

            prev_poc = daily_profiles[prev_d]['vpoc']
            df_day = df[df['date'] == curr_d].reset_index(drop=True)
            if len(df_day) < 40:
                continue

            session_bars = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 16)].reset_index(drop=True)
            if len(session_bars) < 15:
                continue

            trade_taken = False
            pos = None

            for i in range(len(session_bars)):
                r = session_bars.iloc[i]
                high_p = r['high']
                low_p = r['low']
                close_p = r['close']
                atr = r['atr']
                delta = r['delta']

                # Manage position
                if pos is not None:
                    if pos['type'] == 'BUY':
                        if low_p <= pos['sl']:
                            pos['pnl'] = (pos['sl'] - pos['entry']) * pos['units']
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            pos = None
                        elif high_p >= pos['tp']:
                            pos['pnl'] = (pos['tp'] - pos['entry']) * pos['units']
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            pos = None
                    elif pos['type'] == 'SELL':
                        if high_p >= pos['sl']:
                            pos['pnl'] = (pos['entry'] - pos['sl']) * pos['units']
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            pos = None
                        elif low_p <= pos['tp']:
                            pos['pnl'] = (pos['entry'] - pos['tp']) * pos['units']
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            pos = None

                # Entry Logic: Displacement from VPOC + Delta Reversal
                if pos is None and not trade_taken and i >= 3:
                    full_dist = abs(close_p - prev_poc)

                    if 0.5 * atr <= full_dist <= 2.5 * atr:
                        sl_dist = 1.5 * atr

                        if cfg['mode'] == 'fixed_rr':
                            target_dist = cfg['rr'] * sl_dist
                        else:
                            target_dist = cfg['ratio'] * full_dist

                        # BUY Signal
                        if close_p < prev_poc and delta > 0 and r['close'] > r['open']:
                            sl = close_p - sl_dist
                            tp = close_p + target_dist
                            pos = {
                                'type': 'BUY',
                                'entry': close_p + spread,
                                'sl': sl,
                                'tp': tp,
                                'entry_time': r['datetime'],
                                'units': 100.0 / sl_dist # Fixed $100 risk
                            }
                            trade_taken = True

                        # SELL Signal
                        elif close_p > prev_poc and delta < 0 and r['close'] < r['open']:
                            sl = close_p + sl_dist
                            tp = close_p - target_dist
                            pos = {
                                'type': 'SELL',
                                'entry': close_p,
                                'sl': sl,
                                'tp': tp,
                                'entry_time': r['datetime'],
                                'units': 100.0 / sl_dist
                            }
                            trade_taken = True

            # Session close
            if pos is not None:
                last_bar = session_bars.iloc[-1]
                if pos['type'] == 'BUY':
                    pos['pnl'] = (last_bar['close'] - pos['entry']) * pos['units']
                else:
                    pos['pnl'] = (pos['entry'] - last_bar['close']) * pos['units']
                pos['exit_time'] = last_bar['datetime']
                trades.append(pos)

        if len(trades) > 0:
            df_t = pd.DataFrame(trades)
            wins = df_t[df_t['pnl'] > 0]
            losses = df_t[df_t['pnl'] <= 0]
            wr = (len(wins) / len(df_t)) * 100.0
            gross_win = wins['pnl'].sum() if len(wins) > 0 else 0
            gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            net = df_t['pnl'].sum()

            # Calculate daily win rate
            df_t['date'] = pd.to_datetime(df_t['entry_time']).dt.date
            daily_pnl = df_t.groupby('date')['pnl'].sum()
            green_days = (daily_pnl > 0).mean() * 100.0

            # Calculate monthly consistency
            df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')
            monthly_pnl = df_t.groupby('month')['pnl'].sum()
            green_months = (monthly_pnl > 0).mean() * 100.0

            summary_rows.append({
                'Setup': cfg['name'],
                'Trades': len(df_t),
                'WinRate': wr,
                'PF': pf,
                'NetProfit': net,
                'AvgWin': wins['pnl'].mean() if len(wins) > 0 else 0,
                'AvgLoss': losses['pnl'].mean() if len(losses) > 0 else 0,
                'GreenDaysRate': green_days,
                'GreenMonthsRate': green_months
            })

    df_sum = pd.DataFrame(summary_rows)
    for _, row in df_sum.iterrows():
        print(f"[{row['Setup']}]")
        print(f"   Win Rate       : {row['WinRate']:5.2f}% ({row['Trades']} trades)")
        print(f"   Profit Factor  : {row['PF']:5.2f}")
        print(f"   Net PnL        : ${row['NetProfit']:,.2f}")
        print(f"   Avg Win / Loss : ${row['AvgWin']:.2f} / ${row['AvgLoss']:.2f}")
        print(f"   Daily Green %  : {row['GreenDaysRate']:5.1f}% of days positive")
        print(f"   Monthly Green %: {row['GreenMonthsRate']:5.1f}% of months positive\n", flush=True)

    return df_sum


if __name__ == "__main__":
    test_rr_variations("XAUUSD", bar_count=50000)
    test_rr_variations("US100", bar_count=50000)

