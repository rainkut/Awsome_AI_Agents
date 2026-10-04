"""Deterministic FAKE SerpApi responses so the pipeline can be tested offline. Never real prices."""
import hashlib, json, random


class Mock:
    def respond(self, p):
        rnd = random.Random(int(hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest(), 16) % 10**9)
        if p["engine"] == "google_flights":
            return self.flights(p, rnd)
        return self.hotels(p, rnd)

    def flights(self, p, rnd):
        if p["type"] == 3:
            legs = json.loads(p["multi_city_json"]); o, a, d = legs[0]["departure_id"], legs[0]["arrival_id"], legs[1]["departure_id"]
        else:
            o, a, d = p["departure_id"], p["arrival_id"], p["arrival_id"]
        base = {"AMD": 215000, "BOM": 190000, "DEL": 180000}[o] + (12000 if a != d else 0) + rnd.randint(-15000, 25000)
        opts = []
        for i in range(5):
            hub = rnd.choice(["DOH", "DXB", "IST", "AUH", "FRA"])
            lay = rnd.choice([95, 140, 200, 280, 420])
            opts.append({"flights": [
                {"departure_airport": {"id": o, "time": "2027-05-27 0%d:15" % rnd.randint(1, 9)},
                 "arrival_airport": {"id": hub, "time": "2027-05-27 14:00"}, "airline": rnd.choice(["Qatar Airways", "Emirates", "Turkish Airlines", "Air India"]),
                 "flight_number": "XX%d" % rnd.randint(100, 999)},
                {"departure_airport": {"id": hub, "time": "2027-05-27 17:00"},
                 "arrival_airport": {"id": a.split(",")[0], "time": "2027-05-28 0%d:40" % rnd.randint(6, 9)},
                 "airline": "Qatar Airways", "flight_number": "XX%d" % rnd.randint(100, 999)}],
                "layovers": [{"id": hub, "duration": lay, "overnight": lay > 400}],
                "total_duration": 720 + lay + rnd.randint(0, 200), "price": base + i * rnd.randint(2000, 12000),
                "departure_token": "tok%d" % i})
        return {"search_metadata": {"google_flights_url": "SAMPLE://flights"}, "best_flights": opts[:2], "other_flights": opts[2:]}

    def hotels(self, p, rnd):
        n = 10
        nights = 5
        props = []
        for i in range(n):
            nightly = rnd.randint(14000, 45000)
            props.append({"name": f"SAMPLE {p['q'][:12]} #{i}", "overall_rating": round(rnd.uniform(3.8, 4.8), 1),
                          "reviews": rnd.randint(40, 3000), "rate_per_night": {"extracted_lowest": nightly},
                          "total_rate": {"extracted_lowest": int(nightly * nights * 1.12)} if i % 3 else {}, "link": "SAMPLE://hotel"})
        return {"search_metadata": {"google_hotels_url": "SAMPLE://hotels"}, "properties": props}
