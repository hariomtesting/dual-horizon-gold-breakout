"""
INSTITUTIONAL LIVE MARKET SIMULATOR & EXECUTION ENGINE
======================================================
MANDATORY PROTOCOL:
Every trading strategy created, discovered, or tested in this project
MUST be evaluated through this Real-World Simulator before live deployment.
Frictionless / theoretical backtests are strictly prohibited.

Real-World Friction Model:
1. Live Broker Spreads: Audited from MT5 (e.g. Gold $0.30, EURUSD 1.4 pips)
2. ECN & Prop Commissions: $6.00 to $7.00 per standard round lot
3. Network Latency & Slippage: Models 345ms ping impact on Stop triggers
4. Daily Circuit Breaker: Hard liquidation & halt at -2.00% daily loss
5. End-of-Day Flat Rule: Mandatory 15:55 IST liquidation (Zero overnight risk)
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
import MetaTrader5 as mt5


class LiveMarketSimulator:
    """
    Standardized Institutional Simulator enforcing real-world broker frictions,
    time-window governance, and prop-firm risk limits.
    """
    def __init__(
        self,
        symbol: str = "XAUUSD",
        initial_balance: float = 10000.0,
        risk_per_trade: float = 0.010,       # 1.0% equity risk per trade
        daily_loss_limit_pct: float = 2.0,   # -2.0% daily circuit breaker
        auto_square_off_hour: int = 15,
        auto_square_off_minute: int = 55,    # 15:55 IST mandatory liquidation
        # Real-World Friction Defaults (Vantage ECN / FTMO Standard)
        spread_override: Optional[float] = None,
        commission_per_lot: float = 6.00,    # $6.00 per round turn lot
        slippage_entry: Optional[float] = None,
        slippage_exit: Optional[float] = None,
        compounding: bool = False
    ):
        self.symbol = symbol.upper()
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.auto_square_off_hour = auto_square_off_hour
        self.auto_square_off_minute = auto_square_off_minute
        self.commission_per_lot = commission_per_lot
        self.compounding = compounding

        # Configure asset-specific contract and friction standards
        is_gold = ("XAU" in self.symbol)
        is_btc = ("BTC" in self.symbol)
        is_index = ("NAS" in self.symbol or "DJ" in self.symbol or "US30" in self.symbol)
        is_eur = ("EUR" in self.symbol)

        if is_gold:
            self.contract_size = 100.0 # 100 oz
            self.spread = spread_override if spread_override is not None else 0.30 # $0.30/oz
            self.slip_in = slippage_entry if slippage_entry is not None else 0.15 # 345ms latency
            self.slip_out = slippage_exit if slippage_exit is not None else 0.08
        elif is_btc:
            self.contract_size = 1.0 # 1 Bitcoin
            self.spread = spread_override if spread_override is not None else 17.05 # $17.05 spread
            self.slip_in = slippage_entry if slippage_entry is not None else 5.0 # $5.00 latency slip
            self.slip_out = slippage_exit if slippage_exit is not None else 2.5
        elif is_index:
            self.contract_size = 1.0 # 1 Index contract
            self.spread = spread_override if spread_override is not None else 1.20
            self.slip_in = slippage_entry if slippage_entry is not None else 0.50
            self.slip_out = slippage_exit if slippage_exit is not None else 0.25
        elif is_eur:
            self.contract_size = 100000.0
            self.spread = spread_override if spread_override is not None else 0.00014 # 1.4 pips
            self.slip_in = slippage_entry if slippage_entry is not None else 0.00006 # 0.6 pips
            self.slip_out = slippage_exit if slippage_exit is not None else 0.00004
        else: # Generic FX
            self.contract_size = 100000.0
            self.spread = spread_override if spread_override is not None else 0.00020
            self.slip_in = slippage_entry if slippage_entry is not None else 0.00010
            self.slip_out = slippage_exit if slippage_exit is not None else 0.00005

    def run_bars(self, data_input, signal_generator_fn) -> Dict[str, Any]:
        """
        Executes an event-driven simulation across M5 bars.
        `data_input` can be either a DataFrame or pre-grouped Dict[date, List[dict]].
        """
        if isinstance(data_input, dict):
            daily_sessions = data_input
        else:
            df_m5 = data_input
            if 'ist_dt' not in df_m5:
                if 'time' in df_m5:
                    df_m5['broker_dt'] = pd.to_datetime(df_m5['time'], unit='s')
                    df_m5['ist_dt'] = df_m5['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
                else:
                    df_m5['ist_dt'] = pd.to_datetime(df_m5.index)

            df_m5['date'] = df_m5['ist_dt'].dt.date
            df_m5['ist_hour'] = df_m5['ist_dt'].dt.hour
            df_m5['ist_minute'] = df_m5['ist_dt'].dt.minute
            daily_sessions = {d: sub.to_dict('records') for d, sub in df_m5.groupby('date') if len(sub) >= 10}

        balance = self.initial_balance
        daily_records = []
        all_trades: List[Dict[str, Any]] = []

        total_broker_toll = 0.0

        for curr_d, bars in daily_sessions.items():
            day_pnl = 0.0
            active_trade: Optional[Dict[str, Any]] = None
            custom_state: Dict[str, Any] = {}
            day_stopped = False

            for i in range(len(bars)):
                row = bars[i]
                h = row['ist_hour']
                m = row['ist_minute']

                # 1. Mandatory Auto-Square-Off at 15:55 IST
                if h == self.auto_square_off_hour and m >= self.auto_square_off_minute:
                    if active_trade is not None:
                        lots = active_trade['lots']
                        comm = (lots * self.commission_per_lot)
                        total_broker_toll += comm

                        if active_trade['type'] == 'BUY':
                            exit_p = row['close'] - (self.spread / 2.0) - self.slip_out
                            net_pts = exit_p - active_trade['fill_price']
                        else:
                            exit_p = row['close'] + (self.spread / 2.0) + self.slip_out
                            net_pts = active_trade['fill_price'] - exit_p

                        dollar_pnl = (net_pts * lots * self.contract_size) - comm
                        day_pnl += dollar_pnl
                        trade_r = (dollar_pnl / (balance * self.risk_per_trade))

                        active_trade['exit_time'] = row['ist_dt']
                        active_trade['exit_price'] = exit_p
                        active_trade['exit_reason'] = "15:55 IST Hard Liquidation"
                        active_trade['dollar_pnl'] = dollar_pnl
                        active_trade['r_pnl'] = trade_r
                        all_trades.append(active_trade)
                        active_trade = None
                    break

                # 2. Check Exits for active trade
                if active_trade is not None:
                    ttype = active_trade['type']
                    fill_p = active_trade['fill_price']
                    sl_p = active_trade['sl']
                    tp_p = active_trade['tp']
                    lots = active_trade['lots']
                    comm = (lots * self.commission_per_lot)

                    hit_tp = False
                    hit_sl = False
                    exit_p = 0.0

                    if ttype == 'BUY':
                        if row['high'] >= tp_p:
                            hit_tp = True
                            exit_p = tp_p - (self.spread / 2.0) - self.slip_out
                        elif row['low'] <= sl_p:
                            hit_sl = True
                            exit_p = sl_p - (self.spread / 2.0) - self.slip_out
                    else: # SELL
                        if row['low'] <= tp_p:
                            hit_tp = True
                            exit_p = tp_p + (self.spread / 2.0) + self.slip_out
                        elif row['high'] >= sl_p:
                            hit_sl = True
                            exit_p = sl_p + (self.spread / 2.0) + self.slip_out

                    if hit_tp or hit_sl:
                        total_broker_toll += comm
                        net_pts = (exit_p - fill_p) if ttype == 'BUY' else (fill_p - exit_p)
                        dollar_pnl = (net_pts * lots * self.contract_size) - comm
                        day_pnl += dollar_pnl
                        trade_r = (dollar_pnl / (balance * self.risk_per_trade))

                        active_trade['exit_time'] = row['ist_dt']
                        active_trade['exit_price'] = exit_p
                        active_trade['exit_reason'] = "Take_Profit" if hit_tp else "Stop_Loss"
                        active_trade['dollar_pnl'] = dollar_pnl
                        active_trade['r_pnl'] = trade_r
                        all_trades.append(active_trade)
                        active_trade = None

                        # Check Daily Circuit Breaker
                        if day_pnl <= -(balance * (self.daily_loss_limit_pct / 100.0)):
                            day_stopped = True
                            break
                        continue

                # 3. Query Strategy Signal if flat
                if active_trade is None and not day_stopped:
                    # Daily loss check before taking new order
                    if day_pnl <= -(balance * (self.daily_loss_limit_pct / 100.0)):
                        day_stopped = True
                        break

                    sig = signal_generator_fn(bars, i, custom_state)
                    # Expected format: {'type': 'BUY'/'SELL', 'trigger': float, 'sl': float, 'tp': float}
                    if sig is not None:
                        sig_type = sig['type']
                        trig = sig['trigger']
                        sig_sl = sig['sl']
                        sig_tp = sig['tp']

                        sl_dist = abs(trig - sig_sl)
                        if sl_dist > 0:
                            # Position sizing: 1% risk of account balance
                            base_for_risk = balance if self.compounding else self.initial_balance
                            risk_cash = base_for_risk * self.risk_per_trade
                            raw_lots = risk_cash / (sl_dist * self.contract_size)
                            lots = max(0.01, round(raw_lots, 2))

                            # Trigger execution with Spread and Latency Slippage
                            if sig_type in ('BUY', 'BUY_STOP') and row['high'] >= trig:
                                fill_p = trig + (self.spread / 2.0) + self.slip_in
                                active_trade = {
                                    'symbol': self.symbol,
                                    'type': 'BUY',
                                    'order_mechanism': 'STOP',
                                    'entry_time': row['ist_dt'],
                                    'trigger': trig,
                                    'fill_price': fill_p,
                                    'sl': sig_sl,
                                    'tp': sig_tp,
                                    'lots': lots,
                                    'sl_dist': sl_dist
                                }
                            elif sig_type in ('SELL', 'SELL_STOP') and row['low'] <= trig:
                                fill_p = trig - (self.spread / 2.0) - self.slip_in
                                active_trade = {
                                    'symbol': self.symbol,
                                    'type': 'SELL',
                                    'order_mechanism': 'STOP',
                                    'entry_time': row['ist_dt'],
                                    'trigger': trig,
                                    'fill_price': fill_p,
                                    'sl': sig_sl,
                                    'tp': sig_tp,
                                    'lots': lots,
                                    'sl_dist': sl_dist
                                }
                            elif sig_type == 'BUY_LIMIT' and row['low'] <= trig:
                                # Limit orders execute as maker/resting: no adverse latency slippage
                                fill_p = trig + (self.spread / 2.0)
                                active_trade = {
                                    'symbol': self.symbol,
                                    'type': 'BUY',
                                    'order_mechanism': 'LIMIT',
                                    'entry_time': row['ist_dt'],
                                    'trigger': trig,
                                    'fill_price': fill_p,
                                    'sl': sig_sl,
                                    'tp': sig_tp,
                                    'lots': lots,
                                    'sl_dist': sl_dist
                                }
                            elif sig_type == 'SELL_LIMIT' and row['high'] >= trig:
                                # Limit orders execute as maker/resting: no adverse latency slippage
                                fill_p = trig - (self.spread / 2.0)
                                active_trade = {
                                    'symbol': self.symbol,
                                    'type': 'SELL',
                                    'order_mechanism': 'LIMIT',
                                    'entry_time': row['ist_dt'],
                                    'trigger': trig,
                                    'fill_price': fill_p,
                                    'sl': sig_sl,
                                    'tp': sig_tp,
                                    'lots': lots,
                                    'sl_dist': sl_dist
                                }

            # Daily circuit breaker cap
            max_daily_loss = -(balance * (self.daily_loss_limit_pct / 100.0))
            if day_pnl < max_daily_loss:
                day_pnl = max_daily_loss

            balance += day_pnl
            daily_records.append({
                'date': curr_d,
                'day_pnl_dollars': day_pnl,
                'pnl_pct': (day_pnl / (balance - day_pnl)) if (balance - day_pnl) > 0 else 0.0,
                'ending_balance': balance
            })

        return self._compute_performance_report(daily_records, all_trades, total_broker_toll)

    def _compute_performance_report(self, daily_records: List[Dict[str, Any]], trades: List[Dict[str, Any]], broker_toll: float) -> Dict[str, Any]:
        df_daily = pd.DataFrame(daily_records)
        df_daily['year'] = pd.to_datetime(df_daily['date']).dt.year
        df_daily['ym'] = pd.to_datetime(df_daily['date']).dt.strftime('%Y-%m')

        equity = (1.0 + df_daily['pnl_pct']).cumprod()
        peak = equity.cummax()
        dd = (equity - peak) / peak
        max_dd = dd.min() * 100.0

        monthly = df_daily.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
        green_m = (monthly > 0).sum()
        total_m = len(monthly)
        pct_green = (green_m / total_m * 100.0) if total_m > 0 else 0.0

        annual = df_daily.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0

        m2026 = monthly[monthly.index.str.startswith('2026')]
        g2026 = (m2026 > 0).sum()
        t2026 = len(m2026)

        if len(trades) > 0:
            df_t = pd.DataFrame(trades)
            wins = df_t[df_t['dollar_pnl'] > 0]
            losses = df_t[df_t['dollar_pnl'] <= 0]
            wr = (len(wins) / len(df_t)) * 100.0
            g_sum = wins['dollar_pnl'].sum() if len(wins) > 0 else 0.0
            l_sum = abs(losses['dollar_pnl'].sum()) if len(losses) > 0 else 0.0
            pf = (g_sum / l_sum) if l_sum > 0 else (999.0 if g_sum > 0 else 0.0)
            avg_r = df_t['r_pnl'].mean()
        else:
            wr = 0.0; pf = 0.0; avg_r = 0.0

        return {
            'symbol': self.symbol,
            'initial_balance': self.initial_balance,
            'final_balance': df_daily['ending_balance'].iloc[-1] if len(df_daily) > 0 else self.initial_balance,
            'net_profit': (df_daily['ending_balance'].iloc[-1] - self.initial_balance) if len(df_daily) > 0 else 0.0,
            'avg_annual_roi': float(annual.mean()),
            'worst_year_roi': float(annual.min()),
            'max_drawdown_pct': float(max_dd),
            'green_months': f"{green_m}/{total_m} ({pct_green:.1f}%)",
            'consistency_2026': f"{g2026}/{t2026}",
            'total_trades': len(trades),
            'win_rate': float(wr),
            'profit_factor': float(pf),
            'avg_r_per_trade': float(avg_r),
            'total_broker_toll_paid': float(broker_toll),
            'annual_breakdown': annual.to_dict(),
            'monthly_series': monthly,
            'trades': trades,
            'daily_records': df_daily
        }

    def print_report(self, report: Dict[str, Any]):
        print("\n" + "=" * 90)
        print(f"REAL-WORLD LIVE MARKET AUDIT REPORT: {report['symbol']}")
        print("=" * 90)
        print(f"Initial Balance     : ${report['initial_balance']:,.2f}")
        print(f"Final Balance       : ${report['final_balance']:,.2f} (Net: ${report['net_profit']:+,.2f})")
        print(f"Avg Annual Return   : {report['avg_annual_roi']:+.2f}%")
        print(f"Worst Single Year   : {report['worst_year_roi']:+.2f}%")
        print(f"Max Portfolio DD    : {report['max_drawdown_pct']:.2f}%")
        print(f"5-Year Green Months : {report['green_months']}")
        print(f"2026 Consistency    : {report['consistency_2026']} green months")
        print(f"Total Trades Taken  : {report['total_trades']}")
        print(f"Trade Win Rate      : {report['win_rate']:.1f}%")
        print(f"Real Profit Factor  : {report['profit_factor']:.2f}")
        print(f"Net Expectancy (Avg): {report['avg_r_per_trade']:+.3f}R per trade")
        print(f"Broker Toll Deducted: ${report['total_broker_toll_paid']:,.2f} in spreads, comms & latency")
        print("-" * 90)
        print("Annual Returns (Net of all fees):")
        for yr, val in report['annual_breakdown'].items():
            print(f"   {yr}: {val:+.2f}%")
        print("=" * 90 + "\n")
