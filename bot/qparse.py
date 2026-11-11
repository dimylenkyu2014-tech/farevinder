"""Разбор запросов из Telegram (только стандартная библиотека).

Примеры:
  14.01-17.01                 туда 14 янв, обратно 17 янв
  14.01±1 17.01±1             ±1 день к каждой дате
  14.01-17.01 ±2              ±2 дня к обеим датам
  пт-вс январь                все выходные января (вылет пт, возврат вс)
  чт,пт-вс,пн фев-мар         вылет чт или пт, возврат вс или пн
  пт-вс 01.02-31.03           то же, в периоде
  в одну сторону 14.01±2      только туда
  ... из Варны / из SOF       ограничить город вылета
  ... до 80                   не дороже 80 EUR
"""
import re
from datetime import date, timedelta

ORIGINS = ["VAR", "SOF", "OTP", "BOJ"]
CITY = {"варн": "VAR", "соф": "SOF", "бухар": "OTP", "бург": "BOJ"}
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "ма", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MON_RE = r"(янв\w*|фев\w*|мар\w*|апр\w*|ма[йяе]\w*|июн\w*|июл\w*|авг\w*|сен\w*|окт\w*|ноя\w*|дек\w*)"
WD_LIST = r"((?:пн|вт|ср|чт|пт|сб|вс)(?:\s*,\s*(?:пн|вт|ср|чт|пт|сб|вс))*)"
MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

HELP = """<b>Как искать билеты</b>

<code>14.01-17.01</code> — туда 14 янв, обратно 17 янв
<code>14.01±1 17.01±1</code> — каждая дата ±1 день
<code>14.01-17.01 ±2</code> — обе даты ±2 дня
<code>пт-вс январь</code> — все выходные января
<code>чт,пт-вс,пн фев-мар</code> — вылет чт/пт, возврат вс/пн
<code>пт-вс 01.02-31.03</code> — то же в своём периоде
<code>в одну сторону 14.01±2</code> — только туда

Добавки: <code>все</code> — показать все направления (по умолчанию ТОП-15); <code>топ 30</code> — первые 30;
<code>из Варны</code> / <code>из SOF,OTP</code> — откуда; <code>до 80</code> — не дороже 80 €.
Ответ придёт через 5–15 минут."""


class ParseError(ValueError):
    pass


def _month_idx(word):
    w = word.lower()
    if w.startswith("ма") and not w.startswith("мар"):
        return 5
    for i, m in enumerate(MONTHS):
        if w.startswith(m):
            return i + 1
    raise ParseError(f"не понял месяц «{word}»")


def _resolve(d, m, y, today, after=None):
    try:
        if y:
            y = int(y)
            return date(y + 2000 if y < 100 else y, m, d)
        res = date(today.year, m, d)
        ref = after or today
        if res < ref:
            res = date(today.year + 1, m, d)
        return res
    except ValueError:
        raise ParseError(f"нет такой даты {d:02d}.{m:02d}")


def _month_range(m1, m2, today):
    y1 = today.year if m1 >= today.month else today.year + 1
    start = date(y1, m1, 1)
    y2 = y1 if m2 >= m1 else y1 + 1
    end = (date(y2, m2, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return max(start, today + timedelta(days=1)), end


def fmt(d):
    return f"{d.day} {MONTHS_RU[d.month - 1]} ({WD[d.weekday()]})"


def parse(text, today=None):
    """-> dict(mode='rt'|'ow', pairs=set((out, ret)) | outs=set(out), origins, max_price, title, windows)"""
    today = today or date.today()
    t = text.strip()
    low = t.lower().replace("ё", "е").replace("—", "-").replace("–", "-")

    # города вылета
    origins = set(re.findall(r"\b(VAR|SOF|OTP|BOJ)\b", t.upper()))
    for stem, code in CITY.items():
        if stem in low:
            origins.add(code)
    origins = [o for o in ORIGINS if o in origins] or list(ORIGINS)

    m = re.search(r"\bдо\s*(\d{2,4})(?![\d./])", low)
    max_price = float(m.group(1)) if m else None
    if m:
        low = low[:m.start()] + low[m.end():]

    top = None                       # None = по умолчанию из config, 0 = все
    m = re.search(r"\bвсе\b|\ball\b", low)
    if m:
        top = 0
        low = low[:m.start()] + low[m.end():]
    m = re.search(r"\bтоп\s*-?\s*(\d{1,3})\b", low)
    if m:
        top = int(m.group(1))
        low = low[:m.start()] + low[m.end():]

    one_way = bool(re.search(r"одну|односторон|\bow\b|one", low))

    flex = [int(x) for x in re.findall(r"(?:±|\+-|\+/-)\s*(\d{1,2})", low)]
    low = re.sub(r"(?:±|\+-|\+/-)\s*\d{1,2}", " ", low)
    if any(f > 7 for f in flex):
        raise ParseError("гибкость максимум ±7 дней")

    dates = [(int(d), int(mo), y) for d, mo, y in re.findall(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", low)]
    tomorrow = today + timedelta(days=1)

    # --- дни недели ---
    wm = re.search(WD_LIST + r"\s*-\s*" + WD_LIST, low)
    if wm and not one_way:
        out_wd = {WD.index(x.strip()) for x in wm.group(1).split(",")}
        ret_wd = {WD.index(x.strip()) for x in wm.group(2).split(",")}
        months = re.findall(MON_RE, low)
        if len(dates) >= 2:
            p_from = _resolve(*dates[0], today)
            p_to = _resolve(*dates[1], today, after=p_from)
        elif months:
            p_from, p_to = _month_range(_month_idx(months[0]), _month_idx(months[-1]), today)
        else:
            p_from, p_to = tomorrow, today + timedelta(days=90)
        p_from = max(p_from, tomorrow)
        if p_to < p_from:
            raise ParseError("период уже прошёл")
        pairs = set()
        d = p_from
        while d <= p_to:
            if d.weekday() in out_wd:
                for k in range(1, 8):
                    r = d + timedelta(days=k)
                    if r.weekday() in ret_wd:
                        pairs.add((d, r))
            d += timedelta(days=1)
        if not pairs:
            raise ParseError("в этом периоде нет подходящих дат")
        title = (f"вылет {wm.group(1).replace(' ', '')}, возврат {wm.group(2).replace(' ', '')}, "
                 f"{fmt(p_from)} – {fmt(p_to)}")
        return _result("rt", pairs, origins, max_price, title, top)

    if not dates:
        raise ParseError("не нашёл даты")

    # --- в одну сторону ---
    if one_way or len(dates) == 1:
        d1 = _resolve(*dates[0], today)
        if len(dates) >= 2:          # диапазон дат
            d2 = _resolve(*dates[1], today, after=d1)
            lo, hi = d1, d2
        else:
            f = flex[0] if flex else 0
            lo, hi = d1 - timedelta(days=f), d1 + timedelta(days=f)
        lo = max(lo, tomorrow)
        if hi < lo:
            raise ParseError("дата уже прошла")
        outs = {lo + timedelta(days=i) for i in range((hi - lo).days + 1)}
        title = fmt(lo) if lo == hi else f"{fmt(lo)} – {fmt(hi)}"
        return {"mode": "ow", "outs": outs, "origins": origins, "max_price": max_price,
                "title": "в одну сторону, " + title, "top": top}

    # --- точные даты ± гибкость ---
    d1 = _resolve(*dates[0], today)
    d2 = _resolve(*dates[1], today, after=d1)
    f1 = flex[0] if flex else 0
    f2 = flex[1] if len(flex) > 1 else f1
    pairs = set()
    for i in range(-f1, f1 + 1):
        o = d1 + timedelta(days=i)
        if o < tomorrow:
            continue
        for j in range(-f2, f2 + 1):
            r = d2 + timedelta(days=j)
            if r > o:
                pairs.add((o, r))
    if not pairs:
        raise ParseError("даты уже прошли или обратно раньше, чем туда")
    title = f"туда {fmt(d1)}{f' ±{f1}' if f1 else ''}, обратно {fmt(d2)}{f' ±{f2}' if f2 else ''}"
    return _result("rt", pairs, origins, max_price, title, top)


def _result(mode, pairs, origins, max_price, title, top=None):
    # окна для Ryanair: группируем вылеты по неделям
    weeks = {}
    for o, r in pairs:
        weeks.setdefault(o.isocalendar()[:2], []).append((o, r))
    windows = []
    for ps in weeks.values():
        outs, rets = [p[0] for p in ps], [p[1] for p in ps]
        nights = [(r - o).days for o, r in ps]
        windows.append((min(outs), max(outs), min(rets), max(rets), min(nights), max(nights)))
    windows.sort()
    return {"mode": mode, "pairs": pairs, "origins": origins, "max_price": max_price,
            "title": title, "windows": windows, "top": top}
