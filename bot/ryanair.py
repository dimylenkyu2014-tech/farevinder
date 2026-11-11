"""Ryanair Fare Finder: самый дешёвый тариф в каждое направление за период."""
import time
from datetime import date, timedelta
import requests
from .common import Fare, RoundTrip, UA, log, month_windows

URL = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"


def parse(data: dict, fx) -> list:
    out = []
    for item in data.get("fares", []):
        ob = item.get("outbound") or {}
        pr = ob.get("price") or {}
        if pr.get("value") is None:
            continue
        arr = ob.get("arrivalAirport", {})
        name = (arr.get("city") or {}).get("name") or arr.get("name") or arr.get("iataCode")
        out.append(Fare(
            airline="ryanair",
            origin=ob["departureAirport"]["iataCode"],
            dest=arr["iataCode"],
            dest_name=name,
            date=ob["departureDate"][:10],
            price=fx.to_base(float(pr["value"]), pr.get("currencyCode")),
            orig_price=float(pr["value"]),
            orig_ccy=pr.get("currencyCode", "EUR"),
        ))
    return out


def fetch(origins, start, end, fx, currency="EUR") -> list:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    fares = []
    for origin in origins:
        for d_from, d_to in month_windows(start, end):
            params = {
                "departureAirportIataCode": origin,
                "outboundDepartureDateFrom": d_from.isoformat(),
                "outboundDepartureDateTo": d_to.isoformat(),
                "currency": currency,
                "market": "en-gb",
                "adultPaxCount": 1,
                "language": "en",
            }
            try:
                r = s.get(URL, params=params, timeout=30)
                if r.status_code == 404:   # нет рейсов Ryanair из аэропорта
                    break
                r.raise_for_status()
                got = parse(r.json(), fx)
                fares += got
                log.info("Ryanair %s %s: %d направлений", origin, d_from.strftime("%Y-%m"), len(got))
            except Exception as e:  # noqa
                log.warning("Ryanair %s %s: ошибка %s", origin, d_from, e)
            time.sleep(0.5)
    return fares


RT_URL = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"


def parse_rt(data: dict, fx) -> list:
    out = []
    for item in data.get("fares", []):
        ob, ib = item.get("outbound") or {}, item.get("inbound") or {}
        try:
            op, ip = ob["price"], ib["price"]
            o_eur = fx.to_base(float(op["value"]), op.get("currencyCode"))
            i_eur = fx.to_base(float(ip["value"]), ip.get("currencyCode"))
        except (KeyError, TypeError):
            continue
        arr = ob.get("arrivalAirport", {})
        d_out, d_in = ob["departureDate"][:10], ib["departureDate"][:10]
        out.append(RoundTrip(
            airline="ryanair", origin=ob["departureAirport"]["iataCode"], dest=arr["iataCode"],
            dest_name=(arr.get("city") or {}).get("name") or arr.get("name") or arr["iataCode"],
            date=d_out, ret_date=d_in, ret_to=ib["arrivalAirport"]["iataCode"],
            nights=(date.fromisoformat(d_in) - date.fromisoformat(d_out)).days,
            price=round(o_eur + i_eur, 2), out_price=o_eur, ret_price=i_eur,
        ))
    return out


def fetch_rt(origins, start, end, fx, min_n, max_n, currency="EUR") -> list:
    """Самый дешёвый туда-обратно в каждое направление, по месяцам вылета."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    trips = []
    for origin in origins:
        for d_from, d_to in month_windows(start, end):
            params = {
                "departureAirportIataCode": origin,
                "outboundDepartureDateFrom": d_from.isoformat(),
                "outboundDepartureDateTo": d_to.isoformat(),
                "inboundDepartureDateFrom": (d_from + timedelta(days=min_n)).isoformat(),
                "inboundDepartureDateTo": (d_to + timedelta(days=max_n)).isoformat(),
                "durationFrom": min_n, "durationTo": max_n,
                "currency": currency, "market": "en-gb", "adultPaxCount": 1, "language": "en",
            }
            try:
                r = s.get(RT_URL, params=params, timeout=30)
                if r.status_code == 404:
                    break
                r.raise_for_status()
                got = parse_rt(r.json(), fx)
                trips += got
                log.info("Ryanair RT %s %s: %d направлений", origin, d_from.strftime("%Y-%m"), len(got))
            except Exception as e:  # noqa
                log.warning("Ryanair RT %s %s: ошибка %s", origin, d_from, e)
            time.sleep(0.5)
    return trips
