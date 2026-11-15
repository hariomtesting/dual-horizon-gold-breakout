import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import MetaTrader5 as mt5
import datetime
import pandas as pd
from backtest.engine import LiveMarketSimulator

def run_test():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M5, datetime.datetime(2026, 1, 1), datetime.datetime(2026, 11, 6))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    
    sim = LiveMarketSimulator(symbol="XAUUSD")
    
    # Simple signal generator: 12:00 PM Breakout with SL $7.50, TP $15.00
    def signal_fn(df_day, i, state):
        row = df_day.iloc[i]
        if row['ist_hour'] == 12 and row['ist_minute'] == 0:
            state['base'] = row['close']
            state['armed'] = True
            
        if state.get('armed', False) and not state.get('taken', False) and row['ist_hour'] < 14:
            base = state['base']
            # If triggered, mark taken
            if row['high'] >= base + 1.50:
                state['taken'] = True
                return {
                    'type': 'BUY',
                    'trigger': base + 1.50,
                    'sl': base + 1.50 - 7.50,
                    'tp': base + 1.50 + 15.00
                }
        return None

    report = sim.run_bars(df, signal_fn)
    sim.print_report(report)

if __name__ == "__main__":
    run_test()
