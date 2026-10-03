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
from ..geo import haversine_miles
from ..models import Provider

log = logging.getLogger(__name__)

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
NETWORK_EXT = "network-reference"


class NoNetwork:
    kind = "none"
    label = ""
    eager_limit = 10**9

    async def check(self, providers: list[Provider]) -> None:
        for p in providers:
            p.network = "unknown"
            p.network_detail = "No network source configured for this service"

    async def check_one(self, p: Provider) -> None:
        await self.check([p])


def _last_name(name: str) -> str:
    words = re.findall(r"[a-z'-]+", name.lower())
    words = [w for w in words if w not in {"md", "do", "dds", "dmd", "od", "jr", "sr", "ii", "iii", "dr"}]
    return words[-1] if words else ""


def _accepting_text(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    if v in ("y", "yes", "true", "1", "accepting", "newpt"):
        return ACCEPTING["newpt"]
    if v in ("n", "no", "false", "0", "not accepting", "nopt"):
        return ACCEPTING["nopt"]
    return None


class CsvNetwork:
    """A CSV of in-network providers.

    Matches on an 'npi' column if there is one. Directory exports often have no NPI
    (dental and vision especially), so it also accepts 'name'/'last_name' plus 'zip'
    columns and matches on last name + ZIP.
    """

    kind = "csv"
    eager_limit = 10**9

    def __init__(self, path: Path, label: str = ""):
        self.path = path
        self.label = label or path.stem
        self.npis: set[str] = set()
        self.name_zip: set[tuple[str, str]] = set()
        self.accepting: dict[str, str] = {}  # npi or "name|zip" -> status
        self.missing = not path.exists()
        if self.missing:
            log.warning("Network CSV %s not found; network status will be unknown", path)
            return
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = csv.DictReader(f)
            cols = {k.strip().lower().replace(" ", "_"): k for k in rows.fieldnames or []}
            npi_c = cols.get("npi")
            name_c = cols.get("last_name") or cols.get("name") or cols.get("provider_name")
            zip_c = cols.get("zip") or cols.get("zip_code") or cols.get("postal_code")
            acc_c = cols.get("accepting") or cols.get("accepting_new_patients")
            for r in rows:
                acc = _accepting_text(r.get(acc_c)) if acc_c else None
                if npi_c and (v := (r.get(npi_c) or "").strip()):
                    self.npis.add(v)
                    if acc:
                        self.accepting[v] = acc
                if name_c and zip_c and r.get(name_c) and r.get(zip_c):
                    key = (_last_name(r[name_c]), r[zip_c].strip()[:5])
                    self.name_zip.add(key)
                    if acc:
                        self.accepting["|".join(key)] = acc
        log.info("%s: %d NPIs, %d name+ZIP rows from %s", self.label, len(self.npis), len(self.name_zip), path)

    async def check(self, providers: list[Provider]) -> None:
        if self.missing:
            for p in providers:
                p.network, p.network_detail = "unknown", f"{self.label}: add {self.path.name} to data/ (see README)"
            return
        for p in providers:
            # hospitals have no NPI in CMS data; list their CMS CCN in the npi column instead
            key = (_last_name(p.name), p.zip)
            if p.id in self.npis or p.facts.get("ccn") in self.npis:
                p.network, p.network_detail = "in_network", f"{self.label}: NPI on your list"
                acc = self.accepting.get(p.id)
            elif key in self.name_zip:
                p.network, p.network_detail = "in_network", f"{self.label}: name + ZIP match on your list"
                acc = self.accepting.get("|".join(key))
            else:
                p.network, p.network_detail = "not_listed", f"{self.label}: not on your list"
                acc = None
            if acc:
                p.facts["accepting"] = acc

    async def check_one(self, p: Provider) -> None:
        await self.check([p])


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

    kind = "fhir"

    def __init__(self, http: Http, base_url: str, network_ids: list[str], eager: int, label: str = ""):
        self.label = label or "Insurer"
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
        ids: list[str] = []
        if p.npi:
            found = await self._get(rtype, {"identifier": f"{NPI_SYSTEM}|{p.npi}"})
            ids = [r["id"] for r in _entries(found, rtype)]
            if not ids:  # some servers dislike system|value
                found = await self._get(rtype, {"identifier": p.npi})
                ids = [r["id"] for r in _entries(found, rtype)]
        if not ids and p.kind != "clinician":
            # facilities often carry no NPI in payer directories (MVP's don't):
            # match on name + ZIP instead
            words = [w for w in re.findall(r"[A-Za-z]{3,}", p.name) if w.lower() not in {"the", "and", "inc", "pllc"}]
            if words:
                # ZIPs disagree between sources (CMS says 14617 for a hospital MVP
                # files under 14620), so search by name and match on distance.
                q = {"name": " ".join(words[:2]), "_count": 100}
                ids = [
                    r["id"]
                    for r in _entries(await self._get(rtype, q), rtype)
                    if _similar(r.get("name", ""), p.name) and _near(r, p)
                ]
        return ids

    async def networks_for(self, p: Provider) -> tuple[set[str], bool]:
        """(network ids, listed) for one provider. Also records new-patient status."""
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
                acc: dict[str, str] = {}
                for r in _entries(b, "PractitionerRole"):
                    nets |= _network_refs(r)
                    acc.update(_accepting_by_network(r))
                if a := _pick_accepting(acc, self.network_ids):
                    p.facts["accepting"] = a
        return nets, True

    async def check_one(self, p: Provider) -> None:
        try:
            nets, listed = await self.networks_for(p)
        except Exception as e:
            p.network, p.network_detail = "unknown", f"{self.label} directory lookup failed: {e}"
            return
        L = self.label
        if not listed:
            p.network, p.network_detail = "not_listed", f"Not in the {L} directory"
        elif not nets:
            p.network = "listed"
            p.network_detail = f"Listed in the {L} directory, which doesn't say which plans it takes. Call to confirm."
        elif not self.network_ids:
            p.network = "in_network"
            p.network_detail = f"Listed in the {L} directory ({len(nets)} networks); set fhir_network_ids to narrow to your plan"
        elif nets & self.network_ids:
            p.network, p.network_detail = "in_network", f"In your {L} plan's network"
        else:
            p.network, p.network_detail = "out_of_network", f"Listed with {L}, but not in your plan's network"

    # ---- roster mode -------------------------------------------------------------
    # Checking providers one at a time costs 2 requests each (3,000+ for primary care
    # in a city). Instead, pull the directory's roster for the search area: one query
    # per (ZIP, specialty code), chained through Location.

    async def _all_pages(self, path: str, params: dict) -> list[dict]:
        bundle = await self._get(path, params)
        out = [bundle]
        for _ in range(50):
            nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
            if not nxt:
                break
            bundle = await self.http.get(nxt, ttl=7 * DAY)
            out.append(bundle)
        return out

    async def roster(self, zips: list[str], codes: set[str]) -> dict[str, dict]:
        """NPI -> {nets, acc, languages} for everyone listed in these ZIPs/specialties."""
        async def one(z: str, code: str) -> list[dict]:
            try:
                pages = await self._all_pages(
                    "PractitionerRole",
                    {"location.address-postalcode": z, "specialty": code, "_count": 100},
                )
            except Exception as e:
                log.warning("%s roster %s/%s failed: %s", self.label, z, code, e)
                return []
            return [r for b in pages for r in _entries(b, "PractitionerRole")]

        jobs = [one(z, c) for z in zips for c in sorted(codes)]
        roles = [r for rs in await asyncio.gather(*jobs) for r in rs]
        by_prac: dict[str, dict] = {}
        for r in roles:
            pid = ((r.get("practitioner") or {}).get("reference") or "").rsplit("/", 1)[-1]
            if not pid:
                continue
            e = by_prac.setdefault(pid, {"nets": set(), "acc": {}})
            e["nets"] |= _network_refs(r)
            e["acc"].update(_accepting_by_network(r))

        # practitioner id -> NPI (+ languages), 50 per request
        ids = sorted(by_prac)
        batches = await asyncio.gather(*(
            self._get("Practitioner", {"_id": ",".join(ids[i : i + 50]), "_count": 100})
            for i in range(0, len(ids), 50)
        ))
        roster: dict[str, dict] = {}
        for b in batches:
            for prac in _entries(b, "Practitioner"):
                npi = next(
                    (i.get("value") for i in prac.get("identifier", []) if i.get("system") == NPI_SYSTEM), None
                )
                if not npi or prac["id"] not in by_prac:
                    continue
                langs = {
                    c.get("display")
                    for comm in prac.get("communication", [])
                    for c in comm.get("coding", [])
                    if c.get("display") and c.get("code") != "en"
                }
                e = roster.setdefault(npi, {"nets": set(), "acc": {}, "languages": set()})
                e["nets"] |= by_prac[prac["id"]]["nets"]
                e["acc"].update(by_prac[prac["id"]]["acc"])
                e["languages"] |= langs
        log.info("%s roster: %d roles, %d practitioners over %d queries", self.label, len(roles), len(roster), len(jobs))
        return roster

    def _from_roster(self, p: Provider, roster: dict[str, dict] | None) -> bool:
        if roster is None or p.kind != "clinician":
            return False
        e = roster.get(p.id)
        L = self.label
        if not e:
            p.network, p.network_detail = "not_listed", f"Not in the {L} directory near here"
            return True
        if e["languages"]:
            p.facts["languages"] = sorted(e["languages"])
        if a := _pick_accepting(e["acc"], self.network_ids):
            p.facts["accepting"] = a
        if not self.network_ids:
            p.network, p.network_detail = "in_network", f"Listed in the {L} directory"
        elif e["nets"] & self.network_ids:
            p.network, p.network_detail = "in_network", f"In your {L} plan's network"
        else:
            p.network, p.network_detail = "out_of_network", f"Listed with {L}, but not in your plan's network"
        return True

    async def check(self, providers: list[Provider], roster: dict[str, dict] | None = None) -> None:
        rest = [p for p in providers if not self._from_roster(p, roster)]
        await asyncio.gather(*(self.check_one(p) for p in rest[: self.eager_limit]))
        for p in rest[self.eager_limit :]:
            p.network, p.network_detail = "unchecked", "Not checked yet; open to check"

    async def find_networks(self, q: str) -> list[dict]:
        """Search InsurancePlans by name and list their networks, to fill in fhir_network_ids."""
        out = []
        try:
            b = await self._get("InsurancePlan", {"name": q, "_count": 50})
        except Exception:
            b = {}
        for plan in _entries(b, "InsurancePlan"):
            nets = [n.get("reference", "").rsplit("/", 1)[-1] for n in plan.get("network", [])]
            out.append({"plan": plan.get("name"), "plan_id": plan.get("id"), "network_ids": nets})
        # Some insurers (MVP) leave InsurancePlan.network empty; list network
        # Organizations by name too.
        b = await self._get("Organization", {"type": "ntwk", "_count": 100})
        words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 1]
        out += [
            {"plan": None, "network": r.get("name"), "network_ids": [r.get("id")]}
            for r in _entries(b, "Organization")
            if any(w in (r.get("name") or "").lower() for w in words)
        ]
        return out


ACCEPTING = {
    "newpt": "Accepting new patients",
    "nopt": "Not accepting new patients",
    "existptonly": "Existing patients only",
    "existptfam": "Existing patients and their families",
}


def _accepting_by_network(role: dict) -> dict[str, str]:
    """network id -> accepting code. Plan-Net puts `newpatients` either right after the
    network-reference it applies to (MVP does this) or with a nested `fromNetwork`."""
    out: dict[str, str] = {}
    current = "*"
    for ext in role.get("extension", []):
        url = ext.get("url", "")
        if url.endswith(NETWORK_EXT):
            current = ((ext.get("valueReference") or {}).get("reference") or "*").rsplit("/", 1)[-1]
        elif url.endswith("newpatients"):
            net, code = current, None
            for sub in ext.get("extension", []):
                if sub.get("url") == "fromNetwork":
                    net = ((sub.get("valueReference") or {}).get("reference") or net).rsplit("/", 1)[-1]
                if sub.get("url") == "acceptingPatients":
                    for c in (sub.get("valueCodeableConcept") or {}).get("coding", []):
                        code = c.get("code")
            if code in ACCEPTING:
                out[net] = code
    return out


def _pick_accepting(by_net: dict[str, str], mine: set[str]) -> str | None:
    """Status for the user's network(s); else the most favorable listed one."""
    codes = [c for n, c in by_net.items() if not mine or n in mine or n == "*"]
    for best in ("newpt", "existptfam", "existptonly", "nopt"):
        if best in codes:
            return ACCEPTING[best]
    return None


def make_network(n, settings, http: Http):
    if n.type == "csv":
        return CsvNetwork(settings.path(n.csv_path), n.name)
    if n.type == "fhir" and n.fhir_base_url:
        return FhirNetwork(http, n.fhir_base_url, n.fhir_network_ids, n.fhir_eager_check, n.name)
    if n.type not in ("none", "", None):
        log.warning("Unknown/incomplete network config %r; using none", n.type)
    return NoNetwork()


class NetworkRouter:
    """Picks the network that covers a service (e.g. medical vs dental vs vision)."""

    def __init__(self, settings, http: Http):
        self.nets = [(set(n.services), make_network(n, settings, http)) for n in settings.networks]

    def for_service(self, key: str):
        for services, net in self.nets:
            if key in services:
                return net
        for services, net in self.nets:
            if "*" in services:
                return net
        return NoNetwork()

    def fhir(self) -> list["FhirNetwork"]:
        return [n for _, n in self.nets if isinstance(n, FhirNetwork)]


def _near(resource: dict, p: Provider, miles: float = 1.5) -> bool:
    for addr in resource.get("address", []):
        if p.zip and addr.get("postalCode", "")[:5] == p.zip:
            return True
        for ext in addr.get("extension", []):
            if ext.get("url", "").endswith("geolocation") and p.lat is not None:
                v = {e.get("url"): e.get("valueDecimal") for e in ext.get("extension", [])}
                if v.get("latitude") is not None and v.get("longitude") is not None:
                    if haversine_miles((p.lat, p.lon), (v["latitude"], v["longitude"])) <= miles:
                        return True
    return False


def _similar(a: str, b: str) -> bool:
    words = lambda s: set(re.findall(r"[a-z]{3,}", s.lower())) - {"the", "and", "hospital", "center", "medical"}
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6
