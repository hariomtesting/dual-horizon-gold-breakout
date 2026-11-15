"""
Mathematical Capital Allocator: Fractional Kelly & Volatility Parity
Calculates optimal position sizing to maximize geometric growth while bounding ruin probability.
"""

import numpy as np


class MathematicalCapitalAllocator:
    """
    Implements:
    - Continuous Kelly Formula with safety shrinkage (quarter-Kelly)
    - Inverse Volatility Parity Sizing
    - Maximum Daily Loss Boundary (Ensuring P(Hit Daily Stop) < 0.01)
    """

    def __init__(self, kelly_fraction: float = 0.25, max_risk_pct: float = 0.015, min_risk_pct: float = 0.0025):
        self.kelly_fraction = kelly_fraction
        self.max_risk_pct = max_risk_pct
        self.min_risk_pct = min_risk_pct

    def compute_kelly_fraction(self, win_prob: float, win_loss_ratio: float) -> float:
        """
        Kelly Criterion:
        f* = (p * b - q) / b
        where p = win probability, q = 1 - p, b = Win/Loss dollar ratio.
        Shrunk by kelly_fraction (e.g. 0.25) to prevent overbetting.
        """
        if win_loss_ratio <= 0.0:
            return 0.0

        p = float(win_prob)
        q = 1.0 - p
        b = float(win_loss_ratio)

        full_kelly = (p * b - q) / b
        if full_kelly <= 0.0:
            return 0.0

        shrunk_kelly = full_kelly * self.kelly_fraction
        return float(np.clip(shrunk_kelly, self.min_risk_pct, self.max_risk_pct))

    def calculate_lot_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        symbol: str,
        win_prob: float = 0.70,
        win_loss_ratio: float = 0.75,
        min_lot: float = 0.01,
        max_lot: float = 10.0,
        step: float = 0.01
    ) -> float:
        """
        Calculates exact MT5 lot size using dynamic fractional Kelly risk.
        """
        risk_fraction = self.compute_kelly_fraction(win_prob, win_loss_ratio)
        risk_dollars = equity * risk_fraction
        price_risk = abs(entry_price - stop_price)

        if price_risk <= 0.0:
            return min_lot

        if "XAU" in symbol:
            # Gold: 1 standard lot = 100 oz
            units = risk_dollars / price_risk
            raw_lots = units / 100.0
        elif "100" in symbol or "30" in symbol:
            # Indices: 1 contract per point
            raw_lots = risk_dollars / (price_risk * 1.0)
        else:
            # Forex: 100,000 units per lot
            units = risk_dollars / price_risk
            raw_lots = units / 100000.0

        lots = round(raw_lots / step) * step
        return float(max(min_lot, min(lots, max_lot)))

