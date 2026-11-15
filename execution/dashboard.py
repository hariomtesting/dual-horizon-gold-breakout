import os
import sys
import json
import time
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import MetaTrader5 as mt5

app = FastAPI(title="Institutional Quantitative Trading Dashboard")

STATE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'live_state.json'))
TESTING_STATE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'testing_algo_state.json'))

@app.get("/api/state")
def get_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except Exception as e:
            return JSONResponse(content={"status": "reading_file", "error": str(e)})
    return JSONResponse(content={"status": "waiting_for_daemon", "ist_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

@app.get("/api/gold_testing_state")
def get_gold_testing_state():
    if os.path.exists(TESTING_STATE_FILE):
        try:
            with open(TESTING_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except Exception as e:
            return JSONResponse(content={"status": "reading_file", "error": str(e)})
    return JSONResponse(content={"status": "waiting_for_testing_engine"})

@app.post("/api/emergency_square_off")
def emergency_square_off():
    if not mt5.initialize():
        raise HTTPException(status_code=500, detail="Failed to initialize MT5")
    
    # Square off all positions for both Anchor (888999) and Scalper (777888)
    closed_count = 0
    positions = mt5.positions_get()
    if positions:
        for p in positions:
            c_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(p.symbol)
            price = tick.bid if c_type == mt5.ORDER_TYPE_SELL else tick.ask
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": c_type,
                "position": p.ticket,
                "price": price,
                "deviation": 25,
                "magic": p.magic,
                "comment": "Dashboard Emergency Square-off"
            })
            closed_count += 1
                
    # Delete all pending orders
    orders = mt5.orders_get()
    deleted_count = 0
    if orders:
        for o in orders:
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            deleted_count += 1
                
    return {"status": "success", "closed_positions": closed_count, "deleted_orders": deleted_count}

@app.get("/", response_class=HTMLResponse)
def get_dashboard_html():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Institutional Trading Command Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #06090e;
            --bg-card: rgba(16, 23, 38, 0.7);
            --bg-card-hover: rgba(22, 32, 54, 0.85);
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-glow: rgba(0, 242, 254, 0.25);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-cyan: #00f2fe;
            --accent-blue: #4facfe;
            --accent-emerald: #10b981;
            --accent-amber: #f59e0b;
            --accent-rose: #f43f5e;
            --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(0, 242, 254, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(79, 172, 254, 0.04) 0%, transparent 40%);
            color: var(--text-primary);
            font-family: var(--font-sans);
            min-height: 100vh;
            padding: 24px;
            overflow-x: hidden;
        }

        .container {
            max-width: 1440px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        /* HEADER */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 18px 24px;
            backdrop-filter: blur(12px);
        }

        .logo-box {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .badge-pulse {
            width: 12px;
            height: 12px;
            background: var(--accent-emerald);
            border-radius: 50%;
            box-shadow: 0 0 12px var(--accent-emerald);
            animation: pulse 2s infinite ease-in-out;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.2); opacity: 1; filter: drop-shadow(0 0 8px var(--accent-emerald)); }
            100% { transform: scale(0.95); opacity: 0.8; }
        }

        .header-title h1 {
            font-size: 1.25rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, #fff 0%, #cbd5e1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header-title p {
            font-size: 0.82rem;
            color: var(--text-muted);
            margin-top: 2px;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .time-box {
            text-align: right;
            font-family: var(--font-mono);
        }

        .time-box .ist-clock {
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--accent-cyan);
            letter-spacing: 0.03em;
        }

        .time-box .session-tag {
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .btn-emergency {
            background: rgba(244, 63, 94, 0.15);
            border: 1px solid rgba(244, 63, 94, 0.4);
            color: var(--accent-rose);
            font-family: var(--font-sans);
            font-weight: 700;
            font-size: 0.8rem;
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        .btn-emergency:hover {
            background: rgba(244, 63, 94, 0.3);
            border-color: var(--accent-rose);
            box-shadow: 0 0 16px rgba(244, 63, 94, 0.4);
        }

        /* METRIC CARDS GRID */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
        }

        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 16px 20px;
            backdrop-filter: blur(10px);
            transition: all 0.2s ease;
        }

        .metric-card:hover {
            border-color: var(--border-glow);
            transform: translateY(-2px);
        }

        .metric-label {
            font-size: 0.78rem;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.04em;
            margin-bottom: 6px;
        }

        .metric-val {
            font-size: 1.45rem;
            font-weight: 800;
            font-family: var(--font-mono);
            letter-spacing: -0.02em;
        }

        .metric-sub {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 6px;
            font-family: var(--font-mono);
        }

        .pos-green { color: var(--accent-emerald); }
        .neg-red { color: var(--accent-rose); }
        .neutral-cyan { color: var(--accent-cyan); }
        .warn-amber { color: var(--accent-amber); }

        /* SESSION COUNTDOWN BANNER */
        .schedule-banner {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: linear-gradient(90deg, rgba(16, 23, 38, 0.9) 0%, rgba(30, 41, 59, 0.6) 100%);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 14px 24px;
        }

        .timeline-step {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .step-circle {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.75rem;
            font-weight: 700;
            font-family: var(--font-mono);
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-subtle);
        }

        .step-circle.active {
            background: var(--accent-cyan);
            color: #06090e;
            box-shadow: 0 0 12px var(--accent-cyan);
            border-color: var(--accent-cyan);
        }

        .step-circle.done {
            background: var(--accent-emerald);
            color: #06090e;
        }

        .step-label {
            font-size: 0.8rem;
            font-weight: 600;
        }

        .step-time {
            font-size: 0.72rem;
            color: var(--text-muted);
            font-family: var(--font-mono);
        }

        /* ASSETS DUAL VIEW */
        .assets-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
            gap: 20px;
        }

        .asset-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 22px;
            backdrop-filter: blur(10px);
        }

        .asset-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 16px;
            border-bottom: 1px solid var(--border-subtle);
            padding-bottom: 14px;
        }

        .asset-title {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .asset-symbol {
            font-size: 1.35rem;
            font-weight: 800;
            letter-spacing: -0.01em;
        }

        .asset-type {
            font-size: 0.72rem;
            background: rgba(255, 255, 255, 0.06);
            padding: 3px 8px;
            border-radius: 6px;
            color: var(--text-secondary);
        }

        .asset-status-pill {
            font-family: var(--font-mono);
            font-size: 0.75rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 20px;
            text-transform: uppercase;
        }

        .status-WAIT_OPEN { background: rgba(100, 116, 139, 0.2); color: #94a3b8; border: 1px solid rgba(100, 116, 139, 0.3); }
        .status-EVAL { background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.4); }
        .status-LIMIT_WAIT { background: rgba(0, 242, 254, 0.2); color: var(--accent-cyan); border: 1px solid rgba(0, 242, 254, 0.4); }
        .status-ACTIVE { background: rgba(16, 185, 129, 0.2); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.4); animation: pulse 2s infinite; }
        .status-DONE { background: rgba(255, 255, 255, 0.05); color: #64748b; }

        .price-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 16px;
            background: rgba(0, 0, 0, 0.25);
            padding: 12px 16px;
            border-radius: 10px;
        }

        .price-col .label {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        .price-col .val {
            font-family: var(--font-mono);
            font-size: 1.15rem;
            font-weight: 700;
            margin-top: 2px;
        }

        .trade-params-box {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 12px;
        }

        .param-cell .p-label {
            font-size: 0.68rem;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
        }

        .param-cell .p-val {
            font-family: var(--font-mono);
            font-size: 0.88rem;
            font-weight: 700;
            margin-top: 3px;
        }

        /* TABLES & LOGS SECTION */
        .bottom-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }

        .table-card, .log-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 18px;
            backdrop-filter: blur(10px);
        }

        .card-header-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }

        .card-header-bar h3 {
            font-size: 0.95rem;
            font-weight: 700;
            color: var(--text-primary);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.78rem;
            font-family: var(--font-mono);
        }

        th {
            text-align: left;
            padding: 8px 10px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-subtle);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.68rem;
        }

        td {
            padding: 8px 10px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }

        .log-console {
            background: #020408;
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 12px;
            height: 190px;
            overflow-y: auto;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .log-row {
            display: flex;
            gap: 10px;
            line-height: 1.4;
        }

        .log-time { color: var(--accent-cyan); flex-shrink: 0; }
        .log-level-INFO { color: var(--text-muted); }
        .log-level-WARNING { color: var(--accent-amber); font-weight: 700; }
        .log-level-SUCCESS { color: var(--accent-emerald); font-weight: 700; }
        .log-msg { color: #e2e8f0; }

        @media (max-width: 1024px) {
            .metrics-grid { grid-template-columns: repeat(2, 1fr); }
            .assets-grid { grid-template-columns: 1fr; }
            .bottom-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- HEADER -->
        <header>
            <div class="logo-box">
                <div class="badge-pulse"></div>
                <div class="header-title">
                    <h1>INSTITUTIONAL QUANTITATIVE COMMAND CENTER</h1>
                    <p id="broker-status">Dual-Engine Ensemble: Anchor Momentum (888999) + Liquidity Scalper (777888)</p>
                </div>
            </div>
            <div class="header-right">
                <div class="time-box">
                    <div class="ist-clock" id="clock-display">--:--:-- IST</div>
                    <div class="session-tag">Active Hours: 10:00 - 15:55 IST (Square-Off)</div>
                </div>
                <button class="btn-emergency" onclick="triggerEmergencySquareOff()">Square Off Flat</button>
            </div>
        </header>

        <!-- TOP METRICS GRID -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Account Balance</div>
                <div class="metric-val neutral-cyan" id="metric-balance">$0.00</div>
                <div class="metric-sub" id="metric-peak">Peak: $0.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Account Equity</div>
                <div class="metric-val" id="metric-equity">$0.00</div>
                <div class="metric-sub" id="metric-margin">Free Margin: OK</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Today's P&L</div>
                <div class="metric-val" id="metric-day-pnl">$0.00</div>
                <div class="metric-sub" id="metric-day-roi">0.00% Daily ROI</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Current Drawdown</div>
                <div class="metric-val" id="metric-dd">0.00%</div>
                <div class="metric-sub" id="metric-cb">Circuit Limit: -3.00%</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Dual-Engine Risk</div>
                <div class="metric-val pos-green" id="metric-risk">2.00%</div>
                <div class="metric-sub" id="metric-risk-mode">1.25% Anchor + 0.75% Scalper</div>
            </div>
        </div>

        <!-- TIMELINE BANNER -->
        <div class="schedule-banner">
            <div class="timeline-step">
                <div class="step-circle done">1</div>
                <div>
                    <div class="step-label">Scalper Scanning</div>
                    <div class="step-time">10:00 - 15:55 IST</div>
                </div>
            </div>
            <div style="flex: 1; height: 2px; background: rgba(255,255,255,0.06); margin: 0 16px;"></div>
            <div class="timeline-step">
                <div class="step-circle done" id="step-anchor">2</div>
                <div>
                    <div class="step-label">Anchor Momentum</div>
                    <div class="step-time" id="countdown-anchor">12:00 PM IST</div>
                </div>
            </div>
            <div style="flex: 1; height: 2px; background: rgba(255,255,255,0.06); margin: 0 16px;"></div>
            <div class="timeline-step">
                <div class="step-circle active" id="step-eval">3</div>
                <div>
                    <div class="step-label">Liquidity Reversals</div>
                    <div class="step-time">Wick Sweeps (0.20 ATR)</div>
                </div>
            </div>
            <div style="flex: 1; height: 2px; background: rgba(255,255,255,0.06); margin: 0 16px;"></div>
            <div class="timeline-step">
                <div class="step-circle" id="step-flat">4</div>
                <div>
                    <div class="step-label">15:55 IST Flat Square-Off</div>
                    <div class="step-time" id="countdown-cutoff">Auto-Flatten (No VPS)</div>
                </div>
            </div>
        </div>

        <!-- DECOUPLED ASSETS GRID (DYNAMIC TOP 5 ASSETS) -->
        <div class="assets-grid" id="assets-container">
            <!-- Dynamically populated via JS -->
        </div>

        <!-- DUAL-HORIZON GOLD BREAKOUT ENGINE (V2 TESTING SIMULATOR) -->
        <div class="table-card" style="border: 1px solid rgba(245, 158, 11, 0.4); background: rgba(16, 23, 38, 0.9); border-radius: 16px; padding: 18px 24px; margin-top: 10px;">
            <div class="card-header-bar" style="border-bottom: 1px solid rgba(245, 158, 11, 0.2); padding-bottom: 12px; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 12px; height: 12px; background: #f59e0b; border-radius: 50%; box-shadow: 0 0 10px #f59e0b;"></div>
                    <div>
                        <h3 style="color: #fbbf24; font-size: 1rem; font-weight: 700; margin: 0;">🏛️ DUAL-HORIZON GOLD BREAKOUT ENGINE (TESTING SIMULATOR V2)</h3>
                        <p style="color: var(--text-muted); font-size: 0.75rem; margin: 2px 0 0 0;">1D Macro Trend Runner (3.0% Risk) + 1H Chandelier Super-Runner (2.5% Risk) | 30% Max DD Mandate</p>
                    </div>
                </div>
                <div style="display: flex; gap: 16px; align-items: center;">
                    <span id="gold-market-status" style="font-family: var(--font-mono); font-size: 0.72rem; padding: 4px 10px; border-radius: 6px; background: rgba(255,255,255,0.06); color: #94a3b8;">MARKET: CHECKING...</span>
                    <span id="gold-sim-equity" style="font-family: var(--font-mono); font-size: 0.95rem; font-weight: 700; color: #10b981;">Sim Equity: $10,000.00</span>
                    <span id="gold-sim-roi" style="font-family: var(--font-mono); font-size: 0.82rem; font-weight: 700; color: #38bdf8;">+0.00% Net ROI</span>
                </div>
            </div>

            <!-- DUAL HORIZON METRICS GRID -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin-bottom: 14px;">
                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px;">
                    <div style="font-size: 0.72rem; color: #fbbf24; font-weight: 700; text-transform: uppercase;">1D Macro Trend Runner</div>
                    <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Active Position:</span>
                        <span id="gold-1d-pos" style="font-family: var(--font-mono); font-weight: 700; color: #f8fafc;">FLAT</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Resting Order:</span>
                        <span id="gold-1d-pending" style="font-family: var(--font-mono); color: #38bdf8;">NONE</span>
                    </div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px;">
                    <div style="font-size: 0.72rem; color: #38bdf8; font-weight: 700; text-transform: uppercase;">1H Chandelier Super-Runner</div>
                    <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Active Position:</span>
                        <span id="gold-1h-pos" style="font-family: var(--font-mono); font-weight: 700; color: #f8fafc;">FLAT</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Resting Order:</span>
                        <span id="gold-1h-pending" style="font-family: var(--font-mono); color: #38bdf8;">NONE</span>
                    </div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px;">
                    <div style="font-size: 0.72rem; color: #10b981; font-weight: 700; text-transform: uppercase;">Risk Governor & Circuit Breaker</div>
                    <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Current Mode:</span>
                        <span id="gold-gov-mode" style="font-family: var(--font-mono); font-weight: 700; color: #10b981;">NORMAL</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Peak Drawdown:</span>
                        <span id="gold-gov-dd" style="font-family: var(--font-mono); color: #f43f5e;">0.00% (Ceiling: 20%)</span>
                    </div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px;">
                    <div style="font-size: 0.72rem; color: #a78bfa; font-weight: 700; text-transform: uppercase;">Testing Performance Stats</div>
                    <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Win Rate:</span>
                        <span id="gold-win-rate" style="font-family: var(--font-mono); font-weight: 700; color: #10b981;">0.0%</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 4px; font-size: 0.8rem;">
                        <span style="color: var(--text-muted);">Profit Factor:</span>
                        <span id="gold-pf" style="font-family: var(--font-mono); color: #fbbf24;">0.00</span>
                    </div>
                </div>
            </div>

            <!-- SIMULATED TRADES LOG -->
            <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; font-weight: 600;">Recent Simulated Breakout Trades:</div>
            <div id="gold-trades-container" style="max-height: 110px; overflow-y: auto; font-family: var(--font-mono); font-size: 0.72rem; background: rgba(0,0,0,0.25); border-radius: 6px; padding: 8px;">
                <div style="color: var(--text-muted);">Awaiting trade events...</div>
            </div>
        </div>

        <!-- ORDERS TABLE & LIVE EVENT AUDIT CONSOLE -->
        <div class="bottom-grid">
            <!-- ACTIVE ORDERS / POSITIONS -->
            <div class="table-card">
                <div class="card-header-bar">
                    <h3>Resting Orders & Active Positions</h3>
                    <span style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);" id="order-count">0 items</span>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Ticket</th>
                            <th>Symbol</th>
                            <th>Type</th>
                            <th>Lots</th>
                            <th>Price</th>
                            <th>SL</th>
                            <th>TP</th>
                            <th>Floating P&L</th>
                        </tr>
                    </thead>
                    <tbody id="orders-tbody">
                        <tr>
                            <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px;">No resting limit orders or active positions currently</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- LIVE EVENT CONSOLE -->
            <div class="log-card">
                <div class="card-header-bar">
                    <h3>Live System Telemetry & Event Stream</h3>
                    <span style="font-size: 0.72rem; color: var(--accent-cyan); font-family: var(--font-mono);">Polling 1000ms</span>
                </div>
                <div class="log-console" id="log-stream">
                    <div class="log-row">
                        <span class="log-time">SYSTEM</span>
                        <span class="log-msg">Initializing real-time stream from daemon...</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function fetchState() {
            try {
                const res = await fetch('/api/state');
                const data = await res.json();
                if (data.status === 'waiting_for_daemon') return;

                // Update Clock
                if (data.ist_time) {
                    document.getElementById('clock-display').innerText = data.ist_time.split(' ')[1] + ' IST';
                }

                // Header
                if (data.account) {
                    document.getElementById('broker-status').innerText = 
                        `Connected: ${data.account.server} (Login: ${data.account.login}) | Execution: ${data.paper_mode ? 'PAPER SIMULATION' : 'LIVE MT5 REAL ORDER'}`;
                    
                    document.getElementById('metric-balance').innerText = '$' + data.account.balance.toLocaleString('en-US', {minimumFractionDigits: 2});
                    document.getElementById('metric-peak').innerText = 'Peak: $' + data.account.peak.toLocaleString('en-US', {minimumFractionDigits: 2});
                    document.getElementById('metric-equity').innerText = '$' + data.account.equity.toLocaleString('en-US', {minimumFractionDigits: 2});
                    
                    const dayPnl = data.account.day_pnl;
                    const pnlElem = document.getElementById('metric-day-pnl');
                    pnlElem.innerText = (dayPnl >= 0 ? '+$' : '-$') + Math.abs(dayPnl).toFixed(2);
                    pnlElem.className = 'metric-val ' + (dayPnl >= 0 ? 'pos-green' : 'neg-red');
                    
                    document.getElementById('metric-day-roi').innerText = (data.account.day_pnl_pct >= 0 ? '+' : '') + data.account.day_pnl_pct.toFixed(2) + '% Daily ROI';
                    
                    const ddElem = document.getElementById('metric-dd');
                    ddElem.innerText = data.account.dd_pct.toFixed(2) + '%';
                    ddElem.className = 'metric-val ' + (data.account.dd_pct > 8.0 ? 'neg-red' : (data.account.dd_pct > 4.0 ? 'warn-amber' : 'neutral-cyan'));
                }

                // Risk
                if (data.risk) {
                    const totalRisk = (data.risk.combined_risk !== undefined ? data.risk.combined_risk : (data.risk.risk_pct || 0.02)) * 100;
                    document.getElementById('metric-risk').innerText = totalRisk.toFixed(2) + '%';
                    document.getElementById('metric-risk-mode').innerText = data.risk.risk_mode || 'DUAL-ENGINE ALLOCATION';
                }

                // Assets (Dynamic Render for all 5 Decoupled Assets)
                if (data.assets) {
                    renderAssetCards(data.assets);
                }

                // Orders / Positions
                renderOrders(data.open_positions || [], data.pending_orders || []);

                // Logs
                if (data.recent_logs && data.recent_logs.length > 0) {
                    renderLogs(data.recent_logs);
                }

                // Countdowns
                updateCountdowns();

                // Gold Testing State
                await fetchGoldTestingState();

            } catch (e) {
                console.error("Dashboard poll error:", e);
            }
        }

        async function fetchGoldTestingState() {
            try {
                const res = await fetch('/api/gold_testing_state');
                const g = await res.json();
                if (!g || g.status === 'waiting_for_testing_engine') return;

                // Market status
                const mktElem = document.getElementById('gold-market-status');
                if (mktElem) {
                    mktElem.innerText = 'MARKET: ' + (g.market_status || 'UNKNOWN');
                    mktElem.style.color = (g.market_status && g.market_status.startsWith('OPEN')) ? '#10b981' : '#f59e0b';
                }

                // Capital & ROI
                if (g.capital) {
                    document.getElementById('gold-sim-equity').innerText = 'Sim Equity: $' + g.capital.current_equity.toLocaleString('en-US', {minimumFractionDigits: 2});
                    const roiElem = document.getElementById('gold-sim-roi');
                    const roi = g.capital.return_pct || 0;
                    roiElem.innerText = (roi >= 0 ? '+' : '') + roi.toFixed(2) + '% Net ROI';
                    roiElem.style.color = roi >= 0 ? '#10b981' : '#f43f5e';
                }

                // 1D Macro Runner
                if (g.horizons && g.horizons['1D_macro_runner']) {
                    const h1d = g.horizons['1D_macro_runner'];
                    const pos1dElem = document.getElementById('gold-1d-pos');
                    if (h1d.active_position) {
                        pos1dElem.innerText = `${h1d.active_position.dir} ${h1d.active_position.lots}L @ $${h1d.active_position.entry}`;
                        pos1dElem.style.color = '#10b981';
                    } else {
                        pos1dElem.innerText = 'FLAT';
                        pos1dElem.style.color = '#94a3b8';
                    }
                    const pend1dElem = document.getElementById('gold-1d-pending');
                    if (h1d.pending_order) {
                        pend1dElem.innerText = `${h1d.pending_order.type} @ $${h1d.pending_order.entry_trigger} (${h1d.pending_order.lots}L)`;
                        pend1dElem.style.color = '#38bdf8';
                    } else {
                        pend1dElem.innerText = 'NONE';
                        pend1dElem.style.color = '#64748b';
                    }
                }

                // 1H Chandelier Runner
                if (g.horizons && g.horizons['1H_chandelier_runner']) {
                    const h1h = g.horizons['1H_chandelier_runner'];
                    const pos1hElem = document.getElementById('gold-1h-pos');
                    if (h1h.active_position) {
                        const p = h1h.active_position;
                        const beBadge = p.t1_hit ? ' [BE LOCKED]' : '';
                        pos1hElem.innerText = `${p.dir} (${p.lots_t1}+${p.lots_t2}L) @ $${p.entry}${beBadge}`;
                        pos1hElem.style.color = '#10b981';
                    } else {
                        pos1hElem.innerText = 'FLAT';
                        pos1hElem.style.color = '#94a3b8';
                    }
                    const pend1hElem = document.getElementById('gold-1h-pending');
                    if (h1h.pending_order) {
                        pend1hElem.innerText = `${h1h.pending_order.type} @ $${h1h.pending_order.entry_trigger} (${h1h.pending_order.total_lots}L)`;
                        pend1hElem.style.color = '#38bdf8';
                    } else {
                        pend1hElem.innerText = 'NONE';
                        pend1hElem.style.color = '#64748b';
                    }
                }

                // Governor & Performance
                if (g.governor) {
                    document.getElementById('gold-gov-mode').innerText = g.governor.mode;
                    const ddVal = (g.capital && g.capital.drawdown_pct !== undefined) ? g.capital.drawdown_pct : 0;
                    document.getElementById('gold-gov-dd').innerText = `${ddVal.toFixed(2)}% (Limit: ${g.governor.max_limit_dd}%)`;
                }

                if (g.performance) {
                    document.getElementById('gold-win-rate').innerText = `${g.performance.win_rate_pct.toFixed(1)}% (${g.performance.winning_trades}W / ${g.performance.losing_trades}L)`;
                    document.getElementById('gold-pf').innerText = `${g.performance.profit_factor.toFixed(2)}`;
                }

                // Recent Trades
                if (g.recent_trades && g.recent_trades.length > 0) {
                    const tCont = document.getElementById('gold-trades-container');
                    let tHtml = '';
                    g.recent_trades.slice().reverse().forEach((t, i) => {
                        const pnlColor = t.pnl >= 0 ? '#10b981' : '#f43f5e';
                        const pnlStr = (t.pnl >= 0 ? '+$' : '-$') + Math.abs(t.pnl).toFixed(2);
                        tHtml += `<div style="display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.04);">
                            <span style="color:#94a3b8;">${t.time} [${t.horizon}] ${t.dir} ${t.lots}L</span>
                            <span style="color:#f8fafc;">In: $${t.entry} -> Out: $${t.exit}</span>
                            <span style="color:${pnlColor}; font-weight:700;">${pnlStr}</span>
                            <span style="color:#64748b;">${t.type}</span>
                        </div>`;
                    });
                    tCont.innerHTML = tHtml;
                }
            } catch (err) {
                console.error("Gold testing state error:", err);
            }
        }

        function renderAssetCards(assets) {
            const container = document.getElementById('assets-container');
            if (!container) return;

            let html = '';
            for (const [sym, a] of Object.entries(assets)) {
                if (!a.enabled) continue;
                
                const phase = a.anchor_phase !== 'N/A' && a.anchor_phase ? a.anchor_phase : (a.scalper_trades_today >= a.scalper_max_trades ? 'DONE' : 'ACTIVE_SCAN');
                const bidStr = a.bid ? a.bid.toFixed(a.digits) : '--';
                const askStr = a.ask ? a.ask.toFixed(a.digits) : '--';
                const spreadStr = a.spread ? a.spread.toFixed(a.digits) : '--';
                const scalpsStr = `${a.scalper_trades_today || 0}/${a.scalper_max_trades || 3} Scalps`;
                const riskPctStr = (a.risk_pct ? (a.risk_pct * 100).toFixed(2) : '0.50') + '% Risk';

                html += `
                <div class="asset-card" id="card-${sym}">
                    <div class="asset-header">
                        <div class="asset-title">
                            <span class="asset-symbol">${sym}</span>
                            <span class="asset-type">${a.name} (Magic: ${a.magic || '--'})</span>
                        </div>
                        <span class="asset-status-pill status-${phase}">${phase}</span>
                    </div>
                    <div class="price-row">
                        <div class="price-col">
                            <div class="label">Bid Price</div>
                            <div class="val">${bidStr}</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Ask Price</div>
                            <div class="val">${askStr}</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Live Spread</div>
                            <div class="val neutral-cyan">${spreadStr}</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Today's Quota</div>
                            <div class="val pos-green">${scalpsStr}</div>
                        </div>
                    </div>
                    <div class="price-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border-subtle);">
                        <div class="price-col">
                            <div class="label">Decoupled Swing LB</div>
                            <div class="val" style="font-size: 0.95rem; color: #f59e0b;">${a.lookback || 12} bars</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Wick Buffer</div>
                            <div class="val" style="font-size: 0.95rem; color: #38bdf8;">${a.dist_atr || 0.2} ATR</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Dedicated Risk</div>
                            <div class="val" style="font-size: 0.95rem; color: #a78bfa;">${riskPctStr}</div>
                        </div>
                        <div class="price-col">
                            <div class="label">Strategy Model</div>
                            <div class="val" style="font-size: 0.82rem; color: var(--accent-emerald);">WICK RETEST LIMIT</div>
                        </div>
                    </div>
                    <div class="trade-params-box">
                        <div class="param-cell">
                            <div class="p-label">Execution Mode</div>
                            <div class="p-val">MAKER LIMIT</div>
                        </div>
                        <div class="param-cell">
                            <div class="p-label">Stop Buffer</div>
                            <div class="p-val neg-red">${a.sl_atr || 0.2} ATR Beyond Wick</div>
                        </div>
                        <div class="param-cell">
                            <div class="p-label">Take Profit 1</div>
                            <div class="p-val pos-green">${a.tp1_r || 1.4}R (Locks BE)</div>
                        </div>
                        <div class="param-cell">
                            <div class="p-label">Take Profit 2</div>
                            <div class="p-val pos-green">${a.tp2_r || 2.5}R Runner</div>
                        </div>
                    </div>
                </div>`;
            }
            container.innerHTML = html;
        }

        function renderOrders(positions, orders) {
            const tbody = document.getElementById('orders-tbody');
            const total = positions.length + orders.length;
            document.getElementById('order-count').innerText = `${total} items`;

            if (total === 0) {
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">No resting limit orders or open positions</td></tr>`;
                return;
            }

            let html = '';
            // Positions
            positions.forEach(p => {
                const pnlClass = p.profit >= 0 ? 'pos-green' : 'neg-red';
                const beBadge = p.is_breakeven ? `<span style="background:rgba(16,185,129,0.25);color:#10b981;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;margin-left:6px;">RISK-FREE</span>` : '';
                const engineBadge = p.magic === 777888 ? `<span style="background:rgba(56,189,248,0.2);color:#38bdf8;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;margin-right:6px;">SCALPER</span>` : `<span style="background:rgba(167,139,250,0.2);color:#a78bfa;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;margin-right:6px;">ANCHOR</span>`;
                html += `<tr>
                    <td>${engineBadge}#${p.ticket} ${beBadge}</td>
                    <td><strong>${p.symbol}</strong></td>
                    <td class="pos-green">ACTIVE ${p.type}</td>
                    <td>${p.volume.toFixed(2)}</td>
                    <td>${p.price_open.toFixed(2)}</td>
                    <td class="${p.is_breakeven ? 'pos-green' : 'neg-red'}">${p.sl.toFixed(2)}</td>
                    <td class="pos-green">${p.tp.toFixed(2)}</td>
                    <td class="${pnlClass}"><strong>$${p.profit.toFixed(2)}</strong></td>
                </tr>`;
            });
            // Pending Orders
            orders.forEach(o => {
                const engineBadge = o.magic === 777888 ? `<span style="background:rgba(56,189,248,0.2);color:#38bdf8;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;margin-right:6px;">SCALPER</span>` : `<span style="background:rgba(167,139,250,0.2);color:#a78bfa;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;margin-right:6px;">ANCHOR</span>`;
                html += `<tr>
                    <td>${engineBadge}#${o.ticket}</td>
                    <td><strong>${o.symbol}</strong></td>
                    <td class="neutral-cyan">LIMIT RESTING (${o.type})</td>
                    <td>${o.volume.toFixed(2)}</td>
                    <td>${o.price_open.toFixed(2)}</td>
                    <td class="neg-red">${o.sl.toFixed(2)}</td>
                    <td class="pos-green">${o.tp.toFixed(2)}</td>
                    <td>Resting</td>
                </tr>`;
            });
            tbody.innerHTML = html;
        }

        function renderLogs(logs) {
            const stream = document.getElementById('log-stream');
            let html = '';
            logs.forEach(l => {
                html += `<div class="log-row">
                    <span class="log-time">[${l.time}]</span>
                    <span class="log-level-${l.level}">[${l.level}]</span>
                    <span class="log-msg">${l.msg}</span>
                </div>`;
            });
            stream.innerHTML = html;
        }

        function updateCountdowns() {
            const now = new Date();
            // 12:00 PM IST Anchor
            const targetAnchor = new Date();
            targetAnchor.setHours(12, 0, 0, 0);
            const diffA = Math.floor((targetAnchor - now) / 1000);
            if (diffA > 0) {
                const h = Math.floor(diffA / 3600);
                const m = Math.floor((diffA % 3600) / 60);
                const s = diffA % 60;
                document.getElementById('countdown-anchor').innerText = `12:00 PM (in ${h > 0 ? h + 'h ' : ''}${m}m ${s}s)`;
            } else {
                document.getElementById('countdown-anchor').innerText = `12:00 PM (Active / Completed)`;
                document.getElementById('step-anchor').className = 'step-circle done';
                document.getElementById('step-eval').className = 'step-circle active';
            }

            // 15:55 PM IST Cutoff
            const targetCutoff = new Date();
            targetCutoff.setHours(15, 55, 0, 0);
            const diffC = Math.floor((targetCutoff - now) / 1000);
            if (diffC > 0) {
                const h = Math.floor(diffC / 3600);
                const m = Math.floor((diffC % 3600) / 60);
                document.getElementById('countdown-cutoff').innerText = `15:55 PM (in ${h}h ${m}m)`;
            } else {
                document.getElementById('countdown-cutoff').innerText = `15:55 PM (Square-off Done)`;
                document.getElementById('step-flat').className = 'step-circle done';
            }
        }

        async function triggerEmergencySquareOff() {
            if (confirm("Are you sure you want to SQUARE OFF ALL POSITIONS and CANCEL ALL ORDERS immediately?")) {
                try {
                    const res = await fetch('/api/emergency_square_off', { method: 'POST' });
                    const resJson = await res.json();
                    alert("Emergency square-off executed: " + JSON.stringify(resJson));
                    fetchState();
                } catch (e) {
                    alert("Emergency square-off error: " + e);
                }
            }
        }

        setInterval(fetchState, 1000);
        fetchState();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")

