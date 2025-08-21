import os
import asyncio
import re
import requests
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------- Переменные окружения ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

USER_DATA = {
    "PESEL": os.getenv("USER_PESEL"),
    "Email": os.getenv("USER_EMAIL"),
}

OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")

ACTION_TIMEOUT_MS = 30_000
CHECK_INTERVAL_SEC = 60
BOOK_ASAP = True
# ------------------------------------------

@dataclass
class FoundSlot:
    date_str: str
    time_str: str

# Флаг работы
is_running = False  

# ---------- Telegram Helper ----------
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("❌ Telegram error:", e)

# ---------- Bot Logic ----------
async def goto_home(page: Page):
    await page.goto("https://bez-kolejki.um.wroc.pl", timeout=25_000)
    try:
        await page.locator("div:has-text('AKCEPTUJĘ')").first.click(timeout=5000, force=True)
    except Exception:
        pass

async def click_dalej(page: Page) -> bool:
    try:
        btn = page.locator("button:has-text('DALEJ'):not([disabled])").first
        await btn.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
        await btn.click(force=True)
        return True
    except Exception:
        return False

async def select_office_and_service(page: Page):
    await page.get_by_text(OFFICE_TEXT, exact=False).first.click(force=True)
    await click_dalej(page)
    await page.get_by_text(SERVICE_TEXT, exact=False).first.click(force=True)
    await click_dalej(page)

async def choose_first_available_date(page: Page) -> Optional[str]:
    day_btns = page.get_by_role("button").filter(
        has_text=re.compile(r"^\s*(?:[1-9]|[12]\d|3[01])\s*$")
    )
    for _ in range(15):
        count = await day_btns.count()
        for i in range(count):
            el = day_btns.nth(i)
            disabled = await el.evaluate(
                "(el) => el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'"
            )
            if not disabled:
                txt = (await el.inner_text()).strip()
                await el.click(force=True)
                return txt
        await asyncio.sleep(0.2)
    return None

async def choose_first_available_time(page: Page) -> Optional[str]:
    time_btns = page.get_by_role("button").filter(has_text=re.compile(r"\b\d{1,2}:\d{2}\b"))
    try:
        await time_btns.first.wait_for(state="visible", timeout=10_000)
    except:
        return None
    for i in range(await time_btns.count()):
        el = time_btns.nth(i)
        disabled = await el.evaluate(
            "(el) => el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'"
        )
        if not disabled:
            t = (await el.inner_text()).strip()
            await el.click(force=True)
            return t
    return None

async def run_once(browser: Browser) -> Optional[FoundSlot]:
    context = await browser.new_context()
    page = await context.new_page()
    try:
        await goto_home(page)
        await select_office_and_service(page)

        date_str = await choose_first_available_date(page)
        if not date_str:
            await page.close()
            await context.close()
            return None

        time_str = await choose_first_available_time(page)
        if not time_str:
            await page.close()
            await context.close()
            return None

        slot = FoundSlot(date_str=date_str, time_str=time_str)
        await page.close()
        await context.close()
        return slot
    except Exception as e:
        print("❌ Ошибка run_once:", e)
        await page.close()
        await context.close()
        return None

# ---------- Telegram Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if is_running:
        await update.message.reply_text("⚠️ Проверка уже запущена!")
        return

    is_running = True
    await update.message.reply_text("🤖 Бот запущен! Начинаю проверку дат...")
    send_telegram("Бот запущен по команде /start ✅")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        while is_running:
            slot = await run_once(browser)
            if slot:
                msg = f"✅ Найден слот: {slot.date_str} {slot.time_str}"
                await update.message.reply_text(msg)
                send_telegram(msg)
            else:
                msg = "⏳ Нет доступных дат, проверим снова через минуту..."
                await update.message.reply_text(msg)
            await asyncio.sleep(CHECK_INTERVAL_SEC)
        await browser.close()

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if not is_running:
        await update.message.reply_text("⚠️ Проверка не запущена.")
        return

    is_running = False
    await update.message.reply_text("⛔ Проверка остановлена.")
    send_telegram("Бот остановлен по команде /stop ❌")

# ---------- Main ----------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.run_polling()

if __name__ == "__main__":
    main()
