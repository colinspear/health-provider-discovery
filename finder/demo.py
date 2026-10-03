"""Offline demo: fictional providers so the UI can be tried without any network.

Every name here is made up. Nothing in demo mode reflects a real provider.
"""

from __future__ import annotations

import math
import random

from . import scoring
from .config import Settings
from .geo import haversine_miles
from .models import Provider
from .services import BY_KEY

CENTER = (41.9214, -87.6513)  # Lincoln Park, Chicago

FIRST = "Ana Ben Chloe Dev Elena Farid Grace Hiro Imani Jonah Kara Luis Maya Nina Omar Priya Quinn Rosa Sam Tara".split()
LAST = "Abbott Brennan Castillo Dawson Ellison Fujita Garza Holloway Ibarra Jensen Kowalski Lindqvist Moreno Nakamura Okafor Patel Quintero Rasmussen Silva Thornton".split()
STREETS = "Clark Halsted Lincoln Fullerton Armitage Belmont Ashland Damen Diversey Western".split()
HOSPITALS = ["Lakeshore General", "North Park Medical Center", "Riverside Memorial", "St. Brigid's", "Westside University Hospital", "Prairie Community Hospital"]
ORG_WORDS = ["Northside", "Lakeview", "Prairie", "Riverfront", "Midtown", "Elm Street", "Harbor"]


class DemoEngine:
    def __init__(self, settings: Settings):
        self.s = settings
        self._last: dict[str, Provider] = {}

    async def close(self) -> None:
        pass

    def _make(self, svc, rng: random.Random, i: int, radius: float) -> Provider:
        ang = rng.random() * 2 * math.pi
        dist = radius * math.sqrt(rng.random())
        lat = CENTER[0] + dist / 69 * math.sin(ang)
        lon = CENTER[1] + dist / (69 * math.cos(math.radians(CENTER[0]))) * math.cos(ang)
        street = f"{rng.randint(100, 3999)} N {rng.choice(STREETS)} Ave"
        base = dict(
            street=street, city="Chicago", state="IL", zip=f"606{rng.randint(10, 60)}",
            phone=f"312555{rng.randint(1000, 9999)}", lat=lat, lon=lon, located="address",
        )
        if svc.kind == "hospital":
            name = HOSPITALS[i % len(HOSPITALS)] + ("" if i < len(HOSPITALS) else f" {i}")
            return Provider(
                id=f"ccn:DEMO{i:04d}", kind="hospital", name=name, specialty="Acute Care Hospitals",
                facts={"stars": rng.choice([None, 2, 3, 3, 4, 4, 5]), "patient_stars": rng.choice([None, 2, 3, 4, 5]),
                       "emergency": rng.random() > 0.3},
                **base,
            )
        if svc.kind == "organization":
            name = f"{rng.choice(ORG_WORDS)} {svc.label.split(' ')[0].title()} {rng.choice(['Center', 'Clinic', 'Partners'])}"
            return Provider(id=f"99{i:08d}", kind="organization", name=name, specialty=svc.label, **base)
        grad = rng.randint(1982, 2021)
        facts = {
            "grad_year": grad if rng.random() > 0.1 else None,
            "medical_school": rng.choice(["Demo State University", "Example College of Medicine", None]),
            "group": f"{rng.choice(ORG_WORDS)} Medical Group",
            "telehealth": rng.random() > 0.5,
        }
        if rng.random() > 0.3:
            facts["mips_score"] = round(rng.uniform(40, 100), 1)
        if rng.random() > 0.5:
            facts["group_measures"] = [{"title": "Controlling High Blood Pressure", "rate": "70", "stars": rng.choice([2, 3, 4, 5])}]
        if svc.procedural and rng.random() > 0.4:
            facts["procedures"] = [{"name": "Demo procedure", "count": str(rng.randint(11, 200)), "percentile": rng.randint(5, 99)}]
        if rng.random() > 0.25:
            facts["hospitals"] = [{"ccn": "DEMO", "name": rng.choice(HOSPITALS), "stars": rng.choice([2, 3, 4, 5])}]
        return Provider(
            id=f"98{i:08d}", kind="clinician",
            name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            specialty=svc.label, credential=rng.choice(["MD", "MD", "DO", "MD"]) if "therap" not in svc.key else "LCSW",
            facts=facts, **base,
        )

    async def search(self, service: str, where: str | None = None, radius: float | None = None) -> dict:
        svc = BY_KEY[service]
        radius = radius or self.s.radius_miles
        rng = random.Random(f"{service}")
        n = {"hospital": 9, "organization": 14}.get(svc.kind, 45)
        providers = [self._make(svc, rng, i, max(radius, 1)) for i in range(n)]
        for i, p in enumerate(providers):
            p.distance = round(haversine_miles(CENTER, (p.lat, p.lon)), 2)
            p.network = rng.choices(["in_network", "out_of_network", "not_listed"], [0.7, 0.15, 0.15])[0]
            p.network_detail = "Demo data"
            scoring.score(p, radius, svc.procedural)
        providers.sort(key=lambda p: -(p.score or 0))
        self._last.update({p.id: p for p in providers})
        return {
            "service": {"key": svc.key, "label": svc.label, "kind": svc.kind, "tip": svc.tip},
            "origin": {"lat": CENTER[0], "lon": CENTER[1], "query": "DEMO (Chicago)"},
            "radius": radius, "zips_searched": 0, "network_source": "demo", "network_label": "Demo", "elapsed": 0,
            "demo": True,
            "providers": [p.to_dict() for p in providers],
        }

    async def check_network(self, pid: str) -> dict:
        return self._last[pid].to_dict()  # KeyError -> 404

    async def find_networks(self, q: str) -> list[dict]:
        return [{"plan": f"Demo {q} PPO", "plan_id": "demo", "network_ids": ["demo-net"]}]
