#!/usr/bin/env python3
"""
Telegram Bot для общения с клиентами
Переписанная версия с улучшенной логикой
"""

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import Database

# ===== НАСТРОЙКА ЛОГИРОВАНИЯ =====
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


# ===== НАСТРОЙКИ =====
@dataclass
class Settings:
    """Настройки бота"""
    token: str
    admin_chat_id: int
    database_path: str = "data/bot.db"

    @classmethod
    def load(cls) -> "Settings":
        """Загрузка настроек из переменных окружения"""
        load_dotenv()
        token = os.getenv("BOT_TOKEN")
        admin_chat_id = os.getenv("ADMIN_CHAT_ID")

        if not token:
            raise RuntimeError("BOT_TOKEN не установлен в .env файле")
        if not admin_chat_id:
            raise RuntimeError("ADMIN_CHAT_ID не установлен в .env файле")

        return cls(token=token, admin_chat_id=int(admin_chat_id))


# Глобальные переменные
settings: Optional[Settings] = None
db: Optional[Database] = None


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_user_info(update: Update) -> tuple[int, Optional[str], Optional[str]]:
    """Получить информацию о пользователе"""
    if not update.effective_user:
        raise RuntimeError("Пользователь не найден")

    user = update.effective_user
    user_id = user.id
    username = user.username
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or None

    return user_id, username, full_name


def get_user_display_name(user_id: int, username: Optional[str], full_name: Optional[str]) -> str:
    """Получить отображаемое имя пользователя"""
    if full_name:
        return full_name
    elif username:
        return f"@{username}"
    else:
        return f"ID: {user_id}"


# ===== ОБРАБОТЧИКИ КЛИЕНТСКИХ СООБЩЕНИЙ =====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start от клиента"""
    if not update.message or not settings or not db:
        return

    user_id, username, full_name = get_user_info(update)

    # Если это админ, просто приветствуем
    if update.effective_chat and update.effective_chat.id == settings.admin_chat_id:
        await update.message.reply_text(
            "👋 Привет, Админ!\n\n"
            "Доступные команды:\n"
            "/clients - Список клиентов\n"
            "/history <user_id> - История с клиентом"
        )
        return

    # Сохраняем в базу
    db.add_message(
        user_id=user_id,
        username=username,
        full_name=full_name,
        direction="from_client",
        message_type="command",
        content="/start",
    )

    # Отвечаем клиенту
    await update.message.reply_text(
        "👋 Здравствуйте!\n\n"
        "Я бот для связи с поддержкой. Напишите ваш вопрос, "
        "и я передам его оператору. Скоро вам ответят!"
    )

    # Уведомляем админа
    display_name = get_user_display_name(user_id, username, full_name)
    await context.bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"🆕 Новый пользователь: {display_name} (ID: {user_id})\nОтправил команду /start",
    )


async def handle_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик сообщений от клиентов"""
    if not update.message or not settings or not db:
        return

    # Игнорируем сообщения от админа
    if update.effective_chat and update.effective_chat.id == settings.admin_chat_id:
        return

    user_id, username, full_name = get_user_info(update)
    message = update.message

    # Определяем тип сообщения
    if message.text:
        content = message.text
        message_type = "text"
        file_id = None
    elif message.photo:
        content = message.caption
        message_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.document:
        content = message.caption
        message_type = "document"
        file_id = message.document.file_id
    elif message.voice:
        content = None
        message_type = "voice"
        file_id = message.voice.file_id
    elif message.video:
        content = message.caption
        message_type = "video"
        file_id = message.video.file_id
    else:
        content = None
        message_type = "unknown"
        file_id = None

    # Сохраняем в базу
    db.add_message(
        user_id=user_id,
        username=username,
        full_name=full_name,
        direction="from_client",
        message_type=message_type,
        content=content,
        file_id=file_id,
    )

    # Формируем уведомление для админа
    display_name = get_user_display_name(user_id, username, full_name)

    # Создаем кнопку "Ответить"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{user_id}")],
        [InlineKeyboardButton("📜 История", callback_data=f"history:{user_id}")]
    ])

    # Отправляем уведомление админу
    if message_type == "text":
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"💬 Сообщение от {display_name}\nID: {user_id}\n\n{content}",
            reply_markup=keyboard,
        )
    else:
        # Сначала отправляем заголовок
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"💬 Сообщение от {display_name}\nID: {user_id}\nТип: {message_type}",
            reply_markup=keyboard,
        )

        # Затем пересылаем само сообщение
        try:
            await context.bot.copy_message(
                chat_id=settings.admin_chat_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as e:
            logger.error(f"Не удалось переслать медиа: {e}")

    logger.info(f"Получено сообщение от клиента {user_id} ({message_type})")


# ===== ОБРАБОТЧИКИ КНОПОК =====
async def button_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик нажатия кнопки "Ответить"
    Показывает приглашение написать ответ
    """
    if not update.callback_query or not settings:
        return

    query = update.callback_query
    await query.answer()

    # Извлекаем user_id из callback_data
    callback_data = query.data or ""
    if not callback_data.startswith("reply:"):
        return

    try:
        user_id = int(callback_data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Убираем кнопки с исходного сообщения
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Отправляем приглашающее сообщение
    prompt_msg = await context.bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"✍️ Ответ для клиента ID: {user_id}\n\n"
             "Напишите следующее текстовое сообщение в этом чате, "
             "чтобы отправить его клиенту.",
    )

    # Сохраняем информацию о том, что ждём ответ для этого клиента
    if "pending_replies" not in context.bot_data:
        context.bot_data["pending_replies"] = {}

    context.bot_data["pending_replies"][settings.admin_chat_id] = {
        "user_id": user_id,
        "prompt_message_id": prompt_msg.message_id,
    }

    logger.info(f"Админ начал отвечать клиенту {user_id}")


async def button_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия кнопки "История" """
    if not update.callback_query or not settings or not db:
        return

    query = update.callback_query
    await query.answer()

    # Извлекаем user_id из callback_data
    callback_data = query.data or ""
    if not callback_data.startswith("history:"):
        return

    try:
        user_id = int(callback_data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Получаем историю
    history = db.get_history(user_id, limit=20)

    if not history:
        await context.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"📜 История с клиентом {user_id}\n\nИстория пуста.",
        )
        return

    # Формируем текст истории
    lines = [f"📜 <b>История с клиентом {user_id}</b>", ""]

    for direction, msg_type, content, created_at in history:
        author = "👤 Клиент" if direction == "from_client" else "👨‍💼 Вы"

        if msg_type in {"text", "command"}:
            text = content or ""
        else:
            text = f"[{msg_type}] {content or ''}"

        lines.append(
            f"{created_at}\n{author}: {html.escape(text)}\n"
        )

    history_text = "\n".join(lines)

    await context.bot.send_message(
        chat_id=settings.admin_chat_id,
        text=history_text,
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"Показана история для клиента {user_id}")


# ===== ОБРАБОТЧИКИ СООБЩЕНИЙ АДМИНА =====
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений от админа (ответы клиентам)"""
    if not update.message or not settings or not db:
        return

    # Проверяем, что это сообщение от админа
    if update.effective_chat and update.effective_chat.id != settings.admin_chat_id:
        return

    # Проверяем, есть ли ожидающий ответ
    if "pending_replies" not in context.bot_data:
        return

    pending = context.bot_data["pending_replies"].get(settings.admin_chat_id)
    if not pending:
        return

    user_id = pending["user_id"]
    prompt_message_id = pending["prompt_message_id"]

    # Получаем текст ответа
    reply_text = update.message.text
    if not reply_text:
        await update.message.reply_text("❌ Ответ должен содержать текст")
        return

    try:
        # Отправляем сообщение клиенту
        await context.bot.send_message(
            chat_id=user_id,
            text=reply_text,
        )

        # Сохраняем в базу
        db.add_message(
            user_id=user_id,
            username=None,
            full_name=None,
            direction="from_admin",
            message_type="text",
            content=reply_text,
        )

        # Удаляем приглашающее сообщение
        try:
            await context.bot.delete_message(
                chat_id=settings.admin_chat_id,
                message_id=prompt_message_id,
            )
        except Exception as e:
            logger.debug(f"Не удалось удалить приглашение: {e}")

        # Показываем подтверждение
        await update.message.reply_text(
            f"✅ Сообщение отправлено клиенту {user_id}"
        )

        # Очищаем состояние
        del context.bot_data["pending_replies"][settings.admin_chat_id]

        logger.info(f"Админ отправил ответ клиенту {user_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки сообщения клиенту: {e}")
        await update.message.reply_text(
            f"❌ Ошибка отправки сообщения: {e}"
        )


# ===== КОМАНДЫ АДМИНА =====
async def clients_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /clients - показать список клиентов"""
    if not update.message or not settings or not db:
        return

    # Проверяем, что команда от админа
    if update.effective_chat and update.effective_chat.id != settings.admin_chat_id:
        return

    clients = db.list_clients()

    if not clients:
        await update.message.reply_text("📋 Клиентов пока нет")
        return

    # Формируем список клиентов
    lines = ["👥 <b>Список клиентов:</b>\n"]
    keyboard = []

    for user_id, username, full_name, last_message in clients[:20]:
        display_name = get_user_display_name(user_id, username, full_name)
        lines.append(
            f"• {html.escape(display_name)}\n"
            f"  ID: {user_id}\n"
            f"  Последнее сообщение: {last_message}\n"
        )

        # Добавляем кнопки для быстрого доступа
        keyboard.append([
            InlineKeyboardButton(
                f"💬 {display_name}",
                callback_data=f"history:{user_id}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )

    logger.info(f"Показан список из {len(clients)} клиентов")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /history <user_id> - показать историю с клиентом"""
    if not update.message or not settings or not db:
        return

    # Проверяем, что команда от админа
    if update.effective_chat and update.effective_chat.id != settings.admin_chat_id:
        return

    # Проверяем аргументы
    if not context.args:
        await update.message.reply_text(
            "❌ Использование: /history <user_id> [лимит]\n"
            "Пример: /history 123456789 50"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")
        return

    # Получаем лимит (по умолчанию 20)
    limit = 20
    if len(context.args) >= 2:
        try:
            limit = max(1, min(100, int(context.args[1])))
        except ValueError:
            pass

    # Получаем историю
    history = db.get_history(user_id, limit)

    if not history:
        await update.message.reply_text(
            f"📜 История с клиентом {user_id}\n\nИстория пуста."
        )
        return

    # Формируем текст истории
    lines = [f"📜 <b>История с клиентом {user_id}</b> (последние {len(history)})", ""]

    for direction, msg_type, content, created_at in history:
        author = "👤 Клиент" if direction == "from_client" else "👨‍💼 Вы"

        if msg_type in {"text", "command"}:
            text = content or ""
        else:
            text = f"[{msg_type}] {content or ''}"

        lines.append(
            f"{created_at}\n{author}: {html.escape(text)}\n"
        )

    history_text = "\n".join(lines)

    await update.message.reply_text(
        history_text,
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"Показана история для клиента {user_id} ({len(history)} сообщений)")


# ===== ГЛАВНАЯ ФУНКЦИЯ =====
async def main() -> None:
    """Главная функция запуска бота"""
    global settings, db

    # Загружаем настройки
    settings = Settings.load()

    # Инициализируем базу данных
    db = Database(settings.database_path)

    logger.info("✅ Настройки загружены")
    logger.info(f"✅ База данных: {settings.database_path}")
    logger.info(f"✅ Админ ID: {settings.admin_chat_id}")

    # Создаем приложение
    application = ApplicationBuilder().token(settings.token).build()

    # ===== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ =====

    # Команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("clients", clients_command))
    application.add_handler(CommandHandler("history", history_command))

    # Кнопки (callback queries) - ПЕРВЫМИ!
    application.add_handler(CallbackQueryHandler(button_reply, pattern=r"^reply:"))
    application.add_handler(CallbackQueryHandler(button_history, pattern=r"^history:"))

    # Сообщения от админа (ответы клиентам) - более специфичный фильтр идёт раньше
    application.add_handler(
        MessageHandler(
            filters.Chat(settings.admin_chat_id)
            & filters.TEXT
            & (~filters.COMMAND),
            handle_admin_message,
        )
    )

    # Сообщения от клиентов В ПОСЛЕДНЮЮ ОЧЕРЕДЬ - используем конкретные типы вместо ALL
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.DOCUMENT | filters.VOICE | filters.VIDEO)
            & (~filters.COMMAND)
            & (~filters.Chat(settings.admin_chat_id)),
            handle_client_message,
        )
    )

    # Запускаем бота
    logger.info("🚀 Бот запущен и готов к работе!")

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
        logger.info("👋 Бот остановлен")
