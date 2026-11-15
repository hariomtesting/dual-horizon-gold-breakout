"""
Calibrate Optimal Multi-Horizon Mathematical Expectancy
Analyzes the exact displacement boundaries that maximize win rate and multi-horizon profitability.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def calibrate_horizons(symbol: str = "XAUUSD"):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50000)
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

    # Test displacement brackets:
    # Bracket A: 0.4x to 1.5x ATR (Sweet spot mean-reversion)
    # Bracket B: 0.5x to 2.5x ATR (Standard)
    # Bracket C: 1.0x to 2.5x ATR (Large extension only)
    brackets = [
        {"name": "Sweet Spot Mean Reversion (0.4x - 1.5x ATR)", "min_d": 0.4, "max_d": 1.5},
        {"name": "Standard Boundary (0.5x - 2.5x ATR)",          "min_d": 0.5, "max_d": 2.5},
        {"name": "Large Extensions Only (1.0x - 2.5x ATR)",      "min_d": 1.0, "max_d": 2.5},
    ]

    print(f"\n{'='*80}")
    print(f"   MATHEMATICAL HORIZON OPTIMIZATION: {symbol} ({len(days)} Days)")
    print(f"{'='*80}\n", flush=True)

    for b in brackets:
        trades = []
        capital = 10000.0

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
                high_p, low_p, close_p = r['high'], r['low'], r['close']
                atr, delta = r['atr'], r['delta']

                if pos is not None:
                    if pos['type'] == 'BUY':
                        if low_p <= pos['sl']:
                            pnl = (pos['sl'] - pos['entry']) * pos['units']
                            pos['pnl'] = pnl
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            capital += pnl
                            pos = None
                        elif high_p >= pos['tp']:
                            pnl = (pos['tp'] - pos['entry']) * pos['units']
                            pos['pnl'] = pnl
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            capital += pnl
                            pos = None
                    elif pos['type'] == 'SELL':
                        if high_p >= pos['sl']:
                            pnl = (pos['entry'] - pos['sl']) * pos['units']
                            pos['pnl'] = pnl
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            capital += pnl
                            pos = None
                        elif low_p <= pos['tp']:
                            pnl = (pos['entry'] - pos['tp']) * pos['units']
                            pos['pnl'] = pnl
                            pos['exit_time'] = r['datetime']
                            trades.append(pos)
                            capital += pnl
                            pos = None

                if pos is None and not trade_taken and i >= 3:
                    full_dist = abs(close_p - prev_poc)

                    if b['min_d'] * atr <= full_dist <= b['max_d'] * atr:
                        sl_dist = 1.8 * atr

                        if close_p < prev_poc and delta > 0 and r['close'] > r['open']:
                            pos = {
                                'type': 'BUY',
                                'entry': close_p + spread,
                                'sl': close_p - sl_dist,
                                'tp': close_p + (0.60 * full_dist),
                                'entry_time': r['datetime'],
                                'units': 100.0 / sl_dist
                            }
                            trade_taken = True
                        elif close_p > prev_poc and delta < 0 and r['close'] < r['open']:
                            pos = {
                                'type': 'SELL',
                                'entry': close_p,
                                'sl': close_p + sl_dist,
                                'tp': close_p - (0.60 * full_dist),
                                'entry_time': r['datetime'],
                                'units': 100.0 / sl_dist
                            }
                            trade_taken = True

            if pos is not None:
                last_b = session_bars.iloc[-1]
                if pos['type'] == 'BUY':
                    pnl = (last_b['close'] - pos['entry']) * pos['units']
                else:
                    pnl = (pos['entry'] - last_b['close']) * pos['units']
                pos['pnl'] = pnl
                pos['exit_time'] = last_b['datetime']
                trades.append(pos)
                capital += pnl

        if len(trades) > 0:
            df_t = pd.DataFrame(trades)
            wins = df_t[df_t['pnl'] > 0]
            losses = df_t[df_t['pnl'] <= 0]
            wr = (len(wins) / len(df_t)) * 100.0
            pf = wins['pnl'].sum() / abs(losses['pnl'].sum()) if len(losses) > 0 else 999.0
            tot_pnl = df_t['pnl'].sum()

            df_t['date'] = pd.to_datetime(df_t['entry_time']).dt.date
            df_t['week'] = pd.to_datetime(df_t['entry_time']).dt.to_period('W')
            df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')

            daily_green = (df_t.groupby('date')['pnl'].sum() > 0).mean() * 100.0
            weekly_green = (df_t.groupby('week')['pnl'].sum() > 0).mean() * 100.0
            monthly_green = (df_t.groupby('month')['pnl'].sum() > 0).mean() * 100.0

            print(f"[{b['name']}]")
            print(f"   Trades       : {len(df_t)} ({len(wins)} W / {len(losses)} L)")
            print(f"   WIN RATE     : {wr:.2f}%")
            print(f"   Profit Factor: {pf:.2f}")
            print(f"   Net PnL      : ${tot_pnl:,.2f}")
            print(f"   DAILY HORIZON: {daily_green:.1f}% Green Days")
            print(f"   WEEKLY HORIZON: {weekly_green:.1f}% Green Weeks")
            print(f"   MONTHLY HORIZON: {monthly_green:.1f}% Green Months\n", flush=True)


if __name__ == "__main__":
    calibrate_horizons("XAUUSD")
    calibrate_horizons("NAS100.r")

