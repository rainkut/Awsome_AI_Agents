# Europe Family Trip Research Agent

Pipeline that searches flights (incl. open-jaw) and accommodation via SerpApi, builds every sensible
Paris / Switzerland / Italy itinerary for 7-10 days, computes total trip cost in INR (low / realistic / high)
and ranks them with weighted scores. Deterministic code does the searching, pricing and scoring;
judgment calls (soft-score overrides, final write-up) are made afterwards on the saved data.

```
pip install -r requirements.txt
cp .env.example .env            # put SERPAPI_KEY in .env (gitignored)
python run.py --mock            # offline pipeline test, FAKE prices, writes out_SAMPLE_FAKE_DATA/
python run.py --live            # real searches, writes out/europe_trip_research/
python run.py --live --fallback # March departure instead of May
```

Edit `config.yaml` for dates, airports, weights, FX and all ESTIMATE cost tables.
`judgments.yaml` overrides soft scores per itinerary.

## Honest limits
- Flight prices are Google Flights fares via SerpApi, not bookable guarantees -> verify on the airline site.
- Baggage, fare conditions, cancellation and "sleeps 4" are marked UNVERIFIED (not in the data).
- Rail, Disney, attractions, food, visa, insurance and positioning costs are dated config ESTIMATES until checked.
- Airbnb has no API; Google Hotels' vacation-rentals results are used instead (partial coverage).
- Call cap (`search.max_api_calls`) and a disk cache protect the SerpApi quota; stage 1 searches one duration, stage 2 only the cheapest combos.
