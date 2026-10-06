"""Accommodation search (Google Hotels via SerpApi, optionally vacation rentals). Total payable cost only."""
import statistics


def search_city(serp, city_key, q, check_in, check_out, trip, rentals=False):
    params = {"engine": "google_hotels", "q": q, "check_in_date": check_in, "check_out_date": check_out,
              "adults": trip["adults"], "children": len(trip["children_ages"]), "children_ages": ",".join(map(str, trip["children_ages"])),
              "currency": trip["currency"], "hl": "en", "gl": "in", "sort_by": 3}
    if rentals:
        params["vacation_rentals"] = "true"
        params["bedrooms"] = 2
    data, at, url = serp.get(params)
    nights = (_d(check_out) - _d(check_in)).days
    out = []
    for p in data.get("properties", []):
        total = (p.get("total_rate") or {}).get("extracted_lowest")
        nightly = (p.get("rate_per_night") or {}).get("extracted_lowest")
        if not total and nightly:
            total, status = nightly * nights, "UNVERIFIED (nightly x nights; fees/taxes may be missing)"
        else:
            status = "total as quoted by source" if total else "UNVERIFIED"
        out.append({"city": city_key, "name": p.get("name", "?"), "type": p.get("type", "rental" if rentals else "hotel"),
                    "check_in": check_in, "nights": nights, "nightly_inr": nightly, "total_inr": total,
                    "price_status": status, "rating": p.get("overall_rating"), "reviews": p.get("reviews"),
                    "sleeps_4_ok": "UNVERIFIED", "cancellation": "UNVERIFIED",
                    "source": "SAMPLE-MOCK" if serp.mock else "Google Hotels via SerpApi",
                    "source_url": p.get("link") or url, "searched_at": at, "currency": "INR"})
    return out


def _d(s):
    import datetime as dt
    return dt.date.fromisoformat(s)


def per_night_stats(rows, city):
    """Family nightly price low/real/high from verified totals only (p25 / median / p75)."""
    xs = sorted(r["total_inr"] / r["nights"] for r in rows
                if r["city"] == city and r["total_inr"] and r["nights"] and (r["rating"] or 0) >= 4.0)
    if len(xs) < 4:
        return None
    q = statistics.quantiles(xs, n=4)
    return {"low": q[0], "real": q[1], "high": q[2], "n": len(xs)}
