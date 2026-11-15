"""
High-Speed Vectorized / Record Gold (XAUUSD) 1% Risk Optimization Engine
Pre-extracts daily session records to run 48 grid combinations in seconds.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from core.orderflow import OrderFlowEngine


def run_fast_gold_optimization():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50000)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Could not fetch rates for {symbol}")

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

    # Pre-extract volume profiles & session records
    day_sessions = []
    daily_profiles = {}
    for d in days:
        sub = df[df['date'] == d]
        if len(sub) >= 40:
            vp = of.calculate_volume_profile(sub)
            daily_profiles[d] = {
                'vpoc': vp.get('vpoc', (sub['high'].max() + sub['low'].min()) / 2.0)
            }

    for d_idx in range(1, len(days)):
        prev_d = days[d_idx - 1]
        curr_d = days[d_idx]
        if prev_d not in daily_profiles:
            continue
        prev_poc = daily_profiles[prev_d]['vpoc']
        df_day = df[df['date'] == curr_d]
        session_sub = df_day[(df_day['hour'] >= 7) & (df_day['hour'] <= 18)].reset_index(drop=True)
        if len(session_sub) < 15:
            continue
        records = session_sub[['datetime', 'high', 'low', 'close', 'open', 'atr', 'delta']].to_dict('records')
        day_sessions.append((curr_d, prev_poc, records))

    print(f"\n{'='*85}", flush=True)
    print(f"   HIGH-SPEED GOLD 1% OPTIMIZER ({len(day_sessions)} Trading Days Preloaded)", flush=True)
    print(f"{'='*85}\n", flush=True)

    target_ratios = [0.55, 0.65, 0.75, 0.85]
    sl_multipliers = [1.5, 1.75, 2.0]
    max_trades_options = [1, 2]
    be_options = [False, True]

    results = []

    for max_tr in max_trades_options:
        for sl_m in sl_multipliers:
            for tg_r in target_ratios:
                for be_flag in be_options:
                    trades = []

                    for curr_d, prev_poc, bars in day_sessions:
                        trades_today = 0
                        pos = None

                        for i in range(len(bars)):
                            r = bars[i]
                            high_p = r['high']
                            low_p = r['low']
                            close_p = r['close']
                            open_p = r['open']
                            atr = r['atr']
                            delta = r['delta']

                            # Manage position
                            if pos is not None:
                                if be_flag and not pos['be']:
                                    if pos['type'] == 'BUY' and high_p >= pos['entry'] + (pos['tp_dist'] * 0.5):
                                        pos['sl'] = pos['entry'] + spread
                                        pos['be'] = True
                                    elif pos['type'] == 'SELL' and low_p <= pos['entry'] - (pos['tp_dist'] * 0.5):
                                        pos['sl'] = pos['entry'] - spread
                                        pos['be'] = True

                                if pos['type'] == 'BUY':
                                    if low_p <= pos['sl']:
                                        pos['pnl'] = (pos['sl'] - pos['entry']) * pos['units']
                                        trades.append(pos)
                                        trades_today += 1
                                        pos = None
                                    elif high_p >= pos['tp']:
                                        pos['pnl'] = (pos['tp'] - pos['entry']) * pos['units']
                                        trades.append(pos)
                                        trades_today += 1
                                        pos = None
                                elif pos['type'] == 'SELL':
                                    if high_p >= pos['sl']:
                                        pos['pnl'] = (pos['entry'] - pos['sl']) * pos['units']
                                        trades.append(pos)
                                        trades_today += 1
                                        pos = None
                                    elif low_p <= pos['tp']:
                                        pos['pnl'] = (pos['entry'] - pos['tp']) * pos['units']
                                        trades.append(pos)
                                        trades_today += 1
                                        pos = None

                            # Entry
                            if pos is None and trades_today < max_tr and i >= 3:
                                full_dist = abs(close_p - prev_poc)

                                if 0.45 * atr <= full_dist <= 2.2 * atr:
                                    sl_dist = sl_m * atr
                                    tp_dist = tg_r * full_dist

                                    if close_p < prev_poc and delta > 0 and close_p > open_p:
                                        pos = {
                                            'type': 'BUY',
                                            'entry': close_p + spread,
                                            'sl': close_p - sl_dist,
                                            'tp': close_p + tp_dist,
                                            'tp_dist': tp_dist,
                                            'be': False,
                                            'entry_time': r['datetime'],
                                            'units': 100.0 / sl_dist
                                        }
                                    elif close_p > prev_poc and delta < 0 and close_p < open_p:
                                        pos = {
                                            'type': 'SELL',
                                            'entry': close_p,
                                            'sl': close_p + sl_dist,
                                            'tp': close_p - tp_dist,
                                            'tp_dist': tp_dist,
                                            'be': False,
                                            'entry_time': r['datetime'],
                                            'units': 100.0 / sl_dist
                                        }

                        if pos is not None:
                            last_b = bars[-1]
                            if pos['type'] == 'BUY':
                                pos['pnl'] = (last_b['close'] - pos['entry']) * pos['units']
                            else:
                                pos['pnl'] = (pos['entry'] - last_b['close']) * pos['units']
                            trades.append(pos)

                    if len(trades) > 30:
                        df_t = pd.DataFrame(trades)
                        wins = df_t[df_t['pnl'] > 0]
                        losses = df_t[df_t['pnl'] <= 0]
                        wr = (len(wins) / len(df_t)) * 100.0
                        gross_win = wins['pnl'].sum() if len(wins) > 0 else 0
                        gross_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
                        pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0

                        cap = 10000.0
                        peak = cap
                        max_dd = 0.0
                        for _, row in df_t.iterrows():
                            r_factor = row['pnl'] / 100.0
                            cap += cap * 0.01 * r_factor
                            if cap > peak:
                                peak = cap
                            dd = (peak - cap) / peak * 100.0
                            if dd > max_dd:
                                max_dd = dd

                        roi = (cap - 10000.0) / 10000.0 * 100.0
                        calmar = roi / max_dd if max_dd > 0 else 0

                        df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')
                        green_months_pct = (df_t.groupby('month')['pnl'].sum() > 0).mean() * 100.0

                        results.append({
                            'MaxTrades': max_tr,
                            'SL_Mult': sl_m,
                            'TargetRatio': tg_r,
                            'Breakeven': be_flag,
                            'Trades': len(df_t),
                            'WinRate': wr,
                            'PF': pf,
                            'FinalCapital': cap,
                            'NetROI': roi,
                            'MaxDD': max_dd,
                            'Calmar': calmar,
                            'GreenMonths': green_months_pct
                        })

    df_res = pd.DataFrame(results).sort_values('NetROI', ascending=False).reset_index(drop=True)

    print("--- TOP 10 HIGHEST RETURNING CONFIGURATIONS ON GOLD (AT 1% RISK) ---", flush=True)
    print(f"{'Rank':<4} | {'Trades/Day':<10} | {'SL Mult':<7} | {'TP Ratio':<8} | {'BE':<5} | {'Trades':<6} | {'Win Rate':<8} | {'PF':<5} | {'Net ROI':<9} | {'Max DD':<7} | {'Calmar':<6} | {'Green Mo':<8}", flush=True)
    print("-" * 115, flush=True)

    for i in range(min(10, len(df_res))):
        r = df_res.iloc[i]
        be_str = "YES" if r['Breakeven'] else "NO"
        print(f"#{i+1:<3} | {int(r['MaxTrades']):<10} | {r['SL_Mult']:<7.2f} | {r['TargetRatio']:<8.2f} | {be_str:<5} | {int(r['Trades']):<6} | {r['WinRate']:5.2f}%  | {r['PF']:4.2f} | +{r['NetROI']:6.2f}%  | {r['MaxDD']:5.2f}% | {r['Calmar']:5.2f} | {r['GreenMonths']:5.1f}%", flush=True)

    print("\n" + "=" * 85, flush=True)


if __name__ == "__main__":
    run_fast_gold_optimization()
