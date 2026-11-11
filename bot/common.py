from dataclasses import dataclass, asdict, fields
from datetime import date, timedelta
import logging

log = logging.getLogger("farebot")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def ry_url(o, d, out, ret=None):
    u = ("https://www.ryanair.com/gb/en/trip/flights/select?adults=1&teens=0&children=0&infants=0"
         f"&dateOut={out}&originIata={o}&destinationIata={d}")
    return u + (f"&isReturn=true&dateIn={ret}" if ret else "&isReturn=false")


def wizz_url(o, d, out, ret=None):
    return f"https://wizzair.com/en-gb/booking/select-flight/{o}/{d}/{out}/{ret or 'null'}/1/0/0/null"


@dataclass
class Fare:
    """Перелёт в одну сторону."""
    airline: str          # "ryanair" | "wizzair"
    origin: str
    dest: str
    dest_name: str
    date: str             # YYYY-MM-DD
    price: float          # в валюте конфига (EUR)
    orig_price: float
    orig_ccy: str
    type = "ow"

    @property
    def month(self):
        return self.date[:7]

    @property
    def key(self):
        return f"{self.airline}|{self.origin}|{self.dest}|{self.month}"

    def to_dict(self):
        return {**asdict(self), "type": self.type}

    def links(self):
        fn = ry_url if self.airline == "ryanair" else wizz_url
        return [("купить", fn(self.origin, self.dest, self.date))]


@dataclass
class RoundTrip:
    """Туда-обратно. ret_to может отличаться от origin (вернуться в другой из 4 городов)."""
    airline: str
    origin: str
    dest: str
    dest_name: str
    date: str             # дата вылета туда
    ret_date: str
    ret_to: str
    nights: int
    price: float          # сумма туда + обратно
    out_price: float
    ret_price: float
    type = "rt"

    @property
    def month(self):
        return self.date[:7]

    @property
    def key(self):
        return f"rt|{self.airline}|{self.origin}|{self.dest}|{self.month}"

    def to_dict(self):
        return {**asdict(self), "type": self.type}

    def links(self):
        fn = ry_url if self.airline == "ryanair" else wizz_url
        if self.ret_to == self.origin:
            return [("купить", fn(self.origin, self.dest, self.date, self.ret_date))]
        return [("туда", fn(self.origin, self.dest, self.date)),
                ("обратно", fn(self.dest, self.ret_to, self.ret_date))]


def from_dict(d):
    cls = RoundTrip if d.get("type") == "rt" else Fare
    return cls(**{f.name: d[f.name] for f in fields(cls)})


def month_windows(start: date, end: date):
    """Разбивает [start, end] на календарные месяцы -> [(from, to), ...]."""
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(end, nxt - timedelta(days=1))
        cur = nxt


def cheapest_per_month(items):
    """По одному самому дешёвому варианту на ключ (авиакомпания|откуда|куда|месяц)."""
    best = {}
    for f in items:
        if f.key not in best or f.price < best[f.key].price:
            best[f.key] = f
    return best


def pair_round_trips(outs, rets, origins, min_n, max_n, any_return=True):
    """Склеивает дневные цены туда (outs) и обратно (rets) в самые дешёвые пары.
    outs, rets: списки Fare одной авиакомпании (rets: из направления обратно в наши города)."""
    by_route = {}
    for r in rets:  # (откуда_обратно, куда) -> {дата: Fare}
        cur = by_route.setdefault((r.origin, r.dest), {})
        if r.date not in cur or r.price < cur[r.date].price:
            cur[r.date] = r
    trips = []
    for o in outs:
        d0 = date.fromisoformat(o.date)
        # свой город первым: при равной цене предпочитаем вернуться туда же
        targets = [o.origin] + ([x for x in origins if x != o.origin] if any_return else [])
        best = None
        for back_to in targets:
            days = by_route.get((o.dest, back_to))
            if not days:
                continue
            for n in range(min_n, max_n + 1):
                r = days.get((d0 + timedelta(days=n)).isoformat())
                if r and (best is None or r.price < best[0].price):
                    best = (r, n)
        if best:
            r, n = best
            trips.append(RoundTrip(o.airline, o.origin, o.dest, o.dest_name, o.date, r.date, r.dest,
                                   n, round(o.price + r.price, 2), o.price, r.price))
    return trips
