"""
========================================================================================
TELEGRAM BOT SETUP & TEST UTILITY
========================================================================================
Run this script to configure or test your Telegram alerts:
  python execution/setup_telegram.py --token "YOUR_BOT_TOKEN" --chat-id "YOUR_CHAT_ID"
Or simply run without arguments to check status or send a test alert!
========================================================================================
"""

import sys
import argparse
from telegram_notifier import TelegramNotifier

def main():
    parser = argparse.ArgumentParser(description="Configure Telegram Alerts for Algo Execution")
    parser.add_argument("--token", type=str, help="Telegram Bot Token from @BotFather")
    parser.add_argument("--chat-id", type=str, help="Telegram Chat ID from @userinfobot")
    parser.add_argument("--test", action="store_true", help="Send a test message")
    args = parser.parse_args()

    bot = TelegramNotifier()

    if args.token and args.chat_id:
        bot.save_config(args.token, args.chat_id)
        print("✅ Telegram credentials saved successfully!")
        print("Sending test message...")
        success = bot.send_message(
            "🎉 <b>CONGRATULATIONS! Telegram Connected Successfully</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Your <b>Antigravity Gold Breakout Engine</b> is now connected to your phone.\n"
            "You will receive instant alerts for:\n"
            "• 🎯 New Pre-Breakout Orders\n"
            "• ⚡ Live Order Fills & Slippage\n"
            "• 💰 TP1 Profit Locks (+BE Ratchets)\n"
            "• 🔒 Chandelier Trailing Updates\n"
            "• ⏰ 15:55 IST Daily Square-Off Summaries"
        )
        if success:
            print("🚀 Test alert delivered to your Telegram! Check your phone.")
        else:
            print("❌ Failed to deliver test message. Please verify your bot token and chat ID.")
        return

    if bot.is_configured:
        print(f"Telegram is currently CONFIGURED (Chat ID: {bot.chat_id}).")
        print("Sending test alert...")
        success = bot.send_message("✅ <b>Antigravity Algo Test Alert</b>: Connection is active and ready for Monday!")
        if success:
            print("🚀 Test message sent successfully!")
        else:
            print("❌ Failed to send message. Please check your credentials.")
    else:
        print("\n⚠️ Telegram is NOT YET CONFIGURED.")
        print("\nTo connect Telegram in under 1 minute:")
        print("1. Open Telegram on your phone and search for '@BotFather'.")
        print("2. Send '/newbot', choose a name, and copy the API TOKEN.")
        print("3. Search for '@userinfobot' to get your personal CHAT ID.")
        print("4. Send any message (like 'hello') to your new bot.")
        print("5. Run this command:")
        print('   python execution/setup_telegram.py --token "YOUR_TOKEN" --chat-id "YOUR_CHAT_ID"')

if __name__ == '__main__':
    main()
