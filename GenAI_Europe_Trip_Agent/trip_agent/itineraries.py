"""Route generation, feasibility, total-cost build-up and weighted scoring (deterministic)."""
import itertools, statistics

ORDERS = {"PAR": ("PAR",), "PAR+CH": ("PAR", "CH"), "PAR+IT": ("PAR", "IT"), "PAR+CH+IT": ("PAR", "CH", "IT")}


def route_variants(cfg):
    c = cfg["cities"]
    out = []
    for name, stops in ORDERS.items():
        for perm in set(itertools.permutations(stops)):
            if "PAR" not in perm:
                continue
            for ch in (c["CH"]["gateways"] if "CH" in perm else [None]):
                for it in (c["IT"]["gateways"] if "IT" in perm else [None]):
                    gw = {"PAR": c["PAR"]["airports"], "CH": ch, "IT": c["IT"]["gateways"].get(it) if it else None}
                    stay = {"PAR": "PAR", "CH": "CH", "IT": it}
                    seq = [stay[s] for s in perm]
                    out.append({"category": name, "stops": perm, "ch_gw": ch, "it_city": it,
                                "arr": gw[perm[0]], "dep": gw[perm[-1]],
                                "label": " > ".join(f"{stay[s]}" + (f"({ch})" if s == "CH" else "") for s in perm)})
    return out


def _tkey(a, b):
    return "-".join(sorted([a, b], key=lambda x: ["PAR", "CH", "MIL", "ROM"].index(x)))


def transfers(route, cfg):
    seq = [("MIL" if s == "IT" and route["it_city"] == "MIL" else "ROM" if s == "IT" else s) for s in route["stops"]]
    legs = []
    for a, b in zip(seq, seq[1:]):
        legs.append(cfg["transfers"].get(_tkey(a, b)))
    return seq, legs


def allocate(route, days, disney_days, cfg):
    """Returns {stop: nights} or None if infeasible/unreasonable."""
    seq, legs = transfers(route, cfg)
    if any(l is None or l["hours"] > cfg["max_transfer_hours"] for l in legs):
        return None
    nights = days - 2
    mins = {s: cfg["cities"][s]["min_nights"] for s in route["stops"]}
    mins["PAR"] = max(mins["PAR"], disney_days + 2)
    if sum(mins.values()) + 0 > nights:
        return None
    alloc = dict(mins)
    spare = nights - sum(mins.values())
    pri = {"PAR": 3, "CH": 2, "IT": 1}
    order = sorted(route["stops"], key=lambda s: -pri[s])
    while spare:
        for s in order:
            if spare:
                alloc[s] += 1; spare -= 1
    return alloc


def _pick(d, scen):
    return d[scen]


def cost(route, alloc, days, disney_days, flight_inr, origin, cfg, stay_stats, positioning_inr):
    fx, fc = cfg["fx"], cfg["fixed_costs_eur"]
    seq, legs = transfers(route, cfg)
    res = {}
    for scen in ("low", "real", "high"):
        acc = 0
        for s, n in alloc.items():
            st = (stay_stats or {}).get(s)
            if st:
                acc += st[scen] * n
            else:
                cc = cfg["cities"][s]
                eur = cc.get("hotel_night_family_eur") or {k: v * fx["CHF"] / fx["EUR"] for k, v in cc["hotel_night_family_chf"].items()}
                acc += eur[scen] * n * fx["EUR"]
        trains = sum(l["eur"][scen] for l in legs) * fx["EUR"]
        local = (alloc.get("PAR", 0) * fc["paris_local_per_day"][scen] + alloc.get("CH", 0) * fc["swiss_local_per_day"][scen]
                 + 2 * fc["airport_transfer_one_way"][scen]) * fx["EUR"]
        disney = (fc["disney"][disney_days][scen] + disney_days * fc["disney"]["transport_per_day"][scen]) * fx["EUR"]
        attr = sum(fc["attractions_per_city"][s][scen] for s in route["stops"]) * fx["EUR"]
        food_key = {"low": "cook", "real": "cook", "high": "eat_out"}[scen]
        food_eur = (fc["food_per_day"][food_key][scen] if scen != "real"
                    else 0.5 * fc["food_per_day"]["cook"]["real"] + 0.5 * fc["food_per_day"]["eat_out"]["real"])
        food = food_eur * (days - 2) * fx["EUR"]
        visa = fc["schengen_visa_family_eur"] * fx["EUR"] + fc["visa_service_fee_inr"]
        ins = fc["insurance_family_inr"][scen]
        sub = flight_inr + positioning_inr + acc + trains + local + disney + attr + food + visa + ins
        res[scen] = {"flights": flight_inr, "positioning": positioning_inr, "accommodation": acc, "trains": trains,
                     "local": local, "disney": disney, "attractions": attr, "food": food, "visa": visa,
                     "insurance": ins, "misc": sub * fc["misc_pct"], "total": sub * (1 + fc["misc_pct"])}
    return res


def soft_scores(route, alloc, days, disney_days, flight_score, cfg):
    seq, legs = transfers(route, cfg)
    hours = sum(l["hours"] for l in legs)
    changes = len(route["stops"]) - 1
    comfort = 100 - 12 * changes - 3 * hours + (6 if disney_days == 2 else 0) - (8 if days == 7 else 0) - (8 if days >= 10 and changes >= 2 else 0)
    attr = min(100, sum(cfg["cities"][s]["attraction_score"] * alloc[s] for s in alloc) / sum(alloc.values()) + 5 * (len(alloc) - 1))
    transport = 100 - 7 * hours - 6 * changes
    return {"comfort": max(0, comfort), "attractions": attr, "transport": max(0, transport),
            "flights": flight_score or 0}


def weighted(parts, w):
    return round(sum(parts[k] * w[k] for k in w) / sum(w.values()), 1)
