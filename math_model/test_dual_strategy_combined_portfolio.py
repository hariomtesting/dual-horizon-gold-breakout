"""
DUAL-STRATEGY ENSEMBLE PORTFOLIO BENCHMARK (10:00 AM - 15:55 IST)
==================================================================
Simulating the combination of two structurally uncorrelated models on a single $10k account:
1. Strategy 1 (Macro Momentum Anchor): SSRN ITSM triggered at 12:00 IST (Trend Expansion)
2. Strategy 2 (Liquidity Sweep Scalper): ICT/Judas Sweeps across 10:00 - 15:55 IST (Mean Reversion)

Under live Vantage broker frictions ($0.30 Gold spread, $17 BTC spread, $6 comm, $0 slippage limit fills).
Calculates:
- Return correlation between Strategy 1 and Strategy 2.
- Standalone vs Combined CAGR, Max Drawdown, Sharpe, and Profit Factor.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_data(symbol, n_bars=75000):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=30)
    df['date'] = df['ist_dt'].dt.date
    df['year'] = df['ist_dt'].dt.year
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()))
    df['atr'] = tr.rolling(14, min_periods=5).mean().bfill()
    df['swing_high'] = df['high'].shift(1).rolling(12).max()
    df['swing_low'] = df['low'].shift(1).rolling(12).min()
    return df

class DualStrategyPortfolio:
    def __init__(self, initial_capital=10000.0, s1_risk=0.0125, s2_risk=0.0075):
        self.initial_capital = initial_capital
        self.s1_risk = s1_risk
        self.s2_risk = s2_risk
        self.contract = 100.0
        self.spread = 0.30
        self.comm_lot = 6.00
        
    def run(self, df):
        dates = sorted(df['date'].unique())
        
        # Tracking three parallel portfolios:
        # P1: Only Strategy 1 (Anchor Momentum)
        # P2: Only Strategy 2 (Scalper)
        # P_Both: Combined Dual-Engine Portfolio
        
        bal_s1 = self.initial_capital
        bal_s2 = self.initial_capital
        bal_both = self.initial_capital
        
        peak_s1 = self.initial_capital
        peak_s2 = self.initial_capital
        peak_both = self.initial_capital
        
        pnl_s1_list = []
        pnl_s2_list = []
        
        records_s1 = []
        records_s2 = []
        records_both = []
        
        trades_s1 = []
        trades_s2 = []
        
        for cur_date in dates:
            bars = df[df['date'] == cur_date].to_dict('records')
            if len(bars) < 30:
                continue
                
            # Simulate S1 (Anchor Momentum @ 12:00 IST)
            pnl_s1, t_s1 = self._sim_s1(bars, bal_s1, self.s1_risk)
            # Simulate S2 (Liquidity Scalper across 10:00 - 15:55 IST)
            pnl_s2, t_s2 = self._sim_s2(bars, bal_s2, self.s2_risk)
            
            # Simulate Combined (Running both on bal_both)
            pnl_c1, _ = self._sim_s1(bars, bal_both, self.s1_risk)
            pnl_c2, _ = self._sim_s2(bars, bal_both, self.s2_risk)
            pnl_both = pnl_c1 + pnl_c2
            
            # Daily Circuit Breaker (-3.0%)
            if pnl_s1 < -(bal_s1 * 0.03): pnl_s1 = -(bal_s1 * 0.03)
            if pnl_s2 < -(bal_s2 * 0.03): pnl_s2 = -(bal_s2 * 0.03)
            if pnl_both < -(bal_both * 0.03): pnl_both = -(bal_both * 0.03)
            
            bal_s1 += pnl_s1
            bal_s2 += pnl_s2
            bal_both += pnl_both
            
            peak_s1 = max(peak_s1, bal_s1)
            peak_s2 = max(peak_s2, bal_s2)
            peak_both = max(peak_both, bal_both)
            
            pnl_s1_list.append(pnl_s1)
            pnl_s2_list.append(pnl_s2)
            
            records_s1.append({'date': cur_date, 'bal': bal_s1, 'pnl': pnl_s1, 'dd': ((peak_s1 - bal_s1) / peak_s1)*100.0})
            records_s2.append({'date': cur_date, 'bal': bal_s2, 'pnl': pnl_s2, 'dd': ((peak_s2 - bal_s2) / peak_s2)*100.0})
            records_both.append({'date': cur_date, 'bal': bal_both, 'pnl': pnl_both, 'dd': ((peak_both - bal_both) / peak_both)*100.0})
            
            trades_s1.extend(t_s1)
            trades_s2.extend(t_s2)
            
        corr = pd.Series(pnl_s1_list).corr(pd.Series(pnl_s2_list))
        
        res_s1 = self._stats(records_s1, trades_s1)
        res_s2 = self._stats(records_s2, trades_s2)
        res_both = self._stats(records_both, trades_s1 + trades_s2)
        
        return res_s1, res_s2, res_both, corr

    def _sim_s1(self, bars, balance, risk_pct):
        # Strategy 1: European Session Anchor @ 12:00 IST
        state = {'phase': 'WAIT_OPEN', 'cnt': 0}
        active = None
        dollar_pnl = 0.0
        trades = []
        
        for row in bars:
            h = row['ist_hour']
            m = row['ist_minute']
            
            if (h == 15 and m >= 55) or h > 15:
                if active:
                    exit_p = row['close'] - (self.spread/2.0) if active['dir'] == 'BUY' else row['close'] + (self.spread/2.0)
                    pts = (exit_p - active['entry']) if active['dir'] == 'BUY' else (active['entry'] - exit_p)
                    lots = active['lots_t1'] + active['lots_t2']
                    pnl = (pts * lots * self.contract) - (lots * self.comm_lot)
                    dollar_pnl += pnl
                    trades.append({'pnl': pnl, 'type': 'S1_CUTOFF'})
                    active = None
                break
                
            if active:
                d = active['dir']
                entry = active['entry']
                sl = active['sl']
                tp1 = active['tp1']
                tp2 = active['tp2']
                if active['lots_t1'] > 0:
                    hit = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit:
                        pnl1 = (abs(tp1 - entry) * active['lots_t1'] * self.contract) - (active['lots_t1'] * self.comm_lot)
                        dollar_pnl += pnl1
                        trades.append({'pnl': pnl1, 'type': 'S1_TP1'})
                        active['lots_t1'] = 0.0
                        active['sl'] = entry + 0.05 if d == 'BUY' else entry - 0.05
                        sl = active['sl']
                if active['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active['lots_t2'] * self.contract) - (active['lots_t2'] * self.comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'S1_TP2'})
                        active = None
                        break
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active['lots_t2'] * self.contract) - (active['lots_t2'] * self.comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'S1_SL'})
                        active = None
                        break
                continue
                
            if state['phase'] == 'WAIT_OPEN' and h == 12 and m == 0:
                if row['atr'] < 3.50:
                    break
                state['phase'] = 'EVAL'
                state['p0'] = row['open']
                state['atr'] = row['atr']
                state['ema'] = row['ema200']
                state['cnt'] = 0
            elif state['phase'] == 'EVAL':
                state['cnt'] += 1
                if state['cnt'] >= 6:
                    p0 = state['p0']
                    p1 = row['close']
                    atr = state['atr']
                    r_norm = (p1 - p0) / (atr + 1e-8)
                    if r_norm >= 1.40 and p1 >= state['ema']:
                        state['phase'] = 'LIMIT'
                        state['dir'] = 'BUY'
                        state['lp'] = p1 - (0.25 * atr)
                        state['sl'] = state['lp'] - (1.5 * atr)
                        state['tp1'] = state['lp'] + (1.4 * 1.5 * atr)
                        state['tp2'] = state['lp'] + (3.5 * 1.5 * atr)
                    elif r_norm <= -1.40 and p1 <= state['ema']:
                        state['phase'] = 'LIMIT'
                        state['dir'] = 'SELL'
                        state['lp'] = p1 + (0.25 * atr)
                        state['sl'] = state['lp'] + (1.5 * atr)
                        state['tp1'] = state['lp'] - (1.4 * 1.5 * atr)
                        state['tp2'] = state['lp'] - (3.5 * 1.5 * atr)
                    else:
                        break
            elif state['phase'] == 'LIMIT':
                lp = state['lp']
                sl_dist = abs(lp - state['sl'])
                lots = max(0.02, round((balance * risk_pct) / (sl_dist * self.contract), 2))
                l1 = round(lots / 2.0, 2)
                l2 = lots - l1
                if state['dir'] == 'BUY' and row['low'] <= lp:
                    active = {'dir': 'BUY', 'entry': lp, 'sl': state['sl'], 'tp1': state['tp1'], 'tp2': state['tp2'], 'lots_t1': l1, 'lots_t2': l2}
                elif state['dir'] == 'SELL' and row['high'] >= lp:
                    active = {'dir': 'SELL', 'entry': lp, 'sl': state['sl'], 'tp1': state['tp1'], 'tp2': state['tp2'], 'lots_t1': l1, 'lots_t2': l2}
                    
        return dollar_pnl, trades

    def _sim_s2(self, bars, balance, risk_pct):
        # Strategy 2: Liquidity Sweep Scalper (10:00 - 15:55 IST)
        active = None
        pending = None
        dollar_pnl = 0.0
        trades = []
        trades_today = 0
        
        for row in bars:
            h = row['ist_hour']
            m = row['ist_minute']
            
            if h < 10:
                continue
            if (h == 15 and m >= 55) or h > 15:
                if active:
                    exit_p = row['close'] - (self.spread/2.0) if active['dir'] == 'BUY' else row['close'] + (self.spread/2.0)
                    pts = (exit_p - active['entry']) if active['dir'] == 'BUY' else (active['entry'] - exit_p)
                    lots = active['lots_t1'] + active['lots_t2']
                    pnl = (pts * lots * self.contract) - (lots * self.comm_lot)
                    dollar_pnl += pnl
                    trades.append({'pnl': pnl, 'type': 'S2_CUTOFF'})
                    active = None
                break
                
            if active:
                d = active['dir']
                entry = active['entry']
                sl = active['sl']
                tp1 = active['tp1']
                tp2 = active['tp2']
                if active['lots_t1'] > 0:
                    hit = (row['high'] >= tp1) if d == 'BUY' else (row['low'] <= tp1)
                    if hit:
                        pnl1 = (abs(tp1 - entry) * active['lots_t1'] * self.contract) - (active['lots_t1'] * self.comm_lot)
                        dollar_pnl += pnl1
                        trades.append({'pnl': pnl1, 'type': 'S2_TP1'})
                        active['lots_t1'] = 0.0
                        active['sl'] = entry + 0.05 if d == 'BUY' else entry - 0.05
                        sl = active['sl']
                if active['lots_t2'] > 0:
                    hit_tp2 = (row['high'] >= tp2) if d == 'BUY' else (row['low'] <= tp2)
                    hit_sl = (row['low'] <= sl) if d == 'BUY' else (row['high'] >= sl)
                    if hit_tp2:
                        pnl2 = (abs(tp2 - entry) * active['lots_t2'] * self.contract) - (active['lots_t2'] * self.comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'S2_TP2'})
                        active = None
                        continue
                    elif hit_sl:
                        net_p = (sl - entry) if d == 'BUY' else (entry - sl)
                        pnl2 = (net_p * active['lots_t2'] * self.contract) - (active['lots_t2'] * self.comm_lot)
                        dollar_pnl += pnl2
                        trades.append({'pnl': pnl2, 'type': 'S2_SL'})
                        active = None
                        continue
                continue
                
            if pending:
                lp = pending['lp']
                filled = (row['low'] <= lp) if pending['dir'] == 'BUY' else (row['high'] >= lp)
                if filled:
                    active = pending
                    pending = None
                    continue
                else:
                    pending['bars'] += 1
                    if pending['bars'] >= 6:
                        pending = None
                        
            if active is None and pending is None and trades_today < 3:
                sw_h = row['swing_high']
                sw_l = row['swing_low']
                atr = row['atr']
                if pd.isna(sw_h) or pd.isna(sw_l) or atr <= 0:
                    continue
                is_bull = (row['low'] < (sw_l - 0.20*atr)) and (row['close'] > sw_l)
                is_bear = (row['high'] > (sw_h + 0.20*atr)) and (row['close'] < sw_h)
                
                if is_bull and row['close'] >= row['ema200']:
                    lp = (row['close'] + row['low']) / 2.0
                    sl = row['low'] - 0.20*atr
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        lots = max(0.02, round((balance * risk_pct) / (sl_dist * self.contract), 2))
                        l1 = round(lots / 2.0, 2)
                        l2 = lots - l1
                        pending = {'dir': 'BUY', 'entry': lp, 'lp': lp, 'sl': sl, 'tp1': lp + 1.4*sl_dist, 'tp2': lp + 2.5*sl_dist, 'lots_t1': l1, 'lots_t2': l2, 'bars': 0}
                        trades_today += 1
                elif is_bear and row['close'] <= row['ema200']:
                    lp = (row['close'] + row['high']) / 2.0
                    sl = row['high'] + 0.20*atr
                    sl_dist = abs(lp - sl)
                    if sl_dist > 0:
                        lots = max(0.02, round((balance * risk_pct) / (sl_dist * self.contract), 2))
                        l1 = round(lots / 2.0, 2)
                        l2 = lots - l1
                        pending = {'dir': 'SELL', 'entry': lp, 'lp': lp, 'sl': sl, 'tp1': lp - 1.4*sl_dist, 'tp2': lp - 2.5*sl_dist, 'lots_t1': l1, 'lots_t2': l2, 'bars': 0}
                        trades_today += 1
                        
        return dollar_pnl, trades

    def _stats(self, records, trades):
        df_rec = pd.DataFrame(records)
        years = len(df_rec) / 252.0
        final_bal = df_rec.iloc[-1]['bal']
        cagr = ((final_bal / self.initial_capital) ** (1.0 / max(0.1, years)) - 1.0) * 100.0
        max_dd = df_rec['dd'].max()
        
        win_trades = [t for t in trades if t['pnl'] > 0]
        loss_trades = [t for t in trades if t['pnl'] < 0]
        win_rate = (len(win_trades) / len(trades) * 100.0) if trades else 0.0
        gross_p = sum(t['pnl'] for t in win_trades)
        gross_l = abs(sum(t['pnl'] for t in loss_trades))
        pf = (gross_p / gross_l) if gross_l > 0 else 999.0
        
        daily_rets = df_rec['pnl'] / df_rec['bal'].shift(1).fillna(self.initial_capital)
        sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-6)) * np.sqrt(252)
        
        return {
            'cagr': cagr, 'max_dd': max_dd, 'pf': pf, 'win_rate': win_rate,
            'sharpe': sharpe, 'trades': len(trades), 'final_bal': final_bal
        }

def main():
    if not mt5.initialize():
        print("MT5 failed")
        return
        
    print("Loading historical data for Gold (XAUUSD)...")
    df_gold = load_data("XAUUSD", 75000)
    mt5.shutdown()
    
    print("\n" + "=" * 110)
    print("QUANTITATIVE BENCHMARK: WHAT IF WE RUN BOTH STRATEGIES TOGETHER?")
    print("Strategy 1: Session Momentum Anchor (Trend Expansion)")
    print("Strategy 2: Liquidity Sweep Scalper (Mean Reversion Trap)")
    print("=" * 110)
    
    sim = DualStrategyPortfolio(initial_capital=10000.0, s1_risk=0.0125, s2_risk=0.0075)
    res_s1, res_s2, res_both, corr = sim.run(df_gold)
    
    print(f"\n1. CORRELATION ANALYSIS:")
    print(f"   Daily P&L Correlation between Strategy 1 and Strategy 2: r = {corr:+.3f}")
    print(f"   Interpretation: {'STRUCTURALLY UNCORRELATED (Near Zero Correlation)' if abs(corr) < 0.15 else 'Correlated'}")
    
    print(f"\n2. PERFORMANCE COMPARISON (ON THE SAME $10,000 ACCOUNT):")
    print("-" * 110)
    print(f"{'STRATEGY VARIANT':<40} | {'CAGR ROI':<12} | {'MAX DD':<10} | {'SHARPE':<8} | {'PF':<6} | {'FINAL BAL'}")
    print("-" * 110)
    print(f"{'Strategy 1 (Anchor Momentum Standalone)':<40} | {res_s1['cagr']:+9.2f}% | {res_s1['max_dd']:8.2f}% | {res_s1['sharpe']:6.2f} | {res_s1['pf']:<6.2f} | ${res_s1['final_bal']:,.2f}")
    print(f"{'Strategy 2 (Liquidity Scalper Standalone)':<40} | {res_s2['cagr']:+9.2f}% | {res_s2['max_dd']:8.2f}% | {res_s2['sharpe']:6.2f} | {res_s2['pf']:<6.2f} | ${res_s2['final_bal']:,.2f}")
    print(f"{'BOTH STRATEGIES COMBINED (DUAL-ENGINE)':<40} | {res_both['cagr']:+9.2f}% | {res_both['max_dd']:8.2f}% | {res_both['sharpe']:6.2f} | {res_both['pf']:<6.2f} | ${res_both['final_bal']:,.2f}")
    print("=" * 110)
    
    # Calculate Sharpe increase and drawdown reduction
    dd_reduction = ((res_s1['max_dd'] - res_both['max_dd']) / res_s1['max_dd']) * 100.0 if res_s1['max_dd'] > 0 else 0.0
    sharpe_gain = ((res_both['sharpe'] - res_s1['sharpe']) / res_s1['sharpe']) * 100.0 if res_s1['sharpe'] > 0 else 0.0
    
    print("\n3. PORTFOLIO SYNERGY & RISK REDUCTION METRICS:")
    print(f"   • Drawdown Reduction    : Max DD dropped to {res_both['max_dd']:.2f}%")
    print(f"   • Sharpe Ratio Boost    : Sharpe improved from {res_s1['sharpe']:.2f} to {res_both['sharpe']:.2f} (+{sharpe_gain:.1f}%)")
    print(f"   • Combined CAGR Return  : {res_both['cagr']:+.2f}%")
    print("=" * 110 + "\n")

if __name__ == "__main__":
    main()

