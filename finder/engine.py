"""Search orchestration: locate -> gather -> place -> enrich -> network -> score."""

from __future__ import annotations

import asyncio
import logging
import time

from . import scoring
from .cache import Cache, Http
from .config import Settings
from .geo import Geocoder, ZipIndex, haversine_miles
from .models import Provider
from .services import BY_KEY, Service
from .sources import cms, nppes
from .sources.network import NetworkRouter

log = logging.getLogger(__name__)

LOCAL_HOSPITAL_MILES = 60


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.cache = Cache(settings.path(settings.cache_path))
        self.http = Http(self.cache)
        self.zips = ZipIndex(settings.path("data/zcta.tsv"), self.http)
        self.geo = Geocoder(self.http, self.cache, self.zips)
        self.networks = NetworkRouter(settings, self.http)
        self._last: dict[str, tuple[str, Provider]] = {}  # id -> (service, provider)

    async def close(self) -> None:
        await self.http.close()

    async def locate(self, where: str | None) -> tuple[float, float]:
        loc = await self.geo.locate(where or self.s.home)
        if not loc:
            raise ValueError(f"Couldn't locate {where!r}. Try a 5-digit ZIP.")
        return loc

    async def _gather(self, svc: Service, zips: list[str]) -> list[Provider]:
        if svc.kind == "hospital":
            return await cms.hospitals_in_zips(self.http, self.s.datasets, zips)
        lists = await asyncio.gather(*(nppes.search_zip(self.http, svc, z) for z in zips))
        seen: dict[str, Provider] = {}
        for p in (p for lst in lists for p in lst):
            seen.setdefault(p.id, p)
        return list(seen.values())

    async def _place(self, providers: list[Provider]) -> None:
        precise: dict[str, tuple[float, float]] = {}
        if self.s.precise_geocoding:
            # many clinicians share an office; geocode each address once
            addrs = {(p.street, p.city, p.state, p.zip) for p in providers if p.street}
            ids = {a: str(i) for i, a in enumerate(sorted(addrs))}
            got = await self.geo.batch({i: a for a, i in ids.items()})
            precise = {a: got[i] for a, i in ids.items() if i in got}
        for p in providers:
            a = (p.street, p.city, p.state, p.zip)
            if a in precise:
                p.lat, p.lon = precise[a]
                p.located = "address"
            elif c := await self.zips.centroid(p.zip):
                p.lat, p.lon = c
                p.located = "zip"

    async def _localize(self, providers: list[Provider]) -> list[Provider]:
        """Keep only nearby hospital affiliations; drop clinicians who don't see office patients."""
        keep = []
        for p in providers:
            f = p.facts
            if cms.NOT_OFFICE_BASED.search(f.get("medicare_specialty") or ""):
                continue
            hosps = f.get("hospitals")
            if hosps and p.lat is not None:
                local = []
                for h in hosps:
                    c = await self.zips.centroid(h.get("zip", ""))
                    if c and haversine_miles((p.lat, p.lon), c) <= LOCAL_HOSPITAL_MILES:
                        local.append(h)
                if not local:
                    # credentialed only at faraway hospitals: a telehealth-only clinician
                    continue
                f["hospitals"] = local
            keep.append(p)
        return keep

    async def search(self, service: str, where: str | None = None, radius: float | None = None) -> dict:
        t0 = time.time()
        timing: dict[str, float] = {}

        def lap(name: str) -> None:
            timing[name] = round(time.time() - t0 - sum(timing.values()), 1)

        svc = BY_KEY[service]
        radius = radius or self.s.radius_miles
        origin = await self.locate(where)
        zips = await self.zips.near(origin, radius, self.s.max_zips)
        providers = await self._gather(svc, zips)
        lap("directory")
        await self._place(providers)
        for p in providers:
            if p.lat is not None:
                p.distance = round(haversine_miles(origin, (p.lat, p.lon)), 2)
        providers = [p for p in providers if p.distance is not None and p.distance <= radius]
        lap("geocode")

        if svc.kind == "clinician":
            await cms.enrich_clinicians(self.http, self.s.datasets, providers)
            n0 = len(providers)
            providers = await self._localize(providers)
            log.info("%s: dropped %d hospital-based/remote clinicians", service, n0 - len(providers))
        lap("quality")
        for p in providers:
            scoring.score(p, radius, svc.procedural)
        providers.sort(key=lambda p: -(p.score or 0))

        # Network: check the best-ranked first if lookups are expensive.
        net = self.networks.for_service(service)
        n = net.eager_limit
        await net.check(providers[:n])
        for p in providers[n:]:
            p.network, p.network_detail = "unchecked", "Not checked yet; open to check"
        lap("network")
        log.info("search %s %s: %d providers, timing %s", service, where or self.s.home, len(providers), timing)

        self._last.update({p.id: (service, p) for p in providers})
        return {
            "service": {"key": svc.key, "label": svc.label, "kind": svc.kind, "tip": svc.tip},
            "origin": {"lat": origin[0], "lon": origin[1], "query": where or self.s.home},
            "radius": radius,
            "zips_searched": len(zips),
            "network_source": net.kind,
            "network_label": net.label,
            "elapsed": round(time.time() - t0, 1),
            "timing": timing,
            "providers": [p.to_dict() for p in providers],
        }

    async def check_network(self, pid: str) -> dict:
        service, p = self._last[pid]
        await self.networks.for_service(service).check_one(p)
        return p.to_dict()

    async def find_networks(self, q: str) -> list[dict]:
        fhir = self.networks.fhir()
        if not fhir:
            raise ValueError("Add a network with type: fhir and fhir_base_url in config.yaml first")
        out = []
        for net in fhir:
            out += [{"network": net.label, **r} for r in await net.find_networks(q)]
        return out
