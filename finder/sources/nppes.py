"""CMS NPPES NPI Registry API: every licensed US provider, free, no key.

https://npiregistry.cms.hhs.gov/api-page
"""

from __future__ import annotations

import logging

from ..cache import DAY, Http
from ..models import Provider
from ..services import Service, code_matches

log = logging.getLogger(__name__)

URL = "https://npiregistry.cms.hhs.gov/api/"
PAGE = 200
MAX_SKIP = 1000  # registry refuses skip > 1000


def _title(s: str) -> str:
    return s.title() if s.isupper() else s


def parse_result(r: dict, svc: Service) -> Provider | None:
    basic = r.get("basic", {})
    if basic.get("status", "A") != "A":
        return None
    taxes = r.get("taxonomies", [])
    if svc.codes:
        # Judge by the provider's *primary* taxonomy when they mark one: a hospitalist
        # who also lists Internal Medicine shouldn't show up as a primary care doctor.
        primary = [t for t in taxes if t.get("primary")]
        pool = primary or taxes
        matching = [t for t in pool if code_matches(t.get("code", ""), svc.codes)]
        if not matching:
            return None
        tax = matching[0]
    else:
        tax = next((t for t in taxes if t.get("primary")), taxes[0] if taxes else {})
    addr = next(
        (a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None
    )
    if not addr:
        return None
    if r.get("enumeration_type") == "NPI-2":
        name = _title(basic.get("organization_name", ""))
        kind = "organization"
    else:
        parts = [basic.get("first_name", ""), basic.get("middle_name", "")[:1], basic.get("last_name", "")]
        name = _title(" ".join(p for p in parts if p))
        kind = "clinician"
    street = " ".join(x for x in (addr.get("address_1", ""), addr.get("address_2", "")) if x)
    return Provider(
        id=str(r["number"]),
        kind=kind,
        name=name,
        specialty=(tax.get("desc") or "").split(", ")[-1] if tax.get("desc") else "",
        credential=(basic.get("credential") or "").replace(".", ""),
        street=_title(street),
        city=_title(addr.get("city", "")),
        state=addr.get("state", ""),
        zip=(addr.get("postal_code") or "")[:5],
        phone=addr.get("telephone_number", ""),
        facts={"gender": basic.get("gender") or basic.get("sex") or ""},
    )


async def search_zip(http: Http, svc: Service, zip5: str) -> list[Provider]:
    enum = "NPI-2" if svc.kind == "organization" else "NPI-1"
    out: list[Provider] = []
    for desc in svc.nppes:
        skip = 0
        while skip <= MAX_SKIP:
            params = {
                "version": "2.1",
                "taxonomy_description": desc,
                "postal_code": zip5,
                "address_purpose": "LOCATION",
                "enumeration_type": enum,
                "limit": PAGE,
                "skip": skip,
            }
            try:
                data = await http.get(URL, params=params, ttl=7 * DAY)
            except Exception as e:
                log.warning("NPPES %s %s failed: %s", zip5, desc, e)
                break
            if data.get("Errors"):
                log.warning("NPPES error %s: %s", params, data["Errors"])
                break
            results = data.get("results") or []
            out += [p for r in results if (p := parse_result(r, svc))]
            if len(results) < PAGE:
                break
            skip += PAGE
    return out
