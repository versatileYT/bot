#!/usr/bin/env python3
"""
Telegram-бот для проверки дат.
Команды:
  /start  — старт проверки
  /stop   — остановка проверки
Бот пишет все действия в чат.
"""

import os
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from checker import run_checker_forever, CheckerSettings, CheckerState

# --------- ENV ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
USER_EMAIL  = os.getenv("USER_EMAIL", "")
USER_PESEL  = os.getenv("USER_PESEL", "")
OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
# ------------------------

# ===== Глобальное состояние =====
class GlobalState:
    running: bool = False
    bg_task: asyncio.Task | None = None
    checker_state: CheckerState = CheckerState()

state = GlobalState()

# ===== Telegram =====
async def send_message(text: str):
    """Отправка сообщения в TG и лог в консоль."""
    print(text)
    if TELEGRAM_TOKEN and CHAT_ID:
        import requests
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": text},
                timeout=10,
            )
        except Exception as e:
            print("Telegram notify error:", e)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if state.running:
        await update.message.reply_text("Проверка уже запущена.")
        return

    await update.message.reply_text("Запускаю проверку дат...")
    state.running = True

    settings = CheckerSettings(
        telegram_token=TELEGRAM_TOKEN,
        chat_id=CHAT_ID,
        user_email=USER_EMAIL,
        user_pesel=USER_PESEL,
        office_text=OFFICE_TEXT,
        service_text=SERVICE_TEXT,
        check_interval_sec=CHECK_INTERVAL_SEC,
        headless=HEADLESS,
    )

    async def runner():
        try:
            await run_checker_forever(settings, state.checker_state, stop_flag=lambda: not state.running)
        except Exception as e:
            await send_message(f"❌ Критическая ошибка: {e}")
        finally:
            state.running = False
            await send_message("Проверка остановлена.")

    state.bg_task = asyncio.create_task(runner())

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state.running:
        await update.message.reply_text("Проверка не запущена.")
        return
    state.running = False
    await update.message.reply_text("Остановка проверки...")

# ===== Main =====
async def main():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN не задан!")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))

    print("Запуск Telegram бота...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()  # держим приложение живым

if __name__ == "__main__":
    asyncio.run(main())
