"""Проверяет новые сообщения боту (только стандартная библиотека, запускается без pip install).
Найденные запросы пишет в queries.json и выставляет has=true для GitHub Actions."""
import json
import os
import sys
import urllib.parse
import urllib.request

from .qparse import parse, ParseError, HELP

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
API = f"https://api.telegram.org/bot{TOKEN}/"


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=30) as r:
        return json.loads(r.read().decode())


def send(text):
    call("sendMessage", chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview="true")


def main():
    out_file = sys.argv[1] if len(sys.argv) > 1 else "queries.json"
    upd = call("getUpdates", timeout=0).get("result", [])
    queries = []
    if upd:
        call("getUpdates", offset=upd[-1]["update_id"] + 1, timeout=0)  # пометить прочитанными
    for u in upd:
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT:
            continue  # чужие сообщения игнорируем
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        if text.lower().split()[0] in ("/start", "/help", "помощь", "help", "?"):
            send(HELP)
            continue
        try:
            q = parse(text)
        except ParseError as e:
            send(f"❓ {e}\n\n{HELP}")
            continue
        n = len(q.get("pairs") or q.get("outs"))
        send(f"🔎 Принял: <b>{q['title']}</b>\nиз {', '.join(q['origins'])}"
             f" ({n} вариант. дат). Ищу, пришлю через 5–15 минут.")
        queries.append(text)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(queries, f, ensure_ascii=False)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"has={'true' if queries else 'false'}\n")
    print(f"запросов: {len(queries)}")


if __name__ == "__main__":
    main()
