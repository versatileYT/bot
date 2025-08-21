from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from typing import Optional, Callable
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright, Page, Browser

# ===== Состояние для панели =====
class CheckerState:
    def __init__(self):
        self.last_check_iso: Optional[str] = None
        self.last_event: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_found_slot: Optional[dict] = None

    def mark_check(self):
        self.last_check_iso = datetime.now(timezone.utc).isoformat()

    def set_event(self, msg: str):
        self.last_event = msg

    def set_error(self, msg: str):
        self.last_error = msg

    def set_slot(self, date_str: str, time_str: str):
        self.last_found_slot = {"date": date_str, "time": time_str}


@dataclass
class CheckerSettings:
    telegram_token: str
    chat_id: str
    user_email: str
    user_pesel: str
    office_text: str
    service_text: str
    check_interval_sec: int = 60
    headless: bool = True


@dataclass
class FoundSlot:
    date_str: str
    time_str: str


def tg_send(token: str, chat_id: str, text: str):
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=8,
        )
    except Exception:
        pass


# ===== Playwright Helpers =====
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
        await btn.wait_for(state="visible", timeout=30_000)
        await btn.click(force=True)
        return True
    except Exception:
        return False


async def select_office_and_service(page: Page, office_text: str, service_text: str):
    await page.get_by_text(office_text, exact=False).first.click(force=True)
    await click_dalej(page)
    await page.get_by_text(service_text, exact=False).first.click(force=True)
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
            await el.click(force=True)
            return t
    return None


async def fill_email_and_pesel(page: Page, email: str, pesel: str):
    try:
        await page.get_by_label("E-mail *").fill(email)
        await page.get_by_label("5 ostatnich znaków PESEL lub numeru paszportu *").fill(pesel)
    except Exception:
        pass


async def run_once(browser: Browser, st: CheckerSettings, state: CheckerState) -> Optional[FoundSlot]:
    context = await browser.new_context()
    page = await context.new_page()
    try:
        await goto_home(page)
        await select_office_and_service(page, st.office_text, st.service_text)

        date_str = await choose_first_available_date(page)
        if not date_str:
            state.set_event("Доступных дат нет")
            await page.close()
            await context.close()
            return None

        time_str = await choose_first_available_time(page)
        if not time_str:
            state.set_event("Доступного времени нет")
            await page.close()
            await context.close()
            return None

        slot = FoundSlot(date_str=date_str, time_str=time_str)
        state.set_slot(slot.date_str, slot.time_str)
        return slot
    except Exception as e:
        state.set_error(f"run_once: {e}")
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await context.close()
        except Exception:
            pass


async def run_checker_forever(st: CheckerSettings, state: CheckerState, stop_flag: Callable[[], bool]):
    """
    Бесконечный цикл проверок. Останавливается, когда stop_flag() возвращает True.
    """
    state.set_event("Старт чекера")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=st.headless)
        while True:
            if stop_flag():
                state.set_event("Чекер остановлен")
                break

            state.mark_check()
            slot = await run_once(browser, st, state)

            if slot:
                msg = f"✅ Найден слот: {slot.date_str} {slot.time_str}"
                tg_send(st.telegram_token, st.chat_id, msg)
                state.set_event(msg)
                # здесь можно продолжать или сделать паузу
                await asyncio.sleep(st.check_interval_sec)
            else:
                msg = "⏳ Нет доступных дат, повтор через минуту"
                state.set_event(msg)
                await asyncio.sleep(st.check_interval_sec)

        try:
            await browser.close()
        except Exception:
            pass
