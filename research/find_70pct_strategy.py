"""
Search & Discovery Engine for 70%+ Win-Rate Institutional Strategies
Tests high-probability statistical anomalies:
1. Setup A: Initial Balance Sweep Reversal to VWAP / IB Midpoint
2. Setup B: Virgin POC Magnet Touch
3. Setup C: VWAP 2.0 Sigma Mean Reversion to 1.0 Sigma Band
4. Setup D: Value Area Re-entry (80% Rule of Market Profile)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, List
import MetaTrader5 as mt5

from core.orderflow import OrderFlowEngine
from backtest.engine import BacktestEngine


def evaluate_setup_win_rate(name: str, trades: List[Dict[str, Any]], initial_balance: float = 10000.0) -> Dict[str, Any]:
    if len(trades) == 0:
        return {"name": name, "trades": 0, "win_rate": 0.0, "net_profit": 0.0, "pf": 0.0}

    df_t = pd.DataFrame(trades)
    wins = df_t[df_t['pnl'] > 0]
    losses = df_t[df_t['pnl'] <= 0]
    tot = len(df_t)
    wr = (len(wins) / tot) * 100.0

    gross_win = wins['pnl'].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    net_p = df_t['pnl'].sum()

    return {
        "name": name,
        "trades": tot,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": wr,
        "profit_factor": pf,
        "net_profit": net_p,
        "avg_win": float(wins['pnl'].mean()) if len(wins) > 0 else 0,
        "avg_loss": float(losses['pnl'].mean()) if len(losses) > 0 else 0,
        "trades_list": trades
    }


def test_70pct_setups(symbol: str = "US100", bar_count: int = 50000):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    print(f"\n{'='*75}")
    print(f"   HUNTING FOR 70%+ WIN-RATE STRATEGIES: {symbol}")
    print(f"{'='*75}\n", flush=True)

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bar_count)
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['date'] = df['datetime'].dt.date
    df['session_id'] = df['datetime'].dt.floor('D')

    tick_sz = 1.0 if ("100" in symbol or "30" in symbol) else (0.10 if "XAU" in symbol else 0.0001)
    spread = 0.50 if "100" in symbol else (0.20 if "XAU" in symbol else 0.0001)
    of = OrderFlowEngine(tick_size=tick_sz)

    df = of.calculate_vwap(df, 'session_id')
    df = of.calculate_delta(df)

    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()

    days = sorted(df['date'].unique())

    # =========================================================================
    # SETUP 1: The Initial Balance (IB) False Breakout Fade (Target: IB Midpoint)
    # The 60-min high/low is swept, candle rejects back inside IB range with Delta
    # Target: IB Midpoint (Quick high-probability target)
    # Stop: Just beyond the sweep high/low (tight risk)
    # =========================================================================
    trades_ib_sweep = []

    # =========================================================================
    # SETUP 2: The VWAP 2.0 Sigma Reversion to VWAP 1.0 Sigma
    # When price stretches to +-2.0 StdDev band, target mean reversion to +-1.0 band
    # (High probability quick reversion before full VWAP touch)
    # =========================================================================
    trades_vwap_revert = []

    # =========================================================================
    # SETUP 3: The Market Profile 80% Rule (Value Area Re-entry)
    # Dalton's rule: If price opens outside yesterday's Value Area and then accepts
    # two consecutive 5m bars inside the Value Area, there is an 80% probability
    # it fills the entire Value Area to the opposite side!
    # =========================================================================
    trades_va_rule = []

    for d_idx in range(1, len(days)):
        prev_date = days[d_idx - 1]
        curr_date = days[d_idx]

        df_prev = df[df['date'] == prev_date]
        df_curr = df[df['date'] == curr_date].reset_index(drop=True)

        if len(df_prev) < 50 or len(df_curr) < 50:
            continue

        vp_prev = of.calculate_volume_profile(df_prev)
        prev_poc = vp_prev.get('vpoc', (df_prev['high'].max() + df_prev['low'].min()) / 2.0)
        prev_vah = vp_prev.get('vah', df_prev['high'].max())
        prev_val = vp_prev.get('val', df_prev['low'].min())

        # Filter session hours: 07:00 UTC onwards
        df_session = df_curr[df_curr['datetime'].dt.hour >= 7].reset_index(drop=True)
        if len(df_session) < 15:
            continue

        ib_window = df_session.iloc[:12] # First 60 mins
        trade_session = df_session.iloc[12:].reset_index(drop=True)

        ib_high = ib_window['high'].max()
        ib_low = ib_window['low'].min()
        ib_mid = (ib_high + ib_low) / 2.0
        ib_range = ib_high - ib_low

        if ib_range <= 0:
            continue

        # --- Test Setup 1: IB Sweep Fade ---
        pos1 = None
        for i in range(len(trade_session)):
            r = trade_session.iloc[i]
            prev_r = trade_session.iloc[i - 1] if i > 0 else ib_window.iloc[-1]
            c_time, high_p, low_p, close_p, open_p = r['datetime'], r['high'], r['low'], r['close'], r['open']
            atr = r['atr']
            delta = r['delta']

            if pos1 is not None:
                # Check exit
                if pos1['type'] == 'BUY':
                    if low_p <= pos1['sl']:
                        pos1['pnl'] = (pos1['sl'] - pos1['entry_price']) * pos1['units']
                        trades_ib_sweep.append(pos1)
                        pos1 = None
                    elif high_p >= pos1['tp']:
                        pos1['pnl'] = (pos1['tp'] - pos1['entry_price']) * pos1['units']
                        trades_ib_sweep.append(pos1)
                        pos1 = None
                elif pos1['type'] == 'SELL':
                    if high_p >= pos1['sl']:
                        pos1['pnl'] = (pos1['entry_price'] - pos1['sl']) * pos1['units']
                        trades_ib_sweep.append(pos1)
                        pos1 = None
                    elif low_p <= pos1['tp']:
                        pos1['pnl'] = (pos1['entry_price'] - pos1['tp']) * pos1['units']
                        trades_ib_sweep.append(pos1)
                        pos1 = None

            if pos1 is None and c_time.hour < 16:
                # Sweep IB Low then close back above IB Low with positive delta
                if prev_r['low'] < ib_low and close_p > ib_low and delta > 0:
                    sl = min(low_p, prev_r['low']) - (0.2 * atr)
                    tp = ib_mid # Target midpoint
                    risk = close_p - sl
                    if risk > 0 and tp > close_p + (0.5 * risk):
                        pos1 = {
                            'type': 'BUY',
                            'entry_price': close_p + spread,
                            'sl': sl,
                            'tp': tp,
                            'units': 100.0 / risk
                        }

                # Sweep IB High then close back below IB High with negative delta
                elif prev_r['high'] > ib_high and close_p < ib_high and delta < 0:
                    sl = max(high_p, prev_r['high']) + (0.2 * atr)
                    tp = ib_mid
                    risk = sl - close_p
                    if risk > 0 and tp < close_p - (0.5 * risk):
                        pos1 = {
                            'type': 'SELL',
                            'entry_price': close_p,
                            'sl': sl,
                            'tp': tp,
                            'units': 100.0 / risk
                        }

        # --- Test Setup 2: VWAP 2.0 Band to 1.0 Band Quick Reversion ---
        pos2 = None
        for i in range(len(df_curr)):
            r = df_curr.iloc[i]
            prev_r = df_curr.iloc[i - 1] if i > 0 else df_curr.iloc[0]
            high_p, low_p, close_p, open_p = r['high'], r['low'], r['close'], r['open']
            vwap, u1, l1, u2, l2 = r['vwap'], r['vwap_upper1'], r['vwap_lower1'], r['vwap_upper2'], r['vwap_lower2']
            atr = r['atr']
            delta = r['delta']

            if pos2 is not None:
                if pos2['type'] == 'BUY':
                    if low_p <= pos2['sl']:
                        pos2['pnl'] = (pos2['sl'] - pos2['entry_price']) * pos2['units']
                        trades_vwap_revert.append(pos2)
                        pos2 = None
                    elif high_p >= pos2['tp']:
                        pos2['pnl'] = (pos2['tp'] - pos2['entry_price']) * pos2['units']
                        trades_vwap_revert.append(pos2)
                        pos2 = None
                elif pos2['type'] == 'SELL':
                    if high_p >= pos2['sl']:
                        pos2['pnl'] = (pos2['entry_price'] - pos2['sl']) * pos2['units']
                        trades_vwap_revert.append(pos2)
                        pos2 = None
                    elif low_p <= pos2['tp']:
                        pos2['pnl'] = (pos2['entry_price'] - pos2['tp']) * pos2['units']
                        trades_vwap_revert.append(pos2)
                        pos2 = None

            if pos2 is None and r['datetime'].hour >= 7 and r['datetime'].hour < 17:
                # Oversold: Tested Lower Band 2, delta turns positive
                if prev_r['low'] <= prev_r['vwap_lower2'] and close_p > l2 and delta > 0:
                    sl = low_p - (0.4 * atr)
                    tp = l1 # Target quick snap back to Lower Band 1 (or VWAP)
                    risk = close_p - sl
                    if risk > 0 and tp > close_p + (0.6 * risk):
                        pos2 = {'type': 'BUY', 'entry_price': close_p + spread, 'sl': sl, 'tp': tp, 'units': 100.0 / risk}

                # Overbought: Tested Upper Band 2, delta turns negative
                elif prev_r['high'] >= prev_r['vwap_upper2'] and close_p < u2 and delta < 0:
                    sl = high_p + (0.4 * atr)
                    tp = u1 # Target quick snap back to Upper Band 1
                    risk = sl - close_p
                    if risk > 0 and tp < close_p - (0.6 * risk):
                        pos2 = {'type': 'SELL', 'entry_price': close_p, 'sl': sl, 'tp': tp, 'units': 100.0 / risk}

        # --- Test Setup 3: Market Profile 80% Rule (Value Area Fill) ---
        pos3 = None
        for i in range(1, len(df_session)):
            r = df_session.iloc[i]
            prev_r = df_session.iloc[i - 1]
            high_p, low_p, close_p = r['high'], r['low'], r['close']
            atr = r['atr']

            if pos3 is not None:
                if pos3['type'] == 'BUY':
                    if low_p <= pos3['sl']:
                        pos3['pnl'] = (pos3['sl'] - pos3['entry_price']) * pos3['units']
                        trades_va_rule.append(pos3)
                        pos3 = None
                    elif high_p >= pos3['tp']:
                        pos3['pnl'] = (pos3['tp'] - pos3['entry_price']) * pos3['units']
                        trades_va_rule.append(pos3)
                        pos3 = None
                elif pos3['type'] == 'SELL':
                    if high_p >= pos3['sl']:
                        pos3['pnl'] = (pos3['entry_price'] - pos3['sl']) * pos3['units']
                        trades_va_rule.append(pos3)
                        pos3 = None
                    elif low_p <= pos3['tp']:
                        pos3['pnl'] = (pos3['entry_price'] - pos3['tp']) * pos3['units']
                        trades_va_rule.append(pos3)
                        pos3 = None

            if pos3 is None and r['datetime'].hour < 16:
                # Re-entered Value Area from below (Crossed above prev_val)
                if prev_r['close'] < prev_val and close_p > prev_val and r['delta'] > 0:
                    sl = prev_val - (0.5 * atr)
                    tp = prev_poc # Target VPOC
                    risk = close_p - sl
                    if risk > 0 and tp > close_p + (0.8 * risk):
                        pos3 = {'type': 'BUY', 'entry_price': close_p + spread, 'sl': sl, 'tp': tp, 'units': 100.0 / risk}

                # Re-entered Value Area from above (Crossed below prev_vah)
                elif prev_r['close'] > prev_vah and close_p < prev_vah and r['delta'] < 0:
                    sl = prev_vah + (0.5 * atr)
                    tp = prev_poc
                    risk = sl - close_p
                    if risk > 0 and tp < close_p - (0.8 * risk):
                        pos3 = {'type': 'SELL', 'entry_price': close_p, 'sl': sl, 'tp': tp, 'units': 100.0 / risk}

    # Evaluate results of all 3 setups
    res1 = evaluate_setup_win_rate("Setup 1: Initial Balance Sweep Reversal (Target: IB Mid)", trades_ib_sweep)
    res2 = evaluate_setup_win_rate("Setup 2: VWAP 2.0 Sigma Reversion to Band 1", trades_vwap_revert)
    res3 = evaluate_setup_win_rate("Setup 3: Market Profile 80% Rule (Value Area to VPOC)", trades_va_rule)

    for r in [res1, res2, res3]:
        print(f"[{r['name']}]")
        print(f"   Trades       : {r['trades']} trades")
        print(f"   Win Rate     : {r['win_rate']:.2f}% ({r['wins']} Wins / {r['losses']} Losses)")
        print(f"   Profit Factor: {r['profit_factor']:.2f}")
        print(f"   Net Profit   : ${r['net_profit']:,.2f}")
        print(f"   Avg Win/Loss : ${r['avg_win']:.2f} / ${r['avg_loss']:.2f}\n")

    return res1, res2, res3


if __name__ == "__main__":
    for s in ["US100", "XAUUSD", "EURUSD"]:
        test_70pct_setups(s, bar_count=50000)

