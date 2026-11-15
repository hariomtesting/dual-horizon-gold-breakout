import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print("Failed to initialize MT5")
    quit()

print("Checking available M5 bars:")
for s in ['US100', 'XAUUSD', 'EURUSD']:
    rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M5, 0, 80000)
    if rates is not None and len(rates) > 0:
        df = pd.DataFrame(rates)
        df['dt'] = pd.to_datetime(df['time'], unit='s')
        days = (df['dt'].iloc[-1] - df['dt'].iloc[0]).days
        print(f"{s} M5: {len(rates):,} bars | {df['dt'].iloc[0].date()} -> {df['dt'].iloc[-1].date()} ({days} days, ~{days//30} months)")
    else:
        print(f"{s} M5: 0 bars")

mt5.shutdown()

