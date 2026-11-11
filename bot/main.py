"""Fare Finder bot: Ryanair + Wizz Air -> Telegram.

Запуск:  python -m bot.main            обычная проверка
         python -m bot.main --digest   принудительно отправить сводку
         python -m bot.main --test     проверить связь с Telegram
         python -m bot.main --dry-run  без отправки и без сохранения состояния
"""
import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

import yaml

from . import ryanair, wizz, telegram
from .common import cheapest_per_month, pair_round_trips, from_dict, log
from .fx import FX

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "fares.json"
AIRLINE = {"ryanair": "Ryanair", "wizzair": "Wizz"}
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"fares": {}, "last_digest": None}


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def fmt_date(iso, wd=True):
    d = date.fromisoformat(iso)
    s = f"{d.day} {MONTHS[d.month - 1]}"
    return f"{s} ({WD[d.weekday()]})" if wd else s


def fmt_line(f, cur, extra=""):
    links = " · ".join(f'<a href="{u}">{t}</a>' for t, u in f.links())
    head = f'<b>{f.origin} → {escape(f.dest_name)} ({f.dest})</b> · {AIRLINE[f.airline]}'
    if f.type == "rt":
        back = f" → {f.ret_to}" if f.ret_to != f.origin else ""
        return (f'{head}\n{fmt_date(f.date)} – {fmt_date(f.ret_date)}{back}, {f.nights} ноч. — '
                f'<b>{f.price:.0f} {cur}</b> ({f.out_price:.0f} + {f.ret_price:.0f}){extra}\n{links}')
    return f'{head}\n{fmt_date(f.date)} — <b>{f.price:.2f} {cur}</b>{extra} · {links}'


def evaluate(best, state, th, a, now, first_run):
    """Сравнивает новые цены с прошлыми. th — пороги для этого типа (one_way / round_trip)."""
    realert = timedelta(hours=a.get("realert_after_hours", 72))
    alerts = []
    for key, f in best.items():
        prev = state["fares"].get(key)
        kind, extra = None, ""

        if prev is None:
            if not first_run and f.price <= th["new_route_max_price"]:
                kind = "new"
        else:
            drop = prev["price"] - f.price
            pct = drop / prev["price"] * 100 if prev["price"] else 0
            if drop >= a["drop_abs"] and pct >= a["drop_pct"]:
                kind, extra = "drop", f' (было {prev["price"]:.0f}, −{pct:.0f}%)'

        if kind is None and f.price <= th["max_price"] and not first_run:
            kind = "cheap"

        # анти-спам: не повторяем алерт по той же (или большей) цене в течение realert
        if kind and prev and prev.get("last_alert_price") is not None:
            last_at = datetime.fromisoformat(prev["last_alert_at"])
            if f.price >= prev["last_alert_price"] - 0.5 and now - last_at < realert:
                kind = None

        rec = {**f.to_dict(), "updated": now.isoformat(),
               "last_alert_price": (prev or {}).get("last_alert_price"),
               "last_alert_at": (prev or {}).get("last_alert_at"),
               "min_seen": min((prev or {}).get("min_seen", f.price), f.price)}
        if kind:
            rec["last_alert_price"], rec["last_alert_at"] = f.price, now.isoformat()
            alerts.append((kind, f, extra))
        state["fares"][key] = rec
    return alerts


def build_alert_msg(alerts, cur, section):
    titles = {"drop": "📉 Цена упала", "cheap": "🔥 Дёшево", "new": "🆕 Новое направление / месяц"}
    parts = []
    for kind in ("drop", "cheap", "new"):
        groups = {}  # один блок на маршрут: лучший месяц + остальные кратко
        for k, f, extra in alerts:
            if k == kind:
                groups.setdefault((f.airline, f.origin, f.dest), []).append((f, extra))
        if not groups:
            continue
        parts.append(f"<b>{titles[kind]} · {section}</b>")
        for items in sorted(groups.values(), key=lambda g: min(x[0].price for x in g))[:25]:
            items.sort(key=lambda x: x[0].price)
            f, extra = items[0]
            line = fmt_line(f, cur, extra)
            if len(items) > 1:
                line += "\nещё: " + ", ".join(f"{fmt_date(o.date, False)} {o.price:.0f}" for o, _ in items[1:6])
            parts.append(line)
    return "\n\n".join(parts)


def top_by_route(state, typ, n):
    top = {}
    for rec in state["fares"].values():
        if rec.get("type", "ow") != typ:
            continue
        k = (rec["origin"], rec["dest"])
        if k not in top or rec["price"] < top[k]["price"]:
            top[k] = rec
    return [from_dict(r) for r in sorted(top.values(), key=lambda r: r["price"])[:n]]


def build_digest(state, cfg, cur, errors):
    n = cfg["digest"]["top_n"]
    lines = [f"<b>✈️ Сводка дешёвых билетов</b> из {', '.join(cfg['origins'])}"]
    rt = top_by_route(state, "rt", n)
    if rt:
        lines.append(f"<b>🔁 ТОП-{len(rt)} туда-обратно</b>")
        lines += [fmt_line(f, cur) for f in rt]
    ow = top_by_route(state, "ow", n)
    if ow:
        lines.append(f"<b>➡️ ТОП-{len(ow)} в одну сторону</b>")
        lines += [fmt_line(f, cur) for f in ow]
    if errors:
        lines.append(f"⚠️ Ошибок при сборе: {len(errors)} (напр. {escape(errors[0][:150])})")
    return "\n\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.test:
        telegram.send("✅ Fare Finder на связи. Буду писать, когда найду дешёвые билеты.")
        return

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cur = cfg.get("currency", "EUR")
    origins = [o.upper() for o in cfg["origins"]]
    only = {d.upper() for d in cfg.get("only_destinations") or []}
    excl = {d.upper() for d in cfg.get("exclude_destinations") or []}
    dest_ok = lambda d: (not only or d in only) and d not in excl  # noqa
    rtc = cfg.get("round_trip", {})
    rt_on = rtc.get("enabled", False)
    min_n, max_n = rtc.get("min_nights", 2), rtc.get("max_nights", 10)

    now = datetime.now(timezone.utc)
    start = now.date() + timedelta(days=1)
    end = start + timedelta(days=cfg.get("horizon_days", 180))
    fx = FX(cur)

    ow, rt, errors = [], [], []
    if cfg["airlines"].get("ryanair"):
        ow += ryanair.fetch(origins, start, end, fx, cur)
        if rt_on:
            rt += ryanair.fetch_rt(origins, start, end, fx, min_n, max_n, cur)
    if cfg["airlines"].get("wizzair"):
        w_out, w_ret, we = wizz.fetch(origins, start, end, fx, cfg.get("wizz_request_delay", 1.2),
                                      cfg.get("wizz_discount_club", False), dest_ok,
                                      ret_extra_days=max_n if rt_on else 0)
        ow += w_out
        errors += we
        if rt_on:
            rt += pair_round_trips(w_out, w_ret, origins, min_n, max_n, rtc.get("return_to_any_origin", True))

    ow = [f for f in ow if dest_ok(f.dest)]
    rt = [f for f in rt if dest_ok(f.dest)]
    best_ow, best_rt = cheapest_per_month(ow), cheapest_per_month(rt)
    log.info("В одну сторону: %d тарифов / %d маршрут×месяц; туда-обратно: %d", len(ow), len(best_ow), len(best_rt))

    state = load_state()
    have = {v.get("type", "ow") for v in state["fares"].values()}
    a = cfg["alerts"]
    alerts_rt = evaluate(best_rt, state, a["round_trip"], a, now, "rt" not in have)
    alerts_ow = evaluate(best_ow, state, a["one_way"], a, now, "ow" not in have)

    this_month = now.strftime("%Y-%m")  # чистим прошедшие месяцы
    state["fares"] = {k: v for k, v in state["fares"].items() if v["date"][:7] >= this_month}

    msgs = []
    if not have and (best_ow or best_rt):
        msgs.append(f"✅ Первый запуск: запомнил {len(best_ow)} цен в одну сторону и {len(best_rt)} туда-обратно. "
                    "Дальше пишу только об изменениях.")
    if alerts_rt:
        msgs.append(build_alert_msg(alerts_rt, cur, "туда-обратно"))
    if alerts_ow and a["one_way"].get("enabled", True):
        msgs.append(build_alert_msg(alerts_ow, cur, "в одну сторону"))

    today = now.date().isoformat()
    d = cfg.get("digest", {})
    if args.digest or (d.get("enabled") and now.hour == d.get("hour_utc", 6) and state.get("last_digest") != today):
        msgs.append(build_digest(state, cfg, cur, errors))
        state["last_digest"] = today

    if not ow:
        msgs.append("⚠️ Не удалось получить ни одной цены — возможно, авиакомпании блокируют запросы.")

    for m in msgs:
        if args.dry_run:
            print(m, "\n" + "-" * 40)
        else:
            telegram.send(m)
    if not args.dry_run:
        save_state(state)
    log.info("Готово: алертов туда-обратно %d, в одну сторону %d", len(alerts_rt), len(alerts_ow))


if __name__ == "__main__":
    main()
