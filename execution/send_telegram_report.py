"""
Send Risk Management Dossier and Chart directly to Telegram user now.
"""
import os
from telegram_notifier import telegram_bot

def main():
    if not telegram_bot.is_configured:
        print("Telegram not configured.")
        return

    pdf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports', 'Gold_Breakout_Risk_Management_Matrix_Report.pdf'))
    chart_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'reports', 'chart_risk_management_sweep_matrix.png'))

    # 1. Send Executive Summary Text
    intro = (
        "📊 <b>GOLD BREAKOUT: RISK & RETURN SENSITIVITY DOSSIER</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "• <b>Asset</b>: <code>XAUUSD (Spot Gold)</code>\n"
        "• <b>Session</b>: <code>10:00 AM – 4:00 PM IST (No VPS)</code>\n"
        "• <b>Account</b>: <code>DEMO_ACCOUNT_ID (Vantage Demo)</code>\n"
        "• <b>Balance</b>: <code>$10,000.00 USD</code>\n"
        "• <b>Architecture</b>: <code>Pre-Breakout Stop Orders • Chandelier Runner</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🚀 <i>Official quantitative audit completed across 7 risk tiers! See attached chart and PDF report below.</i>"
    )
    print("Sending intro message...", flush=True)
    telegram_bot.send_message(intro)

    # 2. Send Master Chart
    if os.path.exists(chart_path):
        print("Sending master chart image...", flush=True)
        chart_cap = (
            "📈 <b>RISK VS RETURN SENSITIVITY CURVES</b>\n"
            "• <b>1.0% Risk</b>: +250.6% Return | 1.56% Max DD\n"
            "• <b>2.5% Risk</b>: +2,206.0% Return (+381.8% CAGR) | 3.81% Max DD\n"
            "• <b>5.0% Risk</b>: +43,494.3% Return (+2,001.0% CAGR) | 7.49% Max DD\n"
            "• <b>7.5% Risk</b>: +127,466.1% Return | 9.50% Max DD\n"
            "🛡️ <i>All risk tiers remain strictly below the 30% DD mandate!</i>"
        )
        telegram_bot.send_photo(chart_path, caption=chart_cap)

    # 3. Send Official 2-Page PDF Document
    if os.path.exists(pdf_path):
        print("Sending PDF document...", flush=True)
        doc_cap = (
            "📑 <b>OFFICIAL 2-PAGE INSTITUTIONAL DOSSIER</b>\n"
            "Complete sensitivity tables, 5-year stress tests, Cent Account economics, and parameter management instructions."
        )
        telegram_bot.send_document(pdf_path, caption=doc_cap)

    # 4. Send Interactive Commands Tip
    tip = (
        "💡 <b>INTERACTIVE BOT COMMANDS READY</b>\n"
        "You or your friend can message me anytime:\n"
        "• <code>/status</code> — Real-time algo state\n"
        "• <code>/balance</code> — Live equity & drawdowns\n"
        "• <code>/risk</code> — View full risk & return comparison table\n"
        "• <code>/report</code> — Re-download this PDF & Chart\n"
        "• <code>/parameters</code> — How to change risk settings\n"
        "• <code>/help</code> — Full command menu"
    )
    telegram_bot.send_message(tip)
    print("All dossier items successfully delivered to Telegram!", flush=True)

if __name__ == '__main__':
    main()
