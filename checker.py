import asyncio
from datetime import datetime
from typing import Callable
from playwright.async_api import async_playwright

class CheckerSettings:
    def __init__(self, telegram_token, chat_id, user_email, user_pesel,
                 office_text, service_text, check_interval_sec=60, headless=True):
        self.telegram_token = telegram_token
        self.chat_id = chat_id
        self.user_email = user_email
        self.user_pesel = user_pesel
        self.office_text = office_text
        self.service_text = service_text
        self.check_interval_sec = check_interval_sec
        self.headless = headless

class CheckerState:
    def __init__(self):
        self.last_event = ""
        self.last_error = ""
        self.last_check_iso = ""
        self.last_found_slot = ""

    def set_event(self, text: str):
        self.last_event = text
        self.last_check_iso = datetime.now().isoformat()

    def set_error(self, text: str):
        self.last_error = text
        self.last_check_iso = datetime.now().isoformat()

async def send_message(token, chat_id, text):
    import requests
    print(text)
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            print("Telegram notify error:", e)

async def run_checker_forever(settings: CheckerSettings, state: CheckerState, stop_flag: Callable[[], bool]):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.headless)
        context = await browser.new_context()
        page = await context.new_page()

        await send_message(settings.telegram_token, settings.chat_id, "Бот стартовал, начинаем проверки.")

        while not stop_flag():
            try:
                state.set_event("Начало нового цикла проверки")
                await send_message(settings.telegram_token, settings.chat_id, "Захожу на сайт bez-kolejki.um.wroc.pl...")
                await page.goto("https://bez-kolejki.um.wroc.pl", timeout=60000)

                # Выбор офиса
                await page.select_option("select[name='office']", label=settings.office_text)
                await send_message(settings.telegram_token, settings.chat_id, f"Выбран офис: {settings.office_text}")

                # Выбор услуги
                await page.select_option("select[name='service']", label=settings.service_text)
                await send_message(settings.telegram_token, settings.chat_id, f"Выбрана услуга: {settings.service_text}")

                # Жмём кнопку "Sprawdź terminy"
                await page.click("button:has-text('Sprawdź terminy')")
                await page.wait_for_timeout(2000)  # ждём обновления страницы

                # Проверяем наличие дат
                dates = await page.query_selector_all(".day.available")  # пример селектора доступных дней
                if not dates:
                    await send_message(settings.telegram_token, settings.chat_id, "Свободных дат нет.")
                    state.set_event("Свободных дат нет")
                else:
                    # Выбираем первую доступную дату
                    first_date = dates[0]
                    date_text = await first_date.inner_text()
                    await first_date.click()
                    await send_message(settings.telegram_token, settings.chat_id, f"Найдена дата: {date_text}")

                    # Выбираем любое свободное время
                    times = await page.query_selector_all(".hour.available")
                    if times:
                        first_time = times[0]
                        time_text = await first_time.inner_text()
                        await first_time.click()
                        await send_message(settings.telegram_token, settings.chat_id, f"Выбрано время: {time_text}")
                        state.last_found_slot = f"{date_text} {time_text}"
                    else:
                        await send_message(settings.telegram_token, settings.chat_id, "Нет доступного времени!")

                state.set_event("Цикл проверки завершён")

            except Exception as e:
                state.set_error(f"Ошибка при проверке: {e}")
                await send_message(settings.telegram_token, settings.chat_id, f"❌ Ошибка: {e}")

            await asyncio.sleep(settings.check_interval_sec)

        await send_message(settings.telegram_token, settings.chat_id, "Бот завершает работу.")
        await browser.close()
