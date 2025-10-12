# Telegram бот для общения с клиентами

Этот проект содержит готового Telegram-бота, который позволяет работать с клиентами через один аккаунт, сохранять историю переписок и просматривать статистику по клиентам.

## Возможности

- Приём сообщений от клиентов и пересылка их администратору (владельцу бота).
- Ответ администратора клиенту прямо из Telegram командой `/reply`.
- Хранение полной истории переписок в SQLite базе данных.
- Просмотр списка клиентов и времени последнего контакта (`/clients`).
- Получение истории переписки с конкретным пользователем (`/history`).

## Подготовка

1. **Создайте бота** через [@BotFather](https://t.me/BotFather) и получите токен.
2. **Узнайте свой chat_id** (ID администратора). Самый простой способ — написать сообщение боту [@userinfobot](https://t.me/userinfobot).
3. На вашем VPS должны быть установлены Python 3.10+ и git.

## Установка на VPS

```bash
# Клонируем репозиторий
git clone https://example.com/your/bot_tg.git
cd bot_tg

# Создаем виртуальное окружение (опционально, но рекомендуется)
python3 -m venv .venv
source .venv/bin/activate

# Устанавливаем зависимости
pip install -r requirements.txt
```

Создайте файл `.env` в корне проекта и добавьте в него параметры окружения:

```env
BOT_TOKEN=ваш_токен_бота
ADMIN_CHAT_ID=123456789  # ваш Telegram ID
```

## Запуск

```bash
python bot.py
```

Бот начнет опрашивать Telegram API и будет доступен для клиентов. История сообщений сохраняется в `data/bot.db`.

### Автозапуск (systemd)

Создайте unit-файл `/etc/systemd/system/client-bot.service` со следующим содержимым (не забудьте скорректировать пути):

```ini
[Unit]
Description=Telegram client bot
After=network.target

[Service]
User=youruser
WorkingDirectory=/path/to/bot_tg
Environment="BOT_TOKEN=ваш_токен" "ADMIN_CHAT_ID=123456789"
ExecStart=/path/to/bot_tg/.venv/bin/python /path/to/bot_tg/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

После этого выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable client-bot.service
sudo systemctl start client-bot.service
```

## Использование

- Клиент начинает диалог командой `/start` или просто пишет сообщение боту.
- Вы получите уведомление в личном чате с ID клиента и текстом сообщения.
- Ответьте командой `/reply <user_id> <сообщение>`, чтобы клиент получил ваш ответ.
- Посмотреть список клиентов: `/clients`.
- Просмотреть историю переписки: `/history <user_id> [количество_сообщений]`.

## Расширение

- При необходимости можно добавить поддержку дополнительных типов сообщений (например, пересылка файлов от администратора).
- Базу данных SQLite легко заменить на PostgreSQL или другую СУБД, расширив модуль `database.py`.
