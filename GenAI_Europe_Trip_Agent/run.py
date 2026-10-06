#!/usr/bin/env python3
"""Europe family trip research agent.  python run.py --mock   (offline pipeline test, FAKE data)
                                      python run.py --live   (real SerpApi searches; needs SERPAPI_KEY)"""
import argparse, csv, datetime as dt, os, pathlib, sys, yaml
from trip_agent.serp import Serp, QuotaExceeded
from trip_agent import flights as F, hotels as H, itineraries as I

HERE = pathlib.Path(__file__).parent
PAR_AREAS = ["Montmartre Paris", "Latin Quarter Paris", "Bastille Paris", "Marne-la-Vallee Disneyland Paris"]
CH_AREAS = ["Interlaken", "Interlaken Ost", "Lauterbrunnen", "Grindelwald", "Zurich"]
IT_AREAS = {"ROM": "Rome", "MIL": "Milan, Italy"}


def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())


def write_csv(path, rows, cols=None):
    cols = cols or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def inr(x):
    return f"₹{x:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--live", action="store_true")
    ap.add_argument("--fallback", action="store_true", help="use fallback (March) departure date")
    ap.add_argument("--max-calls", type=int)
    a = ap.parse_args()
    if a.mock == a.live:
        sys.exit("choose exactly one of --mock / --live")
    load_env()
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    trip = cfg["trip"]
    depart = dt.date.fromisoformat(str(trip["fallback_depart_date" if a.fallback else "depart_date"]))
    mock = None
    if a.mock:
        from trip_agent.mock import Mock; mock = Mock()
    elif not os.environ.get("SERPAPI_KEY"):
        sys.exit("SERPAPI_KEY missing")
    tag = "_SAMPLE_FAKE_DATA" if a.mock else ""
    out = HERE / ("out" + tag) / "europe_trip_research"; (out / "raw").mkdir(parents=True, exist_ok=True)
    serp = Serp(os.environ.get("SERPAPI_KEY"), cache_dir=(out / "raw"), max_calls=a.max_calls or cfg["search"]["max_api_calls"], mock=mock)

    routes = I.route_variants(cfg)
    pairs = sorted({(r["arr"], r["dep"]) for r in routes})
    fl_rows, notes = [], []
    dates = lambda n: (depart.isoformat(), (depart + dt.timedelta(days=n - 1)).isoformat())

    def run_flights(origin, pair, n):
        o, r_ = dates(n)
        try:
            rows = F.search_pair(serp, origin, pair[0], pair[1], o, r_, cfg)
        except QuotaExceeded as e:
            notes.append(f"call cap hit: {e}"); return []
        for x in rows: x["duration_days"] = n
        fl_rows.extend(rows); return rows

    # Stage 1: every origin x airport pair at the coarse duration
    cd = cfg["search"]["coarse_duration"]
    best = {}
    for og in cfg["origins"]:
        for p in pairs:
            ok = [x for x in run_flights(og, p, cd) if not x["reject"]]
            if ok: best[(og, p)] = min(x["price_inr_family"] for x in ok)
    # Stage 2: other durations only for the cheapest combos
    top = sorted(best, key=best.get)[: cfg["search"]["top_pairs_for_other_durations"] * len(cfg["origins"])]
    for og, p in top:
        for n in trip["durations"]:
            if n != cd: run_flights(og, p, n)

    # Stage 3: accommodation (hotels + vacation rentals), representative 5-night stay
    ci = (depart + dt.timedelta(days=1)); co = ci + dt.timedelta(days=5)
    ac_rows = []
    areas = [("PAR", x) for x in PAR_AREAS] + [("CH", x) for x in CH_AREAS] + [(k, v) for k, v in IT_AREAS.items()]
    for key, area in areas:
        for rent in (False, True):
            try:
                rows = H.search_city(serp, key, f"{area} {'apartment' if rent else 'aparthotel family'}", ci.isoformat(), co.isoformat(), trip, rentals=rent)
            except QuotaExceeded as e:
                notes.append(f"call cap hit: {e}"); rows = []
            for r in rows: r["area"], r["kind"] = area, "rental" if rent else "hotel"
            ac_rows.extend(rows)
    seen, dedup = set(), []
    for r in ac_rows:
        k = (r["name"].lower(), r["area"])
        if k in seen or not r["total_inr"]: continue
        seen.add(k); dedup.append(r)
    stats = {k: H.per_night_stats(dedup, k) for k in ("PAR", "CH", "ROM", "MIL")}

    # Stage 4: itineraries
    ov = (yaml.safe_load((HERE / "judgments.yaml").read_text()) or {}).get("overrides", {})
    index = {}
    for x in fl_rows:
        if not x["reject"]:
            index.setdefault((x["origin"], x["arr_airports"], x["dep_airports"], x["duration_days"]), []).append(x)
    it_rows = []
    for og in cfg["origins"]:
        for rt in routes:
            for n in trip["durations"]:
                for dd in (1, 2):
                    alloc = I.allocate(rt, n, dd, cfg)
                    if not alloc: continue
                    cand = index.get((og, rt["arr"], rt["dep"], n))
                    proxy = False
                    if not cand:
                        cand = index.get((og, rt["arr"], rt["dep"], cd)); proxy = True
                    if not cand: continue
                    f = max(cand, key=lambda x: x["score"])
                    st = {"PAR": stats["PAR"], "CH": stats["CH"], "IT": stats.get(rt["it_city"])}
                    pos = cfg["positioning"][og]
                    pos_inr = pos["family_return_inr"] + pos["extra_hotel_nights"] * pos.get("hotel_night_inr", 0) + pos.get("friction_inr", 0)
                    c = I.cost(rt, alloc, n, dd, f["price_inr_family"], og, cfg, st, pos_inr)
                    sc = I.soft_scores(rt, alloc, n, dd, f["score"], cfg)
                    label = f'{rt["label"]}|{n}d|disney{dd}'
                    sc.update(ov.get(label, {}))
                    elim = []
                    if sc["comfort"] < 45: elim.append("poor family comfort")
                    if sc["transport"] < 35: elim.append("excess transfers")
                    it_rows.append({"label": label, "category": rt["category"], "origin": og, "days": n, "disney_days": dd,
                                    "countries": 1 + ("CH" in rt["stops"]) + ("IT" in rt["stops"]),
                                    "nights": "/".join(f"{k}{v}" for k, v in alloc.items()),
                                    "flight": f'{f["airline"]} {f["stops"]}stop {f["total_minutes"]}m',
                                    "flight_price_proxy_from_coarse_duration": proxy,
                                    "cost_low": c["low"]["total"], "cost_real": c["real"]["total"], "cost_high": c["high"]["total"],
                                    "breakdown_real": {k: round(v) for k, v in c["real"].items()}, "parts": sc, "route": rt,
                                    "alloc": alloc, "eliminated": "; ".join(elim),
                                    "source": "SAMPLE-MOCK" if mock else "SerpApi + config ESTIMATES",
                                    "researched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    live = [r for r in it_rows if not r["eliminated"]]
    if live:
        lo = min(r["cost_real"] for r in live); prices = [r["cost_real"] for r in live]
        acc_lo = min(r["breakdown_real"]["accommodation"] for r in live)
        for r in live:
            r["parts"]["cost"] = 100 * lo / r["cost_real"]
            r["parts"]["accommodation"] = 100 * acc_lo / max(1, r["breakdown_real"]["accommodation"])
            r["score"] = I.weighted(r["parts"], cfg["itinerary_score_weights"])
        live.sort(key=lambda r: -r["score"])

    # Write outputs
    clean = lambda rows: [{k: v for k, v in r.items() if not isinstance(v, (dict, tuple))} for r in rows]
    write_csv(out / "flights.csv", fl_rows)
    write_csv(out / "accommodations.csv", dedup)
    write_csv(out / "transport.csv", [{"leg": k, "hours_ESTIMATE": v["hours"], **{f"eur_{s}": v["eur"][s] for s in ("low", "real", "high")},
                                        "status": "ESTIMATE_UNVERIFIED", "source_url": "", "researched_at": cfg["assumptions_date"]} for k, v in cfg["transfers"].items()])
    fc = cfg["fixed_costs_eur"]
    write_csv(out / "attractions.csv", [{"item": f"Disneyland {d}-day family", **{s: fc["disney"][d][s] for s in ("low", "real", "high")}, "currency": "EUR",
                                          "status": "ESTIMATE_UNVERIFIED", "source_url": "", "researched_at": cfg["assumptions_date"]} for d in (1, 2)] +
              [{"item": f"Attractions {c}", **fc["attractions_per_city"][c], "currency": "EUR", "status": "ESTIMATE_UNVERIFIED", "source_url": "", "researched_at": cfg["assumptions_date"]} for c in fc["attractions_per_city"]])
    write_csv(out / "itineraries.csv", clean(live + [r for r in it_rows if r["eliminated"]]))
    bud = []
    for r in live[:25]:
        for k, v in r["breakdown_real"].items():
            bud.append({"itinerary": r["label"], "origin": r["origin"], "scenario": "real", "item": k, "inr": v, "fx": f'EUR={cfg["fx"]["EUR"]} CHF={cfg["fx"]["CHF"]} (ESTIMATE)'})
    write_csv(out / "budget.csv", bud)

    # Report
    L = [f"# Final recommendation {'(SAMPLE / FAKE DATA - pipeline test only)' if mock else ''}", "",
         f"Departure {depart} | durations {trip['durations']} | family 2A+2C | API calls used: {serp.live_calls} | FX EUR={cfg['fx']['EUR']} CHF={cfg['fx']['CHF']} (ESTIMATE)", ""]
    if notes: L += ["Notes: " + "; ".join(sorted(set(notes))), ""]
    L += ["| Rank | Itinerary | Origin | Days | Countries | Nights | Flight | Disney | Est. Total (realistic) | Score |", "|---|---|---|---:|---:|---|---|---|---:|---:|"]
    for i, r in enumerate(live[:15], 1):
        L.append(f'| {i} | {r["label"].split("|")[0]} | {r["origin"]} | {r["days"]} | {r["countries"]} | {r["nights"]} | {r["flight"]} | {r["disney_days"]}d | {inr(r["cost_real"])} | {r["score"]} |')
    if live:
        b = live[0]
        cheap = min((r for r in live if r["score"] >= b["score"] - 8), key=lambda r: r["cost_real"])
        exp = max((r for r in live if r["cost_real"] <= 1.25 * b["cost_real"]), key=lambda r: r["parts"]["attractions"] + r["parts"]["comfort"])
        L += ["", f'## BEST OVERALL\n{b["label"]} from {b["origin"]} - {inr(b["cost_real"])} (low {inr(b["cost_low"])} / high {inr(b["cost_high"])}), score {b["score"]}',
              f'\n## CHEAPEST GOOD OPTION\n{cheap["label"]} from {cheap["origin"]} - {inr(cheap["cost_real"])}, score {cheap["score"]}',
              f'\n## BEST EXPERIENCE\n{exp["label"]} from {exp["origin"]} - {inr(exp["cost_real"])}, score {exp["score"]}', "", "## Data-derived answers"]
        by = lambda key: {k: max((r for r in live if key(r) == k), key=lambda r: r["score"]) for k in {key(r) for r in live}}
        L.append("1. Origin: " + "; ".join(f'{k} best {inr(v["cost_real"])} (score {v["score"]})' for k, v in by(lambda r: r["origin"]).items()))
        L.append("2-4. Route category: " + "; ".join(f'{k} best {inr(v["cost_real"])} (score {v["score"]})' for k, v in by(lambda r: r["category"]).items()))
        L.append("5. Duration: " + "; ".join(f'{k}d best score {v["score"]} at {inr(v["cost_real"])}' for k, v in sorted(by(lambda r: r["days"]).items())))
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
        for key in ("PAR", "CH", "ROM", "MIL"):
            for area in sorted({r["area"] for r in dedup if r["city"] == key}):
                for kind in ("hotel", "rental"):
                    xs = [r["total_inr"] / r["nights"] for r in dedup if r["area"] == area and r["kind"] == kind and (r["rating"] or 0) >= 4]
                    if xs: L.append(f"- {area} [{kind}] median ₹/night (rating>=4, n={len(xs)}): {inr(med(xs))}")
        L += ["", "## Day-by-day skeleton for BEST OVERALL (refine after live data)", "- Day 1: depart India (overnight flight)"]
        day = 2; seq = b["route"]["stops"]
        for si, s in enumerate(seq):
            n = b["alloc"][s]
            L.append(f"- Day {day}: " + ("arrive " if si == 0 else "train/flight to ") + f"{s}; check in, light afternoon, early night"); day += 1
            for k in range(n - 1):
                if s == "PAR" and k < b["disney_days"]: act = f"Disneyland Paris (RER A, book tickets ahead) - day {k+1} of {b['disney_days']}"
                elif s == "PAR": act = "Paris sightseeing (1 major sight + park/playground; keep afternoons free)"
                elif s == "CH": act = "Swiss day: scenic train/cable car + short family walk"
                else: act = "Italy sightseeing (1 major site, long lunch)"
                L.append(f"- Day {day}: {s} - {act}"); day += 1
        L.append(f"- Day {b['days']}: transfer to airport, fly home")
        L += ["", "## Booking order (default; confirm after live data)", "1. Flights (fares move fastest)", "2. Schengen visa appointments (apply 3 months ahead - do this in parallel)",
              "3. Accommodation (free-cancellation)", "4. Disneyland tickets (date-specific)", "5. Intercity trains (SBB/SNCF open ~3-4 months ahead)", "6. Insurance, 7. Other attractions"]
    else:
        L += ["", "No viable itineraries - no flight data yet (run with --live and a reachable SerpApi)."]
    (out / "final_recommendation.md").write_text("\n".join(L))
    (out / "research_notes.md").write_text(f"# Research notes\n\n- Flights: {len(fl_rows)} option rows, {len([r for r in fl_rows if not r['reject']])} accepted\n"
        f"- Accommodation: {len(dedup)} unique properties\n- Itineraries built: {len(it_rows)}, eliminated: {len(it_rows)-len(live)}\n"
        f"- Positioning, rail, Disney, attractions, food, visa, insurance are config ESTIMATES until verified\n- Baggage/fare conditions/cancellation are UNVERIFIED (not in the source data)\n"
        f"- Return-leg details need a departure_token call (not fetched)\n- Live API calls this run: {serp.live_calls}\n" + "\n".join(f"- {n}" for n in set(notes)))
    print(f"calls={serp.live_calls} flights={len(fl_rows)} stays={len(dedup)} itineraries={len(live)} -> {out}")


if __name__ == "__main__":
    main()
