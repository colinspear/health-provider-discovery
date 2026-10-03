"""CMS Provider Data Catalog (Care Compare data): quality signals, free, no key.

https://data.cms.gov/provider-data/  (DKAN datastore query API)

Column names drift between yearly releases, so lookups below match columns by
pattern rather than exact name.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..cache import DAY, Http
from ..models import Provider

log = logging.getLogger(__name__)

BASE = "https://data.cms.gov/provider-data/api/1/datastore/query"
LIMIT = 500


async def query(http: Http, dataset: str, conditions: list[dict], ttl=14 * DAY) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        body = {"conditions": conditions, "limit": LIMIT, "offset": offset, "keys": True}
        try:
            data = await http.post(f"{BASE}/{dataset}/0", json_body=body, ttl=ttl)
        except Exception as e:
            log.warning("CMS %s query failed: %s", dataset, e)
            return rows
        batch = data.get("results") or []
        rows += batch
        if len(batch) < LIMIT:
            return rows
        offset += LIMIT


async def query_in(http: Http, dataset: str, prop: str, values: Iterable[str], extra=()) -> list[dict]:
    vals = sorted(set(values))
    rows: list[dict] = []
    for i in range(0, len(vals), 100):
        cond = [{"property": prop, "value": vals[i : i + 100], "operator": "in"}, *extra]
        rows += await query(http, dataset, cond)
    return rows


def col(row: dict, *patterns: str, exclude: str | None = None) -> Any:
    """First non-empty value whose column name matches any pattern (case-insensitive)."""
    for p in patterns:
        for k, v in row.items():
            if re.search(p, k, re.I) and not (exclude and re.search(exclude, k, re.I)):
                if v not in (None, "", "Not Available", "Not Applicable"):
                    return v
    return None


def num(v: Any) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


# ---- clinicians ---------------------------------------------------------------


async def enrich_clinicians(http: Http, ds: dict[str, str], providers: list[Provider]) -> None:
    by_npi = {p.id: p for p in providers if p.kind == "clinician"}
    if not by_npi:
        return
    npis = list(by_npi)

    for row in await query_in(http, ds["clinicians"], "npi", npis):
        p = by_npi.get(str(row.get("npi")))
        if not p:
            continue
        f = p.facts
        f.setdefault("medical_school", col(row, r"^med_sch"))
        if (yr := num(col(row, r"^grd_yr|grad"))) and yr > 1900:
            f["grad_year"] = int(yr)
        f.setdefault("group", col(row, r"^facility_name|^org_nm"))
        if col(row, r"telehlth|telehealth") in ("Y", "Yes"):
            f["telehealth"] = True
        f.setdefault("medicare_specialty", col(row, r"^pri_spec"))

    for row in await query_in(http, ds["mips"], "npi", npis):
        p = by_npi.get(str(row.get("npi")))
        score = num(col(row, r"final.*mips.*score$", r"final.*score", exclude="without|cpb"))
        if p and score is not None:
            p.facts["mips_score"] = max(score, p.facts.get("mips_score", 0))

    ccns: dict[str, set[str]] = {}
    for row in await query_in(http, ds["affiliations"], "npi", npis):
        npi = str(row.get("npi"))
        ccn = col(row, r"affiliations?_certification_number|^facility_ccn")
        ftype = (col(row, r"^facility_type$") or "").lower()
        if npi in by_npi and ccn and (not ftype or "hospital" in ftype):
            ccns.setdefault(npi, set()).add(str(ccn))
    if ccns:
        hosp = await hospitals_by_ccn(http, ds, {c for s in ccns.values() for c in s})
        for npi, cs in ccns.items():
            affs = [hosp[c] for c in cs if c in hosp]
            affs.sort(key=lambda h: -(h["stars"] or 0))
            by_npi[npi].facts["hospitals"] = affs


# ---- hospitals ------------------------------------------------------------------


def _hospital_summary(row: dict) -> dict:
    return {
        "ccn": str(col(row, r"^facility_id$", r"ccn") or ""),
        "name": (col(row, r"^facility_name") or "").title(),
        "stars": num(col(row, r"overall_rating")),
    }


async def hospitals_by_ccn(http: Http, ds: dict[str, str], ccns: set[str]) -> dict[str, dict]:
    rows = await query_in(http, ds["hospitals"], "facility_id", ccns)
    return {h["ccn"]: h for h in map(_hospital_summary, rows)}


async def hospitals_in_zips(http: Http, ds: dict[str, str], zips: list[str]) -> list[Provider]:
    rows = await query_in(http, ds["hospitals"], "zip_code", zips)
    out: dict[str, Provider] = {}
    for row in rows:
        s = _hospital_summary(row)
        if not s["ccn"]:
            continue
        out[s["ccn"]] = Provider(
            id=f"ccn:{s['ccn']}",
            kind="hospital",
            name=s["name"],
            specialty=col(row, r"^hospital_type") or "Hospital",
            street=(col(row, r"^address") or "").title(),
            city=(col(row, r"^citytown|^city") or "").title(),
            state=col(row, r"^state$") or "",
            zip=str(col(row, r"^zip") or "")[:5],
            phone=str(col(row, r"telephone|phone") or ""),
            facts={
                "ccn": s["ccn"],
                "stars": s["stars"],
                "emergency": col(row, r"emergency") in ("Yes", "Y", True),
                "ownership": col(row, r"ownership"),
            },
        )
    if out:
        survey = await query_in(
            http,
            ds["hcahps"],
            "facility_id",
            list(out),
            extra=[{"property": "hcahps_measure_id", "value": "H_STAR_RATING", "operator": "="}],
        )
        for row in survey:
            p = out.get(str(col(row, r"^facility_id$")))
            stars = num(col(row, r"patient_survey_star_rating"))
            if p and stars is not None:
                p.facts["patient_stars"] = stars
    return list(out.values())
