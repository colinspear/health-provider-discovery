# Provider Finder

Find and rank health care providers near you, filtered to your insurance network,
with a list view and a map. It runs locally, needs no accounts, and uses only free
public data.

- **Top picks**: the best 5 for a service (primary care, dermatology, hospitals,
  urgent care, therapy, and 18 more), each with a reason it was picked.
- **Browse all**: every provider within your radius. You can sort and filter it.
- **Map**: pins colored by network status, with your top picks numbered.
- **Detail panel**: a breakdown of the score, hospital affiliations, phone number,
  directions, and a button to check network status on demand.

## Quick start

```bash
pip install -e '.[dev]'
python -m finder --demo        # fictional data, works offline
cp config.example.yaml config.yaml   # set your ZIP + network
python -m finder               # real data → http://127.0.0.1:8765
```

The first search in an area makes a lot of API calls (NPPES once per ZIP code, plus CMS
and the geocoder), so it can take 30–90 seconds. Responses are cached in
`data/cache.sqlite` for 1–2 weeks, so later searches are fast.

## Connect your network

The NPPES registry lists every provider, but it doesn't know which ones are in your
network. Pick one of these three sources in `config.yaml`:

| `network.type` | What you do | Accuracy |
|---|---|---|
| `fhir` | Set `fhir_base_url` to your insurer's public provider-directory API. Then run `GET /api/networks?q=<plan name>` and put the IDs it returns in `fhir_network_ids`. | Best, if your insurer publishes your plan's network there. |
| `csv` | Export or save your insurer's search results as a CSV with an `npi` column. | As good as your export. |
| `none` | Nothing. Every provider shows "unknown". | n/a |

Why the API needs no login: since 2021, CMS has required Medicare Advantage and
Medicaid plans to publish an open FHIR provider directory (Da Vinci PDex Plan-Net),
and many insurers serve their commercial networks from the same API. Coverage of
employer plans varies by insurer. That's the main thing to test for your plan.

## How ranking works

The code is in `finder/scoring.py`. Each component is scored from 0 to 1 and given a weight:

- **Clinicians**: the Medicare MIPS final score (×0.30), the CMS star rating of their
  best affiliated hospital (×0.25), years in practice, capped at 10 (×0.15), and
  distance (×0.25).
- **Hospitals**: the CMS overall star rating (×0.55), patient-survey stars (×0.20),
  and distance.
- **Urgent care, imaging, and labs**: distance only. No public quality data exists for these.

Missing data is skipped, not counted as zero. The score is then pulled toward 50 in
proportion to how much quality data is missing, so a clinician with no data can't
beat one with strong data just by being closer. The UI shows a "% data" figure next
to each score so you can see this.

**Caveat.** Public quality data on individual clinicians is thin. MIPS mostly measures
Medicare reporting compliance, not patient outcomes, and many good doctors
(pediatricians, therapists, clinicians who see few Medicare patients) have no
score. Use the ranking to build a shortlist, not to make the final call. Always confirm
with the office that they take your specific plan.

## Data sources (all free, no keys)

- [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page): who exists, their specialty and address
- [CMS Provider Data Catalog](https://data.cms.gov/provider-data/): clinician details, MIPS scores, hospital affiliations, hospital stars, HCAHPS patient surveys
- [Census Geocoder](https://geocoding.geo.census.gov/) and the ZCTA gazetteer: map pins and radius search
- Your insurer's Plan-Net FHIR API, or your own CSV: network status
- OpenStreetMap tiles, drawn with Leaflet (bundled in `web/vendor`)

## Layout

```
finder/
  engine.py        search pipeline: locate → gather → place → enrich → network → score
  sources/nppes.py NPI Registry client
  sources/cms.py   Provider Data Catalog client (matches column names by pattern; they drift)
  sources/network.py none / csv / FHIR Plan-Net adapters
  scoring.py       ranking
  services.py      service → taxonomy codes catalog
  demo.py          fictional offline data
web/               static UI (vanilla JS + Leaflet)
tests/             offline end-to-end tests with mocked APIs
```

Run the tests with `pytest`.
