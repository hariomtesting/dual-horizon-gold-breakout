import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 Init failed")
    exit(1)

candidates = ['GER40', 'DAX40', 'DE40', 'DE30', 'US30', 'DJ30', 'NAS100', 'USTEC', 'SPX500', 'US500', 'BTCUSD', 'ETHUSD']
found = []

for s in candidates:
    info = mt5.symbol_info(s)
    if info is not None:
        tick = mt5.symbol_info_tick(s)
        spread_val = (tick.ask - tick.bid) if tick else info.spread
        found.append({'symbol': s, 'digits': info.digits, 'spread': round(spread_val, 2), 'contract': info.trade_contract_size})

print("Available symbols on Vantage Markets:")
for f in found:
    print(f"  {f['symbol']:<10} | Spread: {f['spread']:<8} | Contract: {f['contract']}")

mt5.shutdown()

