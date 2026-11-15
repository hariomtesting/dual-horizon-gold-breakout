"""
Ornstein-Uhlenbeck (OU) Mean-Reversion Calibration Engine
Calibrates the stochastic differential equation:
    dX_t = theta * (mu - X_t) dt + sigma * dW_t
via Maximum Likelihood / AR(1) discrete exact mapping.
Calculates:
- Mean reversion speed (theta)
- Half-life of deviation (tau_half)
- Long-term equilibrium (mu)
- Scale function s(x) and analytical first-passage probability
"""

import numpy as np
from typing import Dict, Any, Optional


class OrnsteinUhlenbeckCalibrator:
    """
    Fits continuous-time OU process to price deviation series:
    X_t = Price_t - VolumeWeightedFairValue_t
    """

    @staticmethod
    def calibrate(series: np.ndarray, dt: float = 1.0) -> Optional[Dict[str, float]]:
        """
        Calibrates OU parameters theta, mu, sigma, and half_life from series.
        """
        x = np.asarray(series, dtype=float)
        n = len(x)
        if n < 20:
            return None

        x_prev = x[:-1]
        x_curr = x[1:]

        sx = np.sum(x_prev)
        sy = np.sum(x_curr)
        sxx = np.sum(x_prev ** 2)
        sxy = np.sum(x_prev * x_curr)

        denom = (n - 1) * sxx - sx ** 2
        if abs(denom) < 1e-12:
            return None

        b = ((n - 1) * sxy - sx * sy) / denom
        a = (sy - b * sx) / (n - 1)

        # Mean-reverting condition: 0 < b < 1
        if b <= 0.0 or b >= 0.999:
            return None

        theta = -np.log(b) / dt
        mu = a / (1.0 - b)

        residuals = x_curr - (a + b * x_prev)
        sigma_eps = np.std(residuals, ddof=2)
        if sigma_eps < 1e-8:
            return None

        sigma = sigma_eps * np.sqrt(-2.0 * np.log(b) / ((1.0 - b ** 2) * dt))
        asymptotic_std = np.sqrt(sigma_eps ** 2 / (1.0 - b ** 2))
        half_life = np.log(2.0) / theta

        return {
            'b': float(b),
            'a': float(a),
            'theta': float(theta),
            'mu': float(mu),
            'sigma': float(sigma),
            'asymptotic_std': float(asymptotic_std),
            'half_life': float(half_life),
            'is_mean_reverting': True
        }

    @staticmethod
    def compute_z_score(current_val: float, ou_params: Dict[str, float]) -> float:
        """Standardized deviation from OU equilibrium in units of asymptotic sigma"""
        mu = ou_params['mu']
        std = ou_params['asymptotic_std']
        if std <= 1e-8:
            return 0.0
        return float((current_val - mu) / std)

    @staticmethod
    def _scale_function(x: float) -> float:
        """
        Scale function for standard OU process:
        s(x) = int_0^x exp(u^2 / 2) du
        """
        if abs(x) < 1e-8:
            return 0.0
        # 50-point numerical integration
        u = np.linspace(0.0, x, 50)
        # Using trapezoidal rule
        integrand = np.exp(np.clip(u ** 2 / 2.0, 0.0, 30.0))
        return float(np.sum((integrand[:-1] + integrand[1:]) / 2.0 * (u[1:] - u[:-1])))

    @classmethod
    def exit_probability(cls, z_entry: float, z_target: float, z_stop: float) -> float:
        """
        Exact first-passage probability of reaching target before stop
        under OU diffusion using the scale function s(x):
        P(T_target < T_stop | z_entry) = [s(z_entry) - s(z_stop)] / [s(z_target) - s(z_stop)]
        """
        s_entry = cls._scale_function(z_entry)
        s_target = cls._scale_function(z_target)
        s_stop = cls._scale_function(z_stop)

        denom = s_target - s_stop
        if abs(denom) < 1e-8:
            return 0.5

        prob = (s_entry - s_stop) / denom
        return float(np.clip(prob, 0.05, 0.95))
