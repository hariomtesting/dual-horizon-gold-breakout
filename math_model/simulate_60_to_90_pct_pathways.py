"""
MATHEMATICAL SIMULATION: 60% TO 90% MONTHLY RETURN PATHWAYS
============================================================
Investigates the exact mathematical mechanisms to achieve 60% - 90% monthly ROI:
1. Pathway 1: High-Turnover Multi-Asset Scalping (Law of Large Numbers)
2. Pathway 2: Asymmetric Convex Pyramiding (Unrealized Profit Scaling)
3. Pathway 3: Dynamic Kelly Sizing on A+ Order Flow Confluences
Evaluates drawdown, probability of ruin, and prop firm limit compliance (< 4.5% daily DD).
"""

import numpy as np
import pandas as pd


def simulate_pathway_1_high_turnover():
    """
    Pathway 1: Multi-Asset High-Turnover Scalping
    - 4 assets (DAX, Gold, Nasdaq, EURUSD)
    - 2.5 trades per asset per day = 10 trades per day = 200 trades / month
    - Win rate = 42%, R:R = 1.8:1
    - Risk per trade = 1.0% (Worst day 3 consecutive losses = -3.0%, passes prop firm)
    """
    np.random.seed(42)
    n_days = 20
    trades_per_day = 10
    total_trades = n_days * trades_per_day
    
    wr = 0.42
    rr = 1.8
    risk_pct = 0.010 # 1.0%
    
    monthly_rois = []
    max_daily_losses = []
    
    for sim in range(1000):
        cap = 10000.0
        start_cap = cap
        daily_losses = []
        
        for d in range(n_days):
            day_start = cap
            # 10 trades today
            outcomes = np.random.rand(trades_per_day) < wr
            for win in outcomes:
                if win:
                    cap += cap * (risk_pct * rr)
                else:
                    cap -= cap * risk_pct
            day_pnl = (cap - day_start) / day_start
            daily_losses.append(day_pnl)
            
        roi = (cap - start_cap) / start_cap * 100.0
        monthly_rois.append(roi)
        max_daily_losses.append(min(daily_losses) * 100.0)
        
    return {
        'name': 'High-Turnover Multi-Asset (10 trades/day, 1.8 R:R, 1.0% risk)',
        'avg_roi': np.mean(monthly_rois),
        'median_roi': np.median(monthly_rois),
        'p25_roi': np.percentile(monthly_rois, 25),
        'p75_roi': np.percentile(monthly_rois, 75),
        'worst_day': np.mean(max_daily_losses),
        'worst_day_ever': np.min(max_daily_losses),
        'prob_over_60': (np.array(monthly_rois) >= 60.0).mean() * 100.0
    }


def simulate_pathway_2_convex_pyramiding():
    """
    Pathway 2: Convex Pyramiding (Anti-Martingale Scaling)
    - 20 trading days
    - 2-3 trades per day
    - On 70% of days (chop): trades hit normal SL (-1.25%) or TP (+2.25%)
    - On 30% of days (institutional trend expansion):
      * Base trade hits +1.0 ATR -> lock to BE (+0.1R)
      * Pyramid Unit 2 added (+1.5R)
      * Pyramid Unit 3 added (+2.0R)
      * Total payoff on runner = +4.5R to +6.5R (+5.5% to +8.0% in one day)
    """
    np.random.seed(42)
    n_days = 20
    monthly_rois = []
    max_daily_losses = []
    
    for sim in range(1000):
        cap = 10000.0
        start_cap = cap
        daily_losses = []
        
        for d in range(n_days):
            day_start = cap
            is_trend_day = (np.random.rand() < 0.35) # 35% of days trend strongly
            
            if is_trend_day:
                # 65% win probability on trend day when aligned with London Open Drive
                won = np.random.rand() < 0.65
                if won:
                    # Runner with 3 pyramid units: +5.5R gain
                    pnl_pct = 0.0125 * 5.5 # +6.87% day
                else:
                    # Stopped out at initial SL before pyramid
                    pnl_pct = -0.0125
            else:
                # Normal 2 chop trades
                t1_win = np.random.rand() < 0.40
                t2_win = np.random.rand() < 0.40
                p1 = (0.0125 * 1.8) if t1_win else -0.0125
                p2 = (0.0125 * 1.8) if t2_win else -0.0125
                pnl_pct = p1 + p2
                
            cap += cap * pnl_pct
            daily_losses.append(pnl_pct)
            
        roi = (cap - start_cap) / start_cap * 100.0
        monthly_rois.append(roi)
        max_daily_losses.append(min(daily_losses) * 100.0)
        
    return {
        'name': 'Convex Pyramiding (Runners scale to +5.5R, 1.25% initial risk)',
        'avg_roi': np.mean(monthly_rois),
        'median_roi': np.median(monthly_rois),
        'p25_roi': np.percentile(monthly_rois, 25),
        'p75_roi': np.percentile(monthly_rois, 75),
        'worst_day': np.mean(max_daily_losses),
        'worst_day_ever': np.min(max_daily_losses),
        'prob_over_60': (np.array(monthly_rois) >= 60.0).mean() * 100.0
    }


def simulate_pathway_3_dynamic_kelly():
    """
    Pathway 3: Confluence-Weighted Dynamic Sizing
    - Base setups (B Grade): 0.75% risk, 40% WR, 1.8 R:R (1.5 trades/day)
    - A+ Grade Setups (Sweep + Delta Exhaustion + Session Key Level):
      * 2.5% risk, 68% WR, 1.6 R:R (0.8 trades/day = ~16 trades/month)
    """
    np.random.seed(42)
    n_days = 20
    monthly_rois = []
    max_daily_losses = []
    
    for sim in range(1000):
        cap = 10000.0
        start_cap = cap
        daily_losses = []
        
        for d in range(n_days):
            day_start = cap
            # A+ setup today?
            has_a_plus = np.random.rand() < 0.75
            pnl_day = 0.0
            
            if has_a_plus:
                a_win = np.random.rand() < 0.68
                pnl_day += (0.025 * 1.6) if a_win else -0.025
                
            # 1 B-grade trade
            b_win = np.random.rand() < 0.40
            pnl_day += (0.008 * 1.8) if b_win else -0.008
            
            cap += cap * pnl_day
            daily_losses.append(pnl_day)
            
        roi = (cap - start_cap) / start_cap * 100.0
        monthly_rois.append(roi)
        max_daily_losses.append(min(daily_losses) * 100.0)
        
    return {
        'name': 'Dynamic Sizing on A+ Confluences (2.5% on A+, 0.8% on B)',
        'avg_roi': np.mean(monthly_rois),
        'median_roi': np.median(monthly_rois),
        'p25_roi': np.percentile(monthly_rois, 25),
        'p75_roi': np.percentile(monthly_rois, 75),
        'worst_day': np.mean(max_daily_losses),
        'worst_day_ever': np.min(max_daily_losses),
        'prob_over_60': (np.array(monthly_rois) >= 60.0).mean() * 100.0
    }


if __name__ == "__main__":
    p1 = simulate_pathway_1_high_turnover()
    p2 = simulate_pathway_2_convex_pyramiding()
    p3 = simulate_pathway_3_dynamic_kelly()
    
    print("=" * 95)
    print("           MONTE CARLO SIMULATION: HOW TO ACHIEVE 60% TO 90% RETURN A MONTH           ")
    print("=" * 95 + "\n")
    
    for p in [p1, p2, p3]:
        print(f"--- {p['name']} ---")
        print(f"Average Monthly Return (ROI)   : {p['avg_roi']:+6.2f}% (Median: {p['median_roi']:+6.2f}%)")
        print(f"Interquartile Range (25% - 75%): {p['p25_roi']:+6.1f}% to {p['p75_roi']:+6.1f}%")
        print(f"Probability of ROI >= +60.0%   : {p['prob_over_60']:5.1f}%")
        print(f"Average Worst Single Day Loss  : {p['worst_day']:6.2f}% (Worst Case Ever: {p['worst_day_ever']:6.2f}%)\n")

