import os
import json
import requests

def send_paper():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'config', 'telegram_config.json'))
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)

    bot_token = cfg['bot_token']
    chat_id = cfg['admin_chat_id']
    pdf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'reports', 'SSRN_Dual_Horizon_Gold_Breakout_Research_Paper.pdf'))

    caption = (
        "📑 <b>OFFICIAL SSRN ACADEMIC RESEARCH PAPER RELEASE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎓 <b>Title</b>: <i>Dual-Horizon Volatility-Normalized Breakout Mechanics and Capital Preservation in Spot Gold Bullion (XAUUSD)</i>\n"
        "✍️ <b>Authors</b>: Hari Om Kumar & Antigravity Quant Research\n"
        "🏛️ <b>Series</b>: SSRN Electronic Working Paper Series\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Highlights from the Paper:</b>\n"
        "• Complete mathematical formulations of Fractal Triggers & Chandelier Trailing Mechanics.\n"
        "• 2-Year Empirical Results (+2,206% Return, 3.81% Max DD, Calmar 579.0).\n"
        "• 7,000-Run Monte Carlo Simulation (0.00% Probability of Ruin).\n"
        "• 40-Cell Parameter Sensitivity Plateau (100% Robustness Score).\n"
        "• Multi-Asset Cross-Validation (Bitcoin +5,503%, 1% Tri-Asset Portfolio +1,047%).\n"
        "• Zero-VPS Broker-Side Execution Architecture.\n\n"
        "<i>Attached is the full 5-page formal research paper!</i>"
    )

    url_doc = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(pdf_path, 'rb') as f:
        r = requests.post(
            url_doc,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={'document': (os.path.basename(pdf_path), f, 'application/pdf')},
            timeout=60
        )
        print("Sent SSRN PDF to Telegram:", r.json().get('ok'))

if __name__ == '__main__':
    send_paper()
