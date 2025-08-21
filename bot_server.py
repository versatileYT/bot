#!/usr/bin/env python3
"""
Wrocław "Bez Kolejki" auto-booking bot (HTTP, Python).
Проверка сайта без браузера. Проверка начинается после /start.
"""
import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# -------- Настройки через ENV --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
USER_EMAIL = os.getenv("USER_EMAIL", "")
USER_PESEL = os.getenv("USER_PESSEL", "")
OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 60))

state = {"running": False}

# -------- Telegram ---------
def send_telegram(text: str):
    print(text)
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": text},
                timeout=10,
            )
        except Exception as e:
            print("❌ Telegram error:", e)

# -------- Проверка сайта ---------
def check_site():
    url = "https://bez-kolejki.um.wroc.pl"
    send_telegram(f"🌐 Проверяем сайт: {url}")
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            send_telegram(f"❌ Ошибка доступа к сайту: {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Логируем доступность офисов
        send_telegram("📄 Смотрим доступные офисы и услуги...")
        offices = [el.text.strip() for el in soup.find_all("div") if OFFICE_TEXT in el.text]
        services = [el.text.strip() for el in soup.find_all("div") if SERVICE_TEXT in el.text]
        send_telegram(f"🏢 Офисы: {offices}")
        send_telegram(f"🛎 Услуги: {services}")

        # Проверка дат (грубо, ищем кнопки с числами)
        dates = [btn.text.strip() for btn in soup.find_all("button") if btn.text.strip().isdigit()]
        send_telegram(f"📅 Найденные даты: {dates}")
        if not dates:
            return None

        # Проверка времени (ищем кнопки с форматом HH:MM)
        times = []
        for btn in soup.find_all("button"):
            if ":" in btn.text and len(btn.text.strip()) == 5:
                times.append(btn.text.strip())
        send_telegram(f"⏰ Найденные времена: {times}")
        if not times:
            return None

        # Берем первую дату и время
        slot = {"date": dates[0], "time": times[0]}
        send_telegram(f"✅ Слот найден: {slot['date']} {slot['time']}")
        return slot

    except Exception as e:
        send_telegram(f"❌ Ошибка проверки сайта: {e}")
        return None

# -------- Команды Telegram ---------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if state["running"]:
        await update.message.reply_text("Проверка уже запущена.")
        return
    await update.message.reply_text("Запускаю проверку дат...")
    state["running"] = True

    async def loop_task():
        while state["running"]:
            check_site()
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    asyncio.create_task(loop_task())

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not state["running"]:
        await update.message.reply_text("Проверка не запущена.")
        return
    state["running"] = False
    await update.message.reply_text("Остановка проверки...")
    send_telegram("🛑 Проверка остановлена.")

# -------- Main ---------
async def main():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN не задан!")
        return
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    send_telegram("🟢 Бот запущен, ожидаем /start")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
