from __future__ import annotations

import asyncio
import html
import logging
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

from database import Database

log_handlers = [logging.StreamHandler()]
log_file = os.getenv("LOG_FILE")
if log_file:
    log_path = Path(log_file)
    if log_path.parent and not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=log_handlers,
)
logger = logging.getLogger(__name__)


@dataclass
class Settings:
    token: str
    admin_chat_id: int
    database_path: str = "data/bot.db"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("BOT_TOKEN")
        admin_chat_id = os.getenv("ADMIN_CHAT_ID")

        if not token:
            raise RuntimeError("BOT_TOKEN is not set")
        if not admin_chat_id:
            raise RuntimeError("ADMIN_CHAT_ID is not set")

        return cls(token=token, admin_chat_id=int(admin_chat_id))


db: Optional[Database] = None
settings: Optional[Settings] = None

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Клиенты"), KeyboardButton("История")], [KeyboardButton("Меню")]],
    resize_keyboard=True,
)


def get_user_display(update: Update) -> tuple[int, Optional[str], Optional[str]]:
    assert update.effective_user is not None
    user = update.effective_user
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or None
    username = user.username
    return user.id, username, full_name


def get_admin_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    if settings is None:
        raise RuntimeError("Settings not loaded")
    admin_states = context.bot_data.setdefault("admin_states", {})
    state = admin_states.setdefault(settings.admin_chat_id, {})
    return state


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if db is None:
        raise RuntimeError("Database not initialized")
    if settings is None:
        raise RuntimeError("Settings not loaded")
    message = update.message
    if message is None:
        return

    user_id, username, full_name = get_user_display(update)

    if update.effective_chat and update.effective_chat.id == settings.admin_chat_id:
        await message.reply_text(
            "Админ-меню:",
            reply_markup=ADMIN_KEYBOARD,
        )
        return

    db.add_message(
        user_id=user_id,
        username=username,
        full_name=full_name,
        direction="from_client",
        message_type="command",
        content="/start",
    )
    await message.reply_text(
        "Здравствуйте! Напишите ваш вопрос, и я скоро отвечу."
    )


async def handle_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if db is None:
        raise RuntimeError("Database not initialized")

    message = update.effective_message
    if message is None:
        return

    if update.effective_chat and update.effective_chat.id == settings.admin_chat_id:
        return

    user_id, username, full_name = get_user_display(update)
    content = message.text or message.caption

    if message.photo:
        file_id = message.photo[-1].file_id
        message_type = "photo"
    elif message.document:
        file_id = message.document.file_id
        message_type = "document"
    elif message.voice:
        file_id = message.voice.file_id
        message_type = "voice"
    elif message.text:
        file_id = None
        message_type = "text"
    else:
        file_id = None
        message_type = "unknown"

    db.add_message(
        user_id=user_id,
        username=username,
        full_name=full_name,
        direction="from_client",
        message_type=message_type,
        content=content,
        file_id=file_id,
    )

    display_name = full_name or username or str(user_id)
    prefix = f"Новое сообщение от {display_name} (ID: {user_id}):"
    reply_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Ответить", callback_data=f"reply:{user_id}")]]
    )

    forwarded = None
    if message_type == "text":
        notification = await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\n{message.text}",
            reply_markup=reply_keyboard,
        )
    else:
        notification = await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\nТип: {message_type}",
            reply_markup=reply_keyboard,
        )
        try:
            forwarded = await context.bot.copy_message(
                chat_id=settings.admin_chat_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception:
            logger.exception("Failed to copy client message to admin chat")

    admin_state = get_admin_state(context)
    reply_targets = admin_state.setdefault("reply_targets", {})
    reply_targets[notification.message_id] = {
        "user_id": user_id,
        "display": display_name,
    }

    if forwarded is not None:
        reply_targets[forwarded.message_id] = {
            "user_id": user_id,
            "display": display_name,
        }


async def prompt_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None:
        raise RuntimeError("Settings not loaded")
    query = update.callback_query
    if query is None or query.message is None:
        return
    logger.debug(
        "Prompt reply callback from user_id=%s chat_id=%s data=%r message_id=%s",
        query.from_user.id if query.from_user else None,
        query.message.chat.id,
        query.data,
        query.message.message_id,
    )
    _, _, user_id_str = query.data.partition(":") if query.data else ("", "", "")
    if not user_id_str.isdigit():
        await query.answer(text="Не удалось определить пользователя", show_alert=True)
        return

    target_user_id = int(user_id_str)
    await query.answer("Введите сообщение в админ-чате")

    logger.info("Reply requested for user_id=%s via message_id=%s", target_user_id, query.message.message_id)

    # Remove inline buttons so the admin sees that the request is handled and
    # prevent repeated presses that confuse the reply UI.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("Could not remove inline keyboard for reply prompt", exc_info=True)

    admin_state = get_admin_state(context)
    reply_targets = admin_state.setdefault("reply_targets", {})
    identity = str(target_user_id)
    stored_target = reply_targets.get(query.message.message_id)
    if stored_target and stored_target.get("display"):
        identity = stored_target["display"]

    admin_state["pending_reply"] = {
        "user_id": target_user_id,
        "display": identity,
    }

    prompt_message = await context.bot.send_message(
        chat_id=settings.admin_chat_id,
        text=(
            f"Ответ для клиента {identity} (ID: {target_user_id}).\n"
            "Напишите следующее текстовое сообщение в этом чате, чтобы отправить его клиенту."
        ),
    )

    context.chat_data["pending_reply_prompt_id"] = prompt_message.message_id


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None or db is None:
        raise RuntimeError("Settings or database not initialized")
    message = update.effective_message
    if message is None or update.effective_chat is None:
        return
    if update.effective_chat.id != settings.admin_chat_id:
        return

    admin_state = get_admin_state(context)
    reply_targets = admin_state.get("reply_targets", {})

    target_info: Optional[dict[str, object]] = None

    reply_to = message.reply_to_message
    if reply_to is not None:
        target_info = reply_targets.get(reply_to.message_id)

    pending_raw = admin_state.get("pending_reply")
    pending = pending_raw if isinstance(pending_raw, dict) else None
    prompt_message_id = context.chat_data.get("pending_reply_prompt_id")

    if target_info is None and pending is not None:
        if reply_to is None:
            logger.debug(
                "Using pending reply for user_id=%s without explicit reply", pending.get("user_id")
            )
            target_info = pending
        elif prompt_message_id is not None and reply_to.message_id == prompt_message_id:
            logger.debug(
                "Using pending reply for user_id=%s via prompt reply", pending.get("user_id")
            )
            target_info = pending

    if not target_info:
        logger.debug(
            "Ignoring admin message id=%s: no reply target found (reply_to=%s, pending=%s)",
            message.message_id,
            reply_to.message_id if reply_to else None,
            bool(pending),
        )
        return

    target_user_id = target_info.get("user_id")
    if not isinstance(target_user_id, int):
        logger.warning(
            "Reply target missing numeric user_id. target_info=%s", target_info
        )
        return

    reply_text = message.text
    if not reply_text:
        await message.reply_text("Ответ должен содержать текст")
        return

    service_commands = {"Клиенты", "История", "Меню"}
    if reply_text in service_commands:
        logger.debug("Admin message matches service command '%s', ignoring", reply_text)
        return
    if reply_text.startswith("/"):
        logger.debug("Admin message '%s' starts with '/', ignoring", reply_text)
        return

    await context.bot.send_message(chat_id=target_user_id, text=reply_text)

    db.add_message(
        user_id=target_user_id,
        username=None,
        full_name=None,
        direction="from_admin",
        message_type="text",
        content=reply_text,
    )

    # Delete the prompt message after successfully sending the reply
    if prompt_message_id is not None:
        try:
            await context.bot.delete_message(
                chat_id=settings.admin_chat_id,
                message_id=prompt_message_id
            )
            logger.debug("Deleted prompt message id=%s", prompt_message_id)
        except Exception:
            logger.debug("Could not delete prompt message id=%s", prompt_message_id, exc_info=True)

    admin_state.pop("pending_reply", None)
    context.chat_data.pop("pending_reply_prompt_id", None)
    logger.info("Sent reply from admin to user_id=%s", target_user_id)
    await message.reply_text("Сообщение отправлено клиенту")


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if db is None:
        raise RuntimeError("Database not initialized")

    if update.effective_chat is None or update.effective_chat.id != settings.admin_chat_id:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /reply <user_id> <текст ответа>"
        )
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID пользователя должен быть числом")
        return

    reply_text = " ".join(context.args[1:])
    await context.bot.send_message(chat_id=target_user_id, text=reply_text)

    db.add_message(
        user_id=target_user_id,
        username=None,
        full_name=None,
        direction="from_admin",
        message_type="text",
        content=reply_text,
    )

    await update.message.reply_text("Сообщение отправлено клиенту")


def format_history_entries(history: list[tuple[str, str, Optional[str], str]], user_id: int) -> str:
    if not history:
        return "История пуста"

    header = (
        f"<b>История переписки с {html.escape(str(user_id))} "
        f"(последние {len(history)}):</b>"
    )
    lines = [header]
    for direction, message_type, body, created_at in history:
        author = "Клиент" if direction == "from_client" else "Вы"
        if message_type in {"text", "command"}:
            content = body or ""
        else:
            content = f"[{message_type}] {body or ''}"

        line = (
            f"{html.escape(created_at)} — {html.escape(author)}: "
            f"{html.escape(content)}"
        )
        lines.append(line)

    return "\n".join(lines)


async def clients_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if db is None:
        raise RuntimeError("Database not initialized")

    if update.effective_chat is None or update.effective_chat.id != settings.admin_chat_id:
        return

    clients = db.list_clients()
    logger.info("Fetched %s clients for admin listing", len(clients))
    if not clients:
        await update.message.reply_text("Клиентов пока нет")
        return

    lines = ["Клиенты:"]
    keyboard_buttons = []
    for user_id, username, full_name, last_message in clients[:10]:
        identity = full_name or (f"@{username}" if username else str(user_id))
        details: list[str] = []
        if username and identity != f"@{username}":
            details.append(f"@{username}")
        details.append(f"ID: {user_id}")
        details_text = f" ({', '.join(details)})" if details else ""
        lines.append(
            f"• {identity}{details_text} — последний контакт: {last_message}"
        )
        keyboard_buttons.append(
            [InlineKeyboardButton(identity, callback_data=f"history:{user_id}")]
        )

    reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None

    await update.message.reply_text("\n".join(lines), reply_markup=reply_markup)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if db is None:
        raise RuntimeError("Database not initialized")

    if update.effective_chat is None or update.effective_chat.id != settings.admin_chat_id:
        return

    if not context.args:
        await update.message.reply_text(
            "Использование: /history <user_id> [количество сообщений]"
        )
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID пользователя должен быть числом")
        return

    limit = 20
    if len(context.args) >= 2:
        try:
            limit = max(1, min(100, int(context.args[1])))
        except ValueError:
            await update.message.reply_text("Некорректное количество сообщений")
            return

    history = db.get_history(target_user_id, limit)
    if not history:
        await update.message.reply_text("История пуста")
        return

    await update.message.reply_text(
        format_history_entries(history, target_user_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def show_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None or db is None:
        raise RuntimeError("Settings or database not initialized")
    query = update.callback_query
    if query is None or query.message is None:
        return
    logger.debug(
        "History callback from user_id=%s chat_id=%s data=%r message_id=%s",
        query.from_user.id if query.from_user else None,
        query.message.chat.id,
        query.data,
        query.message.message_id,
    )
    if query.message.chat.id != settings.admin_chat_id:
        await query.answer()
        return

    data = query.data or ""
    _, _, user_id_str = data.partition(":")
    if not user_id_str.isdigit():
        await query.answer(text="Некорректный идентификатор", show_alert=True)
        return

    target_user_id = int(user_id_str)
    history = db.get_history(target_user_id, 20)
    text = format_history_entries(history, target_user_id)
    await query.answer()
    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if update.effective_chat is None or update.effective_chat.id != settings.admin_chat_id:
        return

    await update.message.reply_text(
        "Доступные действия:\n"
        "• Клиенты — показать последних собеседников\n"
        "• История — запросить историю по ID\n"
        "• Меню — показать эту подсказку",
        reply_markup=ADMIN_KEYBOARD,
    )


async def main() -> None:
    global settings, db
    settings = Settings.load()
    db = Database(settings.database_path)

    application: Application = ApplicationBuilder().token(settings.token).build()

    async def log_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        logger.info(
            "Button pressed: user_id=%s chat_id=%s data=%r inline_message_id=%s",
            query.from_user.id if query.from_user else None,
            query.message.chat.id if query.message else None,
            query.data,
            query.inline_message_id,
        )

    application.add_handler(
        CallbackQueryHandler(log_button_press, block=False)
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reply", reply_command))
    application.add_handler(CommandHandler("clients", clients_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("menu", menu_command))

    application.add_handler(CallbackQueryHandler(prompt_reply, pattern=r"^reply:"))
    application.add_handler(CallbackQueryHandler(show_history_callback, pattern=r"^history:"))

    application.add_handler(
        MessageHandler(
            filters.ALL
            & (~filters.COMMAND)
            & (~filters.Chat(settings.admin_chat_id)),
            handle_client_message,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(settings.admin_chat_id)
            & filters.Regex("^Клиенты$"),
            clients_command,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Chat(settings.admin_chat_id)
            & filters.Regex("^История$"),
            history_command,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Chat(settings.admin_chat_id)
            & filters.Regex("^Меню$"),
            menu_command,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(settings.admin_chat_id)
            & filters.TEXT
            & (~filters.COMMAND),
            handle_admin_reply,
        )
    )

    logger.info("Bot started")
    await application.initialize()
    await application.start()
    try:
        await application.updater.start_polling()
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
