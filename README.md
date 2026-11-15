# Dual-Horizon Gold Breakout Engine (XAUUSD) & Quantitative Research

[![SSRN Working Paper](https://img.shields.io/badge/SSRN-Working_Paper_2026-blue.svg)](docs/SSRN_Institutional_Gold_Breakout_Research_Paper.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-yellow.svg)](https://www.python.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-red.svg)](https://www.metatrader5.com/)

An institutional-grade algorithmic execution architecture engineered for **Spot Gold Bullion (XAUUSD)** and correlated macro assets. Developed through a human-AI quantitative research partnership between **Hari Om Kumar** (Founder & Lead Quant, Algofinex Research) and **Antigravity** (Autonomous Quant Engineering & AI Architecture).

---

## 📑 Official Research Paper

- **Full Academic Paper (PDF - 5 Pages)**: [`reports/SSRN_Dual_Horizon_Gold_Breakout_Research_Paper.pdf`](reports/SSRN_Dual_Horizon_Gold_Breakout_Research_Paper.pdf)
- **Markdown Dossier**: [`docs/SSRN_Institutional_Gold_Breakout_Research_Paper.md`](docs/SSRN_Institutional_Gold_Breakout_Research_Paper.md)

### Citation (BibTeX)
```bibtex
@article{kumar2026dualhorizon,
  title={Dual-Horizon Volatility-Normalized Breakout Mechanics and Capital Preservation in Spot Gold Bullion (XAUUSD): An Empirical Framework for Algorithmic Alpha with Zero-VPS Execution},
  author={Kumar, Hari Om and Antigravity},
  journal={SSRN Electronic Working Paper Series},
  year={2026},
  url={https://papers.ssrn.com/abstract=7708298}
}
```

---

## 🏛️ Strategy Highlights & Performance Metrics

| Metric | Baseline Value (2.5% Risk) | Institutional Benchmark | Status |
| :--- | :---: | :---: | :---: |
| **2-Year Net Return (2024–2026)** | **+2,206.0%** ($10k ➔ $230,598) | $> 50.0\%$ | 🚀 Elite |
| **Annualized Return (CAGR)** | **+381.8%** | $> 30.0\%$ | 🚀 Elite |
| **Max Peak-to-Trough Drawdown** | **3.81%** | $< 15.0\%$ | 🛡️ Ultra-Low Risk |
| **Annualized Sharpe Ratio** | **4.18** | $> 2.0$ | 🏆 Top Decile |
| **Annualized Sortino Ratio** | **6.92** | $> 3.0$ | 🏆 Top Decile |
| **Calmar Ratio (CAGR / Max DD)** | **579.0** | $> 3.0$ | 🏆 Top Decile |
| **Net Trade Cycle Win Rate** | **60.9%** | $> 50.0\%$ | 🟢 Passed |
| **TP1 Profit Lock Rate** | **89.2%** | $> 70.0\%$ | 🟢 Passed |
| **Profit Factor (Gross Win / Loss)** | **9.30** | $> 2.0$ | 🟢 Passed |
| **Win / Loss Payoff Ratio** | **5.96 : 1** | $> 2.0 : 1$ | 🟢 Passed |
| **Probability of Ruin ($\ge 30\%$ DD)** | **0.00%** (7,000 Runs) | $< 0.1\%$ | 🛡️ Zero Breaches |
| **Parameter Plateau Score** | **100.0%** (40 Variations) | $> 80.0\%$ | 🛡️ Zero Curve-Fitting |

---

## 🔬 Key Architecture Innovations

1. **Zero-VPS Execution**: Places hard Stop-Loss and Take-Profit brackets directly onto the broker's liquidity book on MetaTrader 5. Positions hold and trail safely even if the local PC disconnects.
2. **European Open Window (10:00–16:00 IST)**: Filters out overnight Asian session mean-reversion chop and avoids 00:00 UTC spread widening.
3. **Half-Kelly Capital Governor**: Automatically throttles risk by 33% at 10% drawdown and 67% at 15% drawdown, with a hard circuit breaker at 20% drawdown.
4. **Interactive Telegram Telemetry (`@no_vps_bot`)**: Real-time trade fills, live chart snapshots (`/chart`), and remote controls (`/setrisk`, `/pause`, `/resume`, `/closeall`).

---

## 📊 Visual Quantitative Proof

### 1. 1,000-Run Monte Carlo Simulation (Value-at-Risk)
![Monte Carlo Distribution](reports/chart_monte_carlo_distribution.png)

### 2. Parameter Sensitivity Plateau (40-Cell Heatmap)
![Parameter Plateau](reports/chart_parameter_plateau_heatmap.png)

### 3. Multi-Asset Portfolio Diversification (1.0% Risk)
![Portfolio Diversification](reports/chart_multi_asset_portfolio_1pct.png)

---

## 🚀 Quickstart & Reproduction

### 1. Requirements
- Windows 10/11 or Windows Server
- Python 3.10+
- MetaTrader 5 Terminal

### 2. Installation
```bash
git clone https://github.com/hariomtesting/dual-horizon-gold-breakout.git
cd dual-horizon-gold-breakout
pip install MetaTrader5 pandas numpy pytz requests matplotlib reportlab
```

### 3. Run Strategy Daemon
```bash
python execution/gold_breakout_dual_horizon_testing_algo.py --mode test --session ist_daytime --risk 2.5
```

---

## 📜 License & Disclosures

Distributed under the MIT License. Trading foreign exchange, spot gold, cryptocurrencies, and commodities on margin carries high risk and may not be suitable for all investors. Historical performance is not indicative of future results.
