import sys, os, datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 init failed")
    sys.exit(1)

rates = mt5.copy_rates_range('XAUUSD', mt5.TIMEFRAME_M5, datetime.datetime(2022,1,1), datetime.datetime(2026,11,30))
mt5.shutdown()

df = pd.DataFrame(rates)
df['ist_dt'] = pd.to_datetime(df['time'], unit='s') + datetime.timedelta(hours=3)
df['year'] = df['ist_dt'].dt.year
df['date'] = df['ist_dt'].dt.date
df['hour'] = df['ist_dt'].dt.hour
df['minute'] = df['ist_dt'].dt.minute

rows = []
for yr, y_df in df.groupby('year'):
    if yr < 2022:
        continue
    daily_ranges = []
    session_ranges = []
    trend_runs = []
    
    for d, d_df in y_df.groupby('date'):
        d_range = d_df['high'].max() - d_df['low'].min()
        daily_ranges.append(d_range)
        sess = d_df[(d_df['hour'] >= 11) & (d_df['hour'] < 16)]
        if len(sess) >= 10:
            s_range = sess['high'].max() - sess['low'].min()
            session_ranges.append(s_range)
            # Check how much directional displacement occurred vs range (Efficiency)
            sess_displacement = abs(sess.iloc[-1]['close'] - sess.iloc[0]['open'])
            trend_runs.append(sess_displacement / s_range if s_range > 0 else 0)
            
    start_p = y_df.iloc[0]['open']
    end_p = y_df.iloc[-1]['close']
    drift = ((end_p - start_p) / start_p) * 100.0
    
    rows.append({
        'Year': yr,
        'Price Band': f"${y_df['low'].min():.0f} - ${y_df['high'].max():.0f}",
        'Net Year Drift': f"{drift:+.1f}%",
        'Avg Daily Range': f"${np.mean(daily_ranges):.2f}",
        'Avg 11-16 IST Volatility': f"${np.mean(session_ranges):.2f}",
        'Session Directional Efficiency': f"{np.mean(trend_runs)*100:.1f}%",
        'Days with Range > $25': f"{(np.array(daily_ranges) > 25).sum()} / {len(daily_ranges)} ({(np.array(daily_ranges) > 25).mean()*100:.1f}%)"
    })

print(pd.DataFrame(rows).to_string(index=False))

