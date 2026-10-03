"""Geography: ZIP centroids (Census ZCTA gazetteer), Census geocoder, distances."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import re
import zipfile
from pathlib import Path

from .cache import DAY, Cache, Http

log = logging.getLogger(__name__)

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2024_Gazetteer/2024_Gaz_zcta_national.zip"
)
CENSUS = "https://geocoding.geo.census.gov/geocoder"
BENCHMARK = "Public_AR_Current"


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2
    ) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def parse_gazetteer(text: str) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    rows = csv.reader(io.StringIO(text), delimiter="\t")
    header = [h.strip() for h in next(rows)]
    gi, la, lo = header.index("GEOID"), header.index("INTPTLAT"), header.index("INTPTLONG")
    for r in rows:
        if len(r) > lo:
            out[r[gi].strip()] = (float(r[la]), float(r[lo]))
    return out


class ZipIndex:
    """ZIP (ZCTA) -> centroid, loaded once from the Census gazetteer and kept on disk."""

    def __init__(self, path: Path, http: Http):
        self.path = path
        self.http = http
        self._zips: dict[str, tuple[float, float]] | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> dict[str, tuple[float, float]]:
        async with self._lock:
            if self._zips is None:
                if not self.path.exists():
                    log.info("Downloading Census ZIP centroids (one time, ~1MB)…")
                    r = await self.http.client.get(GAZETTEER_URL)
                    r.raise_for_status()
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        name = next(n for n in z.namelist() if n.endswith(".txt"))
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        self.path.write_bytes(z.read(name))
                self._zips = parse_gazetteer(self.path.read_text(encoding="latin-1"))
        return self._zips

    async def centroid(self, zip5: str) -> tuple[float, float] | None:
        return (await self.load()).get(zip5[:5])

    async def near(self, origin: tuple[float, float], miles: float, cap: int) -> list[str]:
        zips = await self.load()
        # ZCTA centroids can sit a few miles from a ZIP's edge; pad the radius a bit.
        hits = [
            (d, z)
            for z, c in zips.items()
            if abs(c[0] - origin[0]) < 2 and (d := haversine_miles(origin, c)) <= miles + 2
        ]
        return [z for _, z in sorted(hits)[:cap]]


def _addr_key(street: str, city: str, state: str, zip5: str) -> str:
    return "geo:" + re.sub(r"\s+", " ", f"{street}|{city}|{state}|{zip5}".upper()).strip()


def parse_batch_response(text: str) -> dict[str, tuple[float, float]]:
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 6 and row[2] == "Match" and "," in row[5]:
            lon, lat = row[5].split(",")
            out[row[0]] = (float(lat), float(lon))
    return out


class Geocoder:
    def __init__(self, http: Http, cache: Cache, zips: ZipIndex):
        self.http, self.cache, self.zips = http, cache, zips

    async def locate(self, query: str) -> tuple[float, float] | None:
        """A ZIP or a one-line street address -> (lat, lon)."""
        q = query.strip()
        if re.fullmatch(r"\d{5}(-\d{4})?", q):
            return await self.zips.centroid(q)
        data = await self.http.get(
            f"{CENSUS}/locations/onelineaddress",
            params={"address": q, "benchmark": BENCHMARK, "format": "json"},
            ttl=365 * DAY,
        )
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            m = re.search(r"\b(\d{5})\b", q)
            return await self.zips.centroid(m.group(1)) if m else None
        c = matches[0]["coordinates"]
        return (c["y"], c["x"])

    async def batch(self, addrs: dict[str, tuple[str, str, str, str]]) -> dict[str, tuple[float, float]]:
        """id -> (street, city, state, zip) to id -> (lat, lon). Cached per address."""
        out: dict[str, tuple[float, float]] = {}
        todo: dict[str, tuple[str, str, str, str]] = {}
        for i, a in addrs.items():
            hit = self.cache.get(_addr_key(*a))
            if hit == "miss":
                continue
            if hit:
                out[i] = tuple(hit)  # type: ignore[assignment]
            else:
                todo[i] = a
        items = list(todo.items())
        for start in range(0, len(items), 1000):
            chunk = items[start : start + 1000]
            buf = io.StringIO()
            w = csv.writer(buf)
            for i, (street, city, state, zip5) in chunk:
                w.writerow([i, street, city, state, zip5])
            try:
                text = await self.http.post(
                    f"{CENSUS}/locations/addressbatch",
                    files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
                    data={"benchmark": BENCHMARK},
                    parse="text",
                    ttl=0,  # cached per address below instead
                    cache_key=f"batch-nocache:{start}:{len(chunk)}:{id(chunk)}",
                )
            except Exception as e:  # geocoding is an enhancement; fall back to ZIPs
                log.warning("Census batch geocode failed (%s); using ZIP centroids", e)
                break
            got = parse_batch_response(text)
            for i, a in chunk:
                if i in got:
                    out[i] = got[i]
                    self.cache.set(_addr_key(*a), list(got[i]), 365 * DAY)
                else:
                    self.cache.set(_addr_key(*a), "miss", 30 * DAY)
        return out
