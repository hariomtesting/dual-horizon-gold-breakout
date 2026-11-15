"""
Order Flow & Volume Profile Analytical Engine
Calculates institutional metrics:
- VWAP, Anchored VWAP (AVWAP), TWAP, VWAP StDev Bands
- Volume Profile (VPOC, Value Area 70%, VAH, VAL, HVN, LVN)
- Delta, Cumulative Volume Delta (CVD), Diagonal Imbalances, Block Trades
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple, List, Optional


class OrderFlowEngine:
    def __init__(self, tick_size: float = 0.0001, value_area_pct: float = 0.70):
        self.tick_size = tick_size
        self.value_area_pct = value_area_pct

    # =========================================================================
    # 1. Volume & Price-Weighted Anchors (VWAP, AVWAP, TWAP, Bands)
    # =========================================================================
    @staticmethod
    def calculate_vwap(df: pd.DataFrame, anchor_col: Optional[str] = 'session_id') -> pd.DataFrame:
        """
        Calculates VWAP and Standard Deviation Bands (+-1, +-2, +-3 std).
        Resets at each new session if anchor_col is provided.
        """
        df = df.copy()
        tp = (df['high'] + df['low'] + df['close']) / 3.0
        vol = df['volume'] if ('volume' in df and df['volume'].sum() > 0) else df['tick_volume']
        vol = vol.replace(0, 1.0) # Prevent zero volume division
        
        pv = tp * vol

        if anchor_col and anchor_col in df.columns:
            cum_vol = df.groupby(anchor_col)[vol.name].cumsum()
            df['_pv'] = pv
            cum_pv = df.groupby(anchor_col)['_pv'].cumsum()
            vwap = cum_pv / cum_vol.replace(0, np.nan)
            
            df['_vwap'] = vwap
            df['_sq_diff'] = ((tp - vwap) ** 2) * vol
            cum_sq = df.groupby(anchor_col)['_sq_diff'].cumsum()
            variance = cum_sq / cum_vol.replace(0, np.nan)
            
            df.drop(columns=['_pv', '_vwap', '_sq_diff'], inplace=True, errors='ignore')
        else:
            cum_vol = vol.cumsum()
            cum_pv = pv.cumsum()
            vwap = cum_pv / cum_vol.replace(0, np.nan)
            variance = (((tp - vwap) ** 2) * vol).cumsum() / cum_vol.replace(0, np.nan)

        stdev = np.sqrt(np.maximum(variance.fillna(0), 0))
        
        df['vwap'] = vwap
        df['vwap_upper1'] = vwap + stdev
        df['vwap_lower1'] = vwap - stdev
        df['vwap_upper2'] = vwap + 2.0 * stdev
        df['vwap_lower2'] = vwap - 2.0 * stdev
        df['vwap_upper3'] = vwap + 3.0 * stdev
        df['vwap_lower3'] = vwap - 3.0 * stdev
        
        return df

    @staticmethod
    def calculate_avwap(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
        """Anchored VWAP starting from a specific bar index"""
        sub_df = df.iloc[anchor_idx:].copy()
        tp = (sub_df['high'] + sub_df['low'] + sub_df['close']) / 3.0
        vol = sub_df['volume'] if 'volume' in sub_df and sub_df['volume'].sum() > 0 else sub_df['tick_volume']
        avwap = (tp * vol).cumsum() / vol.cumsum().replace(0, np.nan)
        
        full_avwap = pd.Series(index=df.index, dtype=float)
        full_avwap.iloc[anchor_idx:] = avwap
        return full_avwap

    @staticmethod
    def calculate_twap(df: pd.DataFrame, window: int = 20) -> pd.Series:
        """Time-Weighted Average Price over rolling window"""
        tp = (df['high'] + df['low'] + df['close']) / 3.0
        return tp.rolling(window=window).mean()

    # =========================================================================
    # 2. Transaction & Delta Trackers (Delta, CVD, Imbalance, Block Trades)
    # =========================================================================
    @staticmethod
    def calculate_delta(df: pd.DataFrame) -> pd.DataFrame:
        """Calculates Delta and Cumulative Volume Delta (CVD)"""
        df = df.copy()
        vol = df['volume'] if ('volume' in df and df['volume'].sum() > 0) else df['tick_volume']
        vol = vol.replace(0, 1.0) # Prevent zero volume division
        
        if 'buy_volume' in df.columns and 'sell_volume' in df.columns:
            df['delta'] = df['buy_volume'] - df['sell_volume']
        else:
            candle_range = (df['high'] - df['low']).replace(0, 1e-9)
            buyer_ratio = (df['close'] - df['low']) / candle_range
            seller_ratio = (df['high'] - df['close']) / candle_range
            direction_factor = np.clip((buyer_ratio - seller_ratio), -1.0, 1.0)
            df['delta'] = direction_factor * vol
            
        df['cvd'] = df['delta'].cumsum()
        return df

    @staticmethod
    def detect_block_trades(df: pd.DataFrame, threshold_std: float = 3.0) -> pd.Series:
        """Identifies large institutional block orders (volume > mean + N * std)"""
        vol = df['volume'] if ('volume' in df and df['volume'].sum() > 0) else df['tick_volume']
        vol = vol.replace(0, 1.0) # Prevent zero volume division
        mean_vol = vol.rolling(50, min_periods=10).mean()
        std_vol = vol.rolling(50, min_periods=10).std()
        return vol > (mean_vol + threshold_std * std_vol)

    # =========================================================================
    # 3. Volume Profile & Liquidity Indicators (VPOC, Value Area, HVN, LVN)
    # =========================================================================
    def calculate_volume_profile(self, df_window: pd.DataFrame) -> Dict[str, Any]:
        """Calculates discrete Volume Profile across a price window"""
        if len(df_window) == 0:
            return {}

        min_price = float(df_window['low'].min())
        max_price = float(df_window['high'].max())
        
        if max_price <= min_price:
            return {'vpoc': min_price, 'vah': min_price, 'val': min_price, 'hvn': [], 'lvn': []}

        bins = np.arange(min_price, max_price + self.tick_size, self.tick_size)
        if len(bins) < 2:
            bins = np.linspace(min_price, max_price, 50)
            
        vol_profile = np.zeros(len(bins))
        vol_col = 'volume' if 'volume' in df_window and df_window['volume'].sum() > 0 else 'tick_volume'
        
        for _, row in df_window.iterrows():
            c_low, c_high, c_vol = row['low'], row['high'], row[vol_col]
            if c_vol <= 0:
                continue
            idx_start = np.searchsorted(bins, c_low)
            idx_end = np.searchsorted(bins, c_high, side='right')
            span = max(idx_end - idx_start, 1)
            vol_profile[idx_start:idx_end] += (c_vol / span)

        total_vol = vol_profile.sum()
        if total_vol == 0:
            return {'vpoc': min_price, 'vah': min_price, 'val': min_price, 'hvn': [], 'lvn': []}

        poc_idx = int(np.argmax(vol_profile))
        vpoc = bins[poc_idx]

        target_vol = total_vol * self.value_area_pct
        accum_vol = vol_profile[poc_idx]
        up_idx = poc_idx
        down_idx = poc_idx

        while accum_vol < target_vol and (up_idx < len(vol_profile) - 1 or down_idx > 0):
            next_up = vol_profile[up_idx + 1] if up_idx < len(vol_profile) - 1 else 0
            next_down = vol_profile[down_idx - 1] if down_idx > 0 else 0

            if next_up >= next_down and up_idx < len(vol_profile) - 1:
                up_idx += 1
                accum_vol += next_up
            elif down_idx > 0:
                down_idx -= 1
                accum_vol += next_down
            else:
                break

        val = bins[down_idx]
        vah = bins[up_idx]

        hvn_levels = []
        lvn_levels = []
        window = max(2, len(bins) // 25)

        for i in range(window, len(vol_profile) - window):
            local_window = vol_profile[i - window : i + window + 1]
            if vol_profile[i] == np.max(local_window) and vol_profile[i] > (total_vol / len(bins)) * 1.5:
                hvn_levels.append(float(bins[i]))
            elif vol_profile[i] == np.min(local_window) and vol_profile[i] < (total_vol / len(bins)) * 0.5:
                lvn_levels.append(float(bins[i]))

        return {
            'vpoc': float(vpoc),
            'vah': float(vah),
            'val': float(val),
            'hvn': hvn_levels[:5],
            'lvn': lvn_levels[:5],
            'total_volume': float(total_vol)
        }
