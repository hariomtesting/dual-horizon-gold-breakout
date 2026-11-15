"""
High-Speed Vectorized / Tuple Edge Calibration for 70%+ Win Rate Scalper
Evaluates:
- Asymmetric Target/Stop distances (e.g. 0.8 ATR TP / 1.5 ATR SL)
- Dynamic Breakeven (+0.5R trigger -> SL to entry)
- Volume Delta absorption confirmation
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_fast_calibration(symbol: str = "US100", bar_count: int = 50000):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    print(f"\n{'='*75}", flush=True)
    print(f"   FAST 70%+ WIN-RATE CALIBRATION: {symbol}", flush=True)
    print(f"{'='*75}\n", flush=True)

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

    df = of.calculate_vwap(df, 'session_id')
    df = of.calculate_delta(df)

    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()

    days = sorted(df['date'].unique())

    # Pre-extract daily sessions and initial balance
    day_sessions = []
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) < 50:
            continue
        session_sub = sub[sub['hour'] >= 7].reset_index(drop=True)
        if len(session_sub) < 20:
            continue
        ib = session_sub.iloc[:12]
        ib_h = float(ib['high'].max())
        ib_l = float(ib['low'].min())
        trade_bars = session_sub.iloc[12:].reset_index(drop=True)
        # Store as records or tuples for hyper-speed
        records = trade_bars[['high', 'low', 'close', 'open', 'atr', 'delta', 'hour', 'vwap', 'vwap_upper1', 'vwap_lower1']].to_dict('records')
        day_sessions.append((ib_h, ib_l, records))

    print(f"Loaded {len(day_sessions)} valid trading days for {symbol}.", flush=True)

    configs = [
        {"tp_atr": 0.6, "sl_atr": 1.2, "be": True,  "desc": "TP 0.6x ATR | SL 1.2x ATR | +BE"},
        {"tp_atr": 0.8, "sl_atr": 1.2, "be": True,  "desc": "TP 0.8x ATR | SL 1.2x ATR | +BE"},
        {"tp_atr": 0.8, "sl_atr": 1.6, "be": True,  "desc": "TP 0.8x ATR | SL 1.6x ATR | +BE"},
        {"tp_atr": 1.0, "sl_atr": 1.5, "be": True,  "desc": "TP 1.0x ATR | SL 1.5x ATR | +BE"},
        {"tp_atr": 0.8, "sl_atr": 1.6, "be": False, "desc": "TP 0.8x ATR | SL 1.6x ATR | No BE"},
        {"tp_atr": 0.8, "sl_atr": 2.0, "be": True,  "desc": "TP 0.8x ATR | SL 2.0x ATR | +BE"},
        {"tp_atr": 1.0, "sl_atr": 2.0, "be": True,  "desc": "TP 1.0x ATR | SL 2.0x ATR | +BE"},
    ]

    for cfg in configs:
        trades = []
        for ib_h, ib_l, bars in day_sessions:
            pos = None
            for idx in range(len(bars)):
                r = bars[idx]
                prev_r = bars[idx - 1] if idx > 0 else None
                high_p = r['high']
                low_p = r['low']
                close_p = r['close']
                atr = r['atr']
                delta = r['delta']

                # Manage position
                if pos is not None:
                    # Breakeven condition
                    if cfg['be'] and not pos['be']:
                        if pos['type'] == 'BUY' and high_p >= pos['entry'] + (pos['tp_dist'] * 0.5):
                            pos['sl'] = pos['entry'] + spread
                            pos['be'] = True
                        elif pos['type'] == 'SELL' and low_p <= pos['entry'] - (pos['tp_dist'] * 0.5):
                            pos['sl'] = pos['entry'] - spread
                            pos['be'] = True

                    # Exit evaluation
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

                # Entry signals
                if pos is None and r['hour'] < 16 and prev_r is not None:
                    # Sweep IB Low then close back above with positive delta absorption
                    if prev_r['low'] < ib_l and close_p > ib_l and delta > 0:
                        tp_dist = cfg['tp_atr'] * atr
                        sl_dist = cfg['sl_atr'] * atr
                        sl = close_p - sl_dist
                        tp = close_p + tp_dist
                        pos = {
                            'type': 'BUY', 'entry': close_p + spread, 'sl': sl, 'tp': tp,
                            'tp_dist': tp_dist, 'be': False, 'units': 100.0 / sl_dist
                        }
                    # Sweep IB High then close back below with negative delta absorption
                    elif prev_r['high'] > ib_h and close_p < ib_h and delta < 0:
                        tp_dist = cfg['tp_atr'] * atr
                        sl_dist = cfg['sl_atr'] * atr
                        sl = close_p + sl_dist
                        tp = close_p - tp_dist
                        pos = {
                            'type': 'SELL', 'entry': close_p, 'sl': sl, 'tp': tp,
                            'tp_dist': tp_dist, 'be': False, 'units': 100.0 / sl_dist
                        }

        if len(trades) > 0:
            df_t = pd.DataFrame(trades)
            wins = df_t[df_t['pnl'] > 0]
            scratch = df_t[df_t['pnl'] == 0]
            losses = df_t[df_t['pnl'] < 0]
            wr = (len(wins) / len(df_t)) * 100.0
            win_be = ((len(wins) + len(scratch)) / len(df_t)) * 100.0
            gross_win = wins['pnl'].sum() if len(wins) > 0 else 0
            gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            net = df_t['pnl'].sum()

            flag = "[TARGET HIT >=70%]" if wr >= 70.0 or win_be >= 75.0 else "[BELOW]"
            print(f"{flag} {cfg['desc']}", flush=True)
            print(f"   Trades: {len(df_t):3d} | Wins: {len(wins):3d} | Scratch: {len(scratch):2d} | Losses: {len(losses):3d}", flush=True)
            print(f"   Pure Win Rate: {wr:.2f}% | Win+Scratch Rate: {win_be:.2f}% | PF: {pf:.2f} | Net: ${net:,.2f}\n", flush=True)


if __name__ == "__main__":
    for s in ["US100", "XAUUSD", "EURUSD"]:
        run_fast_calibration(s, bar_count=50000)
