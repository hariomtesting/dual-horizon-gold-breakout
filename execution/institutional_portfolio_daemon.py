"""
========================================================================================
DECOUPLED 5-ASSET INSTITUTIONAL PORTFOLIO DAEMON (LIVE EXECUTION)
========================================================================================
Executes an uncorrelated, decoupled multi-asset portfolio where each instrument
runs on its own asset-calibrated parameters (zero shared parameters between trades):

1. ETHUSD   (Crypto)       | Lookback: 20 | Dist: 0.30 ATR | SL: 0.30 ATR | TP: 1.4R/3.0R | Risk: 0.50% | Magic: 101001
2. XAUUSD   (Gold Bullion) | Lookback: 12 | Dist: 0.20 ATR | SL: 0.20 ATR | TP: 1.4R/2.5R | Risk: 0.75% | Magic: 101002
3. GER40.r  (DAX Index)    | Lookback: 16 | Dist: 0.15 ATR | SL: 0.20 ATR | TP: 1.4R/2.5R | Risk: 0.75% | Magic: 101003
4. CL-OIL   (Crude Oil)    | Lookback: 18 | Dist: 0.20 ATR | SL: 0.25 ATR | TP: 1.4R/2.5R | Risk: 0.50% | Magic: 101004
5. NAS100.r (Nasdaq 100)   | Lookback: 14 | Dist: 0.25 ATR | SL: 0.25 ATR | TP: 1.4R/2.5R | Risk: 0.50% | Magic: 101005
+ Gold European Anchor Momentum (12:00 IST) | Magic: 888999

Institutional Safeguards:
- 15:55 PM IST Hard Auto-Square-Off (100% Flat daily, zero overnight risk, NO VPS required)
- -3.00% Daily Account Circuit Breaker (halts execution immediately)
- Half-Kelly Drawdown De-Leveraging Gate (-8% DD -> throttles risk by 33%, -12% DD -> by 67%)
- Auto-Breakeven lock on TP1 hit across all active positions
========================================================================================
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import time
import datetime
import argparse
import json
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
import MetaTrader5 as mt5

class DecoupledPortfolioDaemon:
    def __init__(
        self,
        paper_mode: bool = True,
        initial_paper_balance: float = 10000.0,
        anchor_risk: float = 0.0125
    ):
        self.paper_mode = paper_mode
        self.paper_balance = initial_paper_balance
        self.anchor_risk = anchor_risk
        self.anchor_magic = 888999
        
        # 5 Top-Performing Decoupled Asset Configurations (Zero Shared Parameters)
        self.assets: Dict[str, Dict[str, Any]] = {
            'ETHUSD': {
                'name': 'Ethereum (Crypto)',
                'lookback': 20,
                'dist_atr': 0.30,
                'sl_atr': 0.30,
                'start_time': 1000,
                'tp1_r': 1.4,
                'tp2_r': 3.0,
                'risk_pct': 0.0050,
                'digits': 2,
                'magic': 101001,
                'max_trades': 3,
                'enabled': True
            },
            'XAUUSD': {
                'name': 'Gold Bullion',
                'lookback': 12,
                'dist_atr': 0.20,
                'sl_atr': 0.20,
                'start_time': 1000,
                'tp1_r': 1.4,
                'tp2_r': 2.5,
                'risk_pct': 0.0075,
                'digits': 2,
                'magic': 101002,
                'max_trades': 3,
                'open_h': 12, 'open_m': 0, 'tau': 1.4, 'sl_mult': 1.5,
                'anchor_tp1_r': 1.4, 'anchor_tp2_r': 3.5, 'min_atr': 3.50,
                'enabled': True
            },
            'GER40.r': {
                'name': 'DAX 40 (EU Index)',
                'lookback': 16,
                'dist_atr': 0.15,
                'sl_atr': 0.20,
                'start_time': 1000,
                'tp1_r': 1.4,
                'tp2_r': 2.5,
                'risk_pct': 0.0075,
                'digits': 2,
                'magic': 101003,
                'max_trades': 3,
                'enabled': True
            },
            'CL-OIL': {
                'name': 'Crude Oil (Energy)',
                'lookback': 18,
                'dist_atr': 0.20,
                'sl_atr': 0.25,
                'start_time': 1000,
                'tp1_r': 1.4,
                'tp2_r': 2.5,
                'risk_pct': 0.0050,
                'digits': 3,
                'magic': 101004,
                'max_trades': 3,
                'enabled': True
            },
            'NAS100.r': {
                'name': 'Nasdaq 100 (US Tech)',
                'lookback': 14,
                'dist_atr': 0.25,
                'sl_atr': 0.25,
                'start_time': 1200, # Active from 12:00 IST
                'tp1_r': 1.4,
                'tp2_r': 2.5,
                'risk_pct': 0.0050,
                'digits': 2,
                'magic': 101005,
                'max_trades': 3,
                'enabled': True
            }
        }
        
        self.all_magics = [self.anchor_magic] + [cfg['magic'] for cfg in self.assets.values()]
        
        # State tracking per symbol
        self.anchor_state: Dict[str, Dict[str, Any]] = {}
        self.scalper_state: Dict[str, Dict[str, Any]] = {}
        for s in self.assets.keys():
            self._reset_anchor_state(s)
            self._reset_scalper_state(s)
            
        self.peak_balance: float = initial_paper_balance
        self.current_day: datetime.date = datetime.date.today()
        self.day_start_balance: float = initial_paper_balance
        self.circuit_breaker_triggered: bool = False
        self.recent_logs: List[Dict[str, str]] = []
        self.state_file: str = os.path.abspath(os.path.join(os.path.dirname(__file__), 'live_state.json'))

    def log_event(self, msg: str, level: str = 'INFO'):
        ist_str = self.get_ist_now().strftime('%H:%M:%S')
        entry = {'time': ist_str, 'level': level, 'msg': msg}
        self.recent_logs.append(entry)
        if len(self.recent_logs) > 80:
            self.recent_logs.pop(0)
        print(f"[{ist_str} IST] [{level}] {msg}", flush=True)

    def _reset_anchor_state(self, sym: str):
        self.anchor_state[sym] = {
            'phase': 'WAIT_OPEN',
            'open_price': None, 'atr': None, 'ema': None,
            'dir': None, 'limit_price': None, 'sl': None, 'tp1': None, 'tp2': None,
            'ticket_t1': None, 'ticket_t2': None, 'lots_t1': 0.0, 'lots_t2': 0.0,
            't1_closed': False, 'done_today': False
        }

    def _reset_scalper_state(self, sym: str):
        max_t = self.assets[sym].get('max_trades', 3)
        self.scalper_state[sym] = {
            'phase': 'SCANNING',
            'active_pos': None,
            'pending_limit': None,
            'trades_today': 0,
            'max_trades': max_t,
            'last_sweep_time': None,
            'done_today': False
        }

    def initialize(self) -> bool:
        if not mt5.initialize():
            print(f"[ERROR] MT5 Initialization failed: {mt5.last_error()}", flush=True)
            return False
            
        acc = mt5.account_info()
        if acc is None:
            print("[ERROR] Failed to fetch MT5 account info.", flush=True)
            return False
            
        if not self.paper_mode:
            self.day_start_balance = acc.balance
            self.peak_balance = max(self.peak_balance, acc.balance)
        else:
            self.day_start_balance = self.paper_balance
            self.peak_balance = self.paper_balance
            
        print("\n" + "=" * 95, flush=True)
        print("🏛️  DECOUPLED 5-ASSET INSTITUTIONAL PORTFOLIO DAEMON (LIVE TRADING)", flush=True)
        print("=" * 95, flush=True)
        print(f"Connected Broker Account : {acc.login} ({acc.server})", flush=True)
        print(f"Live Balance / Equity    : ${acc.balance:,.2f} / ${acc.equity:,.2f}", flush=True)
        print(f"Execution Mode           : {'[PAPER SIMULATION - ZERO RISK]' if self.paper_mode else '🚨 [LIVE MT5 REAL EXECUTION]'}", flush=True)
        print(f"Daily Circuit Breaker    : -3.00% Max Account Loss Halt", flush=True)
        print(f"Auto-Square-Off          : 15:55 IST Sharp (100% Flat daily, zero overnight risk)", flush=True)
        print("-" * 95, flush=True)
        print("DECOUPLED ASSET BASKET (ZERO SHARED PARAMETERS):")
        for s, cfg in self.assets.items():
            if cfg['enabled']:
                mt5.symbol_select(s, True)
                tick = mt5.symbol_info_tick(s)
                bid_str = f"{tick.bid:.{cfg['digits']}f}" if tick else "N/A"
                print(f"  • {s:<10} | {cfg['name']:<22} | Magic: {cfg['magic']} | Risk: {cfg['risk_pct']*100:.2f}% | LB: {cfg['lookback']} | Dist: {cfg['dist_atr']} ATR | Bid: {bid_str}", flush=True)
        print("=" * 95 + "\n", flush=True)
        self.export_state_json()
        return True

    def get_ist_now(self) -> datetime.datetime:
        utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return utc_now + datetime.timedelta(hours=5, minutes=30)

    def get_current_account_metrics(self) -> (float, float, float):
        if self.paper_mode:
            bal = self.paper_balance
        else:
            acc = mt5.account_info()
            bal = acc.balance if acc else self.day_start_balance
            
        if bal > self.peak_balance:
            self.peak_balance = bal
            
        dd_pct = ((self.peak_balance - bal) / self.peak_balance) * 100.0 if self.peak_balance > 0 else 0.0
        return bal, self.peak_balance, dd_pct

    def get_throttled_multiplier(self, dd_pct: float) -> (float, str):
        # Half-Kelly Drawdown De-Leveraging Gate
        if dd_pct >= 12.0:
            return 0.33, "BUNKER_MODE (67% Throttled)"
        elif dd_pct >= 8.0:
            return 0.67, "CAUTION_MODE (33% Throttled)"
        else:
            return 1.00, "NORMAL_MODE (Full Allocation)"

    def fetch_m5_data(self, sym: str, lookback: int, n_bars: int = 250) -> Optional[pd.DataFrame]:
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, n_bars)
        if rates is None or len(rates) < 50:
            return None
        df = pd.DataFrame(rates)
        df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
        df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
        df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
        
        # Asset-dedicated swing high/low using its own lookback
        df['swing_high'] = df['high'].shift(1).rolling(lookback).max()
        df['swing_low'] = df['low'].shift(1).rolling(lookback).min()
        
        # Volume profile & RVOL
        vol_ma = df['tick_volume'].rolling(20, min_periods=5).mean().bfill()
        df['rvol'] = df['tick_volume'] / (vol_ma + 1e-8)
        
        # Asian Session Range (05:30 to 11:30 IST)
        df['time_str'] = df['ist_dt'].dt.strftime('%H:%M')
        today_date = df['ist_dt'].dt.date.iloc[-1]
        today_asian = df[(df['ist_dt'].dt.date == today_date) & (df['time_str'] >= '05:30') & (df['time_str'] < '11:30')]
        
        if len(today_asian) >= 5:
            ah = round(float(today_asian['high'].max()), self.assets[sym]['digits'])
            al = round(float(today_asian['low'].min()), self.assets[sym]['digits'])
            self.assets[sym]['asian_high'] = ah
            self.assets[sym]['asian_low'] = al
            df['asian_high'] = ah
            df['asian_low'] = al
        else:
            self.assets[sym]['asian_high'] = None
            self.assets[sym]['asian_low'] = None
            df['asian_high'] = None
            df['asian_low'] = None
            
        last_rvol = round(float(df['rvol'].iloc[-1]), 2)
        self.assets[sym]['rvol'] = last_rvol
        df['last_rvol'] = last_rvol
        return df

    def calculate_lots(self, sym: str, sl_dist: float, risk_cash: float, entry_price: float) -> (float, float):
        info = mt5.symbol_info(sym)
        if not info:
            return 0.01, 0.01
            
        step = info.volume_step if info.volume_step > 0 else 0.01
        min_v = info.volume_min if info.volume_min > 0 else 0.01
        max_v = info.volume_max if info.volume_max > 0 else 100.0
        
        calc_profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, 1.0, entry_price, entry_price + 1.0)
        val_per_unit = calc_profit if (calc_profit is not None and calc_profit > 0) else (info.trade_contract_size)
        
        raw_lots = risk_cash / (sl_dist * val_per_unit + 1e-8)
        rounded_total = round(round(raw_lots / step) * step, 2)
        total_lots = max(min_v * 2, min(max_v, rounded_total))
        
        t1_steps = round((total_lots / 2.0) / step)
        lots_t1 = max(min_v, round(t1_steps * step, 2))
        lots_t2 = max(min_v, round(total_lots - lots_t1, 2))
        return lots_t1, lots_t2

    def _reconcile_broker_positions(self):
        """
        Broker State Reconciliation across all 5 assets:
        1. Queries active broker positions matching our magics.
        2. Syncs state and locks breakeven when TP1 is hit.
        3. Clears pending orders that have filled or expired.
        """
        if self.paper_mode:
            return

        positions = mt5.positions_get()
        orders = mt5.orders_get()
        
        active_tickets_by_sym = {}
        if positions:
            for p in positions:
                if p.magic in self.all_magics:
                    active_tickets_by_sym.setdefault(p.symbol, []).append(p)
                    
        pending_by_sym = {}
        if orders:
            for o in orders:
                if o.magic in self.all_magics:
                    pending_by_sym.setdefault(o.symbol, []).append(o)
                    
        # History deals to check if Tranche 1 closed
        today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        recent_deals = mt5.history_deals_get(today_start, datetime.datetime.now())
        tp1_closed_symbols = set()
        if recent_deals:
            for d in recent_deals:
                comm = (d.comment or "").lower()
                if ("_t1" in comm or "tp" in comm) and d.profit > 0:
                    tp1_closed_symbols.add(d.symbol)

        for sym, cfg in self.assets.items():
            sst = self.scalper_state[sym]
            sym_pos = active_tickets_by_sym.get(sym, [])
            sym_ords = pending_by_sym.get(sym, [])
            
            # Clear pending if broker has no orders
            if not sym_ords and sst['pending_limit']:
                sst['pending_limit'] = None
                
            # If position exists in MT5
            if sym_pos:
                if sst['active_pos'] is None:
                    p = sym_pos[0]
                    sst['active_pos'] = {
                        'dir': 'BUY' if p.type == mt5.ORDER_TYPE_BUY else 'SELL',
                        'entry': p.price_open,
                        'sl': p.sl,
                        'tp1': p.tp,
                        'ticket_t2': p.ticket,
                        't1_closed': (sym in tp1_closed_symbols)
                    }
                else:
                    if sym in tp1_closed_symbols and not sst['active_pos']['t1_closed']:
                        sst['active_pos']['t1_closed'] = True
                        for p in sym_pos:
                            is_buy = (p.type == mt5.ORDER_TYPE_BUY)
                            be_sl = p.price_open
                            already_be = (p.sl >= be_sl) if is_buy else (p.sl <= be_sl and p.sl > 0)
                            if not already_be:
                                self._modify_mt5_sl(p.ticket, be_sl, p.tp)
                                self.log_event(f"🛡️ [Breakeven Lock] {sym} Runner #{p.ticket} SL moved to {be_sl}", 'SUCCESS')
            else:
                if sst['active_pos'] is not None and not sym_ords:
                    sst['active_pos'] = None

    def _garbage_collect_stale_orders(self):
        """Cancels pending limit orders older than 6 bars (30 mins)"""
        if self.paper_mode:
            return
        orders = mt5.orders_get()
        if not orders:
            return
        now_ts = time.time()
        for o in orders:
            if o.magic in self.all_magics:
                if (now_ts - o.time_setup) > 1800:
                    mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                    self.log_event(f"🧹 [GC] Stale limit order #{o.ticket} ({o.symbol}) expired and removed.", 'INFO')

    def run_tick(self):
        now = self.get_ist_now()
        
        # Reset at 05:30 IST (Asian Open)
        if now.date() != self.current_day:
            self.current_day = now.date()
            bal, _, _ = self.get_current_account_metrics()
            self.day_start_balance = bal
            self.circuit_breaker_triggered = False
            for s in self.assets.keys():
                self._reset_anchor_state(s)
                self._reset_scalper_state(s)
            self.log_event("🌅 NEW TRADING DAY DETECTED. Resetting daily quotas.", 'INFO')
            
        # Circuit Breaker Check (-3.00%)
        bal, peak, dd = self.get_current_account_metrics()
        day_pnl = bal - self.day_start_balance
        day_loss_pct = (day_pnl / self.day_start_balance) * 100.0 if self.day_start_balance > 0 else 0.0
        
        if day_loss_pct <= -3.00:
            if not self.circuit_breaker_triggered:
                self.circuit_breaker_triggered = True
                self.log_event(f"🚨 CIRCUIT BREAKER TRIGGERED ({day_loss_pct:.2f}%). Squaring off all positions!", 'WARNING')
                self.emergency_square_off_all("Circuit Breaker -3.0% Hit")
            return
            
        # Hard 15:55 PM IST Square-Off (NO VPS Overnight Risk)
        h, m = now.hour, now.minute
        t_num = h * 100 + m
        if t_num >= 1555:
            any_active = any(not self.anchor_state[s]['done_today'] or not self.scalper_state[s]['done_today'] for s in self.assets.keys())
            if any_active:
                self.log_event(f"⏰ [15:55 IST Auto-Cutoff] Enforcing 100% Flat Square-off across all 5 assets.", 'INFO')
                self.emergency_square_off_all("Mandatory 15:55 Flat Close")
            self.export_state_json()
            return
            
        # Run Broker State Reconciliation & Order GC
        self._reconcile_broker_positions()
        self._garbage_collect_stale_orders()
        
        # Multiplier from Drawdown Gate
        mult, mode_str = self.get_throttled_multiplier(dd)
        
        # Process each decoupled asset independently
        for sym, cfg in self.assets.items():
            if not cfg['enabled']:
                continue
                
            df = self.fetch_m5_data(sym, lookback=cfg['lookback'], n_bars=250)
            if df is None:
                continue
            last_bar = df.iloc[-1]
            
            # Asset-specific risk sizing
            asset_risk = cfg['risk_pct'] * mult
            
            # Engine 1: Macro Momentum Anchor (Only on XAUUSD @ 12:00 IST)
            if sym == 'XAUUSD':
                self._process_anchor_engine(sym, cfg, now, bal, self.anchor_risk * mult, last_bar)
                
            # Engine 2: Decoupled Liquidity Scalper
            self._process_scalper_engine(sym, cfg, now, bal, asset_risk, last_bar, t_num)
            
        self.export_state_json()

    # =========================================================================
    # ENGINE 1: MACRO SESSION MOMENTUM ANCHOR (SSRN ITSM @ 12:00 IST)
    # =========================================================================
    def _process_anchor_engine(self, sym: str, cfg: Dict[str, Any], now: datetime.datetime, bal: float, risk_pct: float, last_bar: pd.Series):
        st = self.anchor_state[sym]
        if st['done_today']:
            return
            
        h, m = now.hour, now.minute
        
        # Phase 1: Wait for 12:00 PM Anchor
        if st['phase'] == 'WAIT_OPEN':
            if (h > cfg['open_h']) or (h == cfg['open_h'] and m > cfg['open_m'] + 35):
                st['phase'] = 'DONE'
                st['done_today'] = True
                return
            elif h == cfg['open_h'] and (m >= cfg['open_m'] and m <= cfg['open_m'] + 2):
                if last_bar['atr'] < cfg['min_atr']:
                    self.log_event(f"⚠️ [Anchor] {sym} Volatility drought ({last_bar['atr']:.2f} < {cfg['min_atr']:.2f}). Skipping session.", 'WARNING')
                    st['phase'] = 'DONE'
                    st['done_today'] = True
                    return
                st['phase'] = 'EVAL'
                st['open_price'] = last_bar['open']
                st['atr'] = last_bar['atr']
                st['ema'] = last_bar['ema200']
                self.log_event(f"🎯 [Anchor] {sym} European Anchor Triggered @ 12:00 IST | Open: {st['open_price']:.{cfg['digits']}f}", 'INFO')
                
        # Phase 2: Evaluating 30-min Institutional Drift
        elif st['phase'] == 'EVAL':
            elapsed_mins = (now.hour - cfg['open_h']) * 60 + (now.minute - cfg['open_m'])
            if elapsed_mins >= 30:
                p0 = st['open_price']
                p1 = last_bar['close']
                atr = st['atr']
                r_norm = (p1 - p0) / (atr + 1e-8)
                tau = cfg['tau']
                sl_mult = cfg['sl_mult']
                
                self.log_event(f"📊 [Anchor] {sym} Drift: {r_norm:.2f} ATR (Threshold: +/-{tau:.2f} ATR)", 'INFO')
                
                if r_norm >= tau and p1 >= st['ema']:
                    st['phase'] = 'LIMIT_WAIT'
                    st['dir'] = 'BUY'
                    st['limit_price'] = round(p1 - (0.25 * atr), cfg['digits'])
                    st['sl'] = round(st['limit_price'] - (sl_mult * atr), cfg['digits'])
                    st['tp1'] = round(st['limit_price'] + (cfg['anchor_tp1_r'] * sl_mult * atr), cfg['digits'])
                    st['tp2'] = round(st['limit_price'] + (cfg['anchor_tp2_r'] * sl_mult * atr), cfg['digits'])
                    self.log_event(f"🚀 [Anchor] {sym} BUY LIMIT Signal @ {st['limit_price']}", 'SUCCESS')
                    self._place_maker_limit(sym, cfg, bal, risk_pct, st, self.anchor_magic, "Anchor")
                elif r_norm <= -tau and p1 <= st['ema']:
                    st['phase'] = 'LIMIT_WAIT'
                    st['dir'] = 'SELL'
                    st['limit_price'] = round(p1 + (0.25 * atr), cfg['digits'])
                    st['sl'] = round(st['limit_price'] + (sl_mult * atr), cfg['digits'])
                    st['tp1'] = round(st['limit_price'] - (cfg['anchor_tp1_r'] * sl_mult * atr), cfg['digits'])
                    st['tp2'] = round(st['limit_price'] - (cfg['anchor_tp2_r'] * sl_mult * atr), cfg['digits'])
                    self.log_event(f"🚀 [Anchor] {sym} SELL LIMIT Signal @ {st['limit_price']}", 'SUCCESS')
                    self._place_maker_limit(sym, cfg, bal, risk_pct, st, self.anchor_magic, "Anchor")
                else:
                    self.log_event(f"⚪ [Anchor] {sym} No Trade: Insufficient drift or trend conflict.", 'INFO')
                    st['phase'] = 'DONE'
                    st['done_today'] = True

    # =========================================================================
    # ENGINE 2: DECOUPLED LIQUIDITY SWEEP SCALPER (ASSET-CALIBRATED)
    # =========================================================================
    def _process_scalper_engine(self, sym: str, cfg: Dict[str, Any], now: datetime.datetime, bal: float, risk_pct: float, last_bar: pd.Series, t_num: int):
        st = self.scalper_state[sym]
        if st['done_today']:
            return
            
        # Check asset-specific start time
        if t_num < cfg.get('start_time', 1000):
            return
            
        sw_h = last_bar['swing_high']
        sw_l = last_bar['swing_low']
        atr = last_bar['atr']
        if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0:
            return
            
        # If position or pending limit is already active, return
        if st['active_pos'] is not None or st['pending_limit'] is not None:
            return
            
        # Scan for Decoupled Liquidity Sweep Signal
        if st['trades_today'] < st['max_trades'] and t_num < 1530:
            sweep_dist = cfg['dist_atr'] * atr
            sl_dist_atr = cfg['sl_atr'] * atr
            
            swept_low = (last_bar['low'] < (sw_l - sweep_dist)) and (last_bar['close'] > sw_l)
            swept_high = (last_bar['high'] > (sw_h + sweep_dist)) and (last_bar['close'] < sw_h)
            
            is_bull_sweep = swept_low and (last_bar['close'] >= last_bar['ema200'])
            is_bear_sweep = swept_high and (last_bar['close'] <= last_bar['ema200'])
            
            if is_bull_sweep:
                lp = round((last_bar['close'] + last_bar['low']) / 2.0, cfg['digits'])
                sl = round(last_bar['low'] - sl_dist_atr, cfg['digits'])
                sl_dist = abs(lp - sl)
                tp1 = round(lp + (cfg['tp1_r'] * sl_dist), cfg['digits'])
                tp2 = round(lp + (cfg['tp2_r'] * sl_dist), cfg['digits'])
                
                self.log_event(f"⚡ [{sym} Scalp] BULLISH SWEEP (LB: {cfg['lookback']})! Rests Limit @ {lp} (SL: {sl}, TP: {tp1})", 'SUCCESS')
                self._place_scalp_limit(sym, cfg, bal, risk_pct, 'BUY', lp, sl, tp1, tp2, cfg['magic'])
                st['trades_today'] += 1
                
            elif is_bear_sweep:
                lp = round((last_bar['close'] + last_bar['high']) / 2.0, cfg['digits'])
                sl = round(last_bar['high'] + sl_dist_atr, cfg['digits'])
                sl_dist = abs(lp - sl)
                tp1 = round(lp - (cfg['tp1_r'] * sl_dist), cfg['digits'])
                tp2 = round(lp - (cfg['tp2_r'] * sl_dist), cfg['digits'])
                
                self.log_event(f"⚡ [{sym} Scalp] BEARISH SWEEP (LB: {cfg['lookback']})! Rests Limit @ {lp} (SL: {sl}, TP: {tp1})", 'SUCCESS')
                self._place_scalp_limit(sym, cfg, bal, risk_pct, 'SELL', lp, sl, tp1, tp2, cfg['magic'])
                st['trades_today'] += 1

    # =========================================================================
    # ORDER PLACEMENT & EXECUTION HELPERS
    # =========================================================================
    def _place_maker_limit(self, sym: str, cfg: Dict[str, Any], bal: float, risk_pct: float, st: Dict[str, Any], magic: int, tag: str):
        sl_dist = abs(st['limit_price'] - st['sl'])
        risk_cash = bal * risk_pct
        lots_t1, lots_t2 = self.calculate_lots(sym, sl_dist, risk_cash, st['limit_price'])
        st['lots_t1'] = lots_t1
        st['lots_t2'] = lots_t2
        
        if not self.paper_mode:
            self._send_mt5_limit(sym, st['dir'], st['limit_price'], st['sl'], st['tp1'], lots_t1, f"{tag}_T1", magic)
            self._send_mt5_limit(sym, st['dir'], st['limit_price'], st['sl'], st['tp2'], lots_t2, f"{tag}_T2", magic)

    def _place_scalp_limit(self, sym: str, cfg: Dict[str, Any], bal: float, risk_pct: float, d: str, lp: float, sl: float, tp1: float, tp2: float, magic: int):
        sl_dist = abs(lp - sl)
        risk_cash = bal * risk_pct
        lots_t1, lots_t2 = self.calculate_lots(sym, sl_dist, risk_cash, lp)
        
        st = self.scalper_state[sym]
        st['pending_limit'] = {'dir': d, 'lp': lp, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'lots_t1': lots_t1, 'lots_t2': lots_t2}
        
        if not self.paper_mode:
            self._send_mt5_limit(sym, d, lp, sl, tp1, lots_t1, f"{sym}_Scalp_T1", magic)
            self._send_mt5_limit(sym, d, lp, sl, tp2, lots_t2, f"{sym}_Scalp_T2", magic)

    def _send_mt5_limit(self, sym: str, direction: str, price: float, sl: float, tp: float, volume: float, tag: str, magic: int):
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == 'BUY' else mt5.ORDER_TYPE_SELL_LIMIT
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": magic,
            "comment": tag,
            "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": mt5.ORDER_FILLING_RETURN
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            self.log_event(f"⚠️ [MT5 Order Error] {tag} for {sym}: {res.comment} (code {res.retcode})", 'WARNING')
        else:
            self.log_event(f"✅ [MT5 Limit Resting] {tag} placed! Ticket: {res.order} | Price: {price} | Lots: {volume}", 'SUCCESS')

    def _modify_mt5_sl(self, ticket: int, new_sl: float, current_tp: float):
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl, "tp": current_tp}
        res = mt5.order_send(req)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            self.log_event(f"🛡️ [MT5 Breakeven] Position {ticket} SL moved to {new_sl}", 'SUCCESS')

    def _square_off_asset(self, sym: str, reason: str):
        if not self.paper_mode:
            orders = mt5.orders_get(symbol=sym)
            if orders:
                for o in orders:
                    if o.magic in self.all_magics:
                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            positions = mt5.positions_get(symbol=sym)
            if positions:
                for pos in positions:
                    if pos.magic in self.all_magics:
                        c_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        tick = mt5.symbol_info_tick(sym)
                        price = tick.bid if c_type == mt5.ORDER_TYPE_SELL else tick.ask
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": pos.volume,
                            "type": c_type, "position": pos.ticket, "price": price,
                            "deviation": 25, "magic": pos.magic, "comment": reason
                        })
        self.anchor_state[sym]['phase'] = 'DONE'
        self.anchor_state[sym]['done_today'] = True
        self.scalper_state[sym]['done_today'] = True

    def emergency_square_off_all(self, reason: str):
        for s in self.assets.keys():
            self._square_off_asset(s, reason)

    def export_state_json(self):
        try:
            now = self.get_ist_now()
            bal, peak, dd = self.get_current_account_metrics()
            mult, risk_mode = self.get_throttled_multiplier(dd)
            day_pnl = bal - self.day_start_balance
            day_pnl_pct = (day_pnl / self.day_start_balance) * 100.0 if self.day_start_balance > 0 else 0.0
            
            acc = mt5.account_info() if not self.paper_mode else None
            equity = acc.equity if acc else bal
            login = acc.login if acc else "PAPER_SIMULATOR"
            server = acc.server if acc else "Local"
            
            assets_data = {}
            for s, cfg in self.assets.items():
                ast = self.anchor_state[s]
                sst = self.scalper_state[s]
                tick = mt5.symbol_info_tick(s) if not self.paper_mode else None
                bid = tick.bid if tick else 0.0
                ask = tick.ask if tick else 0.0
                spread = round((ask - bid), cfg['digits']) if tick else 0.0
                
                assets_data[s] = {
                    'name': cfg['name'],
                    'enabled': cfg['enabled'],
                    'bid': bid, 'ask': ask, 'spread': spread,
                    'magic': cfg['magic'],
                    'lookback': cfg['lookback'],
                    'dist_atr': cfg['dist_atr'],
                    'sl_atr': cfg['sl_atr'],
                    'tp1_r': cfg['tp1_r'],
                    'tp2_r': cfg['tp2_r'],
                    'risk_pct': cfg['risk_pct'] * mult,
                    'anchor_phase': ast['phase'] if s == 'XAUUSD' else 'N/A',
                    'anchor_limit': ast['limit_price'] if s == 'XAUUSD' else None,
                    'scalper_trades_today': sst['trades_today'],
                    'scalper_max_trades': sst['max_trades'],
                    'asian_high': cfg.get('asian_high'),
                    'asian_low': cfg.get('asian_low'),
                    'rvol': cfg.get('rvol', 1.0),
                    'digits': cfg['digits']
                }
                
            open_pos = []
            pending_ords = []
            if not self.paper_mode:
                pos_list = mt5.positions_get()
                if pos_list:
                    for p in pos_list:
                        if p.magic in self.all_magics:
                            is_buy = (p.type == mt5.ORDER_TYPE_BUY)
                            is_be = (p.sl >= p.price_open) if is_buy else (p.sl <= p.price_open and p.sl > 0)
                            open_pos.append({
                                'ticket': p.ticket, 'symbol': p.symbol,
                                'magic': p.magic,
                                'type': 'BUY' if is_buy else 'SELL',
                                'volume': p.volume, 'price_open': p.price_open,
                                'price_current': p.price_current, 'sl': p.sl, 'tp': p.tp,
                                'profit': p.profit, 'comment': p.comment,
                                'is_breakeven': is_be
                            })
                ord_list = mt5.orders_get()
                if ord_list:
                    for o in ord_list:
                        if o.magic in self.all_magics:
                            o_type_str = "BUY_LIMIT" if o.type == mt5.ORDER_TYPE_BUY_LIMIT else ("SELL_LIMIT" if o.type == mt5.ORDER_TYPE_SELL_LIMIT else str(o.type))
                            pending_ords.append({
                                'ticket': o.ticket, 'symbol': o.symbol,
                                'magic': o.magic,
                                'type': o_type_str, 'volume': o.volume_initial,
                                'price_open': o.price_open, 'sl': o.sl, 'tp': o.tp,
                                'comment': o.comment
                            })
                            
            data = {
                'timestamp': time.time(),
                'ist_time': now.strftime('%Y-%m-%d %H:%M:%S IST'),
                'paper_mode': self.paper_mode,
                'architecture': 'DECOUPLED 5-ASSET INSTITUTIONAL PORTFOLIO (Zero Shared Parameters)',
                'account': {
                    'login': login, 'server': server,
                    'balance': round(bal, 2), 'equity': round(equity, 2),
                    'peak': round(peak, 2), 'dd_pct': round(dd, 2),
                    'day_start_balance': round(self.day_start_balance, 2),
                    'day_pnl': round(day_pnl, 2),
                    'day_pnl_pct': round(day_pnl_pct, 2)
                },
                'risk': {
                    'combined_risk': round(sum(cfg['risk_pct'] for cfg in self.assets.values()) * mult, 4),
                    'risk_mode': risk_mode
                },
                'circuit_breaker': {
                    'triggered': self.circuit_breaker_triggered,
                    'limit_pct': -3.00
                },
                'assets': assets_data,
                'open_positions': open_pos,
                'pending_orders': pending_ords,
                'recent_logs': list(reversed(self.recent_logs[-30:]))
            }
            
            temp = self.state_file + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp, self.state_file)
        except Exception:
            pass

    def run_daemon_loop(self, poll_interval_sec: int = 5):
        print(f"[DAEMON] Starting Decoupled 5-Asset Execution Loop (Polling every {poll_interval_sec}s)...", flush=True)
        last_hb = 0
        try:
            while True:
                self.run_tick()
                if time.time() - last_hb >= 60:
                    now = self.get_ist_now()
                    bal, peak, dd = self.get_current_account_metrics()
                    mult, risk_mode = self.get_throttled_multiplier(dd)
                    active_assets = ", ".join(self.assets.keys())
                    print(f"[{now.strftime('%H:%M:%S')} IST] Bal: ${bal:,.2f} | DD: {dd:4.2f}% | Mode: {risk_mode} | Active: [{active_assets}]", flush=True)
                    last_hb = time.time()
                time.sleep(poll_interval_sec)
        except KeyboardInterrupt:
            print("\n[DAEMON] 🛑 Operator stopped daemon safely.", flush=True)
            mt5.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Decoupled 5-Asset Institutional Portfolio Daemon")
    parser.add_argument("--live", action="store_true", help="Enable Real Live MT5 Trading")
    args = parser.parse_args()
    
    daemon = DecoupledPortfolioDaemon(paper_mode=not args.live)
    if daemon.initialize():
        daemon.run_daemon_loop()
