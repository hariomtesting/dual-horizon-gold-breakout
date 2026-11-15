import os
import json
import requests

def send_vps_tutorial():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'config', 'telegram_config.json'))
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)

    bot_token = cfg['bot_token']
    chat_id = cfg['admin_chat_id']

    msg = (
        "🖥️ <b>STEP-BY-STEP VPS DEPLOYMENT & SETUP GUIDE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Run this algo 24/7 in the cloud without keeping your PC on!</i>\n\n"
        "📌 <b>1. What VPS to Buy:</b>\n"
        "• <b>OS</b>: Windows Server (2019 or 2022 64-bit)\n"
        "• <b>Specs</b>: 2 vCPUs | 4 GB RAM | 40 GB+ SSD\n"
        "• <b>Location</b>: <b>London (UK)</b> or Europe (1-5ms ping to Vantage)\n"
        "• <b>Budget Providers</b>: Contabo (~$8/mo), ForexVPS, Kamatera, AWS\n\n"
        "🔑 <b>2. How to Connect:</b>\n"
        "• On Windows: Open <b>Remote Desktop Connection</b> (RDP)\n"
        "• On Phone: Install <b>Microsoft Remote Desktop</b> app\n"
        "• Enter the VPS IP, Username (<code>Administrator</code>) and Password\n\n"
        "⚙️ <b>3. 5-Minute VPS Setup:</b>\n"
        "1️⃣ <b>Install MT5</b>: Download Vantage MT5. Log into account <code>26426942</code>. Enable <i>Tools -> Options -> Expert Advisors -> 'Allow Algo Trading'</i>.\n"
        "2️⃣ <b>Install Python</b>: Download Python 3.11 from python.org. ✅ <b>Check 'Add Python to PATH'</b>.\n"
        "3️⃣ <b>Copy Project</b>: Copy-paste the <code>Expermental</code> folder onto VPS desktop.\n"
        "4️⃣ <b>Install Dependencies</b> (in VPS PowerShell):\n"
        "<code>pip install MetaTrader5 pandas numpy pytz requests matplotlib</code>\n"
        "5️⃣ <b>Start Algo</b>:\n"
        "<code>python execution/gold_breakout_dual_horizon_testing_algo.py --mode live --risk 2.5</code>\n\n"
        "🔄 <b>4. Auto-Restart on VPS Reboot:</b>\n"
        "• Create a file <code>start_algo.bat</code> with:\n"
        "<code>@echo off\ncd /d C:\\Expermental\npython execution\\gold_breakout_dual_horizon_testing_algo.py --mode live --risk 2.5</code>\n"
        "• Press <b>Win + R</b>, type <code>shell:startup</code>, and drop a shortcut inside.\n\n"
        "📱 <b>5. 100% Mobile Control:</b>\n"
        "Once running, close your laptop! You will receive all alerts, charts (<code>/chart</code>), and controls (<code>/setrisk</code>, <code>/pause</code>) right here on Telegram.\n\n"
        "💡 <i>You can recall this guide anytime by typing <code>/vps</code> to this bot!</i>"
    )

    url_msg = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    r = requests.post(url_msg, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=20)
    print("Sent VPS Tutorial:", r.json().get('ok'))

if __name__ == '__main__':
    send_vps_tutorial()
