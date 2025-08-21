#!/usr/bin/env python3
"""
Wrocław "Bez Kolejki" auto-booking bot (Playwright, Python).
Запуск бота без проверки, проверка начинается только после /start.
"""
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from typing import Optional
import os
import subprocess
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

# -------- Настройки через ENV --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
USER_EMAIL = os.getenv("USER_EMAIL", "")
USER_PESEL = os.getenv("USER_PESEL", "")
OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 60))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
BOOK_ASAP = True

# ===== Глобальное состояние =====
@dataclass
class FoundSlot:
    date_str: str
    time_str: str

state = {
    "running": False,
    "bg_task": None,
    "browser_context": None
}

USER_DATA = {"PESEL": USER_PESEL, "Email": USER_EMAIL}

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

# -------- Playwright установка ---------
def ensure_playwright_browsers():
    """Скачиваем Chromium автоматически на Railway."""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        send_telegram("✅ Chromium установлен автоматически.")
    except Exception as e:
        send_telegram(f"❌ Ошибка установки Chromium: {e}")

# -------- Проверка сайта ---------
async def goto_home(page: Page):
    send_telegram("🌐 Переходим на главную страницу...")
    await page.goto("https://bez-kolejki.um.wroc.pl", timeout=25_000)
    for txt in ["AKCEPTUJĘ", "akceptuj", "Akceptuj"]:
        try:
            btn = page.locator(f"div:has-text('{txt}')").first
            await btn.click(timeout=5000)
            send_telegram(f"✅ Нажата кнопка '{txt}'")
        except Exception:
            pass

async def click_dalej(page: Page) -> bool:
    try:
        await page.locator("button:has-text('DALEJ'):not([disabled])").first.click(timeout=30_000)
        send_telegram("➡️ Нажата кнопка DALEJ")
        return True
    except Exception:
        return False

async def select_office_and_service(page: Page):
    send_telegram(f"🏢 Выбираем офис: {OFFICE_TEXT}")
    await page.get_by_text(OFFICE_TEXT, exact=False).first.click(timeout=30_000)
    await click_dalej(page)
    send_telegram(f"🛎 Выбираем услугу: {SERVICE_TEXT}")
    await page.get_by_text(SERVICE_TEXT, exact=False).first.click(timeout=30_000)
    await click_dalej(page)

async def choose_first_available_date(page: Page) -> Optional[str]:
    day_btns = page.get_by_role("button").filter(
        has_text=re.compile(r"^\s*(?:[1-9]|[12]\d|3[01])\s*$")
    )
    for _ in range(25):
        count = await day_btns.count()
        for i in range(count):
            el = day_btns.nth(i)
            disabled = await el.evaluate(
                "(el) => el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'"
            )
            if not disabled:
                txt = (await el.inner_text()).strip()
                await el.click()
                send_telegram(f"📅 Выбрана дата: {txt}")
                return txt
        await asyncio.sleep(0.2)
    return None

async def choose_first_available_time(page: Page) -> Optional[str]:
    time_btns = page.get_by_role("button").filter(
        has_text=re.compile(r"\b\d{1,2}:\d{2}\b")
    )
    await time_btns.first.wait_for(state="visible", timeout=10_000)
    for i in range(await time_btns.count()):
        el = time_btns.nth(i)
        disabled = await el.evaluate(
            "(el) => el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'"
        )
        if not disabled:
            t = (await el.inner_text()).strip()
            await el.click()
            send_telegram(f"⏰ Выбрано время: {t}")
            return t
    return None

async def fill_email_and_pesel(page):
    try:
        await page.get_by_label("E-mail *").fill(USER_DATA["Email"])
        await page.get_by_label("5 ostatnich znaków PESEL lub numeru paszportu *").fill(USER_DATA["PESEL"])
        send_telegram("✅ Email и PESEL заполнены")
    except Exception as e:
        send_telegram(f"❌ Ошибка автозаполнения Email/PESEL: {e}")

async def run_once(context: BrowserContext) -> Optional[FoundSlot]:
    page = await context.new_page()
    try:
        await goto_home(page)
        await select_office_and_service(page)

        date_str = await choose_first_available_date(page)
        if not date_str:
            send_telegram("⚠️ Доступных дат нет.")
            await page.close()
            return None

        time_str = await choose_first_available_time(page)
        if not time_str:
            send_telegram("⚠️ Доступного времени нет.")
            await page.close()
            return None

        await fill_email_and_pesel(page)
        slot = FoundSlot(date_str=date_str, time_str=time_str)
        return slot
    except Exception as e:
        send_telegram(f"❌ Ошибка run_once: {e}")
        await page.close()
        return None

# -------- Команды Telegram ---------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if state["running"]:
        await update.message.reply_text("Проверка уже запущена.")
        return
    await update.message.reply_text("Запускаю проверку дат...")
    state["running"] = True

    async def checker_task():
        ensure_playwright_browsers()  # Скачиваем Chromium
        async with async_playwright() as p:
            state["browser_context"] = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
            while state["running"]:
                try:
                    slot = await run_once(state["browser_context"])
                    if slot:
                        send_telegram(f"✅ Найден слот: {slot.date_str} {slot.time_str}")
                    await asyncio.sleep(CHECK_INTERVAL_SEC)
                except Exception as e:
                    send_telegram(f"❌ Ошибка основного цикла: {e}")
                    await asyncio.sleep(CHECK_INTERVAL_SEC)

    state["bg_task"] = asyncio.create_task(checker_task())

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
    await asyncio.Event().wait()  # держим приложение живым

if __name__ == "__main__":
    asyncio.run(main())
