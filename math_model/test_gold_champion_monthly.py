"""
Evaluate Monthly Returns on Gold Champion Model (Rank #1: 79.44% WR, PF 2.21)
Calculates exact monthly returns at 1.0%, 1.5%, and 2.0% risk.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def evaluate_gold_champion():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50000)

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour

    spread = 0.20
    of = OrderFlowEngine(tick_size=0.10)

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

    # Configuration #1: SL 1.5x ATR, TP 0.55x Dist, max 1 trade/day
    trades = []
    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        if prev_d not in daily_profiles:
            continue
        prev_poc = daily_profiles[prev_d]['vpoc']
        df_day = df[df['date'] == curr_d].reset_index(drop=True)
        session_sub = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 18)].reset_index(drop=True)
        if len(session_sub) < 15:
            continue

        pos = None
        for i in range(len(session_sub)):
            r = session_sub.iloc[i]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            atr, delta = r['atr'], r['delta']

            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pos['pnl'] = (pos['sl'] - pos['entry']) * pos['units']
                        trades.append(pos)
                        pos = None
                    elif high_p >= pos['tp']:
                        pos['pnl'] = (pos['tp'] - pos['entry']) * pos['units']
                        trades.append(pos)
                        pos = None
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pos['pnl'] = (pos['entry'] - pos['sl']) * pos['units']
                        trades.append(pos)
                        pos = None
                    elif low_p <= pos['tp']:
                        pos['pnl'] = (pos['entry'] - pos['tp']) * pos['units']
                        trades.append(pos)
                        pos = None

            if pos is None and len([t for t in trades if t['date'] == curr_d]) == 0 and i >= 3:
                full_dist = abs(close_p - prev_poc)
                if 0.45 * atr <= full_dist <= 2.2 * atr:
                    sl_dist = 1.50 * atr
                    tp_dist = 0.55 * full_dist

                    if close_p < prev_poc and delta > 0 and close_p > open_p:
                        pos = {
                            'type': 'BUY', 'date': curr_d, 'entry': close_p + spread,
                            'sl': close_p - sl_dist, 'tp': close_p + tp_dist,
                            'entry_time': r['datetime'], 'units': 100.0 / sl_dist
                        }
                    elif close_p > prev_poc and delta < 0 and close_p < open_p:
                        pos = {
                            'type': 'SELL', 'date': curr_d, 'entry': close_p,
                            'sl': close_p + sl_dist, 'tp': close_p - tp_dist,
                            'entry_time': r['datetime'], 'units': 100.0 / sl_dist
                        }

    df_t = pd.DataFrame(trades)
    df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')
    months = sorted(df_t['month'].unique())

    print(f"\n{'='*85}")
    print(f"      GOLD CHAMPION MODEL MONTHLY BREAKDOWN (79.44% WIN RATE, PF 2.21)      ")
    print(f"{'='*85}\n")
    print(f"Total Trades: {len(df_t)} | Overall Win Rate: {(df_t['pnl'] > 0).mean()*100:.2f}%\n")

    for risk in [0.010, 0.015, 0.020]:
        print(f"--- MONTHLY RETURNS AT {risk*100:.1f}% RISK PER TRADE ---")
        monthly_rois = []
        monthly_dds = []

        for m in months:
            m_t = df_t[df_t['month'] == m]
            m_cap = 10000.0
            m_peak = m_cap
            m_dd = 0.0

            for _, row in m_t.iterrows():
                r_mult = row['pnl'] / 100.0
                pnl = m_cap * risk * r_mult
                m_cap += pnl
                if m_cap > m_peak:
                    m_peak = m_cap
                dd = (m_peak - m_cap) / m_peak * 100.0
                if dd > m_dd:
                    m_dd = dd

            m_roi = (m_cap - 10000.0) / 10000.0 * 100.0
            monthly_rois.append(m_roi)
            monthly_dds.append(m_dd)
            status = "[TOP MONTH >=10%]" if m_roi >= 10.0 else ("[GREEN]" if m_roi > 0 else "[RED]")
            print(f"   {m} : {len(m_t):2d} Trades | Net ROI: {m_roi:+6.2f}% | Max DD: {m_dd:4.2f}% | {status}")

        avg_m = np.mean(monthly_rois)
        green_m = (np.array(monthly_rois) > 0).mean() * 100.0
        print(f"\n   Average Monthly Return: +{avg_m:.2f}% | Green Months: {green_m:.1f}% ({green_m/100*len(months):.0f}/{len(months)} Months)")
        print(f"   Average Monthly Drawdown: {np.mean(monthly_dds):.2f}% | Worst Drawdown: {np.max(monthly_dds):.2f}%\n")


if __name__ == "__main__":
    evaluate_gold_champion()

