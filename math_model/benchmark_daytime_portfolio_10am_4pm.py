"""
DAYTIME INSTITUTIONAL PORTFOLIO BENCHMARK: STRICTLY 10:00 AM - 16:00 PM IST
===========================================================================
Strict Operational Rules:
- Running strictly on local PC between 10:00 AM and 16:00 PM IST (No VPS needed).
- All positions auto-square-off at 15:55 IST (100% Flat before 4:00 PM shutdown).
- Eliminates US Evening Session (No Nasdaq at 19:00 IST).

Assets Evaluated in Daytime Window:
1. Gold (XAUUSD)       : European Bullion Anchor @ 12:00 PM IST (Square-off: 15:55 IST)
2. Bitcoin (BTCUSD)    : European Crypto Anchor  @ 12:00 PM IST (Square-off: 15:55 IST)
3. USDJPY (Forex)      : London Macro Flow       @ 13:00 PM IST (Square-off: 15:55 IST)
4. DAX 40 (GER40.r)    : Frankfurt Cash Open     @ 12:30 PM IST (Square-off: 15:55 IST)

Under Real Live Vantage Broker Fees & Backup Safeguards (Split-Target + De-leveraging).
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data(symbol, n_bars=70000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    return df

class DaytimePortfolioSimulator:
    def __init__(self, initial_capital=10000.0, use_safeguards=True):
        self.initial_capital = initial_capital
        self.use_safeguards = use_safeguards
        
        self.specs = {
            'XAUUSD': {'contract': 100.0, 'spread': 0.30, 'comm': 6.0, 'open_h': 12, 'open_m': 0, 'tau': 1.4, 'sl': 1.5, 'min_atr': 3.50},
            'BTCUSD': {'contract': 1.0, 'spread': 17.05, 'comm': 6.0, 'open_h': 12, 'open_m': 0, 'tau': 1.4, 'sl': 2.0, 'min_atr': 120.0},
            'USDJPY': {'contract': 100000.0, 'spread': 0.015, 'comm': 6.0, 'open_h': 13, 'open_m': 0, 'tau': 1.0, 'sl': 1.5, 'min_atr': 0.05},
            'GER40.r': {'contract': 1.0, 'spread': 1.50, 'comm': 4.0, 'open_h': 12, 'open_m': 30, 'tau': 1.4, 'sl': 1.5, 'min_atr': 20.0}
        }
        
    def run(self, asset_dfs):
        common_dates = sorted(list(set.intersection(*[set(df['date'].unique()) for df in asset_dfs.values()])))
        
        balance = self.initial_capital
        peak_balance = self.initial_capital
        equity_curve = [balance]
        daily_records = []
        all_trades = []
        
        for cur_date in common_dates:
            # Drawdown De-leveraging
            dd_pct = ((peak_balance - balance) / peak_balance) * 100.0
            if self.use_safeguards:
                if dd_pct >= 12.0:
                    current_risk = 0.005
                elif dd_pct >= 8.0:
                    current_risk = 0.010
                else:
                    current_risk = 0.015
            else:
                current_risk = 0.015
                
            day_pnl = 0.0
            day_trades = []
            
            for sym, cfg in self.specs.items():
                if sym not in asset_dfs:
                    continue
                bars = asset_dfs[sym][asset_dfs[sym]['date'] == cur_date].to_dict('records')
                if len(bars) < 30:
                    continue
                    
                if self.use_safeguards:
                    if bars[0]['atr'] < cfg['min_atr']:
                        continue
                        
                pnl, trades = self._simulate_daytime_session(bars, sym, cfg, balance, current_risk)
                day_pnl += pnl
                day_trades.extend(trades)
                
            # Hard Daily Circuit Breaker (-3.0%)
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
        
    def _simulate_daytime_session(self, bars, sym, cfg, balance, risk_pct):
        contract = cfg['contract']
        spread = cfg['spread']
        comm_lot = cfg['comm']
        open_h = cfg['open_h']
        open_m = cfg['open_m']
        tau = cfg['tau']
        sl_mult = cfg['sl']
        
        # Hard square-off cutoff at 15:55 IST
        cutoff_h = 15
        cutoff_m = 55
        
        state = {'phase': 'WAIT_OPEN', 'cnt': 0}
        active_trade = None
        dollar_pnl = 0.0
        trades = []
        
        for row in bars:
            h = row['ist_hour']
            m = row['ist_minute']
            
            # MANDATORY 15:55 IST SQUARE-OFF
            if (h == cutoff_h and m >= cutoff_m) or h > cutoff_h:
                if active_trade is not None:
                    exit_p = row['close'] - (spread/2.0) if active_trade['dir'] == 'BUY' else row['close'] + (spread/2.0)
                    pts = (exit_p - active_trade['entry']) if active_trade['dir'] == 'BUY' else (active_trade['entry'] - exit_p)
                    lots = active_trade['lots_t1'] + active_trade['lots_t2']
                    comm = lots * comm_lot
                    pnl = (pts * lots * contract) - comm
                    dollar_pnl += pnl
                    trades.append({'sym': sym, 'pnl': pnl, 'type': '15:55_CUTOFF'})
                    active_trade = None
                break
                
            if active_trade is not None:
                d = active_trade['dir']
                entry = active_trade['entry']
                sl = active_trade['sl']
                tp1 = active_trade['tp1']
                tp2 = active_trade['tp2']
                
                # Tranche 1: Lock @ 1.4R
                if active_trade['lots_t1'] > 0:
                    hit_tp1 = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit_tp1:
                        pnl1 = (abs(tp1 - entry) * active_trade['lots_t1'] * contract) - (active_trade['lots_t1'] * comm_lot)
                        dollar_pnl += pnl1
                        trades.append({'sym': sym, 'pnl': pnl1, 'type': 'TP1_LOCK'})
                        active_trade['lots_t1'] = 0.0
                        # Move Tranche 2 SL to Breakeven
                        active_trade['sl'] = entry + (0.05 * abs(entry - sl)) if d == 'BUY' else entry - (0.05 * abs(entry - sl))
                        sl = active_trade['sl']
                        
                # Tranche 2: Runner @ 3.5R or BE
                if active_trade['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active_trade['lots_t2'] * contract) - (active_trade['lots_t2'] * comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'sym': sym, 'pnl': pnl2, 'type': 'TP2_RUNNER'})
                        active_trade = None
                        break
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active_trade['lots_t2'] * contract) - (active_trade['lots_t2'] * comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'sym': sym, 'pnl': pnl2, 'type': 'SL_OR_BE'})
                        active_trade = None
                        break
                continue
                
            # Signal
            if state['phase'] == 'WAIT_OPEN' and h == open_h and m == open_m:
                state['phase'] = 'EVAL'
                state['p0'] = row['open']
                state['atr'] = row['atr']
                state['ema'] = row['ema200']
                state['cnt'] = 0
            elif state['phase'] == 'EVAL':
                state['cnt'] += 1
                if state['cnt'] >= 6: # 30 mins
                    p0 = state['p0']
                    p1 = row['close']
                    atr = state['atr']
                    ema = state['ema']
                    r_norm = (p1 - p0) / (atr + 1e-8)
                    if r_norm >= tau and p1 >= ema:
                        state['phase'] = 'LIMIT'
                        state['dir'] = 'BUY'
                        state['lp'] = p1 - (0.25 * atr)
                        state['sl'] = state['lp'] - (sl_mult * atr)
                        state['tp1'] = state['lp'] + (1.4 * sl_mult * atr)
                        state['tp2'] = state['lp'] + (3.5 * sl_mult * atr)
                    elif r_norm <= -tau and p1 <= ema:
                        state['phase'] = 'LIMIT'
                        state['dir'] = 'SELL'
                        state['lp'] = p1 + (0.25 * atr)
                        state['sl'] = state['lp'] + (sl_mult * atr)
                        state['tp1'] = state['lp'] - (1.4 * sl_mult * atr)
                        state['tp2'] = state['lp'] - (3.5 * sl_mult * atr)
                    else:
                        break
            elif state['phase'] == 'LIMIT':
                lp = state['lp']
                sl = state['sl']
                sl_dist = abs(lp - sl)
                risk_cash = balance * risk_pct
                total_lots = max(0.02, round(risk_cash / (sl_dist * contract), 2))
                lots_t1 = round(total_lots / 2.0, 2)
                lots_t2 = total_lots - lots_t1
                if state['dir'] == 'BUY' and row['low'] <= lp:
                    active_trade = {'dir': 'BUY', 'entry': lp, 'sl': sl, 'tp1': state['tp1'], 'tp2': state['tp2'], 'lots_t1': lots_t1, 'lots_t2': lots_t2}
                elif state['dir'] == 'SELL' and row['high'] >= lp:
                    active_trade = {'dir': 'SELL', 'entry': lp, 'sl': sl, 'tp1': state['tp1'], 'tp2': state['tp2'], 'lots_t1': lots_t1, 'lots_t2': lots_t2}
                    
        return dollar_pnl, trades

    def _calc_stats(self, daily_records, equity_curve, all_trades):
        df = pd.DataFrame(daily_records)
        df['year'] = pd.to_datetime(df['date']).dt.year
        final_bal = equity_curve[-1]
        n_days = len(daily_records)
        years = n_days / 252.0
        cagr = ((final_bal / self.initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0
        max_dd = df['dd_pct'].max()
        
        yearly = {}
        for yr, grp in df.groupby('year'):
            yr_start = grp.iloc[0]['balance'] - grp.iloc[0]['daily_pnl']
            yr_end = grp.iloc[-1]['balance']
            yr_roi = ((yr_end - yr_start) / yr_start) * 100.0
            yearly[yr] = {'roi': yr_roi, 'max_dd': grp['dd_pct'].max()}
            
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
            'total_trades': len(all_trades), 'yearly': yearly, 'breakdown': breakdown
        }

def main():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    print("Loading daytime multi-asset data from MT5 (XAUUSD, BTCUSD, USDJPY, GER40.r)...")
    dfs = {}
    for s in ['XAUUSD', 'BTCUSD', 'USDJPY', 'GER40.r']:
        d = load_data(s, 50000)
        if d is not None:
            dfs[s] = d
            print(f"Loaded {s}: {len(d)} bars")
    mt5.shutdown()
    
    print("\n" + "=" * 115)
    print("DAYTIME PORTFOLIO BENCHMARK: STRICTLY 10:00 AM TO 16:00 IST (15:55 SQUARE-OFF)")
    print("Evaluated under real live Vantage spreads, commissions, and backup safeguards")
    print("=" * 115)
    
    # 1. Dual-Core Daytime: Gold + Bitcoin
    sim_dual = DaytimePortfolioSimulator(10000.0, use_safeguards=True)
    del sim_dual.specs['USDJPY']
    del sim_dual.specs['GER40.r']
    res_dual = sim_dual.run({'XAUUSD': dfs['XAUUSD'], 'BTCUSD': dfs['BTCUSD']})
    
    # 2. Triple Daytime: Gold + Bitcoin + DAX 40 (GER40.r)
    sim_tri = DaytimePortfolioSimulator(10000.0, use_safeguards=True)
    del sim_tri.specs['USDJPY']
    res_tri = sim_tri.run({'XAUUSD': dfs['XAUUSD'], 'BTCUSD': dfs['BTCUSD'], 'GER40.r': dfs['GER40.r']})
    
    # 3. Quad Daytime: Gold + Bitcoin + DAX 40 + USDJPY
    sim_quad = DaytimePortfolioSimulator(10000.0, use_safeguards=True)
    res_quad = sim_quad.run(dfs)
    
    print(f"\n{'PORTFOLIO VARIANT':<35} | {'CAGR ROI':<12} | {'MAX DD':<10} | {'SHARPE':<8} | {'PF':<6} | {'WIN %':<6} | {'FINAL BAL'}")
    print("-" * 115)
    print(f"{'1. Dual-Core: Gold + Bitcoin':<35} | {res_dual['cagr']:+9.2f}% | {res_dual['max_drawdown_pct']:8.2f}% | {res_dual['sharpe_ratio']:6.2f} | {res_dual['profit_factor']:<6.2f} | {res_dual['win_rate']:4.1f}% | ${res_dual['final_balance']:,.2f}")
    print(f"{'2. Triple: Gold + BTC + DAX 40':<35} | {res_tri['cagr']:+9.2f}% | {res_tri['max_drawdown_pct']:8.2f}% | {res_tri['sharpe_ratio']:6.2f} | {res_tri['profit_factor']:<6.2f} | {res_tri['win_rate']:4.1f}% | ${res_tri['final_balance']:,.2f}")
    print(f"{'3. Quad: Gold + BTC + DAX + USDJPY':<35} | {res_quad['cagr']:+9.2f}% | {res_quad['max_drawdown_pct']:8.2f}% | {res_quad['sharpe_ratio']:6.2f} | {res_quad['profit_factor']:<6.2f} | {res_quad['win_rate']:4.1f}% | ${res_quad['final_balance']:,.2f}")
    print("=" * 115)
    
    print("\nINDIVIDUAL ASSET BREAKDOWN (TRIPLE PORTFOLIO: GOLD + BTC + DAX):")
    for s, p in res_tri['breakdown'].items():
        print(f"  • {s:<10}: Profit = ${p['pnl']:+9,.2f} | Trades: {p['trades']:<4} | Win Rate: {p['win_rate']:4.1f}%")
    print("=" * 115)

if __name__ == "__main__":
    main()

