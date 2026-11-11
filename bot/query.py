"""Разовый поиск по запросу из Telegram.

python -m bot.query queries.json     (файл готовит bot.poll)
python -m bot.query "14.01-17.01"    (вручную)
"""
import argparse
import json
import logging
import os
from datetime import timedelta
from html import escape
from pathlib import Path

import yaml

from . import telegram
from .collect import collect_query, merge, from_json, load_parts
from .common import RoundTrip, log
from .main import fmt_line, ROOT
from .qparse import parse, ParseError, HELP

TOP_N = 15


def pair_wizz(outs, rets, pairs, origins, any_return):
    """Склеивает дневные цены Wizz в пары (туда, обратно) только из разрешённых дат."""
    allowed = {}
    for o, r in pairs:
        allowed.setdefault(o.isoformat(), []).append(r.isoformat())
    idx = {}
    for r in rets:  # (откуда_обратно, дата) -> [Fare]
        idx.setdefault((r.origin, r.date), []).append(r)
    trips = []
    for o in outs:
        best = None
        for rd in allowed.get(o.date, []):
            for r in idx.get((o.dest, rd), []):
                if r.dest not in origins or (not any_return and r.dest != o.origin):
                    continue
                if best is None or r.price < best.price or (r.price == best.price and r.dest == o.origin):
                    best = r
        if best:
            from datetime import date
            n = (date.fromisoformat(best.date) - date.fromisoformat(o.date)).days
            trips.append(RoundTrip("wizzair", o.origin, o.dest, o.dest_name, o.date, best.date, best.dest,
                                   n, round(o.price + best.price, 2), o.price, best.price))
    return trips


def search(q, cfg, data=None):
    """data — сырые цены от bot.collect (если None — собираем сами, в одном процессе)."""
    if data is None:
        data = collect_query(q, cfg, q["origins"])
    origins = q["origins"]
    excl = {d.upper() for d in cfg.get("exclude_destinations") or []}
    any_ret = cfg.get("round_trip", {}).get("return_to_any_origin", True)
    errors = data["errors"]
    if q["mode"] == "rt":
        ok = {(o.isoformat(), r.isoformat()) for o, r in q["pairs"]}
        items = [t for t in data["rt"] if (t.date, t.ret_date) in ok]
        items += pair_wizz(data["w_out"], data["w_ret"], q["pairs"], origins, any_ret)
    else:
        ok = {d.isoformat() for d in q["outs"]}
        items = [f for f in data["ow"] + data["w_out"] if f.date in ok]

    items = [i for i in items if i.dest not in excl and i.origin in origins]
    if q["max_price"]:
        items = [i for i in items if i.price <= q["max_price"]]

    best = {}  # одно (самое дешёвое) предложение на направление
    for i in items:
        if i.dest not in best or i.price < best[i.dest].price:
            best[i.dest] = i
    return sorted(best.values(), key=lambda i: i.price)[:TOP_N], len(best), errors


def answer(text, cfg, data=None):
    try:
        q = parse(text)
    except ParseError as e:
        telegram.send(f"❓ {escape(str(e))}\n\n{HELP}")
        return
    log.info("Запрос: %s -> %s", text, q["title"])
    top, total, errors = search(q, cfg, data)
    cur = cfg.get("currency", "EUR")
    head = f"<b>🔎 {escape(q['title'])}</b>\nиз {', '.join(q['origins'])}"
    if q["max_price"]:
        head += f", до {q['max_price']:.0f} {cur}"
    if not top:
        msg = head + "\n\nНичего не нашлось 😕 Попробуй расширить даты (±1–2 дня) или другие дни недели."
    else:
        lines = [head + f"\n\n<b>ТОП-{len(top)}</b> из {total} направлений:"]
        lines += [fmt_line(i, cur) for i in top]
        msg = "\n\n".join(lines)
    if errors:
        msg += f"\n\n⚠️ Wizz: ошибок {len(errors)} ({escape(errors[0][:120])})"
    telegram.send(msg)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--parts", help="папка с part-*.json от bot.collect")
    a = ap.parse_args()
    if a.text.endswith(".json") and Path(a.text).exists():
        texts = json.loads(Path(a.text).read_text(encoding="utf-8"))
    elif a.text:
        texts = [a.text]
    else:
        texts = json.loads(os.environ.get("QUERIES") or "[]")
    parts = load_parts(a.parts) if a.parts else None
    for t in texts:
        if not t.strip():
            continue
        data = merge([from_json(p[t]) for p in parts if t in p]) if parts is not None else None
        try:
            answer(t, cfg, data)
        except Exception as e:  # noqa
            log.exception("Ошибка запроса")
            telegram.send(f"⚠️ Не получилось выполнить «{escape(t)}»: {escape(str(e)[:200])}")


if __name__ == "__main__":
    main()
