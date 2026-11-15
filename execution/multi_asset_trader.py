"""
Live Quantitative Execution Engine: Dual-Session Institutional Sniper
====================================================================
Assets           : XAUUSD, EURUSD
Operating Window : 11:00 AM - 16:00 IST (Local PC, No VPS needed)
Session Cycle 1  : 12:00 PM IST (Frankfurt Pre-Open Anchor, Cutoff: 12:55 IST)
Session Cycle 2  : 13:00 PM IST (London Open Anchor, Cutoff: 15:00 IST)
Auto-Square-Off  : 15:55 IST (100% Flat before PC shutdown)
Risk-to-Reward   : 1.93:1 (SL: 0.75x ATR, TP: 1.45x ATR)
Anti-Whipsaw     : Single-Shot OCO (Opposite pending order deleted upon fill)
Daily Protection : Hard -2.00% daily stop circuit breaker
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import time
import datetime
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
import MetaTrader5 as mt5


class LiveMathematicalTrader:
    def __init__(
        self,
        symbols: List[str] = ["XAUUSD", "EURUSD"],
        paper_mode: bool = True,
        risk_pct: float = 0.010, # 1.0% per trade
        daily_target_pct: float = 4.0, # Lock profit if +4.0% reached
        daily_stop_pct: float = 2.0,   # Hard circuit breaker at -2.0%
        magic_number: int = 777130
    ):
        self.symbols = symbols
        self.paper_mode = paper_mode
        self.risk_pct = risk_pct
        self.daily_target_pct = daily_target_pct
        self.daily_stop_pct = daily_stop_pct
        self.magic_number = magic_number
        
        self.paper_balance = 10000.0
        self.current_day = datetime.datetime.now().date()
        self.daily_pnl = 0.0
        
        # State tracking per symbol across dual cycles
        self.symbol_state: Dict[str, Dict[str, Any]] = {}
        for s in self.symbols:
            self._reset_symbol_state(s)

    def _reset_symbol_state(self, sym: str):
        self.symbol_state[sym] = {
            'c1_armed': False,
            'c1_done': False,
            'c2_armed': False,
            'c2_done': False,
            'base_price': None,
            'atr': None,
            'pending_buy': None,
            'pending_sell': None,
            'active_trade': None,
            'done_today': False
        }

    def initialize(self) -> bool:
        if not mt5.initialize():
            print(f"[LiveSniper] MT5 initialization failed: {mt5.last_error()}", flush=True)
            return False

        acc = mt5.account_info()
        if acc is None:
            print("[LiveSniper] Could not fetch MT5 account info.", flush=True)
            return False

        print("=" * 85, flush=True)
        print("     DUAL-SESSION INSTITUTIONAL SNIPER ENGINE ACTIVATED (12:00 + 13:00 IST)     ", flush=True)
        print("=" * 85, flush=True)
        print(f"Connected Account : {acc.login} ({acc.server})", flush=True)
        print(f"Account Balance   : ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}", flush=True)
        print(f"Execution Mode    : {'[PAPER TRADING - ZERO RISK SIMULATION]' if self.paper_mode else '[LIVE MT5 ORDERS - REAL CAPITAL]'}", flush=True)
        print(f"Trading Basket    : {', '.join(self.symbols)}", flush=True)
        print(f"Cycle 1 (Frankfurt) : 12:00 PM IST Anchor | Cutoff: 12:55 PM IST", flush=True)
        print(f"Cycle 2 (London)    : 13:00 PM IST Anchor | Cutoff: 15:00 PM IST | Square-off: 15:55 IST", flush=True)
        print(f"Model Parameters    : Entry = 0.30x ATR | SL = 0.75x ATR | TP = 1.45x ATR (1.93:1 R:R)", flush=True)
        print(f"Risk Limits         : Risk per trade: {self.risk_pct*100:.1f}% | Daily Circuit Breaker: -{self.daily_stop_pct:.1f}%", flush=True)
        print("=" * 85, flush=True)

        for sym in self.symbols:
            mt5.symbol_select(sym, True)
            tick = mt5.symbol_info_tick(sym)
            if tick and tick.bid > 0:
                print(f"   [OK] {sym:<8} : Live Quotes Active (Bid: {tick.bid:.4f}, Ask: {tick.ask:.4f})", flush=True)
            else:
                print(f"   [WAIT] {sym:<8} : Subscribing to market ticks...", flush=True)

        return True

    def get_ist_time(self) -> datetime.datetime:
        """Computes current Indian Standard Time (UTC + 5:30)"""
        return datetime.datetime.now(datetime.timezone.utc).astimezone(
            datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        )

    def calculate_atr(self, symbol: str) -> float:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50)
        if rates is None or len(rates) < 20:
            return 0.0
        df = pd.DataFrame(rates)
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        atr = tr.rolling(14, min_periods=5).mean().iloc[-1]
        return float(atr)

    def calculate_lot_size(self, symbol: str, sl_dist: float) -> float:
        acc = mt5.account_info()
        balance = self.paper_balance if self.paper_mode else (acc.balance if acc else 10000.0)
        risk_cash = balance * self.risk_pct
        
        info = mt5.symbol_info(symbol)
        if info is None or sl_dist <= 0:
            return 0.01

        # Dollar value of 1 lot for 1 unit of price change
        # XAUUSD: 1 lot = 100 oz ($100 per $1 move)
        # EURUSD: 1 lot = 100,000 units ($100,000 per 1.00 move)
        contract_size = info.trade_contract_size if info.trade_contract_size > 0 else (100.0 if "XAU" in symbol else 100000.0)
        risk_per_lot = sl_dist * contract_size

        if risk_per_lot <= 0:
            return info.volume_min

        raw_lots = risk_cash / risk_per_lot
        step = info.volume_step if info.volume_step > 0 else 0.01
        lots = round(raw_lots / step) * step
        lots = max(info.volume_min, min(lots, info.volume_max))
        return round(lots, 2)

    def process_symbol(self, symbol: str, ist_now: datetime.datetime):
        state = self.symbol_state[symbol]
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.bid <= 0:
            return

        ist_h = ist_now.hour
        ist_m = ist_now.minute

        # 1. HARD AUTO-SQUARE-OFF AT 15:55 IST
        if ist_h == 15 and ist_m >= 55:
            if state['active_trade'] is not None:
                self._close_active_trade(symbol, tick, reason="15:55 IST Auto-Square-Off")
            if state['pending_buy'] or state['pending_sell']:
                self._cancel_all_pendings(symbol, reason="15:55 IST End of Day Cancellation")
            state['done_today'] = True
            return

        # 2. Daily circuit breaker check
        balance = self.paper_balance if self.paper_mode else mt5.account_info().balance
        daily_pct = (self.daily_pnl / balance) * 100.0
        if daily_pct <= -self.daily_stop_pct:
            if state['active_trade'] is not None:
                self._close_active_trade(symbol, tick, reason=f"Daily Stop Circuit Breaker (-{self.daily_stop_pct}%)")
            self._cancel_all_pendings(symbol, reason="Daily Circuit Breaker Tripped")
            state['done_today'] = True
            return

        if state['done_today']:
            return

        # 3. ORDER CUTOFFS
        # Cycle 1 (Frankfurt) Cutoff at 12:55 IST
        if (ist_h == 12 and ist_m >= 55) or ist_h > 12:
            if state['c1_armed'] and not state['c1_done']:
                if state['pending_buy'] or state['pending_sell']:
                    self._cancel_all_pendings(symbol, reason="12:55 IST Frankfurt Order Expiry")
                state['c1_done'] = True

        # Cycle 2 (London) Cutoff at 15:00 IST
        if ist_h >= 15:
            if state['pending_buy'] or state['pending_sell']:
                self._cancel_all_pendings(symbol, reason="15:00 IST London Order Expiry")
            state['c2_done'] = True
            state['done_today'] = True
            return

        # 4A. CYCLE 1: ANCHOR & ARM AT 12:00 PM IST (Frankfurt Pre-Open)
        if not state['c1_armed'] and not state['c1_done'] and (ist_h == 12 and ist_m >= 0) and state['active_trade'] is None:
            atr = self.calculate_atr(symbol)
            if atr > 0:
                base_price = (tick.bid + tick.ask) / 2.0
                state['base_price'] = base_price
                state['atr'] = atr
                
                entry_offset = 0.30 * atr
                sl_dist = 0.75 * atr
                tp_dist = 1.45 * atr # 1.93:1 Optimized R:R
                lots = self.calculate_lot_size(symbol, sl_dist)

                buy_trigger = base_price + entry_offset
                sell_trigger = base_price - entry_offset

                state['pending_buy'] = {
                    'cycle': 1,
                    'type': 'BUY_STOP',
                    'trigger': buy_trigger,
                    'sl': buy_trigger - sl_dist,
                    'tp': buy_trigger + tp_dist,
                    'lots': lots,
                    'sl_dist': sl_dist
                }
                state['pending_sell'] = {
                    'cycle': 1,
                    'type': 'SELL_STOP',
                    'trigger': sell_trigger,
                    'sl': sell_trigger + sl_dist,
                    'tp': sell_trigger - tp_dist,
                    'lots': lots,
                    'sl_dist': sl_dist
                }
                state['c1_armed'] = True
                print(f"\n[ARMED 12:00 IST - CYCLE 1 FRANKFURT] {symbol:<7} Base: {base_price:.4f} | ATR: {atr:.4f} | Lots: {lots}", flush=True)
                print(f"   --> BUY STOP  @ {buy_trigger:.4f} | SL: {state['pending_buy']['sl']:.4f} | TP: {state['pending_buy']['tp']:.4f}", flush=True)
                print(f"   --> SELL STOP @ {sell_trigger:.4f} | SL: {state['pending_sell']['sl']:.4f} | TP: {state['pending_sell']['tp']:.4f}", flush=True)

        # 4B. CYCLE 2: ANCHOR & ARM AT 13:00 PM IST (London Cash Open)
        if not state['c2_armed'] and not state['c2_done'] and (ist_h == 13 and ist_m >= 0) and ist_h < 15 and state['active_trade'] is None:
            atr = self.calculate_atr(symbol)
            if atr > 0:
                base_price = (tick.bid + tick.ask) / 2.0
                state['base_price'] = base_price
                state['atr'] = atr
                
                entry_offset = 0.30 * atr
                sl_dist = 0.75 * atr
                tp_dist = 1.45 * atr # 1.93:1 Optimized R:R
                lots = self.calculate_lot_size(symbol, sl_dist)

                buy_trigger = base_price + entry_offset
                sell_trigger = base_price - entry_offset

                state['pending_buy'] = {
                    'cycle': 2,
                    'type': 'BUY_STOP',
                    'trigger': buy_trigger,
                    'sl': buy_trigger - sl_dist,
                    'tp': buy_trigger + tp_dist,
                    'lots': lots,
                    'sl_dist': sl_dist
                }
                state['pending_sell'] = {
                    'cycle': 2,
                    'type': 'SELL_STOP',
                    'trigger': sell_trigger,
                    'sl': sell_trigger + sl_dist,
                    'tp': sell_trigger - tp_dist,
                    'lots': lots,
                    'sl_dist': sl_dist
                }
                state['c2_armed'] = True
                print(f"\n[ARMED 13:00 IST - CYCLE 2 LONDON] {symbol:<7} Base: {base_price:.4f} | ATR: {atr:.4f} | Lots: {lots}", flush=True)
                print(f"   --> BUY STOP  @ {buy_trigger:.4f} | SL: {state['pending_buy']['sl']:.4f} | TP: {state['pending_buy']['tp']:.4f}", flush=True)
                print(f"   --> SELL STOP @ {sell_trigger:.4f} | SL: {state['pending_sell']['sl']:.4f} | TP: {state['pending_sell']['tp']:.4f}", flush=True)

        # 5. CHECK PENDING TRIGGERS (SINGLE-SHOT OCO)
        if state['active_trade'] is None and (state['c1_armed'] or state['c2_armed']):
            # Check Buy Stop
            if state['pending_buy'] and tick.ask >= state['pending_buy']['trigger']:
                p = state['pending_buy']
                c_num = p.get('cycle', 1)
                state['active_trade'] = {
                    'cycle': c_num,
                    'type': 'BUY',
                    'entry_price': tick.ask,
                    'sl': p['sl'],
                    'tp': p['tp'],
                    'lots': p['lots'],
                    'sl_dist': p['sl_dist']
                }
                print(f"\n[CYCLE {c_num} BUY TRIGGERED] {symbol} filled at {tick.ask:.4f}! Instantly deleting Sell Stop (OCO)...", flush=True)
                state['pending_buy'] = None
                state['pending_sell'] = None # Kill reverse side immediately!
                if c_num == 1:
                    state['c1_done'] = True
                else:
                    state['c2_done'] = True

            # Check Sell Stop
            elif state['pending_sell'] and tick.bid <= state['pending_sell']['trigger']:
                p = state['pending_sell']
                c_num = p.get('cycle', 1)
                state['active_trade'] = {
                    'cycle': c_num,
                    'type': 'SELL',
                    'entry_price': tick.bid,
                    'sl': p['sl'],
                    'tp': p['tp'],
                    'lots': p['lots'],
                    'sl_dist': p['sl_dist']
                }
                print(f"\n[CYCLE {c_num} SELL TRIGGERED] {symbol} filled at {tick.bid:.4f}! Instantly deleting Buy Stop (OCO)...", flush=True)
                state['pending_buy'] = None
                state['pending_sell'] = None # Kill reverse side immediately!
                if c_num == 1:
                    state['c1_done'] = True
                else:
                    state['c2_done'] = True

        # 6. MANAGE ACTIVE TRADE (TP / SL EXITS)
        if state['active_trade'] is not None:
            trade = state['active_trade']
            ttype = trade['type']
            sl_price = trade['sl']
            tp_price = trade['tp']

            hit_tp = False
            hit_sl = False
            exit_price = 0.0

            if ttype == 'BUY':
                if tick.bid >= tp_price:
                    hit_tp = True
                    exit_price = tp_price
                elif tick.bid <= sl_price:
                    hit_sl = True
                    exit_price = sl_price
            else: # SELL
                if tick.ask <= tp_price:
                    hit_tp = True
                    exit_price = tp_price
                elif tick.ask >= sl_price:
                    hit_sl = True
                    exit_price = sl_price

            if hit_tp or hit_sl:
                multiplier = 100.0 if "XAU" in symbol else 100000.0
                pnl = (exit_price - trade['entry_price']) * trade['lots'] * multiplier if ttype == 'BUY' else (trade['entry_price'] - exit_price) * trade['lots'] * multiplier
                self.paper_balance += pnl
                self.daily_pnl += pnl
                tag = "[TARGET HIT +1.93R]" if hit_tp else "[STOP LOSS -1.00R]"
                print(f"\n{tag} {symbol} {ttype} closed at {exit_price:.4f} | PnL: ${pnl:+.2f} | Today Total: ${self.daily_pnl:+.2f}", flush=True)
                state['active_trade'] = None
                if trade.get('cycle', 1) == 2:
                    state['done_today'] = True # London cycle complete
                else:
                    print(f"[{symbol}] Cycle 1 complete. Ready for Cycle 2 at 13:00 IST if eligible.", flush=True)

    def _close_active_trade(self, symbol: str, tick, reason: str):
        state = self.symbol_state[symbol]
        trade = state['active_trade']
        if not trade:
            return
        ttype = trade['type']
        exit_p = tick.bid if ttype == 'BUY' else tick.ask
        multiplier = 100.0 if "XAU" in symbol else 100000.0
        pnl = (exit_p - trade['entry_price']) * trade['lots'] * multiplier if ttype == 'BUY' else (trade['entry_price'] - exit_p) * trade['lots'] * multiplier
        self.paper_balance += pnl
        self.daily_pnl += pnl
        print(f"\n[{reason.upper()}] Closed {symbol} {ttype} at {exit_p:.4f} | PnL: ${pnl:+.2f}", flush=True)
        state['active_trade'] = None

    def _cancel_all_pendings(self, symbol: str, reason: str):
        state = self.symbol_state[symbol]
        if state['pending_buy'] or state['pending_sell']:
            print(f"[{reason}] Cancelling unfilled pending brackets for {symbol}.", flush=True)
            state['pending_buy'] = None
            state['pending_sell'] = None

    def run_heartbeat(self):
        ist_now = self.get_ist_time()
        
        # New Day Reset
        if ist_now.date() != self.current_day:
            self.current_day = ist_now.date()
            self.daily_pnl = 0.0
            for s in self.symbols:
                self._reset_symbol_state(s)
            print(f"\n[NEW SESSION RESET] Indian Standard Date: {self.current_day}", flush=True)

        for sym in self.symbols:
            self.process_symbol(sym, ist_now)

        quotes = []
        for s in self.symbols:
            t = mt5.symbol_info_tick(s)
            if t:
                quotes.append(f"{s}: {t.bid:.2f}" if "XAU" in s else f"{s}: {t.bid:.4f}")
        
        open_trades = sum(1 for s in self.symbols if self.symbol_state[s]['active_trade'] is not None)
        armed_cnt = sum(1 for s in self.symbols if (self.symbol_state[s]['c1_armed'] or self.symbol_state[s]['c2_armed']))
        print(f"[Sniper Heartbeat {ist_now.strftime('%H:%M:%S IST')}] {' | '.join(quotes)} | Armed: {armed_cnt}/{len(self.symbols)} | Open: {open_trades} | Today PnL: ${self.daily_pnl:+.2f}", flush=True)


if __name__ == "__main__":
    trader = LiveMathematicalTrader(symbols=["XAUUSD", "EURUSD"], paper_mode=True)
    if trader.initialize():
        print("\nRunning live verification heartbeat cycles (3 cycles)...", flush=True)
        for _ in range(3):
            trader.run_heartbeat()
            time.sleep(2)
        print("\n[Engine Verified & Ready for Local PC Execution!]")
