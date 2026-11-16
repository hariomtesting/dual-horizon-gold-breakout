"""
========================================================================================
ON-DEMAND LIVE MT5 CHART GENERATOR FOR TELEGRAM BOT
Generates high-resolution candlestick snapshots of XAUUSD with EMA200 & Fractals
========================================================================================
"""

import os
import datetime
import pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import MetaTrader5 as mt5

def generate_live_gold_chart(output_path="reports/live_xauusd_chart.png", state=None):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    if not mt5.initialize():
        return None
        
    rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 50)
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df['dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
    ist = pytz.timezone('Asia/Kolkata')
    df['dt_ist'] = df['dt'].dt.tz_convert(ist)
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

    # Style: Dark Institutional Fintech Theme
    fig, ax = plt.subplots(figsize=(12, 6.5), facecolor='#0b1120')
    ax.set_facecolor('#0f172a')

    # Draw Candlesticks
    width = 0.025
    width2 = 0.005
    
    up = df[df.close >= df.open]
    down = df[df.close < df.open]

    # Up candles
    ax.bar(up['dt_ist'], up.close - up.open, width, bottom=up.open, color='#10b981', edgecolor='#059669', linewidth=0.8, label='Bullish')
    ax.bar(up['dt_ist'], up.high - up.close, width2, bottom=up.close, color='#10b981', linewidth=0.8)
    ax.bar(up['dt_ist'], up.low - up.open, width2, bottom=up.open, color='#10b981', linewidth=0.8)

    # Down candles
    ax.bar(down['dt_ist'], down.close - down.open, width, bottom=down.open, color='#ef4444', edgecolor='#dc2626', linewidth=0.8, label='Bearish')
    ax.bar(down['dt_ist'], down.high - down.open, width2, bottom=down.open, color='#ef4444', linewidth=0.8)
    ax.bar(down['dt_ist'], down.low - down.close, width2, bottom=down.close, color='#ef4444', linewidth=0.8)

    # 200 EMA
    ax.plot(df['dt_ist'], df['ema200'], color='#f59e0b', linewidth=2.0, label='200 EMA (Regime Filter)', linestyle='--')

    cur_p = df['close'].iloc[-1]
    last_dt = df['dt_ist'].iloc[-1]
    ax.axhline(cur_p, color='#38bdf8', linestyle=':', linewidth=1.2, alpha=0.8)
    ax.text(last_dt, cur_p, f"  ${cur_p:.2f} (Current)", color='#38bdf8', fontweight='bold', fontsize=9, va='center')

    # If state contains fractals/pending orders, plot them
    if state:
        h1 = state.get('horizons', {}).get('1H_chandelier_runner', {})
        pend = h1.get('pending_order')
        pos = h1.get('active_position')
        
        if pend:
            trig = pend.get('entry_trigger')
            sl = pend.get('sl')
            tp1 = pend.get('tp1')
            p_type = pend.get('type')
            p_col = '#10b981' if 'BUY' in p_type else '#ef4444'
            ax.axhline(trig, color=p_col, linestyle='-.', linewidth=1.8, label=f"Pending: {p_type} (${trig:.2f})")
            if sl: ax.axhline(sl, color='#f43f5e', linestyle=':', linewidth=1.2, label=f"Stop Loss (${sl:.2f})")
            if tp1: ax.axhline(tp1, color='#22c55e', linestyle=':', linewidth=1.2, label=f"TP1 Target (${tp1:.2f})")

        if pos:
            entry = pos.get('entry')
            sl = pos.get('sl')
            d = pos.get('dir')
            pos_col = '#06b6d4'
            ax.axhline(entry, color=pos_col, linestyle='-', linewidth=2.0, label=f"Active {d} (${entry:.2f})")
            if sl: ax.axhline(sl, color='#fb7185', linestyle='--', linewidth=1.5, label=f"Trailing SL (${sl:.2f})")

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b\n%H:%M IST', tz=ist))
    ax.tick_params(axis='x', colors='#94a3b8', labelsize=8)
    ax.tick_params(axis='y', colors='#94a3b8', labelsize=9)
    ax.grid(True, color='#1e293b', linestyle='--', alpha=0.7)

    # Title & Branding
    plt.title(f"XAUUSD (Spot Gold 1-Hour) — Live Institutional Fractal Telemetry", color='#f8fafc', fontsize=12, fontweight='bold', pad=12)
    ax.legend(loc='upper left', facecolor='#0f172a', edgecolor='#334155', labelcolor='#cbd5e1', fontsize=8.5)

    plt.tight_layout()
    plt.savefig(output_path, dpi=110, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    return output_path

if __name__ == '__main__':
    chart_file = generate_live_gold_chart()
    print("Generated live chart:", chart_file)
