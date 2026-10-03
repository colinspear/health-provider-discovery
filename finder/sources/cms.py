"""CMS Provider Data Catalog (Care Compare data): quality signals, free, no key.

https://data.cms.gov/provider-data/  (DKAN datastore query API)

Column names drift between yearly releases, so lookups below match columns by
pattern rather than exact name.
"""

from __future__ import annotations

import asyncio
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
    chunks = [
        [{"property": prop, "value": vals[i : i + 100], "operator": "in"}, *extra]
        for i in range(0, len(vals), 100)
    ]
    results = await asyncio.gather(*(query(http, dataset, c) for c in chunks))
    return [r for rows in results for r in rows]


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

# Medicare primary specialties that mean "not someone you book an office visit with".
# NPPES taxonomies are self-reported and stale; Medicare enrollment is more current.
NOT_OFFICE_BASED = re.compile(
    r"HOSPITALIST|EMERGENCY|CRITICAL CARE|ANESTHE|RADIOLOGY|PATHOLOGY|NEUROPSYCHIATRY", re.I
)


async def enrich_clinicians(http: Http, ds: dict[str, str], providers: list[Provider]) -> None:
    by_npi = {p.id: p for p in providers if p.kind == "clinician"}
    if not by_npi:
        return
    npis = list(by_npi)

    async def nothing() -> list[dict]:
        return []

    dac, mips, util, affil = await asyncio.gather(
        query_in(http, ds["clinicians"], "npi", npis),
        query_in(http, ds["mips"], "npi", npis),
        query_in(http, ds["utilization"], "npi", npis) if ds.get("utilization") else nothing(),
        query_in(http, ds["affiliations"], "npi", npis),
    )

    groups: dict[str, set[str]] = {}  # org_pac_id -> npis
    for row in dac:
        p = by_npi.get(str(row.get("npi")))
        if not p:
            continue
        f = p.facts
        f.setdefault("medical_school", (col(row, r"^med_sch") or "").title() or None)
        if (yr := num(col(row, r"^grd_yr|grad"))) and yr > 1900:
            f["grad_year"] = int(yr)
        if (g := col(row, r"^facility_name|^org_nm")) and not f.get("group"):
            f["group"] = g.title()
        if col(row, r"telehlth|telehealth") in ("Y", "Yes"):
            f["telehealth"] = True
        f.setdefault("medicare_specialty", col(row, r"^pri_spec"))
        if pac := col(row, r"^org_pac_id"):
            groups.setdefault(str(pac), set()).add(p.id)

    for row in mips:
        p = by_npi.get(str(row.get("npi")))
        score = num(col(row, r"^final_mips_score$", r"final.*mips.*score$", exclude="without|cpb"))
        if p and score is not None and score >= p.facts.get("mips_score", -1):
            p.facts["mips_score"] = score
            # "group"/"individual"/...: most clinicians inherit their group's score
            p.facts["mips_source"] = (col(row, r"^source$") or "").lower() or None

    # Procedure volumes with national percentiles (surgeons and proceduralists)
    for row in util:
        p = by_npi.get(str(row.get("npi")))
        pct = num(col(row, r"^percentile$"))
        if p and pct is not None:
            p.facts.setdefault("procedures", []).append(
                {"name": col(row, r"procedure_category") or "", "count": col(row, r"^count$"), "percentile": pct}
            )
    for p in by_npi.values():
        if p.facts.get("procedures"):
            p.facts["procedures"].sort(key=lambda x: -x["percentile"])

    ccns: dict[str, set[str]] = {}
    for row in affil:
        npi = str(row.get("npi"))
        ccn = col(row, r"affiliations?_certification_number|^facility_ccn")
        ftype = (col(row, r"^facility_type$") or "").lower()
        if npi in by_npi and ccn and (not ftype or "hospital" in ftype):
            ccns.setdefault(npi, set()).add(str(ccn))

    async def group_rows() -> list[dict]:
        if ds.get("group_measures") and groups:
            return await query_in(http, ds["group_measures"], "org_pac_id", groups)
        return []

    rows, hosp = await asyncio.gather(
        group_rows(), hospitals_by_ccn(http, ds, {c for s in ccns.values() for c in s})
    )

    # Clinical quality measure stars reported by the clinician's group
    if rows:
        by_pac: dict[str, list[dict]] = {}
        for row in rows:
            stars = num(col(row, r"^star_value$"))
            if stars is None:
                continue
            by_pac.setdefault(str(row.get("org_pac_id")), []).append(
                {"title": col(row, r"measure_title") or "", "rate": col(row, r"prf_rate"), "stars": stars}
            )
        for pac, members in groups.items():
            for npi in members:
                if pac in by_pac:
                    by_npi[npi].facts["group_measures"] = by_pac[pac]

    if hosp:
        for npi, cs in ccns.items():
            affs = [hosp[c] for c in cs if c in hosp]
            affs.sort(key=lambda h: -(h["stars"] or 0))
            by_npi[npi].facts["hospitals"] = affs


# ---- hospitals ------------------------------------------------------------------


def _hospital_summary(row: dict) -> dict:
    return {
        "ccn": str(col(row, r"^facility_id$", r"ccn") or ""),
        "name": (col(row, r"^facility_name") or "").title(),
        # anchored: the *_footnote column holds codes like "16" when unrated
        "stars": num(col(row, r"overall_rating$")),
        "zip": str(col(row, r"^zip") or "")[:5],
    }


def _better_worse(row: dict, group: str) -> dict | None:
    """CMS's measure-group tallies, e.g. mortality: 2 better / 6 same / 0 worse than national."""
    b = num(col(row, rf"count_of_{group}_measures_better"))
    w = num(col(row, rf"count_of_{group}_measures_worse"))
    n = num(col(row, rf"count_of_facility_{group}_measures"))
    if n:
        return {"better": int(b or 0), "worse": int(w or 0), "of": int(n)}
    return None


async def hospitals_by_ccn(http: Http, ds: dict[str, str], ccns: set[str]) -> dict[str, dict]:
    if not ccns:
        return {}
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
                "birthing_friendly": col(row, r"birthing_friendly") in ("Y", "Yes"),
                "mortality": _better_worse(row, "mort"),
                "safety": _better_worse(row, "safety"),
                "readmission": _better_worse(row, "readm"),
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
