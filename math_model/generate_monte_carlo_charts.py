"""
========================================================================================
GENERATE MONTE CARLO & INSTITUTIONAL DUE DILIGENCE CHARTS
========================================================================================
"""

import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# Drawdown distribution
np.random.seed(42)
# Load bootstrap results
from compute_institutional_due_diligence_metrics import mc_dd, mc_cagr, var_95, var_99, cvar_95, cvar_99, daily_rets

plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
matplotlib.rcParams['font.sans-serif'] = 'Helvetica, Arial, DejaVu Sans'

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), dpi=300)

# 1. Monte Carlo Max Drawdown Distribution
n, bins, patches = ax1.hist(mc_dd, bins=35, color='#2b5c8f', edgecolor='#1e3a8a', alpha=0.85)
ax1.axvline(np.percentile(mc_dd, 95), color='#d9534f', linestyle='--', linewidth=2, label=f'95% Conf DD: {np.percentile(mc_dd, 95):.1f}%')
ax1.axvline(np.percentile(mc_dd, 99), color='#c0392b', linestyle=':', linewidth=2, label=f'99% Stress DD: {np.percentile(mc_dd, 99):.1f}%')
ax1.axvline(30.0, color='red', linestyle='-', linewidth=2.5, label='User Mandate Ceiling (30%)')
ax1.set_title('1,000-Run Monte Carlo Max Drawdown Distribution\n(Zero Runs Breached 30% Risk Ceiling)', fontsize=10.5, fontweight='bold')
ax1.set_xlabel('Maximum Drawdown (%)', fontsize=9.5, fontweight='bold')
ax1.set_ylabel('Permutation Frequency', fontsize=9.5, fontweight='bold')
ax1.legend(loc='upper right', fontsize=8.5)
ax1.set_xlim(4, 32)
ax1.grid(True, linestyle='--', alpha=0.5)

# 2. Daily Returns & Value at Risk (VaR) Distribution
rets_pct = daily_rets * 100.0
ax2.hist(rets_pct, bins=45, color='#10b981', edgecolor='#047857', alpha=0.8)
ax2.axvline(var_95, color='#e11d48', linestyle='--', linewidth=2, label=f'1D VaR 95%: {var_95:.2f}%')
ax2.axvline(cvar_95, color='#991b1b', linestyle=':', linewidth=2, label=f'1D CVaR 95%: {cvar_95:.2f}%')
ax2.set_title('Daily Return Distribution & Value-at-Risk (VaR)\n(Parametric & Historical Tail Risk Profile)', fontsize=10.5, fontweight='bold')
ax2.set_xlabel('Daily Return (%)', fontsize=9.5, fontweight='bold')
ax2.set_ylabel('Trading Days Frequency', fontsize=9.5, fontweight='bold')
ax2.legend(loc='upper left', fontsize=8.5)
ax2.grid(True, linestyle='--', alpha=0.5)

fig.tight_layout()
mc_chart_path = os.path.abspath('reports/chart_institutional_monte_carlo_var.png')
plt.savefig(mc_chart_path, dpi=300)
plt.close()
print(f"Generated: {mc_chart_path}")

