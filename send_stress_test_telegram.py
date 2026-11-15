import os
import json
import requests
import time

def send_all():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'config', 'telegram_config.json'))
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)

    bot_token = cfg['bot_token']
    chat_id = cfg['admin_chat_id']

    # 1. Message 1: Monte Carlo Results
    mc_msg = (
        "🎲 <b>INSTITUTIONAL STRESS TEST #1: 1,000-RUN MONTE CARLO SIMULATION</b>\n"
        "<i>(Randomized Trade Sequence Permutations across 1,000 Parallel Universes)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Base Asset</b>: <code>XAUUSD (Spot Gold)</code>\n"
        "• <b>Risk Model</b>: <code>2.5% Base Risk with Half-Kelly Governor</code>\n"
        "• <b>Total Permutations</b>: <b>1,000 Simulated Universes</b>\n\n"
        "📊 <b>Risk & Drawdown Distribution:</b>\n"
        "• <b>50th Percentile (Median DD)</b>: <b>4.06%</b>\n"
        "• <b>95th Percentile (95% VaR)</b>: <b>6.32%</b>\n"
        "• <b>99th Percentile (Worst 1% Risk)</b>: <b>7.47%</b>\n"
        "• <b>Absolute Worst Reality Realized</b>: <b>11.15%</b>\n"
        "• <b>Probability of Ruin (DD &ge; 30%)</b>: <b>0.00% (ZERO BREACHES)</b>\n\n"
        "📉 <b>Consecutive Loss Streaks:</b>\n"
        "• <b>Average Max Streak</b>: <code>3.8 trades</code>\n"
        "• <b>95th Percentile Streak</b>: <code>7 trades</code>\n"
        "• <b>Absolute Worst Streak</b>: <code>11 trades</code>\n\n"
        "💰 <b>Median 2-Year Return</b>: <b>+2,205.5%</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>Institutional Verdict</b>: Even in the most hostile 1% of randomized sequences, drawdown never came close to the 30% mandate ceiling."
    )
    url_msg = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    r1 = requests.post(url_msg, json={"chat_id": chat_id, "text": mc_msg, "parse_mode": "HTML"}, timeout=20)
    print("Sent MC Msg:", r1.json().get('ok'))

    # Send MC Chart
    mc_chart_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'reports', 'chart_monte_carlo_distribution.png'))
    if os.path.exists(mc_chart_path):
        url_photo = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        with open(mc_chart_path, 'rb') as f_img:
            r_img1 = requests.post(
                url_photo,
                data={"chat_id": chat_id, "caption": "🎲 <b>1,000-Run Monte Carlo Simulation Chart</b>\nEquity curves, Drawdown distribution (95% VaR: 6.32%), and loss streak histogram.", "parse_mode": "HTML"},
                files={'photo': (os.path.basename(mc_chart_path), f_img, 'image/png')},
                timeout=60
            )
            print("Sent MC Chart:", r_img1.json().get('ok'))

    time.sleep(1.5)

    # 2. Message 2: Parameter Plateau Results
    plat_msg = (
        "🗺️ <b>INSTITUTIONAL STRESS TEST #2: PARAMETER SENSITIVITY & PLATEAU HEATMAP</b>\n"
        "<i>(Testing 40 Neighboring Variations of Chandelier Multiplier vs Stop ATR)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Tested Trailing Multipliers</b>: <code>2.0x, 2.2x, 2.4x, 2.6x, 2.8x, 3.0x, 3.2x, 3.5x ATR</code>\n"
        "• <b>Tested Stop-Loss Multipliers</b>: <code>1.6x, 1.8x, 2.0x, 2.2x, 2.5x ATR</code>\n"
        "• <b>Total Grid Points Tested</b>: <b>40 Separate Parameter Combinations</b>\n\n"
        "🏆 <b>Plateau Robustness Score</b>: <b>100.0%</b>\n"
        "• <b>100% of tested variations</b> generated massive positive returns:\n"
        "  Range: <b>+1,105.6% to +5,092.4% Net Return</b>\n"
        "• <b>100% of tested variations</b> kept Max Drawdown strictly between <b>3.00% and 4.15%</b>\n"
        "• <b>Profit Factor</b> remained rock-solid between <b>8.50 and 10.43</b> everywhere\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>Institutional Verdict</b>: The strategy does NOT sit on a fragile 'curve-fitted peak'. It is anchored on a vast, resilient mountain plateau of statistical edge."
    )
    r2 = requests.post(url_msg, json={"chat_id": chat_id, "text": plat_msg, "parse_mode": "HTML"}, timeout=20)
    print("Sent Plateau Msg:", r2.json().get('ok'))

    # Send Plateau Chart
    plat_chart_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'reports', 'chart_parameter_plateau_heatmap.png'))
    if os.path.exists(plat_chart_path):
        with open(plat_chart_path, 'rb') as f_img:
            r_img2 = requests.post(
                url_photo,
                data={"chat_id": chat_id, "caption": "🗺️ <b>Parameter Robustness Plateau Heatmaps</b>\nReturns (+1,105% to +5,092%) & Drawdowns (3.0% to 4.1%) across 40 parameter variations.", "parse_mode": "HTML"},
                files={'photo': (os.path.basename(plat_chart_path), f_img, 'image/png')},
                timeout=60
            )
            print("Sent Plateau Chart:", r_img2.json().get('ok'))

    # 3. Notification of new bot commands
    cmd_msg = (
        "💡 <b>NEW BOT COMMANDS NOW AVAILABLE:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "You or your friends can now ask the bot directly:\n"
        "• <code>/montecarlo</code> — Retrieves the 1,000-Run Monte Carlo stats & chart\n"
        "• <code>/plateau</code> — Retrieves the Parameter Robustness Heatmaps\n"
        "• <code>/help</code> — Full list of commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>All stress test data is now fully embedded in @no_vps_bot!</i>"
    )
    r3 = requests.post(url_msg, json={"chat_id": chat_id, "text": cmd_msg, "parse_mode": "HTML"}, timeout=20)
    print("Sent Cmds Msg:", r3.json().get('ok'))

if __name__ == '__main__':
    send_all()
