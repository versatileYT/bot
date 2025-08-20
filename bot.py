#!/usr/bin/env python3
"""
Wrocław "Bez Kolejki" auto-booking bot (Playwright, Python).
Полностью автоматическое бронирование с ручным вводом капчи через Telegram.
"""
from __future__ import annotations
import asyncio
import random
import re
from dataclasses import dataclass
from typing import Optional
import requests
from playwright.async_api import async_playwright, Page, BrowserContext
import io
from PIL import Image

# -------- Настройки --------
TELEGRAM_TOKEN = "8290373098:AAHKBnt2jpz16cclCxWJj2r6NnrbsVhyIBw"
CHAT_ID = "712297341"

USER_DATA = {
    "PESEL": "22399",
    "Email": "vladsil82@gmail.com",
}

OFFICE_TEXT = "USC przy ul. Włodkowica 20"
SERVICE_TEXT = "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu"

CHECK_INTERVAL_SEC = (30, 60)
ACTION_TIMEOUT_MS = 30_000
BOOK_ASAP = True
# ----------------------------

@dataclass
class FoundSlot:
    date_str: str
    time_str: str

# ---------- Telegram ----------
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
    """Отправляет фото и возвращает ID последнего сообщения"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": ("captcha.png", image_bytes)}
    data = {"chat_id": CHAT_ID, "caption": caption}
    try:
        resp = requests.post(url, files=files, data=data, timeout=10).json()
        if resp.get("ok"):
            print("✅ Скрин капчи отправлен в Telegram")
            return resp["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки скрина: {resp}")
            return None
    except Exception as e:
        print("❌ Не удалось отправить скрин:", e)
        return None

def get_new_telegram_message(last_id: int) -> Optional[str]:
    """Возвращает текст нового сообщения в Telegram после last_id"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, timeout=10).json()
        if "result" in resp and resp["result"]:
            for update in reversed(resp["result"]):
                msg = update.get("message")
                if msg and str(msg.get("chat", {}).get("id")) == CHAT_ID:
                    if msg.get("message_id", 0) > last_id:
                        text = msg.get("text")
                        if text:
                            return text.strip()
        return None
    except Exception as e:
        print("❌ Ошибка при получении Telegram сообщений:", e)
        return None
# -----------------------------

async def goto_home(page: Page):
    await page.goto("https://bez-kolejki.um.wroc.pl", timeout=25_000)
    await accept_rules(page)
    try:
        akceptuj_btn = page.locator("div:has-text('AKCEPTUJĘ')").first
        await akceptuj_btn.click(timeout=5000)
    except Exception:
        pass
    try:
        cookies_btn = page.locator("div:has-text('akceptuj')").first
        await cookies_btn.click(timeout=5000)
    except Exception:
        pass

async def accept_rules(page: Page):
    while True:
        try:
            btn = await page.query_selector("button:has-text('Akceptuj')")
            if btn:
                await btn.click()
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)

async def click_dalej(page: Page) -> bool:
    try:
        await page.locator("button:has-text('DALEJ'):not([disabled])").first.click(timeout=ACTION_TIMEOUT_MS)
        return True
    except Exception:
        return False

async def select_office_and_service(page: Page):
    await page.get_by_text(OFFICE_TEXT, exact=False).first.click(timeout=ACTION_TIMEOUT_MS)
    await click_dalej(page)
    await page.get_by_text(SERVICE_TEXT, exact=False).first.click(timeout=ACTION_TIMEOUT_MS)
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
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.1)
                await page.keyboard.press("Enter")
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
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.1)
            await page.keyboard.press("Enter")
            return t
    return None

async def fill_email_and_pesel(page):
    try:
        await page.get_by_label("E-mail *").fill(USER_DATA["Email"])
        await page.get_by_label("5 ostatnich znaków PESEL lub numeru paszportu *").fill(USER_DATA["PESEL"])
    except Exception as e:
        print("❌ Ошибка при автозаполнении Email/PESEL:", e)

async def wait_for_captcha_input(page: Page) -> Optional[str]:
    """Отправляем скрин капчи в Telegram и ждём твой ввод"""
    captcha_img = await page.locator("div.captcha-div img").screenshot()
    last_msg_id = send_photo_telegram(captcha_img, caption="Введите капчу и пришлите текст сюда")
    if not last_msg_id:
        print("❌ Не удалось отправить скрин капчи")
        return None
    print("⌛ Ждём твой ввод капчи в Telegram...")
    solution = None
    while solution is None:
        await asyncio.sleep(3)
        solution = get_new_telegram_message(last_msg_id)
    return solution

async def submit(page: Page, slot: FoundSlot):
    send_telegram(f"🚀 Дата {slot.date_str} {slot.time_str} выбрана. Скрин капчи отправлен в Telegram...")
    await fill_email_and_pesel(page)
    solution = await wait_for_captcha_input(page)
    if solution:
        try:
            # старый вариант: captcha_input = await page.query_selector("#input-127")
            # новый, универсальный поиск поля капчи:
            captcha_img = page.locator("div.captcha-div img")
            captcha_input = await captcha_img.locator("xpath=following::input[1]").element_handle()

            if captcha_input:
                await captcha_input.fill(solution)
                await page.get_by_role("button", name="Wyślij").click()
                send_telegram("✅ Данные отправлены после ввода капчи!")
            else:
                print("⚠️ Поле ввода капчи не найдено")
        except Exception as e:
            print("❌ Ошибка при подстановке капчи:", e)

async def run_once(context: BrowserContext) -> Optional[FoundSlot]:
    page = await context.new_page()
    try:
        await goto_home(page)
        await select_office_and_service(page)

        date_str = await choose_first_available_date(page)
        if not date_str:
            send_telegram("⚠️ Доступных дат нет, повторяем через минуту...")
            await page.close()
            return None

        time_str = await choose_first_available_time(page)
        if not time_str:
            send_telegram("⚠️ Доступного времени нет, повторяем через минуту...")
            await page.close()
            return None

        slot = FoundSlot(date_str=date_str, time_str=time_str)
        if BOOK_ASAP:
            await submit(page, slot)

        return slot
    except Exception as e:
        print("Ошибка в run_once:", e)
        await page.close()
        return None

async def main():
    send_telegram("🟢 Бот для Bez Kolejki запущен!")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        while True:
            try:
                slot = await run_once(browser)
                if not slot:
                    print("⏳ Доступных дат нет. Ждем 60 секунд перед новой проверкой...")
                    await asyncio.sleep(60)
                    continue
                else:
                    print(f"✅ Найден слот: {slot.date_str} {slot.time_str}")
                    # Можно снять следующую строку, чтобы бот продолжал работать и после нахождения слота
                    # await asyncio.sleep(60)  # или продолжить проверку через цикл
            except Exception as e:
                print("❌ Ошибка в основном цикле:", e)
                await asyncio.sleep(60)



if __name__ == "__main__":
    asyncio.run(main())
