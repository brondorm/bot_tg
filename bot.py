from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
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
    [
        [KeyboardButton("Клиенты"), KeyboardButton("История")],
        [KeyboardButton("Меню")],
    ],
    resize_keyboard=True,
)


def get_user_display(update: Update) -> tuple[int, Optional[str], Optional[str]]:
    assert update.effective_user is not None
    user = update.effective_user
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or None
    username = user.username
    return user.id, username, full_name


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")

    message = update.message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if chat.id == settings.admin_chat_id:
        await message.reply_text(
            "Здравствуйте! Здесь вы можете управлять ботом.",
            reply_markup=ADMIN_KEYBOARD,
        )
        return

    if db is None:
        raise RuntimeError("Database not initialized")

    user_id, username, full_name = get_user_display(update)
    db.add_message(
        user_id=user_id,
        username=username,
        full_name=full_name,
        direction="from_client",
        message_type="command",
        content="/start",
    )
    await message.reply_text("Здравствуйте! Напишите ваш вопрос, и я скоро отвечу.")


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings
    if settings is None:
        raise RuntimeError("Settings not loaded")

    message = update.message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if chat.id != settings.admin_chat_id:
        return

    await message.reply_text(
        "Доступные кнопки:\n"
        "• «Клиенты» — показать список клиентов.\n"
        "• «История» — используйте команду /history <id> для просмотра переписки.\n"
        "• «Меню» — повторно показать клавиатуру.",
        reply_markup=ADMIN_KEYBOARD,
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
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Ответить", callback_data=f"reply:{user_id}")]
    ])

    if message_type == "text":
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\n{message.text}",
            reply_markup=reply_markup,
        )
    else:
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"{prefix}\nТип: {message_type}",
            reply_markup=reply_markup,
        )
        await context.bot.copy_message(
            chat_id=settings.admin_chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )


async def prompt_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings
    if settings is None:
        raise RuntimeError("Settings not loaded")

    query = update.callback_query
    if query is None or query.data is None:
        return

    if query.message is None or query.message.chat_id != settings.admin_chat_id:
        await query.answer()
        return

    if not query.data.startswith("reply:"):
        await query.answer()
        return

    target = query.data.split(":", 1)[1]
    try:
        target_user_id = int(target)
    except ValueError:
        await query.answer(text="Некорректный идентификатор", show_alert=True)
        return

    context.chat_data["reply_user_id"] = target_user_id

    await query.message.reply_text(
        "Введите ответ клиенту:",
        reply_markup=ForceReply(selective=True),
    )
    await query.answer()


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global settings, db
    if settings is None:
        raise RuntimeError("Settings not loaded")
    if db is None:
        raise RuntimeError("Database not initialized")

    message = update.effective_message
    if message is None:
        return

    if update.effective_chat is None or update.effective_chat.id != settings.admin_chat_id:
        return

    target_user_id = context.chat_data.get("reply_user_id")
    if target_user_id is None:
        return

    if not message.text:
        await message.reply_text("Можно отправлять только текстовые ответы")
        return

    await context.bot.send_message(chat_id=target_user_id, text=message.text)

    db.add_message(
        user_id=target_user_id,
        username=None,
        full_name=None,
        direction="from_admin",
        message_type="text",
        content=message.text,
    )

    context.chat_data.pop("reply_user_id", None)
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
    for user_id, username, full_name, last_message in clients:
        identity = full_name or username or str(user_id)
        handle = f" (@{username})" if username else ""
        lines.append(f"• {identity}{handle} — последний контакт: {last_message}")

    await update.message.reply_text("\n".join(lines))


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

    lines = [f"История переписки с {target_user_id} (последние {len(history)}):"]
    for direction, message_type, body, created_at in history:
        author = "Клиент" if direction == "from_client" else "Вы"
        if message_type == "text" or message_type == "command":
            content = body
        else:
            content = f"[{message_type}] {body}"
        lines.append(f"{created_at} — {author}: {content}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
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

    admin_chat_filter = filters.Chat(settings.admin_chat_id)

    application.add_handler(
        MessageHandler(admin_chat_filter & filters.Regex("^Клиенты$"), clients_command)
    )
    application.add_handler(
        MessageHandler(admin_chat_filter & filters.Regex("^История$"), history_command)
    )
    application.add_handler(
        MessageHandler(admin_chat_filter & filters.Regex("^Меню$"), menu_command)
    )

    application.add_handler(
        MessageHandler(
            (~admin_chat_filter) & (~filters.COMMAND),
            handle_client_message,
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
