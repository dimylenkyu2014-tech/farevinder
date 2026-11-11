import os
import requests
from .common import log

LIMIT = 3800  # Telegram режет на 4096 символах


def send(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.info("Telegram не настроен, вывод в консоль:\n%s", text)
        return
    for chunk in _split(text):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat, "text": chunk, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=30)
        if not r.ok:
            log.error("Telegram: %s %s", r.status_code, r.text)


def _split(text):
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > LIMIT and cur:
            parts.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block
    if cur:
        parts.append(cur)
    return parts
