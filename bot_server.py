#!/usr/bin/env python3
"""
FastAPI + python-telegram-bot + WebApp панель.
Команды в Telegram:
  /start  — приветствие + кнопка "Открыть панель"
Панель: показывает состояние, последние события; кнопки Старт/Стоп.
"""

from __future__ import annotations
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from checker import run_checker_forever, CheckerSettings, CheckerState

# --------- ENV ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

USER_EMAIL  = os.getenv("USER_EMAIL", "")
USER_PESEL  = os.getenv("USER_PESEL", "")
OFFICE_TEXT = os.getenv("OFFICE_TEXT", "USC przy ul. Włodkowica 20")
SERVICE_TEXT = os.getenv("SERVICE_TEXT", "UT: Wpis zagranicznego urodzenia/małżeństwa/zgonu")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))  # пауза между циклами, если нет дат
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")  # ваш домен Railway, например: https://yourapp.up.railway.app
# ------------------------

# ===== Глобальное состояние =====
class Global:
    app_tg: Optional[Application] = None          # Telegram App
    bg_task: Optional[asyncio.Task] = None        # фоновая задача с чекером
    state = CheckerState()                        # состояние проверок (для панели)
    running: bool = False                         # флаг «идут проверки»


global_state = Global()

# ===== Утилиты TG =====
def tg_notify(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"[TG SKIP] {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=8,
        )
    except Exception as e:
        print("Telegram notify error:", e)


# ===== Telegram Handlers =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Кнопка WebApp — откроет нашу панель прямо в Telegram
    web_url = PUBLIC_BASE_URL.rstrip("/") + "/webapp"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Открыть панель", web_app=WebAppInfo(web_url))]]
    )
    await update.message.reply_text(
        "Привет! Это панель управления ботом.\n"
        "Нажми «Открыть панель», чтобы запустить/остановить проверки и смотреть статус.",
        reply_markup=kb,
    )


# ===== FastAPI =====
app = FastAPI(title="Bez Kolejki — Bot & Panel")

# отдаём статик
app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")


@app.get("/", include_in_schema=False)
async def root():
    # редиректим на панель
    return FileResponse("webapp/index.html")


@app.get("/api/status")
async def api_status():
    # вернём текущее состояние для панели
    return JSONResponse(
        {
            "running": global_state.running,
            "last_check_iso": global_state.state.last_check_iso,
            "last_event": global_state.state.last_event,
            "last_error": global_state.state.last_error,
            "last_found_slot": global_state.state.last_found_slot,
            "interval_sec": CHECK_INTERVAL_SEC,
        }
    )


@app.post("/api/start")
async def api_start():
    if global_state.running:
        return JSONResponse({"ok": True, "message": "Уже запущено"}, status_code=200)

    # стартуем фоновую задачу
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

    global_state.running = True
    global_state.state.set_event("Проверки запущены из панели")

    async def runner():
        try:
            await run_checker_forever(settings, global_state.state, stop_flag=lambda: not global_state.running)
        except Exception as e:
            global_state.state.set_error(f"Критическая ошибка фоновой задачи: {e}")
        finally:
            global_state.running = False

    global_state.bg_task = asyncio.create_task(runner())
    return JSONResponse({"ok": True, "message": "Запущено"})


@app.post("/api/stop")
async def api_stop():
    if not global_state.running:
        return JSONResponse({"ok": True, "message": "Уже остановлено"}, status_code=200)
    global_state.running = False
    global_state.state.set_event("Остановка запрошена")
    # задача сама завершится на следующей итерации
    return JSONResponse({"ok": True, "message": "Останавливаемся"})


# ===== События жизненного цикла =====
@app.on_event("startup")
async def on_startup():
    # стартуем Telegram-bot (polling) НЕ блокируя event loop FastAPI
    if TELEGRAM_TOKEN:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        application.add_handler(CommandHandler("start", cmd_start))
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        global_state.app_tg = application
        print("Telegram bot polling started.")
    else:
        print("TELEGRAM_TOKEN не задан — Telegram-бот не запущен.")


@app.on_event("shutdown")
async def on_shutdown():
    # останавливаем фоновые задачи
    global_state.running = False
    if global_state.bg_task:
        try:
            await asyncio.wait_for(global_state.bg_task, timeout=10)
        except asyncio.TimeoutError:
            global_state.bg_task.cancel()

    # останавливаем Telegram
    if global_state.app_tg:
        await global_state.app_tg.updater.stop()
        await global_state.app_tg.stop()
        await global_state.app_tg.shutdown()
        print("Telegram bot stopped.")
