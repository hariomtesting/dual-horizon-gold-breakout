"""
Virgin POC & Value Area Magnet Strategy Backtest
Empirical Edge: Previous Day VPOC is revisited 70%+ of days across US100, XAUUSD, and EURUSD.
Rules:
1. Filter: At session open (08:00 UTC), check if price is displaced from Previous Day VPOC by >= 0.5x ATR.
2. Signal: Price forms a 5m reversal bar with Order Flow Delta aligning towards Previous VPOC.
3. Target: Previous Day VPOC.
4. Stop: 1.0x - 1.5x ATR beyond the session displacement high/low.
5. Max 1 trade per day per asset.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def test_vpoc_magnet_strategy(symbol: str = "US100", bar_count: int = 50000):
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

    print(f"\n{'='*75}", flush=True)
    print(f"   TESTING VIRGIN VPOC MAGNET STRATEGY: {symbol} ({len(days)} Days)", flush=True)
    print(f"{'='*75}\n", flush=True)

    # Pre-calculate each day's VPOC and Value Area
    daily_profiles = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_profiles[d] = {
                'vpoc': vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0),
                'vah': vp.get('vah', sub['high'].max()),
                'val': vp.get('val', sub['low'].min())
            }

    # Test parameter variations of SL Distance (1.0x ATR to 2.0x ATR)
    for sl_mult in [1.0, 1.25, 1.5, 2.0]:
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

                # Manage active trade
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

                # Entry Logic: Only 1 trade per session
                if pos is None and not trade_taken and i >= 3:
                    dist_to_poc = abs(close_p - prev_poc)

                    # Only take trade if distance to POC is at least 0.5x ATR and at most 2.5x ATR
                    if 0.5 * atr <= dist_to_poc <= 2.5 * atr:
                        # Price is BELOW Previous VPOC -> Look for BUY rotation
                        if close_p < prev_poc and delta > 0 and r['close'] > r['open']:
                            sl_dist = sl_mult * atr
                            sl = close_p - sl_dist
                            tp = prev_poc
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

                        # Price is ABOVE Previous VPOC -> Look for SELL rotation
                        elif close_p > prev_poc and delta < 0 and r['close'] < r['open']:
                            sl_dist = sl_mult * atr
                            sl = close_p + sl_dist
                            tp = prev_poc
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

            # End of session close if trade still open
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
            print(f"{status} VPOC Magnet (SL Mult: {sl_mult}x ATR):", flush=True)
            print(f"   Trades       : {len(df_res)} trades ({len(wins)} W / {len(losses)} L)", flush=True)
            print(f"   Win Rate     : {wr:.2f}%", flush=True)
            print(f"   Profit Factor: {pf:.2f}", flush=True)
            print(f"   Net Profit   : ${net:,.2f}", flush=True)
            print(f"   Avg Win / Loss: ${avg_w:.2f} / ${avg_l:.2f}\n", flush=True)


if __name__ == "__main__":
    for sym in ["US100", "XAUUSD", "EURUSD"]:
        test_vpoc_magnet_strategy(sym, bar_count=50000)

