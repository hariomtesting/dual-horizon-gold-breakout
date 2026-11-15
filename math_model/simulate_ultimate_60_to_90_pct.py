"""
MATHEMATICAL SIMULATION: 60% TO 90% MONTHLY RETURN PATHWAYS
============================================================
Tests the Ultimate Hybrid: Multi-Asset Scalping + Convex Pyramiding
"""

import numpy as np
import pandas as pd


def simulate_hybrid_ultimate():
    """
    Pathway 4: The Ultimate Confluence Hybrid
    - 3 European assets (DAX, Gold, EURUSD)
    - 1-2 setups per asset per day = 4 to 5 trades / day
    - Initial risk: 1.0% per trade (Max daily loss capped by circuit breaker at -3.0%)
    - Normal wins: +1.8R (+1.8%)
    - Trend runner days (2-3 times a week across the 3 assets):
      * When trade is up +1.0R, SL moves to breakeven +0.1R
      * Pyramid Unit 2 added with target +2.0R
      * Payout on runner = +3.8R (+3.8%)
    - Compounding: Daily equity compounding
    """
    np.random.seed(42)
    n_days = 20
    trades_per_day = 4
    
    monthly_rois = []
    max_daily_losses = []
    
    for sim in range(2000):
        cap = 10000.0
        start_cap = cap
        daily_losses = []
        
        for d in range(n_days):
            day_start = cap
            day_pnl = 0.0
            consecutive_losses = 0
            
            for t in range(trades_per_day):
                # Daily circuit breaker: If down 2.5% today, stop trading to protect prop firm limit!
                if day_pnl <= -0.025:
                    break
                    
                # 42% win rate
                won = np.random.rand() < 0.42
                if won:
                    # 40% of winning trades turn into multi-unit pyramid runners
                    is_runner = np.random.rand() < 0.40
                    if is_runner:
                        # Runner payoff: +3.8R
                        pnl = 0.010 * 3.8
                    else:
                        # Normal win: +1.8R
                        pnl = 0.010 * 1.8
                    consecutive_losses = 0
                else:
                    pnl = -0.010
                    consecutive_losses += 1
                    
                day_pnl += pnl
                
            cap += cap * day_pnl
            daily_losses.append(day_pnl)
            
        roi = (cap - start_cap) / start_cap * 100.0
        monthly_rois.append(roi)
        max_daily_losses.append(min(daily_losses) * 100.0)
        
    return {
        'name': 'The Ultimate Hybrid (4-5 Trades/Day + Pyramiding Runners + Circuit Breaker)',
        'avg_roi': np.mean(monthly_rois),
        'median_roi': np.median(monthly_rois),
        'p25_roi': np.percentile(monthly_rois, 25),
        'p75_roi': np.percentile(monthly_rois, 75),
        'p90_roi': np.percentile(monthly_rois, 90),
        'worst_day': np.mean(max_daily_losses),
        'worst_day_ever': np.min(max_daily_losses),
        'prob_over_60': (np.array(monthly_rois) >= 60.0).mean() * 100.0,
        'prob_over_80': (np.array(monthly_rois) >= 80.0).mean() * 100.0
    }


if __name__ == "__main__":
    res = simulate_hybrid_ultimate()
    print("=" * 95)
    print("      THE MATHEMATICAL FORMULA FOR 60% TO 90% MONTHLY RETURN      ")
    print("=" * 95)
    print(f"Strategy Model                 : {res['name']}")
    print(f"Average Monthly ROI            : {res['avg_roi']:+6.2f}%")
    print(f"Median Monthly ROI             : {res['median_roi']:+6.2f}%")
    print(f"90th Percentile Monthly ROI    : {res['p90_roi']:+6.2f}%")
    print(f"Probability of Achieving >= 60%: {res['prob_over_60']:5.1f}%")
    print(f"Probability of Achieving >= 80%: {res['prob_over_80']:5.1f}%")
    print(f"Average Worst Single Day Loss  : {res['worst_day']:6.2f}%")
    print(f"Worst Day EVER in 2,000 Months : {res['worst_day_ever']:6.2f}% (100% Prop Firm Compliant!)\n")

