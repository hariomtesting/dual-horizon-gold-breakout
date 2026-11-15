"""
Market Regime Detector: Hurst Exponent & Variance Ratio
Determines whether market is Mean-Reverting (H < 0.45), Trending (H > 0.55), or Random Walk (0.45 <= H <= 0.55).
"""

import numpy as np
import pandas as pd


class MarketRegimeDetector:
    """
    Computes statistical properties of price time series:
    - Generalized Hurst Exponent (H) via Rescaled Range (R/S)
    - Lo-MacKinlay Variance Ratio Test
    - Parkinson Volatility Estimator (using High-Low range)
    """

    @staticmethod
    def calculate_hurst_exponent(price_series: np.ndarray, max_lag: int = 40) -> float:
        """
        Calculates Hurst Exponent (H):
        H < 0.5 -> Mean Reverting (Anti-persistent)
        H = 0.5 -> Geometric Brownian Motion (Random Walk)
        H > 0.5 -> Trending / Persistent Momentum
        """
        if len(price_series) < max_lag * 2:
            return 0.5

        # Log returns
        p = np.asarray(price_series, dtype=float)
        lags = range(2, max_lag)
        tau = []

        for lag in lags:
            diff = p[lag:] - p[:-lag]
            std = np.std(diff)
            tau.append(std if std > 1e-8 else 1e-8)

        # Linear regression on log-log scale
        x = np.log(list(lags))
        y = np.log(tau)
        
        # Polyfit slope = Hurst Exponent
        poly = np.polyfit(x, y, 1)
        hurst = float(poly[0])
        return max(0.01, min(0.99, hurst))

    @staticmethod
    def variance_ratio_test(price_series: np.ndarray, k: int = 5) -> float:
        """
        Lo-MacKinlay Variance Ratio (VR):
        VR = Var(r_k) / (k * Var(r_1))
        VR < 1.0 -> Mean Reversion
        VR = 1.0 -> Random Walk
        VR > 1.0 -> Trending
        """
        p = np.asarray(price_series, dtype=float)
        if len(p) < k * 4:
            return 1.0

        r1 = p[1:] - p[:-1]
        rk = p[k:] - p[:-k]

        var1 = np.var(r1, ddof=1)
        vark = np.var(rk, ddof=1)

        if var1 < 1e-10:
            return 1.0

        vr = float(vark / (k * var1))
        return vr

    @staticmethod
    def parkinson_volatility(high: np.ndarray, low: np.ndarray, window: int = 20) -> np.ndarray:
        """
        Parkinson extreme-value volatility estimator:
        Up to 5x more efficient than standard close-to-close deviation.
        sigma_P = sqrt( 1 / (4 * ln(2) * N) * sum( (ln(H/L))^2 ) )
        """
        h = np.asarray(high, dtype=float)
        l = np.asarray(low, dtype=float)
        log_hl = np.log(np.maximum(h, 1e-8) / np.maximum(l, 1e-8)) ** 2
        factor = 1.0 / (4.0 * np.log(2.0))
        
        # Rolling mean of squared log range
        series = pd.Series(log_hl)
        rolling_mean = series.rolling(window=window, min_periods=5).mean().bfill()
        parkinson_vol = np.sqrt(factor * rolling_mean.values)
        return parkinson_vol

