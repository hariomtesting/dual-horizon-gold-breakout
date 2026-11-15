import time
import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 Init failed")
    exit(1)

acc = mt5.account_info()
term = mt5.terminal_info()

print("=" * 70)
print("BROKER CONNECTION & FRICTION AUDIT")
print("=" * 70)
print(f"Server        : {acc.server}")
print(f"Account       : {acc.login} ({acc.currency})")
print(f"Company       : {acc.company}")
print(f"Leverage      : 1:{acc.leverage}")
print(f"Ping Latency  : {term.ping_last / 1000.0:.2f} ms" if hasattr(term, 'ping_last') else "Ping: N/A")
print("=" * 70)

for s in ["XAUUSD", "EURUSD"]:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    tick = mt5.symbol_info_tick(s)
    spread_pips = (tick.ask - tick.bid) / (info.point * 10) if info.digits in [3, 5] else (tick.ask - tick.bid)
    print(f"Symbol        : {s}")
    print(f"  Bid / Ask   : {tick.bid:.4f} / {tick.ask:.4f}")
    print(f"  Live Spread : {tick.ask - tick.bid:.4f} (Raw points: {info.spread}, ~{spread_pips:.2f} pips)")
    print(f"  Contract Sz : {info.trade_contract_size}")
    print(f"  Min Lot     : {info.volume_min} | Step: {info.volume_step} | Max: {info.volume_max}")
    print(f"  Tick Size   : {info.trade_tick_size} | Tick Value: ${info.trade_tick_value}")
    print(f"  Stops Level : {info.trade_stops_level} points")
    print("-" * 70)

mt5.shutdown()

