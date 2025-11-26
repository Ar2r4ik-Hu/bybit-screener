import os
import time
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MIN_LIQ = 0          # меняется командой /set
MODE = "top20"       # top20 / top50 / others / all
prev = {}

API = f"https://api.telegram.org/bot{TOKEN}"
COINGLASS = "https://open-api.coinglass.com/public/v2/liquidation_aggregated"
HEADERS = {"coinglassSecret": "F7D0C0E7B6A04A0BB88E2A0D0C0B0F0E"}

def send(msg):
    requests.post(f"{API}/sendMessage", data={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True})

def get_top20_50():
    r = requests.get("https://open-api.coinglass.com/public/v2/symbols", headers=HEADERS, timeout=10).json()
    symbols = sorted(r["data"], key=lambda x: x.get("volumeUsd24h", 0), reverse=True)
    return [s["symbol"] for s in symbols[:20]], [s["symbol"] for s in symbols[:50]]

top20, top50 = get_top20_50()

def get_liq():
    r = requests.get(COINGLASS, headers=HEADERS, params={"exchange": "bybit", "interval": "1h"}, timeout=10).json()
    data = {}
    for item in r.get("data", []):
        sym = item["symbol"]
        total = float(item.get("longLiquidationAmount", 0)) + float(item.get("shortLiquidationAmount", 0))
        if total >= MIN_LIQ:
            data[sym] = total
    return data

def format_message(data):
    if not data: return None
    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)[:25]
    total = sum(data.values())
    text = f"<b>Bybit • Ликвидации за 1ч</b>  |  {datetime.now():%H:%M:%S}\n\n"
    text += f"<b>Всего: ${total:,.0f}</b>  |  {len(data)} монет\n\n"
    for i, (sym, val) in enumerate(sorted_data, 1):
        delta = f" (+${int(val - prev.get(sym, 0)):,.0f})" if sym in prev and val > prev[sym] else ""
        text += f"{i:>2}. <b>{sym}</b>  ${val:,.0f}{delta}\n"
    text += f"\nРежим: {MODE}  |  Мин: ${MIN_LIQ:,}"
    return text

def handle_updates():
    global prev, MIN_LIQ, MODE, top20, top50
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=15).json()
            for u in r["result"]:
                offset = u["update_id"] + 1
                if "message" not in u or "text" not in u["message"]: continue
                msg = u["message"]
                if msg["chat"]["id"] != ADMIN_ID: continue
                txt = msg["text"].strip().lower()
                if txt == "/top20": MODE = "top20"; send("Режим: топ-20")
                elif txt == "/top50": MODE = "top50"; send("Режим: топ-50")
                elif txt == "/others": MODE = "others"; send("Режим: остальные")
                elif txt == "/all": MODE = "all"; send("Режим: все")
                elif txt.startswith("/set "):
                    try: MIN_LIQ = int(txt.split()[1]); send(f"Минимум: ${MIN_LIQ:,}")
                    except: send("Ошибка")
                elif txt in ["/start", "/status"]:
                    send(f"Скринер Bybit работает\nРежим: {MODE}\nМин: ${MIN_LIQ:,}")
        except: pass

def main():
    send("Bybit скринер запущен (1-минутный скан)")
    time.sleep(3)
    import threading
    threading.Thread(target=handle_updates, daemon=True).start()
    global prev
    while True:
        try:
            data = get_liq()
            filtered = data
            if MODE == "top20": filtered = {k:v for k,v in data.items() if k in top20}
            elif MODE == "top50": filtered = {k:v for k,v in data.items() if k in top50}
            elif MODE == "others": filtered = {k:v for k,v in data.items() if k not in top20}
            if filtered:
                delta = sum(filtered.values()) - sum(v for k,v in prev.items() if k in filtered)
                if abs(delta) >= 50_000 or not prev:
                    msg = format_message(filtered)
                    if msg: send(msg)
            prev = data
        except Exception as e: logging.error(e)
        time.sleep(60)

if __name__ == "__main__":
    main()
