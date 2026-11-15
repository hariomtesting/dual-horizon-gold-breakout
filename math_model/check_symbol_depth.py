import MetaTrader5 as mt5
import datetime

mt5.initialize()
for s in ['DJ30.r', 'NAS100.r', 'BTCUSD', 'XAUUSD', 'EURUSD']:
    mt5.symbol_select(s, True)
    rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, datetime.datetime(2023,1,1), datetime.datetime(2026,11,1))
    if rates is not None and len(rates) > 0:
        t0 = datetime.datetime.fromtimestamp(rates[0]['time'])
        t1 = datetime.datetime.fromtimestamp(rates[-1]['time'])
        print(f"{s}: {len(rates)} M5 bars from {t0} to {t1}")
    else:
        print(f"{s}: NO DATA")
mt5.shutdown()

