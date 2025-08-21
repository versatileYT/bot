#!/usr/bin/env python3
"""
Wrocław "Bez Kolejki" auto-booking bot (Playwright, Python).
Полностью автоматическое бронирование с ручным вводом капчи через Telegram.
Адаптировано для Railway.
"""
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from typing import Optional
import os
import requests
from playwright.async_api import async_playwright, Page, BrowserContext

# -------- Настройки через ENV --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
USER_EMAIL = os.getenv("USER_EMAIL", "")
USER_PESAL = os.getenv("USER_PESEL", "")
OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 60))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
BOOK_ASAP = True

# --------- Telegram ---------
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

# --------- Основные классы ---------
@dataclass
class FoundSlot:
    date_str: str
    time_str: str

USER_DATA = {
    "PESEL": USER_PESAL,
    "Email": USER_EMAIL,
}

# --------- Вспомогательные функции ---------
async def goto_home(page: Page):
    await page.goto("https://bez-kolejki.um.wroc.pl", timeout=25_000)
    # Кнопки акцепта правил/куки
    for txt in ["AKCEPTUJĘ", "akceptuj", "Akceptuj"]:
        try:
            btn = page.locator(f"div:has-text('{txt}')").first
            await btn.click(timeout=5000)
        except Exception:
            pass

async def click_dalej(page: Page) -> bool:
    try:
        await page.locator("button:has-text('DALEJ'):not([disabled])").first.click(timeout=30_000)
        return True
    except Exception:
        return False

async def select_office_and_service(page: Page):
    await page.get_by_text(OFFICE_TEXT, exact=False).first.click(timeout=30_000)
    await click_dalej(page)
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
            return t
    return None

async def fill_email_and_pesel(page):
    try:
        await page.get_by_label("E-mail *").fill(USER_DATA["Email"])
        await page.get_by_label("5 ostatnich znaków PESEL lub numeru paszportu *").fill(USER_DATA["PESEL"])
    except Exception as e:
        print("❌ Ошибка автозаполнения Email/PESEL:", e)

# --------- Основной запуск ---------
async def run_once(context: BrowserContext) -> Optional[FoundSlot]:
    page = await context.new_page()
    try:
        await goto_home(page)
        await select_office_and_service(page)
        date_str = await choose_first_available_date(page)
        if not date_str:
            send_telegram("⚠️ Доступных дат нет, повтор через минуту...")
            await page.close()
            return None
        time_str = await choose_first_available_time(page)
        if not time_str:
            send_telegram("⚠️ Доступного времени нет, повтор через минуту...")
            await page.close()
            return None
        slot = FoundSlot(date_str=date_str, time_str=time_str)
        if BOOK_ASAP:
            await fill_email_and_pesel(page)
        return slot
    except Exception as e:
        print("❌ Ошибка run_once:", e)
        await page.close()
        return None

async def main():
    send_telegram("🟢 Бот Bez Kolejki запущен!")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        while True:
            try:
                slot = await run_once(browser)
                if not slot:
                    await asyncio.sleep(CHECK_INTERVAL_SEC)
                    continue
                else:
                    send_telegram(f"✅ Найден слот: {slot.date_str} {slot.time_str}")
                    await asyncio.sleep(CHECK_INTERVAL_SEC)
            except Exception as e:
                print("❌ Ошибка основного цикла:", e)
                await asyncio.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    asyncio.run(main())
