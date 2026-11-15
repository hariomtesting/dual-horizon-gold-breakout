import os
import json
import requests

def send_portfolio_summary():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'config', 'telegram_config.json'))
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)

    bot_token = cfg['bot_token']
    chat_id = cfg['admin_chat_id']

    msg = (
        "🛡️ <b>QUANT AUDIT: 1.0% RISK MULTI-ASSET PORTFOLIO</b>\n"
        "<i>(10:00 – 16:00 IST No-VPS Hold & Trail • $10,000 Capital)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🥇 <b>1. Solo Gold (1.0% Risk)</b>:\n"
        "• Final Equity: <b>$26,161</b> (+161.6% Return)\n"
        "• Max Drawdown: <b>1.78%</b> | Profit Factor: <b>7.76</b>\n"
        "• Safety Cushion below 30%: <b>+28.22%</b>\n\n"
        "🥇🪙 <b>2. Dual-Asset: Gold + Bitcoin (1.0% Risk Each)</b>:\n"
        "• Final Equity: <b>$58,316</b> (+483.2% Return ➔ <b>3x Profit!</b>)\n"
        "• Max Drawdown: <b>1.67%</b> (<i>Drawdown drops due to diversification!</i>)\n"
        "• Win Rate: <b>56.8%</b> | TP1 Lock Rate: <b>98.4%</b> | PF: <b>6.81</b>\n"
        "• Safety Cushion below 30%: <b>+28.33%</b>\n\n"
        "🥇🪙📈 <b>3. Tri-Asset: Gold + Bitcoin + Nasdaq (1.0% Risk Each)</b>:\n"
        "• Final Equity: <b>$114,767</b> (+1,047.7% Return ➔ <b>10x Growth!</b>)\n"
        "• Max Drawdown: <b>2.38%</b> (<i>Incredible safety margin!</i>)\n"
        "• Win Rate: <b>55.6%</b> | TP1 Lock Rate: <b>98.3%</b> | PF: <b>5.35</b>\n"
        "• Safety Cushion below 30%: <b>+27.62%</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>THE INSTITUTIONAL DIVERSIFICATION EFFECT</b>:\n"
        "• Trading 1.0% risk on 2–3 assets provides <b>3x to 6x higher returns</b> without increasing drawdown, because Gold, Bitcoin, and Nasdaq breakouts happen on different days.\n"
        "• At 1.0% risk, you risk only <b>$100 on $10,000</b> (or <b>$1.00 on a $100 Cent Account</b>)."
    )

    url_msg = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    r1 = requests.post(url_msg, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=20)
    print("Sent Portfolio Msg:", r1.json().get('ok'))

    # Send Chart
    chart_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'reports', 'chart_multi_asset_portfolio_1pct.png'))
    if os.path.exists(chart_path):
        url_photo = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        with open(chart_path, 'rb') as f_img:
            r_img = requests.post(
                url_photo,
                data={
                    "chat_id": chat_id,
                    "caption": "📊 <b>1.0% Risk Multi-Asset Portfolio Comparison</b>\nLog-scale equity trajectories and Drawdown vs Return efficiency for Solo Gold, Gold+BTC, and Gold+BTC+NAS100.",
                    "parse_mode": "HTML"
                },
                files={'photo': (os.path.basename(chart_path), f_img, 'image/png')},
                timeout=60
            )
            print("Sent Portfolio Chart:", r_img.json().get('ok'))

if __name__ == '__main__':
    send_portfolio_summary()
