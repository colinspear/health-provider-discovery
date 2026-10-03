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
from .sources.network import FhirNetwork, make_network

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.cache = Cache(settings.path(settings.cache_path))
        self.http = Http(self.cache)
        self.zips = ZipIndex(settings.path("data/zcta.tsv"), self.http)
        self.geo = Geocoder(self.http, self.cache, self.zips)
        self.network = make_network(settings, self.http)
        self._last: dict[str, Provider] = {}  # id -> provider from recent searches

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
        precise = {}
        if self.s.precise_geocoding:
            precise = await self.geo.batch(
                {p.id: (p.street, p.city, p.state, p.zip) for p in providers if p.street}
            )
        for p in providers:
            if p.id in precise:
                p.lat, p.lon = precise[p.id]
                p.located = "address"
            elif c := await self.zips.centroid(p.zip):
                p.lat, p.lon = c
                p.located = "zip"

    async def search(self, service: str, where: str | None = None, radius: float | None = None) -> dict:
        t0 = time.time()
        svc = BY_KEY[service]
        radius = radius or self.s.radius_miles
        origin = await self.locate(where)
        zips = await self.zips.near(origin, radius, self.s.max_zips)
        providers = await self._gather(svc, zips)
        await self._place(providers)
        for p in providers:
            if p.lat is not None:
                p.distance = round(haversine_miles(origin, (p.lat, p.lon)), 2)
        providers = [p for p in providers if p.distance is not None and p.distance <= radius]

        if svc.kind == "clinician":
            await cms.enrich_clinicians(self.http, self.s.datasets, providers)
        for p in providers:
            scoring.score(p, radius)
        providers.sort(key=lambda p: -(p.score or 0))

        # Network: check the best-ranked first if lookups are expensive.
        n = self.network.eager_limit
        await self.network.check(providers[:n])
        for p in providers[n:]:
            p.network, p.network_detail = "unchecked", "Not checked yet; open to check"

        self._last.update({p.id: p for p in providers})
        return {
            "service": {"key": svc.key, "label": svc.label, "kind": svc.kind, "tip": svc.tip},
            "origin": {"lat": origin[0], "lon": origin[1], "query": where or self.s.home},
            "radius": radius,
            "zips_searched": len(zips),
            "network_source": self.network.name,
            "elapsed": round(time.time() - t0, 1),
            "providers": [p.to_dict() for p in providers],
        }

    async def check_network(self, pid: str) -> dict:
        p = self._last.get(pid)
        if not p:
            raise KeyError(pid)
        if isinstance(self.network, FhirNetwork):
            await self.network.check_one(p)
        else:
            await self.network.check([p])
        return p.to_dict()

    async def find_networks(self, q: str) -> list[dict]:
        if not isinstance(self.network, FhirNetwork):
            raise ValueError("Set network.type: fhir and fhir_base_url in config.yaml first")
        return await self.network.find_networks(q)
