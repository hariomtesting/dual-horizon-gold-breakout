"""
Multi-Horizon Quantitative Validator
Tests the Mathematical Model across:
1. Daily Horizon (Target: 70%+ Green Days)
2. Weekly Horizon (Target: 80%+ Green Weeks)
3. Monthly Horizon (Target: 85%+ Green Months)
4. Yearly Horizon (Target: 100% Profitable Years, Calmar > 2.5)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from typing import Dict, Any, List

from math_model.engine import QuantitativeMathematicalModel


def run_multi_horizon_validation(symbol: str = "XAUUSD", bar_count: int = 50000) -> Dict[str, Any]:
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    print(f"\n{'='*80}", flush=True)
    print(f"   VALIDATING MATHEMATICAL MODEL ACROSS MULTI-HORIZONS: {symbol}", flush=True)
    print(f"{'='*80}\n", flush=True)

    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bar_count)
    if rates is None or len(rates) == 0:
        print(f"[Validator] Could not fetch rates for {symbol}.", flush=True)
        return {}

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['session_id'] = df['datetime'].dt.floor('D')
    df['hour'] = df['datetime'].dt.hour

    tick_sz = 1.0 if ("100" in symbol or "30" in symbol) else (0.10 if "XAU" in symbol else 0.0001)
    spread = 0.50 if "100" in symbol else (0.20 if "XAU" in symbol else 0.0001)

    model = QuantitativeMathematicalModel(symbol, tick_size=tick_sz, min_win_prob=0.60)

    days = sorted(df['date'].unique())

    trades = []
    capital = 10000.0

    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]

        df_prev = df[df['date'] == prev_d]
        df_curr = df[df['date'] == curr_d].reset_index(drop=True)

        if len(df_prev) < 40 or len(df_curr) < 40:
            continue

        model.update_historical_context(df_prev)

        session_bars = df_curr[(df_curr['hour'] >= 7) & (df_curr['hour'] <= 16)].reset_index(drop=True)
        if len(session_bars) < 15:
            continue

        pos = None

        for i in range(len(session_bars)):
            r = session_bars.iloc[i]
            high_p, low_p, close_p = r['high'], r['low'], r['close']

            # Manage active position
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

            # Evaluate new signal
            if pos is None and i >= 10:
                sub_history = df_curr.iloc[:i+1]
                sig = model.evaluate_mathematical_edge(sub_history)
                if sig:
                    lots = model.allocator.calculate_lot_size(
                        equity=capital,
                        entry_price=sig['entry_price'],
                        stop_price=sig['sl'],
                        symbol=symbol,
                        win_prob=sig['win_probability'],
                        win_loss_ratio=0.75
                    )
                    sl_dist = abs(sig['entry_price'] - sig['sl'])
                    units = (capital * 0.01) / sl_dist # Standardized 1% Kelly unit

                    pos = {
                        'symbol': symbol,
                        'type': sig['action'],
                        'entry_time': r['datetime'],
                        'entry': sig['entry_price'],
                        'sl': sig['sl'],
                        'tp': sig['tp'],
                        'units': units,
                        'lots': lots,
                        'win_prob': sig['win_probability'],
                        'reason': sig['reason']
                    }

        # End of session close
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

    df_t = pd.DataFrame(trades)
    if len(df_t) == 0:
        print("[Validator] No trades executed.", flush=True)
        return {}

    # Multi-horizon aggregations
    df_t['date'] = pd.to_datetime(df_t['entry_time']).dt.date
    df_t['week'] = pd.to_datetime(df_t['entry_time']).dt.to_period('W')
    df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')
    df_t['year'] = pd.to_datetime(df_t['entry_time']).dt.year

    # 1. Trade Horizon
    wins = df_t[df_t['pnl'] > 0]
    losses = df_t[df_t['pnl'] <= 0]
    wr = (len(wins) / len(df_t)) * 100.0
    pf = wins['pnl'].sum() / abs(losses['pnl'].sum()) if len(losses) > 0 else 999.0
    tot_pnl = df_t['pnl'].sum()

    # 2. Daily Horizon
    daily_pnl = df_t.groupby('date')['pnl'].sum()
    green_days = (daily_pnl > 0).sum()
    tot_days = len(daily_pnl)
    daily_green_pct = (green_days / tot_days) * 100.0

    # 3. Weekly Horizon
    weekly_pnl = df_t.groupby('week')['pnl'].sum()
    green_weeks = (weekly_pnl > 0).sum()
    tot_weeks = len(weekly_pnl)
    weekly_green_pct = (green_weeks / tot_weeks) * 100.0

    # 4. Monthly Horizon
    monthly_pnl = df_t.groupby('month')['pnl'].sum()
    green_months = (monthly_pnl > 0).sum()
    tot_months = len(monthly_pnl)
    monthly_green_pct = (green_months / tot_months) * 100.0

    # 5. Yearly Horizon
    yearly_pnl = df_t.groupby('year')['pnl'].sum()
    green_years = (yearly_pnl > 0).sum()
    tot_years = len(yearly_pnl)
    yearly_green_pct = (green_years / tot_years) * 100.0

    print("--- 1. TRADE HORIZON METRICS ---")
    print(f"   Total Executions   : {len(df_t)}")
    print(f"   Mathematical Win % : {wr:.2f}% ({len(wins)} W / {len(losses)} L)")
    print(f"   Profit Factor      : {pf:.2f}")
    print(f"   Total Net PnL      : ${tot_pnl:,.2f}\n")

    print("--- 2. MULTI-HORIZON PROFITABILITY LAW ---")
    print(f"   DAILY HORIZON   : {daily_green_pct:5.1f}% Green Days   ({green_days}/{tot_days} Active Days)")
    print(f"   WEEKLY HORIZON  : {weekly_green_pct:5.1f}% Green Weeks  ({green_weeks}/{tot_weeks} Weeks)")
    print(f"   MONTHLY HORIZON : {monthly_green_pct:5.1f}% Green Months ({green_months}/{tot_months} Months)")
    print(f"   YEARLY HORIZON  : {yearly_green_pct:5.1f}% Green Years  ({green_years}/{tot_years} Years)\n")

    return {
        'symbol': symbol,
        'trades': len(df_t),
        'win_rate': wr,
        'pf': pf,
        'net_pnl': tot_pnl,
        'daily_green_pct': daily_green_pct,
        'weekly_green_pct': weekly_green_pct,
        'monthly_green_pct': monthly_green_pct,
        'yearly_green_pct': yearly_green_pct,
        'trades_df': df_t
    }


if __name__ == "__main__":
    run_multi_horizon_validation("XAUUSD", bar_count=50000)
    run_multi_horizon_validation("NAS100.r", bar_count=50000)
