"""
Live & Paper Trading Execution Engine for MetaTrader 5
Supports:
1. Paper Trading Mode (Zero risk simulated execution on live tick stream)
2. Live / Demo Execution Mode (Real orders sent via MT5 API with automated SL/TP)
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional, Dict, Any
import MetaTrader5 as mt5

from strategy.orderflow_strategy import OrderFlowStrategy
from core.orderflow import OrderFlowEngine


class MT5OrderFlowTrader:
    def __init__(
        self,
        symbol: str = "EURUSD",
        timeframe: int = mt5.TIMEFRAME_M5,
        paper_mode: bool = True,
        risk_per_trade: float = 0.01, # 1% risk per trade
        magic_number: int = 777888
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.paper_mode = paper_mode
        self.risk_per_trade = risk_per_trade
        self.magic_number = magic_number
        
        self.strategy = OrderFlowStrategy(tick_size=0.0001, vp_window=48)
        self.paper_balance = 10000.0
        self.paper_position: Optional[Dict[str, Any]] = None
        self.is_running = False

    def initialize_terminal(self) -> bool:
        """Initializes connection to MT5 terminal"""
        if not mt5.initialize():
            print(f"[LiveTrader] MT5 init failed: {mt5.last_error()}")
            return False
            
        if not mt5.symbol_select(self.symbol, True):
            print(f"[LiveTrader] Failed to select symbol {self.symbol}")
            return False
            
        acc = mt5.account_info()
        print(f"[LiveTrader] Connected to Account {acc.login} ({acc.server}) - Balance: ${acc.balance:.2f}")
        print(f"[LiveTrader] Mode: {'[PAPER TRADING - SIMULATED]' if self.paper_mode else '[LIVE / DEMO MT5 TRADING]'}")
        return True

    def calculate_lot_size(self, entry_price: float, sl_price: float) -> float:
        """Calculates lot size based on 1% equity risk and SL distance"""
        acc = mt5.account_info()
        equity = self.paper_balance if self.paper_mode else (acc.equity if acc else 10000.0)
        risk_amount = equity * self.risk_per_trade
        sl_distance = abs(entry_price - sl_price)
        
        if sl_distance <= 0:
            return 0.01

        # Forex standard lot: 1 lot = 100,000 units. Pip value approx $10 per pip on EURUSD
        symbol_info = mt5.symbol_info(self.symbol)
        min_lot = symbol_info.volume_min if symbol_info else 0.01
        max_lot = symbol_info.volume_max if symbol_info else 10.0
        lot_step = symbol_info.volume_step if symbol_info else 0.01

        # Position size in units = risk_amount / sl_distance
        units = risk_amount / sl_distance
        raw_lots = units / 100000.0
        
        # Round to lot step
        lots = round(raw_lots / lot_step) * lot_step
        lots = max(min_lot, min(lots, max_lot))
        return float(lots)

    def send_live_order(self, order_type: str, lots: float, sl: float, tp: float) -> bool:
        """Sends real order to MT5 terminal"""
        symbol_info = mt5.symbol_info_tick(self.symbol)
        if symbol_info is None:
            print("[LiveTrader] Tick info unavailable")
            return False

        price = symbol_info.ask if order_type == "BUY" else symbol_info.bid
        action_type = mt5.ORDER_TYPE_BUY if order_type == "BUY" else mt5.ORDER_TYPE_SELL

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lots,
            "type": action_type,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 20,
            "magic": self.magic_number,
            "comment": "OrderFlow Algo",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"[LiveTrader] Order failed! Retcode: {result.retcode}, comment: {result.comment}")
            return False
            
        print(f"[LiveTrader] Live {order_type} executed! Ticket: {result.order}, Lots: {lots}, Price: {price}, SL: {sl}, TP: {tp}")
        return True

    def run_cycle(self):
        """Processes one bar cycle to detect setups and manage positions"""
        # Fetch latest bars
        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 0, 150)
        if rates is None or len(rates) < 60:
            return

        df = pd.DataFrame(rates)
        df['datetime'] = pd.to_datetime(df['time'], unit='s')
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['session_id'] = df['datetime'].dt.floor('D')

        # Run Order Flow Strategy
        df_sig = self.strategy.generate_signals(df)
        latest = df_sig.iloc[-1]
        current_tick = mt5.symbol_info_tick(self.symbol)
        if not current_tick:
            return

        # Check existing positions in paper mode
        if self.paper_mode and self.paper_position:
            pos = self.paper_position
            bid = current_tick.bid
            ask = current_tick.ask
            
            # Check exit
            if pos['type'] == 'BUY':
                if bid <= pos['sl']:
                    pnl = (pos['sl'] - pos['entry_price']) * pos['lots'] * 100000
                    self.paper_balance += pnl
                    print(f"🛑 [PAPER STOP HIT] BUY closed at {pos['sl']:.5f} | PnL: ${pnl:.2f} | Balance: ${self.paper_balance:.2f}")
                    self.paper_position = None
                elif bid >= pos['tp']:
                    pnl = (pos['tp'] - pos['entry_price']) * pos['lots'] * 100000
                    self.paper_balance += pnl
                    print(f"🎯 [PAPER TARGET HIT] BUY closed at {pos['tp']:.5f} | PnL: ${pnl:.2f} | Balance: ${self.paper_balance:.2f}")
                    self.paper_position = None
            elif pos['type'] == 'SELL':
                if ask >= pos['sl']:
                    pnl = (pos['entry_price'] - pos['sl']) * pos['lots'] * 100000
                    self.paper_balance += pnl
                    print(f"🛑 [PAPER STOP HIT] SELL closed at {pos['sl']:.5f} | PnL: ${pnl:.2f} | Balance: ${self.paper_balance:.2f}")
                    self.paper_position = None
                elif ask <= pos['tp']:
                    pnl = (pos['entry_price'] - pos['tp']) * pos['lots'] * 100000
                    self.paper_balance += pnl
                    print(f"🎯 [PAPER TARGET HIT] SELL closed at {pos['tp']:.5f} | PnL: ${pnl:.2f} | Balance: ${self.paper_balance:.2f}")
                    self.paper_position = None

        # Check if new signal fired on the current bar
        if latest['signal'] != 0:
            order_type = "BUY" if latest['signal'] == 1 else "SELL"
            sl = latest['sl']
            tp = latest['tp']
            lots = self.calculate_lot_size(latest['close'], sl)

            print(f"⚡ [ORDERFLOW SIGNAL] {order_type} on {self.symbol} | Reason: {latest['reason']}")
            print(f"   Entry: {latest['close']:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Calculated Lots: {lots}")

            if self.paper_mode:
                if self.paper_position is None:
                    self.paper_position = {
                        'type': order_type,
                        'entry_price': latest['close'],
                        'sl': sl,
                        'tp': tp,
                        'lots': lots,
                        'entry_time': latest['datetime']
                    }
                    print(f"📝 [PAPER TRADE OPENED] {order_type} {lots} lots at {latest['close']:.5f}")
            else:
                # Check MT5 open positions with our magic number
                open_positions = mt5.positions_get(symbol=self.symbol)
                my_positions = [p for p in open_positions if p.magic == self.magic_number] if open_positions else []
                if len(my_positions) == 0:
                    self.send_live_order(order_type, lots, sl, tp)
                else:
                    print(f"[LiveTrader] Position already open for magic {self.magic_number}, skipping.")
        else:
            # Heartbeat print
            print(f"[LiveTrader Heartbeat {datetime.now().strftime('%H:%M:%S')}] {self.symbol} Close: {latest['close']:.5f} | VWAP: {latest['vwap']:.5f} | Delta: {latest['delta']:.1f} | CVD: {latest['cvd']:.1f}")

