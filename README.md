# Telegram Keyword Auto-Reply Bot

Simple Python Telegram bot that replies when a message contains a keyword.

## 1) Create your bot token
1. Open Telegram and message `@BotFather`.
2. Run `/newbot` and follow prompts.
3. Copy the bot token.

## 2) Setup project
```powershell
cd C:\Users\krita\telegram-keyword-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3) Configure environment
```powershell
Copy-Item .env.example .env
```

Open `.env` and set:
- `TELEGRAM_BOT_TOKEN=...`
- `KEYWORD_FILE=keyword_replies.json`
- `REPLY_DELAY_SECONDS=30`
- `ADMIN_CHAT_ID=` (optional)

## 4) Define keyword replies
Edit `keyword_replies.json`:
```json
{
  "pricing": "Our plans start at $29/month.",
  "demo": "Book a demo: https://example.com/demo"
}
```

If incoming text contains `pricing` or `demo`, bot will send that reply.

## 5) Run the bot
```powershell
python main.py
```

## Notes
- Keyword matching is case-insensitive.
- Matching is word-based with light variation support (for example, `deposit`, `deposits`, `depositing`).
- First matching keyword in the JSON file is used.
- Bot ignores `group` and `supergroup` chats (replies in private chats only).
- Bot waits `REPLY_DELAY_SECONDS` before replying and shows typing status during the wait.
- If no keyword matches, bot sends no auto-reply (so human follow-up can happen manually).
- If `ADMIN_CHAT_ID` is set, bot forwards unmatched messages to that chat as an alert.
- Bot auto-reloads `keyword_replies.json` when the file changes (no restart needed).
- Logs are written to `logs/bot.log` (rotating log files).

## Telegram Business Setup
1. Open `@BotFather` -> `/mybots` -> choose your bot -> `Bot Settings`.
2. Enable Business support for the bot (option name may appear as `Business Mode` depending on app version).
3. In Telegram Business settings on your account, connect this bot under Chatbots.
4. This bot already handles `business_message` and `edited_business_message` updates.

If Telegram shows `This bot doesn't support Telegram Business yet`, Business support is not enabled on the bot/account side yet. Code changes alone cannot bypass that restriction.
