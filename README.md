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

## Connect your networks

The NPPES registry lists every provider, but it doesn't know which ones are in your
network. List your networks in `config.yaml`; each search uses the network that
covers that service. See `config.example.yaml` for an MVP (medical) + Guardian
(dental/vision) setup.

| `type` | What you do | Accuracy |
|---|---|---|
| `fhir` | Set `fhir_base_url` to your insurer's public provider-directory API. To narrow it to your plan, run `GET /api/networks?q=<plan name>` and put the IDs it returns in `fhir_network_ids`. | Best, if your insurer publishes your plan's network there. MVP publishes one. |
| `csv` | Export or save your insurer's search results as a CSV with an `npi` column, or `name` + `zip` columns. | As good as your export. |
| `none` | Nothing. Every provider shows "unknown". | n/a |

Why the API needs no login: since 2021, CMS has required Medicare Advantage and
Medicaid plans to publish an open FHIR provider directory (Da Vinci PDex Plan-Net),
and many insurers serve their commercial networks from the same API.

## How ranking works

The code is in `finder/scoring.py`. For what each signal means, and which other
sources were considered and why, see [docs/quality-signals.md](docs/quality-signals.md).

- **Clinicians**:
  - the practice's clinical-quality stars (×0.25)
  - procedure volume as a national percentile, for surgical and procedural specialties (×0.25)
  - MIPS score (×0.15)
  - the best *local* affiliated hospital (×0.15)
  - years in practice, capped at 10 (×0.10)
  - distance (×0.25)
- **Hospitals**: the CMS overall star rating (×0.55), patient-survey stars (×0.20), and distance.
  The detail panel also shows how many mortality, safety and readmission measures are
  better or worse than the national rate.
- **Urgent care, imaging, and labs**: distance only. No public quality data exists for these.

Missing data is skipped, not counted as zero. The score is then pulled toward 50 in
proportion to how much quality data is missing, so a clinician with no data can't
beat one with strong data just by being closer. Searches also drop hospitalists,
ER, anesthesia, radiology and pathology clinicians, and anyone credentialed only at
hospitals more than 60 miles away (telehealth-only clinicians).

**Caveat.** Most of these signals describe the practice, not the individual doctor.
Use the ranking to build a shortlist, not to make the final call. Always confirm
with the office that they take your specific plan.

## Data sources (all free, no keys)

- [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page): who exists, their specialty and address
- [CMS Provider Data Catalog](https://data.cms.gov/provider-data/): clinician details, MIPS scores, procedure volumes, practice quality measures, hospital affiliations, hospital stars and outcome measures, HCAHPS patient surveys
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
