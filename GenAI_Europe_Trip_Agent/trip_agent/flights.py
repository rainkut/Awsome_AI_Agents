"""Flight search, filtering and scoring (deterministic -- no LLM)."""
import json, datetime as dt
from .serp import QuotaExceeded

CLASS_AIRPORTS = {"PAR": "CDG,ORY"}


def build_params(origin, arr, dep, out_date, ret_date, trip):
    base = {"engine": "google_flights", "currency": trip["currency"], "hl": "en", "gl": "in",
            "adults": trip["adults"], "children": trip["children"], "travel_class": 1}
    if arr == dep:
        return {**base, "type": 1, "departure_id": origin, "arrival_id": arr,
                "outbound_date": out_date, "return_date": ret_date}
    legs = [{"departure_id": origin, "arrival_id": arr, "date": out_date},
            {"departure_id": dep, "arrival_id": origin, "date": ret_date}]
    return {**base, "type": 3, "multi_city_json": json.dumps(legs)}


def _mins(t):
    return dt.datetime.strptime(t, "%Y-%m-%d %H:%M")


def parse(data, origin, arr, dep, out_date, ret_date, searched_at, url, mock):
    rows = []
    for opt in data.get("best_flights", []) + data.get("other_flights", []):
        segs = opt.get("flights", [])
        if not segs:
            continue
        lay = opt.get("layovers", [])
        blob = json.dumps(opt).lower()
        rows.append({
            "origin": origin, "arr_airports": arr, "dep_airports": dep,
            "out_date": out_date, "ret_date": ret_date,
            "airline": "/".join(dict.fromkeys(s.get("airline", "?") for s in segs)),
            "flight_numbers": " ".join(s.get("flight_number", "?") for s in segs),
            "dep_time": segs[0]["departure_airport"].get("time", ""),
            "arr_time": segs[-1]["arrival_airport"].get("time", ""),
            "stops": len(segs) - 1,
            "layovers": "; ".join(f'{l.get("id","?")} {l.get("duration",0)}m' for l in lay),
            "max_layover_min": max([l.get("duration", 0) for l in lay] or [0]),
            "overnight_layover": any(l.get("overnight") for l in lay),
            "self_transfer_flag": "self" in blob and "transfer" in blob,
            "airport_change_flag": any(a["arrival_airport"]["id"] != b["departure_airport"]["id"]
                                       for a, b in zip(segs, segs[1:])),
            "total_minutes": opt.get("total_duration", 0),
            "price_inr_family": opt.get("price"),
            "baggage": "UNVERIFIED", "fare_conditions": "UNVERIFIED",
            "return_leg": "NOT FETCHED (departure_token)" if opt.get("departure_token") else "",
            "departure_token": opt.get("departure_token", ""),
            "source": "SAMPLE-MOCK" if mock else "Google Flights via SerpApi", "source_url": url,
            "searched_at": searched_at, "currency": "INR", "inr": opt.get("price"),
        })
    return rows


def reject_reason(r, ff):
    if r["price_inr_family"] in (None, 0):
        return "no price (unverifiable)"
    if r["total_minutes"] > ff["max_total_minutes_one_way"]:
        return "journey too long"
    if r["overnight_layover"] and ff["reject_overnight_layover"]:
        return "overnight layover"
    if r["max_layover_min"] > ff["max_layover_minutes"]:
        return "layover too long"
    for part in r["layovers"].split("; "):
        if part and int(part.split()[-1][:-1]) < ff["min_layover_minutes"]:
            return "connection too tight"
    if r["self_transfer_flag"]:
        return "self-transfer risk"
    if r["airport_change_flag"]:
        return "airport change"
    return ""


def score_flights(rows, cfg):
    w, aq = cfg["flight_score_weights"], cfg["airline_quality"]
    ok = [r for r in rows if not r["reject"]]
    if not ok:
        return
    lo, hi = min(r["price_inr_family"] for r in ok), max(r["price_inr_family"] for r in ok)
    dlo, dhi = min(r["total_minutes"] for r in ok), max(r["total_minutes"] for r in ok)
    rng = lambda v, a, b: 100 if b == a else 100 * (b - v) / (b - a)
    for r in ok:
        hour = _mins(r["dep_time"]).hour if r["dep_time"] else 12
        ahour = _mins(r["arr_time"]).hour if r["arr_time"] else 12
        timing = 100 - (35 if hour < 6 else 0) - (35 if ahour < 5 or ahour >= 23 else 0)
        airline = sum(aq.get(a, aq["default"]) for a in r["airline"].split("/")) / len(r["airline"].split("/"))
        parts = {"price": rng(r["price_inr_family"], lo, hi), "duration": rng(r["total_minutes"], dlo, dhi),
                 "stops": {0: 100, 1: 70, 2: 30}.get(r["stops"], 0), "timing": timing,
                 "baggage": 50, "airline": airline,  # baggage UNVERIFIED -> neutral
                 "family": max(0, 100 - 25 * r["stops"] - (30 if r["max_layover_min"] > 240 else 0))}
        r["score"] = round(sum(parts[k] * w[k] for k in w) / sum(w.values()), 1)


def search_pair(serp, origin, arr, dep, out_date, ret_date, cfg):
    params = build_params(origin, arr, dep, out_date, ret_date, cfg["trip"])
    data, at, url = serp.get(params)
    rows = parse(data, origin, arr, dep, out_date, ret_date, at, url, serp.mock is not None)
    for r in rows:
        r["reject"] = reject_reason(r, cfg["flight_filter"])
    score_flights(rows, cfg)
    return rows
