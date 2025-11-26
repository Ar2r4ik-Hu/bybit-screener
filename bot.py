import asyncio
import logging
import os
import requests
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
import json

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # Твой Telegram ID

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Настройки (по умолчанию)
MIN_LIQ_USD = 0  # Минимальная сумма ликвидаций (0 = без фильтра)
FILTER_MODE = "top20"  # top20, top50, others, all
SCAN_INTERVAL = 60  # секунд
DELTA_THRESHOLD = 50000  # Отправлять, если изменение > $50k
EXCHANGE = "Bybit"  # Только Bybit
INTERVAL = "1h"  # Совокупные за 1 час

# Хранилище предыдущих данных
prev_data = {}
top_coins_cache = {}  # Кэш топ-монет по объёму (обновляем раз в час)

headers = {
    "accept": "application/json",
    "coinglassSecret": "F7D0C0E7B6A04A0BB88E2A0D0C0B0F0E"  # Публичный ключ Coinglass
}

def format_number(num):
    return f"{num:,.0f}".replace(",", " ")

def get_top_coins():
    """Получаем топ-монеты по 24h volume с Coinglass"""
    url = "https://open-api.coinglass.com/public/v2/symbols"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") == "0":
            symbols = data.get("data", [])
            # Сортируем по volume USD desc
            sorted_symbols = sorted(symbols, key=lambda x: float(x.get("volumeUsd24h", 0)), reverse=True)
            top20 = [s["symbol"] for s in sorted_symbols[:20]]
            top50 = [s["symbol"] for s in sorted_symbols[:50]]
            return top20, top50
    except Exception as e:
        logging.error(f"Ошибка топ-монет: {e}")
    return [], []

async def fetch_liquidations():
    """Запрашиваем совокупные ликвидации Bybit за 1h"""
    url = "https://open-api.coinglass.com/public/v2/liquidation_aggregated"
    params = {"exchange": EXCHANGE.lower(), "interval": INTERVAL}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("code") != "0":
            logging.error(f"API error: {data}")
            return {}
        items = data.get("data", [])
        liq_dict = {item["symbol"]: {
            "long": float(item.get("longLiquidationAmount", 0)),
            "short": float(item.get("shortLiquidationAmount", 0)),
            "total": float(item.get("longLiquidationAmount", 0)) + float(item.get("shortLiquidationAmount", 0))
        } for item in items if float(item.get("longLiquidationAmount", 0)) + float(item.get("shortLiquidationAmount", 0)) >= MIN_LIQ_USD}
        return liq_dict
    except Exception as e:
        logging.error(f"Ошибка запроса: {e}")
        return {}

def apply_filter(liq_dict, top20, top50):
    """Применяем фильтр"""
    if FILTER_MODE == "top20":
        return {k: v for k, v in liq_dict.items() if k in top20}
    elif FILTER_MODE == "top50":
        return {k: v for k, v in liq_dict.items() if k in top50}
    elif FILTER_MODE == "others":
        return {k: v for k, v in liq_dict.items() if k not in top20}
    else:  # all
        return liq_dict

def build_message(liq_dict, timestamp):
    """Формируем сообщение"""
    if not liq_dict:
        return None
    # Сортируем по total desc
    sorted_liq = sorted(liq_dict.items(), key=lambda x: x[1]["total"], reverse=True)
    text = f"<b>{EXCHANGE} — Совокупные ликвидации ({INTERVAL})</b>\n\n<i>Обновлено: {timestamp} UTC</i>\n\n"
    total_all = sum(d["total"] for d in liq_dict.values())
    text += f"<b>{len(sorted_liq)} монет | Всего: ${format_number(total_all)}</b>\n\n"
    
    for i, (symbol, data) in enumerate(sorted_liq[:20], 1):  # Показываем топ-20 в сообщении
        delta = f" (+${format_number(data['total'] - prev_data.get(symbol, {}).get('total', 0))})" if symbol in prev_data else ""
        text += f"{i}. <b>{symbol}</b>\n"
        text += f"   🟢 Лонг: ${format_number(data['long'])}\n"
        text += f"   🔴 Шорт: ${format_number(data['short'])}\n"
        text += f"   💥 Всего: ${format_number(data['total'])}{delta}\n\n"
    
    if len(sorted_liq) > 20:
        text += f"... и ещё {len(sorted_liq) - 20} монет\n"
    
    text += f"\nНастройки: мин. ${format_number(MIN_LIQ_USD)} | {FILTER_MODE.replace('top', 'топ-').upper()}"
    return text

async def send_update():
    """Отправляем обновление, если есть изменения"""
    global prev_data, top_coins_cache
    current_time = datetime.now().strftime("%H:%M:%S")
    
    # Обновляем топ-монеты раз в час
    now_hour = datetime.now().hour
    if not top_coins_cache or now_hour != getattr(send_update, 'last_hour', None):
        top20, top50 = get_top_coins()
        top_coins_cache = {"top20": top20, "top50": top50}
        send_update.last_hour = now_hour
    
    liq_dict = await fetch_liquidations()
    filtered_dict = apply_filter(liq_dict, top_coins_cache.get("top20", []), top_coins_cache.get("top50", []))
    
    # Проверяем на изменения
    has_changes = False
    total_delta = 0
    for symbol, data in filtered_dict.items():
        prev_total = prev_data.get(symbol, {}).get("total", 0)
        if abs(data["total"] - prev_total) > DELTA_THRESHOLD:
            has_changes = True
            total_delta += data["total"] - prev_total
            break
    
    if has_changes and filtered_dict:
        message = build_message(filtered_dict, current_time)
        if message:
            try:
                await bot.send_message(ADMIN_ID, message, disable_web_page_preview=True)
                # Громкий звук: notification_sound=6 (самый пронзительный)
                # Примечание: aiogram не поддерживает напрямую, но для Render используем webhook или просто текст; для реального звука — добавь в payload при кастомном клиенте
                logging.info(f"Отправлено обновление: delta ~${format_number(total_delta)}")
            except Exception as e:
                logging.error(f"Ошибка отправки: {e}")
    
    prev_data = liq_dict  # Обновляем prev только после отправки

@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.reply("🟢 Бот запущен! Сканирую Bybit каждые 60с.\n\nКоманды:\n/top20 — топ-20\n/top50 — топ-50\n/others — остальные\n/all — все\n/set <сумма> — мин. сумма (0=off)\n/status — настройки")

@dp.message(Command("top20"))
async def top20_handler(message: Message):
    global FILTER_MODE
    FILTER_MODE = "top20"
    await message.reply("✅ Фильтр: топ-20 монет")

@dp.message(Command("top50"))
async def top50_handler(message: Message):
    global FILTER_MODE
    FILTER_MODE = "top50"
    await message.reply("✅ Фильтр: топ-50 монет")

@dp.message(Command("others"))
async def others_handler(message: Message):
    global FILTER_MODE
    FILTER_MODE = "others"
    await message.reply("✅ Фильтр: остальные монеты")

@dp.message(Command("all"))
async def all_handler(message: Message):
    global FILTER_MODE
    FILTER_MODE = "all"
    await message.reply("✅ Фильтр: все монеты")

@dp.message(Command("set"))
async def set_handler(message: Message):
    global MIN_LIQ_USD
    try:
        args = message.text.split()
        MIN_LIQ_USD = int(args[1]) if len(args) > 1 else 0
        await message.reply(f"✅ Мин. сумма: ${format_number(MIN_LIQ_USD)}")
    except:
        await message.reply("❌ Используй: /set 1000000")

@dp.message(Command("status"))
async def status_handler(message: Message):
    await message.reply(f"📊 Статус:\nФильтр: {FILTER_MODE}\nМин. сумма: ${format_number(MIN_LIQ_USD)}\nСкан: каждые {SCAN_INTERVAL}с\nБиржа: {EXCHANGE}")

async def main_loop():
    logging.info("Бот запущен! Сканирую Bybit...")
    await bot.send_message(ADMIN_ID, "🟢 Скринер Bybit запущен (1-мин скан)")
    while True:
        await send_update()
        await asyncio.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    if ADMIN_ID == 0:
        logging.error("Укажи ADMIN_ID в .env!")
        exit(1)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop())