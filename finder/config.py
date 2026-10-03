"""Settings loaded from config.yaml (path overridable with HPD_CONFIG)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class NetworkSettings:
    name: str = "Network"  # shown in the UI, e.g. "MVP" or "Guardian Dental"
    # service keys this network covers (see finder/services.py); "*" = everything
    # not claimed by another network
    services: list[str] = field(default_factory=lambda: ["*"])
    # none  - no network data; every provider shows as "unknown"
    # csv   - a file of in-network NPIs (exported from your insurer, or hand-kept)
    # fhir  - your insurer's public FHIR Plan-Net provider directory API
    type: str = "none"
    csv_path: str = "data/in_network.csv"
    fhir_base_url: str = ""
    # Plan-Net network Organization ids that count as "your" network. Find them via
    # GET /api/networks?q=<plan name>. Empty = any listing with the payer counts.
    fhir_network_ids: list[str] = field(default_factory=list)
    # FHIR lookups are 2 requests per provider, so only the best N are checked
    # eagerly; the rest can be checked on demand from the UI.
    fhir_eager_check: int = 60


@dataclass
class Settings:
    home: str = "60614"  # ZIP code or street address
    radius_miles: float = 10.0
    max_zips: int = 60  # cap on ZIP codes queried per search (nearest first)
    precise_geocoding: bool = True  # Census batch geocoder; else ZIP centroids
    demo: bool = False
    cache_path: str = "data/cache.sqlite"
    networks: list[NetworkSettings] = field(default_factory=list)
    # CMS Provider Data Catalog dataset ids (override if CMS re-issues them)
    datasets: dict[str, str] = field(
        default_factory=lambda: {
            "clinicians": "mj5m-pzi6",  # Doctors and Clinicians National Downloadable File
            "mips": "a174-a962",  # Clinician Public Reporting: Overall MIPS Performance
            "affiliations": "27ea-46a8",  # Facility Affiliation Data
            "hospitals": "xubh-q36u",  # Hospital General Information
            "hcahps": "dgck-syfz",  # Patient survey (HCAHPS) - Hospital
            "utilization": "n0yb-util",  # Doctors and Clinicians Utilization Data (procedure volumes)
            "group_measures": "0ba7-2cb0",  # Group Public Reporting: MIPS Measures and Attestations
        }
    )

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else ROOT / q


def load_settings(path: str | None = None) -> Settings:
    path = path or os.environ.get("HPD_CONFIG") or str(ROOT / "config.yaml")
    raw: dict = {}
    if Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}
    nets = raw.pop("networks", None) or []
    if raw.get("network"):  # older single-network form
        nets.append(raw.pop("network"))
    raw.pop("network", None)
    datasets = raw.pop("datasets", None) or {}
    s = Settings(**raw, networks=[NetworkSettings(**n) for n in nets])
    s.datasets.update(datasets)
    if os.environ.get("HPD_DEMO", "").lower() in ("1", "true", "yes"):
        s.demo = True
    return s
