"""Wizz Air: карта маршрутов + календарь цен (timetable) по каждому маршруту."""
import re
import time
from datetime import timedelta
from .common import Fare, UA, log

try:  # curl_cffi имитирует TLS-отпечаток Chrome — Wizz реже блокирует
    from curl_cffi import requests as http
    IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover
    import requests as http
    IMPERSONATE = {}

FALLBACK_VERSION = "29.24.0"  # последняя известная (ноябрь 2026)
CHUNK_DAYS = 30


class Wizz:
    def __init__(self, delay=1.2, wdc=False):
        self.s = http.Session(**IMPERSONATE)
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://wizzair.com",
            "Referer": "https://wizzair.com/",
            "Content-Type": "application/json",
        })
        self.delay = delay
        self.wdc = wdc
        self.base = f"https://be.wizzair.com/{self._version()}/Api"

    def _ok(self, v):
        try:
            r = self.s.get(f"https://be.wizzair.com/{v}/Api/asset/currencies", timeout=15)
            return r.status_code != 404
        except Exception:  # noqa
            return False

    def _version(self):
        """Wizz меняет версию API каждые 1–2 недели. Ищем актуальную."""
        # 1) официальный buildnumber
        try:
            txt = self.s.get("https://wizzair.com/buildnumber", timeout=20).text
            m = re.search(r"be\.wizzair\.com/(\d+\.\d+\.\d+)", txt) or re.search(r"(\d+\.\d+\.\d+)", txt)
            if m and self._ok(m.group(1)):
                log.info("Wizz API версия (buildnumber): %s", m.group(1))
                return m.group(1)
            log.warning("Wizz: buildnumber не подошёл: %r", txt[:120])
        except Exception as e:  # noqa
            log.warning("Wizz: не получил buildnumber (%s)", e)
        # 2) перебор версий вокруг последней известной
        major, minor = (int(x) for x in FALLBACK_VERSION.split(".")[:2])
        cands = [f"{major}.{n}.{p}" for n in range(minor + 25, minor - 6, -1) for p in (0, 1)]
        cands += [f"{major + 1}.{n}.0" for n in range(0, 15)]
        for v in cands:
            if self._ok(v):
                log.info("Wizz API версия (перебор): %s", v)
                return v
        log.error("Wizz: актуальная версия API не найдена")
        return FALLBACK_VERSION

    def routes(self, origins):
        """{origin: {dest: city_name}}"""
        r = self.s.get(f"{self.base}/asset/map", params={"languageCode": "en-gb"}, timeout=30)
        r.raise_for_status()
        return parse_map(r.json(), origins)

    def timetable(self, origin, dest, out_range, ret_range=None):
        """out_range / ret_range: (from, to) или None. Обратный рейс — в том же запросе."""
        legs = []
        if out_range:
            legs.append({"departureStation": origin, "arrivalStation": dest,
                         "from": out_range[0].isoformat(), "to": out_range[1].isoformat()})
        if ret_range:
            legs.append({"departureStation": dest, "arrivalStation": origin,
                         "from": ret_range[0].isoformat(), "to": ret_range[1].isoformat()})
        body = {
            "flightList": legs,
            "priceType": "wdc" if self.wdc else "regular",
            "adultCount": 1, "childCount": 0, "infantCount": 0,
        }
        for attempt in range(3):
            r = self.s.post(f"{self.base}/search/timetable", json=body, timeout=30)
            if r.status_code == 429:
                wait = 20 * (attempt + 1)
                log.warning("Wizz 429 (лимит), жду %ss", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Wizz: слишком много 429")


def parse_map(data, origins):
    out = {}
    names = {c["iata"]: c.get("shortName") or c.get("name") or c["iata"] for c in data.get("cities", [])}
    for c in data.get("cities", []):
        if c["iata"] in origins:
            out[c["iata"]] = {k["iata"]: names.get(k["iata"], k["iata"]) for k in c.get("connections", [])}
    return out


def parse_timetable(data, dest_name, fx, key="outboundFlights"):
    out = []
    for f in data.get(key) or []:
        if f.get("priceType") != "price":       # soldOut / checkPrice — пропускаем
            continue
        pr = f.get("price") or {}
        amt = pr.get("amount")
        if not amt:
            continue
        out.append(Fare(
            airline="wizzair",
            origin=f["departureStation"],
            dest=f["arrivalStation"],
            dest_name=dest_name,
            date=f["departureDate"][:10],
            price=fx.to_base(float(amt), pr.get("currencyCode")),
            orig_price=float(amt),
            orig_ccy=pr.get("currencyCode", ""),
        ))
    return out


def _split(data, origin, dest_name, fx):
    """Разбирает ответ timetable на (туда, обратно) по аэропорту вылета —
    Wizz кладёт единственную ногу в outboundFlights, даже если это обратный рейс."""
    allf = parse_timetable(data, dest_name, fx, "outboundFlights") + parse_timetable(data, dest_name, fx, "returnFlights")
    outs = [f for f in allf if f.origin == origin]
    rets = [f for f in allf if f.origin != origin]
    for r in rets:
        r.dest_name = origin
    return outs, rets


def _clip(a, b, lo, hi):
    lo2, hi2 = max(a, lo), min(b, hi)
    return (lo2, hi2) if lo2 <= hi2 else None


def fetch_span(origins, out_from, out_to, ret_from=None, ret_to=None, fx=None, delay=1.2, wdc=False,
               dest_filter=lambda d: True):
    """Цены по дням: туда в [out_from, out_to], обратно (если задано) в [ret_from, ret_to].
    Возвращает (туда, обратно, ошибки)."""
    try:
        w = Wizz(delay=delay, wdc=wdc)
        net = w.routes(origins)
    except Exception as e:  # noqa
        log.error("Wizz: не удалось загрузить карту маршрутов: %s", e)
        return [], [], [f"Wizz Air недоступен: {e}"]

    outs, rets, errors = [], [], []
    first = min(out_from, ret_from or out_from)
    last = max(out_to, ret_to or out_to)
    for origin, dests in net.items():
        for dest, name in dests.items():
            if not dest_filter(dest):
                continue
            cur = first
            while cur <= last:
                to = cur + timedelta(days=CHUNK_DAYS - 1)
                o_rng = _clip(cur, to, out_from, out_to)
                r_rng = _clip(cur, to, ret_from, ret_to) if ret_from else None
                if o_rng or r_rng:
                    try:
                        o, r = _split(w.timetable(origin, dest, o_rng, r_rng), origin, name, fx)
                        outs += o
                        rets += r
                    except Exception as e:  # noqa
                        errors.append(f"{origin}-{dest}: {e}")
                        log.warning("Wizz %s-%s %s: %s", origin, dest, cur, e)
                    time.sleep(w.delay)
                cur = to + timedelta(days=1)
        log.info("Wizz %s: %d маршрутов обработано", origin, len(dests))
    return outs, rets, errors


def fetch(origins, start, end, fx, delay=1.2, wdc=False, dest_filter=lambda d: True, ret_extra_days=0):
    """Для регулярной проверки: туда в [start, end], обратно в [start, end + ret_extra_days]."""
    if ret_extra_days:
        return fetch_span(origins, start, end, start, end + timedelta(days=ret_extra_days), fx, delay, wdc, dest_filter)
    return fetch_span(origins, start, end, None, None, fx, delay, wdc, dest_filter)
