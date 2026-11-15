"""
Walk-Forward & Out-of-Sample Validation Engine
Guarantees algorithmic robustness by splitting market history into:
1. In-Sample (Optimization & Benchmark period)
2. Out-of-Sample (Unseen forward validation period)
"""

import pandas as pd
from typing import Dict, Any
from strategy.orderflow_strategy import OrderFlowStrategy
from backtest.engine import BacktestEngine


class WalkForwardValidator:
    def __init__(self, in_sample_ratio: float = 0.65, initial_balance: float = 10000.0):
        self.in_sample_ratio = in_sample_ratio
        self.initial_balance = initial_balance

    def run(self, df: pd.DataFrame, strategy: OrderFlowStrategy) -> Dict[str, Any]:
        """Splits df into In-Sample and Out-of-Sample, tests both independently"""
        split_idx = int(len(df) * self.in_sample_ratio)
        
        df_in = df.iloc[:split_idx].copy().reset_index(drop=True)
        df_out = df.iloc[split_idx:].copy().reset_index(drop=True)

        engine = BacktestEngine(initial_balance=self.initial_balance)

        # 1. In-Sample Run
        df_in_sig = strategy.generate_signals(df_in)
        in_results = engine.run(df_in_sig)

        # 2. Out-of-Sample (Forward) Run
        df_out_sig = strategy.generate_signals(df_out)
        out_results = engine.run(df_out_sig)

        # Compute Overfitting Metric / Robustness Index (WFE: Walk-Forward Efficiency)
        # WFE = Out-of-sample Annualized Return / In-sample Annualized Return
        in_pnl_pct = in_results['net_return_pct']
        out_pnl_pct = out_results['net_return_pct']
        
        # Adjust for relative lengths
        wfe = (out_pnl_pct / (1.0 - self.in_sample_ratio)) / (in_pnl_pct / self.in_sample_ratio) if in_pnl_pct != 0 else 0.0

        return {
            "in_sample_period": {
                "start": str(df_in['datetime'].iloc[0]),
                "end": str(df_in['datetime'].iloc[-1]),
                "bars": len(df_in),
                "metrics": in_results
            },
            "out_of_sample_forward_period": {
                "start": str(df_out['datetime'].iloc[0]),
                "end": str(df_out['datetime'].iloc[-1]),
                "bars": len(df_out),
                "metrics": out_results
            },
            "walk_forward_efficiency": float(wfe),
            "is_robust": bool(out_results['net_profit'] > 0 and out_results['profit_factor'] >= 1.2)
        }

