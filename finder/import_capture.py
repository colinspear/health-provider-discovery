"""Turn a browser capture of an insurer's provider-search responses into a network CSV.

    python -m finder.import_capture directory_capture.json data/davis_vision.csv

The capture (tools/capture_directory.js) is whatever JSON the insurer's site
returned, in whatever shape. This walks it looking for objects that look like a
provider: something name-like plus something ZIP-like. NPI and accepting-new-
patients are picked up when present.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from typing import Any, Iterator

NPI = re.compile(r"npi|nationalprovider", re.I)
ZIP = re.compile(r"zip|postal", re.I)
LAST = re.compile(r"^(last_?name|lname|surname|family(_?name)?)$", re.I)
FIRST = re.compile(r"^(first_?name|fname|given(_?name)?)$", re.I)
NAME = re.compile(r"^(provider_?name|full_?name|display_?name|name|doctor_?name)$", re.I)
OFFICE = re.compile(r"office|practice|location_?name|group|business", re.I)
ACCEPT = re.compile(r"accept", re.I)
STREET = re.compile(r"address_?line_?1|^address1$|^street", re.I)


def _flat(d: dict, prefix: str = "") -> dict[str, Any]:
    """One level of nesting is common (address: {...}); flatten it."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if not isinstance(v2, (dict, list)):
                    out.setdefault(k2, v2)
        elif not isinstance(v, list):
            out[k] = v
    return out


def _pick(d: dict[str, Any], pat: re.Pattern) -> Any:
    return next((v for k, v in d.items() if pat.search(k) and v not in (None, "")), None)


def providers(node: Any) -> Iterator[dict]:
    if isinstance(node, list):
        for x in node:
            yield from providers(x)
    elif isinstance(node, dict):
        f = _flat(node)
        z = _pick(f, ZIP)
        last, first, name = _pick(f, LAST), _pick(f, FIRST), _pick(f, NAME)
        if z and (last or name) and re.match(r"\d{5}", str(z)):
            npi = _pick(f, NPI)
            yield {
                "npi": str(npi) if npi and re.fullmatch(r"\d{10}", str(npi)) else "",
                "name": " ".join(str(x) for x in (first, last) if x) or str(name),
                "office": _pick(f, OFFICE) or "",
                "address": _pick(f, STREET) or "",
                "zip": str(z)[:5],
                "accepting": _pick(f, ACCEPT) if _pick(f, ACCEPT) is not None else "",
            }
        for v in node.values():
            if isinstance(v, (list, dict)):
                yield from providers(v)


def main(src: str, dst: str) -> None:
    captured = json.load(open(src))
    rows: dict[tuple, dict] = {}
    for item in captured:
        for p in providers(item.get("data", item)):
            rows.setdefault((p["npi"] or p["name"].lower(), p["zip"]), p)
    with open(dst, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["npi", "name", "office", "address", "zip", "accepting"])
        w.writeheader()
        w.writerows(rows.values())
    with_npi = sum(1 for r in rows.values() if r["npi"])
    print(f"{len(rows)} providers ({with_npi} with NPI) -> {dst}")
    if not rows:
        print("Nothing provider-shaped found. Did the capture include result pages?")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
