"""New York State Department of Health open data (health.data.ny.gov, Socrata).

- Professional Medical Conduct Board actions since 1990 (physicians, PAs): a red
  flag. Matched on NY license number, which NPPES lists per taxonomy, so there
  are no name-collision false positives.
- PCI (angioplasty/stent) outcomes by cardiologist: risk-adjusted mortality vs
  the statewide rate. Shown as context for cardiologists.
"""

from __future__ import annotations

import logging
import re

from ..cache import DAY, Http
from ..models import Provider

log = logging.getLogger(__name__)

BASE = "https://health.data.ny.gov/resource"
DISCIPLINE = "ebmi-8ctw"
PCI = "ekig-i57g"

SEVERE = re.compile(
    r"surrender|revoc|revok|suspen|preclu|limitation|annul|never (?:re)?(?:activate|register|apply)", re.I
)
# Records that aren't discipline against the person: restorations, modifications of
# earlier orders, corporate filings, explicitly non-disciplinary orders.
INFO = re.compile(
    r"restored|restoration|does not constitute a new action|certificate of incorporation|non-disciplinary", re.I
)


def norm_license(v: str | None) -> str:
    return (re.sub(r"\D", "", v or "")).lstrip("0")


def ny_licenses(p: Provider) -> set[str]:
    return {norm_license(lic) for st, lic in p.facts.get("licenses", []) if st == "NY" and norm_license(lic)}


def _first_sentence(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    m = re.match(r"(.{0,160}?[.;])(\s|$)", s)
    return m.group(1) if m else s[:160]


async def discipline_index(http: Http) -> dict[str, list[dict]]:
    rows = await http.get(
        f"{BASE}/{DISCIPLINE}.json",
        params={"$limit": 50000, "$select": "licensenum,licensetype,first_name,last_name,effectivedate,webaction,webnotes"},
        ttl=1 * DAY,
    )
    idx: dict[str, list[dict]] = {}
    for r in rows:
        action = r.get("webaction") or ""
        if not r.get("licensenum") or action.lower().startswith("dismiss"):
            continue
        idx.setdefault(norm_license(r["licensenum"]), []).append(
            {
                "date": (r.get("effectivedate") or "")[:10],
                "action": _first_sentence(action),
                "notes": _first_sentence(r.get("webnotes") or ""),
                "severity": "info" if INFO.search(action) else "severe" if SEVERE.search(action) else "moderate",
                "name": f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
            }
        )
    return idx


async def enrich(http: Http, providers: list[Provider], cardiology: bool = False) -> None:
    clinicians = [p for p in providers if p.kind == "clinician" and ny_licenses(p)]
    if not clinicians:
        return
    try:
        idx = await discipline_index(http)
    except Exception as e:
        log.warning("NY discipline data unavailable: %s", e)
        idx = {}
    for p in clinicians:
        hits = [a for lic in ny_licenses(p) for a in idx.get(lic, [])]
        if hits:
            p.facts["discipline"] = sorted(hits, key=lambda a: a["date"], reverse=True)

    if cardiology:
        npis = [p.id for p in clinicians]
        rows: list[dict] = []
        for i in range(0, len(npis), 100):
            where = "national_provider_id in (" + ",".join(f"'{n}'" for n in npis[i : i + 100]) + ")"
            try:
                rows += await http.get(
                    f"{BASE}/{PCI}.json",
                    params={"$where": where + " AND procedure='All PCI'", "$limit": 5000},
                    ttl=14 * DAY,
                )
            except Exception as e:
                log.warning("NY PCI data unavailable: %s", e)
                break
        by_npi = {p.id: p for p in clinicians}
        latest: dict[str, dict] = {}
        for r in rows:
            npi = r.get("national_provider_id")
            if npi in by_npi and r.get("year_of_hospital_discharge", "") >= latest.get(npi, {}).get("year_of_hospital_discharge", ""):
                latest[npi] = r
        for npi, r in latest.items():
            by_npi[npi].facts["pci"] = {
                "years": r.get("year_of_hospital_discharge"),
                "cases": r.get("number_of_cases"),
                "hospital": r.get("hospital_name"),
                "risk_adjusted_mortality": r.get("risk_adjusted_mortality_rate"),
                "comparison": (r.get("comparison_results") or "").replace("Rate ", "").replace(" Statewide Rate", " statewide"),
            }
