#!/usr/bin/env python3
"""
Wrocław "Bez Kolejki" auto-booking bot (Playwright, Python) для запуска через Telegram.
Работает на Railway или другом хостинге.
"""

from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from typing import Optional
import requests
import os
from playwright.async_api import async_playwright, Page, Browser
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# -------- Переменные окружения --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

USER_DATA = {
    "PESEL": os.getenv("USER_PESEL"),
    "Email": os.getenv("USER_EMAIL"),
}

OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")

ACTION_TIMEOUT_MS = 30_000
BOOK_ASAP = True
CHECK_INTERVAL_SEC = 60
# --------------------------------------

@dataclass
class FoundSlot:
    date_str: str
    time_str: str

# ---------- Telegram Helper ----------
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        if resp.status_code == 200:
            print("✅ Уведомление отправлено в Telegram")
        else:
            print(f"❌ Ошибка Telegram: {resp.status_code}, {resp.text}")
    except Exception as e:
        print("❌ Не удалось отправить Telegram:", e)

def send_photo_telegram(image_bytes: bytes, caption: str = "") -> Optional[int]:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": ("captcha.png", image_bytes)}
    data = {"chat_id": CHAT_ID, "caption": caption}
    try:
        resp = requests.post(url, files=files, data=data, timeout=10).json()
        if resp.get("ok"):
            print("✅ Скрин капчи отправлен в Telegram")
            return resp["result"]["message_id"]
    except Exception as e:
        print("❌ Не удалось отправить скрин:", e)
    return None

def get_new_telegram_message(last_id: int) -> Optional[str]:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, timeout=10).json()
        for update in reversed(resp.get("result", [])):
            msg = update.get("message")
            if msg and str(msg.get("chat", {}).get("id")) == CHAT_ID:
                if msg.get("message_id", 0) > last_id:
                    return msg.get("text", "").strip()
    except Exception as e:
        print("❌ Ошибка при получении Telegram сообщений:", e)
    return None

# ---------- Bot Logic ----------
async def goto_home(page: Page):
    await page.goto("https://bez-kolejki.um.wroc.pl", timeout=25_000)
    try:
        await page.locator("div:has-text('AKCEPTUJĘ')").first.click(timeout=5000, force=True)
    except Exception:
        pass
    try:
        await page.locator("div:has-text('akceptuj')").first.click(timeout=5000, force=True)
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
    for _ in range(25):
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
    await time_btns.first.wait_for(state="visible", timeout=10_000)
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

async def fill_email_and_pesel(page: Page):
    try:
        await page.get_by_label("E-mail *").fill(USER_DATA["Email"])
        await page.get_by_label("5 ostatnich znaków PESEL lub numeru paszportu *").fill(USER_DATA["PESEL"])
    except Exception as e:
        print("❌ Ошибка автозаполнения:", e)

async def wait_for_captcha_input(page: Page) -> Optional[str]:
    captcha_img = await page.locator("div.captcha-div img").screenshot()
    last_msg_id = send_photo_telegram(captcha_img, caption="Введите капчу")
    solution = None
    while solution is None:
        await asyncio.sleep(3)
        solution = get_new_telegram_message(last_msg_id)
    return solution

async def submit(page: Page, slot: FoundSlot):
    send_telegram(f"🚀 Дата {slot.date_str} {slot.time_str} выбрана.")
    await fill_email_and_pesel(page)
    solution = await wait_for_captcha_input(page)
    if solution:
        try:
            captcha_img = page.locator("div.captcha-div img")
            captcha_input = await captcha_img.locator("xpath=following::input[1]").element_handle()
            if captcha_input:
                await captcha_input.fill(solution)
                await page.get_by_role("button", name="Wyślij").click(force=True)
                send_telegram("✅ Данные отправлены после ввода капчи!")
        except Exception as e:
            print("❌ Ошибка при подстановке капчи:", e)

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
            send_telegram("⚠️ Доступных дат нет")
            return None

        time_str = await choose_first_available_time(page)
        if not time_str:
            await page.close()
            await context.close()
            send_telegram("⚠️ Доступного времени нет")
            return None

        slot = FoundSlot(date_str=date_str, time_str=time_str)
        if BOOK_ASAP:
            await submit(page, slot)

        await page.close()
        await context.close()
        return slot
    except Exception as e:
        print("❌ Ошибка в run_once:", e)
        await page.close()
        await context.close()
        return None

# ---------- Telegram Command Handlers ----------
async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Поиск слотов запущен!")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        while True:
            slot = await run_once(browser)
            if slot:
                await update.message.reply_text(f"✅ Найден слот: {slot.date_str} {slot.time_str}")
            else:
                await update.message.reply_text("⏳ Нет доступных дат, проверяем снова через минуту...")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

from telegram.ext import Application, CommandHandler

async def start(update, context):
    await update.message.reply_text("Бот запущен, начинаю поиск дат...")

def main():
    app = Application.builder().token("YOUR_TOKEN").build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()   # тут без await

if __name__ == "__main__":
    main()
