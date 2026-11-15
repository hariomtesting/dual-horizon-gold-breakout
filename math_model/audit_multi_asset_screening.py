import MetaTrader5 as mt5
import json, os, pandas as pd, sys

cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_account.json'))
init_kwargs = {}
with open(cfg_path) as f:
    c = json.load(f)
    if c.get('path'): init_kwargs['path'] = c['path']
    if c.get('login'): init_kwargs['login'] = int(c['login'])
    if c.get('password'): init_kwargs['password'] = c['password']
    if c.get('server'): init_kwargs['server'] = c['server']

print("Initializing MT5...", flush=True)
if not mt5.initialize(**init_kwargs):
    print("MT5 init failed", flush=True)
    sys.exit(1)

print("Connected to MT5 successfully!", flush=True)
symbols = ['BTCUSD', 'ETHUSD', 'EURUSD', 'GBPUSD', 'NAS100.r']
for s in symbols:
    print(f"Selecting symbol {s}...", flush=True)
    sel = mt5.symbol_select(s, True)
    if not sel:
        print(f"Failed to select {s}", flush=True)
        continue
    info = mt5.symbol_info(s)
    print(f"Fetching rates for {s}...", flush=True)
    rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H1, 0, 15000)
    if rates is not None and len(rates) > 0 and info is not None:
        df = pd.DataFrame(rates)
        start = pd.to_datetime(df['time'].iloc[0], unit='s')
        end = pd.to_datetime(df['time'].iloc[-1], unit='s')
        print(f"--> {s:<10} | Bars: {len(rates)} | From {start} to {end} | Close: {df['close'].iloc[-1]} | Contract: {info.trade_contract_size} | Spread: {info.spread}", flush=True)
    else:
        print(f"--> {s:<10} | No rates available (error: {mt5.last_error()})", flush=True)

mt5.shutdown()
print("Done!", flush=True)
