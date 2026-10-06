"""SerpApi client: disk cache, hard call cap, raw-response audit trail, optional mock."""
import hashlib, json, pathlib, datetime as dt
import requests

API = "https://serpapi.com/search.json"


class QuotaExceeded(Exception):
    pass


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Serp:
    def __init__(self, api_key, cache_dir="cache", max_calls=120, mock=None):
        self.key, self.max_calls, self.mock = api_key, max_calls, mock
        self.dir = pathlib.Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.live_calls = 0

    def get(self, params):
        """Returns (data, searched_at, source_url). Cached results cost nothing."""
        ident = json.dumps(params, sort_keys=True)
        h = hashlib.sha1(ident.encode()).hexdigest()[:16]
        f = self.dir / f"{params['engine']}_{h}.json"
        if f.exists():
            rec = json.loads(f.read_text())
        else:
            if self.mock:
                data = self.mock.respond(params)
            else:
                if self.live_calls >= self.max_calls:
                    raise QuotaExceeded(f"call cap {self.max_calls} reached")
                r = requests.get(API, params={**params, "api_key": self.key}, timeout=60)
                if not r.ok:
                    raise RuntimeError(f"SerpApi {r.status_code}: {r.text[:300]}")
                data = r.json()
                self.live_calls += 1
            rec = {"params": params, "searched_at": now(), "mock": bool(self.mock), "data": data}
            if not (isinstance(data, dict) and data.get('error')):
                f.write_text(json.dumps(rec))
        md = rec["data"].get("search_metadata", {})
        url = md.get("google_flights_url") or md.get("google_hotels_url") or md.get("json_endpoint", "")
        return rec["data"], rec["searched_at"], url
