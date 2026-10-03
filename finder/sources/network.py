"""Is a provider in *my* network?

Three adapters:
  none  - no information; everything is "unknown".
  csv   - a list of in-network NPIs you supply (export from your insurer's site, or
          maintain by hand). Anything not on the list is "not_listed".
  fhir  - your insurer's public provider-directory API. CMS requires Medicare
          Advantage / Medicaid plans to publish one (FHIR R4, Da Vinci PDex Plan-Net),
          and many insurers expose their commercial networks through the same API.
          No login needed.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import re
from pathlib import Path

from ..cache import DAY, Http
from ..models import Provider

log = logging.getLogger(__name__)

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
NETWORK_EXT = "network-reference"


class NoNetwork:
    name = "none"
    eager_limit = 10**9

    async def check(self, providers: list[Provider]) -> None:
        for p in providers:
            p.network = "unknown"
            p.network_detail = "No network source configured"


class CsvNetwork:
    """Any CSV with an 'npi' column (case-insensitive); other columns are ignored."""

    name = "csv"
    eager_limit = 10**9

    def __init__(self, path: Path):
        self.path = path
        self.npis: set[str] = set()
        if path.exists():
            with path.open(newline="") as f:
                rows = csv.DictReader(f)
                key = next((k for k in rows.fieldnames or [] if k.strip().lower() == "npi"), None)
                if key:
                    self.npis = {r[key].strip() for r in rows if r.get(key)}
            log.info("Loaded %d in-network NPIs from %s", len(self.npis), path)
        else:
            log.warning("Network CSV %s not found; everything will be not_listed", path)

    async def check(self, providers: list[Provider]) -> None:
        for p in providers:
            # hospitals have no NPI in CMS data; list their CMS CCN in the CSV instead
            ok = p.id in self.npis or p.facts.get("ccn") in self.npis
            p.network = "in_network" if ok else "not_listed"
            p.network_detail = f"From {self.path.name}"


def _network_refs(resource: dict) -> set[str]:
    refs = set()
    for ext in resource.get("extension", []):
        if ext.get("url", "").endswith(NETWORK_EXT):
            ref = (ext.get("valueReference") or {}).get("reference", "")
            if ref:
                refs.add(ref.rsplit("/", 1)[-1])
    for n in resource.get("network", []):  # OrganizationAffiliation.network
        if n.get("reference"):
            refs.add(n["reference"].rsplit("/", 1)[-1])
    return refs


def _entries(bundle: dict, rtype: str) -> list[dict]:
    return [
        e["resource"]
        for e in bundle.get("entry", []) or []
        if e.get("resource", {}).get("resourceType") == rtype
    ]


class FhirNetwork:
    """Da Vinci PDex Plan-Net provider directory."""

    name = "fhir"

    def __init__(self, http: Http, base_url: str, network_ids: list[str], eager: int):
        self.http = http
        self.base = base_url.rstrip("/")
        self.network_ids = set(network_ids)
        self.eager_limit = eager

    async def _get(self, path: str, params: dict) -> dict:
        return await self.http.get(
            f"{self.base}/{path}",
            params=params,
            ttl=7 * DAY,
        )

    async def _ids(self, p: Provider) -> list[str]:
        rtype = "Practitioner" if p.kind == "clinician" else "Organization"
        if p.kind == "hospital":  # CMS hospital data has no NPI; match on name + ZIP
            first = re.split(r"\W+", p.name)[0]
            q = {"name:contains": first, "address-postalcode": p.zip}
            return [
                r["id"]
                for r in _entries(await self._get(rtype, q), rtype)
                if _similar(r.get("name", ""), p.name)
            ]
        found = await self._get(rtype, {"identifier": f"{NPI_SYSTEM}|{p.id}"})
        ids = [r["id"] for r in _entries(found, rtype)]
        if not ids:  # some servers dislike system|value
            found = await self._get(rtype, {"identifier": p.id})
            ids = [r["id"] for r in _entries(found, rtype)]
        return ids

    async def networks_for(self, p: Provider) -> tuple[set[str], bool]:
        """(network ids, listed) for one provider."""
        org = p.kind != "clinician"
        ids = await self._ids(p)
        if not ids:
            return set(), False
        nets: set[str] = set()
        for rid in ids[:5]:
            if org:
                b = await self._get("OrganizationAffiliation", {"participating-organization": f"Organization/{rid}"})
                for r in _entries(b, "OrganizationAffiliation"):
                    nets |= _network_refs(r)
            else:
                b = await self._get("PractitionerRole", {"practitioner": f"Practitioner/{rid}"})
                for r in _entries(b, "PractitionerRole"):
                    nets |= _network_refs(r)
        return nets, True

    async def check_one(self, p: Provider) -> None:
        try:
            nets, listed = await self.networks_for(p)
        except Exception as e:
            p.network, p.network_detail = "unknown", f"Directory lookup failed: {e}"
            return
        if not listed:
            p.network, p.network_detail = "not_listed", "Not in your insurer's directory"
        elif not self.network_ids:
            p.network = "in_network"
            p.network_detail = f"Listed with your insurer ({len(nets)} networks); set fhir_network_ids to narrow to your plan"
        elif nets & self.network_ids:
            p.network, p.network_detail = "in_network", "Listed in your plan's network"
        else:
            p.network, p.network_detail = "out_of_network", "Listed with insurer, but not in your plan's network"

    async def check(self, providers: list[Provider]) -> None:
        await asyncio.gather(*(self.check_one(p) for p in providers))

    async def find_networks(self, q: str) -> list[dict]:
        """Search InsurancePlans by name and list their networks, to fill in fhir_network_ids."""
        out = []
        b = await self._get("InsurancePlan", {"name:contains": q, "_count": 50})
        for plan in _entries(b, "InsurancePlan"):
            nets = [n.get("reference", "").rsplit("/", 1)[-1] for n in plan.get("network", [])]
            out.append({"plan": plan.get("name"), "plan_id": plan.get("id"), "network_ids": nets})
        if not out:  # fall back to network Organizations directly
            b = await self._get("Organization", {"type": "ntwk", "name:contains": q, "_count": 50})
            out = [
                {"plan": r.get("name"), "plan_id": None, "network_ids": [r.get("id")]}
                for r in _entries(b, "Organization")
            ]
        return out


def make_network(settings, http: Http):
    n = settings.network
    if n.type == "csv":
        return CsvNetwork(settings.path(n.csv_path))
    if n.type == "fhir" and n.fhir_base_url:
        return FhirNetwork(http, n.fhir_base_url, n.fhir_network_ids, n.fhir_eager_check)
    if n.type not in ("none", "", None):
        log.warning("Unknown/incomplete network config %r; using none", n.type)
    return NoNetwork()


def _similar(a: str, b: str) -> bool:
    words = lambda s: set(re.findall(r"[a-z]{3,}", s.lower())) - {"the", "and", "hospital", "center", "medical"}
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6
