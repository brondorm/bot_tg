from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from telegram import (ForceReply, InlineKeyboardButton, InlineKeyboardMarkup,
                      KeyboardButton, ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

from database import Database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
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

    prefix = f"Новое сообщение от {full_name or username or user_id} (ID: {user_id}):"
    reply_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Ответить", callback_data=f"reply:{user_id}")]]
    )

    if message_type == "text":
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\n{message.text}",
            reply_markup=reply_keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\nТип: {message_type}",
            reply_markup=reply_keyboard,
        )
        await context.bot.copy_message(
            chat_id=settings.admin_chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )


async def prompt_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None:
        raise RuntimeError("Settings not loaded")
    query = update.callback_query
    if query is None or query.message is None:
        return
    if query.message.chat.id != settings.admin_chat_id:
        await query.answer()
        return

    _, _, user_id_str = query.data.partition(":") if query.data else ("", "", "")
    if not user_id_str.isdigit():
        await query.answer(text="Не удалось определить пользователя", show_alert=True)
        return

    target_user_id = int(user_id_str)
    context.chat_data["pending_reply_to"] = target_user_id
    await query.answer()
    await query.message.reply_text(
        "Введите ответ для клиента:",
        reply_markup=ForceReply(),
    )


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if settings is None or db is None:
        raise RuntimeError("Settings or database not initialized")
    message = update.effective_message
    if message is None or update.effective_chat is None:
        return
    if update.effective_chat.id != settings.admin_chat_id:
        return

    target_user_id = context.chat_data.get("pending_reply_to")
    if target_user_id is None:
        return

    reply_text = message.text
    if not reply_text:
        await message.reply_text("Ответ должен содержать текст")
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

    context.chat_data.pop("pending_reply_to", None)
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

    lines = [f"История переписки с {user_id} (последние {len(history)}):"]
    for direction, message_type, body, created_at in history:
        author = "Клиент" if direction == "from_client" else "Вы"
        if message_type in {"text", "command"}:
            content = body
        else:
            content = f"[{message_type}] {body}"
        lines.append(f"{created_at} — {author}: {content}")

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
    if not clients:
        await update.message.reply_text("Клиентов пока нет")
        return

    lines = ["Клиенты:"]
    keyboard_buttons = []
    for user_id, username, full_name, last_message in clients[:10]:
        identity = full_name or username or str(user_id)
        handle = f" (@{username})" if username else ""
        lines.append(f"• {identity}{handle} — последний контакт: {last_message}")
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
            & filters.REPLY,
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
