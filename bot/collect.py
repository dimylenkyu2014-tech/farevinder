"""Сбор цен по частям — для параллельного запуска на GitHub Actions.

Каждая параллельная задача берёт один город вылета и половину направлений (shard),
поэтому у каждой свой IP и свой лимит у Wizz Air -> всё вместе в ~8 раз быстрее.

python -m bot.collect daily --origin SOF --shard 0 --shards 2 --out part.json
python -m bot.collect query --origin SOF --shard 0 --shards 2 --out part.json   (запросы в env QUERIES)
"""
import argparse
import json
import logging
import os
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ryanair, wizz
from .common import from_dict, log
from .fx import FX

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("ow", "rt", "w_out", "w_ret")


def load_cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def empty():
    return {k: [] for k in KEYS} | {"errors": []}


def dest_filter(cfg, shard=0, shards=1):
    only = {d.upper() for d in cfg.get("only_destinations") or []}
    excl = {d.upper() for d in cfg.get("exclude_destinations") or []}

    def ok(d):
        if (only and d not in only) or d in excl:
            return False
        return zlib.crc32(d.encode()) % shards == shard
    return ok


def collect_daily(cfg, origins, shard=0, shards=1):
    cur = cfg.get("currency", "EUR")
    rtc = cfg.get("round_trip", {})
    rt_on = rtc.get("enabled", False)
    min_n, max_n = rtc.get("min_nights", 2), rtc.get("max_nights", 10)
    start = datetime.now(timezone.utc).date() + timedelta(days=1)
    end = start + timedelta(days=cfg.get("horizon_days", 180))
    fx = FX(cur)
    data = empty()
    if cfg["airlines"].get("ryanair") and shard == 0:
        data["ow"] += ryanair.fetch(origins, start, end, fx, cur)
        if rt_on:
            data["rt"] += ryanair.fetch_rt(origins, start, end, fx, min_n, max_n, cur)
    if cfg["airlines"].get("wizzair"):
        o, r, e = wizz.fetch(origins, start, end, fx, cfg.get("wizz_request_delay", 2.0),
                             cfg.get("wizz_discount_club", False), dest_filter(cfg, shard, shards),
                             ret_extra_days=max_n if rt_on else 0)
        data["w_out"], data["w_ret"], data["errors"] = o, r, e
    return data


def collect_query(q, cfg, origins, shard=0, shards=1):
    """Сырые цены под запрос (только указанные города вылета)."""
    cur = cfg.get("currency", "EUR")
    fx = FX(cur)
    data = empty()
    origins = [o for o in origins if o in q["origins"]]
    if not origins:
        return data
    ok = dest_filter(cfg, shard, shards)
    delay = cfg.get("wizz_request_delay", 2.0)
    wdc = cfg.get("wizz_discount_club", False)
    if q["mode"] == "rt":
        pairs = q["pairs"]
        if cfg["airlines"].get("ryanair") and shard == 0:
            data["rt"] = ryanair.fetch_rt_windows(origins, q["windows"], fx, cur)
        if cfg["airlines"].get("wizzair"):
            outs_d, rets_d = [p[0] for p in pairs], [p[1] for p in pairs]
            data["w_out"], data["w_ret"], data["errors"] = wizz.fetch_span(
                origins, min(outs_d), max(outs_d), min(rets_d), max(rets_d), fx, delay, wdc, ok)
    else:
        lo, hi = min(q["outs"]), max(q["outs"])
        if cfg["airlines"].get("ryanair") and shard == 0:
            data["ow"] = ryanair.fetch(origins, lo, hi, fx, cur)
        if cfg["airlines"].get("wizzair"):
            data["w_out"], _, data["errors"] = wizz.fetch_span(origins, lo, hi, None, None, fx, delay, wdc, ok)
    return data


def to_json(data):
    return {k: ([x.to_dict() for x in v] if k in KEYS else v) for k, v in data.items()}


def from_json(d):
    return {k: ([from_dict(x) for x in d.get(k, [])] if k in KEYS else d.get(k, [])) for k in (*KEYS, "errors")}


def merge(parts):
    out = empty()
    for p in parts:
        for k in out:
            out[k] += p.get(k, [])
    return out


def load_parts(folder):
    """-> список JSON-объектов из всех part-*.json"""
    res = []
    for f in sorted(Path(folder).rglob("*.json")):
        try:
            res.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa
            log.warning("Не прочитал %s: %s", f, e)
    return res


def main():
    from .qparse import parse, ParseError
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "query"])
    ap.add_argument("--origin", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_cfg()
    origin = a.origin.upper()
    enabled = [o.upper() for o in cfg["origins"]]

    if a.mode == "daily":
        res = to_json(collect_daily(cfg, [origin], a.shard, a.shards)) if origin in enabled else to_json(empty())
    else:
        res = {}
        for text in json.loads(os.environ.get("QUERIES") or "[]"):
            try:
                res[text] = to_json(collect_query(parse(text), cfg, [origin], a.shard, a.shards))
            except ParseError:
                pass
            except Exception as e:  # noqa
                log.exception("Ошибка сбора")
                res[text] = to_json(empty()) | {"errors": [f"{origin}: {e}"]}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    log.info("Сохранено в %s", a.out)


if __name__ == "__main__":
    main()
