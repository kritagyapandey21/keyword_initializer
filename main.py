import json
import logging
import os
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

logger = logging.getLogger(__name__)


def setup_logging(base_dir: Path) -> None:
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler = RotatingFileHandler(log_dir / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


def load_keyword_replies(file_path: Path) -> Dict[str, str]:
    if not file_path.exists():
        raise FileNotFoundError(f"Keyword file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Keyword file must be a JSON object: {\"keyword\": \"reply\"}")

    cleaned: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        cleaned[key.strip().lower()] = value.strip()

    return cleaned


def normalize_token(token: str) -> str:
    token = token.lower().strip("'\"")

    # Very light stemming to catch common message variations like
    # deposit/deposits/depositing while keeping matching predictable.
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]

    return token


def tokenize_normalized(text: str) -> List[str]:
    parts = text.lower().replace("\n", " ").split()
    cleaned: List[str] = []
    for part in parts:
        token = "".join(ch for ch in part if ch.isalnum() or ch in {"'", "_"})
        if not token:
            continue
        cleaned.append(normalize_token(token))
    return cleaned


def contains_keyword_tokens(message_tokens: List[str], keyword_tokens: List[str]) -> bool:
    if not keyword_tokens or len(keyword_tokens) > len(message_tokens):
        return False

    window_size = len(keyword_tokens)
    for index in range(len(message_tokens) - window_size + 1):
        if message_tokens[index : index + window_size] == keyword_tokens:
            return True
    return False


def find_matching_reply(message_text: str, keyword_replies: Dict[str, str]) -> Optional[Tuple[str, str]]:
    message_tokens = tokenize_normalized(message_text)
    if not message_tokens:
        return None

    for keyword, reply in keyword_replies.items():
        keyword_tokens = tokenize_normalized(keyword)
        if keyword_tokens and contains_keyword_tokens(message_tokens, keyword_tokens):
            return keyword, reply
    return None


def maybe_reload_keyword_replies(application: Application) -> None:
    keyword_file: Path = application.bot_data["keyword_file"]
    previous_mtime: Optional[float] = application.bot_data.get("keyword_file_mtime")

    try:
        current_mtime = keyword_file.stat().st_mtime
    except FileNotFoundError:
        logger.warning("Keyword file missing at runtime: %s", keyword_file)
        return

    if previous_mtime is not None and current_mtime == previous_mtime:
        return

    try:
        updated_replies = load_keyword_replies(keyword_file)
    except Exception as exc:
        logger.exception("Failed to reload keyword file: %s", exc)
        return

    if not updated_replies:
        logger.warning("Reload skipped because keyword file has no valid entries.")
        return

    application.bot_data["keyword_replies"] = updated_replies
    application.bot_data["keyword_file_mtime"] = current_mtime
    logger.info("Reloaded keyword replies from %s with %d entries.", keyword_file, len(updated_replies))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! I am your keyword auto-reply bot. Send a message and I will reply when a keyword matches."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Edit keyword_replies.json to control replies. Example: {\"pricing\": \"Our price is...\"}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    business_connection_id = getattr(message, "business_connection_id", None)

    chat = update.effective_chat
    if chat is None:
        return

    if chat and chat.type in {"group", "supergroup"}:
        return

    sender_name = update.effective_user.full_name if update.effective_user else "Unknown"
    logger.info(
        "Incoming message chat_id=%s sender=%s business_connection_id=%s text=%s",
        chat.id,
        sender_name,
        business_connection_id,
        message.text,
    )

    maybe_reload_keyword_replies(context.application)

    keyword_replies: Dict[str, str] = context.application.bot_data["keyword_replies"]
    reply_delay_seconds: int = context.application.bot_data["reply_delay_seconds"]
    admin_chat_id: Optional[int] = context.application.bot_data.get("admin_chat_id")
    match = find_matching_reply(message.text, keyword_replies)
    if not match:
        logger.info("No keyword matched for message: %s", message.text)
        # No auto-reply on unmatched messages so a human can handle it manually.
        if admin_chat_id:
            preview = message.text[:1000]
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text=(
                    "Unmatched client message\n"
                    f"From: {sender_name}\n"
                    f"Chat ID: {chat.id}\n"
                    f"Message: {preview}"
                ),
            )
        return

    keyword, reply = match
    logger.info("Matched keyword '%s' in message: %s", keyword, message.text)

    # Show typing state while waiting to make response timing feel human.
    remaining = max(reply_delay_seconds, 0)
    while remaining > 0:
        typing_payload = {"chat_id": chat.id, "action": ChatAction.TYPING}
        if business_connection_id:
            typing_payload["business_connection_id"] = business_connection_id
        await context.bot.send_chat_action(**typing_payload)
        wait_for = min(4, remaining)
        await asyncio.sleep(wait_for)
        remaining -= wait_for

    try:
        reply_payload = {"chat_id": chat.id, "text": reply}
        if getattr(message, "message_thread_id", None) is not None:
            reply_payload["message_thread_id"] = message.message_thread_id
        if business_connection_id:
            reply_payload["business_connection_id"] = business_connection_id
        await context.bot.send_message(**reply_payload)
        logger.info("Successfully sent reply to chat_id=%s", chat.id)
    except Exception as exc:
        logger.exception("Failed to send message: %s", exc)


async def log_update_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Diagnostic logging so we can verify which update types Telegram delivers.
    update_types = []
    for attr in (
        "message",
        "edited_message",
        "business_message",
        "edited_business_message",
        "business_connection",
        "my_chat_member",
        "chat_member",
        "channel_post",
        "callback_query",
    ):
        if getattr(update, attr, None) is not None:
            update_types.append(attr)

    logger.info("Update received types=%s", ",".join(update_types) if update_types else "unknown")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    setup_logging(base_dir)
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    keyword_file_raw = Path(os.getenv("KEYWORD_FILE", "keyword_replies.json"))
    keyword_file = keyword_file_raw if keyword_file_raw.is_absolute() else (base_dir / keyword_file_raw)
    try:
        reply_delay_seconds = int(os.getenv("REPLY_DELAY_SECONDS", "30"))
    except ValueError:
        reply_delay_seconds = 30

    raw_admin_chat_id = os.getenv("ADMIN_CHAT_ID", "").strip()
    admin_chat_id: Optional[int] = None
    if raw_admin_chat_id:
        try:
            admin_chat_id = int(raw_admin_chat_id)
        except ValueError:
            logger.warning("Invalid ADMIN_CHAT_ID. Ignoring admin notification setting.")

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Put it in your .env file.")

    keyword_replies = load_keyword_replies(keyword_file)
    if not keyword_replies:
        raise RuntimeError("No valid keyword replies found in keyword_replies.json")

    app = Application.builder().token(token).build()
    app.bot_data["keyword_replies"] = keyword_replies
    app.bot_data["keyword_file"] = keyword_file
    app.bot_data["keyword_file_mtime"] = keyword_file.stat().st_mtime
    app.bot_data["reply_delay_seconds"] = max(reply_delay_seconds, 0)
    app.bot_data["admin_chat_id"] = admin_chat_id

    app.add_handler(TypeHandler(Update, log_update_type), group=-1)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGES & filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Listening for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
