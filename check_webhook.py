#!/usr/bin/env python3
"""
Скрипт для проверки и удаления webhook у бота
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не найден в .env")
    exit(1)

print(f"🔍 Проверяю webhook для бота...")

# Получаем информацию о webhook
url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
response = requests.get(url)
data = response.json()

if data.get("ok"):
    webhook_info = data.get("result", {})
    webhook_url = webhook_info.get("url", "")

    print(f"\n📋 Информация о webhook:")
    print(f"   URL: {webhook_url if webhook_url else '(не установлен)'}")
    print(f"   Pending updates: {webhook_info.get('pending_update_count', 0)}")

    if webhook_url:
        print(f"\n⚠️  ПРОБЛЕМА НАЙДЕНА! Webhook установлен: {webhook_url}")
        print(f"   Это блокирует получение updates через polling!")

        answer = input("\n❓ Удалить webhook? (y/n): ")

        if answer.lower() == 'y':
            delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
            delete_response = requests.post(delete_url)

            if delete_response.json().get("ok"):
                print("✅ Webhook успешно удален!")
                print("   Теперь перезапустите бота: sudo systemctl restart client-bot.service")
            else:
                print(f"❌ Ошибка удаления webhook: {delete_response.text}")
        else:
            print("❌ Webhook НЕ удален. Бот не будет получать callback queries!")
    else:
        print("\n✅ Webhook не установлен. Проблема в другом.")
        print("\nДругие возможные причины:")
        print("1. Вы нажимаете кнопки НЕ под тем Telegram аккаунтом, который указан в ADMIN_CHAT_ID")
        print("2. У бота несколько экземпляров с разными токенами")
        print("3. Проблема с правами Telegram бота")
else:
    print(f"❌ Ошибка API: {data}")
