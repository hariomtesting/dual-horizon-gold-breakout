"""
INSTITUTIONAL DAYTIME LIQUIDITY SWEEP SCALPER (10:00 AM - 15:55 PM IST)
========================================================================
Designed specifically for active daytime intraday trading under real Vantage broker frictions.
Key Pillars:
1. Multi-Opportunity Daytime Engine: Operates continuously between 10:00 AM and 15:55 IST (2-5 trades/day).
2. Liquidity Pool Sweep (SMC/ICT Judas Model):
   - Identifies rolling 12-bar (60-min) Swing Highs / Lows.
   - Triggers when price sweeps the pool to trap retail breakout traders.
   - Enters on displacement rejection back inside the range.
3. Maker Limit Retest: Enters on 50% candle pullback -> $0.00 adverse slippage.
4. Asymmetric Split Target:
   - Tranche 1: 1.4R quick profit lock + Move SL to Breakeven.
   - Tranche 2: 2.5R runner to opposite liquidity pool.
5. Hard 15:55 IST square-off: 100% Flat daily before 4:00 PM shutdown.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_m5_data(symbol, n_bars=70000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    # November standard time offset: UTC+2 broker -> UTC+5:30 IST (+3.5h)
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    # ATR & Trend EMA
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    
    # Rolling 12-bar (60 min) Swing High & Low
    df['swing_high'] = df['high'].shift(1).rolling(12).max()
    df['swing_low'] = df['low'].shift(1).rolling(12).min()
    return df

class DaytimeLiquidityScalper:
    def __init__(self, initial_capital=10000.0, risk_per_trade=0.010):
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.specs = {
            'XAUUSD': {'contract': 100.0, 'spread': 0.30, 'comm': 6.0, 'min_sweep': 0.5, 'tp1_r': 1.4, 'tp2_r': 2.5},
            'BTCUSD': {'contract': 1.0, 'spread': 17.05, 'comm': 6.0, 'min_sweep': 25.0, 'tp1_r': 1.4, 'tp2_r': 2.5},
            'GER40.r': {'contract': 1.0, 'spread': 1.50, 'comm': 4.0, 'min_sweep': 5.0, 'tp1_r': 1.4, 'tp2_r': 2.5}
        }
        
    def backtest(self, asset_dfs):
        common_dates = sorted(list(set.intersection(*[set(df['date'].unique()) for df in asset_dfs.values()])))
        
        balance = self.initial_capital
        peak_balance = self.initial_capital
        equity_curve = [balance]
        daily_records = []
        all_trades = []
        
        for cur_date in common_dates:
            day_pnl = 0.0
            day_trades = []
            
            for sym, cfg in self.specs.items():
                if sym not in asset_dfs:
                    continue
                df_sym = asset_dfs[sym]
                bars = df_sym[df_sym['date'] == cur_date].to_dict('records')
                if len(bars) < 30:
                    continue
                    
                pnl, trades = self._simulate_day(bars, sym, cfg, balance)
                day_pnl += pnl
                day_trades.extend(trades)
                
            # Daily -3% Circuit Breaker
            if day_pnl < -(balance * 0.03):
                day_pnl = -(balance * 0.03)
                
            balance += day_pnl
            balance = max(100.0, balance)
            if balance > peak_balance:
                peak_balance = balance
                
            equity_curve.append(balance)
            daily_records.append({
                'date': cur_date, 'balance': balance, 'daily_pnl': day_pnl,
                'dd_pct': ((peak_balance - balance) / peak_balance) * 100.0
            })
            all_trades.extend(day_trades)
            
        return self._calc_stats(daily_records, equity_curve, all_trades)
        
    def _simulate_day(self, bars, sym, cfg, balance):
        contract = cfg['contract']
        spread = cfg['spread']
        comm_lot = cfg['comm']
        tp1_r = cfg['tp1_r']
        tp2_r = cfg['tp2_r']
        
        active_pos = None
        pending_limit = None
        trades_today = 0
        max_trades_per_day = 3
        dollar_pnl = 0.0
        trades = []
        
        for i, row in enumerate(bars):
            h = row['ist_hour']
            m = row['ist_minute']
            
            # Trading window: 10:00 to 15:55 IST
            if h < 10:
                continue
                
            # Hard 15:55 IST Square-off
            if (h == 15 and m >= 55) or h > 15:
                if pending_limit:
                    pending_limit = None
                if active_pos:
                    exit_p = row['close'] - (spread/2.0) if active_pos['dir'] == 'BUY' else row['close'] + (spread/2.0)
                    pts = (exit_p - active_pos['entry']) if active_pos['dir'] == 'BUY' else (active_pos['entry'] - exit_p)
                    lots = active_pos['lots_t1'] + active_pos['lots_t2']
                    comm = lots * comm_lot
                    pnl = (pts * lots * contract) - comm
                    dollar_pnl += pnl
                    trades.append({'sym': sym, 'pnl': pnl, 'type': '15:55_CUTOFF'})
                    active_pos = None
                break
                
            # 1. Manage Active Position
            if active_pos is not None:
                d = active_pos['dir']
                entry = active_pos['entry']
                sl = active_pos['sl']
                tp1 = active_pos['tp1']
                tp2 = active_pos['tp2']
                
                # Tranche 1 check (1.4R quick lock)
                if active_pos['lots_t1'] > 0:
                    hit_tp1 = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit_tp1:
                        pnl1 = (abs(tp1 - entry) * active_pos['lots_t1'] * contract) - (active_pos['lots_t1'] * comm_lot)
                        dollar_pnl += pnl1
                        trades.append({'sym': sym, 'pnl': pnl1, 'type': 'TP1_LOCK'})
                        active_pos['lots_t1'] = 0.0
                        # Move Tranche 2 SL to Breakeven
                        active_pos['sl'] = entry + (0.05 * abs(entry - sl)) if d == 'BUY' else entry - (0.05 * abs(entry - sl))
                        sl = active_pos['sl']
                        
                # Tranche 2 check (2.5R runner or SL/BE)
                if active_pos['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active_pos['lots_t2'] * contract) - (active_pos['lots_t2'] * comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'sym': sym, 'pnl': pnl2, 'type': 'TP2_RUNNER'})
                        active_pos = None
                        continue
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active_pos['lots_t2'] * contract) - (active_pos['lots_t2'] * comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'sym': sym, 'pnl': pnl2, 'type': 'SL_OR_BE'})
                        active_pos = None
                        continue
                continue
                
            # 2. Check Pending Retest Limit Order
            if pending_limit is not None:
                lp = pending_limit['lp']
                filled = (row['low'] <= lp) if pending_limit['dir'] == 'BUY' else (row['high'] >= lp)
                if filled:
                    active_pos = {
                        'dir': pending_limit['dir'],
                        'entry': lp,
                        'sl': pending_limit['sl'],
                        'tp1': pending_limit['tp1'],
                        'tp2': pending_limit['tp2'],
                        'lots_t1': pending_limit['lots_t1'],
                        'lots_t2': pending_limit['lots_t2']
                    }
                    pending_limit = None
                    continue
                else:
                    pending_limit['bars_alive'] += 1
                    if pending_limit['bars_alive'] >= 6: # Cancel after 30 mins unfilled
                        pending_limit = None
                        
            # 3. Look for Fresh Liquidity Sweep Signal
            if active_pos is None and pending_limit is None and trades_today < max_trades_per_day:
                sw_h = row['swing_high']
                sw_l = row['swing_low']
                atr = row['atr']
                if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0:
                    continue
                    
                # Bullish Sweep: Low wicks below Swing Low by min_sweep, but Close rejects back ABOVE Swing Low
                is_bull_sweep = (row['low'] < (sw_l - cfg['min_sweep'])) and (row['close'] > sw_l)
                # Bearish Sweep: High wicks above Swing High by min_sweep, but Close rejects back BELOW Swing High
                is_bear_sweep = (row['high'] > (sw_h + cfg['min_sweep'])) and (row['close'] < sw_h)
                
                risk_cash = balance * self.risk_per_trade
                
                if is_bull_sweep and row['close'] >= row['ema200']:
                    # Retest entry at 50% of the sweep candle
                    lp = (row['close'] + row['low']) / 2.0
                    sl = row['low'] - (0.20 * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                        lots_t1 = round(total_lots / 2.0, 2)
                        lots_t2 = total_lots - lots_t1
                        pending_limit = {
                            'dir': 'BUY', 'lp': lp, 'sl': sl,
                            'tp1': lp + (tp1_r * sl_dist),
                            'tp2': lp + (tp2_r * sl_dist),
                            'lots_t1': lots_t1, 'lots_t2': lots_t2,
                            'bars_alive': 0
                        }
                        trades_today += 1
                elif is_bear_sweep and row['close'] <= row['ema200']:
                    # Retest entry at 50% of the sweep candle
                    lp = (row['close'] + row['high']) / 2.0
                    sl = row['high'] + (0.20 * atr)
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                        lots_t1 = round(total_lots / 2.0, 2)
                        lots_t2 = total_lots - lots_t1
                        pending_limit = {
                            'dir': 'SELL', 'lp': lp, 'sl': sl,
                            'tp1': lp - (tp1_r * sl_dist),
                            'tp2': lp - (tp2_r * sl_dist),
                            'lots_t1': lots_t1, 'lots_t2': lots_t2,
                            'bars_alive': 0
                        }
                        trades_today += 1
                        
        return dollar_pnl, trades

    def _calc_stats(self, daily_records, equity_curve, all_trades):
        df = pd.DataFrame(daily_records)
        df['year'] = pd.to_datetime(df['date']).dt.year
        final_bal = equity_curve[-1]
        n_days = len(daily_records)
        years = n_days / 252.0
        cagr = ((final_bal / self.initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0
        max_dd = df['dd_pct'].max()
        
        win_trades = [t for t in all_trades if t['pnl'] > 0]
        loss_trades = [t for t in all_trades if t['pnl'] < 0]
        win_rate = (len(win_trades) / len(all_trades) * 100.0) if all_trades else 0.0
        gross_p = sum(t['pnl'] for t in win_trades)
        gross_l = abs(sum(t['pnl'] for t in loss_trades))
        pf = (gross_p / gross_l) if gross_l > 0 else 999.0
        daily_rets = df['daily_pnl'] / df['balance'].shift(1).fillna(self.initial_capital)
        sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-6)) * np.sqrt(252)
        
        breakdown = {}
        for sym in self.specs.keys():
            s_trades = [t for t in all_trades if t['sym'] == sym]
            s_pnl = sum(t['pnl'] for t in s_trades)
            s_wins = [t for t in s_trades if t['pnl'] > 0]
            s_wr = (len(s_wins) / len(s_trades) * 100.0) if s_trades else 0.0
            breakdown[sym] = {'pnl': s_pnl, 'trades': len(s_trades), 'win_rate': s_wr}
            
        return {
            'final_balance': final_bal, 'cagr': cagr, 'max_drawdown_pct': max_dd,
            'sharpe_ratio': sharpe, 'profit_factor': pf, 'win_rate': win_rate,
            'total_trades': len(all_trades), 'breakdown': breakdown
        }

def main():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    print("Loading M5 data for Daytime Liquidity Scalper (XAUUSD, BTCUSD, GER40.r)...")
    dfs = {}
    for s in ['XAUUSD', 'BTCUSD', 'GER40.r']:
        d = load_m5_data(s, 60000)
        if d is not None:
            dfs[s] = d
            print(f"Loaded {s}: {len(d)} bars")
    mt5.shutdown()
    
    print("\n" + "=" * 110)
    print("DAYTIME LIQUIDITY SWEEP SCALPER (STRICTLY 10:00 AM - 15:55 IST)")
    print("Live Vantage Spreads, Commissions, Maker Limit Retests & Split Targets")
    print("=" * 110)
    
    for r in [0.0075, 0.010, 0.0125]:
        scalper = DaytimeLiquidityScalper(10000.0, risk_per_trade=r)
        res = scalper.backtest(dfs)
        print(f"Risk: {r*100:4.2f}% | CAGR: {res['cagr']:+7.2f}% | Max DD: {res['max_drawdown_pct']:5.2f}% | Sharpe: {res['sharpe_ratio']:4.2f} | PF: {res['profit_factor']:4.2f} | Win%: {res['win_rate']:4.1f}% | Trades: {res['total_trades']:<4} | Final: ${res['final_balance']:,.2f}")
        
    print("\nINDIVIDUAL ASSET BREAKDOWN (At 1.0% Risk):")
    scalper10 = DaytimeLiquidityScalper(10000.0, risk_per_trade=0.010)
    res10 = scalper10.backtest(dfs)
    for s, p in res10['breakdown'].items():
        print(f"  • {s:<10}: Profit = ${p['pnl']:+9,.2f} | Trades: {p['trades']:<4} | Win Rate: {p['win_rate']:4.1f}%")
    print("=" * 110)

if __name__ == "__main__":
    main()

