"""
Market Data Downloader & Preprocessor
Connects to MT5 or downloads from internet sources (e.g., yfinance)
Standardizes columns: [time, open, high, low, close, tick_volume, volume, session_id]
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
import MetaTrader5 as mt5


class MarketDataLoader:
    @staticmethod
    def load_from_mt5(symbol: str = "EURUSD", timeframe: int = mt5.TIMEFRAME_M5, count: int = 5000) -> pd.DataFrame:
        """Loads historical bars directly from MT5 terminal"""
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        
        # Ensure symbol is selected in Market Watch
        mt5.symbol_select(symbol, True)
        
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Failed to fetch rates for {symbol}: {mt5.last_error()}")
        
        df = pd.DataFrame(rates)
        df['datetime'] = pd.to_datetime(df['time'], unit='s')
        
        # Ensure 'volume' column exists (use real_volume if present, else tick_volume)
        if 'real_volume' in df.columns and df['real_volume'].sum() > 0:
            df['volume'] = df['real_volume']
        else:
            df['volume'] = df['tick_volume']
            
        df['session_id'] = df['datetime'].dt.floor('D') # Daily session anchor
        return df

    @staticmethod
    def load_from_yahoo(symbol: str = "EURUSD=X", period: str = "60d", interval: str = "5m") -> pd.DataFrame:
        """Loads intraday or daily data from Yahoo Finance"""
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            raise ValueError(f"No data returned for {symbol} from Yahoo")
            
        df = df.reset_index()
        # Rename columns to standard lowercase
        rename_map = {
            'Datetime': 'datetime',
            'Date': 'datetime',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }
        df = df.rename(columns=rename_map)
        df['tick_volume'] = df['volume']
        df['session_id'] = df['datetime'].dt.floor('D')
        return df

    @staticmethod
    def get_data(symbol: str = "EURUSD", timeframe_mt5=mt5.TIMEFRAME_M5, count: int = 5000) -> pd.DataFrame:
        """Attempts MT5 first; if unavailable, uses Yahoo Finance fallback"""
        try:
            print(f"[MarketData] Fetching {count} bars for {symbol} from MT5...")
            df = MarketDataLoader.load_from_mt5(symbol, timeframe_mt5, count)
            print(f"[MarketData] Successfully loaded {len(df)} bars from MT5.")
            return df
        except Exception as e:
            print(f"[MarketData] MT5 fetch error ({e}), falling back to Yahoo Finance...")
            yf_symbol = symbol + "=X" if len(symbol) == 6 else symbol
            if symbol == "XAUUSD":
                yf_symbol = "GC=F"
            df = MarketDataLoader.load_from_yahoo(yf_symbol, period="60d", interval="5m")
            print(f"[MarketData] Successfully loaded {len(df)} bars from Yahoo Finance.")
            return df

