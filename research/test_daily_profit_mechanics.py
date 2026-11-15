"""
Test Institutional Daily Green Mechanics:
1. Bank & Free-Roll: Close 50% at +0.5R, move SL to breakeven
2. One-and-Done Rule: If trade 1 wins, lock the day immediately!
3. Max 2 Trades Per Day Circuit Breaker
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def simulate_bank_and_lock_daily(symbol: str = "XAUUSD"):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

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

    daily_outcomes = []
    
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

        day_pnl = 0.0
        trades_today = 0
        locked_green = False

        pos = None

        for i in range(len(session_bars)):
            if locked_green or trades_today >= 2:
                break

            r = session_bars.iloc[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']

            # Manage open trade with Bank & Free-roll
            if pos is not None:
                # 1. Partial scale-out trigger (+0.5R)
                if not pos['partial_taken']:
                    if pos['type'] == 'BUY' and high_p >= pos['entry'] + (0.5 * pos['risk_dist']):
                        # Bank 50%
                        pos['banked_pnl'] = (0.5 * pos['risk_dist']) * (0.5 * pos['units'])
                        pos['units'] *= 0.5 # Remaining 50% runs
                        pos['sl'] = pos['entry'] + spread # Move SL to breakeven
                        pos['partial_taken'] = True
                    elif pos['type'] == 'SELL' and low_p <= pos['entry'] - (0.5 * pos['risk_dist']):
                        pos['banked_pnl'] = (0.5 * pos['risk_dist']) * (0.5 * pos['units'])
                        pos['units'] *= 0.5
                        pos['sl'] = pos['entry'] - spread
                        pos['partial_taken'] = True

                # 2. Exit check
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        final_pnl = pos['banked_pnl'] + ((pos['sl'] - pos['entry']) * pos['units'])
                        day_pnl += final_pnl
                        trades_today += 1
                        if day_pnl > 0:
                            locked_green = True
                        pos = None
                    elif high_p >= pos['tp']:
                        final_pnl = pos['banked_pnl'] + ((pos['tp'] - pos['entry']) * pos['units'])
                        day_pnl += final_pnl
                        trades_today += 1
                        locked_green = True # Winner locks day!
                        pos = None
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        final_pnl = pos['banked_pnl'] + ((pos['entry'] - pos['sl']) * pos['units'])
                        day_pnl += final_pnl
                        trades_today += 1
                        if day_pnl > 0:
                            locked_green = True
                        pos = None
                    elif low_p <= pos['tp']:
                        final_pnl = pos['banked_pnl'] + ((pos['entry'] - pos['tp']) * pos['units'])
                        day_pnl += final_pnl
                        trades_today += 1
                        locked_green = True
                        pos = None

            # New Entry
            if pos is None and trades_today < 2 and not locked_green and i >= 3:
                full_dist = abs(close_p - prev_poc)
                if 0.5 * atr <= full_dist <= 2.5 * atr:
                    if close_p < prev_poc and delta > 0 and close_p > open_p:
                        sl_dist = 1.8 * atr
                        risk_dist = sl_dist
                        pos = {
                            'type': 'BUY',
                            'entry': close_p + spread,
                            'sl': close_p - sl_dist,
                            'tp': close_p + (0.65 * full_dist),
                            'risk_dist': risk_dist,
                            'units': 100.0 / risk_dist,
                            'banked_pnl': 0.0,
                            'partial_taken': False
                        }
                    elif close_p > prev_poc and delta < 0 and close_p < open_p:
                        sl_dist = 1.8 * atr
                        risk_dist = sl_dist
                        pos = {
                            'type': 'SELL',
                            'entry': close_p,
                            'sl': close_p + sl_dist,
                            'tp': close_p - (0.65 * full_dist),
                            'risk_dist': risk_dist,
                            'units': 100.0 / risk_dist,
                            'banked_pnl': 0.0,
                            'partial_taken': False
                        }

        # End of day check
        if pos is not None:
            last_b = session_bars.iloc[-1]
            if pos['type'] == 'BUY':
                day_pnl += pos['banked_pnl'] + ((last_b['close'] - pos['entry']) * pos['units'])
            else:
                day_pnl += pos['banked_pnl'] + ((pos['entry'] - last_b['close']) * pos['units'])
            trades_today += 1

        if trades_today > 0:
            daily_outcomes.append({
                'date': curr_d,
                'pnl': day_pnl,
                'trades': trades_today,
                'green': day_pnl > 0,
                'be': day_pnl == 0,
                'red': day_pnl < 0
            })

    df_d = pd.DataFrame(daily_outcomes)
    g = df_d['green'].sum()
    be = df_d['be'].sum()
    r = df_d['red'].sum()
    tot = len(df_d)
    green_rate = (g / tot) * 100.0
    green_or_be = ((g + be) / tot) * 100.0
    tot_profit = df_d['pnl'].sum()

    print(f"\n{'='*75}")
    print(f"   INSTITUTIONAL DAILY GREEN RESULTS: {symbol}")
    print(f"{'='*75}")
    print(f"   Active Days Traded : {tot} days")
    print(f"   GREEN DAYS         : {g} ({green_rate:.1f}%)")
    print(f"   Scratch / BE Days  : {be} ({be/tot*100:.1f}%)")
    print(f"   RED DAYS           : {r} ({r/tot*100:.1f}%)")
    print(f"   NON-LOSING DAYS    : {g + be} of {tot} ({green_or_be:.1f}% Win/Scratch Daily Rate!)")
    print(f"   Total Net PnL      : ${tot_profit:,.2f}")
    print(f"   Avg Green Day      : +${df_d[df_d['pnl'] > 0]['pnl'].mean():.2f}")
    print(f"   Avg Red Day        : -${abs(df_d[df_d['pnl'] < 0]['pnl'].mean()):.2f}")
    print(f"{'='*75}\n", flush=True)


if __name__ == "__main__":
    for sym in ["XAUUSD", "US100"]:
        simulate_bank_and_lock_daily(sym)

