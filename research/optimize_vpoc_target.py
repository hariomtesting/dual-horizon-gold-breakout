"""
Optimizing VPOC Magnet: Front-Running Target & Trend Confirmation
Testing:
1. Target: 0.70x, 0.75x, 0.80x, 1.0x distance to VPOC (Front-running the institutional exit wall)
2. Stop: 1.5x ATR vs 2.0x ATR
3. Trend alignment check: 50 EMA slope
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def optimize_vpoc(symbol: str = "XAUUSD", bar_count: int = 50000):
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
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()

    days = sorted(df['date'].unique())

    print(f"\n{'='*75}", flush=True)
    print(f"   OPTIMIZING VPOC FRONT-RUN TARGET: {symbol} ({len(days)} Days)", flush=True)
    print(f"{'='*75}\n", flush=True)

    daily_profiles = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_profiles[d] = {
                'vpoc': vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            }

    # Test Target Distance fractions (0.60 to 1.0) with 1.8x ATR Stop
    for target_pct in [0.60, 0.70, 0.75, 0.80, 0.90, 1.00]:
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

                # Trade Management
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

                # Entry Logic
                if pos is None and not trade_taken and i >= 3:
                    full_dist = abs(close_p - prev_poc)

                    if 0.5 * atr <= full_dist <= 2.5 * atr:
                        # Price is BELOW Previous VPOC
                        if close_p < prev_poc and delta > 0 and r['close'] > r['open']:
                            sl_dist = 1.8 * atr
                            sl = close_p - sl_dist
                            tp = close_p + (target_pct * full_dist)
                            risk_dist = close_p - sl
                            if risk_dist > 0:
                                pos = {
                                    'type': 'BUY',
                                    'entry': close_p + spread,
                                    'sl': sl,
                                    'tp': tp,
                                    'units': 100.0 / risk_dist
                                }
                                trade_taken = True

                        # Price is ABOVE Previous VPOC
                        elif close_p > prev_poc and delta < 0 and r['close'] < r['open']:
                            sl_dist = 1.8 * atr
                            sl = close_p + sl_dist
                            tp = close_p - (target_pct * full_dist)
                            risk_dist = sl - close_p
                            if risk_dist > 0:
                                pos = {
                                    'type': 'SELL',
                                    'entry': close_p,
                                    'sl': sl,
                                    'tp': tp,
                                    'units': 100.0 / risk_dist
                                }
                                trade_taken = True

            # Session close
            if pos is not None:
                last_bar = session_bars.iloc[-1]
                if pos['type'] == 'BUY':
                    pos['pnl'] = (last_bar['close'] - pos['entry']) * pos['units']
                else:
                    pos['pnl'] = (pos['entry'] - last_bar['close']) * pos['units']
                trades.append(pos)

        if len(trades) > 0:
            df_res = pd.DataFrame(trades)
            wins = df_res[df_res['pnl'] > 0]
            losses = df_res[df_res['pnl'] <= 0]
            wr = (len(wins) / len(df_res)) * 100.0
            gross_win = wins['pnl'].sum() if len(wins) > 0 else 0
            gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            net = df_res['pnl'].sum()
            avg_w = wins['pnl'].mean() if len(wins) > 0 else 0
            avg_l = losses['pnl'].mean() if len(losses) > 0 else 0

            status = "[TARGET HIT >=70%]" if wr >= 70.0 else "[BELOW 70%]"
            print(f"{status} Target: {int(target_pct*100)}% to VPOC (SL 1.8x ATR):", flush=True)
            print(f"   Trades       : {len(df_res)} trades ({len(wins)} W / {len(losses)} L)", flush=True)
            print(f"   Win Rate     : {wr:.2f}%", flush=True)
            print(f"   Profit Factor: {pf:.2f}", flush=True)
            print(f"   Net Profit   : ${net:,.2f}", flush=True)
            print(f"   Avg Win / Loss: ${avg_w:.2f} / ${avg_l:.2f}\n", flush=True)


if __name__ == "__main__":
    for sym in ["XAUUSD", "US100", "EURUSD"]:
        optimize_vpoc(sym, bar_count=50000)

