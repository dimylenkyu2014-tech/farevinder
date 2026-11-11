"""Разовый поиск по запросу из Telegram.

python -m bot.query queries.json     (файл готовит bot.poll)
python -m bot.query "14.01-17.01"    (вручную)
"""
import json
import logging
import sys
from datetime import timedelta
from html import escape
from pathlib import Path

import yaml

from . import ryanair, wizz, telegram
from .common import RoundTrip, log
from .fx import FX
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


def search(q, cfg):
    cur = cfg.get("currency", "EUR")
    fx = FX(cur)
    origins = q["origins"]
    excl = {d.upper() for d in cfg.get("exclude_destinations") or []}
    any_ret = cfg.get("round_trip", {}).get("return_to_any_origin", True)
    delay = cfg.get("wizz_request_delay", 1.2)
    wdc = cfg.get("wizz_discount_club", False)
    items, errors = [], []

    if q["mode"] == "rt":
        pairs = q["pairs"]
        ok = {(o.isoformat(), r.isoformat()) for o, r in pairs}
        if cfg["airlines"].get("ryanair"):
            got = ryanair.fetch_rt_windows(origins, q["windows"], fx, cur)
            items += [t for t in got if (t.date, t.ret_date) in ok]
        if cfg["airlines"].get("wizzair"):
            outs_d = [p[0] for p in pairs]
            rets_d = [p[1] for p in pairs]
            w_out, w_ret, errors = wizz.fetch_span(origins, min(outs_d), max(outs_d), min(rets_d), max(rets_d),
                                                   fx, delay, wdc, lambda d: d not in excl)
            items += pair_wizz(w_out, w_ret, pairs, origins, any_ret)
    else:
        outs = q["outs"]
        lo, hi = min(outs), max(outs)
        ok = {d.isoformat() for d in outs}
        if cfg["airlines"].get("ryanair"):
            # Ryanair отдаёт самый дешёвый день в диапазоне — этого и достаточно
            items += [f for f in ryanair.fetch(origins, lo, hi, fx, cur) if f.date in ok]
        if cfg["airlines"].get("wizzair"):
            w_out, _, errors = wizz.fetch_span(origins, lo, hi, None, None, fx, delay, wdc,
                                               lambda d: d not in excl)
            items += [f for f in w_out if f.date in ok]

    items = [i for i in items if i.dest not in excl and i.origin in origins]
    if q["max_price"]:
        items = [i for i in items if i.price <= q["max_price"]]

    best = {}  # одно (самое дешёвое) предложение на направление
    for i in items:
        if i.dest not in best or i.price < best[i.dest].price:
            best[i.dest] = i
    return sorted(best.values(), key=lambda i: i.price)[:TOP_N], len(best), errors


def answer(text, cfg):
    try:
        q = parse(text)
    except ParseError as e:
        telegram.send(f"❓ {escape(str(e))}\n\n{HELP}")
        return
    log.info("Запрос: %s -> %s", text, q["title"])
    top, total, errors = search(q, cfg)
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
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    p = Path(arg)
    texts = json.loads(p.read_text(encoding="utf-8")) if arg.endswith(".json") and p.exists() else [arg]
    for t in texts:
        if t.strip():
            try:
                answer(t, cfg)
            except Exception as e:  # noqa
                log.exception("Ошибка запроса")
                telegram.send(f"⚠️ Не получилось выполнить «{escape(t)}»: {escape(str(e)[:200])}")


if __name__ == "__main__":
    main()
