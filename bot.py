#!/usr/bin/env python3
"""
Telegram Bot для общения с клиентами
Версия на aiogram 3.x
"""

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.enums import ParseMode
from dotenv import load_dotenv

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
bot: Optional[Bot] = None

# Словарь для отслеживания ожидающих ответов (user_id -> prompt_message_id)
pending_replies: dict[int, int] = {}

# Роутер для всех обработчиков
router = Router()


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_user_info(message: Message) -> tuple[int, Optional[str], Optional[str]]:
    """Получить информацию о пользователе"""
    if not message.from_user:
        raise RuntimeError("Пользователь не найден")

    user = message.from_user
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
@router.message(CommandStart())
async def start_command(message: Message) -> None:
    """Обработчик команды /start от клиента"""
    if not message.from_user or not settings or not db or not bot:
        return

    user_id, username, full_name = get_user_info(message)

    # Если это админ, показываем меню
    if message.chat.id == settings.admin_chat_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Все клиенты", callback_data="clients_list")],
        ])
        await message.answer(
            "👋 Привет, Админ!\n\n"
            "Доступные команды:\n"
            "/clients - Список клиентов\n"
            "/history <user_id> - История с клиентом\n\n"
            "Или используйте меню ниже:",
            reply_markup=keyboard
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
    await message.answer(
        "👋 Здравствуйте!\n\n"
        "Я бот для связи с поддержкой. Напишите ваш вопрос, "
        "и я передам его оператору. Скоро вам ответят!"
    )

    # Уведомляем админа
    display_name = get_user_display_name(user_id, username, full_name)
    await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"🆕 Новый пользователь: {display_name} (ID: {user_id})\nОтправил команду /start",
    )


def is_client_message(message: Message) -> bool:
    """Проверка, что это сообщение от клиента (не от админа)"""
    return settings is None or message.chat.id != settings.admin_chat_id


@router.message(
    F.chat.type == "private",
    ~F.text.startswith("/"),
    is_client_message
)
async def handle_client_message(message: Message) -> None:
    """Обработчик сообщений от клиентов"""
    if not message.from_user or not settings or not db or not bot:
        return

    user_id, username, full_name = get_user_info(message)

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

    # Создаем кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply:{user_id}")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"history:{user_id}")]
    ])

    # Отправляем уведомление админу
    if message_type == "text":
        await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"💬 Сообщение от {display_name}\nID: {user_id}\n\n{content}",
            reply_markup=keyboard,
        )
    else:
        # Сначала отправляем заголовок
        await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"💬 Сообщение от {display_name}\nID: {user_id}\nТип: {message_type}",
            reply_markup=keyboard,
        )

        # Затем пересылаем само сообщение
        try:
            await message.copy_to(settings.admin_chat_id)
        except Exception as e:
            logger.error(f"Не удалось переслать медиа: {e}")

    logger.info(f"Получено сообщение от клиента {user_id} ({message_type})")


# ===== ОБРАБОТЧИКИ КНОПОК =====
@router.callback_query(F.data.startswith("reply:"))
async def button_reply(callback: CallbackQuery) -> None:
    """
    Обработчик нажатия кнопки "Ответить"
    Показывает приглашение написать ответ
    """
    if not callback.data or not settings or not bot:
        return

    await callback.answer()

    # Извлекаем user_id из callback_data
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Убираем кнопки с исходного сообщения
    try:
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Отправляем приглашающее сообщение
    prompt_msg = await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"✍️ Ответ для клиента ID: {user_id}\n\n"
             "Напишите следующее текстовое сообщение в этом чате, "
             "чтобы отправить его клиенту.",
    )

    # Сохраняем информацию о том, что ждём ответ для этого клиента
    pending_replies[settings.admin_chat_id] = prompt_msg.message_id
    # Также сохраняем user_id в глобальном состоянии
    global current_reply_user_id
    current_reply_user_id = user_id

    logger.info(f"Админ начал отвечать клиенту {user_id}")


@router.callback_query(F.data.startswith("history:"))
async def button_history(callback: CallbackQuery) -> None:
    """Обработчик нажатия кнопки "История" """
    if not callback.data or not settings or not db or not bot:
        return

    await callback.answer()

    # Извлекаем user_id из callback_data
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Получаем историю
    history = db.get_history(user_id, limit=20)

    if not history:
        await bot.send_message(
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

    await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=history_text,
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"Показана история для клиента {user_id}")


@router.callback_query(F.data == "clients_list")
async def button_clients_list(callback: CallbackQuery) -> None:
    """Обработчик кнопки "Все клиенты" - показывает список всех клиентов"""
    if not callback.message or not settings or not db or not bot:
        return

    await callback.answer()

    clients = db.list_clients()

    if not clients:
        await callback.message.answer("📋 Клиентов пока нет")
        return

    # Формируем список клиентов с кнопками
    lines = ["👥 <b>Список клиентов:</b>\n"]

    for user_id, username, full_name, last_message in clients[:20]:
        display_name = get_user_display_name(user_id, username, full_name)
        lines.append(
            f"• {html.escape(display_name)}\n"
            f"  ID: <code>{user_id}</code>\n"
            f"  Последнее сообщение: {last_message}\n"
        )

    # Создаем кнопки для каждого клиента (по 2 кнопки на ряд)
    keyboard = []
    for user_id, username, full_name, _ in clients[:20]:
        display_name = get_user_display_name(user_id, username, full_name)
        # Ограничиваем длину имени для кнопки
        short_name = display_name[:20] + "..." if len(display_name) > 20 else display_name

        keyboard.append([
            InlineKeyboardButton(
                text=f"📜 {short_name}",
                callback_data=f"client_detail:{user_id}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )

    logger.info(f"Показан список из {len(clients)} клиентов через кнопку")


@router.callback_query(F.data.startswith("client_detail:"))
async def button_client_detail(callback: CallbackQuery) -> None:
    """Обработчик кнопки с деталями клиента - показывает меню действий"""
    if not callback.data or not callback.message or not settings or not db or not bot:
        return

    await callback.answer()

    # Извлекаем user_id из callback_data
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Получаем информацию о клиенте
    clients = db.list_clients()
    client_info = None
    for cid, username, full_name, last_message in clients:
        if cid == user_id:
            client_info = (username, full_name, last_message)
            break

    if not client_info:
        await callback.message.answer(f"❌ Клиент {user_id} не найден")
        return

    username, full_name, last_message = client_info
    display_name = get_user_display_name(user_id, username, full_name)

    # Создаем меню действий для этого клиента
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📜 История", callback_data=f"history:{user_id}"),
            InlineKeyboardButton(text="✉️ Написать", callback_data=f"write:{user_id}")
        ],
        [InlineKeyboardButton(text="« Назад к списку", callback_data="clients_list")]
    ])

    await callback.message.answer(
        f"👤 <b>Клиент:</b> {html.escape(display_name)}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📧 <b>Username:</b> {f'@{username}' if username else 'не указан'}\n"
        f"📝 <b>Имя:</b> {html.escape(full_name) if full_name else 'не указано'}\n"
        f"🕐 <b>Последняя активность:</b> {last_message}\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

    logger.info(f"Показана детальная информация о клиенте {user_id}")


@router.callback_query(F.data.startswith("write:"))
async def button_write(callback: CallbackQuery) -> None:
    """
    Обработчик кнопки "Написать" - инициирует режим отправки сообщения клиенту
    """
    if not callback.data or not callback.message or not settings or not bot:
        return

    await callback.answer()

    # Извлекаем user_id из callback_data
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: неверный ID пользователя", show_alert=True)
        return

    # Отправляем приглашающее сообщение
    prompt_msg = await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"✍️ Напишите сообщение для клиента ID: {user_id}\n\n"
             "Следующее текстовое сообщение в этом чате будет отправлено клиенту.",
    )

    # Сохраняем информацию о том, что ждём ответ для этого клиента
    pending_replies[settings.admin_chat_id] = prompt_msg.message_id
    # Также сохраняем user_id в глобальном состоянии
    global current_reply_user_id
    current_reply_user_id = user_id

    logger.info(f"Админ начал писать сообщение клиенту {user_id}")


# ===== ОБРАБОТЧИКИ СООБЩЕНИЙ АДМИНА =====
current_reply_user_id: Optional[int] = None


def is_admin_chat(message: Message) -> bool:
    """Проверка, что сообщение от админа"""
    return settings is not None and message.chat.id == settings.admin_chat_id


@router.message(
    is_admin_chat,
    F.text,
    ~F.text.startswith("/")
)
async def handle_admin_message(message: Message) -> None:
    """Обработчик текстовых сообщений от админа (ответы клиентам)"""
    if not settings or not db or not bot:
        return

    global current_reply_user_id

    # Проверяем, есть ли ожидающий ответ
    if settings.admin_chat_id not in pending_replies or current_reply_user_id is None:
        return

    user_id = current_reply_user_id
    prompt_message_id = pending_replies[settings.admin_chat_id]

    # Получаем текст ответа
    reply_text = message.text
    if not reply_text:
        await message.answer("❌ Ответ должен содержать текст")
        return

    try:
        # Отправляем сообщение клиенту
        await bot.send_message(
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
            await bot.delete_message(
                chat_id=settings.admin_chat_id,
                message_id=prompt_message_id,
            )
        except Exception as e:
            logger.debug(f"Не удалось удалить приглашение: {e}")

        # Показываем подтверждение
        await message.answer(
            f"✅ Сообщение отправлено клиенту {user_id}"
        )

        # Очищаем состояние
        del pending_replies[settings.admin_chat_id]
        current_reply_user_id = None

        logger.info(f"Админ отправил ответ клиенту {user_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки сообщения клиенту: {e}")
        await message.answer(
            f"❌ Ошибка отправки сообщения: {e}"
        )


# ===== КОМАНДЫ АДМИНА =====
@router.message(
    Command("clients"),
    is_admin_chat
)
async def clients_command(message: Message) -> None:
    """Команда /clients - показать список клиентов"""
    if not settings or not db:
        return

    clients = db.list_clients()

    if not clients:
        await message.answer("📋 Клиентов пока нет")
        return

    # Формируем список клиентов
    lines = ["👥 <b>Список клиентов:</b>\n"]
    keyboard = []

    for user_id, username, full_name, last_message in clients[:20]:
        display_name = get_user_display_name(user_id, username, full_name)
        lines.append(
            f"• {html.escape(display_name)}\n"
            f"  ID: <code>{user_id}</code>\n"
            f"  Последнее сообщение: {last_message}\n"
        )

        # Ограничиваем длину имени для кнопки
        short_name = display_name[:15] + "..." if len(display_name) > 15 else display_name

        # Добавляем кнопки для быстрого доступа (История и Написать)
        keyboard.append([
            InlineKeyboardButton(
                text=f"📜 {short_name}",
                callback_data=f"history:{user_id}"
            ),
            InlineKeyboardButton(
                text="✉️",
                callback_data=f"write:{user_id}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )

    logger.info(f"Показан список из {len(clients)} клиентов")


@router.message(
    Command("history"),
    is_admin_chat
)
async def history_command(message: Message) -> None:
    """Команда /history <user_id> - показать историю с клиентом"""
    if not settings or not db or not message.text:
        return

    # Парсим аргументы из текста команды
    parts = message.text.split()

    # Проверяем аргументы
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /history <user_id> [лимит]\n"
            "Пример: /history 123456789 50"
        )
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом")
        return

    # Получаем лимит (по умолчанию 20)
    limit = 20
    if len(parts) >= 3:
        try:
            limit = max(1, min(100, int(parts[2])))
        except ValueError:
            pass

    # Получаем историю
    history = db.get_history(user_id, limit)

    if not history:
        await message.answer(
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

    await message.answer(
        history_text,
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"Показана история для клиента {user_id} ({len(history)} сообщений)")


# ===== ГЛАВНАЯ ФУНКЦИЯ =====
async def main() -> None:
    """Главная функция запуска бота"""
    global settings, db, bot

    # Загружаем настройки
    settings = Settings.load()

    # Инициализируем базу данных
    db = Database(settings.database_path)

    logger.info("✅ Настройки загружены")
    logger.info(f"✅ База данных: {settings.database_path}")
    logger.info(f"✅ Админ ID: {settings.admin_chat_id}")

    # Создаем бота и диспетчер
    bot = Bot(token=settings.token)
    dp = Dispatcher()

    # Регистрируем роутер
    dp.include_router(router)

    # Запускаем бота
    logger.info("🚀 Бот запущен и готов к работе!")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("👋 Бот остановлен")
