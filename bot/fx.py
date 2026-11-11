import requests
from .common import log

# Запасные курсы (за 1 EUR), если ECB/Frankfurter недоступен
FALLBACK = {"EUR": 1.0, "BGN": 1.95583, "RON": 4.97, "HUF": 395.0, "PLN": 4.27,
            "GBP": 0.84, "CZK": 25.2, "USD": 1.08}


class FX:
    def __init__(self, base="EUR"):
        self.base = base
        self.rates = dict(FALLBACK)
        try:
            r = requests.get(f"https://api.frankfurter.app/latest?from={base}", timeout=15)
            r.raise_for_status()
            self.rates.update(r.json()["rates"])
            self.rates[base] = 1.0
        except Exception as e:  # noqa
            log.warning("FX: использую запасные курсы (%s)", e)

    def to_base(self, amount: float, ccy: str) -> float:
        ccy = (ccy or self.base).upper()
        if ccy == self.base:
            return round(amount, 2)
        rate = self.rates.get(ccy)
        if not rate:
            log.warning("FX: нет курса для %s, оставляю как есть", ccy)
            return round(amount, 2)
        return round(amount / rate, 2)
