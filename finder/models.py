from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Provider:
    id: str  # NPI, or "ccn:<CMS id>" for hospitals
    kind: str  # clinician | organization | hospital
    name: str
    specialty: str = ""
    credential: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""
    lat: float | None = None
    lon: float | None = None
    located: str = ""  # "address" | "zip" (precision of lat/lon)
    distance: float | None = None
    # quality / context facts, all optional
    facts: dict[str, Any] = field(default_factory=dict)
    # network: in_network | out_of_network | not_listed | unknown | unchecked
    network: str = "unknown"
    network_detail: str = ""
    score: float | None = None
    confidence: float = 0.0
    breakdown: list[dict] = field(default_factory=list)
    flagged: bool = False  # serious/recent board action: kept out of top picks

    @property
    def npi(self) -> str | None:
        return None if self.id.startswith("ccn:") else self.id

    def to_dict(self) -> dict:
        return asdict(self)
