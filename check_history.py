import MetaTrader5 as mt5
from datetime import datetime
import pandas as pd

if not mt5.initialize():
    print("Failed to initialize MT5")
    quit()

start = datetime(2020, 1, 1)
end = datetime(2026, 11, 1)

print("Checking MT5 historical availability 2020 - 2026:")
for sym in ['US100', 'XAUUSD', 'BTCUSD', 'EURUSD']:
    for tf_name, tf in [('M15', mt5.TIMEFRAME_M15), ('H1', mt5.TIMEFRAME_H1), ('H4', mt5.TIMEFRAME_H4), ('D1', mt5.TIMEFRAME_D1)]:
        rates = mt5.copy_rates_range(sym, tf, start, end)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df['dt'] = pd.to_datetime(df['time'], unit='s')
            print(f"{sym} {tf_name}: {len(rates)} bars | {df['dt'].iloc[0].date()} -> {df['dt'].iloc[-1].date()}")
        else:
            print(f"{sym} {tf_name}: 0 bars")

mt5.shutdown()

