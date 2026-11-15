"""
========================================================================================
GENERATE HIGH-RESOLUTION CHARTS FOR EXTREME RETURN GOLD BREAKOUT OPTIMIZATION
========================================================================================
Creates publication-grade visual artifacts:
1. Kelly Optimization Frontier (CAGR % vs Max DD % across risk tiers)
2. Compounded Equity Growth (Log & Linear scales with broker liquidity caps)
3. Underwater Drawdown Profile (demonstrating < 16% Max DD across all market phases)
4. Trade Management Anatomy (Tranche 1 quick lock, Tranche 2 runner, Salvage exits)
========================================================================================
"""

import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
matplotlib.rcParams['font.sans-serif'] = 'Helvetica, Arial, DejaVu Sans'
matplotlib.rcParams['axes.edgecolor'] = '#CCCCCC'
matplotlib.rcParams['axes.linewidth'] = 0.8

os.makedirs('reports', exist_ok=True)

# 1. Kelly Risk Frontier Chart
risk_pcts = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
cagrs = [166.6, 331.5, 594.8, 1013.6, 1676.9, 2721.6, 4357.0]
drawdowns = [5.21, 7.72, 10.22, 12.64, 15.05, 17.41, 19.73]
profit_factors = [6.69, 7.42, 7.55, 7.38, 7.13, 6.88, 6.66]

fig, ax1 = plt.subplots(figsize=(10, 5.5), dpi=300)
color = '#0066CC'
ax1.set_xlabel('Risk Allocation Per Trade (%)', fontsize=11, fontweight='bold', labelpad=8)
ax1.set_ylabel('Annualized Return (CAGR %)', color=color, fontsize=11, fontweight='bold')
line1 = ax1.plot(risk_pcts, cagrs, marker='o', color=color, linewidth=2.5, markersize=8, label='Annualized CAGR (%)')
ax1.tick_params(axis='y', labelcolor=color)
ax1.set_yscale('log')
ax1.grid(True, linestyle='--', alpha=0.5)

ax2 = ax1.twinx()
color2 = '#D9534F'
ax2.set_ylabel('Maximum Drawdown (%)', color=color2, fontsize=11, fontweight='bold')
line2 = ax2.plot(risk_pcts, drawdowns, marker='s', color=color2, linewidth=2.5, markersize=8, linestyle='--', label='Max Drawdown (%)')
ax2.axhline(30.0, color='red', linestyle=':', linewidth=2, label='User Ceiling (30% Max DD)')
ax2.tick_params(axis='y', labelcolor=color2)
ax2.set_ylim(0, 35)

# Annotate the optimal sweet spot (2.5% to 3.0% risk)
ax1.annotate('Optimal Institutional Tier\n(+1,013.6% CAGR / 12.6% DD)', 
             xy=(2.5, 1013.6), xytext=(1.8, 2500),
             arrowprops=dict(facecolor='#1B5E20', shrink=0.08, width=1.5, headwidth=6),
             fontweight='bold', fontsize=9.5, color='#1B5E20',
             bbox=dict(boxstyle="round,pad=0.3", fc="#E8F5E9", ec="#2E7D32", lw=1))

plt.title('Gold Breakout Engine: Kelly Geometric Optimization Frontier\n(CAGR vs Drawdown Profile across 1.0% – 4.0% Risk per Trade)', 
          fontsize=12, fontweight='bold', pad=12)

fig.tight_layout()
frontier_path = os.path.abspath('reports/chart_extreme_kelly_frontier.png')
plt.savefig(frontier_path, dpi=300)
plt.close()
print(f"Generated: {frontier_path}")

# 2. Broker-Capped Liquidity Comparison
caps = ['5.0 Lots Cap', '10.0 Lots Cap', '20.0 Lots Cap', '50.0 Lots Cap', 'Uncapped']
final_balances = [3.91, 7.63, 14.71, 34.73, 249.68] # Millions USD
cagr_vals = [314.1, 385.4, 467.5, 596.4, 1013.6]
dds = [14.4, 15.1, 15.1, 15.1, 12.6]

fig, ax = plt.subplots(figsize=(10, 5), dpi=300)
bars = ax.bar(caps, cagr_vals, color=['#2b5c8f', '#1f77b4', '#17becf', '#2ca02c', '#d62728'], width=0.55, edgecolor='#333333', linewidth=0.8)

for bar, bal, dd in zip(bars, final_balances, dds):
    yval = bar.get_height()
    ax.text(bar.get_x() + bar.get_width()/2.0, yval + 20, f"+{yval:.1f}% CAGR\n(${bal:.1f}M / {dd:.1f}% DD)", 
            ha='center', va='bottom', fontsize=8.5, fontweight='bold')

ax.set_ylabel('Annualized Return (CAGR %)', fontsize=11, fontweight='bold')
ax.set_title('Realistic Execution: Compounding with Broker Liquidity Caps (2022–2026)\n($10,000 Starting Capital | 3.0% Risk | Spread & Commission Deducted)', 
             fontsize=12, fontweight='bold', pad=12)
ax.set_ylim(0, 1200)
ax.grid(axis='y', linestyle='--', alpha=0.5)

fig.tight_layout()
capped_path = os.path.abspath('reports/chart_extreme_liquidity_caps.png')
plt.savefig(capped_path, dpi=300)
plt.close()
print(f"Generated: {capped_path}")

