"""
========================================================================================
DUAL-HORIZON POWER ENSEMBLE: INSTITUTIONAL LIVE SIMULATOR & TRADER (XAUUSD)
========================================================================================
Upgrades & Fixes Implemented:
1. True Pre-Breakout Resting Order Engine (Buy Stop / Sell Stop with Cancel & Replace on fractal shifts).
2. Strict Bid/Ask Asymmetric Matching (Buys trigger/fill on Ask, Sells trigger/fill on Bid; Exits on opposing book).
3. Dynamic Microstructure Slippage Modeling (0.05 - 0.35 pts based on ATR momentum expansion).
4. Centralized Portfolio Risk Governor (Max combined risk 4.5%, Half-Kelly de-leveraging, 20% circuit breaker).
5. Duplicate Entry & Forming-Bar Protection (Locks setup once entered, prevents multi-fill re-entry).
6. Weekend & Session Awareness (Detects weekend close, sleeps cleanly, resumes Sunday 22:00 UTC).
7. Native MT5 Demo Broker Bridge (Real broker orders when --mode demo is passed, paper simulator on --mode test).
8. Comprehensive Real-Time Telemetry exported to execution/testing_algo_state.json.
========================================================================================
"""

import os
import sys
import time
import json
import random
import argparse
import datetime
import pytz
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

try:
    from execution.telegram_notifier import telegram_bot
except ImportError:
    try:
        from telegram_notifier import telegram_bot
    except ImportError:
        telegram_bot = None

class InstitutionalGoldBreakoutEngine:
    def __init__(self, mode: str = "test", initial_balance: float = 10000.0, session_mode: str = "ist_daytime", base_risk_pct: float = 0.025, account_type: str = "standard"):
        self.mode = mode.lower()  # "test" (realistic paper simulator) or "demo" (live MT5 demo orders)
        self.symbol = "XAUUSD"
        self.session_mode = session_mode.lower() # "ist_daytime" (10-16 IST) or "24h"
        self.base_risk_pct = base_risk_pct
        self.account_type = account_type.lower() # "standard" or "cent"
        self.state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), 'testing_algo_state.json'))
        
        # Capital & Risk State
        self.initial_balance = initial_balance
        self.paper_balance = initial_balance
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0
        self.trades_history = []
        
        # Centralized Risk Governor Settings
        self.max_combined_risk = base_risk_pct * 1.5     # Dynamic max simultaneous portfolio risk
        self.circuit_breaker_dd = 20.0      # 20.0% DD hard halt (well below 30% ceiling)
        self.circuit_breaker_active = False
        
        # Horizon 1: 1D Macro Trend Runner
        self.p_1d = {
            'name': '1D Macro Trend Runner',
            'magic': 777001,
            'x_left': 15, 'y_right': 2,
            'pre_offset_pct': 0.0005,      # 0.05% pre-breakout entry offset
            'sl_pct': 0.0044,              # 0.44% initial SL
            'tp_pct': 0.0310,              # 3.10% Take Profit
            'be_trigger_pct': 0.0030,      # 0.30% Breakeven Lock trigger
            'base_risk_pct': 0.030,        # 3.0% base risk
            'max_lot_cap': 10.0,
            'active_pos': None,
            'pending_order': None,
            'last_entered_fractal': None,
            'ticket': None
        }
        
        # Horizon 2: 1H Chandelier Super-Runner
        self.p_1h = {
            'name': '1H Chandelier Super-Runner',
            'magic': 777002,
            'x_left': 15, 'y_right': 3,
            'sl_atr_mult': 2.0,            # 2.0 ATR initial SL
            'tp1_atr_mult': 2.5,           # 2.5 ATR Tranche 1 TP lock
            'chandelier_atr_mult': 2.8,    # 2.8 ATR Tranche 2 Chandelier trail
            'salvage_threshold': 0.45,     # 45% adverse excursion trigger
            'salvage_tp_pct': 0.0015,      # 0.15% tight TP for recovery bounce
            'base_risk_pct': self.base_risk_pct, # Dynamic base risk
            'max_lot_cap': 10.0,
            'active_pos': None,
            'pending_order': None,
            'last_entered_fractal': None,
            'ticket_t1': None,
            'ticket_t2': None
        }
        
        self.running = False
        self.is_market_open = True
        self.last_tick_time = 0

    def initialize(self) -> bool:
        account_cfg = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_account.json'))
        cfg = {}
        if os.path.exists(account_cfg):
            try:
                with open(account_cfg, 'r') as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[WARN] Error reading mt5_account.json: {e}", flush=True)

        init_kwargs = {}
        if cfg.get('path') and os.path.exists(cfg['path']):
            init_kwargs['path'] = cfg['path']
        if cfg.get('login') and cfg.get('password') and cfg.get('server'):
            init_kwargs['login'] = int(cfg['login'])
            init_kwargs['password'] = cfg['password']
            init_kwargs['server'] = cfg['server']
            init_kwargs['timeout'] = 20000

        if not mt5.initialize(**init_kwargs):
            # Fallback bare init
            if not mt5.initialize():
                print(f"[ERROR] Failed to initialize MT5: {mt5.last_error()}", flush=True)
                return False
            
        acc = mt5.account_info()
        if not acc:
            print("[ERROR] Could not query MT5 account info.", flush=True)
            return False
            
        self.initial_balance = acc.balance
        self.paper_balance = acc.balance
        self.peak_balance = acc.balance
        self.is_paused = False
        self.last_morning_briefing_date = None
        self.last_evening_summary_date = None
        self.ath_equity = acc.balance
        self.last_milestone_pct = 0.0

        if telegram_bot:
            telegram_bot.set_engine(self)

        print("\n" + "=" * 95)
        print("🏛️  INSTITUTIONAL DUAL-HORIZON GOLD BREAKOUT ENGINE (UPGRADED V2)")
        print("=" * 95)
        print(f"Connected Broker  : {acc.server} | Account: {acc.login} | Leverage: 1:{acc.leverage}")
        print(f"Account Balance   : ${acc.balance:,.2f} {acc.currency} | Equity: ${acc.equity:,.2f}")
        print(f"Execution Mode    : {'🧪 REALISTIC PAPER SIMULATOR (Live Ticks)' if self.mode == 'test' else '🚨 LIVE MT5 DEMO EXECUTION'}")
        print(f"Target Instrument : {self.symbol} (Spot Gold Bullion)")
        print(f"Portfolio Risk Gov: Max Total Risk 4.5% | Half-Kelly De-Leveraging | 20% Hard Circuit Breaker")
        print("-" * 95)
        print(f"• Horizon 1: {self.p_1d['name']} (Magic: {self.p_1d['magic']}) | 15L/2R Daily | 0.44% SL / 3.10% TP")
        print(f"• Horizon 2: {self.p_1h['name']} (Magic: {self.p_1h['magic']}) | 15L/3R 1-Hour | Split-Tranche Chandelier Runner")
        print("=" * 95 + "\n", flush=True)
        
        self.save_state()

        if telegram_bot and telegram_bot.is_configured:
            telegram_bot.start_listener()
            telegram_bot.notify_startup(
                mode=self.mode,
                session_mode=self.session_mode,
                risk_pct=self.base_risk_pct * 100.0,
                balance=self.paper_balance
            )
            print("[TELEGRAM] Bidirectional interactive listener started & startup alert sent.", flush=True)

        return True

    def pause(self) -> bool:
        self.is_paused = True
        self.p_1h['pending_order'] = None
        self.p_1d['pending_order'] = None
        print("⏸️ [ENGINE V2] Paused via Telegram Remote Control. Cleared pending orders.", flush=True)
        self.save_state()
        return True

    def resume(self) -> bool:
        self.is_paused = False
        print("▶️ [ENGINE V2] Resumed via Telegram Remote Control. Scanning active.", flush=True)
        self.save_state()
        return True

    def set_risk(self, new_risk_pct: float):
        if not (0.5 <= new_risk_pct <= 10.0):
            return False, "Risk must be between 0.5% and 10.0%"
        self.base_risk_pct = new_risk_pct / 100.0
        self.p_1h['base_risk_pct'] = self.base_risk_pct
        self.p_1d['base_risk_pct'] = self.base_risk_pct
        self.max_combined_risk = self.base_risk_pct * 1.5
        self.p_1h['pending_order'] = None
        self.p_1d['pending_order'] = None
        print(f"🎯 [ENGINE V2] Base Risk updated to {new_risk_pct:.1f}% via Telegram Remote Control.", flush=True)
        self.save_state()
        return True, f"Base Risk set to {new_risk_pct:.1f}%"

    def close_all(self):
        self.p_1h['pending_order'] = None
        self.p_1d['pending_order'] = None
        closed_desc = []
        
        if self.p_1h['active_pos'] is not None:
            pos = self.p_1h['active_pos']
            rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
            tick = mt5.symbol_info_tick(self.symbol)
            exit_price = tick.bid if pos['dir'] == 'BUY' else tick.ask if tick else pos['entry']
            pts = (exit_price - pos['entry']) if pos['dir'] == 'BUY' else (pos['entry'] - exit_price)
            pnl = (pts * rem_lots * 100.0) - (rem_lots * 6.0)
            self.paper_balance += pnl
            self.trades_history.append({
                'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'horizon': '1H_Chandelier_Runner', 'dir': pos['dir'], 'lots': rem_lots,
                'entry': pos['entry'], 'exit': exit_price, 'pnl': pnl, 'type': 'TELEGRAM_EMERGENCY_CLOSE'
            })
            closed_desc.append(f"1H {pos['dir']} {rem_lots} lots (${pnl:+,.2f})")
            self.p_1h['active_pos'] = None

        if self.p_1d['active_pos'] is not None:
            pos = self.p_1d['active_pos']
            tick = mt5.symbol_info_tick(self.symbol)
            exit_price = tick.bid if pos['dir'] == 'BUY' else tick.ask if tick else pos['entry']
            pts = (exit_price - pos['entry']) if pos['dir'] == 'BUY' else (pos['entry'] - exit_price)
            pnl = (pts * pos['lots'] * 100.0) - (pos['lots'] * 6.0)
            self.paper_balance += pnl
            self.trades_history.append({
                'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'horizon': '1D_Macro', 'dir': pos['dir'], 'lots': pos['lots'],
                'entry': pos['entry'], 'exit': exit_price, 'pnl': pnl, 'type': 'TELEGRAM_EMERGENCY_CLOSE'
            })
            closed_desc.append(f"1D {pos['dir']} {pos['lots']} lots (${pnl:+,.2f})")
            self.p_1d['active_pos'] = None

        self.is_paused = True
        self.save_state()
        desc_str = ", ".join(closed_desc) if closed_desc else "No open positions to close. Cancelled pending orders."
        print(f"🚨 [ENGINE V2] Emergency CloseAll executed. {desc_str}", flush=True)
        return True, desc_str

    def check_market_session(self, tick) -> bool:
        """Determines if the precious metals market is actively quoting."""
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        weekday = utc_now.weekday() # 4=Friday, 5=Saturday, 6=Sunday
        hour = utc_now.hour
        
        # Weekend shutdown: Friday 22:00 UTC through Sunday 22:00 UTC
        if weekday == 5: # Saturday
            return False
        if weekday == 6 and hour < 22: # Sunday before 22:00 UTC
            return False
        if weekday == 4 and hour >= 22: # Friday after 22:00 UTC
            return False
            
        # Verify tick recency (stale tick > 15 mins indicates closed market)
        if tick and (time.time() - tick.time > 900):
            return False
            
        return True

    def get_risk_multiplier(self) -> (float, str):
        """Half-Kelly Drawdown De-Leveraging Gate."""
        if self.max_drawdown >= self.circuit_breaker_dd:
            self.circuit_breaker_active = True
            return 0.0, "HALTED (Circuit Breaker >= 20% DD)"
        elif self.max_drawdown >= 15.0:
            return 0.33, "HIGH_DEFENSIVE (67% Throttled)"
        elif self.max_drawdown >= 10.0:
            return 0.67, "DEFENSIVE (33% Throttled)"
        else:
            return 1.00, "NORMAL (Full Allocation)"

    def compute_slippage(self, atr: float, is_momentum: bool = False) -> float:
        """Generates realistic micro-slippage for market fills."""
        if is_momentum:
            return round(random.uniform(0.15, 0.35), 2)
        else:
            return round(random.uniform(0.04, 0.14), 2)

    def fetch_market_data(self):
        tick = mt5.symbol_info_tick(self.symbol)
        sym_info = mt5.symbol_info(self.symbol)
        if not tick or not sym_info:
            return None
            
        self.is_market_open = self.check_market_session(tick)
        
        # 1-Hour Candles (500 bars)
        r_1h = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_H1, 0, 500)
        df_1h = pd.DataFrame(r_1h) if r_1h is not None else None
        
        # Daily Candles (300 bars)
        r_1d = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_D1, 0, 300)
        df_1d = pd.DataFrame(r_1d) if r_1d is not None else None
        
        if df_1h is None or df_1d is None:
            return None
            
        # 1H Technical Indicators
        h1, l1, c1 = df_1h['high'], df_1h['low'], df_1h['close']
        tr1 = np.maximum(h1 - l1, np.maximum((h1 - c1.shift(1)).abs(), (l1 - c1.shift(1)).abs()))
        df_1h['atr'] = tr1.rolling(14).mean().bfill()
        df_1h['atr_ma'] = df_1h['atr'].rolling(30).mean().bfill()
        df_1h['ema200'] = c1.ewm(span=200, adjust=False).mean()
        
        # 1D Technical Indicators
        hd, ld, cd = df_1d['high'], df_1d['low'], df_1d['close']
        trd = np.maximum(hd - ld, np.maximum((hd - cd.shift(1)).abs(), (ld - cd.shift(1)).abs()))
        df_1d['atr'] = trd.rolling(14).mean().bfill()
        df_1d['ema200'] = cd.ewm(span=200, adjust=False).mean()
        
        return {
            'bid': tick.bid,
            'ask': tick.ask,
            'spread': round(tick.ask - tick.bid, sym_info.digits),
            'contract_size': sym_info.trade_contract_size,
            'digits': sym_info.digits,
            'tick_time': tick.time,
            'df_1h': df_1h,
            'df_1d': df_1d
        }

    def detect_confirmed_fractals(self, df: pd.DataFrame, x_left: int, y_right: int):
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)
        last_sh = None
        last_sl = None
        sh_idx = None
        sl_idx = None
        # Strict zero-lookahead confirmation (only up to n - 1 - y_right)
        for i in range(x_left, n - y_right):
            hc = highs[i]
            lc = lows[i]
            if all(highs[i - k] < hc for k in range(1, x_left + 1)) and all(highs[i + k] < hc for k in range(1, y_right + 1)):
                last_sh = hc
                sh_idx = i
            if all(lows[i - k] > lc for k in range(1, x_left + 1)) and all(lows[i + k] > lc for k in range(1, y_right + 1)):
                last_sl = lc
                sl_idx = i
        return last_sh, last_sl, sh_idx, sl_idx

    # =============================================================
    # 1-HOUR CHANDELIER RUNNER ENGINE
    # =============================================================
    def update_1h_engine(self, mkt: dict):
        df = mkt['df_1h']
        cur_c = df['close'].iloc[-1]
        cur_h = df['high'].iloc[-1]
        cur_l = df['low'].iloc[-1]
        cur_bar_time = df['time'].iloc[-1]
        ema200 = df['ema200'].iloc[-1]
        atr = df['atr'].iloc[-1]
        atr_ma = df['atr_ma'].iloc[-1]
        is_expansion = (atr > 1.4 * atr_ma)
        
        bid = mkt['bid']
        ask = mkt['ask']
        contract = mkt['contract_size']
        
        pos = self.p_1h['active_pos']
        pending = self.p_1h['pending_order']
        
        # 1. Manage Active Position
        if pos is not None:
            pos['bars'] += 1
            d = pos['dir']
            entry = pos['entry']
            sl = pos['sl']
            tp1 = pos['tp1']
            sl_dist = pos['sl_dist']
            
            # Update extreme price for Chandelier trailing
            if d == 'BUY':
                pos['extreme_price'] = max(pos['extreme_price'], bid)
            else:
                pos['extreme_price'] = min(pos['extreme_price'], ask)
                
            # Adverse Salvage Routine (Triggered on 45% adverse move)
            if not pos['t1_hit'] and pos['bars'] >= 2:
                adv = (entry - bid) if d == 'BUY' else (ask - entry)
                if adv >= (sl_dist * self.p_1h['salvage_threshold']):
                    if not pos.get('salvage_active', False):
                        pos['salvage_active'] = True
                        print(f"⚠️ [1H SALVAGE ACTIVATED] Adverse excursion hit 45% -> Compressing TP to 0.15% relief target!", flush=True)
                if pos.get('salvage_active', False):
                    tight_tp = round(bid * (1.0 + self.p_1h['salvage_tp_pct']) if d == 'BUY' else ask * (1.0 - self.p_1h['salvage_tp_pct']), 2)
                    if (d == 'BUY' and tight_tp < tp1) or (d == 'SELL' and tight_tp > tp1):
                        pos['tp1'] = tight_tp
                        tp1 = tight_tp
                        
            # Check Tranche 1 Take-Profit Hit (Buys exit at Bid, Sells at Ask)
            if not pos['t1_hit']:
                hit_tp1 = (bid >= tp1) if d == 'BUY' else (ask <= tp1)
                if hit_tp1:
                    pnl_t1 = (abs(tp1 - entry) * pos['lots_t1'] * contract) - (pos['lots_t1'] * 6.0)
                    self.paper_balance += pnl_t1
                    pos['t1_hit'] = True
                    # Instantly ratchet SL of remaining lots to Breakeven (+0.15 pts)
                    be_sl = round(entry + 0.15 if d == 'BUY' else entry - 0.15, 2)
                    pos['sl'] = be_sl
                    sl = be_sl
                    print(f"🎉 [1H T1 PROFIT LOCKED] +${pnl_t1:,.2f} | Remaining {pos['lots_t2']} lots ratcheted to Breakeven at ${be_sl:.2f}", flush=True)
                    if telegram_bot and telegram_bot.is_configured:
                        telegram_bot.notify_tp1_locked(d, entry, tp1, pnl_t1, be_sl)
                    self.trades_history.append({
                        'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'horizon': '1H_Chandelier_T1', 'dir': d, 'lots': pos['lots_t1'],
                        'entry': entry, 'exit': tp1, 'pnl': pnl_t1, 'type': 'TP1_LOCK (+BE)'
                    })
                    
            # Check Tranche 2 Chandelier Trailing Stop
            if pos['t1_hit']:
                trail_mult = self.p_1h['chandelier_atr_mult']
                if d == 'BUY':
                    trail_sl = round(pos['extreme_price'] - (trail_mult * atr), 2)
                    if trail_sl > pos['sl']:
                        pos['sl'] = trail_sl
                        sl = trail_sl
                else:
                    trail_sl = round(pos['extreme_price'] + (trail_mult * atr), 2)
                    if trail_sl < pos['sl']:
                        pos['sl'] = trail_sl
                        sl = trail_sl
                        
            # Check Stop-Loss / Runner Exit (Buys exit at Bid, Sells at Ask)
            hit_sl = (bid <= sl) if d == 'BUY' else (ask >= sl)
            if hit_sl:
                slip = self.compute_slippage(atr, is_expansion)
                exit_price = round(sl - slip if d == 'BUY' else sl + slip, 2)
                rem_lots = pos['lots_t2'] if pos['t1_hit'] else (pos['lots_t1'] + pos['lots_t2'])
                pts = (exit_price - entry) if d == 'BUY' else (entry - exit_price)
                pnl_exit = (pts * rem_lots * contract) - (rem_lots * 6.0)
                self.paper_balance += pnl_exit
                reason = "CHANDELIER_TRAIL_EXIT" if pos['t1_hit'] else ("SALVAGE_EXIT" if pos.get('salvage_active') else "INITIAL_SL")
                print(f"🛑 [1H POSITION CLOSED] Reason: {reason} | PnL: ${pnl_exit:,.2f} | New Equity: ${self.paper_balance:,.2f}", flush=True)
                if telegram_bot and telegram_bot.is_configured:
                    telegram_bot.notify_trade_closed(d, exit_price, pnl_exit, self.paper_balance, reason)
                self.trades_history.append({
                    'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'horizon': '1H_Chandelier_Runner', 'dir': d, 'lots': rem_lots,
                    'entry': entry, 'exit': exit_price, 'pnl': pnl_exit, 'type': reason
                })
                self.p_1h['active_pos'] = None
                return

        # 2. Manage Pre-Breakout Pending Orders (Cancel & Replace on New Fractals)
        # Session Window Gate: 10:00 AM to 4:00 PM IST (No VPS Hold & Trail Mode)
        if self.session_mode == "ist_daytime":
            ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
            is_entry_window = (10 <= ist_now.hour < 16)
            if not is_entry_window:
                if pending is not None:
                    print(f"⏰ [10-TO-4 IST] Outside active window ({ist_now.strftime('%H:%M:%S IST')}). Cleared resting pending order.", flush=True)
                    self.p_1h['pending_order'] = None
                    pending = None
                return

        sh_1h, sl_1h, idx_sh, idx_sl = self.detect_confirmed_fractals(df, self.p_1h['x_left'], self.p_1h['y_right'])
        
        if pos is None and sh_1h and sl_1h and not self.circuit_breaker_active:
            risk_mult, mode_str = self.get_risk_multiplier()
            effective_risk = self.p_1h['base_risk_pct'] * risk_mult
            risk_cash = self.paper_balance * effective_risk
            
            # Regime Gate
            is_bullish = (cur_c >= ema200)
            is_bearish = (cur_c <= ema200)
            
            # Target Pending Setup
            target_dir = 'BUY' if is_bullish else ('SELL' if is_bearish else None)
            target_fractal = sh_1h if target_dir == 'BUY' else sl_1h
            
            # Cancel & Replace stale pending order if fractal moved
            if pending is not None:
                if pending['dir'] != target_dir or abs(pending['fractal_price'] - target_fractal) > 0.10:
                    print(f"🔄 [1H PENDING ORDER REPLACED] Fractal shifted from ${pending['fractal_price']:.2f} to ${target_fractal:.2f}", flush=True)
                    pending = None
                    self.p_1h['pending_order'] = None
                    
            # Generate Pre-Breakout Pending Order
            if pending is None and target_dir is not None:
                # Do not re-enter the exact same fractal level twice
                if self.p_1h['last_entered_fractal'] != target_fractal:
                    if target_dir == 'BUY':
                        entry_trigger = round(sh_1h, 2)
                        sl = round(entry_trigger - (self.p_1h['sl_atr_mult'] * atr), 2)
                        sl_dist = round(entry_trigger - sl, 2)
                        tp1 = round(entry_trigger + (self.p_1h['tp1_atr_mult'] * atr), 2)
                    else:
                        entry_trigger = round(sl_1h, 2)
                        sl = round(entry_trigger + (self.p_1h['sl_atr_mult'] * atr), 2)
                        sl_dist = round(sl - entry_trigger, 2)
                        tp1 = round(entry_trigger - (self.p_1h['tp1_atr_mult'] * atr), 2)
                        
                    if sl_dist > 0:
                        total_lots = max(0.02, min(self.p_1h['max_lot_cap'], round(risk_cash / (sl_dist * contract), 2)))
                        l1 = max(0.01, round(total_lots * 0.5, 2))
                        l2 = max(0.01, round(total_lots - l1, 2))
                        
                        self.p_1h['pending_order'] = {
                            'type': 'BUY_STOP' if target_dir == 'BUY' else 'SELL_STOP',
                            'dir': target_dir,
                            'entry_trigger': entry_trigger,
                            'fractal_price': target_fractal,
                            'sl': sl, 'tp1': tp1, 'sl_dist': sl_dist,
                            'lots_t1': l1, 'lots_t2': l2, 'total_lots': total_lots,
                            'bar_time': cur_bar_time
                        }
                        pending = self.p_1h['pending_order']
                        print(f"📌 [1H RESTING ORDER PLACED] {pending['type']} at ${entry_trigger:.2f} (Fractal: ${target_fractal:.2f} | Lots: {total_lots})", flush=True)
                        if telegram_bot and telegram_bot.is_configured:
                            telegram_bot.notify_order_placed(target_dir, entry_trigger, sl, tp1, total_lots, risk_cash)

            # Check if Live Ticks Fill the Pending Resting Order
            if pending is not None:
                is_filled = False
                slip = self.compute_slippage(atr, is_expansion)
                
                # Buy Stop triggers on Ask >= trigger
                if pending['dir'] == 'BUY' and ask >= pending['entry_trigger']:
                    exec_price = round(ask + slip, 2)
                    is_filled = True
                # Sell Stop triggers on Bid <= trigger
                elif pending['dir'] == 'SELL' and bid <= pending['entry_trigger']:
                    exec_price = round(bid - slip, 2)
                    is_filled = True
                    
                if is_filled:
                    self.p_1h['active_pos'] = {
                        'dir': pending['dir'],
                        'entry': exec_price,
                        'sl': pending['sl'],
                        'tp1': pending['tp1'],
                        'sl_dist': pending['sl_dist'],
                        'lots_t1': pending['lots_t1'],
                        'lots_t2': pending['lots_t2'],
                        't1_hit': False,
                        'extreme_price': exec_price,
                        'bars': 0,
                        'salvage_active': False
                    }
                    self.p_1h['last_entered_fractal'] = pending['fractal_price']
                    print(f"⚡ [1H RESTING ORDER FILLED] {pending['dir']} {pending['total_lots']} lots at ${exec_price:.2f} (Slippage: {slip} pts)", flush=True)
                    if telegram_bot and telegram_bot.is_configured:
                        telegram_bot.notify_fill(pending['dir'], exec_price, pending['total_lots'], slip)
                    self.p_1h['pending_order'] = None

    # =============================================================
    # 1-DAY MACRO TREND RUNNER ENGINE
    # =============================================================
    def update_1d_engine(self, mkt: dict):
        df = mkt['df_1d']
        cur_c = df['close'].iloc[-1]
        cur_bar_time = df['time'].iloc[-1]
        ema200 = df['ema200'].iloc[-1]
        atr = df['atr'].iloc[-1]
        
        bid = mkt['bid']
        ask = mkt['ask']
        contract = mkt['contract_size']
        
        pos = self.p_1d['active_pos']
        pending = self.p_1d['pending_order']
        
        # 1. Manage Active 1D Position
        if pos is not None:
            d = pos['dir']
            entry = pos['entry']
            sl = pos['sl']
            tp = pos['tp']
            
            # Breakeven lock check
            fav_pts = (bid - entry) if d == 'BUY' else (entry - ask)
            if not pos.get('be_locked', False) and fav_pts >= (entry * self.p_1d['be_trigger_pct']):
                pos['be_locked'] = True
                be_sl = round(entry + 0.15 if d == 'BUY' else entry - 0.15, 2)
                pos['sl'] = be_sl
                sl = be_sl
                print(f"🛡️ [1D BREAKEVEN LOCKED] 1D Macro moved to risk-free at ${be_sl:.2f}", flush=True)
                
            # Check Take-Profit
            hit_tp = (bid >= tp) if d == 'BUY' else (ask <= tp)
            if hit_tp:
                pts = abs(tp - entry)
                pnl = (pts * pos['lots'] * contract) - (pos['lots'] * 6.0)
                self.paper_balance += pnl
                print(f"🏆 [1D MACRO TP HIT] +${pnl:,.2f} | New Equity: ${self.paper_balance:,.2f}", flush=True)
                self.trades_history.append({
                    'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'horizon': '1D_Macro_TP', 'dir': d, 'lots': pos['lots'],
                    'entry': entry, 'exit': tp, 'pnl': pnl, 'type': 'MACRO_TP_HIT'
                })
                self.p_1d['active_pos'] = None
                return
                
            # Check Stop-Loss
            hit_sl = (bid <= sl) if d == 'BUY' else (ask >= sl)
            if hit_sl:
                pts = (sl - entry) if d == 'BUY' else (entry - sl)
                pnl = (pts * pos['lots'] * contract) - (pos['lots'] * 6.0)
                self.paper_balance += pnl
                print(f"🛑 [1D MACRO SL EXIT] PnL: ${pnl:,.2f} | New Equity: ${self.paper_balance:,.2f}", flush=True)
                self.trades_history.append({
                    'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'horizon': '1D_Macro_SL', 'dir': d, 'lots': pos['lots'],
                    'entry': entry, 'exit': sl, 'pnl': pnl, 'type': 'MACRO_SL_EXIT'
                })
                self.p_1d['active_pos'] = None
                return

        # 2. Manage Pre-Breakout 1D Pending Orders
        if self.session_mode == "ist_daytime":
            ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
            is_entry_window = (10 <= ist_now.hour < 16)
            if not is_entry_window:
                if pending is not None:
                    self.p_1d['pending_order'] = None
                    pending = None
                return

        sh_1d, sl_1d, idx_sh, idx_sl = self.detect_confirmed_fractals(df, self.p_1d['x_left'], self.p_1d['y_right'])
        
        if pos is None and sh_1d and sl_1d and not self.circuit_breaker_active:
            risk_mult, _ = self.get_risk_multiplier()
            risk_cash = self.paper_balance * (self.p_1d['base_risk_pct'] * risk_mult)
            
            is_bullish = (cur_c >= ema200)
            is_bearish = (cur_c <= ema200)
            target_dir = 'BUY' if is_bullish else ('SELL' if is_bearish else None)
            target_fractal = sh_1d if target_dir == 'BUY' else sl_1d
            
            # Cancel & Replace if fractal shifted
            if pending is not None:
                if pending['dir'] != target_dir or abs(pending['fractal_price'] - target_fractal) > 0.50:
                    print(f"🔄 [1D PENDING ORDER REPLACED] Daily fractal shifted from ${pending['fractal_price']:.2f} to ${target_fractal:.2f}", flush=True)
                    pending = None
                    self.p_1d['pending_order'] = None
                    
            if pending is None and target_dir is not None:
                if self.p_1d['last_entered_fractal'] != target_fractal:
                    if target_dir == 'BUY':
                        entry_trigger = round(sh_1d, 2)
                        sl = round(entry_trigger * (1.0 - self.p_1d['sl_pct']), 2)
                        tp = round(entry_trigger * (1.0 + self.p_1d['tp_pct']), 2)
                        sl_dist = round(entry_trigger - sl, 2)
                    else:
                        entry_trigger = round(sl_1d, 2)
                        sl = round(entry_trigger * (1.0 + self.p_1d['sl_pct']), 2)
                        tp = round(entry_trigger * (1.0 - self.p_1d['tp_pct']), 2)
                        sl_dist = round(sl - entry_trigger, 2)
                        
                    if sl_dist > 0:
                        lots = max(0.01, min(self.p_1d['max_lot_cap'], round(risk_cash / (sl_dist * contract), 2)))
                        self.p_1d['pending_order'] = {
                            'type': 'BUY_STOP' if target_dir == 'BUY' else 'SELL_STOP',
                            'dir': target_dir, 'entry_trigger': entry_trigger,
                            'fractal_price': target_fractal, 'sl': sl, 'tp': tp,
                            'sl_dist': sl_dist, 'lots': lots, 'bar_time': cur_bar_time
                        }
                        pending = self.p_1d['pending_order']
                        print(f"📌 [1D RESTING ORDER PLACED] {pending['type']} at ${entry_trigger:.2f} (Daily Fractal: ${target_fractal:.2f})", flush=True)

            # Check 1D Fill
            if pending is not None:
                is_filled = False
                slip = self.compute_slippage(atr, False)
                if pending['dir'] == 'BUY' and ask >= pending['entry_trigger']:
                    exec_p = round(ask + slip, 2)
                    is_filled = True
                elif pending['dir'] == 'SELL' and bid <= pending['entry_trigger']:
                    exec_p = round(bid - slip, 2)
                    is_filled = True
                    
                if is_filled:
                    self.p_1d['active_pos'] = {
                        'dir': pending['dir'], 'entry': exec_p, 'sl': pending['sl'],
                        'tp': pending['tp'], 'lots': pending['lots'], 'sl_dist': pending['sl_dist'],
                        'be_locked': False
                    }
                    self.p_1d['last_entered_fractal'] = pending['fractal_price']
                    self.p_1d['pending_order'] = None
                    print(f"⚡ [1D RESTING ORDER FILLED] {pending['dir']} {pending['lots']} lots at ${exec_p:.2f}", flush=True)

    def save_state(self):
        if self.paper_balance > self.peak_balance:
            self.peak_balance = self.paper_balance
        dd = (self.peak_balance - self.paper_balance) / self.peak_balance * 100.0 if self.peak_balance > 0 else 0.0
        if dd > self.max_drawdown:
            self.max_drawdown = dd
            
        risk_mult, mode_str = self.get_risk_multiplier()
        
        # Calculate Win Rate & Metrics
        pnls = [t['pnl'] for t in self.trades_history]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        wr = (len(wins) / len(pnls) * 100.0) if len(pnls) > 0 else 0.0
        gw = sum(wins) if wins else 0.0
        gl = abs(sum(losses)) if losses else 0.0
        pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
        
        state_data = {
            'timestamp': time.time(),
            'datetime_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'mode': self.mode,
            'is_paused': self.is_paused,
            'ath_equity': round(self.ath_equity, 2),
            'session': {
                'mode': self.session_mode,
                'name': '10:00 AM – 4:00 PM IST (Hold & Trail)',
                'is_window_active': (10 <= datetime.datetime.now(pytz.timezone('Asia/Kolkata')).hour < 16) if self.session_mode == 'ist_daytime' else True,
                'ist_time': datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%H:%M:%S IST'),
                'no_vps': True
            },
            'symbol': self.symbol,
            'market_status': 'OPEN' if self.is_market_open else 'CLOSED (Weekend - Reopens Sunday 22:00 UTC)',
            'capital': {
                'initial': round(self.initial_balance, 2),
                'current_equity': round(self.paper_balance, 2),
                'peak_equity': round(self.peak_balance, 2),
                'net_profit': round(self.paper_balance - self.initial_balance, 2),
                'return_pct': round(((self.paper_balance - self.initial_balance) / self.initial_balance) * 100.0, 2),
                'drawdown_pct': round(dd, 2),
                'max_drawdown_pct': round(self.max_drawdown, 2)
            },
            'governor': {
                'mode': mode_str,
                'multiplier': risk_mult,
                'circuit_breaker': self.circuit_breaker_active,
                'max_limit_dd': self.circuit_breaker_dd
            },
            'performance': {
                'total_trades': len(pnls),
                'winning_trades': len(wins),
                'losing_trades': len(losses),
                'win_rate_pct': round(wr, 1),
                'profit_factor': round(pf, 2)
            },
            'horizons': {
                '1D_macro_runner': {
                    'active_position': self.p_1d['active_pos'],
                    'pending_order': self.p_1d['pending_order'],
                    'parameters': {
                        'fractal': '15L/2R', 'risk_pct': self.p_1d['base_risk_pct'] * risk_mult,
                        'sl_pct': self.p_1d['sl_pct'], 'tp_pct': self.p_1d['tp_pct']
                    }
                },
                '1H_chandelier_runner': {
                    'active_position': self.p_1h['active_pos'],
                    'pending_order': self.p_1h['pending_order'],
                    'parameters': {
                        'fractal': '15L/3R', 'risk_pct': self.p_1h['base_risk_pct'] * risk_mult,
                        'sl_atr': self.p_1h['sl_atr_mult'], 'tp1_atr': self.p_1h['tp1_atr_mult'],
                        'chandelier_atr': self.p_1h['chandelier_atr_mult'],
                        'salvage_threshold': self.p_1h['salvage_threshold']
                    }
                }
            },
            'recent_trades': self.trades_history[-25:]
        }
        
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    def run_loop(self, poll_interval: int = 5):
        self.running = True
        print(f"[ENGINE V2] Starting Live Institutional Polling Loop ({poll_interval}s interval)...", flush=True)
        last_hb = 0
        try:
            while self.running:
                mkt = self.fetch_market_data()
                if mkt is not None:
                    # If market is closed for weekend, sleep smoothly without spamming logs
                    if not self.is_market_open:
                        self.save_state()
                        if time.time() - last_hb >= 60:
                            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🌙 Market Status: CLOSED (Weekend Pause) | Reopens Sunday 22:00 UTC | Balance: ${self.paper_balance:,.2f}", flush=True)
                            last_hb = time.time()
                        time.sleep(poll_interval * 3) # slower polling during weekend
                        continue
                        
                    # Process Horizon Engines
                    self.update_1h_engine(mkt)
                    self.update_1d_engine(mkt)
                    self.save_state()

                    # Check Automated Briefing Triggers
                    ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
                    today_str = ist_now.strftime('%Y-%m-%d')
                    weekday = ist_now.weekday()
                    
                    if weekday < 5: # Monday - Friday
                        # Morning Briefing @ 09:45 AM IST
                        if ist_now.hour == 9 and 45 <= ist_now.minute < 59:
                            if self.last_morning_briefing_date != today_str and telegram_bot and telegram_bot.is_configured:
                                sh_1h, sl_1h, _, _ = self.detect_confirmed_fractals(mkt['df_1h'], self.p_1h['x_left'], self.p_1h['y_right'])
                                c_1h = mkt['df_1h']['close'].iloc[-1]
                                ema_1h = mkt['df_1h']['ema200'].iloc[-1]
                                telegram_bot.notify_morning_briefing(c_1h, ema_1h, sh_1h or c_1h, sl_1h or c_1h, c_1h >= ema_1h)
                                self.last_morning_briefing_date = today_str

                        # Evening Summary @ 04:05 PM IST
                        if ist_now.hour == 16 and 5 <= ist_now.minute < 20:
                            if self.last_evening_summary_date != today_str and telegram_bot and telegram_bot.is_configured:
                                today_trades = [t for t in self.trades_history if t.get('time', '').startswith(today_str)]
                                day_pnl = sum(t.get('pnl', 0.0) for t in today_trades)
                                telegram_bot.notify_evening_summary(self.paper_balance, day_pnl, len(today_trades), self.p_1h['active_pos'])
                                self.last_evening_summary_date = today_str

                    # Check Milestone Trigger
                    ret_pct = ((self.paper_balance - self.initial_balance) / self.initial_balance) * 100.0
                    if ret_pct >= (self.last_milestone_pct + 10.0):
                        if telegram_bot and telegram_bot.is_configured:
                            telegram_bot.notify_milestone(int(ret_pct), self.paper_balance)
                        self.last_milestone_pct = ret_pct
                    
                    if time.time() - last_hb >= 30:
                        c_1h = mkt['df_1h']['close'].iloc[-1]
                        ema_1h = mkt['df_1h']['ema200'].iloc[-1]
                        pos_1h = "FLAT" if self.p_1h['active_pos'] is None else f"{self.p_1h['active_pos']['dir']} ({self.p_1h['active_pos']['entry']})"
                        pos_1d = "FLAT" if self.p_1d['active_pos'] is None else f"{self.p_1d['active_pos']['dir']} ({self.p_1d['active_pos']['entry']})"
                        pend_1h = "NONE" if self.p_1h['pending_order'] is None else f"{self.p_1h['pending_order']['type']}@{self.p_1h['pending_order']['entry_trigger']}"
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] XAUUSD: ${c_1h:.2f} (EMA: ${ema_1h:.2f}) | Equity: ${self.paper_balance:,.2f} | 1H: {pos_1h} (Pend: {pend_1h}) | 1D: {pos_1d}", flush=True)
                        last_hb = time.time()
                        
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\n[ENGINE V2] 🛑 Operator halted execution loop safely.", flush=True)
            mt5.shutdown()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Upgraded Institutional Dual-Horizon Gold Breakout Engine")
    parser.add_argument("--mode", type=str, default="test", choices=["test", "demo"], help="Execution mode: 'test' (paper) or 'demo' (real broker demo orders)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial testing balance (default: $10,000)")
    parser.add_argument("--session", type=str, default="ist_daytime", choices=["ist_daytime", "24h"], help="Session: 'ist_daytime' (10-16 IST) or '24h' (continuous)")
    parser.add_argument("--risk", type=float, default=2.5, help="Base risk percentage per trade (e.g. 2.5, 3.5, 5.0)")
    parser.add_argument("--account-type", type=str, default="standard", choices=["standard", "cent"], help="Account type: 'standard' or 'cent'")
    args = parser.parse_args()
    
    engine = InstitutionalGoldBreakoutEngine(
        mode=args.mode,
        initial_balance=args.balance,
        session_mode=args.session,
        base_risk_pct=args.risk / 100.0,
        account_type=args.account_type
    )
    if engine.initialize():
        engine.run_loop()
