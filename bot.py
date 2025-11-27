import os
import time
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MIN_LIQ = 0
MODE = "top20"
prev = {}

API = f"https://api.telegram.org/bot{TOKEN}"
COINGLASS_HEADERS = {"coinglassSecret": "F7D0C0E7B6A04A0BB88E2A0D0C0B0F0E"}

def send(msg, sound=True):
    payload = {
        "chat_id": ADMIN_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if sound:
        payload["notification_sound"] = "6"  # самый громкий звук Telegram
    try:
        requests.post(f"{API}/sendMessage", data=payload, timeout=10)
    except:
        pass

def get_top_coins():
    try:
        r = requests.get("https://open-api.coinglass.com/public/v2/symbols", headers=COINGLASS_HEADERS, timeout=10).json()
        if r.get("code") == "0" and "data" in r:
            symbols = sorted(r["data"], key=lambda x: float(x.get("volumeUsd24h", 0) or 0), reverse=True)
            top20 = [s["symbol"] for s in symbols[:20]]
            top50 = [s["symbol"] for s in symbols[:50]]
            return top20, top50
    except:
        pass
    return [], []

top20, top50 = get_top_coins()

def get_liq():
    try:
        r = requests.get(
            "https://open-api.coinglass.com/public/v2/liquidation_aggregated",
            headers=COINGLASS_HEADERS,
            params={"exchange": "bybit", "interval": "1h"},
            timeout=10
        ).json()
        if r.get("code") != "0": return {}
        data = {}
        for item in r.get("data", []):
            sym = item["symbol"]
            total = float(item.get("longLiquidationAmount", 0)) + float(item.get("shortLiquidationAmount", 0))
            if total >= MIN_LIQ:
                data[sym] = total
        return data
    except:
        return {}

def format_message(data):
    if not data: return None
    filtered = data
    if MODE == "top20" and top20: filtered = {k:v for k,v in data.items() if k in top20}
    elif MODE == "top50" and top50: filtered = {k:v for k,v in data.items() if k in top50}
    elif MODE == "others" and top20: filtered = {k:v for k,v in data.items() if k not in top20}

    sorted_data = sorted(filtered.items(), key=lambda x: x[1], reverse=True)[:25]
    total = sum(filtered.values())
    text = f"<b>Bybit • Ликвидации за 1ч</b>\n"
    text += f"<i>{datetime.now().strftime('%H:%M:%S')} UTC</i>\n\n"
    text += f"<b>Всего: ${total:,.0f}</b> • {len(filtered)} монет\n\n"
    for i, (sym, val) in enumerate(sorted_data, 1):
        delta = f" (+${int(val - prev.get(sym, 0)):,.0f})" if sym in prev and val > prev.get(sym, 0) else ""
        text += f"{i}. <b>{sym}</b>  ${val:,.0f}{delta}\n"
    text += f"\nРежим: <b>{MODE}</b> | Мин: <b>${MIN_LIQ:,}</b>"
    return text

def handle_updates():
    global MIN_LIQ, MODE, prev
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=10).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "message" not in u: continue
                msg = u["message"]
                if msg["chat"]["id"] != ADMIN_ID: continue
                txt = msg.get("text", "").strip().lower()
                if txt == "/top20": MODE = "top20"; send("Режим: топ-20")
                elif txt == "/top50": MODE = "top50"; send("Режим: топ-50")
                elif txt == "/others": MODE = "others"; send("Режим: остальные")
                elif txt == "/all": MODE = "all"; send("Режим: все монеты")
                elif txt.startswith("/set "):
                    try: MIN_LIQ = int(txt.split()[1]); send(f"Минимум: ${MIN_LIQ:,}")
                    except: send("Ошибка")
                elif txt in ["/start", "/status", "/help"]:
                    send(f"Скринер Bybit работает\nРежим: {MODE}\nМин: ${MIN_LIQ:,}\n\nКоманды:\n/top20 /top50 /others /all\n/set 1000000\n/force — текущее состояние")
                elif txt == "/force":
                    data = get_liq()
                    msg = format_message(data)
                    if msg: send(msg)
        except: time.sleep(5)

def main():
    send("Bybit скринер запущен (сканирую каждую минуту)\nГромкий звук включён", sound=True)
    import threading
    threading.Thread(target=handle_updates, daemon=True).start()
    global prev
    while True:
        try:
            data = get_liq()
            msg = format_message(data)
            if msg and (not prev or sum(data.values()) - sum(prev.values()) >= 50000):
                send(msg, sound=True)
            prev = data.copy()
        except Exception as e:
            logging.error(e)
        time.sleep(60)

if __name__ == "__main__":
    main()
