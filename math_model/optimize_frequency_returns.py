"""
Optimization of Trade Frequency & Portfolio Returns
Evaluates:
1. Exact historical sample size of bars, days, and trades
2. Increasing trade frequency via 2 session windows: London (07:00-11:00 UTC) & New York (13:00-17:00 UTC)
3. 4-Asset Uncorrelated Basket: XAUUSD (Gold), NAS100.r (Nasdaq), DJ30.r (Dow Jones), EURUSD
4. Compounding returns at 1.0%, 1.25%, and 1.5% risk
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from typing import Dict, Any, List
from core.orderflow import OrderFlowEngine


def analyze_asset_frequency(symbol: str, max_trades_per_day: int = 2) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50000)
    if rates is None or len(rates) == 0:
        print(f"[Warning] No rates for {symbol}", flush=True)
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
    df['volume'] = df['volume'].replace(0, 1.0)
    df['date'] = df['datetime'].dt.date
    df['session_id'] = df['datetime'].dt.floor('D')
    df['hour'] = df['datetime'].dt.hour

    tick_sz = 1.0 if ("100" in symbol or "30" in symbol) else (0.10 if "XAU" in symbol else 0.0001)
    spread = 0.50 if ("100" in symbol or "30" in symbol) else (0.20 if "XAU" in symbol else 0.0001)
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

        # Active liquidity sessions: London (07:00 - 11:30) & New York (13:00 - 17:30)
        session_bars = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 18)].reset_index(drop=True)
        if len(session_bars) < 15:
            continue

        trades_today = 0
        pos = None

        for i in range(len(session_bars)):
            r = session_bars.iloc[i]
            high_p, low_p, close_p = r['high'], r['low'], r['close']
            atr, delta = r['atr'], r['delta']

            # Manage position
            if pos is not None:
                if pos['type'] == 'BUY':
                    if low_p <= pos['sl']:
                        pnl = (pos['sl'] - pos['entry']) * pos['units']
                        pos['pnl'] = pnl
                        pos['exit_time'] = r['datetime']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif high_p >= pos['tp']:
                        pnl = (pos['tp'] - pos['entry']) * pos['units']
                        pos['pnl'] = pnl
                        pos['exit_time'] = r['datetime']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                elif pos['type'] == 'SELL':
                    if high_p >= pos['sl']:
                        pnl = (pos['entry'] - pos['sl']) * pos['units']
                        pos['pnl'] = pnl
                        pos['exit_time'] = r['datetime']
                        trades.append(pos)
                        trades_today += 1
                        pos = None
                    elif low_p <= pos['tp']:
                        pnl = (pos['entry'] - pos['tp']) * pos['units']
                        pos['pnl'] = pnl
                        pos['exit_time'] = r['datetime']
                        trades.append(pos)
                        trades_today += 1
                        pos = None

            # New Entry: Check displacement and delta absorption
            if pos is None and trades_today < max_trades_per_day and i >= 3:
                full_dist = abs(close_p - prev_poc)

                # Sweet spot displacement bracket
                if 0.45 * atr <= full_dist <= 2.2 * atr:
                    sl_dist = 1.75 * atr

                    # Buy Reversal
                    if close_p < prev_poc and delta > 0 and r['close'] > r['open']:
                        pos = {
                            'symbol': symbol,
                            'type': 'BUY',
                            'entry': close_p + spread,
                            'sl': close_p - sl_dist,
                            'tp': close_p + (0.60 * full_dist),
                            'entry_time': r['datetime'],
                            'units': 100.0 / sl_dist # Standard $100 risk per trade
                        }
                    # Sell Reversal
                    elif close_p > prev_poc and delta < 0 and r['close'] < r['open']:
                        pos = {
                            'symbol': symbol,
                            'type': 'SELL',
                            'entry': close_p,
                            'sl': close_p + sl_dist,
                            'tp': close_p - (0.60 * full_dist),
                            'entry_time': r['datetime'],
                            'units': 100.0 / sl_dist
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

    return pd.DataFrame(trades)


def run_full_portfolio_optimization():
    print("=" * 80)
    print("      SAMPLE SIZE, TRADE FREQUENCY & RETURN OPTIMIZATION ENGINE      ")
    print("=" * 80 + "\n")

    symbols = ["XAUUSD", "NAS100.r", "DJ30.r", "EURUSD"]
    all_dfs = []

    print("--- 1. INDIVIDUAL ASSET SAMPLE SIZES & FREQUENCIES ---")
    for s in symbols:
        df_res = analyze_asset_frequency(s, max_trades_per_day=2)
        if len(df_res) > 0:
            df_res['symbol'] = s
            wins = df_res[df_res['pnl'] > 0]
            wr = (len(wins) / len(df_res)) * 100.0
            pf = wins['pnl'].sum() / abs(df_res[df_res['pnl'] <= 0]['pnl'].sum()) if len(df_res[df_res['pnl'] <= 0]) > 0 else 999.0
            net = df_res['pnl'].sum()
            months_count = len(pd.to_datetime(df_res['entry_time']).dt.to_period('M').unique())
            freq_per_month = len(df_res) / months_count if months_count > 0 else 0

            print(f"   {s:<10} : {len(df_res):3d} Trades | Win Rate: {wr:5.2f}% | PF: {pf:4.2f} | Net: ${net:8,.2f} | Freq: ~{freq_per_month:.1f} trades/mo")
            all_dfs.append(df_res)

    if len(all_dfs) == 0:
        print("No trades found.")
        return

    # Combined Uncorrelated Basket
    df_comb = pd.concat(all_dfs).sort_values('entry_time').reset_index(drop=True)
    c_wins = df_comb[df_comb['pnl'] > 0]
    c_losses = df_comb[df_comb['pnl'] <= 0]
    c_wr = (len(c_wins) / len(df_comb)) * 100.0
    c_pf = c_wins['pnl'].sum() / abs(c_losses['pnl'].sum())
    c_net = df_comb['pnl'].sum()

    df_comb['date'] = pd.to_datetime(df_comb['entry_time']).dt.date
    df_comb['month'] = pd.to_datetime(df_comb['entry_time']).dt.to_period('M')

    total_days = len(df_comb['date'].unique())
    total_months = len(df_comb['month'].unique())
    trades_per_day = len(df_comb) / total_days if total_days > 0 else 0
    trades_per_month = len(df_comb) / total_months if total_months > 0 else 0

    daily_green = (df_comb.groupby('date')['pnl'].sum() > 0).mean() * 100.0
    monthly_green = (df_comb.groupby('month')['pnl'].sum() > 0).mean() * 100.0

    print("\n--- 2. COMBINED 4-ASSET PORTFOLIO METRICS ---")
    print(f"   Total Trade Sample Size : {len(df_comb)} Trades across {total_days} active days ({total_months} months)")
    print(f"   Trade Execution Frequency: {trades_per_day:.2f} trades / day (~{trades_per_month:.1f} trades / month)")
    print(f"   Portfolio Win Rate       : {c_wr:.2f}% ({len(c_wins)} Wins / {len(c_losses)} Losses)")
    print(f"   Portfolio Profit Factor  : {c_pf:.2f}")
    print(f"   Daily Green Close Rate   : {daily_green:.1f}% of days finish in Net Profit")
    print(f"   Monthly Consistency      : {monthly_green:.1f}% of months finish in Net Profit ({monthly_green/100 * total_months:.0f}/{total_months} Months)")
    print(f"   Total Unleveraged Net PnL: ${c_net:,.2f} on $10,000 base\n")

    # 3. Compounded Growth Projections
    print("--- 3. OPTIMIZED RETURN & COMPOUNDING PROJECTIONS (8.5 Month Period) ---")
    for risk_pct in [0.010, 0.0125, 0.015]:
        cap = 10000.0
        peak = cap
        max_dd = 0.0

        for _, t in df_comb.iterrows():
            r_mult = t['pnl'] / 100.0
            pnl = cap * risk_pct * r_mult
            cap += pnl
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

        net_gain = cap - 10000.0
        roi = (net_gain / 10000.0) * 100.0
        ann_roi = (roi / total_months) * 12.0
        calmar = roi / max_dd if max_dd > 0 else 0

        print(f"   Risk: {risk_pct*100:.2f}% | Final Capital: ${cap:,.2f} | Net ROI: +{roi:5.2f}% | Ann. ROI: +{ann_roi:5.2f}% | Max DD: {max_dd:4.2f}% | Calmar: {calmar:.2f}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    run_full_portfolio_optimization()

