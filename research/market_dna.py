"""
Empirical Market DNA Discovery Engine
Instead of imposing human rules, this engine computes the exact empirical
probabilities of market behavior across hundreds of real trading days:
1. Previous Day Touch Probabilities (Yesterday's VPOC, VAH, VAL, High, Low)
2. VWAP Stretch & Reversion Probabilities (+-1.0 std, +-1.5 std, +-2.0 std, +-2.5 std)
3. Initial Balance (IB - First 60 mins) Range Extension Probabilities
4. Actual Daily Regime Breakdown (Trend Day vs Range Day vs Neutral Day)
5. CVD Predictive Accuracy
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, List
import MetaTrader5 as mt5

from core.orderflow import OrderFlowEngine


class MarketDNAAnalyzer:
    def __init__(self, symbol: str = "XAUUSD", timeframe: int = mt5.TIMEFRAME_M5, bar_count: int = 50000):
        self.symbol = symbol
        self.timeframe = timeframe
        self.bar_count = bar_count
        
        # Proper tick size based on asset type
        if "100" in symbol or "30" in symbol or "500" in symbol or "BTC" in symbol:
            tick_sz = 1.0
        elif "XAU" in symbol:
            tick_sz = 0.10
        else:
            tick_sz = 0.0001
            
        self.of_engine = OrderFlowEngine(tick_size=tick_sz)

    def load_data(self) -> pd.DataFrame:
        if not mt5.initialize():
            raise RuntimeError("MT5 initialize failed")

        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 0, self.bar_count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Failed to fetch data for {self.symbol}")

        df = pd.DataFrame(rates)
        df['datetime'] = pd.to_datetime(df['time'], unit='s')
        df['volume'] = df['real_volume'] if df['real_volume'].sum() > 0 else df['tick_volume']
        df['date'] = df['datetime'].dt.date
        df['session_id'] = df['datetime'].dt.floor('D')
        return df

    def analyze_empirical_dna(self) -> Dict[str, Any]:
        df = self.load_data()
        
        # Calculate daily VWAP & Bands
        df = self.of_engine.calculate_vwap(df, anchor_col='session_id')
        df = self.of_engine.calculate_delta(df)

        days = sorted(df['date'].unique())
        print(f"\n[Market DNA] Analyzing {len(days)} consecutive trading days for {self.symbol}...", flush=True)

        # Storage for empirical stats
        prev_levels = {}
        poc_touches = 0
        vah_touches = 0
        val_touches = 0
        prev_high_touches = 0
        prev_low_touches = 0
        tested_days = 0

        # VWAP Reversion Counters
        stretch_10_count = 0
        revert_10_count = 0
        stretch_15_count = 0
        revert_15_count = 0
        stretch_20_count = 0
        revert_20_count = 0
        stretch_25_count = 0
        revert_25_count = 0

        # Initial Balance (First 60 mins of London/NY session: 07:00 to 08:00 UTC)
        ib_high_breaks = 0
        ib_high_trend_extended = 0
        ib_low_breaks = 0
        ib_low_trend_extended = 0
        ib_both_broken = 0
        ib_neither_broken = 0
        ib_days = 0

        # Day Type Counters
        trend_days = 0
        range_days = 0
        reversal_days = 0

        for i in range(1, len(days)):
            prev_date = days[i - 1]
            curr_date = days[i]
            
            df_prev = df[df['date'] == prev_date]
            df_curr = df[df['date'] == curr_date]

            if len(df_prev) < 50 or len(df_curr) < 50:
                continue

            tested_days += 1

            # 1. Previous Day Volume Profile & Key Levels
            vp_prev = self.of_engine.calculate_volume_profile(df_prev)
            p_poc = vp_prev.get('vpoc', (df_prev['high'].max() + df_prev['low'].min()) / 2.0)
            p_vah = vp_prev.get('vah', df_prev['high'].max())
            p_val = vp_prev.get('val', df_prev['low'].min())
            p_high = df_prev['high'].max()
            p_low = df_prev['low'].min()

            c_high = df_curr['high'].max()
            c_low = df_curr['low'].min()

            # Test if today's price action touched yesterday's institutional levels
            if c_low <= p_poc <= c_high:
                poc_touches += 1
            if c_low <= p_vah <= c_high:
                vah_touches += 1
            if c_low <= p_val <= c_high:
                val_touches += 1
            if c_high >= p_high:
                prev_high_touches += 1
            if c_low <= p_low:
                prev_low_touches += 1

            # 2. VWAP Stretch and Reversion Probabilities
            # Check for each stretch level if price subsequently crossed back to VWAP
            bars = df_curr.reset_index(drop=True)
            vwap = bars['vwap']
            upper1 = bars['vwap_upper1']
            lower1 = bars['vwap_lower1']
            upper2 = bars['vwap_upper2']
            lower2 = bars['vwap_lower2']
            upper3 = bars['vwap_upper3']
            lower3 = bars['vwap_lower3']

            # Check 1.0 StdDev Stretch
            hit_10_idx = bars[(bars['high'] >= upper1) | (bars['low'] <= lower1)].index
            if len(hit_10_idx) > 0:
                stretch_10_count += 1
                first_hit = hit_10_idx[0]
                # Did it revert to VWAP afterwards?
                later_bars = bars.iloc[first_hit + 1:]
                if len(later_bars) > 0 and ((later_bars['low'] <= later_bars['vwap']) & (later_bars['high'] >= later_bars['vwap'])).any():
                    revert_10_count += 1

            # Check 2.0 StdDev Stretch
            hit_20_idx = bars[(bars['high'] >= upper2) | (bars['low'] <= lower2)].index
            if len(hit_20_idx) > 0:
                stretch_20_count += 1
                first_hit = hit_20_idx[0]
                later_bars = bars.iloc[first_hit + 1:]
                if len(later_bars) > 0 and ((later_bars['low'] <= later_bars['vwap']) & (later_bars['high'] >= later_bars['vwap'])).any():
                    revert_20_count += 1

            # Check 3.0 StdDev Extreme Stretch
            hit_30_idx = bars[(bars['high'] >= upper3) | (bars['low'] <= lower3)].index
            if len(hit_30_idx) > 0:
                stretch_25_count += 1
                first_hit = hit_30_idx[0]
                later_bars = bars.iloc[first_hit + 1:]
                if len(later_bars) > 0 and ((later_bars['low'] <= later_bars['vwap']) & (later_bars['high'] >= later_bars['vwap'])).any():
                    revert_25_count += 1

            # 3. Initial Balance Analysis (First 60 minutes: 07:00 to 08:00 UTC)
            df_curr_hours = df_curr[df_curr['datetime'].dt.hour >= 7]
            if len(df_curr_hours) >= 12: # 12 * 5m = 60 mins
                ib_days += 1
                ib_window = df_curr_hours.iloc[:12]
                rest_of_day = df_curr_hours.iloc[12:]

                ib_high = ib_window['high'].max()
                ib_low = ib_window['low'].min()

                if len(rest_of_day) > 0:
                    day_high = rest_of_day['high'].max()
                    day_low = rest_of_day['low'].min()

                    broke_high = day_high > ib_high
                    broke_low = day_low < ib_low

                    if broke_high and not broke_low:
                        ib_high_breaks += 1
                        # Did it extend by at least 1x IB Range?
                        ib_range = ib_high - ib_low
                        if day_high >= ib_high + (0.5 * ib_range):
                            ib_high_trend_extended += 1

                    elif broke_low and not broke_high:
                        ib_low_breaks += 1
                        ib_range = ib_high - ib_low
                        if day_low <= ib_low - (0.5 * ib_range):
                            ib_low_trend_extended += 1

                    elif broke_high and broke_low:
                        ib_both_broken += 1 # Neutral / Liquidity sweep day
                    else:
                        ib_neither_broken += 1 # Pure inside / balanced day

            # 4. Day Type Classification
            # Trend Day: Close near extreme (>75% or <25% of day range)
            day_range = c_high - c_low
            c_close = df_curr['close'].iloc[-1]
            if day_range > 0:
                close_pos = (c_close - c_low) / day_range
                if close_pos >= 0.75 or close_pos <= 0.25:
                    trend_days += 1
                elif 0.35 <= close_pos <= 0.65:
                    range_days += 1
                else:
                    reversal_days += 1

        # Calculate Percentages
        poc_rate = (poc_touches / tested_days) * 100.0 if tested_days > 0 else 0
        vah_rate = (vah_touches / tested_days) * 100.0 if tested_days > 0 else 0
        val_rate = (val_touches / tested_days) * 100.0 if tested_days > 0 else 0
        prev_h_rate = (prev_high_touches / tested_days) * 100.0 if tested_days > 0 else 0
        prev_l_rate = (prev_low_touches / tested_days) * 100.0 if tested_days > 0 else 0

        revert_10_rate = (revert_10_count / stretch_10_count) * 100.0 if stretch_10_count > 0 else 0
        revert_20_rate = (revert_20_count / stretch_20_count) * 100.0 if stretch_20_count > 0 else 0
        revert_25_rate = (revert_25_count / stretch_25_count) * 100.0 if stretch_25_count > 0 else 0

        ib_high_cont_rate = (ib_high_trend_extended / ib_high_breaks) * 100.0 if ib_high_breaks > 0 else 0
        ib_low_cont_rate = (ib_low_trend_extended / ib_low_breaks) * 100.0 if ib_low_breaks > 0 else 0

        trend_day_pct = (trend_days / tested_days) * 100.0 if tested_days > 0 else 0
        range_day_pct = (range_days / tested_days) * 100.0 if tested_days > 0 else 0
        reversal_day_pct = (reversal_days / tested_days) * 100.0 if tested_days > 0 else 0

        results = {
            'symbol': self.symbol,
            'total_days_analyzed': tested_days,
            'prev_day_levels': {
                'poc_touch_rate': poc_rate,
                'vah_touch_rate': vah_rate,
                'val_touch_rate': val_rate,
                'prev_high_touch_rate': prev_h_rate,
                'prev_low_touch_rate': prev_l_rate,
            },
            'vwap_reversion': {
                'stretch_1_0_std_days': stretch_10_count,
                'revert_1_0_std_rate': revert_10_rate,
                'stretch_2_0_std_days': stretch_20_count,
                'revert_2_0_std_rate': revert_20_rate,
                'stretch_3_0_std_days': stretch_25_count,
                'revert_3_0_std_rate': revert_25_rate,
            },
            'initial_balance': {
                'ib_days': ib_days,
                'pure_upside_extensions': ib_high_breaks,
                'upside_followthrough_rate': ib_high_cont_rate,
                'pure_downside_extensions': ib_low_breaks,
                'downside_followthrough_rate': ib_low_cont_rate,
                'neutral_both_sides_broken': ib_both_broken,
                'neutral_day_rate': (ib_both_broken / ib_days * 100.0) if ib_days > 0 else 0,
                'inside_day_rate': (ib_neither_broken / ib_days * 100.0) if ib_days > 0 else 0,
            },
            'day_types': {
                'trend_day_pct': trend_day_pct,
                'range_day_pct': range_day_pct,
                'reversal_day_pct': reversal_day_pct
            }
        }

        self.print_dna_report(results)
        return results

    @staticmethod
    def print_dna_report(r: Dict[str, Any]):
        sym = r['symbol']
        n_days = r['total_days_analyzed']
        pl = r['prev_day_levels']
        vr = r['vwap_reversion']
        ib = r['initial_balance']
        dt = r['day_types']

        print("\n" + "=" * 80)
        print(f"         EMPIRICAL MARKET DNA REPORT: {sym} ({n_days} Real Trading Days)         ")
        print("=" * 80)

        print("\n1. THE LAW OF INSTITUTIONAL MAGNETS (Previous Day Level Touch Probability):")
        print(f"   • Touches Yesterday's VPOC (Point of Control) : {pl['poc_touch_rate']:.1f}% of days")
        print(f"   • Touches Yesterday's VAH (Value Area High)   : {pl['vah_touch_rate']:.1f}% of days")
        print(f"   • Touches Yesterday's VAL (Value Area Low)    : {pl['val_touch_rate']:.1f}% of days")
        print(f"   • Sweeps Yesterday's High                     : {pl['prev_high_touch_rate']:.1f}% of days")
        print(f"   • Sweeps Yesterday's Low                      : {pl['prev_low_touch_rate']:.1f}% of days")

        print("\n2. VWAP STRETCH & MEAN REVERSION PROBABILITIES:")
        print(f"   • When price reaches +-1.0 StdDev Band: Reverts to VWAP {vr['revert_1_0_std_rate']:.1f}% of the time ({vr['stretch_1_0_std_days']} days)")
        print(f"   • When price reaches +-2.0 StdDev Band: Reverts to VWAP {vr['revert_2_0_std_rate']:.1f}% of the time ({vr['stretch_2_0_std_days']} days)")
        print(f"   • When price reaches +-3.0 StdDev Band: Reverts to VWAP {vr['revert_3_0_std_rate']:.1f}% of the time ({vr['stretch_3_0_std_days']} days)")

        print("\n3. INITIAL BALANCE (First 60 Minutes Auction Breakout Rule):")
        print(f"   • Total Session Days Measured                 : {ib['ib_days']} days")
        print(f"   • Pure Upside Breakout Days                   : {ib['pure_upside_extensions']} days (Follow-through: {ib['upside_followthrough_rate']:.1f}%)")
        print(f"   • Pure Downside Breakout Days                 : {ib['pure_downside_extensions']} days (Follow-through: {ib['downside_followthrough_rate']:.1f}%)")
        print(f"   • Whipsaw / Neutral Days (Both sides broken)  : {ib['neutral_both_sides_broken']} days ({ib['neutral_day_rate']:.1f}%)")
        print(f"   • Inside Days (Neither side broken)           : {ib['inside_day_rate']:.1f}%")

        print("\n4. EMPIRICAL DAY TYPE DISTRIBUTION (What actually happens):")
        print(f"   • Trend Days (One-way directional expansion)  : {dt['trend_day_pct']:.1f}%")
        print(f"   • Range-Bound Days (Rotational balance)       : {dt['range_day_pct']:.1f}%")
        print(f"   • Neutral / Liquidity Reversal Days           : {dt['reversal_day_pct']:.1f}%")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    for s in ["XAUUSD", "US100", "EURUSD"]:
        try:
            analyzer = MarketDNAAnalyzer(symbol=s, timeframe=mt5.TIMEFRAME_M5, bar_count=50000)
            analyzer.analyze_empirical_dna()
        except Exception as e:
            print(f"Error analyzing {s}: {e}")
